# RELEASE PERF-WIALON-MAP-001 - Wialon mapping response size optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

86317434c0d39b89c7812225483e5ea8178358f2

## Purpose

PERF-WIALON-MAP-001 optimized the `/wialon/mapping` page by reducing response size and rendering cost.

Target route:

- GET /wialon/mapping

## Baseline diagnostic

PERF-WIALON-MAP-001A read-only staging diagnostic found:

- /wialon/mapping response size:
  - 19,225,278 chars
  - 19,898,023 UTF-8 bytes
- HTML markers:
  - `<option>` count: 128,692
  - `<select>` count: 384
  - `<tr>` count: 384
  - `<form>` count: 763
- SQL:
  - 20 SELECT
  - 4 unique SELECT patterns
  - 17 repeated organization lazy-load queries
- DML count: 0
- no traceback
- no POST requests
- no source changes
- production untouched

Root cause:

- `templates/wialon_mapping_list.html` rendered the full active equipment list inside each mapping dropdown.
- The page had 384 select controls, each repeating the equipment options.
- `wialon_import.py` did not eager-load mapping equipment organizations, causing repeated organization queries.

## Implemented change

PERF-WIALON-MAP-001B changed only:

- wialon_import.py
- templates/wialon_mapping_list.html

Main changes:

- added eager loading for:
  - `VialonMapping.equipment`
  - `Equipment.organization`
- added eager loading for active equipment organization labels;
- built a single `equipment_options` list in the Flask view;
- passed `equipment_options` once to the template;
- removed repeated `{% for eq in all_equipment %}` option loops from the template;
- rendered only initial placeholder/current select state in HTML;
- populated equipment dropdowns on the client side from one shared JSON payload;
- preserved existing manual add/edit/save/skip/delete behavior;
- preserved existing route and template names;
- did not change DB schema;
- did not run migrations.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /wialon/mapping response size reduced to:
  - 898,253 chars
  - 947,349 UTF-8 bytes
- /wialon/mapping `<option>` count reduced to 387;
- /wialon/mapping SQL reduced to 3 SELECT;
- repeated SQL count reduced to 0;
- DML count: 0;
- no traceback;
- no POST requests;
- staging post-restart smoke OK.

Sampled route validation after patch:

- /wialon/mapping
- /wialon
- /ref/organizations
- /ref/equipment
- /ref/customers
- /ref/work_types
- /fuel/
- /spare-parts/

## Production rollout

PERF-WIALON-MAP-001C deployed the code commit to production.

Production rollout details:

- production source backup created before pull;
- pull scope verified as source-only:
  - wialon_import.py
  - templates/wialon_mapping_list.html
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production validation result:

- /wialon/mapping response size:
  - 898,253 chars
  - 947,349 UTF-8 bytes
- /wialon/mapping `<option>` count: 387
- /wialon/mapping SQL count: 3 SELECT
- repeated SQL count: 0
- DML count: 0
- no traceback
- production post-restart smoke OK

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

The route `/wialon/mapping` is now much lighter:

- response size reduced by about 95%;
- option count reduced by about 99.7%;
- SQL query count reduced by 85%;
- repeated lazy-load SQL removed.

## Remaining observations

Reference pages after recent optimizations:

- /ref/equipment: 8 SELECT, but response body still about 2.5 MB
- /ref/work_types: 2 SELECT
- /ref/customers: 2 SELECT
- /ref/organizations: 6 SELECT
- /wialon/mapping: 3 SELECT, response body about 0.95 MB

Future candidates:

- PERF-REF-BODY-001: reduce heavy reference page response size where useful.
- PERF-WIALON-AUTOMATCH-001: audit `/wialon/auto_match` response size and SQL behavior if needed.
