# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_STAGE1 -- Spare parts module Stage 1 redesign schema.

Creates:
  - spare_part_categories
  - spare_part_price_audit
  - spare_part_attachments
  - additive nullable/defaulted columns on spare_parts and
    spare_part_request_items (added only if missing)
  - supporting indexes
  - three new app_modules permission rows (INSERT OR IGNORE):
      spare_parts_catalog_manage, spare_parts_price_confirm,
      spare_parts_approve

Safe / idempotent:
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
  - ALTER TABLE ... ADD COLUMN only when the column is absent.
  - INSERT OR IGNORE for app_modules seed rows (code column is UNIQUE).
  - Registers itself in schema_migrations and skips on re-run.
  - Does not touch or delete any existing business data.

[REASON]: This project's migration registry (migration_utils.py /
migrate_000_migration_registry.py) uses `id INTEGER PRIMARY KEY AUTOINCREMENT`
and registers by the `name` column with `applied_at` NOT NULL. This script
follows the proven name-based pattern used by migrate_work_orders_001.py.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report-staging
  .\\nssm.exe stop TransportReportStaging
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_stage1.py
  .\\nssm.exe start TransportReportStaging

Rollback:
  This migration is purely additive (new tables + nullable/defaulted columns
  + INSERT OR IGNORE permission rows that no user is granted automatically).
  DROP TABLE IF EXISTS spare_part_attachments;
  DROP TABLE IF EXISTS spare_part_price_audit;
  DROP TABLE IF EXISTS spare_part_categories;
  DELETE FROM app_modules WHERE code IN ('spare_parts_catalog_manage',
    'spare_parts_price_confirm', 'spare_parts_approve');
  (Dropping the added columns requires a SQLite table rebuild. Safest
   rollback is to restore the pre-migration DB backup.)
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_STAGE1'

CREATE_CATEGORIES = """
    CREATE TABLE IF NOT EXISTS spare_part_categories (
        id          INTEGER PRIMARY KEY,
        name_ru     VARCHAR(200) NOT NULL,
        name_uz     VARCHAR(200) NOT NULL,
        parent_id   INTEGER REFERENCES spare_part_categories(id),
        kind        VARCHAR(20) NOT NULL DEFAULT 'unit',
        is_active   BOOLEAN DEFAULT 1,
        sort_order  INTEGER DEFAULT 0,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        created_by  INTEGER REFERENCES users(id)
    )
"""

CREATE_PRICE_AUDIT = """
    CREATE TABLE IF NOT EXISTS spare_part_price_audit (
        id         INTEGER PRIMARY KEY,
        item_id    INTEGER NOT NULL REFERENCES spare_part_request_items(id),
        old_price  FLOAT,
        new_price  FLOAT,
        action     VARCHAR(20) NOT NULL DEFAULT 'set',
        changed_by INTEGER REFERENCES users(id),
        changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""

CREATE_ATTACHMENTS = """
    CREATE TABLE IF NOT EXISTS spare_part_attachments (
        id                INTEGER PRIMARY KEY,
        item_id           INTEGER NOT NULL REFERENCES spare_part_request_items(id),
        file_path         VARCHAR(500) NOT NULL,
        original_filename VARCHAR(300) DEFAULT '',
        file_size         INTEGER DEFAULT 0,
        uploaded_by       INTEGER REFERENCES users(id),
        uploaded_at       DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_spare_part_categories_parent_id"
    " ON spare_part_categories(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_price_audit_item_id"
    " ON spare_part_price_audit(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_attachments_item_id"
    " ON spare_part_attachments(item_id)",
    # [REASON]: repeat-order warning engine scans prior items by
    # spare_part_id and requests by equipment_id + request_date.
    "CREATE INDEX IF NOT EXISTS idx_spare_part_request_items_spare_part_id"
    " ON spare_part_request_items(spare_part_id)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_requests_equipment_id_date"
    " ON spare_part_requests(equipment_id, request_date)",
]

