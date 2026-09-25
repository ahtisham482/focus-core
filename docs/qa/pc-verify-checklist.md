# Focus Core — PC verification checklist

**Run on:** the real Windows 10/11 PC (not the build machine).
**Why:** the tray icon, Windows pop-ups, app-mode window, and .bat
launchers are Windows-only. The code for them is unit-tested on Linux,
but a unit test cannot prove a real tray icon appears or a real
notification pops up. This checklist covers exactly what unit tests
cannot.

**Status legend:** every item is `pending-pc` until a human runs it on
the real PC and ticks pass or fail.

> Tip: do the items in order. Items 1–3 need only the desktop icon;
> item 5 can be done any time.

---

## Item A — Tray icon appears near the clock

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. Double-click the **Focus Core** icon on your desktop.
2. Wait about 10 seconds.
3. Look at the bottom-right corner of the screen, near the clock. If
   you don't see it, click the little up-arrow (^) to show hidden icons.

Expected result: a small dark-green square icon with a green pulse line
(the Focus Core icon) appears near the clock.

---

## Item B — Tray menu actions work

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. **Right-click** the Focus Core icon near the clock. A menu should
   open with: Open Focus Core, Start 25-min focus session,
   Today's Pulse, Back up now, Quit.
2. Click **Open Focus Core** → the Focus Core window should open (or
   come to the front) like a desktop app, no address bar.
3. Right-click the icon again → **Start 25-min focus session** → a
   Windows notification should pop up saying the session started.
4. Right-click the icon again → look at the **Today's Pulse** line. It
   should show your score (e.g. "Today's Pulse: 63.5") or
   "Today's Pulse: -- (no data yet)" if you haven't worked today.
5. Right-click the icon again → **Back up now** → a Windows
   notification should say the backup was saved.
6. Right-click the icon again → **Quit**.

Expected result: every menu action works — window opens, session
starts with a pop-up, pulse line shows a sensible value, backup
notification appears, and Quit closes everything (the tray icon
disappears and no Focus Core window is left).

---

## Item C — Google Drive folder auto-detection

**Status:** pending-pc — `[ ]` pass `[ ]` fail

**Part 1 — with Drive for Desktop installed:**
1. Install Google Drive for Desktop and let it finish signing in (a
   "Google Drive" or "My Drive" folder appears in your home folder).
2. Double-click the **Focus Core** desktop icon.
3. Open the **Backup** page.

Expected result: the page shows your backups go to a
**"Focus Core Backups"** folder inside your Google Drive.

**Part 2 — without Drive:**
1. Quit Google Drive for Desktop (or temporarily rename the
   "Google Drive" folder).
2. Re-open Focus Core and look at the **Backup** page again.

Expected result: the page now shows backups going to the local
`backups` folder next to the app instead. (Restore the Drive folder
name afterwards.)

---

## Item D — Windows notification on goal threshold alert

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. Open the **Goals** page and set a very low daily target for a goal
   you will surely pass (for example: target 5 minutes of "Very
   Distracting" time, which is easy to reach).
2. Double-click `watch-alerts.bat` in the Focus Core folder. A window
   titled "Focus Core Alerts" should open and stay open.
3. Wait until the 5-minute check runs (or do 5 minutes of distracting
   activity to trigger it).

Expected result: a Windows pop-up notification appears with the alert
message. (Note: the check runs every 5 minutes, so it may take a few
minutes. The notification needs the plyer package from requirements.)

---

## Item E — setup.bat is idempotent (run it twice)

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. Double-click `setup.bat` in the Focus Core folder. Wait until it
   prints "Setup finished!" and press a key to close it.
2. **Immediately double-click `setup.bat` again.**

Expected result: the second run also finishes with "Setup finished!"
— no red error text, no crash. On the desktop there is still exactly
**one** "Focus Core" icon (the old "Focus Core Dashboard" icon stays
removed — the script deletes it on every run).

---

## Item F — Start Focus Core.bat opens an app-mode window

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. Close any Focus Core windows and quit the tray icon.
2. Double-click `Start Focus Core.bat` in the Focus Core folder.

Expected result: Focus Core opens in its **own window, like a desktop
app — no address bar, no browser tabs**. Behind it, no black console
window stays open (the launcher uses `pythonw`, which is windowless).

---

## Item G — Focus-session strict blocking overlay

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. Double-click the **Focus Core** desktop icon. Open the **Focus**
   page, start a 25-minute session.
2. Double-click `focus-watch.bat`. A window titled "Focus Core Focus
   Guard" opens and stays open.
3. Open **YouTube** in your browser (rated Distracting, -2).
4. Wait up to 5 seconds.

Expected result: a Windows pop-up plus a **fullscreen "back to work"
reminder** appears. Close the overlay.

5. Open **VS Code** (rated Very Productive, +2). Wait up to 5 seconds.

Expected result: **nothing happens** — no pop-up, no overlay. VS Code
is allowed.

6. When done: end the session on the Focus page, and close the
   "Focus Core Focus Guard" window to stop enforcement.

---

## Item H — Backup restore flow end-to-end

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. Open Focus Core → **Backup** page → click **Back up now**. Note the
   new backup file name (e.g. `focuscore-2026...db`).
2. On the same page, click **Restore** next to that backup.
3. Confirm if it asks. (Since v1.1.0, restore checks the backup's
   SHA256 checksum first — a broken backup is refused.)
4. Reload the **Home** page and check your numbers (Pulse, hours,
   goals) look right.
5. In the Focus Core folder, check that a file named like
   `focuscore.db.pre-restore-2026...` exists — that is the automatic
   **safety copy** of your data from before the restore.

Expected result: data is intact after restore, and the safety copy
file exists.

---

## Item I — Install-from-zip keeps your history

**Status:** pending-pc — `[ ]` pass `[ ]` fail

Steps:
1. In Focus Core, note today's Pulse and a goal's progress (write them
   down).
2. Quit Focus Core (tray icon → Quit).
3. Make a **copy** of the `focuscore.db` file somewhere safe (e.g. your
   Desktop) — just in case.
4. Download a fresh Focus Core zip (from the GitHub release page) and
   **extract it over the existing Focus Core folder**, replacing files
   when asked. Do NOT delete `focuscore.db`.
5. Double-click the **Focus Core** desktop icon again.
6. Check Home: today's Pulse and the goal progress.

Expected result: all history is preserved — Pulse, goals, sessions,
timesheets, and review scores are exactly as before. The zip update
only replaces code, never your data file.
