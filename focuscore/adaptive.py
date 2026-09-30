"""Adaptive session engine (Phase 8).

Pure, deterministic functions that learn from the user's own history.
No ML, no network — documented arithmetic over local SQLite data,
in the same spirit as ``intelligence.py``. Every suggestion returns a
plain-English reason so the user can see *why*.

Qwen audit constraints (Revision 2) implemented here:
- R4 intent-based flow: qualifying stretches need 10 min of continuous
  productive focus; switching among productive work tools does not
  break a stretch; 120 s AFK tolerated; suggestions are advisory.
- R5 proportional fatigue recovery: 5 min base break, 15 min every
  4th work cycle, +5 min when the switch rate exceeds 1.5x baseline.
"""

from datetime import datetime, timedelta

import logging

from . import intelligence
from . import store
from .scoring import productivity_pulse

logger = logging.getLogger(__name__)

# --- named thresholds -------------------------------------------------------
HISTORY_DAYS = 14       # suggestion window: last 2 weeks
WARM_MIN_DAYS = 7       # fewer focused days than this -> cold start
WORK_MIN_MINUTES = 15   # suggestion clamps
WORK_MAX_MINUTES = 120
PEAK_NUDGE_MINUTES = 10  # +/- nudge inside/outside today's peak window
POMODORO_COLD_START = 25
FLOW_COLD_START = 50

BREAK_BASE_MINUTES = 5
BREAK_LONG_MINUTES = 15
BREAK_LONG_EVERY = 4        # every 4th completed work cycle
BREAK_FATIGUE_BONUS = 5
BREAK_MAX_MINUTES = 30
SWITCH_FATIGUE_RATIO = 1.5  # recent switch rate vs baseline

FLOW_MIN_STRETCH_MINUTES = 10  # R4: a "real" stretch lasts >= this
FLOW_AFK_TOLERANCE_SECONDS = 120  # R4: gaps <= this merge into a stretch
PRODUCTIVE_SCORES = (1, 2)

FATIGUE_MIN_CYCLES = 3  # tired when >= this many work cycles done ...
FATIGUE_MAX_PULSE = 40  # ... and today's Pulse is below this


def _today():
    return datetime.now().date().isoformat()


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _clamp_round(value, low, high):
    """Clamp to [low, high] and round to the nearest 5 minutes."""
    return int(5 * round(min(high, max(low, value)) / 5.0))


def suggest_work_minutes(db_path=None, now=None, mode="pomodoro"):
    """Suggest a work length in minutes; returns (minutes, reason).

    Warm: median of each day's longest productive stretch over the
    last HISTORY_DAYS, nudged for time of day. Cold: fixed defaults.
    """
    now = now or datetime.now()
    day_to = now.date()
    day_from = day_to - timedelta(days=HISTORY_DAYS - 1)
    depth = intelligence.depth_summary(day_from.isoformat(),
                                       day_to.isoformat(), db_path=db_path)
    if not depth["days"] or depth["days"] < WARM_MIN_DAYS \
            or not depth["avg_longest"]:
        default = (POMODORO_COLD_START if mode == "pomodoro"
                   else FLOW_COLD_START)
        return (default, "Not enough history yet — starting with the "
                         "classic %d minutes." % default)
    minutes = depth["avg_longest"]
    reason_bits = ["Your best focus stretches lately average about "
                   "%d minutes." % round(minutes)]
    # Time-of-day nudge: longer inside today's peak window.
    try:
        curves = intelligence.chronotype_curves(
            (day_to - timedelta(days=28)).isoformat(), day_to.isoformat(),
            db_path=db_path)
        peaks = intelligence.weekday_peak_windows(curves)
        weekday = now.weekday()
        in_peak = any(p["weekday"] == weekday
                      and p["start_hour"] <= now.hour < p["end_hour"]
                      for p in peaks)
        if in_peak:
            minutes += PEAK_NUDGE_MINUTES
            reason_bits.append("You are in a peak window, so a little "
                               "longer.")
        else:
            minutes -= PEAK_NUDGE_MINUTES
            reason_bits.append("This is not a peak hour for you, so a "
                               "little shorter.")
    except Exception:
        # The nudge is best-effort and the base suggestion stands --
        # but a broken peak calculation should not be invisible.
        logger.exception("peak-window nudge failed; using the base "
                         "suggestion")
    return (_clamp_round(minutes, WORK_MIN_MINUTES, WORK_MAX_MINUTES),
            " ".join(reason_bits))


