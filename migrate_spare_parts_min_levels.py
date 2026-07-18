# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_MIN_LEVELS -- SP-MINSTOCK-004.

Creates the spare_part_min_levels table (minimum / safety stock level per
(warehouse, SKU) pair) plus its unique index. The minimum lives in its own
table rather than as a column on spare_part_inventory because an inventory
row is only created lazily on the first stock movement -- a minimum for a
SKU that has never been received at the warehouse would have nowhere to
live. min_quantity = 0 is never stored: clearing a minimum deletes the row.

NO BACKFILL. There is no pre-existing minimum-level data anywhere in the
database, so the table starts empty and only fills through the warehouse
screen after deploy.

This migration modifies NO pre-existing row and NO pre-existing table:
pre-existing tables are only read for the prerequisite check below; the
only writes are the CREATE TABLE / CREATE UNIQUE INDEX for the new table
and the INSERT of the migration's own registration row into
schema_migrations.

Safe / idempotent:
  - CREATE TABLE IF NOT EXISTS / CREATE UNIQUE INDEX IF NOT EXISTS.
  - Registers itself in schema_migrations and skips on re-run.
  - Runs as ONE transaction: an interrupted/failed run rolls back completely
    (including the CREATE TABLE -- SQLite DDL is transactional), so a re-run
    always starts from a clean slate.
  - A deploy-before-migrate ordering error is harmless here: if the new
    application code ran first, db.create_all() made an identical (possibly
    already populated) table, the IF NOT EXISTS statements are no-ops and no
    row is touched -- there is no backfill to collide with.

Hardened (RE-SP-011 pattern, same as migrate_spare_parts_reservations):
  - FAILS LOUDLY (and records nothing) if any prerequisite table is missing
    (spare_part_warehouses, spare_part_skus).
  - Verifies its postconditions BEFORE recording itself: the table and its
    unique index exist.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_min_levels.py
  .\\nssm.exe start TransportReport

Rollback:
  Everything this migration creates lives in exactly one new table and no
  pre-existing row is ever modified, so there is nothing else to undo:

    DROP TABLE IF EXISTS spare_part_min_levels;
    DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_MIN_LEVELS';
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_MIN_LEVELS'

ENSURE_REGISTRY = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""

# [REASON]: SP-MINSTOCK-004 -- mirrors models.SparePartMinLevel exactly
# (same column names, types, nullability). Defaults are ORM-level in the
# model and the application always supplies every NOT NULL column
# explicitly, so no DEFAULT clauses are needed here -- this matches what
# db.create_all() emits on a fresh install. id carries an explicit NOT
# NULL so PRAGMA table_info is byte-identical to a db.create_all() table
# (the model's primary key is NOT NULL); the column is still the rowid
# alias, so autoincrement behaviour is unchanged.
CREATE_MIN_LEVELS = """
    CREATE TABLE IF NOT EXISTS spare_part_min_levels (
        id           INTEGER NOT NULL PRIMARY KEY,
        warehouse_id INTEGER NOT NULL REFERENCES spare_part_warehouses(id),
        sku_id       INTEGER NOT NULL REFERENCES spare_part_skus(id),
        min_quantity FLOAT NOT NULL,
        note         VARCHAR(300) NOT NULL,
        updated_at   DATETIME,
        updated_by   INTEGER REFERENCES users(id)
    )
"""

CREATE_INDEXES = [
    # [REASON]: one minimum per (warehouse, SKU) pair, enforced by the
    # database itself -- the save route upserts against this constraint.
    ("uq_spare_part_min_levels_wh_sku",
     "CREATE UNIQUE INDEX IF NOT EXISTS uq_spare_part_min_levels_wh_sku"
     " ON spare_part_min_levels (warehouse_id, sku_id)"),
]

PREREQUISITE_TABLES = (
    'spare_part_warehouses',
    'spare_part_skus',
)


def _table_exists(cur, name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _index_exists(cur, name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,))
    return cur.fetchone() is not None


def _assert_prerequisites(cur):
    # [REASON]: RE-SP-011 -- fail loudly instead of silently registering on a
    # fresh/misordered install where the spare parts tables do not exist yet.
    missing = [t for t in PREREQUISITE_TABLES if not _table_exists(cur, t)]
    if missing:
        raise RuntimeError(
            "prerequisite table(s) missing: {} -- run the earlier spare "
            "parts migrations (stage2) first; NOT recording this "
            "migration".format(', '.join(missing)))


def _create_table_and_indexes(cur):
    cur.execute(CREATE_MIN_LEVELS)
    for _name, ddl in CREATE_INDEXES:
        cur.execute(ddl)
    print("  table spare_part_min_levels and its unique index ensured")


def _assert_postconditions(cur):
    # [REASON]: RE-SP-011 -- only a verified-complete run may be recorded.
    if not _table_exists(cur, 'spare_part_min_levels'):
        raise RuntimeError(
            "postcondition check failed: table spare_part_min_levels"
            " missing -- NOT recording this migration")
    for name, _ddl in CREATE_INDEXES:
        if not _index_exists(cur, name):
            raise RuntimeError(
                "postcondition check failed: index {} missing -- NOT"
                " recording this migration".format(name))


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
             'SP-MINSTOCK-004: spare_part_min_levels table + unique index'
             ' (minimum stock level per warehouse/SKU). No backfill; no'
             ' pre-existing row modified.'),
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

        print("Creating spare_part_min_levels table and unique index...")
        _create_table_and_indexes(cur)

        _assert_postconditions(cur)
        _record_migration(cur)
        con.commit()
        print("Migration {} applied successfully.".format(MIGRATION_ID))
        print("Summary: empty table created (no backfill -- minimums are"
              " entered through the warehouse screen).")

    except BaseException as exc:
        con.rollback()
        print("Migration {} FAILED: {}".format(MIGRATION_ID, exc), file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


if __name__ == '__main__':
    run()
