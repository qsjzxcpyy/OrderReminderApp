import sqlite3
from pathlib import Path

from .workflow import LEGACY_STAGE_MAP


def connect_database(db_path):
    connection = sqlite3.connect(str(db_path), isolation_level="DEFERRED")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = connect_database(db_path)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL UNIQUE,
            platform_user_name TEXT,
            platform_seller_id TEXT,
            erp_status TEXT,
            latest_ship_at TEXT,
            erp_tracking_number TEXT,
            mark_content TEXT,
            marking_time TEXT,
            system_note TEXT,
            operator_note TEXT,
            abnormal_reason TEXT,
            consignee_name TEXT,
            consignee_country_name TEXT,
            consignee_email TEXT,
            warehouse_name TEXT,
            shipping_method_name_cn TEXT,
            shipping_method_name_en TEXT,
            latest_erp_snapshot_json TEXT NOT NULL DEFAULT '{}',
            last_imported_at TEXT,
            latest_arrival_at TEXT,
            deadline_at TEXT,
            deadline_override_at TEXT,
            deadline_rule TEXT NOT NULL DEFAULT 'NONE',
            deadline_issue TEXT NOT NULL DEFAULT 'MISSING_ARRIVAL',
            stage TEXT NOT NULL DEFAULT 'NEEDS_ARRIVAL_DATE',
            stage_suggestion TEXT,
            actual_tracking_number TEXT,
            return_confirmed_at TEXT,
            processing_result TEXT,
            processing_note TEXT,
            completed_at TEXT,
            overdue_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orders_stage_deadline ON orders(stage, deadline_at);
        CREATE INDEX IF NOT EXISTS idx_orders_arrival ON orders(latest_arrival_at);
        CREATE TABLE IF NOT EXISTS reminder_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            schedule_key TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            claimed_at TEXT,
            windows_status TEXT NOT NULL DEFAULT 'PENDING',
            windows_result TEXT,
            email_status TEXT NOT NULL DEFAULT 'PENDING',
            email_result TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(order_id, event_type, schedule_key)
        );
        CREATE INDEX IF NOT EXISTS idx_events_status_scheduled ON reminder_events(status, scheduled_at);
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            actor TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            imported_at TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    # Existing local databases were created before overdue state existed.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
    if "overdue_at" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN overdue_at TEXT")
    for legacy_stage, current_stage in LEGACY_STAGE_MAP.items():
        connection.execute("UPDATE orders SET stage = ? WHERE stage = ?", (current_stage, legacy_stage))
    connection.commit()
    connection.close()
