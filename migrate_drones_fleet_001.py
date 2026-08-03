# -*- coding: utf-8 -*-
"""Migration DRONES_FLEET_001 -- DRONE-FLEET-001 fleet directories.

Creates FOUR tables and adds THREE columns to drone_units. Purely additive:
no existing table is dropped, no existing column is altered, no existing row
is read for modification.

[REASON]: the task text says "five new tables" in its scope line but then
enumerates exactly four (2.1 drone_operators, 2.2 drone_operator_assignments,
2.3 drone_unit_status_log, 2.4 drone_batteries); 2.5 is the additive column
set on drone_units, not a table. Four tables are created here, because
inventing a fifth to match a count would put a table nobody specified into a
register people will trust. The discrepancy is reported, not silently
resolved.

Creates:
  - drone_operators              -- the operator directory. No deletion, only
                                    deactivation: an operator who left still
                                    owns historical hectares.
  - drone_operator_assignments   -- operator <-> machine WITH VALIDITY DATES.
  - drone_unit_status_log        -- append-only status ledger of a machine.
  - drone_batteries              -- the battery register.

Adds to drone_units:
  - hardware_id       TEXT  factory identifier of the airframe
  - generator_id      TEXT
  - subdivision_name  TEXT  descriptive only, never an attribution key

[REASON]: drone_operator_assignments.date_from is NOT NULL and date_to is
nullable ("still current") because an assignment without a validity period
cannot reproduce history. The 2026-08-03 reconciliation against the
dispatchers' April ledgers matched the month total to 0.15 % while two
per-subdivision errors of +460 ha and -544 ha cancelled out: one operator flew
machine No 4 until 13 April and machine No 8 from 14 April. Our own data
confirms the boundary independently -- No 4 has 198 flights, none after
13 April; No 8 jumps from 107 to 541 flights on the same date. A dateless
assignment would put those hectares on the wrong person again the next time
an operator moves.

[REASON]: OVERLAPS ARE NOT BLOCKED at the schema level (no unique index, no
exclusion constraint over the period). Two operators genuinely appear on one
machine in the source data, and substitutions are written as
"Anvarov Usmon (Qodirov Nurali)". A database that refuses the real data makes
the screen unusable and pushes someone to invent dates. The screen saves the
row and warns; the report shows a flight covered by more than one assignment
in its own visible row instead of picking a candidate.

[REASON]: drone_batteries.serial_number carries a NON-UNIQUE index. The
source contains a genuine duplicate -- batteries labelled No 8 and No 10 both
bear 65VPN19DA50MSU. That is either a transcription error or two identical
labels, and neither this migration nor the person running it can tell which. A
unique index would make the seed fail and would push someone to "fix" data he
cannot verify. Duplicates are surfaced on the screen as a warning instead.

[REASON]: drone_unit_status_log.changed_on is a DATE supplied by the user, not
now(): a machine is reported broken after the fact and the ledger has to carry
the date it actually broke. Only future dates are refused, and that check
lives in the route, not here.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS plus a PRAGMA table_info guard before every
    ALTER TABLE (SQLite has no ADD COLUMN IF NOT EXISTS), so a second run
    over an already-migrated database is a no-op even if the registry row
    were lost;
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
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_fleet_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are SEPARATE, and in that order. Reverting
  the DRONE-FLEET-001 commits (7 -> 1) leaves the four tables and the three
  columns in place; that is harmless -- nothing reads them once the models are
  gone, and db.create_all() never drops anything. Dropping them is a manual
  step and is only correct AFTER the code revert, because a running instance
  of the new code would 500 on every drones screen without them.

  Data rollback, only after the code revert:

    DROP TABLE drone_operator_assignments;
    DROP TABLE drone_operators;
    DROP TABLE drone_unit_status_log;
    DROP TABLE drone_batteries;
    ALTER TABLE drone_units DROP COLUMN hardware_id;
    ALTER TABLE drone_units DROP COLUMN generator_id;
    ALTER TABLE drone_units DROP COLUMN subdivision_name;
    DELETE FROM schema_migrations WHERE name = 'DRONES_FLEET_001';

  ALTER TABLE ... DROP COLUMN needs SQLite 3.35+ (Python 3.14 on the server
  ships 3.45). On an older SQLite the three columns stay; they are nullable
  and unread after the revert, so leaving them is the safe choice -- do NOT
  rebuild drone_units by hand to remove them.

  No rollback step edits or deletes any pre-existing row of any other table.
  If the seed (DRONES_FLEET_SEED_001) was already applied, its rollback runs
  FIRST and is documented in its own docstring.
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
MIGRATION_ID = 'DRONES_FLEET_001'
DESCRIPTION = ('DRONE-FLEET-001: create drone_operators, '
               'drone_operator_assignments (operator to machine WITH dates), '
               'drone_unit_status_log (append-only, two statuses) and '
               'drone_batteries (serial NOT unique); add hardware_id, '
               'generator_id and subdivision_name to drone_units. Additive '
               'only, no pre-existing row modified.')

CREATE_DRONE_OPERATORS = """
    CREATE TABLE IF NOT EXISTS drone_operators (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name        TEXT NOT NULL,
        phone_primary    TEXT,
        phone_secondary  TEXT,
        subdivision_name TEXT,
        is_active        BOOLEAN DEFAULT 1,
        note             TEXT,
        created_at       DATETIME,
        updated_at       DATETIME
    )
