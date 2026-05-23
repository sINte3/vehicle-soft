@echo off
setlocal

:: ============================================================
::  update.bat
::  Production update helper for Vehicle Soft.
::  Created by TASK-DEPLOY-004.
::
::  What this script does:
::    1. Verifies working directory is C:\transport-report
::    2. Creates a pre-update DB backup (before_update folder)
::    3. Stops the TransportReport NSSM service
::    4. Runs git status and git pull --ff-only origin main
::    5. Runs Python syntax check on all main modules
::    6. Runs application import check
::    7. Prompts operator to confirm no migration is needed
::    8. Starts the TransportReport NSSM service
::
::  MIGRATIONS ARE NOT AUTOMATIC.
::  If this release includes a migration script, do NOT press
::  any key at the migration prompt. Follow docs\MIGRATIONS.md
::  first, then return and press any key to start the service.
::
::  If any step fails, the script exits immediately and leaves
::  the service stopped so the operator can investigate or roll back.
::
::  Rollback instructions: docs\RELEASE_AND_BACKUP_PROCEDURE.md
:: ============================================================

echo ============================================================
echo  Vehicle Soft - Production Update
echo  TASK-DEPLOY-004
echo ============================================================
echo.

:: --- Verify current directory ---
if /i not "%CD%"=="C:\transport-report" (
    echo ERROR: This script must be run from C:\transport-report
    echo.
    echo Current directory: %CD%
    echo.
    echo To fix: open CMD, then run:
    echo    cd C:\transport-report
    echo    update.bat
    echo.
    exit /b 1
)
echo Working directory: %CD%
echo.

set BACKUP_DIR=C:\transport-report-backups\before_update
set SOURCE_DB=%CD%\instance\transport.db
set PYTHON="C:\Program Files\Python314\python.exe"

:: ============================================================
echo STEP 1: Pre-update database backup (SQLite online backup API)
echo.
echo  Backup tool : backup_transport_db.py
echo  Dest dir    : %BACKUP_DIR%
echo.

%PYTHON% "%~dp0backup_transport_db.py" --dest-dir "%BACKUP_DIR%" --suffix before_update
if errorlevel 1 (
    echo.
    echo ERROR: Pre-update backup FAILED.
    echo        Do NOT continue.
    echo        Check disk space, permissions, and backup_transport_db.py output.
    exit /b 1
)
echo Backup created by backup_transport_db.py (SQLite online backup API).
echo.

:: ============================================================
echo STEP 2: Stopping TransportReport service...
echo.
if exist "%CD%\nssm.exe" (
    "%CD%\nssm.exe" stop TransportReport
) else (
    net stop TransportReport
)
echo.

:: ============================================================
echo STEP 3: Current git status:
echo.
git status
echo.

:: ============================================================
echo STEP 4: Pulling latest code from origin/main (fast-forward only)...
echo.
git pull --ff-only origin main
if errorlevel 1 (
    echo.
    echo ERROR: git pull --ff-only failed.
    echo        The service is currently stopped.
    echo.
    echo        Next steps - choose one:
    echo.
    echo        Option A - Fix the git issue and retry:
    echo          After resolving, run update.bat again.
    echo.
    echo        Option B - Roll back and restart service now:
    echo          dir "%BACKUP_DIR%"
    echo          copy /Y "%BACKUP_DIR%\transport_YYYYMMDD_HHMMSS_before_update.db" "%SOURCE_DB%"
    echo          nssm.exe start TransportReport
    echo          (or: net start TransportReport)
    echo.
    echo        See docs\RELEASE_AND_BACKUP_PROCEDURE.md for details.
    exit /b 1
)
echo.

:: ============================================================
echo STEP 5: Python syntax check on all main modules...
echo.
%PYTHON% -m py_compile app.py models.py config.py run_server.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py translations.py
if errorlevel 1 (
    echo.
    echo ERROR: Syntax check FAILED.
    echo        The service is currently stopped.
    echo.
    echo        Next steps:
    echo          1. Fix the syntax error shown above.
    echo          2. Or revert the file: git checkout -- ^<filename^>
    echo          3. Start service when ready:
    echo               nssm.exe start TransportReport
    echo             (or: net start TransportReport)
    exit /b 1
)
echo Syntax check PASSED.
echo.

:: ============================================================
echo STEP 6: Application import check...
echo.
%PYTHON% -c "from app import app; print('APP IMPORT OK')"
if errorlevel 1 (
    echo.
    echo ERROR: Application import FAILED.
    echo        The service is currently stopped.
    echo.
    echo        Next steps:
    echo          1. Read the error above (missing dependency, config issue, etc.).
    echo          2. Or roll back:
    echo               dir "%BACKUP_DIR%"
    echo               copy /Y "%BACKUP_DIR%\transport_YYYYMMDD_HHMMSS_before_update.db" "%SOURCE_DB%"
    echo               git checkout -- .
    echo          3. Start service when ready:
    echo               nssm.exe start TransportReport
    echo             (or: net start TransportReport)
    exit /b 1
)
echo.

:: ============================================================
echo ============================================================
echo  !!! READ BEFORE CONTINUING !!!
echo.
echo  MIGRATIONS ARE NOT AUTOMATIC.
echo.
echo  If this release includes a migration script:
echo    1. DO NOT press any key yet.
echo    2. Open a second CMD window.
echo    3. Follow docs\MIGRATIONS.md exactly:
echo         - stop service (already done)
echo         - back up DB (already done)
echo         - run each migration script one at a time
echo         - verify schema_migrations table
echo    4. Return here and press any key.
echo.
echo  If NO migration is required:
echo    Press any key to start the service.
echo ============================================================
echo.
pause

:: ============================================================
echo STEP 7: Starting TransportReport service...
echo.
if exist "%CD%\nssm.exe" (
    "%CD%\nssm.exe" start TransportReport
) else (
    net start TransportReport
)
if errorlevel 1 (
    echo.
    echo WARNING: Service start reported an error.
    echo         Check logs\error.log and logs\service.log for details.
    echo         Verify SECRET_KEY is set: see docs\DEPLOYMENT_SECURITY.md
    exit /b 1
)

echo.
echo ============================================================
echo  Update complete.
echo.
echo  Pre-update backup : %BACKUP_DIR% (check dir for latest timestamp)
echo  Application URL   : http://10.103.25.200:5050
echo.
echo  Smoke test checklist: docs\QA_CHECKLIST.md
echo ============================================================
exit /b 0
