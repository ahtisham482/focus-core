"""Phase 11 (v1.13.0): Deep Focus & Sensory Gamification.

Tests depth gauge, XP engine, badges, chronotype, peak escalation
(I-1), tray toast idempotency, M9 migration, and new routes.
"""

from datetime import datetime, timedelta

from focuscore import chronotype, gamification as gami_mod
from focuscore import focus as focus_mod
from focuscore import migrations, shield, store


# ---------------------------------------------------------------- helpers ---

def _session(db, label="Test", minutes=50, started_ago_min=0, **kw):
    """Start a session, optionally backdating its start. Returns its id."""
    res = focus_mod.start_session(label, minutes, db_path=db, **kw)
    assert "error" not in res, res
    sid = res["id"]
    if started_ago_min:
        conn = store.get_db(db)
        start = (datetime.now() - timedelta(
            minutes=started_ago_min)).isoformat(timespec="seconds")
        conn.execute("UPDATE focus_sessions SET started_at = ? "
                     "WHERE id = ?", (start, sid))
        conn.commit()
        conn.close()
    return sid


def _end_session(db, sid, now=None):
    # Move the session start back so it has duration, then end.
    conn = store.get_db(db)
    ref = now or datetime.now()
    start = (ref - timedelta(
        minutes=60, seconds=5)).isoformat(timespec="seconds")
    conn.execute("UPDATE focus_sessions SET started_at = ? WHERE id = ?",
                 (start, sid))
    conn.commit()
    conn.close()
    return focus_mod.end_session(db_path=db, now=ref)


def _add_activity(db, day, ts, duration, app, score):
    conn = store.get_db(db)
    conn.execute(
        "INSERT INTO activities (ts, duration, app, title, url, category, "
        "score, override_score, match_key, day) VALUES (?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?)",
        (ts, duration, app, app, None, "Work" if score > 0 else "Distraction",
         score, None, app, day))
    conn.commit()
    conn.close()


# ------------------------------------------------------------- depth ---

def test_depth_flow_state(tmp_path):
    db = str(tmp_path / "d.db")
    sid = _session(db, started_ago_min=25)
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    # 20 min of +2 in one app, no switches, no distractions.
    base = now - timedelta(minutes=20)
    for i in range(4):
        ts = (base + timedelta(minutes=5 * i)).isoformat(timespec="seconds")
        _add_activity(db, day, ts, 300, "CodeEditor", 2)
    depth = focus_mod.depth_state(sid, db_path=db, now=now)
    assert depth["state"] == "flow"
    assert depth["dominant_score"] == 2
    assert depth["switches_15m"] == 0
    assert depth["uninterrupted_min"] >= 15


def test_depth_deep_work(tmp_path):
    db = str(tmp_path / "d.db")
    sid = _session(db, started_ago_min=20)
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    base = now - timedelta(minutes=14)
    _add_activity(db, day, base.isoformat(timespec="seconds"), 600,
                  "CodeEditor", 1)
    _add_activity(db, day, (base + timedelta(minutes=10)).isoformat(
        timespec="seconds"), 240, "Docs", 2)
    depth = focus_mod.depth_state(sid, db_path=db, now=now)
    assert depth["state"] == "deep"


def test_depth_surface_warmup(tmp_path):
    db = str(tmp_path / "d.db")
    sid = _session(db)
    # Fresh session, no activity -> surface.
    depth = focus_mod.depth_state(sid, db_path=db)
    assert depth["state"] == "surface"


def test_depth_surface_high_switching(tmp_path):
    db = str(tmp_path / "d.db")
    sid = _session(db, started_ago_min=20)
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    base = now - timedelta(minutes=14)
    for i in range(8):
        ts = (base + timedelta(minutes=i)).isoformat(timespec="seconds")
        _add_activity(db, day, ts, 60, "App%d" % i, 1)
    depth = focus_mod.depth_state(sid, db_path=db, now=now)
    assert depth["switches_15m"] >= 7
    assert depth["state"] == "surface"


# ------------------------------------------------------------------ XP ---

