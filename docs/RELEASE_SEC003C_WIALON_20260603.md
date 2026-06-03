# RELEASE_SEC003C_WIALON_20260603 - Wialon audit log

Task: TASK-SEC-003C-1
Status: COMPLETED on production
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Service: TransportReport
Database migration: not required

## Scope

Added audit logging for Wialon-related user actions:

- wialon_import_uploaded
- wialon_auto_match_saved
- wialon_mapping_created
- wialon_mapping_updated
- wialon_mapping_deleted
- wialon_engine_hours_exported
- wialon_workload_exported

## Files changed

- wialon_import.py

## Production verification

- py_compile passed for core modules.
- Application import check passed: APP IMPORT OK.
- TransportReport service running after restart.
- Production database backup created successfully before final verification.
- Backup file: D:\transport-report-backups\production\daily\transport_20260602_221500.db
- Backup integrity_check: ok.
- /admin/audit verified manually.
- wialon_mapping_updated event appeared after saving Wialon mapping on production.
- module_required(wialon) decorators restored and verified after regression check.

## Notes

No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
