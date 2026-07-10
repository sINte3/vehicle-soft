# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_STAGE2 -- Spare parts module Stage 2 schema.

Creates (Task 2 -- fuzzy search):
  - index idx_spare_parts_status_active on spare_parts(status, is_active)
    (the fuzzy catalog search fetches the full active candidate list on
    every keystroke-triggered request)

Creates (Task 3 -- SKU catalog):
  - spare_part_skus (brand/article/supplier variants of a canonical part;
    last_price/avg_price are informational, written one-way by the price
    confirm workflow)
  - additive nullable column spare_part_request_items.sku_id
  - supporting indexes

Creates (Task 4 -- warehouses + inventory + movements):
  - spare_part_warehouses (one per organization, UNIQUE(organization_id)
    enforced at the DB level)
  - spare_part_inventory (one row per warehouse+SKU, UNIQUE(warehouse_id,
    sku_id); rows are created lazily on first movement -- no zero seeding)
  - spare_part_inventory_movements (signed quantity + balance_after snapshot;
    every quantity write goes through a movement in the same transaction)
  - one new app_modules permission row (INSERT OR IGNORE):
      spare_parts_inventory_manage

Safe / idempotent:
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
  - ALTER TABLE ... ADD COLUMN only when the column is absent.
  - INSERT OR IGNORE for app_modules seed rows (code column is UNIQUE).
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
  DROP INDEX IF EXISTS idx_spare_part_skus_spare_part_id;
  DROP INDEX IF EXISTS idx_spare_part_request_items_sku_id;
  DROP INDEX IF EXISTS idx_spare_part_inv_movements_wh_sku;
  DROP INDEX IF EXISTS idx_spare_part_inv_movements_created_at;
  DROP INDEX IF EXISTS idx_spare_part_inv_movements_reference;
  DROP TABLE IF EXISTS spare_part_inventory_movements;
  DROP TABLE IF EXISTS spare_part_inventory;
  DROP TABLE IF EXISTS spare_part_warehouses;
  DROP TABLE IF EXISTS spare_part_skus;
  DELETE FROM app_modules WHERE code IN ('spare_parts_inventory_manage');
  DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_STAGE2';
  (Dropping the added spare_part_request_items.sku_id column requires a
   SQLite table rebuild. As with prior spare-parts migrations, the real
   safety net is the pre-migration database backup.)
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_STAGE2'

CREATE_SKUS = """
    CREATE TABLE IF NOT EXISTS spare_part_skus (
        id             INTEGER PRIMARY KEY,
        spare_part_id  INTEGER NOT NULL REFERENCES spare_parts(id),
        brand          VARCHAR(200) DEFAULT '',
        article_number VARCHAR(100) DEFAULT '',
        supplier       VARCHAR(200) DEFAULT '',
        last_price     FLOAT,
        avg_price      FLOAT,
        is_active      BOOLEAN DEFAULT 1,
        created_by     INTEGER REFERENCES users(id),
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

# ALTER TABLE ... ADD COLUMN statements, applied only when the column is absent.
REQUEST_ITEM_COLUMNS = [
    ("sku_id", "ALTER TABLE spare_part_request_items ADD COLUMN sku_id INTEGER"
               " REFERENCES spare_part_skus(id)"),
]

CREATE_WAREHOUSES = """
    CREATE TABLE IF NOT EXISTS spare_part_warehouses (
        id              INTEGER PRIMARY KEY,
        organization_id INTEGER NOT NULL UNIQUE REFERENCES organizations(id),
        name            VARCHAR(200) NOT NULL,
        is_active       BOOLEAN DEFAULT 1,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

CREATE_INVENTORY = """
    CREATE TABLE IF NOT EXISTS spare_part_inventory (
        id           INTEGER PRIMARY KEY,
        warehouse_id INTEGER NOT NULL REFERENCES spare_part_warehouses(id),
        sku_id       INTEGER NOT NULL REFERENCES spare_part_skus(id),
        quantity     FLOAT NOT NULL DEFAULT 0,
        unit         VARCHAR(30) DEFAULT 'dona',
        updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_spare_part_inventory_wh_sku UNIQUE (warehouse_id, sku_id)
    )
"""

CREATE_MOVEMENTS = """
    CREATE TABLE IF NOT EXISTS spare_part_inventory_movements (
        id             INTEGER PRIMARY KEY,
        warehouse_id   INTEGER NOT NULL REFERENCES spare_part_warehouses(id),
        sku_id         INTEGER NOT NULL REFERENCES spare_part_skus(id),
        movement_type  VARCHAR(20) NOT NULL,
        quantity       FLOAT NOT NULL,
        balance_after  FLOAT NOT NULL,
        reference_type VARCHAR(30) NOT NULL DEFAULT 'manual',
        reference_id   INTEGER,
        note           TEXT NOT NULL DEFAULT '',
        created_by     INTEGER REFERENCES users(id),
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

# [REASON]: New permission primitive surfaces automatically in the existing
# /admin/permissions UI (it iterates active app_modules generically). Nobody
# is granted access here -- deny-by-default; only is_admin passes until an
# admin explicitly grants the module. Same pattern as the prior spare-parts
# permission rows.
NEW_MODULES = [
    ('spare_parts_inventory_manage',
     'Эҳтиёт қисмлар: омбор ва қолдиқлар',
     'Запчасти: склад и остатки'),
]

INDEXES = [
    # [REASON]: SPARE-STAGE2 fuzzy search scans active catalog parts on every
    # keystroke-triggered search request; keep the candidate fetch indexed.
    "CREATE INDEX IF NOT EXISTS idx_spare_parts_status_active"
    " ON spare_parts(status, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_skus_spare_part_id"
    " ON spare_part_skus(spare_part_id)",
    # [REASON]: the SKU price-stats recompute scans confirm audit rows of all
    # items referencing one SKU.
    "CREATE INDEX IF NOT EXISTS idx_spare_part_request_items_sku_id"
    " ON spare_part_request_items(sku_id)",
    # [REASON]: movement journal is queried per warehouse (screen) and per
    # (warehouse, sku) pair (audit-sum check), plus newest-first by date.
    "CREATE INDEX IF NOT EXISTS idx_spare_part_inv_movements_wh_sku"
    " ON spare_part_inventory_movements(warehouse_id, sku_id)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_inv_movements_created_at"
    " ON spare_part_inventory_movements(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_inv_movements_reference"
    " ON spare_part_inventory_movements(reference_type, reference_id)",
]


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


def _add_missing_columns(cur, table, columns):
    existing = [r[1] for r in cur.execute(
        "PRAGMA table_info({})".format(table)).fetchall()]
    for col_name, ddl in columns:
        if col_name not in existing:
            cur.execute(ddl)
            print("  added column {}.{}".format(table, col_name))
        else:
            print("  column {}.{} already present, skipped".format(table, col_name))

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
             'SPARE-STAGE2: fuzzy search index; SKU catalog table, '
             'request-item sku_id column; warehouses, inventory, movements, '
             '1 permission module row; indexes.'),
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

        print("Creating tables...")
        cur.execute(CREATE_SKUS)
        cur.execute(CREATE_WAREHOUSES)
        cur.execute(CREATE_INVENTORY)
        cur.execute(CREATE_MOVEMENTS)

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
