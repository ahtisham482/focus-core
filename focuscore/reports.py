"""Weekly report (RescueTime-style, without the email).

RescueTime emails a weekly summary; we have no email setup, so the same
report is served as a dashboard page instead (/report). The numbers are
plain documented arithmetic over the stored data -- no invented formulas.

week_start is a date (a Monday). The week always covers 7 days,
Monday through Sunday.
"""

from datetime import date, timedelta

from . import store
from .scoring import productivity_pulse

# Goal statuses that count as a "hit" for the day (see goals.evaluate_goal).
HIT_STATUSES = ("achieved", "on_track")

# How many top categories the report lists.
TOP_CATEGORIES = 5


def _week_dates(week_start):
    return [week_start + timedelta(days=i) for i in range(7)]


def weekly_report(week_start, db_path=None):
    """Summarize one week (Monday..Sunday).

    Returns {"week_start", "week_end", "days": [...], "total_hours",
    "avg_pulse", "top_categories", "goals", "focus_sessions"} where:
      - days: [{date, total_hours, pulse (None when no data),
                focus_hours (scores +2/+1)}]
      - total_hours: tracked hours over the whole week
      - avg_pulse: mean of the days' pulses, SKIPPING days with no data
        (None when the week has no data at all)
      - top_categories: [(category, hours)] top 5 by time
      - goals: [{name, days_hit, days_total}] -- each goal is evaluated
        per day against that day's stored summary; days_total counts only
        days with tracked data
      - focus_sessions: {count, focus_minutes, blocks} for sessions that
        started inside the week
    """
    from . import focus as focus_mod
    from . import goals as goals_mod

    if isinstance(week_start, str):
        week_start = date.fromisoformat(week_start)
    days = _week_dates(week_start)
    week_end = days[-1]

    day_rows = []
    category_seconds = {}
    pulses = []
    total_hours = 0.0
    for day in days:
        day_str = day.isoformat()
        summary = store.get_day_summary(day_str, path=db_path)
        seconds = summary["total_seconds"]
        hours = seconds / 3600.0
        if seconds > 0:
            pulse = productivity_pulse(summary["seconds_by_level"])
            pulses.append(pulse)
            focus_hours = (summary["seconds_by_level"].get(2, 0)
                           + summary["seconds_by_level"].get(1, 0)) / 3600.0
            for name, secs in summary["seconds_by_category"].items():
                category_seconds[name] = category_seconds.get(name, 0.0) + secs
        else:
            pulse = None
            focus_hours = 0.0
        total_hours += hours
        day_rows.append({
            "date": day_str,
            "total_hours": round(hours, 2),
            "pulse": pulse,
            "focus_hours": round(focus_hours, 2),
        })

    top_categories = [
        (name, round(seconds / 3600.0, 2))
        for name, seconds in sorted(
            category_seconds.items(), key=lambda kv: kv[1], reverse=True
        )[:TOP_CATEGORIES]
    ]

    goals = []
    for goal in store.list_goals(path=db_path):
        days_hit = 0
        days_total = 0
        for day in days:
            day_str = day.isoformat()
            summary = store.get_day_summary(day_str, path=db_path)
            if summary["total_seconds"] <= 0:
                continue
            days_total += 1
            if goals_mod.evaluate_goal(goal, summary)["status"] \
                    in HIT_STATUSES:
                days_hit += 1
        goals.append({"name": goal["name"], "days_hit": days_hit,
                      "days_total": days_total})

    session_count = 0
    focus_minutes = 0.0
    blocks = 0
    week_days = {d.isoformat() for d in days}
    for session in store.list_sessions(limit=1000, path=db_path):
        if (session["started_at"] or "")[:10] not in week_days:
            continue
        session_count += 1
        if session["status"] == "completed":
            summary = focus_mod.session_summary(session["id"],
                                                db_path=db_path)
            if "error" not in summary:
                focus_minutes += summary["focus_minutes"]
                blocks += summary["blocks_count"]

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "days": day_rows,
        "total_hours": round(total_hours, 2),
        "avg_pulse": round(sum(pulses) / len(pulses), 1) if pulses else None,
        "top_categories": top_categories,
        "goals": goals,
        "focus_sessions": {
            "count": session_count,
            "focus_minutes": round(focus_minutes, 1),
            "blocks": blocks,
        },
    }
