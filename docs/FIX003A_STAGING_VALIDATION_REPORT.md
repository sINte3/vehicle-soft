# FIX003A Staging Validation Report

Date: 2026-06-13

## Summary

FIX003A fixes two dormant features from EXTAUDIT001:

1. spare_part_status_history is now written during spare part status transitions.
2. users.tg_notifications is now respected by BOT003 recipient selection.

No schema changes were made.
No legacy tables were removed.
No return-for-revision workflow was implemented.

## Files changed

- spare_parts.py
- bot003_notifications.py
- docs/FIX003A_STAGING_VALIDATION_REPORT.md

## Implementation

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

## Explicit non-changes

- No schema changes.
- No migration scripts.
- No backfill of old history rows.
- No changes to legacy bot_notification_queue.
- No return-for-revision route.
- No spare_request_revision_requested producer.
- No new statuses.
- No token or secret handling changes.

## Staging validation results

Encoding check:

- spare_parts.py BAD_MARKERS = []
- bot003_notifications.py BAD_MARKERS = []
- docs/FIX003A_STAGING_VALIDATION_REPORT.md BAD_MARKERS = []

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

DB read check:

- INTEGRITY = ok
- spare_part_requests COUNT = 8
- spare_part_status_history COUNT = 0
- bot003_notification_outbox COUNT = 2
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

BOT003 dry-run:

- processed = 0
- sent = 0
- failed = 0
- skipped = 0
- error = null
- dry_run = true

## Rollback notes

Code rollback:

git checkout HEAD~1 -- spare_parts.py bot003_notifications.py
git rm docs/FIX003A_STAGING_VALIDATION_REPORT.md

Then restart affected services.

Database rollback is not required because there are no schema changes.

## Production rollout notes

Use standard backup-first rollout:

1. Confirm production git tree is clean.
2. Back up production DB with sqlite3 online backup API.
3. Back up production source with git archive.
4. Pull commit with git pull --ff-only origin main.
5. Run py_compile.
6. Run app import check.
7. Run Flask route checks.
8. Run DB read checks.
9. Restart TransportReport.
10. Restart TransportBot003.
11. Confirm TransportReport, TransportBot, and TransportBot003 are RUNNING.
12. Run BOT003 dry-run.
13. Confirm git status is clean.

## Result

FIX003A is validated on staging and ready for commit and production rollout.
