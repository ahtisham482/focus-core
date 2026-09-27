# Changelog

## [v1.14.0] — 2026-09-27 (Phase 12: Deep Time Visual Analytics & Executive Reports)
Comprehensive Deep Time visual intelligence overhaul for `/intelligence` and `/intelligence/report`:
- 24-hour SVG depth timeline chart with chronotype peak window overlays and fixed 24-hour binning capped at ~120 rects.
- Composite 0–100 daily Flow Index with tri-state definedness (evaluates to None under 15 minutes of tracked activity), zero-focus floor, and strict integer clamping.
- Distraction recovery cost analysis calculating lost minutes and cognitive switching overhead.
- Deep vs. Shallow work ratio donut chart with category breakdowns.
- Context switch rate 7x24 heatmap with local timezone offset alignment.
- Prescriptive chronotype coaching cards comparing peak window performance to non-peak hours.
- Standalone printable HTML executive report (`/intelligence/report`) with strict CSP (`default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data:`), clean-room redaction (stripped window titles, domain-only URLs), zero JavaScript, and exact-color print styling.
- Zero-schema migration invariant preserved (`PRAGMA user_version = 9`).

## [v1.13.0] — 2026-09-27 (Phase 11: Deep Focus & Sensory Gamification)
Active focus session UI overhaul with sensory feedback and integer XP ledger:
- Live circular SVG progress ring and full-screen Zen mode on `/focus`.
- Real-time Depth Gauge (Flow State, Deep Work, Surface Work) based on sustained productivity and context-switching thresholds.
- Zero-dependency native Web Audio soundscapes (Brownian rain, pink noise, 40Hz Gamma binaural beats).
- Balanced integer XP engine (`xp_ledger`), daily focus rings, and milestone badges (Iron Will, Peak Master, Flow Initiate).
- Chronotype peak synergy with 1-click launch card, 5-minute pre-peak tray toast notification, and in-memory dynamic shield escalation.
- Database Migration 9 (`user_version = 9`): `xp_ledger` table with cascading delete, `badges` table, and `idx_activities_ts` index.

## [v1.11.0] — 2026-09-27 (Sprint 3, Phase 10)
Client invoicing and budget rollover/forecasting. Create draft invoices
from accepted timesheet entries — entries are claimed atomically so the
same hours can never be billed twice. Drafts are fully editable; sending
assigns a sequential number (INV-2026-0001…) and freezes the invoice
permanently — sent/paid/void invoices cannot be edited, only voided and
reissued as a corrected draft. Record payments (partial or full);
overpayments are shown as a warning, never a negative balance. Every
line is a frozen snapshot (date, description, hours, rate, amount) that
never changes even if you edit the source entry. Integer tax/discount
percentages, one currency per invoice, printable invoice HTML with
strict CSP. Budget rollover: unused hours from last period roll into
this period's effective cap (one period only, use-it-or-lose-it, capped
by a global percentage), shown separately as Base / Rolled In /
Effective on project cards. Hours-only advisory forecast with
working-day-aware pacing that matches the budget page. Database
migration 8 (invoice tables, counters, immutability triggers).

## [v1.10.0] — 2026-09-27 (Sprint 3, Phase 9 — UNRELEASED, pending PC verification)
Project tagging with hourly rates, advisory budget tracking, and client
timesheet exports. Set an hourly rate per project — new time entries
snapshot it automatically while old entries keep whatever rate they had
(historical entries are never silently rewritten; an explicit, confirmed
backfill with an audit trail is available from the project card).
Weekly/monthly budgets in hours and/or money per project, with
working-day-aware pacing bars (Mon–Fri by default, configurable);
budgets are strictly advisory and every cap change is kept in an
append-only history ledger, so past reports always reproduce. Client
exports for any date range: safe CSV (no app names, window titles, or
URLs by default; notes/tags opt-in; formula-injection protected),
structured JSON (`focuscore.timesheet/v1`), and a standalone
print-ready "Timesheet Statement" HTML (no external assets, works from a
saved file). Money uses integer minor units throughout — no floating
point. Every export carries a redaction manifest; detailed internal
exports require explicit opt-in and are audit-logged. Database migration
7 (rate snapshots, budget ledger, finance audit events, export
indexes). Financial honesty rule: billed totals separate confirmed
amounts, estimates, and unrated time — never mixed.

