"""Focus coaching: when you focus best, plus burnout warnings.

Everything here is plain documented arithmetic over the stored data --
no machine learning, no invented formulas, no guesswork about the user.

- hourly_productivity(): for each hour of the day (local time), how many
  minutes were tracked and what the Pulse was in that hour.
- best_windows(): the top non-overlapping N-hour windows ranked by focus
  minutes (scores +2/+1).
- burnout_warnings(): rule-based checks with every threshold kept as a
  named constant at the top of this module.
"""

from datetime import date, timedelta

from . import store
from .scoring import productivity_pulse

# --- burnout rule thresholds (documented constants, not hidden magic) ---
LATE_NIGHT_HOUR = 23        # activity at this local hour or later = "late"
LATE_NIGHT_MINUTES = 30     # ...counts when it exceeds this many minutes
LATE_NIGHT_DAYS = 3         # ...warn when this many such days are seen

MARATHON_HOURS = 10         # a single day with more tracked hours = marathon

DISTRACTION_CREEP_DAYS = 14     # comparison needs this many days of range
DISTRACTION_CREEP_POINTS = 10   # warn when the -1/-2 share rises by more
                                # than this many percentage points between
                                # the first 7 days and the last 7 days

LOW_PULSE = 40              # average Pulse below this ...
LOW_PULSE_MIN_HOURS = 30    # ...with more than this many tracked hours
                            # = low recovery warning

FOCUS_SCORES = (2, 1)
DISTRACTING_SCORES = (-1, -2)


def _parse_local(value):
    """Stored timestamp -> naive local datetime (see focus._to_naive)."""
    from datetime import datetime

    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _iter_range(day_from, day_to):
    if isinstance(day_from, str):
        day_from = date.fromisoformat(day_from)
    if isinstance(day_to, str):
        day_to = date.fromisoformat(day_to)
    day = day_from
    while day <= day_to:
        yield day
        day += timedelta(days=1)


def daily_hourly(day_from, day_to, db_path=None):
    """Per-day, per-hour breakdown.

    Returns {day_str: {hour 0..23: {"minutes": float,
    "seconds_by_level": {-2..2: seconds}}}}. Hours with no activity still
    appear, with 0 minutes. Timestamps are read as LOCAL time.
    """
    grid = {}
    for day in _iter_range(day_from, day_to):
        day_str = day.isoformat()
        hours = {h: {"minutes": 0.0,
                     "seconds_by_level": {s: 0.0 for s in
                                          (2, 1, 0, -1, -2)}}
                 for h in range(24)}
        for event in store.get_day_activities(day_str, path=db_path):
            try:
                start = _parse_local(event.get("ts"))
            except (ValueError, TypeError):
                continue
            duration = float(event.get("duration") or 0)
            if duration <= 0:
                continue
            score = event.get("score")
            score = score if score in (2, 1, 0, -1, -2) else 0
            # Split events that cross an hour boundary so each hour only
            # gets the minutes that really happened inside it.
            cursor = start
            remaining = duration
            while remaining > 0:
                next_hour = cursor.replace(minute=0, second=0,
                                           microsecond=0) + timedelta(hours=1)
                chunk = min(remaining, (next_hour - cursor).total_seconds())
                bucket = hours[cursor.hour]
                bucket["minutes"] += chunk / 60.0
                bucket["seconds_by_level"][score] += chunk
                cursor = next_hour
                remaining -= chunk
        grid[day_str] = hours
    return grid


def hourly_productivity(day_from, day_to, db_path=None):
    """Aggregate hourly productivity over a date range.

    Returns {hour 0..23: {"minutes": float (rounded to 1),
    "pulse": float|None}} where pulse is RescueTime's exact weighted Pulse
    computed over that hour's scored seconds across all days in the range,
    and None when the hour has no tracked time.
    """
    totals = {h: {"minutes": 0.0,
                  "seconds_by_level": {s: 0.0 for s in (2, 1, 0, -1, -2)}}
              for h in range(24)}
    for hours in daily_hourly(day_from, day_to, db_path=db_path).values():
        for hour, bucket in hours.items():
            totals[hour]["minutes"] += bucket["minutes"]
            for score, seconds in bucket["seconds_by_level"].items():
                totals[hour]["seconds_by_level"][score] += seconds
    result = {}
    for hour in range(24):
        bucket = totals[hour]
        seconds = bucket["seconds_by_level"]
        total = sum(seconds.values())
        result[hour] = {
            "minutes": round(bucket["minutes"], 1),
            "pulse": productivity_pulse(seconds) if total > 0 else None,
        }
    return result


