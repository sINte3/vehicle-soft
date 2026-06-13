# FIX001 Staging Validation Report

## Summary

FIX001 fixes production-correctness issues confirmed after EXTAUDIT001:

1. BOT003 Telegram notification links were hardcoded to staging port 5051.
2. A few functional mojibake strings existed in app.py.
3. One corrupted idle_reason value exists in the database and needs a safe report/apply script.

This staging patch does not change schema and does not expose or modify Telegram tokens.

## Base Commit

Base before FIX001:

b14d5cc Document EXTAUDIT001 external audit report

## Files Changed

- app.py
- bot003_outbox_worker.py
- fix001_mojibake_idle_reason.py
- docs/FIX001_STAGING_VALIDATION_REPORT.md

## app.py Changes

Fixed only confirmed functional mojibake locations:

- login_manager.login_message changed to:
  Tizimga kiring

- copy_previous_day idle_reason changed to:
  Вақтинча бўш

- fresh-install AppModule seed names corrected:
  - transport: Транспорт ҳисоботи / Транспортный отчёт
  - wialon: Виалон / Виалон GPS
  - fuel: Ёқилғи модули / Модуль ГСМ
  - deficiencies: Камчиликлар / Недостатки
  - spare_parts: Эҳтиёт қисмлар / Запчасти

No business logic, auth, CSRF, permission, fuel, Wialon, spare-parts, or Telegram API behavior was changed in app.py.

## BOT003 Changes

bot003_outbox_worker.py:

- Removed hardcoded staging link:
  http://10.103.25.14:5051/spare-parts/{id}

- Added APP_PUBLIC_BASE_URL support:
  - default: http://10.103.25.14:5050
  - staging can override to: http://10.103.25.14:5051

- Added HTML escaping for payload-derived fields before sending parse_mode=HTML:
  - organization_name
  - equipment_name
  - created_by_name
  - status

No schema, retry, dedupe, locking, token, or Telegram sending logic was changed.

## Data-Fix Script

Created:

fix001_mojibake_idle_reason.py

Purpose:

- report or apply correction for exactly one known corrupted idle_reason value
- table: daily_records
- corrupted value:
  Р’Р°Т›С‚РёРЅС‡Р° Р±СћС€
- corrected value:
  Вақтинча бўш

Modes:

- default / --report: report only, no DB changes
- --apply: update exact matching rows only
- --db PATH: optional custom database path

The script is idempotent and does not change schema.

## Staging Validation Results

### py_compile

Command used with custom cache path because normal __pycache__ had ACL write restrictions:

PYTHONPYCACHEPREFIX=C:\transport-report-staging\.pycache_fix001

Result:

PASS

Files compiled:

- app.py
- bot003_outbox_worker.py
- fix001_mojibake_idle_reason.py

### App Import

Command:

python -c "from app import app; print('APP IMPORT OK', app.name)"

Result:

APP IMPORT OK app

### Flask Test Client

Results:

- / -> 302
- /report -> 302
- /fuel -> 308
- /fuel/ -> 302
- /fuel/receipts -> 302
- /spare-parts/ -> 302
- /spare-parts/new -> 302
- /wialon -> 302

PASS

### BOT003 Notification Text Test

Tested with:

- APP_PUBLIC_BASE_URL=http://10.103.25.14:5051
- APP_PUBLIC_BASE_URL=http://10.103.25.14:5050
- organization_name containing < and &
- equipment_name containing < and &
- created_by_name containing &

Result:

PASS

Confirmed:

- staging override link produced:
  http://10.103.25.14:5051/spare-parts/123

- production default/override link produced:
  http://10.103.25.14:5050/spare-parts/123

- HTML escaping worked:
  - Test&lt;Org&gt;&amp;Co
  - Mashina&lt;2&gt;&amp;A
  - John&amp;Doe

### Data-Fix Report Mode

Command:

python fix001_mojibake_idle_reason.py --report

Result on staging DB:

Rows found: 1

Affected row:

id=110393 work_date=2026-05-31 equipment_id=245

No DB changes were made.

## Staging Deployment Notes

Before restarting staging services, set TransportBot003Staging environment:

APP_PUBLIC_BASE_URL=http://10.103.25.14:5051

Then restart only the BOT003 staging worker if validating Telegram link generation through real outbox delivery.

TransportReportStaging restart is required only because app.py changed.

## Production Rollout Notes

Production rollout must be done only after commit and staging validation.

Production steps must include:

1. production DB backup
2. source backup or git rollback point
3. git pull --ff-only
4. py_compile with custom cache prefix if __pycache__ ACL blocks normal compilation
5. app import check
6. Flask test client checks
7. restart TransportReport
8. restart TransportBot003
9. verify new Telegram message link points to:
   http://10.103.25.14:5050/spare-parts/{id}
10. run fix001_mojibake_idle_reason.py --report
11. run fix001_mojibake_idle_reason.py --apply only after confirming expected row count

Production default APP_PUBLIC_BASE_URL is already:
http://10.103.25.14:5050

So production does not need an NSSM env var unless URL changes later.

## Rollback Notes

Code rollback:

git checkout <previous_commit> -- app.py bot003_outbox_worker.py fix001_mojibake_idle_reason.py

Then restart affected services:

- TransportReport
- TransportBot003

Data rollback:

The data-fix script changes only one exact idle_reason value.
If --apply was run and rollback is required, restore the production DB backup made before apply.

Preferred rollback for DB is backup restore, not reverse SQL.

## Risks

Low risk.

Reasons:

- no schema changes
- no token handling changes
- no Telegram API token exposure
- no role or permission logic changes
- no business workflow change
- data-fix script is exact-match and report-only by default

Main operational risk:

- staging must set APP_PUBLIC_BASE_URL=http://10.103.25.14:5051 for real staging Telegram link validation.
- production default points to 5050.
