# -*- coding: utf-8 -*-
"""Migration FIN_RECON_STORAGE_001 -- storage of immutable financial
reconciliation snapshots of the drone operators (one snapshot per ledger
month and version).

Creates ELEVEN tables, their indexes and the triggers that make them
immutable. Purely additive: no existing table, column or row is created,
modified, read or dropped. drone_works, drone_flights, dji_* and every other
table of the application are not referenced by any statement here.

Creates:
  - fin_recon_period_snapshot   -- one row per (period, version): status,
                                   empty approval fields, hashes of the
                                   content, the snapshot file and the package
                                   manifest, the canonical snapshot JSON;
  - fin_recon_source            -- every source file of the snapshot with
                                   its SHA-256 (books, register, owner
                                   decisions, DJI exports, technical export);
  - fin_recon_metric            -- the declared control figures with their
                                   meaning, unit and evidence type;
  - fin_recon_operator_row      -- per operator: book area, accrual, total
                                   recognized as paid, difference;
  - fin_recon_payout            -- payout records: book text or owner's
                                   interpretation, never mixed;
  - fin_recon_work_row          -- per work of the books: source file, sheet
                                   and row, area, work dates, work month and
                                   ledger month, Vehicle Soft verdict;
  - fin_recon_anomaly           -- the anomalies with their IDs and statuses
                                   exactly as in the register;
  - fin_recon_owner_decision    -- owner decisions and control notes;
  - fin_recon_cross_period_link -- works of another calendar month inside
                                   the ledger month;
  - fin_recon_dji_snapshot      -- the DJI technical snapshot of the period
                                   (RAW, accepted, excluded, decisions);
  - fin_recon_snapshot_seal     -- the seal: after it no row can be added to
                                   the snapshot.

[REASON]: the tables are immutable by the database, not by a promise of the
importer: UPDATE and DELETE raise on every table, a sealed snapshot refuses
new rows, approval fields refuse a value and version N>1 must reference
version N-1 of the same month by its content hash. A financial snapshot that
a later fix could quietly rewrite would not be a snapshot.

[REASON]: no ORM model. db.create_all() would create these tables EMPTY on
any database where this migration was not applied; the screen reads them
through a separate sqlite3 connection with mode=ro and the only writer is
the stdlib importer (tools/fin_recon_storage.py).

Safe / idempotent:
  - refuses to run and exits with code 2 when the database file is absent
    (sqlite3.connect would otherwise CREATE an empty database);
  - registered through migration_utils; a second run prints
    'Already applied. Nothing to do.';
  - precondition: no fin_recon_* object may exist while the registry row is
    absent -- such objects were made by hand or by a foreign script, and
    adopting them could silently keep a different schema; exit code 1 with
    full rollback, nothing registered;
  - ONE transaction for everything: tables, indexes, triggers, the
    postconditions and the schema_migrations row land on a single commit;
    postconditions are verified BEFORE the registry row is written;
  - no Flask app context, stdlib sqlite3 only; console output ASCII only.

Run (the isolated staging clone or an empty restore database -- never
production in this macro-stage; block 3 of docs/FIN_RECON_STORAGE_001_RUNBOOK.md
runs it on the same database):

  cd C:\\VehicleSoft_FinRecon\\app
  & "C:\\Program Files\\Python314\\python.exe" -I migrate_fin_recon_storage_001.py --db C:\\VehicleSoft_FinRecon\\app\\instance\\transport.db

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the FIN-RECON-STORAGE-001 commits leaves the tables in place; that is
  harmless -- nothing reads them once the code is gone, and db.create_all()
  never drops a table. Dropping them is a manual step, only AFTER the code
  revert, with the application service stopped (DROP TABLE removes the
  triggers of the table with it):

    DROP TABLE fin_recon_snapshot_seal;
    DROP TABLE fin_recon_dji_snapshot;
    DROP TABLE fin_recon_cross_period_link;
    DROP TABLE fin_recon_owner_decision;
    DROP TABLE fin_recon_anomaly;
    DROP TABLE fin_recon_work_row;
    DROP TABLE fin_recon_payout;
    DROP TABLE fin_recon_operator_row;
    DROP TABLE fin_recon_metric;
    DROP TABLE fin_recon_source;
    DROP TABLE fin_recon_period_snapshot;
    DELETE FROM schema_migrations WHERE name = 'FIN_RECON_STORAGE_001';

  The private evidence package is not in the database and is not touched by
  either rollback. No rollback step edits or deletes any row of any other
  table.
"""

import argparse
import os
import sqlite3
import sys

from datetime import datetime, timezone

# [REASON]: `python -I` (так запускает ранбук FIN-RECON-STORAGE-001) не
# кладёт каталог скрипта в sys.path; без этой строки migration_utils и
# fin_recon не нашлись бы, и миграция упала бы раньше любой проверки.
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402
from migration_utils import (  # noqa: E402
    ensure_schema_migrations_table,
    is_migration_applied,
    migration_checksum,
)

