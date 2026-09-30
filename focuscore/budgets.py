"""Phase 9: project budgets with an append-only ledger (Qwen audit M2/M3).

Binding rules:
- M2: caps live in project_budget_ledger (append-only, effective-dated);
  projects.* columns are a current-state cache only. Historical reports
  MUST use the ledger.
- M3: pacing is working-day aware (Mon-Fri default, configurable),
  routine pacing warnings are suppressed on non-working days, tolerance
  bands + materiality thresholds apply, everything is advisory.
- A/B: money = integer minor units, time budgets = integer seconds.
- D: budgets NEVER block anything.
"""

from datetime import date, datetime, timedelta
import logging

from focuscore import money as money_mod
from focuscore import store

# Roadmap 0.3: log failures that used to be swallowed silently.
logger = logging.getLogger(__name__)

PERIOD_TYPES = ("week", "month")

# Pacing tolerance bands (Qwen M3): projected end vs cap.
_PACING_WATCH = 1.05
_PACING_WARN = 1.15
# Materiality: ignore projected overruns smaller than this (M3.4).
_MATERIALITY_SECONDS = 3600


def utcnow_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_working_days(value):
    """'0,1,2,3,4' -> {0,1,2,3,4} (Mon=0). Falls back to Mon-Fri."""
    try:
        days = {int(p.strip()) for p in str(value).split(",") if p.strip() != ""}
        days = {d for d in days if 0 <= d <= 6}
        return days or {0, 1, 2, 3, 4}
    except Exception:
        return {0, 1, 2, 3, 4}


def get_working_days(path=None):
    return parse_working_days(store.get_setting("working_days", "0,1,2,3,4", path=path))


def period_bounds(period_type, ref_day=None):
    """(start_date, end_date) for the week (Mon-Sun) or calendar month
    containing ref_day. ref_day may be a date or 'YYYY-MM-DD' string."""
    if ref_day is None:
        ref_day = date.today()
    elif isinstance(ref_day, str):
        ref_day = date.fromisoformat(ref_day)
    if period_type == "week":
        start = ref_day - timedelta(days=ref_day.weekday())
        return start, start + timedelta(days=6)
    if period_type == "month":
        start = ref_day.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1) - timedelta(days=1)
        return start, end
    raise ValueError("period_type must be 'week' or 'month'")


def working_days_between(start_day, end_day, working):
    """Count of working days in [start_day, end_day] (inclusive)."""
    count = 0
    day = start_day
    while day <= end_day:
        if day.weekday() in working:
            count += 1
        day += timedelta(days=1)
    return count


