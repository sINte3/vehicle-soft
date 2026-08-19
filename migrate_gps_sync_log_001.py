# -*- coding: utf-8 -*-
"""Migration GPS_SYNC_LOG_001 -- the ingestion journal of the GPS collector.

Creates ONE table, gps_sync_log. Purely additive: no existing table, column or
row is created, modified or dropped.

WHY IT EXISTS
After a night run nothing is visible from the application: the points live in
monthly files outside transport.db, and "the collector ran and found nothing
new" looks exactly like "the collector did not run". The difference between
those two is the difference between a calm morning and a lost day of history.

THE IDENTITY, MODELLED ON drone_sync_logs

    messages_seen = points_written + points_duplicate + points_no_position

Every message lands in exactly one bucket: stored as a point, dropped by the
key as already known, or carrying no fix at all. The old drones collector
always reported records_written == records_seen -- verified on all 520 rows of
its sync_runs -- so its log could not answer whether anything new had arrived.
These buckets must never be collapsed.

[REASON]: the journal is NOT drone_sync_logs and must not be put there. That
model documents a different invariant (records_seen = new + duplicate + error,
unresolved <= new) which the project's verification scripts rely on; a GPS
collector run stores no flight and belongs in none of those buckets.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS, so a second run over an existing table is a
    no-op even if the registry row were lost;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only;
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260819
  "C:\\Program Files\\Python314\\python.exe" migrate_gps_sync_log_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the GPS-7 commits leaves gps_sync_log in place; that is harmless -- nothing
  reads it once the model is gone, and db.create_all() does not drop tables.

    DROP TABLE gps_sync_log;
    DELETE FROM schema_migrations WHERE name = 'GPS_SYNC_LOG_001';

  Nothing else references this table, and no rollback step edits or deletes
  any pre-existing row of any other table. The journal is derived from runs
  that already happened: dropping it loses the account of them, not the points.
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
MIGRATION_ID = 'GPS_SYNC_LOG_001'
DESCRIPTION = ('GPS-7: create gps_sync_log, one row per collector run -- how '
               'many objects were asked, skipped as silent or failed, how '
               'many messages were seen and where each of them went '
               '(written / duplicate / no position), the request counters and '
               'the interval covered. Additive only, one new table.')

CREATE_GPS_SYNC_LOG = """
    CREATE TABLE IF NOT EXISTS gps_sync_log (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at          DATETIME NOT NULL,
        finished_at         DATETIME NOT NULL,
        status              VARCHAR(20) NOT NULL,
        units_total         INTEGER NOT NULL DEFAULT 0,
        units_asked         INTEGER NOT NULL DEFAULT 0,
        units_silent        INTEGER NOT NULL DEFAULT 0,
        units_uptodate      INTEGER NOT NULL DEFAULT 0,
        units_empty         INTEGER NOT NULL DEFAULT 0,
        units_failed        INTEGER NOT NULL DEFAULT 0,
        messages_seen       INTEGER NOT NULL DEFAULT 0,
        points_written      INTEGER NOT NULL DEFAULT 0,
        points_duplicate    INTEGER NOT NULL DEFAULT 0,
        points_no_position  INTEGER NOT NULL DEFAULT 0,
        requests            INTEGER NOT NULL DEFAULT 0,
        logins              INTEGER NOT NULL DEFAULT 0,
        abandoned           INTEGER NOT NULL DEFAULT 0,
        truncated           INTEGER NOT NULL DEFAULT 0,
        interval_from       DATETIME,
        interval_to         DATETIME,
        error               TEXT
    )
"""

INDEXES = (
    ('ix_gps_sync_log_started',
     'CREATE INDEX IF NOT EXISTS ix_gps_sync_log_started '
     'ON gps_sync_log (started_at)'),
)

EXPECTED_COLUMNS = (
    'id', 'started_at', 'finished_at', 'status', 'units_total', 'units_asked',
    'units_silent', 'units_uptodate', 'units_empty', 'units_failed',
    'messages_seen', 'points_written', 'points_duplicate',
    'points_no_position', 'requests', 'logins', 'abandoned', 'truncated',
    'interval_from', 'interval_to', 'error')


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


def _index_names(cur, table):
    cur.execute('PRAGMA index_list(%s)' % table)
    return {row[1] for row in cur.fetchall()}


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

        # [REASON]: the journal has no foreign keys on purpose -- a collector
        # run belongs to no user and to no order. The precondition is only
        # that this is the application's database at all, and the migration
        # registry itself is the cheapest proof of that.
        if not _table_exists(cur, 'schema_migrations'):
            raise RuntimeError('precondition failed: table schema_migrations '
                               'does not exist')

        cur.execute(CREATE_GPS_SYNC_LOG)
        for _, statement in INDEXES:
            cur.execute(statement)

        # Postconditions before recording anything.
        if not _table_exists(cur, 'gps_sync_log'):
            raise RuntimeError('postcondition failed: table gps_sync_log '
                               'missing')
        columns = _column_names(cur, 'gps_sync_log')
        for name in EXPECTED_COLUMNS:
            if name not in columns:
                raise RuntimeError('postcondition failed: column %s missing '
                                   'on gps_sync_log' % name)
        present = _index_names(cur, 'gps_sync_log')
        for name, _ in INDEXES:
            if name not in present:
                raise RuntimeError('postcondition failed: index %s missing '
                                   'on gps_sync_log' % name)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. Table gps_sync_log with %d columns and %d index is in place.'
          % (len(EXPECTED_COLUMNS), len(INDEXES)))


if __name__ == '__main__':
    run()
