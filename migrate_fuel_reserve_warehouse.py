# -*- coding: utf-8 -*-
"""Migration FUEL_RESERVE_WAREHOUSE -- FUEL-RESERVE (increment C).

Stands up the off-balance reserve («Захира» / «Резерв») inside the fuel
module. Pakhtasanoattrans keeps this reserve outside the system today (only
in an Excel sheet); its diesel physically leaves through the same dispenser,
so Topaz records it as ordinary Pakhtasanoattrans expense. This migration
lays the schema and the one deterministic warehouse row the application code
needs; the actual attribution (which Topaz transactions belong to the
reserve) is done by a human in the marking screen, never by this migration.

It does exactly three things, in order:

  (a) Adds the column fuel_warehouses.show_in_ui (INTEGER NOT NULL DEFAULT 0).
      This flag means "show this warehouse in the fuel module even though it
      owns no target station". It HIDES nothing: warehouses that qualify
      through their stations stay visible regardless of the flag. Guarded by
      PRAGMA table_info so a re-run is a no-op.

  (b) Creates the table fuel_transaction_reattributions (the mark that moves a
      Topaz transaction's litres from its station's warehouse to the reserve,
      at the report layer only -- fuel_transactions2 is never edited), a
      PARTIAL UNIQUE index on transaction_id where coalesce(is_deleted,0)=0
      (at most one ACTIVE mark per transaction while the mark/unmark history
      survives), and plain indexes on transaction_id and target_warehouse_id
      for the report queries.

  (c) Creates the reserve warehouse itself, idempotently, matching on the
      exact name «Резерв». Its organization_id is resolved FROM the existing
      Pakhtasanoattrans warehouse (the warehouse whose stations carry topaz_id
      811971 or 825241), NOT from a hardcoded id -- the backlog item
      AZS-ORG-REFACTOR records duplicate organisation ids 20-24, so a
      hardcoded value would likely pick a duplicate. The row gets
      show_in_ui = 1 so the reserve becomes visible everywhere in the module.
      The lookup fails loudly (recording nothing) if it returns no
      organisation.

NOT seeded here: the reserve's initial balance of 10,429 L (dated 2026-05-01).
The owner enters it through the existing /fuel/initial-balance screen once the
warehouse is visible. That keeps this migration to schema plus one
deterministic row, and gives the owner an audited record of who entered the
figure.

This migration modifies NO pre-existing row: pre-existing tables are only read
for the prerequisite/organisation lookups; the only writes are the guarded
ALTER TABLE ADD COLUMN, the CREATE TABLE/INDEX statements, the single INSERT
of the reserve warehouse row (only when it does not already exist), and the
INSERT of this migration's own registration row into schema_migrations.

Safe / idempotent:
  - PRAGMA table_info is read before ALTER TABLE ADD COLUMN, so a re-run adds
    nothing. show_in_ui is added with DEFAULT 0, which SQLite writes to every
    existing row without a table rewrite.
  - CREATE TABLE / CREATE [UNIQUE] INDEX IF NOT EXISTS everywhere.
  - The reserve warehouse is matched on its exact name «Резерв»; on a re-run it
    is found and NOT duplicated (postcondition asserts exactly one).
  - Runs as ONE transaction: an interrupted/failed run rolls back completely
    (SQLite DDL is transactional), so a re-run always starts clean.
  - Registers itself in schema_migrations and skips on re-run.

Hardened (RE-SP-011 pattern, same as migrate_spare_parts_reservations):
  - FAILS LOUDLY (and records nothing) if any prerequisite table is missing
    (fuel_warehouses, fuel_stations2, organizations, users).
  - FAILS LOUDLY if the Pakhtasanoattrans organisation lookup returns nothing.
  - Verifies its postconditions BEFORE recording itself: the column exists, the
    table and all three indexes exist, and exactly one «Резерв» warehouse
    exists carrying show_in_ui = 1 and a non-null organization_id.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_fuel_reserve_warehouse.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rolls back independently of data (see the task's section 11). The data
  rollback is:

    DROP TABLE IF EXISTS fuel_transaction_reattributions;
    -- the partial unique index and the two plain indexes are dropped with the
    -- table; DROP INDEX lines are therefore not needed.
    DELETE FROM schema_migrations WHERE name = 'FUEL_RESERVE_WAREHOUSE';

  Delete the reserve warehouse row ONLY if it has no reattributions and no
  initial balance (and, by the module's own delete guard, no receipts):

    DELETE FROM fuel_warehouses
     WHERE name = 'Резерв'
       AND id NOT IN (SELECT target_warehouse_id FROM fuel_transaction_reattributions)
       AND id NOT IN (SELECT warehouse_id FROM fuel_initial_balances)
       AND id NOT IN (SELECT warehouse_id FROM fuel_receipts2);

  fuel_warehouses.show_in_ui STAYS: SQLite before 3.35 cannot DROP COLUMN, and
  the column is DEFAULT 0 and inert once the application code is reverted.

No rollback step edits or deletes a fuel_transactions2 row.
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'FUEL_RESERVE_WAREHOUSE'
DESCRIPTION = ('FUEL-RESERVE: add fuel_warehouses.show_in_ui, create '
               'fuel_transaction_reattributions (+ partial unique index and '
               'two plain indexes), and create the reserve warehouse «Резерв» '
               'hung off Pakhtasanoattrans with show_in_ui=1. No initial '
               'balance seeded; no pre-existing row modified.')

# [REASON]: FUEL-RESERVE -- the reserve is matched on this EXACT Cyrillic name
# so a re-run finds the existing row instead of creating a duplicate. This is
# the Russian label; the Uzbek label «Захира» is a UI-only string.
RESERVE_WAREHOUSE_NAME = 'Резерв'

# [REASON]: FUEL-RESERVE -- resolve the reserve's organisation FROM the
# Pakhtasanoattrans warehouse, never from a hardcoded id. These two topaz_ids
# are Pakhtasanoattrans stations (they are also in FUEL_TARGET_TOPAZ_IDS); the
# warehouse that owns either one is Pakhtasanoattrans, and its organization_id
# is the correct, non-duplicate organisation to hang the reserve off of.
PAKHTA_STATION_TOPAZ_IDS = (811971, 825241)

PREREQUISITE_TABLES = (
    'fuel_warehouses',
    'fuel_stations2',
    'organizations',
    'users',
)

ENSURE_REGISTRY = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""

# [REASON]: FUEL-RESERVE -- mirrors models.FuelTransactionReattribution exactly
# (same column names, types, nullability). This is what db.create_all() emits on
# a fresh install; the migration brings existing production databases to the
# same shape.
CREATE_REATTRIBUTIONS = """
    CREATE TABLE IF NOT EXISTS fuel_transaction_reattributions (
        id                  INTEGER PRIMARY KEY,
        transaction_id      INTEGER NOT NULL REFERENCES fuel_transactions2(id),
        target_warehouse_id INTEGER NOT NULL REFERENCES fuel_warehouses(id),
        note                TEXT,
        created_by          INTEGER REFERENCES users(id),
        created_at          DATETIME NOT NULL DEFAULT current_timestamp,
        is_deleted          INTEGER NOT NULL DEFAULT 0,
        deleted_by          INTEGER REFERENCES users(id),
        deleted_at          DATETIME
    )
