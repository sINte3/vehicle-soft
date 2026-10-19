# -*- coding: utf-8 -*-
"""Migration GPS_ENGINE_001 -- daily ignition hours of every GPS object.

Creates ONE table, gps_daily_engine. Purely additive: no existing table, column
or row is created, modified or dropped.

WHY IT EXISTS
The «time» method of the application reconciliation (owner's decision of
09.10.2026: an engine hour is time with the ignition on, by the tracker)
needs, for every object and local day, how long the ignition was on. The
collector keeps the raw ignition value beside each point (GPS-24); rule R
(gps/ignition.py, preregistered in the GPS track on 19.10.2026) turns the
points of a day into seconds on / off / unknown. gps/engine_hours.py writes
one row per object-day here; the reconciliation reads it.

[REASON]: a table of its own, not columns on gps_daily_aggregates. Hours are
recomputed on their own clock -- after the ignition refill, after a late
collection -- and such a recomputation must not touch the hectares and the
operators' answers that hang on the aggregate rows. And no ORM model: the
reconciliation reads it with plain SQL, as it reads the agro_work_* tables,
so models.py (a file shared by all tracks) is not touched.

[REASON]: track_km (GPS-29) was added on 19.10.2026 BEFORE the migration was
first applied anywhere: the migration reaches production with release v1.29
and no database ever had the table without the column. Owner's decision of 19.10: whether a truck worked is judged by its
mileage (rule P, gps/mileage.py), and the hours table is where the
reconciliation reads a day of a machine.

[REASON]: a row with points_with_ign = 0 is a fact, not a gap: "we looked and
the tracker sent no ignition" differs from "we did not look" (no row), as on
gps_daily_aggregates.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - precondition: gps_daily_aggregates exists (GPS_DAILY_001 applied) --
    without the GPS calculation the table would be fed by nothing;
  - CREATE TABLE IF NOT EXISTS; a table of that name with other columns
    fails the postcondition and rolls everything back;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only;
  - console output is ASCII only (NSSM/Windows log encoding).

Run (services stopped -- the release runbook does it):

  cd C:\\transport-report
  "C:\\Program Files\\Python314\\python.exe" migrate_gps_engine_001.py

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the GPS-26 commits leaves gps_daily_engine in place; that is harmless --
  nothing reads it once the code is gone, and db.create_all() neither creates
  nor drops a table without a model.

    DROP TABLE gps_daily_engine;
    DELETE FROM schema_migrations WHERE name = 'GPS_ENGINE_001';

  Nothing else references this table, and no rollback step edits or deletes
  any pre-existing row of any other table. The hours are derived from the
  point files: dropping the table loses the computation, not the data, and
  `python -m gps.engine_hours --from ... --to ...` rebuilds it while the
  points are on disk.
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
MIGRATION_ID = 'GPS_ENGINE_001'
DESCRIPTION = ('GPS-26: create gps_daily_engine, one row per GPS object and '
               'local day -- seconds with the ignition on, off and unknown by '
               'rule R, points of the day and how many carried ignition, the '
               'mileage by rule P, the method version. Additive only, one new '
               'table.')

CREATE_GPS_DAILY_ENGINE = """
    CREATE TABLE IF NOT EXISTS gps_daily_engine (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        work_date        DATE NOT NULL,
        wialon_id        INTEGER NOT NULL,
        on_seconds       INTEGER NOT NULL DEFAULT 0,
        off_seconds      INTEGER NOT NULL DEFAULT 0,
        unknown_seconds  INTEGER NOT NULL DEFAULT 0,
        points_total     INTEGER NOT NULL DEFAULT 0,
        points_with_ign  INTEGER NOT NULL DEFAULT 0,
        track_km         REAL NOT NULL DEFAULT 0,
        method_version   VARCHAR(60) NOT NULL,
        computed_at      DATETIME NOT NULL,
        CONSTRAINT uq_gps_daily_engine_day_unit UNIQUE (work_date, wialon_id)
    )
"""

INDEXES = (
    ('ix_gps_daily_engine_unit_day',
     'CREATE INDEX IF NOT EXISTS ix_gps_daily_engine_unit_day '
     'ON gps_daily_engine (wialon_id, work_date)'),
)

EXPECTED_COLUMNS = (
    'id', 'work_date', 'wialon_id', 'on_seconds', 'off_seconds',
    'unknown_seconds', 'points_total', 'points_with_ign', 'track_km',
    'method_version', 'computed_at')


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


def _unique_on_day_and_unit(cur):
    """True when some UNIQUE index covers exactly (work_date, wialon_id)."""
    cur.execute('PRAGMA index_list(gps_daily_engine)')
    for row in cur.fetchall():
        name, unique = row[1], row[2]
        if not unique:
            continue
        cur.execute('PRAGMA index_info(%s)' % name)
        if [r[2] for r in cur.fetchall()] == ['work_date', 'wialon_id']:
            return True
    return False


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

        if not _table_exists(cur, 'gps_daily_aggregates'):
            raise RuntimeError('precondition failed: table gps_daily_aggregates '
                               'does not exist (GPS_DAILY_001 not applied)')

        cur.execute(CREATE_GPS_DAILY_ENGINE)
        for _, statement in INDEXES:
            cur.execute(statement)

        # Postconditions before recording anything.
        if not _table_exists(cur, 'gps_daily_engine'):
            raise RuntimeError('postcondition failed: table gps_daily_engine '
                               'missing')
        columns = _column_names(cur, 'gps_daily_engine')
        for name in EXPECTED_COLUMNS:
            if name not in columns:
                raise RuntimeError('postcondition failed: column %s missing '
                                   'on gps_daily_engine' % name)
        present = _index_names(cur, 'gps_daily_engine')
        for name, _ in INDEXES:
            if name not in present:
                raise RuntimeError('postcondition failed: index %s missing '
                                   'on gps_daily_engine' % name)
        # [REASON]: the upsert of gps/engine_hours.py stands on this key; a
        # table without it would take a second row for the same object-day.
        if not _unique_on_day_and_unit(cur):
            raise RuntimeError('postcondition failed: no UNIQUE (work_date, '
                               'wialon_id) on gps_daily_engine')

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. Table gps_daily_engine with %d columns and %d index is in place.'
          % (len(EXPECTED_COLUMNS), len(INDEXES)))


if __name__ == '__main__':
    run()
