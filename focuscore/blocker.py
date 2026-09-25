"""Focus-session enforcement: block distracting activity while a session is active.

How it works: every few seconds the enforcer asks ActivityWatch what the
current window is. If that window's score is blocked by the session's block
level (strict: -1 and -2, lenient: only -2), the event is recorded in
``focus_blocks``, a desktop notification pops up, and a fullscreen
"back to work" overlay appears.

All decision logic is PURE and unit-testable (is_blocked, enforce_once).
Every Windows-only / OS-interaction part lives in show_block_overlay()
and fails silently when tkinter or a display is unavailable.

Usage:
    python -m focuscore.blocker --enforce        # loop until session ends
    python -m focuscore.blocker --enforce --poll 10
"""

import argparse
import sys
import time
from datetime import datetime, timedelta

from .focus import BLOCK_LEVELS
from .ingest import BROWSER_APPS

# Dedupe: app key -> datetime of the last block notification/overlay.
# One distraction = one pop-up per minute, not one per 5-second poll.
_LAST_NOTIFIED = {}
NOTIFY_DEDUPE_SECONDS = 60
# A window event only counts as "current" if it started recently.
CURRENT_WINDOW_SECONDS = 60


def is_blocked(app, title, url, block_level, categorize_fn, overrides=None):
    """Pure decision function: should this window be blocked?

    Returns (blocked: bool, effective_score: int, category: str).
    The per-activity override (from store.get_overrides) always wins over
    the inherited category score -- an activity the user re-scored as
    productive is never blocked.
    """
    if block_level not in BLOCK_LEVELS:
        raise ValueError("block_level must be one of %s."
                         % sorted(BLOCK_LEVELS))
    from .scoring import resolve_activity_score
    from .taxonomy import match_key

    category, inherited, _rule = categorize_fn(app, title, url)
    key = match_key(app, url)
    score = resolve_activity_score(
        key, inherited, (overrides or {}).get(key))
    return score in BLOCK_LEVELS[block_level], score, category


def _to_naive_local(ts):
    aware = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if aware.tzinfo is None:
        return aware
    return aware.astimezone().replace(tzinfo=None)


def _as_aware_local(dt):
    """Return an aware datetime; a naive input is assumed to be local time."""
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt


def get_current_window(client, now=None,
                       freshness_seconds=CURRENT_WINDOW_SECONDS):
    """Ask ActivityWatch what the user is doing RIGHT NOW.

    Returns {"app", "title", "url", "ts"} or None. Returns None when the
    tracker is unreachable, the newest event is stale (user is AFK or the
    tracker stopped), or anything unexpected happens -- we never block
    when we are unsure.
    """
    now = _as_aware_local(now or datetime.now())
    # Local wall clock, matching what _to_naive_local() returns, so the
    # freshness math below compares like with like.
    now_naive = now.replace(tzinfo=None)
    try:
        buckets = client.get_buckets() or {}
        window_ids = [b for b in buckets
                      if b.startswith("aw-watcher-window_")]
        web_ids = [b for b in buckets if b.startswith("aw-watcher-web-")]
        if not window_ids:
            return None

        cutoff = now - timedelta(seconds=120)
        # The bounds MUST carry an explicit UTC offset. ActivityWatch
        # interprets a naive ISO string as UTC, which shifts the query
        # window by the machine's UTC offset and returns the wrong events.
        start_s, end_s = cutoff.isoformat(), now.isoformat()
        latest = None  # (end_ts, start_ts, app, title)
        for bucket_id in window_ids:
            try:
                events = client.get_events(
                    bucket_id, start_s, end_s) or []
            except Exception:
                continue
            for raw in events:
                try:
                    ts = _to_naive_local(raw.get("timestamp"))
                    duration = float(raw.get("duration") or 0)
                except (ValueError, TypeError, AttributeError):
                    continue
                data = raw.get("data") or {}
                end = ts + timedelta(seconds=duration)
                if latest is None or end > latest[0]:
                    latest = (end, ts, data.get("app") or "",
                              data.get("title") or "")
        if latest is None:
            return None
        end, ts, app, title = latest
        # Freshness is measured from the event's END, not its start: a
        # window focused for several minutes is a single long event, and
        # it is still current even though it started a while ago.
        if (now_naive - end).total_seconds() > freshness_seconds:
            return None  # stale: AFK or tracker down -- never block then

        # Attach the tab URL when the current window is a browser.
        url = None
        if app.lower() in BROWSER_APPS:
            best = None  # (ts, url)
            for bucket_id in web_ids:
                try:
                    events = client.get_events(
                        bucket_id, start_s, end_s) or []
                except Exception:
                    continue
                for raw in events:
                    try:
                        wts = _to_naive_local(raw.get("timestamp"))
                    except (ValueError, TypeError, AttributeError):
                        continue
                    wurl = (raw.get("data") or {}).get("url")
                    if wurl and (best is None or wts > best[0]):
                        best = (wts, wurl)
            if best and abs((best[0] - ts).total_seconds()) <= 15:
                url = best[1]

        return {"app": app, "title": title, "url": url,
                "ts": ts.isoformat(timespec="seconds")}
    except Exception:
        return None


