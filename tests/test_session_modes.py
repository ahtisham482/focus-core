"""Phase 8: session modes, hybrid timer, break stand-down, cues."""

import sys
import time
from datetime import datetime, timedelta

from focuscore import cues, focus, shield, store


def _base():
    return datetime(2026, 9, 26, 9, 0, 0)


def _start_pomodoro(db, minutes=25, target_cycles=4, now=None):
    now = now or _base()
    res = focus.start_session("Pom", minutes, db_path=db, now=now,
                              session_type="pomodoro",
                              target_cycles=target_cycles)
    assert "error" not in res, res
    return res


def test_start_session_rejects_bad_mode(tmp_path):
    db = str(tmp_path / "s.db")
    res = focus.start_session("X", 25, db_path=db, session_type="turbo")
    assert "error" in res


def test_pomodoro_starts_work_cycle(tmp_path):
    db = str(tmp_path / "s2.db")
    res = _start_pomodoro(db)
    cycle = store.get_active_cycle(res["id"], path=db)
    assert cycle["kind"] == "work"
    assert cycle["planned_minutes"] == 25
    assert cycle["status"] == "active"


def test_settle_work_to_break(tmp_path):
    db = str(tmp_path / "s3.db")
    res = _start_pomodoro(db)
    cycle = store.get_active_cycle(res["id"], path=db)
    mono0 = cycle["started_monotonic"]
    out = focus.settle_session(db_path=db, now=_base() + timedelta(minutes=26),
                               now_mono=mono0 + 26 * 60, cues=False)
    kinds = [e["type"] for e in out["events"]]
    assert kinds == ["work_completed", "break_started"]
    assert not out["suspend_detected"]
    new_cycle = store.get_active_cycle(res["id"], path=db)
    assert new_cycle["kind"] == "break"
    assert new_cycle["planned_minutes"] == 5  # R5 base
    session = store.get_session(res["id"], path=db)
    assert session["completed_cycles"] == 1


def test_sleep_never_completes_cycle(tmp_path):
    db = str(tmp_path / "s4.db")
    res = _start_pomodoro(db)
    cycle = store.get_active_cycle(res["id"], path=db)
    mono0 = cycle["started_monotonic"]
    # Wall clock jumps 2 h; monotonic (real focus time) barely moves.
    out = focus.settle_session(db_path=db, now=_base() + timedelta(hours=2),
                               now_mono=mono0 + 60, cues=False)
    assert out["suspend_detected"] is True  # R1 tick-gap detection
    assert out["events"] == []  # R1: never marked completed
    assert store.get_active_cycle(res["id"], path=db)["kind"] == "work"


def test_monotonic_reset_resyncs_conservatively(tmp_path):
    db = str(tmp_path / "s5.db")
    res = _start_pomodoro(db)
    # Daemon restarted: monotonic clock went backwards.
    out = focus.settle_session(db_path=db, now=_base() + timedelta(minutes=5),
                               now_mono=1.0, cues=False)
    assert out["events"] == []
    kept = store.get_active_cycle(res["id"], path=db)
    assert kept["last_tick_mono"] == 1.0  # re-based, no over-credit
    assert kept["elapsed_offset_seconds"] == 0.0


def test_break_hard_cap_30_minutes(tmp_path):
    db = str(tmp_path / "s6.db")
    res = _start_pomodoro(db)
    sid = res["id"]
    cycle = store.get_active_cycle(sid, path=db)
    mono0 = cycle["started_monotonic"]
    focus.settle_session(db_path=db, now=_base() + timedelta(minutes=26),
                         now_mono=mono0 + 26 * 60, cues=False)
    brk = store.get_active_cycle(sid, path=db)
    assert brk["kind"] == "break"
    # Force an over-long break plan; the 30-min cap still applies (R2).
    store.end_cycle(brk["id"], "completed",
                    _base().isoformat(timespec="seconds"), path=db)
    store.start_cycle(sid, "break", 60,
                      _base().isoformat(timespec="seconds"),
                      mono0 + 26 * 60, path=db)
    out = focus.settle_session(db_path=db,
                               now=_base() + timedelta(minutes=60),
                               now_mono=mono0 + 26 * 60 + 31 * 60,
                               cues=False)
    assert "break_completed" in [e["type"] for e in out["events"]]
    nxt = store.get_active_cycle(sid, path=db)
    assert nxt["kind"] == "work"  # next cycle auto-started


def test_target_reached_waits_for_user(tmp_path):
    db = str(tmp_path / "s7.db")
    res = _start_pomodoro(db, target_cycles=1)
    sid = res["id"]
    cycle = store.get_active_cycle(sid, path=db)
    mono0 = cycle["started_monotonic"]
    focus.settle_session(db_path=db, now=_base() + timedelta(minutes=26),
                         now_mono=mono0 + 26 * 60, cues=False)
    focus.settle_session(db_path=db, now=_base() + timedelta(minutes=32),
                         now_mono=mono0 + 32 * 60, cues=False)
    # Break done, target reached: no new work cycle; session stays
    # active for the user to end (advisory).
    assert store.get_active_cycle(sid, path=db) is None
    assert store.get_active_session(path=db)["id"] == sid


