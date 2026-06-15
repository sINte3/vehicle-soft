# RELEASE CLEAN-TPL-001 - Legacy fuel template cleanup

Date: 2026-06-15

## Status

Completed on staging and production.

## Code commit

3bb4385472c54fefb19656608df33e3398085bfd

Commit message:

Remove orphaned legacy fuel templates

## Source changes

Deleted files:

- templates/fuel_balance.html
- templates/fuel_dashboard.html
- templates/fuel_history.html
- templates/fuel_receipts.html
- templates/fuel_sync_log.html

No DB schema changes were made.

No application code was changed.

## Purpose

CLEAN-TPL-001 removes old root-level fuel templates from the pre-v2 fuel module.

The active fuel UI now uses templates under templates/fuel/.

The deleted root-level templates were legacy files and were not referenced by current Flask render_template calls.

## Read-only audit

CLEAN-TPL-001A confirmed:

- legacy root-level templates existed;
- active fuel templates under templates/fuel/ existed;
- current render_template references point to templates/fuel/*.html;
- current route map uses the active fuel blueprint routes;
- unauthenticated HTTP smoke passed for key fuel pages;
- no source files were modified;
- no DB writes were performed;
- production was not touched.

Legacy templates found before cleanup:

- templates/fuel_dashboard.html
- templates/fuel_balance.html
- templates/fuel_history.html
- templates/fuel_receipts.html
- templates/fuel_sync_log.html

Active templates confirmed:

- templates/fuel/dashboard.html
- templates/fuel/initial_balance.html
- templates/fuel/receipts.html
- templates/fuel/report.html
- templates/fuel/stations.html
- templates/fuel/transactions.html
- templates/fuel/warehouses.html
- templates/fuel/warnings.html

## Staging cleanup

CLEAN-TPL-001B removed only the five confirmed orphaned legacy templates.

Staging backup before delete:

- D:\transport-report-backups\staging\source\CLEAN_TPL_001B_*

Staging validation confirmed:

- all five legacy files removed;
- all active fuel templates still exist;
- no Python render_template references to deleted templates;
- app import OK;
- URL rules count: 86;
- authenticated render passed:
  - /fuel/
  - /fuel/warehouses
  - /fuel/initial-balance
  - /fuel/receipts
  - /fuel/transactions
  - /fuel/stations
  - /fuel/report
  - /fuel/warnings
- unauthenticated smoke passed:
  - /login
  - /fuel/
  - /fuel/receipts
  - /fuel/transactions
  - /fuel/warehouses
  - /fuel/report
  - /fuel/stations
  - /fuel/initial-balance
  - /fuel/warnings
- no DB writes were performed;
- no POST requests were executed;
- no service restart was performed;
- production was not touched.

## Production rollout

CLEAN-TPL-001C committed and rolled out the source deletions to production.

Production pull scope:

- templates/fuel_balance.html
- templates/fuel_dashboard.html
- templates/fuel_history.html
- templates/fuel_receipts.html
- templates/fuel_sync_log.html

Production backup before pull:

- D:\transport-report-backups\production\source\CLEAN_TPL_001C_*\

Production validation confirmed:

- all five legacy files removed from production;
- all active templates/fuel/*.html files still exist;
- app import OK;
- URL rules count: 86;
- authenticated render passed:
  - /fuel/
  - /fuel/warehouses
  - /fuel/initial-balance
  - /fuel/receipts
  - /fuel/transactions
  - /fuel/stations
  - /fuel/report
  - /fuel/warnings
- unauthenticated smoke passed:
  - /login
  - /fuel/
  - /fuel/receipts
  - /fuel/transactions
  - /fuel/warehouses
  - /fuel/report
  - /fuel/stations
  - /fuel/initial-balance
  - /fuel/warnings
- no DB writes were performed;
- no POST requests were executed;
- no service restart was performed.

Final production services:

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

CLEAN-TPL-001 is complete.

The confirmed orphaned root-level fuel templates were removed from staging and production.

The active fuel UI remains on templates/fuel/*.html.
