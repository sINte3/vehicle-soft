# FIX001 Production Rollout Report

Date: 2026-06-13

## Summary

FIX001 was successfully rolled out to production.

The release fixed:

1. BOT003 Telegram notification links pointing to staging port 5051.
2. Confirmed functional mojibake strings in app.py.
3. One corrupted idle_reason value in production database.

## Production Commit

573e576 Fix BOT003 public URL and mojibake strings

## Production Backups

Code rollout backup:

- DB backup before code pull:
  D:\transport-report-backups\production\daily\transport_fix001_before_20260613_131139.db

- Source backup before code pull:
  D:\transport-report-backups\production\source\transport_prod_source_before_fix001_20260613_131139.zip

Data apply backup:

- DB backup before data apply:
  D:\transport-report-backups\production\daily\transport_fix001_before_data_apply_20260613_131320.db

## Validation Before Restart

py_compile:

PASS

Files checked:

- app.py
- bot003_outbox_worker.py
- fix001_mojibake_idle_reason.py

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

BOT003 production link test:

PASS

Confirmed production link:

http://10.103.25.14:5050/spare-parts/999

Confirmed staging port 5051 is not used in production notification link generation.

## Data Fix

Report before apply:

Rows found: 1

Affected row:

- id=110393
- work_date=2026-05-31
- equipment_id=245

Apply result:

- Rows before: 1
- Rows updated: 1
- Rows remaining: 0

Verification after apply:

- CORRUPTED_ROWS_AFTER = 0
- ROW_110393_CORRECTED = 1

## Production Services After Rollout

All services are running:

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

BOT003 production dry-run:

- processed: 0
- sent: 0
- failed: 0
- skipped: 0
- error: null

## Git Status

Production working tree after rollout:

clean

Production HEAD:

573e576 Fix BOT003 public URL and mojibake strings

## Result

FIX001 production rollout completed successfully.

No rollback required.
