"""Phase 10: budget rollover + forecasting (Qwen audit Q6-Q9, Q14).

Binding rules:
- Q14: rollover is HOURS ONLY. Money caps are ignored by rollover and
  by the rollover forecast.
- Q6: rollover is COMPUTED, never stored. compute_rollover() is a pure
  function of the append-only project_budget_ledger: no side effects,
  no audit events. Consequence (documented in UI copy): changing
  rollover_cap_pct changes historical rollover displays, because the
  value is derived, not stored.
- Q7: rolled = min(unused, next_base_cap * cap_pct // 100), integer
  math only. cap_pct is an integer 0-100 (0 = disabled). No next-period
  base cap -> rollover is 0.
- Q8: exactly one period, no chaining, not configurable. "Use it or
  lose it": rolled-in budget that goes unused does not carry forward.
- Q9: forecasts NEVER reach client-facing documents. This module is
  imported by budgets.py (dashboard) and dashboard/app.py only --
  focuscore/exports.py and focuscore/invoices.py MUST NOT import it.
- D (Phase 9): everything here is advisory. Nothing blocks.
"""

from datetime import date, timedelta

from focuscore import budgets as budgets_mod
from focuscore import money as money_mod
from focuscore import store

# Pacing bands mirror budgets.budget_status (Qwen M3). A parity test in
# tests/test_forecast.py asserts both agree on sample inputs.
_PACING_WATCH = 1.05
_PACING_WARN = 1.15
_MATERIALITY_SECONDS = 3600


def get_rollover_cap_pct(path=None):
    """Integer 0..100. 0 disables rollover. Bad values fall back to 50."""
    try:
        pct = int(store.get_setting("rollover_cap_pct", "50", path=path))
    except (TypeError, ValueError):
        return 50
    return max(0, min(100, pct))


