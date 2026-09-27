# Phase 10 Plan — Client Invoicing + Budget Rollover & Forecasting

Version target: **v1.11.0**. Status: **DRAFT — pending Qwen 3.8 Max audit.
Nothing builds until the audit clears this plan.**

## 1. Goal

Complete the money loop started in Phase 9 (v1.10.0):

1. **Client Invoicing** — turn accepted timesheet entries into real
   invoices: sequential invoice numbers, frozen line items, tax/discount,
   partial payments, paid/unpaid/overdue tracking, printable standalone
   HTML invoice (same zero-dependency approach as the Phase 9 Timesheet
   Statement).
2. **Budget Rollover & Forecasting** — unused weekly/monthly budget rolls
   into the next period (capped, non-punitive), plus a working-day-aware
   forecast of where the current period's budget lands by period end.

Both features inherit Phase 9's binding constraints: integer minor units
for all money (no float), append-only financial history, advisory-only
budgets (never blocking), explicit audit events, privacy-safe outputs.

## 2. Client Invoicing — specification

### 2.1 Invoice lifecycle

```
draft → sent → paid
  ↓       ↓
void    void
```

- **draft**: editable (lines, tax, discount, dates, notes). Not yet a
  financial record.
- **sent**: frozen. Totals are snapshotted at send time. No edits —
  corrections require void + reissue (new number). This keeps every
  issued invoice permanently reproducible.
- **paid**: balance reaches zero (sum of payments ≥ total). Set
  automatically when payments cover the total; a zero-total invoice can
  be marked paid manually.
- **overdue**: NOT stored — computed for display as
  `sent AND due_date < today AND balance > 0`.
- **void**: terminal. The number is retained forever (gapless audit
  trail). Linked entries are released back to uninvoiced so they can be
  re-billed on a new invoice. Void requires a reason, audit-logged.

### 2.2 Invoice numbers

Format `INV-2026-0001`: prefix `INV-`, 4-digit year, 4-digit sequence,
zero-padded. Sequence is **per calendar year** and resets each January.
Generated inside the creation transaction from `invoice_counters`
(`SELECT …; UPDATE …`) so two simultaneous creations can never collide.
`invoice_number` has a UNIQUE constraint as a backstop. Voided invoices
keep their numbers — the sequence never reuses a number.

### 2.3 Line items

Two kinds:

- **Entry lines**: built from accepted, billable, uninvoiced timesheet
  entries in a chosen date range for one project. At creation the line
  **freezes** a snapshot: description (task, else `"{category} — {date}"`),
  seconds, `rate_minor`/`rate_currency` copied from the entry's Phase 9
  rate snapshot, and `amount_minor` computed then. Later rate changes or
  entry edits never alter an existing line (draft lines excepted — see
  below).
- **Manual lines**: description + hours + rate, for fixed fees, expenses
  passed through, or corrections. `entry_id` is NULL.

Draft invoices may add/remove lines. Removing an entry line releases the
entry (`invoice_id = NULL`) so it can be picked up by another draft.
**Double-invoicing protection**: entry selection filters
`invoice_id IS NULL`; the link `UPDATE … WHERE invoice_id IS NULL` runs
in the same transaction as invoice creation; a UNIQUE partial index is
not needed because the column holds at most one invoice id by
construction, and tests will assert no entry appears on two non-void
invoices.

Entries on **locked days** may be invoiced (the day lock protects the
time record; linking an invoice id does not change hours, rate, or
money). The link operation is audit-logged.

### 2.4 Totals math (integer only)

- `subtotal_minor` = Σ line `amount_minor`
- `discount_minor` = half-up(`subtotal_minor` × `discount_bps` / 10000)
- `taxable_minor` = `subtotal_minor` − `discount_minor`
- `tax_minor` = half-up(`taxable_minor` × `tax_bps` / 10000)
- `total_minor` = `taxable_minor` + `tax_minor`

