"""
migrate_bot003_outbox_v1.py - Create bot003_notification_outbox table.

This migration creates the isolated notification outbox table for BOT003
Telegram push notifications using the safe outbox pattern.

The outbox table is separate from the old bot_notification_queue table
to avoid any schema compatibility issues with BOT001/BOT002.

Design principles:
- Isolated from the main spare parts transaction (separate sqlite3 connection)
- Best-effort only: business operations never depend on this table
- Idempotent: safe to run multiple times

Usage:
    "C:\\Program Files\\Python314\\python.exe" migrate_bot003_outbox_v1.py

This script is NOT automatically called by app or bot imports.
"""

import sqlite3
import os
import sys
from datetime import datetime


MIGRATION_NAME = "migrate_bot003_outbox_v1"
MIGRATION_DESCRIPTION = "Create bot003_notification_outbox table for isolated BOT003 notification outbox"


def get_db_path():
    """Resolve the SQLite database path relative to this script."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "instance", "transport.db")


def table_exists(cursor, table_name):
    """Check if a table exists in the database."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def migration_applied(cursor):
    """Check if this migration has already been registered."""
    cursor.execute(
        "SELECT name FROM schema_migrations WHERE name=?",
        (MIGRATION_NAME,)
    )
    return cursor.fetchone() is not None


def register_migration(cursor):
    """Register this migration in the schema_migrations table."""
    now = datetime.utcnow().isoformat()
    cursor.execute(
        "INSERT INTO schema_migrations (name, applied_at, description) VALUES (?, ?, ?)",
        (MIGRATION_NAME, now, MIGRATION_DESCRIPTION)
    )


def run_migration():
    """Execute the migration. Prints PASS/FAIL for each step."""
    db_path = get_db_path()

    if not os.path.exists(db_path):
        print("FAIL: Database not found at {}".format(db_path))
        print("Hint: Run from the project root directory.")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    success = True

    # Step 1: Check if table already exists
    if table_exists(cursor, "bot003_notification_outbox"):
        print("PASS: bot003_notification_outbox already exists (idempotent skip).")
        conn.close()
        return True

    # Step 2: Verify schema_migrations table exists
    if not table_exists(cursor, "schema_migrations"):
        print("FAIL: schema_migrations table does not exist.")
        print("Hint: Run migrate_000_migration_registry.py first.")
        conn.close()
        return False

    # Step 3: Create the outbox table
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot003_notification_outbox (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type         TEXT    NOT NULL,
                request_id         INTEGER NOT NULL,
                target_user_id     INTEGER NULL,
                target_telegram_id TEXT    NULL,
                payload_json       TEXT    NOT NULL,
                dedupe_key         TEXT    NOT NULL UNIQUE,
                status             TEXT    NOT NULL DEFAULT 'pending',
                attempts           INTEGER NOT NULL DEFAULT 0,
                max_attempts       INTEGER NOT NULL DEFAULT 5,
                available_at       TEXT    NOT NULL,
                locked_at          TEXT    NULL,
                sent_at            TEXT    NULL,
                last_error         TEXT    NULL,
                created_at         TEXT    NOT NULL,
                updated_at         TEXT    NOT NULL
            )
        """)
        print("PASS: bot003_notification_outbox table created.")
    except sqlite3.Error as e:
        print("FAIL: Could not create bot003_notification_outbox table: {}".format(e))
        conn.close()
        return False

    # Step 4: Create indexes
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bot003_outbox_status_available
            ON bot003_notification_outbox (status, available_at)
        """)
        print("PASS: Index idx_bot003_outbox_status_available created.")
    except sqlite3.Error as e:
        print("FAIL: Could not create index: {}".format(e))
        success = False

    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bot003_outbox_request_id
            ON bot003_notification_outbox (request_id)
        """)
        print("PASS: Index idx_bot003_outbox_request_id created.")
    except sqlite3.Error as e:
        print("FAIL: Could not create index: {}".format(e))
        success = False

    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bot003_outbox_target_user_id
            ON bot003_notification_outbox (target_user_id)
        """)
        print("PASS: Index idx_bot003_outbox_target_user_id created.")
    except sqlite3.Error as e:
        print("FAIL: Could not create index: {}".format(e))
        success = False

    # Step 5: Register migration
    if success:
        try:
            register_migration(cursor)
            print("PASS: Migration registered in schema_migrations.")
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
            # If schema_migrations got created mid-flight or there's a dupe
            if "UNIQUE" in str(e):
                print("PASS: Migration already registered (idempotent).")
            else:
                print("FAIL: Could not register migration: {}".format(e))
                success = False

    # Step 6: Verify the table
    if success:
        try:
            cursor.execute("PRAGMA table_info(bot003_notification_outbox)")
            columns = cursor.fetchall()
            required = {"id", "event_type", "request_id", "payload_json", "dedupe_key", "status"}
            found = {col[1] for col in columns}
            missing = required - found
            if missing:
                print("FAIL: Missing columns: {}".format(missing))
                success = False
            else:
                print("PASS: All required columns present ({} total).".format(len(columns)))
        except sqlite3.Error as e:
            print("FAIL: Could not verify table: {}".format(e))
            success = False

    # Step 7: Verify indexes
    if success:
        try:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='bot003_notification_outbox'"
            )
            indexes = {row[0] for row in cursor.fetchall()}
            expected = {"idx_bot003_outbox_status_available", "idx_bot003_outbox_request_id",
                        "idx_bot003_outbox_target_user_id"}
            missing_idx = expected - indexes
            if missing_idx:
                print("WARN: Expected indexes not found: {}".format(missing_idx))
            else:
                print("PASS: All required indexes present.")
        except sqlite3.Error as e:
            print("FAIL: Could not verify indexes: {}".format(e))
            success = False

    conn.commit()
    conn.close()

    if success:
        print("\nRESULT: MIGRATION COMPLETE - bot003_notification_outbox is ready.")
    else:
        print("\nRESULT: MIGRATION COMPLETED WITH WARNINGS - review output above.")
    return success


if __name__ == "__main__":
    result = run_migration()
    sys.exit(0 if result else 1)
