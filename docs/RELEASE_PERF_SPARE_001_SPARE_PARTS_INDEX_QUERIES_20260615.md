# RELEASE PERF-SPARE-001 - Spare parts index query optimization

Date: 2026-06-15

## Status

Completed and deployed to production.

## Code commit

578980a4818c536d2ec77d22ef935c3005489e59

## Purpose

PERF-SPARE-001 optimized repeated SQL queries on the spare parts index page.

Target route:

- /spare-parts/

## Baseline audit result

PERF-SPARE-001A read-only staging audit confirmed:

- /spare-parts/ had 29 SELECT statements;
- repeated spare_part_request_items SELECTs: 12;
- repeated equipment SELECTs: 8;
- repeated status count SELECTs: 4;
- repeated users SELECTs: 2.

No source files were modified during the audit.
No DB writes were performed.
No POST requests were executed.
No service restart was performed.
Production was not touched.

## Implemented change

PERF-SPARE-001B V2 changed only:

- spare_parts.py

Main changes:

- added eager loading for relationships used by templates/spare_parts_list.html:
  - SparePartRequest.organization;
  - SparePartRequest.equipment;
  - SparePartRequest.creator;
  - SparePartRequest.items;
- replaced per-status count loop with one grouped aggregate query;
- removed repeated N+1 relationship SELECTs on the list page.

## Staging validation after patch

After patch, authenticated read-only SQL audit showed:

- /spare-parts/ reduced from 29 SELECT to 4 SELECT;
- repeated SELECT patterns removed;
- source scan OK;
- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- no traceback;
- no DB writes;
- no POST requests;
- staging post-restart smoke OK.

## Production rollout

PERF-SPARE-001C deployed the code commit to production.

Production rollout details:

- production source backup created before pull;
- pull scope verified as source-only:
  - spare_parts.py
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production post-restart smoke passed for:

- /login
- /spare-parts/
- /spare-parts/new
- /
- /fuel/
- /fuel/report
- /wialon

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Notes

PERF-SPARE-001 is limited to the spare parts index page.

Recommended future task candidates:

- AUDIT-GET-SIDE-EFFECT-001 or WIALON-EXPORT-AUDIT-001: review GET export routes that write audit logs.
- API-FUEL-LEGACY-002: confirm Topaz endpoint config and plan safe legacy alias removal.
- CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.