Tax and discount are **per-invoice basis points** (INTEGER, e.g. 750 =
7.5%), never floats, never per-line in v1. All rounding uses
`Decimal` with `ROUND_HALF_UP` (reuse `focuscore/money.py`). Drafts
recompute on every view; at send time the four totals are **frozen**
onto the invoice row so a sent invoice never depends on recomputation.

### 2.5 Payments

`invoice_payments` rows: date, `amount_minor`, note. Multiple partial
payments allowed. `balance_minor = total_minor − Σ payments`
(recomputed; payments are never edited, only added — a mistaken payment
is corrected by adding a negative adjusting payment, audit-logged).
Status flips to `paid` automatically when `balance_minor <= 0` and
`total_minor > 0`.

### 2.6 Scope cuts (deliberate)

- **One project per invoice** (`invoices.project_id` NOT NULL). A client
  with several projects gets one invoice per project. Multi-project
  invoices are a later phase if real users ask.
- **No invoice JSON export in v1.** The printable HTML + audit events
  are the record. (Phase 9 set a JSON precedent; revisit if needed.)
- **No email sending.** The invoice is printed/saved as PDF from the
  browser, same as Phase 9 statements.
- **Single currency per invoice**, inherited from the project's rate
  currency. Mixed-currency invoices are out of scope.

## 3. Budget Rollover & Forecasting — specification

### 3.1 Rollover rules

For a finished period P (week/month) and project:

- `unused_seconds = max(0, base_cap_seconds − consumed_seconds_P)`
- `unused_minor = max(0, base_cap_amount_minor − confirmed_minor_P)`
  (money rollover counts **confirmed** amounts only — consistent with
  Phase 9 M2.5; unrated time never creates money rollover).
- Rollover applies to period P+1 **only if P+1 has its own base cap**
  (a cap must exist to extend; rollover never creates a cap from
  nothing).
- **Capped**: rollover into P+1 ≤ `rollover_max_pct`% of P+1's base cap
  (default 50, global setting, per-project toggle `rollover_enabled`
  default ON). Prevents runaway accumulation.
- **No chaining**: rollover into P+1 is computed from P's base cap and
  P's spend only. Budget that rolled into P and went unused does not
  roll again — it simply expires. Predictable and easy to explain.
- **No negative rollover**: overspend in P never reduces P+1's cap.
  Budgets stay advisory and non-punitive (Phase 9 constraint D).
- **Computed, not stored**: `effective_cap(P) = ledger_cap(P) +
  rollover_into(P)`. The Phase 9 ledger stays append-only and honest;
  rollover is always shown as a separate line ("+6h rollover") so a
  report reader can see base vs rolled amounts.

### 3.2 Forecast rules

For the **current** period only, per project and period type:

- `effective_cap` = base cap + rollover in (both hours and money).
- `projected` = working-day-aware projection reusing the Phase 9 pacing
  engine (consumed so far + daily rate × remaining working days).
- Verdicts reuse Phase 9 bands and materiality thresholds:
  `on track` (projected ≤ 100%), `likely over` (>100% by a material
  amount). Suppression rules carry over unchanged: quiet on
  non-working days, quiet with < 2 elapsed working days, quiet when the
  projected overage is immaterial.
- Extra output: `rollover_out_forecast = max(0, effective_cap −
  projected)` — "at this pace, ~X would roll into next period".
- Display: one forecast line under each budget bar on the project card,
  plus a `forecast` block in `budget_status()` output. **Not** added to
  client exports in v1 — exports stay factual/historical (a forecast is
  a prediction, not a record).

## 4. Migration 8 — schema

