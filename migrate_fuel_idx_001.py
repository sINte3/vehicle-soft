"""
FUEL-IDX-001  add indexes for active fuel_transactions2 table.

Purpose:
- Speed up fuel dashboard/report queries filtering by txn_datetime.
- Speed up station + date range queries.

Safe/idempotent:
- Uses CREATE INDEX IF NOT EXISTS.
- Records schema_migrations row if available and not already recorded.
- Does not change business data.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "instance" / "transport.db"

MIGRATION_NAME = "FUEL_IDX_001_FUEL_TRANSACTIONS2_INDEXES"

INDEXES = [
    (
        "ix_fuel_transactions2_txn_datetime",
        "CREATE INDEX IF NOT EXISTS ix_fuel_transactions2_txn_datetime "
        "ON fuel_transactions2 (txn_datetime)",
    ),
    (
        "ix_fuel_transactions2_station_datetime",
        "CREATE INDEX IF NOT EXISTS ix_fuel_transactions2_station_datetime "
        "ON fuel_transactions2 (station_id, txn_datetime)",
    ),
]


def table_counts(cur: sqlite3.Cursor) -> dict[str, int]:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    result: dict[str, int] = {}
    for table in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        result[table] = int(cur.fetchone()[0])
    return result


def index_exists(cur: sqlite3.Cursor, index_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    )
    return cur.fetchone() is not None


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

        print("BEFORE_INDEXES:")
        for name, _sql in INDEXES:
            print("INDEX_BEFORE=", {"name": name, "exists": index_exists(cur, name)})

        for name, sql in INDEXES:
            print("CREATE_INDEX=", name)
            cur.execute(sql)

        inserted_migration = record_migration_if_possible(cur)

        after_counts = table_counts(cur)

        business_count_errors: list[str] = []
        for table, before in before_counts.items():
            after = after_counts.get(table)
            if table in {"schema_migrations", "sqlite_sequence"}:
                continue
            if before != after:
                business_count_errors.append(
                    f"{table}: before={before}, after={after}"
                )

        print("AFTER_INDEXES:")
        for name, _sql in INDEXES:
            exists = index_exists(cur, name)
            print("INDEX_AFTER=", {"name": name, "exists": exists})
            if not exists:
                raise SystemExit(f"INDEX_NOT_CREATED:{name}")

        print("MIGRATION_ROW_EXISTS=", migration_row_exists(cur))
        print("MIGRATION_ROW_INSERTED_THIS_RUN=", inserted_migration)
        print("BUSINESS_COUNT_ERRORS=", business_count_errors)

        if business_count_errors:
            raise SystemExit("BUSINESS_TABLE_COUNTS_CHANGED")

        con.commit()
        print("FUEL_IDX_001_MIGRATION_OK=YES")
        print("No business data rows were changed.")

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
