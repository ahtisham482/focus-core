"""Budget routes: project rates, budgets, rollover settings.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
from datetime import date

from flask import redirect, request

from focuscore import store
from dashboard.app import app
from dashboard.app import (
    _parse_day,
)

@app.route("/timesheet/project/rate", methods=["POST"])
def timesheet_project_rate():
    from focuscore import budgets as budgets_mod
    from focuscore import money as money_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    msg = ""
    try:
        pid = int(request.form.get("id"))
        rate_minor = money_mod.parse_rate_to_minor(request.form.get("rate"))
        if rate_minor is None:
            msg = "That rate was not understood -- use a number like 95.50."
        else:
            ok, msg = budgets_mod.set_project_rate(pid, rate_minor)
    except (TypeError, ValueError):
        msg = "Could not save the rate."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/project/budget", methods=["POST"])
def timesheet_project_budget():
    from focuscore import budgets as budgets_mod
    from focuscore import money as money_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    msg = "Budget saved."
    try:
        pid = int(request.form.get("id"))
        week_s = money_mod.parse_hours_to_seconds(
            request.form.get("week_hours"))
        month_s = money_mod.parse_hours_to_seconds(
            request.form.get("month_hours"))
        week_a = money_mod.parse_rate_to_minor(request.form.get("week_amount"))
        month_a = money_mod.parse_rate_to_minor(
            request.form.get("month_amount"))
        # Blank fields mean "leave unchanged"; an explicit 0 removes a cap.
        for period_type, cap_s, cap_a in (("week", week_s, week_a),
                                         ("month", month_s, month_a)):
            if cap_s is None and cap_a is None:
                continue
            ok, m = budgets_mod.set_budget(pid, period_type, cap_s, cap_a)
            if not ok:
                msg = m
    except (TypeError, ValueError):
        msg = "Could not save the budget."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/project/backfill-rate", methods=["POST"])
def timesheet_project_backfill_rate():
    """Explicit, user-confirmed backfill (Qwen M1.5). The confirm() dialog
    in the form IS the explicit confirmation; the action is audit-logged."""
    from focuscore import budgets as budgets_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        pid = int(request.form.get("id"))
        rate_minor, currency = budgets_mod.get_project_rate(pid)
        if not rate_minor:
            msg = "Set a project rate first."
        else:
            n = budgets_mod.backfill_rate(pid, rate_minor, currency)
            msg = "%d entries marked as confirmed at the current rate." % n
    except (TypeError, ValueError):
        msg = "Could not apply the rate."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/project/rollover", methods=["POST"])
def timesheet_project_rollover():
    """Per-project rollover toggle (Phase 10, Q6: hours only)."""
    from focuscore import forecast as forecast_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        pid = int(request.form.get("id"))
        forecast_mod.set_rollover_enabled(
            pid, request.form.get("enabled") == "1")
        msg = "Rollover preference saved."
    except (TypeError, ValueError):
        msg = "Could not save the rollover preference."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/rollover-settings", methods=["POST"])
def timesheet_rollover_settings():
    """Global rollover cap percentage (Phase 10, Q7: integer 0-100)."""
    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        pct = int((request.form.get("rollover_cap_pct") or "").strip())
        if 0 <= pct <= 100:
            store.set_setting("rollover_cap_pct", str(pct))
            msg = "Rollover cap set to %d%%." % pct
        else:
            msg = "Use a whole number from 0 to 100."
    except (TypeError, ValueError):
        msg = "Use a whole number from 0 to 100."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


