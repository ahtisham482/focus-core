# focus-core (Phase 5)

A local-first, generic RescueTime-style tracking core. Phase 5 makes it
feel like a real desktop app: one-click launcher, guided home page,
welcome tour, system tray icon, automatic backups, and a polished
interface -- on top of the foundation (automatic activity capture,
categories, the exact productivity scoring system, goals, alerts, focus
sessions with blocking, timesheets, weekly reports, and focus coaching).

Everything runs on your own computer. No accounts, no cloud, no tracking
of you by anyone else. Backups can optionally sync through your own
Google Drive, so your data survives a lost or replaced laptop.

## Super simple setup (Windows, no experience needed)

You only do this once. There are 3 steps and each one is just double-clicking:

1. **Install ActivityWatch** (the tracker): open
   https://activitywatch.net/downloads/ in your browser and click the
   **Windows** download. Run the downloaded file and keep clicking Next.
   When it asks, let it start with Windows. (Optional but recommended:
   also install the ActivityWatch extension for Chrome or Firefox so it
   can see website names.)
2. **Download focus-core.zip** (the link Merlin gave you), right-click it
   and choose **Extract All**, then open the extracted folder.
3. **Double-click `setup.bat`** inside the folder. It installs everything
   by itself and puts a **"Focus Core"** icon on your desktop.

From then on: whenever you want to see your score, double-click the
**"Focus Core"** desktop icon. Focus Core opens in its own window (like
a real desktop app), and a small icon appears near the Windows clock
with quick actions: open Focus Core, start a 25-minute focus session,
back up now, quit.

If anything shows an error, take a screenshot or photo of the window and
send it to Merlin — he will fix it.

## Update (getting a new version)

New versions come from the **GitHub Releases** page (no more expiring
download links):

1. Open the Releases page and download the newest `focus-core.zip`.
2. Check that `focuscore.db` is still in your Focus Core folder — never
   delete it; it holds all your history.
3. Right-click the zip → **Extract All** → extract into your Focus Core
   folder, choosing **Replace the files**.
4. Double-click `setup.bat` again and wait for it to finish.

Your data and settings are kept. The installer backs nothing over them.

## Manual setup (for developers)

## What Phase 1 includes

- **Capture** -- reads tracked apps, window titles and browser tab URLs from
  ActivityWatch (free, open source) through its local API. Idle/AFK time is
  measured separately and excluded from scoring.
- **Categories** -- generic top-level categories mirroring RescueTime
  (Business, Communication & Scheduling, Social Networking,
  Design & Composition, Entertainment, News, Software Development,
  Reference & Learning, Shopping, Utilities), each with a default score.
  ~100 popular apps/sites are pre-seeded (e.g. YouTube = Very Distracting,
  GitHub = Very Productive). You can add custom sub-categories.
- **Scoring** -- the exact RescueTime mechanics: every activity scores
  +2 (Very Productive) to -2 (Very Distracting), inherited from its
  category, but you can override any single activity. The daily
  Productivity Pulse (0-100) uses the official weighted formula:
  `((vd*0 + d*1 + n*2 + p*3 + vp*4) / (total*4)) * 100`.
- **Uncategorized queue** -- anything unmatched counts as Neutral in the
  Pulse but is listed for you to review and score.
- **Dashboard** -- local web page with the day's Pulse, score breakdown,
  categories, the uncategorized queue, and per-activity score overrides.

## What is NOT in focus-core (deliberately out of scope)

Network-level blocking (editing the hosts file), music during sessions,
phone enforcement, invoicing, and automatic report emails. The weekly
report lives on the dashboard instead of email because no email setup
exists.

## Phase 2: goals and alerts

**Goals** are daily targets you set for yourself. Each goal is either
"more than X" (do at least this much) or "less than Y" (stay under this).
The target can be time in a category or your Productivity Pulse score.
Examples:

- More than 120 minutes of Software Development per day.
- Less than 30 minutes of Entertainment per day.
- Productivity Pulse of at least 70.

Your progress is **live**: the Today page re-reads today's tracked data
every time it loads, and the page refreshes itself every 5 minutes. Goals
you "pin" appear at the top of the Today page. Manage everything on the
**Goals** page in the dashboard.

**Alerts** watch one category (e.g. Entertainment) or one activity
(e.g. `domain:youtube.com`) and pop up a Windows notification when today's
time on it reaches your threshold. Each alert waits a "cooldown" period
before it can fire again, so it never spams you. Manage them on the
**Alerts** page.

For real-time desktop pop-ups, double-click **`watch-alerts.bat`**
(on Windows). It checks every 5 minutes. Close its window to stop the
notifications.

You can also check once from a terminal:

```
python -m focuscore.alerts --check
```

**Not in Phase 2:** webpage actions when an alert fires and automatic
focus sessions (those come with Phase 3: focus sessions + blocking).

