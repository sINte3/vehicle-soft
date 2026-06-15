# RELEASE FUEL-IDX-002 - Sargable fuel transaction date filters

Date: 2026-06-15

## Status

Completed on staging and production.

## Code commit

781a826eab6e7e662032b1da1d29a373912a24fd

Commit message:

Use sargable fuel transaction date filters

## Source changes

Changed file:

- fuel_routes.py

No DB schema changes were made in this task.

## Purpose

FUEL-IDX-002 completes the second part of the fuel transaction performance fix started by FUEL-IDX-001.

FUEL-IDX-001 added indexes to fuel_transactions2.

FUEL-IDX-002 removed the remaining non-sargable date filters that wrapped FuelTransaction2.txn_datetime in func.date(...).

## Issue confirmed during read-only audit

FUEL-IDX-002A found two problematic filters in fuel_routes.py:

- line 272
- line 1094

Old pattern:

    func.date(FuelTransaction2.txn_datetime) == date.today()

Problem:

- SQLite had to scan indexed values instead of using a direct range search.
- Query plan before patch:
  - SCAN fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_txn_datetime

## Implemented change

The old func.date(...) filter was replaced with explicit sargable day ranges:

    FuelTransaction2.txn_datetime >= datetime.combine(date.today(), datetime.min.time())
    FuelTransaction2.txn_datetime < datetime.combine(date.today() + timedelta(days=1), datetime.min.time())

This allows SQLite to use the index range search:

    SEARCH fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_txn_datetime

## Staging validation

FUEL-IDX-002A read-only audit confirmed:

- current date(txn_datetime) expression used scan over covering index;
- proposed explicit day range used ix_fuel_transactions2_txn_datetime;
- count comparison matched:
  - date_func_count = 30
  - range_count = 30
  - equal = True

FUEL-IDX-002B staging patch validation confirmed:

- changed file only:
  - fuel_routes.py
- old target no longer present:
  - TARGET_PRESENT=False
- AST scan found no remaining func.date(...) calls:
  - FUNC_DATE_CALL_LINES_AFTER_PATCH=[]
- count comparison matched:
  - old_date_func_count = 30
  - new_range_count = 30
  - equal = True
- new range filter uses:
  - SEARCH fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_txn_datetime
- authenticated direct render passed:
  - /fuel/
  - /fuel/stations
  - /fuel/transactions
  - /fuel/report
- unauthenticated smoke passed:
  - /login
  - /fuel/
  - /fuel/stations
  - /fuel/transactions
  - /fuel/report
- TransportReportStaging was restarted to load source patch.
- staging services final status:
  - TransportReportStaging: RUNNING
  - TransportBotStaging: RUNNING
  - TransportBot003Staging: RUNNING

## Production rollout

Production rollout completed successfully.

Production pull scope:

- fuel_routes.py only

Production backup before pull:

- D:\transport-report-backups\production\source\FUEL_IDX_002C_*\fuel_routes.py

Production actions:

- pulled source change;
- compiled fuel_routes.py;
- restarted only TransportReport;
- did not restart TransportBot;
- did not restart TransportBot003;
- no DB writes were performed.

Production validation confirmed:

- source scan:
  - TARGET_PRESENT=False
  - FUNC_DATE_CALL_LINES_AFTER_PATCH=[]
- app import OK;
- URL rules count: 86;
- count comparison matched:
  - old_date_func_count = 47
  - new_range_count = 47
  - equal = True
- query plan:
  - SEARCH fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_txn_datetime
- HTTP smoke passed:
  - /login
  - /fuel/
  - /fuel/stations
  - /fuel/transactions
  - /fuel/report

Final production services:

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

FUEL-IDX-002 is complete.

The remaining non-sargable func.date(FuelTransaction2.txn_datetime) filters were removed.

Together, FUEL-IDX-001 and FUEL-IDX-002 close the main EXTAUDIT002 fuel transaction date performance finding.
