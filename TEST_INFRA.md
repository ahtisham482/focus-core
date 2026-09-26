# E2E Test Infra: Focus Core 100x Evolution

## Test Philosophy
- Opaque-box, requirement-driven. No dependency on implementation design.
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Combinations + Real-World Workload Testing.
- Zero-telemetry, zero-network, local test isolation via temporary SQLite databases and mock platform abstraction.

## Feature Inventory & Test Coverage
| # | Feature | Source | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---------|--------|:------:|:------:|:------:|:------:|
| 1 | Raw Trace & Micro-AFK Event Correlator | ORIGINAL_REQUEST § R1 | 5 | 5 | ✓ | ✓ |
| 2 | Circadian Chronotype Profiling | ORIGINAL_REQUEST § R1 | 5 | 5 | ✓ | ✓ |
| 3 | Enhanced Auto-Productivity Categorization | ORIGINAL_REQUEST § R1 | 5 | 5 | ✓ | ✓ |
| 4 | Meeting Overhead & Fragmentation Engine | ORIGINAL_REQUEST § R1 | 5 | 5 | ✓ | ✓ |
| 5 | Context-Switching Latency Metrics | ORIGINAL_REQUEST § R1 | 5 | 5 | ✓ | ✓ |
| 6 | Multi-Factor Burnout Diagnostic Engine | ORIGINAL_REQUEST § R1 | 5 | 5 | ✓ | ✓ |
| 7 | Interactive Minute-by-Minute Timeline | ORIGINAL_REQUEST § R1 | 5 | 5 | ✓ | ✓ |
| 8 | Tri-Tier Enforcement Controller | ORIGINAL_REQUEST § R2 | 5 | 5 | ✓ | ✓ |
| 9 | Native Win32 Window & Process Watcher | ORIGINAL_REQUEST § R2 | 5 | 5 | ✓ | ✓ |
| 10 | Foreground Interception & Enforcement | ORIGINAL_REQUEST § R2 | 5 | 5 | ✓ | ✓ |
| 11 | Non-Work Application Focus Dimmer | ORIGINAL_REQUEST § R2 | 5 | 5 | ✓ | ✓ |
| 12 | Floating Real-Time HUD Status Counter | ORIGINAL_REQUEST § R2 | 5 | 5 | ✓ | ✓ |
| 13 | Standard Pomodoro Engine (25/5 & 50/10) | ORIGINAL_REQUEST § R3 | 5 | 5 | ✓ | ✓ |
| 14 | Adaptive Flowtime Deep Work Engine | ORIGINAL_REQUEST § R3 | 5 | 5 | ✓ | ✓ |
| 15 | Offline Audio Cues & Milestone Chimes | ORIGINAL_REQUEST § R3 | 5 | 5 | ✓ | ✓ |
| 16 | Automated Focus Timesheet Tagging | ORIGINAL_REQUEST § R3 | 5 | 5 | ✓ | ✓ |
| 17 | Enhanced Project & Client Registry | ORIGINAL_REQUEST § R4 | 5 | 5 | ✓ | ✓ |
| 18 | Billable vs Non-Billable Time Analytics | ORIGINAL_REQUEST § R4 | 5 | 5 | ✓ | ✓ |
| 19 | Weekly Project Budget Cap Monitor | ORIGINAL_REQUEST § R4 | 5 | 5 | ✓ | ✓ |
| 20 | Multi-Format Timesheet Exporter (CSV/JSON) | ORIGINAL_REQUEST § R4 | 5 | 5 | ✓ | ✓ |
| 21 | Formatted Timesheet PDF Report Generator | ORIGINAL_REQUEST § R4 | 5 | 5 | ✓ | ✓ |
| 22 | Versioned Idempotent SQLite Migration Runner | ORIGINAL_REQUEST § R5 | 5 | 5 | ✓ | ✓ |
| 23 | Pre-Migration Snapshot & Auto-Rollback | ORIGINAL_REQUEST § R5 | 5 | 5 | ✓ | ✓ |
| 24 | Packaging & Test Verification Suite | ORIGINAL_REQUEST § R5 | 5 | 5 | ✓ | ✓ |

## Test Architecture
- Test runner: `pytest -q`
- Lint gate: `uvx ruff check --select E,F focuscore dashboard tests`
- Opaque-box E2E: `tests/test_e2e_opaque_box.py`
- Test directories and modules:
  - `tests/test_migrations.py` (M0)
  - `tests/test_time_intel.py` (M1)
  - `tests/test_shield.py` (M2)
  - `tests/test_flowtime_pomodoro.py` (M3)
  - `tests/test_projects_export.py` (M4)
  - `tests/test_scenario_full_workday.py` (M5 Tier 4)
  - `tests/test_scenario_distraction_storm.py` (M5 Tier 4)
  - `tests/test_scenario_upgrade_integrity.py` (M5 Tier 4)

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Full Workday Simulation (Pomodoro + Meetings + Flowtime + Timesheet) | F1-F7, F13-F21 | High |
| 2 | Distraction Storm & Hardcore Interception | F8-F12 | High |
| 3 | Crash & Power Loss Recovery | F8-F10, F22-F23 | Medium |
| 4 | Multi-Version Upgrade & Backward Compatibility | F22-F23, F17-F21 | High |

## Coverage Thresholds
- Tier 1: >=5 per feature
- Tier 2: >=5 per feature
- Tier 3: Pairwise coverage of major feature interactions
- Tier 4: >=4 realistic application scenarios
- Gate: 100% pytest pass (0 failures), 0 ruff errors.
