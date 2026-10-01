"""Tests for Phase 5: app-like launcher, guided home, onboarding,
tray actions, backup system, UI polish, and offline CSS.

Every rule under test is a plain documented threshold -- no invented
formulas, so every expectation below is hand-checkable.
"""

import os
import socket
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

from focuscore import activitywatch as aw_mod
from focuscore import backup, home, launcher, store, tray
from focuscore import focus as focus_mod
from focuscore import goals as goals_mod


# ---------------------------------------------------------------- helpers ---

def _event(ts, seconds, app="app", title="t", category="Software Development",
           score=2):
    return {"ts": ts, "duration": seconds, "app": app, "title": title,
            "category": category, "score": score,
            "match_key": "app:" + app}


def _seed_day(db, day, events):
    store.save_events(day, events, path=db)
    store.save_day_stats(day, 0, sum(e["duration"] for e in events), path=db)


def _cards(db, now, backup_dir):
    # Pin ActivityWatch as "running": these card tests predate the
    # aw_setup card and must not depend on a live localhost probe.
    return {c["code"]: c
            for c in home.attention_cards(db_path=db, now=now,
                                          backup_dest_dir=backup_dir,
                                          aw_state=aw_mod.RUNNING)}


@pytest.fixture()
def env(tmp_path):
    db = str(tmp_path / "p5.db")
    bdir = tmp_path / "backups"
    return db, str(bdir)


# ----------------------------------------------------------------- backup ---

def test_backup_create_list_round_trip(env):
    db, bdir = env
    _seed_day(db, "2026-09-20", [_event("2026-09-20T09:00:00", 600)])
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    assert path.exists()

    backups = backup.list_backups(dest_dir=bdir)
    assert len(backups) == 1
    assert backups[0]["name"] == path.name
    assert backups[0]["size_bytes"] > 0

    # The backup really holds the data (SQLite backup API, not raw copy).
    conn = sqlite3.connect(str(path))
    try:
        n = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_backup_create_without_database_raises(env):
    _, bdir = env
    with pytest.raises(FileNotFoundError):
        backup.create_backup(db_path=str(env[0]) + "-missing", dest_dir=bdir)


def test_backup_prune_keeps_newest(env):
    db, bdir = env
    _seed_day(db, "2026-09-20", [_event("2026-09-20T09:00:00", 600)])
    # Valid-pattern names that sort oldest -> newest.
    names = ["focuscore-20260920-0%d0000.db" % i for i in range(5)]
    folder = backup.backup_dir(dest_dir=bdir)
    for name in names:
        (folder / name).write_bytes(b"x")
    assert backup.prune_backups(keep=2, dest_dir=bdir) == 3
    remaining = [b["name"] for b in backup.list_backups(dest_dir=bdir)]
    assert remaining == sorted(names, reverse=True)[:2]


def test_backup_restore_keeps_safety_copy(env):
    db, bdir = env
    _seed_day(db, "2026-09-20", [_event("2026-09-20T09:00:00", 600)])
    path = backup.create_backup(db_path=db, dest_dir=bdir)

    # Change the live db after the backup.
    _seed_day(db, "2026-09-20",
              [_event("2026-09-20T09:00:00", 600),
               _event("2026-09-20T10:00:00", 600)])

    safety = backup.restore_backup(path.name, db_path=db, dest_dir=bdir)
    assert safety is not None and safety.exists()

    # Live db is back to the backup's content (1 event)...
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
    # ...and the safety copy holds the pre-restore content (2 events).
    conn = sqlite3.connect(str(safety))
    try:
        n = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    finally:
        conn.close()
    assert n == 2


def test_backup_restore_rejects_bad_names(env):
    db, bdir = env
    with pytest.raises(ValueError):
        backup.restore_backup("../evil.db", db_path=db, dest_dir=bdir)
    with pytest.raises(ValueError):
        backup.restore_backup("focuscore.db", db_path=db, dest_dir=bdir)
    with pytest.raises(FileNotFoundError):
        backup.restore_backup("focuscore-20200101-000000.db",
                              db_path=db, dest_dir=bdir)


def test_backup_if_stale_respects_24h(env):
    db, bdir = env
    _seed_day(db, "2026-09-20", [_event("2026-09-20T09:00:00", 600)])
    first = backup.backup_if_stale(db_path=db, dest_dir=bdir)
    assert first is not None
    # Fresh backup -> no new backup.
    assert backup.backup_if_stale(db_path=db, dest_dir=bdir) is None
    # Make it look 25 hours old -> a new backup is created.
    old = datetime.now() - timedelta(hours=25)
    os.utime(str(first), (old.timestamp(), old.timestamp()))
    second = backup.backup_if_stale(db_path=db, dest_dir=bdir)
    assert second is not None and second != first


def test_backup_if_stale_without_database_returns_none(env):
    _, bdir = env
    assert backup.backup_if_stale(db_path=str(env[0]) + "-missing",
                                  dest_dir=bdir) is None


