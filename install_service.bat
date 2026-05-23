@echo off
echo =============================================
echo  Install as Windows Service (NSSM)
echo =============================================
echo.
echo Requires NSSM (Non-Sucking Service Manager):
echo Download from https://nssm.cc/download
echo Place nssm.exe in this folder or C:\nssm\
echo.

set SERVICE_NAME=TransportReport
set APP_DIR=%~dp0

REM Find Python full path
for /f "tokens=*" %%i in ('where python 2^>nul') do set PYTHON_PATH=%%i
if "%PYTHON_PATH%"=="" (
    echo ERROR: Python not found in PATH!
    pause
    exit /b 1
)
echo Found Python: %PYTHON_PATH%

REM Find NSSM
set NSSM=
where nssm >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%i in ('where nssm') do set NSSM=%%i
)
if "%NSSM%"=="" if exist "%APP_DIR%nssm.exe" set NSSM=%APP_DIR%nssm.exe
if "%NSSM%"=="" if exist "C:\nssm\nssm.exe" set NSSM=C:\nssm\nssm.exe
if "%NSSM%"=="" (
    echo ERROR: NSSM not found!
    echo Download from https://nssm.cc/download
    echo Place nssm.exe in: %APP_DIR%
    pause
    exit /b 1
)
echo Found NSSM: %NSSM%
echo.

REM Install requirements
echo Step 1: Installing dependencies...
pip install -r "%APP_DIR%requirements.txt"
echo.

REM Initialize DB if needed
if not exist "%APP_DIR%instance\transport.db" (
    echo Step 2: Initializing database...
    python "%APP_DIR%init_data.py"
    echo.
)

REM Create logs folder
if not exist "%APP_DIR%logs" mkdir "%APP_DIR%logs"

REM Remove old service if exists
%NSSM% stop %SERVICE_NAME% >nul 2>&1
%NSSM% remove %SERVICE_NAME% confirm >nul 2>&1

REM Install service
echo Step 3: Installing service %SERVICE_NAME%...
%NSSM% install %SERVICE_NAME% "%PYTHON_PATH%" "%APP_DIR%run_server.py"
%NSSM% set %SERVICE_NAME% AppDirectory "%APP_DIR%"
%NSSM% set %SERVICE_NAME% DisplayName "Bukhoro Agrocluster - Transport Report"
%NSSM% set %SERVICE_NAME% Description "Daily transport work reporting system"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppEnvironmentExtra "FLASK_ENV=sqlite_prod" "PORT=5050"

REM Logging
%NSSM% set %SERVICE_NAME% AppStdout "%APP_DIR%logs\service.log"
%NSSM% set %SERVICE_NAME% AppStderr "%APP_DIR%logs\error.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 1048576

echo.
echo Step 4: Starting service...
%NSSM% start %SERVICE_NAME%

echo.
echo =============================================
echo  Done! Service %SERVICE_NAME% installed.
echo  Address: http://localhost:5050
echo  Login: admin / Password: admin123
echo.
echo  Management commands:
echo    nssm start %SERVICE_NAME%
echo    nssm stop %SERVICE_NAME%
echo    nssm restart %SERVICE_NAME%
echo    nssm remove %SERVICE_NAME% confirm
echo =============================================
pause
