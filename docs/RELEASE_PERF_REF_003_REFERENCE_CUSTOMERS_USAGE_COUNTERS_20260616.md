# RELEASE PERF-REF-003 - Reference customers usage counters optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

99cd898824ad75eca120795f3aa40325c50bc143

## Purpose

PERF-REF-003 optimized the `/ref/customers` reference page by removing N+1 usage counter queries against `daily_records`.

Target route:

- GET /ref/customers

## Baseline diagnostic

PERF-REF-003A read-only staging diagnostic found:

- /ref/customers: 11 SELECT
- repeated SQL:
  - 9 repeated `daily_records` count queries
- DML count: 0
- no traceback
- no POST requests
- no source changes
- production untouched

Source location:

- file: app.py
- function: ref_customers
- baseline lines around 2409-2413

Problematic pattern before patch:

- loaded all customers;
- for each customer, executed:
  - `DailyRecord.query.filter(DailyRecord.customer == customer.name).count()`

## Implemented change

PERF-REF-003B changed only:

- app.py

Main change:

- replaced per-customer `.count()` calls with one grouped bulk usage count query:
  - `GROUP BY DailyRecord.customer`
- reused the same grouped count map for `missing_from_ref` calculation;
- removed the separate `distinct()` query for `DailyRecord.customer`;
- preserved existing filtering behavior:
  - all
  - internal / external
  - used / unused
- preserved `render_template('ref_customers.html', ...)`;
- did not change DB schema;
- did not run migrations;
- did not modify templates.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /ref/customers SELECT count reduced from 11 to 2;
- /ref/customers repeated SQL count reduced to 0;
- /ref/customers DML count: 0;
- no traceback;
- no POST requests;
- staging post-restart smoke OK.

Sampled route validation after patch:

- /ref/customers
- /ref/work_types
- /ref/equipment
- /ref/organizations
- /fuel/
- /spare-parts/
- /wialon

## Production rollout

PERF-REF-003C deployed the code commit to production.

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

- /ref/customers SELECT count: 2
- /ref/customers repeated SQL count: 0
- /ref/customers DML count: 0
- no traceback
- production post-restart smoke OK

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Remaining observations

PERF-REF-003 intentionally fixed only `/ref/customers`.

Remaining optimization candidates from the reference-page audit:

- /ref/organizations: 86 SELECT, repeated per-organization count queries
- /wialon/mapping: response body about 19.2 MB
- /ref/equipment: response body still about 2.33 MB, but SQL query count is already optimized to 8
- /ref/work_types: already optimized to 2 SELECT
- /ref/customers: already optimized to 2 SELECT

Future candidates:

- PERF-REF-004: optimize `/ref/organizations` per-organization counters.
- PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.