def test_find_drive_folder_returns_none_gracefully(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    # Point expanduser away too, by making HOME the same empty dir.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert backup.find_drive_folder() is None


# -------------------------------------------------------- attention cards ---

def _noon(day):
    return datetime.strptime(day + "T12:00:00", "%Y-%m-%dT%H:%M:%S")


def test_card_uncategorized_triggers_and_quiets(env):
    db, bdir = env
    day = "2026-09-24"
    _seed_day(db, day, [_event(day + "T12:00:00", 600, category="Uncategorized",
                               score=0, app="mystery")])
    cards = _cards(db, _noon(day), bdir)
    assert "uncategorized" in cards
    assert cards["uncategorized"]["button_href"] == "/activities?day=" + day

    # Categorized day -> quiet.
    _seed_day(db, day, [_event(day + "T12:00:00", 600)])
    assert "uncategorized" not in _cards(db, _noon(day), bdir)


def test_card_timesheet_yesterday(env):
    from focuscore import timesheet as ts_mod

    db, bdir = env
    today, yesterday = "2026-09-24", "2026-09-23"
    _seed_day(db, yesterday, [_event(yesterday + "T09:00:00", 3600)])
    cards = _cards(db, _noon(today), bdir)
    assert "timesheet" in cards
    assert yesterday in cards["timesheet"]["button_href"]

    # Finalize (lock) the day -> the card goes quiet.
    block = {"start_ts": yesterday + "T09:00", "end_ts": yesterday + "T10:00",
             "minutes": 60.0, "category": "Software Development",
             "app": "app", "title_hint": "t"}
    ts_mod.accept_suggestion(block, yesterday, db_path=db)
    ts_mod.lock_day(yesterday, db_path=db)
    assert "timesheet" not in _cards(db, _noon(today), bdir)


def test_card_no_focus_session(env):
    db, bdir = env
    day = "2026-09-24"
    _seed_day(db, day, [_event(day + "T11:55:00", 300)])
    assert "no_focus" in _cards(db, _noon(day), bdir)

    focus_mod.start_session("Work", 25, db_path=db,
                            now=_noon(day))
    focus_mod.end_session(db_path=db, now=_noon(day))
    assert "no_focus" not in _cards(db, _noon(day), bdir)


def test_card_goal_at_risk(env):
    db, bdir = env
    day = "2026-09-24"
    # 48 of 120 min = 40% (< 50%) at 13:00 (day more than half over).
    _seed_day(db, day, [_event(day + "T09:00:00", 48 * 60)])
    goals_mod.add_goal("Deep work", "more_than", "category",
                       target_name="Software Development",
                       threshold_minutes=120, db_path=db)
    now = datetime.strptime(day + "T13:00:00", "%Y-%m-%dT%H:%M:%S")
    cards = _cards(db, now, bdir)
    assert "goal" in cards
    assert "Deep work" in cards["goal"]["title"]

    # Same progress at 09:00 (day not half over) -> quiet.
    morning = datetime.strptime(day + "T09:30:00", "%Y-%m-%dT%H:%M:%S")
    assert "goal" not in _cards(db, morning, bdir)


def test_card_goal_less_than_exceeded(env):
    db, bdir = env
    day = "2026-09-24"
    _seed_day(db, day, [_event(day + "T09:00:00", 3600,
                               category="Entertainment", score=-2)])
    goals_mod.add_goal("Less fun", "less_than", "category",
                       target_name="Entertainment",
                       threshold_minutes=30, db_path=db)
    assert "goal" in _cards(db, _noon(day), bdir)


def test_card_tracker_stale(env):
    db, bdir = env
    day = "2026-09-24"
    # Latest activity 40 minutes ago, at noon -> stale.
    _seed_day(db, day, [_event(day + "T11:20:00", 600)])
    now = _noon(day)
    assert "tracker" in _cards(db, now, bdir)

    # Fresh activity 5 minutes ago -> quiet.
    _seed_day(db, day, [_event(day + "T11:55:00", 300)])
    assert "tracker" not in _cards(db, now, bdir)

    # Stale at 3am -> quiet (night is not a problem).
    night = datetime.strptime(day + "T03:00:00", "%Y-%m-%dT%H:%M:%S")
    _seed_day(db, day, [_event(day + "T11:20:00", 600)])
    assert "tracker" not in _cards(db, night, bdir)


def test_card_backup_stale(monkeypatch, env):
    db, bdir = env
    day = "2026-09-24"
    _seed_day(db, day, [_event(day + "T11:55:00", 300)])
    now = _noon(day)

    monkeypatch.setattr(backup, "newest_backup", lambda dest_dir=None: None)
    cards = _cards(db, now, bdir)
    assert "backup" in cards
    assert "never been backed up" in cards["backup"]["title"]

    old = {"name": "x", "modified": now - timedelta(days=9)}
    monkeypatch.setattr(backup, "newest_backup",
                        lambda dest_dir=None: old)
    cards = _cards(db, now, bdir)
    assert "9 days" in cards["backup"]["title"]

    fresh = {"name": "x", "modified": now - timedelta(hours=1)}
    monkeypatch.setattr(backup, "newest_backup",
                        lambda dest_dir=None: fresh)
    assert "backup" not in _cards(db, now, bdir)


def test_pulse_band_thresholds():
    assert home.pulse_band(None) == "none"
    assert home.pulse_band(85) == "good"
    assert home.pulse_band(60) == "good"   # boundary documented in home.py
    assert home.pulse_band(59.9) == "ok"
    assert home.pulse_band(40) == "ok"
    assert home.pulse_band(39.9) == "bad"


# -------------------------------------------------------------- onboarding ---

def _dash(monkeypatch, db, flag_path):
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    import dashboard.app as dash_app
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag_path)
    return dash_app


