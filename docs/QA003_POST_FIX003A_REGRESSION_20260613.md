# QA003 Post-FIX003A Regression Audit

Date: 2026-06-13

## Summary

QA003 was performed after the following external-audit fixes:

- FIX001: BOT003 production URL and mojibake fixes.
- FIX002: SQLite WAL and runtime busy timeout hardening.
- FIX003A: spare part status history writes and tg_notifications enforcement.

Result: PASS WITH NOTES.

No rollback required.

## Production Git State

Production HEAD:

c8bef0a Document FIX003A production rollout

Production working tree:

clean

Recent commits:

- c8bef0a Document FIX003A production rollout
- 9a5603e Honor Telegram notification preference and write spare history
- 97c0748 Document FIX002 production rollout
- 0f7789c Enable SQLite WAL and busy timeout hardening
- 2c1051b Document FIX001 production rollout

## Production Services

All production services were running:

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Production Syntax Check

py_compile passed for:

- app.py
- config.py
- spare_parts.py
- bot003_notifications.py
- bot003_outbox_worker.py
- bot_api.py
- sqlite_runtime.py

## Production App Routes

Unauthenticated Flask test client:

- / -> 302
- /report -> 302
- /fuel -> 308
- /fuel/ -> 302
- /fuel/receipts -> 302
- /spare-parts/ -> 302
- /spare-parts/new -> 302
- /wialon -> 302

Authenticated admin render test:

- /fuel/receipts -> 200

## Production Database Health

Database integrity:

- INTEGRITY = ok

Raw sqlite3 connection PRAGMAs:

- journal_mode = wal
- busy_timeout = 5000
- synchronous = 2
- locking_mode = normal
- wal_autocheckpoint = 1000
- foreign_keys = 0

Note:

This raw sqlite3 result is expected. WAL is persistent at DB level. busy_timeout and synchronous are per-connection settings. Runtime configured connections are hardened through SQLAlchemy event listener and sqlite_runtime.open_connection().

Production table counts:

- spare_part_requests COUNT = 3
- spare_part_status_history COUNT = 0
- bot003_notification_outbox COUNT = 1
- bot_notification_queue COUNT = 0
- users COUNT = 7

BOT003 outbox grouped counts:

- spare_request_submitted / sent = 1

## Production BOT003

BOT003 dry-run:

- processed = 0
- sent = 0
- failed = 0
- skipped = 0
- error = null
- dry_run = true

## Production Logs Note

Production logs/error.log contains historical /fuel/receipts errors:

- TypeError: Object of type Undefined is not JSON serializable
- variable: L_add
- route: /fuel/receipts

Focused follow-up check confirmed the current issue is not active:

- production authenticated /fuel/receipts -> 200
- staging authenticated /fuel/receipts -> 200

Therefore this is treated as a historical log entry from before the current template fix, not an active regression.

## Staging Checks

Staging HEAD:

c8bef0a Document FIX003A production rollout

Staging working tree:

clean

Staging services:

- TransportReportStaging: RUNNING
- TransportBotStaging: RUNNING
- TransportBot003Staging: RUNNING

Staging unauthenticated Flask test client:

- / -> 302
- /report -> 302
- /fuel -> 308
- /fuel/ -> 302
- /fuel/receipts -> 302
- /spare-parts/ -> 302
- /spare-parts/new -> 302
- /wialon -> 302

Staging authenticated admin render test:

- /fuel/receipts -> 200

Staging BOT003 dry-run:

- processed = 0
- sent = 0
- failed = 0
- skipped = 0
- error = null
- dry_run = true

## Final Status

Production git status:

clean

Staging git status:

clean

## Result

QA003 PASS WITH NOTES.

Notes:

1. error.log contains historical /fuel/receipts errors, but current authenticated render is 200.
2. raw sqlite3 busy_timeout and synchronous remain default per raw connection, which is expected.
3. No active regression found after FIX001, FIX002, and FIX003A.
