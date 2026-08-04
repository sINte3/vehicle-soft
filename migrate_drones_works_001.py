# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_001 -- DRONE-WORKS-001 drone customers and works.

Creates THREE tables. Purely additive: no existing table is dropped, no
existing column is altered, no existing row is read for modification.

Creates:
  - drone_customers        -- the drone service's OWN customer directory.
  - drone_customer_aliases -- one spelling of a farm name -> one customer,
                              exactly the shape of drone_nicknames.
  - drone_works            -- the work ledger: one job, its area, its price.

[REASON]: drone_customers is a SEPARATE directory from `customers`, by the
owner's explicit decision of 2026-08-03. The dispatchers' books carry about
526 distinct farm spellings, most without an INN; a foreign key into
`customers` would presume the outcome of a merge nobody has decided yet.
This migration does not read, alter or copy a single row of `customers`.

[REASON]: drone_works HAS NO MACHINE COLUMN, and one of the postconditions
below asserts that it does not. The dispatchers' sheets name the OPERATOR,
never the machine; the machine is derived through drone_operator_assignments
from operator + date. That inversion is what lets the import run today, before
the owner has entered his assignments: every imported row acquires its machine
retroactively with no re-import. A drone_unit_id column here would invert the
dependency and force a reload later, so the check reads the table back rather
than trusting the CREATE statement.

[REASON]: the unique index on (source_file, source_sheet, source_row) is what
makes re-running the import a no-op instead of a doubling. SQLite treats NULLs
inside a unique index as DISTINCT, so rows typed into the manual form -- which
carry no provenance -- are unaffected and any number of them may exist. That
is deliberate, not an oversight.

[REASON]: period_month is NOT NULL while both dates are nullable. 34 rows of
about 708 carry no date at all, and without a month they would fall out of
every monthly report. The month is always knowable from the source file.

