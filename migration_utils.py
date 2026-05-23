# -*- coding: utf-8 -*-
"""
migration_utils.py -- TASK-OPS-001
Lightweight migration registry helpers for the TransportReport SQLite project.

These helpers operate directly on SQLite via the stdlib sqlite3 module.
No Flask app context required. No external dependencies.

Safety:
  - Never creates a new empty database silently.
  - Raises FileNotFoundError if instance/transport.db is missing.
  - Creates only schema_migrations table, no business tables.

Typical usage in a migration script:

    from migration_utils import (
        ensure_schema_migrations_table,
        is_migration_applied,
        record_migration,
        migration_checksum,
    )

    THIS_MIGRATION = 'migrate_XXX_my_change'

    ensure_schema_migrations_table()
    if is_migration_applied(THIS_MIGRATION):
        print('Already applied. Nothing to do.')
    else:
        # ... do the migration work ...
        record_migration(
            THIS_MIGRATION,
            description='What this migration did.',
            checksum=migration_checksum(__file__),
        )
        print('Done.')
"""

import hashlib
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

# [REASON]: CREATE TABLE IF NOT EXISTS is safe on every call — no state side-effects.
_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""


def _connect_existing_db():
    """Open the existing production SQLite database.

    sqlite3.connect(path) creates a new empty database when the file is missing.
    That is dangerous for migration scripts, so all helpers use this guard.
    """
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            'Database not found at {0}. Check that instance\\transport.db exists '
            'and run migrations from C:\\transport-report.'.format(DB_PATH)
        )
    return sqlite3.connect(DB_PATH)


def ensure_schema_migrations_table():
    """Create the schema_migrations table if it does not already exist."""
    con = _connect_existing_db()
    try:
        con.execute(_CREATE_TABLE_SQL)
        con.commit()
    finally:
        con.close()


def is_migration_applied(name):
    """Return True if name is already recorded in schema_migrations.

    Raises FileNotFoundError when the database file is missing. This prevents
    accidentally treating a missing database as "migration not applied".
    """
    con = _connect_existing_db()
    try:
        cur = con.cursor()
        # [REASON]: Table may not exist before migrate_000; check before querying.
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if cur.fetchone() is None:
            return False
        cur.execute("SELECT id FROM schema_migrations WHERE name = ?", (name,))
        return cur.fetchone() is not None
    finally:
        con.close()


def record_migration(name, description=None, checksum=None):
    """Insert a migration record. INSERT OR IGNORE makes this idempotent.

    Returns True if the row was newly inserted, False if it already existed.
    """
    ensure_schema_migrations_table()
    con = _connect_existing_db()
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations "
            "(name, applied_at, checksum, description) VALUES (?, ?, ?, ?)",
            (name, datetime.utcnow().isoformat(), checksum, description),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def migration_checksum(path):
    """Return the SHA-256 hex digest of a migration file.

    Store this alongside the migration record so reviewers can detect if an
    already-applied script was modified on disk after the fact.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()
