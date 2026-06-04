# RELEASE_SEC003F_PERMISSIONS_20260603 - Roles and access control hardening

Task: TASK-SEC-003F
Status: COMPLETED on production
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Service: TransportReport
Database migration: not required

## Scope

Hardened role and module access checks for transport and spare parts actions.

## Changes

- Added transport module permission checks to core transport routes.
- Added transport module permission checks to daily entry, report, and reference routes.
- Hardened spare parts organization access for non-admin users.
- Non-admin spare parts users can only see and create requests for accessible organizations.
- Non-admin spare parts users can only select equipment from accessible organizations.
- Non-admin spare parts users cannot open requests from inaccessible organizations by direct URL.
- Spare parts approve/reject actions are now admin-only.
- Spare parts detail template now shows approve/reject panel only for admin users.

## Files changed

- app.py
- spare_parts.py
- templates/spare_part_detail.html

## Production verification

- py_compile passed for core modules.
- Application import check passed: APP IMPORT OK.
- TransportReport service running after restart.
- Production database backup created successfully before deployment.
- Backup file: D:\transport-report-backups\production\daily\transport_20260603_120118.db
- Backup integrity_check: ok.
- SEC003F production smoke test passed.
- Admin access verified.
- Operator module restrictions verified.
- test_operator_prod zero-module access restrictions verified.

## Notes

No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
