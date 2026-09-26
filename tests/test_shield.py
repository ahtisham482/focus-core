"""Phase 7: shield policy unit tests (pure logic, no OS calls)."""

from datetime import datetime, timedelta

import pytest

from focuscore import shield


@pytest.fixture(autouse=True)
def _clean_memory_passes():
    """The in-memory pass fallback is module-global: isolate tests."""
    shield._MEMORY_PASSES.clear()
    yield
    shield._MEMORY_PASSES.clear()


# ---------------------------------------------------------------------------
# is_protected
# ---------------------------------------------------------------------------

def test_protected_matches_case_insensitive():
    assert shield.is_protected("Explorer.EXE")
    assert shield.is_protected("dwm.exe")
    assert shield.is_protected("Taskmgr.Exe")


def test_protected_includes_qwen_additions():
    for exe in ("lockapp.exe", "logonui.exe", "searchui.exe",
                "startmenuexperiencehost.exe", "csrss.exe",
                "wininit.exe", "services.exe"):
        assert shield.is_protected(exe), exe


def test_elevated_unknown_is_protected_fail_open():
    # R2: when we cannot identify a process, the shield fails OPEN.
    assert shield.is_protected("__ELEVATED_UNKNOWN__")
    assert shield.is_protected("__elevated_unknown__")


def test_not_protected_normal_apps():
    assert not shield.is_protected("chrome.exe")
    assert not shield.is_protected("notepad.exe")
    assert not shield.is_protected("")


# ---------------------------------------------------------------------------
# rule_is_active
# ---------------------------------------------------------------------------

def _rule(**kw):
    base = {"days": "all", "start_time": "", "end_time": ""}
    base.update(kw)
    return base


def test_rule_always_active():
    now = datetime(2026, 9, 27, 15, 30)  # a Sunday
    assert shield.rule_is_active(_rule(), now)


def test_rule_day_filter():
    monday = datetime(2026, 9, 28, 10, 0)   # Monday
    sunday = datetime(2026, 9, 27, 10, 0)   # Sunday
    assert shield.rule_is_active(_rule(days="0,1,2,3,4"), monday)
    assert not shield.rule_is_active(_rule(days="0,1,2,3,4"), sunday)


def test_rule_time_window():
    assert shield.rule_is_active(
        _rule(start_time="09:00", end_time="18:00"),
        datetime(2026, 9, 28, 12, 0))
    assert not shield.rule_is_active(
        _rule(start_time="09:00", end_time="18:00"),
        datetime(2026, 9, 28, 20, 0))
    # Boundary: start inclusive, end exclusive.
    assert shield.rule_is_active(
        _rule(start_time="09:00", end_time="18:00"),
        datetime(2026, 9, 28, 9, 0))
    assert not shield.rule_is_active(
        _rule(start_time="09:00", end_time="18:00"),
        datetime(2026, 9, 28, 18, 0))


def test_rule_overnight_wrap():
    assert shield.rule_is_active(
        _rule(start_time="22:00", end_time="06:00"),
        datetime(2026, 9, 28, 23, 30))
    assert shield.rule_is_active(
        _rule(start_time="22:00", end_time="06:00"),
        datetime(2026, 9, 28, 2, 0))
    assert not shield.rule_is_active(
        _rule(start_time="22:00", end_time="06:00"),
        datetime(2026, 9, 28, 12, 0))


def test_rule_bad_schedule_fails_closed_for_rule():
    assert not shield.rule_is_active(
        _rule(start_time="nope", end_time="18:00"),
        datetime(2026, 9, 28, 12, 0))
    assert not shield.rule_is_active(
        _rule(days="banana"), datetime(2026, 9, 28, 12, 0))


# ---------------------------------------------------------------------------
# matching_rules
# ---------------------------------------------------------------------------

