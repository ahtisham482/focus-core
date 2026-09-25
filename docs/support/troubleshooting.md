# Focus Core — Troubleshooting

Simple English. Each entry: **symptom → likely cause → fix.**

## ActivityWatch not running / tray icon missing

**Symptom:** Home shows no data, or "Tracker isn't sending data".

**Cause:** ActivityWatch is the tracker that feeds Focus Core. If it is
not running, there is no data to show.

**Fix:**
1. Look near the Windows clock for the ActivityWatch icon. If it is
   missing, open ActivityWatch from the Start menu.
2. ActivityWatch should start with Windows by itself. If it keeps
   going missing, open ActivityWatch's own settings and turn on
   "start on login".

*(The Focus Core tray icon — the small green icon near the clock —
needs the pystray + Pillow packages. If it never appears after
launching, double-click `setup.bat` again to reinstall them. If it
still doesn't show, you can run everything through the app window;
the tray is a convenience, not a requirement.)*

## Port 5000 already in use

**Symptom:** Focus Core won't open, or you see "The Focus Core server
did not start."

**Cause:** something else is already using port 5000 (often a second
copy of Focus Core left running).

**Fix:**
1. Right-click the Focus Core tray icon → **Quit** (this stops the
   server the tray started).
2. If you used `run-dashboard.bat`: close the black "Focus Core
   Server" window.
3. Double-click the **Focus Core** desktop icon again.

## Backup stale or missing warnings

**Symptom:** Home shows "your newest backup is older than 7 days" or
"no backups yet".

**Cause:** Focus Core auto-backs-up on launch only if the newest backup
is older than 24 hours. The warning fires after 7 days without one.

**Fix:**
1. Open the **Backup** page and click **Back up now**.
2. Check the backup location shown on the page: if you have Google
   Drive for Desktop installed, backups go to the "Focus Core Backups"
   folder in your Drive; otherwise to the `backups` folder next to the
   app.
3. If backups never appear even after clicking, look for a red error
   message on the Backup page and send a screenshot to Merlin.

## Dashboard won't start — Python missing

**Symptom:** a pop-up says "Python was not found. Please double-click
setup.bat first."

**Cause:** Python is not installed, or `setup.bat` was never run on
this PC.

**Fix:**
1. Double-click `setup.bat` and let it finish (it can install Python
   3.12 automatically on Windows 10/11).
2. If it says it could not install Python: open the Microsoft Store,
   search "Python 3.12", install it, then double-click `setup.bat`
   again.
3. Then double-click the **Focus Core** desktop icon.

## Restore failures / checksum mismatch (new in v1.1.0)

**Symptom:** clicking **Restore** fails with an error about the backup
being "corrupt", "modified", or a SHA256 checksum problem.

**Cause:** every backup since v1.1.0 carries a checksum fingerprint.
Before restoring, Focus Core verifies the backup still matches it. A
mismatch means the file is damaged or was changed (e.g. a failed Drive
sync) — restoring it could corrupt your data, so it is refused.

**Fix:**
1. Your current data is **not** touched — restore never overwrites
   before verifying.
2. Pick the next-newest backup and restore that instead.
3. If all recent backups fail: your live `focuscore.db` in the app
   folder is still fine — keep using it, and click **Back up now** to
   make a fresh, healthy backup.

*(Restoring always saves an automatic safety copy named
`focuscore.db.pre-restore-<date-time>` next to `focuscore.db`, so even
a successful restore can be undone by renaming the safety copy back to
`focuscore.db` while Focus Core is closed.)*

## Windows notifications not appearing

**Symptom:** tray actions or alerts run, but no pop-up ever appears.

**Cause:** either the `plyer` package is missing (it sends the
notifications), or Windows has notifications turned off for Focus
Core, or — for alerts — the watcher isn't running.

**Fix:**
1. Double-click `setup.bat` again (it installs `plyer`).
2. For goal alerts: keep `watch-alerts.bat` running — it checks every
   5 minutes. Closing its window stops the pop-ups.
3. Windows Settings → System → Notifications → make sure
   notifications are turned on for the app.

## Google Drive folder not detected

**Symptom:** the **Backup** page says backups go to the local
`backups` folder even though Drive for Desktop is installed.

**Cause:** Focus Core looks for a folder named "Google Drive" or "My
Drive" directly in your Windows user folder. It cannot find Drive if
the folder has a different name, sync hasn't finished, or Drive isn't
signed in.

**Fix:**
1. Open File Explorer and check you can see "Google Drive" (or "My
   Drive") in your user folder and it is signed in and synced.
2. Restart Focus Core after Drive finishes signing in — detection
   happens at launch.

## "Server did not start" / app opens to a blank page

**Symptom:** the app window opens but is blank, or a message box says
the server did not start.

**Cause:** the dashboard server crashed on launch (missing package, or
port 5000 blocked).

**Fix:**
1. Double-click `setup.bat` again to reinstall packages.
2. If it persists: run `run-dashboard.bat` instead — its black window
   shows the real error message. Take a screenshot and send it to
   Merlin.
