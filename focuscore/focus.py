"""Focus sessions (RescueTime-style FocusTime).

A focus session is a timed work block: the user sets a label and a
duration, sees a countdown, and gets a summary at the end. While a
session is active, the enforcer (blocker.py) blocks distracting
activity -- scores -1 (Personal) and -2 (Distracting) on "strict",
only -2 on "lenient".

Only one session may be active at a time.

Usage:
    python -m focuscore.focus start --label "Deep work" --minutes 50
    python -m focuscore.focus end
    python -m focuscore.focus abort
    python -m focuscore.focus status
"""

import argparse
import logging
import time
from datetime import datetime, timedelta

from . import store
from .scoring import productivity_pulse

# Roadmap 0.3: log failures that used to be swallowed silently.
logger = logging.getLogger(__name__)

BLOCK_LEVELS = {
    "strict": frozenset({-1, -2}),
    "lenient": frozenset({-2}),
}

MAX_MINUTES = 480  # sanity cap: one session is at most 8 hours

SESSION_TYPES = ("classic", "flowtime", "pomodoro")

# Hybrid resilient timer (R1): a wall/monotonic gap bigger than this
# means the PC slept or hibernated.
SUSPEND_GAP_SECONDS = 60.0
# R2: a break left running snaps back after 30 minutes, no matter what.
BREAK_HARD_CAP_MINUTES = 30.0
# R2: after a sleep resume, enforcement stays paused this long.
RESUME_GRACE_SECONDS = 60.0


def _now_iso(now=None):
    return (now or datetime.now()).isoformat(timespec="seconds")


