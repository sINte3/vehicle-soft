# FIX003A Production Rollout Report

Date: 2026-06-13

## Summary

FIX003A was successfully rolled out to production.

The release fixes two dormant features from EXTAUDIT001:

1. spare_part_status_history is now written during spare part status transitions.
2. users.tg_notifications is now respected by BOT003 recipient selection.

No schema changes were made.
No legacy tables were removed.
No return-for-revision workflow was implemented.

## Production Commit

9a5603e Honor Telegram notification preference and write spare history

## Production Backups

DB backup before code pull:

D:\transport-report-backups\production\daily\transport_fix003a_before_20260613_144443.db

Source backup before code pull:

D:\transport-report-backups\production\source\transport_prod_source_before_fix003a_20260613_144443.zip

## Files Changed

- spare_parts.py
- bot003_notifications.py
- docs/FIX003A_STAGING_VALIDATION_REPORT.md

## Implementation Summary

spare_parts.py:

- Imported SparePartStatusHistory.
- Added _add_status_history().
- Writes history before commit for:
  - new request created directly as submitted
  - draft -> submitted
  - submitted -> approved
  - submitted -> rejected
- No historical backfill was done.

bot003_notifications.py:

- _get_admin_telegram_ids now requires tg_notifications = 1.
- _get_user_telegram_id now requires tg_notifications = 1.
- BOT003 best-effort behavior remains unchanged.

## Explicit Non-Changes

- No schema changes.
- No migration scripts.
- No backfill of old history rows.
- No changes to legacy bot_notification_queue.
- No return-for-revision route.
- No spare_request_revision_requested producer.
- No new statuses.
- No token or secret handling changes.

## Production Validation Results

py_compile passed for:

- spare_parts.py
- bot003_notifications.py
- app.py
- bot_api.py

App import:

- APP IMPORT OK app

Flask test client:

- / -> 302
- /report -> 302
- /fuel -> 308
- /fuel/ -> 302
- /fuel/receipts -> 302
- /spare-parts/ -> 302
- /spare-parts/new -> 302
- /wialon -> 302

Database read check:

- INTEGRITY = ok
- spare_part_requests COUNT = 3
- spare_part_status_history COUNT = 0
- bot003_notification_outbox COUNT = 1
- bot_notification_queue COUNT = 0
- users COUNT = 7
- HAS_TG_NOTIFICATIONS = True

Rollback functional test for _add_status_history:

- HISTORY_COUNT_BEFORE = 0
- HISTORY_COUNT_DURING = 1
- HISTORY_COUNT_AFTER_ROLLBACK = 0

Rollback functional test for tg_notifications:

- ORIGINAL_TG_NOTIFICATIONS = 1
- ADMINS_WHEN_OFF = []
- USER_WHEN_OFF = None
- ADMINS_WHEN_ON = linked admin
- USER_WHEN_ON = linked admin

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

9a5603e Honor Telegram notification preference and write spare history

## Result

FIX003A production rollout completed successfully.

No rollback required.
