# -*- coding: utf-8 -*-
"""Migration DRONES_FOUNDATION_001 -- DRONE-001 data foundation.

Creates the four tables of the drones module and seeds the machine list
and the nickname alias map. Purely additive: no existing table, column or
row is modified.

Creates:
  - drone_units       -- 15 machines, numbers 1..15, all organization 12,
                         model 'DJI Agras T40', none excluded (owner
                         decision 2026-07-29: the exclusion flag ships
                         empty);
  - drone_nicknames   -- 20 alias rows mapped by leading number, with
                         normalized = lowercase, all whitespace removed
                         (indexes on drone_unit_id and normalized;
                         normalized is deliberately NOT unique: '8 Garden U'
                         and '8 GardenU' both normalise to '8gardenu');
  - drone_flights     -- empty; unique dji_flight_id; indexes
                         ix_drone_flights_started_at,
                         ix_drone_flights_unit_started (drone_unit_id,
                         started_at), ix_drone_flights_region;
  - drone_sync_logs   -- empty; five separate counters (new / duplicate /
                         unresolved / error / seen), because the old
                         collector's seen==written log could not show
                         whether new data had arrived.

[REASON]: the seed carries 20 nicknames, not the 19 previously circulated.
The raw DJI payloads contain the Cyrillic spelling '4 Ghijduvon' (211
flights) which the old collector transliterated into '4 Gijduvon' in its
derived column -- '19' was counted over that column, not over the raw
payloads the new ingest receives. Details: docs/DRONES_DOMAIN.md section 1.

Safe / idempotent (same contract as migrate_core_foundation_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS everywhere;
  - INSERT OR IGNORE on both seeds (number and nickname are UNIQUE);
  - guards the organisation: if organizations row id=12 is missing the
    migration FAILS instead of inserting orphans;
  - registered through migration_utils, skips itself on a re-run;
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and becomes a writer);
  - console output is ASCII only (NSSM/Windows log encoding); Cyrillic
    nickname text lives in SQL parameters and is never printed.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_foundation_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate. Reverting the DRONE-001
  commits leaves the four tables in place; that is harmless -- nothing
  reads them once the models are gone, and db.create_all() will not drop
  them. Dropping is a manual step and is only correct AFTER the code
  revert, foreign keys first:

    DROP TABLE drone_flights;
    DROP TABLE drone_sync_logs;
    DROP TABLE drone_nicknames;
    DROP TABLE drone_units;
    DELETE FROM schema_migrations WHERE name = 'DRONES_FOUNDATION_001';

  No rollback step edits or deletes any pre-existing row.
"""

import os
import sqlite3
import sys
from datetime import datetime

from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'DRONES_FOUNDATION_001'
DESCRIPTION = ('DRONE-001 foundation: create drone_units, drone_nicknames, '
               'drone_flights, drone_sync_logs (+5 indexes); seed 15 units '
               '(organization 12, DJI Agras T40, none excluded) and 20 '
               'nickname aliases. Additive only, no pre-existing row '
               'modified.')

DRONE_ORGANIZATION_ID = 12
DRONE_MODEL = 'DJI Agras T40'

# [REASON]: 20 raw spellings -> 15 machines, mapped by leading number. The
# 20th row '4 \u0492\u0438\u0436\u0434\u0443\u0432\u043e\u043d' is the
# Cyrillic spelling the old collector transliterated away; the raw payloads
# the ingest receives carry it in 211 flights (docs/DRONES_DOMAIN.md).
NICKNAME_SEED = [
    ('1 Klaster', 1),
    ('2 Klaster', 2),
    ('3 Gijduvon', 3),
    ('4 Gijduvon', 4),
    ('4 \u0492\u0438\u0436\u0434\u0443\u0432\u043e\u043d', 4),
    ('5 Shofirko', 5),
    ('6 Shofirko', 6),
    ('7 Garden', 7),
    ('8 Garden', 8),
    ('8 Garden U', 8),
    ('8 GardenU', 8),
    ('9 Garden', 9),
    ('10 Kogon', 10),
    ('11 Kogon', 11),
    ('12 Servis', 12),
    ('12 Peshku', 12),
    ('13 Peshku', 13),
    ('13 Servis', 13),
    ('14 Servis', 14),
    ('15 Servis', 15),
]