"""

CREATE_DRONE_OPERATOR_ASSIGNMENTS = """
    CREATE TABLE IF NOT EXISTS drone_operator_assignments (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id   INTEGER NOT NULL REFERENCES drone_operators (id),
        drone_unit_id INTEGER NOT NULL REFERENCES drone_units (id),
        date_from     DATE NOT NULL,
        date_to       DATE,
        note          TEXT,
        created_at    DATETIME,
        created_by    INTEGER REFERENCES users (id)
    )
"""

CREATE_DRONE_UNIT_STATUS_LOG = """
    CREATE TABLE IF NOT EXISTS drone_unit_status_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        drone_unit_id INTEGER NOT NULL REFERENCES drone_units (id),
        status        TEXT NOT NULL,
        comment       TEXT,
        changed_on    DATE NOT NULL,
        changed_by    INTEGER REFERENCES users (id),
        created_at    DATETIME
    )
"""

CREATE_DRONE_BATTERIES = """
    CREATE TABLE IF NOT EXISTS drone_batteries (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        serial_number TEXT NOT NULL,
        label_number  INTEGER,
        drone_unit_id INTEGER REFERENCES drone_units (id),
        condition     TEXT,
        note          TEXT,
        is_active     BOOLEAN DEFAULT 1,
        created_at    DATETIME,
        updated_at    DATETIME
    )
