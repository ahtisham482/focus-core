# Phase 11 (v1.13.0) — Deep Focus & Sensory Gamification

**Sprint:** Sprint 5, first half. **Status:** Blueprint approved by user via `/grill-me` interview.
**Predecessor:** v1.12.0 (Sprint 4, Polish & Stability — Invariant I-1 in force).

## 1. Goal

Make the active focus session feel like a product, not a timer page — live visual
feedback (progress ring, depth gauge), sensory immersion (synthesized ambient sound,
zero downloads), and balanced gamification (rings, XP, badges) — while holding
every Sprint 4 invariant.

## 2. Architecture

```
dashboard/routes/focus.py        -- overhauled: SVG ring, Zen mode, depth gauge,
                                    soundscape controls, peak-launch card
dashboard/static/focus.js        -- NEW: ring animation, depth polling, zen toggle
dashboard/static/soundscapes.js  -- NEW: Web Audio API generators (rain/pink/gamma)
dashboard/static/style.css       -- new classes: ring, zen-mode, depth gauge, badges
focuscore/gamification.py        -- NEW: XP engine, daily rings, streaks, badges
focuscore/chronotype.py          -- NEW: peak-window detection + settings
focuscore/tray.py                -- peak-window toast (5 min before)
focuscore/shield.py              -- snapshot builder reads chronotype flag
                                    (I-1 SAFE: refresher thread only, never worker)
focuscore/migrations.py          -- M9: gamification tables (user_version 8 -> 9)
tests/test_phase11_focus.py      -- NEW: full coverage of A-D below
```

### Data flow (I-1 compliance)

- **Shield enforcement path:** unchanged. The 45s snapshot refresher thread reads
  `chronotype.is_peak_now()` (one SQLite settings read, off the critical path) and
  bakes `force_hardcore: bool` into the immutable `RulesSnapshot`. The worker only
  reads the snapshot. **Zero new SQLite calls on the enforcement path.**
- **Depth gauge:** computed server-side per page render from already-cached
  session activities (same queries `session_summary` already runs). A lightweight
  `/focus/depth` JSON endpoint polls every 30s for live updates — plain Flask,
  no shield involvement.
- **Soundscapes:** 100% client-side. No server endpoint, no files, no network.
- **Gamification writes:** happen once at `end_session()` (XP award, badge check,
  streak update) — a single transaction, off every hot path.

## 3. Component specs

### A. Active session UI overhaul

**A1. Live SVG circular progress ring** (`focus.js` + inline SVG in template)
- `<svg>` ring: circumference math in JS, `stroke-dashoffset` animated via
  `requestAnimationFrame` (smooth, no jank).
- Shows remaining (classic/pomodoro) or elapsed-vs-target (flowtime).
- Pomodoro cycle dots: N dots under the ring, filled = completed work blocks.
- Color transitions by depth state: green (flow) → amber (deep) → grey (surface).
- No external libraries; ~60 lines of vanilla JS.

**A2. Full-screen Zen mode**
- Toggle button on the session card + keyboard shortcut (`z`).
- CSS class `zen-mode` on `<body>`: hides nav/header/footer/cards, centers a large
  ring + timer + depth label + minimal controls (end/abort/sound).
- Pure CSS + one JS class toggle. Escape or `z` exits.

**A3. Real-time depth gauge**
- Server function `focuscore/focus.py::depth_state(session_id)` returns one of
  `flow | deep | surface` plus the inputs (dominant score, switches/15min,
  uninterrupted minutes).
- Inputs: active app's category score from the session's tracked activities
  (score +2/+1/0/-1/-2 via existing taxonomy), context-switch count = distinct
  apps in the trailing 15 min window, uninterrupted = minutes since session start
  (or last break) with no -1/-2 app.
- Rules:
  - **Flow** 🟢: dominant score +2 AND 0 switches in trailing 15 min AND
    uninterrupted ≥ 15 min.
  - **Deep** 🟡: dominant score in (+1,+2) AND switches ≤ 2 in trailing 15 min.
  - **Surface** ⚪: everything else (warm-up < 15 min, or high switching).
