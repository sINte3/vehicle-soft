# -*- coding: utf-8 -*-
"""Migration DRONES_USEFUL_AREA_001 -- DRONE-USEFUL-AREA-001 storage.

Creates TWO tables and their indexes. Purely additive: no existing table,
column or row is created, modified or dropped. In particular
drone_flights.area_ha -- the DJI figure the invoices were written against --
is not read, not written and not referenced by any statement here.

Creates:
  - drone_flight_routes  -- the geometry of one flight's route as the
                            collector delivered it: points, the recorded
                            spray width or NULL, decoder/collector versions,
                            the structural point-shape census, a content
                            hash and the idempotency audit fields.
  - drone_coverage_works -- the result of one useful-area calculation over a
                            WORK (one machine, one local UTC+5 day,
                            overlapping routes): the estimate or NULL, the
                            three explaining areas, the method uncertainty,
                            the algorithm version with its parameter
                            snapshot, the input-completeness counters and the
                            quality status with a machine-readable reason.

[REASON]: two tables, not one. Routes are RECEIVED DATA and exist whether or
not anything has been computed from them; a coverage result is a CONCLUSION
drawn under one named version of the rules. Bumping the algorithm version has
to rewrite every conclusion and must not touch a single byte of the received
geometry -- one table would make that impossible to express.

[REASON]: drone_coverage_works.unit_key is NOT NULL and carries the identity
of the machine as text ('unit:6', or 'nick:<spelling>' for a flight whose
machine is still unresolved). The natural key would have been
(drone_unit_id, work_date, group_index), but SQLite treats NULLs inside a
UNIQUE constraint as distinct: an unattributed work would then insert a
second row on every recalculation instead of updating the first, and the
totals would climb with each run while every row looked correct.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE / CREATE INDEX IF NOT EXISTS, so a second run over existing
    tables is a no-op even if the registry row were lost;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording anything;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and turns a reader
    into a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260902
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_useful_area_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the DRONE-USEFUL-AREA-001 commits leaves both tables in place; that is
  harmless -- nothing reads them once the models are gone, and
  db.create_all() does not drop tables. Dropping them is a manual step and is
  only correct AFTER the code revert:

    DROP TABLE drone_coverage_works;
    DROP TABLE drone_flight_routes;
    DELETE FROM schema_migrations WHERE name = 'DRONES_USEFUL_AREA_001';

  No rollback step edits or deletes any pre-existing row of any other table.
  drone_flights, drone_works and field_contours are untouched by both the
  migration and its rollback.
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
MIGRATION_ID = 'DRONES_USEFUL_AREA_001'
DESCRIPTION = ('DRONE-USEFUL-AREA-001: create drone_flight_routes (the '
               'received geometry of one flight: points, recorded spray '
               'width or NULL, decoder/collector versions, point-shape '
               'census, content hash, idempotency audit) and '
               'drone_coverage_works (the useful-area result of one work: '
               'estimate or NULL, the three explaining areas, method '
               'uncertainty, algorithm version with parameter snapshot, '
               'input-completeness counters, quality status and reason). '
               'Additive only; drone_flights.area_ha is neither read nor '
               'written.')

CREATE_DRONE_FLIGHT_ROUTES = """
    CREATE TABLE IF NOT EXISTS drone_flight_routes (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        dji_flight_id           BIGINT NOT NULL UNIQUE,
        drone_flight_id         INTEGER NOT NULL REFERENCES drone_flights (id),
        point_count             INTEGER NOT NULL DEFAULT 0,
        points_json             TEXT NOT NULL,
        takeoff_json            TEXT,
        spray_width_m           FLOAT,
        spray_width_recorded    BOOLEAN NOT NULL DEFAULT 0,
        dji_area_m2             FLOAT,
        data_type               VARCHAR(40),
        decoder_version         VARCHAR(40),
        collector_version       VARCHAR(40),
        point_shape_census_json TEXT,
        content_sha256          VARCHAR(64) NOT NULL,
        mission_uuid            VARCHAR(100),
        source                  VARCHAR(40) NOT NULL DEFAULT 'dji-ui-capture',
        received_at             DATETIME NOT NULL,
        updated_at              DATETIME NOT NULL,
        ingest_count            INTEGER NOT NULL DEFAULT 1
    )
