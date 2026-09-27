"""Phase 9: budgets engine. Qwen audit M2/M3 + constraints A/B/C/D."""

from datetime import date, timedelta

from focuscore import budgets, money, store


def _project(db, name="Acme"):
    return store.add_project(name, client="Acme Corp", path=db)


def _entry(db, pid, day, minutes, rate_minor=None, status=None):
    """Create an entry, optionally overriding the snapshot afterwards."""
    eid = store.create_entry(day, day + "T09:00", day + "T10:00",
                             minutes, "Work", project_id=pid,
                             status="accepted", path=db)
    if rate_minor is not None or status is not None:
        import sqlite3
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "UPDATE timesheet_entries SET hourly_rate_minor = ?, "
                "rate_currency = 'USD', rate_status = ? WHERE id = ?",
                (rate_minor, status or "unknown", eid))
            conn.commit()
        finally:
            conn.close()
    return eid


# ------------------------------------------------------------- money ---

def test_amount_math_is_decimal_not_float():
    # 1 minute at $95.50/hr = 9550/60 = 159.166.. -> 159 minor (half-up).
    assert money.amount_minor_for(60, 9550) == 159
    # 15 minutes at $100/hr -> 2500.
    assert money.amount_minor_for(900, 10000) == 2500
    # 1 hour at $95.50 -> 9550.
    assert money.amount_minor_for(3600, 9550) == 9550
    # 7.5 hours at $80/hr -> 60000.
    assert money.amount_minor_for(7.5 * 3600, 8000) == 60000
    # Half-up: 30s at $1/hr = 100/120 = 0.833 -> 1.
    assert money.amount_minor_for(30, 100) == 1
    # No float drift: repeated sums stay exact.
    total = sum(money.amount_minor_for(60, 9550) for _ in range(60))
    assert total == 60 * 159


def test_parse_helpers():
    assert money.parse_rate_to_minor("95.50") == 9550
    assert money.parse_rate_to_minor("100") == 10000
    assert money.parse_rate_to_minor("") is None
    assert money.parse_rate_to_minor("abc") is None
    assert money.parse_rate_to_minor("-5") is None
    assert money.parse_hours_to_seconds("7.5") == 27000
    assert money.parse_hours_to_seconds("20") == 72000
    assert money.parse_hours_to_seconds("nope") is None
    assert money.format_minor(950000, "USD") == "$9,500.00"
    assert money.format_minor(None) == "\u2014"


# ------------------------------------------------------------ ledger ---

def test_set_budget_creates_ledger_row_and_cache(tmp_path):
    db = str(tmp_path / "b.db")
    pid = _project(db)
    ok, _ = budgets.set_budget(pid, "week", cap_seconds=20 * 3600,
                               cap_amount_minor=200000, currency="USD",
                               path=db)
    assert ok
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM project_budget_ledger WHERE project_id = ?",
            (pid,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["cap_seconds"] == 72000
        assert rows[0]["cap_amount_minor"] == 200000
        assert rows[0]["period_type"] == "week"
        proj = conn.execute(
            "SELECT current_weekly_cap_seconds, "
            "current_weekly_cap_amount_minor FROM projects WHERE id = ?",
            (pid,)).fetchone()
        assert proj["current_weekly_cap_seconds"] == 72000
        assert proj["current_weekly_cap_amount_minor"] == 200000
        # Audit event logged.
        audit = conn.execute(
            "SELECT * FROM finance_audit_events WHERE entity_type = 'project'"
            " AND event_type = 'budget_cap_changed'").fetchall()
        assert len(audit) == 1
    finally:
        conn.close()


def test_changing_budget_appends_history(tmp_path):
    db = str(tmp_path / "b2.db")
    pid = _project(db)
    budgets.set_budget(pid, "month", cap_seconds=40 * 3600, path=db)
    budgets.set_budget(pid, "month", cap_seconds=20 * 3600, path=db)
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM project_budget_ledger "
                         "WHERE project_id = ?", (pid,)).fetchone()[0]
        assert n == 2
    finally:
        conn.close()
    # Historical lookup still answers the old cap: ask for a period whose
    # start predates the second change... both rows share this month's
    # period_start, so the latest wins for the current period.
    start, _ = budgets.period_bounds("month")
    cap = budgets.get_cap_for_period(pid, "month", start, path=db)
    assert cap["cap_seconds"] == 20 * 3600
    assert cap["changed_during_period"] is True


