# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_002 -- DRONE-WORKS-IMPORT-FIX-001, received_kind.

Adds ONE nullable column to drone_works. Nothing else: no table is created,
no row is read for modification, no existing column is altered.

  received_kind TEXT NULL   -- 'received' | 'operator_due'

[REASON]: «Кирим қилинган» is money already handed in. «Оператор топшириши
керак» is what the operator still owes. In the real dispatchers' sheets the two
headers occupy the SAME position and both equal amount - other_costs, so a
reader that maps them to one column cannot tell afterwards which it stored.
Conflating them reports an unpaid job as collected and understates the debt
report by the whole column. Both figures still go into received_amount --
they are the same quantity measured from the same row -- and received_kind
records which header it came from.

The column is NULLABLE and nothing is backfilled. [REASON]: the rows already
in drone_works came from a run whose provenance for this distinction does not
exist; inventing 'received' for them would be an audit record this migration
made up. CLAUDE.md: historical audit records are not backfilled and not
invented. A NULL here means "this row predates the distinction", which is the
truth.

ALSO VERIFIED, NOT ASSUMED: drone_works.payment_type carries no CHECK
constraint. The import gains a fourth value, 'unknown', for the roughly two
fifths of the real corpus whose sheets have no payment block at all. If a
CHECK existed, every such row would fail on INSERT at the moment of --apply,
on the owner's machine, after a successful --dry-run. The postcondition below
reads the stored CREATE TABLE text back out of sqlite_master and refuses to
record the migration if a CHECK mentioning payment_type is there. That check
is the reason this migration exists at all for a project that could otherwise
have shipped the fourth value with no schema change.

Safe / idempotent (same contract as migrate_drones_works_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - PRAGMA table_info guard before the ALTER -- SQLite has no
    ADD COLUMN IF NOT EXISTS, and a second ALTER raises "duplicate column
    name" and kills the whole transaction;
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
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_002.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are SEPARATE, and in that order. Reverting
  the DRONE-WORKS-IMPORT-FIX-001 commits leaves the column in place; that is
  harmless -- it is nullable and unread once the model is gone, and
  db.create_all() never drops anything.

  Data rollback, only after the code revert:

    ALTER TABLE drone_works DROP COLUMN received_kind;
    DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_002';

  ALTER TABLE ... DROP COLUMN needs SQLite 3.35+ (Python 3.14 on the server
  ships 3.45). On an older SQLite the column stays; it is nullable and unread
  after the revert, so leaving it is the safe choice -- do NOT rebuild
  drone_works by hand to remove it, the table carries the imported ledger.

  No rollback step edits or deletes a single row of any table.
"""

import os
import re
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
MIGRATION_ID = 'DRONES_WORKS_002'
DESCRIPTION = ('DRONE-WORKS-IMPORT-FIX-001: add drone_works.received_kind '
               "('received' | 'operator_due'), nullable, no backfill. Verify "
               'that payment_type carries no CHECK constraint so the fourth '
               "value 'unknown' can be stored. Additive only.")

# (column, DDL type) added to drone_works.
NEW_WORK_COLUMNS = (
    ('received_kind', 'VARCHAR(20)'),
)

# [REASON]: matches a CHECK constraint that mentions payment_type anywhere in
# the stored CREATE TABLE text, in either constraint form -- inline on the
# column or as a table constraint. Deliberately broad: a false positive stops
# a migration and asks a human to look, a false negative ships a tool that
# fails on the owner's first --apply.
_PAYMENT_CHECK_RE = re.compile(r'check\s*\([^)]*payment_type', re.I | re.S)


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


def _table_sql(cur, table):
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,))
    row = cur.fetchone()
    return row[0] if row and row[0] else ''


def payment_type_has_check(sql_text):
    """True when the stored table definition CHECKs payment_type.

    Kept module-level and pure so the test suite can feed it a table
    definition that DOES carry the constraint. A guard that has only ever
    been run against a database where the answer is 'no' has not been shown
    to discriminate.
    """
    return bool(_PAYMENT_CHECK_RE.search(sql_text or ''))


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

        # [REASON]: drone_works missing means DRONES_WORKS_001 was never
        # applied and this migration is pointed at the wrong database.
        if not _table_exists(cur, 'drone_works'):
            raise RuntimeError(
                'precondition failed: table drone_works does not exist')

        # [REASON]: SQLite has no ADD COLUMN IF NOT EXISTS -- a second ALTER
        # raises "duplicate column name" and kills the whole transaction, so
        # the ALTER is guarded by PRAGMA table_info.
        work_columns = _column_names(cur, 'drone_works')
        for column, ddl_type in NEW_WORK_COLUMNS:
            if column not in work_columns:
                cur.execute('ALTER TABLE drone_works ADD COLUMN %s %s'
                            % (column, ddl_type))

        # ---- Postconditions, all of them BEFORE the registry write. ----
        work_columns = _column_names(cur, 'drone_works')
        for column, _ddl in NEW_WORK_COLUMNS:
            if column not in work_columns:
                raise RuntimeError('postcondition failed: column %s missing '
                                   'on drone_works' % column)

        # [REASON]: the check this migration exists for. Read the definition
        # back out of the database rather than trusting what
        # migrate_drones_works_001.py wrote -- a hand-edit or a table rebuilt
        # by somebody else is exactly the case that would bite, and it would
        # bite on the owner's first --apply, after a clean --dry-run.
        if payment_type_has_check(_table_sql(cur, 'drone_works')):
            raise RuntimeError(
                'postcondition failed: drone_works.payment_type carries a '
                "CHECK constraint; the fourth value 'unknown' cannot be "
                'stored. Rebuild the table without it before importing.')

        # [REASON]: NOT NULL on payment_type must survive. The fourth value is
        # a value, not an absence: a row whose payment type is unknown still
        # says so explicitly, and a NULL creeping in would make two different
        # spellings of the same fact.
        cur.execute('PRAGMA table_info(drone_works)')
        not_null = {row[1]: row[3] for row in cur.fetchall()}
        if not not_null.get('payment_type'):
            raise RuntimeError('postcondition failed: payment_type lost its '
                               'NOT NULL')

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. %d column added to drone_works, payment_type has no CHECK '
          'and keeps NOT NULL.' % len(NEW_WORK_COLUMNS))


if __name__ == '__main__':
    run()
