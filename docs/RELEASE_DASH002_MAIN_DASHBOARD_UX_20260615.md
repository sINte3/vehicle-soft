# RELEASE DASH002  Main dashboard UX

Date: 2026-06-15

## Status

Completed and deployed to production.

## Commit

`f2d73a9976e43346e9164d22ca33def90ba9d277`  `Improve main dashboard UX`

## Scope

DASH002 improved the main dashboard `/` UX.

Changed file:

- `templates/index.html`

No changes were made to:

- database schema
- migrations
- `app.py` route logic
- business calculations
- Topaz sync
- BOT003
- Telegram bot services
- production data

## What changed

- Reworked the visual structure of the main dashboard cards.
- Added marker `DASH002_MAIN_DASHBOARD_UX`.
- Kept the existing `/` route and all existing filters.
- Kept the legacy daily report section below the dashboard.
- Moved quick links out of metric blocks into separate card footer areas.
- Improved layout for:
  - transport work KPI
  - fuel KPI
  - fuel warnings
  - spare parts
  - Wialon mapping
  - system status
- Preserved existing links to:
  - `/report`
  - `/entry`
  - `/fuel/`
  - `/fuel/report`
  - `/fuel/warnings`
  - `/fuel/transactions`
  - `/spare-parts/`
  - `/wialon`

## Staging validation

Completed before commit:

- `py_compile` passed
- app import passed
- `templates/index.html` loaded successfully
- direct authenticated render of `/` passed
- route checks returned expected login redirects for unauthenticated access
- `TransportReportStaging` restarted and was RUNNING
- visual browser QA passed on `http://10.103.25.14:5051/`

## Production rollout

Completed after commit/push:

- production git status was clean before deploy
- source backup created
- production DB backup created
- `git pull --ff-only origin main` completed
- production HEAD matched expected commit
- `py_compile` passed
- app import passed
- `templates/index.html` loaded successfully
- direct authenticated render of `/` passed
- route checks returned expected login redirects for unauthenticated access
- only `TransportReport` was restarted
- `TransportBot` and `TransportBot003` remained RUNNING
- HTTP `/` returned expected `302` to login
- visual browser QA passed on `http://10.103.25.14:5050/`

## Production backups

Source backup:

`D:\transport-report-backups\production\source\DASH002_MAIN_DASHBOARD_UX_20260615_125933`

DB backup:

`D:\transport-report-backups\production\daily\transport_dash002_main_dashboard_ux_20260615_125933.db`

## Final result

DASH002 is complete. Staging, production, and origin/main were synchronized at the DASH002 code commit before this docs-only update.
