"""Tests for Phase 2: goals and alerts.

All tests use synthetic day summaries and a throwaway SQLite file --
no ActivityWatch needed.
"""

from datetime import datetime, timedelta

from focuscore import alerts as alerts_mod
from focuscore import goals as goals_mod
from focuscore import store


def _summary(**kwargs):
    """Synthetic day summary: minutes per category -> seconds dict."""
    by_category = {name: minutes * 60.0
                   for name, minutes in kwargs.get("categories", {}).items()}
    levels = {2: 0.0, 1: 0.0, 0: 0.0, -1: 0.0, -2: 0.0}
    for level, minutes in kwargs.get("levels", {}).items():
        levels[level] = minutes * 60.0
    return {
        "seconds_by_level": levels,
        "seconds_by_category": by_category,
        "uncategorized": [],
        "afk_seconds": 0.0,
        "total_seconds": sum(levels.values()),
    }


def _goal(direction, target_type, target_name=None, minutes=None, pulse=None):
    return {
        "id": 1, "name": "Test goal", "direction": direction,
        "target_type": target_type, "target_name": target_name,
        "threshold_minutes": minutes, "threshold_pulse": pulse,
        "pinned": False, "created_at": "",
    }


# ---------------------------------------------------------------- goals ---

def test_more_than_achieved():
    goal = _goal("more_than", "category", "Software Development", minutes=60)
    ev = goals_mod.evaluate_goal(
        goal, _summary(categories={"Software Development": 120}))
    assert ev["status"] == "achieved"
    assert ev["current"] == 120.0
    assert ev["target"] == 60.0
    assert ev["pct"] == 200.0
    assert ev["unit"] == "min"


def test_more_than_behind():
    goal = _goal("more_than", "category", "Software Development", minutes=60)
    ev = goals_mod.evaluate_goal(
        goal, _summary(categories={"Software Development": 30}))
    assert ev["status"] == "behind"
    assert ev["pct"] == 50.0


def test_less_than_on_track():
    goal = _goal("less_than", "category", "Entertainment", minutes=60)
    ev = goals_mod.evaluate_goal(
        goal, _summary(categories={"Entertainment": 30}))
    assert ev["status"] == "on_track"


def test_less_than_missed():
    goal = _goal("less_than", "category", "Entertainment", minutes=60)
    ev = goals_mod.evaluate_goal(
        goal, _summary(categories={"Entertainment": 90}))
    assert ev["status"] == "missed"
    assert ev["pct"] == 150.0


def test_pulse_goal():
    goal = _goal("more_than", "pulse", pulse=70)
    # all Focus Work -> pulse 100 -> achieved
    ev = goals_mod.evaluate_goal(goal, _summary(levels={2: 60}))
    assert ev["current"] == 100.0
    assert ev["target"] == 70.0
    assert ev["status"] == "achieved"
    assert ev["unit"] == "pts"
    # all Distracting -> pulse 0 -> behind
    ev2 = goals_mod.evaluate_goal(goal, _summary(levels={-2: 60}))
    assert ev2["current"] == 0.0
    assert ev2["status"] == "behind"


def test_pct_math_exact():
    goal = _goal("more_than", "category", "News", minutes=60)
    ev = goals_mod.evaluate_goal(goal, _summary(categories={"News": 45}))
    assert ev["pct"] == 75.0


def test_goal_crud(tmp_path):
    db = str(tmp_path / "goals.db")
    gid = goals_mod.add_goal("Deep work", "more_than", "category",
                             target_name="Software Development",
                             threshold_minutes=120, pinned=True, db_path=db)
    goals = store.list_goals(path=db)
    assert len(goals) == 1
    assert goals[0]["name"] == "Deep work"
    assert goals[0]["pinned"] is True

    store.set_pinned(gid, False, path=db)
    assert store.list_goals(path=db)[0]["pinned"] is False

    store.delete_goal(gid, path=db)
    assert store.list_goals(path=db) == []


# --------------------------------------------------------------- alerts ---

def _alert(target_type="category", target_name="Entertainment",
           threshold=30, cooldown=60, enabled=True):
    return {
        "id": 1, "name": "Test alert", "target_type": target_type,
        "target_name": target_name, "threshold_minutes": threshold,
        "message": "test", "cooldown_minutes": cooldown,
        "enabled": enabled, "created_at": "",
    }


def _seed_alert(db, **kwargs):
    defaults = dict(name="Test alert", target_type="category",
                    target_name="Entertainment", threshold_minutes=30,
                    message="test", cooldown_minutes=60, enabled=True)
    defaults.update(kwargs)
    return store.add_alert(path=db, **defaults)


