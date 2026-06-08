"""
migrate_bot001_telegram_foundation.py -- BOT001 (corrected: BOT001A)
Telegram Foundation database migration.

What this migration does:
  1. PRAGMA integrity_check before migration
  2. Adds Telegram columns to users table (idempotent ALTER TABLE)
  3. Creates spare_part_status_history table (if not exists)
  4. Creates bot_api_sessions table (if not exists)
  5. Creates bot_notification_queue table (if not exists)
  6. Creates required indexes (if not exists), including a UNIQUE partial
     index on users(telegram_id) WHERE telegram_id IS NOT NULL
  7. PRAGMA integrity_check after migration
  8. Records migration in schema_migrations

SQLite ALTER TABLE limitation:
  ALTER TABLE ADD COLUMN does NOT support UNIQUE constraints on new columns.
  Therefore users.telegram_id is added as plain INTEGER, and uniqueness is
  enforced by a separate UNIQUE partial index (see idx_users_telegram_id below).

Safety:
  - Fully idempotent: safe to run multiple times
  - Checks column existence before ALTER TABLE
  - Checks table existence before CREATE TABLE
  - Checks index existence before CREATE INDEX
  - Uses INSERT OR IGNORE for migration registry record
  - Never drops or modifies existing data
  - Refuses to run if instance/transport.db is missing

Run on staging ONLY (service MUST be stopped first):

  cd /d C:\\transport-report-staging
  nssm.exe stop TransportReportStaging
  copy instance\\transport.db instance\\transport.db.backup_before_bot001a
  "C:\\Program Files\\Python314\\python.exe" migrate_bot001_telegram_foundation.py
  "C:\\Program Files\\Python314\\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); print(c.execute('PRAGMA integrity_check').fetchall()); c.close()"
"""

import os
import sqlite3
from datetime import datetime

THIS_MIGRATION = "migrate_bot001_telegram_foundation"
DESCRIPTION = (
    "BOT001A: Add telegram columns to users (telegram_id as INTEGER, uniqueness via partial index); "
    "create spare_part_status_history, bot_api_sessions, bot_notification_queue."
)

SCRIPT_PATH = os.path.abspath(__file__)
DB_PATH = os.path.join(os.path.dirname(SCRIPT_PATH), "instance", "transport.db")


def _columns_in_table(cur, table_name):
    """Return a set of column names for table_name (lowercase)."""
    cur.execute("PRAGMA table_info({})".format(table_name))
    rows = cur.fetchall()
    return {row[1].lower() for row in rows}


def _table_exists(cur, table_name):
    """Return True if table_name exists in the database."""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cur.fetchone() is not None


def _index_exists(cur, index_name):
    """Return True if index_name exists in the database."""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,)
    )
    return cur.fetchone() is not None


def _integrity_check(con):
    """Run PRAGMA integrity_check and return a list of result strings."""
    cur = con.cursor()
    cur.execute("PRAGMA integrity_check")
    rows = cur.fetchall()
    return [r[0] for r in rows]


def _ensure_schema_migrations(cur):
    """Create schema_migrations table if absent."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            applied_at  DATETIME NOT NULL,
            checksum    TEXT,
            description TEXT
        )
    """)


def _is_migration_applied(cur, name):
    """Return True if migration name is already recorded in schema_migrations."""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if cur.fetchone() is None:
        return False
    cur.execute("SELECT id FROM schema_migrations WHERE name=?", (name,))
    return cur.fetchone() is not None


def _record_migration(cur, name, description):
    """Insert migration record (INSERT OR IGNORE = idempotent)."""
    cur.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at, description) VALUES (?,?,?)",
        (name, datetime.utcnow().isoformat(), description),
    )


