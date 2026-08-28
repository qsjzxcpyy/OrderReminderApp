from datetime import datetime, time, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import DeadlineResult


try:
    LOCAL_TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    # Asia/Shanghai has no daylight-saving transitions; this keeps local runs
    # usable when the host Python distribution omits the IANA tzdata package.
    LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _local(value: datetime) -> datetime:
    return value.astimezone(LOCAL_TZ)


def calculate_deadline(
    latest_ship_at: datetime,
    latest_arrival_at: datetime | None,
    compensation_days: int,
) -> DeadlineResult:
    """Calculate the processing deadline using local calendar arithmetic."""
    if compensation_days not in (2, 3):
        raise ValueError("compensation_days must be exactly 2 or 3")
    _require_aware(latest_ship_at, "latest_ship_at")
    if latest_arrival_at is not None:
        _require_aware(latest_arrival_at, "latest_arrival_at")

    ship = _local(latest_ship_at)
    if latest_arrival_at is None:
        return DeadlineResult(deadline_at=None, rule="NONE", issue="MISSING_ARRIVAL")

    arrival = _local(latest_arrival_at)
    arrival_minus_four = arrival - timedelta(days=4)
    if arrival_minus_four >= ship:
        return DeadlineResult(
            deadline_at=arrival_minus_four,
            rule="ARRIVAL_MINUS_4",
            issue="NONE",
        )

    compensated = ship + timedelta(days=compensation_days)
    if compensated > arrival:
        return DeadlineResult(deadline_at=None, rule="NONE", issue="DATE_CONFLICT")

    rule = "SHIP_PLUS_2" if compensation_days == 2 else "SHIP_PLUS_3"
    return DeadlineResult(deadline_at=compensated, rule=rule, issue="NONE")


def _at_ten_am(calendar_date) -> datetime:
    return datetime.combine(calendar_date, time(10, 0), tzinfo=LOCAL_TZ)


def build_reminder_schedule(
    deadline_at: datetime,
    arrival_at: datetime,
) -> Mapping[str, datetime]:
    """Build the three local 10:00 reminders for a valid deadline."""
    _require_aware(deadline_at, "deadline_at")
    _require_aware(arrival_at, "arrival_at")
    deadline = _local(deadline_at)
    arrival = _local(arrival_at)

    return {
        "PROCESS_DAY": _at_ten_am(deadline.date()),
        "ARRIVAL_EVE": _at_ten_am(arrival.date() - timedelta(days=1)),
        "ARRIVAL_DAY": _at_ten_am(arrival.date()),
    }
