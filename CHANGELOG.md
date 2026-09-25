# Changelog — Focus Core

All notable changes, newest first. Dates are PKT.

## [v1.2.0] — 2026-09-25
Sprint 2, Phase 1: the app now opens in a real native desktop window
(pywebview, backed by WebView2 on Windows) instead of a browser --app
window. Own titled window, taskbar entry, minimize/restore, no address
bar. Closing the window hides it to the tray (the app keeps running);
Quit is still in the tray menu. If pywebview is missing or unusable,
the app falls back to the previous browser window, so it always works.
- New module `focuscore/desktop.py` (window config, cancellable
  close-to-tray, main-thread GUI loop); tray owns the window + server.
- `pywebview>=5.0` added to requirements (installed by setup.bat).

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
