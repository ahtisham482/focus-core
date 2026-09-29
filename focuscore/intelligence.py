"""Deep time intelligence: when you focus best, how deep you go, and
what breaks your focus.

Plain documented arithmetic over the stored ActivityWatch events -- no
machine learning, no hidden formulas. Every threshold is a named
constant below. All functions are pure (read-only over the DB) and take
``db_path=None``.

Builds on coaching.daily_hourly() so hour bucketing (including events
that cross hour boundaries) is identical to the Coaching page. The
stored ``score`` column is already override-resolved (see
store.apply_override_to_day), so it is used directly.
"""

from datetime import date, timedelta
from statistics import median

from . import store
from .coaching import (
    _parse_local,
    daily_hourly,
    DISTRACTING_SCORES,
    FOCUS_SCORES,
)
from .scoring import productivity_pulse

# --- documented constants (not hidden magic) ---
CHRONOTYPE_DAYS = 28      # curve window: 4 full weeks, 4 samples/weekday
MORNING_START = 5           # morning focus window: 05:00 (local) ...
MORNING_END = 12            # ... to 12:00
EVENING_START = 18          # evening focus window: 18:00 to 24:00
CHRONOTYPE_THRESHOLD = 0.55  # >=55% of focus minutes in a window -> type

STRETCH_GAP_MINUTES = 5     # gaps <= this merge into one productive
                            # stretch (matches the timesheet block rule)
FOCUS_STRETCH_MINUTES = 25  # a "real focus" stretch lasts >= this long
PEAK_WINDOW_HOURS = 2       # peak protection windows are 2 hours long
TIMELINE_SLOT_MINUTES = 15  # day-timeline granularity (96 slots/day)

VALID_SCORES = (2, 1, 0, -1, -2)


def _as_date(value):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value


def _effective_score(event):
    score = event.get("score")
    return score if score in VALID_SCORES else 0


def chronotype_curves(day_from, day_to, db_path=None):
    """Per-weekday, per-hour productivity curves.

    Returns {weekday 0..6 (Mon..Sun): {hour 0..23:
    {"minutes": float, "pulse": float|None, "days": int,
    "focus_minutes": float}}}.
    "pulse" is RescueTime's exact weighted Pulse over that weekday-hour
    across the range; "days" counts days with any tracked time so thin
    cells are visible, not silently averaged; "focus_minutes" is the
    total time at scores +2/+1 (drives classification and peak windows).
    """
    day_from, day_to = _as_date(day_from), _as_date(day_to)
    grid = daily_hourly(day_from, day_to, db_path=db_path)
    curves = {w: {h: {"minutes": 0.0,
                      "seconds_by_level": {s: 0.0 for s in VALID_SCORES},
                      "days": 0}
                  for h in range(24)} for w in range(7)}
    for day_str, hours in grid.items():
        weekday = date.fromisoformat(day_str).weekday()
        for hour, bucket in hours.items():
            cell = curves[weekday][hour]
            cell["minutes"] += bucket["minutes"]
            if bucket["minutes"] > 0:
                cell["days"] += 1
            for score, seconds in bucket["seconds_by_level"].items():
                cell["seconds_by_level"][score] += seconds
    for weekday in curves:
        for hour in range(24):
            cell = curves[weekday][hour]
            levels = cell["seconds_by_level"]
            total = sum(levels.values())
            cell["pulse"] = (productivity_pulse(levels)
                             if total > 0 else None)
            cell["minutes"] = round(cell["minutes"], 1)
            cell["focus_minutes"] = round(
                sum(levels[s] for s in FOCUS_SCORES) / 60.0, 1)
            del cell["seconds_by_level"]
    return curves


