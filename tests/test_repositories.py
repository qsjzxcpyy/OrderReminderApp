import json
import sqlite3

import pytest

from app.db import connect_database, initialize_database
from app.repositories import Repository


def make_repo(tmp_path):
    path = tmp_path / "orders.sqlite3"
    initialize_database(path)
    return Repository(connect_database(path))


def snapshot(status="pending"):
    return {
        "platform_user_name": "shop-a",
        "platform_seller_id": "seller-1",
        "order_status": status,
        "date_latest_ship": "2026-08-28 14:59:59",
        "tracking_number": "ERP-TRACK-1",
        "mark_content": "marked",
        "markding_time": "2026-08-28 10:00:00",
        "system_note": "system",
        "operator_note": "operator",
        "abnormal_reason": "waiting stock",
        "consignee_name": "Alice",
        "consignee_country_name": "UNITED STATES",
        "consignee_email": "alice@example.com",
        "warehouse_name": "DJYCV2",
        "shipping_method_name_cn": "shipping-cn",
        "shipping_method_name_en": "Standard",
    }


def test_schema_has_required_tables_columns_indexes_and_foreign_keys(tmp_path):
    path = tmp_path / "schema.sqlite3"
    initialize_database(path)
    connection = connect_database(path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"orders", "reminder_events", "activity_log", "app_settings", "imports"} <= tables
    order_columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
    required = {
        "id", "order_no", "platform_user_name", "platform_seller_id", "erp_status",
        "latest_ship_at", "erp_tracking_number", "mark_content", "marking_time", "system_note",
        "operator_note", "abnormal_reason", "consignee_name", "consignee_country_name",
        "consignee_email", "warehouse_name", "shipping_method_name_cn", "shipping_method_name_en",
        "latest_erp_snapshot_json", "last_imported_at", "latest_arrival_at", "deadline_at",
        "deadline_override_at", "deadline_rule", "deadline_issue", "stage", "stage_suggestion",
        "actual_tracking_number", "return_confirmed_at", "processing_result", "processing_note",
        "completed_at", "overdue_at", "created_at", "updated_at",
    }
    assert required <= order_columns
    indexes = {row[1] for row in connection.execute("PRAGMA index_list(orders)")}
    assert "idx_orders_stage_deadline" in indexes
    assert "idx_orders_arrival" in indexes
    event_indexes = {row[1] for row in connection.execute("PRAGMA index_list(reminder_events)")}
    assert "idx_events_status_scheduled" in event_indexes
    event_foreign_keys = list(connection.execute("PRAGMA foreign_key_list(reminder_events)"))
    assert any(row[2] == "orders" and row[3] == "order_id" for row in event_foreign_keys)
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    connection.close()


def test_upsert_preserves_every_human_owned_field(tmp_path):
    repository = make_repo(tmp_path)
    repository.upsert_erp_snapshot("ORD-1", snapshot(), "2026-08-28T09:00:00+08:00")
    human = {
        "latest_arrival_at": "2026-09-11T14:59:00+08:00",
        "deadline_at": "2026-09-07T14:59:00+08:00",
        "deadline_override_at": "2026-09-06T10:00:00+08:00",
        "deadline_rule": "ARRIVAL_MINUS_4",
        "deadline_issue": "NONE",
        "stage": "WAITING_STOCK",
        "stage_suggestion": "READY_TO_SHIP",
        "actual_tracking_number": "REAL-1",
        "return_confirmed_at": "2026-09-07T11:00:00+08:00",
        "processing_result": "continue waiting",
        "processing_note": "keep this note",
        "completed_at": "2026-09-07T12:00:00+08:00",
    }
    repository.update_human_fields("ORD-1", human)
    repository.upsert_erp_snapshot("ORD-1", snapshot("shipped"), "2026-08-29T09:00:00+08:00")
    order = repository.get_order("ORD-1")
    for field, value in human.items():
        assert order[field] == value


def test_order_number_is_unique(tmp_path):
    repository = make_repo(tmp_path)
    repository.upsert_erp_snapshot("ORD-1", snapshot(), "2026-08-28T09:00:00+08:00")
    with pytest.raises(sqlite3.IntegrityError):
        repository.connection.execute("INSERT INTO orders (order_no, created_at, updated_at) VALUES (?, ?, ?)", ("ORD-1", "x", "x"))
    repository.connection.rollback()


def test_reminder_unique_key_and_foreign_key(tmp_path):
    repository = make_repo(tmp_path)
    repository.upsert_erp_snapshot("ORD-1", snapshot(), "2026-08-28T09:00:00+08:00")
    event = {"order_id": repository.get_order("ORD-1")["id"], "event_type": "DEADLINE_DAY", "schedule_key": "2026-09-07", "scheduled_at": "2026-09-07T10:00:00+08:00"}
    event_id = repository.insert_reminder_event(event)
    assert repository.insert_reminder_event(event) is None
    with pytest.raises(sqlite3.IntegrityError):
        repository.insert_reminder_event({**event, "schedule_key": "other", "order_id": 99999})
    assert repository.claim_pending_event(event_id)["status"] == "CLAIMED"
    repository.mark_channel_result(event_id, "windows", {"status": "sent"})
    repository.mark_channel_result(event_id, "email", {"status": "failed", "error": "SMTP down"})
    saved = repository.get_reminder_event(event_id)
    assert json.loads(saved["windows_result"])["status"] == "sent"
    assert json.loads(saved["email_result"])["status"] == "failed"


def test_save_settings_rolls_back_all_writes_on_failure(tmp_path):
    repository = make_repo(tmp_path)
    repository.connection.execute("CREATE TRIGGER fail_bad_setting BEFORE INSERT ON app_settings WHEN NEW.key = 'bad' BEGIN SELECT RAISE(ABORT, 'forced write failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_settings({"first": "must rollback", "bad": "fail"})
    assert repository.get_settings() == {}


def test_settings_roundtrip_and_activity_log(tmp_path):
    repository = make_repo(tmp_path)
    assert repository.get_settings() == {}
    repository.save_settings({"ship_plus_days": 3, "email_to": "ops@example.com"})
    assert repository.get_settings() == {"email_to": "ops@example.com", "ship_plus_days": 3}
    entry_id = repository.append_activity("ORD-1", "IMPORT", {"rows": 1}, "tester")
    row = repository.connection.execute("SELECT * FROM activity_log WHERE id = ?", (entry_id,)).fetchone()
    assert row["action"] == "IMPORT"
    assert json.loads(row["details_json"]) == {"rows": 1}


def test_list_orders_filters(tmp_path):
    repository = make_repo(tmp_path)
    repository.upsert_erp_snapshot("ORD-1", snapshot(), "2026-08-28T09:00:00+08:00")
    repository.update_human_fields("ORD-1", {"stage": "READY_TO_SHIP", "deadline_at": "2026-09-07T10:00:00+08:00"})
    assert len(repository.list_orders({"stage": "READY_TO_SHIP"})) == 1
    assert repository.list_orders({"stage": "DONE"}) == []