def test_matching_rules_by_app_and_process():
    now = datetime(2026, 9, 28, 12, 0)
    rules = [
        {"id": 1, "rule_type": "app", "key": "chrome.exe",
         "action": "firm", "enabled": 1, "days": "all",
         "start_time": "", "end_time": ""},
        {"id": 2, "rule_type": "app", "key": "notepad.exe",
         "action": "soft", "enabled": 1, "days": "all",
         "start_time": "", "end_time": ""},
        {"id": 3, "rule_type": "category", "key": "social",
         "action": "hardcore", "enabled": 1, "days": "all",
         "start_time": "", "end_time": ""},
        {"id": 4, "rule_type": "app", "key": "chrome.exe",
         "action": "firm", "enabled": 0, "days": "all",
         "start_time": "", "end_time": ""},
    ]
    matched = shield.matching_rules(rules, "Chrome", "chrome.exe",
                                    "social", now)
    assert {r["id"] for r in matched} == {1, 3}  # disabled rule excluded


def test_matching_rules_respects_schedule():
    now = datetime(2026, 9, 28, 20, 0)
    rules = [{"id": 1, "rule_type": "app", "key": "chrome.exe",
              "action": "firm", "enabled": 1, "days": "all",
              "start_time": "09:00", "end_time": "18:00"}]
    assert shield.matching_rules(rules, "Chrome", "chrome.exe",
                                 "other", now) == []


# ---------------------------------------------------------------------------
# resolve_action priority
# ---------------------------------------------------------------------------

def test_resolve_protected_wins_over_everything():
    action, source = shield.resolve_action(
        True, "hardcore", ["hardcore"], "explorer.exe")
    assert (action, source) == ("allow", "protected")


def test_resolve_session_before_rules():
    action, source = shield.resolve_action(
        True, "strict", ["hardcore"], "chrome.exe")
    assert (action, source) == ("soft", "session")


def test_resolve_session_hardcore_maps_to_hardcore():
    action, source = shield.resolve_action(
        True, "hardcore", [], "chrome.exe")
    assert (action, source) == ("hardcore", "session")


def test_resolve_strongest_rule_wins():
    action, source = shield.resolve_action(
        False, "strict", ["soft", "hardcore", "firm"], "chrome.exe")
    assert (action, source) == ("hardcore", "rule")


def test_resolve_allow_when_nothing_matches():
    assert shield.resolve_action(
        False, "strict", [], "chrome.exe") == ("allow", "none")


# ---------------------------------------------------------------------------
# shield_once with injected fakes
# ---------------------------------------------------------------------------

def _window(app="Chrome", title="Twitter", url="https://twitter.com"):
    return {"app": app, "title": title, "url": url}


class _FakeClient:
    pass


def _run_once(monkeypatch, tmp_path, window, score=-2, category="social",
              session=None, rules=(), process_name="chrome.exe",
              fg_hwnd=1234, session_only=False, fullscreen=False,
              last_notified=None):
    """Drive shield_once with every side effect faked."""
    import focuscore.blocker as blocker_mod
    import focuscore.focus as focus_mod
    import focuscore.store as store_mod

    db = str(tmp_path / "t.db")
    monkeypatch.setattr(blocker_mod, "get_current_window",
                        lambda client, now=None, **kw: dict(window))
    monkeypatch.setattr(
        blocker_mod, "is_blocked",
        lambda app, title, url, level, cat_fn, overrides=None:
        (score < 0, score, category))
    monkeypatch.setattr(focus_mod, "get_active_session",
                        lambda db_path=None: session)
    monkeypatch.setattr(store_mod, "get_overrides", lambda path=None: {})
    monkeypatch.setattr(store_mod, "get_block_rules",
                        lambda path=None, only_enabled=False: list(rules))
    recorded = {}

    def fake_record(session_id, ts, app, title, url, score, category,
                    path=None, **kw):
        recorded.update(session_id=session_id, action_taken=kw.get(
            "action_taken"), process_name=kw.get("process_name"),
            window_handle=kw.get("window_handle"))

    monkeypatch.setattr(store_mod, "record_block", fake_record)
    monkeypatch.setattr(store_mod, "increment_intercepted",
                        lambda sid, path=None: None)
    calls = {"notify": [], "minimize": [], "overlay": []}
    fg = {"hwnd": fg_hwnd, "pid": 999, "process_name": process_name}
    result = shield.shield_once(
        {}, _FakeClient(), lambda *a, **k: (score, category),
        db_path=db, fg_info=fg,
        notify_fn=lambda app, title, label: calls["notify"].append(app),
        minimize_fn=lambda hwnd: calls["minimize"].append(hwnd) or True,
        overlay_fn=lambda **kw: calls["overlay"].append(kw),
        fullscreen_fn=lambda hwnd: fullscreen,
        foreground_fn=lambda hwnd: True,
        last_notified={} if last_notified is None else last_notified,
        session_only=session_only)
    return result, calls, recorded


