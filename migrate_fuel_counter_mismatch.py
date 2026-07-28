# -*- coding: utf-8 -*-
"""Migration FUEL_COUNTER_MISMATCH -- FUEL-SHIFT-001 (FUEL-BIG-001, commit 29).

Card 30 ("PERELIV") is Topaz's own record of a mismatch between a hose's
hardware counter and the software total. Dropping it from consumption at
ingest is correct and stays; but the signal itself was thrown away -- one
such row once carried -1 804 674.55 litres. This migration creates the log
table the sync will write those excluded rows into, so a per-dispenser
picture can accumulate. History before deployment was never stored and
cannot be recovered without changing the agent on the Topaz host (out of
scope here).

Creates:
  - table fuel_counter_mismatches:
        id            INTEGER PRIMARY KEY AUTOINCREMENT
        topaz_txn_id  TEXT
        topaz_col_id  INTEGER
        station_id    INTEGER NULL   -- an unresolvable column id must still
        warehouse_id  INTEGER NULL   --   be recorded; never drop the row
        txn_datetime  DATETIME
        card_number   TEXT
        quantity      REAL
        amount        REAL
        created_at    DATETIME
  - UNIQUE index on (topaz_col_id, topaz_txn_id) -- a repeated sync (the
    agent re-reads a three-day window) inserts nothing twice;
  - index on txn_datetime for the report queries.

Safe / idempotent:
  - refuses to run and exits non-zero when instance/transport.db is absent
    (sqlite3.connect would otherwise create an empty database);
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS everywhere;
  - registered through migration_utils (INSERT OR IGNORE semantics), skips
    itself on a re-run -- running twice is safe;
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and becomes a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_fuel_counter_mismatch.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate. Reverting the code leaves the
  table in place, which is harmless -- nothing else reads it. Dropping the
  table is a manual step and is only correct AFTER the ingest and report
  commits are reverted:

    DROP TABLE IF EXISTS fuel_counter_mismatches;
    -- both indexes are dropped with the table
    DELETE FROM schema_migrations WHERE name = 'FUEL_COUNTER_MISMATCH';

  No rollback step edits or deletes any pre-existing row.
"""

import os
import sqlite3
import sys

from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'FUEL_COUNTER_MISMATCH'
DESCRIPTION = ('FUEL-SHIFT-001: create fuel_counter_mismatches (log of card-30 '
               'PERELIV rows excluded at ingest) with a unique index on '
               '(topaz_col_id, topaz_txn_id) and an index on txn_datetime. '
               'No pre-existing row modified.')

CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS fuel_counter_mismatches (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        topaz_txn_id  TEXT,
        topaz_col_id  INTEGER,
        station_id    INTEGER,
        warehouse_id  INTEGER,
        txn_datetime  DATETIME,
        card_number   TEXT,
        quantity      REAL,
        amount        REAL,
        created_at    DATETIME
    )
"""

CREATE_UNIQUE_INDEX = """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_fuel_counter_mismatch_txn
        ON fuel_counter_mismatches (topaz_col_id, topaz_txn_id)
"""

CREATE_DT_INDEX = """
    CREATE INDEX IF NOT EXISTS ix_fuel_counter_mismatches_txn_datetime
        ON fuel_counter_mismatches (txn_datetime)
"""


def run():
    if not os.path.exists(DB_PATH):
        # [REASON]: sqlite3.connect(path) would CREATE an empty database when
        # the file is missing -- never do that in a migration.
        print('ERROR: database not found at %s - refusing to run.' % DB_PATH)
        sys.exit(2)

    ensure_schema_migrations_table()
    if is_migration_applied(MIGRATION_ID):
        print('Already applied. Nothing to do.')
        return

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute('BEGIN')
        cur.execute(CREATE_TABLE)
        cur.execute(CREATE_UNIQUE_INDEX)
        cur.execute(CREATE_DT_INDEX)

        # Postconditions before recording anything.
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='fuel_counter_mismatches'")
        if cur.fetchone() is None:
            raise RuntimeError('postcondition failed: table missing')
        cur.execute("SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name IN ('uq_fuel_counter_mismatch_txn', "
                    "'ix_fuel_counter_mismatches_txn_datetime')")
        if len(cur.fetchall()) != 2:
            raise RuntimeError('postcondition failed: index missing')

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. Table fuel_counter_mismatches and both indexes are in place.')


if __name__ == '__main__':
    run()
