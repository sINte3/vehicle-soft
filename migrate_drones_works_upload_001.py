# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_UPLOAD_001 -- DRONE-WORKS-UPLOAD-001, upload journal.

Creates ONE new table, drone_work_imports, and one index on it. Nothing else:
no existing table is altered, no column is added anywhere, no row of any
existing table is read for modification.

  drone_work_imports -- one row per UPLOAD BATCH, not per file.

The dispatchers' books arrive by hand once a month and until now the only way
in was tools/import_drone_works.py run from a PowerShell prompt with a
hand-written manifest. This table is the journal behind the screen that
replaces that prompt: what was uploaded, by whom, for which period, what the
dry run predicted, what the apply actually wrote, and where the original files
were put. It is a journal, not a ledger: nothing in it participates in any
report, and deleting a row loses provenance but no work.

[REASON]: files_json holds name + sha256 + size per file rather than a child
table. The list is read as a whole, always with its batch, never joined and
never filtered on -- one to twelve short rows per batch. A child table would
buy a query nobody makes and cost a second migration.

[REASON]: storage_dir is stored RELATIVE to the books root (it is the batch id
as text, e.g. '7'), never as an absolute path. The books root is derived at
runtime from the SQLite file's own directory, so production and staging
separate automatically; an absolute path baked in here would point one
installation at the other's files after a database copy -- which is exactly
how staging gets refreshed.

NEGATIVE POSTCONDITION -- the point of this migration as much as the CREATE.
The whole increment rests on the claim that the ledger schema does not move.
PRAGMA table_info() of drone_works, drone_customers and drone_customer_aliases
is captured BEFORE the DDL and again AFTER it, and any difference at all --
a column added, removed, renamed, retyped, or its NOT NULL / DEFAULT / PK flag
changed -- aborts with a non-zero exit and a rolled-back transaction. The claim
is proven on every run rather than asserted in a commit message.

