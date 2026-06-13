# TASK-REF-001B Production Rollout

Date: 2026-06-13

## Summary

TASK-REF-001B was deployed to production.

The release improves reference pages for:

- organizations
- work types
- customers

## Commit

- `be30d1d Improve reference pages filters and diagnostics`

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
  - `templates/ref_organizations.html`
  - `templates/ref_work_types.html`
  - `templates/ref_customers.html`
- database backup:
  - `D:\transport-report-backups\production\daily\transport_task_ref_001b_before.db`

Database backup integrity:

- `PRAGMA integrity_check = ok`

## Implemented

### Organizations

- Search by organization name and short name.
- Statistics cards.
- Visible short name field in create/edit forms.
- Linked-record count display.
- Existing delete blocking preserved.

### Work types

- Search by name/default unit.
- Usage filter: all / used / unused.
- Statistics cards.
- Diagnostics for:
  - empty default unit
  - zero default price
  - duplicate names
  - values used in daily reports but missing from reference table
- Usage count column.
- Existing edit/delete-block behavior preserved.

### Customers

- Search by customer name.
- Customer type filter: all / internal / external.
- Usage filter: all / used / unused.
- Statistics cards.
- Diagnostics for values used in daily reports but missing from reference table.
- Usage count column.
- Existing edit/delete-block behavior preserved.

## Safety scope

No database schema changes.

No data migrations.

No automatic duplicate cleanup.

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
  - `TASK_REF_001B_MARKER`
  - `TASK_REF_001B_ORG_TEMPLATE_MARKER`
  - `TASK_REF_001B_WORK_TYPES_TEMPLATE_MARKER`
  - `TASK_REF_001B_CUSTOMERS_TEMPLATE_MARKER`
- template load checks:
  - `ref_organizations.html`
  - `ref_work_types.html`
  - `ref_customers.html`
  - `ref_equipment.html`
- authenticated route checks:
  - `/ref/organizations`
  - `/ref/organizations?q=ПСТ`
  - `/ref/work_types`
  - `/ref/work_types?q=Пахта`
  - `/ref/work_types?usage=used`
  - `/ref/work_types?usage=unused`
  - `/ref/customers`
  - `/ref/customers?q=Кластер`
  - `/ref/customers?customer_type=internal`
  - `/ref/customers?customer_type=external`
  - `/ref/customers?usage=used`
  - `/ref/customers?usage=unused`
  - `/ref/equipment`
  - `/entry`
  - `/report`
  - `/`
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

Production browser screenshots confirmed:

- Organizations page displays statistics/search/short name/links.
- Work types page displays statistics/search/usage filter/diagnostics.
- Customers page displays statistics/search/type filter/usage filter/diagnostics with scroll.

## Final production state

- `HEAD = origin/main = be30d1d`
- `git status = clean`
- `TransportReport = RUNNING`
- `TransportBot = RUNNING`
- `TransportBot003 = RUNNING`

## Status

TASK-REF-001B is complete on production.
