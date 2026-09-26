# Focus Core — User Guide

Simple English. Everything here runs on your own computer.

## Opening Focus Core

Double-click the **Focus Core** icon on your desktop. It opens in its
own window, like a desktop app. A small icon also appears near the
Windows clock (bottom-right, by the time). Right-click it for quick
actions:

- **Open Focus Core** — opens the window again if you closed it.
- **Start 25-min focus session** — starts a focus session right away.
- **Today's Pulse** — shows your score for today.
- **Back up now** — saves a backup copy of your data.
- **Quit** — closes everything cleanly.

You never need the black console window. The old `run-dashboard.bat`
still works if you ever want it.

## Your daily routine (3 minutes)

**Morning — glance at Home.** The home page tells you what needs you.
Each item has one button. Typical morning: review yesterday's
uncategorized activities (one click each), accept yesterday's timesheet
blocks, start a focus session.

**During work — focus sessions.** Open the **Focus** page, type what
you are working on, pick 25/50/90 minutes, start. Keep
`focus-watch.bat` running if you want distractions blocked with a
pop-up. Ending the session shows your summary.

**Evening — timesheet (2 minutes).** Open the **Timesheet** page. The
top shows your day as suggested blocks on a visual timeline — click a
block to see its row. Accept the blocks, give them a project and task
name, then **Lock day** when it looks right. Locked days cannot be
changed, so your records stay trustworthy.

## What each page is for

- **Home** — today's Pulse (green 60+ is good, amber 40–59 is okay, red
  below 40 is a distracted day), tracked vs focused hours, and the
  "what needs your attention" list.
- **Review** — every tracked activity. New apps start as Neutral; set
  the real score here and Focus Core remembers it forever.
- **Timesheet** — turn tracked time into clean entries with
  project/client/task, lock the day, export CSV.
- **Report** — any week's summary: hours, Pulse trend, top categories,
  goal hit-rate, focus sessions.
- **Coaching** — the 7-day hourly heatmap (when you focus best), your
  top 3 two-hour focus windows, and warnings (late nights, marathon
  days, distraction creep, low recovery).
- **Deep time** — your chronotype (morning person / night owl), a
  rhythm grid per weekday, each weekday's best 2-hour window, focus
  depth (longest stretches, time to first focus, app switches), what
  breaks your focus (top distractors + entry points), week-over-week
  trends, and an interactive day timeline (click any hour).
- **Focus** — start/end sessions, see your streak and past summaries.
  Choose **Standard** (reminds you and minimizes the app) or **Hardcore**
  (minimizes it and locks a 30-second "back to work" note on screen).
- **Shield** — the distraction blocker: turn it on or off, add rules
  (e.g. "no social apps on weekdays 9 to 6"), grant yourself an
  emergency pass (1 to 60 minutes) when life interrupts, and see how
  many times it stepped in today. A small floating badge can also show
  the Shield's status on your desktop.
- **Goals** — daily targets with live progress bars. Pin the important
  ones to see them on Home.
- **Alerts** — pop-up warnings when you pass a limit (needs
  `watch-alerts.bat` running for real-time pop-ups).
- **Backup** — where your backups go, back up now, restore an old
  backup.

## Backups

Every time you start Focus Core, it quietly saves a backup if the last
one is older than 24 hours. If you installed **Google Drive for
Desktop**, backups go to a **"Focus Core Backups"** folder in your Drive
and sync to your Google account automatically. If not, they go to a
`backups` folder next to the app.

Home will nudge you if your newest backup is older than 7 days.

## Moving to a new laptop

1. On the new laptop, install Focus Core and Google Drive for Desktop,
   and let Drive finish syncing.
2. Copy the newest `focuscore-2026....db` file from the **"Focus Core
   Backups"** folder into the Focus Core folder, and rename it to
   `focuscore.db`.

Done. All your history, goals, sessions, and timesheets are back.

## If something looks wrong

- **"Tracker isn't sending data"** — look for the ActivityWatch icon
  near the Windows clock. If it is missing, open ActivityWatch from the
  Start menu. It should start with Windows by itself.
- **A page shows no data** — check that ActivityWatch was running while
  you worked, then reload the page (Home re-collects today's data on
  every load).
- **Anything else** — take a screenshot or photo and send it to Merlin.
