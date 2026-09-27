# Stability & Hardening Blueprint — Sprint 4 (v1.12.0)

**Scope:** Phases 1–10 (v1.1.0 → v1.11.0). Zero new features. Zero schema
changes. Zero behavior changes visible to the user unless the current
behavior is a bug.

**Guiding rule:** Every change must be provably safe — covered by a test,
reversible, and invisible when things work. If we can't prove it's safe,
we don't ship it.

---

## 1. Daemon & Process Resilience

### 1.1 Shield daemon (`focuscore/shield.py`, ~852 lines)

**Current state:** Single process, stdlib ctypes + tkinter only. Main loop
polls the foreground window, evaluates block rules, shows overlay via a
tkinter UI queue. Graduated enforcement: soft → firm → hardcore.

**Hardening items:**

| # | Item | Risk if ignored |
|---|------|-----------------|
| S-1 | **Watchdog for the shield process.** If shield.exe dies (crash, OOM
killer, user kills it), blocking silently stops and the user thinks
they're protected. Add a lightweight supervisor: the tray process checks
shield heartbeat (a timestamp file updated every 30s) and restarts it,
logging the restart. | User works unprotected believing Shield is on |
| S-2 | **Crash-safe overlay teardown.** If the daemon crashes while a
fullscreen overlay is up, the overlay window can orphan (stuck on
screen, no parent to close it). Overlays must register a
`WM_CLOSE`-independent kill path: write overlay HWND to a file on show,
delete on clean close; supervisor kills orphan HWNDs on restart. | Frozen screen requiring Task Manager |
| S-3 | **Enforcement state survives restart.** If the daemon restarts
mid-focus-session, it must re-read the active session from the DB
(`session_cycles`) rather than assuming "no session." Currently the
in-memory session state is lost on restart. Add: on startup, query for
any session with `status='active'` and resume enforcement. | Blocking gap after crash during a session |
| S-4 | **Bounded UI queue.** The `ui_q` between the poll thread and tkinter
thread is unbounded (`get_nowait` in a `while True` loop). A stuck
foreground app generating rapid window-change events could flood it.
Cap at 100, drop oldest with a counter. | Memory growth during pathological window churn |
| S-5 | **Poll interval backoff on error.** If `ctypes` window queries start
failing (e.g., during UAC prompt / secure desktop), the loop should
back off (1s → 5s → 30s) instead of hot-spinning on exceptions. | CPU spike during secure desktop |

### 1.2 Focus session engine (`focuscore/focus.py`)

| # | Item | Risk |
|---|------|------|
| F-1 | **Sleep/resume already handled (R2), but verify hibernate.** The
R2 snap-back logic covers sleep; test hibernate (longer, clock jumps
hours) explicitly on the PC gate. | Inflated session after hibernate |
| F-2 | **Tick watchdog.** `update_cycle_ticks` writes every tick; if ticks
stop arriving (process suspended), the session should be marked
`interrupted`, not left `active` forever. Add: any `active` session
with `last_tick_wall` older than 15 minutes gets auto-interrupted on
next dashboard load. | Zombie sessions blocking "start new session" |

### 1.3 Tray (`focuscore/tray.py`) and launcher (`focuscore/launcher.py`)

| # | Item | Risk |
|---|------|------|
| T-1 | **Tray double-instance guard.** Two tray processes = two shield
supervisors = restart fights. Use a named mutex
(`CreateMutexW("FocusCoreTray")`); second instance exits with a log
line. | Competing supervisors |
| T-2 | **Launcher port race.** `ensure_server()` checks `port_open` then
starts the server — TOCTOU if two launchers run simultaneously.
Bind with `SO_REUSEADDR` off and let the second bind fail gracefully,
then attach to the winner. | Two Flask servers, confused dashboard |
| T-3 | **Silent-install firewall prompt.** First Flask bind can trigger a
Windows Firewall prompt on some configs. Document; consider binding
`127.0.0.1` explicitly (already done — verify no regression). | Install friction |

---

## 2. Database Concurrency & Performance

### 2.1 Current posture (verified)