def _to_naive(dt):
    """Normalize a datetime (or ISO string) to naive local time.

    Session timestamps are stored naive (local), but ActivityWatch event
    timestamps arrive offset-aware (UTC, e.g. "+00:00"). Comparing a naive
    and an aware datetime raises TypeError, so everything is normalized
    to naive local time before any comparison.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


ENFORCEMENT_MODES = ("strict", "hardcore")


def start_session(label, duration_minutes, block_level="strict",
                  db_path=None, now=None, enforcement_mode="strict",
                  session_type="classic", target_cycles=4,
                  suggested_minutes=None):
    """Start a focus session; returns the session dict or {"error": ...}.

    Starting fails when another session is already active -- finish or
    abort it first. enforcement_mode: "strict" (notify + overlay) or
    "hardcore" (minimize + 30 s locked overlay; explicit opt-in).
    session_type: "classic" (fixed timer), "flowtime" (open-ended, the
    minutes are a soft target), "pomodoro" (work/break cycles; the
    minutes are the work-cycle length and the first cycle starts now).
    """
    now = now or datetime.now()
    label = (label or "").strip()
    if not label:
        return {"error": "Please give the session a label."}
    try:
        minutes = float(duration_minutes)
    except (TypeError, ValueError):
        return {"error": "Duration must be a number of minutes."}
    if not 0 < minutes <= MAX_MINUTES:
        return {"error": "Duration must be between 1 and %d minutes."
                % MAX_MINUTES}
    if block_level not in BLOCK_LEVELS:
        return {"error": "block_level must be one of %s."
                % sorted(BLOCK_LEVELS)}
    if enforcement_mode not in ENFORCEMENT_MODES:
        return {"error": "enforcement_mode must be one of %s."
                % list(ENFORCEMENT_MODES)}
    if session_type not in SESSION_TYPES:
        return {"error": "session_type must be one of %s."
                % list(SESSION_TYPES)}
    try:
        target_cycles = int(target_cycles)
    except (TypeError, ValueError):
        return {"error": "target_cycles must be a whole number."}
    if not 1 <= target_cycles <= 24:
        return {"error": "target_cycles must be between 1 and 24."}

    active = store.get_active_session(path=db_path)
    if active:
        return {"error": "A focus session is already active: %r. "
                "End or abort it first." % active["label"]}

    planned_end = now + timedelta(minutes=minutes)
    session_id = store.create_session(
        label, minutes, _now_iso(now), _now_iso(planned_end), block_level,
        enforcement_mode=enforcement_mode, path=db_path,
        session_type=session_type, suggested_minutes=suggested_minutes,
        target_cycles=target_cycles)
    if session_type == "pomodoro":
        # The first work cycle starts immediately.
        store.start_cycle(session_id, "work", minutes, _now_iso(now),
                          time.monotonic(), path=db_path)
    return store.get_session(session_id, path=db_path)


def end_session(db_path=None, now=None):
    """Finish the active session; returns {"session", "summary"} or error."""
    from . import cues as cues_mod
    now = now or datetime.now()
    active = store.get_active_session(path=db_path)
    if not active:
        return {"error": "No active focus session to end."}
    _close_active_cycle(active["id"], "aborted", now, db_path)
    store.end_session(active["id"], "completed", _now_iso(now), path=db_path)
    session = store.get_session(active["id"], path=db_path)
    cues_mod.play_cue("session_end", enabled=cue_enabled(db_path))
    # Phase 11: award XP + badges once per completed session. Failure
    # here must never break session completion.
    xp = {}
    try:
        from . import gamification as gami_mod
        xp = gami_mod.award_session_xp(session["id"], db_path=db_path)
    except Exception:
        # Roadmap 0.3: lost XP used to fail with no trace.
        logger.exception("award_session_xp failed for session %s",
                         session["id"])
        xp = {}
    return {"session": session,
            "summary": session_summary(session["id"], db_path=db_path,
                                       now=now),
            "xp": xp}


def abort_session(db_path=None, now=None):
    """Give up on the active session; returns the session dict or error."""
    now = now or datetime.now()
    active = store.get_active_session(path=db_path)
    if not active:
        return {"error": "No active focus session to abort."}
    _close_active_cycle(active["id"], "aborted", now, db_path)
    store.end_session(active["id"], "aborted", _now_iso(now), path=db_path)
    return store.get_session(active["id"], path=db_path)


def get_active_session(db_path=None):
    return store.get_active_session(path=db_path)


def list_sessions(limit=20, db_path=None):
    return store.list_sessions(limit=limit, path=db_path)


def _clip_overlap(start_a, end_a, start_b, end_b):
    """Seconds of overlap between two [start, end) datetime ranges."""
    latest_start = max(start_a, start_b)
    earliest_end = min(end_a, end_b)
    return max(0.0, (earliest_end - latest_start).total_seconds())


def _session_window_activities(session, db_path, now):
    """Stored activities overlapping the session window, clipped to it.

    Returns a list of {"duration": clipped seconds, "score": int}.
    """
    start = _to_naive(session["started_at"])
    end_iso = session["ended_at"] or _now_iso(now)
    end = _to_naive(end_iso)

    # A session can in theory span midnight, so pull every day it touches.
    days = set()
    cursor = start
    while cursor.date() <= end.date():
        days.add(cursor.date().isoformat())
        cursor += timedelta(days=1)

    clipped = []
    for day in sorted(days):
        for event in store.get_day_activities(day, path=db_path):
            ts_raw = event.get("ts")
            if not ts_raw:
                continue
            try:
                ev_start = _to_naive(ts_raw)
            except ValueError:
                continue
            ev_end = ev_start + timedelta(
                seconds=float(event.get("duration") or 0))
            overlap = _clip_overlap(ev_start, ev_end, start, end)
            if overlap > 0:
                score = event.get("score")
                clipped.append({
                    "duration": overlap,
                    "score": score if score in (2, 1, 0, -1, -2) else 0,
                })
    return clipped


def session_summary(session_id, db_path=None, now=None):
    """Summarize one session.

    Returns {"focus_minutes" (scores +2/+1 inside the window),
             "neutral_minutes" (score 0),
             "distracting_minutes" (scores -1/-2),
             "blocks_count", "pulse" (Pulse over the session window),
             "planned_minutes", "actual_minutes"}.
    """
    now = now or datetime.now()
    session = store.get_session(session_id, path=db_path)
    if not session:
        return {"error": "Unknown session id %r." % session_id}

    seconds_by_level = {2: 0.0, 1: 0.0, 0: 0.0, -1: 0.0, -2: 0.0}
    for event in _session_window_activities(session, db_path, now):
        seconds_by_level[event["score"]] += event["duration"]

    start = _to_naive(session["started_at"])
    end = _to_naive(session["ended_at"] or _now_iso(now))
    return {
        "session_id": session_id,
        "label": session["label"],
        "status": session["status"],
        "focus_minutes": (seconds_by_level[2] + seconds_by_level[1]) / 60.0,
        "neutral_minutes": seconds_by_level[0] / 60.0,
        "distracting_minutes":
            (seconds_by_level[-1] + seconds_by_level[-2]) / 60.0,
        "blocks_count": store.count_blocks(session_id, path=db_path),
        "pulse": productivity_pulse(seconds_by_level),
        "planned_minutes": float(session["planned_minutes"]),
        "actual_minutes": (end - start).total_seconds() / 60.0,
    }


def current_streak(db_path=None, today=None):
    """Consecutive days up to today with at least one completed session."""
    today = today or (datetime.now().date())
    done_days = store.session_days_with_completion(path=db_path)
    streak = 0
    day = today
    while day.isoformat() in done_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def remaining_seconds(session, now=None):
    """Seconds left in an active session (0 if the time is up)."""
    now = _to_naive(now or datetime.now())
    planned_end = _to_naive(session["planned_end_at"])
    return max(0.0, (planned_end - now).total_seconds())


# ------------------------------------------------- Phase 8 cycle engine ---

def cue_enabled(db_path=None):
    """User's audio-cues toggle (R6); defaults to on."""
    try:
        return store.get_setting("audio_cues", path=db_path) != "0"
    except Exception:
        return True


