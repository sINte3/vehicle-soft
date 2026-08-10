@echo off
REM Obertka dlya cmd.exe: sam skript vhoda napisan na PowerShell.
REM
REM [REASON]: UI-P9. Konsol na servere po umolchaniyu -- cmd, i tam
REM `.\login-ui-p9.ps1` ne zapuskaetsya vovse: cmd ne ispolnyaet .ps1.
REM -ExecutionPolicy Bypass nuzhen vtoroy prichinoy: fayl, prinesennyy cherez
REM brauzer, neset metku zony, i politika RemoteSigned blokiruet ego MOLCHA.
REM
REM Zapusk:  cd C:\qa
REM          login-ui-p9.bat

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0login-ui-p9.ps1"
exit /b %ERRORLEVEL%