- WAL journal mode + `busy_timeout=5000` on every connection
  (`store.get_db`, `migrations.ensure_wal_and_timeout`).
- All `get_db()` call sites across `store.py`, `dashboard/app.py`,
  `invoices.py`, `budgets.py`, `forecast.py` close their connections
  (0 leaks found by static scan, 2026-09-27).
- Invoice numbering uses `BEGIN IMMEDIATE` + `UPDATE ... AND
  next_number=?` + rowcount check (race-tested: 4 threads, unique
  sequential numbers).
- 17 indexes across migrations 1–8; migration 8 added 6.

### 2.2 Hardening items

| # | Item | Detail |
|---|------|--------|
| D-1 | **Connection-per-request audit in dashboard.** Flask opens a new
SQLite connection per DB call (not per request). Under concurrent
requests (user clicks fast, tray polls), this is fine for reads, but
**every write path must be checked for `BEGIN IMMEDIATE`**. Scan all
`INSERT/UPDATE/DELETE` in `dashboard/app.py` route handlers: any
multi-statement write without an explicit transaction is a race. Fix:
wrap in `BEGIN IMMEDIATE`/`COMMIT` like `invoices.py` does. |
| D-2 | **WAL checkpoint policy.** WAL grows unboundedly if no checkpoint
runs. SQLite auto-checkpoints at 1000 pages by default, but a
long-running dashboard process holding a read transaction can pin the
WAL. Add: `PRAGMA wal_checkpoint(TRUNCATE)` on clean dashboard
shutdown + a weekly checkpoint in the backup job. Target: WAL file
stays under 5 MB in normal use. |
| D-3 | **Query-plan regression tests.** Add `EXPLAIN QUERY PLAN` assertions
for the 5 hottest queries (timesheet day view, invoice list, budget
status, activity ingest, dashboard home). If a future change drops an
index usage to SCAN, the test fails. (Phase 10 plan called for this;
not yet implemented.) |
| D-4 | **Busy-timeout uniformity.** `store.get_db` sets 5000ms, but
`backup.py` line 207 uses `timeout=30.0` and some ad-hoc
`sqlite3.connect` calls in tests/tools may set none. Grep the whole
tree for bare `sqlite3.connect` and route them through `get_db` or set
the pragma. One timeout policy everywhere. |
| D-5 | **Index on `invoice_lines.timesheet_entry_id`.** Migration 8 created
`idx_invoice_lines_invoice` but the FK `timesheet_entry_id REFERENCES
... ON DELETE RESTRICT` has no dedicated index (the downgrade helper
references `idx_invoice_lines_entry`, suggesting it was intended).
`DELETE FROM timesheet_entries` does a full scan of `invoice_lines`
without it. Add via a **non-versioned idempotent index migration**
(`CREATE INDEX IF NOT EXISTS`, no user_version bump — indexes don't
change semantics). |
| D-6 | **Long-transaction guard.** No write transaction should hold the DB
longer than ~2s in normal operation. Add a debug-mode timer:
if `FOCUSCORE_DEBUG=1`, log any transaction open >2s with the calling
function name. Catches future regressions, zero prod overhead. |

### 2.3 What we explicitly will NOT do