def session_events(session, db_path=None, now=None):
    """Activity events clipped to the session window, sorted by start.

    Returns [{start: datetime, end: datetime, score: int}]. Used by the
    intent-based flow learning (R4).
    """
    start = _to_naive(session["started_at"])
    end_iso = session["ended_at"] or _now_iso(now)
    end = _to_naive(end_iso)
    days = set()
    cursor = start
    while cursor.date() <= end.date():
        days.add(cursor.date().isoformat())
        cursor += timedelta(days=1)
    events = []
    for day in sorted(days):
        for event in store.get_day_activities(day, path=db_path):
            ts_raw = event.get("ts")
            if not ts_raw:
                continue
            try:
                ev_start = _to_naive(ts_raw)
            except ValueError:
                continue
            ev_end = ev_start + timedelta(
                seconds=float(event.get("duration") or 0))
            overlap = _clip_overlap(ev_start, ev_end, start, end)
            if overlap > 0:
                clip_start = max(ev_start, start)
                score = event.get("score")
                events.append({
                    "start": clip_start,
                    "end": clip_start + timedelta(seconds=overlap),
                    "score": score if score in (2, 1, 0, -1, -2) else 0,
                })
    events.sort(key=lambda e: e["start"])
    return events


def _cycle_focused(cycle, now, now_mono, db_path):
    """Focused seconds for a cycle + this tick's suspend gap (R1).

    Per-tick accounting: compares this tick against the *previous* tick
    (tick deltas), not against the cycle start:

        gap = wall_delta - mono_delta

    A gap > SUSPEND_GAP_SECONDS means the PC slept between ticks. The
    previous tick is re-based every settle, so a detected suspend does
    not stick to later ticks. Focused time is monotonic-based, so
    sleep is excluded by construction and can never inflate or
    complete a cycle. On a monotonic clock reset (daemon restart) the
    cycle keeps its credited offset, adds nothing for the jump, and
    re-baselines — conservative, never over-credits.

    Returns (focused_seconds, gap_seconds).
    """
    offset = cycle.get("elapsed_offset_seconds") or 0.0
    prev_wall = _to_naive(cycle.get("last_tick_wall") or cycle["started_at"])
    prev_mono = cycle.get("last_tick_mono")
    if prev_mono is None:
        prev_mono = cycle.get("started_monotonic")

    if prev_mono is None:
        # Wall-clock-only cycle (legacy): best effort.
        tick_focused = max(0.0, (now - prev_wall).total_seconds())
        gap = 0.0
        mono_for_store = None
    elif now_mono < prev_mono:
        # Clock reset: keep credited offset, add nothing, re-baseline.
        tick_focused = 0.0
        gap = 0.0
        mono_for_store = now_mono
    else:
        mono_delta = now_mono - prev_mono
        wall_delta = (now - prev_wall).total_seconds()
        gap = wall_delta - mono_delta
        tick_focused = max(0.0, mono_delta)
        mono_for_store = now_mono

    focused = offset + tick_focused
    store.update_cycle_ticks(cycle["id"],
                             now.isoformat(timespec="seconds"),
                             mono_for_store, focused, path=db_path)
    return focused, gap


