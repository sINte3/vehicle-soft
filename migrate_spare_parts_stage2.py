# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_STAGE2 -- Spare parts module Stage 2 schema.

Creates (Task 2 -- fuzzy search):
  - index idx_spare_parts_status_active on spare_parts(status, is_active)
    (the fuzzy catalog search fetches the full active candidate list on
    every keystroke-triggered request)

Safe / idempotent:
  - CREATE INDEX IF NOT EXISTS only.
  - Registers itself in schema_migrations and skips on re-run.
  - Does not touch or delete any existing business data.

[REASON]: This project's migration registry (migration_utils.py /
migrate_000_migration_registry.py) uses `id INTEGER PRIMARY KEY AUTOINCREMENT`
and registers by the `name` column with `applied_at` NOT NULL. This script
follows the proven name-based pattern used by migrate_spare_parts_stage1.py
and migrate_spare_parts_catalog_seed.py.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report-staging
  .\\nssm.exe stop TransportReportStaging
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_stage2.py
  .\\nssm.exe start TransportReportStaging

Rollback:
  This migration is purely additive.
  DROP INDEX IF EXISTS idx_spare_parts_status_active;
  DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_STAGE2';
  (As with prior spare-parts migrations, the real safety net is the
   pre-migration database backup.)
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_STAGE2'

INDEXES = [
    # [REASON]: SPARE-STAGE2 fuzzy search scans active catalog parts on every
    # keystroke-triggered search request; keep the candidate fetch indexed.
    "CREATE INDEX IF NOT EXISTS idx_spare_parts_status_active"
    " ON spare_parts(status, is_active)",
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
            (MIGRATION_ID, datetime.utcnow().isoformat(timespec='seconds'),
             'SPARE-STAGE2: fuzzy search index.'),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_ID,),
        )


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: Database not found at ' + DB_PATH, file=sys.stderr)
        print('Run this migration from the project folder with '
              'instance\\transport.db present.', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        cur = con.cursor()
        cur.execute(ENSURE_REGISTRY)

        if _migration_applied(cur):
            print("Migration {} already applied. Skipping.".format(MIGRATION_ID))
            return

        print("Creating indexes...")
        for sql in INDEXES:
            cur.execute(sql)

        _record_migration(cur)
        con.commit()
        print("Migration {} applied successfully.".format(MIGRATION_ID))

    except BaseException as exc:
        con.rollback()
        print("Migration {} FAILED: {}".format(MIGRATION_ID, exc), file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


if __name__ == '__main__':
    run()
