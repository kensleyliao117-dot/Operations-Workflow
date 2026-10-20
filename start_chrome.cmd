@echo off
setlocal
title Operations Workflow - Chrome
echo Starting project Chrome...
echo Please wait for the browser window and CDP connection check.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0start_chrome.ps1" %*
set "chromeStartupExit=%ERRORLEVEL%"
if not "%chromeStartupExit%"=="0" (
    echo Chrome startup failed. See the error above.
    pause
)
endlocal & exit /b %chromeStartupExit%