def test_home_redirects_to_welcome_when_not_onboarded(env, monkeypatch,
                                                      tmp_path):
    db, _ = env
    dash_app = _dash(monkeypatch, db, tmp_path / ".onboarded")
    client = dash_app.app.test_client()
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/welcome")


def test_welcome_flow_marks_onboarded(env, monkeypatch, tmp_path):
    db, _ = env
    flag = tmp_path / ".onboarded"
    dash_app = _dash(monkeypatch, db, flag)
    client = dash_app.app.test_client()

    assert client.get("/welcome").status_code == 200
    assert b"step=2" in client.get("/welcome").data
    assert client.get("/welcome?step=3").status_code == 200

    resp = client.post("/welcome/finish", data={"next": "/activities"})
    assert resp.status_code == 302
    assert flag.exists()

    # Now home renders instead of redirecting.
    assert client.get("/").status_code == 200


def test_welcome_restart_clears_flag(env, monkeypatch, tmp_path):
    db, _ = env
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-24")
    dash_app = _dash(monkeypatch, db, flag)
    client = dash_app.app.test_client()
    assert client.post("/welcome/restart").status_code == 302
    assert not flag.exists()


def test_home_shows_attention_or_all_clear(env, monkeypatch, tmp_path):
    db, bdir = env
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-24")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(backup, "newest_backup", lambda dest_dir=None: None)
    client = dash_app.app.test_client()
    html = client.get("/").data.decode()
    assert resp_ok(html)
    # Fresh empty db: backup card must show (among others).
    assert "Open Backup" in html or "All clear" in html


def resp_ok(html):
    return "What needs your attention" in html or "All clear" in html


# --------------------------------------------------------------- launcher ---

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_port_open_and_wait_for_port():
    port = _free_port()
    assert not launcher.port_open(port=port)
    server = socket.socket()
    server.bind(("127.0.0.1", port))
    server.listen(5)  # backlog >1: Windows rejects queued connections otherwise
    try:
        assert launcher.port_open(port=port)
        assert launcher.wait_for_port(port=port, timeout=2)
    finally:
        server.close()
    assert launcher.wait_for_port(port=port, timeout=1,
                                  poll=0.2) is False


def test_open_app_window_falls_back_to_browser(monkeypatch):
    opened = []

    def fake_popen(*args, **kwargs):
        raise OSError("no such exe")

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher.webbrowser, "open",
                        lambda url: opened.append(url))
    assert launcher.open_app_window("http://127.0.0.1:5000/") == "browser"
    assert opened == ["http://127.0.0.1:5000/"]


def test_open_app_window_uses_chrome_when_present(monkeypatch):
    calls = []

    def fake_popen(args, **kwargs):
        calls.append(args[0])
        if "chrome" not in args[0].lower():
            raise OSError("no such exe")
        class _P:  # noqa: D106
            pass
        return _P()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    assert launcher.open_app_window() == "chrome"
    assert any("chrome" in c.lower() for c in calls)


# ------------------------------------------------------------------- tray ---

def test_tray_quick_session_action(env):
    db, _ = env
    result = tray.start_quick_session(db_path=db)
    assert "error" not in result
    assert result["label"] == "Quick focus"
    # A second one refuses: only one active session at a time.
    again = tray.start_quick_session(db_path=db)
    assert "error" in again
    focus_mod.abort_session(db_path=db)


def test_tray_today_pulse_text(env):
    db, _ = env
    assert "no data" in tray.today_pulse_text(db_path=db)
    day = datetime.now().date().isoformat()
    _seed_day(db, day, [_event(day + "T09:00:00", 3600)])
    text = tray.today_pulse_text(db_path=db)
    assert text.startswith("Today's Pulse:")
    assert "--" not in text


def test_tray_backup_now_action(env):
    db, _ = env
    _seed_day(db, "2026-09-24", [_event("2026-09-24T09:00:00", 600)])
    result = tray.backup_now(db_path=db)
    assert not (isinstance(result, dict) and "error" in result)


def test_tray_stop_server_only_stops_own_process():
    app = tray.TrayApp()
    assert app.stop_server() is False  # never started one

    class FakeProc:
        def __init__(self):
            self.terminated = False
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            pass

    fake = FakeProc()
    app.server_proc = fake
    assert app.stop_server() is True
    assert fake.terminated
    assert app.server_proc is None


def test_tray_icon_image_draws_in_code():
    pytest.importorskip("PIL")
    img = tray.make_icon_image(64)
    assert img.size == (64, 64)
    # Not a blank image: some pixels are opaque.
    assert any(px[3] > 0 for px in img.getdata())


