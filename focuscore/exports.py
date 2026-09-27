"""Phase 9: client timesheet exports (Qwen audit M1.6/M4/M5/F + E).

Binding rules:
- M4.1: client-facing exports EXCLUDE app names, window titles, URLs by
  default. The detailed internal profile (include_app_details=True) is an
  explicit opt-in and is audit-logged (M4.2, E).
- M4.4: every export carries a redaction manifest (JSON object, visible
  HTML section, '# manifest:' comment line in CSV).
- M4.5: CSV formula-injection protection ('=','+','-','@' prefixed).
- M4.6: notes are opt-in; task is included by default because it is the
  user-authored billing line-item (documented choice).
- M5: the statement is standalone print-ready HTML — inline CSS only,
  no external assets, no JS required, all user text escaped.
- M1.6/F: confirmed and estimated amounts are separate; JSON uses
  integer seconds + integer minor units, schema 'focuscore.timesheet/v1'.
- M2.6: single-project exports declare the budget cap used.
"""

import csv
import html
import io
import json
from datetime import datetime

from focuscore import budgets as budgets_mod
from focuscore import money as money_mod
from focuscore import store

SCHEMA_VERSION = "focuscore.timesheet/v1"
APP_VERSION = "1.10.0"

# Fields that are client-safe by default. app/title/note are gated.
_CLIENT_COLUMNS = [
    "date",
    "start",
    "end",
    "duration_hours",
    "project",
    "client",
    "task",
    "billable",
    "hourly_rate",
    "rate_status",
    "amount",
]


def _sanitize_csv_field(value):
    """Neutralize CSV formula injection (M4.5)."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def _entry_seconds(entry_minutes):
    return int(round((entry_minutes or 0) * 60))


def build_export_rows(
    day_from,
    day_to,
    project_id=None,
    client=None,
    billable_only=False,
    include_app_details=False,
    include_notes=False,
    path=None,
):
    """Shared row builder: ONE code path feeds CSV, JSON, and HTML so
    formats can never disagree on totals (F)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        query = (
            "SELECT e.id, e.day, e.start_ts, e.end_ts, e.minutes, "
            "e.category, e.task, e.note, e.is_billable, e.project_id, "
            "p.name AS project_name, p.client AS client, "
            "e.hourly_rate_minor, e.rate_currency, e.rate_status, "
            "e.app, e.title "
            "FROM timesheet_entries e "
            "LEFT JOIN projects p ON p.id = e.project_id "
            "WHERE e.day >= ? AND e.day <= ? AND e.status = 'accepted'"
        )
        args = [day_from, day_to]
        if project_id is not None:
            query += " AND e.project_id = ?"
            args.append(project_id)
        if billable_only:
            query += " AND (e.is_billable IS NULL OR e.is_billable = 1)"
        query += " ORDER BY e.day, e.start_ts"
        db_rows = conn.execute(query, args).fetchall()
    finally:
        conn.close()

    rows = []
    for r in db_rows:
        if client and (r["client"] or "") != client:
            continue
        seconds = _entry_seconds(r["minutes"])
        _raw = r["is_billable"]
        billable = (_raw if _raw is not None else 1) == 1
        status = r["rate_status"] or "unknown"
        amount = None
        if billable and status in ("confirmed", "estimated"):
            amount = money_mod.amount_minor_for(seconds, r["hourly_rate_minor"])
        rows.append(
            {
                "id": r["id"],
                "day": r["day"],
                "start_ts": r["start_ts"] or "",
                "end_ts": r["end_ts"] or "",
                "duration_seconds": seconds,
                "project": r["project_name"] or "",
                "client": r["client"] or "",
                "task": r["task"] or "",
                "note": (r["note"] or "") if include_notes else "",
                "category": r["category"] or "",
                "billable": billable,
                "hourly_rate_minor": r["hourly_rate_minor"],
                "rate_currency": r["rate_currency"] or "USD",
                "rate_status": status,
                "amount_minor": amount,
                # Gated: only present when the internal profile is requested.
                "app": (r["app"] or "") if include_app_details else "",
                "title": (r["title"] or "") if include_app_details else "",
            }
        )
    return rows


def redaction_manifest(include_app_details=False, include_notes=False):
    """What this export includes / excludes (M4.4)."""
    return {
        "app_names": bool(include_app_details),
        "window_titles": bool(include_app_details),
        "urls": bool(include_app_details),
        "notes": bool(include_notes),
        "task": True,
    }