def cycle_remaining_seconds(cycle, db_path=None, now=None, now_mono=None):
    """Seconds left in a cycle (R2 hard cap applies to breaks)."""
    now = now or datetime.now()
    now_mono = time.monotonic() if now_mono is None else now_mono
    focused, _gap = _cycle_focused(cycle, now, now_mono, db_path)
    planned = float(cycle["planned_minutes"]) * 60.0
    if cycle["kind"] == "break":
        planned = min(planned, BREAK_HARD_CAP_MINUTES * 60.0)
    return max(0.0, planned - focused)


def _switch_baseline(db_path, today):
    """Mean daily app-switch rate over the last 14 days (R5)."""
    from . import intelligence
    rates = []
    for back in range(1, 15):
        day = (today - timedelta(days=back)).isoformat()
        try:
            rates.append(intelligence.switch_rate(day, db_path=db_path))
        except Exception:
            logger.exception("switch-rate baseline failed for %s", day)
    return sum(rates) / len(rates) if rates else 0.0


def _start_work_cycle(session, db_path, now, now_mono, planned_minutes):
    return store.start_cycle(session["id"], "work", planned_minutes,
                             now.isoformat(timespec="seconds"), now_mono,
                             path=db_path)


def _start_break_cycle(session, db_path, now, now_mono, cycles_done):
    from . import adaptive
    from . import intelligence
    today = now.date()
    try:
        recent = intelligence.switch_rate(today.isoformat(),
                                          db_path=db_path)
        baseline = _switch_baseline(db_path, today)
    except Exception:
        recent, baseline = None, None
    minutes, _reason = adaptive.suggest_break_minutes(
        cycles_done, recent_switch_rate=recent,
        baseline_switch_rate=baseline or None)
    return store.start_cycle(session["id"], "break", minutes,
                             now.isoformat(timespec="seconds"), now_mono,
                             path=db_path)


def settle_session(db_path=None, now=None, now_mono=None,
                   cues=None):
    """Advance pomodoro cycles with the hybrid resilient timer (R1).

    Idempotent — safe to call from the dashboard, the shield worker,
    or the CLI on every tick. Completes due cycles, fires audio cues
    (R6), auto-starts breaks, and enforces the 30-minute break
    snap-back (R2). A sleep-inflated cycle is NEVER marked completed:
    completion needs focused (monotonic) time >= planned.

    Returns {"events": [...], "suspend_detected": bool,
             "session": session|None}.
    """
    from . import cues as cues_mod
    now = now or datetime.now()
    now_mono = time.monotonic() if now_mono is None else now_mono
    cues_on = cue_enabled(db_path) if cues is None else cues
    session = store.get_active_session(path=db_path)
    if not session or session.get("session_type") != "pomodoro":
        return {"events": [], "suspend_detected": False,
                "session": session}
    events = []
    suspend = False
    for _ in range(4):  # at most a couple of transitions per settle
        cycle = store.get_active_cycle(session["id"], path=db_path)
        if cycle is None:
            if session["completed_cycles"] >= session["target_cycles"]:
                break  # target reached: advisory, wait for the user
            from . import adaptive
            minutes, _reason = adaptive.suggest_work_minutes(
                db_path, now, mode="pomodoro")
            _start_work_cycle(session, db_path, now, now_mono, minutes)
            events.append({"type": "work_started",
                           "planned_minutes": minutes})
            break
        focused, gap = _cycle_focused(cycle, now, now_mono, db_path)
        if gap > SUSPEND_GAP_SECONDS:
            suspend = True
        planned = float(cycle["planned_minutes"]) * 60.0
        if cycle["kind"] == "break":
            planned = min(planned, BREAK_HARD_CAP_MINUTES * 60.0)
        if focused < planned:
            break  # nothing due
        store.end_cycle(cycle["id"], "completed",
                        now.isoformat(timespec="seconds"), path=db_path)
        if cycle["kind"] == "work":
            done = store.bump_completed_cycles(session["id"],
                                               path=db_path)
            session["completed_cycles"] = done
            events.append({"type": "work_completed",
                           "cycles_done": done})
            cues_mod.play_cue("cycle_end", enabled=cues_on)
            _start_break_cycle(session, db_path, now, now_mono, done)
            events.append({"type": "break_started"})
            break  # fresh break cannot be due yet
        events.append({"type": "break_completed"})
        cues_mod.play_cue("break_end", enabled=cues_on)
        # loop: start the next work cycle (or stop at the target)
    return {"events": events, "suspend_detected": suspend,
            "session": session}


