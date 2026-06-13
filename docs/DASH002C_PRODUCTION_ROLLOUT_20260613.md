# DASH002C Production Rollout Report

Date: 2026-06-13

## Summary

DASH002C was successfully rolled out to production.

Scope:

- Main dashboard route: `/`
- Dashboard template: `templates/index.html`
- UI polish only
- No database schema changes
- No data migrations
- No route changes
- No business logic changes
- Only `TransportReport` web service was restarted
- Bot services were not restarted

## Git state

Production was updated to:

- `db191cd` - Polish dashboard legacy report separation

Related commits:

- `db191cd` - Polish dashboard legacy report separation

Previous dashboard stage:

- `99d3d5b` - Update project state after DASH002B rollout

## Production backups

Created before rollout:

- Source backup:
  - `D:\transport-report-backups\production\source\index_before_dash002c_20260613_162522.html`

- DB backup:
  - `D:\transport-report-backups\production\daily\transport_dash002c_before_20260613_162522.db`

DB backup validation:

- `BACKUP_INTEGRITY = ok`

## Production validation

Validated before restart:

- `py_compile` passed for:
  - `app.py`
  - `fuel_routes.py`
  - `spare_parts.py`
  - `wialon_import.py`
  - `models.py`
  - `config.py`
  - `sqlite_runtime.py`
  - `bot003_notifications.py`
  - `bot003_outbox_worker.py`

- App import:
  - `APP_IMPORT_OK`

- Template:
  - `TEMPLATE_INDEX_LOAD_OK`

- Authenticated dashboard render:
  - `AUTH_GET_/ = 200`

- Rendered page contains:
  - main panel header
  - management dashboard
  - daily report/data-entry section
  - legacy section marker
  - legacy filter card
  - date mode tabs
  - dashboard quick links
  - warning severity banner

Unauthenticated route checks:

- `/` -> 302 `/login?next=%2F`
- `/report` -> 302 `/login?next=%2Freport`
- `/fuel/` -> 302 `/login?next=%2Ffuel%2F`
- `/fuel/warnings` -> 302 `/login?next=%2Ffuel%2Fwarnings`
- `/spare-parts/` -> 302 `/login?next=%2Fspare-parts%2F`
- `/wialon` -> 302 `/login?next=%2Fwialon`

These redirects are expected for unauthenticated access.

## Service restart

Restarted:

- `TransportReport`

Final state:

- `TransportReport`: RUNNING

Not restarted:

- `TransportBot`
- `TransportBot003`

Final state:

- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

## HTTP check

Production HTTP check:

- `/login STATUS=200`
- `/ STATUS=302`

`/ STATUS=302` is expected because anonymous users are redirected to login.

## BOT003 check

BOT003 dry-run result:

- processed: 0
- sent: 0
- failed: 0
- skipped: 0
- error: null
- dry_run: true

## Result

DASH002C production rollout completed successfully.

No rollback required.