"""

CREATE_INDEXES = [
    # [REASON]: FUEL-RESERVE -- partial UNIQUE, mirroring
    # uq_spare_part_reservations_active_item: at most one ACTIVE mark per
    # transaction, enforced by the database itself, while the full history of
    # marks and unmarks for that transaction survives as is_deleted=1 rows.
    ("uq_fuel_reattribution_active_txn",
     "CREATE UNIQUE INDEX IF NOT EXISTS uq_fuel_reattribution_active_txn"
     " ON fuel_transaction_reattributions (transaction_id)"
     " WHERE coalesce(is_deleted, 0) = 0"),
    ("idx_fuel_reattribution_txn",
     "CREATE INDEX IF NOT EXISTS idx_fuel_reattribution_txn"
     " ON fuel_transaction_reattributions (transaction_id)"),
    ("idx_fuel_reattribution_target_wh",
     "CREATE INDEX IF NOT EXISTS idx_fuel_reattribution_target_wh"
     " ON fuel_transaction_reattributions (target_warehouse_id)"),
]


def _table_exists(cur, name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _index_exists(cur, name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,))
    return cur.fetchone() is not None


def _warehouse_columns(cur):
    cur.execute("PRAGMA table_info(fuel_warehouses)")
    return {row[1] for row in cur.fetchall()}


def _assert_prerequisites(cur):
    # [REASON]: RE-SP-011 -- fail loudly instead of silently registering on a
    # fresh/misordered install where the fuel tables do not exist yet.
    missing = [t for t in PREREQUISITE_TABLES if not _table_exists(cur, t)]
    if missing:
        raise RuntimeError(
            "prerequisite table(s) missing: {} -- run the earlier fuel "
            "migrations first; NOT recording this migration"
            .format(', '.join(missing)))


def _add_show_in_ui_column(cur):
    if 'show_in_ui' in _warehouse_columns(cur):
        print("  column fuel_warehouses.show_in_ui already present -- skipped")
        return
    # [REASON]: FUEL-RESERVE -- INTEGER NOT NULL DEFAULT 0 is a constant default,
    # which SQLite ADD COLUMN accepts and applies to every existing row without
    # a table rewrite. Existing warehouses keep visibility through their stations
    # (fuel_target_warehouse_ids), so 0 changes nothing for them.
    cur.execute(
        "ALTER TABLE fuel_warehouses"
        " ADD COLUMN show_in_ui INTEGER NOT NULL DEFAULT 0")
    print("  column fuel_warehouses.show_in_ui added")


def _create_table_and_indexes(cur):
    cur.execute(CREATE_REATTRIBUTIONS)
    for _name, ddl in CREATE_INDEXES:
        cur.execute(ddl)
    print("  table fuel_transaction_reattributions and its 3 indexes ensured")


def _resolve_pakhta_org_id(cur):
    # [REASON]: FUEL-RESERVE -- read the organisation FROM the Pakhtasanoattrans
    # warehouse (whichever owns a station with one of these topaz_ids), never a
    # hardcoded id (AZS-ORG-REFACTOR: duplicate org ids 20-24). Fail loudly if
    # nothing matches -- the reserve must not be created with a guessed org.
    placeholders = ','.join('?' for _ in PAKHTA_STATION_TOPAZ_IDS)
    cur.execute(
        "SELECT w.organization_id FROM fuel_warehouses w"
        " JOIN fuel_stations2 s ON s.warehouse_id = w.id"
        " WHERE s.topaz_id IN ({})"
        "   AND w.organization_id IS NOT NULL"
        " LIMIT 1".format(placeholders),
        PAKHTA_STATION_TOPAZ_IDS)
    row = cur.fetchone()
    if not row or row[0] is None:
        raise RuntimeError(
            "Pakhtasanoattrans lookup failed: no warehouse owns a station with "
            "topaz_id in {} (or its organization_id is NULL) -- cannot resolve "
            "the reserve's organisation; NOT recording this migration"
            .format(PAKHTA_STATION_TOPAZ_IDS))
    return int(row[0])


def _create_reserve_warehouse(cur, org_id):
    cur.execute("SELECT id, show_in_ui FROM fuel_warehouses WHERE name = ?",
                (RESERVE_WAREHOUSE_NAME,))
    existing = cur.fetchone()
    if existing:
        # [REASON]: idempotent -- the reserve row already exists (a prior run, or
        # created by hand). Do not duplicate it. Ensure the visibility flag is on
        # so the module shows it; leave everything else as the operator has it.
        wh_id = existing[0]
        if not existing[1]:
            cur.execute("UPDATE fuel_warehouses SET show_in_ui = 1 WHERE id = ?",
                        (wh_id,))
            print("  reserve warehouse «{}» already existed -- show_in_ui set to 1"
                  .format(RESERVE_WAREHOUSE_NAME))
        else:
            print("  reserve warehouse «{}» already present -- skipped"
                  .format(RESERVE_WAREHOUSE_NAME))
        return wh_id

    cur.execute(
        "INSERT INTO fuel_warehouses (name, organization_id, notes, created_at, show_in_ui)"
        " VALUES (?, ?, '', ?, 1)",
        (RESERVE_WAREHOUSE_NAME, org_id, datetime.utcnow().isoformat(timespec='seconds')))
    print("  reserve warehouse «{}» created (organization_id={}, show_in_ui=1)"
          .format(RESERVE_WAREHOUSE_NAME, org_id))
    return cur.lastrowid


def _assert_postconditions(cur):
    # [REASON]: RE-SP-011 -- only a verified-complete run may be recorded.
    if 'show_in_ui' not in _warehouse_columns(cur):
        raise RuntimeError(
            "postcondition check failed: fuel_warehouses.show_in_ui missing -- "
            "NOT recording this migration")
    if not _table_exists(cur, 'fuel_transaction_reattributions'):
        raise RuntimeError(
            "postcondition check failed: table fuel_transaction_reattributions "
            "missing -- NOT recording this migration")
    for name, _ddl in CREATE_INDEXES:
        if not _index_exists(cur, name):
            raise RuntimeError(
                "postcondition check failed: index {} missing -- NOT recording "
                "this migration".format(name))
    cur.execute(
        "SELECT id, organization_id, show_in_ui FROM fuel_warehouses WHERE name = ?",
        (RESERVE_WAREHOUSE_NAME,))
    reserve_rows = cur.fetchall()
    if len(reserve_rows) != 1:
        raise RuntimeError(
            "postcondition check failed: expected exactly one «{}» warehouse, "
            "found {} -- NOT recording this migration"
            .format(RESERVE_WAREHOUSE_NAME, len(reserve_rows)))
    _id, org_id, show_in_ui = reserve_rows[0]
    if org_id is None:
        raise RuntimeError(
            "postcondition check failed: reserve warehouse has NULL "
            "organization_id -- NOT recording this migration")
    if int(show_in_ui or 0) != 1:
        raise RuntimeError(
            "postcondition check failed: reserve warehouse show_in_ui != 1 -- "
            "NOT recording this migration")


def _migration_applied(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
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
            (MIGRATION_ID, datetime.utcnow().isoformat(timespec='seconds'), DESCRIPTION))
    else:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_ID,))


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

        print("Adding fuel_warehouses.show_in_ui...")
        _add_show_in_ui_column(cur)

        print("Creating fuel_transaction_reattributions table and indexes...")
        _create_table_and_indexes(cur)

        print("Resolving reserve organisation from Pakhtasanoattrans...")
        org_id = _resolve_pakhta_org_id(cur)

        print("Ensuring reserve warehouse «{}»...".format(RESERVE_WAREHOUSE_NAME))
        _create_reserve_warehouse(cur, org_id)

        _assert_postconditions(cur)
        _record_migration(cur)
        con.commit()
        print("Migration {} applied successfully.".format(MIGRATION_ID))
        print("Summary: show_in_ui column ensured, reattribution table + 3 "
              "indexes ensured, exactly one reserve warehouse present. Initial "
              "balance NOT seeded -- enter it via /fuel/initial-balance.")

    except BaseException as exc:
        con.rollback()
        print("Migration {} FAILED: {}".format(MIGRATION_ID, exc), file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


if __name__ == '__main__':
    run()
