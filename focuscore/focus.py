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
from datetime import datetime, timedelta

from . import store
from .scoring import productivity_pulse

BLOCK_LEVELS = {
    "strict": frozenset({-1, -2}),
    "lenient": frozenset({-2}),
}

MAX_MINUTES = 480  # sanity cap: one session is at most 8 hours


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
                  db_path=None, now=None, enforcement_mode="strict"):
    """Start a focus session; returns the session dict or {"error": ...}.

    Starting fails when another session is already active -- finish or
    abort it first. enforcement_mode: "strict" (notify + overlay) or
    "hardcore" (minimize + 30 s locked overlay; explicit opt-in).
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

    active = store.get_active_session(path=db_path)
    if active:
        return {"error": "A focus session is already active: %r. "
                "End or abort it first." % active["label"]}

    planned_end = now + timedelta(minutes=minutes)
    session_id = store.create_session(
        label, minutes, _now_iso(now), _now_iso(planned_end), block_level,
        enforcement_mode=enforcement_mode, path=db_path)
    return store.get_session(session_id, path=db_path)


def end_session(db_path=None, now=None):
    """Finish the active session; returns {"session", "summary"} or error."""
    now = now or datetime.now()
    active = store.get_active_session(path=db_path)
    if not active:
        return {"error": "No active focus session to end."}
    store.end_session(active["id"], "completed", _now_iso(now), path=db_path)
    session = store.get_session(active["id"], path=db_path)
    return {"session": session,
            "summary": session_summary(session["id"], db_path=db_path,
                                       now=now)}


def abort_session(db_path=None, now=None):
    """Give up on the active session; returns the session dict or error."""
    now = now or datetime.now()
    active = store.get_active_session(path=db_path)
    if not active:
        return {"error": "No active focus session to abort."}
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


def _fmt_hms(total_seconds):
    total = int(total_seconds)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


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
                               db_path=args.db)
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