def start_break_now(db_path=None, now=None, now_mono=None):
    """End the work cycle early (counts as completed) and start a break."""
    from . import cues as cues_mod
    now = now or datetime.now()
    now_mono = time.monotonic() if now_mono is None else now_mono
    session = store.get_active_session(path=db_path)
    if not session or session.get("session_type") != "pomodoro":
        return {"error": "No active pomodoro session."}
    cycle = store.get_active_cycle(session["id"], path=db_path)
    if not cycle or cycle["kind"] != "work":
        return {"error": "No active work cycle to pause."}
    store.end_cycle(cycle["id"], "completed",
                    now.isoformat(timespec="seconds"), path=db_path)
    done = store.bump_completed_cycles(session["id"], path=db_path)
    cues_mod.play_cue("cycle_end", enabled=cue_enabled(db_path))
    _start_break_cycle(session, db_path, now, now_mono, done)
    return {"ok": True, "cycles_done": done}


def end_break_now(db_path=None, now=None, now_mono=None, skipped=False):
    """Finish the break early and start the next work cycle."""
    from . import cues as cues_mod
    now = now or datetime.now()
    now_mono = time.monotonic() if now_mono is None else now_mono
    session = store.get_active_session(path=db_path)
    if not session or session.get("session_type") != "pomodoro":
        return {"error": "No active pomodoro session."}
    cycle = store.get_active_cycle(session["id"], path=db_path)
    if not cycle or cycle["kind"] != "break":
        return {"error": "No active break to end."}
    store.end_cycle(cycle["id"], "skipped" if skipped else "completed",
                    now.isoformat(timespec="seconds"), path=db_path)
    cues_mod.play_cue("break_end", enabled=cue_enabled(db_path))
    if session["completed_cycles"] >= session["target_cycles"]:
        return {"ok": True, "target_reached": True}
    from . import adaptive
    minutes, _reason = adaptive.suggest_work_minutes(
        db_path, now, mode="pomodoro")
    _start_work_cycle(session, db_path, now, now_mono, minutes)
    return {"ok": True, "work_minutes": minutes}


def _close_active_cycle(session_id, status, now, db_path):
    try:
        cycle = store.get_active_cycle(session_id, path=db_path)
        if cycle:
            store.end_cycle(cycle["id"], status,
                            now.isoformat(timespec="seconds"),
                            path=db_path)
    except Exception:
        # Roadmap 0.3: a stuck-open cycle used to fail with no trace.
        logger.exception("close_active_cycle failed for session %s",
                         session_id)
        pass


def _fmt_hms(total_seconds):
    total = int(total_seconds)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


