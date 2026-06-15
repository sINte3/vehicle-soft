# RELEASE SPARE001F  Final spare parts QA closure

Date: 2026-06-15

## Status

Completed on staging and production.

## Base commit before docs

`2740519babb05dd7d640307a0c87f1d2d6fa62b1`  `Document SPARE001E operator workflow test`

## Scope

SPARE001F was the final read-only QA closure check for the spare parts module after:

- SPARE001A template UX
- SPARE001B status history audit/backfill
- SPARE001C controlled staging workflow test
- SPARE001D role/access audit and permission enablement
- SPARE001E controlled operator workflow test on staging

No source code was changed.

No DB writes were performed.

No POST requests were executed.

No service restart was performed.

## Git state

Both environments were synchronized on:

`2740519babb05dd7d640307a0c87f1d2d6fa62b1`

Staging:

- HEAD = expected commit
- origin/main = expected commit
- Git working tree clean

Production:

- HEAD = expected commit
- origin/main = expected commit
- Git working tree clean

## Services

Staging services were RUNNING:

- `TransportReportStaging`
- `TransportBotStaging`
- `TransportBot003Staging`

Production services were RUNNING:

- `TransportReport`
- `TransportBot`
- `TransportBot003`

No service restart was performed.

## Staging final QA result

Final validation marker:

`STAGING_SPARE001F_FINAL_QA_OK=YES`

Staging table counts:

- users: 7
- user_module_permissions: 30
- organizations: 17
- equipment: 336
- spare_part_requests: 12
- spare_part_request_items: 12
- spare_part_status_history: 15
- audit_logs: 751
- bot003_notification_outbox: 8

Staging active operator permissions:

- `muhiddin`: spare_parts_access=1, org_count=17
- `abdugani`: spare_parts_access=1, org_count=17
- `mirfayz`: spare_parts_access=1, org_count=17
- `sardor`: spare_parts_access=1, org_count=17

Staging request statuses:

- approved: 2
- rejected: 1
- submitted: 9

Staging status history coverage:

- all submitted requests have submitted history
- all approved requests have approved history
- all rejected requests have rejected history
- coverage errors: 0

Staging BOT003 outbox:

- `spare_request_submitted`: sent = 6
- `spare_request_approved`: sent = 1
- `spare_request_rejected`: sent = 1
- pending rows: 0
- failed rows: 0

Staging route checks:

- admin `/spare-parts/`: OK
- admin `/spare-parts/new`: OK
- admin `/spare-parts/catalog`: OK
- operator `/spare-parts/`: OK
- operator `/spare-parts/new`: OK
- operator detail page: OK
- operator `/spare-parts/catalog`: 403 Forbidden
- unauthenticated users: redirected to login

Staging final errors:

- permission_errors: 0
- coverage_errors: 0
- outbox_errors: 0
- route_errors: 0
- final_errors: 0

## Production final QA result

Final validation marker:

`PRODUCTION_SPARE001F_FINAL_QA_OK=YES`

Production table counts:

- users: 7
- user_module_permissions: 30
- organizations: 17
- equipment: 336
- spare_part_requests: 3
- spare_part_request_items: 3
- spare_part_status_history: 4
- audit_logs: 1110
- bot003_notification_outbox: 1

Production active operator permissions:

- `muhiddin`: spare_parts_access=1, org_count=17
- `abdugani`: spare_parts_access=1, org_count=17
- `mirfayz`: spare_parts_access=1, org_count=17
- `sardor`: spare_parts_access=1, org_count=17

Production request statuses:

- approved: 1
- submitted: 2

Production status history coverage:

- all submitted requests have submitted history
- all approved requests have approved history
- coverage errors: 0

Production BOT003 outbox:

- `spare_request_submitted`: sent = 1
- pending rows: 0
- failed rows: 0

Production route checks:

- admin `/spare-parts/`: OK
- admin `/spare-parts/new`: OK
- admin `/spare-parts/catalog`: OK
- operator `/spare-parts/`: OK
- operator `/spare-parts/new`: OK
- operator detail page: OK
- operator `/spare-parts/catalog`: 403 Forbidden
- unauthenticated users: redirected to login

Production final errors:

- permission_errors: 0
- coverage_errors: 0
- outbox_errors: 0
- route_errors: 0
- final_errors: 0

## Final access model confirmed

Admin:

- can open spare parts list
- can create requests
- can open request details
- can approve/reject requests
- can open catalog
- can manage catalog

Operator:

- can open spare parts list
- can create requests
- can open request details for assigned organizations
- cannot open catalog
- cannot approve/reject requests

Unauthenticated user:

- redirected to login

## Final result

SPARE001F is complete.

The spare parts module QA cycle is closed for the current scope.

Current spare parts module status:

- UX templates updated
- status history restored for historical data
- new workflow status history confirmed
- BOT003 notification delivery confirmed
- active operators have access
- admin-only catalog restriction confirmed
- operator workflow confirmed
- staging and production final QA passed