def classify_chronotype(curves):
    """Morning person / night owl / balanced, from focus-minute shares.

    Shares are computed over focus minutes (scores +2/+1) in the morning
    window (05:00-12:00) vs the evening window (18:00-24:00). Documented
    rule: morning_share >= 0.55 -> "morning"; evening_share >= 0.55 ->
    "evening"; otherwise "balanced". peak_hour is the hour with the most
    focus minutes (None when there is no data).

    Returns {"type", "morning_share", "evening_share", "peak_hour"}.
    """
    morning = 0.0
    evening = 0.0
    total = 0.0
    focus_by_hour = {h: 0.0 for h in range(24)}
    for hours in curves.values():
        for hour, cell in hours.items():
            focus = cell["focus_minutes"]
            total += focus
            focus_by_hour[hour] += focus
            if MORNING_START <= hour < MORNING_END:
                morning += focus
            elif hour >= EVENING_START:
                evening += focus
    if total <= 0:
        return {"type": "balanced", "morning_share": 0.0,
                "evening_share": 0.0, "peak_hour": None}
    morning_share = morning / total
    evening_share = evening / total
    if morning_share >= CHRONOTYPE_THRESHOLD:
        kind = "morning"
    elif evening_share >= CHRONOTYPE_THRESHOLD:
        kind = "evening"
    else:
        kind = "balanced"
    peak_hour = max(focus_by_hour, key=lambda h: focus_by_hour[h])
    return {"type": kind,
            "morning_share": round(morning_share, 3),
            "evening_share": round(evening_share, 3),
            "peak_hour": peak_hour}


def weekday_peak_windows(curves, n=1, window_hours=PEAK_WINDOW_HOURS):
    """Best non-overlapping window(s) per weekday.

    Ranked by focus minutes (+2/+1) inside the window, summed across the
    sampled days. Returns {weekday: [{"start_hour", "end_hour",
    "focus_minutes"}]}. Weekdays with no tracked time are omitted.
    Windows never overlap within a weekday (greedy pick).
    """
    result = {}
    for weekday, hours in curves.items():
        focus_by_hour = {h: hours[h]["focus_minutes"] for h in range(24)}
        if sum(focus_by_hour.values()) <= 0:
            continue
        candidates = []
        for start in range(0, 24 - window_hours + 1):
            minutes = sum(focus_by_hour[h]
                          for h in range(start, start + window_hours))
            candidates.append((minutes, start))
        # Stable sort: ties keep the earliest start first (protect the
        # earlier window when two are equal).
        candidates.sort(key=lambda c: c[0], reverse=True)
        picked = []
        used = set()
        for minutes, start in candidates:
            span = set(range(start, start + window_hours))
            if span & used:
                continue
            picked.append({"start_hour": start,
                           "end_hour": start + window_hours,
                           "focus_minutes": round(minutes, 1)})
            used |= span
            if len(picked) >= n:
                break
        result[weekday] = picked
    return result


def focus_stretches(day_str, db_path=None):
    """Productive stretches for one day, in time order.

    A stretch is a run of consecutive events scoring > 0 where gaps
    between consecutive events are <= STRETCH_GAP_MINUTES. Returns
    [{"start": "HH:MM", "minutes": float}].
    """
    events = [e for e in store.get_day_activities(day_str, path=db_path)
              if float(e.get("duration") or 0) > 0]
    stretches = []
    current = None
    prev_end = None
    for event in events:
        try:
            start = _parse_local(event.get("ts"))
        except (ValueError, TypeError):
            continue
        duration = float(event.get("duration") or 0)
        end = start + timedelta(seconds=duration)
        if _effective_score(event) > 0:
            gap = ((start - prev_end).total_seconds() / 60.0
                   if prev_end is not None else 0)
            if current is not None and gap <= STRETCH_GAP_MINUTES:
                current["minutes"] += duration / 60.0
                current["end"] = end
            else:
                if current is not None:
                    stretches.append(current)
                current = {"start": start.strftime("%H:%M"),
                           "minutes": duration / 60.0, "end": end}
        else:
            if current is not None:
                stretches.append(current)
                current = None
        prev_end = end
    if current is not None:
        stretches.append(current)
    return [{"start": s["start"], "minutes": round(s["minutes"], 1)}
            for s in stretches]


def depth_summary(day_from, day_to, db_path=None):
    """Average longest productive stretch over a range.

    Days with no productive stretch are skipped, never counted as zero.
    Returns {"avg_longest": float|None, "best_day": str|None,
    "best_minutes": float, "days": int}.
    """
    day_from, day_to = _as_date(day_from), _as_date(day_to)
    longest = []
    day = day_from
    while day <= day_to:
        stretches = focus_stretches(day.isoformat(), db_path=db_path)
        if stretches:
            best = max(s["minutes"] for s in stretches)
            longest.append((day.isoformat(), best))
        day += timedelta(days=1)
    if not longest:
        return {"avg_longest": None, "best_day": None,
                "best_minutes": 0.0, "days": 0}
    best_day, best_minutes = max(longest, key=lambda t: t[1])
    return {"avg_longest": round(sum(m for _, m in longest) / len(longest), 1),
            "best_day": best_day, "best_minutes": best_minutes,
            "days": len(longest)}


