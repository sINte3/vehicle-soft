# BACKUP_RESTORE_TEST_20260604

Status: PASSED
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Test folder: C:\transport-report-restore-test

## Purpose

Verify that a production SQLite backup can be restored into an isolated test folder and that the application imports successfully against the restored database.

## Source backup

Backup restored:

D:\transport-report-backups\production\daily\transport_20260604_104248.db

## Restore target

Restored database path:

C:\transport-report-restore-test\instance\transport.db

Restored DB size:

51,245,056 bytes

## Commands executed

### Create isolated restore folder

- cd /d C:\
- if exist C:\transport-report-restore-test rmdir /s /q C:\transport-report-restore-test
- mkdir C:\transport-report-restore-test

### Copy production code without production runtime data

robocopy C:\transport-report C:\transport-report-restore-test /E /XD .git __pycache__ instance logs /XF *.pyc *.db *.zip data001_*.txt sec003*.txt audit_*.txt /NFL /NDL /NJH /NJS /NP

### Restore latest production backup

The latest production backup was copied to:

C:\transport-report-restore-test\instance\transport.db

Output:

RESTORE_DB=D:\transport-report-backups\production\daily\transport_20260604_104248.db

### Integrity check

Output:

INTEGRITY=ok
TABLES=32

### Restore app import check

Output:

RESTORE APP IMPORT OK

## Result

The restore test passed. The backup database was copied into an isolated restore folder, SQLite integrity check returned ok, the restored database contains 32 tables, and the application imported successfully against the restored code/database set.

## Notes

- Production service was not modified during this restore test.
- Restore test was performed outside C:\transport-report and outside staging.
- This test proves that the backup file is usable for application recovery at import/database integrity level.
- For full disaster recovery, the next step would be a temporary service startup on a non-production port, if required.