# ALTER TABLE ... ADD COLUMN statements, applied only when the column is absent.
# [REASON]: SQLite requires a constant DEFAULT for NOT NULL columns added to a
# populated table; existing rows receive the DEFAULT value ('active'/'pending').
SPARE_PARTS_COLUMNS = [
    ("category_id", "ALTER TABLE spare_parts ADD COLUMN category_id INTEGER"
                    " REFERENCES spare_part_categories(id)"),
    ("status", "ALTER TABLE spare_parts ADD COLUMN status VARCHAR(20)"
               " NOT NULL DEFAULT 'active'"),
    ("merged_into_id", "ALTER TABLE spare_parts ADD COLUMN merged_into_id INTEGER"
                       " REFERENCES spare_parts(id)"),
    ("created_by", "ALTER TABLE spare_parts ADD COLUMN created_by INTEGER"
                   " REFERENCES users(id)"),
    ("source_request_item_id", "ALTER TABLE spare_parts ADD COLUMN"
                               " source_request_item_id INTEGER"
                               " REFERENCES spare_part_request_items(id)"),
    ("is_active", "ALTER TABLE spare_parts ADD COLUMN is_active BOOLEAN DEFAULT 1"),
]

REQUEST_ITEM_COLUMNS = [
    ("price", "ALTER TABLE spare_part_request_items ADD COLUMN price FLOAT"),
    ("price_status", "ALTER TABLE spare_part_request_items ADD COLUMN"
                     " price_status VARCHAR(20) NOT NULL DEFAULT 'pending'"),
    ("price_set_by", "ALTER TABLE spare_part_request_items ADD COLUMN"
                     " price_set_by INTEGER REFERENCES users(id)"),
    ("price_set_at", "ALTER TABLE spare_part_request_items ADD COLUMN"
                     " price_set_at DATETIME"),
]

# [REASON]: New permission primitives surface automatically in the existing
# /admin/permissions UI (it iterates active app_modules generically). Nobody
# is granted access here -- deny-by-default; only is_admin passes until an
# admin explicitly grants the module.
NEW_MODULES = [
    ('spare_parts_catalog_manage',
     'Эҳтиёт қисмлар: каталогни бошқариш',
     'Запчасти: управление каталогом'),
    ('spare_parts_price_confirm',
     'Эҳтиёт қисмлар: нархни тасдиқлаш',
     'Запчасти: подтверждение цен'),
    ('spare_parts_approve',
     'Эҳтиёт қисмлар: сўровни тасдиқлаш',
     'Запчасти: утверждение заявок'),
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
             'SPARE-STAGE1: categories, price audit, attachments, additive '
             'columns, indexes, 3 permission module rows.'),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_ID,),
        )


def _add_missing_columns(cur, table, columns):
    existing = [r[1] for r in cur.execute(
        "PRAGMA table_info({})".format(table)).fetchall()]
    for col_name, ddl in columns:
        if col_name not in existing:
            cur.execute(ddl)
            print("  added column {}.{}".format(table, col_name))
        else:
            print("  column {}.{} already present, skipped".format(table, col_name))


def _seed_modules(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_modules'"
    )
    if cur.fetchone() is None:
        # Fresh install path: app.py create_app() seeds app_modules on first
        # start via db.create_all(); nothing to do here.
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

        print("Creating tables...")
        cur.execute(CREATE_CATEGORIES)
        cur.execute(CREATE_PRICE_AUDIT)
        cur.execute(CREATE_ATTACHMENTS)

        print("Adding columns to spare_parts...")
        _add_missing_columns(cur, 'spare_parts', SPARE_PARTS_COLUMNS)

        print("Adding columns to spare_part_request_items...")
        _add_missing_columns(cur, 'spare_part_request_items', REQUEST_ITEM_COLUMNS)

        print("Creating indexes...")
        for sql in INDEXES:
            cur.execute(sql)

        print("Seeding permission module rows...")
        _seed_modules(cur)

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