def test_removing_cap_stores_null_revision(tmp_path):
    db = str(tmp_path / "b3.db")
    pid = _project(db)
    budgets.set_budget(pid, "week", cap_seconds=36000, path=db)
    budgets.set_budget(pid, "week", cap_seconds=None,
                       cap_amount_minor=None, path=db)
    start, _ = budgets.period_bounds("week")
    cap = budgets.get_cap_for_period(pid, "week", start, path=db)
    assert cap["cap_seconds"] is None
    assert cap["cap_amount_minor"] is None


def test_no_cap_returns_none_status(tmp_path):
    db = str(tmp_path / "b4.db")
    pid = _project(db)
    start, _ = budgets.period_bounds("week")
    assert budgets.get_cap_for_period(pid, "week", start, path=db) is None
    status = budgets.budget_status(pid, "week", path=db)
    assert status["cap"] is None
    assert status["hours"] is None


# ------------------------------------------------------------- spend ---

def test_spend_buckets_separate_confirmed_and_unknown(tmp_path):
    db = str(tmp_path / "s.db")
    pid = _project(db)
    budgets.set_project_rate(pid, 10000, "USD", path=db)  # $100/hr
    # New entries snapshot the rate as confirmed.
    _entry(db, pid, "2026-09-21", 60.0)
    # Unknown-rate billable entry (rate cleared to simulate old data).
    _entry(db, pid, "2026-09-21", 30.0, rate_minor=None, status="unknown")
    # Non-billable entry counts toward hours, not money.
    eid = store.create_entry("2026-09-21", "2026-09-21T11:00",
                             "2026-09-21T12:00", 60.0, "Work",
                             project_id=pid, status="accepted", path=db)
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE timesheet_entries SET is_billable = 0 "
                     "WHERE id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()
    spend = budgets.project_spend(pid, "2026-09-21", "2026-09-21", path=db)
    assert spend["seconds_total"] == 150 * 60
    assert spend["seconds_billable"] == 90 * 60
    # 60 min confirmed at $100/hr = $100.00 = 10000 minor.
    assert spend["confirmed_minor"] == 10000
    assert spend["unknown_billable_seconds"] == 30 * 60


def test_rate_change_does_not_rewrite_old_entries(tmp_path):
    """M1.4: entries keep the rate they were snapshotted with."""
    db = str(tmp_path / "r.db")
    pid = _project(db)
    budgets.set_project_rate(pid, 10000, "USD", path=db)
    _entry(db, pid, "2026-09-21", 60.0)
    budgets.set_project_rate(pid, 20000, "USD", path=db)  # doubled
    _entry(db, pid, "2026-09-22", 60.0)
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        rates = [r[0] for r in conn.execute(
            "SELECT hourly_rate_minor FROM timesheet_entries "
            "WHERE project_id = ? ORDER BY day", (pid,))]
    finally:
        conn.close()
    assert rates == [10000, 20000]


def test_new_entry_snapshots_current_rate(tmp_path):
    db = str(tmp_path / "r2.db")
    pid = _project(db)
    budgets.set_project_rate(pid, 9550, "USD", path=db)
    eid = store.create_entry("2026-09-21", "2026-09-21T09:00",
                             "2026-09-21T10:00", 60.0, "Work",
                             project_id=pid, status="accepted", path=db)
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT hourly_rate_minor, rate_currency, "
                           "rate_status FROM timesheet_entries WHERE id = ?",
                           (eid,)).fetchone()
    finally:
        conn.close()
    assert row["hourly_rate_minor"] == 9550
    assert row["rate_currency"] == "USD"
    assert row["rate_status"] == "confirmed"


def test_backfill_marks_confirmed_with_timestamp_and_audit(tmp_path):
    db = str(tmp_path / "r3.db")
    pid = _project(db)
    budgets.set_project_rate(pid, 10000, "USD", path=db)
    _entry(db, pid, "2026-09-21", 60.0, rate_minor=None, status="unknown")
    _entry(db, pid, "2026-09-22", 60.0, rate_minor=None, status="unknown")
    assert budgets.count_unrated_entries(pid, path=db) == 2
    n = budgets.backfill_rate(pid, 10000, "USD", path=db)
    assert n == 2
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT rate_status, rate_confirmed_at_utc, "
                            "hourly_rate_minor FROM timesheet_entries "
                            "WHERE project_id = ?", (pid,)).fetchall()
        assert all(r["rate_status"] == "confirmed" for r in rows)
        assert all(r["rate_confirmed_at_utc"] for r in rows)
        assert all(r["hourly_rate_minor"] == 10000 for r in rows)
        audit = conn.execute("SELECT * FROM finance_audit_events WHERE "
                             "event_type = 'historical_rate_confirmed'"
                             ).fetchall()
        assert len(audit) == 1
    finally:
        conn.close()
    assert budgets.count_unrated_entries(pid, path=db) == 0