def test_once_session_soft_notifies_and_overlays(monkeypatch, tmp_path):
    session = {"id": 7, "status": "active", "block_level": "strict",
               "enforcement_mode": "strict", "label": "Deep work"}
    result, calls, recorded = _run_once(
        monkeypatch, tmp_path, _window(), session=session)
    assert result["action"] == "soft"
    assert result["source"] == "session"
    assert calls["notify"] == ["Chrome"]
    assert len(calls["overlay"]) == 1
    assert calls["overlay"][0]["locked"] is False
    assert calls["minimize"] == []
    assert recorded["action_taken"] == "overlay"
    assert recorded["session_id"] == 7


def test_once_session_hardcore_minimizes_and_locks(monkeypatch, tmp_path):
    session = {"id": 7, "status": "active", "block_level": "strict",
               "enforcement_mode": "hardcore", "label": "Deep work"}
    result, calls, recorded = _run_once(
        monkeypatch, tmp_path, _window(), session=session)
    assert result["action"] == "hardcore"
    assert calls["minimize"] == [1234]
    assert calls["overlay"][0]["locked"] is True
    assert recorded["action_taken"] == "minimized"


def test_once_rule_blocks_outside_session(monkeypatch, tmp_path):
    rules = [{"id": 1, "rule_type": "app", "key": "chrome.exe",
              "action": "firm", "enabled": 1, "days": "all",
              "start_time": "", "end_time": ""}]
    result, calls, recorded = _run_once(
        monkeypatch, tmp_path, _window(), session=None, rules=rules)
    assert result["action"] == "firm"
    assert result["source"] == "rule"
    assert calls["minimize"] == [1234]
    assert recorded["session_id"] == shield.RULE_ONLY_SESSION_ID


def test_once_session_only_skips_rules(monkeypatch, tmp_path):
    rules = [{"id": 1, "rule_type": "app", "key": "chrome.exe",
              "action": "hardcore", "enabled": 1, "days": "all",
              "start_time": "", "end_time": ""}]
    result, calls, _ = _run_once(
        monkeypatch, tmp_path, _window(), session=None, rules=rules,
        session_only=True)
    assert result["action"] == "allow"


def test_once_protected_process_never_touched(monkeypatch, tmp_path):
    session = {"id": 7, "status": "active", "block_level": "strict",
               "enforcement_mode": "hardcore", "label": "Deep work"}
    result, calls, _ = _run_once(
        monkeypatch, tmp_path, _window(), session=session,
        process_name="explorer.exe")
    assert result["action"] == "allow"
    assert result["reason"] == "protected process"
    assert calls["minimize"] == [] and calls["overlay"] == []


def test_once_elevated_unknown_fails_open(monkeypatch, tmp_path):
    session = {"id": 7, "status": "active", "block_level": "strict",
               "enforcement_mode": "hardcore", "label": "Deep work"}
    result, calls, _ = _run_once(
        monkeypatch, tmp_path, _window(), session=session,
        process_name="__ELEVATED_UNKNOWN__")
    assert result["action"] == "allow"
    assert calls["minimize"] == []


