# RELEASE PERF-REF-001 - Reference equipment linked counters optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

bdd9b6b6e4a96b1799643d9613875f8e43cf0a1d

## Purpose

PERF-REF-001 optimized the `/ref/equipment` reference page by removing N+1 linked-record count queries used to determine delete/deactivate state for equipment rows.

Target route:

- GET /ref/equipment

## Baseline audit result

PERF-REF-001A read-only staging SQL audit found:

- /ref/equipment: 1348 SELECT
- /ref/equipment response body: about 2.33 MB
- DML count: 0
- no traceback
- no POST requests
- no source changes
- no production changes

The repeated queries were:

- 336 repeated count queries against daily_records
- 336 repeated count queries against engine_hours_records
- 336 repeated count queries against vialon_mappings
- 336 repeated count queries against spare_part_requests

Total repeated count queries:

- 336 equipment rows x 4 linked-count queries = 1344 repeated SELECT queries

## Source diagnostic

PERF-REF-001B confirmed the source in:

- file: app.py
- function: ref_equipment
- approximate lines before patch: 1654-1660

The template `templates/ref_equipment.html` did not contain `.count()` expressions. The N+1 counts were generated directly in the Flask view.

## Implemented change

PERF-REF-001C changed only:

- app.py

Main change:

- replaced per-equipment `.count()` queries with four grouped bulk count maps;
- preserved existing delete/deactivate decision structure;
- preserved `render_template('ref_equipment.html', ...)`;
- did not change DB schema;
- did not run migrations;
- did not modify templates.

New logic:

- collect equipment IDs from the filtered equipment list;
- query linked counts in bulk using `GROUP BY equipment_id`;
- build:
  - daily record count map;
  - engine-hours count map;
  - Wialon mapping count map;
  - spare-part request count map;
- use maps to populate `equipment_delete_info`.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /ref/equipment SELECT count reduced from 1348 to 8;
- /ref/equipment repeated SQL count reduced to 0;
- /ref/equipment DML count: 0;
- no traceback;
- no POST requests;
- staging post-restart smoke OK.

Sampled route validation after patch:

- /ref/equipment
- /ref/equipment/export
- /ref/organizations
- /ref/work_types
- /ref/customers
- /fuel/
- /spare-parts/
- /wialon

## Production rollout

PERF-REF-001D deployed the code commit to production.

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

- /ref/equipment SELECT count: 8
- /ref/equipment repeated SQL count: 0
- /ref/equipment DML count: 0
- no traceback
- production post-restart smoke OK

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Remaining observations

PERF-REF-001 intentionally fixed only the critical `/ref/equipment` N+1 linked counter issue.

Remaining optimization candidates from the same audit:

- /ref/work_types: 106 SELECT, mostly repeated daily_records count queries
- /ref/organizations: 86 SELECT, repeated count queries per organization
- /wialon/mapping: response body about 19.2 MB
- /ref/equipment: response body still about 2.33 MB, but SQL query count is now acceptable

Future candidates:

- PERF-REF-002: optimize `/ref/work_types` repeated daily_records counters.
- PERF-REF-003: optimize `/ref/organizations` per-organization counters.
- PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.
