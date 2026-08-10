@echo off
REM Obertka dlya cmd.exe: sam skript vhoda napisan na PowerShell.
REM
REM [REASON]: UI-P9. Konsol na servere po umolchaniyu -- cmd, i tam
REM `.\login-ui-p9.ps1` ne zapuskaetsya vovse: cmd ne ispolnyaet .ps1.
REM -ExecutionPolicy Bypass nuzhen vtoroy prichinoy: fayl, prinesennyy cherez
REM brauzer, neset metku zony, i politika RemoteSigned blokiruet ego MOLCHA.
REM
REM [REASON]: preflight nizhe -- ne akkuratnost, a ekonomiya krugov. Kazhdaya
REM nedostayushchaya chast (vtoroy fayl, node, playwright) davala soobshchenie
REM ot chuzhoy programmy, po kotoromu ne vidno, chego imenno net i gde eto
REM vzyat. Zdes kazhdyy sluchay nazvan i k nemu dana komanda.
REM
REM Zapusk:  cd C:\qa
REM          login-ui-p9.bat

setlocal

set "QA=%~dp0"
set "QADISP=%QA:~0,-1%"
set "REPO=C:\transport-report-staging"
set "MISS="

if not exist "%QA%login-ui-p9.ps1"   set "MISS=%MISS% login-ui-p9.ps1"
if not exist "%QA%login_staging.mjs" set "MISS=%MISS% login_staging.mjs"

if defined MISS (
  echo.
  echo NET FAYLOV:%MISS%
  echo.
  echo Vzyat ih iz rabochey kopii na etom zhe servere:
  echo     copy /Y %REPO%\tools\ux\login-ui-p9.ps1 %QADISP%
  echo     copy /Y %REPO%\tools\ux\login_staging.mjs %QADISP%
  echo.
  echo Esli v rabochey kopii ih eshche net -- snachala obnovit ee:
  echo     cd %REPO%
  echo     git fetch origin main
  echo     git merge --ff-only origin/main
  echo.
  exit /b 2
)

where node >nul 2>&1
if errorlevel 1 (
  echo.
  echo NE NAYDEN node v PATH etoy konsoli.
  echo Otkroyte konsol, v kotoroy rabotaet agent, ili dobavte Node v PATH.
  echo Proverit:  where node
  echo.
  exit /b 3
)

if not exist "%QA%node_modules\playwright" (
  echo.
  echo NET %QADISP%\node_modules\playwright
  echo Sessiyu vypustit nechem. Ustanovka v etom zhe kataloge:
  echo     cd %QADISP%
  echo     npm install playwright
  echo Brauzery uzhe stoyat -- `npx playwright install` zapuskat NE nado.
  echo.
  exit /b 4
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%QA%login-ui-p9.ps1"
exit /b %ERRORLEVEL%