- No connection pooling (SQLite + WAL doesn't need it; adds complexity).
- No `user_version` bump (no schema changes — D-5 uses idempotent DDL only).
- No migration 9.

---

## 3. Financial & Ledger Robustness

### 3.1 Current posture (verified)

- All money in integer minor units; `money.pct_of_minor` uses integer
  arithmetic (`amount * pct // 100`).
- Invoice state machine enforced at **three layers**: Python
  (`_require_draft`, transition map), DB triggers
  (`trg_invoices_immutable`, line immutability, payment guard), and
  `ON DELETE RESTRICT` FKs.
- Budget ledger is append-only; rollover is pure computed (never
  written).

### 3.2 Hardening items

| # | Item | Detail |
|---|------|--------|
| M-1 | **Rounding-consistency fuzz test.** Property test: for 10,000 random
(subtotal, tax_pct, discount_pct) triples, assert
`discount + taxable == subtotal` and `taxable + tax == total` exactly,
and `total >= 0`. Catches any future float creep. (Phase 10 used
`int(round(minutes*60))` in one spot — exact-arithmetic audit should be
continuous, not one-time.) |
| M-2 | **Invoice load test.** 50 concurrent `send_invoice` calls on one
counter year → assert 50 unique sequential numbers, no gaps beyond the
expected range, no `INV-YYYY-0000`. Extends the existing 4-thread test
to a realistic burst (user spams "Send" + tray + API). |
| M-3 | **Payment race test.** Two concurrent `record_payment` calls for the
full balance → exactly one must flip status to `paid`; the other must
record as overpayment (balance stays 0, warning shown). Assert no
negative balance and no lost payment row under concurrency. |
| M-4 | **Ledger append-only audit.** Nightly self-check (or test): scan
`project_budget_ledger` and `finance_audit_events` for any `UPDATE` or
`DELETE` — the tables should only ever grow. Implement as a test that
runs the full suite then asserts row counts only increased. (Cheap
invariant, high value.) |
| M-5 | **Trigger-semantics lock test.** The six migration-8 triggers encode
Qwen's Q1 invariants. Add a test that attempts every forbidden
transition directly via SQL (`draft→paid`, `paid→sent`, `void→draft`,
line edit on sent, payment delete) and asserts each raises. This is the
contract GLM/Qwen audit against — it must be explicit, not implied. |

---

## 4. UI/UX Polish & Consistency

### 4.1 Dashboard (`dashboard/app.py`, 3,651 lines — the biggest risk)

The dashboard grew organically across 10 phases. Polish, not redesign:

| # | Item | Detail |
|---|------|--------|
| U-1 | **Split `app.py` into blueprints.** 3,651 lines in one file is a
merge-conflict and onboarding hazard. Split into
`dashboard/routes/{core,timesheet,focus,invoices,budgets,system}.py`
with a shared `helpers.py`. **No route URL changes, no HTML changes —
pure file move.** Verify with the full test suite (route tests catch
regressions). |
| U-2 | **Navigation consistency audit.** Every page must show the same nav
bar in the same order: Focus, Timesheet, Projects, Budgets, Invoices,
Intelligence, Coaching, Help. Grep each route's HTML for the nav block;
add a test asserting all registered routes render the nav. (Invoices
nav was added in Phase 10 — verify no page was missed.) |
| U-3 | **Empty-state copy.** Every list page (invoices, budgets, timesheet
day with no entries, goals with no goals) needs a one-line empty state
telling the user what to do next, not a blank table. Audit all list
routes; fill gaps. |
| U-4 | **Currency formatting consistency.** Invoices show `$1,234.56`;
budgets show `123456¢` in some spots (minor units leaked to UI). Grep
for `format_minor` vs raw minor-unit display; every user-visible money
figure must go through `money.format_minor`. Add a test that renders
each money page and asserts no bare 5+ digit integers adjacent to a
currency code. |
| U-5 | **Mobile/responsive sanity.** The dashboard is desktop-first, but the
user checks it on his Pixel 7 (weekly review inputs). Add a
`<meta name="viewport">` tag to the base template and spot-check the
5 most-visited pages at 360px width. No redesign — just no horizontal
scroll on tables (wrap in `overflow-x:auto`). |
| U-6 | **Help article coverage.** 13 articles exist (Phase 5). Phase 6–10
added: Intelligence, Shield/HUD, Flowtime/Smart Pomodoro, Budgets,
Exports, Invoices, Rollover. Every new page needs its footer help link
→ add articles + nav mapping, following the Phase 5 pattern. |
| U-7 | **Consistent date/time display.** Some pages show `2026-09-27`,
others `Sep 27`. Pick one (ISO `YYYY-MM-DD` — unambiguous for a
non-native English speaker) and normalize. |

### 4.2 What we explicitly will NOT do

- No visual redesign, no new CSS framework, no dark mode.
- No new pages. No new nav items.
- No changes to the invoice print HTML (it's a legal/financial
  document — frozen).

---

## 5. Targeted Questions for GLM 5.3's Deep-Thinking Stress Audit

These are the five questions where GLM's adversarial perspective is
most valuable. Each names the exact subsystem, the failure mode we
worry about, and what a "pass" looks like.

**Q-GLM-1 — Shield daemon vs. SQLite writer starvation.**
The shield poll loop writes enforcement events to SQLite every few
seconds while the dashboard may hold a write transaction (e.g., during
invoice send's `BEGIN IMMEDIATE`). With `busy_timeout=5000`, the loser
waits — but what happens when the shield's write waits the full 5s
during a long dashboard transaction? Does the poll loop stall (missing
window-change events → blocking gap), or does it degrade gracefully?
*Pass criteria:* a written sequence diagram showing the worst-case
interleaving, plus a concrete recommendation (e.g., "shield writes must
never wait >500ms; use a separate retry queue").

**Q-GLM-2 — Tkinter thread-safety under exception storms.**
The shield runs tkinter on the main thread and a poll worker on a
background thread, communicating via `ui_q`. Python's tkinter is not
thread-safe. We only touch tkinter from the main thread today — but
what happens when the worker thread raises while the main thread is
inside `root.mainloop()` processing a malformed queue payload? Enumerate
every `ui_q.put` call site and classify: can any payload cause the main
thread to raise inside the event loop? *Pass criteria:* a call-site
inventory with a verdict per site (safe / needs guard), and a
recommendation for a poison-pill shutdown path.

**Q-GLM-3 — WAL-mode backup/restore race.**
`backup.py` copies the DB with the atomic temp-file + `os.replace`
pattern, and Google Drive sync may read the backup mid-write. But the
harder race: a backup starts while a dashboard write transaction is
open in WAL mode. Does the backup capture a consistent snapshot, or can
it capture a torn WAL (DB page from before the transaction, WAL frame
from after)? SQLite's `VACUUM INTO` / online-backup API exists for this
reason. *Pass criteria:* verdict on whether the current file-copy
approach is safe under WAL + concurrent writer, with a test design to
prove it either way.

**Q-GLM-4 — Invoice counter exhaustion and year rollover.**
`invoice_counters(year PRIMARY KEY, next_number)` — what happens at
`next_number = 9999` (format is `INV-YYYY-NNNN`, 4 digits)? What happens
on Jan 1 when the year changes mid-`BEGIN IMMEDIATE` (two sends racing
across the year boundary)? What happens if the system clock jumps
backward (NTP correction, dual-boot)? *Pass criteria:* a decision for
each edge (widen format? cap with error? monotonic clock guard?) with
the invariant each decision protects.

**Q-GLM-5 — Memory growth in long-running processes.**
Three processes run for days: tray (with shield supervisor), dashboard
(Flask), shield daemon. Identify every unbounded growth vector: the
`ui_q` queue (S-4 above), Flask request logs, the in-memory block-rule
cache, tkinter overlay references, Python's SQLite statement cache.
*Pass criteria:* a ranked list of leak vectors by expected severity,
each with a measurement method (e.g., "sample `tracemalloc` after 72h")
and a proposed bound or flush policy.

---

## Appendix: Non-goals (explicitly out of scope for v1.12.0)

1. No new features, pages, or settings.
2. No schema changes (`user_version` stays 8).
3. No dependency changes (stdlib + Flask + current pins only).
4. No installer changes except version bump to 1.12.0.
5. No changes to invoice print HTML, export formats, or the JSON schema
   (`focuscore.timesheet/v1` is a public contract).
6. The `app.py` blueprint split (U-1) is the only structural refactor;
   everything else is additive hardening.

## Appendix: Acceptance bar for v1.12.0

- Full pytest suite green locally **and** on the PC gate (465+ tests).
- Ruff E/F clean.
- No new `sqlite3.connect` without `busy_timeout`; no new unbounded
  queues/threads.
- Every item above has either a test or a written "verified safe, no
  change needed" note from the GLM audit.
- Qwen 3.8 Max issues binding constraints before implementation begins.
