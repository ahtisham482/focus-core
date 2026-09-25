@echo off
title Focus Core Dashboard
cd /d "%~dp0"

where python >nul 2>nul
if not %errorlevel%==0 (
  echo Python was not found. Please double-click setup.bat first (one-time setup).
  echo.
  pause
  exit /b 1
)

echo Collecting today's tracked time...
python -m focuscore.pipeline
echo.
echo Starting your dashboard...
start "Focus Core Server" /min python -m dashboard.app
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5000/
echo.
echo Your dashboard should now be open in the browser.
echo To stop it later, close the black "Focus Core Server" window.
echo.
pause
