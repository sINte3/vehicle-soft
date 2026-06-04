# RELEASE_QA_BACKUP_20260604 - QA checklist and backup restore test

Task: QA001 + BACKUP001
Status: COMPLETED
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Database migration: not required

## Scope

Documented formal release QA checklist and executed a real production backup restore test in an isolated folder.

## Files added

- docs/QA_CHECKLIST.md
- docs/BACKUP_RESTORE_TEST_20260604.md
- docs/RELEASE_QA_BACKUP_20260604.md

## Restore test result

- Restore folder: C:\transport-report-restore-test
- Source backup: D:\transport-report-backups\production\daily\transport_20260604_104248.db
- Restored DB: C:\transport-report-restore-test\instance\transport.db
- Restored DB size: 51,245,056 bytes
- SQLite integrity_check: ok
- Table count: 32
- Restore app import: RESTORE APP IMPORT OK

## QA checklist coverage

- Pre-release code checks
- Production backup requirement
- Login/Auth smoke test
- Module permissions smoke test
- Transport module smoke test
- Reference directory smoke test
- Wialon smoke test
- Fuel smoke test
- Spare parts smoke test
- Deficiencies smoke test
- Audit log smoke test
- Validation UX smoke test
- Post-release Git checks
- Rollback rule

## Notes

No application code was changed. No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
