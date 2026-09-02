import json
import sqlite3
from datetime import datetime, timezone, timedelta


CHINA_TZ = timezone(timedelta(hours=8))
ERP_COLUMNS = {
    "platform_user_name", "platform_seller_id", "erp_status", "latest_ship_at",
    "erp_tracking_number", "mark_content", "marking_time", "system_note",
    "operator_note", "abnormal_reason", "consignee_name", "consignee_country_name",
    "consignee_email", "warehouse_name", "shipping_method_name_cn",
    "shipping_method_name_en", "latest_erp_snapshot_json", "last_imported_at",
}
HUMAN_COLUMNS = {
    "latest_arrival_at", "deadline_at", "deadline_override_at", "deadline_rule",
    "deadline_issue", "stage", "stage_suggestion", "actual_tracking_number",
    "return_confirmed_at", "processing_result", "processing_note", "completed_at",
}


def now_iso():
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


class Repository:
    def __init__(self, connection):
        self.connection = connection

    def get_order(self, order_no):
        return self.connection.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()

    def delete_orders(self, order_nos):
        unique_order_nos = list(dict.fromkeys(order_nos))
        if not unique_order_nos:
            return []
        placeholders = ", ".join("?" for _ in unique_order_nos)
        rows = self.connection.execute(f"SELECT order_no FROM orders WHERE order_no IN ({placeholders})", unique_order_nos).fetchall()
        deleted = [row["order_no"] for row in rows]
        if deleted:
            delete_placeholders = ", ".join("?" for _ in deleted)
            with self.connection:
                self.connection.execute(f"DELETE FROM orders WHERE order_no IN ({delete_placeholders})", deleted)
        return deleted

    def list_orders(self, filters=None):
        filters = filters or {}
        clauses, values = [], []
        for key in ("stage", "deadline_issue", "erp_status"):
            if filters.get(key) is not None:
                clauses.append(f"{key} = ?")
                values.append(filters[key])
        sql = "SELECT * FROM orders"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY deadline_at IS NULL, deadline_at, id"
        return self.connection.execute(sql, values).fetchall()

    def upsert_erp_snapshot(self, order_no, snapshot, imported_at):
        values = {column: snapshot.get(source, snapshot.get(column)) for column, source in {
            "platform_user_name": "platform_user_name", "platform_seller_id": "platform_seller_id",
            "erp_status": "order_status", "latest_ship_at": "date_latest_ship",
            "erp_tracking_number": "tracking_number", "mark_content": "mark_content",
            "marking_time": "markding_time", "system_note": "system_note",
            "operator_note": "operator_note", "abnormal_reason": "abnormal_reason",
            "consignee_name": "consignee_name", "consignee_country_name": "consignee_country_name",
            "consignee_email": "consignee_email", "warehouse_name": "warehouse_name",
            "shipping_method_name_cn": "shipping_method_name_cn", "shipping_method_name_en": "shipping_method_name_en",
        }.items()}
        values["latest_erp_snapshot_json"] = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        values["last_imported_at"] = imported_at
        current = self.get_order(order_no)
        timestamp = now_iso()
        with self.connection:
            if current:
                assignments = ", ".join(f"{column} = ?" for column in ERP_COLUMNS)
                params = [values[column] for column in ERP_COLUMNS] + [timestamp, order_no]
                self.connection.execute(f"UPDATE orders SET {assignments}, updated_at = ? WHERE order_no = ?", params)
            else:
                columns = ["order_no"] + sorted(ERP_COLUMNS) + ["deadline_rule", "deadline_issue", "stage", "created_at", "updated_at"]
                defaults = [order_no] + [values[column] for column in sorted(ERP_COLUMNS)] + ["NONE", "MISSING_ARRIVAL", "NEEDS_ARRIVAL_DATE", timestamp, timestamp]
                placeholders = ", ".join("?" for _ in columns)
                self.connection.execute(f"INSERT INTO orders ({', '.join(columns)}) VALUES ({placeholders})", defaults)
        return self.get_order(order_no)

    def update_human_fields(self, order_no, patch):
        updates = {key: value for key, value in patch.items() if key in HUMAN_COLUMNS}
        if not updates:
            return self.get_order(order_no)
        updates["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self.connection:
            self.connection.execute(f"UPDATE orders SET {assignments} WHERE order_no = ?", [*updates.values(), order_no])
        return self.get_order(order_no)

    def insert_reminder_event(self, event):
        timestamp = now_iso()
        try:
            with self.connection:
                cursor = self.connection.execute(
                    "INSERT INTO reminder_events (order_id, event_type, schedule_key, scheduled_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (event["order_id"], event["event_type"], event["schedule_key"], event["scheduled_at"], timestamp, timestamp),
                )
            return cursor.lastrowid
        except sqlite3.IntegrityError as error:
            if "UNIQUE constraint failed" in str(error):
                return None
            raise

    def get_reminder_event(self, event_id):
        return self.connection.execute("SELECT * FROM reminder_events WHERE id = ?", (event_id,)).fetchone()

    def list_reminder_events(self, order_id):
        return self.connection.execute(
            "SELECT * FROM reminder_events WHERE order_id = ? ORDER BY scheduled_at DESC, id DESC",
            (order_id,),
        ).fetchall()

    def find_pending_reminder_event(self, order_id, event_type, schedule_key):
        return self.connection.execute(
            "SELECT * FROM reminder_events WHERE order_id = ? AND event_type = ? AND schedule_key = ? AND status = 'PENDING'",
            (order_id, event_type, schedule_key),
        ).fetchone()

    def claim_pending_event(self, event_id):
        with self.connection:
            cursor = self.connection.execute("UPDATE reminder_events SET status = 'CLAIMED', claimed_at = ?, updated_at = ? WHERE id = ? AND status = 'PENDING'", (now_iso(), now_iso(), event_id))
        return self.get_reminder_event(event_id) if cursor.rowcount else None

    def mark_channel_result(self, event_id, channel, result):
        if channel not in ("windows", "email"):
            raise ValueError("channel must be windows or email")
        status = result.get("status", "success") if isinstance(result, dict) else "success"
        with self.connection:
            self.connection.execute(f"UPDATE reminder_events SET {channel}_status = ?, {channel}_result = ?, updated_at = ? WHERE id = ?", (status.upper(), json.dumps(result, ensure_ascii=False), now_iso(), event_id))
        return self.get_reminder_event(event_id)

    def set_reminder_event_status(self, event_id, status):
        with self.connection:
            self.connection.execute(
                "UPDATE reminder_events SET status = ?, updated_at = ? WHERE id = ?",
                (status, now_iso(), event_id),
            )
        return self.get_reminder_event(event_id)

    def mark_overdue(self, order_id, overdue_at):
        with self.connection:
            self.connection.execute(
                "UPDATE orders SET overdue_at = ?, updated_at = ? WHERE id = ?",
                (overdue_at, now_iso(), order_id),
            )

    def append_activity(self, order_no, action, details=None, actor=None):
        order = self.get_order(order_no) if order_no else None
        with self.connection:
            cursor = self.connection.execute("INSERT INTO activity_log (order_id, action, details_json, actor, created_at) VALUES (?, ?, ?, ?, ?)", (order["id"] if order else None, action, json.dumps(details or {}, ensure_ascii=False), actor, now_iso()))
        return cursor.lastrowid

    def get_settings(self):
        rows = self.connection.execute("SELECT key, value_json FROM app_settings ORDER BY key").fetchall()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def save_settings(self, settings):
        timestamp = now_iso()
        with self.connection:
            for key, value in settings.items():
                self.connection.execute("INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at", (key, json.dumps(value, ensure_ascii=False), timestamp))
        return self.get_settings()