- Rendered as a labeled pill + thin meter bar; `/focus/depth` JSON endpoint
  refreshes it every 30s without full page reload.

### B. Zero-dependency ambient soundscapes (`soundscapes.js`)

Three generators, all synthesized with the Web Audio API at runtime:

1. **Brownian Rain** — brown-noise buffer (integrated white noise, normalized),
   lowpass filter ~800Hz, slow LFO on filter frequency for rain-like movement.
2. **Calibrated Pink Noise** — pink noise via Paul Kellet's filter cascade
   (constant power per octave), fixed gain staging.
3. **40Hz Gamma Beat** — two oscillators (e.g. 200Hz left / 240Hz right) for a
   40Hz binaural beat; low gain, sine waves.

Controls: play/stop toggle per soundscape, master volume slider, all persisted
to `localStorage` (client-side only — no server state). Integrated on the active
session card and in Zen mode. Autoplay policy: start on user gesture only
(clicking play counts). Total: 0 audio files, 0 bytes installer overhead.

### C. Gamification engine (`focuscore/gamification.py`)

**C1. Daily focus rings**
- `daily_minutes(day)` = sum of completed-session focus minutes for the day
  (reuse `session_summary` focus_minutes for status='completed' sessions).
- Target configurable via settings key `daily_focus_target_min` (default 120).
- SVG ring on `/focus` (idle state) + home page card: `minutes / target`.

**C2. XP scoring** — awarded once per completed session in `end_session()`:
- Base: 1 XP per focus minute (rounded down).
- Peak bonus: ×1.5 if session overlapped the chronotype peak window.
- Clean-run bonus: ×1.25 if `blocks_count == 0` (zero distraction blocks).
- Streak multiplier: 1.0 + 0.1 × consecutive active days, capped at 2.0x.
- Order: `total = floor(base × peak? × clean? × streak)`. Integer XP always.
- Stored in `xp_ledger` (one row per session, append-only).

**C3. Milestone badges** — checked after each XP award, stored in `badges`:
- **Centurion**: lifetime focus hours ≥ 100.
- **Iron Will**: completed a hardcore session with zero blocks.
- **Peak Master**: 10 completed sessions overlapping peak window.
- Badge toast on the session-summary page (CSS animation, no JS lib).

**C4. Streaks** — consecutive days with ≥1 completed session (extends existing
`current_streak()`; gamification reuses it, no duplicate logic).

### D. Chronotype peak window & Windows synergy (`focuscore/chronotype.py`)

- Settings keys: `peak_start` ("09:00"), `peak_end` ("11:30"), `peak_enabled`
  ("1"). Editable on `/focus` preferences card. Sensible default 09:00–11:30.
- `is_peak_now(now=None)` → bool. `peak_status()` → `before | in | after` plus
  minutes until next transition (for the toast scheduler).
- **1-Click Peak Launch Card**: on `/focus` (no active session) during peak,
  a highlighted card: "⚡ You are in your peak energy window (09:00–11:30) —
  1-Click Launch Deep Focus" → POSTs to `/focus/start` with preset 90,
  block_level strict, enforcement hardcore pre-selected.
- **5-minute tray toast**: `tray.py` background thread checks `peak_status()`
  every 60s; when `before` and minutes-until-start ≤ 5 (and not already toasted
  today — settings key `peak_toast_date`), fires a Windows toast via pystray
  notification with action opening `/focus`. Silent no-op when pystray missing.
- **Dynamic shield escalation**: snapshot refresher bakes `force_hardcore`
  into `RulesSnapshot` when `is_peak_now()`; worker maps it to hardcore
  enforcement for that cycle. In-memory only — I-1 holds.

## 4. Schema changes (Migration M9: user_version 8 → 9)

