from datetime import datetime

from fastapi.testclient import TestClient

from app.date_rules import LOCAL_TZ
from app.db import connect_database, initialize_database
from app.main import create_app
from app.notifications import SmtpMailer
from app.repositories import Repository


class FakeRepository:
    def __init__(self, orders, settings=None):
        self.orders = orders
        self.settings = settings or {
            "windows_notifications_enabled": True,
            "email_enabled": True,
            "recipients": ["ops@example.com"],
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_username": "ops@example.com",
        }
        self.events = {}
        self.results = {}

    def list_orders(self, _filters=None):
        return self.orders

    def get_settings(self):
        return self.settings

    def insert_reminder_event(self, event):
        key = (event["order_id"], event["event_type"], event["schedule_key"])
        if key in self.events:
            return None
        event_id = len(self.events) + 1
        self.events[key] = {"id": event_id, **event, "status": "PENDING"}
        return event_id

    def claim_pending_event(self, event_id):
        event = next((item for item in self.events.values() if item["id"] == event_id), None)
        if event is None or event["status"] != "PENDING":
            return None
        event["status"] = "CLAIMED"
        return event

    def find_pending_reminder_event(self, order_id, event_type, schedule_key):
        return next((event for event in self.events.values() if (
            event["order_id"], event["event_type"], event["schedule_key"], event["status"]
        ) == (order_id, event_type, schedule_key, "PENDING")), None)

    def mark_channel_result(self, event_id, channel, result):
        self.results[(event_id, channel)] = result
        event = next(item for item in self.events.values() if item["id"] == event_id)
        event[f"{channel}_status"] = result["status"]

    def set_reminder_event_status(self, event_id, status):
        next(item for item in self.events.values() if item["id"] == event_id)["status"] = status

    def mark_overdue(self, order_id, at):
        next(item for item in self.orders if item["id"] == order_id)["overdue_at"] = at

    def append_activity(self, *_args, **_kwargs):
        return 1


