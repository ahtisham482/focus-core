# Project: Focus Core 100x Evolution

## Architecture
Focus Core is a privacy-first, zero-telemetry desktop productivity command center for Windows that integrates with ActivityWatch, provides intelligent focus coaching, visual timelines, and distraction blocking.
The 100x Evolution expands Focus Core into a high-assurance focus and time-intelligence OS comprising:
1. **Core Database & Migration Engine** (`focuscore/migrations.py`, `focuscore/store.py`):
   - SQLite with WAL mode (`PRAGMA journal_mode = WAL`), `busy_timeout = 5000`.
   - Versioned, idempotent migrations with automatic atomic pre-migration backups (`focuscore/backup.py`).
2. **Deep Time Intelligence & Chronotype Engine** (`focuscore/time_intel.py`, `dashboard/static/timeline.js`):
   - Granular AFK tracking (`afk_intervals` table).
   - Diurnal chronotype curves (Morning Lark / Intermediate / Night Owl) and peak focus detection.
   - Meeting overhead & fragmentation heuristics (Zoom/Teams/Meet).
   - Context-switching latency and attention residue index.
   - Multi-factor burnout diagnostic score (0-100).
   - Interactive minute-by-minute timeline visualization in dashboard (HTML/Canvas/SVG).
3. **Hardcore Enforced Focus & Distraction Shield** (`focuscore/shield.py`, `focuscore/hud.py`, `focuscore/focus.py`):
   - Tri-tier enforcement: Gentle (toast/HUD), Strict (minimize `SW_MINIMIZE`), Hardcore (minimize/close `WM_CLOSE`).
   - Native Windows API (`ctypes` with `user32.dll` and `kernel32.dll`) sub-second active window monitoring.
   - Non-work display/window dimming overlay.
   - Floating real-time HUD status counter.
   - Cross-platform safe fallback stubs (`focuscore/platform/winapi.py`) for Linux CI compatibility.
4. **Comprehensive Flowtime & Smart Pomodoro Engine** (`focuscore/flowtime.py`, `focuscore/audio.py`, `focuscore/focus.py`):
   - Turnkey Pomodoro cycles (25/5, 50/10) with 4-cycle long break transitions.
   - Adaptive Flowtime tracking open-ended deep work to voluntary fatigue with proportional recovery calculation.
   - Local offline audio cues and milestone chimes (`winsound` with safe non-Windows fallback).
   - Automated focus timesheet tagging on session completion.
5. **Project, Tagging & Export Capabilities** (`focuscore/store.py`, `focuscore/timesheet.py`, `focuscore/timesheet_export.py`):
   - Enhanced Project & Client registry (billable flag, hourly rates, colors, weekly budget hours).
   - Billable vs non-billable utilization and financial valuation metrics.
   - Weekly project budget cap progress bars and alerts.
   - Multi-format exports: standardized CSV, JSON, and publication-grade PDF via ReportLab.
6. **High-Assurance Verification & Installer Packaging** (`tests/`, `installer/`):
   - 4-Tier test architecture (Tiers 1-4) + Tier 5 adversarial coverage hardening.
   - Local staging verification script (`installer/verify_staging.py`) and Inno Setup packaging.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Raw Trace & Micro-AFK Event Correlator | Ingests and correlates raw window, web, and AFK micro-intervals into persistent storage | M1 | `ORIGINAL_REQUEST.md` § R1 |