def compute_totals(
    rows, project_id=None, currency="USD", include_estimates=False, path=None
):
    """Totals with confirmed/estimated/unknown kept separate (M1.6)."""
    seconds_total = sum(r["duration_seconds"] for r in rows)
    seconds_billable = sum(r["duration_seconds"] for r in rows if r["billable"])
    confirmed_minor = sum(
        r["amount_minor"] or 0 for r in rows if r["rate_status"] == "confirmed"
    )
    estimated_minor = sum(
        r["amount_minor"] or 0 for r in rows if r["rate_status"] == "estimated"
    )
    unknown_seconds = sum(
        r["duration_seconds"]
        for r in rows
        if r["billable"] and r["rate_status"] == "unknown"
    )
    unknown_count = sum(
        1 for r in rows if r["billable"] and r["rate_status"] == "unknown"
    )
    estimated_at_current = 0
    if include_estimates and unknown_seconds and project_id:
        rate_minor, _ = budgets_mod.get_project_rate(project_id, path=path)
        if rate_minor:
            estimated_at_current = (
                money_mod.amount_minor_for(unknown_seconds, rate_minor) or 0
            )
    return {
        "seconds_total": seconds_total,
        "seconds_billable": seconds_billable,
        "confirmed_minor": confirmed_minor,
        "estimated_minor": estimated_minor,
        "unknown_billable_seconds": unknown_seconds,
        "entries_without_rate": unknown_count,
        "estimated_at_current_minor": estimated_at_current,
        "currency": currency,
    }


def _local_tzname():
    try:
        return datetime.now().astimezone().tzname() or "local"
    except Exception:
        return "local"


# ---------------------------------------------------------------- CSV ---


def rows_to_csv(rows, manifest):
    """Client CSV. First line is a '# manifest:' JSON comment (M4.4)."""
    buf = io.StringIO()
    buf.write("# manifest: %s\n" % json.dumps(manifest, separators=(",", ":")))
    writer = csv.writer(buf)
    writer.writerow(_CLIENT_COLUMNS)
    for r in rows:
        writer.writerow(
            [
                _sanitize_csv_field(r["day"]),
                _sanitize_csv_field(r["start_ts"]),
                _sanitize_csv_field(r["end_ts"]),
                _sanitize_csv_field("%.2f" % (r["duration_seconds"] / 3600)),
                _sanitize_csv_field(r["project"]),
                _sanitize_csv_field(r["client"]),
                _sanitize_csv_field(r["task"]),
                _sanitize_csv_field("yes" if r["billable"] else "no"),
                _sanitize_csv_field(
                    money_mod.format_minor(r["hourly_rate_minor"], r["rate_currency"])
                    if r["hourly_rate_minor"]
                    else ""
                ),
                _sanitize_csv_field(r["rate_status"]),
                _sanitize_csv_field(
                    money_mod.format_minor(r["amount_minor"], r["rate_currency"])
                    if r["amount_minor"] is not None
                    else ""
                ),
            ]
        )
    return buf.getvalue()


