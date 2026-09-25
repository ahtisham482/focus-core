@echo off
title Focus Core Setup
echo ===============================================
echo  Focus Core - one-time setup
echo ===============================================
echo.

REM --- Step 1: find Python, install it automatically if missing ---
where python >nul 2>nul
if %errorlevel%==0 goto :have_python

echo Python was not found. Trying to install it automatically...
where winget >nul 2>nul
if not %errorlevel%==0 goto :no_winget

winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
if not %errorlevel%==0 goto :py_failed

set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
where python >nul 2>nul
if %errorlevel%==0 goto :have_python
goto :py_failed

:no_winget
echo.
echo I could not install Python automatically on this PC.
echo Please open the Microsoft Store, search for "Python 3.12",
echo install it, then double-click this setup file again.
echo.
pause
exit /b 1

:py_failed
echo.
echo The automatic Python install did not work.
echo Please install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" during install),
echo then double-click this setup file again.
echo.
pause
exit /b 1

:have_python
echo Found Python. Installing the needed packages (one time, may take a minute)...
python -m pip install --quiet -r "%~dp0requirements.txt"
if not %errorlevel%==0 (
  echo.
  echo Something went wrong while installing packages.
  echo Please take a screenshot of this window and send it to Merlin.
  echo.
  pause
  exit /b 1
)

REM --- Step 2: desktop shortcut ---
echo Creating a desktop shortcut...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $desk = $ws.SpecialFolders('Desktop'); $old = [IO.Path]::Combine($desk, 'Focus Core Dashboard.lnk'); if (Test-Path $old) { Remove-Item $old -Force }; $sc = $ws.CreateShortcut([IO.Path]::Combine($desk, 'Focus Core.lnk')); $sc.TargetPath = '%~dp0Start Focus Core.bat'; $sc.WorkingDirectory = '%~dp0'; $sc.Save()"

echo.
echo ===============================================
echo  Setup finished!
echo.
echo  From now on, just double-click
echo  "Focus Core" on your desktop.
echo.
echo  It opens Focus Core in its own window
echo  (like a desktop app) and puts a small icon
echo  near the Windows clock with quick actions.
echo.
echo  Your old "Focus Core Dashboard" icon was
echo  replaced. run-dashboard.bat still works as
echo  a fallback if you ever need it.
echo ===============================================
echo.
pause
