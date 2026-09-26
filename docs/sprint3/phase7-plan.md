# Sprint 3 — Phase 7: Hardcore Distraction Blocker + Visual HUD (architectural plan)

**Version:** v1.8.0 · **Status:** planned → Qwen audit (conditional pass) → revising → implementing · **Date:** 2026-09-27

## Revision 2 — Qwen 3.8 Max audit remediations (2026-09-27)

Qwen returned **CONDITIONAL PASS**. The no-kill deferral, zero-dependency
rule, and graduated ladder were strongly approved. Five remediations are
P0 and are folded into this spec before any code is written:

**R1. Event-driven detection (replaces 1 s polling).** Qwen rejected the
`time.sleep(1)` `GetForegroundWindow` poll (battery/CPU drain, and a 5 s
classify gap lets a 4-second Twitter scroll through unblocked).
`win32.py` now installs **`SetWinEventHook(EVENT_SYSTEM_FOREGROUND
= 0x0003, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)`**; the hook
callback pushes the new hwnd to a queue in <1 ms and classification runs
immediately on the event (<5 ms target). A **2 s fallback poll** remains
as a safety net (hook can be dropped across UAC prompts / Secure
Desktop transitions). `HWND_WATCH_SECONDS` is replaced by
`FALLBACK_POLL_SECONDS = 2`.

**R2. Elevation fail-open.** `QueryFullProcessImageNameW` is the right
API, but `OpenProcess` fails with `ERROR_ACCESS_DENIED` (5) against
elevated processes (admin Task Manager, UAC, installers). If identity
can't be confirmed, the shield must **fail open**: `get_foreground_info()`
returns `process_name = "__ELEVATED_UNKNOWN__"`, and `is_protected()`
returns `True` for it. The shield never minimizes what it cannot
identify.

**R3. Named mutex replaces `msvcrt.locking`.** Byte-range locks can go
stale on hard crashes / antivirus holds. Single instance is now a Win32
named mutex: `kernel32.CreateMutexW(None, True,
"FocusCore_Shield_Singleton_Mutex_v1")`; `GetLastError() == 183
(ERROR_ALREADY_EXISTS)` → second instance exits quietly. The kernel
destroys the mutex when the owning process dies — no stale locks, ever.
`SINGLE_INSTANCE_LOCK` constant is replaced by `SHIELD_MUTEX_NAME`.

**R4. Hardcore hardening (no-kill verdict: STRONG PASS, keep the
deferral).** Window-level enforcement stands, plus:
- Overlay windows get `HWND_TOPMOST` via `SetWindowPos` and span **all
  monitors** (`EnumDisplayMonitors` / `SM_CXVIRTUALSCREEN` metrics).
- During `LOCK_SECONDS`, a **`WH_KEYBOARD_LL` low-level keyboard hook**
  swallows Alt+Tab and the Win key. Installed only for the lock window,
  removed in a `finally`; if installation fails the lock proceeds
  without the swallow (fail-safe, logged). **Ctrl+Alt+Del is never
  interceptable** (secure attention sequence) — it remains the ultimate
  exit and that is intentional.
- **Fullscreen exclusive bypass:** if the foreground window's rect
  matches its monitor rect AND it carries `WS_EX_TOPMOST`, the overlay
  is suspended (no rendering-context tearing in games/video); the
  minimize action still applies for firm/hardcore and the skip is
  logged. Overlay resumes when fullscreen exits.

**R5. Threading model (tkinter is single-threaded).**
- **Thread 1 (main):** `tkinter.mainloop()` — owns the HUD and all
  overlay windows. Polls a `queue.Queue` via `root.after(100,
  drain_ui_queue)`.
- **Thread 2 (worker):** Win32 message pump (`GetMessage` loop) for the
  event hook + the policy engine (classify → resolve → act). Never
  touches tkinter; pushes UI commands (`("overlay", …)`,
  `("hud_update", …)`) into the queue.
- Headless/no-display: UI thread is skipped, engine runs on main,
  overlays degrade to notifications (existing fail-silent rule).