def test_xp_base_and_peak(tmp_path):
    db = str(tmp_path / "x.db")
    # Peak window covers now.
    now = datetime.now()
    chronotype.set_window(
        (now - timedelta(hours=1)).strftime("%H:%M"),
        (now + timedelta(hours=1)).strftime("%H:%M"), db_path=db)
    sid = _session(db, minutes=60)
    # 60 focus minutes of +2 activity inside the session window.
    day = now.strftime("%Y-%m-%d")
    start = now - timedelta(minutes=60)
    for i in range(6):
        ts = (start + timedelta(minutes=10 * i)).isoformat(
            timespec="seconds")
        _add_activity(db, day, ts, 600, "CodeEditor", 2)
    result = _end_session(db, sid, now=now)
    xp = result["xp"]
    assert xp["awarded"] is True
    assert xp["base_xp"] == 60
    assert xp["peak"] is True
    assert xp["peak_bonus"] == 30  # 60 // 2
    assert xp["total_xp"] >= 90
    # Integer-only in the ledger.
    conn = store.get_db(db)
    row = conn.execute(
        "SELECT base_xp, peak_bonus, clean_bonus, streak_mult_x10, "
        "total_xp FROM xp_ledger WHERE session_id = ?", (sid,)).fetchone()
    conn.close()
    assert all(isinstance(v, int) for v in row)


def test_xp_clean_run_bonus(tmp_path):
    db = str(tmp_path / "x.db")
    chronotype.set_window("00:00", "00:01", db_path=db)  # peak off
    sid = _session(db, minutes=60)
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    start = now - timedelta(minutes=60)
    for i in range(6):
        ts = (start + timedelta(minutes=10 * i)).isoformat(
            timespec="seconds")
        _add_activity(db, day, ts, 600, "CodeEditor", 2)
    result = _end_session(db, sid)
    xp = result["xp"]
    assert xp["clean"] is True  # zero blocks
    assert xp["clean_bonus"] == xp["base_xp"] // 4


def test_xp_streak_multiplier_cap(tmp_path):
    db = str(tmp_path / "x.db")
    store.init_db(db)
    # Simulate a 15-day streak by backfilling completed sessions.
    conn = store.get_db(db)
    for d in range(15):
        day = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO focus_sessions (label, started_at, ended_at, "
            "planned_minutes, planned_end_at, status) "
            "VALUES (?, ?, ?, 25, ?, 'completed')",
            ("backfill", day + "T09:00:00", day + "T09:25:00",
             day + "T09:25:00"))
    conn.commit()
    conn.close()
    assert focus_mod.current_streak(db_path=db) >= 15
    mult = gami_mod._streak_mult_x10(db_path=db)
    assert mult == 20  # capped at 2.0x


def test_xp_idempotent(tmp_path):
    db = str(tmp_path / "x.db")
    sid = _session(db)
    _end_session(db, sid)
    first = gami_mod.award_session_xp(sid, db_path=db)
    assert first.get("awarded") is False  # already awarded by end_session


# --------------------------------------------------------------- badges ---

def test_badge_iron_will(tmp_path):
    db = str(tmp_path / "b.db")
    sid = _session(db, enforcement_mode="hardcore")
    _seed_focus_minutes(db)
    result = _end_session(db, sid)
    assert "iron_will" in result["xp"].get("new_badges", [])


def test_badge_peak_master(tmp_path):
    db = str(tmp_path / "b.db")
    now = datetime.now()
    chronotype.set_window(
        (now - timedelta(hours=2)).strftime("%H:%M"),
        (now + timedelta(hours=2)).strftime("%H:%M"), db_path=db)
    for _ in range(10):
        sid = _session(db)
        conn = store.get_db(db)
        start = (now - timedelta(minutes=30)).isoformat(timespec="seconds")
        end = now.isoformat(timespec="seconds")
        conn.execute(
            "UPDATE focus_sessions SET started_at = ?, ended_at = ?, "
            "status = 'completed' WHERE id = ?", (start, end, sid))
        conn.commit()
        conn.close()
    assert gami_mod.peak_session_count(db_path=db) >= 10
    sid = _session(db)
    _seed_focus_minutes(db)
    result = _end_session(db, sid)
    assert "peak_master" in result["xp"].get("new_badges", [])