def project_rollover_enabled(project_id, path=None):
    """Per-project toggle (migration 8 column; defaults ON)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        try:
            row = conn.execute(
                "SELECT rollover_enabled FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        except Exception:
            return True  # column missing on exotic DBs -> behave as enabled
        if row is None:
            return False
        return (row["rollover_enabled"] or 0) == 1
    finally:
        conn.close()


def set_rollover_enabled(project_id, enabled, path=None):
    store.init_db(path)
    conn = store.get_db(path)
    try:
        conn.execute(
            "UPDATE projects SET rollover_enabled = ? WHERE id = ?",
            (1 if enabled else 0, project_id),
        )
        conn.commit()
    finally:
        conn.close()


def previous_period_start(period_type, period_start_day):
    """First day of the period before the given period start."""
    if isinstance(period_start_day, str):
        period_start_day = date.fromisoformat(period_start_day)
    if period_type == "week":
        return period_start_day - timedelta(days=7)
    # month: first day of the previous calendar month
    first = period_start_day.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def compute_rollover(project_id, period_type, period_start_day, path=None):
    """Pure function: rollover INTO the given period (Q6).

    Returns dict with base_cap_seconds, rolled_in_seconds,
    effective_cap_seconds, and an explain string. Hours only (Q14).
    """
    if isinstance(period_start_day, str):
        period_start_day = date.fromisoformat(period_start_day)
    pct = get_rollover_cap_pct(path=path)
    enabled = project_rollover_enabled(project_id, path=path)
    result = {
        "project_id": project_id,
        "period_type": period_type,
        "period_start": period_start_day.isoformat(),
        "cap_pct": pct,
        "enabled": enabled,
        "base_cap_seconds": 0,
        "previous_period_start": None,
        "previous_base_cap_seconds": 0,
        "previous_unused_seconds": 0,
        "rolled_in_seconds": 0,
        "capped": False,
        "effective_cap_seconds": 0,
        "reason": "",
        "note": "Use-it-or-lose-it: rolled-in budget does not carry "
                "forward to the following period.",
    }
    if not enabled:
        result["reason"] = "Rollover is turned off for this project."
        return result
    if pct == 0:
        result["reason"] = "Rollover cap is 0% (disabled in settings)."
        return result

    next_cap = budgets_mod.get_cap_for_period(
        project_id, period_type, period_start_day, path=path
    )
    next_base = (next_cap or {}).get("cap_seconds") or 0
    result["base_cap_seconds"] = next_base
    result["effective_cap_seconds"] = next_base
    if not next_cap or not next_base:
        # Q8: no ledger entry governing the next period -> 0, silently.
        result["reason"] = "No hour cap set for this period."
        return result

    prev_start = previous_period_start(period_type, period_start_day)
    prev_cap = budgets_mod.get_cap_for_period(
        project_id, period_type, prev_start, path=path
    )
    prev_base = (prev_cap or {}).get("cap_seconds") or 0
    result["previous_period_start"] = prev_start.isoformat()
    result["previous_base_cap_seconds"] = prev_base
    if not prev_cap or not prev_base:
        result["reason"] = "No hour cap set for the previous period."
        return result

    _, prev_end = budgets_mod.period_bounds(period_type, prev_start)
    spend = budgets_mod.project_spend(
        project_id, prev_start.isoformat(), prev_end.isoformat(), path=path
    )
    unused = max(0, prev_base - spend["seconds_total"])
    result["previous_unused_seconds"] = unused
    if unused == 0:
        result["reason"] = "Previous period's hour cap was fully used."
        return result

    # Q7: integer math only; denominator is the NEXT period's base cap.
    limit = next_base * pct // 100
    rolled = min(unused, limit)
    result["rolled_in_seconds"] = rolled
    result["capped"] = rolled < unused
    result["effective_cap_seconds"] = next_base + rolled
    result["reason"] = ""
    return result


def _pacing_projection(cap_seconds, used_seconds, start_day, end_day,
                       working, today):
    """Working-day-aware projection. Mirrors budgets.budget_status pacing
    rules (suppression, bands, materiality) so both stay consistent."""
    pacing = {
        "suppressed": False,
        "suppress_reason": "",
        "projected_seconds": None,
        "band": "none",
        "explain": "",
    }
    total_wd = budgets_mod.working_days_between(start_day, end_day, working)
    elapsed_end = min(today, end_day)
    elapsed_wd = (
        budgets_mod.working_days_between(start_day, elapsed_end, working)
        if elapsed_end >= start_day
        else 0
    )
    remaining_wd = total_wd - elapsed_wd
    if total_wd == 0 or elapsed_wd < 2:
        pacing["suppressed"] = True
        pacing["suppress_reason"] = (
            "Not enough working days yet for a reliable forecast."
        )
        return pacing
    if today.weekday() not in working:
        pacing["suppressed"] = True
        pacing["suppress_reason"] = (
            "Today is not a working day; the forecast resumes tomorrow."
        )
        return pacing
    projected = used_seconds + (used_seconds / elapsed_wd) * remaining_wd
    pacing["projected_seconds"] = int(projected)
    pacing["explain"] = (
        "Used %s in %d working days; at this pace about %s by %s."
        % (
            money_mod.format_duration(used_seconds),
            elapsed_wd,
            money_mod.format_duration(int(projected)),
            end_day.isoformat(),
        )
    )
    over = projected - cap_seconds
    if used_seconds >= cap_seconds:
        pacing["band"] = (
            "exceeded" if used_seconds > cap_seconds else "reached"
        )
    elif (projected > cap_seconds * _PACING_WARN
          and over >= _MATERIALITY_SECONDS):
        pacing["band"] = "warning"
    elif (projected > cap_seconds * _PACING_WATCH
          and over >= _MATERIALITY_SECONDS):
        pacing["band"] = "watch"
    return pacing


def forecast_period(project_id, period_type, path=None):
    """Hours-only forecast for the CURRENT period (Q14). Advisory.

    Returns effective cap (base + rollover), consumed, projected,
    verdict, and the projected rollover into next period.
    """
    today = date.today()
    start_day, end_day = budgets_mod.period_bounds(period_type, today)
    result = {
        "project_id": project_id,
        "period_type": period_type,
        "period_start": start_day.isoformat(),
        "period_end": end_day.isoformat(),
        "has_cap": False,
        "effective_cap_seconds": 0,
        "base_cap_seconds": 0,
        "rolled_in_seconds": 0,
        "consumed_seconds": 0,
        "projected_seconds": None,
        "verdict": "no_cap",
        "rollover_out_seconds": 0,
        "suppressed": False,
        "explain": "No hour cap set for this period.",
    }
    roll = compute_rollover(project_id, period_type, start_day, path=path)
    if not roll["base_cap_seconds"]:
        return result
    result["has_cap"] = True
    result["base_cap_seconds"] = roll["base_cap_seconds"]
    result["rolled_in_seconds"] = roll["rolled_in_seconds"]
    effective = roll["effective_cap_seconds"]
    result["effective_cap_seconds"] = effective

    spend = budgets_mod.project_spend(
        project_id, start_day.isoformat(), today.isoformat(), path=path
    )
    used = spend["seconds_total"]
    result["consumed_seconds"] = used

    working = budgets_mod.get_working_days(path=path)
    pacing = _pacing_projection(
        effective, used, start_day, end_day, working, today
    )
    result["suppressed"] = pacing["suppressed"]
    if pacing["suppressed"]:
        result["verdict"] = "unknown"
        result["explain"] = pacing["suppress_reason"]
        return result

    projected = pacing["projected_seconds"]
    result["projected_seconds"] = projected
    if used >= effective:
        result["verdict"] = "over"
        result["explain"] = (
            "Already over the effective cap of %s "
            "(%s base + %s rolled in)."
            % (
                money_mod.format_duration(effective),
                money_mod.format_duration(roll["base_cap_seconds"]),
                money_mod.format_duration(roll["rolled_in_seconds"]),
            )
        )
    elif projected > effective:
        result["verdict"] = "likely_over"
        result["explain"] = (
            "On track to use %s of %s by %s — likely over budget."
            % (
                money_mod.format_duration(projected),
                money_mod.format_duration(effective),
                end_day.isoformat(),
            )
        )
    else:
        result["verdict"] = "on_track"
        result["explain"] = (
            "On track to use %s of %s by %s."
            % (
                money_mod.format_duration(projected),
                money_mod.format_duration(effective),
                end_day.isoformat(),
            )
        )
    # What would roll into next period if the pace holds (Q8: one hop).
    result["rollover_out_seconds"] = max(0, effective - projected)
    return result