def depth_state(session_id, db_path=None, now=None):
    """Real-time depth gauge for an active session (Phase 11).

    Returns {'state': 'flow'|'deep'|'surface',
             'dominant_score': int, 'switches_15m': int,
             'uninterrupted_min': float}.

    Inputs (trailing 15-minute window of tracked activities):
    - dominant score: score bucket (+2/+1/0/-1/-2) with most seconds.
    - switches: number of distinct apps in the window.
    - uninterrupted: minutes since session start (or last break end)
      with no -1/-2 app activity.

    Rules:
    - flow:    dominant +2, 0 switches, uninterrupted >= 15.
    - deep:    dominant +1/+2, switches <= 2.
    - surface: everything else (warm-up, high switching).

    Council remediation: single bounded range query via
    idx_activities_ts -- no full-day scans, no unbounded LIMIT.
    """
    now = now or datetime.now()
    session = store.get_session(session_id, path=db_path)
    if not session:
        return {"state": "surface", "dominant_score": 0,
                "switches_15m": 0, "uninterrupted_min": 0.0}

    start = _to_naive(session["started_at"])
    window_start = now - timedelta(minutes=15)

    seconds_by_score = {2: 0.0, 1: 0.0, 0: 0.0, -1: 0.0, -2: 0.0}
    apps = set()
    last_distraction_end = None

    for event in store.get_activities_range(
            window_start.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"), path=db_path):
        try:
            ev_start = _to_naive(event["ts"])
        except (ValueError, TypeError):
            continue
        ev_end = ev_start + timedelta(
            seconds=float(event.get("duration") or 0))
        # Clip to both the session window and the trailing-15m window.
        clip_start = max(ev_start, window_start, start)
        clip_end = min(ev_end, now)
        secs = (clip_end - clip_start).total_seconds()
        if secs <= 0:
            continue
        score = event.get("score")
        score = score if score in (2, 1, 0, -1, -2) else 0
        seconds_by_score[score] += secs
        app = (event.get("app") or "").strip()
        if app:
            apps.add(app)
        if score in (-1, -2):
            last_distraction_end = clip_end \
                if last_distraction_end is None \
                else max(last_distraction_end, clip_end)

    if sum(seconds_by_score.values()) > 0:
        dominant = max(seconds_by_score,
                       key=lambda s: (seconds_by_score[s], s))
    else:
        dominant = 0  # no activity data: neutral, never assume +2
    switches = max(0, len(apps) - 1)
    anchor = last_distraction_end or start
    uninterrupted = max(0.0, (now - anchor).total_seconds() / 60.0)

    if dominant == 2 and switches == 0 and uninterrupted >= 15:
        state = "flow"
    elif dominant in (1, 2) and switches <= 2:
        state = "deep"
    else:
        state = "surface"
    return {"state": state, "dominant_score": dominant,
            "switches_15m": switches,
            "uninterrupted_min": round(uninterrupted, 1)}


def main():
    parser = argparse.ArgumentParser(description="Focus Core focus sessions.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=None,
                        help="SQLite file to use (default: focuscore.db "
                             "next to the code).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start a focus session.",
                             parents=[common])
    p_start.add_argument("--label", required=True,
                         help="What this session is for, e.g. 'Deep work'.")
    p_start.add_argument("--minutes", type=float, default=50,
                         help="Duration in minutes (default: 50).")
    p_start.add_argument("--block-level", default="strict",
                         choices=sorted(BLOCK_LEVELS),
                         help="strict blocks scores -1 and -2; "
                              "lenient blocks only -2 (default: strict).")
    p_start.add_argument("--mode", default="classic",
                         choices=list(SESSION_TYPES),
                         help="classic: fixed timer; flowtime: open-ended "
                              "with a soft target; pomodoro: work/break "
                              "cycles (default: classic).")
    p_start.add_argument("--target-cycles", type=int, default=4,
                         help="pomodoro work cycles before the target is "
                              "reached (default: 4).")

    sub.add_parser("end", help="Finish the active session with a summary.",
                   parents=[common])
    sub.add_parser("abort", help="Give up on the active session.",
                   parents=[common])
    sub.add_parser("status",
                   help="Show the active session and time left.",
                   parents=[common])
    parser.add_argument("--db", default=None,
                        help="SQLite file to use (default: focuscore.db "
                             "next to the code).")
    args = parser.parse_args()

    if args.command == "start":
        result = start_session(args.label, args.minutes,
                               block_level=args.block_level,
                               db_path=args.db,
                               session_type=args.mode,
                               target_cycles=args.target_cycles)
        if "error" in result:
            print("Error: %s" % result["error"])
            raise SystemExit(1)
        print("Focus session started: %r for %.0f minutes (%s blocking)."
              % (result["label"], result["planned_minutes"],
                 result["block_level"]))
        print("Run focus-watch.bat (double-click it) so distractions "
              "are blocked while you work.")
    elif args.command == "end":
        result = end_session(db_path=args.db)
        if "error" in result:
            print("Error: %s" % result["error"])
            raise SystemExit(1)
        summary = result["summary"]
        print("Session %r completed." % summary["label"])
        print("  Focus work: %.1f min | Neutral: %.1f min | "
              "Distracting: %.1f min"
              % (summary["focus_minutes"], summary["neutral_minutes"],
                 summary["distracting_minutes"]))
        print("  Pulse: %.1f | Distractions blocked: %d | "
              "Planned %.0f min, actual %.1f min"
              % (summary["pulse"], summary["blocks_count"],
                 summary["planned_minutes"], summary["actual_minutes"]))
    elif args.command == "abort":
        result = abort_session(db_path=args.db)
        if "error" in result:
            print("Error: %s" % result["error"])
            raise SystemExit(1)
        print("Session %r aborted." % result["label"])
    elif args.command == "status":
        active = get_active_session(db_path=args.db)
        if not active:
            print("No active focus session.")
        else:
            print("Active: %r -- %s left of %.0f planned minutes (%s)."
                  % (active["label"],
                     _fmt_hms(remaining_seconds(active)),
                     active["planned_minutes"], active["block_level"]))


if __name__ == "__main__":
    main()
