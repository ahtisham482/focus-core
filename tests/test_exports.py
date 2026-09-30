"""Phase 9: exports. Qwen audit M1.6/M4/M5/F + constraints E/G."""

import datetime
import json

from focuscore import budgets, exports, store


def _setup(db):
    pid = store.add_project("Website", client="Acme Corp", path=db)
    budgets.set_project_rate(pid, 10000, "USD", path=db)  # $100/hr
    # 60 min confirmed.
    store.create_entry("2026-09-21", "2026-09-21T09:00",
                       "2026-09-21T10:00", 60.0, "Work",
                       app="Code.exe", title="Acme Corp - Layoff Plan - Docs",
                       project_id=pid, task="Homepage",
                       note="remember the logo", status="accepted", path=db)
    # 30 min unknown rate (cleared after snapshot).
    eid = store.create_entry("2026-09-22", "2026-09-22T09:00",
                             "2026-09-22T09:30", 30.0, "Work",
                             project_id=pid, task="Contact page",
                             status="accepted", path=db)
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE timesheet_entries SET hourly_rate_minor = NULL,"
                     " rate_status = 'unknown' WHERE id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()
    # Entry with a hostile task name (CSV injection test).
    store.create_entry("2026-09-22", "2026-09-22T10:00",
                       "2026-09-22T10:30", 30.0, "Work",
                       project_id=pid, task="=cmd|'/c calc'!A0",
                       status="accepted", path=db)
    return pid


# ---------------------------------------------------------- row build ---

def test_client_rows_exclude_app_and_title_by_default(tmp_path):
    db = str(tmp_path / "e.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, path=db)
    assert len(rows) == 3
    for r in rows:
        assert r["app"] == ""
        assert r["title"] == ""
    # Notes are opt-in too.
    assert all(r["note"] == "" for r in rows)
    # Task (user-authored billing line) is included.
    assert rows[0]["task"] == "Homepage"
    # Rate snapshot rode along.
    assert rows[0]["hourly_rate_minor"] == 10000
    assert rows[0]["rate_status"] == "confirmed"
    assert rows[0]["amount_minor"] == 10000  # 1h @ $100


def test_internal_profile_includes_app_details(tmp_path):
    db = str(tmp_path / "e2.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid,
                                     include_app_details=True, path=db)
    assert rows[0]["app"] == "Code.exe"
    assert "Layoff Plan" in rows[0]["title"]


def test_filters_combine(tmp_path):
    db = str(tmp_path / "e3.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-22", "2026-09-22",
                                     project_id=pid, path=db)
    assert len(rows) == 2
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, client="Nobody",
                                     path=db)
    assert rows == []


# --------------------------------------------------------------- CSV ---

