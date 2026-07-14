# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_SKU_UNIQUENESS -- SP-F-008.

Owner decision (2026-07-14): exact normalized SKU duplicates are FORBIDDEN.
Creates a normalized, active-only unique index:

  CREATE UNIQUE INDEX uq_spare_part_skus_normalized
    ON spare_part_skus (
      spare_part_id,
      lower(trim(brand)),
      lower(trim(article_number)),
      lower(trim(supplier))
    )
    WHERE is_active = 1;

Confirmed safe to apply directly: production has zero normalized duplicate
groups today (2026-07-14 read-only check, DQ-034: 0). The migration still
re-verifies this before creating the index and aborts cleanly otherwise.
SQLite supports partial/expression unique indexes -- this is normal DDL,
not a table rebuild.

NOTE: SQLite's lower() folds ASCII only; Cyrillic case variants are treated
as distinct by this index (and by the matching pre-check in sku_save), which
is the deliberate, consistent contract.

Safe / idempotent:
  - CREATE UNIQUE INDEX IF NOT EXISTS.
  - Registers itself in schema_migrations and skips on re-run.
  - Does not touch or delete any existing business data.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_sku_uniqueness.py
  .\\nssm.exe start TransportReport

Rollback:
  DROP INDEX IF EXISTS uq_spare_part_skus_normalized;
  DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_SKU_UNIQUENESS';
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_SKU_UNIQUENESS'

CREATE_INDEX = """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_spare_part_skus_normalized
      ON spare_part_skus (
        spare_part_id,
        lower(trim(brand)),
        lower(trim(article_number)),
        lower(trim(supplier))
      )
      WHERE is_active = 1
"""

DUPLICATE_CHECK = """
    SELECT spare_part_id, lower(trim(brand)), lower(trim(article_number)),
           lower(trim(supplier)), COUNT(*) AS c
    FROM spare_part_skus
    WHERE is_active = 1
    GROUP BY 1, 2, 3, 4
    HAVING c > 1
"""

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
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {r[1] for r in cur.fetchall()}
    if 'applied_at' in cols:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at, description) "
            "VALUES (?, ?, ?)",
            (MIGRATION_ID, datetime.utcnow().isoformat(timespec='seconds'),
             'SP-F-008: normalized active-only unique index '
             'uq_spare_part_skus_normalized on spare_part_skus.'),
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

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='spare_part_skus'"
        )
        if cur.fetchone() is None:
            print("  spare_part_skus table not found (Stage 2 not applied?), "
                  "index step skipped")
        else:
            # [REASON]: SP-F-008 -- defensive re-verification even though
            # DQ-034 confirmed zero duplicate groups: abort readably instead
            # of failing mid-DDL if the data changed since the check.
            cur.execute(DUPLICATE_CHECK)
            dupes = cur.fetchall()
            if dupes:
                raise RuntimeError(
                    "cannot create unique index -- normalized duplicate SKU "
                    "groups exist: {}".format(
                        '; '.join('part #{} [{}|{}|{}] x{}'.format(*d)
                                  for d in dupes)))
            print("Creating normalized active-only unique index...")
            cur.execute(CREATE_INDEX)
            print("  uq_spare_part_skus_normalized ensured")

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
