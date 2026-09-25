"""Daily goals with live progress (RescueTime-style).

A goal is a daily target of one of two kinds:
    - "more_than": do at least X (e.g. 120 minutes of Software Development,
      or a Productivity Pulse of at least 70).
    - "less_than": stay under Y (e.g. at most 30 minutes of Entertainment).

The target can be time in a category ("category") or the day's
Productivity Pulse score ("pulse").

Status rules (plain threshold comparison, no invented formula):
    - direction "less_than": current <= target -> "on_track",
                              otherwise      -> "missed"
    - direction "more_than": current >= target -> "achieved",
                              otherwise       -> "behind"
"""

from . import store
from .scoring import productivity_pulse

DIRECTIONS = ("more_than", "less_than")
TARGET_TYPES = ("category", "pulse")


def add_goal(name, direction, target_type, target_name=None,
             threshold_minutes=None, threshold_pulse=None, pinned=False,
             db_path=None):
    """Validate and store a new goal; returns its id."""
    if direction not in DIRECTIONS:
        raise ValueError("direction must be one of %r" % (DIRECTIONS,))
    if target_type not in TARGET_TYPES:
        raise ValueError("target_type must be one of %r" % (TARGET_TYPES,))
    if target_type == "category":
        if not target_name:
            raise ValueError("category goals need a category name")
        if threshold_minutes is None or float(threshold_minutes) < 0:
            raise ValueError("category goals need threshold_minutes >= 0")
    else:
        if threshold_pulse is None:
            raise ValueError("pulse goals need threshold_pulse between 0 and 100")
        threshold = float(threshold_pulse)
        if not 0 <= threshold <= 100:
            raise ValueError("pulse goals need threshold_pulse between 0 and 100")
    return store.add_goal(
        name.strip(), direction, target_type, target_name,
        threshold_minutes=(float(threshold_minutes)
                           if threshold_minutes is not None else None),
        threshold_pulse=(float(threshold_pulse)
                         if threshold_pulse is not None else None),
        pinned=pinned, path=db_path)


def evaluate_goal(goal, day_summary):
    """Evaluate one goal against a day summary (see store.get_day_summary).

    Returns {"goal_id", "name", "direction", "target_type", "target_name",
             "current", "target", "pct", "status", "pinned", "unit"} where
    current/target are minutes for category goals and Pulse points
    (0-100) for pulse goals, and pct = current/target*100 (when the
    target is 0, pct is 100.0 if current <= target else 0.0).
    """
    if goal["target_type"] == "pulse":
        current = productivity_pulse(day_summary["seconds_by_level"])
        target = float(goal["threshold_pulse"] or 0)
        unit = "pts"
    else:
        seconds = day_summary["seconds_by_category"].get(
            goal["target_name"], 0.0)
        current = float(seconds) / 60.0
        target = float(goal["threshold_minutes"] or 0)
        unit = "min"

    if target > 0:
        pct = current / target * 100.0
    else:
        pct = 100.0 if current <= target else 0.0

    if goal["direction"] == "less_than":
        status = "on_track" if current <= target else "missed"
    else:
        status = "achieved" if current >= target else "behind"

    return {
        "goal_id": goal["id"],
        "name": goal["name"],
        "direction": goal["direction"],
        "target_type": goal["target_type"],
        "target_name": goal["target_name"],
        "current": current,
        "target": target,
        "pct": pct,
        "status": status,
        "pinned": bool(goal["pinned"]),
        "unit": unit,
    }


def evaluate_all(day_summary, db_path=None):
    """Evaluate every stored goal against a day summary."""
    return [evaluate_goal(g, day_summary)
            for g in store.list_goals(path=db_path)]