| 2 | Circadian Chronotype Profiling | Computes 24h diurnal focus curves and determines chronotype class and peak focus hours | M1 | `ORIGINAL_REQUEST.md` § R1 |
| 3 | Enhanced Auto-Productivity Categorization | Context-aware categorization using process name, window titles, URLs, and heuristics | M1 | `ORIGINAL_REQUEST.md` § R1 |
| 4 | Meeting Overhead & Fragmentation Engine | Detects collaboration calls, calculates total meeting time and work fragmentation | M1 | `ORIGINAL_REQUEST.md` § R1 |
| 5 | Context-Switching Latency Metrics | Quantifies cognitive thrashing and attention residue from rapid task switching | M1 | `ORIGINAL_REQUEST.md` § R1 |
| 6 | Multi-Factor Burnout Diagnostic Engine | Evaluates 6 stress signals (overwork, late nights, weekends, thrashing, break deficit, pulse drop) | M1 | `ORIGINAL_REQUEST.md` § R1 |
| 7 | Interactive Minute-by-Minute Timeline | 100% local interactive timeline in dashboard with zoom, scrub cursor, and category filters | M1 | `ORIGINAL_REQUEST.md` § R1 |
| 8 | Tri-Tier Enforcement Controller | Configures Gentle, Strict, or Hardcore distraction enforcement mode for focus sessions | M2 | `ORIGINAL_REQUEST.md` § R2 |
| 9 | Native Win32 Window & Process Watcher | Sub-second foreground window handle monitor detecting active process, title, and hwnd | M2 | `ORIGINAL_REQUEST.md` § R2 |
| 10 | Foreground Interception & Enforcement | Minimizes (Strict) or closes/terminates (Hardcore) blacklisted distraction windows | M2 | `ORIGINAL_REQUEST.md` § R2 |
| 11 | Non-Work Application Focus Dimmer | Dims non-work application windows with a semi-transparent ambient backdrop overlay | M2 | `ORIGINAL_REQUEST.md` § R2 |
| 12 | Floating Real-Time HUD Status Counter | Compact always-on-top floating HUD displaying timer, mode badge, and blocked counter | M2 | `ORIGINAL_REQUEST.md` § R2 |
| 13 | Standard Pomodoro Engine (25/5 & 50/10) | Turnkey Pomodoro timer managing work intervals, short breaks, and 4-cycle long breaks | M3 | `ORIGINAL_REQUEST.md` § R3 |
| 14 | Adaptive Flowtime Deep Work Engine | Open-ended deep work timer tracking work until voluntary fatigue with proportional breaks | M3 | `ORIGINAL_REQUEST.md` § R3 |
| 15 | Offline Audio Cues & Milestone Chimes | Plays local audio alerts for session start, milestones (25m/50m), completion, and breaks | M3 | `ORIGINAL_REQUEST.md` § R3 |
| 16 | Automated Focus Timesheet Tagging | Automatically generates an accepted timesheet entry upon focus session completion | M3 | `ORIGINAL_REQUEST.md` § R3 |
| 17 | Enhanced Project & Client Registry | Manages projects with client name, billable flag, hourly rate, color tag, and budget cap | M4 | `ORIGINAL_REQUEST.md` § R4 |
| 18 | Billable vs Non-Billable Time Analytics | Computes billable hours, non-billable hours, ratio, and accrued financial valuation | M4 | `ORIGINAL_REQUEST.md` § R4 |
| 19 | Weekly Project Budget Cap Monitor | Tracks logged hours against weekly budget caps with visual burn-rate progress bars | M4 | `ORIGINAL_REQUEST.md` § R4 |
| 20 | Multi-Format Timesheet Exporter (CSV/JSON) | Exports timesheet records in standardized CSV and machine-readable JSON | M4 | `ORIGINAL_REQUEST.md` § R4 |
| 21 | Formatted Timesheet PDF Report Generator | Generates publication-grade PDF invoice/summary using Python `reportlab` | M4 | `ORIGINAL_REQUEST.md` § R4 |
| 22 | Versioned Idempotent SQLite Migration Runner | Applies ordered, incremental SQLite schema migrations inside atomic transactions | M0 | `ORIGINAL_REQUEST.md` § R5 |
| 23 | Pre-Migration Snapshot & Auto-Rollback | Automatically creates verified snapshot before migration and restores on error | M0 | `ORIGINAL_REQUEST.md` § R5 |
| 24 | Packaging & Test Verification Suite | Deterministic pytest test suites and Inno Setup installer build validation | M5 | `ORIGINAL_REQUEST.md` § R5 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M0 | High-Assurance Database Migrations & Tooling Foundation | Idempotent migration runner, pre-migration snapshots, WAL mode, pyproject.toml | none | IN_PROGRESS |
| M1 | Deep Time Intelligence & Interactive Chronotype Analytics | Raw AFK streams, chronotype curves, meeting overhead, context switching, burnout index, minute-by-minute timeline | M0 | PLANNED |
| M2 | Hardcore Enforced Focus & Distraction Shield | Win32 ctypes window watcher, Gentle/Strict/Hardcore interception, dimmer, floating HUD | M0 | PLANNED |
| M3 | Comprehensive Flowtime & Smart Pomodoro Engine | Pomodoro 25/5 & 50/10 state machine, adaptive Flowtime recovery curve, local audio cues, auto-timesheet tagging | M0, M2 | PLANNED |
| M4 | Project, Tagging & Export Capabilities | Client/project registry, billable breakdowns, weekly budget caps, CSV/JSON/PDF exports | M0, M3 | PLANNED |
| M5 | Final E2E Test Suite, Adversarial Hardening & Packaging Verification | 100% test pass (Tiers 1-4), Tier 5 adversarial hardening, clean Inno Setup staging validation | M0, M1, M2, M3, M4 | PLANNED |

## Interface Contracts

### M0: Migrations ↔ Store
- `focuscore.migrations.apply_migrations(db_path: Optional[str] = None) -> int`:
  - Returns highest migration version applied.
  - Takes pre-migration snapshot via `focuscore.backup.create_backup(db_path)`.
  - Records migration in `schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)`.
  - Sets `PRAGMA user_version = N` and `PRAGMA journal_mode = WAL`.
  - On failure, rolls back transaction and restores snapshot.

