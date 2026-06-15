# RELEASE SEC-HARD-001 - Basic security hardening

Date: 2026-06-15

## Status

Completed on staging and production.

## Code commit

3b95db567dde716cd95a885d1b4e568af8153dcb

Commit message:

Add basic security hardening

## Source changes

Changed files:

- app.py
- fuel_routes.py

No DB schema changes were made.

No migrations were executed.

## Purpose

SEC-HARD-001 closes three small security hardening findings discovered during read-only audit:

- missing MAX_CONTENT_LENGTH;
- missing explicit 500 error handler;
- non-constant-time comparison for Topaz fuel sync API token.

## Read-only audit

SEC-HARD-001A confirmed:

- app import OK;
- URL rules count: 86;
- MAX_CONTENT_LENGTH was not configured;
- explicit 500 error handler was not registered;
- project already used hmac.compare_digest in other places;
- fuel sync token check still used direct string comparison in fuel_routes.py;
- HTTP smoke passed;
- no source files were modified;
- no DB writes were performed;
- no POST requests were executed;
- no service restart was performed;
- production was not touched.

Findings:

- MAX_CONTENT_LENGTH is not configured or is falsy.
- Source does not contain MAX_CONTENT_LENGTH.
- Source does not contain explicit 500 error handler.

Fuel sync focus before patch:

- fuel_routes.py used payload.get('token') != api_token.

## Implemented changes

app.py:

- added default request body limit:
  - MAX_CONTENT_LENGTH = 16 * 1024 * 1024
  - runtime value: 16777216 bytes
- added explicit 500 error handler:
  - rolls back db.session if possible;
  - renders error.html with code 500.

fuel_routes.py:

- added import hmac;
- replaced direct token comparison with hmac.compare_digest;
- old expression payload.get('token') != api_token removed.

## Staging validation

SEC-HARD-001B staging validation confirmed:

- changed files only:
  - app.py
  - fuel_routes.py
- py_compile passed;
- git diff --check passed;
- source scan:
  - app_has_MAX_CONTENT_LENGTH=True
  - app_has_500_handler=True
  - fuel_has_hmac_import=True
  - fuel_has_compare_digest=True
  - fuel_old_token_compare_removed=True
- app import OK;
- URL rules count: 86;
- MAX_CONTENT_LENGTH runtime value: 16777216;
- 500 error handler registered;
- HTTP smoke passed:
  - /login
  - /fuel/api/fuel_ping
  - /fuel/
  - /fuel/report
  - /fuel/transactions
  - /nonexistent-security-audit-url
- TransportReportStaging restarted and running.
- No DB writes were performed.
- No POST requests were executed.
- Production was not touched.

## Production rollout

SEC-HARD-001C committed and rolled out the source patch to production.

Production pull scope:

- app.py
- fuel_routes.py

Production backup before pull:

- D:\transport-report-backups\production\source\SEC_HARD_001C_*\app.py
- D:\transport-report-backups\production\source\SEC_HARD_001C_*\fuel_routes.py

Production validation confirmed:

- source scan:
  - app_has_MAX_CONTENT_LENGTH=True
  - app_has_500_handler=True
  - fuel_has_hmac_import=True
  - fuel_has_compare_digest=True
  - fuel_old_token_compare_removed=True
- app import OK;
- URL rules count: 86;
- MAX_CONTENT_LENGTH runtime value: 16777216;
- 500 error handler registered;
- HTTP smoke passed:
  - /login
  - /fuel/api/fuel_ping
  - /fuel/
  - /fuel/report
  - /fuel/transactions
  - /nonexistent-security-audit-url
- TransportReport was restarted and is running.
- TransportBot was not restarted.
- TransportBot003 was not restarted.
- No DB writes were performed.
- No POST requests were executed.

Final production services:

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

SEC-HARD-001 is complete.

The application now has:

- 16 MiB request body limit;
- explicit 500 error handler;
- constant-time Topaz fuel sync token comparison.
