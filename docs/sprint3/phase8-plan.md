# Sprint 3 — Phase 8: Adaptive Flowtime & Smart Pomodoro Engine (architectural plan)

Version target: **v1.9.0**. Status: **IMPLEMENTATION IN PROGRESS —
Qwen audit gave a CONDITIONAL PASS (Revision 2, 2026-09-27); all six
mandatory constraints are implemented and unit-tested locally. Pending:
full PC verification before any release/tag.**

## Revision 2 — Qwen 3.8 Max audit remediations (2026-09-27)

Verdict: **CONDITIONAL PASS**. The six constraints below are mandatory
and override the corresponding Revision 1 text.

- **R1 Hybrid Resilient Timer.** Countdowns combine `time.monotonic()`
  with the UTC wall clock plus 60-second tick-gap suspend detection.
  Sleep time is *subtracted* from elapsed time; a sleep-inflated cycle
  is **never** marked completed. (Overrides Rev 1's "mark complete
  after planned + 15 min grace".)
- **R2 Shield Break Stand-Down.** During pomodoro breaks the Shield
  drops to a *soft* stand-down, not a full suspension: session-driven
  enforcement pauses but global **soft** block rules keep applying.
  Hard 30-minute snap-back cap on breaks; 60-second grace before
  enforcement resumes after a sleep resume.
- **R3 Relational `session_cycles` schema.** Cycles are relational rows
  with a partial unique index `WHERE status = 'active'` guaranteeing
  exactly one active cycle. (Confirms Rev 1's data-model choice.)
- **R4 Intent-Based Flow.** Flowtime learning is intent-based: a
  qualifying stretch needs 10 minutes of continuous focus; semantic
  switching among work tools (productive scores) does not break a
  stretch; 120 s AFK tolerance; at most one 10-minute soft deferral
  suggestion; hardcore locks are never auto-extended.
- **R5 Proportional Fatigue Recovery.** Break = 5 min base, 15 min on
  every 4th work cycle, +5 min when switch rate exceeds 1.5x baseline.
  Advisory only — the user can always skip or end a break.
- **R6 Audio Cues.** Non-blocking async WAV cues via `winsound`,
  user-toggleable, fail completely silent (no-op off Windows, every
  call wrapped so a cue can never crash or block the app).

## Goal

Today every focus session is a fixed timer the user picks by guesswork.
Phase 8 makes sessions adapt to the user's real rhythms, measured from
their own history:

- **Flowtime** — no fixed end. Start with a label, work until a natural
  stopping point, end the session. The system records natural lengths
  and learns what "a good stretch" looks like for this user.
- **Smart Pomodoro** — work/break cycles where the work length and the
  break length adapt: longer breaks when fatigue signals appear, a long
  break after 4 cycles, a gentle suggestion to stop when the day's focus
  is spent.
- **Classic** — today's fixed timer, unchanged. The default. Nothing
  the user already does breaks.

## Non-negotiable design constraints (product standards)

1. **Zero new dependencies.** Stdlib + existing stack only (same as
   Phase 7).
2. **Local-first.** All adaptation arithmetic runs on the user's own
   history in their own SQLite file. No network, no telemetry.
3. **Existing sessions keep working.** `mode` defaults to `classic`;
   old rows behave exactly as today.
4. **Fail-safe timers.** A break left running ends itself after 30 min.
   A work cycle that outlives sleep/hibernate is marked complete, never
   punished. When in doubt the engine suggests less, not more.
5. **Explainable, not magic.** Every suggestion carries a one-line
   plain-English reason ("your best stretches lately are ~50 min").
   No ML, no black box — documented arithmetic like `intelligence.py`.
6. **No process killing.** Unchanged from Phase 7: blocking means
   blocking the window.

## What exists today (do not reinvent)

- `focuscore/focus.py` — start/end/abort/status, `MAX_MINUTES = 480`,
  `BLOCK_LEVELS`, `ENFORCEMENT_MODES = ("strict", "hardcore")`.
- `focuscore/intelligence.py` — `focus_stretches(day)`,
  `depth_summary(from, to)` (median longest stretch),
  `median_time_to_focus(from, to)`, `switch_rate(day)`,
  `weekday_peak_windows(curves)`. Phase 8 consumes these; it does not
  recompute them.
- `focuscore/shield.py` — enforcement reads the active session; Phase 8
  only tells it "a break is running, stand down" and "break over,
  resume".
- `/focus` dashboard page, `session_summary()`, streaks.

## New concepts

### 1. Session modes

`focus_sessions.mode`: `classic` (default), `flowtime`, `pomodoro`.

- **Classic**: exactly today's behavior.
- **Flowtime**: `duration_minutes` becomes a *soft target* shown as a
  progress hint, not an alarm. The session has no planned end; the user
  ends it at a natural break. `actual_minutes` is the truth.
- **Pomodoro**: the session is a container for `session_cycles` rows.
  Work cycles use the adaptive work length; breaks use the adaptive
  break length. The user can start/skip/end a break; the engine never
  forces one.

### 2. Adaptive engine — new module `focuscore/adaptive.py`

Pure functions, deterministic, unit-testable with fixed fixtures.
Named constants, every threshold justified in a comment.

- `suggest_work_minutes(db_path=None, now=None) -> (minutes, reason)`:
  - Cold start (fewer than 7 days with focus data): 25 for pomodoro,
    50 soft target for flowtime. Reason says so plainly.
  - Warm: median of each day's longest focus stretch over the last
    14 days (`depth_summary`), clamped to 15–120, rounded to 5.
  - Time-of-day nudge: if `now` falls inside today's peak window
    (`weekday_peak_windows`), +10 (cap 120); if in the worst 2-hour
    window, −10 (floor 15).
- `suggest_break_minutes(work_cycles_done, recent_switch, baseline_switch)
  -> (minutes, reason)`:
  - Base 5. Every 4th completed work cycle → 15 long break.
  - If `recent_switch > 1.5 * baseline_switch` → +5
    ("your app-switching is up — take a longer breather"). Cap 30.
- `fatigue_check(db_path=None, now=None) -> (tired: bool, message)`:
  - True when 3+ work cycles are done today AND today's Pulse < 40.
    Message suggests stopping; it never auto-ends anything.
- `flow_qualifying_minutes(session_id, db_path=None) -> float`
  (R4 intent-based flow): productive (+1/+2) stretches inside the
  session; gaps ≤ 120 s AFK tolerated; switching among productive
  work tools does not break a stretch; only stretches ≥ 10 min count.
- `suggest_flow_target(db_path=None) -> (minutes, reason)`: median of
  past flowtime qualifying minutes (warm) else 50; clamped 15–120.
- At most **one** 10-minute soft-deferral suggestion when a flowtime
  soft target is reached; hardcore locks are never extended.

### 6. Audio cues (R6) — new module `focuscore/cues.py`

- `play_cue(name)`: `cycle_end`, `break_end`, `session_end`.
- Windows: synthesize a short digital WAV (sine blip, stdlib `wave`
  module) to the temp dir once per cue, play with
  `winsound.PlaySound(..., SND_ASYNC | SND_FILENAME)` — never blocks.
- Every call wrapped in try/except: a cue can never crash or hang the
  app. Off Windows: no-op. Toggle: settings key `audio_cues`
  (dashboard checkbox on /focus).

### 3. Breaks and the Shield (revised per R2)

- While a pomodoro break cycle is active, the Shield goes to **soft
  stand-down**: session-driven enforcement pauses, but global block
  rules with action `soft` keep applying; `firm`/`hardcore` rules are
  capped at `soft` for the break's duration.
- Hard **30-minute snap-back cap**: a break left running auto-completes
  at 30 min and enforcement resumes.
- **60-second grace**: after a sleep resume is detected, enforcement
  stays paused for 60 s before resuming (no punishing the user for
  waking the PC).

### 4. Timers (revised per R1 — hybrid resilient timer)

- Countdowns use **both** `time.monotonic()` and the UTC wall clock.
  Focused elapsed = monotonic delta (sleep excluded by construction).
- **Suspend detection:** a 60-second tick-gap check. If
  `wall_elapsed − monotonic_elapsed > 60 s`, a sleep/hibernate
  happened: the cycle's monotonic start is shifted forward by the gap
  so sleep is never counted as focus, and the Shield's 60 s resume
  grace (R2) begins.
- A sleep-inflated cycle is **never** marked completed: completion
  requires focused (monotonic) elapsed ≥ planned minutes.
- If the monotonic clock resets (daemon restart), the cycle keeps its
  previously credited offset and resyncs from now — conservative,
  never over-credits.
- Audio cues (R6) fire on transitions: work→break, break→work,
  session end.

## Store API additions

- `create_session(...)` gains `mode="classic"`,
  `suggested_minutes=None`, `target_minutes=None` (keyword-only; the
  Phase 7 positional fix stays).
- `start_cycle(session_id, kind, planned_minutes, ...)` /
  `end_cycle(cycle_id, status, ...)` / `get_active_cycle(session_id)`.
- `cycles_for_session(session_id)`.

## Migration 6 — `0006_session_modes` (revised per R3)

- Reuse migration-3 columns: normalize pre-Phase-8 rows with
  `UPDATE focus_sessions SET session_type = 'classic'
  WHERE session_type = 'pomodoro'` (every old session was a classic
  fixed timer); new sessions always write `session_type` explicitly
  (`classic` / `flowtime` / `pomodoro`). `completed_cycles`,
  `target_cycles`, `break_minutes`, `work_minutes` are reused as-is.
- `ALTER TABLE focus_sessions ADD COLUMN suggested_minutes INTEGER`
  (what the engine suggested; NULL when none).
- New table `session_cycles`: `id`, `session_id` (FK),
  `kind` ('work'/'break'), `planned_minutes`, `started_at` (UTC wall),
  `started_monotonic REAL`, `elapsed_offset_seconds REAL DEFAULT 0`
  (credited focus time, survives daemon restarts),
  `ended_at`, `status` ('active'/'completed'/'skipped'/'aborted').
- `CREATE UNIQUE INDEX uq_one_active_cycle ON
  session_cycles(session_id) WHERE status = 'active'` — the database
  itself guarantees at most one active cycle per session.
- `CREATE INDEX idx_cycles_session ON session_cycles(session_id)`.
- Settings default: `audio_cues = '1'` (INSERT OR IGNORE).
- Same M0 guarantees as migrations 1–5: atomic per-migration
  transaction, snapshot + rollback, idempotent re-run.

## Routes & pages

- `/focus`: mode picker (Classic / Flowtime / Smart Pomodoro) added to
  the start-session form; the adaptive suggestion card shows above it
  ("Based on your last 14 days, your best stretches are ~50 min —
  try Flowtime, or a 25-minute Pomodoro.").
- `/focus` live view per mode:
  - Pomodoro: current cycle + countdown, buttons: Start break /
    Skip break / End session.
  - Flowtime: elapsed time, soft-target progress hint, "End at a
    natural break".
- `/help/focus` article extended with the two new modes.

## Tests (local gate: full suite green before packaging)

- `tests/test_adaptive.py`: suggestion determinism on fixed fixtures;
  cold start defaults; clamps (15/120, break cap 30); time-of-day
  nudge; fatigue check true/false boundaries.
- `tests/test_migration_6.py`: fresh DB, idempotent re-run, v5→v6
  upgrade preserves sessions, defaults (`classic`) on old rows.
- `tests/test_session_modes.py`: flowtime start/end records natural
  length; pomodoro cycle lifecycle; break auto-end at 30 min;
  shield stand-down flag during breaks; classic unchanged.
- Existing suite must stay green (no signature breaks).

## PC verification checklist

1. Full pytest pasted verbatim (the count is the gate, not a summary).
2. Migration 6 runs on the real `focuscore.db` with zero data loss;
   old sessions read as `classic`.
3. Live: start one session in each mode; suggestion card shows real
   numbers from the user's history; a pomodoro break suspends the
   Shield and enforcement resumes after; a 30-min break auto-ends.
4. Uninstall preserves `focuscore.db`; dev setup restored.

## Open questions — for the Qwen audit (nothing builds until cleared)

- **Q1 (timers):** monotonic vs wall-clock across Windows
  sleep/hibernate — is there an edge where the monotonic clock jumps
  and a cycle is mis-marked?
- **Q2 (breaks):** Shield full stand-down during breaks vs dropping to
  soft nudges — which default is safer against "gaming" breaks?
- **Q3 (data model):** `session_cycles` as rows vs a JSON column on
  `focus_sessions` — challenge the choice.
- **Q4 (cold start):** are 25/50 defensible defaults with < 7 days of
  data, or should the engine refuse to suggest until warm?
- **Q5 (learning):** per-label natural-length medians vs one global
  number — worth the complexity in v1.9.0, or defer?
