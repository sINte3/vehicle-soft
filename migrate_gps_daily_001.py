# -*- coding: utf-8 -*-
"""Migration GPS_DAILY_001 -- the two tables GPS-2 writes its results into.

Creates TWO tables, gps_daily_aggregates and gps_work_polygons. Purely
additive: no existing table, column or row is created, modified or dropped.

Creates:
  - gps_daily_aggregates -- one row per object-day, always. A day with no
                            movement gets a row too: "we looked and there was
                            no work" and "we did not look" are different facts
                            and a missing row cannot tell them apart.
                            `reason` is NULL when the area was published and
                            carries the refusal cause otherwise
                            (redkaya_zapis / net_tochek / net_dvizheniya).
  - gps_work_polygons   -- one row per work site found that day: area,
                            minutes, the polygon itself, and the two label
                            columns the GPS-3 screen fills in.

[REASON]: operator_label and decided_at live on the polygon row rather than in
a table of their own. They are the training set for telling work from a
transit -- the one thing no measured feature has managed so far (roadmap
section 2.8) -- and an answer separated from the geometry it was given about
is worth nothing. gps/daily.py carries them across a recomputation by
overlap, not by row number.

[REASON]: contour_id is NULLABLE and that is the normal case, not a defect.
Operators mostly do not create geozones: they trace the track by hand and
write the number down. Requiring a contour lost 31.77 ha of 159 on the
spraying set of 12.08 and produced two works of exactly zero while the machine
had spent the day in the field (roadmap 2.4).

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS, so a second run over existing tables is a
    no-op even if the registry row were lost;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and turns a reader into
    a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_gps_daily_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the GPS-1/GPS-2 commits leaves both tables in place; that is harmless --
  nothing reads them once the code is gone, and db.create_all() does not drop
  tables. Dropping them is a manual step and is only correct AFTER the code
  revert.

  BEFORE dropping, save the answers: operator_label is hand-entered data that
  no recomputation can restore, and gps_work_polygons is the only place it
  exists.

    .mode csv
    .output gps_labels_backup.csv
    SELECT work_date, wialon_id, site_number, area_ha, minutes,
           operator_label, decided_at FROM gps_work_polygons
     WHERE operator_label IS NOT NULL;
    .output stdout
    DROP TABLE gps_work_polygons;
    DROP TABLE gps_daily_aggregates;
    DELETE FROM schema_migrations WHERE name = 'GPS_DAILY_001';

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
MIGRATION_ID = 'GPS_DAILY_001'
DESCRIPTION = ('GPS-2: create gps_daily_aggregates (one row per object-day, '
               'including days with no work, with the refusal reason) and '
               'gps_work_polygons (one row per work site: area, minutes, '
               'polygon, contour, alpha, spacing, suggested and operator '
               'label). Additive only, two new tables, no pre-existing row '
               'modified.')

CREATE_DAILY_AGGREGATES = """
    CREATE TABLE IF NOT EXISTS gps_daily_aggregates (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        work_date         DATE    NOT NULL,
        wialon_id         INTEGER NOT NULL,
        points_total      INTEGER NOT NULL DEFAULT 0,
        points_work       INTEGER NOT NULL DEFAULT 0,
        track_km          REAL    NOT NULL DEFAULT 0,
        interval_median_s REAL,
        sats_median       REAL,
        motion_gaps       INTEGER NOT NULL DEFAULT 0,
        lost_seconds      REAL    NOT NULL DEFAULT 0,
        gps_jumps         INTEGER NOT NULL DEFAULT 0,
        reason            VARCHAR(40),
        method_version    VARCHAR(60) NOT NULL,
        computed_at       DATETIME NOT NULL,
        CONSTRAINT uq_gps_daily_aggregates_day_unit
            UNIQUE (work_date, wialon_id)
    )
"""

CREATE_WORK_POLYGONS = """
    CREATE TABLE IF NOT EXISTS gps_work_polygons (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        work_date       DATE    NOT NULL,
        wialon_id       INTEGER NOT NULL,
        site_number     INTEGER NOT NULL,
        area_ha         REAL    NOT NULL,
        minutes         REAL    NOT NULL,
        polygon_geojson TEXT    NOT NULL,
        contour_id      INTEGER REFERENCES field_contours (id),
        alpha_used_m    REAL,
        pass_spacing_m  REAL,
        quality_flag    VARCHAR(120),
        suggested_label VARCHAR(20),
        operator_label  VARCHAR(20),
        decided_at      DATETIME,
        CONSTRAINT uq_gps_work_polygons_day_unit_site
            UNIQUE (work_date, wialon_id, site_number)
    )