```sql
CREATE TABLE IF NOT EXISTS xp_ledger (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL UNIQUE,
    day TEXT NOT NULL,
    base_xp INTEGER NOT NULL,
    peak_bonus INTEGER NOT NULL DEFAULT 0,   -- extra XP from ×1.5
    clean_bonus INTEGER NOT NULL DEFAULT 0,  -- extra XP from ×1.25
    streak_mult_x10 INTEGER NOT NULL DEFAULT 10, -- ×10 for integer math
    total_xp INTEGER NOT NULL,
    awarded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xp_ledger_day ON xp_ledger(day);

CREATE TABLE IF NOT EXISTS badges (
    id INTEGER PRIMARY KEY,
    badge_key TEXT NOT NULL UNIQUE,
    awarded_at TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
-- chronotype lives in the existing settings table (no new table).
```

Integer-only XP math (bonuses stored as integer deltas; multiplier as ×10 int).
Idempotent, atomic per-migration transaction (M0 engine), pre-migration snapshot.

## 5. Invariant compliance checklist

- [ ] **I-1 (Enforcement Independence):** shield worker gains zero SQLite calls.
      `force_hardcore` arrives via the snapshot; verify by test with poisoned
      `store.get_db` during `shield_once(..., snapshot=...)`.
- [ ] **Zero audio deps:** `soundscapes.js` uses only `AudioContext`; grep the
      built installer file list for `.mp3/.wav/.ogg` → must be empty.
- [ ] **Zero chart libs:** only inline `<svg>`; no new `<script src>`.
- [ ] **No schema change without migration:** all new tables behind M9.
- [ ] **Existing tests untouched:** 474 pass unmodified; new file only.

## 6. Test plan (`tests/test_phase11_focus.py`)

1. `test_depth_flow_state` — +2 dominant, 0 switches, 20 min uninterrupted → flow.
2. `test_depth_deep_work` — +1 dominant, 1 switch → deep.
3. `test_depth_surface_warmup` — 5 min session → surface.
4. `test_depth_surface_high_switching` — 8 switches/15min → surface.
5. `test_xp_base_and_peak` — 60 focus min in peak → base 60, peak bonus 30.
6. `test_xp_clean_run_bonus` — 0 blocks → +25%.
7. `test_xp_streak_multiplier_cap` — 15-day streak → exactly 2.0x.
8. `test_xp_integer_only` — no floats in ledger row.
9. `test_badge_centurion` / `test_badge_iron_will` / `test_badge_peak_master`.
10. `test_daily_ring_minutes` — sums completed sessions only.
11. `test_chronotype_peak_boundaries` — 08:59 no, 09:00 yes, 11:30 yes, 11:31 no.
12. `test_peak_escalation_in_snapshot` — refresher bakes `force_hardcore`;
    worker `shield_once` with snapshot performs zero SQLite (poisoned store).
13. `test_tray_toast_once_per_day` — toast fires once, not twice.
14. `test_migration_m9_idempotent` — re-run safe, user_version == 9.
15. Route tests: `/focus` 200 idle + active; `/focus/depth` JSON shape;
    peak-launch card present during peak (time-mocked), absent otherwise.

## 7. Acceptance criteria

- All 474 existing tests pass unmodified; 15 new tests pass.
- Ruff E/F clean on all touched files.
- `/focus` renders ring + depth pill + soundscape controls with no JS errors
  (page-source grep for `soundscapes.js`, `focus.js`).
- Zen mode hides nav (CSS class check) and exits on Escape.
- I-1 probe test passes (item 12 above).
- Fresh-zip extraction: full suite green.

## 8. Council checkpoints

- **Qwen audit (before merge):** I-1 probe, M9 migration safety, integer XP math,
  no new deps.
- **GLM stress audit (before tag):** XP race on concurrent `end_session`,
  depth-gauge query cost under load, toast thread leak check.
- **AntiGravity PC gate:** pasted pytest output, installer smoke (ring renders,
  sound plays on click, toast fires — or documented skip with reason).

## 9. Explicit non-goals

- No process-killing, no new block levels, no changes to scoring taxonomy.
- No server-side audio, no audio file assets, no CDN.
- No changes to invoice/budget logic. No export-format changes.
