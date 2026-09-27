# Sprint 3 — Phase 9: Project Budgets & Client Timesheet Exports (architectural plan)

Version target: **v1.10.0**. Status: **CONDITIONAL PASS from Qwen 3.8 Max
(2026-09-27) — implementation complete locally 2026-09-27; all M1–M5/A–G
constraints applied; local gate 426 passed / 1 skipped, Ruff E/F clean,
EXPLAIN QUERY PLAN confirms the new export indexes; routes smoke-tested.
PC verification + release still pending.**
M1–M5 and A–G incorporated.** Original draft constraints superseded by the
audit; see section 10 for the binding constraint list.

## 1. Goal

Turn tagged time into money answers. Two features:

1. **Budget tracking** — per-project weekly/monthly caps (hours and/or
   money), with burndown, pacing, and plain-English warnings.
2. **Client exports** — filtered timesheet exports in CSV, structured
   JSON, and a print-ready statement (standalone HTML the user prints
   to PDF).

## 2. Non-goals and boundaries

- **Not full invoicing.** No invoice numbering, no tax/VAT, no payment
  tracking, no accounts receivable. The statement is a readable
  timesheet summary for a client, labeled "Timesheet Statement".
  (Invoicing was deliberately omitted from the product before; this
  phase does not reverse that decision.)
- **Budgets are advisory only.** They never block logging time or
  starting sessions — consistent with the Phases 7–8 philosophy that
  the app suggests, the user decides.
- **No new dependencies** in the base proposal (stdlib + the existing
  `requirements.txt`). The PDF question is put to Qwen as audit
  question 5.

## 3. What already exists (build on it, don't duplicate)

- Migration 4 added `projects.weekly_budget_hours`,
  `projects.hourly_rate`, `projects.is_billable`, `projects.color`,
  and `timesheet_entries.is_billable`.
- `timesheet_entries` has `project_id`, `task`, `note`, `status`,
  `locked`; `store.list_entries()` supports day ranges.
- `/timesheet/export` (legacy) now **redirects** to the safe client export
  `/timesheet/export/client` (M4.1 — the old app/title columns no longer
  leak by default). Detailed app/title output is only available via the
  explicit internal opt-in `?detail=internal`, which is audit-logged.

## 4. Migration 7 — schema (additive, idempotent)

New columns on `projects` (same pattern as migration 4):

- `monthly_budget_hours REAL NOT NULL DEFAULT 0.0`
- `weekly_budget_amount REAL NOT NULL DEFAULT 0.0`
- `monthly_budget_amount REAL NOT NULL DEFAULT 0.0`

(`weekly_budget_hours` already exists from migration 4. `0` means
"no cap".)

New column on `timesheet_entries`:

- `hourly_rate REAL` — NULL allowed. **Rate snapshot**: new entries
  store the project's rate at accept/create time, so later rate
  changes never rewrite history. Backfill rule:
  `SET hourly_rate = (SELECT hourly_rate FROM projects WHERE
  projects.id = timesheet_entries.project_id)` for rows that have a
  project; rows with no project keep NULL (= rate unknown, shown as
  "—", excluded from amount totals with a visible note).

New index:

- `idx_ts_entries_project_day ON timesheet_entries(project_id, day)`
  for budget aggregation queries.

New setting:

- `currency` (default `"USD"`), single global setting, editable on
  the Timesheet page next to the export panel.

All `add_column_if_missing` / `CREATE INDEX IF NOT EXISTS` —
idempotent re-runs, snapshot + rollback via the M0 engine.

## 5. Budget engine (new module `focuscore/budgets.py`)

- `period_bounds(period, ref_date)` — `"weekly"` = Monday–Sunday
  (matches the Sunday review cadence), `"monthly"` = calendar month.
  All in local wall-clock days (Phase 6 timezone logic).
- `project_spend(project_id, from_day, to_day, db_path)` — over
  entries with `status = 'accepted'`:
  - `hours` = all tagged hours in the period,
  - `billable_hours` = tagged hours with `is_billable = 1`,
  - `amount` = Σ per-entry `round(hours × hourly_rate, 2)` over
    billable entries with a non-NULL rate (half-up, 2 decimals).
- `budget_status(project, period, ref_date)` — for each of the four
  caps with cap > 0:
  - `consumed`, `cap`, `pct = consumed / cap`,
  - `expected = cap × elapsed_days / total_days` (elapsed includes
    today, capped at the period end),
  - `ahead_behind = consumed − expected` (positive = burning fast),
  - band: **< 75%** on track · **75–90%** watch · **90–100%**
    warning · **> 100%** over budget.
- Hours caps count **all** tagged hours; amount caps count
  **billable** hours only. Stated in the UI so there is no surprise.
- `plain_summary(...)` — one plain-English line, e.g.
  "18 of 20 hours used, 2 days left — about 1 hour a day keeps you
  inside." No jargon, no unexplained percentages.
- **Rounding contract** (so CSV, JSON, and statement always agree):
  round each entry half-up to 2 decimals; totals are sums of rounded
  entries, never a re-rounded total.

## 6. Export engines (new module `focuscore/exports.py`)

