"""Chronotype peak-window detection (Phase 11).

The user's personal peak energy window (default 09:00-11:30) drives:
- the 1-click peak launch card on /focus,
- the 5-minute tray toast,
- dynamic shield escalation (via the snapshot refresher -- I-1 safe),
- the +50% XP peak bonus.

Settings keys (existing settings table, no schema change):
    peak_start   "HH:MM"  default "09:00"
    peak_end     "HH:MM"  default "11:30"
    peak_enabled "1"/"0"  default "1"
"""

from datetime import datetime, time as dtime, timedelta


def _parse_hhmm(value, fallback):
    try:
        h, m = str(value or fallback).split(":")
        return dtime(int(h) % 24, int(m) % 60)
    except (ValueError, AttributeError):
        h, m = fallback.split(":")
        return dtime(int(h), int(m))


def get_window(db_path=None):
    """Return (enabled, start_time, end_time) for the peak window."""
    from . import store
    enabled = store.get_setting("peak_enabled", "1", path=db_path) == "1"
    start = _parse_hhmm(
        store.get_setting("peak_start", "09:00", path=db_path), "09:00")
    end = _parse_hhmm(
        store.get_setting("peak_end", "11:30", path=db_path), "11:30")
    return enabled, start, end


def set_window(start, end, enabled=True, db_path=None):
    """Persist the peak window. start/end are 'HH:MM' strings."""
    from . import store
    # Validate by parsing; raises ValueError on garbage.
    _parse_hhmm(start, "09:00")
    _parse_hhmm(end, "11:30")
    store.set_setting("peak_start", start, path=db_path)
    store.set_setting("peak_end", end, path=db_path)
    store.set_setting("peak_enabled", "1" if enabled else "0",
                      path=db_path)


def is_peak_now(now=None, db_path=None):
    """True when `now` falls inside the enabled peak window."""
    now = now or datetime.now()
    enabled, start, end = get_window(db_path=db_path)
    if not enabled:
        return False
    t = now.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= t <= end
    # Overnight window (e.g. 22:00-02:00).
    return t >= start or t <= end


def peak_status(now=None, db_path=None):
    """Return {'state': 'before'|'in'|'after', 'minutes': int}.

    'minutes' = minutes until the window starts (before), until it ends
    (in), or since it ended (after). Used by the tray toast scheduler.
    """
    now = now or datetime.now()
    enabled, start, end = get_window(db_path=db_path)
    if not enabled:
        return {"state": "after", "minutes": 0}
    t = now.time().replace(second=0, microsecond=0)

    def _mins(a, b):
        return (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)

    if start <= end:
        if t < start:
            return {"state": "before", "minutes": _mins(t, start)}
        if t <= end:
            return {"state": "in", "minutes": _mins(t, end)}
        return {"state": "after", "minutes": _mins(end, t)}
    # Overnight window.
    if t >= start or t <= end:
        # Inside: minutes until end (wrap past midnight).
        mins = _mins(t, end) % (24 * 60)
        return {"state": "in", "minutes": mins}
    mins = _mins(t, start) % (24 * 60)
    return {"state": "before", "minutes": mins}


def session_overlaps_peak(started_at, ended_at, db_path=None):
    """True if any part of [started_at, ended_at] overlapped the peak
    window. Accepts datetime objects or ISO strings."""
    from .focus import _to_naive
    enabled, start, end = get_window(db_path=db_path)
    if not enabled:
        return False
    s = _to_naive(started_at) if isinstance(started_at, str) \
        else started_at
    # Active sessions have no end yet -- use now as the open boundary.
    e = datetime.now() if ended_at is None else (
        _to_naive(ended_at) if isinstance(ended_at, str) else ended_at)
    if e <= s:
        return False
    # Check in 5-minute steps (cheap; sessions are hours at most).
    cur = s
    step = timedelta(minutes=5)
    while cur <= e:
        if is_peak_now(cur, db_path=db_path):
            return True
        cur = cur + step
    return is_peak_now(e, db_path=db_path)


def window_label(db_path=None):
    """Human label like '09:00 - 11:30'."""
    _, start, end = get_window(db_path=db_path)
    return "%02d:%02d - %02d:%02d" % (
        start.hour, start.minute, end.hour, end.minute)


def peak_window_end_iso(now=None, db_path=None):
    """Today's peak-window end as an ISO string ('' when disabled).

    Baked into RulesSnapshot so the enforcement worker can expire a
    stale peak escalation with zero SQLite (I-1).
    """
    now = now or datetime.now()
    enabled, _, end = get_window(db_path=db_path)
    if not enabled:
        return ""
    return now.replace(hour=end.hour, minute=end.minute, second=0,
                       microsecond=0).isoformat(timespec="seconds")
