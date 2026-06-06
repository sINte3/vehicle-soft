# DASH001 — Management dashboard for main page

Date: 2026-06-06  
Status: COMPLETED  
Production: http://10.103.25.14:5050  
Database migration: not required  
Production backup before deployment: `D:\transport-report-backups\production\daily\transport_20260606_093202.db`  
Production smoke test: passed  
Commit message: `Add management dashboard`

## Summary

DASH001 adds a management dashboard to the main page. The dashboard gives a fast executive overview of transport work, fuel operations, Fuel warnings, spare part requests, Wialon mapping, recent audit activity, Topaz sync status, and latest production backup status.

## Changed files

- `app.py`
- `templates/index.html`

## Database changes

No database migration was required.

## Functional scope

- Added dashboard context builder for the main page.
- Added KPI cards for transport work:
  - total records;
  - working rows;
  - idle/no-work rows;
  - total work quantity;
  - total work amount.
- Added Fuel KPI cards:
  - issued fuel quantity;
  - Topaz transaction count;
  - latest Topaz sync time;
  - open and critical Fuel warnings.
- Added spare part request KPI cards.
- Added Wialon mapping KPI cards.
- Added system status block:
  - latest backup;
  - backup age;
  - latest audit events;
  - Topaz sync age.
- Added quick links to key operational sections.
- Preserved the existing daily work report below the dashboard.
- Preserved existing filters on the main page.

## Verification

Production checks completed:

- production DB backup completed successfully;
- code compiled successfully;
- `APP IMPORT OK`;
- `/` returned HTTP 200 via test client;
- dashboard marker found via test client;
- Windows service `TransportReport` restarted and is running;
- browser smoke test passed.

## Notes

This release does not change database schema. Rollback can be performed by restoring the previous `app.py` and `templates/index.html` files from the pre-update backup.