def enforce_once(session, client, categorize_fn, notify_fn, overlay_fn,
                 db_path=None, now=None, last_notified=None):
    """Run one enforcement poll cycle (pure logic, injectable sides).

    Returns {"action": "none"|"allowed"|"blocked", ...}. A blocked window
    is recorded in focus_blocks and triggers the notifier + overlay at
    most once per app per minute (dedupe) -- the block count in the
    session summary therefore counts distinct distractions, not polls.
    """
    from . import store

    now = now or datetime.now()
    if last_notified is None:
        last_notified = _LAST_NOTIFIED

    if not session or session.get("status") != "active":
        return {"action": "none", "reason": "no active session"}

    window = get_current_window(client, now=now)
    if not window:
        return {"action": "none", "reason": "no current window"}

    overrides = store.get_overrides(path=db_path)
    blocked, score, category = is_blocked(
        window["app"], window["title"], window["url"],
        session.get("block_level") or "strict", categorize_fn,
        overrides=overrides)

    base = {"app": window["app"], "title": window["title"],
            "url": window["url"], "score": score, "category": category}
    if not blocked:
        return {"action": "allowed", **base}

    app_key = (window["app"] or "unknown").lower()
    last = last_notified.get(app_key)
    if last is not None and \
            (now - last).total_seconds() < NOTIFY_DEDUPE_SECONDS:
        return {"action": "blocked", "notified": False,
                "reason": "deduped", **base}

    store.record_block(session["id"], now.isoformat(timespec="seconds"),
                       window["app"], window["title"], window["url"],
                       score, category, path=db_path)
    last_notified[app_key] = now
    try:
        notify_fn(window["app"], window["title"], session["label"])
    except Exception as exc:
        print("block notification failed: %s" % exc, file=sys.stderr)
    try:
        overlay_fn(session["label"], window["app"], session["id"],
                   db_path=db_path)
    except Exception as exc:
        print("block overlay failed: %s" % exc, file=sys.stderr)
    return {"action": "blocked", "notified": True, **base}


def _desktop_notify_block(app, title, session_label):
    """Default notifier: Windows desktop pop-up via plyer."""
    try:
        from plyer import notification
        notification.notify(
            title="Focus Core: stay focused",
            message="%s is blocked during %r." % (app or "This app",
                                                  session_label),
            app_name="Focus Core",
            timeout=10,
        )
    except Exception as exc:  # never break enforcement over a pop-up
        print("desktop notification failed: %s" % exc, file=sys.stderr)


