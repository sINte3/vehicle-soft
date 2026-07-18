# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_RESERVATIONS -- SP-RESERVE-003.

Creates the spare_part_reservations table (a claim on warehouse stock held
by an approved request item) plus its three indexes, and backfills one
reservation row per SKU item of every already-approved request so existing
approved requests do not appear falsely issuable the moment the feature
ships.

Backfill rules (FIFO, oldest approved request first by request_date, id):
  - Only items with a non-null sku_id, and only when the request's
    organization has a spare parts warehouse. No-warehouse organizations
    simply get no rows; items without a SKU are outside the mechanism.
  - requested_quantity = item.quantity;
    quantity = round(max(0, min(item.quantity, remaining_available)), 3),
    where remaining_available starts at the (warehouse, sku) pair's
    spare_part_inventory.quantity (0 if no row) and is decremented by each
    granted reservation -- the backfill can never reserve more than exists.
  - created_at = request.reviewed_at (or now), created_by = request.reviewed_by,
    status = 'active', close_note = 'backfill SPARE_PARTS_RESERVATIONS'.

Safe / idempotent:
  - CREATE TABLE IF NOT EXISTS / CREATE [UNIQUE] INDEX IF NOT EXISTS.
  - Registers itself in schema_migrations and skips on re-run.
  - Runs as ONE transaction: an interrupted/failed run rolls back completely
    (including the CREATE TABLE -- SQLite DDL is transactional), so a re-run
    always starts from a clean slate.
  - ALL data this migration creates lives in exactly ONE new table
    (spare_part_reservations). It never modifies, updates or deletes a
    single row of any pre-existing table: pre-existing tables are only read
    (SELECT); the only writes are INSERTs into the new table and the
    INSERT of its own registration row into schema_migrations.

Hardened (RE-SP-011 pattern, same as migrate_spare_parts_name_uz):
  - FAILS LOUDLY (and records nothing) if any prerequisite table is missing
    (spare_part_requests, spare_part_request_items, spare_part_warehouses,
    spare_part_skus, spare_part_inventory).
  - FAILS LOUDLY if spare_part_reservations already contains rows while the
    migration is not recorded (deploy-before-migrate ordering error: the new
    application code was started before this script ran and has already
    created live reservations; sort that out manually instead of guessing).
  - Verifies its postconditions BEFORE recording itself: the table and all
    three indexes exist, and for every (warehouse, sku) pair the sum of
    ACTIVE reserved quantities is <= the pair's spare_part_inventory.quantity
    (float tolerance 0.001).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_reservations.py
  .\\nssm.exe start TransportReport

Rollback:
  Every row this increment creates lives in exactly one new table and no
  pre-existing row is ever modified, so there is nothing else to undo:

    DROP TABLE IF EXISTS spare_part_reservations;
    DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_RESERVATIONS';
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_RESERVATIONS'

# Tolerance for float quantity comparisons (quantities are rounded to 3
# decimals throughout the spare parts module).
FLOAT_TOLERANCE = 0.001

ENSURE_REGISTRY = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""

# [REASON]: SP-RESERVE-003 -- mirrors models.SparePartReservation exactly
# (same column names, types, nullability). Defaults are ORM-level in the
# model, and both the backfill below and the application always supply every
# NOT NULL column explicitly, so no DEFAULT clauses are needed here -- this
# matches what db.create_all() emits on a fresh install.
CREATE_RESERVATIONS = """
    CREATE TABLE IF NOT EXISTS spare_part_reservations (
        id                 INTEGER PRIMARY KEY,
        request_id         INTEGER NOT NULL REFERENCES spare_part_requests(id),
        request_item_id    INTEGER NOT NULL REFERENCES spare_part_request_items(id),
        warehouse_id       INTEGER NOT NULL REFERENCES spare_part_warehouses(id),
        sku_id             INTEGER NOT NULL REFERENCES spare_part_skus(id),
        quantity           FLOAT NOT NULL,
        requested_quantity FLOAT NOT NULL,
        status             VARCHAR(20) NOT NULL,
        created_at         DATETIME,
        created_by         INTEGER REFERENCES users(id),
        closed_at          DATETIME,
        closed_by          INTEGER REFERENCES users(id),
        close_note         VARCHAR(300) NOT NULL
    )
"""

