# -*- coding: utf-8 -*-
"""Migration CORE_FOUNDATION_001 -- PHASE1 shared data model (PR after #32).

One additive migration for the shared foundation all four tracks (spare
parts, fuel/AZS, GPS plan-vs-fact, drones) stand on, so that two tracks
never create two versions of the same table. Every new column is nullable
or has a default; no existing column changes type, name or nullability; no
existing row is modified.

Creates / adds:
  - table field_contours (shared directory for Wialon geozones, DJI fields
    and manual contours):
        id               INTEGER PRIMARY KEY AUTOINCREMENT
        source           VARCHAR(20) NOT NULL    -- 'wialon'/'dji'/'manual'
        external_id      VARCHAR(100) NULL       -- manual contours have none
        name             VARCHAR(300) NOT NULL
        name_uz          VARCHAR(300) NULL
        geometry_geojson TEXT NULL
        area_ha          FLOAT NULL
        customer_id      INTEGER NULL REFERENCES customers (id)
        season_year      INTEGER NULL
        is_active        BOOLEAN DEFAULT 1
        created_at       DATETIME
        updated_at       DATETIME
    with UNIQUE (source, external_id) as a table constraint and two
    indexes: ix_field_contours_customer_id, ix_field_contours_source_active.
    The table is created EMPTY -- nothing populates or reads it yet.
  - customers:      + inn, phone, address, notes (NULL), active DEFAULT 1
  - organizations:  + is_active DEFAULT 1, archived_at (NULL)
  - vialon_mappings:+ wialon_id (NULL) with index ix_vialon_mappings_wialon_id
  - app_modules:    INSERT OR IGNORE row ('drones', ...) -- the seed list in
    app.py runs only when the table is empty, so existing databases get the
    row from here. Names are Cyrillic by project rule; they live in the SQL
    parameters, never in console output.

Safe / idempotent:
  - refuses to run and exits non-zero when instance/transport.db is absent
    (sqlite3.connect would otherwise create an empty database);
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS everywhere;
  - SQLite has no ADD COLUMN IF NOT EXISTS, so every ALTER TABLE checks
    PRAGMA table_info first and skips columns that already exist;
  - INSERT OR IGNORE on app_modules (code is UNIQUE);
  - registered through migration_utils, skips itself on a re-run -- the
    second run prints that it is already applied and changes nothing;
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and becomes a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_core_foundation_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate. Reverting the code (commits
  of this PR in reverse order) leaves the new columns and the empty
  field_contours table in place. That is harmless -- nothing reads them once
  the models are gone, and db.create_all() will not drop them. Dropping is a
  manual step and is only correct AFTER the code revert:

    DROP TABLE IF EXISTS field_contours;
    -- its indexes are dropped with the table
    DELETE FROM app_modules WHERE code = 'drones';
    DELETE FROM schema_migrations WHERE name = 'CORE_FOUNDATION_001';

  The added columns on customers / organizations / vialon_mappings are
  deliberately LEFT IN PLACE: ALTER TABLE ... DROP COLUMN on a live
  production database is a larger risk than an unused nullable column.
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
MIGRATION_ID = 'CORE_FOUNDATION_001'
DESCRIPTION = ('PHASE1 shared foundation: create field_contours (unique '
               '(source, external_id), indexes on customer_id and '
               '(source, is_active)); add customers.inn/phone/address/notes/'
               'active, organizations.is_active/archived_at, '
               'vialon_mappings.wialon_id (+index); seed app_modules row '
               "'drones'. Additive only, no pre-existing row modified.")

CREATE_FIELD_CONTOURS = """
    CREATE TABLE IF NOT EXISTS field_contours (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        source           VARCHAR(20) NOT NULL,
        external_id      VARCHAR(100),
        name             VARCHAR(300) NOT NULL,
        name_uz          VARCHAR(300),
        geometry_geojson TEXT,
        area_ha          FLOAT,
        customer_id      INTEGER REFERENCES customers (id),
        season_year      INTEGER,
        is_active        BOOLEAN DEFAULT 1,
        created_at       DATETIME,
        updated_at       DATETIME,
        CONSTRAINT uq_field_contours_source_external
            UNIQUE (source, external_id)
    )
"""

CREATE_CUSTOMER_INDEX = """
    CREATE INDEX IF NOT EXISTS ix_field_contours_customer_id
        ON field_contours (customer_id)
"""

CREATE_SOURCE_ACTIVE_INDEX = """
    CREATE INDEX IF NOT EXISTS ix_field_contours_source_active
        ON field_contours (source, is_active)