def test_once_emergency_pass_allows(monkeypatch, tmp_path):
    session = {"id": 7, "status": "active", "block_level": "strict",
               "enforcement_mode": "hardcore", "label": "Deep work"}
    monkeypatch.setattr(shield, "pass_active",
                        lambda now=None, db_path=None: {
                            "reason": "call", "minutes": 5})
    result, calls, _ = _run_once(
        monkeypatch, tmp_path, _window(), session=session)
    assert result["action"] == "allow"
    assert result["reason"] == "emergency pass"
    assert calls["minimize"] == []


def test_once_dedupe_within_minute(monkeypatch, tmp_path):
    session = {"id": 7, "status": "active", "block_level": "strict",
               "enforcement_mode": "strict", "label": "Deep work"}
    last_notified = {"chrome": datetime.now()}
    result, calls, _ = _run_once(
        monkeypatch, tmp_path, _window(), session=session,
        last_notified=last_notified)
    assert result["notified"] is False
    assert result["reason"] == "deduped"
    assert calls["notify"] == []


def test_once_fullscreen_suspends_overlay(monkeypatch, tmp_path):
    session = {"id": 7, "status": "active", "block_level": "strict",
               "enforcement_mode": "hardcore", "label": "Deep work"}
    result, calls, recorded = _run_once(
        monkeypatch, tmp_path, _window(), session=session,
        fullscreen=True)
    assert result["fullscreen"] is True
    assert calls["overlay"] == []       # no overlay on fullscreen
    assert calls["minimize"] == [1234]  # minimize still applies


def test_once_no_window_means_no_action(monkeypatch, tmp_path):
    import focuscore.blocker as blocker_mod
    monkeypatch.setattr(blocker_mod, "get_current_window",
                        lambda client, now=None, **kw: None)
    result = shield.shield_once(
        {}, _FakeClient(), lambda *a, **k: (0, "other"),
        fg_info={"hwnd": 1, "pid": 2, "process_name": "x.exe"},
        last_notified={})
    assert result["action"] == "none"


def test_once_productive_app_allowed(monkeypatch, tmp_path):
    result, calls, _ = _run_once(
        monkeypatch, tmp_path, _window(), score=2, category="work")
    assert result["action"] == "allow"
    assert calls["notify"] == []


# ---------------------------------------------------------------------------
# pass_active fallback chain (R7)
# ---------------------------------------------------------------------------

def test_pass_active_db_first(monkeypatch, tmp_path):
    import focuscore.store as store_mod
    db = str(tmp_path / "t.db")
    now = datetime.now()
    store_mod.create_pass(30, "db pass", now=now, path=db)
    found = shield.pass_active(now=now, db_path=db)
    assert found and found["reason"] == "db pass"


def test_pass_active_memory_fallback(monkeypatch, tmp_path):
    import focuscore.store as store_mod
    db = str(tmp_path / "missing-dir" / "t.db")  # unreachable
    monkeypatch.setattr(store_mod, "get_active_pass",
                        lambda now=None, path=None: (_ for _ in ()).throw(
                            OSError("locked")))
    now = datetime.now()
    shield.remember_memory_pass(now.isoformat(timespec="seconds"), 30,
                                "memory pass")
    found = shield.pass_active(now=now, db_path=db)
    assert found and found["reason"] == "memory pass"


def test_pass_active_expired_is_not_active(monkeypatch, tmp_path):
    import focuscore.store as store_mod
    db = str(tmp_path / "t2.db")
    past = datetime.now() - timedelta(minutes=60)
    store_mod.create_pass(5, "old pass", now=past, path=db)
    assert shield.pass_active(db_path=db) is None


def test_create_pass_rejects_bad_minutes(tmp_path):
    import focuscore.store as store_mod
    db = str(tmp_path / "t3.db")
    with pytest.raises(ValueError):
        store_mod.create_pass(0, "x", path=db)
    with pytest.raises(ValueError):
        store_mod.create_pass(999, "x", path=db)