class FakeWindows:
    def __init__(self, result=None):
        self.result = result or {"status": "SENT"}
        self.calls = []

    def send(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class SequencedWindows(FakeWindows):
    def __init__(self, results):
        super().__init__(results[0])
        self.results = list(results)

    def send(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.results.pop(0) if self.results else {"status": "SENT"}


class FakeMailer:
    def __init__(self, results=None):
        self.results = list(results or [{"status": "SENT"}])
        self.calls = []

    def send_digest(self, event_type, orders, settings, secret):
        self.calls.append((event_type, orders, settings, secret))
        return self.results.pop(0) if self.results else {"status": "SENT"}


def order(order_id, *, stage="VIRTUAL_PENDING", deadline="2026-09-07T14:59:00+08:00", arrival="2026-09-11T14:59:00+08:00"):
    return {
        "id": order_id,
        "order_no": f"ORDER-{order_id}",
        "stage": stage,
        "deadline_at": deadline,
        "deadline_override_at": None,
        "latest_arrival_at": arrival,
    }


def service_for(orders, settings=None, windows=None, email=None):
    from app.reminders import ReminderService

    return ReminderService(
        FakeRepository(orders, settings),
        windows or FakeWindows(),
        email or FakeMailer(),
        secret_provider=lambda: "test-secret",
    )


def test_all_schedule_groups_are_created_at_due_times():
    orders = [order(1)]
    cases = [
        ("PROCESS_DAY", datetime(2026, 9, 7, 10, 0, tzinfo=LOCAL_TZ)),
        ("ARRIVAL_EVE", datetime(2026, 9, 10, 10, 0, tzinfo=LOCAL_TZ)),
        ("ARRIVAL_DAY", datetime(2026, 9, 11, 10, 0, tzinfo=LOCAL_TZ)),
    ]
    for event_type, now in cases:
        service = service_for(orders)
        result = service.check(now)
        assert result["created"] >= 1
        assert event_type in {event["event_type"] for event in service.repository.events.values()}


def test_process_day_filters_stages_and_arrival_events_exclude_only_completed():
    now = datetime(2026, 9, 7, 10, 0, tzinfo=LOCAL_TZ)
    service = service_for([order(1), order(2, stage="SHIPPED_PENDING_RETURN"), order(3, stage="COMPLETED")])
    assert service.check(now)["created"] == 1

    now = datetime(2026, 9, 10, 10, 0, tzinfo=LOCAL_TZ)
    service = service_for([order(1), order(2, stage="SHIPPED_PENDING_RETURN"), order(3, stage="COMPLETED")])
    service.check(now)
    arrival_events = [event for event in service.repository.events.values() if event["event_type"] == "ARRIVAL_EVE"]
    assert len(arrival_events) == 2


def test_event_identity_changes_with_schedule_inputs_and_prevents_duplicates():
    now = datetime(2026, 9, 7, 10, 0, tzinfo=LOCAL_TZ)
    service = service_for([order(1)])
    assert service.check(now)["created"] == 1
    assert service.check(now)["created"] == 0
    service.repository.orders[0]["deadline_override_at"] = "2026-09-08T14:59:00+08:00"
    service.repository.orders[0]["deadline_at"] = "2026-09-08T14:59:00+08:00"
    assert service.check(datetime(2026, 9, 8, 10, 0, tzinfo=LOCAL_TZ))["created"] == 1


def test_catch_up_and_overdue_events_are_dispatched_once():
    windows = FakeWindows()
    service = service_for([order(1)], windows=windows)
    assert service.check(datetime(2026, 9, 7, 11, 0, tzinfo=LOCAL_TZ))["sent"] == 1

    overdue = service_for([order(2, deadline="2026-09-07T10:30:00+08:00")])
    result = overdue.check(datetime(2026, 9, 7, 11, 0, tzinfo=LOCAL_TZ))
    assert result["overdue"] == ["ORDER-2"]
    assert overdue.check(datetime(2026, 9, 7, 11, 1, tzinfo=LOCAL_TZ))["created"] == 0


def test_failed_overdue_windows_notification_is_retried():
    windows = SequencedWindows([{"status": "FAILED", "message": "temporary"}, {"status": "SENT"}])
    settings = {"windows_notifications_enabled": True, "email_enabled": False}
    service = service_for([order(1, deadline="2026-09-07T10:30:00+08:00")], settings=settings, windows=windows)
    first = service.check(datetime(2026, 9, 7, 11, 0, tzinfo=LOCAL_TZ))
    second = service.check(datetime(2026, 9, 7, 11, 1, tzinfo=LOCAL_TZ))

    assert first["sent"] == 1
    assert second["sent"] == 1
    assert len(windows.calls) == 2
    assert all(event["status"] == "SENT" for event in service.repository.events.values())


def test_channel_results_are_independent_and_disabled_channels_are_skipped():
    failing_email = FakeMailer([{"status": "FAILED", "message": "smtp down"}])
    service = service_for([order(1)], email=failing_email)
    service.check(datetime(2026, 9, 7, 10, 0, tzinfo=LOCAL_TZ))
    assert service.repository.results[(1, "windows")]["status"] == "SENT"
    assert service.repository.results[(1, "email")]["status"] == "FAILED"

    service = service_for([order(1)], {"windows_notifications_enabled": False, "email_enabled": False})
    service.check(datetime(2026, 9, 7, 10, 0, tzinfo=LOCAL_TZ))
    assert service.repository.results[(1, "windows")]["status"] == "SKIPPED"
    assert service.repository.results[(1, "email")]["status"] == "SKIPPED"


def test_due_orders_share_one_email_digest_per_event_date_bucket():
    mailer = FakeMailer()
    service = service_for([order(1), order(2)], email=mailer)
    service.check(datetime(2026, 9, 7, 10, 0, tzinfo=LOCAL_TZ))
    assert len(mailer.calls) == 1
    assert [item["order_no"] for item in mailer.calls[0][1]] == ["ORDER-1", "ORDER-2"]
    assert service.repository.results[(1, "email")]["status"] == "SENT"
    assert service.repository.results[(2, "email")]["status"] == "SENT"


def test_failed_grouped_email_retries_only_the_failed_channel():
    windows = FakeWindows()
    mailer = FakeMailer([{"status": "FAILED", "message": "down"}, {"status": "SENT"}])
    service = service_for([order(1), order(2)], windows=windows, email=mailer)
    now = datetime(2026, 9, 7, 10, 0, tzinfo=LOCAL_TZ)
    service.check(now)
    service.check(now)
    assert len(mailer.calls) == 2
    assert len(windows.calls) == 1
    assert all(event["status"] == "SENT" for event in service.repository.events.values())


def test_overdue_timestamp_is_persisted_on_the_order(tmp_path):
    database = tmp_path / "overdue.sqlite3"
    initialize_database(database)
    repository = Repository(connect_database(database))
    repository.upsert_erp_snapshot("ORDER-1", {"order_status": "pending"}, "2026-09-01T10:00:00+08:00")
    repository.update_human_fields("ORDER-1", {
        "stage": "VIRTUAL_PENDING", "latest_arrival_at": "2026-09-11T14:59:00+08:00",
        "deadline_at": "2026-09-07T10:30:00+08:00",
    })
    from app.reminders import ReminderService
    service = ReminderService(repository, FakeWindows(), FakeMailer(), secret_provider=lambda: "test")
    service.check(datetime(2026, 9, 7, 11, 0, tzinfo=LOCAL_TZ))
    assert repository.get_order("ORDER-1")["overdue_at"] is not None
    repository.connection.close()


def test_credential_store_uses_named_entries_without_cross_leakage(monkeypatch):
    from app import credential_store

    values = {}
    monkeypatch.setattr(credential_store.keyring, "set_password", lambda service, name, value: values.__setitem__((service, name), value))
    monkeypatch.setattr(credential_store.keyring, "get_password", lambda service, name: values.get((service, name)))
    monkeypatch.setattr(credential_store.keyring, "delete_password", lambda service, name: values.pop((service, name)))
    credential_store.save_secret("smtp", "one")
    credential_store.save_secret("other", "two")
    assert credential_store.get_secret("smtp") == "one"
    credential_store.delete_secret("smtp")
    assert credential_store.get_secret("smtp") is None
    assert credential_store.get_secret("other") == "two"


def test_credential_store_falls_back_to_dpapi_when_keyring_save_or_get_fails(monkeypatch, tmp_path):
    from app import credential_store

    store = credential_store.CredentialStore(secret_path=tmp_path / "smtp_secret.dpapi")
    monkeypatch.setattr(credential_store.keyring, "set_password", lambda *_args: (_ for _ in ()).throw(RuntimeError("unavailable")))
    monkeypatch.setattr(credential_store.keyring, "get_password", lambda *_args: (_ for _ in ()).throw(RuntimeError("unavailable")))
    assert store.set("smtp", "secret") is True
    assert store.exists("smtp") is True
    assert store.get("smtp") == "secret"
    assert store.secret_path.read_bytes() != b"secret"
    store.delete("smtp")


def test_credential_store_reports_failure_when_keyring_and_dpapi_fail(monkeypatch, tmp_path):
    from app import credential_store

    store = credential_store.CredentialStore(secret_path=tmp_path / "smtp_secret.dpapi")
    monkeypatch.setattr(credential_store.keyring, "set_password", lambda *_args: (_ for _ in ()).throw(RuntimeError("unavailable")))
    monkeypatch.setattr(credential_store, "protect_secret", lambda *_args: (_ for _ in ()).throw(RuntimeError("dpapi unavailable")))

    assert store.set("smtp", "secret") is False


def test_smtp_malformed_port_returns_not_configured_instead_of_raising():
    result = SmtpMailer().send_digest("PROCESS_DAY", [], {
        "smtp_host": "smtp.example.com", "smtp_port": "invalid", "smtp_username": "ops@example.com", "recipients": ["ops@example.com"],
    }, "secret")
    assert result["status"] == "NOT_CONFIGURED"


def test_qq_smtp_uses_login_authentication(monkeypatch):
    from app import notifications

    class FakeSmtp:
        def __init__(self, *_args, **_kwargs):
            self.auth_calls = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def auth_login(self, challenge=None):
            return "encoded-login"

        def auth(self, mechanism, authobject, **kwargs):
            self.auth_calls.append((mechanism, authobject, kwargs))
            return 235, b"ok"

        def send_message(self, _message):
            return None

    smtp = FakeSmtp()
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", lambda *_args, **_kwargs: smtp)

    result = SmtpMailer().send_digest("TEST", [], {
        "smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_username": "ops@qq.com", "recipients": ["owner@qq.com"],
    }, "secret")

    assert result["status"] == "SENT"
    assert smtp.auth_calls[0][0] == "LOGIN"
    assert smtp.auth_calls[0][2] == {"initial_response_ok": False}


def test_manual_reminder_check_route(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "orders.sqlite3", start_scheduler=False))
    response = client.post("/api/reminders/check")
    assert response.status_code == 200
    assert {"created", "sent", "overdue"} <= response.json().keys()