def test_alert_fires_at_threshold(tmp_path):
    db = str(tmp_path / "alerts.db")
    _seed_alert(db, threshold_minutes=30)
    calls = []
    summary = _summary(categories={"Entertainment": 45})
    fired = alerts_mod.check_alerts(
        summary, notifier=lambda a, c: calls.append((a["name"], c)),
        db_path=db,
        now=datetime(2026, 1, 5, 12, 0, 0))
    assert len(fired) == 1
    assert fired[0]["current_minutes"] == 45.0
    assert calls == [("Test alert", 45.0)]
    assert len(store.recent_firings(path=db)) == 1


def test_alert_does_not_refire_inside_cooldown(tmp_path):
    db = str(tmp_path / "alerts.db")
    _seed_alert(db, threshold_minutes=30, cooldown_minutes=60)
    summary = _summary(categories={"Entertainment": 45})
    noop = lambda a, c: None  # noqa: E731
    t0 = datetime(2026, 1, 5, 12, 0, 0)
    assert len(alerts_mod.check_alerts(summary, notifier=noop,
                                      db_path=db, now=t0)) == 1
    # 30 minutes later: still inside the 60-minute cooldown -> silent
    assert alerts_mod.check_alerts(
        summary, notifier=noop, db_path=db,
        now=t0 + timedelta(minutes=30)) == []
    assert len(store.recent_firings(path=db)) == 1


def test_alert_refires_after_cooldown(tmp_path):
    db = str(tmp_path / "alerts.db")
    _seed_alert(db, threshold_minutes=30, cooldown_minutes=60)
    summary = _summary(categories={"Entertainment": 45})
    noop = lambda a, c: None  # noqa: E731
    t0 = datetime(2026, 1, 5, 12, 0, 0)
    alerts_mod.check_alerts(summary, notifier=noop, db_path=db, now=t0)
    # 61 minutes later: cooldown expired -> fires again
    fired = alerts_mod.check_alerts(
        summary, notifier=noop, db_path=db,
        now=t0 + timedelta(minutes=61))
    assert len(fired) == 1
    assert len(store.recent_firings(path=db)) == 2


def test_alert_below_threshold_does_not_fire(tmp_path):
    db = str(tmp_path / "alerts.db")
    _seed_alert(db, threshold_minutes=30)
    calls = []
    summary = _summary(categories={"Entertainment": 10})
    fired = alerts_mod.check_alerts(
        summary, notifier=lambda a, c: calls.append(a), db_path=db)
    assert fired == []
    assert calls == []


def test_disabled_alert_never_fires(tmp_path):
    db = str(tmp_path / "alerts.db")
    _seed_alert(db, threshold_minutes=30, enabled=False)
    calls = []
    summary = _summary(categories={"Entertainment": 999})
    fired = alerts_mod.check_alerts(
        summary, notifier=lambda a, c: calls.append(a), db_path=db)
    assert fired == []
    assert calls == []


def test_notifier_failure_does_not_break_check(tmp_path):
    db = str(tmp_path / "alerts.db")
    _seed_alert(db, threshold_minutes=30)

    def bad_notifier(alert, current):
        raise RuntimeError("no desktop here")

    summary = _summary(categories={"Entertainment": 45})
    # must not raise ...
    fired = alerts_mod.check_alerts(summary, notifier=bad_notifier,
                                    db_path=db)
    # ... and the firing is still recorded (so it won't spam retries)
    assert len(fired) == 1
    assert len(store.recent_firings(path=db)) == 1


def test_activity_target_uses_match_key(tmp_path):
    db = str(tmp_path / "alerts.db")
    _seed_alert(db, target_type="activity", target_name="domain:youtube.com",
                threshold_minutes=20)
    summary = _summary()
    activities = [
        {"match_key": "domain:youtube.com", "duration": 15 * 60.0},
        {"match_key": "domain:youtube.com", "duration": 10 * 60.0},
        {"match_key": "app:code", "duration": 120 * 60.0},
    ]
    calls = []
    fired = alerts_mod.check_alerts(
        summary, notifier=lambda a, c: calls.append(c),
        db_path=db, activities=activities)
    assert len(fired) == 1
    assert fired[0]["current_minutes"] == 25.0


def test_alert_crud(tmp_path):
    db = str(tmp_path / "alerts2.db")
    aid = alerts_mod.add_alert("Social cap", "category", "Social Networking",
                               30, message="Take a break!", db_path=db)
    alerts = store.list_alerts(path=db)
    assert len(alerts) == 1
    assert alerts[0]["message"] == "Take a break!"
    assert alerts[0]["enabled"] is True

    store.set_alert_enabled(aid, False, path=db)
    assert store.list_alerts(path=db)[0]["enabled"] is False

    store.delete_alert(aid, path=db)
    assert store.list_alerts(path=db) == []
