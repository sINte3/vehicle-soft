# RELEASE PERF-REF-004 - Reference organizations linked counters optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

735fa96ee1373e22ece08c1fd4908e0b14b63306

## Purpose

PERF-REF-004 optimized the `/ref/organizations` reference page by removing repeated per-organization linked-data count queries.

Target route:

- GET /ref/organizations

## Baseline diagnostic

PERF-REF-004A read-only staging diagnostic found:

- /ref/organizations: 86 SELECT
- repeated SQL:
  - 17 repeated `equipment` count queries
  - 17 repeated `fuel_warehouses` count queries
  - 17 repeated `spare_part_requests` count queries
  - 17 repeated `deficiencies` count queries
  - 17 repeated `users` relationship count queries
- DML count: 0
- no traceback
- no POST requests
- no source changes
- production untouched

Source location:

- file: app.py
- function: ref_organizations
- baseline lines around 1479-1498

Problematic pattern before patch:

- loaded organizations;
- for each organization, executed separate count queries for:
  - Equipment
  - FuelWarehouse
  - SparePartRequest
  - Deficiency
  - User via `org.users.count()`

## Implemented change

PERF-REF-004B changed only:

- app.py

Main change:

- replaced per-organization `.count()` calls with grouped bulk linked counter maps;
- used one grouped query per linked entity type:
  - Equipment by `organization_id`
  - FuelWarehouse by `organization_id`
  - SparePartRequest by `organization_id`
  - Deficiency by `organization_id`
  - User count through `user_organizations`
- preserved delete-protection logic:
  - `can_delete`
  - `linked_total`
  - `linked`
- preserved statistics:
  - total
  - filtered
  - with_equipment
  - without_equipment
- preserved `render_template('ref_organizations.html', ...)`;
- did not change DB schema;
- did not run migrations;
- did not modify templates.

## Repair during staging patch

Initial PERF-REF-004B staging patch attempted to count users through `User.organization_id`.

Validation correctly failed because `User` has no `organization_id`; users are linked to organizations through many-to-many table `user_organizations`.

Repair changed user counting to a grouped query through:

- `db.metadata.tables.get('user_organizations')`
- `user_org_table.c.organization_id`
- `user_org_table.c.user_id`

## Staging validation

Staging validation after repaired patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /ref/organizations SELECT count reduced from 86 to 6;
- /ref/organizations repeated SQL count reduced to 0;
- /ref/organizations DML count: 0;
- no traceback;
- no POST requests;
- staging post-restart smoke OK.

Sampled route validation after patch:

- /ref/organizations
- /ref/customers
- /ref/work_types
- /ref/equipment
- /fuel/
- /spare-parts/
- /wialon

## Production rollout

PERF-REF-004C deployed the code commit to production.

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

- /ref/organizations SELECT count: 6
- /ref/organizations repeated SQL count: 0
- /ref/organizations DML count: 0
- no traceback
- production post-restart smoke OK

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Reference page performance state after PERF-REF-004

Optimized:

- /ref/equipment: 8 SELECT
- /ref/work_types: 2 SELECT
- /ref/customers: 2 SELECT
- /ref/organizations: 6 SELECT

Remaining observations:

- /ref/equipment response body is still about 2.33 MB, though SQL count is already optimized.
- /wialon/mapping response body is about 19.2 MB and remains a separate optimization candidate.

Future candidates:

- PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.
- PERF-REF-BODY-001: reduce heavy reference page response size where useful.
