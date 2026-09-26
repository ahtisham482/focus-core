"""Phase 8: adaptive suggestion engine (focuscore/adaptive.py)."""

from datetime import date, datetime, timedelta

from focuscore import adaptive, focus, store


def _ev(ts, minutes, app="Code", score=2):
    return {"ts": ts, "duration": minutes * 60.0, "app": app,
            "title": "work", "url": None, "category": "Work",
            "score": score, "override_score": None,
            "match_key": "app:%s" % app}


def _seed_stretches(db, days=10, minutes=60, end="2026-09-26"):
    end_d = date.fromisoformat(end)
    for back in range(days):
        d = (end_d - timedelta(days=back)).isoformat()
        store.save_events(d, [_ev(d + "T09:00:00", minutes)], path=db)


def test_cold_start_pomodoro(tmp_path):
    db = str(tmp_path / "a.db")
    minutes, reason = adaptive.suggest_work_minutes(db_path=db,
                                                   mode="pomodoro")
    assert minutes == 25
    assert "history" in reason.lower()


def test_cold_start_flowtime(tmp_path):
    db = str(tmp_path / "a2.db")
    minutes, reason = adaptive.suggest_flow_target(db_path=db)
    assert minutes == 50
    assert "history" in reason.lower()


def test_warm_suggestion_from_stretches(tmp_path):
    db = str(tmp_path / "a3.db")
    _seed_stretches(db, days=10, minutes=60)
    minutes, reason = adaptive.suggest_work_minutes(
        db_path=db, now=datetime(2026, 9, 26, 10, 0), mode="pomodoro")
    # base 60 +/- 10 peak nudge, clamped and rounded to 5
    assert minutes in (50, 60, 70)
    assert "stretch" in reason.lower()


def test_suggestion_clamped(tmp_path):
    db = str(tmp_path / "a4.db")
    _seed_stretches(db, days=10, minutes=200)
    minutes, _ = adaptive.suggest_work_minutes(
        db_path=db, now=datetime(2026, 9, 26, 10, 0), mode="pomodoro")
    assert minutes == 120  # WORK_MAX clamp wins over the nudge


def test_break_base_and_long(tmp_path):
    assert adaptive.suggest_break_minutes(2)[0] == 5
    minutes, reason = adaptive.suggest_break_minutes(4)
    assert minutes == 15
    assert "4" in reason


def test_break_fatigue_bonus_and_cap(tmp_path):
    minutes, reason = adaptive.suggest_break_minutes(
        2, recent_switch_rate=30, baseline_switch_rate=10)
    assert minutes == 10  # 5 base + 5 fatigue bonus
    assert "switching" in reason.lower()
    minutes, _ = adaptive.suggest_break_minutes(
        8, recent_switch_rate=1000, baseline_switch_rate=1)
    assert minutes <= 30  # hard cap


def test_fatigue_check_no_data(tmp_path):
    db = str(tmp_path / "a5.db")
    assert adaptive.fatigue_check(db_path=db) == (False, "")


def test_fatigue_check_tired(tmp_path):
    db = str(tmp_path / "a6.db")
    today = date.today().isoformat()
    # Low pulse day: mostly -2.
    store.save_events(today, [_ev(today + "T09:00:00", 60, app="YouTube",
                               score=-2),
                           _ev(today + "T10:00:00", 10, score=2)],
                      path=db)
    sid = store.create_session("x", 25, today + "T09:00:00",
                               today + "T09:25:00", "strict", path=db)
    for _ in range(3):
        cid = store.start_cycle(sid, "work", 25,
                                today + "T09:00:00", 1000.0, path=db)
        store.end_cycle(cid, "completed", today + "T09:25:00", path=db)
    tired, msg = adaptive.fatigue_check(db_path=db)
    assert tired is True
    assert "3" in msg


def _flow_session_with_events(db):
    base = datetime(2026, 9, 26, 9, 0, 0)
    res = focus.start_session("Flow", 50, db_path=db, now=base,
                              session_type="flowtime")
    assert "error" not in res
    sid = res["id"]
    d = "2026-09-26"
    # 12-min stretch + 3-min continuation after a 60 s gap (merges).
    # 5-min stretch later (too short to count). A -2 blip far away.
    store.save_events(d, [
        _ev(d + "T09:00:00", 12, score=2),
        _ev(d + "T09:13:00", 3, score=2),
        _ev(d + "T10:00:00", 5, score=2),
        _ev(d + "T11:00:00", 2, app="YouTube", score=-2),
    ], path=db)
    return sid


def test_flow_qualifying_minutes(tmp_path):
    db = str(tmp_path / "a7.db")
    sid = _flow_session_with_events(db)
    assert adaptive.flow_qualifying_minutes(sid, db_path=db) == 16.0


def test_past_flow_lengths(tmp_path):
    db = str(tmp_path / "a8.db")
    sid = _flow_session_with_events(db)
    focus.end_session(db_path=db)
    assert not store.get_active_session(path=db)
    lengths = adaptive.past_flow_lengths(db_path=db)
    assert lengths == [16.0]
    assert store.get_session(sid, path=db)["session_type"] == "flowtime"
