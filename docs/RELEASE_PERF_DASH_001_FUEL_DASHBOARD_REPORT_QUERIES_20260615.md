# RELEASE PERF-DASH-001 - Fuel dashboard/report query optimization

Date: 2026-06-15

## Status

Completed and deployed to production.

## Code commit

45049de8fd279f5352d89090b61b4716698f27ef

## Purpose

PERF-DASH-001 optimized repeated SQL queries on the fuel dashboard and fuel report pages.

The original read-only audit showed high SELECT counts and repeated query patterns on:

- /
- /fuel/
- /fuel/report

## Baseline audit result

PERF-DASH-001A V3 read-only staging audit confirmed:

- / had 101 SELECT statements;
- /fuel/ had 84 SELECT statements;
- /fuel/report had 94 SELECT statements;
- /spare-parts/ had 29 SELECT statements and remains a secondary future optimization candidate;
- /wialon/report/export performs audit log writes on GET and was excluded from this task.

No source files were modified during the audit.
No DB writes were performed.
No POST requests were executed.
Production was not touched.

## Implemented change

PERF-DASH-001B V3B changed only:

- fuel_routes.py

Main changes:

- added bulk fuel balance helpers;
- replaced repeated per-warehouse fuel balance queries with grouped bulk queries;
- optimized _collect_fuel_report_data warehouse loop;
- added warehouse-level aggregate maps for:
  - balances;
  - station counts;
  - today expenses;
  - latest transactions;
- added joinedload for transaction station access where needed;
- avoided selectinload on dynamic FuelWarehouse.stations relationship.

## Staging validation after patch

After patch, authenticated read-only SQL audit showed:

- / reduced from 101 SELECT to 28 SELECT;
- /fuel/ reduced from 84 SELECT to 11 SELECT;
- /fuel/report reduced from 94 SELECT to 19 SELECT;
- /report remained 3 SELECT;
- /spare-parts/ remained 29 SELECT;
- /wialon remained 3 SELECT;
- /wialon/report remained 3 SELECT.

Validation result:

- source scan OK;
- app import OK;
- URL rules count: 86;
- no traceback;
- no DB writes;
- no POST requests;
- staging post-restart smoke OK.

## Production rollout

PERF-DASH-001C deployed the code commit to production.

Production rollout details:

- production source backup created before pull;
- pull scope verified as source-only:
  - fuel_routes.py
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production post-restart smoke passed for:

- /login
- /
- /fuel/
- /fuel/report
- /fuel/transactions
- /report
- /spare-parts/
- /wialon

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Notes

The following items were intentionally not patched in PERF-DASH-001:

- /spare-parts/ repeated SELECT patterns;
- /wialon/report/export GET audit log write behavior.

Recommended future task candidates:

- PERF-SPARE-001: optimize spare parts index repeated relationship queries.
- AUDIT-GET-SIDE-EFFECT-001 or WIALON-EXPORT-AUDIT-001: review GET export audit log write behavior.