One shared row builder feeds all three formats (single source of
truth — formats cannot disagree):

- `build_export_rows(day_from, day_to, project_id=None, client=None,
  billable_only=False, include_app_details=False, db_path=None)`
  → list of dicts: day, start, end, hours, project, client, task,
  note, category, billable, hourly_rate, amount (or None).
- Filters: date range (required), project dropdown, client dropdown,
  billable-only checkbox, "include app details" checkbox
  (**default OFF** — app/window titles can contain sensitive
  strings; see audit question 4).

Formats:

1. **CSV** — new route `/timesheet/export/client?...`. Columns:
   date, start, end, hours, project, client, task, billable,
   hourly_rate, amount. Filename
   `focuscore-timesheet-<from>-to-<to>.csv`.
2. **JSON** — route `/timesheet/export.json?...`, versioned schema:
   `{"schema": "focuscore.timesheet/v1", "generated_at",
   "app_version", "period": {"from", "to"}, "client", "project",
   "currency", "entries": [...], "totals": {"hours",
   "billable_hours", "amount", "entries_without_rate"}}`.
3. **Printable statement** — route `/timesheet/statement?...`.
   Standalone HTML (no app layout/nav), inline CSS, `@media print`
   rules, a "Print / Save as PDF" button calling `window.print()`.
   Sections: "Timesheet Statement" header, client / project /
   period, entries table (date, task, hours, rate, amount), totals,
   a visible note when entries lack a rate
   ("3 entries have no hourly rate and are excluded from the
   amount"), footer "Generated by Focus Core v1.10.0 on <date>".

## 7. UX specification

- **Timesheet page, project cards**: each project card gains compact
  budget bars — "This week" and "This month", each showing hours and
  amount vs cap with the band color. An inline "Set budget" form per
  project: four numeric fields (weekly hours, monthly hours, weekly
  amount, monthly amount); `0` = no cap; plain-English hint under
  each field.
- **Timesheet page, export panel**: range presets (This week, Last
  week, This month, Last month, Custom), project dropdown (All +
  each project), client dropdown (All + each client text), billable-
  only checkbox, include-app-details checkbox (default off),
  currency field, three buttons: Download CSV, Download JSON,
  Printable statement.
- **Statement page**: clean document, generous whitespace, prints to
  one or more A4 pages without cut-off tables (print CSS:
  `thead { display: table-header-group }`).
- **Help**: extend the timesheet help article (`/help/timesheet`)
  with budgets ("what a budget is, what the colors mean") and
  exports ("what each format is for") in plain English.

## 8. Testing

- Migration 7: fresh apply; idempotent re-run; legacy-DB upgrade
  (backfill assigns project rates, NULL stays NULL for project-less
  rows); index exists; `user_version = 7`.
- Budgets: period bounds incl. week spanning a month boundary and
  February; pacing math at period start/middle/end; band edges
  (74.9 / 75 / 90 / 100 / 100.1 %); rounding contract — CSV, JSON,
  statement totals identical.
- Exports: filters combine correctly; empty result renders a valid
  empty file (not an error); entries without rate excluded from
  amounts with the note present.
- Full suite + `ruff --select E,F` + render tests for the three new
  routes (200 + correct content type).

## 9. Open questions — for the Qwen 3.8 Max audit (nothing builds until cleared)

**Q1 — Rate backfill honesty.** Migration 7 backfills historical
entries' `hourly_rate` from the project's *current* rate. For work
done months ago, that fabricates a financial record the user never
confirmed. Is backfill-from-current-rate acceptable, or must
pre-Phase-9 entries be marked "rate unknown" (NULL, excluded from
amounts) unless the user confirms them?

**Q2 — Mutable caps vs an append-only budget ledger.** The plan
stores caps as mutable columns on `projects` (the migration-4
pattern): changing a cap rewrites what "over budget" meant for past
periods, making old burndowns unreproducible. For money budgets, is
that acceptable, or does Phase 9 need an append-only
`project_budgets` table (project_id, period, cap_hours, cap_amount,
effective_from) so history stays auditable?

**Q3 — Pacing false alarms.** Linear elapsed-day pacing assumes work
spreads evenly, but rest days (e.g. Sundays) break that assumption
and could cry "over budget" every weekend. Should pacing use
business days, the user's own historical daily velocity, or stay
linear with the band thresholds widened? Which is least likely to
train the user to ignore the warnings?

**Q4 — Export PII default.** App/window titles can leak sensitive
strings (document names, URLs, client names the user didn't intend
to share). The plan defaults "include app details" to OFF on the
new client export but leaves the old day-export CSV unchanged (it
already includes `app`). Is that split acceptable, or should the old
export also change, and should titles/URLs be redacted even when
opted in?

**Q5 — True PDF vs print-ready HTML.** The base proposal ships zero
new dependencies: a standalone print-stylesheet HTML the user saves
as PDF via the browser. The alternative is adding `reportlab`
(pure Python, but +~2 MB to the installer and a new supply-chain
surface) for one-click server-side PDFs. For a finance-adjacent
feature, which tradeoff is right for this product?
