import hashlib
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import create_app


HEADERS = [
    "refrence_no", "platform_user_name", "platform_seller_id", "order_status",
    "date_latest_ship", "tracking_number", "mark_content", "markding_time",
    "system_note", "operator_note", "abnormal_reason", "consignee_name",
    "consignee_country_name", "consignee_email", "warehouse_name",
    "shipping_method_name_cn", "shipping_method_name_en",
]


def make_workbook(tmp_path, order_no="ORDER-1", latest_ship="2026-09-01 14:59", operator_note=""):
    path = tmp_path / "orders.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Export orders"
    sheet.append(HEADERS)
    values = {header: "" for header in HEADERS}
    values.update(refrence_no=order_no, date_latest_ship=latest_ship, order_status="pending", operator_note=operator_note)
    sheet.append([values[header] for header in HEADERS])
    book.save(path)
    return path


def make_client(tmp_path):
    return TestClient(create_app(db_path=tmp_path / "test.sqlite3", start_scheduler=False))


def test_test_app_uses_database_directory_for_credential_fallback(tmp_path):
    client = make_client(tmp_path)

    assert client.app.state.secrets.secret_path == tmp_path / "smtp_secret.dpapi"


def import_order(client, workbook):
    with workbook.open("rb") as source:
        return client.post("/api/import", files={"file": ("orders.xlsx", source, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_health_and_static_routes(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/health").json() == {"ok": True, "version": "1.0.0"}
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_dashboard_includes_order_selection_and_copy_controls(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert 'id="select-all"' in index
    assert 'id="delete-selected"' in index
    assert 'id="selection-count"' in index
    assert "navigator.clipboard" in app_script
    assert "copyOrderNo" in app_script


def test_dashboard_includes_manual_stages_and_customer_note_column(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert "客服备注" in index
    assert "超卖跟进客户，未虚发" in index
    assert "超卖跟进客户，已虚发，已退款" in index
    assert "客服备注" in app_script
    assert "operator_note" in app_script
    assert "手动导单，未手动回传单号" in index
    assert "手动导单，未手动回传单号" in app_script


def test_order_stage_can_be_manually_saved_and_customer_note_is_searchable(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path, operator_note="请客服跟进客户"))

    patch = client.patch("/api/orders/ORDER-1", json={"stage": "VIRTUAL_CUSTOMER_FOLLOWUP"})

    assert patch.status_code == 200
    assert patch.json()["order"]["stage"] == "VIRTUAL_CUSTOMER_FOLLOWUP"
    assert client.get("/api/orders", params={"stage": "VIRTUAL_CUSTOMER_FOLLOWUP"}).json()["count"] == 1
    assert client.get("/api/orders", params={"search": "跟进客户"}).json()["count"] == 1


def test_manual_import_pending_return_stage_can_be_saved_and_filtered(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    patch = client.patch("/api/orders/ORDER-1", json={"stage": "MANUAL_IMPORT_PENDING_RETURN"})

    assert patch.status_code == 200
    assert patch.json()["order"]["stage"] == "MANUAL_IMPORT_PENDING_RETURN"
    assert client.get("/api/orders", params={"stage": "MANUAL_IMPORT_PENDING_RETURN"}).json()["count"] == 1


def test_saving_tracking_does_not_overwrite_manually_selected_stage(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    client.patch("/api/orders/ORDER-1", json={"stage": "VIRTUAL_CUSTOMER_FOLLOWUP"})

    response = client.post("/api/orders/ORDER-1/tracking", json={"actual_tracking_number": "TRACK-1"})

    assert response.status_code == 200
    assert response.json()["order"]["stage"] == "VIRTUAL_CUSTOMER_FOLLOWUP"


def test_import_lists_missing_arrival_and_keeps_human_fields(tmp_path):
    client = make_client(tmp_path)
    workbook = make_workbook(tmp_path)
    first = import_order(client, workbook)
    assert first.status_code == 200
    assert first.json()["added"] == 1
    order = client.get("/api/orders/ORDER-1").json()["order"]
    assert order["stage"] == "OVERSELL_CUSTOMER_UNSHIPPED"
    assert order["stage_suggestion"] == "OVERSELL_CUSTOMER_UNSHIPPED"
    assert order["deadline_issue"] == "MISSING_ARRIVAL"
    patch = client.patch("/api/orders/ORDER-1", json={"latest_arrival_at": "2026-09-11 14:59"})
    assert patch.status_code == 200
    assert patch.json()["order"]["deadline_at"].startswith("2026-09-07T14:59")
    second = import_order(client, workbook)
    assert second.status_code == 200
    assert second.json()["updated"] == 1
    assert client.get("/api/orders?stage=OVERSELL_CUSTOMER_UNSHIPPED").json()["count"] == 1


def test_order_flow_logs_history_and_enforces_tracking(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    assert client.post("/api/orders/ORDER-1/return-confirmation").status_code == 409
    tracking = client.post("/api/orders/ORDER-1/tracking", json={"actual_tracking_number": "TRACK-1"})
    assert tracking.status_code == 200
    assert tracking.json()["order"]["stage"] == "OVERSELL_CUSTOMER_UNSHIPPED"
    confirmed = client.post("/api/orders/ORDER-1/return-confirmation")
    assert confirmed.status_code == 200
    assert confirmed.json()["order"]["stage"] == "COMPLETED"
    detail = client.get("/api/orders/ORDER-1").json()
    assert [entry["action"] for entry in detail["activity"]][:2] == ["RETURN_CONFIRMED", "TRACKING_SAVED"]


def test_manual_deadline_and_exception_completion_validation(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    assert client.patch("/api/orders/ORDER-1", json={"deadline_override_at": "2026-09-05 10:00"}).status_code == 422
    client.patch("/api/orders/ORDER-1", json={"latest_arrival_at": "2026-09-11 14:59"})
    assert client.patch("/api/orders/ORDER-1", json={"deadline_override_at": "2026-09-12 10:00"}).status_code == 422
    assert client.post("/api/orders/ORDER-1/complete", json={"processing_result": "done", "processing_note": ""}).status_code == 422
    done = client.post("/api/orders/ORDER-1/complete", json={"processing_result": "cancelled", "processing_note": "handled"})
    assert done.status_code == 200
    assert done.json()["order"]["completed_at"] is not None


def test_settings_never_returns_secret_and_reports_configured_fallback(tmp_path):
    client = make_client(tmp_path)
    response = client.patch("/api/settings", json={"compensation_days": 3, "email_enabled": True, "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_username": "ops@example.com", "smtp_authorization_code": "never-expose-this", "recipients": ["owner@example.com"], "processing_results": ["cancelled"]})
    assert response.status_code == 200
    settings = client.get("/api/settings").json()
    assert "smtp_authorization_code" not in settings
    # The keyring may be unavailable, but the DPAPI fallback still configures
    # the adapter without exposing the secret.
    assert settings["smtp_secret_configured"] is True
    assert client.post("/api/settings/test-notification").json()["status"] == "UNSUPPORTED"


def test_settings_default_to_qq_smtp(tmp_path):
    client = make_client(tmp_path)

    settings = client.get("/api/settings").json()

    assert settings["smtp_host"] == "smtp.qq.com"
    assert settings["smtp_port"] == 465


def test_settings_reports_credential_save_failure(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    monkeypatch.setattr(client.app.state.secrets, "set", lambda *_args: False)

    response = client.patch("/api/settings", json={"smtp_authorization_code": "secret"})

    assert response.status_code == 422
    assert "授权码" in response.json()["detail"]


def test_invalid_upload_and_missing_order_are_rejected(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/import", files={"file": ("orders.csv", b"bad", "text/csv")}).status_code == 422
    assert client.get("/api/orders/missing").status_code == 404


def test_selected_orders_can_be_deleted_and_reminder_events_are_cascaded(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path, "ORDER-A"))
    import_order(client, make_workbook(tmp_path, "ORDER-B"))
    first = client.get("/api/orders/ORDER-A").json()["order"]
    db = client.app.state.db_path
    from app.db import connect_database
    connection = connect_database(db)
    connection.execute("INSERT INTO reminder_events (order_id, event_type, schedule_key, scheduled_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (first["id"], "PROCESS_DAY", "selected", "2026-09-01T10:00:00+08:00", "2026-09-01T10:00:00+08:00", "2026-09-01T10:00:00+08:00"))
    connection.commit()
    connection.close()

    response = client.request("DELETE", "/api/orders", json={"order_nos": ["ORDER-A"]})

    assert response.status_code == 200
    assert response.json() == {"deleted": 1, "not_found": []}
    assert client.get("/api/orders/ORDER-A").status_code == 404
    assert client.get("/api/orders/ORDER-B").status_code == 200
    connection = connect_database(db)
    assert connection.execute("SELECT COUNT(*) FROM reminder_events").fetchone()[0] == 0
    assert connection.execute("SELECT action FROM activity_log WHERE action = 'ORDER_DELETED'").fetchone()[0] == "ORDER_DELETED"
    connection.close()


def test_delete_orders_rejects_empty_selection(tmp_path):
    client = make_client(tmp_path)

    response = client.request("DELETE", "/api/orders", json={"order_nos": []})

    assert response.status_code == 422


def test_upload_over_the_configured_limit_returns_413_without_large_fixture(tmp_path):
    client = make_client(tmp_path)
    client.app.state.max_upload_bytes = 16
    response = client.post("/api/import", files={"file": ("orders.xlsx", b"x" * 17, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 413


def test_upload_temporary_file_is_removed_after_success_and_invalid_workbook(tmp_path, monkeypatch):
    upload_directory = tmp_path / "uploads"
    upload_directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(upload_directory))
    client = make_client(tmp_path)
    assert import_order(client, make_workbook(tmp_path)).status_code == 200
    assert list(upload_directory.glob("*.xlsx")) == []
    bad = client.post("/api/import", files={"file": ("orders.xlsx", b"not a workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert bad.status_code == 422
    assert list(upload_directory.glob("*.xlsx")) == []


def test_extra_request_fields_are_rejected_by_pydantic(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    assert client.patch("/api/orders/ORDER-1", json={"stage": "VIRTUAL_PENDING", "unexpected": True}).status_code == 422
    assert client.post("/api/orders/ORDER-1/tracking", json={"actual_tracking_number": "T-1", "unexpected": True}).status_code == 422
    assert client.patch("/api/settings", json={"compensation_days": 2, "unexpected": True}).status_code == 422


def test_list_orders_applies_stage_search_and_limit_filters(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path, "ORDER-A"))
    import_order(client, make_workbook(tmp_path, "ORDER-B"))
    client.patch("/api/orders/ORDER-A", json={"latest_arrival_at": "2026-09-11 14:59"})
    response = client.get("/api/orders", params={"stage": "OVERSELL_CUSTOMER_UNSHIPPED", "search": "ORDER-A", "limit": 1})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["orders"][0]["order_no"] == "ORDER-A"


def test_order_risk_is_overdue_in_list_and_detail(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    assert client.patch("/api/orders/ORDER-1", json={"latest_arrival_at": "2026-09-11 14:59"}).status_code == 200
    assert client.patch("/api/orders/ORDER-1", json={"deadline_override_at": "2026-08-27 10:00"}).status_code == 200
    assert client.post("/api/reminders/check").status_code == 200
    assert client.get("/api/orders").json()["orders"][0]["risk"] == "OVERDUE"
    assert client.get("/api/orders/ORDER-1").json()["order"]["risk"] == "OVERDUE"


def test_order_detail_includes_reminder_events_and_channel_status(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    order_id = client.get("/api/orders/ORDER-1").json()["order"]["id"]
    connection = client.app.state.db_path
    from app.db import connect_database
    db = connect_database(connection)
    db.execute("INSERT INTO reminder_events (order_id, event_type, schedule_key, scheduled_at, windows_status, email_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (order_id, "PROCESS_DAY", "test", "2026-09-01T10:00:00+08:00", "SENT", "FAILED", "2026-09-01T10:00:00+08:00", "2026-09-01T10:00:00+08:00"))
    db.commit()
    db.close()
    detail = client.get("/api/orders/ORDER-1").json()
    assert detail["reminders"][0]["event_type"] == "PROCESS_DAY"
    assert detail["reminders"][0]["windows_status"] == "SENT"
    assert detail["reminders"][0]["email_status"] == "FAILED"


def test_production_notification_test_returns_channel_result(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "orders.sqlite3", start_scheduler=True))
    client.app.state.windows_notifier.send = lambda *args, **kwargs: {"status": "SENT", "message": "test notification sent"}

    response = client.post("/api/settings/test-notification")

    assert response.json() == {"status": "SENT", "message": "test notification sent"}


def test_error_request_closes_database_connection_for_windows_cleanup(tmp_path):
    database_path = tmp_path / "cleanup.sqlite3"
    client = TestClient(create_app(db_path=database_path, start_scheduler=False))
    assert client.get("/api/orders/missing").status_code == 404
    database_path.unlink()
    assert not database_path.exists()


def test_real_fixture_is_unchanged_when_imported(tmp_path):
    fixture = Path(__file__).parents[2] / "order-1787727280.xlsx"
    if not fixture.exists():
        return
    before = hashlib.sha256(fixture.read_bytes()).hexdigest()
    response = import_order(make_client(tmp_path), fixture)
    assert response.status_code == 200
    assert response.json()["added"] == 62
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == before
