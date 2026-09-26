"""Home-page "what needs your attention" cards.

Each rule below is a plain documented threshold -- no invented formulas.
``attention_cards()`` returns a list of card dicts::

    {"code", "title", "detail", "button_text", "button_href"}

An empty list means "all clear". The dashboard renders one card per item
with exactly one button, so the user always knows the single next step.
"""

from datetime import datetime, timedelta

from . import backup, focus, goals as goals_mod, store, updater

# Pulse color bands shown on the home page (0-100 scale).
PULSE_GOOD = 60  # green at/above this
PULSE_OK = 40    # amber at/above this, red below

# A "more than" goal counts as at-risk only when the user is under half
# the target AND more than half the day is already over -- nagging at
# 9am about a full-day target would just be noise.
GOAL_AT_RISK_PCT = 50.0
DAY_HALF_OVER = 0.5

# "Tracker isn't sending data" fires when nothing was captured in the
# last 30 minutes, but only during waking hours (07:00-23:00) -- a quiet
# night is normal, not a problem.
TRACKER_STALE_MINUTES = 30
DAY_START_HOUR = 7
DAY_END_HOUR = 23


def _to_naive_local(value):
    """Parse an ISO timestamp to naive local datetime (None on failure)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _latest_activity_ts(day, db_path):
    latest = None
    for event in store.get_day_activities(day, path=db_path):
        ts = _to_naive_local(event.get("ts"))
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest


def attention_cards(db_path=None, now=None, backup_dest_dir=None):
    """Compute today's attention cards. Pure logic -- easy to unit test."""
    now = now or datetime.now()
    today = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    cards = []

    summary = store.get_day_summary(today, path=db_path)

    # 1. Uncategorized activities still need a human decision.
    uncat = [u for u in summary["uncategorized"] if u["seconds"] > 0]
    if uncat:
        cards.append({
            "code": "uncategorized",
            "title": "%d %s need%s categories" % (
                len(uncat), "activity" if len(uncat) == 1 else "activities",
                "s" if len(uncat) == 1 else ""),
            "detail": "They counted as Neutral in your Pulse. "
                      "Teach Focus Core once and it remembers.",
            "button_text": "Review now",
            "button_href": "/activities?day=" + today,
        })

    # 2. Yesterday is finished but its timesheet was never finalized.
    y_summary = store.get_day_summary(yesterday, path=db_path)
    if y_summary["total_seconds"] > 0 and not store.day_is_locked(
            yesterday, path=db_path):
        cards.append({
            "code": "timesheet",
            "title": "Yesterday's timesheet isn't final",
            "detail": "Accept yesterday's tracked blocks so the week stays "
                      "accurate.",
            "button_text": "Open timesheet",
            "button_href": "/timesheet?day=" + yesterday,
        })

    # 3. No completed focus session yet today.
    completed_today = any(
        s["status"] == "completed" and (s["started_at"] or "")[:10] == today
        for s in focus.list_sessions(limit=50, db_path=db_path))
    if not completed_today:
        cards.append({
            "code": "no_focus",
            "title": "No focus session yet today",
            "detail": "A short focused block beats a long distracted day.",
            "button_text": "Start one",
            "button_href": "/focus",
        })

    # 4. Goals currently off track.
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_fraction = (now - midnight).total_seconds() / 86400.0
    for ev in goals_mod.evaluate_all(summary, db_path=db_path):
        at_risk = False
        if ev["direction"] == "more_than":
            at_risk = (ev["pct"] < GOAL_AT_RISK_PCT
                       and day_fraction > DAY_HALF_OVER)
        else:  # less_than: already over the limit
            at_risk = ev["status"] == "missed"
        if at_risk:
            cards.append({
                "code": "goal",
                "title": "Goal at risk: %s" % ev["name"],
                "detail": "Now %.0f of %.0f %s." % (
                    ev["current"], ev["target"], ev["unit"]),
                "button_text": "View goals",
                "button_href": "/goals",
            })

    # 5. Tracker stopped sending data (daytime only).
    latest = _latest_activity_ts(today, db_path)
    stale = latest is None or (
        now - latest).total_seconds() > TRACKER_STALE_MINUTES * 60
    if stale and DAY_START_HOUR <= now.hour < DAY_END_HOUR:
        cards.append({
            "code": "tracker",
            "title": "Tracker isn't sending data",
            "detail": "Nothing was captured in the last %d minutes. "
                      "1) Look at the Windows taskbar tray for the "
                      "ActivityWatch icon. 2) If it's missing, start "
                      "ActivityWatch (it should start with Windows)."
                      % TRACKER_STALE_MINUTES,
            "button_text": "Try collecting now",
            "button_href": "/collect",
        })

    # 6. Backups are getting old.
    newest = backup.newest_backup(dest_dir=backup_dest_dir)
    if newest is None:
        cards.append({
            "code": "backup",
            "title": "Your data has never been backed up",
            "detail": "One click keeps a copy safe for a new laptop.",
            "button_text": "Open Backup",
            "button_href": "/backup",
        })
    else:
        age_days = int((now - newest["modified"]).total_seconds() / 86400)
        if age_days >= backup.ATTENTION_AFTER_DAYS:
            cards.append({
                "code": "backup",
                "title": "Your data hasn't been backed up in %d days"
                         % age_days,
                "detail": "Backups protect you if this laptop is lost or "
                          "replaced.",
                "button_text": "Open Backup",
                "button_href": "/backup",
            })

    # 7. A newer version is waiting (installed copies only). Reads the
    # cached check only -- never touches the network from here.
    update = updater.read_cached_check()
    if update and update.get("status") == "ok" \
            and update.get("update_available"):
        cards.append({
            "code": "update",
            "title": "Focus Core %s is available" % update["latest"],
            "detail": "You're on %s. One click updates the app; your data "
                      "is backed up first and never touched."
                      % update["current"],
            "button_text": "Update now",
            "button_href": "/update",
        })

    return cards


def pulse_band(pulse):
    """'good' / 'ok' / 'bad' band for a 0-100 Pulse (None -> 'none')."""
    if pulse is None:
        return "none"
    if pulse >= PULSE_GOOD:
        return "good"
    if pulse >= PULSE_OK:
        return "ok"
    return "bad"
