@echo off
setlocal
title Operations Workflow - Frontend
echo Starting frontend...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" -Service Frontend %*
set "startupExit=%ERRORLEVEL%"
if not "%startupExit%"=="0" pause
endlocal & exit /b %startupExit%
