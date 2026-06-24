"""
FUEL-REPORT-012H-C  Add fuel_cards, fuel_card_aliases, fuel_card_sync_logs tables.

Purpose:
- Store card directory data (display names, aliases) received from Topaz dcCards.
- Allow fuel reports to show card display names instead of raw card numbers/RFID.

Safe/idempotent:
- Uses CREATE TABLE IF NOT EXISTS.
- Uses CREATE INDEX IF NOT EXISTS.
- Registers migration in schema_migrations table.
- Does not change business data.
- Does not modify fuel_transactions2 schema.

Rollback:
- DROP TABLE IF EXISTS fuel_card_aliases;
- DROP TABLE IF EXISTS fuel_cards;
- DROP TABLE IF EXISTS fuel_card_sync_logs;
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "instance" / "transport.db"

MIGRATION_NAME = "FUEL_012H_CARDS_DIRECTORY"

CREATE_TABLES_SQL = [
    # fuel_cards
    """
    CREATE TABLE IF NOT EXISTS fuel_cards (
        id                  INTEGER PRIMARY KEY,
        topaz_card_id       VARCHAR(100) UNIQUE,
        display_name        VARCHAR(300) NOT NULL,
        rfid_code           VARCHAR(150),
        partner_id          VARCHAR(100),
        enabled             BOOLEAN DEFAULT 1,
        car_number          VARCHAR(100),
        car_model           VARCHAR(200),
        topaz_transaction_id VARCHAR(100),
        source              VARCHAR(100) DEFAULT 'topaz_dcCards',
        first_seen          DATETIME,
        last_seen           DATETIME,
        created_at          DATETIME,
        updated_at          DATETIME,
        notes               TEXT DEFAULT ''
    )
    """,
    # fuel_card_aliases
    """
    CREATE TABLE IF NOT EXISTS fuel_card_aliases (
        id          INTEGER PRIMARY KEY,
        card_id     INTEGER NOT NULL,
        alias_type  VARCHAR(30) NOT NULL,
        alias_value VARCHAR(150) NOT NULL,
        source      VARCHAR(100) DEFAULT 'topaz_dcCards',
        first_seen  DATETIME,
        last_seen   DATETIME,
        created_at  DATETIME,
        updated_at  DATETIME,
        UNIQUE(alias_type, alias_value)
    )
    """,
    # fuel_card_sync_logs
    """
    CREATE TABLE IF NOT EXISTS fuel_card_sync_logs (
        id                INTEGER PRIMARY KEY,
        synced_at         DATETIME,
        source            VARCHAR(100),
        rows_received     INTEGER DEFAULT 0,
        cards_created     INTEGER DEFAULT 0,
        cards_updated     INTEGER DEFAULT 0,
        aliases_created   INTEGER DEFAULT 0,
        aliases_updated   INTEGER DEFAULT 0,
        aliases_conflicted INTEGER DEFAULT 0,
        rows_skipped      INTEGER DEFAULT 0,
        status            VARCHAR(30),
        message           TEXT DEFAULT ''
    )
    """,
]

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS ix_fuel_cards_topaz_card_id ON fuel_cards (topaz_card_id)",
    "CREATE INDEX IF NOT EXISTS ix_fuel_cards_display_name ON fuel_cards (display_name)",
    "CREATE INDEX IF NOT EXISTS ix_fuel_cards_rfid_code ON fuel_cards (rfid_code)",
    "CREATE INDEX IF NOT EXISTS ix_fuel_cards_enabled ON fuel_cards (enabled)",
    "CREATE INDEX IF NOT EXISTS ix_fuel_card_aliases_card_id ON fuel_card_aliases (card_id)",
    "CREATE INDEX IF NOT EXISTS ix_fuel_card_aliases_alias_value ON fuel_card_aliases (alias_value)",
]


def table_counts(cur: sqlite3.Cursor) -> dict[str, int]:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    result: dict[str, int] = {}
    for table in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        result[table] = int(cur.fetchone()[0])
    return result


def migration_row_exists(cur: sqlite3.Cursor) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if cur.fetchone() is None:
        return False
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {row[1] for row in cur.fetchall()}
    if "name" not in cols:
        return False
    cur.execute("SELECT 1 FROM schema_migrations WHERE name=?", (MIGRATION_NAME,))
    return cur.fetchone() is not None


def record_migration_if_possible(cur: sqlite3.Cursor) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if cur.fetchone() is None:
        print("SCHEMA_MIGRATIONS_TABLE=ABSENT")
        return False
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {row[1] for row in cur.fetchall()}
    if "name" not in cols:
        print("SCHEMA_MIGRATIONS_NAME_COLUMN=ABSENT")
        return False
    cur.execute("SELECT 1 FROM schema_migrations WHERE name=?", (MIGRATION_NAME,))
    if cur.fetchone() is not None:
        print("MIGRATION_ROW_ALREADY_PRESENT=YES")
        return False
    if "applied_at" in cols:
        cur.execute(
            "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
            (MIGRATION_NAME, datetime.now(UTC).isoformat(timespec="seconds")),
        )
    else:
        cur.execute(
            "INSERT INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_NAME,),
        )
    print("MIGRATION_ROW_INSERTED=YES")
    return True


def main() -> None:
    print("DB_PATH=", DB_PATH)
    if not DB_PATH.exists():
        raise SystemExit("DB_NOT_FOUND")

    con = sqlite3.connect(str(DB_PATH))
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("PRAGMA busy_timeout=30000")

        before_counts = table_counts(cur)

        # Create tables
        for sql in CREATE_TABLES_SQL:
            cur.execute(sql)

        # Create indexes
        for sql in INDEXES_SQL:
            cur.execute(sql)

        inserted_migration = record_migration_if_possible(cur)

        after_counts = table_counts(cur)

        # Verify new tables exist
        new_tables = ["fuel_cards", "fuel_card_aliases", "fuel_card_sync_logs"]
        print("NEW_TABLES:")
        for t in new_tables:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (t,),
            )
            exists = cur.fetchone() is not None
            print(f"  {t}: {'exists' if exists else 'MISSING'}")
            if not exists:
                raise SystemExit(f"TABLE_NOT_CREATED:{t}")

        # Verify indexes exist
        index_names = [sql.split()[-1] for sql in INDEXES_SQL]
        print("NEW_INDEXES:")
        for name in index_names:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            )
            exists = cur.fetchone() is not None
            print(f"  {name}: {'exists' if exists else 'MISSING'}")
            if not exists:
                raise SystemExit(f"INDEX_NOT_CREATED:{name}")

        # Verify no business data changed
        business_count_errors: list[str] = []
        for table, before in before_counts.items():
            after = after_counts.get(table)
            if table in {"schema_migrations", "sqlite_sequence"}:
                continue
            if before != after:
                business_count_errors.append(
                    f"{table}: before={before}, after={after}"
                )

        print("BUSINESS_COUNT_ERRORS=", business_count_errors)
        if business_count_errors:
            raise SystemExit("BUSINESS_TABLE_COUNTS_CHANGED")

        print("MIGRATION_ROW_EXISTS=", migration_row_exists(cur))
        print("MIGRATION_ROW_INSERTED_THIS_RUN=", inserted_migration)

        con.commit()
        print("FUEL_012H_CARDS_MIGRATION_OK=YES")
        print("No business data rows were changed.")

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
