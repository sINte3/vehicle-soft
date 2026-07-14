# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_ACTS_PERMISSION -- SP-F-002 + SP-F-019.

SP-F-002 (acts permission):
  - Seeds one new app_modules permission row (INSERT OR IGNORE):
      spare_parts_acts  -- viewing write-off acts and their PDFs.
  - Auto-grants spare_parts_acts to every user who currently holds
    spare_parts_issue OR spare_parts_approve with has_access=1, so nobody
    who could already reach acts through the issue/approve workflow loses
    access when act_detail/act_pdf start checking the new permission.
    Admins bypass via is_admin and need no row.

SP-F-019 (one act per request):
  - Replaces the plain index idx_spare_part_write_off_acts_request_id with
    a UNIQUE index on spare_part_write_off_acts(request_id). Confirmed safe:
    zero requests with multiple acts on both staging (audit DQ-022) and
    production (2026-07-14 re-check, DQ-022: 0). The migration still
    re-verifies this before touching the index and aborts cleanly if the
    data has changed since.

Safe / idempotent:
  - INSERT OR IGNORE for the app_modules seed row (code is UNIQUE) and for
    the auto-grant rows (user_module_permissions has UNIQUE(user_id,
    module_code)).
  - DROP INDEX IF EXISTS / CREATE UNIQUE INDEX IF NOT EXISTS.
  - Registers itself in schema_migrations and skips on re-run.
  - Does not touch or delete any existing business data.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_acts_permission.py
  .\\nssm.exe start TransportReport

Rollback:
  DROP INDEX IF EXISTS idx_spare_part_write_off_acts_request_id;
  CREATE INDEX IF NOT EXISTS idx_spare_part_write_off_acts_request_id
    ON spare_part_write_off_acts(request_id);
  DELETE FROM user_module_permissions WHERE module_code = 'spare_parts_acts';
  DELETE FROM app_modules WHERE code = 'spare_parts_acts';
  DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_ACTS_PERMISSION';
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_ACTS_PERMISSION'

# [REASON]: SP-F-002 -- new permission primitive surfaces automatically in the
# existing /admin/permissions UI (it iterates active app_modules generically).
# Same pattern as migrate_spare_parts_stage2._seed_modules.
NEW_MODULES = [
    ('spare_parts_acts',
     'Эҳтиёт қисмлар: далолатномаларни кўриш',
     'Запчасти: просмотр актов списания'),
]

# [REASON]: SP-F-002 -- unlike the deny-by-default Stage-1/2 permission rows,
# this one is auto-granted to users who could already reach acts through the
# issue/approve workflow, so tightening act_detail/act_pdf does not lock out
# the people whose daily work produced those acts in the first place.
AUTO_GRANT_SQL = """
    INSERT OR IGNORE INTO user_module_permissions
      (user_id, module_code, has_access)
    SELECT DISTINCT user_id, 'spare_parts_acts', 1
    FROM user_module_permissions
    WHERE module_code IN ('spare_parts_issue', 'spare_parts_approve')
      AND has_access = 1
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


def _seed_modules(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_modules'"
    )
    if cur.fetchone() is None:
        print("  app_modules table not found, module seed skipped "
              "(fresh install seeds via app start)")
        return
    for code, name_uz, name_ru in NEW_MODULES:
        cur.execute(
            "INSERT OR IGNORE INTO app_modules (code, name_uz, name_ru, is_active) "
            "VALUES (?, ?, ?, 1)",
            (code, name_uz, name_ru),
        )
        print("  module {} ensured".format(code))


def _auto_grant(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='user_module_permissions'"
    )
    if cur.fetchone() is None:
        print("  user_module_permissions table not found, auto-grant skipped")
        return
    cur.execute(AUTO_GRANT_SQL)
    granted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    cur.execute(
        "SELECT user_id FROM user_module_permissions "
        "WHERE module_code = 'spare_parts_acts' AND has_access = 1 "
        "ORDER BY user_id"
    )
    holders = [str(r[0]) for r in cur.fetchall()]
    print("  spare_parts_acts auto-granted to {} user(s) this run; "
          "current holders (user ids): {}".format(
              granted, ', '.join(holders) if holders else 'none'))


def _make_request_id_index_unique(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='spare_part_write_off_acts'"
    )
    if cur.fetchone() is None:
        print("  spare_part_write_off_acts table not found, index step skipped")
        return
    # [REASON]: SP-F-019 -- defensive re-verification even though DQ-022
    # confirmed zero duplicates: if data changed between the check and this
    # run, abort with a readable message instead of failing mid-DDL.
    cur.execute(
        "SELECT request_id, COUNT(*) c FROM spare_part_write_off_acts "
        "GROUP BY request_id HAVING c > 1"
    )
    dupes = cur.fetchall()
    if dupes:
        raise RuntimeError(
            "cannot make request_id unique -- requests with multiple acts "
            "exist: {}".format(
                ', '.join('#{} ({} acts)'.format(r, c) for r, c in dupes)))
    cur.execute("DROP INDEX IF EXISTS idx_spare_part_write_off_acts_request_id")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_spare_part_write_off_acts_request_id "
        "ON spare_part_write_off_acts(request_id)"
    )
    print("  idx_spare_part_write_off_acts_request_id recreated as UNIQUE")


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
             'SP-F-002/SP-F-019: spare_parts_acts permission row, auto-grant '
             'to issue/approve holders, UNIQUE index on '
             'spare_part_write_off_acts.request_id.'),
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

        print("Seeding permission module row...")
        _seed_modules(cur)

        print("Auto-granting spare_parts_acts to issue/approve holders...")
        _auto_grant(cur)

        print("Making write-off act request_id index UNIQUE...")
        _make_request_id_index_unique(cur)

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
