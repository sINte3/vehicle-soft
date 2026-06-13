# FIX002 Staging Validation Report

Date: 2026-06-13

## Summary

FIX002 hardens runtime SQLite access for the current three-process deployment:

1. Flask web service.
2. Telegram bot runtime workflows.
3. BOT003 notification outbox worker.

The patch enables SQLite WAL mode and applies a 30-second busy timeout to configured runtime connections.

No schema changes were made.

## Base State Before FIX002

Staging diagnostics before implementation:

- journal_mode = delete
- busy_timeout = 5000
- synchronous = 2 / FULL
- locking_mode = normal
- wal_autocheckpoint = 1000
- foreign_keys = 0
- SQLALCHEMY_ENGINE_OPTIONS = {}

## Files Changed

- sqlite_runtime.py
- config.py
- app.py
- bot003_notifications.py
- bot003_outbox_worker.py
- docs/FIX002_STAGING_VALIDATION_REPORT.md

## Implementation

sqlite_runtime.py:

- Added configure_sqlite_connection().
- Added enable_sqlite_wal().
- Added open_connection().
- Runtime configured connections use busy_timeout=30000, synchronous=NORMAL, and wal_autocheckpoint=1000.
- WAL initialization uses journal_mode=WAL.
- foreign_keys is not forced in FIX002.

config.py:

- Added SQLite SQLALCHEMY_ENGINE_OPTIONS for DevelopmentConfig and SqliteProductionConfig.
- connect_args timeout is 30 seconds.
- pool_size is 5.
- pool_pre_ping is enabled.
- PostgreSQL ProductionConfig was not changed.

app.py:

- Added startup WAL initialization for SQLite DB paths.
- Added SQLAlchemy DBAPI connect event listener for SQLite runtime PRAGMAs.

bot003_notifications.py:

- Replaced runtime sqlite3.connect(db_path) calls with sqlite_runtime.open_connection(db_path).

bot003_outbox_worker.py:

- Replaced runtime sqlite3.connect(db_path) with sqlite_runtime.open_connection(db_path).

## Validation Results

py_compile passed for:

- app.py
- config.py
- bot003_notifications.py
- bot003_outbox_worker.py
- sqlite_runtime.py

App import:

- APP IMPORT OK app

SQLALCHEMY_ENGINE_OPTIONS:

- connect_args timeout = 30
- pool_size = 5
- pool_pre_ping = True

Flask test client:

- / -> 302
- /report -> 302
- /fuel -> 308
- /fuel/ -> 302
- /fuel/receipts -> 302
- /spare-parts/ -> 302
- /spare-parts/new -> 302
- /wialon -> 302

SQLite PRAGMAs through configured helper connection:

- journal_mode = wal
- busy_timeout = 30000
- synchronous = 1
- locking_mode = normal
- wal_autocheckpoint = 1000
- foreign_keys = 0

Raw sqlite3 connection after WAL:

- journal_mode = wal
- busy_timeout = 5000
- synchronous = 2
- locking_mode = normal
- wal_autocheckpoint = 1000
- foreign_keys = 0

Note:

This is expected. WAL is persistent at DB level. busy_timeout and synchronous are per-connection settings. They are applied to configured runtime connections through SQLAlchemy event listener or sqlite_runtime.open_connection().

BOT003 dry-run:

- processed = 0
- sent = 0
- failed = 0
- skipped = 0
- error = null
- dry_run = true

BOT003 link and HTML escaping regression:

- PASS

Schema check:

- No schema changes were made.
- integrity_check = ok.

## Rollback Notes

Code rollback:

git checkout HEAD -- app.py config.py bot003_notifications.py bot003_outbox_worker.py
git rm sqlite_runtime.py

Then restart affected services.

WAL rollback is normally not required. If explicitly needed:

python -c "import sqlite3; con=sqlite3.connect(r'C:\transport-report-staging\instance\transport.db'); con.execute('PRAGMA journal_mode=DELETE'); con.close()"

## Production Rollout Notes

Production rollout must be backup-first:

1. Confirm production git tree is clean.
2. Back up production DB with sqlite3 online backup API.
3. Back up production source with git archive.
4. Pull commit with git pull --ff-only origin main.
5. Run py_compile with custom PYTHONPYCACHEPREFIX if needed.
6. Run app import check.
7. Run Flask test client checks.
8. Validate SQLite PRAGMAs through helper and SQLAlchemy DBAPI connection.
9. Restart TransportReport.
10. Restart TransportBot003.
11. Confirm TransportReport, TransportBot, and TransportBot003 are RUNNING.
12. Run BOT003 dry-run.
13. Confirm git status is clean.

## Risk Assessment

Risk level: low to medium.

Reasons:

- no schema changes
- no token handling changes
- no business workflow changes
- WAL is standard for concurrent SQLite readers and writer
- backup_transport_db.py already uses sqlite3 online backup API

Operational notes:

- WAL mode may create transport.db-wal and transport.db-shm while services are running.
- Raw file copy backup must not be used with WAL.
- sqlite3 online backup API remains the preferred backup method.
