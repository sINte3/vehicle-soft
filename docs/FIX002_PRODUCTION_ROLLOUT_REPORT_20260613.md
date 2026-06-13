# FIX002 Production Rollout Report

Date: 2026-06-13

## Summary

FIX002 was successfully rolled out to production.

The release hardened runtime SQLite access for the current three-process deployment:

1. Flask web service.
2. Telegram bot runtime workflows.
3. BOT003 notification outbox worker.

The patch enables SQLite WAL mode and applies a 30-second busy timeout to configured runtime connections.

No schema changes were made.

## Production Commit

0f7789c Enable SQLite WAL and busy timeout hardening

## Production Backups

DB backup before code pull:

D:\transport-report-backups\production\daily\transport_fix002_before_20260613_142147.db

Source backup before code pull:

D:\transport-report-backups\production\source\transport_prod_source_before_fix002_20260613_142147.zip

## Production Base State Before FIX002

Before rollout:

- journal_mode = delete
- busy_timeout = 5000
- synchronous = 2
- locking_mode = normal
- wal_autocheckpoint = 1000
- foreign_keys = 0

## Validation Results

py_compile:

PASS

Files checked:

- app.py
- config.py
- bot003_notifications.py
- bot003_outbox_worker.py
- sqlite_runtime.py

App import:

APP IMPORT OK app

Flask test client:

- / -> 302
- /report -> 302
- /fuel -> 308
- /fuel/ -> 302
- /fuel/receipts -> 302
- /spare-parts/ -> 302
- /spare-parts/new -> 302
- /wialon -> 302

SQLAlchemy DBAPI connection after app import:

- journal_mode = wal
- busy_timeout = 30000
- synchronous = 1
- locking_mode = normal
- wal_autocheckpoint = 1000
- foreign_keys = 0

Helper connection after app import:

- ENABLE_WAL = True
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

Schema and integrity check:

- TABLE_COUNT = 37
- HAS_SCHEMA_MIGRATIONS = True
- INTEGRITY = ok

BOT003 production dry-run after restart:

- processed = 0
- sent = 0
- failed = 0
- skipped = 0
- error = null
- dry_run = true

## Production Services After Rollout

All services are running:

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Git Status

Production working tree after rollout:

clean

Production HEAD:

0f7789c Enable SQLite WAL and busy timeout hardening

## Result

FIX002 production rollout completed successfully.

No rollback required.
