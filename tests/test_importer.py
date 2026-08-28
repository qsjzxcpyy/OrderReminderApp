from datetime import datetime, timezone
import hashlib
import json
from unittest.mock import Mock, patch
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from app.importer import _parse_datetime, import_workbook
from app.date_rules import LOCAL_TZ


HEADERS = [
    "refrence_no", "platform_user_name", "platform_seller_id", "order_status",
    "date_latest_ship", "tracking_number", "mark_content", "markding_time",
    "system_note", "operator_note", "abnormal_reason", "consignee_name",
    "consignee_country_name", "consignee_email", "warehouse_name",
    "shipping_method_name_cn", "shipping_method_name_en",
]


def make_book(tmp_path, rows, sheet="Export orders", headers=HEADERS):
    path = tmp_path / "orders.xlsx"
    book = openpyxl.Workbook()
    sheet_obj = book.active
    sheet_obj.title = sheet
    sheet_obj.append(headers)
    for row in rows:
        sheet_obj.append(row)
    book.save(path)
    return path


def row(order_no="A-1", latest_ship="2026-09-01 14:59", tracking=""):
    values = {header: "" for header in HEADERS}
    values.update(refrence_no=order_no, date_latest_ship=latest_ship, tracking_number=tracking)
    return [values[header] for header in HEADERS]


def test_import_normalizes_dates_and_keeps_json_safe_snapshot(tmp_path):
    values = row()
    values[HEADERS.index("markding_time")] = "2026-09-01 15:00:00"
    result = import_workbook(make_book(tmp_path, [values]))
    imported = result.rows[0]
    assert imported["refrence_no"] == "A-1"
    assert imported["date_latest_ship"] == datetime(2026, 9, 1, 14, 59, tzinfo=LOCAL_TZ)
    snapshot_columns = imported["latest_erp_snapshot_json"]["columns"]
    assert snapshot_columns[HEADERS.index("date_latest_ship")] == {
        "header": "date_latest_ship",
        "value": "2026-09-01 14:59",
    }
    assert imported["markding_time"] == datetime(2026, 9, 1, 15, 0, tzinfo=LOCAL_TZ)
    json.dumps(imported["latest_erp_snapshot_json"])


def test_import_parses_minute_dates_and_zero_date_as_missing(tmp_path):
    values = row(latest_ship="2026-09-02 14:59")
    values[HEADERS.index("markding_time")] = "0000-00-00 00:00:00"
    imported = import_workbook(make_book(tmp_path, [values])).rows[0]
    assert imported["date_latest_ship"] == datetime(2026, 9, 2, 14, 59, tzinfo=LOCAL_TZ)
    assert imported["markding_time"] is None


def test_import_normalizes_all_selected_fields(tmp_path):
    values = row()
    for index, field in enumerate(HEADERS):
        if field not in {"refrence_no", "date_latest_ship", "markding_time"}:
            values[index] = f"value-{field}"
    imported = import_workbook(make_book(tmp_path, [values])).rows[0]
    for field in HEADERS:
        if field not in {"date_latest_ship", "markding_time"}:
            expected = "A-1" if field == "refrence_no" else f"value-{field}"
            assert imported[field] == expected


def test_parse_timezone_aware_datetime_into_local_timezone():
    parsed = _parse_datetime(datetime(2026, 9, 3, 14, 59, tzinfo=timezone.utc), "date_latest_ship")
    assert parsed == datetime(2026, 9, 3, 22, 59, tzinfo=LOCAL_TZ)


def test_import_opens_workbook_read_only_with_cached_values(tmp_path):
    fake_sheet = Mock()
    fake_sheet.iter_rows.return_value = iter([tuple(HEADERS), tuple(row())])
    fake_book = Mock(sheetnames=["Export orders"])
    fake_book.__getitem__ = Mock(return_value=fake_sheet)
    with patch("openpyxl.load_workbook", return_value=fake_book) as loader:
        import_workbook(tmp_path / "orders.xlsx")
    loader.assert_called_once_with(tmp_path / "orders.xlsx", read_only=True, data_only=True)


def test_import_uses_last_duplicate_and_reports_count(tmp_path):
    result = import_workbook(make_book(tmp_path, [row(tracking="OLD"), row(tracking="NEW")]))
    assert len(result.rows) == 1
    assert result.rows[0]["tracking_number"] == "NEW"
    assert result.duplicates == 1
    assert result.added is None and result.updated is None


def test_snapshot_preserves_duplicate_headers_in_source_order(tmp_path):
    headers = ["refrence_no", "XOCN_US", "XOCN_US", "date_latest_ship"]
    values = ["A-1", "first-value", "second-value", "2026-09-01 14:59"]
    imported = import_workbook(make_book(tmp_path, [values], headers=headers)).rows[0]
    assert imported["latest_erp_snapshot_json"] == {
        "columns": [
            {"header": "refrence_no", "value": "A-1"},
            {"header": "XOCN_US", "value": "first-value"},
            {"header": "XOCN_US", "value": "second-value"},
            {"header": "date_latest_ship", "value": "2026-09-01 14:59"},
        ]
    }


def test_import_rejects_missing_sheet(tmp_path):
    with pytest.raises(ValueError, match="Export orders"):
        import_workbook(make_book(tmp_path, [row()], sheet="Orders"))


def test_import_rejects_missing_order_column(tmp_path):
    with pytest.raises(ValueError, match="refrence_no"):
        import_workbook(make_book(tmp_path, [row()], headers=["order_status"]))


def test_import_reports_malformed_date_and_blank_order(tmp_path):
    result = import_workbook(make_book(tmp_path, [row(order_no="", latest_ship="2026/09/01"), row(latest_ship="not-a-date")]))
    assert result.rows == []
    assert result.skipped == 2
    assert len(result.invalid_rows) == 2
    assert "order number" in result.invalid_rows[0]["reason"]
    assert "date_latest_ship" in result.invalid_rows[1]["reason"]


def test_real_erp_fixture_is_read_only_and_unchanged():
    fixture = Path(__file__).parents[2] / "order-1787727280.xlsx"
    if not fixture.exists():
        pytest.skip("ERP fixture is not present")
    before = hashlib.sha256(fixture.read_bytes()).hexdigest()
    result = import_workbook(fixture)
    after = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert len(result.rows) == 62
    assert result.duplicates == 0
    assert before == after
