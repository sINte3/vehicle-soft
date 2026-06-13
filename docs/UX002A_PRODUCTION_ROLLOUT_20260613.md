# UX002A Production Rollout

Date: 2026-06-13

## Summary

UX002A was deployed to production.

The release adds a shared UX design system baseline to `templates/base.html`.

## Commit

- `1d0488c Add shared UX design system baseline`

## Production environment

- Path: `C:\transport-report`
- URL: `http://10.103.25.14:5050`
- Web service: `TransportReport`
- Bot services checked but not restarted:
  - `TransportBot`
  - `TransportBot003`

## Backups

Completed before production pull:

- source backup:
  - `templates/base.html`
- database backup:
  - `D:\transport-report-backups\production\daily\transport_ux002a_before.db`

Database backup integrity:

- `PRAGMA integrity_check = ok`

## Changed files

- `templates/base.html`
- `docs/UX002A_STAGING_VALIDATION_20260613.md`

## Technical scope

Added shared CSS design system markers:

- `UX002A_DESIGN_SYSTEM_MARKER`
- `UX002A_DESIGN_SYSTEM_END`

Added common visual rules for:

- page headers
- cards
- dashboard cards
- statistics cards
- filter cards
- buttons
- forms
- tables
- badges
- flash/alert blocks
- responsive layout
- print layout

## Safety scope

No database schema changes.

No data migrations.

No route changes.

No business logic changes.

No Telegram bot changes.

No Wialon/fuel/spare-parts logic changes.

## Production validation

Passed:

- `py_compile app.py models.py config.py run_server.py bot003_outbox_worker.py`
- `APP_IMPORT_OK`
- base template marker checks:
  - `UX002A_DESIGN_SYSTEM_MARKER`
  - `UX002A_DESIGN_SYSTEM_END`
  - `--ux-primary`
  - `.page-header`
  - `.ref-filter-card`
  - `@media (max-width: 900px)`
- authenticated route checks returned `200`:
  - `/`
  - `/entry`
  - `/report`
  - `/fuel/`
  - `/fuel/receipts`
  - `/fuel/report`
  - `/fuel/warnings`
  - `/spare-parts/`
  - `/spare-parts/new`
  - `/wialon`
  - `/wialon/report`
  - `/wialon/workload`
  - `/ref/equipment`
  - `/ref/organizations`
  - `/ref/work_types`
  - `/ref/customers`
  - `/admin/users`
- rendered pages include `UX002A_DESIGN_SYSTEM_MARKER`
- unauthenticated route checks returned expected redirects to login
- `TransportReport` restarted successfully
- `TransportBot` remained running
- `TransportBot003` remained running
- `/login` returned `200`
- `/` returned `302` to login, expected for unauthenticated request
- BOT003 dry-run:
  - `processed=0`
  - `sent=0`
  - `failed=0`
  - `skipped=0`
  - `error=null`
  - `dry_run=true`

## Manual browser validation

Production browser validation confirmed by user.

Checked production pages:

- `/`
- `/report`
- `/entry`
- `/ref/equipment`
- `/fuel/report`

## Final production state

- `HEAD = origin/main = 1d0488c`
- `git status = clean`
- `TransportReport = RUNNING`
- `TransportBot = RUNNING`
- `TransportBot003 = RUNNING`

## Status

UX002A is complete on production.