### M1: Time Intelligence ↔ Dashboard
- `focuscore.time_intel.get_chronotype_profile(day: str, days_lookback: int = 14, db_path = None) -> dict`:
  - Returns `{"chronotype": "morning_lark" | "intermediate" | "night_owl", "peak_hours": [9, 10, 11], "hourly_curve": [float x 24]}`.
- `focuscore.time_intel.get_meeting_overhead(day: str, db_path = None) -> dict`:
  - Returns `{"meeting_minutes": int, "meeting_count": int, "fragmentation_ratio": float}`.
- `focuscore.time_intel.get_context_switching_metrics(day: str, db_path = None) -> dict`:
  - Returns `{"switches_per_hour": float, "thrashing_score": int, "lost_latency_minutes": int}`.
- `focuscore.time_intel.get_burnout_risk(day: str, db_path = None) -> dict`:
  - Returns `{"burnout_score": int (0-100), "risk_level": "low" | "moderate" | "high" | "critical", "factors": list}`.
- `GET /api/timeline?day=YYYY-MM-DD`:
  - Returns JSON containing 1440 minute slots, active intervals, chronotype overlay, and metrics.

### M2: Shield ↔ Focus Session
- `focuscore.shield.FocusShield`:
  - Constructor: `FocusShield(session_id: int, mode: str, block_levels: frozenset, db_path = None)`
  - Methods:
    - `start() -> None`: Spawns sub-second non-blocking window monitor thread.
    - `stop() -> dict`: Stops monitor, releases hooks, returns `{"intercepted_count": int, "actions": list}`.
    - `get_hud_status() -> dict`: Returns elapsed seconds, remaining seconds, mode, and blocked counter.
  - Platform Abstraction: `focuscore.platform.winapi` with `Win32API` and fallback `DummyAPI`.

### M3: Flowtime & Pomodoro ↔ Focus Session
- `focuscore.flowtime.FlowtimeEngine`:
  - `compute_recovery_interval(work_minutes: float) -> int`:
    - Clamped formula: `clamp(int(work_minutes * 0.18), 5, 30)` minutes.
- `focuscore.audio.play_cue(cue_name: str) -> None`:
  - Cue names: `"session_start"`, `"halfway"`, `"milestone"`, `"session_complete"`, `"break_start"`, `"break_complete"`, `"distraction"`.
  - Windows: `winsound.PlaySound` or `winsound.MessageBeep`. Safe fallback on non-Windows.
- `focuscore.focus.end_session(session_id, ...)`:
  - If duration > 1 min: creates `timesheet_entries` row with `project_id`, `task`, `minutes`.

### M4: Projects & Timesheet Export ↔ Dashboard
- `focuscore.timesheet_export.export_timesheet(start_day: str, end_day: str, format: str, project_id = None, db_path = None) -> bytes | str`:
  - Formats: `"csv"`, `"json"`, `"pdf"`.
  - PDF: Generates professional ReportLab document with tabular breakdown, billable vs non-billable summary, and clean formatting.
- `focuscore.store`:
  - `projects` table columns: `id, name, client, is_billable, hourly_rate, color, weekly_budget_hours, created_at`.
  - `timesheet_entries` table columns: includes `is_billable`.

## Code Layout
- `focuscore/`:
  - `migrations.py`: Schema migration runner & snapshots.
  - `time_intel.py`: Chronotype, meeting overhead, context switching, burnout metrics.
  - `shield.py`: Native window enforcer (Gentle/Strict/Hardcore).
  - `hud.py`: Floating status HUD counter widget.
  - `flowtime.py`: Adaptive Flowtime logic & recovery interval calculations.
  - `audio.py`: Native offline audio cues & milestone chimes.
  - `timesheet_export.py`: Multi-format timesheet exporter (CSV/JSON/PDF via ReportLab).
  - `platform/winapi.py`: Platform abstraction layer for Windows ctypes APIs and Linux CI fallback.
- `dashboard/`:
  - `app.py`: Extended Flask routes (`/api/timeline`, `/api/flowtime`, `/api/shield`, `/timesheet/export`, `/projects`).
  - `static/timeline.js`: Minute-by-minute interactive timeline component.
  - `static/hud.css`: HUD and timeline styling.
- `tests/`:
  - `test_migrations.py`: SQLite idempotent migrations and auto-rollback tests.
  - `test_time_intel.py`: Chronotype curves, meeting overhead, burnout index tests.
  - `test_shield.py`: Native window monitoring and enforcement mode tests.
  - `test_flowtime_pomodoro.py`: Pomodoro cycling, Flowtime recovery, audio cues tests.
  - `test_projects_export.py`: Billable breakdowns, budget caps, CSV/JSON/PDF exports tests.
  - `test_e2e_opaque_box.py`: Opaque-box black-box HTTP and CLI acceptance test suite.
  - `test_scenario_full_workday.py`: Tier 4 real-world workday scenario.
- `installer/`:
  - `verify_staging.py`: Local staging validator for embedded Python and Inno Setup configuration.
