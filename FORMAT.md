# FORMAT.md — Focus Core data formats

This document describes the formats Focus Core writes to disk, precisely
enough that a stranger could write an independent reader. It documents
the code as it exists today (schema version: PRAGMA user_version = 11).
Where a format loses information, the loss is declared here — and the
tests in `tests/test_format_contract_119.py` assert exactly the losses
declared, never silence.

## Stability policy

Databases written by any released version of Focus Core remain readable by every later version: opening an older database migrates it forward in place (after a snapshot backup), old rows gain the new columns' defaults, and no release deletes or silently reinterprets stored user data. If a future format change cannot be expressed as a forward
migration, that change ships with a converter that reads every released
schema version, tested against the checked-in aged fixtures (section 2)
before release. Export formats follow the same rule: the
`focuscore.timesheet/v1` JSON shape is never changed silently; an
incompatible change gets a new schema string.

## 1. focuscore.db — the on-disk database

- **File:** a single SQLite database named `focuscore.db`
  (`focuscore/paths.py`). Installed builds keep it in the per-user data
  folder (`%LOCALAPPDATA%\Focus Core\` on Windows, `~/.focus-core`
  elsewhere); development runs use the repository root.
- **Journal:** WAL mode; connections set `PRAGMA busy_timeout = 5000`
  and `PRAGMA foreign_keys = ON` (SQLite enforces foreign keys only
  when asked; the app always asks).
- **Opening:** `focuscore.store.init_db(path)` is the normal open path.
  It creates a fresh database at the latest schema when the file is
  absent, and migrates an older one forward when it is behind.

### Schema versioning and migrations

- The current schema version is stored in `PRAGMA user_version`
  (currently **user_version = 11**; `focuscore.migrations.LATEST_VERSION`).
- Each applied migration is also recorded in the `schema_migrations`
  table: `(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)`.
- Migrations are numbered and append-only: a released migration is
  never edited; schema changes arrive as a new migration. Every
  migration is idempotent and runs inside its own `BEGIN IMMEDIATE`
  transaction. Before the first pending migration applies, the runner
  snapshots the database (section "Backups" below), so a failed upgrade
  leaves the pre-upgrade file recoverable.
- Migration history:

  | # | name | adds |
  |---|------|------|
  | 1 | `0001_afk_intervals` | `afk_intervals` table |
  | 2 | `0002_shield_columns` | focus-session enforcement columns, `block_passes` |
  | 3 | `0003_flowtime_pomodoro` | session-type/cycle columns, `timesheet_entries.session_id` |
  | 4 | `0004_projects_budget_billable` | project billing/budget columns |
  | 5 | `0005_shield_rules` | `block_rules` |
  | 6 | `0006_session_modes` | `session_cycles`, `settings` |
  | 7 | `0007_finance` | integer-minor-unit money columns on projects/timesheets, `finance_audit_events`, `project_budget_ledger` |
  | 8 | `0008_invoicing` | `invoices`, `invoice_lines`, `invoice_payments`, `invoice_counters` |
  | 9 | `0009_gamification` | `badges`, `xp_ledger` |
  | 10 | `0010_encrypt_title_columns` | seals title/URL columns at rest (below) |
  | 11 | `0011_orphan_quarantine` | `orphaned_rows` quarantine for child rows whose parent vanished |

### Table inventory (current schema)

Tracking and categorisation:

- `activities` — one row per tracked window: `ts` (local ISO text),
  `duration` (**seconds**, REAL), `app`, `title`, `url`, `category`,
  `score`, `override_score`, `match_key`, `day` (`YYYY-MM-DD`).
- `afk_intervals` — raw away intervals: `day`, `start_ts`, `end_ts`,
  `duration_seconds` (REAL seconds), `status`.
- `day_stats` — per-day rollups keyed by `day`: `afk_seconds`,
  `total_seconds` (REAL seconds).
- `categories` — scoring taxonomy: `name` (PK), `parent`, `score`,
  `custom`. `overrides` — per-app score overrides: `match_key` (PK),
  `score`.

Goals and alerts:

- `goals` — `name`, `direction` (`more`/`less`), `target_type`,
  `target_name`, `threshold_minutes`, `threshold_pulse`, `pinned`.
- `alerts` — `name`, `target_type`, `target_name`,
  `threshold_minutes`, `message`, `cooldown_minutes`, `enabled`.
- `alert_firings` — `alert_id`, `fired_at`, `current_minutes`.

Focus sessions and blocking:

- `focus_sessions` — `label`, `planned_minutes`, `started_at`,
  `planned_end_at`, `ended_at`, `status`, `block_level`,
  `enforcement_mode`, `session_type`, cycle counters.
- `focus_blocks` — apps/windows intercepted during a session:
  `session_id`, `ts`, `app`, `title`, `url`, `score`, `category`,
  `action_taken`.
- `block_rules` — user blocking rules: `rule_type` (`app`/`category`),
  `key`, `action` (`soft`/`firm`/`hardcore`), `days`, time window,
  `enabled`.
- `block_passes` — temporary passes: `started_at`, `minutes`, `reason`.
- `session_cycles` — pomodoro work/break cycles per session.

Projects, timesheets, finance:

- `projects` — `name`, `client`, billing flags, `hourly_rate_minor`
  (integer minor units) with `rate_currency` (ISO code), budget caps.
  (`hourly_rate` REAL is a legacy dollar column kept for old readers;
  exports read the minor-unit columns.)
- `timesheet_entries` — the billable record: `day`, `start_ts`,
  `end_ts`, `minutes` (**minutes**, REAL — exports convert to integer
  seconds with `int(round(minutes * 60))`), `category`, `app`,
  `title`, `project_id`, `task`, `note`, `status`, `session_id`,
  `is_billable`, `hourly_rate_minor`, `rate_currency`, `rate_status`
  (`unknown`/`confirmed`/`estimated`), `invoice_id`.
- `invoices`, `invoice_lines`, `invoice_payments`, `invoice_counters`
  — invoice header/lines/payments and the per-year number counter; all
  money is integer minor units (`*_minor` columns).
- `finance_audit_events` — append-only finance event log (`payload_json`).
- `project_budget_ledger` — budget-cap history per project/period.

Bookkeeping:

- `settings` — `key`/`value` app settings.
- `badges`, `xp_ledger` — gamification awards and XP history.
- `orphaned_rows` — quarantined child rows (source table, row copy in
  `row_json`, quarantine time) kept instead of being destroyed.
- `schema_migrations` — applied-migration records (above).
- (`sqlite_sequence` is SQLite's own AUTOINCREMENT bookkeeping.)

### Units and conventions

- Timestamps are ISO-8601 local-time text (e.g. `2026-02-03T09:00:00`);
  `day` columns are `YYYY-MM-DD` in the same local timezone.
- Durations: `activities`/`afk_intervals`/`day_stats` store **seconds**;
  `timesheet_entries` stores **minutes**, converted to integer seconds
  at export. Money is always integer minor units (cents) next to an
  ISO currency code; amounts are computed as
  `duration_seconds × hourly_rate_minor / 3600`, rounded half-up.
- **Sealed columns:** since migration 0010, `activities.title`,
  `activities.url`, `focus_blocks.title`, `focus_blocks.url` and
  `timesheet_entries.title` are stored sealed: the text `dpapi:v1:`
  followed by base64 of the protected bytes (Windows DPAPI; the bytes
  are bound to the Windows user). Unsealed legacy values pass through
  unchanged. A value that cannot be opened on the current user reads
  back as the placeholder
  `(unreadable — encrypted for a different Windows user)` — never a
  crash, never a silent blank. Every normal read path unseals
  transparently.

### Backups

Backups are plain copies of the database file named
`focuscore-YYYYMMDD-HHMMSS.db` (a `-2`, `-3`… suffix on collision)
with a `<name>.db.sha256` sidecar holding the SHA-256 hex digest.
Encrypted backups, when enabled, are a separate `.db.enc` envelope and
do not change the database format itself.

## 2. The aged fixture

`tests/fixtures/aged_schema_v1.db` is a frozen database at schema
version 1 — user_version reads 1, `schema_migrations` records only
`0001_afk_intervals`, and titles are still plaintext (pre-0010). It was
built once from the base schema plus migration 0001 and seeded with
one sentinel row per then-existing table (an activity titled
"Sentinel Title — café ☕ (Alpha)", a project, a focus session,
a 90-minute timesheet entry, an AFK interval…); the bytes are checked
in and are never regenerated by the test suite.
`tests/test_format_contract_119.py` opens a *copy* through
`store.init_db`, lets it migrate to the current version, and asserts
every sentinel value survives — including the 0010 sealing round-trip.
When a release raises the minimum readable version, add a new aged
fixture for the new floor instead of editing this one.

## 3. Export formats (timesheet exports)

All three formats come from one row builder
(`focuscore.exports.build_export_rows`), so they cannot drift apart.
Only `accepted` entries are exported. Every export carries a redaction
manifest saying which sensitive fields (app names, window titles,
URLs, notes) it includes — visible in the HTML, as a `manifest` object
in JSON, and as the first line `# manifest: {…}` in CSV. Client-facing
exports exclude app names, window titles and URLs by default; the
detailed CSV below is an explicit, audit-logged internal opt-in.

### JSON — `focuscore.timesheet/v1` (exact)

Top level: `schema` (always `"focuscore.timesheet/v1"`), `generated_at`,
`filters`, `manifest`, `entries`, `totals`. Each entry carries exactly:
`day`, `start_ts`, `end_ts`, `duration_seconds` (integer),
`project`, `client`, `task`, `note` (empty unless notes were opted
in), `category`, `billable` (boolean), `hourly_rate_minor` (integer
or null), `rate_currency`, `rate_status`, `amount_minor` (integer or
null). Totals: `seconds_total`, `seconds_billable`,
`confirmed_minor`, `estimated_minor`, `unknown_billable_seconds`,
`entries_without_rate`, plus the export currency. Integers everywhere:
a re-import reproduces durations and amounts **exactly**. JSON never
carries app names or window titles, and exports have no entry `id` —
identity is `(day, start_ts)` ordering, not a stable key.

### Client CSV

Columns: `date,start,end,duration_hours,project,client,task,billable,
hourly_rate,rate_status,amount`, after the `# manifest:` line.
Durations render as hours with **two decimal places** (`1.50`), money
as `$50.00`-style text. Fields starting with `=`, `+`, `-`, `@` are
prefixed with `'` so spreadsheets cannot execute them (the prefix is
part of the declared format; JSON carries the raw text).

### Detailed CSV (internal opt-in)

The client columns plus `app,window_title` (decrypted). Still no
`category`, no `note`.

### HTML statement

A standalone print page (inline CSS, no scripts, all user text
HTML-escaped). Rows show Date, Start, Task, Hours (two decimal
places), Billable, Rate, Amount; per-day totals and a summary table
(Tracked time as `3h 00m`-style whole minutes, Confirmed/Estimated
amounts separated, Unrated time listed). It is a rendering, not a
data interchange format.

### Declared losses (the honesty list)

- CSV and HTML durations carry two decimal places of an hour:
  re-importing them lands within **18 seconds** of the stored value,
  and some values visibly change (`1500s` → `0.42h` → `1512s`). Use
  JSON when exactness matters.
- CSV money is rendered text (`$90.00`); the numeric original is the
  JSON `amount_minor`.
- A missing rate or a non-billable entry exports its amount as an
  empty field in CSV and an em dash (`—`) in HTML — never a `0` or
  an invented figure. Unknown-rate time is totalled separately
  (`unknown_billable_seconds`), not folded into money.
- `category` reaches only JSON; `note` reaches only JSON (and only
  when opted in); app/window-title reach only the detailed CSV.
  End times reach JSON and CSV but are not rendered in HTML.
- No format exports the entry `id`: an export is a period report,
  not a database dump. The database itself (section 1) is the
  lossless artefact, and the fixture test above proves it stays
  readable.