Safe / idempotent (same contract as migrate_drones_works_002.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS, so a second run
    over a half-applied database cannot raise;
  - registered through migration_utils (INSERT OR IGNORE), skips itself on a
    re-run and prints 'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE the registry write;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and turns a reader
    into a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_upload_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are SEPARATE, and in that order. Reverting
  the DRONE-WORKS-UPLOAD-001 commits leaves the table in place; that is
  harmless -- nothing else reads it and db.create_all() never drops anything.

  Data rollback, only after the code revert:

    DROP TABLE drone_work_imports;
    DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_UPLOAD_001';

  Neither step touches drone_works, drone_customers or
  drone_customer_aliases. Rows an apply already wrote into drone_works are
  ORDINARY IMPORTED WORKS and are not rolled back by any of the above; if the
  owner wants them gone they are deleted on
  import_batch = 'upload-<batch id>', which is why the batch value is derived
  from this table's row id. The stored .xlsx books under
  <database directory>/drone_work_books/<batch id>/ are left on disk on
  purpose: they are the evidence the parse can be repeated against.
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
MIGRATION_ID = 'DRONES_WORKS_UPLOAD_001'
DESCRIPTION = ('DRONE-WORKS-UPLOAD-001: add table drone_work_imports (one row '
               'per upload batch of dispatchers\' books) and its created_at '
               'index. Additive only: drone_works, drone_customers and '
               'drone_customer_aliases are verified unchanged before and '
               'after.')

# [REASON]: the tables whose shape this increment promises not to move. The
# promise is checked, not asserted -- see the module docstring.
FROZEN_TABLES = ('drone_works', 'drone_customers', 'drone_customer_aliases')

# [REASON]: the DDL types match models.DroneWorkImport column for column, the
# same rule migrate_drones_works_001.py obeys for drone_works. A fresh install
# gets this table from db.create_all() and a migrated one gets it from here;
# if the two disagreed on a type, the same code would meet two different
# columns depending on how the database came to exist.
CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS drone_work_imports (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at             VARCHAR(40) NOT NULL,
        created_by             INTEGER REFERENCES users (id),
        period_month           VARCHAR(7) NOT NULL,
        status                 VARCHAR(20) NOT NULL,
        import_batch           VARCHAR(100),
        storage_dir            VARCHAR(200) NOT NULL,
        files_json             TEXT NOT NULL,
        note                   TEXT,
        preview_rows           INTEGER,
        preview_area_ha        NUMERIC,
        preview_amount         NUMERIC,
        applied_at             VARCHAR(40),
        applied_by             INTEGER REFERENCES users (id),
        works_inserted         INTEGER,
        works_skipped_existing INTEGER,
        customers_created      INTEGER,
        rows_rejected          INTEGER,
        rows_duplicate         INTEGER,
        report_file            VARCHAR(200),
        error_text             TEXT
    )
"""

# The journal shows the last 50 batches, newest first, and is never filtered.
CREATE_INDEX_SQL = ('CREATE INDEX IF NOT EXISTS ix_drone_work_imports_created '
                    'ON drone_work_imports (created_at DESC)')

# Columns the table must carry when the migration is done. Checked as a
# postcondition so a hand-created table of the same name -- an earlier
# half-finished attempt, say -- cannot be silently accepted by
# CREATE TABLE IF NOT EXISTS and then fail on the first INSERT.
EXPECTED_COLUMNS = (
    'id', 'created_at', 'created_by', 'period_month', 'status',
    'import_batch', 'storage_dir', 'files_json', 'note', 'preview_rows',
    'preview_area_ha', 'preview_amount', 'applied_at', 'applied_by',
    'works_inserted', 'works_skipped_existing', 'customers_created',
    'rows_rejected', 'rows_duplicate', 'report_file', 'error_text',
)


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def table_shape(cur, table):
    """PRAGMA table_info(table) as a comparable tuple of tuples.

    Kept module-level and pure so the test path can feed it two connections.
    The WHOLE row is compared, not just the name: a column that changed type,
    lost NOT NULL, gained a DEFAULT or moved position is a schema move too,
    and 'the column set is the same' would not notice any of them.
    """
    cur.execute('PRAGMA table_info(%s)' % table)
    return tuple(tuple(row) for row in cur.fetchall())


def shapes_of(cur, tables):
    return dict((name, table_shape(cur, name)) for name in tables)


def describe_shape_difference(before, after):
    """Human-readable ASCII list of tables whose shape moved. [] when none.

    Only the rows that actually differ are named. Printing both PRAGMA dumps
    in full would bury the one changed column in twenty-four unchanged ones
    and the message would be read as noise.
    """
    problems = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name) or ()
        new = after.get(name) or ()
        if old == new:
            continue
        gone = [row for row in old if row not in new]
        added = [row for row in new if row not in old]
        problems.append('%s: %d column(s) before, %d after; removed/changed=%r'
                        ' added/changed=%r' % (name, len(old), len(new),
                                               gone, added))
    return problems


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

        # [REASON]: the three ledger tables missing means DRONES_WORKS_001 was
        # never applied and this migration is pointed at the wrong database.
        # Creating the journal there would leave a table nothing can fill.
        for table in FROZEN_TABLES:
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        before = shapes_of(cur, FROZEN_TABLES)

        cur.execute(CREATE_TABLE_SQL)
        cur.execute(CREATE_INDEX_SQL)

        # ---- Postconditions, all of them BEFORE the registry write. ----
        if not _table_exists(cur, 'drone_work_imports'):
            raise RuntimeError(
                'postcondition failed: table drone_work_imports missing')

        columns = [row[1] for row in table_shape(cur, 'drone_work_imports')]
        missing = [c for c in EXPECTED_COLUMNS if c not in columns]
        if missing:
            raise RuntimeError(
                'postcondition failed: drone_work_imports is missing '
                'column(s) %s - a table of that name already existed with a '
                'different shape' % ', '.join(missing))

        cur.execute("SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='ix_drone_work_imports_created'")
        if cur.fetchone() is None:
            raise RuntimeError('postcondition failed: index '
                               'ix_drone_work_imports_created missing')

        # [REASON]: THE negative postcondition. This increment's whole claim is
        # that the ledger schema does not move; proving it beats asserting it,
        # and it costs two PRAGMA reads.
        after = shapes_of(cur, FROZEN_TABLES)
        problems = describe_shape_difference(before, after)
        if problems:
            raise RuntimeError(
                'postcondition failed: the ledger schema MOVED, which this '
                'migration must never do -- %s' % ' | '.join(problems))

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. Table drone_work_imports created with %d columns and one '
          'index; %s verified unchanged.'
          % (len(EXPECTED_COLUMNS), ', '.join(FROZEN_TABLES)))


if __name__ == '__main__':
    run()
