# -*- coding: utf-8 -*-
"""Migration FIELD_CONTOURS_DJI_001 -- columns the DJI field snapshot needs.

field_contours was created EMPTY by CORE_FOUNDATION_001 as the shared
directory for Wialon geozones, DJI fields and manual contours. Its own
docstring named the job this migration finally does: "the drone track
collects 4 776 DJI Field Management records ... the collectors ship in later
tasks". There are 5 489 of them today.

The existing columns carry name, area and geometry. Five things the DJI
payload gives and the table has nowhere to put:

  * the bounding box of the field. It arrives in the LIST payload, while the
    polygon itself lives behind a signed URL that expires six hours after it
    is issued. A point-in-box test on lat/lng is what links a flight to a
    field without downloading 5 489 geometry files, so the box is stored as
    four plain floats and indexed.
  * the field centre. Used to break a tie when a point falls inside more than
    one box.
  * the DJI serial number of the contour ("P11782004"). The dispatchers'
    books carry a column «Ишлаган контур рақами» that the importer skips
    today for want of anything to match it against.
  * DJI's own created/updated timestamps. They arrive in UTC+08:00 and are
    stored in UTC like every other datetime in this database.
  * the raw payload, so every derived column can be recomputed if a rule
    changes -- the same discipline drone_flights.raw_json follows.

Adds to field_contours (all NULLABLE, no existing column touched, no
existing row modified):
        serial_number     VARCHAR(50)
        land_type         VARCHAR(40)
        work_area_ha      FLOAT
        bbox_min_lat      FLOAT
        bbox_min_lng      FLOAT
        bbox_max_lat      FLOAT
        bbox_max_lng      FLOAT
        center_lat        FLOAT
        center_lng        FLOAT
        source_created_at DATETIME     -- UTC, converted from +08:00
        source_updated_at DATETIME     -- UTC, converted from +08:00
        synced_at         DATETIME
        raw_json          TEXT
  and one index:
        ix_field_contours_bbox ON (bbox_min_lat, bbox_max_lat)

Safe / idempotent:
  - refuses to run and exits 2 when instance/transport.db is absent
    (sqlite3.connect would otherwise create an empty database);
  - SQLite has no ADD COLUMN IF NOT EXISTS, so every ALTER TABLE checks
    PRAGMA table_info first and skips a column that already exists;
  - CREATE INDEX IF NOT EXISTS;
  - registered through migration_utils, skips itself on a re-run;
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and becomes a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260818
  "C:\\Program Files\\Python314\\python.exe" migrate_field_contours_dji_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate.

  Code rollback -- revert the commits of this PR in reverse order with
  `git revert`. The new columns stay behind; nothing reads them once the
  models are gone and db.create_all() will not drop them. That is the
  expected end state, and it is harmless.

  Data rollback -- only correct AFTER the code revert, and only if the
  columns must really disappear. ALTER TABLE ... DROP COLUMN on a live
  production database is a larger risk than an unused nullable column, so
  the columns are deliberately left in place and only the registry row and
  the index are removed:

    DROP INDEX IF EXISTS ix_field_contours_bbox;
    DELETE FROM schema_migrations WHERE name = 'FIELD_CONTOURS_DJI_001';

  Removing the collected rows themselves (a separate decision from removing
  the columns) is:

    DELETE FROM field_contours WHERE source = 'dji';

  No rollback step edits or deletes any pre-existing row, and no step here
  touches a contour whose source is 'wialon' or 'manual'.
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
MIGRATION_ID = 'FIELD_CONTOURS_DJI_001'
DESCRIPTION = ('DRONE-LANDS-001: add 13 nullable columns to field_contours '
               '(serial_number, land_type, work_area_ha, bbox x4, center x2, '
               'source_created_at, source_updated_at, synced_at, raw_json) '
               'and index ix_field_contours_bbox on (bbox_min_lat, '
               'bbox_max_lat). Additive only, no pre-existing row modified.')

# (column, ADD COLUMN clause). SQLite has no ADD COLUMN IF NOT EXISTS --
# run() checks PRAGMA table_info first and skips columns that already exist.
ADD_COLUMNS = [
    ('serial_number',
     'ALTER TABLE field_contours ADD COLUMN serial_number VARCHAR(50)'),
    ('land_type',
     'ALTER TABLE field_contours ADD COLUMN land_type VARCHAR(40)'),
    ('work_area_ha',
     'ALTER TABLE field_contours ADD COLUMN work_area_ha FLOAT'),
    ('bbox_min_lat',
     'ALTER TABLE field_contours ADD COLUMN bbox_min_lat FLOAT'),
    ('bbox_min_lng',
     'ALTER TABLE field_contours ADD COLUMN bbox_min_lng FLOAT'),
    ('bbox_max_lat',
     'ALTER TABLE field_contours ADD COLUMN bbox_max_lat FLOAT'),
    ('bbox_max_lng',
     'ALTER TABLE field_contours ADD COLUMN bbox_max_lng FLOAT'),
    ('center_lat',
     'ALTER TABLE field_contours ADD COLUMN center_lat FLOAT'),
    ('center_lng',
     'ALTER TABLE field_contours ADD COLUMN center_lng FLOAT'),
    ('source_created_at',
     'ALTER TABLE field_contours ADD COLUMN source_created_at DATETIME'),
    ('source_updated_at',
     'ALTER TABLE field_contours ADD COLUMN source_updated_at DATETIME'),
    ('synced_at',
     'ALTER TABLE field_contours ADD COLUMN synced_at DATETIME'),
    ('raw_json',
     'ALTER TABLE field_contours ADD COLUMN raw_json TEXT'),
]

# [REASON]: the lookup this index serves is "which contour contains this
# point" -- bbox_min_lat <= lat AND bbox_max_lat >= lat AND the same for lng.
# SQLite uses only the leading column of a composite index for a range scan,
# so the index narrows on latitude and the longitude test runs on the
# survivors. With 5 489 rows that is already the difference between a full
# scan per flight and a handful of rows per flight, and 10 385 flights makes
# that difference worth an index.
CREATE_BBOX_INDEX = """
    CREATE INDEX IF NOT EXISTS ix_field_contours_bbox
        ON field_contours (bbox_min_lat, bbox_max_lat)
