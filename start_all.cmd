@echo off
setlocal
title Operations Workflow - Startup
echo Starting Operations Workflow...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" -Service All %*
set "startupExit=%ERRORLEVEL%"
if not "%startupExit%"=="0" pause
endlocal & exit /b %startupExit%
