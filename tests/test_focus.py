"""Tests for Phase 3: focus sessions and blocking.

All tests use synthetic data and a throwaway SQLite file -- no
ActivityWatch, no tkinter, no real windows needed.
"""

from datetime import date, datetime, timedelta

import pytest

from focuscore import blocker as blocker_mod
from focuscore import focus as focus_mod
from focuscore import store


def _db(tmp_path):
    return str(tmp_path / "test.db")


def _event(day, hour, minute, minutes, app, score, title="t"):
    start = datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute)
    return {
        "ts": start.isoformat(),
        "duration": float(minutes * 60),
        "app": app,
        "title": title,
        "url": None,
        "category": "X",
        "score": score,
        "override_score": None,
        "match_key": "app:" + app,
    }


# ------------------------------------------------------- session lifecycle ---

def test_start_end_lifecycle(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 9, 25, 10, 0, 0)
    session = focus_mod.start_session("Deep work", 60, db_path=db, now=now)
    assert "error" not in session
    assert session["label"] == "Deep work"
    assert session["status"] == "active"
    assert session["block_level"] == "strict"

    active = focus_mod.get_active_session(db_path=db)
    assert active["id"] == session["id"]

    # Only one active session at a time.
    second = focus_mod.start_session("Other", 25, db_path=db, now=now)
    assert "error" in second

    ended = focus_mod.end_session(db_path=db,
                                  now=now + timedelta(minutes=60))
    assert "error" not in ended
    assert ended["session"]["status"] == "completed"
    assert focus_mod.get_active_session(db_path=db) is None


def test_abort(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 9, 25, 10, 0, 0)
    session = focus_mod.start_session("Deep work", 60, db_path=db, now=now)
    aborted = focus_mod.abort_session(db_path=db,
                                      now=now + timedelta(minutes=10))
    assert aborted["status"] == "aborted"
    assert aborted["id"] == session["id"]
    assert focus_mod.get_active_session(db_path=db) is None


def test_start_validation(tmp_path):
    db = _db(tmp_path)
    assert "error" in focus_mod.start_session("", 60, db_path=db)
    assert "error" in focus_mod.start_session("x", 0, db_path=db)
    assert "error" in focus_mod.start_session("x", 9999, db_path=db)
    assert "error" in focus_mod.start_session(
        "x", 30, block_level="bogus", db_path=db)
    assert "error" in focus_mod.end_session(db_path=db)  # nothing active


# ------------------------------------------------------------ summary math ---

def test_session_summary_numbers(tmp_path):
    """Hand-computed: 30 focus + 15 neutral + 15 distracting minutes."""
    db = _db(tmp_path)
    day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 10, 0, 0)
    events = [
        _event(day, 10, 0, 30, "code", 2),     # inside: focus
        _event(day, 10, 30, 15, "docs", 0),    # inside: neutral
        _event(day, 10, 45, 15, "tube", -2),   # inside: distracting
        _event(day, 9, 0, 60, "code", 2),      # before window: ignored
        _event(day, 11, 0, 30, "tube", -2),    # after window: ignored
    ]
    store.save_events(day.isoformat(), events, path=db)

    session = focus_mod.start_session("Deep work", 60, db_path=db, now=now)
    store.record_block(session["id"], now.isoformat(), "tube", "t",
                       None, -2, "Entertainment", path=db)
    store.record_block(session["id"], now.isoformat(), "tube", "t",
                       None, -2, "Entertainment", path=db)
    result = focus_mod.end_session(db_path=db,
                                   now=now + timedelta(minutes=60))
    summary = result["summary"]

    assert summary["focus_minutes"] == 30.0
    assert summary["neutral_minutes"] == 15.0
    assert summary["distracting_minutes"] == 15.0
    assert summary["blocks_count"] == 2
    assert summary["planned_minutes"] == 60.0
    assert summary["actual_minutes"] == 60.0
    # Pulse: vd=900, n=900, vp=1800, total=3600 ->
    # (0 + 1800 + 7200) / 14400 * 100 = 62.5
    assert summary["pulse"] == 62.5


def test_summary_clips_partial_overlap(tmp_path):
    """An event straddling the session start only counts the inside part."""
    db = _db(tmp_path)
    day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 10, 0, 0)
    # 09:30-10:30 at +2: only 30 of its 60 minutes fall in the window.
    store.save_events(day.isoformat(),
                      [_event(day, 9, 30, 60, "code", 2)], path=db)
    focus_mod.start_session("Deep work", 60, db_path=db, now=now)
    result = focus_mod.end_session(db_path=db,
                                   now=now + timedelta(minutes=60))
    assert result["summary"]["focus_minutes"] == 30.0