def best_windows(day_from, day_to, n=3, window_hours=2, db_path=None):
    """Top n non-overlapping windows of window_hours length.

    Candidate windows start at each hour 0..(24 - window_hours). They are
    ranked by focus minutes (scores +2/+1) inside the window, picked
    greedily from best to worst, skipping any window that overlaps an
    already-picked one.

    Returns [{start_hour, end_hour, focus_minutes, avg_pulse}] with
    avg_pulse = the Pulse over the window's scored seconds (None when
    the window has no tracked time).
    """
    # Build candidate windows from the raw per-day grid so the scored
    # seconds stay exact (the aggregated hourly dict rounds minutes).
    grid = daily_hourly(day_from, day_to, db_path=db_path)
    candidates = []
    for start_hour in range(0, 24 - window_hours + 1):
        focus_seconds = 0.0
        seconds_by_level = {s: 0.0 for s in (2, 1, 0, -1, -2)}
        for hours in grid.values():
            for hour in range(start_hour, start_hour + window_hours):
                bucket = hours[hour]
                for score in FOCUS_SCORES:
                    focus_seconds += bucket["seconds_by_level"][score]
                for score, seconds in bucket["seconds_by_level"].items():
                    seconds_by_level[score] += seconds
        total = sum(seconds_by_level.values())
        candidates.append({
            "start_hour": start_hour,
            "end_hour": start_hour + window_hours,
            "focus_minutes": focus_seconds / 60.0,
            "avg_pulse": productivity_pulse(seconds_by_level)
            if total > 0 else None,
        })

    candidates.sort(key=lambda c: c["focus_minutes"], reverse=True)
    picked = []
    used_hours = set()
    for cand in candidates:
        span = set(range(cand["start_hour"], cand["end_hour"]))
        if span & used_hours:
            continue
        picked.append({
            "start_hour": cand["start_hour"],
            "end_hour": cand["end_hour"],
            "focus_minutes": round(cand["focus_minutes"], 1),
            "avg_pulse": cand["avg_pulse"],
        })
        used_hours |= span
        if len(picked) >= n:
            break
    return picked


def _range_stats(day_from, day_to, db_path):
    """(total_hours, distraction_share_pct, avg_pulse, days_with_data)."""
    total_seconds = 0.0
    distracting_seconds = 0.0
    pulses = []
    days_with_data = 0
    for day in _iter_range(day_from, day_to):
        summary = store.get_day_summary(day.isoformat(), path=db_path)
        seconds = summary["total_seconds"]
        if seconds <= 0:
            continue
        days_with_data += 1
        total_seconds += seconds
        distracting_seconds += (
            summary["seconds_by_level"].get(-1, 0)
            + summary["seconds_by_level"].get(-2, 0))
        pulses.append(productivity_pulse(summary["seconds_by_level"]))
    share = (distracting_seconds / total_seconds * 100.0
             if total_seconds > 0 else 0.0)
    avg_pulse = sum(pulses) / len(pulses) if pulses else None
    return total_seconds / 3600.0, share, avg_pulse, days_with_data


def burnout_warnings(day_from, day_to, db_path=None):
    """Rule-based burnout warnings; every threshold is a module constant.

    Returns a list of {"code", "message", "detail"}. Rules that do not
    have enough data stay silent (they are skipped, not guessed).
    """
    if isinstance(day_from, str):
        day_from = date.fromisoformat(day_from)
    if isinstance(day_to, str):
        day_to = date.fromisoformat(day_to)

    warnings = []
    grid = daily_hourly(day_from, day_to, db_path=db_path)

    # 1. Late nights: 30+ minutes of activity at 23:00 or later.
    late_days = [
        day_str for day_str, hours in grid.items()
        if sum(hours[h]["minutes"] for h in range(LATE_NIGHT_HOUR, 24))
        > LATE_NIGHT_MINUTES
    ]
    if len(late_days) >= LATE_NIGHT_DAYS:
        warnings.append({
            "code": "late_nights",
            "message": "You worked late %d nights recently." % len(late_days),
            "detail": "More than %d minutes after %d:00 on %s. Late nights "
                      "steal tomorrow's focus." % (
                          LATE_NIGHT_MINUTES, LATE_NIGHT_HOUR,
                          ", ".join(sorted(late_days))),
        })

    # 2. Marathon days: more than 10 tracked hours in a single day.
    marathon_days = []
    for day_str, hours in grid.items():
        day_hours = sum(b["minutes"] for b in hours.values()) / 60.0
        if day_hours > MARATHON_HOURS:
            marathon_days.append((day_str, round(day_hours, 1)))
    if marathon_days:
        warnings.append({
            "code": "marathon_days",
            "message": "You had %d very long day(s)." % len(marathon_days),
            "detail": "Over %.0f tracked hours on %s. One long day is fine; "
                      "a pattern of them leads to burnout." % (
                          MARATHON_HOURS, ", ".join(
                              "%s (%.1fh)" % d for d in marathon_days)),
        })

    # 3. Distraction creep: the -1/-2 share rose >10 points vs the
    #    previous 7 days. Needs a 14-day range, else skipped silently.
    span_days = (day_to - day_from).days + 1
    if span_days >= DISTRACTION_CREEP_DAYS:
        mid = day_from + timedelta(days=6)
        _h1, share_before, _p1, _d1 = _range_stats(
            day_from, mid, db_path)
        _h2, share_after, _p2, _d2 = _range_stats(
            mid + timedelta(days=1), day_to, db_path)
        if share_after - share_before > DISTRACTION_CREEP_POINTS:
            warnings.append({
                "code": "distraction_creep",
                "message": "Distractions are creeping up.",
                "detail": "Distracting time went from %.0f%% to %.0f%% of "
                          "your tracked time (last 7 days vs the 7 before)."
                          % (share_before, share_after),
            })

    # 4. Low recovery: low average Pulse despite many tracked hours.
    total_hours, _share, avg_pulse, _days = _range_stats(
        day_from, day_to, db_path)
    if (avg_pulse is not None and avg_pulse < LOW_PULSE
            and total_hours > LOW_PULSE_MIN_HOURS):
        warnings.append({
            "code": "low_recovery",
            "message": "Lots of hours, low Pulse -- you may need rest.",
            "detail": "%.1f tracked hours at an average Pulse of %.1f "
                      "(below %d). Consider a lighter day." % (
                          total_hours, avg_pulse, LOW_PULSE),
        })

    return warnings
