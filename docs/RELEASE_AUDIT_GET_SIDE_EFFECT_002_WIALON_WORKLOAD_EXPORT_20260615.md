# RELEASE AUDIT-GET-SIDE-EFFECT-002 - Wialon workload GET export audit side effect

Date: 2026-06-15

## Status

Completed and deployed to production.

## Code commit

a1a2f1ac745ccc9aa7e91aa328377430064d7ac6

## Purpose

AUDIT-GET-SIDE-EFFECT-002 expanded the GET export/download DML audit after AUDIT-GET-SIDE-EFFECT-001 and fixed the confirmed side effect in the Wialon workload Excel export.

Target route:

- GET /wialon/workload/export

## Baseline audit result

AUDIT-GET-SIDE-EFFECT-002A read-only staging audit found one confirmed GET side effect:

- /wialon/workload/export attempted INSERT INTO audit_logs during a GET request.

Checked export/download GET routes without DML after AUDIT-GET-SIDE-EFFECT-001:

- /ref/equipment/export
- /ref/work_types/export_diagnostics
- /ref/customers/export_diagnostics
- /wialon/report/export
- /fuel/report?export=1
- /report?export=1

The audit blocked DML with a SQLAlchemy hook.
No source files were modified.
No DB writes were performed.
No POST requests were executed.
No service restart was performed.
Production was not touched.

## Diagnostic result

AUDIT-GET-SIDE-EFFECT-002A source scan identified the source:

- file: wialon_import.py
- function: wialon_workload_export
- route: /wialon/workload/export

Confirmed source area:

- _audit_wialon(...) inside wialon_workload_export
- db.session.commit() immediately before send_file response

## Implemented change

AUDIT-GET-SIDE-EFFECT-002B changed only:

- wialon_import.py

Main change:

- removed the audit-log write from GET /wialon/workload/export;
- removed db.session.commit() from the GET workload export path;
- preserved Excel generation and send_file response.

The previous AUDIT-GET-SIDE-EFFECT-001 fix for /wialon/report/export was preserved.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /wialon/workload/export DML count: 0;
- /wialon/workload/export SELECT count: 20;
- /wialon/workload/export returned Excel response;
- no traceback;
- no POST requests;
- no DB writes;
- staging post-restart smoke OK.

Validated export/download GET routes after patch:

- /ref/equipment/export
- /ref/work_types/export_diagnostics
- /ref/customers/export_diagnostics
- /wialon/report/export
- /wialon/workload/export
- /fuel/report?export=1
- /report?export=1

## Production rollout

AUDIT-GET-SIDE-EFFECT-002C deployed the code commit to production.

Production rollout details:

- production source backup created before pull;
- pull scope verified as source-only:
  - wialon_import.py
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production post-restart smoke passed for:

- /login
- /
- /fuel/
- /fuel/report
- /spare-parts/
- /wialon
- /wialon/report
- /wialon/workload

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Notes

This task intentionally removed audit logging from a GET export route to preserve read-only GET semantics.

Together with AUDIT-GET-SIDE-EFFECT-001, both Wialon GET Excel export routes now avoid audit_logs writes:

- /wialon/report/export
- /wialon/workload/export

Future candidates:

- AUDIT-GET-SIDE-EFFECT-003: broader read-only DML audit for non-export GET routes.
- CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.
- API-FUEL-LEGACY-002: confirm Topaz endpoint config and plan safe legacy alias removal.