def run():
    if not os.path.exists(DB_PATH):
        print("ERROR: Database not found at " + DB_PATH)
        print("Ensure instance/transport.db exists before running this migration.")
        return False

    print("=" * 60)
    print("BOT001A Telegram Foundation Migration (corrected)")
    print("Database : " + DB_PATH)
    print("Migration: " + THIS_MIGRATION)
    print("=" * 60)
    print()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # -- PRAGMA integrity_check BEFORE ----------------------------------------
    print("[1/8] PRAGMA integrity_check (before)...")
    before_check = _integrity_check(con)
    if before_check == ["ok"]:
        print("      integrity_check: OK")
    else:
        print("      integrity_check RESULT: " + str(before_check))
        print("WARNING: integrity issues detected before migration. Inspect manually.")
    print()

    # -- Check if already applied (idempotency guard) -------------------------
    _ensure_schema_migrations(cur)
    con.commit()

    if _is_migration_applied(cur, THIS_MIGRATION):
        print("[SKIP] Migration '" + THIS_MIGRATION + "' already recorded in schema_migrations.")
        print("       Re-checking all indexes are present anyway (idempotent)...")
        # Fall through to index creation -- those are all IF NOT EXISTS
    else:
        print("       Migration not yet applied. Proceeding...")
    print()

    # -- Step 2: Add columns to users -----------------------------------------
    print("[2/8] Adding Telegram columns to 'users' table...")
    existing_cols = _columns_in_table(cur, "users")

    # IMPORTANT: telegram_id is added as plain INTEGER (no UNIQUE constraint here).
    # SQLite ALTER TABLE ADD COLUMN does not support UNIQUE on new columns.
    # Uniqueness is enforced by a UNIQUE partial index created in step 6.
    user_columns_to_add = [
        ("telegram_id",             "INTEGER"),
        ("tg_notifications",        "INTEGER NOT NULL DEFAULT 1"),
        ("tg_quiet_hours",          "VARCHAR(20)"),
        ("tg_link_code_hash",       "VARCHAR(128)"),
        ("tg_link_code_expires_at", "DATETIME"),
        ("tg_link_code_created_at", "DATETIME"),
    ]

    for col_name, col_def in user_columns_to_add:
        if col_name in existing_cols:
            print("      Column users.{} already exists. Skipping.".format(col_name))
        else:
            ddl = "ALTER TABLE users ADD COLUMN {} {}".format(col_name, col_def)
            cur.execute(ddl)
            print("      Added users.{}  ({})".format(col_name, col_def))

    con.commit()
    print()

    # -- Step 3: Create spare_part_status_history -----------------------------
    print("[3/8] Creating 'spare_part_status_history' table...")
    if _table_exists(cur, "spare_part_status_history"):
        print("      Table spare_part_status_history already exists. Skipping.")
    else:
        cur.execute("""
            CREATE TABLE spare_part_status_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id  INTEGER NOT NULL,
                old_status  VARCHAR(30),
                new_status  VARCHAR(30) NOT NULL,
                comment     TEXT NOT NULL DEFAULT '',
                changed_by  INTEGER,
                changed_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("      Created spare_part_status_history.")

    con.commit()
    print()

    # -- Step 4: Create bot_api_sessions --------------------------------------
    print("[4/8] Creating 'bot_api_sessions' table...")
    if _table_exists(cur, "bot_api_sessions"):
        print("      Table bot_api_sessions already exists. Skipping.")
    else:
        cur.execute("""
            CREATE TABLE bot_api_sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                telegram_id  INTEGER NOT NULL,
                token_hash   VARCHAR(128) NOT NULL UNIQUE,
                expires_at   DATETIME NOT NULL,
                created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at DATETIME,
                revoked_at   DATETIME
            )
        """)
        print("      Created bot_api_sessions.")

    con.commit()
    print()

    # -- Step 5: Create bot_notification_queue --------------------------------
    print("[5/8] Creating 'bot_notification_queue' table...")
    if _table_exists(cur, "bot_notification_queue"):
        print("      Table bot_notification_queue already exists. Skipping.")
    else:
        cur.execute("""
            CREATE TABLE bot_notification_queue (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id  INTEGER NOT NULL,
                user_id      INTEGER,
                request_id   INTEGER,
                event_type   VARCHAR(80) NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       VARCHAR(30) NOT NULL DEFAULT 'pending',
                attempts     INTEGER NOT NULL DEFAULT 0,
                last_error   TEXT NOT NULL DEFAULT '',
                created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at      DATETIME
            )
        """)
        print("      Created bot_notification_queue.")

    con.commit()
    print()

    # -- Step 6: Create indexes -----------------------------------------------
    # idx_users_telegram_id is a UNIQUE PARTIAL index (WHERE telegram_id IS NOT NULL).
    # This enforces uniqueness only for non-NULL values, which is the correct pattern
    # for an optional nullable unique field in SQLite.
    print("[6/8] Creating indexes...")

    # Special case: UNIQUE partial index for users.telegram_id
    if _index_exists(cur, "idx_users_telegram_id"):
        print("      Index idx_users_telegram_id already exists. Skipping.")
    else:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id "
            "ON users(telegram_id) WHERE telegram_id IS NOT NULL"
        )
        print("      Created UNIQUE partial index idx_users_telegram_id.")

    # Regular indexes for bot tables (existence-checked before creation)
    regular_indexes = [
        ("idx_bot_api_sessions_token_hash",          "bot_api_sessions(token_hash)"),
        ("idx_bot_api_sessions_user_id",             "bot_api_sessions(user_id)"),
        ("idx_bot_notification_queue_status",        "bot_notification_queue(status)"),
        ("idx_bot_notification_queue_telegram_id",   "bot_notification_queue(telegram_id)"),
        ("idx_spare_part_status_history_request_id", "spare_part_status_history(request_id)"),
    ]

    for idx_name, idx_target in regular_indexes:
        if _index_exists(cur, idx_name):
            print("      Index {} already exists. Skipping.".format(idx_name))
        else:
            cur.execute(
                "CREATE INDEX IF NOT EXISTS {} ON {}".format(idx_name, idx_target)
            )
            print("      Created index {}.".format(idx_name))

    con.commit()
    print()

    # -- Step 7: Record migration in schema_migrations ------------------------
    print("[7/8] Recording migration in schema_migrations...")
    _record_migration(cur, THIS_MIGRATION, DESCRIPTION)
    con.commit()
    print("      Recorded: " + THIS_MIGRATION)
    print()

    # -- PRAGMA integrity_check AFTER -----------------------------------------
    print("[8/8] PRAGMA integrity_check (after)...")
    after_check = _integrity_check(con)
    if after_check == ["ok"]:
        print("      integrity_check: OK")
    else:
        print("      integrity_check RESULT: " + str(after_check))
        print("ERROR: Database integrity check failed after migration!")

    con.close()
    print()
    print("=" * 60)
    print("BOT001A migration complete.")
    print("=" * 60)
    return after_check == ["ok"]


if __name__ == "__main__":
    success = run()
    if not success:
        raise SystemExit(1)
