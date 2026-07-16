# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_NAME_UZ -- CYCLE-2-3 Part 7.

Adds the nullable column spare_parts.name_uz (optional Uzbek display alias
for the canonical Russian part name). Purely additive:

  - No existing name values are touched.
  - name_uz stays NULL until the owner fills translations in through the
    catalog UI; every display site falls back to `name` when it is empty.

Safe / idempotent:
  - Column added only if missing (PRAGMA table_info check).
  - Registers itself in schema_migrations and skips on re-run.
  - Does not touch or delete any existing business data.

Hardened (RE-SP-011 pattern, same as migrate_spare_parts_acts_permission):
  - FAILS LOUDLY (and records nothing) if the prerequisite spare_parts
    table is missing (fresh/misordered install).
  - Verifies its postcondition (column present) BEFORE recording itself.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_name_uz.py
  .\\nssm.exe start TransportReport

Rollback:
  The column is additive and nullable — it is SAFE TO LEAVE IN PLACE even
  if the application code is rolled back (old code simply never reads it).
  To remove it anyway (SQLite >= 3.35):
    ALTER TABLE spare_parts DROP COLUMN name_uz;
    DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_NAME_UZ';
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_NAME_UZ'

ENSURE_REGISTRY = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""


def _table_exists(cur, name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _column_exists(cur, table, column):
    cur.execute("PRAGMA table_info({})".format(table))
    return column in {row[1] for row in cur.fetchall()}


def _assert_prerequisites(cur):
    # [REASON]: RE-SP-011 — fail loudly instead of silently registering on a
    # fresh/misordered install where spare_parts does not exist yet.
    if not _table_exists(cur, 'spare_parts'):
        raise RuntimeError(
            "prerequisite table 'spare_parts' missing -- run the earlier "
            "spare parts migrations (stage1) first; NOT recording this "
            "migration")


def _assert_postconditions(cur):
    # [REASON]: RE-SP-011 — only a verified-complete run may be recorded.
    if not _column_exists(cur, 'spare_parts', 'name_uz'):
        raise RuntimeError(
            "postcondition check failed: spare_parts.name_uz missing -- "
            "NOT recording this migration")


def _add_column(cur):
    if _column_exists(cur, 'spare_parts', 'name_uz'):
        print("  column spare_parts.name_uz already present")
        return
    cur.execute("ALTER TABLE spare_parts ADD COLUMN name_uz VARCHAR(300)")
    print("  column spare_parts.name_uz added (nullable, no data touched)")


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
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {r[1] for r in cur.fetchall()}
    if 'applied_at' in cols:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at, description) "
            "VALUES (?, ?, ?)",
            (MIGRATION_ID, datetime.utcnow().isoformat(timespec='seconds'),
             'CYCLE-2-3 Part 7: nullable spare_parts.name_uz column '
             '(Uzbek display alias, fallback to name when empty).'),
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

        _assert_prerequisites(cur)

        print("Adding spare_parts.name_uz column...")
        _add_column(cur)

        _assert_postconditions(cur)
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
