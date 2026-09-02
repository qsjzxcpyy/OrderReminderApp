import os
import signal
import tempfile
from zipfile import BadZipFile
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from apscheduler.schedulers.background import BackgroundScheduler

from .config import APP_HOST, APP_PORT, APP_VERSION, WEB_DIR
from .credential_store import CredentialStore
from .date_rules import LOCAL_TZ
from .db import initialize_database
from .importer import import_workbook
from .lifecycle import ClientLifecycle
from .notifications import SmtpMailer, WindowsNotifier
from .reminders import ReminderService
from .services import OrderService


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderPatch(RequestModel):
    latest_arrival_at: str | None = None
    deadline_override_at: str | None = None
    stage: str | None = None
    processing_result: str | None = None
    processing_note: str | None = None


class TrackingRequest(RequestModel):
    actual_tracking_number: str


class CompleteRequest(RequestModel):
    processing_result: str
    processing_note: str


class DeleteOrdersRequest(RequestModel):
    order_nos: list[str] = Field(min_length=1)


class ClientHeartbeatRequest(RequestModel):
    client_id: str = Field(min_length=1, max_length=100)
    active: bool = True


class SettingsPatch(RequestModel):
    compensation_days: int | None = None
    windows_notifications_enabled: bool | None = None
    email_enabled: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_authorization_code: str | None = None
    recipients: list[str] | None = None
    processing_results: list[str] | None = None

    @field_validator("compensation_days")
    @classmethod
    def valid_days(cls, value):
        if value is not None and value not in (2, 3):
            raise ValueError("compensation_days must be 2 or 3")
        return value

    @field_validator("recipients")
    @classmethod
    def valid_recipients(cls, value):
        if value is not None and any("@" not in item or not item.strip() for item in value):
            raise ValueError("recipients must contain email addresses")
        return [item.strip() for item in value] if value is not None else value

    @field_validator("processing_results")
    @classmethod
    def valid_results(cls, value):
        if value is not None and any(not item.strip() for item in value):
            raise ValueError("processing_results cannot contain blank values")
        return [item.strip() for item in value] if value is not None else value


