"""Phase 7: HUD snapshot tests (pure data; no tkinter needed)."""

from datetime import datetime

from focuscore import hud


def test_snapshot_off_when_nothing_running(tmp_path, monkeypatch):
    import focuscore.shield as shield_mod

    db = str(tmp_path / "h.db")
    monkeypatch.setattr(shield_mod, "shield_daemon_running",
                        lambda: False)
    monkeypatch.setattr(shield_mod, "pass_active",
                        lambda now=None, db_path=None: None)
    snap = hud.hud_snapshot(db_path=db)
    import sys
    valid_states = {"off", "protected"} if sys.platform == "win32" else {"off"}
    assert snap["state"] in valid_states
    assert snap["blocks_today"] == 0
    assert snap["pass_active"] is False


def test_snapshot_session_state(tmp_path, monkeypatch):
    from focuscore import focus as focus_mod
    from focuscore import shield as shield_mod

    db = str(tmp_path / "h2.db")
    now = datetime.now()
    focus_mod.start_session("Deep work", 25, db_path=db, now=now)
    monkeypatch.setattr(shield_mod, "shield_daemon_running",
                        lambda: True)
    monkeypatch.setattr(shield_mod, "pass_active",
                        lambda now=None, db_path=None: None)
    snap = hud.hud_snapshot(db_path=db)
    assert snap["state"] == "session"
    assert snap["session_label"] == "Deep work"
    assert snap["session_elapsed"] is not None


def test_snapshot_pass_state(tmp_path, monkeypatch):
    from focuscore import shield as shield_mod
    from focuscore import store as store_mod

    db = str(tmp_path / "h3.db")
    store_mod.create_pass(30, "call", path=db)
    monkeypatch.setattr(shield_mod, "shield_daemon_running",
                        lambda: True)
    snap = hud.hud_snapshot(db_path=db)
    assert snap["state"] == "pass"
    assert snap["pass_active"] is True


def test_snapshot_blocks_today_counted(tmp_path, monkeypatch):
    from focuscore import shield as shield_mod
    from focuscore import store as store_mod

    db = str(tmp_path / "h4.db")
    now = datetime.now().isoformat(timespec="seconds")
    store_mod.record_block(0, now, "Chrome", "Twitter",
                           "https://twitter.com", -2, "social",
                           path=db, action_taken="minimized")
    store_mod.record_block(0, now, "Chrome", "YouTube",
                           "https://youtube.com", -2, "entertainment",
                           path=db, action_taken="overlay")
    monkeypatch.setattr(shield_mod, "shield_daemon_running",
                        lambda: True)
    monkeypatch.setattr(shield_mod, "pass_active",
                        lambda now=None, db_path=None: None)
    snap = hud.hud_snapshot(db_path=db)
    assert snap["blocks_today"] == 2


def test_snapshot_never_raises_on_broken_db(tmp_path):
    snap = hud.hud_snapshot(
        db_path=str(tmp_path / "no-such-dir" / "h.db"))
    assert snap["state"] == "off"