# ---------------------------------------------------------- daily ring ---

def test_daily_ring_minutes(tmp_path):
    db = str(tmp_path / "r.db")
    store.init_db(db)
    day = datetime.now().strftime("%Y-%m-%d")
    ring = gami_mod.daily_ring(day, db_path=db)
    assert ring["minutes"] == 0
    assert ring["target"] == 120  # default
    assert ring["fraction"] == 0.0
    gami_mod.set_daily_target_minutes(60, db_path=db)
    ring = gami_mod.daily_ring(day, db_path=db)
    assert ring["target"] == 60


# ----------------------------------------------------------- chronotype ---

def test_chronotype_peak_boundaries(tmp_path):
    db = str(tmp_path / "c.db")
    chronotype.set_window("09:00", "11:30", db_path=db)
    base = datetime.now().replace(hour=0, minute=0, second=0,
                                  microsecond=0)
    assert chronotype.is_peak_now(base.replace(hour=8, minute=59),
                                  db_path=db) is False
    assert chronotype.is_peak_now(base.replace(hour=9, minute=0),
                                  db_path=db) is True
    assert chronotype.is_peak_now(base.replace(hour=11, minute=30),
                                  db_path=db) is True
    assert chronotype.is_peak_now(base.replace(hour=11, minute=31),
                                  db_path=db) is False


def test_peak_status_before(tmp_path):
    db = str(tmp_path / "c.db")
    chronotype.set_window("09:00", "11:30", db_path=db)
    base = datetime.now().replace(hour=8, minute=57, second=0,
                                  microsecond=0)
    status = chronotype.peak_status(base, db_path=db)
    assert status["state"] == "before"
    assert status["minutes"] == 3


# ------------------------------------------------- I-1 peak escalation ---

def test_peak_escalation_in_snapshot_zero_sqlite(tmp_path, monkeypatch):
    """Refresher bakes force_hardcore; worker still does zero SQLite."""
    db = str(tmp_path / "p.db")
    now = datetime.now()
    chronotype.set_window(
        (now - timedelta(hours=1)).strftime("%H:%M"),
        (now + timedelta(hours=1)).strftime("%H:%M"), db_path=db)
    snap = shield._build_snapshot(db)
    assert snap.force_hardcore is True

    # Poison SQLite: the worker must not touch it.
    def _boom(*a, **k):
        raise AssertionError("SQLite touched on enforcement path!")
    monkeypatch.setattr(store, "get_db", _boom)

    import queue as q
    telemetry_q = q.Queue()
    result = shield.shield_once(
        {}, None, lambda *a: "uncategorized",
        db_path=db, fg_info=None, snapshot=snap,
        telemetry_q=telemetry_q)
    assert result["action"] in ("allow", "none", "soft", "firm",
                                "hardcore")


def test_no_peak_no_escalation(tmp_path):
    db = str(tmp_path / "p.db")
    chronotype.set_window("00:00", "00:01", db_path=db)
    snap = shield._build_snapshot(db)
    assert snap.force_hardcore is False


# ------------------------------------------------------------ migration ---

