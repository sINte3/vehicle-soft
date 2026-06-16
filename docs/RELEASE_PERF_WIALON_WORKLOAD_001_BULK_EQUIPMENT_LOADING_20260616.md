# RELEASE PERF-WIALON-WORKLOAD-001 - Wialon workload bulk equipment loading

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

9c16198af3ccf75fdc1ec4cb0ee50cff19cd1b9d

## Purpose

PERF-WIALON-WORKLOAD-001 optimized Wialon workload report SQL behavior by removing repeated per-organization equipment queries.

Target routes:

- GET /wialon/workload
- GET /wialon/workload/export

## Baseline diagnostic

PERF-WIALON-AUTOMATCH-001A read-only diagnostic showed that `/wialon/auto_match` itself was not a performance problem.

The real SQL issue was found in workload routes:

- `/wialon/workload`:
  - response UTF-8 bytes: 230,785
  - SQL count: 21 SELECT
  - repeated SQL count: 2
  - repeated equipment query count: 17
  - DML count: 0
  - no traceback

- `/wialon/workload/export`:
  - direct passthrough XLSX response
  - SQL count: 20 SELECT
  - repeated SQL count: 1
  - repeated equipment query count: 17
  - DML count: 0
  - no traceback

Root cause:

- `get_workload_data()` loaded organizations and then queried active equipment separately for each organization.
- With 17 organizations this produced 17 repeated equipment SELECT queries.
- `wialon_workload()` also already had preloaded user organizations, but `get_workload_data()` queried organizations again.

## Implemented change

PERF-WIALON-WORKLOAD-001B changed:

- workload_report.py
- wialon_import.py

Main changes:

- updated `get_workload_data()` signature:
  - `get_workload_data(d_from, d_to, org_ids=None, preloaded_orgs=None)`
- allowed reuse of already loaded organizations via `preloaded_orgs`;
- replaced per-organization equipment query loop with one bulk equipment query;
- grouped active equipment by `organization_id` in memory;
- preserved existing mapped-equipment filtering behavior;
- preserved fallback behavior for equipment with engine-hour records;
- updated `/wialon/workload` call to pass `preloaded_orgs=user_orgs`;
- kept `/wialon/workload/export` compatible;
- did not change DB schema;
- did not run migrations;
- did not change templates.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- source checks passed;
- no DB writes;
- no POST requests;
- DML blocked by audit hook.

Route results after patch:

- `/wialon/workload`:
  - response UTF-8 bytes: 230,785
  - SQL count: 4 SELECT
  - repeated SQL count: 0
  - repeated equipment SQL: 0
  - DML count: 0
  - no traceback

- `/wialon/workload/export`:
  - direct passthrough XLSX response
  - SQL count: 4 SELECT
  - repeated SQL count: 0
  - repeated equipment SQL: 0
  - DML count: 0
  - no traceback

Regression sample:

- /wialon/auto_match: 3 SELECT, repeated SQL 0
- /wialon/mapping: 3 SELECT, repeated SQL 0
- /wialon: 3 SELECT, repeated SQL 0
- /wialon/report: 3 SELECT, repeated SQL 0
- /ref/equipment: 8 SELECT, repeated SQL 0

Staging post-restart smoke passed.

## Production rollout

PERF-WIALON-WORKLOAD-001C deployed the code commit to production.

Production rollout details:

- production source backup created before pull:
  - D:\transport-report-backups\production\source\PERF_WIALON_WORKLOAD_001C_20260616_123713
- pull scope verified as source-only:
  - workload_report.py
  - wialon_import.py
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production validation result:

- `/wialon/workload`:
  - response UTF-8 bytes: 230,785
  - SQL count: 4 SELECT
  - repeated SQL count: 0
  - repeated equipment SQL: 0
  - DML count: 0
  - no traceback

- `/wialon/workload/export`:
  - direct passthrough XLSX response
  - SQL count: 4 SELECT
  - repeated SQL count: 0
  - repeated equipment SQL: 0
  - DML count: 0
  - no traceback

Production post-restart smoke passed.

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

Wialon workload SQL behavior is now optimized:

- `/wialon/workload`: 21 SELECT reduced to 4 SELECT;
- `/wialon/workload/export`: 20 SELECT reduced to 4 SELECT;
- repeated equipment SQL eliminated;
- no DB/schema changes required;
- no migrations required;
- no template changes required.

## Remaining observations

Current heavier Wialon/reference areas after this optimization:

- `/wialon/mapping`: about 0.95 MB response body, 3 SELECT, repeated SQL 0.
- `/ref/equipment`: about 0.68 MB response body, 8 SELECT, repeated SQL 0.
- `/wialon/workload`: about 0.23 MB response body, 4 SELECT, repeated SQL 0.
- `/wialon/auto_match`: about 0.031 MB response body, 3 SELECT, repeated SQL 0.

Future candidates:

- PERF-WIALON-MAPPING-BODY-002: optional reduction of remaining `/wialon/mapping` forms/inputs if worth the UX risk.
- PERF-WORK-TYPES-BODY-001: optional optimization of `/ref/work_types` inline edit rows if worth the risk.