```sql
-- Invoice header. status: draft | sent | paid | void (overdue computed).
CREATE TABLE invoices (
    id               INTEGER PRIMARY KEY,
    invoice_number   TEXT NOT NULL UNIQUE,          -- INV-2026-0001
    project_id       INTEGER NOT NULL REFERENCES projects(id),
    client           TEXT NOT NULL DEFAULT '',
    issue_date       TEXT NOT NULL,                 -- YYYY-MM-DD
    due_date         TEXT NOT NULL,                 -- YYYY-MM-DD
    period_from      TEXT NOT NULL,                 -- billed range
    period_to        TEXT NOT NULL,
    discount_bps     INTEGER NOT NULL DEFAULT 0,   -- basis points
    tax_bps          INTEGER NOT NULL DEFAULT 0,
    currency         TEXT NOT NULL DEFAULT 'USD',
    subtotal_minor   INTEGER NULL,                  -- frozen at send
    discount_minor   INTEGER NULL,
    tax_minor        INTEGER NULL,
    total_minor      INTEGER NULL,
    status           TEXT NOT NULL DEFAULT 'draft',
    notes            TEXT NOT NULL DEFAULT '',
    created_at_utc   TEXT NOT NULL,
    sent_at_utc      TEXT NULL,
    voided_at_utc    TEXT NULL,
    void_reason      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_invoices_project ON invoices(project_id);
CREATE INDEX idx_invoices_status ON invoices(status);

-- Frozen line items. entry_id NULL => manual line.
CREATE TABLE invoice_lines (
    id            INTEGER PRIMARY KEY,
    invoice_id    INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    entry_id      INTEGER NULL REFERENCES timesheet_entries(id),
    description   TEXT NOT NULL,
    seconds       INTEGER NOT NULL DEFAULT 0,
    rate_minor    INTEGER NULL,
    rate_currency TEXT NOT NULL DEFAULT 'USD',
    amount_minor  INTEGER NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_invoice_lines_invoice ON invoice_lines(invoice_id);

-- Per-year invoice number counters (race-safe inside transactions).
CREATE TABLE invoice_counters (
    year        TEXT PRIMARY KEY,                   -- '2026'
    last_number INTEGER NOT NULL DEFAULT 0
);

-- Payments are append-only; corrections are adjusting (possibly
-- negative) payments, never edits.
CREATE TABLE invoice_payments (
    id             INTEGER PRIMARY KEY,
    invoice_id     INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    paid_date      TEXT NOT NULL,                   -- YYYY-MM-DD
    amount_minor   INTEGER NOT NULL,
    note           TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL
);
CREATE INDEX idx_invoice_payments_invoice ON invoice_payments(invoice_id);

-- Entry -> invoice link (one invoice per entry by construction).
ALTER TABLE timesheet_entries ADD COLUMN invoice_id INTEGER NULL
    REFERENCES invoices(id);
CREATE INDEX idx_timesheet_invoice_id ON timesheet_entries(invoice_id);

-- Rollover toggle per project.
ALTER TABLE projects ADD COLUMN rollover_enabled INTEGER NOT NULL DEFAULT 1;
```

New settings (via `store.set_setting`, seeded in migration 8):
- `rollover_max_pct` = `'50'`
- `invoice_due_days` = `'14'`
- `invoice_default_tax_bps` = `'0'`
- `invoice_default_discount_bps` = `'0'`

`LATEST_VERSION` becomes 8. Finance audit reuse: `finance_audit_events`
with `entity_type = 'invoice'`, event types `invoice_created`,
`invoice_sent`, `payment_recorded`, `invoice_voided`,
`invoice_line_changed` (draft edits).

## 5. New / changed modules

### 5.1 `focuscore/invoices.py` (new)

- `next_invoice_number(year, conn)` — counter increment inside the
  caller's transaction; returns `INV-YYYY-NNNN`.
- `create_invoice(project_id, day_from, day_to, entry_ids=None, …)` —
  creates draft + frozen entry lines + links entries, all in **one
  transaction**; returns `(invoice_id, invoice_number)`.
