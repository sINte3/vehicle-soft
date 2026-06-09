"""
diagnose_bot003_outbox.py — BOT003 Read-Only Diagnostic Script

Checks the state of the bot003_notification_outbox table without modifying
any data. Use this to verify the outbox setup before and after migration.

Usage:
    "C:\\Program Files\\Python314\\python.exe" diagnose_bot003_outbox.py

This script NEVER modifies the database.
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "instance", "transport.db")


def print_section(title):
    """Print a section header."""
    print()
    print("=" * 70)
    print("  {}".format(title))
    print("=" * 70)


def check_database():
    """Check if the database exists and is accessible."""
    if not os.path.exists(DB_PATH):
        print("FAIL: Database not found at {}".format(DB_PATH))
        return False
    print("OK: Database found at {}".format(DB_PATH))
    return True


def print_table_info(cursor, table_name):
    """Print table schema information."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    if not cursor.fetchone():
        print("  Table '{}' does NOT exist.".format(table_name))
        return False

    cursor.execute("PRAGMA table_info({})".format(table_name))
    columns = cursor.fetchall()
    print("  Table '{}' exists with {} columns:".format(table_name, len(columns)))
    for col in columns:
        cid, name, col_type, notnull, default_val, pk = col
        nullable = "NOT NULL" if notnull else "NULL"
        default = "  DEFAULT {}".format(default_val) if default_val else ""
        pk_str = "  PK" if pk else ""
        print("    {}: {} {} {}{}".format(name, col_type, nullable, default, pk_str))
    return True


def count_rows(cursor, table_name, status=None):
    """Count rows in a table, optionally filtered by status."""
    if status:
        cursor.execute(
            "SELECT COUNT(*) FROM {} WHERE status = ?".format(table_name),
            (status,)
        )
    else:
        cursor.execute("SELECT COUNT(*) FROM {}".format(table_name))
    return cursor.fetchone()[0]


def print_row_counts(cursor, table_name):
    """Print row counts by status."""
    if not print_table_info(cursor, table_name):
        return

    total = count_rows(cursor, table_name)
    print()
    print("  Row counts for '{}':".format(table_name))
    print("    Total: {}".format(total))

    if total == 0:
        return

    for status in ["pending", "sent", "failed"]:
        try:
            cnt = count_rows(cursor, table_name, status)
            if cnt > 0:
                print("    Status '{}': {}".format(status, cnt))
        except Exception:
            pass


def print_old_queue_info(cursor):
    """Print info about the old bot_notification_queue table."""
    print_section("Legacy bot_notification_queue")
    if not print_table_info(cursor, "bot_notification_queue"):
        return

    total = count_rows(cursor, "bot_notification_queue")
    print("  Total rows: {}".format(total))
    if total > 0:
        for status in ["pending", "sent", "failed"]:
            cnt = count_rows(cursor, "bot_notification_queue", status)
            if cnt > 0:
                print("    Status '{}': {}".format(status, cnt))

    # Check for any relation to spare part requests
    try:
        cursor.execute("""
            SELECT event_type, COUNT(*) as cnt
            FROM bot_notification_queue
            GROUP BY event_type
            ORDER BY cnt DESC
        """)
        events = cursor.fetchall()
        if events:
            print("  Event types:")
            for evt, cnt in events:
                print("    {}: {}".format(evt, cnt))
    except Exception:
        pass


def print_indexes(cursor, table_name):
    """Print indexes for a given table."""
    cursor.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table_name,)
    )
    indexes = cursor.fetchall()
    if indexes:
        print("  Indexes:")
        for idx_name, idx_sql in indexes:
            # Skip auto-generated indexes (sqlite_autoindex_*)
            if idx_name.startswith("sqlite_autoindex"):
                continue
            if idx_sql:
                # Extract column names from index definition
                cols = idx_sql.split("(")[-1].rstrip(")")
                print("    {} -> ({})".format(idx_name, cols))
            else:
                print("    {} (auto)".format(idx_name))
    else:
        print("  No indexes (auto-generated PK index exists)")


