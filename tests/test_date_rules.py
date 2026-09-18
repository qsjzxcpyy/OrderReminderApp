from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from app.date_rules import build_reminder_schedule, calculate_deadline


try:
    LOCAL_TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def local_at(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)


ship_at = local_at
arrival_at = local_at


def test_arrival_minus_four_is_used_when_not_before_latest_ship():
    result = calculate_deadline(ship_at("2026-09-01 14:59"), arrival_at("2026-09-05 14:59"), 2)

    assert result.deadline_at == arrival_at("2026-09-01 14:59")
    assert result.rule == "ARRIVAL_MINUS_4"
    assert result.issue == "NONE"


def test_ship_plus_configured_days_is_used_when_arrival_minus_four_is_too_early():
    result = calculate_deadline(ship_at("2026-09-01 14:59"), arrival_at("2026-09-04 14:59"), 3)

    assert result.deadline_at == arrival_at("2026-09-04 14:59")
    assert result.rule == "SHIP_PLUS_3"
    assert result.issue == "NONE"


def test_ship_plus_two_branch_uses_exact_rule_and_issue():
    result = calculate_deadline(ship_at("2026-09-01 14:59"), arrival_at("2026-09-03 14:59"), 2)

    assert result.deadline_at == arrival_at("2026-09-03 14:59")
    assert result.rule == "SHIP_PLUS_2"
    assert result.issue == "NONE"


def test_compensation_after_arrival_is_a_conflict_without_deadline():
    result = calculate_deadline(ship_at("2026-09-08 14:59"), arrival_at("2026-09-10 14:59"), 3)

    assert result.deadline_at is None
    assert result.rule == "NONE"
    assert result.issue == "DATE_CONFLICT"


def test_missing_arrival_requires_manual_completion():
    result = calculate_deadline(ship_at("2026-09-01 14:59"), None, 2)

    assert result.deadline_at is None
    assert result.rule == "NONE"
    assert result.issue == "MISSING_ARRIVAL"


def test_reminders_use_processing_deadline_time_and_arrival_dates():
    schedule = build_reminder_schedule(
        deadline_at=arrival_at("2026-09-11 14:59"),
        arrival_at=arrival_at("2026-09-11 14:59"),
    )

    assert schedule == {
        "PROCESS_DAY": local_at("2026-09-11 14:59"),
        "ARRIVAL_EVE": local_at("2026-09-10 14:59"),
        "ARRIVAL_DAY": local_at("2026-09-11 14:59"),
    }


def test_equal_arrival_minus_four_and_ship_uses_arrival_rule():
    result = calculate_deadline(ship_at("2026-09-01 14:59"), arrival_at("2026-09-05 14:59"), 3)

    assert result.rule == "ARRIVAL_MINUS_4"


def test_manual_deadline_can_build_a_schedule():
    schedule = build_reminder_schedule(
        deadline_at=local_at("2026-09-20 08:30"),
        arrival_at=local_at("2026-09-25 14:59"),
    )

    assert schedule["PROCESS_DAY"] == local_at("2026-09-20 08:30")


def test_process_day_schedule_accepts_a_quick_order_without_arrival():
    from app.date_rules import build_process_day_schedule

    schedule = build_process_day_schedule(local_at("2026-09-02 23:59"))

    assert schedule == {"PROCESS_DAY": local_at("2026-09-02 23:59")}


def test_missing_deadline_is_rejected_for_schedule():
    with pytest.raises(ValueError, match="deadline_at"):
        build_reminder_schedule(deadline_at=None, arrival_at=arrival_at("2026-09-25 14:59"))


def test_invalid_compensation_days_are_rejected():
    with pytest.raises(ValueError, match="2 or 3"):
        calculate_deadline(ship_at("2026-09-01 14:59"), arrival_at("2026-09-05 14:59"), 4)


def test_naive_ship_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_deadline(datetime(2026, 9, 1, 14, 59), arrival_at("2026-09-05 14:59"), 2)


def test_naive_arrival_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_deadline(ship_at("2026-09-01 14:59"), datetime(2026, 9, 5, 14, 59), 2)


def test_none_deadline_is_rejected_with_value_error():
    with pytest.raises(ValueError, match="deadline_at"):
        build_reminder_schedule(None, arrival_at("2026-09-25 14:59"))


def test_deadline_arithmetic_counts_weekends_as_calendar_days():
    result = calculate_deadline(ship_at("2026-09-08 14:59"), arrival_at("2026-09-13 14:59"), 2)

    assert result.deadline_at == arrival_at("2026-09-09 14:59")
    assert result.rule == "ARRIVAL_MINUS_4"