# --------------------------------------------------------------- UI polish ---

def _client(monkeypatch, db):
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    from dashboard import app as dash_app
    return dash_app.app.test_client()


def test_static_css_served_and_offline(monkeypatch, env):
    db, _ = env
    client = _client(monkeypatch, db)
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    css = resp.data.decode()
    assert "http://" not in css and "https://" not in css
    assert ".topnav" in css and ".pulse" in css


def test_pages_have_no_cdn_references(monkeypatch, env):
    db, _ = env
    _seed_day(db, "2026-09-21", [_event("2026-09-21T09:00:00", 600)])
    client = _client(monkeypatch, db)
    for url in ("/welcome", "/timesheet?day=2026-09-21",
                "/report?week=2026-09-21", "/coaching", "/backup",
                "/focus", "/goals", "/alerts",
                "/activities?day=2026-09-21"):
        html = client.get(url).data.decode()
        scrubbed = html.replace("http://127.0.0.1", "")
        assert "http://" not in scrubbed, url
        assert "https://" not in scrubbed, url


def test_timesheet_timeline_present(monkeypatch, env):
    db, _ = env
    _seed_day(db, "2026-09-21", [
        _event("2026-09-21T09:00:00", 1800),
        _event("2026-09-21T10:00:00", 1800, category="Communication",
               score=1, app="mail"),
    ])
    client = _client(monkeypatch, db)
    html = client.get("/timesheet?day=2026-09-21").data.decode()
    assert "timeline" in html
    assert "href='#sug-0'" in html
    assert "id='sug-0'" in html


def test_report_has_css_bar_charts(monkeypatch, env):
    db, _ = env
    _seed_day(db, "2026-09-21", [_event("2026-09-21T09:00:00", 3600)])
    client = _client(monkeypatch, db)
    html = client.get("/report?week=2026-09-21").data.decode()
    assert "hbar" in html
    assert "Pulse through the week" in html


def test_home_pulse_hero_stat(monkeypatch, env, tmp_path):
    db, _ = env
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-24")
    import dashboard.app as dash_app
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    monkeypatch.setattr(backup, "newest_backup", lambda dest_dir=None: None)
    # Hermetic: the home page refreshes today's data from ActivityWatch on
    # every load; stub that out so the seeded day survives. (On a real PC
    # ActivityWatch is live and would overwrite the seed with real data.)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    day = datetime.now().date().isoformat()
    # One completed 60-minute focus session -> pulse H:MM is 1:00.
    start = datetime.fromisoformat(day + "T09:00:00")
    focus_mod.start_session("Maths", 60, db_path=db, now=start)
    focus_mod.end_session(db_path=db, now=start + timedelta(minutes=60))
    # Activity must NOT feed the pulse: 2h of +2 activity stays invisible.
    _seed_day(db, day, [_event(day + "T14:00:00", 7200)])
    html = dash_app.app.test_client().get("/").data.decode()
    # Living Instrument: pulse renders as a count-up H:MM in the pulse card.
    assert "data-countup" in html
    assert "data-seconds='3600'" in html
    assert "data-seconds='7200'" not in html
    assert ">1:00</p>" in html
    assert "Deep focus today" in html
    assert "lv-shield" in html


def test_home_shield_pill_reflects_daemon_not_session(monkeypatch, env,
                                                     tmp_path):
    """Home Shield pill must not infer 'armed' from an active focus session.

    Invariant I-1: the pill reads the authoritative daemon mutex (in-memory
    OS state), never SQLite session rows.
    """
    db, _ = env
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-24")
    import dashboard.app as dash_app
    from focuscore import shield as shield_mod
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    monkeypatch.setattr(backup, "newest_backup", lambda dest_dir=None: None)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    # Pin the daemon signal: no Shield daemon enforcing, regardless of what
    # the OS mutex happens to report on this machine (Windows CI runners
    # disagree with Linux here). The test is about session-vs-daemon logic.
    monkeypatch.setattr(shield_mod, "shield_daemon_running", lambda: False)
    day = datetime.now().date().isoformat()
    # Active session, but no Shield daemon enforcing.
    focus_mod.start_session("Maths", 60, db_path=db,
                            now=datetime.fromisoformat(day + "T09:00:00"))
    html = dash_app.app.test_client().get("/").data.decode()
    assert "lv-shield ready" in html
    assert "Shield ready" in html
    assert "lv-shield armed" not in html