## Phase 3: focus sessions and blocking

A **focus session** is a timed work block. You give it a label
("Deep work") and a duration (25, 50, 90 minutes, or any custom length),
watch the countdown, and get a summary when it ends: focus minutes,
Pulse for the session, how many distractions were blocked, and planned
time vs actual time. Every completed session also builds your **focus
streak** -- the number of days in a row with at least one completed
session, shown as a badge on the Focus page.

**Blocking:** while a session is active, opening something distracting
triggers a Windows pop-up plus a fullscreen "back to work" reminder,
and the distraction is counted. Strict mode blocks scores -1 (Personal)
and -2 (Distracting); lenient mode blocks only -2. An activity you
personally re-scored as productive is never blocked -- your override
always wins.

How to use it:

1. Open the dashboard and go to **Focus**, or run:
   `python -m focuscore.focus start --label "Deep work" --minutes 50`
2. Double-click **focus-watch.bat** (it guards the session: checks what
   you are doing every 5 seconds). Closing its window stops the guard.
3. Work. When the time is up, end the session from the dashboard
   (or `python -m focuscore.focus end`) to see your summary.

**Not in Phase 3:** network-level blocking (editing the hosts file),
music during sessions, and phone enforcement.

## Phase 4: timesheets, weekly report, and coaching

**Timesheets** turn your tracked day into billable-style time entries.
The **Timesheet** page suggests blocks for the day: consecutive tracked
activities in the same category are merged into one block (a gap of more
than 5 minutes starts a new block), and blocks shorter than 5 minutes
are skipped as noise. You can then:

- **Accept** a suggested block, optionally tagging it with a project,
  client, task, and note.
- **Edit or delete** entries, or **add** an entry by hand for time the
  tracker missed (use `YYYY-MM-DDTHH:MM` for start/end, e.g.
  `2026-09-25T09:00`).
- Manage **projects** (name + client) and link entries to them.
- **Lock** a day when you are done -- locked entries cannot be edited,
  deleted, or added to. Locking is permanent.
- **Export** any day (or date range) to CSV with the columns
  `date,start,end,minutes,category,app,project,client,task,note,status`.

From a terminal you can also run:

```
python -m focuscore.timesheet suggest --day 2026-09-25
python -m focuscore.timesheet export --from 2026-09-21 --to 2026-09-27 --out week.csv
```

**Weekly report** (the **Report** page) summarizes any week,
Monday to Sunday: total tracked hours, the average Pulse (days with no
data are skipped, not counted as zero), per-day hours/Pulse/focus hours,
your 5 biggest categories, each goal's hit-rate (days hit out of days
with tracked data), and how many focus sessions you completed with
their focus minutes and blocked distractions. There is no email --
the report is a dashboard page.

**Coaching** (the **Coaching** page) shows you *when* you focus best:

- A **7-day hourly heatmap**: each cell is the Pulse for that hour of
  that day. Green means focused, red means distracted.
- An **average day** view: all 7 days combined, so you can see when your
  focus usually peaks.
- Your **best 2-hour focus windows** (top 3, never overlapping), ranked
  by focused minutes -- protect these hours.
- **Warnings** from four simple, documented rules:
  - *Late nights*: 30+ minutes of activity at 23:00 or later, on 3 or
    more days.
  - *Marathon days*: any single day with more than 10 tracked hours.
  - *Distraction creep*: your distracting-time share rose more than 10
    percentage points vs the previous 7 days (needs 14 days of data,
    otherwise it stays silent).
  - *Low recovery*: average Pulse below 40 while tracking more than 30
    hours.

All coaching thresholds are named constants at the top of
`focuscore/coaching.py` -- no hidden magic, no machine learning, just
plain arithmetic over your tracked data.

## Phase 5: the real app experience

**One click to open.** Double-click the **Focus Core** desktop icon (or
`Start Focus Core.bat`). It starts everything quietly -- no black
window -- waits for the server, then opens Focus Core in its own window
with no address bar, like a desktop app. A small tray icon near the
Windows clock gives you quick actions: open Focus Core, start a 25-min
focus session, see today's Pulse, back up now, quit. If the server is
already running, it just opens the window. The old `run-dashboard.bat`
still works as a fallback.

**Guided home page.** The home page is now a command center: today's
Pulse (big, color-coded: green 60+, amber 40-59, red below 40), tracked
and focused hours, and a **"What needs your attention"** list. Each item
has exactly one button: uncategorized activities to review, yesterday's
timesheet to finalize, a focus session to start, a goal at risk, the
tracker not sending data (with 2 plain steps to fix it), or backups
getting old. Nothing to do? It says "All clear."

**Welcome tour.** The first run shows 3 short screens: what the tracker
does, how scoring works, and how to teach it your activities. A "Take
the tour again" link sits in the footer of every page.