def test_client_csv_safe_and_manifested(tmp_path):
    db = str(tmp_path / "e4.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, path=db)
    manifest = exports.redaction_manifest()
    text = exports.rows_to_csv(rows, manifest)
    lines = text.splitlines()
    # Manifest comment first (M4.4), and it matches the output.
    assert lines[0].startswith("# manifest: ")
    parsed = json.loads(lines[0][len("# manifest: "):])
    assert parsed["app_names"] is False
    assert parsed["window_titles"] is False
    assert parsed["urls"] is False
    # No app names / window titles anywhere in the body.
    body = "\n".join(lines[1:])
    assert "Code.exe" not in body
    assert "Layoff Plan" not in body
    # CSV injection neutralized (M4.5).
    assert "'=cmd" in body
    assert "\n=cmd" not in body


def test_detailed_csv_requires_opt_in_and_warns(tmp_path):
    db = str(tmp_path / "e5.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid,
                                     include_app_details=True, path=db)
    text = exports.rows_to_detailed_csv(rows)
    assert "Code.exe" in text
    assert "Layoff Plan" in text


# -------------------------------------------------------------- JSON ---

def test_json_schema_versioned_and_integer_based(tmp_path):
    db = str(tmp_path / "e6.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, path=db)
    manifest = exports.redaction_manifest()
    totals = exports.compute_totals(rows, project_id=pid, currency="USD",
                                    path=db)
    payload = exports.build_json_payload(
        rows, totals, {"from": "2026-09-21", "to": "2026-09-22"},
        manifest, currency="USD")
    assert payload["schema"] == "focuscore.timesheet/v1"
    assert payload["generated_at_utc"]
    assert payload["redaction"]["app_names"] is False
    for entry in payload["entries"]:
        assert isinstance(entry["duration_seconds"], int)
        assert entry["amount_minor"] is None or isinstance(
            entry["amount_minor"], int)
        assert "app" not in entry and "title" not in entry
    # Totals match entries (F): 60+30+30 min confirmed/unknown mix.
    assert payload["totals"]["seconds_total"] == 120 * 60
    # Two confirmed entries: 60min + 30min at $100/hr = $150 = 15000 minor.
    assert payload["totals"]["confirmed_minor"] == 15000
    # One unknown-rate entry: 30 min, excluded from money.
    assert payload["totals"]["unknown_billable_seconds"] == 30 * 60
    assert payload["totals"]["entries_without_rate"] == 1
    # Deterministic: same input -> same bytes.
    again = exports.build_json_payload(
        rows, totals, {"from": "2026-09-21", "to": "2026-09-22"},
        manifest, currency="USD")
    assert json.dumps(payload, sort_keys=True) == json.dumps(
        again, sort_keys=True)


def test_json_separates_estimated(tmp_path):
    db = str(tmp_path / "e7.db")
    pid = _setup(db)
    budgets.set_budget(pid, "month", cap_seconds=100 * 3600,
                       cap_amount_minor=1000000, currency="USD", path=db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, path=db)
    totals = exports.compute_totals(rows, project_id=pid, currency="USD",
                                    include_estimates=True, path=db)
    # 30 unknown minutes at current $100/hr = $50 estimate.
    assert totals["estimated_at_current_minor"] == 5000
    assert totals["confirmed_minor"] == 15000  # kept separate (M1.6)


# -------------------------------------------------------------- HTML ---

def test_statement_is_standalone_safe_and_printable(tmp_path):
    db = str(tmp_path / "e8.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, path=db)
    manifest = exports.redaction_manifest()
    totals = exports.compute_totals(rows, project_id=pid, currency="USD",
                                    path=db)
    page_html = exports.rows_to_statement_html(
        rows, totals, {"from": "2026-09-21", "to": "2026-09-22"},
        manifest, currency="USD", project_name="Website",
        client_name="Acme Corp")
    low = page_html.lower()
    # Standalone: no external assets (M5.3).
    assert "http://" not in low and "https://" not in low
    assert "<link" not in low and "<script" not in low
    # Print CSS present (M5.6).
    assert "@page" in page_html and "@media print" in page_html
    # Title says statement, not invoice (M5).
    assert "Timesheet Statement" in page_html
    assert "Invoice" not in page_html
    # Escaping: hostile task is escaped, not executed (M5.4).
    assert "&lt;" in page_html or "=cmd" in page_html
    assert "<script" not in low
    # Redaction summary visible (M5.5).
    assert "redacted" in low
    # Unrated time disclosed, not hidden.
    assert "Unrated time" in page_html
    # Generation metadata present.
    assert "focuscore.timesheet/v1" in page_html
    assert "1.10.0" in page_html


def test_statement_escapes_user_content(tmp_path):
    db = str(tmp_path / "e9.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, path=db)
    manifest = exports.redaction_manifest()
    totals = exports.compute_totals(rows, path=db)
    page_html = exports.rows_to_statement_html(
        rows, totals, {"from": "2026-09-21", "to": "2026-09-22"},
        manifest, project_name="<b>Bold</b>")
    assert "<b>Bold</b>" not in page_html
    assert "&lt;b&gt;Bold&lt;/b&gt;" in page_html


def test_statement_declares_budget_cap(tmp_path):
    """M2.6: single-project exports record which cap was used."""
    db = str(tmp_path / "e10.db")
    pid = _setup(db)
    budgets.set_budget(pid, "month", cap_seconds=80 * 3600, path=db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid, path=db)
    manifest = exports.redaction_manifest()
    totals = exports.compute_totals(rows, path=db)
    # set_budget stamps the *current* period; query that same period so the
    # test never depends on the calendar date it runs on. (It broke the day
    # October 2026 began, when a fixed September ref day no longer matched.)
    start, _ = budgets.period_bounds("month", datetime.date.today().isoformat())
    cap = budgets.get_cap_for_period(pid, "month", start, path=db)
    page_html = exports.rows_to_statement_html(
        rows, totals, {"from": "2026-09-21", "to": "2026-09-22"},
        manifest, budget_decl=cap, project_name="Website")
    assert "Budget used for this statement" in page_html
    assert "80h" in page_html


# -------------------------------------------------------------- audit ---

def test_exports_are_audit_logged(tmp_path):
    db = str(tmp_path / "e11.db")
    pid = _setup(db)
    rows = exports.build_export_rows("2026-09-21", "2026-09-22",
                                     project_id=pid,
                                     include_app_details=True, path=db)
    manifest = exports.redaction_manifest(include_app_details=True)
    exports.log_export_generated("detailed-csv", {"from": "2026-09-21"},
                                 manifest, len(rows), path=db)
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        kinds = {r[0] for r in conn.execute(
            "SELECT event_type FROM finance_audit_events")}
    finally:
        conn.close()
    assert "client_export_generated" in kinds
    assert "redaction_override_used" in kinds  # E: override is logged


def test_empty_export_is_valid_not_an_error(tmp_path):
    db = str(tmp_path / "e12.db")
    _setup(db)
    rows = exports.build_export_rows("2020-01-01", "2020-01-02", path=db)
    assert rows == []
    manifest = exports.redaction_manifest()
    text = exports.rows_to_csv(rows, manifest)
    assert text.splitlines()[0].startswith("# manifest: ")
    totals = exports.compute_totals(rows, path=db)
    assert totals["seconds_total"] == 0
    page_html = exports.rows_to_statement_html(
        rows, totals, {"from": "2020-01-01", "to": "2020-01-02"}, manifest)
    assert "No entries in this period." in page_html
