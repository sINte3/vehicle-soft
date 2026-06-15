# RELEASE AUDIT-GET-SIDE-EFFECT-001 - Wialon GET export audit side effect

Date: 2026-06-15

## Status

Completed and deployed to production.

## Code commit

5c86893cf9175210822e502a1e85f259a51938e8

## Purpose

AUDIT-GET-SIDE-EFFECT-001 checked GET routes for unintended database writes and fixed the confirmed side effect in the Wialon engine-hours Excel export.

Target route:

- GET /wialon/report/export

## Baseline audit result

AUDIT-GET-SIDE-EFFECT-001A read-only staging audit found one confirmed GET side effect:

- /wialon/report/export attempted INSERT INTO audit_logs during a GET request.

Checked routes without DML:

- /wialon/report
- /wialon
- /report
- /fuel/report
- /spare-parts/

The audit blocked DML with a SQLAlchemy hook.
No source files were modified.
No DB writes were performed.
No POST requests were executed.
No service restart was performed.
Production was not touched.

## Diagnostic result

AUDIT-GET-SIDE-EFFECT-001B diagnostic identified the source:

- file: wialon_import.py
- function: wialon_report_export
- route: /wialon/report/export

Confirmed source lines:

- _audit_wialon(...) inside wialon_report_export
- db.session.commit() immediately before send_file response

## Implemented change

AUDIT-GET-SIDE-EFFECT-001B V4 changed only:

- wialon_import.py

Main change:

- removed the audit-log write from GET /wialon/report/export;
- removed db.session.commit() from the GET export path;
- preserved Excel generation and send_file response.

The route still returns:

- application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
- direct_passthrough=True
- HTTP 200 during authenticated route-level validation.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /wialon/report/export DML count: 0;
- /wialon/report/export SELECT count: 2;
- no traceback;
- no POST requests;
- no DB writes;
- staging post-restart smoke OK.

Validated GET routes after patch:

- /wialon/report/export
- /wialon/report
- /wialon
- /report
- /fuel/report
- /spare-parts/

## Production rollout

AUDIT-GET-SIDE-EFFECT-001C deployed the code commit to production.

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

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Notes

This task intentionally removed audit logging from a GET export route to preserve read-only GET semantics.

Future candidates:

- AUDIT-GET-SIDE-EFFECT-002: expand DML-blocked audit to all GET-only export/download routes.
- CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.
- API-FUEL-LEGACY-002: confirm Topaz endpoint config and plan safe legacy alias removal.