**Automatic backups.** Every time you start Focus Core, it quietly
makes a backup if the newest one is older than 24 hours (using SQLite's
safe backup API, so it works while the app is running). If Google Drive
for Desktop is installed, backups go to a "Focus Core Backups" folder in
your Drive and sync to your Google account -- so they are waiting on a
new laptop. Otherwise they go to a `backups` folder next to the app.
The **Backup** page shows where backups go, when the last one was made,
lets you back up now, lists all backups, and restores any of them (your
current data is first copied to a safety file, so a restore can never
lose anything). The home page nudges you when the newest backup is
older than 7 days. Moving to a new laptop is 2 steps -- see
USER_GUIDE.md.

**Polished interface.** One shared stylesheet, no internet needed: cards,
stat tiles, a visual timesheet timeline (click a block to highlight its
row), CSS-only bar charts on the report, hover tooltips on the coaching
heatmap, and a layout that works on a phone browser too. Everything
works with JavaScript disabled.

**Phone access (optional).** The server only listens on your own PC by
default. To open Focus Core from your phone on the same Wi-Fi, start it
with `python -m dashboard.app --host 0.0.0.0` -- it prints a warning
that anyone on your Wi-Fi could then open it. Your PC's firewall may
ask for permission the first time.

**Not in Phase 5:** cloud hosting of the app (it stays on your PC --
that is what makes it fast, free, and offline-capable), automatic email
reports, invoicing.

## Install

1. Install Python 3.10 or newer.
2. Install ActivityWatch on this computer (https://activitywatch.net) and
   make sure it is running. For browser tab URLs, also install the
   ActivityWatch browser extension.
3. In this folder, run:

```
pip install -r requirements.txt
```

## Run the pipeline

Process one day (default: today) from ActivityWatch:

```
python -m focuscore.pipeline --day 2026-09-25
```

Try everything without ActivityWatch installed, using synthetic demo data:

```
python -m focuscore.pipeline --day 2026-09-25 --demo
```

Re-running for a day is safe: it replaces that day's stored events, so
score overrides you set in the dashboard apply retroactively.

## Open the dashboard

```
python -m dashboard.app
```

Then open http://127.0.0.1:5000 in your browser.

- `/` -- home: today's Pulse, key stats, "what needs your attention"
- `/welcome` -- first-run tour (3 short screens)
- `/day/2026-09-25` -- any stored day
- `/activities?day=2026-09-25` -- every tracked activity with a score
  override dropdown per row
- `/goals` -- manage daily goals
- `/alerts` -- manage threshold alerts
- `/focus` -- start/end focus sessions, streaks
- `/timesheet?day=2026-09-25` -- suggested blocks, visual timeline,
  entries, projects, lock day, CSV export
- `/report?week=2026-09-21` -- weekly summary (week starts Monday)
- `/coaching` -- hourly heatmap, best focus windows, warnings
- `/backup` -- backup location, back up now, restore

Start the tray app (server + quick actions near the Windows clock):

```
python -m focuscore.tray
```

Or the whole thing quietly, exactly like the desktop icon does:

```
pythonw -m focuscore.launcher
```

## Run the tests

```
python -m pytest
```

## Files

```
focuscore/
  scoring.py    score levels, override resolution, the Pulse formula
  taxonomy.py   categories + the app/site/title matching rules
  seed_data.py  pre-seeded defaults for ~100 popular apps/sites
  ingest.py     ActivityWatch REST client (window/web/AFK buckets)
  store.py      SQLite storage (focuscore.db next to the code)
  pipeline.py   fetch -> categorize -> score -> store -> summary (+ --demo)
  goals.py      daily goals: definitions, live progress, evaluation
  alerts.py     threshold alerts + Windows notifications
  focus.py      timed focus sessions, streaks, summaries
  blocker.py    strict/lenient blocking during focus sessions
  timesheet.py  suggested blocks, entries, projects, locking, CSV export
  reports.py    weekly report aggregation (Mon-Sun)
  coaching.py   hourly productivity, best windows, burnout warnings
  backup.py     automatic local backups (Google Drive if present)
  home.py       home-page attention cards + Pulse color bands
  launcher.py   quiet startup: server, backup, app-mode window
  tray.py       system tray icon: quick actions, owns the server process
dashboard/
  app.py        Flask dashboard (http://127.0.0.1:5000)
  static/
    style.css   the whole interface style (one file, offline)
tests/
  test_scoring.py
  test_goals_alerts.py
  test_focus.py
  test_phase4.py
  test_phase5.py
USER_GUIDE.md   simple-English manual: what each page is for + daily routine
PUSH_STEPS.md   how to publish this folder to GitHub (for Merlin/Ahtisham)
Start Focus Core.bat   the launcher: quiet start + app window
run-dashboard.bat      fallback launcher (visible console window)
```
