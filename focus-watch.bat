@echo off
REM Re-launch minimized so the running window IS the guard:
REM closing the window stops the enforcement.
if "%1"=="--min" goto :run
start /min "" "%~f0" --min
exit /b 0

:run
title Focus Core Focus Guard
cd /d "%~dp0"

where python >nul 2>nul
if not %errorlevel%==0 (
  echo Python was not found. Please double-click setup.bat first (one-time setup).
  pause
  exit /b 1
)

echo ==================================================
echo  Focus Core guard is now ON.
echo.
echo  While a focus session is active, opening a
echo  distracting app or site will trigger a Windows
echo  pop-up plus a fullscreen "back to work" reminder,
echo  and the distraction is counted in your session
echo  summary.
echo.
echo  Strict mode blocks Personal (-1) and Distracting
echo  (-2). Lenient mode blocks only Distracting (-2).
echo.
echo  It checks every 5 seconds. To stop the guard,
echo  just close this window.
echo ==================================================
echo.
python -m focuscore.blocker --enforce
pause
