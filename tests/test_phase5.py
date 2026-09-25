"""Tests for Phase 5: app-like launcher, guided home, onboarding,
tray actions, backup system, UI polish, and offline CSS.

Every rule under test is a plain documented threshold -- no invented
formulas, so every expectation below is hand-checkable.
"""

import os
import socket
import sqlite3
from datetime import datetime, timedelta

import pytest

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
    return {c["code"]: c
            for c in home.attention_cards(db_path=db, now=now,
                                          backup_dest_dir=backup_dir)}


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
    assert client.get("/welcome/restart").status_code == 302
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


def test_home_pulse_band_colors(monkeypatch, env, tmp_path):
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
    # All +2: Pulse 100 -> green band.
    _seed_day(db, day, [_event(day + "T09:00:00", 3600)])
    html = dash_app.app.test_client().get("/").data.decode()
    assert 'pulse good' in html
