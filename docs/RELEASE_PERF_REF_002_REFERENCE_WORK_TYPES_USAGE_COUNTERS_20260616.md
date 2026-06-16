# RELEASE PERF-REF-002 - Reference work types usage counters optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

7afcc32a068fd419bb5743c12f2decec2eded37c

## Purpose

PERF-REF-002 optimized the `/ref/work_types` reference page by removing N+1 usage counter queries against `daily_records`.

Target route:

- GET /ref/work_types

## Baseline diagnostic

PERF-REF-002A read-only staging diagnostic found:

- /ref/work_types: 106 SELECT
- repeated SQL:
  - 104 repeated `daily_records` count queries
- DML count: 0
- no traceback
- no POST requests
- no source changes
- production untouched

Source location:

- file: app.py
- function: ref_work_types
- baseline lines around 2266-2270

Problematic pattern before patch:

- loaded all work types;
- for each work type, executed:
  - `DailyRecord.query.filter(DailyRecord.work_type == wt.name).count()`

## Implemented change

PERF-REF-002B changed only:

- app.py

Main change:

- replaced per-work-type `.count()` calls with one grouped bulk usage count query:
  - `GROUP BY DailyRecord.work_type`
- reused the same grouped count map for `missing_from_ref` calculation;
- removed the separate `distinct()` query for `DailyRecord.work_type`;
- preserved existing filtering behavior:
  - all
  - used
  - unused
- preserved `render_template('ref_work_types.html', ...)`;
- did not change DB schema;
- did not run migrations;
- did not modify templates.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /ref/work_types SELECT count reduced from 106 to 2;
- /ref/work_types repeated SQL count reduced to 0;
- /ref/work_types DML count: 0;
- no traceback;
- no POST requests;
- staging post-restart smoke OK.

Sampled route validation after patch:

- /ref/work_types
- /ref/customers
- /ref/equipment
- /ref/organizations
- /fuel/
- /spare-parts/
- /wialon

## Production rollout

PERF-REF-002C deployed the code commit to production.

Production rollout details:

- production source backup created before pull;
- pull scope verified as source-only:
  - app.py
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production validation result:

- /ref/work_types SELECT count: 2
- /ref/work_types repeated SQL count: 0
- /ref/work_types DML count: 0
- no traceback
- production post-restart smoke OK

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Remaining observations

PERF-REF-002 intentionally fixed only `/ref/work_types`.

Remaining optimization candidates from the reference-page audit:

- /ref/customers: 11 SELECT, including 9 repeated daily_records count queries
- /ref/organizations: 86 SELECT, repeated per-organization count queries
- /wialon/mapping: response body about 19.2 MB
- /ref/equipment: response body still about 2.33 MB, but SQL query count is already optimized to 8

Future candidates:

- PERF-REF-003: optimize `/ref/customers` usage counters.
- PERF-REF-004: optimize `/ref/organizations` per-organization counters.
- PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.