"""

CREATE_INDEXES = (
    # Both index pairs are required by the task: a machine's timeline and an
    # operator's timeline are the two directions the screens read.
    """CREATE INDEX IF NOT EXISTS ix_drone_oper_assign_unit_from
       ON drone_operator_assignments (drone_unit_id, date_from)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_oper_assign_operator_from
       ON drone_operator_assignments (operator_id, date_from)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_unit_status_log_unit_changed
       ON drone_unit_status_log (drone_unit_id, changed_on)""",
    # [REASON]: NON-unique on purpose -- see the module docstring. The index
    # exists so the duplicate-serial warning on the screen is a lookup and
    # not a table scan; it must never enforce uniqueness.
    """CREATE INDEX IF NOT EXISTS ix_drone_batteries_serial
       ON drone_batteries (serial_number)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_batteries_unit
       ON drone_batteries (drone_unit_id)""",
)

NEW_TABLES = ('drone_operators', 'drone_operator_assignments',
              'drone_unit_status_log', 'drone_batteries')

EXPECTED_COLUMNS = {
    'drone_operators': ('id', 'full_name', 'phone_primary', 'phone_secondary',
                        'subdivision_name', 'is_active', 'note', 'created_at',
                        'updated_at'),
    'drone_operator_assignments': ('id', 'operator_id', 'drone_unit_id',
                                   'date_from', 'date_to', 'note',
                                   'created_at', 'created_by'),
    'drone_unit_status_log': ('id', 'drone_unit_id', 'status', 'comment',
                              'changed_on', 'changed_by', 'created_at'),
    'drone_batteries': ('id', 'serial_number', 'label_number',
                        'drone_unit_id', 'condition', 'note', 'is_active',
                        'created_at', 'updated_at'),
}

# (column, DDL type) added to drone_units, in order.
NEW_UNIT_COLUMNS = (
    ('hardware_id', 'TEXT'),
    ('generator_id', 'TEXT'),
    ('subdivision_name', 'TEXT'),
)

EXPECTED_INDEXES = ('ix_drone_oper_assign_unit_from',
                    'ix_drone_oper_assign_operator_from',
                    'ix_drone_unit_status_log_unit_changed',
                    'ix_drone_batteries_serial',
                    'ix_drone_batteries_unit')


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


def _unique_index_columns(cur, table):
    """Columns covered by any UNIQUE index on `table`, as a flat set."""
    columns = set()
    cur.execute('PRAGMA index_list(%s)' % table)
    for row in cur.fetchall():
        # PRAGMA index_list columns: seq, name, unique, origin, partial
        if not row[2]:
            continue
        cur.execute('PRAGMA index_info(%s)' % row[1])
        for info in cur.fetchall():
            if info[2] is not None:
                columns.add(info[2])
    return columns


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
    con.execute('PRAGMA busy_timeout=30000')
    try:
        cur = con.cursor()
        cur.execute('BEGIN')

        # [REASON]: every REFERENCES target must exist before the tables are
        # created. drone_units missing means the drones module was never
        # installed and this migration is pointed at the wrong database.
        for table in ('users', 'drone_units'):
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        cur.execute(CREATE_DRONE_OPERATORS)
        cur.execute(CREATE_DRONE_OPERATOR_ASSIGNMENTS)
        cur.execute(CREATE_DRONE_UNIT_STATUS_LOG)
        cur.execute(CREATE_DRONE_BATTERIES)
        for statement in CREATE_INDEXES:
            cur.execute(statement)

        # [REASON]: SQLite has no ADD COLUMN IF NOT EXISTS -- a second ALTER
        # raises "duplicate column name" and kills the whole transaction, so
        # every ALTER is guarded by PRAGMA table_info.
        unit_columns = _column_names(cur, 'drone_units')
        for column, ddl_type in NEW_UNIT_COLUMNS:
            if column not in unit_columns:
                cur.execute('ALTER TABLE drone_units ADD COLUMN %s %s'
                            % (column, ddl_type))

        # ---- Postconditions, all of them BEFORE the registry write. ----
        for table in NEW_TABLES:
            if not _table_exists(cur, table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
            columns = _column_names(cur, table)
            for name in EXPECTED_COLUMNS[table]:
                if name not in columns:
                    raise RuntimeError('postcondition failed: column %s '
                                       'missing on %s' % (name, table))

        for name in EXPECTED_INDEXES:
            if not _index_exists(cur, name):
                raise RuntimeError('postcondition failed: index %s missing'
                                   % name)

        # [REASON]: an explicit NEGATIVE postcondition. The no-unique-index
        # rule on drone_batteries.serial_number is the one property of this
        # migration that a later hand-edit could quietly reverse, and the
        # seed would then fail on the genuine duplicate 65VPN19DA50MSU. A
        # check that only ever confirms what the CREATE statement said is not
        # a check; this one reads the database back.
        if 'serial_number' in _unique_index_columns(cur, 'drone_batteries'):
            raise RuntimeError('postcondition failed: serial_number must NOT '
                               'be covered by a UNIQUE index')

        unit_columns = _column_names(cur, 'drone_units')
        for column, _ddl in NEW_UNIT_COLUMNS:
            if column not in unit_columns:
                raise RuntimeError('postcondition failed: column %s missing '
                                   'on drone_units' % column)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. %d tables, %d indexes, %d new columns on drone_units.'
          % (len(NEW_TABLES), len(EXPECTED_INDEXES), len(NEW_UNIT_COLUMNS)))


if __name__ == '__main__':
    run()