- `add_manual_line(invoice_id, description, hours, rate_minor, currency)`
  / `remove_line(line_id)` — draft only, else `InvoiceLockedError`.
- `set_tax_discount(invoice_id, tax_bps, discount_bps)` — draft only;
  validates 0 ≤ bps ≤ 10000.
- `compute_totals(invoice_id)` — integer math per §2.4; returns dict,
  does not write (drafts recompute on view).
- `send_invoice(invoice_id)` — draft → sent; freezes the four totals;
  sets `sent_at_utc`; audit event. Refuses empty invoices.
- `record_payment(invoice_id, paid_date, amount_minor, note)` —
  append-only; auto-flips to `paid` when covered; audit event.
- `void_invoice(invoice_id, reason)` — releases entries
  (`invoice_id = NULL`), keeps lines as history; audit event.
- `invoice_balance(invoice_id)` — total − Σ payments.
- `invoice_status_computed(invoice)` — adds `overdue` bool and
  `balance_minor` to the row dict.
- `uninvoiced_entries(project_id, day_from, day_to)` — accepted,
  billable (`is_billable IS NULL OR = 1`), `invoice_id IS NULL`,
  ordered by day/start.

### 5.2 `focuscore/budgets.py` (extended)

- `rollover_into(project_id, period_type, period_start_day, path=None)`
  → `{"seconds": int, "amount_minor": int, "from_period": str, "capped": bool}`
  per §3.1. Returns zeros when disabled, when P+1 has no base cap, or
  when the previous period had no cap.
- `effective_cap(project_id, period_type, period_start_day, path=None)`
  → base cap dict + `rollover_seconds` + `rollover_minor` +
  `effective_seconds` + `effective_minor`.
- `forecast(project_id, period_type, ref_day=None, path=None)` → current
  period: effective cap, consumed, projected (hours + money), verdict,
  `rollover_out_forecast`, suppression flags, plain-English `explain`.
  Reuses the Phase 9 pacing engine, bands, and materiality thresholds.
- `budget_status()` gains `rollover` and `forecast` blocks (additive;
  existing keys unchanged).

### 5.3 `dashboard/app.py` (routes + UI)

Nav: add `("invoices", "Invoices", "/invoices")`.

- `GET /invoices` — list (status filter), outstanding total, "New
  invoice" button.
- `GET /invoices/new` — project + date range form; previews uninvoiced
  billable entries with amounts; creates draft via POST.
- `POST /invoices/create`
- `GET /invoices/<id>` — detail: lines, frozen/computed totals,
  tax/discount form (draft), send / record-payment / void actions with
  confirmations, payment history.
- `POST /invoices/<id>/lines/add`, `/lines/<line_id>/remove`,
  `/set-tax-discount`, `/send`, `/payments/add`, `/void` — each
  redirects back with a message; locked-state violations show a plain
  explanation, never a traceback.
- `GET /invoices/<id>/print` — standalone printable HTML invoice
  (M5-style constraints: no external assets, no JS required, all
  user content escaped, print CSS, generation metadata).
- Timesheet project cards: rollover toggle checkbox per project;
  forecast line under each budget bar; effective cap shown as
  "40h + 6h rollover".
- Help: new `invoices` article in `focuscore/help.py`; timesheet
  article gains a rollover/forecast paragraph.

### 5.4 `focuscore/help.py`

New `invoices` article (what it's for / what to do / if something looks
wrong), footer-linked from the invoices pages.

## 6. Test matrix (must all pass before the PC gate)

**Migration 8** (`tests/test_migration_8.py`)
- Fresh apply is idempotent; `user_version` = 8; all new tables, columns,
  and indexes exist; new settings seeded.