def switch_rate(day_str, db_path=None):
    """App-switch count for one day (attention-fragmentation proxy).

    Counts transitions between different apps in timestamp order.
    Returns {"switches": int, "per_hour": float|None} where per_hour is
    switches per tracked hour (None when nothing was tracked).
    """
    events = [e for e in store.get_day_activities(day_str, path=db_path)
              if float(e.get("duration") or 0) > 0]
    switches = sum(
        1 for prev, cur in zip(events, events[1:])
        if (cur.get("app") or "") != (prev.get("app") or ""))
    tracked_hours = sum(float(e.get("duration") or 0)
                        for e in events) / 3600.0
    return {"switches": switches,
            "per_hour": round(switches / tracked_hours, 1)
            if tracked_hours > 0 else None}


def time_to_first_focus(day_str, db_path=None):
    """Minutes from the day's first tracked event to its first real
    focus stretch (>= FOCUS_STRETCH_MINUTES of score > 0, gaps merged
    per STRETCH_GAP_MINUTES). None when the day has no such stretch."""
    events = [e for e in store.get_day_activities(day_str, path=db_path)
              if float(e.get("duration") or 0) > 0]
    if not events:
        return None
    try:
        day_start = _parse_local(events[0].get("ts"))
    except (ValueError, TypeError):
        return None
    run_start = None
    run_minutes = 0.0
    prev_end = None
    for event in events:
        try:
            start = _parse_local(event.get("ts"))
        except (ValueError, TypeError):
            continue
        duration = float(event.get("duration") or 0)
        end = start + timedelta(seconds=duration)
        if _effective_score(event) > 0:
            gap = ((start - prev_end).total_seconds() / 60.0
                   if prev_end is not None else 0)
            if run_start is not None and gap <= STRETCH_GAP_MINUTES:
                run_minutes += duration / 60.0
            else:
                run_start = start
                run_minutes = duration / 60.0
            if run_minutes >= FOCUS_STRETCH_MINUTES:
                return round((run_start - day_start).total_seconds() / 60.0, 1)
        else:
            run_start = None
            run_minutes = 0.0
        prev_end = end
    return None


def median_time_to_focus(day_from, day_to, db_path=None):
    """Median time-to-first-focus over a range (days without one are
    skipped, never counted as zero). None when no day qualifies."""
    day_from, day_to = _as_date(day_from), _as_date(day_to)
    values = []
    day = day_from
    while day <= day_to:
        minutes = time_to_first_focus(day.isoformat(), db_path=db_path)
        if minutes is not None:
            values.append(minutes)
        day += timedelta(days=1)
    return round(median(values), 1) if values else None


def distraction_anatomy(day_from, day_to, db_path=None, n=5):
    """What eats focus time, and what pulls you in.

    Returns {"top": [{"app", "example", "minutes", "share"}],
             "entry_points": [{"app", "count"}]}.
    "top": minutes with score in (-1,-2) grouped by app; "example" is
    the longest event's title. "entry_points": for each distraction
    block start (score < 0 event whose predecessor scores >= 0), the
    predecessor's app, counted. A distraction block starting the day
    has no predecessor and is skipped, not attributed.
    """
    day_from, day_to = _as_date(day_from), _as_date(day_to)
    by_app = {}
    total_distracting = 0.0
    entries = {}
    day = day_from
    while day <= day_to:
        events = [e for e in store.get_day_activities(day.isoformat(),
                                                      path=db_path)
                  if float(e.get("duration") or 0) > 0]
        prev = None
        prev_score = None
        for event in events:
            score = _effective_score(event)
            minutes = float(event.get("duration") or 0) / 60.0
            if score in DISTRACTING_SCORES:
                total_distracting += minutes
                app = event.get("app") or "unknown"
                slot = by_app.setdefault(
                    app, {"minutes": 0.0, "example": "", "example_min": 0.0})
                slot["minutes"] += minutes
                if minutes > slot["example_min"]:
                    slot["example"] = event.get("title") or ""
                    slot["example_min"] = minutes
                if prev is not None and prev_score is not None \
                        and prev_score >= 0:
                    entry_app = prev.get("app") or "unknown"
                    entries[entry_app] = entries.get(entry_app, 0) + 1
            prev, prev_score = event, score
        day += timedelta(days=1)
    top = sorted(by_app.items(), key=lambda kv: kv[1]["minutes"],
                 reverse=True)[:n]
    return {
        "top": [{"app": app,
                 "example": info["example"],
                 "minutes": round(info["minutes"], 1),
                 "share": round(info["minutes"] / total_distracting * 100, 1)
                 if total_distracting > 0 else 0.0}
                for app, info in top],
        "entry_points": [{"app": app, "count": count}
                         for app, count in sorted(
                             entries.items(), key=lambda kv: kv[1],
                             reverse=True)[:3]],
    }