def create_app(db_path=None, start_scheduler=False, shutdown_when_idle=False):
    path = Path(db_path) if db_path else Path(__file__).resolve().parent.parent / "data" / "orders.sqlite3"
    initialize_database(path)
    app = FastAPI(title="Order Reminder", version=APP_VERSION)
    app.state.db_path = path
    app.state.secrets = CredentialStore(secret_path=path.parent / "smtp_secret.dpapi")
    app.state.max_upload_bytes = 50 * 1024 * 1024
    app.state.windows_notifier = WindowsNotifier()
    app.state.smtp_mailer = SmtpMailer()
    app.state.client_lifecycle = ClientLifecycle()

    @contextmanager
    def service():
        instance = OrderService(app.state.db_path, app.state.secrets)
        try:
            yield instance
        finally:
            instance.close()

    def missing_order(error):
        raise HTTPException(404, str(error)) from error

    def reminder_service():
        connection_service = OrderService(app.state.db_path, app.state.secrets)
        return ReminderService(
            connection_service.repository,
            app.state.windows_notifier,
            app.state.smtp_mailer,
            lambda: app.state.secrets.get("smtp_authorization_code"),
        ), connection_service

    def run_reminder_check():
        reminders, connection_service = reminder_service()
        try:
            return reminders.check()
        finally:
            connection_service.close()

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": APP_VERSION}

    @app.post("/api/client/heartbeat")
    def client_heartbeat(request: ClientHeartbeatRequest):
        app.state.client_lifecycle.heartbeat(request.client_id, request.active)
        return {"ok": True, "active": app.state.client_lifecycle.active_count}

    @app.post("/api/import")
    async def import_orders(file: UploadFile = File(...)):
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(422, "only .xlsx files are supported")
        temporary_path = None
        try:
            content = await file.read()
            if len(content) > app.state.max_upload_bytes:
                raise HTTPException(413, "file must not exceed 50 MB")
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as temporary:
                temporary.write(content)
                temporary_path = temporary.name
            imported = import_workbook(temporary_path)
            summary = {"duplicates": imported.duplicates, "invalid_rows": imported.invalid_rows, "skipped": imported.skipped}
            with service() as orders:
                return orders.import_rows(imported.rows, file.filename, summary)
        except HTTPException:
            raise
        except (BadZipFile, OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        finally:
            await file.close()
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @app.get("/api/orders")
    def list_orders(stage: str | None = None, deadline_issue: str | None = None, erp_status: str | None = None, search: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100):
        with service() as orders:
            rows = orders.list_orders({"stage": stage, "deadline_issue": deadline_issue, "erp_status": erp_status, "search": search}, limit)
            return {"count": len(rows), "orders": rows}

    @app.delete("/api/orders")
    def delete_orders(request: DeleteOrdersRequest):
        with service() as orders:
            result = orders.delete_orders(request.order_nos)
        if result["deleted"] == 0:
            raise HTTPException(404, "没有找到可删除的订单")
        return result

    @app.get("/api/orders/{order_no}")
    def order_detail(order_no: str):
        try:
            with service() as orders:
                return orders.detail(order_no)
        except LookupError as error:
            missing_order(error)

    @app.patch("/api/orders/{order_no}")
    def patch_order(order_no: str, request: OrderPatch):
        try:
            with service() as orders:
                return {"order": orders.patch_order(order_no, request.model_dump(exclude_unset=True))}
        except LookupError as error:
            missing_order(error)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/orders/{order_no}/tracking")
    def save_tracking(order_no: str, request: TrackingRequest):
        try:
            with service() as orders:
                return {"order": orders.save_tracking(order_no, request.actual_tracking_number)}
        except LookupError as error:
            missing_order(error)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/orders/{order_no}/return-confirmation")
    def return_confirmation(order_no: str):
        try:
            with service() as orders:
                return {"order": orders.confirm_return(order_no)}
        except LookupError as error:
            missing_order(error)
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/orders/{order_no}/complete")
    def complete_order(order_no: str, request: CompleteRequest):
        try:
            with service() as orders:
                return {"order": orders.complete(order_no, request.processing_result, request.processing_note)}
        except LookupError as error:
            missing_order(error)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/settings")
    def get_settings():
        with service() as orders:
            return orders.get_settings()

    @app.patch("/api/settings")
    def patch_settings(request: SettingsPatch):
        try:
            with service() as orders:
                return orders.update_settings(request.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/settings/test-email")
    def test_email():
        with service() as orders:
            settings = orders.get_settings()
        return app.state.smtp_mailer.send_digest(
            "TEST", [], settings, app.state.secrets.get("smtp_authorization_code")
        )

    @app.post("/api/settings/test-notification")
    def test_notification():
        if not start_scheduler:
            return {"status": "UNSUPPORTED", "message": "Notification delivery is disabled in deterministic test mode."}
        return app.state.windows_notifier.send("Order reminder test", "Windows notifications are configured.", f"http://{APP_HOST}:{APP_PORT}/")

    @app.post("/api/reminders/check")
    def check_reminders():
        return run_reminder_check()

    if start_scheduler:
        scheduler = BackgroundScheduler(timezone=LOCAL_TZ)
        scheduler.add_job(run_reminder_check, "interval", minutes=1, id="order-reminder-check", replace_existing=True)
        if shutdown_when_idle:
            def stop_when_browser_closes():
                if app.state.client_lifecycle.should_shutdown():
                    os.kill(os.getpid(), signal.SIGTERM)

            scheduler.add_job(stop_when_browser_closes, "interval", seconds=1, id="browser-lifecycle-check", replace_existing=True)
        app.state.scheduler = scheduler

        @app.on_event("startup")
        def start_reminder_scheduler():
            if not scheduler.running:
                scheduler.start()
            run_reminder_check()

        @app.on_event("shutdown")
        def stop_reminder_scheduler():
            if scheduler.running:
                scheduler.shutdown(wait=False)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/app.js", include_in_schema=False)
    def javascript():
        return FileResponse(WEB_DIR / "app.js", media_type="text/javascript")

    @app.get("/styles.css", include_in_schema=False)
    def stylesheet():
        return FileResponse(WEB_DIR / "styles.css", media_type="text/css")

    return app
