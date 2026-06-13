# DASH002B Production Rollout Report

Date: 2026-06-13

## Summary

DASH002B was successfully rolled out to production.

Scope:

- Main dashboard route: `/`
- Dashboard template: `templates/index.html`
- Template-only change
- No database schema changes
- No data migrations
- Only `TransportReport` web service was restarted
- Bot services were not restarted

## Git state

Production was updated to:

- `d05b673` - Fix dashboard warning quick links placement

Related commits:

- `6d3fd4c` - Improve main dashboard drill-down links
- `d05b673` - Fix dashboard warning quick links placement

## Production backups

Created before rollout:

- Source backup:
  - `D:\transport-report-backups\production\source\index_before_dash002b_20260613_160341.html`

- DB backup:
  - `D:\transport-report-backups\production\daily\transport_dash002b_before_20260613_160341.db`

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
  - `dash-quick-links`
  - `dash-severity-banner`
  - warning `status=new` link
  - warning `severity=danger` link
  - `DASH002B_WARNING_LINKS_FIXED` marker

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

DASH002B production rollout completed successfully.

No rollback required.