def week_trends(db_path=None, today=None):
    """This week (Mon..today) vs last week (Mon..Sun).

    Each week: {"hours", "avg_pulse", "focus_minutes",
    "switches_per_hour", "longest_stretch_avg"}. "deltas" holds
    this-minus-last differences (None when either side lacks data).
    Weeks with no tracked data produce None fields, never guesses.
    """
    today = _as_date(today) if today else date.today()
    this_start = today - timedelta(days=today.weekday())
    last_start = this_start - timedelta(days=7)
    last_end = this_start - timedelta(days=1)

    def _week(day_from, day_to):
        hours = 0.0
        pulses = []
        focus_minutes = 0.0
        switches = 0
        stretch_longs = []
        day = day_from
        while day <= day_to:
            day_str = day.isoformat()
            summary = store.get_day_summary(day_str, path=db_path)
            # Tracked time from the activities themselves (not day_stats,
            # which is only populated by the pipeline).
            seconds = sum(summary["seconds_by_level"].values())
            if seconds > 0:
                hours += seconds / 3600.0
                pulses.append(
                    productivity_pulse(summary["seconds_by_level"]))
                focus_minutes += sum(
                    summary["seconds_by_level"].get(s, 0)
                    for s in FOCUS_SCORES) / 60.0
                switches += switch_rate(day_str, db_path=db_path)["switches"]
                stretches = focus_stretches(day_str, db_path=db_path)
                if stretches:
                    stretch_longs.append(
                        max(s["minutes"] for s in stretches))
            day += timedelta(days=1)
        if hours <= 0:
            return {"hours": 0.0, "avg_pulse": None, "focus_minutes": 0.0,
                    "switches_per_hour": None, "longest_stretch_avg": None}
        return {"hours": round(hours, 1),
                "avg_pulse": round(sum(pulses) / len(pulses), 1),
                "focus_minutes": round(focus_minutes, 1),
                "switches_per_hour": round(switches / hours, 1),
                "longest_stretch_avg": round(sum(stretch_longs)
                                             / len(stretch_longs), 1)
                if stretch_longs else None}

    this_week = _week(this_start, today)
    last_week = _week(last_start, last_end)
    deltas = {}
    for key in ("hours", "avg_pulse", "focus_minutes",
                "switches_per_hour", "longest_stretch_avg"):
        a, b = this_week[key], last_week[key]
        deltas[key] = (round(a - b, 1)
                       if a is not None and b is not None else None)
    return {"this_week": this_week, "last_week": last_week,
            "deltas": deltas,
            "this_label": "%s - %s" % (this_start.isoformat(),
                                       today.isoformat()),
            "last_label": "%s - %s" % (last_start.isoformat(),
                                       last_end.isoformat())}