def test_start_and_end_break_early(tmp_path):
    db = str(tmp_path / "s8.db")
    res = _start_pomodoro(db)
    sid = res["id"]
    out = focus.start_break_now(db_path=db, now=_base())
    assert out["ok"] is True
    assert store.get_active_cycle(sid, path=db)["kind"] == "break"
    out = focus.end_break_now(db_path=db, now=_base())
    assert out["ok"] is True
    assert store.get_active_cycle(sid, path=db)["kind"] == "work"
    out = focus.end_break_now(db_path=db, now=_base())
    assert "error" in out  # no active break to end


def test_flowtime_has_no_cycles(tmp_path):
    db = str(tmp_path / "s9.db")
    res = focus.start_session("Flow", 50, db_path=db, now=_base(),
                              session_type="flowtime")
    assert "error" not in res
    assert res["session_type"] == "flowtime"
    settled = focus.settle_session(db_path=db)
    assert settled["events"] == []
    assert store.get_active_cycle(res["id"], path=db) is None
    ended = focus.end_session(db_path=db)
    assert ended["session"]["status"] == "completed"


def test_end_session_closes_active_cycle(tmp_path):
    db = str(tmp_path / "s10.db")
    res = _start_pomodoro(db)
    focus.end_session(db_path=db)
    assert store.get_active_cycle(res["id"], path=db) is None
    cycles = store.cycles_for_session(res["id"], path=db)
    assert cycles and cycles[0]["status"] == "aborted"


def test_cues_never_raise_and_fail_silent():
    # Bad input or disabled cues are always False (R6).
    assert cues.play_cue("nope") is False
    assert cues.play_cue("cycle_end", enabled=False) is False
    # On Windows a valid cue returns True (sound requested on a
    # background thread); off Windows it is a silent no-op.
    assert cues.play_cue("cycle_end") is (sys.platform == "win32")


# ---------------------------------------------------------------------------
# Shield: break soft stand-down (R2) + resume grace
# ---------------------------------------------------------------------------

def _window(app="Chrome", title="Twitter", url="https://twitter.com"):
    return {"app": app, "title": title, "url": url}


class _FakeClient:
    pass


def _run_break_once(monkeypatch, tmp_path, **kw):
    import focuscore.blocker as blocker_mod
    import focuscore.focus as focus_mod
    import focuscore.store as store_mod

    db = str(tmp_path / "b.db")
    session = {"id": 9, "status": "active", "block_level": "strict",
               "enforcement_mode": "strict", "label": "Pom",
               "session_type": "pomodoro"}
    rules = [{"id": 1, "name": "r", "rule_type": "category",
              "key": "social", "action": "firm", "days": "all",
              "start_time": "", "end_time": "", "enabled": 1}]
    monkeypatch.setattr(blocker_mod, "get_current_window",
                        lambda client, now=None, **kw2: dict(_window()))
    monkeypatch.setattr(blocker_mod, "is_blocked",
                        lambda app, title, url, level, cat_fn,
                        overrides=None: (True, -2, "social"))
    monkeypatch.setattr(focus_mod, "get_active_session",
                        lambda db_path=None: session)
    monkeypatch.setattr(store_mod, "get_overrides", lambda path=None: {})
    monkeypatch.setattr(store_mod, "get_block_rules",
                        lambda path=None, only_enabled=False: list(rules))
    monkeypatch.setattr(store_mod, "get_active_cycle",
                        lambda sid, path=None: {"kind": "break"})
    monkeypatch.setattr(store_mod, "record_block", lambda *a, **k: None)
    monkeypatch.setattr(store_mod, "increment_intercepted",
                        lambda sid, path=None: None)
    fg = {"hwnd": 1234, "pid": 999, "process_name": "chrome.exe"}
    return shield.shield_once(
        {}, _FakeClient(), lambda *a, **k: (-2, "social"),
        db_path=db, fg_info=fg,
        notify_fn=lambda *a: None, minimize_fn=lambda hwnd: True,
        overlay_fn=lambda **k: None, fullscreen_fn=lambda hwnd: False,
        foreground_fn=lambda hwnd: True, last_notified={}, **kw)


def test_break_soft_standdown_caps_firm_rule(monkeypatch, tmp_path):
    result = _run_break_once(monkeypatch, tmp_path)
    # Firm rule is capped at soft; session enforcement is paused.
    assert result["action"] == "soft"
    assert result["source"] == "rule"
    assert result["break_standdown"] is True


def test_resume_grace_allows_everything(monkeypatch, tmp_path):
    result = _run_break_once(
        monkeypatch, tmp_path,
        resume_grace_until=time.monotonic() + 600)
    assert result["action"] == "allow"
    assert result["reason"] == "resume grace"


def test_flowtime_deferral_offered_at_most_once(tmp_path):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(
        "dashboard").resolve().parent))
    db = str(tmp_path / "s11.db")
    res = focus.start_session("Flow", 50, db_path=db, now=_base(),
                              session_type="flowtime")
    sid = res["id"]
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dash_app", "dashboard/app.py")
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    assert dash._flowtime_deferral_offered(sid, db_path=db) is False
    dash._mark_flowtime_deferral_offered(sid, db_path=db)
    assert dash._flowtime_deferral_offered(sid, db_path=db) is True
