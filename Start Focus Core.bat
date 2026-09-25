@echo off
REM ============================================================
REM  Focus Core -- double-click this (or the desktop icon).
REM
REM  It starts everything quietly (no black window) and opens
REM  Focus Core in its own app window, like a desktop app.
REM  A small icon also appears near the Windows clock with
REM  quick actions: open Focus Core, start a focus session,
REM  back up now, quit.
REM ============================================================
cd /d "%~dp0"

where python >nul 2>nul
if not %errorlevel%==0 (
  powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Python was not found. Please double-click setup.bat first (one-time setup).', 'Focus Core')"
  exit /b 1
)

start "" pythonw -m focuscore.launcher
