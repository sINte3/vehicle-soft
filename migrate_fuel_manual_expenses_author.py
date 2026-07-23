# -*- coding: utf-8 -*-
"""Migration FUEL_MANUAL_EXPENSES_AUTHOR -- FUEL-MANUAL-EXP.

Adds three nullable author/soft-delete columns to the EXISTING
fuel_manual_expenses table (diesel written off the warehouse bypassing the
Topaz dispenser -- e.g. pumped straight from the vessel into a tanker):

    created_by INTEGER  REFERENCES users(id)  -- who entered the row
    deleted_by INTEGER  REFERENCES users(id)  -- who soft-deleted the row
    deleted_at DATETIME                       -- when it was soft-deleted

The table itself already exists on production and staging with live rows
that the balance report counts today; it was created OUTSIDE the migration
system and is absent from schema_migrations. This migration fixes that
registry gap by registering itself, without disturbing the table contents.

NO BACKFILL. The pre-existing rows were inserted by hand and have no
author; the owner's decision is to leave them exactly as they are. They
keep created_by = NULL and render with a dash in the author column of the
new UI. Do not delete them, do not backfill a fake author, do not rewrite
their notes.

This migration modifies NO pre-existing row and NO other table:
pre-existing tables are only read for the prerequisite check below; the
only writes are the guarded ALTER TABLE ... ADD COLUMN statements and the
INSERT of the migration's own registration row into schema_migrations.

Safe / idempotent:
  - SQLite cannot add a column conditionally, so PRAGMA table_info is read
    first and only the missing columns are added -- a re-run is a no-op.
  - Registers itself in schema_migrations and skips on re-run.
  - All three columns are nullable with no DEFAULT clause, so ALTER TABLE
    ADD COLUMN rewrites no row data.

Hardened (RE-SP-011 pattern, same as migrate_spare_parts_min_levels):
  - FAILS LOUDLY (and records nothing) if any prerequisite table is missing
    (fuel_manual_expenses, users).
  - Verifies its postconditions BEFORE recording itself: all three columns
    present and the table's row count unchanged.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_fuel_manual_expenses_author.py
  .\\nssm.exe start TransportReport

Rollback:
  SQLite before 3.35 cannot DROP COLUMN, so a clean structural rollback
  does not exist and none is pretended here. The practical rollback is:

    DELETE FROM schema_migrations WHERE name = 'FUEL_MANUAL_EXPENSES_AUTHOR';

  and leaving the three unused nullable columns in place, which is
  harmless once the application code that reads them is reverted.
  No rollback step deletes a fuel_manual_expenses row -- the data belongs
  to the operator, not to this feature.
"""

import os
import sqlite3
import sys

from migration_utils import (
    DB_PATH,
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

MIGRATION_ID = 'FUEL_MANUAL_EXPENSES_AUTHOR'
DESCRIPTION = ('FUEL-MANUAL-EXP: add nullable created_by/deleted_by/deleted_at '
               'columns to the pre-existing fuel_manual_expenses table and '
               'register the table in the migration registry. No backfill; '
               'no pre-existing row modified.')

PREREQUISITE_TABLES = (
    'fuel_manual_expenses',
    'users',
)

# [REASON]: FUEL-MANUAL-EXP -- mirrors the three columns added to
# models.FuelManualExpense (same names, types, nullability). All three are
# nullable ON PURPOSE: the two live hand-inserted rows must stay valid with
# created_by = NULL, and SQLite ALTER TABLE ADD COLUMN only accepts NULL /
# constant defaults anyway.
NEW_COLUMNS = [
    ('created_by',
     "ALTER TABLE fuel_manual_expenses"
     " ADD COLUMN created_by INTEGER REFERENCES users(id)"),
    ('deleted_by',
     "ALTER TABLE fuel_manual_expenses"
     " ADD COLUMN deleted_by INTEGER REFERENCES users(id)"),
    ('deleted_at',
     "ALTER TABLE fuel_manual_expenses"
     " ADD COLUMN deleted_at DATETIME"),
]


def _table_exists(cur, name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _existing_columns(cur):
    cur.execute("PRAGMA table_info(fuel_manual_expenses)")
    return {row[1] for row in cur.fetchall()}


def _row_count(cur):
    cur.execute("SELECT count(*) FROM fuel_manual_expenses")
    return cur.fetchone()[0]


def _assert_prerequisites(cur):
    # [REASON]: RE-SP-011 -- fail loudly instead of silently registering on a
    # database where the hand-created fuel_manual_expenses table (or users)
    # does not exist. ALTER TABLE against a missing table must never be
    # papered over by a registry record.
    missing = [t for t in PREREQUISITE_TABLES if not _table_exists(cur, t)]
    if missing:
        raise RuntimeError(
            "prerequisite table(s) missing: {} -- this migration only "
            "extends the existing fuel_manual_expenses table; NOT recording "
            "this migration".format(', '.join(missing)))


def _add_missing_columns(cur):
    existing = _existing_columns(cur)
    for name, ddl in NEW_COLUMNS:
        if name in existing:
            print("  column {} already present -- skipped".format(name))
            continue
        cur.execute(ddl)
        print("  column {} added".format(name))


def _assert_postconditions(cur, row_count_before):
    # [REASON]: RE-SP-011 -- only a verified-complete run may be recorded.
    existing = _existing_columns(cur)
    missing = [name for name, _ddl in NEW_COLUMNS if name not in existing]
    if missing:
        raise RuntimeError(
            "postcondition check failed: column(s) {} missing -- NOT "
            "recording this migration".format(', '.join(missing)))
    row_count_after = _row_count(cur)
    if row_count_after != row_count_before:
        raise RuntimeError(
            "postcondition check failed: row count changed from {} to {} -- "
            "NOT recording this migration".format(
                row_count_before, row_count_after))


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: Database not found at ' + DB_PATH, file=sys.stderr)
        print('Run this migration from the project folder with '
              'instance\\transport.db present.', file=sys.stderr)
        sys.exit(1)

    ensure_schema_migrations_table()

    if is_migration_applied(MIGRATION_ID):
        print("Migration {} already applied. Skipping.".format(MIGRATION_ID))
        return

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        cur = con.cursor()

        _assert_prerequisites(cur)
        row_count_before = _row_count(cur)

        print("Adding author/soft-delete columns to fuel_manual_expenses...")
        _add_missing_columns(cur)

        _assert_postconditions(cur, row_count_before)
        con.commit()
    except BaseException as exc:
        con.rollback()
        print("Migration {} FAILED: {}".format(MIGRATION_ID, exc),
              file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()

    # [REASON]: recorded only after the ALTERs are committed and verified;
    # if recording itself fails, a re-run skips the existing columns and
    # simply records again -- no data is touched twice.
    record_migration(
        MIGRATION_ID,
        description=DESCRIPTION,
        checksum=migration_checksum(__file__),
    )
    print("Migration {} applied successfully.".format(MIGRATION_ID))
    print("Summary: 3 nullable columns ensured, {} existing row(s) left "
          "untouched.".format(row_count_before))


if __name__ == '__main__':
    run()
