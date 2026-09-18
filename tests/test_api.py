import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import create_app
from app.db import connect_database


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


def test_client_heartbeat_tracks_page_presence_without_touching_orders(tmp_path):
    client = make_client(tmp_path)

    online = client.post("/api/client/heartbeat", json={"client_id": "page-1"})
    assert online.status_code == 200
    assert client.app.state.client_lifecycle.active_count == 1

    offline = client.post("/api/client/heartbeat", json={"client_id": "page-1", "active": False})
    assert offline.status_code == 200
    assert client.app.state.client_lifecycle.active_count == 0


def test_dashboard_includes_order_selection_and_copy_controls(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert 'id="select-all"' in index
    assert 'id="delete-selected"' in index
    assert 'id="selection-count"' in index
    assert "navigator.clipboard" in app_script
    assert "copyOrderNo" in app_script


def test_dashboard_has_collapsible_completed_orders_and_deadline_sort(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert '<details id="completed-orders-section"' in index
    assert 'id="completed-order-table-body"' in index
    assert '<details id="completed-orders-section" open' not in index
    assert "sortOrdersByDeadline" in app_script
    assert "if (!aDeadline && bDeadline) return -1" in app_script
    assert "if (aDeadline && !bDeadline) return 1" in app_script
    assert "String(bDeadline).localeCompare(String(aDeadline))" in app_script


def test_dashboard_includes_bulk_complete_action(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert 'id="quick-complete"' in index
    assert "applyCompletionToSelected" in app_script
    assert 'stage: "COMPLETED"' in app_script


def test_shift_selection_is_handled_on_checkbox_click_and_toolbar_has_layout_hooks(tmp_path):
    client = make_client(tmp_path)
    app_script = client.get("/app.js").text
    styles = client.get("/styles.css").text

    assert "selectionAnchor" in app_script
    assert "event.shiftKey" in app_script
    assert 'addEventListener("change", (event) => { const checkbox' not in app_script
    assert ".bulk-stage-field" in styles
    assert ".summary-tomorrow" in styles
    assert ".summary-long-term" in styles


def test_dashboard_includes_manual_stages_and_customer_note_column(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert "客服备注" in index
    assert "客服备注" in app_script
    assert "operator_note" in app_script
    assert "自定义处理阶段" in index
    assert "处理完成" in index


def test_dashboard_includes_unprocessed_orders_summary_filter(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert 'data-summary-filter="UNPROCESSED"' in index
    assert 'id="summary-unprocessed"' in index
    assert "UNPROCESSED" in app_script
    assert "!isCompleted(order)" in app_script
    assert "hasCustomStage(order)" in app_script
    assert "hasProcessingDeadline(order)" in app_script


def test_dashboard_assets_and_api_requests_disable_stale_cache(tmp_path):
    client = make_client(tmp_path)

    index_response = client.get("/")
    app_response = client.get("/app.js")
    styles_response = client.get("/styles.css")

    assert index_response.headers["cache-control"] == "no-store, max-age=0"
    assert app_response.headers["cache-control"] == "no-store, max-age=0"
    assert styles_response.headers["cache-control"] == "no-store, max-age=0"
    assert "/app.js?v=20260909-3" in index_response.text
    assert "/styles.css?v=20260909-3" in index_response.text
    assert 'cache: "no-store"' in app_response.text


def test_unprocessed_filter_includes_custom_stage_without_deadline_and_blank_deadline(tmp_path):
    client = make_client(tmp_path)
    app_script = client.get("/app.js").text
    is_completed = re.search(r"function isCompleted\(order\) \{[^}]+\}", app_script)
    has_stage = re.search(r"function hasCustomStage\(order\) \{[^}]+\}", app_script)
    has_deadline = re.search(r"function hasProcessingDeadline\(order\) \{[^}]+\}", app_script)
    is_unprocessed = re.search(r"function isUnprocessed\(order\) \{[^}]+\}", app_script)
    assert all((is_completed, has_stage, has_deadline, is_unprocessed))
    node_script = f"""
const terminalStages = new Set(["OVERSELL_CUSTOMER_REFUNDED", "COMPLETED"]);
{is_completed.group(0)}
{has_stage.group(0)}
{has_deadline.group(0)}
{is_unprocessed.group(0)}
const actual = [
  {{ stage: "CUSTOM_STAGE", custom_stage: "waiting", deadline_at: null, deadline_override_at: null }},
  {{ stage: "CUSTOM_STAGE", custom_stage: "waiting", deadline_at: "", deadline_override_at: "" }},
  {{ stage: "CUSTOM_STAGE", custom_stage: "", deadline_at: "", deadline_override_at: "" }},
  {{ stage: "CUSTOM_STAGE", custom_stage: "waiting", deadline_at: "2026-09-09T18:00:00+08:00", deadline_override_at: null }},
].map(isUnprocessed);
if (JSON.stringify(actual) !== JSON.stringify([true, true, true, false])) process.exit(1);
"""
    result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_dashboard_uses_freeform_stage_editor_and_removes_erp_stage_filters(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text

    assert 'id="stage-filter"' not in index
    assert 'id="erp-filter"' not in index
    assert 'id="detail-status-input"' in index
    assert 'id="save-custom-stage"' in index
    assert "stageOptions" not in app_script
    assert "stage-select" not in app_script
    assert "custom-stage-input" in app_script


def test_overdue_summary_filter_clears_conflicting_risk_filter(tmp_path):
    client = make_client(tmp_path)
    app_script = client.get("/app.js").text
    apply_summary = re.search(r"function applySummaryFilter\(filter\) \{[^}]+\}", app_script)
    assert apply_summary

    node_script = f"""
const state = {{ summaryFilter: "", selected: new Set() }};
const controls = {{ "#risk-filter": {{ value: "NORMAL" }} }};
function $(selector) {{ return controls[selector]; }}
function renderSummary() {{}}
function renderTable() {{}}
{apply_summary.group(0)}
applySummaryFilter("OVERDUE");
if (controls["#risk-filter"].value !== "") {{
  console.error(controls["#risk-filter"].value);
  process.exit(1);
}}
"""
    result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_saving_custom_stage_marks_order_as_in_progress(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"custom_stage": "等待供应商补货"},
    )

    assert response.status_code == 200
    order = response.json()["order"]
    assert order["stage"] == "CUSTOM_STAGE"
    assert order["custom_stage"] == "等待供应商补货"


def test_detail_status_can_mark_order_completed(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"stage": "COMPLETED", "custom_stage": None},
    )

    assert response.status_code == 200
    order = response.json()["order"]
    assert order["stage"] == "COMPLETED"
    assert order["completed_at"] is not None


def test_dashboard_uses_processing_deadline_for_today_count_and_hides_arrival_fields(tmp_path):
    client = make_client(tmp_path)

    index = client.get("/").text
    app_script = client.get("/app.js").text
    styles = client.get("/styles.css").text

    assert 'id="summary-missing"' not in index
    assert "最晚发货</th>" not in index
    assert "最晚到货</th>" not in index
    assert 'id="arrival-input"' not in index
    assert 'dateKey(order.deadline_override_at || order.deadline_at) === shanghaiToday()' in app_script
    assert 'order.reminder_mode === "TODAY"' not in app_script.split("function dueToday", 1)[1].split("function isOverdue", 1)[0]
    assert "18:00" in app_script
    assert "11:00" in app_script
    assert "T00:00:00Z" in app_script
    assert 'reminder-mode-input").addEventListener("change"' in app_script
    assert "latest_arrival_at) === today" not in app_script
    assert "custom_stage" in app_script
    assert "自定义处理阶段" in app_script
    assert "save-custom-stage" in app_script
    assert 'id="summary-total"' in index
    assert 'id="summary-today"' in index
    assert 'id="summary-overdue"' in index
    assert "summary-filter" in app_script
    assert 'href="/styles.css?v=20260909-3"' in index
    assert 'src="/app.js?v=20260909-3"' in index
    assert ".drawer-body > .detail-section:has(#tracking-input)" in styles
    assert ".drawer-body > .detail-section:has(#exception-result)" in styles
    assert ".drawer-body > .detail-section:has(#detail-platform)" in styles

    due_today = re.search(r"function dueToday\(order\) \{[^}]+\}", app_script)
    assert due_today
    orders = [
        {"stage": "VIRTUAL_PENDING_RETURN", "reminder_mode": "TODAY", "deadline_at": "2026-09-04T18:00:00+08:00"},
        {"stage": "CUSTOM_STAGE", "reminder_mode": "TOMORROW", "deadline_at": "2026-09-04T11:00:00+08:00"},
        {"stage": "DROPSHIP_PENDING_RETURN", "reminder_mode": "TOMORROW", "deadline_at": "2026-09-05T11:00:00+08:00"},
        {"stage": "COMPLETED", "reminder_mode": "TODAY", "deadline_at": "2026-09-04T18:00:00+08:00"},
    ]
    node_script = f"""
const terminalStages = new Set(["OVERSELL_CUSTOMER_REFUNDED", "COMPLETED"]);
function isUnfinished(order) {{ return !terminalStages.has(order.stage); }}
function dateKey(value) {{ return value ? String(value).slice(0, 10) : ""; }}
function shanghaiToday() {{ return "2026-09-04"; }}
{due_today.group(0)}
const actual = {json.dumps(orders)}.map(dueToday);
const expected = [true, true, false, false];
if (JSON.stringify(actual) !== JSON.stringify(expected)) {{
  console.error(JSON.stringify({{ actual, expected }}));
  process.exit(1);
}}
"""
    result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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


def test_order_can_save_quick_processing_mode_without_arrival_date(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"reminder_mode": "TODAY", "process_date": "2026-09-02"},
    )

    assert response.status_code == 200
    order = response.json()["order"]
    assert order["reminder_mode"] == "TODAY"
    assert order["deadline_at"] == "2026-09-02T18:00:00+08:00"
    assert order["latest_arrival_at"] is None


def test_quick_processing_defaults_to_six_pm_and_accepts_manual_deadline(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"reminder_mode": "TODAY", "process_date": "2026-09-02"},
    )

    assert response.status_code == 200
    assert response.json()["order"]["deadline_at"] == "2026-09-02T18:00:00+08:00"

    response = client.patch(
        "/api/orders/ORDER-1",
        json={
            "reminder_mode": "TODAY",
            "process_date": "2026-09-02",
            "deadline_override_at": "2026-09-02 10:30",
        },
    )

    assert response.status_code == 200
    assert response.json()["order"]["deadline_at"] == "2026-09-02T10:30:00+08:00"


def test_tomorrow_processing_defaults_to_next_day_eleven_am(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"reminder_mode": "TOMORROW", "process_date": "2026-09-02"},
    )

    assert response.status_code == 200
    assert response.json()["order"]["deadline_at"] == "2026-09-02T11:00:00+08:00"


def test_existing_quick_orders_with_old_default_time_are_migrated_to_six_pm(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    client.patch("/api/orders/ORDER-1", json={"reminder_mode": "TODAY", "process_date": "2026-09-02"})

    connection = connect_database(client.app.state.db_path)
    connection.execute(
        "UPDATE orders SET deadline_at = ?, deadline_override_at = ? WHERE order_no = ?",
        ("2026-09-02T23:59:00+08:00", "2026-09-02T23:59:00+08:00", "ORDER-1"),
    )
    connection.commit()
    connection.close()

    reopened = make_client(tmp_path)

    assert reopened.get("/api/orders/ORDER-1").json()["order"]["deadline_at"] == "2026-09-02T18:00:00+08:00"


def test_existing_tomorrow_orders_with_old_default_time_are_migrated_to_eleven_am(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    client.patch("/api/orders/ORDER-1", json={"reminder_mode": "TOMORROW", "process_date": "2026-09-02"})

    connection = connect_database(client.app.state.db_path)
    connection.execute(
        "UPDATE orders SET deadline_at = ?, deadline_override_at = ? WHERE order_no = ?",
        ("2026-09-02T23:59:00+08:00", "2026-09-02T23:59:00+08:00", "ORDER-1"),
    )
    connection.commit()
    connection.close()

    reopened = make_client(tmp_path)

    assert reopened.get("/api/orders/ORDER-1").json()["order"]["deadline_at"] == "2026-09-02T11:00:00+08:00"


def test_long_term_reminder_only_needs_a_manual_deadline(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"reminder_mode": "LONG_TERM", "deadline_override_at": "2026-09-20 09:15"},
    )

    assert response.status_code == 200
    order = response.json()["order"]
    assert order["deadline_at"] == "2026-09-20T09:15:00+08:00"
    assert order["deadline_rule"] == "MANUAL"
    assert order["latest_arrival_at"] is None


def test_order_can_save_custom_processing_status(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"custom_stage": "等待供应商补货，周五再次确认"},
    )

    assert response.status_code == 200
    assert response.json()["order"]["custom_stage"] == "等待供应商补货，周五再次确认"


def test_custom_stage_option_can_be_saved_as_the_current_stage(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))

    response = client.patch(
        "/api/orders/ORDER-1",
        json={"stage": "CUSTOM_STAGE", "custom_stage": "等待供应商确认库存"},
    )

    assert response.status_code == 200
    order = response.json()["order"]
    assert order["stage"] == "CUSTOM_STAGE"
    assert order["custom_stage"] == "等待供应商确认库存"


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
    assert client.patch("/api/orders/ORDER-1", json={"deadline_override_at": "2026-09-05 10:00"}).status_code == 200
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


def test_order_risk_returns_to_normal_after_deadline_is_moved_to_the_future(tmp_path):
    client = make_client(tmp_path)
    import_order(client, make_workbook(tmp_path))
    assert client.patch("/api/orders/ORDER-1", json={"deadline_override_at": "2026-08-27 10:00"}).status_code == 200
    assert client.post("/api/reminders/check").status_code == 200
    assert client.get("/api/orders/ORDER-1").json()["order"]["risk"] == "OVERDUE"

    response = client.patch("/api/orders/ORDER-1", json={"deadline_override_at": "2026-09-20 10:00"})

    assert response.status_code == 200
    assert response.json()["order"]["risk"] == "NORMAL"
    assert client.get("/api/orders/ORDER-1").json()["order"]["risk"] == "NORMAL"


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