def test_summary_unknown_session(tmp_path):
    assert "error" in focus_mod.session_summary(999, db_path=_db(tmp_path))


# ------------------------------------------------------------------ streak ---

def test_current_streak(tmp_path):
    db = _db(tmp_path)
    today = date(2026, 9, 25)

    def complete_on(day):
        start = datetime.combine(day, datetime.min.time()).replace(hour=10)
        focus_mod.start_session("s", 25, db_path=db, now=start)
        focus_mod.end_session(db_path=db, now=start + timedelta(minutes=25))

    complete_on(date(2026, 9, 25))
    complete_on(date(2026, 9, 24))
    complete_on(date(2026, 9, 23))
    complete_on(date(2026, 9, 21))  # gap on 9/22 must break the chain
    assert focus_mod.current_streak(db_path=db, today=today) == 3


def test_streak_zero_without_today(tmp_path):
    db = _db(tmp_path)
    start = datetime(2026, 9, 24, 10, 0, 0)
    focus_mod.start_session("s", 25, db_path=db, now=start)
    focus_mod.end_session(db_path=db, now=start + timedelta(minutes=25))
    assert focus_mod.current_streak(db_path=db,
                                    today=date(2026, 9, 25)) == 0


def test_aborted_session_does_not_count(tmp_path):
    db = _db(tmp_path)
    start = datetime(2026, 9, 25, 10, 0, 0)
    focus_mod.start_session("s", 25, db_path=db, now=start)
    focus_mod.abort_session(db_path=db, now=start + timedelta(minutes=5))
    assert focus_mod.current_streak(db_path=db,
                                    today=date(2026, 9, 25)) == 0


# --------------------------------------------------------------- is_blocked ---

def _cat(score, category="Entertainment"):
    return lambda app, title, url: (category, score, "rule:x")


def test_is_blocked_strict():
    for score, expected in [(2, False), (1, False), (0, False),
                            (-1, True), (-2, True)]:
        blocked, got_score, cat = blocker_mod.is_blocked(
            "x", "t", None, "strict", _cat(score))
        assert blocked == expected, score
        assert got_score == score
        assert cat == "Entertainment"


def test_is_blocked_lenient():
    for score, expected in [(2, False), (1, False), (0, False),
                            (-1, False), (-2, True)]:
        blocked, _, _ = blocker_mod.is_blocked(
            "x", "t", None, "lenient", _cat(score))
        assert blocked == expected, score


def test_is_blocked_bad_level():
    try:
        blocker_mod.is_blocked("x", "t", None, "bogus", _cat(0))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_is_blocked_override_wins():
    """A user override to +2 un-blocks an activity scored -2."""
    blocked, score, _ = blocker_mod.is_blocked(
        "chrome", "t", "https://www.youtube.com/watch",
        "strict", _cat(-2, "Entertainment"),
        overrides={"domain:www.youtube.com": 2})
    assert blocked is False
    assert score == 2


# ------------------------------------------------------------- enforce_once ---

class FakeClient:
    """Mimics ActivityWatchClient.get_buckets/get_events."""

    def __init__(self, window_events=(), web_events=(), broken=False):
        self._window = list(window_events)
        self._web = list(web_events)
        self._broken = broken

    def get_buckets(self):
        if self._broken:
            raise ConnectionError("down")
        buckets = {}
        if self._window is not None:
            buckets["aw-watcher-window_host"] = {}
        if self._web:
            buckets["aw-watcher-web-chrome_host"] = {}
        return buckets

    def get_events(self, bucket_id, start_iso, end_iso):
        if "window" in bucket_id:
            return self._window
        return self._web


def _raw_event(now, app, title, url=None):
    return {"timestamp": now.isoformat(),
            "duration": 30,
            "data": {"app": app, "title": title, "url": url}}


def _session_fixture(tmp_path, now, block_level="strict"):
    db = _db(tmp_path)
    session = focus_mod.start_session("Deep work", 60, block_level=block_level,
                                      db_path=db, now=now)
    return db, session