def suggest_flow_target(db_path=None):
    """Suggest a flowtime soft target from past natural lengths."""
    lengths = past_flow_lengths(db_path=db_path)
    if len(lengths) < 3:
        return (FLOW_COLD_START, "Not enough flowtime history yet — "
                                 "aiming for a gentle %d minutes."
                % FLOW_COLD_START)
    med = _median(lengths)
    return (_clamp_round(med, WORK_MIN_MINUTES, WORK_MAX_MINUTES),
            "Your past flow sessions naturally lasted about %d minutes."
            % round(med))


def suggest_break_minutes(work_cycles_done, recent_switch_rate=None,
                          baseline_switch_rate=None):
    """Suggest a break length; returns (minutes, reason). R5."""
    minutes = BREAK_BASE_MINUTES
    reason = "A short breather."
    if work_cycles_done > 0 and \
            work_cycles_done % BREAK_LONG_EVERY == 0:
        minutes = BREAK_LONG_MINUTES
        reason = "You finished %d work blocks — take a proper break." \
            % work_cycles_done
    elif (recent_switch_rate is not None
            and baseline_switch_rate
            and recent_switch_rate > SWITCH_FATIGUE_RATIO
            * baseline_switch_rate):
        minutes += BREAK_FATIGUE_BONUS
        reason = ("Your app-switching is up lately — "
                  "take a longer breather.")
    return (min(minutes, BREAK_MAX_MINUTES), reason)


def fatigue_check(db_path=None, today=None):
    """True when the user looks spent: returns (tired, message).

    Advisory only — it never ends anything by itself (R5).
    """
    day = today or _today()
    try:
        summary = store.get_day_summary(day, path=db_path)
    except Exception:
        return (False, "")
    levels = summary.get("seconds_by_level") or {}
    if sum(levels.values()) <= 0:
        return (False, "")
    pulse = productivity_pulse(levels)
    try:
        cycles = store.work_cycles_completed_today(day, path=db_path)
    except Exception:
        cycles = 0
    if cycles >= FATIGUE_MIN_CYCLES and pulse < FATIGUE_MAX_PULSE:
        return (True, "You have done %d focused blocks today and your "
                      "energy looks low (Pulse %.0f). Consider calling "
                      "it a day — tomorrow will thank you."
                % (cycles, pulse))
    return (False, "")


def flow_qualifying_minutes(session_id, db_path=None):
    """Intent-based focused minutes for a flowtime session (R4).

    Productive (+1/+2) stretches; gaps of <= 120 s AFK tolerated;
    switching among productive work tools does not break a stretch;
    only stretches >= 10 min count. Returns rounded minutes.
    """
    from . import focus as focus_mod
    session = store.get_session(session_id, path=db_path)
    if not session:
        return 0.0
    events = focus_mod.session_events(session, db_path=db_path)
    # events: [{start: datetime, end: datetime, score: int}] sorted.
    stretch_start = None
    stretch_end = None
    qualifying = 0.0

    def _close_stretch():
        nonlocal qualifying, stretch_start, stretch_end
        if stretch_start is not None:
            minutes = (stretch_end - stretch_start).total_seconds() / 60.0
            if minutes >= FLOW_MIN_STRETCH_MINUTES:
                qualifying += minutes
        stretch_start = stretch_end = None

    for ev in events:
        productive = ev["score"] in PRODUCTIVE_SCORES
        if productive:
            if stretch_start is None:
                stretch_start = stretch_end = ev["start"]
            elif (ev["start"] - stretch_end).total_seconds() \
                    <= FLOW_AFK_TOLERANCE_SECONDS:
                pass  # tolerated gap: stretch continues
            else:
                _close_stretch()
                stretch_start = stretch_end = ev["start"]
            stretch_end = max(stretch_end, ev["end"])
        else:
            gap_ok = (stretch_start is not None
                      and (ev["start"] - stretch_end).total_seconds()
                      <= FLOW_AFK_TOLERANCE_SECONDS)
            if not gap_ok:
                _close_stretch()
            # else: brief non-productive blip inside the tolerance —
            # the stretch survives, but the clock does not advance.
    _close_stretch()
    return round(qualifying, 1)


def past_flow_lengths(db_path=None, limit=20):
    """Natural lengths of past completed flowtime sessions."""
    lengths = []
    try:
        for s in store.list_sessions(limit=200, path=db_path):
            if s.get("session_type") != "flowtime":
                continue
            if s.get("status") != "completed":
                continue
            q = flow_qualifying_minutes(s["id"], db_path=db_path)
            if q > 0:
                lengths.append(q)
            if len(lengths) >= limit:
                break
    except Exception:
        logger.exception("past flow lengths scan failed; history "
                         "may be incomplete")
    return lengths