def log_finance_event(entity_type, entity_id, event_type, payload=None, path=None):
    """Append to finance_audit_events (Qwen constraint E). Never raises."""
    import json

    try:
        store.init_db(path)
        conn = store.get_db(path)
        try:
            conn.execute(
                "INSERT INTO finance_audit_events "
                "(entity_type, entity_id, event_type, payload_json, "
                "created_at_utc) VALUES (?, ?, ?, ?, ?)",
                (
                    entity_type,
                    entity_id,
                    event_type,
                    json.dumps(payload or {}),
                    utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Roadmap 0.3: a dropped ledger write was silent.
        logger.exception("budget ledger write failed")
        pass


# ------------------------------------------------------------ budgets ---

_CACHE_COLS = {
    "week": ("current_weekly_cap_seconds", "current_weekly_cap_amount_minor"),
    "month": ("current_monthly_cap_seconds", "current_monthly_cap_amount_minor"),
}


def set_budget(
    project_id,
    period_type,
    cap_seconds=None,
    cap_amount_minor=None,
    currency=None,
    note="",
    path=None,
):
    """Change a budget cap. ALWAYS inserts a ledger row (M2.2); the
    projects.* cache is updated too. cap=None removes that cap (NULL
    revision row, M2 test: 'Removing cap stores NULL cap revision').

    Never raises on bad input: returns (ok, message)."""
    if period_type not in PERIOD_TYPES:
        return False, "Unknown period."
    if cap_seconds is not None and cap_seconds < 0:
        return False, "Hours cap cannot be negative."
    if cap_amount_minor is not None and cap_amount_minor < 0:
        return False, "Amount cap cannot be negative."
    currency = (
        currency or store.get_setting("currency", "USD", path=path) or "USD"
    ).upper()
    start_day, _ = period_bounds(period_type)
    now_utc = utcnow_iso()
    store.init_db(path)
    conn = store.get_db(path)
    try:
        conn.execute(
            "INSERT INTO project_budget_ledger "
            "(project_id, period_type, period_start, cap_seconds, "
            "cap_amount_minor, currency, effective_from_utc, note, "
            "created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                period_type,
                start_day.isoformat(),
                cap_seconds,
                cap_amount_minor,
                currency,
                now_utc,
                note or "",
                now_utc,
            ),
        )
        sec_col, amt_col = _CACHE_COLS[period_type]
        conn.execute(
            "UPDATE projects SET %s = ?, %s = ?, budget_currency = ? "
            "WHERE id = ?" % (sec_col, amt_col),
            (cap_seconds, cap_amount_minor, currency, project_id),
        )
        conn.commit()
    finally:
        conn.close()
    log_finance_event(
        "project",
        project_id,
        "budget_cap_changed",
        {
            "period_type": period_type,
            "cap_seconds": cap_seconds,
            "cap_amount_minor": cap_amount_minor,
            "currency": currency,
            "note": note or "",
        },
        path=path,
    )
    return True, "Budget saved."


def get_cap_for_period(project_id, period_type, period_start_day, path=None):
    """The cap governing a period, from the LEDGER (M2.2). Returns None
    when no cap was ever set. Result includes effective_from_utc and
    changed_during_period so reports can declare what they used (M2.6)."""
    if isinstance(period_start_day, str):
        period_start_day = date.fromisoformat(period_start_day)
    store.init_db(path)
    conn = store.get_db(path)
    try:
        rows = conn.execute(
            "SELECT period_start, cap_seconds, cap_amount_minor, currency, "
            "effective_from_utc FROM project_budget_ledger "
            "WHERE project_id = ? AND period_type = ? "
            "AND period_start <= ? "
            "ORDER BY period_start DESC, effective_from_utc DESC",
            (project_id, period_type, period_start_day.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    latest = rows[0]
    # Did the cap change during this period? (a revision whose effective
    # date falls inside the period, on top of an earlier revision)
    _, period_end = period_bounds(period_type, period_start_day)
    during = [
        r
        for r in rows
        if period_start_day.isoformat()
        <= (r["effective_from_utc"] or "")[:10]
        <= period_end.isoformat()
    ]
    earlier = [
        r
        for r in rows
        if (r["effective_from_utc"] or "")[:10] < period_start_day.isoformat()
    ]
    # Changed during the period: a revision took effect inside the period on
    # top of an earlier revision -- either an earlier-period cap, or the
    # initial cap created earlier inside the same period (two "during"
    # revisions means one of them changed what the period started with).
    changed = bool(earlier and during) or len(during) >= 2
    return {
        "period_type": period_type,
        "period_start": latest["period_start"],
        "cap_seconds": latest["cap_seconds"],
        "cap_amount_minor": latest["cap_amount_minor"],
        "currency": latest["currency"] or "USD",
        "effective_from_utc": latest["effective_from_utc"],
        "changed_during_period": changed,
    }


def project_spend(project_id, day_from, day_to, path=None):
    """Spend buckets for accepted entries (M2: hour caps count ALL tagged
    time; money caps count confirmed amounts only)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        rows = conn.execute(
            "SELECT minutes, is_billable, hourly_rate_minor, rate_status "
            "FROM timesheet_entries "
            "WHERE project_id = ? AND day >= ? AND day <= ? "
            "AND status = 'accepted'",
            (project_id, day_from, day_to),
        ).fetchall()
    finally:
        conn.close()
    seconds_total = 0
    seconds_billable = 0
    confirmed_minor = 0
    estimated_minor = 0
    unknown_billable_seconds = 0
    for r in rows:
        seconds = int(round((r["minutes"] or 0) * 60))
        seconds_total += seconds
        _raw = r["is_billable"]
        billable = (_raw if _raw is not None else 1) == 1
        if billable:
            seconds_billable += seconds
        status = r["rate_status"] or "unknown"
        if billable and status == "confirmed":
            amount = money_mod.amount_minor_for(seconds, r["hourly_rate_minor"])
            confirmed_minor += amount or 0
        elif billable and status == "estimated":
            amount = money_mod.amount_minor_for(seconds, r["hourly_rate_minor"])
            estimated_minor += amount or 0
        elif billable and status == "unknown":
            unknown_billable_seconds += seconds
    return {
        "seconds_total": seconds_total,
        "seconds_billable": seconds_billable,
        "confirmed_minor": confirmed_minor,
        "estimated_minor": estimated_minor,
        "unknown_billable_seconds": unknown_billable_seconds,
    }


def _band_for(pct):
    if pct >= 1.0:
        return "over"
    if pct >= 0.90:
        return "warning"
    if pct >= 0.75:
        return "watch"
    return "on_track"


def budget_status(project_id, period_type, ref_day=None, path=None):
    """Full advisory status for one project + period (M3). Pure read;
    never writes, never blocks (D)."""
    if ref_day is None:
        ref_day = date.today()
    elif isinstance(ref_day, str):
        ref_day = date.fromisoformat(ref_day)
    start_day, end_day = period_bounds(period_type, ref_day)
    cap = get_cap_for_period(project_id, period_type, start_day, path=path)
    spend = project_spend(
        project_id, start_day.isoformat(), end_day.isoformat(), path=path
    )
    working = get_working_days(path=path)
    result = {
        "project_id": project_id,
        "period_type": period_type,
        "period_start": start_day.isoformat(),
        "period_end": end_day.isoformat(),
        "cap": cap,
        "spend": spend,
        "hours": None,
        "amount": None,
        "pacing": None,
        "summary": "No budget set for this period.",
    }
    if cap is None:
        return result

    # -- absolute bands (factual, M3.7) ---------------------------------
    if cap["cap_seconds"]:
        pct = spend["seconds_total"] / cap["cap_seconds"]
        result["hours"] = {
            "consumed_seconds": spend["seconds_total"],
            "cap_seconds": cap["cap_seconds"],
            "pct": pct,
            "band": _band_for(pct),
        }
    if cap["cap_amount_minor"]:
        pct = spend["confirmed_minor"] / cap["cap_amount_minor"]
        result["amount"] = {
            "consumed_minor": spend["confirmed_minor"],
            "cap_minor": cap["cap_amount_minor"],
            "currency": cap["currency"],
            "pct": pct,
            "band": _band_for(pct),
        }

    # -- pacing projection (working-day aware, M3.1-M3.4) ----------------
    if cap["cap_seconds"]:
        total_wd = working_days_between(start_day, end_day, working)
        elapsed_end = min(ref_day, end_day)
        elapsed_wd = (
            working_days_between(start_day, elapsed_end, working)
            if elapsed_end >= start_day
            else 0
        )
        remaining_wd = total_wd - elapsed_wd
        is_working_day = ref_day.weekday() in working
        pacing = {
            "suppressed": False,
            "suppress_reason": "",
            "projected_seconds": None,
            "band": "none",
            "explain": "",
        }
        if total_wd == 0 or elapsed_wd < 2:
            pacing["suppressed"] = True
            pacing["suppress_reason"] = (
                "Not enough working days yet for a reliable projection."
            )
        elif not is_working_day:
            # M3.3: routine pacing warnings stay quiet on rest days.
            pacing["suppressed"] = True
            pacing["suppress_reason"] = (
                "Today is not a working day; pacing resumes tomorrow."
            )
        else:
            used = spend["seconds_total"]
            projected = (
                used + (used / elapsed_wd) * remaining_wd if elapsed_wd else used
            )
            pacing["projected_seconds"] = int(projected)
            pacing["explain"] = (
                "Used %s in %d working days; at this pace about %s "
                "by %s."
                % (
                    money_mod.format_duration(used),
                    elapsed_wd,
                    money_mod.format_duration(int(projected)),
                    end_day.isoformat(),
                )
            )
            over = projected - cap["cap_seconds"]
            if used >= cap["cap_seconds"]:
                pacing["band"] = "exceeded" if used > cap["cap_seconds"] else "reached"
            elif (
                projected > cap["cap_seconds"] * _PACING_WARN
                and over >= _MATERIALITY_SECONDS
            ):
                pacing["band"] = "warning"
            elif (
                projected > cap["cap_seconds"] * _PACING_WATCH
                and over >= _MATERIALITY_SECONDS
            ):
                pacing["band"] = "watch"
        result["pacing"] = pacing

    result["summary"] = _plain_summary(result, cap)
    return result


def _plain_summary(status, cap):
    """One plain-English line (M3.6: explainable)."""
    bits = []
    hours = status["hours"]
    amount = status["amount"]
    if hours:
        bits.append(
            "Used %s of %s this %s."
            % (
                money_mod.format_duration(hours["consumed_seconds"]),
                money_mod.format_duration(hours["cap_seconds"]),
                "week" if status["period_type"] == "week" else "month",
            )
        )
    if amount:
        bits.append(
            "Billed %s of %s."
            % (
                money_mod.format_minor(amount["consumed_minor"], amount["currency"]),
                money_mod.format_minor(amount["cap_minor"], amount["currency"]),
            )
        )
    pacing = status.get("pacing")
    if pacing and not pacing.get("suppressed") and pacing.get("explain"):
        if pacing["band"] in ("warning", "watch"):
            bits.append("Pace warning: " + pacing["explain"])
        elif pacing["band"] in ("reached", "exceeded"):
            bits.append("Budget cap reached.")
    if cap.get("changed_during_period"):
        bits.append(
            "Note: the cap changed during this period; figures use the latest cap."
        )
    unknown = status["spend"]["unknown_billable_seconds"]
    if unknown and amount:
        bits.append(
            "%s of billable time has no confirmed rate and is "
            "not in the billed total." % money_mod.format_duration(unknown)
        )
    return " ".join(bits) if bits else "No budget set for this period."


# ------------------------------------------------- project rates (M1) ---


def set_project_rate(project_id, rate_minor, currency=None, path=None):
    """Set the project's current rate. Updates the cache ONLY — existing
    timesheet entries are NEVER rewritten (M1.4). Returns (ok, message)."""
    if rate_minor is not None and rate_minor < 0:
        return False, "Rate cannot be negative."
    currency = (
        currency or store.get_setting("currency", "USD", path=path) or "USD"
    ).upper()
    store.init_db(path)
    conn = store.get_db(path)
    try:
        conn.execute(
            "UPDATE projects SET hourly_rate_minor = ?, rate_currency = ? WHERE id = ?",
            (rate_minor, currency if rate_minor else None, project_id),
        )
        conn.commit()
    finally:
        conn.close()
    log_finance_event(
        "project",
        project_id,
        "rate_changed",
        {"hourly_rate_minor": rate_minor, "currency": currency},
        path=path,
    )
    return True, "Rate saved. Existing entries were not changed."


def get_project_rate(project_id, path=None):
    """(hourly_rate_minor, rate_currency) cache for a project."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        row = conn.execute(
            "SELECT hourly_rate_minor, rate_currency FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None, None
    return row["hourly_rate_minor"], row["rate_currency"] or "USD"


def count_unrated_entries(project_id, path=None):
    """Billable accepted entries for a project with unknown rate (M1.5 preview)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM timesheet_entries "
            "WHERE project_id = ? AND status = 'accepted' "
            "AND (is_billable IS NULL OR is_billable = 1) "
            "AND (rate_status IS NULL OR rate_status = 'unknown')",
            (project_id,),
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def backfill_rate(project_id, rate_minor, currency, path=None):
    """Explicit, user-confirmed backfill (M1.5): mark unknown-rate entries
    as confirmed at the given rate. The caller MUST have obtained explicit
    user confirmation. Returns the number of entries updated."""
    now_utc = utcnow_iso()
    store.init_db(path)
    conn = store.get_db(path)
    try:
        cur = conn.execute(
            "UPDATE timesheet_entries SET hourly_rate_minor = ?, "
            "rate_currency = ?, rate_status = 'confirmed', "
            "rate_confirmed_at_utc = ? "
            "WHERE project_id = ? AND status = 'accepted' "
            "AND (rate_status IS NULL OR rate_status = 'unknown')",
            (rate_minor, currency, now_utc, project_id),
        )
        conn.commit()
        updated = cur.rowcount or 0
    finally:
        conn.close()
    log_finance_event(
        "project",
        project_id,
        "historical_rate_confirmed",
        {
            "hourly_rate_minor": rate_minor,
            "currency": currency,
            "entries_updated": updated,
            "confirmed_at_utc": now_utc,
        },
        path=path,
    )
    return updated