def test_enforce_blocked_records_and_notifies_once(tmp_path):
    now = datetime(2026, 9, 25, 10, 0, 0)
    db, session = _session_fixture(tmp_path, now)
    client = FakeClient(
        window_events=[_raw_event(now, "chrome", "YouTube")])
    notified, overlaid = [], []

    def fake_categorize(app, title, url):
        return ("Entertainment", -2, "domain:youtube.com")

    result = blocker_mod.enforce_once(
        session, client, fake_categorize,
        lambda a, t, label: notified.append((a, label)),
        lambda label, app, sid, db_path=None: overlaid.append(app),
        db_path=db, now=now, last_notified={})
    assert result["action"] == "blocked"
    assert result["notified"] is True
    assert result["score"] == -2
    assert store.count_blocks(session["id"], path=db) == 1
    assert notified == [("chrome", "Deep work")]
    assert overlaid == ["chrome"]


def test_enforce_dedupes_within_a_minute(tmp_path):
    now = datetime(2026, 9, 25, 10, 0, 0)
    db, session = _session_fixture(tmp_path, now)
    notified = []

    def fake_categorize(app, title, url):
        return ("Entertainment", -2, "domain:youtube.com")

    def poll_at(poll_now, seen):
        # The "current" window always starts at the poll time.
        client = FakeClient(
            window_events=[_raw_event(poll_now, "chrome", "YouTube")])
        return blocker_mod.enforce_once(
            session, client, fake_categorize,
            lambda a, t, label: notified.append(a),
            lambda *a, **k: None,
            db_path=db, now=poll_now, last_notified=seen)

    seen = {}
    r1 = poll_at(now, seen)
    r2 = poll_at(now + timedelta(seconds=30), seen)
    r3 = poll_at(now + timedelta(seconds=61), seen)
    assert (r1["notified"], r2["notified"], r3["notified"]) == \
        (True, False, True)
    assert notified == ["chrome", "chrome"]
    assert store.count_blocks(session["id"], path=db) == 2


def test_enforce_allowed_app(tmp_path):
    now = datetime(2026, 9, 25, 10, 0, 0)
    db, session = _session_fixture(tmp_path, now)
    client = FakeClient(window_events=[_raw_event(now, "code", "VS Code")])
    notified = []

    def fake_categorize(app, title, url):
        return ("Software Development", 2, "app_exact:code")

    result = blocker_mod.enforce_once(
        session, client, fake_categorize,
        lambda a, t, label: notified.append(a),
        lambda *a, **k: None,
        db_path=db, now=now, last_notified={})
    assert result["action"] == "allowed"
    assert result["score"] == 2
    assert notified == []
    assert store.count_blocks(session["id"], path=db) == 0


def test_enforce_stale_window_does_nothing(tmp_path):
    now = datetime(2026, 9, 25, 10, 0, 0)
    db, session = _session_fixture(tmp_path, now)
    stale = now - timedelta(seconds=120)
    client = FakeClient(window_events=[_raw_event(stale, "chrome", "old")])

    def fake_categorize(app, title, url):
        raise AssertionError("must not categorize a stale window")

    result = blocker_mod.enforce_once(
        session, client, fake_categorize,
        lambda *a: None, lambda *a, **k: None,
        db_path=db, now=now, last_notified={})
    assert result["action"] == "none"
    assert result["reason"] == "no current window"


def test_enforce_no_active_session(tmp_path):
    now = datetime(2026, 9, 25, 10, 0, 0)
    db = _db(tmp_path)
    client = FakeClient(window_events=[_raw_event(now, "chrome", "x")])
    result = blocker_mod.enforce_once(
        None, client, _cat(-2),
        lambda *a: None, lambda *a, **k: None,
        db_path=db, now=now, last_notified={})
    assert result["action"] == "none"
    assert result["reason"] == "no active session"


def test_enforce_tracker_down_does_nothing(tmp_path):
    now = datetime(2026, 9, 25, 10, 0, 0)
    db, session = _session_fixture(tmp_path, now)
    client = FakeClient(broken=True)
    result = blocker_mod.enforce_once(
        session, client, _cat(-2),
        lambda *a: None, lambda *a, **k: None,
        db_path=db, now=now, last_notified={})
    assert result["action"] == "none"


def test_get_current_window_attaches_browser_url(tmp_path):
    now = datetime(2026, 9, 25, 10, 0, 0)
    client = FakeClient(
        window_events=[_raw_event(now, "chrome", "Some video")],
        web_events=[_raw_event(now, "chrome", "Some video",
                               url="https://www.youtube.com/watch?v=1")])
    window = blocker_mod.get_current_window(client, now=now)
    assert window["app"] == "chrome"
    assert window["url"] == "https://www.youtube.com/watch?v=1"


def test_overlay_fails_silently_without_display(monkeypatch):
    # Simulate tkinter missing entirely.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tkinter":
            raise ImportError("no tkinter")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # Must not raise.
    blocker_mod.show_block_overlay("label", "app", 1)