def rows_to_detailed_csv(rows):
    """Legacy/internal detailed CSV: includes app + window title.
    Explicit opt-in only (M4.2); the UI must show the sensitivity warning."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CLIENT_COLUMNS + ["app", "window_title"])
    for r in rows:
        writer.writerow(
            [
                _sanitize_csv_field(r["day"]),
                _sanitize_csv_field(r["start_ts"]),
                _sanitize_csv_field(r["end_ts"]),
                _sanitize_csv_field("%.2f" % (r["duration_seconds"] / 3600)),
                _sanitize_csv_field(r["project"]),
                _sanitize_csv_field(r["client"]),
                _sanitize_csv_field(r["task"]),
                _sanitize_csv_field("yes" if r["billable"] else "no"),
                _sanitize_csv_field(
                    money_mod.format_minor(r["hourly_rate_minor"], r["rate_currency"])
                    if r["hourly_rate_minor"]
                    else ""
                ),
                _sanitize_csv_field(r["rate_status"]),
                _sanitize_csv_field(
                    money_mod.format_minor(r["amount_minor"], r["rate_currency"])
                    if r["amount_minor"] is not None
                    else ""
                ),
                _sanitize_csv_field(r["app"]),
                _sanitize_csv_field(r["title"]),
            ]
        )
    return buf.getvalue()


# --------------------------------------------------------------- JSON ---


def build_json_payload(
    rows, totals, filters, manifest, budget_decl=None, currency="USD"
):
    """Versioned, deterministic JSON (F). Durations are integer seconds;
    money is integer minor units."""
    entries = []
    for r in rows:
        entries.append(
            {
                "day": r["day"],
                "start_ts": r["start_ts"],
                "end_ts": r["end_ts"],
                "duration_seconds": r["duration_seconds"],
                "project": r["project"],
                "client": r["client"],
                "task": r["task"],
                "note": r["note"],
                "category": r["category"],
                "billable": r["billable"],
                "hourly_rate_minor": r["hourly_rate_minor"],
                "rate_currency": r["rate_currency"],
                "rate_status": r["rate_status"],
                "amount_minor": r["amount_minor"],
            }
        )
    return {
        "schema": SCHEMA_VERSION,
        "generated_at_utc": budgets_mod.utcnow_iso(),
        "timezone": _local_tzname(),
        "currency": currency,
        "filters": filters,
        "redaction": manifest,
        "budget": budget_decl,
        "entries": entries,
        "totals": totals,
    }


# --------------------------------------------------------------- HTML ---

_STATEMENT_CSS = """
@page { margin: 18mm; }
body { font-family: Arial, Helvetica, sans-serif; color: #1a1a1a;
       margin: 0; padding: 24px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: #555; font-size: 13px; margin-bottom: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
@media print {
  .no-print { display: none; }
  table { page-break-inside: auto; }
  tr { page-break-inside: avoid; }
  thead { display: table-header-group; }
}
th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; }
th { background: #f2f2f2; }
td.num, th.num { text-align: right; }
.day-row td { background: #fafafa; font-weight: bold; }
.totals { margin-top: 20px; max-width: 420px; }
.totals table td:last-child { text-align: right; }
.note { font-size: 12px; color: #555; margin-top: 12px; }
.redact { font-size: 12px; color: #555; margin-top: 16px;
          border-top: 1px solid #ddd; padding-top: 8px; }
.footer { font-size: 11px; color: #888; margin-top: 24px; }
button.print { font-size: 15px; padding: 10px 18px; margin-bottom: 20px; }
"""


def rows_to_statement_html(
    rows,
    totals,
    filters,
    manifest,
    budget_decl=None,
    currency="USD",
    show_estimates=False,
    project_name="",
    client_name="",
):
    """Standalone print-ready Timesheet Statement (M5). All dynamic text
    is escaped; no external assets; no JS required (the print button is
    progressive enhancement)."""
    esc = html.escape
    # Group rows by day with daily subtotals.
    days = []
    for r in rows:
        if not days or days[-1]["day"] != r["day"]:
            days.append({"day": r["day"], "rows": [], "seconds": 0, "amount": 0})
        days[-1]["rows"].append(r)
        days[-1]["seconds"] += r["duration_seconds"]
        days[-1]["amount"] += r["amount_minor"] or 0

    body_rows = []
    for d in days:
        for r in d["rows"]:
            rate = (
                money_mod.format_minor(r["hourly_rate_minor"], r["rate_currency"])
                if r["hourly_rate_minor"]
                else "\u2014"
            )
            amount = (
                money_mod.format_minor(r["amount_minor"], r["rate_currency"])
                if r["amount_minor"] is not None
                else "\u2014"
            )
            body_rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='num'>%.2f</td><td>%s</td>"
                "<td class='num'>%s</td><td class='num'>%s</td></tr>"
                % (
                    esc(r["day"]),
                    esc(r["start_ts"]),
                    esc(r["task"]),
                    r["duration_seconds"] / 3600,
                    esc("yes" if r["billable"] else "no"),
                    esc(rate),
                    esc(amount),
                )
            )
        body_rows.append(
            "<tr class='day-row'><td colspan='3'>Day total \u2014 %s</td>"
            "<td class='num'>%.2f</td><td></td><td></td>"
            "<td class='num'>%s</td></tr>"
            % (
                esc(d["day"]),
                d["seconds"] / 3600,
                esc(money_mod.format_minor(d["amount"], currency)),
            )
        )

    title_bits = ["Timesheet Statement"]
    if project_name:
        title_bits.append(esc(project_name))
    period = "%s to %s" % (esc(filters.get("from", "")), esc(filters.get("to", "")))

    totals_html = [
        "<div class='totals'><table>",
        "<tr><td>Tracked time</td><td>%s</td></tr>"
        % esc(money_mod.format_duration(totals["seconds_total"])),
        "<tr><td>Confirmed billable</td><td>%s</td></tr>"
        % esc(money_mod.format_minor(totals["confirmed_minor"], currency)),
    ]
    if totals["estimated_minor"]:
        totals_html.append(
            "<tr><td>Estimated (marked)</td><td>%s</td></tr>"
            % esc(money_mod.format_minor(totals["estimated_minor"], currency))
        )
    if totals["unknown_billable_seconds"]:
        totals_html.append(
            "<tr><td>Unrated time (excluded from totals)</td><td>%s</td></tr>"
            % esc(money_mod.format_duration(totals["unknown_billable_seconds"]))
        )
    if show_estimates and totals["estimated_at_current_minor"]:
        totals_html.append(
            "<tr><td><i>Estimate at current rate (not confirmed)</i></td>"
            "<td><i>%s</i></td></tr>"
            % esc(
                money_mod.format_minor(totals["estimated_at_current_minor"], currency)
            )
        )
    totals_html.append("</table></div>")

    budget_html = ""
    if budget_decl:
        cap = budget_decl
        budget_html = (
            "<div class='note'><b>Budget used for this statement:</b> %s cap "
            "of %s%s, effective %s%s.</div>"
            % (
                esc(cap["period_type"]),
                esc(
                    money_mod.format_duration(cap["cap_seconds"])
                    if cap.get("cap_seconds")
                    else ""
                ),
                (
                    " + "
                    + esc(
                        money_mod.format_minor(cap["cap_amount_minor"], cap["currency"])
                    )
                    if cap.get("cap_amount_minor")
                    else ""
                ),
                esc(cap.get("effective_from_utc", "")),
                " (cap changed during this period)"
                if cap.get("changed_during_period")
                else "",
            )
        )

    redact_items = []
    for key, label in (
        ("app_names", "App names"),
        ("window_titles", "Window titles"),
        ("urls", "URLs"),
        ("notes", "Notes"),
    ):
        redact_items.append(
            "%s: %s" % (label, "included" if manifest.get(key) else "redacted")
        )
    redact_html = (
        "<div class='redact'><b>Data included in this "
        "statement:</b> %s. Task descriptions are included; "
        "app names, window titles and URLs are never in client "
        "statements.</div>" % esc("; ".join(redact_items))
    )

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Timesheet Statement</title>
<style>%s</style></head>
<body>
<button class="print no-print" onclick="window.print()">Print / Save as PDF</button>
<h1>%s</h1>
<div class="meta">Period: %s<br>Client: %s<br>Currency: %s</div>
<table><thead><tr>
<th>Date</th><th>Start</th><th>Task</th><th class="num">Hours</th>
<th>Billable</th><th class="num">Rate</th><th class="num">Amount</th>
</tr></thead><tbody>
%s
</tbody></table>
%s
%s
%s
<div class="footer">Generated by Focus Core v%s on %s UTC &middot;
Schema %s</div>
</body></html>""" % (
        _STATEMENT_CSS,
        " \u2014 ".join(title_bits),
        period,
        esc(client_name or filters.get("client") or "\u2014"),
        esc(currency),
        "\n".join(body_rows)
        or "<tr><td colspan='7'>No entries in this period.</td></tr>",
        "\n".join(totals_html),
        budget_html,
        redact_html,
        esc(APP_VERSION),
        esc(budgets_mod.utcnow_iso()),
        esc(SCHEMA_VERSION),
    )


# -------------------------------------------------------------- audit ---


def log_export_generated(kind, filters, manifest, row_count, path=None):
    budgets_mod.log_finance_event(
        "export",
        None,
        "client_export_generated",
        {
            "kind": kind,
            "filters": filters,
            "redaction": manifest,
            "row_count": row_count,
        },
        path=path,
    )
    if manifest.get("app_names") or manifest.get("window_titles"):
        budgets_mod.log_finance_event(
            "export",
            None,
            "redaction_override_used",
            {"kind": kind, "filters": filters},
            path=path,
        )
