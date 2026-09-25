"""Threshold-based desktop alerts (RescueTime-style).

An alert watches one target -- either a whole category (e.g. "Entertainment")
or a single activity identified by its match key (e.g. "domain:youtube.com",
the same keys Phase 1 overrides use) -- and fires when today's time on it
reaches ``threshold_minutes``.

To avoid spam, an alert never fires twice within ``cooldown_minutes`` of its
previous firing. Firings are persisted in the ``alert_firings`` table.

The notification is sent through an injectable ``notifier`` callable
``notifier(alert, current_minutes)``; the default sends a Windows desktop
notification via plyer. Notifier failures are logged to stderr and never
break the check.

Usage:
    python -m focuscore.alerts --check                  # one pass now
    python -m focuscore.alerts --watch --interval 300    # loop forever
"""

import argparse
import sys
import time
from datetime import date, datetime

from . import store

DEFAULT_INTERVAL = 300


def _current_minutes(alert, day_summary, activities):
    if alert["target_type"] == "category":
        seconds = day_summary["seconds_by_category"].get(
            alert["target_name"], 0.0)
        return float(seconds) / 60.0
    total = sum(
        float(a.get("duration", 0) or 0)
        for a in (activities or [])
        if a.get("match_key") == alert["target_name"]
    )
    return total / 60.0


def _desktop_notify(alert, current_minutes):
    """Default notifier: Windows desktop notification via plyer."""
    try:
        from plyer import notification
        notification.notify(
            title="Focus Core: " + alert["name"],
            message=(alert["message"]
                     or "You reached %.0f minutes on %s."
                     % (current_minutes, alert["target_name"])),
            app_name="Focus Core",
            timeout=10,
        )
    except Exception as exc:  # never break the check over a notification
        print("desktop notification failed: %s" % exc, file=sys.stderr)


def check_alerts(day_summary, notifier=None, db_path=None,
                 activities=None, now=None):
    """Check all enabled alerts against a day summary.

    Returns a list of the alerts that fired, each as a dict with
    "current_minutes" and "fired_at" added. A firing is recorded in
    ``alert_firings`` before notifying, so a failing notifier can not
    cause repeat spam.
    """
    now = now or datetime.now()
    notify = notifier or _desktop_notify
    fired = []

    for alert in store.list_alerts(path=db_path):
        if not alert["enabled"]:
            continue
        current = _current_minutes(alert, day_summary, activities)
        if current < float(alert["threshold_minutes"]):
            continue
        last = store.last_firing_at(alert["id"], path=db_path)
        cooldown = float(alert["cooldown_minutes"] or 0) * 60.0
        if last is not None and (now - last).total_seconds() < cooldown:
            continue

        store.record_firing(alert["id"], now, current, path=db_path)
        try:
            notify(alert, current)
        except Exception as exc:
            print("alert notifier failed for %r: %s"
                  % (alert["name"], exc), file=sys.stderr)
        fired.append({**alert, "current_minutes": current,
                      "fired_at": now.isoformat(timespec="seconds")})
    return fired


def add_alert(name, target_type, target_name, threshold_minutes,
              message="", cooldown_minutes=60, enabled=True, db_path=None):
    """Validate and store a new alert; returns its id."""
    if target_type not in ("category", "activity"):
        raise ValueError("target_type must be 'category' or 'activity'")
    if not (target_name or "").strip():
        raise ValueError("alerts need a target name")
    if float(threshold_minutes) <= 0:
        raise ValueError("threshold_minutes must be > 0")
    if float(cooldown_minutes) < 0:
        raise ValueError("cooldown_minutes must be >= 0")
    return store.add_alert(
        name.strip(), target_type, target_name.strip(),
        float(threshold_minutes), message.strip(),
        float(cooldown_minutes), enabled, path=db_path)


def _single_pass(db_path):
    from .ingest import ActivityWatchError
    from .pipeline import run_day

    today = date.today()
    try:
        run_day(today, db_path=db_path)
    except ActivityWatchError as exc:
        print("Warning: %s -- checking against stored data." % exc)
    day_str = today.isoformat()
    summary = store.get_day_summary(day_str, path=db_path)
    activities = store.get_day_activities(day_str, path=db_path)
    fired = check_alerts(summary, db_path=db_path, activities=activities)
    if fired:
        for alert in fired:
            print("ALERT: %s -- %.1f min on %s (threshold %.1f min)"
                  % (alert["name"], alert["current_minutes"],
                     alert["target_name"],
                     float(alert["threshold_minutes"])))
    else:
        print("No alerts fired.")


def main():
    parser = argparse.ArgumentParser(
        description="Check Focus Core alert thresholds.")
    parser.add_argument("--check", action="store_true",
                        help="Run one check pass and exit.")
    parser.add_argument("--watch", action="store_true",
                        help="Loop: refresh today's data, check, sleep.")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help="Seconds between checks in --watch mode "
                             "(default: %(default)s).")
    parser.add_argument("--db", default=None,
                        help="SQLite file to use (default: focuscore.db "
                             "next to the code).")
    args = parser.parse_args()

    if not (args.check or args.watch):
        parser.error("use --check for one pass or --watch to loop")
    if args.interval <= 0:
        parser.error("--interval must be > 0")

    if args.watch:
        print("Watching alerts every %d seconds. Close this window to stop."
              % args.interval)
        try:
            while True:
                _single_pass(args.db)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("Stopped.")
    else:
        _single_pass(args.db)


if __name__ == "__main__":
    main()
