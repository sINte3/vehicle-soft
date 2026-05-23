@echo off
setlocal

:: ============================================================
::  backup_transport_db.bat
::  Daily backup wrapper for the production SQLite database.
::  Uses backup_transport_db.py with sqlite3.Connection.backup().
::  Does NOT stop the service.
::  Does NOT delete old backups.
:: ============================================================

echo ============================================================
echo  Transport DB Daily Backup
echo ============================================================
echo.

"C:\Program Files\Python314\python.exe" "%~dp0backup_transport_db.py"
if errorlevel 1 (
    echo.
    echo Backup FAILED. See backup_transport_db.py output above.
    exit /b 1
)

echo.
echo Backup completed successfully.
exit /b 0
