# TASK-REF-001D Production Rollout

Date: 2026-06-13

## Summary

TASK-REF-001D was deployed to production.

The release adds read-only XLSX diagnostic exports for reference cleanup planning.

## Commit

- `34acb33 Add reference cleanup diagnostic exports`

## Production environment

- Path: `C:\transport-report`
- URL: `http://10.103.25.14:5050`
- Web service: `TransportReport`
- Bot services checked but not restarted:
  - `TransportBot`
  - `TransportBot003`

## Backups

Completed before production pull:

- source backups:
  - `app.py`
  - `templates/ref_work_types.html`
  - `templates/ref_customers.html`
- database backup:
  - `D:\transport-report-backups\production\daily\transport_task_ref_001d_before.db`

Database backup integrity:

- `PRAGMA integrity_check = ok`

## Added routes

- `/ref/work_types/export_diagnostics`
- `/ref/customers/export_diagnostics`

## Added UI

Added `Excel диагностика` buttons to:

- `/ref/work_types`
- `/ref/customers`

## Work types diagnostic export

Workbook sheets:

- `Summary`
- `Reference`
- `Duplicate names`
- `Missing from reference`
- `Quality issues`

Purpose:

- export all work types
- show usage by text from `daily_records.work_type`
- show duplicate names
- show missing work type values
- show empty default unit and zero default price

## Customers diagnostic export

Workbook sheets:

- `Summary`
- `Reference`
- `Missing from reference`
- `Similarity groups`
- `Pattern groups`

Purpose:

- export current customer reference table
- export all customer values from `daily_records.customer` missing from reference
- group similar customer strings
- flag common patterns: cluster, PTZ, service, department, numeric prefixes, very long or malformed rows

## Safety scope

No database schema changes.

No data migrations.

No data modification.

No automatic cleanup.

No automatic customer normalization.

No changes to:

- daily report business logic
- Wialon logic
- fuel logic
- spare-parts logic
- Telegram bot logic
- BOT003 notification logic

## Production validation

Passed:

- `py_compile app.py models.py config.py run_server.py bot003_outbox_worker.py`
- `APP_IMPORT_OK`
- source marker checks:
  - `TASK_REF_001D_MARKER`
  - `/ref/work_types/export_diagnostics`
  - `/ref/customers/export_diagnostics`
  - `export_work_types_diagnostics`
  - `export_customers_diagnostics`
- template marker checks:
  - `TASK_REF_001D_WORK_TYPES_EXPORT_BUTTON`
  - `TASK_REF_001D_CUSTOMERS_EXPORT_BUTTON`
- template load checks:
  - `ref_work_types.html`
  - `ref_customers.html`
  - `ref_equipment.html`
  - `ref_organizations.html`
- authenticated route checks:
  - `/ref/work_types`
  - `/ref/customers`
  - `/ref/equipment`
  - `/ref/organizations`
  - `/entry`
  - `/report`
  - `/`
- XLSX export route checks:
  - `/ref/work_types/export_diagnostics`
  - `/ref/customers/export_diagnostics`
- exported workbooks contain expected sheets.
- unauthenticated route checks returned expected redirects to login.
- `TransportReport` restarted successfully.
- `TransportBot` remained running.
- `TransportBot003` remained running.
- `/login` returned `200`.
- `/` returned `302` to login, expected for unauthenticated request.
- BOT003 dry-run:
  - `processed=0`
  - `sent=0`
  - `failed=0`
  - `skipped=0`
  - `error=null`
  - `dry_run=true`

## Manual browser validation

Production browser validation confirmed:

- `Excel диагностика` button is visible on work types page.
- `Excel диагностика` button is visible on customers page.
- Work types XLSX downloads successfully.
- Customers XLSX downloads successfully.
- Main reference pages remain visually valid.

## Final production state

- `HEAD = origin/main = 34acb33`
- `git status = clean`
- `TransportReport = RUNNING`
- `TransportBot = RUNNING`
- `TransportBot003 = RUNNING`

## Status

TASK-REF-001D is complete on production.
