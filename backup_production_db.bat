@echo off
cd /d C:\transport-report
"C:\Program Files\Python314\python.exe" backup_transport_db.py --source "C:\transport-report\instance\transport.db" --dest-dir "D:\transport-report-backups\production\daily"
if errorlevel 1 (
    echo Backup FAILED. See backup_transport_db.py output above.
    exit /b 1
)
echo Backup completed successfully.
exit /b 0