import fin_recon  # noqa: E402
from fin_recon import schema  # noqa: E402

# [REASON]: a literal module-level constant, not an import from fin_recon:
# tools/check_migration_drift.py finds a migration's registry name by parsing
# this exact assignment with ast, and an imported name would leave the file
# "unclassifiable" -- drift reported on every deploy. The assert below keeps
# the two spellings from drifting apart.
MIGRATION_ID = 'FIN_RECON_STORAGE_001'
assert MIGRATION_ID == fin_recon.MIGRATION_ID

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
DESCRIPTION = ('FIN-RECON-STORAGE-001: create fin_recon_period_snapshot, '
               'fin_recon_source, fin_recon_metric, fin_recon_operator_row, '
               'fin_recon_payout, fin_recon_work_row, fin_recon_anomaly, '
               'fin_recon_owner_decision, fin_recon_cross_period_link, '
               'fin_recon_dji_snapshot, fin_recon_snapshot_seal with '
               'immutability triggers. Additive only; no existing table is '
               'read or written.')


def _ascii(text):
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def _object_exists(cur, kind, name):
    cur.execute('SELECT name FROM sqlite_master WHERE type = ? AND name = ?',
                (kind, name))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


def _record_in_transaction(cur, checksum):
    """Registry row on the SAME transaction as the tables (the pattern of
    migrate_dji_area_evidence_001.py: record_migration() commits on its own
    connection and could register a migration whose tables rolled back)."""
    cur.execute(
        'INSERT INTO schema_migrations (name, applied_at, checksum, '
        'description) VALUES (?, ?, ?, ?)',
        (MIGRATION_ID,
         datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
         checksum, DESCRIPTION))


def run(db_path=None):
    """Apply the migration to db_path. Returns the process exit code."""
    path = os.path.abspath(db_path or DB_PATH)
    if not os.path.isfile(path):
        # [REASON]: sqlite3.connect(path) would CREATE an empty database when
        # the file is missing -- never do that in a migration.
        print('ERROR: database not found at %s - refusing to run. Nothing was '
              'created.' % _ascii(path))
        return 2

    # [REASON]: the established override of this project
    # (migrate_drones_assign_*): migration_utils resolves its own DB_PATH,
    # and the registry helpers must look at the SAME file as the DDL below.
    migration_utils.DB_PATH = path
    ensure_schema_migrations_table()
    if is_migration_applied(MIGRATION_ID):
        print('Already applied. Nothing to do.')
        return 0

    con = sqlite3.connect(path, timeout=30)
    try:
        cur = con.cursor()
        cur.execute('BEGIN IMMEDIATE')

        leftovers = schema.present_objects(con)
        if leftovers:
            raise RuntimeError(
                'precondition failed: fin_recon objects exist while %s is not '
                'registered: %s' % (MIGRATION_ID, ', '.join(
                    '%s %s' % pair for pair in leftovers)))

        for _name, ddl in schema.TABLES:
            cur.execute(ddl)
        for _name, ddl in schema.INDEXES:
            cur.execute(ddl)
        for _name, ddl in schema.TRIGGERS:
            cur.execute(ddl)

        # Postconditions before recording anything.
        expected = schema.expected_columns()
        for table in schema.TABLE_NAMES:
            if not _object_exists(cur, 'table', table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
            columns = _column_names(cur, table)
            if columns != expected[table]:
                raise RuntimeError('postcondition failed: columns of %s are '
                                   '%s, expected %s'
                                   % (table, columns, expected[table]))
        for name in schema.INDEX_NAMES:
            if not _object_exists(cur, 'index', name):
                raise RuntimeError('postcondition failed: index %s missing'
                                   % name)
        for name in schema.TRIGGER_NAMES:
            if not _object_exists(cur, 'trigger', name):
                raise RuntimeError('postcondition failed: trigger %s missing'
                                   % name)

        _record_in_transaction(cur, migration_checksum(__file__))
        con.commit()
    except Exception as exc:  # noqa: BLE001 -- every failure rolls back
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % _ascii(exc))
        return 1
    finally:
        con.close()

    total_columns = sum(len(cols) for cols in
                        schema.expected_columns().values())
    print('Done. %d tables (%d columns), %d indexes and %d triggers are in '
          'place.' % (len(schema.TABLE_NAMES), total_columns,
                      len(schema.INDEXES), len(schema.TRIGGERS)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--db', default=DB_PATH,
                        help='database file (default: instance/transport.db '
                             'next to this script)')
    args = parser.parse_args(argv)
    return run(args.db)


if __name__ == '__main__':
    sys.exit(main())
