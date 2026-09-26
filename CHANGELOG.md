# Changelog — Focus Core

All notable changes, newest first. Dates are PKT.

## [v1.5.0] — 2026-09-26 (in progress)
Sprint 2, Phase 4: stranger onboarding — ActivityWatch detection + setup guide.
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
