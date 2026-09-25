# Changelog — Focus Core

All notable changes, newest first. Dates are PKT.

## [v1.3.0] — 2026-09-26 (in progress)
Sprint 2, Phase 2: zero-setup installer for strangers.
- New `focuscore/paths.py`: single place that decides where user data
  lives. Installed copies (`.installed` marker) keep the database,
  backups, and flags in `%LOCALAPPDATA%\Focus Core`; portable/dev
  installs work exactly as before, and a database already sitting next
  to the code is always honored (grandfathered).
- New `installer/`: `build.py` stages embedded Python 3.12 + pip + all
  runtime deps + app code + WebView2 bootstrapper + icon.ico;
  `installer.iss` (Inno Setup 6) compiles it to
  `FocusCore-Setup-<version>.exe` — per-user install, no admin/UAC,
  desktop icon, optional start-with-Windows, and uninstall never
  deletes user data. `README.md` documents local + CI builds.
- New `.github/workflows/installer.yml`: pushing a tag like `v1.3.0`
  builds the installer on Windows CI and attaches it to the GitHub
  Release; manual runs keep it as an artifact.

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