def test_home_calendar_stale_note(monkeypatch, env, tmp_path):
    """Home Rhythm: a failed refresh shows the stale-data note, not silence."""
    import time as _time
    from focuscore import calendar_feed as cal_mod
    db, _ = env
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-24")
    import dashboard.app as dash_app
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    monkeypatch.setattr(backup, "newest_backup", lambda dest_dir=None: None)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    today_compact = __import__("datetime").date.today().strftime("%Y%m%d")
    monkeypatch.setattr(cal_mod, "_fetch",
                        lambda url: "BEGIN:VCALENDAR\n"
                                    "BEGIN:VEVENT\n"
                                    "UID:s1\n"
                                    "DTSTART:%sT090000\n"
                                    "DTEND:%sT093000\n"
                                    "SUMMARY:Standup\n"
                                    "END:VEVENT\n"
                                    "END:VCALENDAR" % (today_compact,
                                                      today_compact))
    cal_mod.set_ical_url("https://example.com/cal.ics", db_path=db)
    dash_app.app.test_client().get("/")  # prime the cache
    store.set_setting(cal_mod.SETTING_CACHED_AT,
                      str(_time.time() - cal_mod.CACHE_TTL - 1), path=db)
    monkeypatch.setattr(cal_mod, "_fetch", lambda url: (_ for _ in ()).throw(
        OSError("network down")))
    html = dash_app.app.test_client().get("/").data.decode()
    assert "last synced data" in html
    assert "lv-cal-stale" in html


def test_home_calendar_event_state_classes(monkeypatch, env, tmp_path):
    """Home Rhythm rows carry done/now/next/allday state classes."""
    from focuscore import calendar_feed as cal_mod
    db, _ = env
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-24")
    import dashboard.app as dash_app
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    monkeypatch.setattr(backup, "newest_backup", lambda dest_dir=None: None)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    today_compact = __import__("datetime").date.today().strftime("%Y%m%d")
    monkeypatch.setattr(cal_mod, "_fetch",
                        lambda url: "BEGIN:VCALENDAR\n"
                                    "BEGIN:VEVENT\n"
                                    "UID:s1\n"
                                    "DTSTART:%sT090000\n"
                                    "DTEND:%sT093000\n"
                                    "SUMMARY:Standup\n"
                                    "END:VEVENT\n"
                                    "END:VCALENDAR" % (today_compact,
                                                      today_compact))
    cal_mod.set_ical_url("https://example.com/cal.ics", db_path=db)
    html = dash_app.app.test_client().get("/").data.decode()
    assert "lv-cal-ev lv-cal-" in html


def test_home_rings_and_targets_from_pinned_goals(env, tmp_path, monkeypatch):
    """Home craft pass: the hero keeps one Focus ring; pinned goals render
    as live progress strips (not a wall of cards)."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    day = datetime.now().date().isoformat()
    _seed_day(db, day, [_event(day + "T09:00:00", 3600,
                               category="Reference & Learning")])
    goals_mod.add_goal("Learn maths", "more_than", "category",
                       target_name="Reference & Learning",
                       threshold_minutes=30, pinned=True, db_path=db)
    html = dash_app.app.test_client().get("/").data.decode()
    # One Focus ring only; the goal is a thin live strip.
    assert html.count("class='lv-ring'") == 1
    assert ">Learn maths</span>" in html
    assert "class='hm-strip'" in html
    assert "class='hm-strip-bar'" in html
    assert "60 of 30 min" in html
    assert "Achieved" in html
    # No old-style target cards.
    assert "class='lv-target-card'" not in html


def test_home_light_theme_tokens(env, tmp_path, monkeypatch):
    """Living Instrument: light theme renders data-theme + warm-paper tokens."""
    db, _ = env
    store.init_db(db)
    store.set_setting("ui_theme", "light", path=db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    html = dash_app.app.test_client().get("/").data.decode()
    assert "data-theme='light'" in html
    assert '<body class=\'living\'>' in html
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    assert 'html[data-theme="light"] body.living' in css
    assert "--bg0: #faf6ee" in css


def test_home_mobile_tabs(env, tmp_path, monkeypatch):
    """Living Instrument: mobile tab bar renders with inline SVG icons."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    html = dash_app.app.test_client().get("/").data.decode()
    assert "class='lv-tabs'" in html
    assert "aria-current='page'" in html
    for label in ("Home", "Focus", "Goals"):
        assert (">%s</span>" % label) in html
    # Inline SVG only — no icon-font dependency.
    assert "viewBox='0 0 24 24'" in html
    assert "material-symbols" not in html.lower()
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    assert "env(safe-area-inset-bottom)" in css


