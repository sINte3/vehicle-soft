# -*- coding: utf-8 -*-
"""Migration WORK_ORDERS_001 -- Work Orders module: create tables, add FK to daily_records.

Creates:
  - work_orders
  - work_order_status_history
  - daily_records.work_order_id (nullable FK, added only if missing)
  - supporting indexes

Safe / idempotent:
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
  - ALTER TABLE ... ADD COLUMN only when the column is absent.
  - Registers itself in schema_migrations and skips on re-run.
  - Does not touch or delete any existing business data.

[REASON]: The WORK-ORDER-001 prompt shipped a draft that assumed a TEXT `id`
primary key in schema_migrations and registered the migration by `id`. This
project's registry (see migration_utils.py / migrate_000_migration_registry.py)
uses `id INTEGER PRIMARY KEY AUTOINCREMENT` and registers by the `name` column,
with `applied_at` NOT NULL. Registering by `id` would raise a datatype mismatch
and a NOT NULL violation on `name`. This script follows the real, proven
name-based pattern used by migrate_fuel_012h_cards.py.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report-staging
  .\\nssm.exe stop TransportReportStaging
  "C:\\Program Files\\Python314\\python.exe" migrate_work_orders_001.py
  .\\nssm.exe start TransportReportStaging

Rollback:
  DROP TABLE IF EXISTS work_order_status_history;
  DROP TABLE IF EXISTS work_orders;
  (daily_records.work_order_id is a nullable additive column; dropping it
   requires a table rebuild in SQLite. Safest rollback is to restore the
   pre-migration DB backup.)
"""

import os
import sqlite3
import sys
from datetime import UTC, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'WORK_ORDERS_001'

CREATE_WORK_ORDERS = """
    CREATE TABLE IF NOT EXISTS work_orders (
        id              INTEGER PRIMARY KEY,
        number          VARCHAR(20) UNIQUE NOT NULL,
        organization_id INTEGER NOT NULL REFERENCES organizations(id),
        equipment_id    INTEGER NOT NULL REFERENCES equipment(id),
        work_type_id    INTEGER REFERENCES work_types(id),
        work_type_text  VARCHAR(200) NOT NULL DEFAULT '',
        customer_id     INTEGER REFERENCES customers(id),
        customer_text   VARCHAR(300) NOT NULL DEFAULT '',
        assigned_to     INTEGER REFERENCES users(id),
        created_by      INTEGER NOT NULL REFERENCES users(id),
        status          VARCHAR(20) NOT NULL DEFAULT 'draft',
        planned_date    DATE NOT NULL,
        actual_date     DATE,
        unit            VARCHAR(30) NOT NULL DEFAULT 'ga',
        planned_quantity REAL,
        actual_quantity  REAL,
        default_price   REAL NOT NULL DEFAULT 0,
        price           REAL NOT NULL DEFAULT 0,
        payment_type    VARCHAR(20) NOT NULL DEFAULT '',
        note            TEXT NOT NULL DEFAULT '',
        daily_record_id INTEGER REFERENCES daily_records(id),
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        closed_at       DATETIME
    )
"""

CREATE_STATUS_HISTORY = """
    CREATE TABLE IF NOT EXISTS work_order_status_history (
        id            INTEGER PRIMARY KEY,
        work_order_id INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
        event_type    VARCHAR(30) NOT NULL DEFAULT 'status_change',
        old_value     VARCHAR(200),
        new_value     VARCHAR(200) NOT NULL,
        changed_by    INTEGER REFERENCES users(id),
        changed_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
        comment       TEXT NOT NULL DEFAULT ''
    )
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_wo_org_status_date ON work_orders(organization_id, status, planned_date)",
    "CREATE INDEX IF NOT EXISTS ix_wo_equipment_date  ON work_orders(equipment_id, planned_date)",
    "CREATE INDEX IF NOT EXISTS ix_wo_assigned        ON work_orders(assigned_to, status)",
]

# [REASON]: Matches migration_utils._CREATE_TABLE_SQL exactly so a fresh install
# (where migrate_000 has not run yet) still gets a compatible registry table.
ENSURE_REGISTRY = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""


def _migration_applied(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if cur.fetchone() is None:
        return False
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {r[1] for r in cur.fetchall()}
    if 'name' not in cols:
        return False
    cur.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (MIGRATION_ID,))
    return cur.fetchone() is not None


def _record_migration(cur):
    # [REASON]: applied_at is NOT NULL in this project's registry, so it must be
    # supplied explicitly. INSERT OR IGNORE keeps the call idempotent.
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {r[1] for r in cur.fetchall()}
    if 'applied_at' in cols:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at, description) "
            "VALUES (?, ?, ?)",
            (MIGRATION_ID, datetime.now(UTC).isoformat(timespec='seconds'),
             'WORK-ORDER-001 Phase 1: work_orders + status history + daily_records FK.'),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_ID,),
        )


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: Database not found at ' + DB_PATH, file=sys.stderr)
        print('Run this migration from C:\\transport-report-staging with '
              'instance\\transport.db present.', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        cur = con.cursor()
        cur.execute(ENSURE_REGISTRY)

        if _migration_applied(cur):
            print(f"Migration {MIGRATION_ID} already applied. Skipping.")
            return

        # --- Create work_orders + status history ---
        cur.execute(CREATE_WORK_ORDERS)
        cur.execute(CREATE_STATUS_HISTORY)
        for sql in INDEXES:
            cur.execute(sql)

        # --- Add work_order_id FK to daily_records (idempotent) ---
        cols = [r[1] for r in cur.execute("PRAGMA table_info(daily_records)").fetchall()]
        if 'work_order_id' not in cols:
            cur.execute(
                "ALTER TABLE daily_records ADD COLUMN work_order_id INTEGER "
                "REFERENCES work_orders(id) ON DELETE SET NULL"
            )

        # --- Register migration ---
        _record_migration(cur)
        con.commit()
        print(f"Migration {MIGRATION_ID} applied successfully.")

    except BaseException as exc:
        con.rollback()
        print(f"Migration {MIGRATION_ID} FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


if __name__ == '__main__':
    run()