**R6. Expanded protected list.** Adds `LockApp.exe` (lock screen),
`LogonUI.exe` (UAC/login), `SearchUI.exe` /
`StartMenuExperienceHost.exe` (start menu), `dwm.exe` (compositor —
minimizing it breaks the visual session), `csrss.exe`, `wininit.exe`,
`services.exe`, plus dynamic `__ELEVATED_UNKNOWN__` (R2). Double-check
kept: `resolve_action()` AND the `minimize_window()` call site.

**R7. Emergency pass never fails closed.** `create_pass()` writes to
SQLite; on `sqlite3.OperationalError` (locked/busy) it appends a JSON
line to `<app-data>/passes.fallback.jsonl` AND keeps an in-memory copy.
`pass_active()` checks DB → memory → fallback file, in that order.

**R8. Migration 5 approved as proposed.** Note: store `HH:MM` schedule
strings as wall-clock and evaluate with the Phase 6 timezone logic
(local wall clock, DST-aggregated).

## Goal

Phase 7 turns blocking from a session-only pop-up into real enforcement:

1. **Shield daemon** — one long-running background process that enforces
   *both* active focus sessions *and* always-on user rules (blocklists +
   schedules, e.g. "no social apps 09:00–17:00 on weekdays").
2. **Graduated enforcement** — soft (notify + overlay, today's behavior) →
   firm (minimize the offending window + overlay) → hardcore (minimize +
   locked overlay that cannot be dismissed for N seconds).
3. **Visual HUD** — a small always-on-top window showing shield state:
   mode, current app score, blocks today, session timer.

## Non-negotiable design constraints (product standards)

1. **Established code pattern:** pure decision logic in a module
   (`focuscore/shield.py`), thin OS-interaction layer that fails silent,
   thin Flask route. The route does no enforcement math; the enforcer
   does no HTML. (The `blocker.py` / `intelligence.py` precedent.)
2. **Zero new dependencies.** Window handling via `ctypes` (stdlib) and
   the HUD via `tkinter` (stdlib) — the same stdlib-only rule the current
   overlay follows. `requirements.txt` is untouched.
3. **Never block when uncertain.** The existing rule stands: stale or
   missing ActivityWatch data → no action, ever.
4. **No process termination in v1.8.0.** "Process blocking" here means
   blocking the *process's window* (minimize + overlay). Killing processes
   risks data loss (unsaved work) and is explicitly deferred to a later
   phase with separate per-app consent. The `process_name` column is for
   *identification and logging*, not for `taskkill`.
5. **Hardcore is opt-in.** Default for every rule and session is `soft`
   (today's behavior). Firm/hardcore require an explicit user choice.
6. **Escape hatches always work:** emergency pass (tray + `/shield`),
   tray "Shield off", and a file kill-switch — all effective within one
   poll interval.
7. **No keylogging, no content capture.** The enforcer sees only what
   ActivityWatch already records: app name, window title, URL, window
   handle, process name.
8. **No JavaScript required** on the `/shield` page (forms + links +
   `<details>`, the existing pattern).

## What exists today (do not reinvent)

- `focuscore/blocker.py` — session-scoped enforcement. Pure functions
  `is_blocked()`, `get_current_window()`, `enforce_once()`; Windows-only
  `show_block_overlay()` (tkinter, fails silent); `run_enforcer()` loop;
  `ensure_guard_running()` spawns one guard subprocess per session.
- `focuscore/focus.py` — `BLOCK_LEVELS = {"strict": {-1,-2},
  "lenient": {-2}}`.
- M0 migration 2 (already on PC main) reserved the columns Phase 7
  needs — **no new columns on existing tables**:
  - `focus_sessions.enforcement_mode` TEXT DEFAULT `'strict'`
  - `focus_sessions.intercepted_count` INTEGER DEFAULT 0
  - `focus_blocks.action_taken` TEXT DEFAULT `'blocked'`
  - `focus_blocks.process_name` TEXT DEFAULT `''`
  - `focus_blocks.window_handle` INTEGER DEFAULT 0

## New concepts

### 1. Enforcement levels

| Level | Meaning | When |
|---|---|---|
| `soft` | Desktop notification + dismissible fullscreen overlay (today's behavior) | Default everywhere |
| `firm` | `soft` + **minimize the offending window** via Win32 `ShowWindow(hwnd, SW_MINIMIZE)` | Opt-in per rule / session |
| `hardcore` | `firm` + **overlay locked**: no "Back to work" button for `LOCK_SECONDS`; only "End session" (session shield) or the emergency pass dismisses it | Opt-in per rule / session |

`enforcement_mode` on a focus session ∈ `{'strict', 'hardcore'}` —
`'strict'` keeps today's behavior (maps to `soft` actions), `'hardcore'`
maps to the hardcore action chain. The M0 default `'strict'` is therefore
already correct; no migration change needed for the enum.

Named constants (module-level, documented like Phase 6):

| Constant | Value | Meaning |
|---|---|---|
| `POLL_SECONDS` | 5 | Classify-and-act cadence when no hook event fires (unchanged from today) |
| `FALLBACK_POLL_SECONDS` | 2 | Safety-net poll in case the Win32 foreground hook is dropped (R1) |
| `NOTIFY_DEDUPE_SECONDS` | 60 | One notification/overlay per app per minute (existing) |
| `LOCK_SECONDS` | 30 | Hardcore overlay lock duration |
| `PASS_DEFAULT_MINUTES` | 5 | Emergency pass length |
| `HUD_REFRESH_MS` | 2000 | HUD redraw cadence |
| `SHIELD_MUTEX_NAME` | `"FocusCore_Shield_Singleton_Mutex_v1"` | Win32 named mutex for single instance (R3) |
| `ELEVATED_UNKNOWN` | `"__ELEVATED_UNKNOWN__"` | Sentinel process name when identity can't be confirmed (R2) |

### 2. Block rules (always-on shield)

New table **`block_rules`** (migration 5):

```
id INTEGER PK, name TEXT, rule_type TEXT ('app'|'category'),
key TEXT,            -- app: lowercase exe/process name; category: taxonomy category
action TEXT,         -- 'soft'|'firm'|'hardcore'
days TEXT,           -- 'all' or CSV of 0-6 (Mon=0)
start_time TEXT,     -- 'HH:MM' or NULL (= all day)
end_time TEXT,       -- 'HH:MM' or NULL
enabled INTEGER DEFAULT 1, created_at TEXT
```

Schedule semantics: a rule is *active* when today ∈ days AND
(start_time IS NULL OR start_time ≤ now < end_time); overnight windows
(e.g. 22:00–06:00) wrap correctly. Pure function
`rule_is_active(rule, now)` — unit-tested, including the wrap case.

Rule matching priority (pure `resolve_action()`):
`emergency pass active` → allow everything >
`protected process` → never act >
`active focus session` (its block level + enforcement_mode) >
`matching block rule` (highest action wins among active rules) >
allow.

Per-activity user overrides (`store.get_overrides`) still win over
everything except the protected list — an activity the user re-scored
as productive is never blocked. (Existing `is_blocked()` guarantee,
kept.)

### 3. Emergency pass

New table **`block_passes`**: `id, started_at TEXT, minutes REAL,
reason TEXT, created_at TEXT`.

- From tray ("Emergency pass…") or `/shield` (reason typed in a form —
  deliberate friction, no one-click bypass).
- While a pass is active, the enforcer allows everything but keeps
  logging (the pass itself is the audit trail).
- Passes are listed on `/shield`; abuse is visible, not prevented —
  the user is an adult, the log is the accountability.

### 4. Protected processes (never touched)

`PROTECTED_PROCESSES = {"explorer.exe", "taskmgr.exe",
"focuscore.exe", "focus core.exe", "python.exe", "pythonw.exe",
"aw-qt.exe", "unins000.exe", "setup.exe", "consent.exe",
"system", "registry", ...}` — matched case-insensitively on
`process_name`. Pure `is_protected()` — unit-tested. The enforcer's
*own* windows (overlay, HUD) are excluded by process + window-class
check so the shield can never fight itself.

### 5. Win32 layer — new module `focuscore/win32.py` (revised per R1/R2/R3/R4)

Stdlib `ctypes` only. Every function guards `os.name == "nt"` and
fails silent (returns `None`/`False`):

- `acquire_singleton_mutex()` → mutex handle or `None` when another
  instance holds `SHIELD_MUTEX_NAME` (`CreateMutexW` +
  `ERROR_ALREADY_EXISTS == 183` check). Kernel-owned: no stale locks
  (R3).
- `get_foreground_info()` → `{hwnd, pid, process_name}` or `None`.
  hwnd via `GetForegroundWindow`; pid via `GetWindowThreadProcessId`;
  process name via `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` +
  `QueryFullProcessImageNameW` (basename, lowercased). On
  `ERROR_ACCESS_DENIED` (5) returns `process_name =
  "__ELEVATED_UNKNOWN__"` — the shield then fails open (R2).
- `minimize_window(hwnd)` → `ShowWindow(hwnd, SW_MINIMIZE)`; returns
  bool. Refuses `hwnd == 0` and refuses windows owned by protected
  processes (double-check at the call site).
- `window_still_foreground(hwnd)` — avoid minimizing a window the user
  already switched away from.
- `is_fullscreen_window(hwnd)` — rect matches monitor rect
  (`MonitorFromWindow` + `GetMonitorInfo`) AND `WS_EX_TOPMOST` set →
  overlay suspended for this window (R4).
- `iter_monitors()` — `(left, top, width, height)` per display via
  `EnumDisplayMonitors` (R4: overlay spans all monitors).
- `install_foreground_hook(callback)` → hook handle.
  `SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
  NULL, callback, 0, 0, WINEVENT_OUTOFCONTEXT |
  WINEVENT_SKIPOWNPROCESS)`; the callback runs on the pumping thread
  and must only enqueue the hwnd and return. `uninstall_foreground_hook(handle)`.
- `pump_messages(stop_event)` — `GetMessageW`/`TranslateMessage`/
  `DispatchMessageW` loop; the worker thread's heartbeat (R1/R5).
- `install_keyboard_swallow()` / `uninstall_keyboard_swallow()` —
  `WH_KEYBOARD_LL` hook that swallows Alt+Tab and Win keys during
  `LOCK_SECONDS` only; try/finally removal; failure → proceed without
  (fail-safe). Ctrl+Alt+Del cannot be intercepted by design (R4).

No pywin32, no psutil — dependency-free was a Phase 6 audit point and
stays one.

### 6. Shield daemon — new module `focuscore/shield.py` (revised per R1/R5)

Pure decision core + daemon loop:

- `rule_is_active(rule, now)`, `is_protected(process_name)` (True for
  `PROTECTED_PROCESSES` and `"__ELEVATED_UNKNOWN__"`),
  `pass_active(now, db_path)` (DB → in-memory → fallback file, R7),
  `resolve_action(...)` — pure, injected `now`, fully unit-tested.
- `shield_once(state, ...)` — one cycle: classify via the existing
  `blocker.get_current_window()` + `blocker.is_blocked()` machinery
  (reused, not duplicated), resolve action, execute via injected
  `act_fn` (mocked in tests). Fullscreen windows: overlay suspended,
  minimize still applies, skip is logged (R4).
- `run_shield()` — threading model per R5:
  - **Main thread:** tkinter root (withdrawn unless HUD enabled),
    `root.after(100, drain_ui_queue)`, `mainloop()`. Owns HUD +
    overlay windows. No tkinter/display → engine runs on main thread,
    UI degrades to notifications.
  - **Worker thread:** `install_foreground_hook()` +
    `pump_messages()`; hook callback enqueues hwnd (<1 ms, never
    blocks); engine loop `queue.get(timeout=FALLBACK_POLL_SECONDS)`
    → `shield_once()` → pushes `("overlay", …)` / `("hud_update", …)`
    commands to the UI queue. Never touches tkinter.
- `run_shield()` — the loop. Single instance via named mutex (R3);
  second start exits quietly. Stops when the kill-switch file exists
  or the tray asks it to.
- `ensure_shield_running()` — mirrors `ensure_guard_running()`:
  spawned once at app startup (server.py) and from the tray; the
  daemon then covers sessions *and* rules, so the old per-session
  guard becomes a thin wrapper (`python -m focuscore.blocker --enforce`
  keeps working by delegating to `shield.run_shield(session_only=True)`).

DB concurrency: WAL + `busy_timeout=5000` are already M0 standard;
the daemon opens short-lived connections per cycle (no long-held
write locks).

Kill switch: file `%LOCALAPPDATA%\Focus Core\shield.off` — the loop
checks each cycle and exits within `POLL_SECONDS`. Tray "Shield off"
writes it; "Shield on" deletes it and re-spawns.

### 7. HUD — new module `focuscore/hud.py`

`tkinter` stdlib, fails silent headless:

- ~240×90 always-on-top (`-topmost`), semi-transparent
  (`-alpha 0.85`), draggable by its header, no taskbar entry
  (`overrideredirect(True)` + toolwindow hint).
- Content (refreshed every 2 s from a pure `hud_snapshot(db_path)`
  also reused by `/shield`): shield state dot (green protected /
  amber session / grey off), current app + score color, blocks today,
  session elapsed or next rule window.
- Right-click (or a tiny × button) → menu: Open Focus Core / Shield
  off / Close HUD. Toggle persisted in a `hud_enabled` setting.
- Launched with the daemon; closing it never stops enforcement.

## Store API additions

- `create_block_rule(...)`, `get_block_rules()`,
  `set_block_rule_enabled(id, enabled)`, `delete_block_rule(id)`
- `create_pass(minutes, reason)`, `get_active_pass(now)`,
  `get_recent_passes(limit)`
- `record_block(...)` gains optional `action_taken`, `process_name`,
  `window_handle` (writes the M0 columns)
- `increment_intercepted(session_id)` (the M0 counter)
- `get_setting`/`set_setting` for `hud_enabled` (or reuse existing
  settings table — whichever exists on main)

## Migration 5 — `0005_shield_rules`

`block_rules` + `block_passes` tables, indexes on
`(enabled)`, `(rule_type, key)`. Follows the M0 adversarial-tested
pattern: `add_column_if_missing`-style `CREATE TABLE IF NOT EXISTS`,
idempotent, snapshot + rollback via the existing runner. **No changes
to migrations 1–4.**

## Routes & pages

- **`/shield`** (nav: "Shield"): status card (daemon state via lock
  file, active session, active rules count, today's blocks, active
  pass), enforcement explainer, rules table with enable/disable/delete,
  add-rule form (name, app-or-category, action radio defaulting to
  soft, days checkboxes, start/end time), emergency-pass form (minutes
  + required reason), today's block log (time, app, action taken,
  source: session/rule), HUD on/off toggle. No JS.
- **`/focus`** (existing): starting a session gains an
  enforcement-mode radio: Standard (today's behavior) / Hardcore
  (minimize + 30 s locked overlay). Stored in M0's
  `focus_sessions.enforcement_mode`.
- **`/help/shield`** article + `USER_GUIDE.md` + `CHANGELOG.md`
  (v1.8.0) + nav. Help explains the three levels in plain English,
  the emergency pass, and "if the shield blocks the wrong app"
  (re-score the activity / disable the rule — overrides win).

## Enforcement loop (daemon), revised per R1/R5

Event-driven; the worker thread blocks on the hook-event queue with a
`FALLBACK_POLL_SECONDS` timeout:

```
on SetWinEventHook(EVENT_SYSTEM_FOREGROUND) event (or 2 s timeout):
1. kill-switch file present? → exit loop
2. fg = win32.get_foreground_info()              # <1 ms on event
   hwnd unchanged AND this was a timeout (not an event)? → next
3. window = blocker.get_current_window(client)   # ActivityWatch identity
   stale/missing? → no action (never block when uncertain)
4. if shield.is_protected(fg.process_name): → no action
   # includes "__ELEVATED_UNKNOWN__" → fail open (R2)
5. if shield.pass_active(now): → log pass-coverage, no action
   # DB → memory → JSONL fallback (R7)
6. session = focus.get_active_session()
   action = shield.resolve_action(session, active_rules, window, now)
   # 'allow' | 'soft' | 'firm' | 'hardcore' + source
7. dedupe per app per 60 s (existing _LAST_NOTIFIED pattern)
8. execute: notify → ui_queue("overlay", …) → (firm+) minimize →
   (hardcore) locked overlay + scoped keyboard swallow
   (fullscreen window? overlay suspended, minimize still applies, logged)
   record_block(..., action_taken, process_name, window_handle)
   increment_intercepted(session_id) when session-sourced
```

## Safety rails (revised per Qwen R2/R4/R6/R7)

1. Protected list is checked twice: in pure `resolve_action()` *and*
   at the `minimize_window()` call site. List includes `explorer.exe`,
   `taskmgr.exe`, `dwm.exe`, `csrss.exe`, `wininit.exe`, `services.exe`,
   `LockApp.exe`, `LogonUI.exe`, `SearchUI.exe`,
   `StartMenuExperienceHost.exe`, Focus Core / ActivityWatch / Python /
   installer processes, **and the dynamic `__ELEVATED_UNKNOWN__`
   sentinel** (R2, R6).
2. Hardcore requires explicit opt-in; nothing upgrades silently.
3. The overlay/HUD windows can never be minimized by the enforcer
   (own-process check).
4. Stale data → no action (inherited rule, kept). Unidentifiable
   (elevated) process → no action (fail open, R2).
5. Emergency pass needs a typed reason (friction, not prevention);
   every pass is logged and listed; **pass creation never fails closed**
   — SQLite busy → JSONL fallback + in-memory (R7).
6. Kill switch file + tray toggle, effective ≤ 5 s.
7. No new network calls, no new dependencies, no admin rights needed
   (user32/ctypes calls work unprivileged for own-session windows).
8. Minimizing is reversible and data-safe by construction; that is
   *why* process-kill is out of scope for v1.8.0.
9. Fullscreen exclusive windows never get an overlay (R4); the HUD and
   overlays span all monitors via `EnumDisplayMonitors` (R4).
10. Keyboard swallow is scoped to `LOCK_SECONDS`, installed
    try/finally, and Ctrl+Alt+Del always remains available (R4).

## Tests (local gate: full suite green before packaging)

- `tests/test_shield.py` — pure logic: schedule windows (incl.
  overnight wrap), rule priority, protected list, pass active/expired,
  `resolve_action` matrix, override-wins, dedupe.
- `tests/test_win32.py` — mocked ctypes: minimize called/not-called
  matrix, protected double-check, headless skip (no tkinter/display
  → skip, the Linux-CI precedent).
- `tests/test_hud.py` — `hud_snapshot()` shape/content; HUD window
  creation skipped headless.
- `tests/test_migration_5.py` — M0 adversarial style: idempotency,
  rollback on injected failure, data preservation, custom indexes
  survive.
- Existing suite must stay green (322 passed / 1 skipped baseline).

## PC verification checklist (Step 30 analogue)

1. Install v1.8.0; M0→v5 migration runs, snapshot taken.
2. Start a Standard session, open a -2 app → notification + overlay.
3. Start a Hardcore session, open a -2 app → window minimizes, overlay
   locked 30 s, "End session" works.
4. Add rule "no X 09:00–17:00 weekdays" (firm) → outside a session,
   X minimizes when foregrounded in-window.
5. Emergency pass with reason → 5 min free, pass listed.
6. Tray Shield off → daemon stops ≤ 5 s; Shield on → resumes.
7. HUD appears, draggable, shows live state; closing it keeps
   enforcement running.
8. Uninstall keeps `focuscore.db`; dev server back to HTTP 200.

## Open questions — answered by the Qwen audit (2026-09-27)

1. ~~1 s hwnd-watch + 5 s classify?~~ → **Rejected.** Event-driven
   `SetWinEventHook` + immediate classification + 2 s fallback poll.
2. ~~`QueryFullProcessImageNameW` reliability?~~ → **Conditional pass**
   with elevation fail-open (`__ELEVATED_UNKNOWN__` → protected).
3. ~~`msvcrt.locking` robustness?~~ → **Failed.** Win32 named mutex.
4. ~~Is no-kill too conservative?~~ → **Strong pass.** Keep the
   deferral; harden with topmost multi-monitor overlay + scoped
   keyboard swallow.
5. ~~Missing safety rails?~~ → Addressed: fullscreen bypass,
   multi-monitor, tkinter threading split, expanded protected list,
   pass resilience.