def day_timeline(day_str, db_path=None):
    """Interactive-timeline data for one day.

    Returns {"hours": [{"hour", "minutes", "pulse", "quarters":
    [score|None x4], "activities": [{"app", "title", "minutes",
    "score"}]}]}. Quarters are 15-minute slots; a quarter's score is the
    level with the most seconds (None when empty). Activities collapse
    the hour's events by (app, title).
    """
    grid = daily_hourly(day_str, day_str, db_path=db_path)
    hours_out = []
    for hour in range(24):
        bucket = grid[day_str][hour]
        minutes = bucket["minutes"]
        total = sum(bucket["seconds_by_level"].values())
        pulse = productivity_pulse(bucket["seconds_by_level"]) \
            if total > 0 else None
        hours_out.append({"hour": hour, "minutes": round(minutes, 1),
                          "pulse": pulse, "quarters": [None] * 4,
                          "activities": []})
    # Quarter scores: walk events, attribute each minute-chunk's score to
    # its 15-minute slot by majority seconds.
    quarter_seconds = {(h, q): {s: 0.0 for s in VALID_SCORES}
                       for h in range(24) for q in range(4)}
    by_hour_activity = {}
    for event in store.get_day_activities(day_str, path=db_path):
        try:
            start = _parse_local(event.get("ts"))
        except (ValueError, TypeError):
            continue
        duration = float(event.get("duration") or 0)
        if duration <= 0:
            continue
        score = _effective_score(event)
        cursor = start
        remaining = duration
        while remaining > 0:
            slot_end_min = ((cursor.minute // TIMELINE_SLOT_MINUTES) + 1) \
                * TIMELINE_SLOT_MINUTES
            slot_end = cursor.replace(minute=0, second=0,
                                      microsecond=0) + timedelta(
                                          minutes=slot_end_min)
            chunk = min(remaining, (slot_end - cursor).total_seconds())
            quarter_seconds[
                (cursor.hour, cursor.minute // TIMELINE_SLOT_MINUTES)
            ][score] += chunk
            cursor = slot_end
            remaining -= chunk
        key = (start.hour, event.get("app") or "unknown",
               event.get("title") or "")
        slot = by_hour_activity.setdefault(
            key, {"hour": start.hour, "app": event.get("app") or "unknown",
                  "title": event.get("title") or "", "minutes": 0.0,
                  "score": score})
        slot["minutes"] += duration / 60.0
    for (hour, quarter), levels in quarter_seconds.items():
        total = sum(levels.values())
        if total > 0:
            best = max(VALID_SCORES, key=lambda s: levels[s])
            hours_out[hour]["quarters"][quarter] = best
    for info in by_hour_activity.values():
        hours_out[info["hour"]]["activities"].append({
            "app": info["app"], "title": info["title"],
            "minutes": round(info["minutes"], 1), "score": info["score"]})
    for hour_info in hours_out:
        hour_info["activities"].sort(key=lambda a: a["minutes"],
                                     reverse=True)
    return {"hours": hours_out}


# ================================================== Phase 12 (v1.14.0) ---
# Deep Time Visual Analytics & Executive Reports.
#
# New documented constants (plain arithmetic, no hidden formulas):
FLOW_DEEP_WEIGHT = 40       # Flow Index points from deep-work ratio
FLOW_TTF_WEIGHT = 30        # Flow Index points from time-to-focus
FLOW_SWITCH_WEIGHT = 30     # Flow Index points from switch resilience
FLOW_TTF_FULL_MIN = 60      # TTF >= this scores 0 on the TTF component
FLOW_SWITCH_FULL = 12.0     # switches/hour >= this scores 0 on switches
SWITCH_RECOVERY_MIN = 1     # estimated recovery minutes per switch
BLOCK_RECOVERY_MIN = 10     # estimated recovery minutes per distraction
                            # block (a contiguous -1/-2 run)
FLOW_BASELINE_DAYS = 7      # week-over-week delta baseline window
FLOW_TREND_WEEKS = 3        # trailing baseline for week trends


def _wide_day_bounds(day_from_str, day_to_str):
    """Naive ISO bounds covering [day_from, day_to] plus one day each side.

    Stored timestamps may carry timezone offsets (ActivityWatch UTC "Z"
    stamps, or wall times like "+05:00"), so a plain lexicographic SQL
    comparison against naive local-day bounds silently drops events near
    midnight. Callers query wide, then keep only events whose LOCAL date
    (via _parse_local) falls inside [day_from, day_to].
    """
    d0 = date.fromisoformat(day_from_str) - timedelta(days=1)
    d1 = date.fromisoformat(day_to_str) + timedelta(days=1)
    return ("%sT00:00:00" % d0.isoformat(),
            "%sT23:59:59" % d1.isoformat())


def day_hourly_depth(day_str, db_path=None):
    """Per-hour score seconds for one day (Phase 12).

    ONE bounded range query via idx_activities_ts. Events crossing hour
    boundaries are split by seconds. Returns
    {hour 0..23: {2: s, 1: s, 0: s, -1: s, -2: s}}.
    """
    hours = {h: {s: 0.0 for s in VALID_SCORES} for h in range(24)}
    start_iso, end_iso = _wide_day_bounds(day_str, day_str)
    want = date.fromisoformat(day_str)
    for event in store.get_activities_range(start_iso, end_iso,
                                            path=db_path):
        try:
            start = _parse_local(event.get("ts"))
        except (ValueError, TypeError):
            continue
        if start.date() != want:
            continue
        duration = float(event.get("duration") or 0)
        if duration <= 0:
            continue
        score = _effective_score(event)
        cursor = start
        remaining = duration
        day_end = start.replace(hour=23, minute=59, second=59,
                                microsecond=0) + timedelta(seconds=1)
        while remaining > 0 and cursor < day_end:
            hour_end = cursor.replace(minute=0, second=0,
                                      microsecond=0) + timedelta(hours=1)
            chunk = min(remaining, (hour_end - cursor).total_seconds(),
                        (day_end - cursor).total_seconds())
            if chunk <= 0:
                break
            hours[cursor.hour][score] += chunk
            cursor = hour_end
            remaining -= chunk
    return hours


def day_ratio_buckets(day_str, db_path=None):
    """Deep/shallow/neutral/distraction minutes for one day (donut)."""
    hours = day_hourly_depth(day_str, db_path=db_path)
    totals = {2: 0.0, 1: 0.0, 0: 0.0, -1: 0.0, -2: 0.0}
    for h in hours.values():
        for s in VALID_SCORES:
            totals[s] += h[s]
    return {
        "deep": round(totals[2] / 60.0, 1),
        "shallow": round(totals[1] / 60.0, 1),
        "neutral": round(totals[0] / 60.0, 1),
        "distraction": round((totals[-1] + totals[-2]) / 60.0, 1),
    }


def _flow_label(score):
    if score >= 80:
        return "Optimal Flow"
    if score >= 60:
        return "Strong Focus"
    if score >= 40:
        return "Moderate Focus"
    if score >= 20:
        return "Fragmented"
    return "Scattered"


FLOW_MIN_ACTIVE_MINUTES = 15  # below this the Flow Index is undefined
                              # (returns None; UI shows "Insufficient Data")


def _flow_score_for_day(day_str, db_path=None):
    """Raw Flow Index 0-100 for one day (integer), or (None, None) when
    under FLOW_MIN_ACTIVE_MINUTES of tracked time.

    Zero-focus floor: a day with no +1/+2 minutes scores 0 even when
    switches and time-to-focus look good. Result is strictly clamped
    to integer 0-100.
    """
    hours = day_hourly_depth(day_str, db_path=db_path)
    focus_s = sum(h[2] + h[1] for h in hours.values())
    active_s = sum(sum(h.values()) for h in hours.values())
    if active_s < FLOW_MIN_ACTIVE_MINUTES * 60:
        return None, None
    if focus_s == 0:
        components = {"deep_ratio_pts": 0, "ttf_pts": 0,
                      "switch_pts": 0, "median_ttf_min": None,
                      "switches_per_hour": None}
        return 0, components

    deep_pts = FLOW_DEEP_WEIGHT * focus_s / active_s

    day = _as_date(day_str)
    ttf = median_time_to_focus(day - timedelta(days=CHRONOTYPE_DAYS - 1),
                               day, db_path=db_path)
    ttf_pts = FLOW_TTF_WEIGHT * max(0.0, 1 - (ttf or FLOW_TTF_FULL_MIN)
                                    / FLOW_TTF_FULL_MIN) if ttf else 0

    rate = switch_rate(day_str, db_path=db_path)["per_hour"]
    switch_pts = FLOW_SWITCH_WEIGHT * max(
        0.0, 1 - (rate or 0) / FLOW_SWITCH_FULL) if rate is not None else 0

    score = max(0, min(100, int(round(deep_pts + ttf_pts + switch_pts))))
    return score, {
        "deep_ratio_pts": round(deep_pts, 1),
        "ttf_pts": round(ttf_pts, 1),
        "switch_pts": round(switch_pts, 1),
        "median_ttf_min": ttf,
        "switches_per_hour": rate,
    }


def flow_index(day_str, db_path=None):
    """Daily 0-100 composite Flow Index (Phase 12).

    Returns {"score": int|None, "label": str, "components": {...}|None,
    "wow_delta": int|None, "note": str|None}. When the day has under
    FLOW_MIN_ACTIVE_MINUTES of tracked time, score is None and the UI
    must render the "Insufficient Data" note. wow_delta is today minus
    the mean of the previous FLOW_BASELINE_DAYS days (None when no
    qualifying baseline).
    """
    score, components = _flow_score_for_day(day_str, db_path=db_path)
    if score is None:
        return {"score": None, "label": "Insufficient Data",
                "components": None, "wow_delta": None,
                "note": "Track at least %d minutes for a Flow Index."
                        % FLOW_MIN_ACTIVE_MINUTES}
    day = _as_date(day_str)
    baseline = []
    for i in range(1, FLOW_BASELINE_DAYS + 1):
        d = (day - timedelta(days=i)).isoformat()
        s, _ = _flow_score_for_day(d, db_path=db_path)
        if s is not None:  # only days with enough tracked activity
            baseline.append(s)
    wow_delta = (score - int(round(sum(baseline) / len(baseline)))) \
        if baseline else None
    return {"score": score, "label": _flow_label(score),
            "components": components, "wow_delta": wow_delta,
            "note": None}


def _distraction_blocks(day_str, db_path=None):
    """Contiguous -1/-2 runs for one day: [(start, end, minutes)]."""
    start_iso, end_iso = _wide_day_bounds(day_str, day_str)
    want = date.fromisoformat(day_str)
    events = []
    for event in store.get_activities_range(start_iso, end_iso,
                                            path=db_path):
        try:
            start = _parse_local(event.get("ts"))
        except (ValueError, TypeError):
            continue
        if start.date() != want:
            continue
        duration = float(event.get("duration") or 0)
        if duration > 0:
            events.append((start, duration, _effective_score(event)))
    events.sort(key=lambda e: e[0])
    blocks = []
    cur_start = cur_end = None
    for start, duration, score in events:
        end = start + timedelta(seconds=duration)
        if score in (-1, -2):
            if cur_start is None:
                cur_start, cur_end = start, end
            elif start <= cur_end + timedelta(
                    minutes=STRETCH_GAP_MINUTES):
                cur_end = max(cur_end, end)
            else:
                blocks.append((cur_start, cur_end,
                               (cur_end - cur_start).total_seconds()
                               / 60.0))
                cur_start, cur_end = start, end
        elif cur_start is not None:
            blocks.append((cur_start, cur_end,
                           (cur_end - cur_start).total_seconds() / 60.0))
            cur_start = cur_end = None
    if cur_start is not None:
        blocks.append((cur_start, cur_end,
                       (cur_end - cur_start).total_seconds() / 60.0))
    return blocks


def recovery_cost(day_str, db_path=None):
    """Distraction recovery cost for one day (Phase 12).

    Returns {"recovery_minutes": int, "switches": int,
    "distraction_blocks": int, "top_friction": [{"app", "minutes"}]}.
    Plain documented arithmetic: switches x 1 min + blocks x 10 min.
    """
    switches = switch_rate(day_str, db_path=db_path)["switches"]
    blocks = _distraction_blocks(day_str, db_path=db_path)
    day = _as_date(day_str)
    anatomy = distraction_anatomy(day, day, db_path=db_path, n=5)
    recovery = switches * SWITCH_RECOVERY_MIN + len(blocks) \
        * BLOCK_RECOVERY_MIN
    return {
        "recovery_minutes": int(recovery),
        "switches": switches,
        "distraction_blocks": len(blocks),
        "distraction_minutes": round(sum(b[2] for b in blocks), 1),
        "top_friction": [{"app": t["app"], "minutes": t["minutes"]}
                         for t in anatomy["top"]],
    }


def coaching_cards(db_path=None, today=None):
    """Prescriptive chronotype coaching cards (Phase 12, max 3).

    Each card: {"title", "body"} in plain language.
    """
    from . import chronotype as chrono_mod
    today = _as_date(today) if today else date.today()
    day_from = today - timedelta(days=CHRONOTYPE_DAYS - 1)
    curves = chronotype_curves(day_from, today, db_path=db_path)
    peaks = weekday_peak_windows(curves, n=1)
    cards = []

    # Card 1: protect your peak (personal window or detected peak).
    enabled, pstart, pend = chrono_mod.get_window(db_path=db_path)
    if enabled:
        label = chrono_mod.window_label(db_path=db_path)
    elif peaks:
        wd, start_h, _ = peaks[0]
        label = "%02d:00-%02d:00" % (start_h,
                                     (start_h + PEAK_WINDOW_HOURS) % 24)
    else:
        label = None
    if label:
        # Share of focus minutes inside the personal peak window.
        focus_in = focus_out = 0.0
        for wd in curves:
            for hour in range(24):
                fm = curves[wd][hour]["focus_minutes"]
                if enabled and pstart.hour <= hour < pend.hour:
                    focus_in += fm
                else:
                    focus_out += fm
        share = (focus_in / (focus_in + focus_out)) \
            if (focus_in + focus_out) > 0 else 0
        cards.append({
            "title": "Protect your peak: %s" % label,
            "body": "About %d%% of your deep work happens in this "
                    "window. Move meetings and admin out of it; "
                    "guard it for your hardest work." % round(share * 100),
        })

    # Card 2: best meeting window (lowest-focus 2h weekday block).
    if any(curves[wd][h]["days"] for wd in range(5) for h in range(24)):
        hour_focus = {h: 0.0 for h in range(24)}
        for wd in range(5):  # weekdays only
            for h in range(24):
                hour_focus[h] += curves[wd][h]["focus_minutes"]
        best = min(range(9, 17),
                   key=lambda h: hour_focus[h] + hour_focus[h + 1])
        cards.append({
            "title": "Take meetings at %02d:00-%02d:00" % (best, best + 2),
            "body": "Your focus is naturally lowest here on workdays, "
                    "so meetings cost you the least deep-work time.",
        })

    # Card 3: top distraction guard.
    day = today.isoformat()
    rec = recovery_cost(day, db_path=db_path)
    if rec["top_friction"]:
        top = rec["top_friction"][0]
        cards.append({
            "title": "Tame %s" % top["app"],
            "body": "It pulled %.0f minutes of your attention today. "
                    "Add it to your focus-session block list to protect "
                    "tomorrow's peak." % top["minutes"],
        })
    if not cards:
        cards.append({
            "title": "Keep tracking",
            "body": "A few more days of tracked activity and your "
                    "personal coaching cards will appear here.",
        })
    return cards[:3]


def week_flow_trends(db_path=None, today=None):
    """This week vs trailing 3-week Flow Index baseline (Phase 12).

    Returns {"this_week": {"label", "mean"}, "baseline_mean",
    "delta", "daily": [{"day", "score"}]}. Weeks run Mon-Sun.
    """
    today = _as_date(today) if today else date.today()
    week_start = today - timedelta(days=today.weekday())
    daily = []
    day = week_start
    while day <= today:
        score, _ = _flow_score_for_day(day.isoformat(), db_path=db_path)
        daily.append({"day": day.isoformat(),
                      "score": score if score is not None else 0,
                      "has_data": score is not None})
        day += timedelta(days=1)
    scored = [d["score"] for d in daily if d["has_data"]]
    this_mean = round(sum(scored) / len(scored), 1) if scored else 0
    baseline_scores = []
    for w in range(1, FLOW_TREND_WEEKS + 1):
        ws = week_start - timedelta(weeks=w)
        for i in range(7):
            d = (ws + timedelta(days=i)).isoformat()
            s, _ = _flow_score_for_day(d, db_path=db_path)
            if s is not None:
                baseline_scores.append(s)
    baseline_mean = round(sum(baseline_scores) / len(baseline_scores),
                          1) if baseline_scores else None
    delta = round(this_mean - baseline_mean, 1) \
        if baseline_mean is not None else None
    return {"this_week": {"label": "This week", "mean": this_mean},
            "baseline_mean": baseline_mean, "delta": delta,
            "daily": daily}


def switch_heatmap_7x24(day_from, day_to, db_path=None):
    """7x24 context-switch heatmap grid (Phase 12).

    Returns {weekday 0..6: {hour 0..23: switches}}. Switches are
    app-to-app transitions in timestamp order, counted in the hour of
    the later event. Bounded range query per day.
    """
    day_from, day_to = _as_date(day_from), _as_date(day_to)
    grid = {wd: {h: 0 for h in range(24)} for wd in range(7)}
    day = day_from
    while day <= day_to:
        ds = day.isoformat()
        start_iso, end_iso = _wide_day_bounds(ds, ds)
        events = []
        for event in store.get_activities_range(start_iso, end_iso,
                                                path=db_path):
            try:
                start = _parse_local(event.get("ts"))
            except (ValueError, TypeError):
                continue
            if start.date() != day:
                continue
            if float(event.get("duration") or 0) > 0:
                events.append((start, event.get("app") or ""))
        events.sort(key=lambda e: e[0])
        for prev, cur in zip(events, events[1:]):
            if cur[1] != prev[1]:
                grid[day.weekday()][cur[0].hour] += 1
        day += timedelta(days=1)
    return grid