- Re-apply on a v7 database upgrades cleanly (downgrade-style test like
  Phase 9's).

**Invoicing** (`tests/test_invoices.py`)
- Numbering: sequential within a year (`INV-2026-0001`, `-0002`),
  resets in a new year, UNIQUE enforced, counter increments inside one
  transaction (no gaps on failure → failed creation consumes no number;
  assert by forcing a rollback).
- Line freezing: entry line keeps the amount even after the project's
  rate changes and after the entry is edited (draft exception: removing
  a line releases the entry).
- No double-invoicing: an entry linked to a non-void invoice never
  appears in `uninvoiced_entries`; creating a second invoice from the
  same range skips it.
- Lifecycle locks: editing tax/lines on a sent invoice raises; sending
  an empty invoice refuses; void releases entries and keeps the number;
  void requires a reason.
- Money math: discount/tax basis-points with half-up rounding
  (e.g. 7.5% of $100.00 = $7.50; 7.5% of $10.01 = $0.75); partial
  payments accumulate; balance hits zero → `paid`; negative adjusting
  payment corrects an over-payment.
- Print HTML: standalone (no http/https, no `<link>`, no `<script>`),
  escaped user content, title "Invoice", totals match frozen values,
  generation metadata present.
- Audit: created/sent/paid/void/line-changed/payment events all logged
  in `finance_audit_events`.
- Non-blocking: invoicing operations never block entry logging,
  sessions, or exports (constraint D).

**Rollover & forecast** (extend `tests/test_budgets.py`)
- Basic roll: 40h cap, 30h used → next period effective 40h + 10h.
- Cap: rollover limited to `rollover_max_pct`% of next period's base
  cap; `capped: True` reported.
- No next cap → no rollover. Previous period had no cap → no rollover.
- No chaining: rolled-in budget unused in P+1 does not roll to P+2.
- No negative rollover: overspend in P leaves P+1's cap untouched.
- Money rollover uses confirmed amounts only; unrated time contributes
  nothing.
- Toggle off → zero rollover; global pct change respected.
- Forecast: on-track vs likely-over verdicts; quiet on non-working
  days; quiet with < 2 elapsed working days; materiality threshold
  (tiny projected overages don't warn); `rollover_out_forecast`
  matches `max(0, effective − projected)`.
- Ledger untouched: rollover adds no ledger rows (append-only history
  preserved); reports still reproduce from ledger + computed rollover.

**Regression**: full suite must stay green; `EXPLAIN QUERY PLAN` on the
uninvoiced-entries query and invoice list queries must use the new
indexes; Ruff E/F clean.

## 7. Questions for Qwen 3.8 Max (audit before implementation)

**Invoicing**
1. Lifecycle honesty: is draft-editable / sent-locked / void-and-reissue
   (new number) the right model, or should sent invoices allow audited
   amendments in place?
2. Numbering: per-year resetting sequence with retained void numbers —
   acceptable for a freelancer's books? Any gap rule we're missing?
3. Payments: are partial payments + negative adjusting payments the
   right call, or should v1 allow only full payments?
4. Tax/discount: per-invoice percentages only (v1 scope) — anything
   about this that would corrupt books later (e.g. should we store the
   computed minor amounts per line instead)?
5. One project per invoice: acceptable scope cut, or does it break a
   realistic freelancer workflow?
6. Invoicing entries from locked days (link only, no data change):
   acceptable, or should locked days be un-invoiceable?
7. Freezing totals at send vs recomputing from frozen lines: is the
   frozen copy redundant or valuable audit insurance?

**Rollover & forecasting**
8. No chaining (rolled-in budget expires if unused): right default, or
   should unused rolled-in budget chain forward with the % cap
   decaying it?
9. Rollover cap default 50% of next period's base cap: sensible? Should
   hours and money have different defaults?
10. Rollover requires the next period to have its own base cap: right,
    or should rollover be able to create a cap from nothing?
11. Forecast excluded from client exports (dashboard only): agree, or
    should the statement show the forecast line?
12. Reusing Phase 9 pacing suppression (rest days, low data,
    materiality): sufficient, or does forecasting need stricter
    confidence rules?
13. Computed-not-stored rollover: does "reports reproduce from ledger +
    deterministic computation" satisfy the auditability bar, or should
    each period's rollover be materialized as a ledger row?

**General**
14. Any new privacy surface? (Invoice print HTML contains client name,
    amounts, line descriptions — all user-initiated, local only.)
15. Anything in this plan that turns budgeting or invoicing into a
    blocking mechanism (must stay advisory per constraint D)?

## 8. Release checklist (same gates as Phase 9)

1. Qwen audit clears this plan (conditional pass → apply conditions).
2. Implement migration 8, `invoices.py`, budgets extensions, routes, UI,
   help, tests.
3. Full local pytest green **before** packaging; Ruff E/F clean;
   `EXPLAIN QUERY PLAN` on the new queries.
4. Source zip → PC: exact pasted pytest output (no paraphrase), schema
   v8 verification, route checks, print-HTML checks.
5. Installer build → silent install → registry version check.
6. Commit, push, tag `v1.11.0`, GitHub Release with installer.
7. No release before the live Windows gate passes.

## 9. Non-goals for v1.11.0

Multi-project invoices, invoice JSON export, email sending, per-line
tax/discount, multi-currency invoices, recurring/automatic invoices,
client-facing online payment, forecast inside client exports.

## 10. Qwen audit resolutions (2026-09-27, CONDITIONAL PASS)

All 15 questions answered; every MUST is implemented. Deviations from
the draft plan above, as mandated by the audit:

- **Tax/discount: integer PERCENTAGES, not basis points** (Q12). Draft
  §2.4's bps math is replaced by Qwen's formula:
  `discount_amount = subtotal * discount_pct // 100`,
  `tax_amount = (subtotal - discount_amount) * tax_pct // 100`.
  Settings: `default_tax_pct`, `default_discount_pct` prefill new
  drafts only; existing invoices never recalculate.
- **Rollover is HOURS ONLY** (Q14). Draft §3.1's money rollover is
  removed. Money caps are ignored by rollover and by the rollover
  forecast.
- **Invoice number assigned at draft → sent, not at draft creation**
  (Q15 + Q2). Drafts have `number = NULL` (UNIQUE allows multiple
  NULLs). The send transaction runs BEGIN IMMEDIATE: read/increment
  `invoice_counters` with the optimistic guard
  (`AND next_number = ?`, rowcount must be 1), assign the number, flip
  status, freeze totals — atomically or not at all.
- **Entry claiming stays at draft creation** (Q3): the claim
  `UPDATE … WHERE invoice_id IS NULL` + rowcount check runs inside a
  BEGIN IMMEDIATE transaction at draft creation, separate from the
  numbering transaction at send. Both races are closed; the intent of
  Q2 and Q3 is satisfied together.
- **Forecast module separation** (Q9): rollover + forecast live in the
  new `focuscore/forecast.py`, which `focuscore/exports.py` and
  `focuscore/invoices.py` never import.
- **Invoice HTML uses Jinja2 autoescape** (Q10), not hand-rolled
  escaping, plus the CSP meta tag and zero-JS rule.
- **DB-layer immutability** (Q1): triggers reject any UPDATE on
  invoices/invoice_lines once status is sent/paid, except the
  sent→void and sent→paid transitions (frozen columns must be
  unchanged). Paid is terminal.
- **invoice_lines snapshot columns** exactly per Q11:
  `entry_date, description, hours_minor_units (= integer seconds, the
  minor unit of time), rate_minor_units, amount_minor_units, currency`,
  plus `timesheet_entry_id … ON DELETE RESTRICT`.
- **Overpayment**: recorded in full, balance capped at zero, no
  `overpaid` status; UI shows a non-blocking warning (Q4).
- **Void + reissue** sets `superseded_by_invoice_id` on the old invoice
  in the same transaction that creates the new draft; only when the
  old value is NULL (Q5).
