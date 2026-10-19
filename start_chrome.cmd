@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_chrome.ps1" %*
set "chromeStartupExit=%ERRORLEVEL%"
if not "%chromeStartupExit%"=="0" pause
endlocal & exit /b %chromeStartupExit%