"""

CREATE_WIALON_ID_INDEX = """
    CREATE INDEX IF NOT EXISTS ix_vialon_mappings_wialon_id
        ON vialon_mappings (wialon_id)
"""

# (table, column, ADD COLUMN clause). SQLite has no ADD COLUMN IF NOT
# EXISTS -- run() checks PRAGMA table_info first and skips existing columns.
ADD_COLUMNS = [
    ('customers', 'inn', "ALTER TABLE customers ADD COLUMN inn VARCHAR(20)"),
    ('customers', 'phone', "ALTER TABLE customers ADD COLUMN phone VARCHAR(50)"),
    ('customers', 'address', "ALTER TABLE customers ADD COLUMN address VARCHAR(300)"),
    ('customers', 'notes', "ALTER TABLE customers ADD COLUMN notes TEXT"),
    ('customers', 'active', "ALTER TABLE customers ADD COLUMN active BOOLEAN DEFAULT 1"),
    ('organizations', 'is_active', "ALTER TABLE organizations ADD COLUMN is_active BOOLEAN DEFAULT 1"),
    ('organizations', 'archived_at', "ALTER TABLE organizations ADD COLUMN archived_at DATETIME"),
    ('vialon_mappings', 'wialon_id', "ALTER TABLE vialon_mappings ADD COLUMN wialon_id INTEGER"),
]

# [REASON]: the app.py seed list runs ONLY when app_modules is empty (fresh
# installs); on every existing database this row must come from the
# migration. Cyrillic names are data (project rule: Uzbek is Cyrillic
# only) -- they are bound as parameters and never printed to the console.
DRONES_MODULE_ROW = ('drones', '\u0414\u0440\u043e\u043d\u043b\u0430\u0440',
                     '\u0414\u0440\u043e\u043d\u044b', 1)


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


def _has_unique_constraint(cur, table):
    """True when the table carries at least one UNIQUE table constraint
    (PRAGMA index_list origin 'u' = index created by a UNIQUE constraint)."""
    cur.execute('PRAGMA index_list(%s)' % table)
    return any(row[2] == 1 and row[3] == 'u' for row in cur.fetchall())


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

        for table in ('customers', 'organizations', 'vialon_mappings',
                      'app_modules'):
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        cur.execute(CREATE_FIELD_CONTOURS)
        cur.execute(CREATE_CUSTOMER_INDEX)
        cur.execute(CREATE_SOURCE_ACTIVE_INDEX)

        for table, column, ddl in ADD_COLUMNS:
            if column in _columns(cur, table):
                print('Column %s.%s already exists - skipped.'
                      % (table, column))
            else:
                cur.execute(ddl)
        cur.execute(CREATE_WIALON_ID_INDEX)

        cur.execute('INSERT OR IGNORE INTO app_modules '
                    '(code, name_uz, name_ru, is_active) VALUES (?, ?, ?, ?)',
                    DRONES_MODULE_ROW)

        # Postconditions before recording anything.
        if not _table_exists(cur, 'field_contours'):
            raise RuntimeError('postcondition failed: field_contours missing')
        contour_indexes = _index_names(cur, 'field_contours')
        for index in ('ix_field_contours_customer_id',
                      'ix_field_contours_source_active'):
            if index not in contour_indexes:
                raise RuntimeError(
                    'postcondition failed: index %s missing' % index)
        if not _has_unique_constraint(cur, 'field_contours'):
            raise RuntimeError('postcondition failed: unique constraint on '
                               '(source, external_id) missing')
        customer_columns = _columns(cur, 'customers')
        for column in ('inn', 'phone', 'address', 'notes', 'active'):
            if column not in customer_columns:
                raise RuntimeError(
                    'postcondition failed: customers.%s missing' % column)
        organization_columns = _columns(cur, 'organizations')
        for column in ('is_active', 'archived_at'):
            if column not in organization_columns:
                raise RuntimeError(
                    'postcondition failed: organizations.%s missing' % column)
        if 'wialon_id' not in _columns(cur, 'vialon_mappings'):
            raise RuntimeError(
                'postcondition failed: vialon_mappings.wialon_id missing')
        if 'ix_vialon_mappings_wialon_id' not in _index_names(
                cur, 'vialon_mappings'):
            raise RuntimeError('postcondition failed: '
                               'ix_vialon_mappings_wialon_id missing')
        cur.execute("SELECT COUNT(*) FROM app_modules WHERE code = 'drones'")
        if cur.fetchone()[0] != 1:
            raise RuntimeError(
                "postcondition failed: app_modules row 'drones' missing")

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. field_contours (unique constraint + 2 indexes), 8 additive '
          'columns and the drones module row are in place.')


if __name__ == '__main__':
    run()