CREATE_INDEXES = [
    ("idx_spare_part_reservations_wh_sku_status",
     "CREATE INDEX IF NOT EXISTS idx_spare_part_reservations_wh_sku_status"
     " ON spare_part_reservations (warehouse_id, sku_id, status)"),
    ("idx_spare_part_reservations_request_id",
     "CREATE INDEX IF NOT EXISTS idx_spare_part_reservations_request_id"
     " ON spare_part_reservations (request_id)"),
    # [REASON]: partial UNIQUE -- at most one ACTIVE reservation per request
    # item, enforced by the database itself; consumed/released history rows
    # for the same item remain possible.
    ("uq_spare_part_reservations_active_item",
     "CREATE UNIQUE INDEX IF NOT EXISTS uq_spare_part_reservations_active_item"
     " ON spare_part_reservations (request_item_id) WHERE status = 'active'"),
]

PREREQUISITE_TABLES = (
    'spare_part_requests',
    'spare_part_request_items',
    'spare_part_warehouses',
    'spare_part_skus',
    'spare_part_inventory',
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
            "parts migrations (stage1/stage2) first; NOT recording this "
            "migration".format(', '.join(missing)))
    # [REASON]: deploy-before-migrate guard -- if the new application code
    # already ran (db.create_all made the table) and users approved requests,
    # live reservation rows exist; backfilling on top of them would collide
    # with the partial unique index mid-transaction. Refuse with a clear
    # message instead of a raw IntegrityError.
    if _table_exists(cur, 'spare_part_reservations'):
        cur.execute("SELECT COUNT(*) FROM spare_part_reservations")
        existing = cur.fetchone()[0]
        if existing:
            raise RuntimeError(
                "spare_part_reservations already contains {} row(s) but the "
                "migration is not recorded -- the new application code was "
                "started before this script ran. Resolve manually (see the "
                "module docstring); NOT recording this migration"
                .format(existing))


def _create_table_and_indexes(cur):
    cur.execute(CREATE_RESERVATIONS)
    for _name, ddl in CREATE_INDEXES:
        cur.execute(ddl)
    print("  table spare_part_reservations and 3 indexes ensured")


