# RELEASE PERF-REF-BODY-002 - Reference equipment inline edit rendering optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

f3be56072961f8f26ec9842a7d9b4d62ab04523c

## Purpose

PERF-REF-BODY-002 further optimized the `/ref/equipment` page by replacing hundreds of hidden inline edit rows with one reusable edit row.

Target route:

- GET /ref/equipment

## Baseline before PERF-REF-BODY-002

After PERF-REF-BODY-001, `/ref/equipment` SQL was already optimized, but the page still had heavy HTML because every equipment row rendered a hidden edit form.

Baseline `/ref/equipment` before this change:

- response chars: 1,422,642
- response UTF-8 bytes: 1,498,837
- `<tr>` count: 673
- `<select>` count: 676
- `<option>` count: 719
- `<input>` count: 2,762
- `<form>` count: 675
- CSRF hidden inputs: 674
- hidden `id` inputs: 336
- old inline edit rows: 336
- SQL count: 8 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- no POST requests
- production untouched

Root cause:

- the page rendered a full hidden edit row for every equipment item;
- each edit row repeated save form fields and hidden inputs;
- edit rows were hidden with `display:none`, but still sent to the browser.

## Implemented change

PERF-REF-BODY-002B changed only:

- templates/ref_equipment.html

Main changes:

- added row-level `data-*` attributes to each display row;
- removed per-equipment hidden inline edit rows;
- added one reusable shared edit row:
  - `ref-equipment-edit-row`
- added JavaScript logic to move the shared edit row under the selected equipment row;
- populated the shared edit form from row `data-*` attributes;
- preserved the existing `/ref/equipment/save` POST contract;
- preserved existing delete/deactivate/enable forms;
- preserved shared organization/category option payloads from PERF-REF-BODY-001;
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
- `/ref/equipment` response size reduced to:
  - 635,796 chars
  - 681,467 UTF-8 bytes
- `/ref/equipment` old inline edit rows reduced to 0;
- shared edit rows: 1;
- data rows: 336;
- `<select>` count reduced to 6;
- `<option>` count reduced to 49;
- `<input>` count reduced to 417;
- `<form>` count reduced to 340;
- SQL remained 8 SELECT;
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

PERF-REF-BODY-002C deployed the code commit to production.

Production rollout details:

- production source backup created before pull:
  - D:\transport-report-backups\production\source\PERF_REF_BODY_002C_20260616_122247
- pull scope verified as source-only:
  - templates/ref_equipment.html
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production validation result:

- `/ref/equipment` response size:
  - 635,930 chars
  - 681,601 UTF-8 bytes
- old inline edit rows: 0
- shared edit rows: 1
- data rows: 336
- `<select>` count: 6
- `<option>` count: 49
- `<input>` count: 417
- `<form>` count: 340
- SQL count: 8 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- production post-restart smoke OK

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

The route `/ref/equipment` is now much lighter:

- response size reduced from about 1.50 MB to about 0.68 MB after PERF-REF-BODY-002;
- response size reduced from about 2.50 MB to about 0.68 MB across PERF-REF-BODY-001 and PERF-REF-BODY-002;
- hidden inline edit rows removed;
- edit form rendering is now reusable instead of repeated per row;
- SQL stayed optimized;
- repeated SQL stayed at 0;
- no DB/schema changes were required.

## Remaining observations

Current heavy-page state after this optimization:

- /wialon/mapping: 3 SELECT, response body about 0.95 MB
- /ref/equipment: 8 SELECT, response body about 0.68 MB
- /ref/work_types: 2 SELECT, response body about 0.27 MB
- /ref/organizations: 6 SELECT, response body about 0.046 MB
- /ref/customers: 2 SELECT, response body about 0.040 MB

Future candidates:

- PERF-WIALON-AUTOMATCH-001: audit `/wialon/auto_match` response size and SQL behavior if needed.
- PERF-WORK-TYPES-BODY-001: optional optimization of `/ref/work_types` inline edit rows if worth the risk.
