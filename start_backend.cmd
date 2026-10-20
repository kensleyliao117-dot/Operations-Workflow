@echo off
setlocal
title Operations Workflow - Backend
echo Starting backend...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" -Service Backend %*
set "startupExit=%ERRORLEVEL%"
if not "%startupExit%"=="0" pause
endlocal & exit /b %startupExit%