def show_block_overlay(label, app, session_id, db_path=None):
    """Fullscreen 'back to work' reminder (Windows, tkinter stdlib only).

    Shows the session label, the blocked app, elapsed time, an "End
    session" button and a "Back to work" button. Fails SILENTLY when
    tkinter or a display is unavailable -- enforcement continues without
    the overlay.
    """
    try:
        import tkinter as tk
    except Exception:
        return
    try:
        from . import focus as focus_mod

        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(bg="#1a1a1a")

        session = focus_mod.get_active_session(db_path=db_path)
        started = datetime.fromisoformat(session["started_at"]) \
            if session else datetime.now()

        tk.Label(root, text="Focus session in progress",
                 font=("Segoe UI", 28), bg="#1a1a1a", fg="#ffffff").pack(pady=60)
        tk.Label(root, text=label, font=("Segoe UI", 20),
                 bg="#1a1a1a", fg="#9e9e9e").pack()
        tk.Label(root, text="Blocked: %s" % (app or "this app"),
                 font=("Segoe UI", 24), bg="#1a1a1a", fg="#e53935").pack(pady=30)
        elapsed_var = tk.StringVar()
        tk.Label(root, textvariable=elapsed_var, font=("Segoe UI", 18),
                 bg="#1a1a1a", fg="#ffffff").pack(pady=10)

        def tick():
            delta = datetime.now() - started
            elapsed_var.set("Elapsed: %d:%02d"
                            % (int(delta.total_seconds()) // 60,
                               int(delta.total_seconds()) % 60))
            root.after(1000, tick)

        def back_to_work():
            root.destroy()

        def end_session():
            focus_mod.end_session(db_path=db_path)
            root.destroy()

        tick()
        tk.Button(root, text="Back to work", font=("Segoe UI", 16),
                  command=back_to_work, padx=30, pady=10).pack(pady=20)
        tk.Button(root, text="End session", font=("Segoe UI", 14),
                  command=end_session, padx=20, pady=8).pack()
        root.mainloop()
    except Exception:
        return  # overlay is best-effort; never crash enforcement


def run_enforcer(poll_seconds=5, db_path=None):
    """Loop: refresh today's data, enforce the active session, sleep.

    Stops when no session is active. One bad poll never kills the loop.
    """
    from datetime import date

    from . import focus as focus_mod
    from .ingest import ActivityWatchClient, ActivityWatchError
    from .pipeline import run_day
    from .taxonomy import categorize as categorize_fn

    print("Focus guard running: blocking distractions while a session is "
          "active. Close this window to stop.")
    client = ActivityWatchClient()
    try:
        while True:
            try:
                session = focus_mod.get_active_session(db_path=db_path)
                if not session:
                    print("No active focus session -- guard stopping.")
                    break
                try:
                    run_day(date.today(), db_path=db_path)
                except ActivityWatchError as exc:
                    print("Warning: %s -- enforcing on stored data." % exc)
                result = enforce_once(
                    session, client, categorize_fn, _desktop_notify_block,
                    show_block_overlay, db_path=db_path)
                if result["action"] == "blocked" and result.get("notified"):
                    print("Blocked: %s (%s)"
                          % (result["app"], result["category"]))
            except Exception as exc:  # one bad poll never kills enforcement
                print("enforcer poll failed: %s" % exc, file=sys.stderr)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("Stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Enforce the active Focus Core focus session.")
    parser.add_argument("--enforce", action="store_true",
                        help="Loop: block distractions until the session ends.")
    parser.add_argument("--poll", type=int, default=5,
                        help="Seconds between checks (default: %(default)s).")
    parser.add_argument("--db", default=None,
                        help="SQLite file to use (default: focuscore.db "
                             "next to the code).")
    args = parser.parse_args()

    if not args.enforce:
        parser.error("use --enforce to start guarding the active session")
    if args.poll <= 0:
        parser.error("--poll must be > 0")
    run_enforcer(poll_seconds=args.poll, db_path=args.db)


if __name__ == "__main__":
    main()