def _backfill(cur):
    """One reservation row per SKU item of every approved request (FIFO).

    Returns (rows_inserted, requests_covered, items_short).
    """
    # Organization -> warehouse (exactly one per organization by UNIQUE).
    cur.execute("SELECT organization_id, id FROM spare_part_warehouses")
    wh_by_org = {row[0]: row[1] for row in cur.fetchall()}

    # Running available per (warehouse, sku), seeded from current on-hand.
    cur.execute("SELECT warehouse_id, sku_id, quantity FROM spare_part_inventory")
    remaining = {(row[0], row[1]): float(row[2] or 0) for row in cur.fetchall()}

    # [REASON]: FIFO by the business date of the request (oldest first), so
    # the earliest approved request gets first claim on today's stock --
    # matches how the warehouse would issue them.
    cur.execute(
        "SELECT id, organization_id, reviewed_at, reviewed_by"
        " FROM spare_part_requests WHERE status = 'approved'"
        " ORDER BY request_date, id")
    approved = cur.fetchall()

    now_text = str(datetime.utcnow())
    rows_inserted = 0
    requests_covered = 0
    items_short = 0
    skipped_no_warehouse = 0

    for req_id, org_id, reviewed_at, reviewed_by in approved:
        warehouse_id = wh_by_org.get(org_id)
        if warehouse_id is None:
            # No warehouse -> outside the mechanism entirely, no rows.
            skipped_no_warehouse += 1
            continue
        cur.execute(
            "SELECT id, sku_id, quantity FROM spare_part_request_items"
            " WHERE request_id = ? AND sku_id IS NOT NULL ORDER BY id",
            (req_id,))
        items = cur.fetchall()
        if not items:
            continue
        requests_covered += 1
        for item_id, sku_id, item_qty in items:
            requested = float(item_qty or 0)
            key = (warehouse_id, sku_id)
            available = remaining.get(key, 0.0)
            granted = round(max(0.0, min(requested, available)), 3)
            remaining[key] = available - granted
            if granted + FLOAT_TOLERANCE < requested:
                items_short += 1
            cur.execute(
                "INSERT INTO spare_part_reservations"
                " (request_id, request_item_id, warehouse_id, sku_id,"
                "  quantity, requested_quantity, status, created_at,"
                "  created_by, closed_at, closed_by, close_note)"
                " VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, NULL, ?)",
                (req_id, item_id, warehouse_id, sku_id,
                 granted, requested, reviewed_at or now_text,
                 reviewed_by, 'backfill SPARE_PARTS_RESERVATIONS'))
            rows_inserted += 1

    print("  backfill: {} approved request(s) found, {} covered with"
          " reservation rows, {} skipped (organization has no warehouse)"
          .format(len(approved), requests_covered, skipped_no_warehouse))
    return rows_inserted, requests_covered, items_short


def _assert_postconditions(cur):
    # [REASON]: RE-SP-011 -- only a verified-complete run may be recorded.
    if not _table_exists(cur, 'spare_part_reservations'):
        raise RuntimeError(
            "postcondition check failed: table spare_part_reservations"
            " missing -- NOT recording this migration")
    for name, _ddl in CREATE_INDEXES:
        if not _index_exists(cur, name):
            raise RuntimeError(
                "postcondition check failed: index {} missing -- NOT"
                " recording this migration".format(name))
    # [REASON]: the backfill formula can never reserve more than exists, so a
    # violation here means a logic error or unexpected concurrent write --
    # refuse to record rather than ship an over-reserved warehouse.
    cur.execute(
        "SELECT r.warehouse_id, r.sku_id, SUM(r.quantity) AS reserved,"
        " COALESCE((SELECT i.quantity FROM spare_part_inventory i"
        "           WHERE i.warehouse_id = r.warehouse_id"
        "             AND i.sku_id = r.sku_id), 0) AS on_hand"
        " FROM spare_part_reservations r"
        " WHERE r.status = 'active'"
        " GROUP BY r.warehouse_id, r.sku_id")
    for warehouse_id, sku_id, reserved, on_hand in cur.fetchall():
        if float(reserved or 0) > float(on_hand or 0) + FLOAT_TOLERANCE:
            raise RuntimeError(
                "postcondition check failed: active reservations {} exceed"
                " on-hand {} for warehouse {} sku {} -- NOT recording this"
                " migration".format(reserved, on_hand, warehouse_id, sku_id))


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
             'SP-RESERVE-003: spare_part_reservations table + 3 indexes;'
             ' FIFO backfill of active reservations for already-approved'
             ' requests. No pre-existing row modified.'),
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

        print("Creating spare_part_reservations table and indexes...")
        _create_table_and_indexes(cur)

        print("Backfilling reservations for approved requests...")
        rows_inserted, requests_covered, items_short = _backfill(cur)

        _assert_postconditions(cur)
        _record_migration(cur)
        con.commit()
        print("Migration {} applied successfully.".format(MIGRATION_ID))
        print("Summary: {} reservation row(s) inserted covering {}"
              " request(s); {} item(s) left short of stock."
              .format(rows_inserted, requests_covered, items_short))

    except BaseException as exc:
        con.rollback()
        print("Migration {} FAILED: {}".format(MIGRATION_ID, exc), file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


if __name__ == '__main__':
    run()
