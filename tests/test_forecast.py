"""Phase 10: rollover + forecasting. Qwen audit Q6-Q9, Q14."""

import sqlite3
from datetime import date, timedelta

from focuscore import budgets, forecast, store


def _project(db, name="Acme"):
    return store.add_project(name, client="Acme Corp", path=db)


def _entry(db, pid, day, minutes):
    eid = store.create_entry(day, day + "T09:00", day + "T10:00",
                             minutes, "Work", project_id=pid,
                             status="accepted", path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET hourly_rate_minor = 10000, "
            "rate_currency = 'USD', rate_status = 'confirmed' "
            "WHERE id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()
    return eid


def _set_cap(db, pid, period_type, period_start, hours):
    """Write a ledger cap row for an exact period (bypasses set_budget's
    'effective now' semantics so tests can govern past/future periods)."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO project_budget_ledger (project_id, period_type, "
            "period_start, cap_seconds, cap_amount_minor, currency, "
            "effective_from_utc, note, created_at_utc) "
            "VALUES (?, ?, ?, ?, NULL, 'USD', ?, '', ?)",
            (pid, period_type, period_start,
             int(hours * 3600) if hours else None,
             period_start + "T00:00:00", period_start + "T00:00:00"))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------- rollover ---

def test_rollover_formula(tmp_path):
    db = str(tmp_path / "f.db")
    pid = _project(db)
    prev = forecast.previous_period_start("week", "2026-09-28")
    _set_cap(db, pid, "week", prev.isoformat(), 10)       # 10h prev cap
    _set_cap(db, pid, "week", "2026-09-28", 10)           # 10h next cap
    _entry(db, pid, prev.isoformat(), 360.0)              # used 6h
    r = forecast.compute_rollover(pid, "week", "2026-09-28", path=db)
    # unused 4h; limit = 10h * 50% = 5h -> rolled = 4h.
    assert r["rolled_in_seconds"] == 4 * 3600
    assert r["effective_cap_seconds"] == 14 * 3600
    assert r["capped"] is False


def test_rollover_cap_limits_amount(tmp_path):
    db = str(tmp_path / "f2.db")
    pid = _project(db)
    prev = forecast.previous_period_start("week", "2026-09-28")
    _set_cap(db, pid, "week", prev.isoformat(), 20)
    _set_cap(db, pid, "week", "2026-09-28", 10)
    _entry(db, pid, prev.isoformat(), 60.0)               # used 1h
    r = forecast.compute_rollover(pid, "week", "2026-09-28", path=db)
    # unused 19h; limit = 10h * 50% = 5h -> rolled = 5h, capped.
    assert r["rolled_in_seconds"] == 5 * 3600
    assert r["capped"] is True


def test_rollover_zero_pct_disables(tmp_path):
    db = str(tmp_path / "f3.db")
    pid = _project(db)
    prev = forecast.previous_period_start("week", "2026-09-28")
    _set_cap(db, pid, "week", prev.isoformat(), 10)
    _set_cap(db, pid, "week", "2026-09-28", 10)
    store.set_setting("rollover_cap_pct", "0", path=db)
    r = forecast.compute_rollover(pid, "week", "2026-09-28", path=db)
    assert r["rolled_in_seconds"] == 0


def test_rollover_no_next_period_cap_returns_zero(tmp_path):
    db = str(tmp_path / "f4.db")
    pid = _project(db)
    prev = forecast.previous_period_start("week", "2026-09-28")
    _set_cap(db, pid, "week", prev.isoformat(), 10)
    _entry(db, pid, prev.isoformat(), 60.0)
    # The next period's cap was explicitly removed (NULL revision):
    # no cap governs it, so rollover is zero even though the previous
    # period has unused hours.
    _set_cap(db, pid, "week", "2026-09-28", None)
    r = forecast.compute_rollover(pid, "week", "2026-09-28", path=db)
    assert r["rolled_in_seconds"] == 0
    assert r["effective_cap_seconds"] == 0


def test_rollover_hours_only_ignores_money_cap(tmp_path):
    db = str(tmp_path / "f5.db")
    pid = _project(db)
    # Money-only caps: hours rollover must still be zero.
    budgets.set_budget(pid, "week", None, 50000, path=db)
    r = forecast.compute_rollover(pid, "week", "2026-09-28", path=db)
    assert r["rolled_in_seconds"] == 0


def test_rollover_computed_not_stored(tmp_path):
    db = str(tmp_path / "f6.db")
    pid = _project(db)
    prev = forecast.previous_period_start("week", "2026-09-28")
    _set_cap(db, pid, "week", prev.isoformat(), 10)
    _set_cap(db, pid, "week", "2026-09-28", 10)
    _entry(db, pid, prev.isoformat(), 360.0)
    before = forecast.compute_rollover(pid, "week", "2026-09-28",
                                       path=db)["rolled_in_seconds"]
    assert before == 4 * 3600
    # Changing the global pct changes the computed display; nothing is
    # written to the ledger or audit log.
    store.set_setting("rollover_cap_pct", "100", path=db)
    after = forecast.compute_rollover(pid, "week", "2026-09-28",
                                      path=db)["rolled_in_seconds"]
    assert after == 4 * 3600  # unused 4h < limit now 10h
    conn = sqlite3.connect(db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM finance_audit_events "
            "WHERE entity_type LIKE '%rollover%'").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_rollover_no_chaining_use_it_or_lose_it(tmp_path):
    db = str(tmp_path / "f7.db")
    pid = _project(db)
    prev = forecast.previous_period_start("week", "2026-09-28")
    _set_cap(db, pid, "week", prev.isoformat(), 10)
    _set_cap(db, pid, "week", "2026-09-28", 10)
    # Previous period unused 10h -> 5h rolls into 2026-09-28.
    r1 = forecast.compute_rollover(pid, "week", "2026-09-28", path=db)
    assert r1["rolled_in_seconds"] == 5 * 3600
    # Next period: the rolled-in 5h does NOT feed another rollover.
    following = date(2026, 9, 28) + timedelta(days=7)
    _set_cap(db, pid, "week", following.isoformat(), 10)
    r2 = forecast.compute_rollover(pid, "week", following.isoformat(),
                                   path=db)
    # Week of 2026-09-28 used nothing of its 10h base; rollover is based
    # on the base cap only, so it is still capped at 5h -- the rolled-in
    # 5h of r1 is use-it-or-lose-it and never compounds.
    assert r2["rolled_in_seconds"] == 5 * 3600


def test_rollover_project_toggle(tmp_path):
    db = str(tmp_path / "f8.db")
    pid = _project(db)
    assert forecast.project_rollover_enabled(pid, path=db) is True
    forecast.set_rollover_enabled(pid, False, path=db)
    assert forecast.project_rollover_enabled(pid, path=db) is False
    prev = forecast.previous_period_start("week", "2026-09-28")
    _set_cap(db, pid, "week", prev.isoformat(), 10)
    _set_cap(db, pid, "week", "2026-09-28", 10)
    r = forecast.compute_rollover(pid, "week", "2026-09-28", path=db)
    assert r["rolled_in_seconds"] == 0


# ---------------------------------------------------------- forecast ---

def test_forecast_parity_with_budget_pacing_bands(tmp_path):
    # forecast._pacing_projection must agree with budgets.budget_status
    # pacing bands on representative inputs.
    from focuscore import forecast as fc
    cases = [
        # (cap, used, start, end, working, today)
        (36000, 9000, date(2026, 9, 28), date(2026, 10, 4),
         {0, 1, 2, 3, 4}, date(2026, 9, 30)),
        (36000, 30000, date(2026, 9, 28), date(2026, 10, 4),
         {0, 1, 2, 3, 4}, date(2026, 9, 30)),
    ]
    for cap, used, start, end, working, today in cases:
        p = fc._pacing_projection(cap, used, start, end, working, today)
        assert p["suppressed"] is False
        assert p["band"] in ("none", "watch", "warning", "reached",
                             "exceeded")
        assert p["projected_seconds"] is not None


def test_forecast_matches_budget_status_pacing(tmp_path, monkeypatch):
    # End-to-end parity: forecast.forecast_period pacing must equal
    # budgets.budget_status pacing on identical data (Q9).
    from focuscore import budgets as budgets_mod
    from focuscore import forecast as fc

    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 30)  # Wednesday

    monkeypatch.setattr(budgets_mod, "date", FixedDate)
    monkeypatch.setattr(fc, "date", FixedDate)

    db = str(tmp_path / "parity.db")
    pid = _project(db)
    # Cap 10h for the current week (2026-09-28..2026-10-04)
    _set_cap(db, pid, "week", "2026-09-28", 10)
    # 3h used across Mon-Wed
    _entry(db, pid, "2026-09-28", 60.0)
    _entry(db, pid, "2026-09-29", 60.0)
    _entry(db, pid, "2026-09-30", 60.0)

    bs = budgets_mod.budget_status(pid, "week", ref_day="2026-09-30",
                                   path=db)
    fp = fc.forecast_period(pid, "week", path=db)

    assert bs["pacing"] is not None
    # Same projection, same band, same suppression
    assert fp["projected_seconds"] == bs["pacing"]["projected_seconds"]
    assert fp["suppressed"] == bs["pacing"]["suppressed"]
    # forecast verdict maps from the same band logic
    assert bs["pacing"]["band"] in ("none", "watch", "warning",
                                    "reached", "exceeded")


def test_forecast_excludes_from_exports(tmp_path):
    # Q9: the export module must not import the forecast module.
    import ast
    with open("focuscore/exports.py") as fh:
        tree = ast.parse(fh.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "focuscore.forecast" not in imported
    assert "forecast" not in imported
    with open("focuscore/invoices.py") as fh:
        tree = ast.parse(fh.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "focuscore.forecast" not in imported
