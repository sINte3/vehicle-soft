# RELEASE PERF-WORK-TYPES-BODY-001 - Reference work types shared forms

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

42452831d2643908a229ef1cb5514f8701dab469

## Purpose

PERF-WORK-TYPES-BODY-001 reduced the `/ref/work_types` response body by replacing repeated inline edit/delete forms with shared reusable forms.

Target route:

- GET /ref/work_types

## Baseline diagnostic

PERF-WORK-TYPES-BODY-001A showed that SQL for `/ref/work_types` was already optimized, but the rendered HTML body still contained repeated per-row edit/delete markup.

Baseline `/ref/work_types`:

- response UTF-8 bytes: 266,902
- SQL count: 2 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- table rows: 209
- forms: 111
- inputs: 530
- selects: 1
- options: 3
- CSRF inputs: 110
- hidden id inputs: 104
- display:none count: 106

Root cause:

- `/ref/work_types` rendered one hidden edit form per work type row.
- It also rendered one delete form per work type row.
- SQL was not the problem; the main issue was repeated HTML form/input markup.

## Implemented change

PERF-WORK-TYPES-BODY-001B changed only:

- templates/ref_work_types.html

Main changes:

- replaced repeated hidden inline edit rows with one shared edit row;
- replaced repeated delete forms with one shared delete form;
- preserved the filter form;
- preserved the add-new-work-type form;
- preserved existing backend POST endpoints:
  - `save_work_type`
  - `delete_work_type`
- added row metadata:
  - `data-wt-id`
  - `data-name`
  - `data-default-unit`
  - `data-default-price`
- added shared delete URL template;
- retained existing edit button behavior through JavaScript override;
- did not change route code;
- did not change DB schema;
- did not run migrations.

Marker added:

- `PERF-WORK-TYPES-BODY-001B_MARKER: shared work type edit/delete forms.`

## Staging validation

Staging validation passed.

Source validation:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- source checks passed.

Staging `/ref/work_types` after patch:

- response UTF-8 bytes: 127,204
- SQL count: 2 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- table rows: 107
- forms: 5
- inputs: 12
- selects: 1
- options: 3
- CSRF inputs: 4
- hidden id inputs: 1
- old inline edit rows: 0
- shared edit row: 1
- shared edit form: 1
- shared delete form: 1
- `data-wt-id` count: 105
- `data-name` count: 104

Regression sample:

- /ref/equipment: 8 SELECT, repeated SQL 0
- /wialon/mapping: 3 SELECT, repeated SQL 0
- /wialon/workload: 4 SELECT, repeated SQL 0
- /fuel/: 11 SELECT
- /spare-parts/: 4 SELECT, repeated SQL 0

Staging post-restart smoke passed.

No DB writes were performed during validation.

No POST requests were executed during validation.

## Production rollout

PERF-WORK-TYPES-BODY-001C deployed the code commit to production.

Production rollout details:

- production source backup created before pull:
  - D:\transport-report-backups\production\source\PERF_WORK_TYPES_BODY_001C_20260616_154849
- pull scope verified as template-only:
  - templates/ref_work_types.html
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production `/ref/work_types` after patch:

- response UTF-8 bytes: 127,209
- SQL count: 2 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- table rows: 107
- forms: 5
- inputs: 12
- selects: 1
- options: 3
- CSRF inputs: 4
- hidden id inputs: 1
- old inline edit rows: 0
- shared edit row: 1
- shared edit form: 1
- shared delete form: 1
- `data-wt-id` count: 105
- `data-name` count: 104

Production post-restart smoke passed.

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

Reference work types response body was reduced without changing backend POST contracts:

- `/ref/work_types`: 266,902 bytes reduced to about 127,209 bytes;
- forms: 111 reduced to 5;
- inputs: 530 reduced to 12;
- CSRF inputs: 110 reduced to 4;
- SQL stayed optimized at 2 SELECT;
- repeated SQL stayed 0;
- no DB/schema changes;
- no migrations;
- no Telegram bot restart.

## Remaining observations

Current heavier areas after this optimization:

- `/ref/equipment`: about 0.68 MB response body, 8 SELECT, repeated SQL 0.
- `/wialon/mapping`: about 0.63 MB response body, 3 SELECT, repeated SQL 0.
- `/wialon/workload`: about 0.23 MB response body, 4 SELECT, repeated SQL 0.
- `/ref/work_types`: about 0.13 MB response body, 2 SELECT, repeated SQL 0.

Future candidates:

- PERF-REF-EQUIPMENT-BODY-003: optional further reduction of `/ref/equipment` remaining forms/actions if worth the UX risk.
- PERF-FUEL-DASH-REPEAT-001: optional investigation of remaining repeated query on `/fuel/`.