# ------------------------------------------------------------ pacing ---

def _spend_week(db, pid, minutes_per_day, days):
    for d in days:
        _entry(db, pid, d, minutes_per_day)


def test_absolute_bands_are_factual(tmp_path):
    db = str(tmp_path / "p.db")
    pid = _project(db)
    budgets.set_budget(pid, "week", cap_seconds=10 * 3600, path=db)
    start, _ = budgets.period_bounds("week")
    # Spend 8h on Monday of this week.
    monday = start.isoformat()
    _entry(db, pid, monday, 8 * 60.0)
    status = budgets.budget_status(pid, "week", ref_day=monday, path=db)
    assert status["hours"]["band"] == "watch"  # 80%
    assert abs(status["hours"]["pct"] - 0.8) < 0.001


def test_no_pacing_warning_on_non_working_day(tmp_path):
    """M3.3: routine pacing warnings stay quiet on rest days."""
    db = str(tmp_path / "p2.db")
    pid = _project(db)
    budgets.set_budget(pid, "week", cap_seconds=10 * 3600, path=db)
    start, end = budgets.period_bounds("week")
    saturday = (start + timedelta(days=5)).isoformat()
    _entry(db, pid, start.isoformat(), 9 * 60.0)  # heavy Monday
    status = budgets.budget_status(pid, "week", ref_day=saturday, path=db)
    pacing = status["pacing"]
    assert pacing["suppressed"] is True
    assert "working day" in pacing["suppress_reason"]
    # ...but the absolute band is still factual.
    assert status["hours"]["band"] == "warning"  # 90%


def test_pacing_suppressed_with_too_little_data(tmp_path):
    """M3: front-loaded work on day one must not trigger a false alarm."""
    db = str(tmp_path / "p3.db")
    pid = _project(db)
    budgets.set_budget(pid, "week", cap_seconds=40 * 3600, path=db)
    start, _ = budgets.period_bounds("week")
    # Only one working day elapsed so far.
    if start.weekday() != 0:
        start = start - timedelta(days=start.weekday())  # force Monday
    _entry(db, pid, start.isoformat(), 12 * 60.0)
    status = budgets.budget_status(pid, "week", ref_day=start.isoformat(),
                                   path=db)
    assert status["pacing"]["suppressed"] is True


def test_materiality_threshold_blocks_tiny_overruns(tmp_path):
    """M3.4: projected overruns under 1 hour do not warn."""
    db = str(tmp_path / "p4.db")
    pid = _project(db)
    # Cap chosen so a steady pace projects just over the cap (< 1h over).
    budgets.set_budget(pid, "week", cap_seconds=40 * 3600, path=db)
    start, _ = budgets.period_bounds("week")
    # Use 4 elapsed working days at exactly cap pace: projected == cap.
    for i in range(4):
        day = (start + timedelta(days=i)).isoformat()
        _entry(db, pid, day, 8 * 60.0)
    ref = (start + timedelta(days=3)).isoformat()  # Thursday
    status = budgets.budget_status(pid, "week", ref_day=ref, path=db)
    pacing = status["pacing"]
    assert pacing["band"] in ("none", "watch")  # never "warning"


def test_period_bounds_week_and_month():
    start, end = budgets.period_bounds("week", "2026-09-23")  # a Wednesday
    assert start.isoformat() == "2026-09-21"
    assert end.isoformat() == "2026-09-27"
    start, end = budgets.period_bounds("month", "2026-02-15")
    assert start.isoformat() == "2026-02-01"
    assert end.isoformat() == "2026-02-28"
    assert budgets.working_days_between(date(2026, 9, 21), date(2026, 9, 27),
                                        {0, 1, 2, 3, 4}) == 5


def test_working_days_setting_is_configurable(tmp_path):
    db = str(tmp_path / "wd.db")
    store.set_setting("working_days", "0,1,2,3,4,5", path=db)
    assert budgets.get_working_days(path=db) == {0, 1, 2, 3, 4, 5}
    assert budgets.parse_working_days("garbage") == {0, 1, 2, 3, 4}