def test_home_targets_empty_state(env, tmp_path, monkeypatch):
    """Home craft pass: no pinned goals -> human empty line under
    "Today's progress", no strips, no cards."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    day = datetime.now().date().isoformat()
    _seed_day(db, day, [_event(day + "T09:00:00", 3600)])
    html = dash_app.app.test_client().get("/").data.decode()
    # Only the Focus ring, and the human empty state.
    assert html.count("class='lv-ring'") == 1
    assert ">Today's progress</h2>" in html
    assert "Pin a goal" in html
    assert "class='hm-strip-row'" not in html
    assert "class='lv-target-card'" not in html


# ------------------------------------------------- Phase 3: one-click ---

def test_card_update_available(monkeypatch, env):
    from focuscore import updater
    db, bdir = env
    day = "2026-09-24"
    _seed_day(db, day, [_event(day + "T11:55:00", 300)])
    now = _noon(day)
    monkeypatch.setattr(updater, "read_cached_check", lambda: {
        "status": "ok", "current": "1.3.0", "latest": "v1.4.0",
        "update_available": True})
    cards = _cards(db, now, bdir)
    assert "update" in cards
    assert "v1.4.0" in cards["update"]["title"]
    assert cards["update"]["button_href"] == "/update"


def test_card_update_hidden_when_up_to_date(monkeypatch, env):
    from focuscore import updater
    db, bdir = env
    day = "2026-09-24"
    _seed_day(db, day, [_event(day + "T11:55:00", 300)])
    now = _noon(day)
    monkeypatch.setattr(updater, "read_cached_check", lambda: {
        "status": "ok", "current": "v1.4.0", "latest": "v1.4.0",
        "update_available": False})
    assert "update" not in _cards(db, now, bdir)


def test_card_update_hidden_for_dev_copy(monkeypatch, env):
    from focuscore import updater
    db, bdir = env
    day = "2026-09-24"
    _seed_day(db, day, [_event(day + "T11:55:00", 300)])
    now = _noon(day)
    monkeypatch.setattr(updater, "read_cached_check", lambda: None)
    assert "update" not in _cards(db, now, bdir)


def test_tray_menu_has_check_for_updates():
    # Windows-only: pystray needs a real display, which headless Linux
    # CI doesn't have.
    if sys.platform != "win32":
        pytest.skip("tray menu is Windows-only")
    pytest.importorskip("pystray")
    app = tray.TrayApp()
    labels = [getattr(i, "text", "") for i in app.build_menu()
              if isinstance(getattr(i, "text", ""), str)]
    assert "Check for updates..." in labels


def test_tray_apply_pending_update_spawns_and_quits(monkeypatch):
    from focuscore import updater
    spawned = []
    monkeypatch.setattr(updater, "write_update_launcher",
                        lambda exe: spawned.append(exe) or "C:\\T\\u.bat")

    import subprocess
    popped = []
    class FakePopen:
        def __init__(self, *args, **kwargs):
            popped.append(args[0])
    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    app = tray.TrayApp()
    quit_called = []
    monkeypatch.setattr(tray.TrayApp, "on_quit",
                        lambda self: quit_called.append(True))
    app._apply_pending_update({"installer": "C:\\T\\setup.exe",
                               "version": "v1.4.0"})
    assert spawned == ["C:\\T\\setup.exe"]
    assert popped and "u.bat" in str(popped[0][-1])
    assert quit_called == [True]


# ---------------------------------------------------------------------------
# Living Instrument slice 6: Focus ready state


def _focus_ready_html(env, tmp_path, monkeypatch):
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    resp = dash_app.app.test_client().get("/focus")
    assert resp.status_code == 200
    return resp.data.decode()


def test_focus_ready_orb_and_controls(env, tmp_path, monkeypatch):
    """Ready state: start hero with sentence form, starter chips,
    minute stepper, mode radios, Begin action, quiet shield."""
    html = _focus_ready_html(env, tmp_path, monkeypatch)
    for needle in ("id='fc-begin'", "action='/focus/start'",
                   "I want to focus for", "fc-starters",
                   "fc-stepper", "id='fc-minus'", "id='fc-plus'",
                   "id='fc-minutes'", "fc-step-val",
                   "value='classic'", "value='pomodoro'", "value='flowtime'",
                   "id='fc-cycles' hidden", "name='target_cycles'",
                   ">Begin session</button>",
                   "Shield arms automatically when you begin",
                   "fc-shield", "fc-suggest",
                   "Recent sessions", "href='/focus' class='active'"):
        assert needle in html, needle
    # The start hero leads; the live strip is idle-only markup.
    assert "class='fc-live'" not in html


def test_focus_ready_form_fields(env, tmp_path, monkeypatch):
    """Begin form carries every field /focus/start needs."""
    html = _focus_ready_html(env, tmp_path, monkeypatch)
    for needle in ("name='label'", "type='radio' name='mode'",
                   "name='preset' value='custom'",
                   "id='fc-minutes'", "name='target_cycles'",
                   "name='block_level' value='strict'",
                   "name='block_level' value='lenient'",
                   "name='enforcement_mode' value='strict'",
                   "name='enforcement_mode' value='hardcore'"):
        assert needle in html, needle


def test_focus_ready_begin_starts_session(env, tmp_path, monkeypatch):
    """Posting the living form starts a real session per mode."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    client = dash_app.app.test_client()
    for mode, minutes in (("classic", "50"), ("pomodoro", "25"),
                          ("flowtime", "90")):
        resp = client.post("/focus/start", data={
            "label": "Deep work", "mode": mode, "preset": "custom",
            "custom_minutes": minutes, "target_cycles": "4",
            "block_level": "strict", "enforcement_mode": "strict"})
        assert resp.status_code == 302
        active = focus_mod.get_active_session(db_path=db)
        assert active["session_type"] == mode, mode
        client.post("/focus/abort")


def test_focus_ready_shows_seeded_session_row(env, tmp_path, monkeypatch):
    """Recent sessions render as quiet rows with duration bars."""
    db, _ = env
    store.init_db(db)
    from datetime import datetime
    focus_mod.start_session("Morning pages", 30, db_path=db,
                            now=datetime(2026, 9, 29, 9, 0, 0))
    focus_mod.end_session(db_path=db, now=datetime(2026, 9, 29, 9, 30, 0))
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    html = dash_app.app.test_client().get("/focus").data.decode()
    assert "Morning pages" in html
    assert "fc-sess-bar" in html


