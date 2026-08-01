# -*- coding: utf-8 -*-
"""Migration DRONES_REATTACH_001 -- DRONE-007 re-attribution audit ledger.

Creates ONE table, drone_reattach_runs. Purely additive: no existing table,
column or row is created, modified or dropped.

Creates:
  - drone_reattach_runs -- one row per re-attribution pass over
                           drone_flights rows whose drone_unit_id is NULL.
                           performed_by / performed_at say who and when,
                           rows_matched / rows_updated say how much,
                           detail_json carries the per-spelling breakdown AND
                           the full list of affected flight ids, and
                           undone_at / undone_by mark a reverted pass.

[REASON]: drone_sync_logs is NOT reused for this. That model documents, and
the project's verification scripts rely on, the invariant
records_seen = records_new + records_duplicate + records_error, with
records_unresolved a subset of records_new. A re-attribution run stores no
flight and belongs in none of those buckets; recording it there would break
the invariant and make every check written against it wrong.

[REASON]: detail_json stores the COMPLETE list of affected flight ids, not
just the counts. That list is what makes undo exact rather than approximate:
it reverts precisely the rows this pass changed and can tell a row that has
since moved from one it still owns. At the current worst case (5 977
unattributed flights) the JSON is roughly 60 KB -- cheap for an audit ledger
that is written once per pass and read by one screen.

Safe / idempotent (same contract as migrate_drones_foundation_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS, so a second run over an existing table is a
    no-op even if the registry row were lost;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and turns a reader
    into a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_reattach_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the DRONE-007 commits leaves drone_reattach_runs in place; that is
  harmless -- nothing reads it once the model is gone, and db.create_all()
  does not drop tables. Dropping it is a manual step and is only correct
  AFTER the code revert. If a re-attribution pass was already applied on a
  live database, restore the blanks FIRST, per run, from
  detail_json.flight_ids -- once the table is dropped that list is gone:

    UPDATE drone_flights SET drone_unit_id = NULL WHERE id IN (<run ids>);
    DROP TABLE drone_reattach_runs;
    DELETE FROM schema_migrations WHERE name = 'DRONES_REATTACH_001';

  No rollback step edits or deletes any pre-existing row of any other table.
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
MIGRATION_ID = 'DRONES_REATTACH_001'
DESCRIPTION = ('DRONE-007: create drone_reattach_runs, the audit ledger of '
               're-attribution passes over drone_flights rows with '
               'drone_unit_id NULL (who, when, matched, updated, the '
               'per-spelling breakdown, the full affected id list, and the '
               'undo stamps). Additive only, one new table, no pre-existing '
               'row modified.')

CREATE_DRONE_REATTACH_RUNS = """
    CREATE TABLE IF NOT EXISTS drone_reattach_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        performed_by INTEGER REFERENCES users (id),
        performed_at DATETIME NOT NULL,
        rows_matched INTEGER NOT NULL DEFAULT 0,
        rows_updated INTEGER NOT NULL DEFAULT 0,
        detail_json  TEXT NOT NULL,
        undone_at    DATETIME,
        undone_by    INTEGER REFERENCES users (id)
    )
"""

EXPECTED_COLUMNS = ('id', 'performed_by', 'performed_at', 'rows_matched',
                    'rows_updated', 'detail_json', 'undone_at', 'undone_by')


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


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

        # [REASON]: the two REFERENCES targets must exist before the table is
        # created, or the ledger would point at nothing. drone_flights is not
        # referenced by a column here -- the ids live inside detail_json -- but
        # its absence means the drones module was never installed and this
        # migration is being run against the wrong database.
        for table in ('users', 'drone_flights'):
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        cur.execute(CREATE_DRONE_REATTACH_RUNS)

        # Postconditions before recording anything.
        if not _table_exists(cur, 'drone_reattach_runs'):
            raise RuntimeError('postcondition failed: table '
                               'drone_reattach_runs missing')
        columns = _column_names(cur, 'drone_reattach_runs')
        for name in EXPECTED_COLUMNS:
            if name not in columns:
                raise RuntimeError('postcondition failed: column %s missing '
                                   'on drone_reattach_runs' % name)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. Table drone_reattach_runs with %d columns is in place.'
          % len(EXPECTED_COLUMNS))


if __name__ == '__main__':
    run()
