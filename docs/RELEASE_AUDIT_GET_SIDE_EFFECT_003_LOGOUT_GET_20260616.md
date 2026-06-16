# RELEASE AUDIT-GET-SIDE-EFFECT-003 - Logout GET audit side effect

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

8132dd9f866d6b28ef6466d58180ad9634e299b3

## Purpose

AUDIT-GET-SIDE-EFFECT-003 expanded the read-only GET DML audit beyond export/download routes and fixed the remaining confirmed GET side effect.

Target route:

- GET /logout

## Baseline audit result

AUDIT-GET-SIDE-EFFECT-003A broad read-only staging audit found one confirmed GET side effect:

- /logout attempted INSERT INTO audit_logs during a GET request.

The audit covered GET routes without URL path parameters.

All other audited GET routes had DML count 0.

Notable heavy but read-only routes observed:

- /ref/equipment: 1348 SELECT
- /wialon/mapping: 20 SELECT, response body about 19 MB
- /fuel/warehouses: 73 SELECT
- /ref/work_types: 106 SELECT

## Implemented change

AUDIT-GET-SIDE-EFFECT-003B changed only:

- app.py

Main change:

- removed audit-log write from GET /logout;
- removed db.session.commit() from GET /logout;
- preserved logout_user();
- preserved redirect behavior.

This is a minimal side-effect cleanup. Converting logout from GET to POST remains a separate future task because it requires UI/navbar/form changes.

## Staging validation

Staging validation after patch:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- /logout DML count: 0;
- /logout SELECT count: 0;
- logout_user() preserved;
- redirect preserved;
- no traceback;
- no POST requests;
- staging post-restart smoke OK.

Validated sampled GET routes after patch:

- /logout
- /
- /admin/audit
- /fuel/
- /fuel/report
- /spare-parts/
- /wialon/report/export
- /wialon/workload/export

## Production rollout

AUDIT-GET-SIDE-EFFECT-003C deployed the code commit to production.

Production rollout details:

- production source backup created before pull;
- pull scope verified as source-only:
  - app.py
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production post-restart smoke passed for:

- /login
- /
- /logout
- /fuel/
- /fuel/report
- /spare-parts/
- /wialon
- /wialon/report
- /wialon/workload

## Corrected post-rollout revalidation

After rollout, an extra post-rollout DML revalidation was executed because the first staging validation script in 003C contained a typo in its SQL normalizer helper.

The typo was in the validation utility only, not in application source.

Corrected revalidation result:

- staging revalidation: OK;
- production revalidation: OK;
- /logout DML count: 0;
- /wialon/report/export DML count: 0;
- /wialon/workload/export DML count: 0;
- selected core GET routes DML count: 0;
- no source files modified;
- no service restart performed.

## Production service state after rollout and revalidation

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Notes

Together with AUDIT-GET-SIDE-EFFECT-001 and AUDIT-GET-SIDE-EFFECT-002, confirmed GET audit-log writes have been removed from:

- /wialon/report/export
- /wialon/workload/export
- /logout

Future candidates:

- LOGOUT-POST-001: convert logout from GET to POST with CSRF-safe UI form.
- PERF-REF-001: optimize /ref/equipment query count and response size.
- PERF-WIALON-MAP-001: reduce /wialon/mapping response size and rendering cost.
- CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.