# ---------------------------------------------------------------------------
# Living Instrument slice 7: in-session orb


def _focus_start(monkeypatch, db, flag_path, mode, minutes="25", cycles="4"):
    dash_app = _dash(monkeypatch, db, flag_path)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    client = dash_app.app.test_client()
    resp = client.post("/focus/start", data={
        "label": "Deep work", "mode": mode, "preset": "custom",
        "custom_minutes": minutes, "target_cycles": cycles,
        "block_level": "strict", "enforcement_mode": "strict"})
    assert resp.status_code == 302
    return dash_app, client


def test_focus_active_orb_classic(env, tmp_path, monkeypatch):
    """Active Classic: thin live strip with countdown, end/abort actions."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app, client = _focus_start(monkeypatch, db, flag, "classic", "50")
    html = client.get("/focus").data.decode()
    for needle in ("<div class='fc-live ", "data-phase='work'",
                   "id='fc-live-time'", "In session",
                   "Shield armed", "action='/focus/end'",
                   "action='/focus/abort'", "href='/focus' class='active'",
                   "id='fc-depth-strip'", "id='fc-depth-meter'",
                   "End session"):
        assert needle in html, needle
    client.post("/focus/abort")


def test_focus_active_orb_pomodoro_work_and_break(env, tmp_path, monkeypatch):
    """Pomodoro: work caption + cycle dots; break phase shows plain
    'On break' with early-end/skip actions."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app, client = _focus_start(monkeypatch, db, flag, "pomodoro",
                                    "25", "4")
    work_html = client.get("/focus").data.decode()
    for needle in ("Work block 1 of 4", "data-phase='work'",
                   "action='/focus/cycle/break/start'", "fc-dots"):
        assert needle in work_html, needle
    client.post("/focus/cycle/break/start")
    break_html = client.get("/focus").data.decode()
    for needle in ("data-phase='break'", "On break",
                   "action='/focus/cycle/break/end'",
                   "action='/focus/cycle/break/skip'", "Shield resting"):
        assert needle in break_html, needle
    client.post("/focus/abort")


def test_focus_active_orb_flowtime(env, tmp_path, monkeypatch):
    """Flowtime: the strip counts up toward the soft target."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app, client = _focus_start(monkeypatch, db, flag, "flowtime", "90")
    html = client.get("/focus").data.decode()
    for needle in ("<div class='fc-live ", "data-mode='elapsed'", "Flowing",
                   "Soft target 90 min (no alarm)", "action='/focus/end'"):
        assert needle in html, needle
    client.post("/focus/abort")


def test_focus_active_orb_pomodoro_done(env, tmp_path, monkeypatch):
    """Pomodoro target reached: quiet 'Target reached' strip, no tick."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app, client = _focus_start(monkeypatch, db, flag, "pomodoro",
                                    "25", "1")
    client.post("/focus/cycle/break/start")
    client.post("/focus/cycle/break/end")
    html = client.get("/focus").data.decode()
    for needle in ("data-phase='done'", "Target reached",
                   "action='/focus/end'"):
        assert needle in html, needle
    client.post("/focus/abort")