# --------------------------------- regression tests: bugs found on the PC ---

def _aware_event(day, hour, minute, minutes, app, score):
    """Event with an offset-aware timestamp, like real ActivityWatch data.

    Uses the machine's local timezone with an explicit offset (not UTC),
    so the test passes in any timezone: _to_naive() must convert it back
    to the same naive local wall-clock time.
    """
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute, tzinfo=local_tz)
    return {
        "ts": start.isoformat(),  # e.g. "2026-09-25T10:00:00+05:00"
        "duration": float(minutes * 60),
        "app": app,
        "title": "t",
        "url": None,
        "category": "X",
        "score": score,
        "override_score": None,
        "match_key": "app:" + app,
    }


def test_summary_mixes_naive_session_with_aware_events(tmp_path):
    # Sessions are stored with naive local timestamps, but ActivityWatch
    # event timestamps are offset-aware UTC. Comparing the two used to
    # raise "can't compare offset-naive and offset-aware datetimes".
    db = _db(tmp_path)
    day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 10, 0, 0)  # naive local, like the real code
    focus_mod.start_session("Mixed tz", 60, db_path=db, now=now)
    store.save_events(
        "2026-09-25",
        [_aware_event(day, 10, 0, 30, "code", 2),
         _aware_event(day, 10, 30, 30, "tube", -2)],
        path=db,
    )
    ended = focus_mod.end_session(db_path=db,
                                  now=now + timedelta(minutes=60))
    summary = ended["summary"]
    assert summary["focus_minutes"] == pytest.approx(30.0)
    assert summary["distracting_minutes"] == pytest.approx(30.0)
    assert summary["pulse"] == pytest.approx(50.0)


def test_focus_page_renders_with_active_session(tmp_path, monkeypatch):
    # The active-session template once had a stray "%s" (7 specifiers,
    # 6 values) and crashed /focus with "not enough arguments for format
    # string" whenever a session was running.
    pytest.importorskip("flask")
    db = _db(tmp_path)
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    import dashboard.app as dash_app

    now = datetime(2026, 9, 25, 10, 0, 0)
    session = focus_mod.start_session("Deep work", 50, db_path=db, now=now)
    assert "error" not in session

    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    resp = client.get("/focus")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Deep work" in html
    # Living Instrument (slice 7): the in-session orb replaced the
    # legacy SVG progress ring.
    assert "data-lv-orb" in html


# ----------------- regression tests: PC blocking bugs (Phase 3 fix) ---

def _raw_event_with_duration(now, app, title, duration_seconds):
    return {"timestamp": now.isoformat(),
            "duration": duration_seconds,
            "data": {"app": app, "title": title, "url": None}}


def test_current_window_counts_ongoing_long_event(tmp_path):
    # A window focused for minutes is ONE long event. It is still current
    # even though it started more than 60s ago -- the old code measured
    # freshness from the event start and wrongly discarded it as stale.
    now = datetime(2026, 9, 25, 10, 0, 0)
    started = now - timedelta(seconds=90)
    client = FakeClient(window_events=[
        _raw_event_with_duration(started, "chrome", "YouTube", 300)])
    window = blocker_mod.get_current_window(client, now=now)
    assert window is not None
    assert window["app"] == "chrome"


def test_current_window_truly_stale_event(tmp_path):
    # An event that ENDED 80s ago is genuinely stale (AFK / tracker down).
    now = datetime(2026, 9, 25, 10, 0, 0)
    started = now - timedelta(seconds=90)
    client = FakeClient(window_events=[
        _raw_event_with_duration(started, "chrome", "old tab", 10)])
    assert blocker_mod.get_current_window(client, now=now) is None


def test_current_window_sends_aware_query_bounds(tmp_path):
    # ActivityWatch interprets a naive ISO bound as UTC, shifting the
    # query window on non-UTC machines. Bounds must carry an offset.
    seen = {}

    class RecordingClient(FakeClient):
        def get_events(self, bucket_id, start_iso, end_iso):
            seen["start"] = start_iso
            seen["end"] = end_iso
            return super().get_events(bucket_id, start_iso, end_iso)

    now = datetime(2026, 9, 25, 10, 0, 0)  # naive local, like real callers
    client = RecordingClient(
        window_events=[_raw_event(now, "chrome", "x")])
    assert blocker_mod.get_current_window(client, now=now) is not None
    for bound in (seen["start"], seen["end"]):
        assert "+" in bound or bound.endswith("Z"), \
            "query bound %r carries no UTC offset" % bound