def test_migration_m9_idempotent(tmp_path):
    db = str(tmp_path / "m.db")
    store.init_db(db)
    conn = store.get_db(db)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert ver == migrations.LATEST_VERSION == 9
    # Re-run is safe.
    store.init_db(db)
    conn = store.get_db(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "xp_ledger" in tables
    assert "badges" in tables


# --------------------------------------------------------------- routes ---

def test_focus_routes_200(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    client = dash_app.app.test_client()
    assert client.get("/focus").status_code == 200
    # Depth endpoint needs a session_id.
    resp = client.get("/focus/depth")
    assert resp.status_code == 400  # missing session_id
    # Peak launch card visible during peak.
    now = datetime.now()
    chronotype.set_window(
        (now - timedelta(hours=1)).strftime("%H:%M"),
        (now + timedelta(hours=1)).strftime("%H:%M"),
        db_path=db)
    html = client.get("/focus").data.decode()
    assert "Peak energy window" in html
    assert "Today's focus ring" in html
    # Soundscape controls appear on the active session page.
    _session(db)
    html = client.get("/focus").data.decode()
    assert "soundscapes.js" in html
    # Living Instrument (slice 7): the in-session orb replaced the
    # legacy SVG progress ring.
    assert "data-lv-orb" in html
    assert "fc-depth-pill" in html
    assert "zen-mode" in html or "data-fc-zen" in html


def test_focus_depth_json(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    sid = _session(db)
    client = dash_app.app.test_client()
    resp = client.get("/focus/depth?session_id=%d" % sid)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["state"] in ("flow", "deep", "surface")
    assert "switches_15m" in data



def _backdate_and_complete(db, sid, ago_min=60):
    """Backdate the session start and mark it completed at the store
    level only -- no auto XP award (for award-race tests)."""
    from datetime import datetime as _dt
    conn = store.get_db(db)
    start = (_dt.now() - timedelta(minutes=ago_min)
             ).isoformat(timespec="seconds")
    conn.execute("UPDATE focus_sessions SET started_at = ? WHERE id = ?",
                 (start, sid))
    conn.commit()
    conn.close()
    store.end_session(sid, "completed",
                      _dt.now().isoformat(timespec="seconds"), path=db)


def _seed_focus_minutes(db, minutes=10):
    """Insert `minutes` of +2 activity ending now (clears the 5-min
    anti-farming threshold)."""
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    start = now - timedelta(minutes=minutes)
    step = 0
    while step < minutes:
        chunk = min(5, minutes - step)
        ts = (start + timedelta(minutes=step)).isoformat(
            timespec="seconds")
        _add_activity(db, day, ts, chunk * 60, "CodeEditor", 2)
        step += chunk


# ------------------------------------------------ council remediations ---

def test_xp_anti_farming_minimum(tmp_path, monkeypatch):
    """Council D1: sessions under 5 focus minutes earn no XP."""
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    sid = _session(db, minutes=50)
    focus_mod.end_session(db_path=db)
    brk = gami_mod.award_session_xp(sid, db_path=db)
    assert brk.get("awarded") is False
    assert brk.get("reason") == "below_minimum"


def test_xp_double_award_idempotent_reason(tmp_path, monkeypatch):
    """Council D1: INSERT OR IGNORE + uq index => idempotent awards."""
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    sid = _session(db, minutes=50)
    _seed_focus_minutes(db)
    _backdate_and_complete(db, sid)
    first = gami_mod.award_session_xp(sid, db_path=db)
    assert first.get("awarded") is True
    second = gami_mod.award_session_xp(sid, db_path=db)
    assert second.get("awarded") is False
    assert second.get("reason") == "already_awarded"
    conn = store.get_db(db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM xp_ledger WHERE session_id = ?",
            (sid,)).fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_xp_concurrent_awards_single_row(tmp_path, monkeypatch):
    """Council D1: concurrent awards (BEGIN IMMEDIATE) => one row."""
    import threading
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    sid = _session(db, minutes=50)
    _seed_focus_minutes(db)
    _backdate_and_complete(db, sid)
    results = []

    def worker():
        results.append(gami_mod.award_session_xp(sid, db_path=db))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    awarded = [r for r in results if r.get("awarded") is True]
    assert len(awarded) == 1
    conn = store.get_db(db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM xp_ledger WHERE session_id = ?",
            (sid,)).fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_xp_award_uncompleted_session_rejected(tmp_path, monkeypatch):
    """Council D1: XP requires a completed session row (rowcount=1)."""
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    sid = _session(db, minutes=50)  # still active
    brk = gami_mod.award_session_xp(sid, db_path=db)
    assert brk.get("awarded") is False
    assert brk.get("reason") == "not_completed"


def test_migration9_indexes_and_fks(tmp_path):
    """Council D2: uq_xp_ledger_session + idx_activities_ts + FK defs."""
    db = str(tmp_path / "w.db")
    migrations.apply_migrations(db)
    conn = store.get_db(db)
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "uq_xp_ledger_session" in idx
        assert "idx_activities_ts" in idx
        xp_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='xp_ledger'"
            ).fetchone()[0]
        badges_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='badges'"
            ).fetchone()[0]
    finally:
        conn.close()
    assert "REFERENCES focus_sessions(id) ON DELETE CASCADE" in xp_sql
    assert "REFERENCES focus_sessions(id) ON DELETE SET NULL" \
        in badges_sql


def test_badge_records_session_id(tmp_path, monkeypatch):
    """Council D2: earned badges carry the triggering session_id."""
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    sid = _session(db, minutes=50, enforcement_mode="hardcore")
    _seed_focus_minutes(db)
    result = _end_session(db, sid)  # auto-award runs the badge check
    assert "iron_will" in result["xp"].get("new_badges", [])
    conn = store.get_db(db)
    try:
        row = conn.execute(
            "SELECT session_id FROM badges WHERE badge_key='iron_will'"
            ).fetchone()
    finally:
        conn.close()
    assert row[0] == sid


def test_snapshot_staleness_fail_safe(monkeypatch):
    """Council D4: stale snapshot or expired peak window => no
    hardcore escalation (in-memory, no SQLite)."""
    import time
    from focuscore.shield import _snapshot_force_hardcore, RulesSnapshot

    # Fresh snapshot with live peak window => escalation applies.
    future_end = (datetime.now() + timedelta(hours=1)
                  ).isoformat(timespec="seconds")
    snap = RulesSnapshot(
        rules=(), overrides=(), session=None, cycle=None,
        active_pass=None, hud_enabled=True, force_hardcore=True,
        generated_at_monotonic=time.monotonic(),
        peak_window_end_iso=future_end)
    assert _snapshot_force_hardcore(snap) is True

    # Stale snapshot (>120s) => fail-safe False.
    stale = RulesSnapshot(
        rules=(), overrides=(), session=None, cycle=None,
        active_pass=None, hud_enabled=True, force_hardcore=True,
        generated_at_monotonic=time.monotonic() - 300.0,
        peak_window_end_iso=future_end)
    assert _snapshot_force_hardcore(stale) is False

    # Peak window ended => fail-safe False even when fresh.
    past_end = (datetime.now() - timedelta(minutes=1)
                ).isoformat(timespec="seconds")
    expired = RulesSnapshot(
        rules=(), overrides=(), session=None, cycle=None,
        active_pass=None, hud_enabled=True, force_hardcore=True,
        generated_at_monotonic=time.monotonic(),
        peak_window_end_iso=past_end)
    assert _snapshot_force_hardcore(expired) is False

    # No escalation requested => False, no SQLite touched.
    plain = RulesSnapshot(
        rules=(), overrides=(), session=None, cycle=None,
        active_pass=None, hud_enabled=True)
    assert _snapshot_force_hardcore(plain) is False


def test_peak_window_end_iso_helper(tmp_path, monkeypatch):
    """Council D4: chronotype.peak_window_end_iso returns today's end."""
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    chronotype.set_window("09:00", "11:30", db_path=db)
    end_iso = chronotype.peak_window_end_iso(db_path=db)
    assert end_iso.endswith("T11:30:00")
    chronotype.set_window("09:00", "11:30", enabled=False, db_path=db)
    assert chronotype.peak_window_end_iso(db_path=db) == ""


def test_activities_range_bounded(tmp_path, monkeypatch):
    """Council D6: get_activities_range is bounded by ts (ASC)."""
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    now = datetime.now()
    day = now.date().isoformat()
    conn = store.get_db(db)
    try:
        for mins_ago, app in ((10, "code.exe"), (60, "old.exe"),
                              (300, "older.exe")):
            ts = (now - timedelta(minutes=mins_ago)
                  ).isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO activities (ts, day, app, title, duration, "
                "score) VALUES (?, ?, ?, '', 60, 2)",
                (ts, day, app))
        conn.commit()
    finally:
        conn.close()
    start = (now - timedelta(minutes=30)).isoformat(timespec="seconds")
    end = now.isoformat(timespec="seconds")
    rows = store.get_activities_range(start, end, path=db)
    apps = {r["app"] for r in rows}
    assert apps == {"code.exe"}
    # ASC ordering
    assert rows == sorted(rows, key=lambda r: r["ts"])