"""

CREATE_DRONE_COVERAGE_WORKS = """
    CREATE TABLE IF NOT EXISTS drone_coverage_works (
        id                            INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_key                      VARCHAR(120) NOT NULL,
        drone_unit_id                 INTEGER REFERENCES drone_units (id),
        work_date                     DATE NOT NULL,
        group_index                   INTEGER NOT NULL DEFAULT 0,
        inputs_fingerprint            VARCHAR(64) NOT NULL,
        route_fingerprint             VARCHAR(64) NOT NULL,
        field_contour_id              INTEGER REFERENCES field_contours (id),
        contour_status                VARCHAR(40),
        estimated_useful_area_ha      FLOAT,
        partial_estimate_ha           FLOAT,
        sum_independent_swaths_ha     FLOAT,
        swath_union_ha                FLOAT,
        clipped_all_ha                FLOAT,
        contour_area_ha               FLOAT,
        uncertainty_percent           FLOAT,
        algorithm_version             VARCHAR(40) NOT NULL,
        params_json                   TEXT NOT NULL,
        flights_total                 INTEGER NOT NULL DEFAULT 0,
        routes_total                  INTEGER NOT NULL DEFAULT 0,
        flights_without_route         INTEGER NOT NULL DEFAULT 0,
        flights_without_width         INTEGER NOT NULL DEFAULT 0,
        flights_without_width_on_work INTEGER NOT NULL DEFAULT 0,
        work_segments                 INTEGER NOT NULL DEFAULT 0,
        route_points                  INTEGER NOT NULL DEFAULT 0,
        quality_status                VARCHAR(40) NOT NULL,
        quality_reason                VARCHAR(80) NOT NULL,
        dji_area_ha                   FLOAT,
        mission_state                 VARCHAR(20),
        computed_at                   DATETIME NOT NULL,
        CONSTRAINT uq_drone_coverage_works_identity
            UNIQUE (unit_key, work_date, group_index)
    )
"""

INDEXES = (
    ('ix_drone_flight_routes_drone_flight_id',
     'CREATE INDEX IF NOT EXISTS ix_drone_flight_routes_drone_flight_id '
     'ON drone_flight_routes (drone_flight_id)'),
    ('ix_drone_flight_routes_content_sha256',
     'CREATE INDEX IF NOT EXISTS ix_drone_flight_routes_content_sha256 '
     'ON drone_flight_routes (content_sha256)'),
    ('ix_drone_coverage_works_work_date',
     'CREATE INDEX IF NOT EXISTS ix_drone_coverage_works_work_date '
     'ON drone_coverage_works (work_date)'),
    ('ix_drone_coverage_works_quality_status',
     'CREATE INDEX IF NOT EXISTS ix_drone_coverage_works_quality_status '
     'ON drone_coverage_works (quality_status)'),
    ('ix_drone_coverage_works_date_status',
     'CREATE INDEX IF NOT EXISTS ix_drone_coverage_works_date_status '
     'ON drone_coverage_works (work_date, quality_status)'),
)

ROUTE_COLUMNS = ('id', 'dji_flight_id', 'drone_flight_id', 'point_count',
                 'points_json', 'takeoff_json', 'spray_width_m',
                 'spray_width_recorded', 'dji_area_m2', 'data_type',
                 'decoder_version', 'collector_version',
                 'point_shape_census_json', 'content_sha256', 'mission_uuid',
                 'source', 'received_at', 'updated_at', 'ingest_count')

WORK_COLUMNS = ('id', 'unit_key', 'drone_unit_id', 'work_date', 'group_index',
                'inputs_fingerprint', 'route_fingerprint', 'field_contour_id',
                'contour_status', 'estimated_useful_area_ha',
                'partial_estimate_ha', 'sum_independent_swaths_ha',
                'swath_union_ha', 'clipped_all_ha', 'contour_area_ha',
                'uncertainty_percent', 'algorithm_version', 'params_json',
                'flights_total', 'routes_total', 'flights_without_route',
                'flights_without_width', 'flights_without_width_on_work',
                'work_segments', 'route_points', 'quality_status',
                'quality_reason', 'dji_area_ha', 'mission_state',
                'computed_at')


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _index_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?",
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

        # [REASON]: every REFERENCES target must exist before the tables are
        # created. Their absence means the drones module was never installed
        # and this migration is being run against the wrong database -- a
        # far more likely mistake than a corrupted schema.
        for table in ('drone_flights', 'drone_units', 'field_contours'):
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        cur.execute(CREATE_DRONE_FLIGHT_ROUTES)
        cur.execute(CREATE_DRONE_COVERAGE_WORKS)
        for _name, statement in INDEXES:
            cur.execute(statement)

        # Postconditions before recording anything.
        for table, expected in (('drone_flight_routes', ROUTE_COLUMNS),
                                ('drone_coverage_works', WORK_COLUMNS)):
            if not _table_exists(cur, table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
            columns = _column_names(cur, table)
            for name in expected:
                if name not in columns:
                    raise RuntimeError('postcondition failed: column %s '
                                       'missing on %s' % (name, table))
        for name, _statement in INDEXES:
            if not _index_exists(cur, name):
                raise RuntimeError('postcondition failed: index %s missing'
                                   % name)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. Tables drone_flight_routes (%d columns) and '
          'drone_coverage_works (%d columns) with %d indexes are in place.'
          % (len(ROUTE_COLUMNS), len(WORK_COLUMNS), len(INDEXES)))


if __name__ == '__main__':
    run()
