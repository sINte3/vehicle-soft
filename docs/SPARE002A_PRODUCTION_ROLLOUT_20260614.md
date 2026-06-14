# SPARE002A Production Rollout

Date: 2026-06-14

## Summary

SPARE002A was deployed to production.

The release improves the UX of the spare parts module pages:

- `/spare-parts/`
- `/spare-parts/new`

A follow-up correction fixed the top action buttons layout on `/spare-parts/`.

## Commits

- `7e8ac60 Improve spare parts UX`
- `b76cede Fix SPARE002A staging doc markers`
- `6d391ab Fix spare parts header actions`

## Production environment

- Path: `C:\transport-report`
- URL: `http://10.103.25.14:5050`
- Web service: `TransportReport`
- Bot services checked but not restarted:
  - `TransportBot`
  - `TransportBot003`

## Backups

Completed before production rollout:

- source backup:
  - `templates/spare_parts_list.html`
  - `templates/spare_part_form.html`
- database backup:
  - `D:\transport-report-backups\production\daily\transport_spare002a_before.db`
- additional source backup before button correction:
  - `templates/spare_parts_list.html`
- additional database backup before button correction:
  - `D:\transport-report-backups\production\daily\transport_spare002a_buttons_fix_before.db`

Database backup integrity:

- `PRAGMA integrity_check = ok`

## Changed files

- `templates/spare_parts_list.html`
- `templates/spare_part_form.html`
- `docs/SPARE002A_STAGING_VALIDATION_20260614.md`

## Technical scope

Template-only UX update with non-blocking visual JavaScript hints on the new request form.

Added markers:

- `SPARE002A_MARKER`
- `SPARE002A_END`
- `SPARE002A_JS_MARKER`
- `SPARE002A_BUTTONS_FIX_V2_MARKER`
- `SPARE002A_BUTTONS_FIX_V2_END`

Added or improved:

- spare parts list page header
- list status/context summary pills
- list guidance panel
- list filter form layout
- list table visual density
- new request page header
- new request context summary pills
- new request guidance panel
- new request form grouping
- new request table styling
- sticky action row styling
- non-blocking visual hints for incomplete item rows
- top action buttons restored into one horizontal header row:
  - `New request`
  - `Catalog`

## Safety scope

No database schema changes.

No data migrations.

No route changes.

No `spare_parts.py` changes.

No spare_parts.py changes.

No `save_request` changes.

No save_request changes.

No `submit_request` changes.

No submit_request changes.

No `approve_request` changes.

No approve_request changes.

No `reject_request` changes.

No reject_request changes.

No Telegram bot changes.

No BOT003 outbox logic changes.

No Wialon/fuel/report logic changes.

## Production validation

Passed:

- `py_compile app.py models.py config.py run_server.py bot003_outbox_worker.py spare_parts.py`
- `APP_IMPORT_OK`
- `TEMPLATE_LOAD_OK spare_parts_list.html`
- `TEMPLATE_LOAD_OK spare_part_form.html`
- source marker checks:
  - `SPARE002A_MARKER`
  - `SPARE002A_END`
  - `SPARE002A_JS_MARKER`
  - `SPARE002A_BUTTONS_FIX_V2_MARKER`
  - `SPARE002A_BUTTONS_FIX_V2_END`
  - `spare002a-header-actions`
- authenticated route checks returned `200`:
  - `/spare-parts/`
  - `/spare-parts/?status=submitted`
  - `/spare-parts/new`
  - `/spare-parts/catalog`
  - `/spare-parts/<latest_id>`
  - `/`
  - `/report`
  - `/entry`
  - `/fuel/receipts`
- rendered list pages include:
  - `SPARE002A_MARKER`
  - `SPARE002A_BUTTONS_FIX_V2_MARKER`
  - `spare002a-guidance-panel`
  - `spare002a-filter-form`
  - `spare002a-table`
  - `spare002a-header-actions`
  - `/spare-parts/new`
  - `/spare-parts/catalog`
- rendered new request page includes:
  - `SPARE002A_MARKER`
  - `SPARE002A_JS_MARKER`
  - `spare002a-guidance-panel`
  - `spare002a-request-form`
  - `spare002a-table`
- unauthenticated route checks returned expected redirects to login
- `TransportReport` restarted successfully
- `TransportBot` remained running
- `TransportBot003` remained running
- `/login` returned `200`
- `/spare-parts/` returned `302` to login, expected for unauthenticated request
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

- `/spare-parts/`
- `/spare-parts/new`

Validated:

- spare parts list page
- filters
- table layout
- new request page
- new request fields
- draft/save/submit buttons
- top action buttons after correction:
  - `New request`
  - `Catalog`

## Final production state

- `HEAD = origin/main = 6d391ab`
- `git status = clean`
- `TransportReport = RUNNING`
- `TransportBot = RUNNING`
- `TransportBot003 = RUNNING`

## Status

SPARE002A is complete on production.