CREATE_DRONE_UNITS = """
    CREATE TABLE IF NOT EXISTS drone_units (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        number          INTEGER NOT NULL UNIQUE,
        organization_id INTEGER NOT NULL REFERENCES organizations (id),
        model           VARCHAR(50),
        is_excluded     BOOLEAN DEFAULT 0,
        excluded_from   DATE,
        excluded_reason VARCHAR(300),
        notes           TEXT,
        is_active       BOOLEAN DEFAULT 1,
        created_at      DATETIME,
        updated_at      DATETIME
    )
"""

CREATE_DRONE_NICKNAMES = """
    CREATE TABLE IF NOT EXISTS drone_nicknames (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        drone_unit_id INTEGER NOT NULL REFERENCES drone_units (id),
        nickname      VARCHAR(100) NOT NULL UNIQUE,
        normalized    VARCHAR(100) NOT NULL,
        first_seen_at DATETIME,
        last_seen_at  DATETIME,
        is_active     BOOLEAN DEFAULT 1,
        created_at    DATETIME
    )
"""

CREATE_DRONE_SYNC_LOGS = """
    CREATE TABLE IF NOT EXISTS drone_sync_logs (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        kind               VARCHAR(20) NOT NULL,
        period_from        DATE,
        period_to          DATE,
        started_at         DATETIME NOT NULL,
        finished_at        DATETIME,
        status             VARCHAR(20) NOT NULL,
        records_seen       INTEGER DEFAULT 0,
        records_new        INTEGER DEFAULT 0,
        records_duplicate  INTEGER DEFAULT 0,
        records_unresolved INTEGER DEFAULT 0,
        records_error      INTEGER DEFAULT 0,
        source_ip          VARCHAR(45),
        error_text         TEXT
    )
"""

CREATE_DRONE_FLIGHTS = """
    CREATE TABLE IF NOT EXISTS drone_flights (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        dji_flight_id BIGINT NOT NULL UNIQUE,
        drone_unit_id INTEGER REFERENCES drone_units (id),
        nickname_raw  VARCHAR(100),
        serial_number VARCHAR(50),
        flyer_name    VARCHAR(100),
        team_name     VARCHAR(100),
        started_at    DATETIME NOT NULL,
        finished_at   DATETIME,
        work_seconds  INTEGER,
        area_ha       FLOAT NOT NULL DEFAULT 0,
        spray_liters  FLOAT,
        sow_kg        FLOAT,
        usage_type    INTEGER,
        mode_name     INTEGER,
        manual_mode   BOOLEAN,
        work_speed    FLOAT,
        spray_width   FLOAT,
        radar_height  FLOAT,
        lat           FLOAT,
        lng           FLOAT,
        location_text VARCHAR(500),
        region        VARCHAR(100),
        raw_json      TEXT NOT NULL,
        sync_log_id   INTEGER REFERENCES drone_sync_logs (id),
        ingested_at   DATETIME NOT NULL
    )
"""

CREATE_INDEXES = [
    ("ix_drone_nicknames_drone_unit_id",
     "CREATE INDEX IF NOT EXISTS ix_drone_nicknames_drone_unit_id "
     "ON drone_nicknames (drone_unit_id)"),
    ("ix_drone_nicknames_normalized",
     "CREATE INDEX IF NOT EXISTS ix_drone_nicknames_normalized "
     "ON drone_nicknames (normalized)"),
    ("ix_drone_flights_started_at",
     "CREATE INDEX IF NOT EXISTS ix_drone_flights_started_at "
     "ON drone_flights (started_at)"),
    ("ix_drone_flights_unit_started",
     "CREATE INDEX IF NOT EXISTS ix_drone_flights_unit_started "
     "ON drone_flights (drone_unit_id, started_at)"),
    ("ix_drone_flights_region",
     "CREATE INDEX IF NOT EXISTS ix_drone_flights_region "
     "ON drone_flights (region)"),
]


