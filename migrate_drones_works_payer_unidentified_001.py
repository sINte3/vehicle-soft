# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_PAYER_UNIDENTIFIED_001 -- FIN-RECON-002, «Фукаро».

Adds ONE nullable column to drone_works. Nothing else: no table is created,
no row is read for modification, no existing column is altered.

  payer_unidentified BOOLEAN NULL   -- 1 = the sheet names the payer only
                                       generically («Фукаро»: a citizen), the
                                       individual is not established

[REASON]: owner's decision of 27.10.2026. «Фукаро» is Uzbek for «citizen»;
the dispatchers write it for any client without a legal entity, so different
works under that one word belong to different people. Linking them all to one
card turns several people into one debtor whose payments offset each other's
debts. The fact «the payer is a citizen not yet identified» belongs to the
WORK, not to a card: drone_customer_id stays NULL and this column says why,
so the reports can show the money without inventing a person
(tools/import_drone_works.py GENERIC_PAYER_KEYS decides which spellings are
generic; drones.py reads the column).

NOTHING IS BACKFILLED. [REASON]: the seven May works already linked to card
#589 «Фукаро» belong to a closed period, and the owner decided they stay as
they are until a separate permission. CLAUDE.md: historical records are not
rewritten and not invented. The postcondition below proves every existing
row still carries NULL.

Safe / idempotent (same contract as migrate_drones_works_002.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - PRAGMA table_info guard before the ALTER -- SQLite has no
    ADD COLUMN IF NOT EXISTS;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only;
  - console output is ASCII only.

ORDER: this migration runs BEFORE the code that reads the column is started.
The model maps the column, so a service started on the new code against a
database without it fails on the first query of drone_works.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_payer_unidentified_001.py
  .\\nssm.exe start TransportReport

  (--db <copy> exists only for the rehearsal on a backup copy.)

Rollback:
  Code rollback and data rollback are SEPARATE, and in that order. Reverting
  the commit that reads the column leaves the column in place; that is
  harmless only while no row carries 1 -- a reverted reader would show a
  flagged work as «Заказчик не определён» and invite an alias for «Фукаро».
  So, after the code revert and before anything else:

    SELECT COUNT(*) FROM drone_works WHERE payer_unidentified = 1;

  and keep the count with the rollback record. Data rollback, only after the
  code revert:

    ALTER TABLE drone_works DROP COLUMN payer_unidentified;
    DELETE FROM schema_migrations
      WHERE name = 'DRONES_WORKS_PAYER_UNIDENTIFIED_001';

  ALTER TABLE ... DROP COLUMN needs SQLite 3.35+. No rollback step edits or
  deletes a row of drone_works.
"""

import argparse
import os
import sqlite3
import sys

import migration_utils
from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'DRONES_WORKS_PAYER_UNIDENTIFIED_001'
DESCRIPTION = ('FIN-RECON-002: add drone_works.payer_unidentified (nullable '
               'boolean) -- the payer of the work is a citizen named only '
               'generically, not established. No backfill. Additive only.')

# (column, DDL type) added to drone_works.
NEW_WORK_COLUMNS = (
    ('payer_unidentified', 'BOOLEAN'),
)


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
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
        # raises "duplicate column name" and kills the whole transaction.
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

        # [REASON]: the no-backfill promise, read back rather than assumed.
        # A column that came back from an earlier half-applied attempt with
        # values in it would otherwise be registered as clean.
        cur.execute('SELECT COUNT(*) FROM drone_works '
                    'WHERE payer_unidentified IS NOT NULL')
        flagged = cur.fetchone()[0]
        if flagged:
            raise RuntimeError('postcondition failed: %d existing work(s) '
                               'already carry payer_unidentified; this '
                               'migration backfills nothing' % flagged)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. %d column added to drone_works, no row backfilled.'
          % len(NEW_WORK_COLUMNS))


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--db', default=None,
                        help='override only for a rehearsal on a copy '
                             '(tools/fin_recon_002_staging_check.py)')
    args = parser.parse_args(argv)
    global DB_PATH
    if args.db:
        # [REASON]: the registry helpers of migration_utils read their own
        # DB_PATH; overriding only this module's would write the registry row
        # into instance/transport.db while the column lands in the copy.
        DB_PATH = migration_utils.DB_PATH = os.path.abspath(args.db)
    run()


if __name__ == '__main__':
    main()