"""


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _columns(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return {row[1] for row in cur.fetchall()}


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

        if not _table_exists(cur, 'field_contours'):
            raise RuntimeError('precondition failed: table field_contours '
                               'does not exist - apply CORE_FOUNDATION_001 '
                               'first')

        before = _columns(cur, 'field_contours')
        for column, ddl in ADD_COLUMNS:
            if column in before:
                print('Column field_contours.%s already exists - skipped.'
                      % column)
            else:
                cur.execute(ddl)
        cur.execute(CREATE_BBOX_INDEX)

        # Postconditions before recording anything.
        after = _columns(cur, 'field_contours')
        for column, _ddl in ADD_COLUMNS:
            if column not in after:
                raise RuntimeError('postcondition failed: field_contours.%s '
                                   'missing' % column)
        if 'ix_field_contours_bbox' not in _index_names(cur, 'field_contours'):
            raise RuntimeError('postcondition failed: index '
                               'ix_field_contours_bbox missing')
        # [REASON]: this migration is additive by contract. If the table lost
        # a column while it ran, something else is writing the schema and the
        # run must not be recorded as clean.
        for column in ('source', 'external_id', 'name', 'area_ha',
                       'geometry_geojson', 'customer_id', 'is_active'):
            if column not in after:
                raise RuntimeError('postcondition failed: pre-existing column '
                                   'field_contours.%s disappeared' % column)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. field_contours: %d additive column(s) and '
          'ix_field_contours_bbox are in place.' % len(ADD_COLUMNS))


if __name__ == '__main__':
    run()