def main():
    """Main diagnostic routine."""
    print("=" * 70)
    print("  BOT003 Outbox Diagnostic Tool (READ-ONLY)")
    print("  Database: {}".format(DB_PATH))
    print("=" * 70)

    if not check_database():
        sys.exit(1)

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
    except sqlite3.Error as e:
        print("FAIL: Cannot connect to database: {}".format(e))
        sys.exit(1)

    # Check bot003_notification_outbox
    print_section("bot003_notification_outbox (BOT003)")
    outbox_exists = print_table_info(cursor, "bot003_notification_outbox")
    if outbox_exists:
        print_row_counts(cursor, "bot003_notification_outbox")
        print_indexes(cursor, "bot003_notification_outbox")

        # Show oldest pending entries
        try:
            cursor.execute("""
                SELECT id, event_type, request_id, status, attempts, available_at, created_at
                FROM bot003_notification_outbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 5
            """)
            oldest = cursor.fetchall()
            if oldest:
                print()
                print("  Oldest pending entries:")
                for row in oldest:
                    print("    ID={} event={} request={} attempts={}/{}\n"
                          "      available_at={} created_at={}".format(
                          row[0], row[1], row[2], row[4], 5,
                          row[5], row[6]))
        except Exception:
            pass

        # Show recent errors
        try:
            cursor.execute("""
                SELECT id, event_type, request_id, attempts, last_error, updated_at
                FROM bot003_notification_outbox
                WHERE last_error IS NOT NULL AND last_error != ''
                ORDER BY updated_at DESC
                LIMIT 5
            """)
            errors = cursor.fetchall()
            if errors:
                print()
                print("  Recent errors:")
                for row in errors:
                    print("    ID={} event={} request={} attempts={}\n"
                          "      error={}\n"
                          "      updated_at={}".format(
                          row[0], row[1], row[2], row[3],
                          row[4][:100], row[5]))
        except Exception:
            pass
    else:
        print("  RESULT: bot003_notification_outbox does not exist.")
        print("  The migration has NOT been run yet.")
        print("  BOT003 code is safe without this table — notifications will be skipped.")
        print()
        print("  To create the table, run:")
        print('    "C:\\Program Files\\Python314\\python.exe" migrate_bot003_outbox_v1.py')

    # Check legacy queue
    print_old_queue_info(cursor)

    # Check migration status
    print_section("Migration Status")
    try:
        cursor.execute(
            "SELECT name, applied_at, description FROM schema_migrations ORDER BY applied_at"
        )
        migrations = cursor.fetchall()
        if migrations:
            print("  Applied migrations:")
            for name, applied_at, desc in migrations:
                print("    {} — {} ({})".format(name, desc, applied_at))
            bot003_applied = any("bot003" in m[0].lower() for m in migrations)
            if bot003_applied:
                print()
                print("  BOT003 migration IS registered in schema_migrations.")
            else:
                print()
                print("  (No BOT003 migration found — not yet applied.)")
        else:
            print("  No migrations registered.")
    except Exception as e:
        print("  Could not read schema_migrations: {}".format(e))

    # Check spare parts counts
    print_section("Spare Parts Request Counts")
    try:
        cursor.execute("SELECT COUNT(*) FROM spare_part_requests")
        total = cursor.fetchone()[0]
        print("  Total spare part requests: {}".format(total))

        cursor.execute("""
            SELECT status, COUNT(*) as cnt
            FROM spare_part_requests
            GROUP BY status
            ORDER BY cnt DESC
        """)
        by_status = cursor.fetchall()
        if by_status:
            for status, cnt in by_status:
                print("    Status '{}': {}".format(status, cnt))
    except Exception as e:
        print("  Could not read spare_part_requests: {}".format(e))

    # Check telegram_id users
    print_section("Telegram-Linked Users")
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM users
            WHERE telegram_id IS NOT NULL AND is_active_user = 1
        """)
        linked_count = cursor.fetchone()[0]
        print("  Active users with telegram_id: {}".format(linked_count))

        cursor.execute("""
            SELECT id, username, role, telegram_id
            FROM users
            WHERE telegram_id IS NOT NULL AND is_active_user = 1
            ORDER BY role, username
        """)
        linked_users = cursor.fetchall()
        if linked_users:
            for uid, uname, role, tg_id in linked_users:
                print("    ID={} {} ({}) tg_id={}".format(uid, uname, role, tg_id))
    except Exception as e:
        print("  Could not read user data: {}".format(e))

    conn.close()

    print()
    print("=" * 70)
    print("  Diagnostic complete. No data was modified.")
    print("=" * 70)


if __name__ == "__main__":
    main()
