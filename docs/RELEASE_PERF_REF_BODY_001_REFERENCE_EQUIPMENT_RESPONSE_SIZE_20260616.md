# RELEASE PERF-REF-BODY-001 - Reference equipment response size optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

89e68c49a8620b4b33202af344fda99a614c2908

## Purpose

PERF-REF-BODY-001 optimized the `/ref/equipment` page by reducing response size while keeping the existing page behavior.

Target route:

- GET /ref/equipment

## Baseline diagnostic

PERF-REF-BODY-001A read-only staging diagnostic found `/ref/equipment` was the largest remaining reference page by response size.

Baseline `/ref/equipment`:

- response chars: 2,330,660
- response UTF-8 bytes: 2,496,903
- `<option>` count: 8,783
- `<select>` count: 676
- `<form>` count: 675
- `<input>` count: 2,762
- SQL count: 8 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- no POST requests
- no source changes
- production untouched

Body-size ranking during diagnostic:

- /ref/equipment: 2,496,903 bytes
- /wialon/mapping: 947,349 bytes
- /ref/work_types: 266,902 bytes
- /ref/organizations: 45,686 bytes
- /ref/customers: 40,297 bytes

Root cause:

- SQL was already optimized.
- The remaining issue was HTML size.
- `templates/ref_equipment.html` repeated organization and category option lists inside each equipment edit row.
- The page contained hundreds of edit select controls.

## Implemented change

PERF-REF-BODY-001B changed only:

- templates/ref_equipment.html

Main changes:

- kept the server-rendered filter controls and add form intact;
- replaced repeated edit-row organization select option loops with shared client-side options;
- replaced repeated edit-row category select option loops with shared client-side options;
- added one shared organization options payload:
  - `REF_EQUIPMENT_ORG_OPTIONS`
- added one shared category options payload:
  - `REF_EQUIPMENT_CATEGORY_OPTIONS`
- added client-side population function:
  - `populateRefEquipmentEditSelects`
- preserved delete/deactivate/enable forms;
- preserved existing route and Flask view;
- did not change DB schema;
- did not run migrations;
- did not change SQL logic.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- source checks passed;
- /ref/equipment response size reduced to:
  - 1,422,642 chars
  - 1,498,837 UTF-8 bytes
- /ref/equipment `<option>` count reduced to 719;
- /ref/equipment SQL remained 8 SELECT;
- repeated SQL count remained 0;
- DML count: 0;
- no traceback;
- no POST requests;
- staging post-restart smoke OK.

Sampled route validation after patch:

- /ref/equipment
- /ref/work_types
- /wialon/mapping
- /ref/organizations
- /ref/customers
- /fuel/
- /spare-parts/

## Production rollout

PERF-REF-BODY-001C deployed the code commit to production.

Production rollout details:

- production source backup created before pull:
  - D:\transport-report-backups\production\source\PERF_REF_BODY_001C_20260616_121122
- pull scope verified as source-only:
  - templates/ref_equipment.html
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production validation result:

- /ref/equipment response size:
  - 1,422,776 chars
  - 1,498,971 UTF-8 bytes
- /ref/equipment `<option>` count: 719
- /ref/equipment SQL count: 8 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- production post-restart smoke OK

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

The route `/ref/equipment` is now lighter:

- response size reduced by about 40%;
- option count reduced by about 91.8%;
- SQL stayed optimized;
- repeated SQL stayed at 0;
- no DB/schema changes were required.

## Remaining observations

Reference page state after this optimization:

- /ref/equipment: 8 SELECT, response body about 1.5 MB
- /wialon/mapping: 3 SELECT, response body about 0.95 MB
- /ref/work_types: 2 SELECT, response body about 0.27 MB
- /ref/organizations: 6 SELECT, response body about 0.046 MB
- /ref/customers: 2 SELECT, response body about 0.040 MB

Future candidates:

- PERF-REF-BODY-002: reduce remaining `/ref/equipment` forms/inputs by converting inline edit rows to one reusable edit modal or lazy edit row.
- PERF-WIALON-AUTOMATCH-001: audit `/wialon/auto_match` response size and SQL behavior if needed.
