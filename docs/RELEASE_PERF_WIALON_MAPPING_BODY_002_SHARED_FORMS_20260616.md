# RELEASE PERF-WIALON-MAPPING-BODY-002 - Wialon mapping shared forms

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

1aa471977684b1fac950fdf758db57544082bd83

## Purpose

PERF-WIALON-MAPPING-BODY-002 reduced the `/wialon/mapping` response body by replacing repeated per-row mapping edit/delete forms with shared reusable forms.

Target route:

- GET /wialon/mapping

## Baseline diagnostic

PERF-WIALON-MAPPING-BODY-002A showed that SQL was already optimized after PERF-WIALON-MAP-001, but the rendered HTML body was still heavy.

Baseline `/wialon/mapping`:

- response UTF-8 bytes: 947,349
- SQL count: 3 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- mapping rows: 379
- pending rows: 3
- forms: 763
- inputs: 1,909
- selects: 384
- options: 387
- CSRF inputs: 763
- edit save forms: 379
- pending save forms: 3
- equipment selects: 384

Root cause:

- `/wialon/mapping` rendered one edit save form and one delete form for every mapping row.
- It also rendered many repeated hidden inputs and repeated equipment selects.
- SQL was not the problem; the main issue was HTML body size and repeated row-level form markup.

## Implemented change

PERF-WIALON-MAPPING-BODY-002B changed only:

- templates/wialon_mapping_list.html

Main changes:

- replaced repeated per-row edit forms with one shared edit form;
- replaced repeated per-row delete forms with one shared delete form;
- preserved pending forms;
- preserved manual add form;
- preserved existing POST endpoints:
  - `wialon_mapping_save`
  - `wialon_mapping_delete`
- added row metadata:
  - `data-map-id`
  - `data-equipment-id`
- removed heavy rendered `data-search`;
- removed repeated rendered `data-delete-url`;
- added shared delete URL template;
- changed mapping search to use row text cache in JavaScript;
- added visible row action buttons:
  - Edit
  - Mark as not in system
  - Delete
- preserved shared equipment options behavior from PERF-WIALON-MAP-001.

Markers added:

- `PERF-WIALON-MAPPING-BODY-002B_MARKER: shared mapping edit/delete forms.`
- `PERF-WIALON-MAPPING-BODY-002B_FIX_MARKER: search uses row text cache and buttons are visible.`
- `PERF-WIALON-MAPPING-BODY-002B_FIX2_MARKER: shared delete URL template.`

No route code was changed.

No DB schema changes were made.

No migrations were required.

## Staging validation

Staging validation passed after the final FIX2 patch.

Source validation:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- source checks passed.

Staging `/wialon/mapping` after patch:

- response UTF-8 bytes: 633,834
- SQL count: 3 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- mapping rows: 379
- pending rows: 3
- forms: 7
- inputs: 18
- selects: 6
- options: 9
- CSRF inputs: 7
- old edit save forms: 0
- pending save forms: 3
- shared edit row: 1
- shared edit form: 1
- shared delete form: 1
- `data-search`: 0
- `data-delete-url`: 0
- visible edit buttons: 379
- visible skip buttons: 379
- visible delete buttons: 379

Regression sample:

- /wialon/auto_match: 3 SELECT, repeated SQL 0
- /wialon/workload: 4 SELECT, repeated SQL 0
- /wialon: 3 SELECT, repeated SQL 0
- /wialon/report: 3 SELECT, repeated SQL 0
- /ref/equipment: 8 SELECT, repeated SQL 0

Staging post-restart smoke passed.

No DB writes were performed during validation.

No POST requests were executed during validation.

## Production rollout

PERF-WIALON-MAPPING-BODY-002C deployed the code commit to production.

Production rollout details:

- production source backup created before pull:
  - D:\transport-report-backups\production\source\PERF_WIALON_MAPPING_BODY_002C_20260616_145746
- pull scope verified as template-only:
  - templates/wialon_mapping_list.html
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production `/wialon/mapping` after patch:

- response UTF-8 bytes: 633,834
- SQL count: 3 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- mapping rows: 379
- pending rows: 3
- forms: 7
- inputs: 18
- selects: 6
- options: 9
- CSRF inputs: 7
- old edit save forms: 0
- pending save forms: 3
- shared edit row: 1
- shared edit form: 1
- shared delete form: 1
- `data-search`: 0
- `data-delete-url`: 0
- visible edit buttons: 379
- visible skip buttons: 379
- visible delete buttons: 379

Production post-restart smoke passed.

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

Wialon mapping response body was reduced without changing backend POST contracts:

- `/wialon/mapping`: 947,349 bytes reduced to 633,834 bytes;
- forms: 763 reduced to 7;
- inputs: 1,909 reduced to 18;
- selects: 384 reduced to 6;
- options: 387 reduced to 9;
- SQL stayed optimized at 3 SELECT;
- repeated SQL stayed 0;
- no DB/schema changes;
- no migrations;
- no Telegram bot restart.

## Remaining observations

The remaining `/wialon/mapping` body size is mostly table row content itself. Further major reduction would likely require pagination, lazy loading, or a more substantial UI redesign.

Current heavier areas after this optimization:

- `/ref/equipment`: about 0.68 MB response body, 8 SELECT, repeated SQL 0.
- `/wialon/mapping`: about 0.63 MB response body, 3 SELECT, repeated SQL 0.
- `/wialon/workload`: about 0.23 MB response body, 4 SELECT, repeated SQL 0.

Future candidates:

- PERF-WIALON-MAPPING-PAGINATION-001: optional pagination/lazy loading for `/wialon/mapping` if the table keeps growing.
- PERF-WORK-TYPES-BODY-001: optional optimization of `/ref/work_types` inline edit rows if worth the risk.
