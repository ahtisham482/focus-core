@echo off
REM Re-launch minimized so the running window IS the watcher:
REM closing the window stops the notifications.
if "%1"=="--min" goto :run
start /min "" "%~f0" --min
exit /b 0

:run
title Focus Core Alerts
cd /d "%~dp0"

where python >nul 2>nul
if not %errorlevel%==0 (
  echo Python was not found. Please double-click setup.bat first (one-time setup).
  pause
  exit /b 1
)

echo ==================================================
echo  Focus Core alerts are now ON.
echo.
echo  You will get a Windows pop-up whenever one of
echo  your alert rules triggers (for example, too much
echo  time on distracting sites).
echo.
echo  It checks every 5 minutes. To stop the alerts,
echo  just close this window.
echo ==================================================
echo.
python -m focuscore.alerts --watch --interval 300
pause