"""

INDEXES = (
    ("ix_gps_daily_aggregates_unit_day",
     "CREATE INDEX IF NOT EXISTS ix_gps_daily_aggregates_unit_day "
     "ON gps_daily_aggregates (wialon_id, work_date)",
     "gps_daily_aggregates"),
    ("ix_gps_work_polygons_unit_day",
     "CREATE INDEX IF NOT EXISTS ix_gps_work_polygons_unit_day "
     "ON gps_work_polygons (wialon_id, work_date)",
     "gps_work_polygons"),
    # [REASON]: the screen that closes a work order asks "what was done on this
    # contour" -- a scan of a table that grows by every machine-day of the
    # season would answer it.
    ("ix_gps_work_polygons_contour",
     "CREATE INDEX IF NOT EXISTS ix_gps_work_polygons_contour "
     "ON gps_work_polygons (contour_id)",
     "gps_work_polygons"),
)

EXPECTED_AGGREGATE_COLUMNS = (
    'id', 'work_date', 'wialon_id', 'points_total', 'points_work', 'track_km',
    'interval_median_s', 'sats_median', 'motion_gaps', 'lost_seconds',
    'gps_jumps', 'reason', 'method_version', 'computed_at')

EXPECTED_POLYGON_COLUMNS = (
    'id', 'work_date', 'wialon_id', 'site_number', 'area_ha', 'minutes',
    'polygon_geojson', 'contour_id', 'alpha_used_m', 'pass_spacing_m',
    'quality_flag', 'suggested_label', 'operator_label', 'decided_at')


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


def _has_unique_index(cur, table, columns):
    """A UNIQUE constraint over exactly `columns`, whatever it is named."""
    cur.execute('PRAGMA index_list(%s)' % table)
    for row in cur.fetchall():
        name, unique = row[1], row[2]
        if not unique:
            continue
        cur.execute('PRAGMA index_info(%s)' % name)
        if [info[2] for info in cur.fetchall()] == list(columns):
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

        # [REASON]: the REFERENCES target must exist before the table is
        # created, or the contour column would point at nothing. field_contours
        # is the shared directory created by migrate_core_foundation_001.py;
        # its absence means this is the wrong database.
        if not _table_exists(cur, 'field_contours'):
            raise RuntimeError('precondition failed: table field_contours '
                               'does not exist')

        cur.execute(CREATE_DAILY_AGGREGATES)
        cur.execute(CREATE_WORK_POLYGONS)
        for _, statement, _ in INDEXES:
            cur.execute(statement)

        # Postconditions before recording anything.
        for table, expected in (('gps_daily_aggregates', EXPECTED_AGGREGATE_COLUMNS),
                                ('gps_work_polygons', EXPECTED_POLYGON_COLUMNS)):
            if not _table_exists(cur, table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
            columns = _column_names(cur, table)
            for name in expected:
                if name not in columns:
                    raise RuntimeError('postcondition failed: column %s missing '
                                       'on %s' % (name, table))
        for name, _, table in INDEXES:
            if name not in _index_names(cur, table):
                raise RuntimeError('postcondition failed: index %s missing '
                                   'on %s' % (name, table))
        # [REASON]: the uniqueness is what makes a recomputation replace rather
        # than accumulate. Without it a day recomputed five times would be
        # counted five times, and the hectares would look plausible.
        if not _has_unique_index(cur, 'gps_daily_aggregates',
                                 ('work_date', 'wialon_id')):
            raise RuntimeError('postcondition failed: gps_daily_aggregates has '
                               'no UNIQUE (work_date, wialon_id)')
        if not _has_unique_index(cur, 'gps_work_polygons',
                                 ('work_date', 'wialon_id', 'site_number')):
            raise RuntimeError('postcondition failed: gps_work_polygons has no '
                               'UNIQUE (work_date, wialon_id, site_number)')

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. gps_daily_aggregates (%d columns) and gps_work_polygons '
          '(%d columns) with %d indexes are in place.'
          % (len(EXPECTED_AGGREGATE_COLUMNS), len(EXPECTED_POLYGON_COLUMNS),
             len(INDEXES)))


if __name__ == '__main__':
    run()
