import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .date_rules import LOCAL_TZ, calculate_deadline
from .db import connect_database
from .repositories import ERP_COLUMNS, Repository, now_iso
from .workflow import ALL_STAGES, COMPLETED, LEGACY_STAGE_MAP, complete_exception, confirm_return


ALLOWED_STAGES = set(ALL_STAGES) | set(LEGACY_STAGE_MAP)


class SecretStore:
    """Task 6 will replace this process-local, non-persistent secret adapter."""

    def __init__(self):
        self._secrets: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        if value:
            self._secrets[name] = value

    def exists(self, name: str) -> bool:
        return bool(self._secrets.get(name))


def parse_local_datetime(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must use YYYY-MM-DD HH:mm")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError as error:
        raise ValueError(f"{field} must use YYYY-MM-DD HH:mm") from error
    return parsed.replace(tzinfo=LOCAL_TZ)


def _stored_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=LOCAL_TZ) if parsed.tzinfo is None else parsed.astimezone(LOCAL_TZ)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


class OrderService:
    def __init__(self, db_path: str | Path, secrets: SecretStore):
        self.connection = connect_database(db_path)
        self.repository = Repository(self.connection)
        self.secrets = secrets

    def close(self) -> None:
        self.connection.close()

    def _order_dict(self, order) -> dict[str, Any]:
        result = dict(order)
        issue = result.get("deadline_issue")
        if result.get("overdue_at"):
            result["risk"] = "OVERDUE"
        else:
            result["risk"] = "OVERDUE_RISK" if issue in {"MISSING_ARRIVAL", "DATE_CONFLICT"} else "NORMAL"
        result["next_reminder"] = None
        return result

    def _require_order(self, order_no: str):
        order = self.repository.get_order(order_no)
        if order is None:
            raise LookupError("order not found")
        return order

    def _apply_deadline(self, order_no: str, arrival: datetime | None, override: datetime | None = None):
        order = self._require_order(order_no)
        if override is not None:
            if arrival is None:
                raise ValueError("latest_arrival_at is required for a manual deadline")
            if override > arrival:
                raise ValueError("manual deadline cannot be later than latest arrival")
            return self.repository.update_human_fields(order_no, {
                "latest_arrival_at": arrival.isoformat(), "deadline_override_at": override.isoformat(),
                "deadline_at": override.isoformat(), "deadline_rule": "MANUAL", "deadline_issue": "NONE",
            })
        ship = _stored_datetime(order["latest_ship_at"])
        if ship is None:
            raise ValueError("latest ship time is required to calculate deadline")
        calculated = calculate_deadline(ship, arrival, int(self.get_settings()["compensation_days"]))
        patch = {
            "latest_arrival_at": arrival.isoformat() if arrival else None,
            "deadline_override_at": None,
            "deadline_at": calculated.deadline_at.isoformat() if calculated.deadline_at else None,
            "deadline_rule": calculated.rule,
            "deadline_issue": calculated.issue,
        }
        return self.repository.update_human_fields(order_no, patch)

    def import_rows(self, rows: list[dict], source_name: str, summary: dict) -> dict:
        added = updated = 0
        with self.connection:
            for row in rows:
                order_no = row["refrence_no"]
                current = self.repository.get_order(order_no)
                values = {
                    "platform_user_name": row.get("platform_user_name"), "platform_seller_id": row.get("platform_seller_id"),
                    "erp_status": row.get("order_status"), "latest_ship_at": _json_value(row.get("date_latest_ship")),
                    "erp_tracking_number": row.get("tracking_number"), "mark_content": row.get("mark_content"),
                    "marking_time": _json_value(row.get("markding_time")), "system_note": row.get("system_note"),
                    "operator_note": row.get("operator_note"), "abnormal_reason": row.get("abnormal_reason"),
                    "consignee_name": row.get("consignee_name"), "consignee_country_name": row.get("consignee_country_name"),
                    "consignee_email": row.get("consignee_email"), "warehouse_name": row.get("warehouse_name"),
                    "shipping_method_name_cn": row.get("shipping_method_name_cn"), "shipping_method_name_en": row.get("shipping_method_name_en"),
                    "latest_erp_snapshot_json": json.dumps(_json_value(row["latest_erp_snapshot_json"]), ensure_ascii=False), "last_imported_at": now_iso(),
                }
                if current:
                    assignments = ", ".join(f"{field} = ?" for field in ERP_COLUMNS)
                    self.connection.execute(f"UPDATE orders SET {assignments}, updated_at = ? WHERE order_no = ?", [values[field] for field in ERP_COLUMNS] + [now_iso(), order_no])
                    updated += 1
                else:
                    columns = ["order_no", *sorted(ERP_COLUMNS), "stage_suggestion", "deadline_rule", "deadline_issue", "stage", "created_at", "updated_at"]
                    timestamp = now_iso()
                    params = [order_no, *[values[field] for field in sorted(ERP_COLUMNS)], row["stage_suggestion"], "NONE", "MISSING_ARRIVAL", row["stage_suggestion"], timestamp, timestamp]
                    self.connection.execute(f"INSERT INTO orders ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", params)
                    added += 1
            result = {**summary, "added": added, "updated": updated}
            self.connection.execute("INSERT INTO imports (source_name, imported_at, row_count, result_json) VALUES (?, ?, ?, ?)", (source_name, now_iso(), len(rows), json.dumps(result)))
        self.repository.append_activity(None, "IMPORT", result)
        return result

    def list_orders(self, filters: dict, limit: int) -> list[dict]:
        clauses, params = [], []
        for key in ("stage", "deadline_issue", "erp_status"):
            if filters.get(key):
                if key == "stage":
                    filters[key] = LEGACY_STAGE_MAP.get(filters[key], filters[key])
                clauses.append(f"{key} = ?")
                params.append(filters[key])
        if filters.get("search"):
            clauses.append("(order_no LIKE ? OR platform_user_name LIKE ? OR consignee_name LIKE ? OR operator_note LIKE ?)")
            params.extend([f"%{filters['search']}%"] * 4)
        sql = "SELECT * FROM orders" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY deadline_at IS NULL, deadline_at, id LIMIT ?"
        return [self._order_dict(row) for row in self.connection.execute(sql, [*params, limit]).fetchall()]

    def delete_orders(self, order_nos: list[str]) -> dict[str, Any]:
        requested = list(dict.fromkeys(order_no.strip() for order_no in order_nos if order_no.strip()))
        deleted = self.repository.delete_orders(requested)
        not_found = [order_no for order_no in requested if order_no not in deleted]
        if deleted:
            self.repository.append_activity(None, "ORDER_DELETED", {"order_nos": deleted})
        return {"deleted": len(deleted), "not_found": not_found}

    def detail(self, order_no: str) -> dict:
        order = self._require_order(order_no)
        history = self.connection.execute("SELECT * FROM activity_log WHERE order_id = ? ORDER BY id DESC", (order["id"],)).fetchall()
        reminders = self.repository.list_reminder_events(order["id"])
        return {"order": self._order_dict(order), "activity": [dict(row) for row in history], "reminders": [dict(row) for row in reminders]}

    def patch_order(self, order_no: str, patch: dict) -> dict:
        order = self._require_order(order_no)
        if "stage" in patch and patch["stage"] not in ALLOWED_STAGES:
            raise ValueError("invalid order stage")
        if "stage" in patch:
            patch["stage"] = LEGACY_STAGE_MAP.get(patch["stage"], patch["stage"])
        arrival_changed = "latest_arrival_at" in patch
        override_changed = "deadline_override_at" in patch
        raw_arrival = patch.pop("latest_arrival_at", None)
        raw_override = patch.pop("deadline_override_at", None)
        arrival = parse_local_datetime(raw_arrival, "latest_arrival_at") if arrival_changed else _stored_datetime(order["latest_arrival_at"])
        override = parse_local_datetime(raw_override, "deadline_override_at") if override_changed and raw_override is not None else None
        if arrival_changed or override_changed:
            if override is not None:
                self._apply_deadline(order_no, arrival, override)
            else:
                self._apply_deadline(order_no, arrival)
        if patch:
            self.repository.update_human_fields(order_no, patch)
        self.repository.append_activity(order_no, "ORDER_UPDATED", {"fields": sorted(patch)})
        return self._order_dict(self._require_order(order_no))

    def save_tracking(self, order_no: str, tracking: str) -> dict:
        self._require_order(order_no)
        if not tracking.strip():
            raise ValueError("actual tracking number is required")
        self.repository.update_human_fields(order_no, {"actual_tracking_number": tracking.strip(), "return_confirmed_at": None})
        self.repository.append_activity(order_no, "TRACKING_SAVED")
        return self._order_dict(self._require_order(order_no))

    def confirm_return(self, order_no: str) -> dict:
        order = self._require_order(order_no)
        try:
            result = confirm_return(order["actual_tracking_number"] or "")
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        self.repository.update_human_fields(order_no, {"stage": result["stage"], "return_confirmed_at": now_iso()})
        self.repository.append_activity(order_no, "RETURN_CONFIRMED")
        return self._order_dict(self._require_order(order_no))

    def complete(self, order_no: str, processing_result: str, processing_note: str) -> dict:
        self._require_order(order_no)
        result = complete_exception(processing_result, processing_note)
        self.repository.update_human_fields(order_no, {**result, "completed_at": now_iso()})
        self.repository.append_activity(order_no, "EXCEPTION_COMPLETED")
        return self._order_dict(self._require_order(order_no))

    def get_settings(self) -> dict:
        stored = self.repository.get_settings()
        result = {"compensation_days": 2, "windows_notifications_enabled": True, "email_enabled": False, "smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_username": "", "recipients": [], "processing_results": [], **stored}
        result["smtp_secret_configured"] = self.secrets.exists("smtp_authorization_code")
        return result

    def update_settings(self, patch: dict) -> dict:
        secret = patch.pop("smtp_authorization_code", None)
        if secret is not None:
            if not self.secrets.set("smtp_authorization_code", secret):
                raise ValueError("授权码保存失败，请检查 Windows 凭据存储权限")
        self.repository.save_settings(patch)
        self.repository.append_activity(None, "SETTINGS_UPDATED", {"keys": sorted(patch)})
        return self.get_settings()