Safe / idempotent (same contract as migrate_drones_fleet_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS plus a PRAGMA table_info guard before every
    ALTER TABLE (SQLite has no ADD COLUMN IF NOT EXISTS) -- this migration
    adds no column to an existing table, but the guard helper stays so a
    later edit cannot forget it;
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
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are SEPARATE, and in that order. Reverting
  the DRONE-WORKS-001 commits (7 -> 1) leaves the three tables in place; that
  is harmless -- nothing reads them once the models are gone, and
  db.create_all() never drops anything. Dropping them is a manual step and is
  only correct AFTER the code revert, because a running instance of the new
  code would 500 on every drone works screen without them.

  Data rollback, only after the code revert, in this order (children first):

    DROP TABLE drone_works;
    DROP TABLE drone_customer_aliases;
    DROP TABLE drone_customers;
    DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_001';

  Dropping drone_works DESTROYS the imported ledger. Re-running
  tools/import_drone_works.py over the same directory and manifest rebuilds
  it, because provenance is stored on every row -- but any job typed into the
  manual form has no source file and is gone for good. Take a backup first.

  No rollback step edits or deletes any pre-existing row of any other table,
  and nothing in `customers`, `equipment`, `drone_flights`, `drone_units` or
  `drone_operators` is touched by this migration at any point.
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
MIGRATION_ID = 'DRONES_WORKS_001'
DESCRIPTION = ('DRONE-WORKS-001: create drone_customers, '
               'drone_customer_aliases (normalized_key UNIQUE) and '
               'drone_works (operator, NOT machine; unique provenance triple '
               'source_file/source_sheet/source_row). Additive only, no '
               'pre-existing row modified, `customers` untouched.')

CREATE_DRONE_CUSTOMERS = """
    CREATE TABLE IF NOT EXISTS drone_customers (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          VARCHAR(300) NOT NULL,
        inn           VARCHAR(20),
        customer_type VARCHAR(20),
        is_active     BOOLEAN DEFAULT 1,
        note          TEXT,
        created_at    DATETIME,
        updated_at    DATETIME
    )
"""

CREATE_DRONE_CUSTOMER_ALIASES = """
    CREATE TABLE IF NOT EXISTS drone_customer_aliases (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_name          VARCHAR(300) NOT NULL,
        normalized_key    VARCHAR(300) NOT NULL UNIQUE,
        drone_customer_id INTEGER NOT NULL REFERENCES drone_customers (id),
        is_active         BOOLEAN DEFAULT 1,
        created_at        DATETIME
    )
"""

# [REASON]: NO drone_unit_id / machine column, on purpose -- see the module
# docstring, and the negative postcondition that reads the table back.
CREATE_DRONE_WORKS = """
    CREATE TABLE IF NOT EXISTS drone_works (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        period_month      VARCHAR(7) NOT NULL,
        work_date_from    DATE,
        work_date_to      DATE,
        date_raw          VARCHAR(100),
        drone_operator_id INTEGER REFERENCES drone_operators (id),
        operator_raw      VARCHAR(200),
        drone_customer_id INTEGER REFERENCES drone_customers (id),
        customer_raw      VARCHAR(300) NOT NULL,
        area_ha           NUMERIC NOT NULL,
        price_per_ha      NUMERIC,
        amount            NUMERIC,
        other_costs       NUMERIC,
        received_amount   NUMERIC,
        payment_type      VARCHAR(20) NOT NULL,
        subdivision_name  VARCHAR(120),
        source_file       VARCHAR(300),
        source_sheet      VARCHAR(200),
        source_row        INTEGER,
        import_batch      VARCHAR(100),
        note              TEXT,
        created_at        DATETIME,
        created_by        INTEGER REFERENCES users (id),
        CONSTRAINT uq_drone_works_source UNIQUE (source_file, source_sheet,
                                                 source_row)
    )
"""

CREATE_INDEXES = (
    """CREATE INDEX IF NOT EXISTS ix_drone_customers_name
       ON drone_customers (name)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_customer_aliases_customer
       ON drone_customer_aliases (drone_customer_id)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_works_period
       ON drone_works (period_month)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_works_customer
       ON drone_works (drone_customer_id)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_works_operator
       ON drone_works (drone_operator_id)""",
    """CREATE INDEX IF NOT EXISTS ix_drone_works_date_from
       ON drone_works (work_date_from)""",
)

NEW_TABLES = ('drone_customers', 'drone_customer_aliases', 'drone_works')

EXPECTED_COLUMNS = {
    'drone_customers': ('id', 'name', 'inn', 'customer_type', 'is_active',
                        'note', 'created_at', 'updated_at'),
    'drone_customer_aliases': ('id', 'raw_name', 'normalized_key',
                               'drone_customer_id', 'is_active', 'created_at'),
    'drone_works': ('id', 'period_month', 'work_date_from', 'work_date_to',
                    'date_raw', 'drone_operator_id', 'operator_raw',
                    'drone_customer_id', 'customer_raw', 'area_ha',
                    'price_per_ha', 'amount', 'other_costs',
                    'received_amount', 'payment_type', 'subdivision_name',
                    'source_file', 'source_sheet', 'source_row',
                    'import_batch', 'note', 'created_at', 'created_by'),
}

EXPECTED_INDEXES = ('ix_drone_customers_name',
                    'ix_drone_customer_aliases_customer',
                    'ix_drone_works_period',
                    'ix_drone_works_customer',
                    'ix_drone_works_operator',
                    'ix_drone_works_date_from')

# Columns that must NOT exist on drone_works. See the module docstring.
FORBIDDEN_WORK_COLUMNS = ('drone_unit_id', 'unit_id', 'machine_id',
                          'machine_number')


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


def _unique_index_column_sets(cur, table):
    """Every UNIQUE index on `table`, as a set of column-name tuples."""
    result = set()
    cur.execute('PRAGMA index_list(%s)' % table)
    for row in cur.fetchall():
        # PRAGMA index_list columns: seq, name, unique, origin, partial
        if not row[2]:
            continue
        cur.execute('PRAGMA index_info(%s)' % row[1])
        columns = tuple(info[2] for info in sorted(cur.fetchall(),
                                                   key=lambda r: r[0]))
        result.add(columns)
    return result


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
        # created. drone_operators missing means DRONES_FLEET_001 was never
        # applied, and drone_works without it cannot resolve a single
        # operator -- the import would silently store NULL on every row.
        for table in ('users', 'drone_operators'):
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        cur.execute(CREATE_DRONE_CUSTOMERS)
        cur.execute(CREATE_DRONE_CUSTOMER_ALIASES)
        cur.execute(CREATE_DRONE_WORKS)
        for statement in CREATE_INDEXES:
            cur.execute(statement)

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

        # [REASON]: POSITIVE read-back of the two uniqueness rules. Both are
        # written as inline table constraints, which produce auto-named
        # sqlite_autoindex_* entries -- _index_exists cannot see them, so the
        # check goes through PRAGMA index_list instead of trusting the DDL.
        alias_unique = _unique_index_column_sets(cur, 'drone_customer_aliases')
        if ('normalized_key',) not in alias_unique:
            raise RuntimeError('postcondition failed: normalized_key is not '
                               'covered by a UNIQUE index')
        works_unique = _unique_index_column_sets(cur, 'drone_works')
        if ('source_file', 'source_sheet', 'source_row') not in works_unique:
            raise RuntimeError('postcondition failed: the provenance triple '
                               'is not covered by a UNIQUE index')

        # [REASON]: an explicit NEGATIVE postcondition, and the structural
        # rule of this whole task. A work row stores the operator; the machine
        # is derived from operator + date. A later hand-edit adding a machine
        # column would invert the dependency and force a re-import, and would
        # do it quietly. A check that only confirms what the CREATE statement
        # said is not a check; this one reads the table back.
        work_columns = _column_names(cur, 'drone_works')
        for forbidden in FORBIDDEN_WORK_COLUMNS:
            if forbidden in work_columns:
                raise RuntimeError('postcondition failed: drone_works must '
                                   'NOT carry a machine column (%s found)'
                                   % forbidden)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. %d tables, %d indexes, 2 uniqueness rules verified, no '
          'machine column on drone_works.'
          % (len(NEW_TABLES), len(EXPECTED_INDEXES)))


if __name__ == '__main__':
    run()
