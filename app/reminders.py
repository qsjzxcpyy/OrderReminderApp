"""Idempotent reminder scheduling and delivery coordination."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from .config import APP_HOST, APP_PORT
from .date_rules import LOCAL_TZ, build_process_day_schedule, build_reminder_schedule
from .workflow import ALL_STAGES, CUSTOM_STAGE, LEGACY_STAGE_MAP, LONG_TERM, QUICK_REMINDER_MODES, TERMINAL_STAGES, UNSCHEDULED


COMPLETED = "COMPLETED"
PROCESS_STAGES = (set(ALL_STAGES) - TERMINAL_STAGES) | {CUSTOM_STAGE, "VIRTUAL_PENDING", "WAITING_EXCEPTION"}


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=LOCAL_TZ) if parsed.tzinfo is None else parsed.astimezone(LOCAL_TZ)


def _local_now(now: datetime | None) -> datetime:
    value = now or datetime.now(LOCAL_TZ)
    return value.replace(tzinfo=LOCAL_TZ) if value.tzinfo is None else value.astimezone(LOCAL_TZ)


def _was_sent(event: Any, channel: str) -> bool:
    try:
        return event[f"{channel}_status"] == "SENT"
    except (KeyError, IndexError):
        return False


class ReminderService:
    def __init__(self, repository, windows, email, secret_provider: Callable[[], str | None]):
        self.repository = repository
        self.windows = windows
        self.email = email
        self.secret_provider = secret_provider
        self._late_process_day_reminders: dict[int, tuple[datetime, datetime]] = {}

    def _settings(self) -> dict[str, Any]:
        return {
            "windows_notifications_enabled": True,
            "email_enabled": False,
            "recipients": [],
            **self.repository.get_settings(),
        }

    @staticmethod
    def _schedule_key(event_type: str, scheduled_at: datetime, deadline: datetime, arrival: datetime | None, override: Any) -> str:
        return "|".join((event_type, scheduled_at.isoformat(), deadline.isoformat(), arrival.isoformat() if arrival else "", str(override or "")))

    def _create_or_retry(self, order: dict[str, Any], event_type: str, scheduled_at: datetime, deadline: datetime, arrival: datetime | None) -> tuple[int, dict[str, Any]] | None:
        schedule_key = self._schedule_key(event_type, scheduled_at, deadline, arrival, order.get("deadline_override_at"))
        event_id = self.repository.insert_reminder_event({
            "order_id": order["id"],
            "event_type": event_type,
            "schedule_key": schedule_key,
            "scheduled_at": scheduled_at.isoformat(),
        })
        if event_id is None:
            existing = self.repository.find_pending_reminder_event(order["id"], event_type, schedule_key)
            if existing is None:
                return None
            event_id = existing["id"]
        return event_id, order

    @staticmethod
    def _digest_body(orders: list[dict[str, Any]]) -> str:
        names = ", ".join(str(order.get("order_no", "")) for order in orders[:5])
        suffix = "" if len(orders) <= 5 else f" and {len(orders) - 5} more"
        return f"{len(orders)} order(s) require attention: {names}{suffix}"

    def _deliver_bucket(self, event_type: str, items: list[tuple[int, dict[str, Any]]], settings: dict[str, Any]) -> int:
        claimed = [(event_id, order, self.repository.claim_pending_event(event_id)) for event_id, order in items]
        claimed = [(event_id, order, event) for event_id, order, event in claimed if event is not None]
        if not claimed:
            return 0

        channel_statuses: dict[int, dict[str, str]] = {event_id: {} for event_id, _, _ in claimed}
        title = f"Order reminder: {event_type}"
        url = f"http://{APP_HOST}:{APP_PORT}/"
        for channel, enabled in (("windows", settings.get("windows_notifications_enabled")), ("email", settings.get("email_enabled"))):
            needed = [(event_id, order, event) for event_id, order, event in claimed if not _was_sent(event, channel)]
            if not enabled:
                result = {"status": "SKIPPED", "message": f"{channel.title()} reminders are disabled"}
            elif not needed:
                result = None
            elif channel == "windows":
                try:
                    result = self.windows.send(title, self._digest_body([order for _, order, _ in needed]), url)
                except Exception as error:
                    result = {"status": "FAILED", "message": f"Windows delivery failed: {type(error).__name__}"}
            else:
                try:
                    result = self.email.send_digest(event_type, [order for _, order, _ in needed], settings, self.secret_provider())
                except Exception as error:
                    result = {"status": "FAILED", "message": f"Email delivery failed: {type(error).__name__}"}
            for event_id, _, event in claimed:
                if _was_sent(event, channel):
                    channel_statuses[event_id][channel] = "SENT"
                elif result is None:
                    channel_statuses[event_id][channel] = "SENT"
                else:
                    recorded = {**result, "channel": channel}
                    self.repository.mark_channel_result(event_id, channel, recorded)
                    channel_statuses[event_id][channel] = recorded["status"]

        enabled_channels = [channel for channel, enabled in (("windows", settings.get("windows_notifications_enabled")), ("email", settings.get("email_enabled"))) if enabled]
        for event_id, _, _ in claimed:
            statuses = channel_statuses[event_id]
            if not enabled_channels:
                status = "SKIPPED"
            elif all(statuses.get(channel) == "SENT" for channel in enabled_channels):
                status = "SENT"
            else:
                status = "PENDING"
            self.repository.set_reminder_event_status(event_id, status)
        return len(claimed)

    def check(self, now: datetime | None = None) -> dict[str, Any]:
        current = _local_now(now)
        settings = self._settings()
        created = sent = 0
        overdue: list[str] = []
        buckets: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = {}
        for source in self.repository.list_orders({}):
            order = dict(source)
            if order.get("stage") in TERMINAL_STAGES:
                continue
            mode = order.get("reminder_mode") or (LONG_TERM if order.get("latest_arrival_at") else UNSCHEDULED)
            if mode == UNSCHEDULED:
                continue
            deadline = _parse(order.get("deadline_override_at") or order.get("deadline_at"))
            arrival = _parse(order.get("latest_arrival_at"))
            if deadline is None:
                continue
            schedule = build_process_day_schedule(deadline) if mode in QUICK_REMINDER_MODES else build_reminder_schedule(deadline)
            process_day_handled = False
            process_day_scheduled_at = schedule.get("PROCESS_DAY")
            for event_type, scheduled_at in schedule.items():
                if scheduled_at > current:
                    continue
                if event_type == "PROCESS_DAY" and order.get("stage") not in PROCESS_STAGES:
                    continue
                item = self._create_or_retry(order, event_type, scheduled_at, deadline, arrival)
                if event_type == "PROCESS_DAY":
                    process_day_handled = item is not None
                if item is not None:
                    created += 1
                    buckets.setdefault((event_type, scheduled_at.date().isoformat(), mode), []).append(item)
            if process_day_handled and process_day_scheduled_at is not None and current > process_day_scheduled_at:
                self._late_process_day_reminders[order["id"]] = (deadline, current)
                self.repository.mark_overdue(order["id"], current.isoformat())
            late_process_day = self._late_process_day_reminders.get(order["id"])
            if late_process_day is not None and late_process_day[0] != deadline:
                self._late_process_day_reminders.pop(order["id"], None)
                late_process_day = None
            if current > deadline or (current == deadline and order.get("stage") not in PROCESS_STAGES):
                elapsed_minutes = int((current - deadline).total_seconds() // 600)
                overdue_scheduled_at = deadline + timedelta(minutes=elapsed_minutes * 10)
                if not process_day_handled and not (overdue_scheduled_at == deadline and order.get("stage") in PROCESS_STAGES) and not (late_process_day is not None and overdue_scheduled_at <= late_process_day[1]):
                    overdue_key = self._schedule_key("OVERDUE", overdue_scheduled_at, deadline, arrival, order.get("deadline_override_at"))
                    event_id = self.repository.insert_reminder_event({
                        "order_id": order["id"], "event_type": "OVERDUE", "schedule_key": overdue_key,
                        "scheduled_at": overdue_scheduled_at.isoformat(),
                    })
                    is_new = event_id is not None
                    if event_id is None:
                        existing = self.repository.find_pending_reminder_event(order["id"], "OVERDUE", overdue_key)
                        event_id = existing["id"] if existing is not None else None
                    if event_id is not None:
                        self.repository.mark_overdue(order["id"], current.isoformat())
                        created += int(is_new)
                        buckets.setdefault(("OVERDUE", overdue_scheduled_at.date().isoformat(), mode), []).append((event_id, order))
                overdue.append(str(order.get("order_no")))
        for (event_type, _schedule_date, mode), items in buckets.items():
            bucket_settings = {**settings, "email_enabled": False} if event_type == "OVERDUE" or mode in QUICK_REMINDER_MODES else settings
            sent += self._deliver_bucket(event_type, items, bucket_settings)
        return {"created": created, "sent": sent, "overdue": sorted(set(overdue))}