## [v1.9.0] — 2026-09-27 (Sprint 3, Phase 8 — UNRELEASED, pending PC verification)
Adaptive Flowtime and Smart Pomodoro: focus sessions now come in three
modes — **Classic** (the fixed timer you already know, unchanged),
**Flowtime** (no fixed end; work until a natural break with a soft
target as a hint), and **Smart Pomodoro** (work/break cycles whose
lengths adapt to your own history). A suggestion card on the Focus page
recommends lengths learned from your last 14 days, in plain English
("your best stretches lately average about 50 minutes"). Timers use a
hybrid resilient clock (monotonic + wall clock + sleep detection), so a
nap never counts as focus and sleep-inflated cycles are never marked
complete. During pomodoro breaks the Shield rests too (only gentle
reminders; 30-minute snap-back cap); after a sleep resume, enforcement
waits 60 seconds before returning. Soft sound cues play on transitions
(toggleable, never blocking, silent if anything fails). Database
migration 6 (`session_cycles` with a one-active-cycle guarantee).
Safety: everything is advisory — suggestions never force anything, and
hardcore locks are never extended.

## [v1.8.0] — 2026-09-27 (Sprint 3, Phase 7 — UNRELEASED, pending PC verification)
Hardcore Distraction Blocker: the old pop-up blocker is replaced by a
single "Shield" background guard built only from the Python standard
library (no new dependencies). The Shield watches which app is in
front (event-driven on Windows, never scans your files) and enforces
three levels: **soft** (reminds you with a note), **firm** (reminds you
and minimizes the app), **hardcore** (minimizes it and locks a
30-second full-screen note). Rules run all day (app / process /
category, with days + hours schedules, e.g. "no social apps weekdays
9 to 6"); focus sessions can start in Standard or Hardcore mode. A
small draggable HUD badge shows the Shield's status; an emergency pass
(1–60 minutes) pauses everything when life interrupts, with a local
backup so it still works if the database is busy. Safety rules: Windows
system apps (Start menu, lock screen, task manager…) and admin-level
apps it cannot identify are never touched — when in doubt the Shield
does nothing. New modules `focuscore/win32.py`, `focuscore/shield.py`,
`focuscore/hud.py`; database migration 5 (`block_rules`,
`block_passes`, `settings`). Old sessions' `strict` mode maps to
Standard. NOTE: no apps are ever closed — "blocking" means blocking the
window, and nothing here can delete your work.

## [v1.7.0] — 2026-09-26 (Sprint 3, Phase 6)
Deep Time Intelligence: a new "Deep time" page with your chronotype
(morning person / night owl / balanced, from 28 days of per-weekday
hourly curves), each weekday's best 2-hour focus window, focus depth
(longest unbroken stretches, median time-to-first-focus, app-switch
rate), distraction anatomy (top distractors + the apps that pull you
into them), this-week-vs-last-week trends, and an interactive day
timeline (click any hour to see its activities). New module
`focuscore/intelligence.py` -- pure documented arithmetic, no ML; all
thresholds are named constants; every card explains how it is computed.
In-app help article included.

## [v1.6.0] — 2026-09-26 (DEPLOYED)
Sprint 2, Phase 5: release polish — in-app help system + stranger-friendly
docs. Fully verified on the PC via the operator loop 2026-09-26: 263/263
tests green (7 new), committed 6e0efa0, tag v1.6.0, CI green
(FocusCore-Setup-1.6.0.exe); installed copy verified the footer Help link
on Home/Timesheet/Focus pages, the /help index with all 13 articles, and
the graceful unknown-key fallback; test copy uninstalled, dev setup
restored. — Focus Core

All notable changes, newest first. Dates are PKT.

## [v1.5.0] — 2026-09-26 (DEPLOYED)
Sprint 2, Phase 4: stranger onboarding — ActivityWatch detection + setup guide.
Fully verified on the PC via the operator loop 2026-09-26: 256/256 tests
green (21 new), committed 27841a1, tag v1.5.0, CI built
FocusCore-Setup-1.5.0.exe; installed copy verified all three detection
states live (/setup/activitywatch green "running" with AW v0.13.2, welcome
step 2 green, "installed but not running" after quitting AW, green again
after restart via "Check again"); test copy uninstalled, dev setup restored.
- New `focuscore/activitywatch.py`: probes ActivityWatch's local API
  (`/api/0/info`, 2s timeout, never raises) and reports one of three
  plain states — `running`, `installed_not_running`, `not_installed`
  (best-effort `aw-qt.exe` / Start Menu shortcut search on Windows).
- New `focuscore/onboarding.py`: pure-HTML builders (no Flask) for the
  setup page and the welcome-tour card; AW version strings are escaped.
- New `/setup/activitywatch` dashboard page: shows the live state and a
  3-step guide (download from the official releases page, run the
  installer, start it from the Start menu) with a "Check again" button;
  plus an optional browser-extension note. No JavaScript needed.
- Welcome tour step 2 now adapts: green "all good" when ActivityWatch is
  running, otherwise a pointer to the setup page (the tour's Next button
  stays, so nobody is trapped).
- Home page: a new "ActivityWatch isn't running" attention card (with the
  right words for each state and one button to the setup page) replaces
  the technical "Tracker isn't sending data" card when the server is
  unreachable; the old card still covers stale-data-while-running.
- Day page: the "Tracker not running" note links to the setup page, and
  the empty-day message uses plain words instead of CLI commands when
  the tracker is down.
- 21 new tests in `tests/test_onboarding.py` (fake local HTTP server,
  injected states, HTML assertions).

## [v1.4.0] — 2026-09-26 (DEPLOYED)
Sprint 2, Phase 3: one-click updates for installed copies. Verified on
the PC via the operator loop 2026-09-26: 235/235 tests green, committed
d058fd0, tag v1.4.0, CI built FocusCore-Setup-1.4.0.exe; installed copy
verified (update-info.json, /update page graceful "couldn't check" while
the repo is private, tray "Check for updates..."); test copy uninstalled
and dev setup restored afterwards.
- New `focuscore/updater.py`: checks the GitHub Releases API for a newer
  version (result cached a day, checked in a background thread at startup
  so launch never blocks), downloads the new `FocusCore-Setup-<ver>.exe`
  and verifies its size.
- New `/update` dashboard page: current version, check status, and an
  "Update now" button. The home page shows an attention card when an
  update is waiting; the tray menu gains "Check for updates...".
- Update flow: safety backup first, then download; the dashboard flags a
  pending install and the tray's watcher thread spawns the installer
  silently and quits the app; the installer upgrades in place and reopens
  the app (new `skipifnotsilent` `[Run]` entry in installer.iss).
  Refuses to update while a focus session is active.
- `installer/build.py --repo owner/name` stamps `update-info.json`
  (repo + version) into the installer; CI passes
  `${{ github.repository }}`. Portable/dev copies don't self-update --
  the page says so plainly.
- Note: GitHub's releases API answers without a login only for PUBLIC
  repos. With a private repo the check fails gracefully ("couldn't
  check for updates") and one-click updates stay dormant.

## [v1.3.0] — 2026-09-26 (DEPLOYED)
Sprint 2, Phase 2: zero-setup installer for strangers. Fully verified on
the PC via the operator loop 2026-09-26: CI built FocusCore-Setup-1.3.0.exe
from tag v1.3.0 and attached it to the GitHub Release; installed cleanly
(desktop icon + Start Menu, fresh db in %LOCALAPPDATA%\Focus Core);
uninstall removed the program but preserved user data; release cleaned.
Two real bugs found and fixed during the cycle: (1) embedded Python had
no setuptools/wheel (CI staging failed); (2) app root missing from
python312._pth so focuscore wasn't importable (PC commit 1225f0d, mirrored
in source). 194/194 tests.

## [v1.2.0] — 2026-09-25 (DEPLOYED)
Sprint 2, Phase 1 approved by Ahtisham 2026-09-25 night: native window via
pywebview. Source commit d57ea5a (189/189 tests, ruff E,F clean).
PC deploy verified by operator loop 2026-09-25 ~23:50: 189/189 pytest on PC,
commit 7d44686 pushed to origin/main, app running. Visual check PASS: real
"Focus Core" pywebview/WebView2 window (no browser chrome), X hides to tray
(tray icon stays alive), tray "Open Focus Core" brings the window back.

## [v1.1.3] — 2026-09-25
Test-isolation fix found by the on-PC loop.
- `test_home_pulse_band_colors` now stubs the ActivityWatch refresh, so it
  passes on a real PC (where ActivityWatch is live) as well as in CI.

## [v1.1.2] — 2026-09-25
Small fixes from real usage.
- The security block page (403) now explains in plain language what happened
  and links back Home, instead of jargon about loopback addresses.

## [v1.1.1] — 2026-09-25
Bug fix from real usage: starting a focus session did not start blocking.
- The blocking guard now starts automatically whenever a focus session is
  created — from the Focus page or the tray menu. No more separate
  `focus-watch.bat` step (it still works as a manual fallback).
- Dashboard pages now show the Focus Core icon (favicon), so the app-mode
  window and browser tab carry the app's identity.
- Focus page copy updated: blocking described as automatic.

## [v1.1.0] — Unreleased (Sprint 1: Track A + stability)
Hardening and verification only — no new user-facing features.
- Backup/restore hardening: atomic writes, SHA256 checksums verified before restore,
  interrupted backup never listed, interrupted restore leaves the old DB intact (FC-001).
- Flask local-security baseline: Host/Origin validation on mutating routes, secure
  response headers; 127.0.0.1 default bind and no debug mode unchanged (FC-002).
- CI workflow: lint + pytest + artifact build on `windows-latest` and `ubuntu-latest`,
  Python 3.12 (FC-003).
- PC-verify checklist for Windows-only features (tray, Drive detection, notifications,
  launchers, app-mode window) — exact human steps, all `pending-pc` (FC-004).
- Troubleshooting guide for common errors (FC-005).
- STRIDE threat model + security checklist (FC-006).
- Dependency audit (pip-audit) + secrets scan, evidence recorded (FC-007).
- QA CYCLE-1 report + evidence bundle + flaky-test check (FC-008).
- UX copy (approved proposals): clearer restore-failure message in plain language;
  legacy-backup note on the restore-success card; new welcome-tour step "Your data stays
  on this computer"; step 1 rewritten to explain what ActivityWatch is; "Your data"
  card (export/delete) on the Backup page.

## [v1.0.0] — 2026-09-25
First complete release: local-first RescueTime-style tracker, verified on the user's
Windows PC. 111/111 pytest tests pass (Python 3.12).

### Phase 1 — Capture, scoring, dashboard
- ActivityWatch ingest (apps, window titles, browser URLs; AFK excluded); SQLite store.
- Generic category taxonomy with RescueTime's exact 5-level scale (+2 … −2) and the
  official weighted Productivity Pulse formula; per-activity overrides; uncategorized queue.
- Local Flask dashboard (127.0.0.1): Pulse, breakdowns, categories, review queue.

### Phase 2 — Goals, alerts, notifications
- Goals with daily targets and live progress; threshold alerts; Windows notifications.
  30/30 tests.

### Phase 3 — Focus sessions with blocking
- Countdown focus sessions, strict/lenient blocking of −1/−2 apps via pop-up +
  fullscreen overlay, session summary, streaks. Verified live on PC (YouTube blocked,
  VS Code allowed). 4 PC-found bugs fixed with regression tests. 58/58 tests.

### Phase 4 — Timesheets, weekly report, focus coaching
- Timesheets (suggested blocks, accept/edit/add/remove, project+client+task tagging,
  day lock, CSV export); weekly report page; focus coaching (7-day hourly heatmap,
  top focus windows, burnout warnings). DB backup preserved all history. 78/78 tests.

### Phase 5 — Real-app experience
- "Focus Core" desktop icon opens the dashboard in its own app window (no address bar);
  guided home page; 3-screen welcome tour; system tray (open, 25-min session, pulse,
  backup now, quit); full UI polish; automatic backups into the user's Google Drive
  folder with one-click restore + new-laptop guide. Server binds 127.0.0.1 only.
  111/111 tests.