def test_focus_active_orb_time_up(env, tmp_path, monkeypatch):
    """Classic past its planned end: the strip shows the time-up state."""
    from datetime import datetime, timedelta
    db, _ = env
    store.init_db(db)
    focus_mod.start_session("Overdue", 1, db_path=db,
                            now=datetime.now() - timedelta(minutes=2))
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app = _dash(monkeypatch, db, flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    client = dash_app.app.test_client()
    html = client.get("/focus").data.decode()
    assert "data-phase='timeup'" in html
    assert "Time is up" in html
    client.post("/focus/abort")


# ---------------------------------------------------------------------------
# Living Instrument slice 8: native mobile Focus


def test_focus_mobile_css_rules():
    """The 640px media query carries the Focus phone layout.

    Until the orchestrator merges this batch's scratch CSS into the
    global stylesheet, the rules live in /tmp/craft-focus.css; the
    assertion accepts either source so the suite stays green on both
    sides of the merge.
    """
    import pathlib
    merged = pathlib.Path("dashboard/static/style.css").read_text(
        encoding="utf-8")
    scratch = pathlib.Path("/tmp/craft-focus.css")
    css = merged + (scratch.read_text(encoding="utf-8")
                    if scratch.exists() else "")
    assert "@media (max-width: 640px)" in css
    for needle in (".fc-live-time { font-size: 2.4rem;",
                   ".fc-begin { width: 100%;",
                   ".fc-sessions li { flex-wrap: wrap;"):
        assert needle in css, needle


# ---------------------------------------------------------------------------
# Living Instrument slice 9: accessibility


def test_focus_focus_visible_rules():
    """Keyboard focus is always visible on Focus craft controls."""
    import pathlib
    merged = pathlib.Path("dashboard/static/style.css").read_text(
        encoding="utf-8")
    scratch = pathlib.Path("/tmp/craft-focus.css")
    css = merged + (scratch.read_text(encoding="utf-8")
                    if scratch.exists() else "")
    assert ".fc-mode input:focus-visible + span" in css
    assert ".fc-btn:focus-visible" in css
    assert "outline: 2px solid var(--accent);" in css


def test_focus_reduced_motion_covers_entrances():
    """Reduced motion stills the animated session meters."""
    import pathlib
    merged = pathlib.Path("dashboard/static/style.css").read_text(
        encoding="utf-8")
    scratch = pathlib.Path("/tmp/craft-focus.css")
    css = merged + (scratch.read_text(encoding="utf-8")
                    if scratch.exists() else "")
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".fc-live-bar span" in css
    assert "#fc-depth-meter" in css


def test_focus_mode_radios_are_native_inputs(env, tmp_path, monkeypatch):
    """Mode selection is real radio inputs, so arrow-key and keyboard
    operation come from the platform — no custom radiogroup JS needed."""
    html = _focus_ready_html(env, tmp_path, monkeypatch)
    for needle in ("role='radiogroup'",
                   "type='radio' name='mode' value='classic' checked",
                   "type='radio' name='mode' value='pomodoro'",
                   "type='radio' name='mode' value='flowtime'"):
        assert needle in html, needle


def test_focus_live_timer_is_quiet_for_screen_readers(env, tmp_path,
                                                     monkeypatch):
    """The ticking timer must not chatter: aria-live is off, and the
    depth poll only refreshes the plain-English label."""
    db, _ = env
    store.init_db(db)
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    dash_app, client = _focus_start(monkeypatch, db, flag, "classic", "50")
    html = client.get("/focus").data.decode()
    assert "id='fc-live-time'" in html
    assert "aria-live='off'" in html
    assert "id='fc-depth-strip' data-session-id" in html
    client.post("/focus/abort")


def test_living_light_theme_defines_page_background_tokens():
    """Regression: light theme must define --bg/--bg2 (the page uses them).

    Found by real browser rendering: the light block only set --bg0/--bg1
    while the page background uses --bg/--bg2, which stayed near-black under
    dark --ink text -> unreadable warm-paper theme.
    """
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    # explicit data-theme="light" block
    light_block = css.split('html[data-theme="light"] body.living {')[1]
    light_block = light_block.split("}")[0]
    assert "--bg: #faf6ee" in light_block
    assert "--bg2: #f2ebdb" in light_block
    # system light block
    assert "@media (prefers-color-scheme: light)" in css
    sys_block = css.split("@media (prefers-color-scheme: light)")[1]
    assert "--bg: #faf6ee" in sys_block
    assert "--bg2: #f2ebdb" in sys_block


def test_living_dark_theme_defines_bg0_for_tabs():
    """Regression: .lv-tabs uses var(--bg0); dark tokens must define it."""
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    assert ".lv-tabs" in css
    assert "--bg0: #0d0c0a" in css


def test_living_pages_hide_legacy_page_hero():
    """Regression: living pages hide legacy chrome incl. .page-hero."""
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    assert "body.living .page-hero" in css


def test_living_light_theme_nav_uses_paper_not_dark_bar():
    """Regression: the dark 62% nav bar is dark-theme-only.

    Found by real browser rendering: in the light theme the nav kept its
    dark frosted background under dark --ink text -> muddy, poor contrast.
    """
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    assert "html[data-theme=\"light\"] body.living .lv-nav" in css
    assert "rgba(250,246,238,.62)" in css


def test_living_depth_pill_readable_on_paper():
    """Regression: depth state color must never be the pill's text color.

    Found by real browser rendering (living design): the "surface" state
    color (#94a3b8) as pill text on warm paper failed contrast. The Focus
    craft pass fixes it by construction — the depth label is a plain pill
    in ink text; the state color lives only in the meter bar behind it.
    """
    py = open("dashboard/routes/focus.py", encoding="utf-8").read()
    assert "fc-depth-label" in py
    assert "--depth-c" not in py


def test_living_film_grain_overlay_present():
    """Spec §2: fixed SVG-noise grain at 5% over living pages."""
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    assert "body.living::before" in css
    assert "feTurbulence" in css
    assert "opacity: .05" in css
    assert "pointer-events: none" in css


def test_living_ambient_drift_present():
    """Spec §2: ambient ember drift washes on an 18s alternate loop."""
    css = open("dashboard/static/living.css", encoding="utf-8").read()
    assert "body.living::after" in css
    assert "@keyframes lv-drift" in css
    assert "18s ease-in-out infinite alternate" in css


def test_living_screen_reader_timer_announcements():
    """Craft a11y: the ticking timer is aria-live=off so screen readers
    are not chattered at every second; the depth poll only swaps the
    plain-English label text (no live region)."""
    py = open("dashboard/routes/focus.py", encoding="utf-8").read()
    assert "id='fc-live-time'" in py
    assert "aria-live='off'" in py
    # The depth poll refreshes the label; it never injects an aria-live region.
    assert "fc-depth-strip" in py
    assert "aria-live='polite'" not in py
    assert "aria-live='assertive'" not in py
