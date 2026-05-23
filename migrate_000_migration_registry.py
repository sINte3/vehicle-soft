# -*- coding: utf-8 -*-
"""
migrate_000_migration_registry.py -- TASK-OPS-001
Bootstrap the migration registry table (schema_migrations).

Purpose:
  Creates the schema_migrations table in the production SQLite database.
  This table tracks which migration scripts have been applied, when, and
  with what file checksum. After creating the table, this script records
  itself as the first applied migration.

  Future migration scripts can use migration_utils.is_migration_applied()
  to guard against accidental re-runs.

Safety:
  - Idempotent: uses CREATE TABLE IF NOT EXISTS.
  - Safe to run multiple times without business-data side effects.
  - Does not touch any business tables.
  - Refuses to create a new empty SQLite database if instance/transport.db is missing.
  - Prints a clear account of what it did or skipped.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_before_ops001
  "C:\\Program Files\\Python314\\python.exe" migrate_000_migration_registry.py
  .\\nssm.exe start TransportReport

Rollback:
  schema_migrations contains no business data; dropping it is safe.
  To undo manually:
    "C:\\Program Files\\Python314\\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); c.execute('DROP TABLE IF EXISTS schema_migrations'); c.commit(); c.close()"

  Safest rollback: restore the backup made before running this script.
    .\\nssm.exe stop TransportReport
    copy /Y instance\\transport.db.backup_before_ops001 instance\\transport.db
    .\\nssm.exe start TransportReport
"""

import hashlib
import os
import sqlite3
from datetime import datetime

THIS_MIGRATION = 'migrate_000_migration_registry'
DESCRIPTION    = 'Bootstrap schema_migrations registry table.'

SCRIPT_PATH = os.path.abspath(__file__)
DB_PATH = os.path.join(os.path.dirname(SCRIPT_PATH), 'instance', 'transport.db')


def migration_checksum(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: Database not found at ' + DB_PATH)
        print('Check that instance\\transport.db exists before running migrations.')
        return

    checksum = migration_checksum(SCRIPT_PATH)

    print('Database : ' + DB_PATH)
    print('Migration: ' + THIS_MIGRATION)
    print('Checksum : ' + checksum)
    print()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Step 1: create the registry table if absent.
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    table_exists = cur.fetchone() is not None

    if table_exists:
        print('schema_migrations table : already exists. No DDL needed.')
    else:
        print('schema_migrations table : not found. Creating...')
        cur.execute("""
            CREATE TABLE schema_migrations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT UNIQUE NOT NULL,
                applied_at  DATETIME NOT NULL,
                checksum    TEXT,
                description TEXT
            )
        """)
        print('schema_migrations table : created OK.')

    # Step 2: record this bootstrap migration.
    cur.execute(
        "SELECT id, checksum FROM schema_migrations WHERE name = ?",
        (THIS_MIGRATION,)
    )
    existing = cur.fetchone()

    if existing:
        row_id, old_checksum = existing
        print('Registry record         : ' + THIS_MIGRATION + ' already present. Skipping insert.')
        if not old_checksum:
            cur.execute(
                "UPDATE schema_migrations SET checksum = ?, description = ? WHERE id = ?",
                (checksum, DESCRIPTION, row_id),
            )
            print('Registry record         : checksum was empty; updated checksum.')
        elif old_checksum != checksum:
            print('WARNING: registry checksum differs from current file checksum.')
            print('         Existing checksum was kept unchanged.')
    else:
        cur.execute(
            "INSERT INTO schema_migrations (name, applied_at, checksum, description) "
            "VALUES (?, ?, ?, ?)",
            (THIS_MIGRATION, datetime.utcnow().isoformat(), checksum, DESCRIPTION),
        )
        print('Registry record         : inserted row for ' + THIS_MIGRATION + '.')

    con.commit()

    # Step 3: print current registry contents.
    cur.execute("SELECT id, name, applied_at, checksum FROM schema_migrations ORDER BY id")
    rows = cur.fetchall()
    con.close()

    print()
    print('schema_migrations now contains ' + str(len(rows)) + ' row(s):')
    for row in rows:
        row_id, name, applied_at, row_checksum = row
        print('  id={0}  name={1}  applied_at={2}  checksum={3}'.format(
            row_id, name, applied_at, row_checksum or ''
        ))

    print()
    print('Migration complete.')
    print('Next step: TASK-OPS-002 -- backfill registry for historical migrations.')


if __name__ == '__main__':
    run()
