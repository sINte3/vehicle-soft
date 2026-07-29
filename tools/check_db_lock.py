# -*- coding: utf-8 -*-
"""check_db_lock.py -- prove that no other process can write to the database.

Run this with all services stopped, immediately before applying migrations.

It tries to take an EXCLUSIVE lock on instance/transport.db with a short
busy timeout. If any other process holds a write lock, this fails fast and
loudly instead of letting a migration discover it halfway through.

The lock is released immediately (rollback). Nothing is written.

Run:
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" check_db_lock.py

Exit code 0 = safe to migrate. Exit code 1 = something still holds the db.
"""

import os
import sqlite3
import sys

# [REASON]: adopted into tools/ from the untracked production copy
# (DEPLOY-DRIFT-001). ROOT must stay the repository root -- one level above
# tools/ -- so instance/transport.db and the report files resolve exactly
# as they did when this script lived at C:\transport-report directly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')


def main():
    if not os.path.exists(DB_PATH):
        print('ERROR: database not found at ' + DB_PATH, file=sys.stderr)
        sys.exit(1)

    for name in ('transport.db-wal', 'transport.db-shm'):
        path = os.path.join(ROOT, 'instance', name)
        if os.path.exists(path):
            print('  {0:<20} present, {1:,} bytes'.format(
                name, os.path.getsize(path)))
        else:
            print('  {0:<20} absent'.format(name))

    con = sqlite3.connect(DB_PATH, timeout=5)
    try:
        con.execute('PRAGMA busy_timeout=5000')
        con.execute('BEGIN EXCLUSIVE')
        con.rollback()
        print('')
        print('EXCLUSIVE LOCK OK -- no other process is writing to the database.')
        print('Safe to run migrations.')
    except sqlite3.OperationalError as exc:
        print('')
        print('LOCKED: {0}'.format(exc), file=sys.stderr)
        print('Another process still holds the database. DO NOT MIGRATE.',
              file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


if __name__ == '__main__':
    main()