def _normalize_nickname(nickname):
    """Lowercase and remove ALL whitespace -- must match the ingest's rule."""
    return ''.join(nickname.lower().split())


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _index_names(cur, table):
    cur.execute('PRAGMA index_list(%s)' % table)
    return {row[1] for row in cur.fetchall()}


def _has_unique_constraint(cur, table):
    """True when the table carries at least one UNIQUE constraint index
    (PRAGMA index_list origin 'u')."""
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

    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute('BEGIN')

        if not _table_exists(cur, 'organizations'):
            raise RuntimeError(
                'precondition failed: table organizations does not exist')
        # [REASON]: guard the organisation -- all 15 machines hang off
        # organizations.id = 12 (owner decision R4); seeding against a
        # database where that row is missing would create orphans.
        cur.execute('SELECT id FROM organizations WHERE id = ?',
                    (DRONE_ORGANIZATION_ID,))
        if cur.fetchone() is None:
            raise RuntimeError(
                'precondition failed: organizations row id=%d is missing'
                % DRONE_ORGANIZATION_ID)

        cur.execute(CREATE_DRONE_UNITS)
        cur.execute(CREATE_DRONE_NICKNAMES)
        cur.execute(CREATE_DRONE_SYNC_LOGS)
        cur.execute(CREATE_DRONE_FLIGHTS)
        for _name, ddl in CREATE_INDEXES:
            cur.execute(ddl)

        for number in range(1, 16):
            cur.execute(
                'INSERT OR IGNORE INTO drone_units '
                '(number, organization_id, model, is_excluded, excluded_from, '
                ' is_active, created_at, updated_at) '
                'VALUES (?, ?, ?, 0, NULL, 1, ?, ?)',
                (number, DRONE_ORGANIZATION_ID, DRONE_MODEL, now, now))

        for nickname, number in NICKNAME_SEED:
            cur.execute(
                'INSERT OR IGNORE INTO drone_nicknames '
                '(drone_unit_id, nickname, normalized, is_active, created_at) '
                'SELECT du.id, ?, ?, 1, ? FROM drone_units du '
                'WHERE du.number = ?',
                (nickname, _normalize_nickname(nickname), now, number))

        # Postconditions before recording anything.
        for table in ('drone_units', 'drone_nicknames', 'drone_sync_logs',
                      'drone_flights'):
            if not _table_exists(cur, table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
        present = (_index_names(cur, 'drone_nicknames')
                   | _index_names(cur, 'drone_flights'))
        for name, _ddl in CREATE_INDEXES:
            if name not in present:
                raise RuntimeError('postcondition failed: index %s missing'
                                   % name)
        for table in ('drone_units', 'drone_nicknames', 'drone_flights'):
            if not _has_unique_constraint(cur, table):
                raise RuntimeError('postcondition failed: UNIQUE constraint '
                                   'missing on %s' % table)
        cur.execute('SELECT COUNT(*) FROM drone_units')
        units = cur.fetchone()[0]
        if units != 15:
            raise RuntimeError(
                'postcondition failed: drone_units count is %d, expected 15'
                % units)
        cur.execute('SELECT COUNT(*) FROM drone_units '
                    'WHERE organization_id != ?', (DRONE_ORGANIZATION_ID,))
        if cur.fetchone()[0] != 0:
            raise RuntimeError('postcondition failed: drone_units row outside '
                               'organization %d' % DRONE_ORGANIZATION_ID)
        cur.execute('SELECT COUNT(*) FROM drone_nicknames')
        nicknames = cur.fetchone()[0]
        if nicknames != len(NICKNAME_SEED):
            raise RuntimeError(
                'postcondition failed: drone_nicknames count is %d, '
                'expected %d' % (nicknames, len(NICKNAME_SEED)))
        cur.execute(
            'SELECT COUNT(*) FROM drone_nicknames dn '
            'LEFT JOIN drone_units du ON du.id = dn.drone_unit_id '
            'WHERE du.id IS NULL')
        if cur.fetchone()[0] != 0:
            raise RuntimeError('postcondition failed: drone_nicknames row '
                               'does not resolve to a drone_units row')

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. 4 tables, 5 indexes, 15 drone units and %d nickname aliases '
          'are in place.' % len(NICKNAME_SEED))


if __name__ == '__main__':
    run()
