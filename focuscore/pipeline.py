"""Daily pipeline: fetch -> categorize -> score -> store -> summary.

Usage:
    python -m focuscore.pipeline --day 2026-09-25          # from ActivityWatch
    python -m focuscore.pipeline --day 2026-09-25 --demo    # synthetic events

The pipeline is idempotent: re-running it for a day replaces that day's
stored events (so per-activity overrides can be applied retroactively).
"""

import argparse
from datetime import date, datetime, time

from . import store
from .ingest import ActivityWatchClient, ActivityWatchError
from .scoring import productivity_pulse, resolve_activity_score
from .taxonomy import categorize, match_key


def _demo_event(day, start_h, start_m, minutes, app, title, url=None):
    start = datetime.combine(day, time(start_h, start_m))
    return {
        "ts": start.isoformat(),
        "duration": float(minutes * 60),
        "app": app,
        "title": title,
        "url": url,
    }


def generate_demo_events(day):
    """Build a fixed, realistic synthetic day (deterministic, no randomness).

    Covers: focused work, communication, distracting sites, an unknown app
    (lands in the uncategorized queue), and a lunch AFK block.
    """
    events = [
        _demo_event(day, 9, 0, 120, "code", "Visual Studio Code - focuscore"),
        _demo_event(day, 11, 0, 30, "chrome", "Inbox - Gmail",
                    "https://mail.google.com/mail/u/0/#inbox"),
        _demo_event(day, 11, 30, 30, "chrome", "Some video - YouTube",
                    "https://www.youtube.com/watch?v=demo"),
        _demo_event(day, 13, 0, 90, "chrome", "org/repo - GitHub",
                    "https://github.com/org/repo"),
        _demo_event(day, 14, 30, 30, "chrome", "How to parse JSON - Stack Overflow",
                    "https://stackoverflow.com/questions/1"),
        _demo_event(day, 15, 0, 20, "chrome", "Home - X",
                    "https://x.com/home"),
        _demo_event(day, 15, 20, 40, "mystery-app-xyz", "Some random window"),
        _demo_event(day, 16, 0, 30, "slack", "general - Slack"),
        _demo_event(day, 16, 30, 30, "excel", "Budget 2026 - Excel"),
        _demo_event(day, 20, 0, 60, "chrome", "Watch - Netflix",
                    "https://www.netflix.com/watch/1"),
    ]
    afk_seconds = 3600.0  # 12:00-13:00 lunch, away from keyboard
    return events, afk_seconds


def run_day(day, demo=False, db_path=None):
    """Run the full pipeline for one date; return the day's summary dict."""
    day_str = day.isoformat()

    if demo:
        events, afk_seconds = generate_demo_events(day)
    else:
        events, afk_seconds = ActivityWatchClient().fetch_day(day)

    overrides = store.get_overrides(path=db_path)

    total_seconds = 0.0
    for event in events:
        app, title, url = event.get("app", ""), event.get("title", ""), event.get("url")
        category, inherited, _rule = categorize(app, title, url)
        key = match_key(app, url)
        override = overrides.get(key)
        score = resolve_activity_score(key, inherited, override)
        event.update(
            category=category, score=score,
            override_score=override, match_key=key, day=day_str,
        )
        total_seconds += float(event.get("duration", 0))

    store.save_events(day_str, events, path=db_path)
    store.save_day_stats(day_str, afk_seconds, total_seconds, path=db_path)

    summary = store.get_day_summary(day_str, path=db_path)
    return {
        "day": day_str,
        "pulse": productivity_pulse(summary["seconds_by_level"]),
        "seconds_by_level": summary["seconds_by_level"],
        "seconds_by_category": summary["seconds_by_category"],
        "uncategorized_count": len(summary["uncategorized"]),
        "uncategorized": summary["uncategorized"][:10],
        "afk_seconds": summary["afk_seconds"],
        "total_seconds": summary["total_seconds"],
    }


def _hours(seconds):
    return seconds / 3600.0


def main():
    parser = argparse.ArgumentParser(
        description="Run the focus-core daily pipeline.")
    parser.add_argument("--day", default=date.today().isoformat(),
                        help="Day to process as YYYY-MM-DD (default: today).")
    parser.add_argument("--demo", action="store_true",
                        help="Use synthetic demo events instead of ActivityWatch.")
    parser.add_argument("--db", default=None,
                        help="SQLite file to use "
                             "(default: focuscore.db next to the code).")
    args = parser.parse_args()

    try:
        day = datetime.strptime(args.day, "%Y-%m-%d").date()
    except ValueError:
        parser.error("--day must look like YYYY-MM-DD")

    try:
        summary = run_day(day, demo=args.demo, db_path=args.db)
    except ActivityWatchError as exc:
        print("Error: %s" % exc)
        raise SystemExit(1)

    print("Day: %s%s" % (summary["day"], " (demo data)" if args.demo else ""))
    print("Productivity Pulse: %.1f / 100" % summary["pulse"])
    print("Tracked: %.2fh | AFK (excluded): %.2fh" % (
        _hours(summary["total_seconds"]), _hours(summary["afk_seconds"])))
    print("By score level:")
    for level in (2, 1, 0, -1, -2):
        print("  %+d: %.2fh" % (level, _hours(summary["seconds_by_level"][level])))
    print("By category:")
    for name, seconds in sorted(summary["seconds_by_category"].items(),
                                key=lambda kv: kv[1], reverse=True):
        print("  %s: %.2fh" % (name, _hours(seconds)))
    print("Uncategorized activities to review: %d" % summary["uncategorized_count"])
    for item in summary["uncategorized"]:
        print("  %s (%s): %.2fh" % (
            item["match_key"], item["app"], _hours(item["seconds"])))


if __name__ == "__main__":
    main()
