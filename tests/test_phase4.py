"""Tests for Phase 4: timesheets, weekly report, focus coaching.

Timesheet arithmetic is plain documented merging/dropping; the weekly
report and coaching numbers are plain documented aggregations -- no
invented formulas anywhere, so every expectation below is hand-checkable.
"""

import csv
from datetime import date

import pytest

from focuscore import coaching, reports, store, timesheet
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


# --------------------------------------------------------------- timesheet ---

def test_suggest_merges_consecutive_same_category(tmp_path):
    db = str(tmp_path / "ts.db")
    day = "2026-09-21"
    _seed_day(db, day, [
        _event(day + "T09:00:00", 600, app="Code"),
        _event(day + "T09:10:00", 900, app="Code"),
        # 1-minute gap (<= 5) keeps the same block going
        _event(day + "T09:26:00", 180, app="Code"),
        # 2-minute block is below the 5-minute minimum -> dropped
        _event(day + "T10:00:00", 120, app="Mail", title="inbox",
               category="Communication", score=1),
        _event(day + "T11:00:00", 1800, app="Mail", title="inbox",
               category="Communication", score=1),
    ])
    blocks = timesheet.suggest_blocks(day, db_path=db)
    assert len(blocks) == 2
    first, second = blocks
    assert first["category"] == "Software Development"
    # Wall-clock span 09:00 -> 09:29 = 29 min (the 1-minute gap is inside
    # the billed block, not billed separately).
    assert first["minutes"] == pytest.approx(29.0)
    # Timestamps are normalized to minute precision.
    assert first["start_ts"] == day + "T09:00"
    assert first["end_ts"] == day + "T09:29"
    assert first["app"] == "Code"  # most common app by seconds
    assert second["category"] == "Communication"
    assert second["minutes"] == pytest.approx(30.0)


def test_suggest_splits_on_long_gap_or_category_change(tmp_path):
    db = str(tmp_path / "ts.db")
    day = "2026-09-21"
    _seed_day(db, day, [
        _event(day + "T09:00:00", 600, app="Code"),
        # 10-minute gap (> 5) starts a new block
        _event(day + "T09:20:00", 600, app="Code"),
        # same time neighborhood but different category -> new block
        _event(day + "T09:21:00", 600, app="Mail", title="inbox",
               category="Communication", score=1),
    ])
    blocks = timesheet.suggest_blocks(day, db_path=db)
    assert [b["minutes"] for b in blocks] == [10.0, 10.0, 10.0]
    assert [b["category"] for b in blocks] == [
        "Software Development", "Software Development", "Communication"]


def test_accept_edit_delete_add(tmp_path):
    db = str(tmp_path / "ts.db")
    day = "2026-09-21"
    block = {"start_ts": day + "T09:00:00", "end_ts": day + "T09:30:00",
             "minutes": 30.0, "category": "Software Development",
             "app": "Code", "title_hint": "main.py"}
    entry_id = timesheet.accept_suggestion(block, day, task="refactor",
                                           db_path=db)
    entry = store.get_entry(entry_id, path=db)
    assert entry["status"] == "accepted"
    assert entry["task"] == "refactor"

    updated = timesheet.edit_entry(entry_id, task="cleanup", note="done",
                                   db_path=db)
    assert updated["task"] == "cleanup"
    assert updated["note"] == "done"
    assert updated["status"] == "edited"

    added = timesheet.add_entry(day, day + "T14:00:00", day + "T14:45:00",
                                "Communication", app="Mail", task="inbox",
                                db_path=db)
    assert added["status"] == "added"
    assert added["minutes"] == pytest.approx(45.0)

    assert timesheet.delete_entry(added["id"], db_path=db) == {}
    assert store.get_entry(added["id"], path=db) is None


def test_lock_day_blocks_changes(tmp_path):
    db = str(tmp_path / "ts.db")
    day = "2026-09-21"
    block = {"start_ts": day + "T09:00:00", "end_ts": day + "T09:30:00",
             "minutes": 30.0, "category": "Software Development",
             "app": "Code", "title_hint": "main.py"}
    entry_id = timesheet.accept_suggestion(block, day, db_path=db)

    timesheet.lock_day(day, db_path=db)
    assert store.day_is_locked(day, path=db) is True

    assert "error" in timesheet.edit_entry(entry_id, task="x", db_path=db)
    assert "error" in timesheet.delete_entry(entry_id, db_path=db)
    assert "error" in timesheet.add_entry(
        day, day + "T10:00:00", day + "T10:30:00", "Communication",
        db_path=db)
    # the entry itself is untouched
    assert store.get_entry(entry_id, path=db)["task"] == ""


def test_export_csv(tmp_path):
    db = str(tmp_path / "ts.db")
    pid = store.add_project("Website", "Acme", path=db)
    timesheet.accept_suggestion(
        {"start_ts": "2026-09-21T09:00:00", "end_ts": "2026-09-21T10:00:00",
         "minutes": 60.0, "category": "Software Development", "app": "Code",
         "title_hint": "main.py"},
        "2026-09-21", project_id=pid, task="build", db_path=db)
    timesheet.add_entry("2026-09-22", "2026-09-22T09:00:00",
                        "2026-09-22T09:30:00", "Communication", app="Mail",
                        db_path=db)
    timesheet.lock_day("2026-09-22", db_path=db)

    out = str(tmp_path / "export.csv")
    assert timesheet.export_csv("2026-09-21", "2026-09-22", out,
                                db_path=db) == 2
    with open(out, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert list(rows[0].keys()) == list(timesheet.CSV_COLUMNS)
    assert rows[0]["date"] == "2026-09-21"
    assert rows[0]["minutes"] == "60.0"
    assert rows[0]["project"] == "Website"
    assert rows[0]["client"] == "Acme"
    assert rows[0]["task"] == "build"
    assert rows[0]["status"] == "accepted"
    assert rows[1]["date"] == "2026-09-22"
    assert rows[1]["status"] == "locked"


def test_projects_crud(tmp_path):
    db = str(tmp_path / "ts.db")
    pid = store.add_project("Website", "Acme", path=db)
    assert store.list_projects(path=db)[0]["name"] == "Website"

    entry_id = timesheet.accept_suggestion(
        {"start_ts": "2026-09-21T09:00:00", "end_ts": "2026-09-21T09:30:00",
         "minutes": 30.0, "category": "Software Development", "app": "Code",
         "title_hint": "x"},
        "2026-09-21", project_id=pid, db_path=db)

    store.delete_project(pid, path=db)
    assert store.list_projects(path=db) == []
    # the time entry survives, it just loses the project link
    entry = store.get_entry(entry_id, path=db)
    assert entry["project_id"] is None
    assert entry["minutes"] == pytest.approx(30.0)


# ------------------------------------------------------------------ report ---

def _seed_week(db):
    monday = "2026-09-21"  # a Monday
    _seed_day(db, monday, [_event(monday + "T09:00:00", 3600, app="Code")])
    _seed_day(db, "2026-09-22",
              [_event("2026-09-22T09:00:00", 3600, app="Browser",
                      title="video", category="Entertainment", score=-2)])
    return monday


def test_weekly_report(tmp_path):
    db = str(tmp_path / "rep.db")
    monday = _seed_week(db)
    goals_mod.add_goal("Deep work", "more_than", "category",
                       target_name="Software Development",
                       threshold_minutes=60, db_path=db)
    sid = store.create_session("morning", 60, monday + "T09:00:00",
                               monday + "T10:00:00", "strict", path=db)
    store.end_session(sid, "completed", monday + "T10:00:00", path=db)

    rep = reports.weekly_report(date(2026, 9, 21), db_path=db)
    assert rep["week_start"] == "2026-09-21"
    assert rep["week_end"] == "2026-09-27"
    assert rep["total_hours"] == 2.0
    assert len(rep["days"]) == 7

    monday_row, tuesday_row = rep["days"][0], rep["days"][1]
    assert monday_row["total_hours"] == 1.0
    assert monday_row["pulse"] == 100.0
    assert tuesday_row["pulse"] == 0.0
    # empty days are skipped by the average, not counted as zero
    assert rep["days"][2]["pulse"] is None
    assert rep["avg_pulse"] == 50.0

    cats = dict(rep["top_categories"])
    assert cats["Software Development"] == 1.0
    assert cats["Entertainment"] == 1.0

    assert rep["goals"] == [{"name": "Deep work", "days_hit": 1,
                             "days_total": 2}]

    fs = rep["focus_sessions"]
    assert fs["count"] == 1
    assert fs["focus_minutes"] == pytest.approx(60.0)
    assert fs["blocks"] == 0


def test_weekly_report_empty_week(tmp_path):
    db = str(tmp_path / "rep.db")
    rep = reports.weekly_report("2026-09-21", db_path=db)
    assert rep["total_hours"] == 0.0
    assert rep["avg_pulse"] is None
    assert rep["top_categories"] == []
    assert all(d["pulse"] is None for d in rep["days"])
    assert rep["focus_sessions"]["count"] == 0


# ----------------------------------------------------------------- coaching ---

def test_hourly_productivity(tmp_path):
    db = str(tmp_path / "coach.db")
    day = "2026-09-21"
    _seed_day(db, day, [
        _event(day + "T09:30:00", 1800, app="Code"),
        _event(day + "T22:30:00", 1800, app="Browser", title="video",
               category="Entertainment", score=-2),
    ])
    hourly = coaching.hourly_productivity(day, day, db_path=db)
    assert hourly[9]["minutes"] == pytest.approx(30.0)
    assert hourly[9]["pulse"] == 100.0
    assert hourly[22]["minutes"] == pytest.approx(30.0)
    assert hourly[22]["pulse"] == 0.0
    assert hourly[10]["minutes"] == 0.0
    assert hourly[10]["pulse"] is None


def test_events_split_across_hour_boundaries(tmp_path):
    db = str(tmp_path / "coach.db")
    day = "2026-09-21"
    _seed_day(db, day, [_event(day + "T09:50:00", 1200, app="Code")])
    grid = coaching.daily_hourly(day, day, db_path=db)
    assert grid[day][9]["minutes"] == pytest.approx(10.0)
    assert grid[day][10]["minutes"] == pytest.approx(10.0)


def test_best_windows_are_non_overlapping(tmp_path):
    db = str(tmp_path / "coach.db")
    day = "2026-09-21"
    _seed_day(db, day, [
        _event(day + "T09:00:00", 3600, app="Code"),
        _event(day + "T10:00:00", 3600, app="Code"),
        _event(day + "T14:00:00", 3600, app="Code"),
        _event(day + "T15:00:00", 3600, app="Code"),
    ])
    windows = coaching.best_windows(day, day, n=2, window_hours=2,
                                    db_path=db)
    spans = [(w["start_hour"], w["end_hour"]) for w in windows]
    assert spans == [(9, 11), (14, 16)]
    assert all(w["focus_minutes"] == pytest.approx(120.0) for w in windows)
    assert all(w["avg_pulse"] == 100.0 for w in windows)


def test_late_nights_warning(tmp_path):
    db = str(tmp_path / "coach.db")
    # 3 late days -> warning (needs >= 3 days, > 30 min at hour >= 23).
    # The 40 minutes sit fully inside hour 23 (23:00 -> 23:40).
    for i, day in enumerate(["2026-09-21", "2026-09-22", "2026-09-23"]):
        _seed_day(db, day, [_event(day + "T23:00:00", 2400, app="Code")])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-21", "2026-09-23", db_path=db)]
    assert "late_nights" in codes


def test_late_nights_quiet_with_two_days(tmp_path):
    db = str(tmp_path / "coach.db")
    for day in ["2026-09-21", "2026-09-22"]:
        _seed_day(db, day, [_event(day + "T23:00:00", 2400, app="Code")])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-21", "2026-09-22", db_path=db)]
    assert "late_nights" not in codes


def test_marathon_day_warning(tmp_path):
    db = str(tmp_path / "coach.db")
    _seed_day(db, "2026-09-21", [_event("2026-09-21T09:00:00", 11 * 3600,
                                        app="Code")])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-21", "2026-09-21", db_path=db)]
    assert "marathon_days" in codes


def test_marathon_day_quiet_at_ten_hours(tmp_path):
    db = str(tmp_path / "coach.db")
    _seed_day(db, "2026-09-21", [_event("2026-09-21T09:00:00", 10 * 3600,
                                        app="Code")])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-21", "2026-09-21", db_path=db)]
    assert "marathon_days" not in codes


def test_distraction_creep_warning(tmp_path):
    db = str(tmp_path / "coach.db")
    # first 7 days: 0% distracting; last 7 days: 50% distracting
    for i in range(14):
        day = "2026-09-%02d" % (7 + i)
        events = [_event(day + "T09:00:00", 1800, app="Code")]
        if i >= 7:
            events.append(_event(day + "T12:00:00", 1800, app="Browser",
                                 title="video", category="Entertainment",
                                 score=-2))
        else:
            events.append(_event(day + "T12:00:00", 1800, app="Code"))
        _seed_day(db, day, events)
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-07", "2026-09-20", db_path=db)]
    assert "distraction_creep" in codes


def test_distraction_creep_skipped_without_14_days(tmp_path):
    db = str(tmp_path / "coach.db")
    for i in range(7):
        day = "2026-09-%02d" % (14 + i)
        _seed_day(db, day, [_event(day + "T09:00:00", 1800, app="Code"),
                            _event(day + "T12:00:00", 1800, app="Browser",
                                   title="video", category="Entertainment",
                                   score=-2)])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-14", "2026-09-20", db_path=db)]
    assert "distraction_creep" not in codes


def test_low_recovery_warning(tmp_path):
    db = str(tmp_path / "coach.db")
    # 35 hours at Pulse 0 -> warning (needs < 40 avg and > 30 hours)
    for i in range(7):
        day = "2026-09-%02d" % (14 + i)
        _seed_day(db, day, [_event(day + "T09:00:00", 5 * 3600,
                                   app="Browser", title="video",
                                   category="Entertainment", score=-2)])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-14", "2026-09-20", db_path=db)]
    assert "low_recovery" in codes


def test_low_recovery_quiet_with_high_pulse_or_few_hours(tmp_path):
    db = str(tmp_path / "coach.db")
    for i in range(7):
        day = "2026-09-%02d" % (14 + i)
        _seed_day(db, day, [_event(day + "T09:00:00", 5 * 3600, app="Code")])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-14", "2026-09-20", db_path=db)]
    assert "low_recovery" not in codes  # 35h but Pulse 100

    db2 = str(tmp_path / "coach2.db")
    for i in range(4):
        day = "2026-09-%02d" % (14 + i)
        _seed_day(db2, day, [_event(day + "T09:00:00", 5 * 3600,
                                    app="Browser", title="video",
                                    category="Entertainment", score=-2)])
    codes = [w["code"] for w in coaching.burnout_warnings(
        "2026-09-14", "2026-09-17", db_path=db2)]
    assert "low_recovery" not in codes  # Pulse 0 but only 20h


# ---------------------------------------------------------------- dashboard ---

def test_dashboard_phase4_pages_return_200(tmp_path, monkeypatch):
    db = str(tmp_path / "dash.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    _seed_week(db)

    from dashboard.app import app
    client = app.test_client()
    for url in ("/timesheet?day=2026-09-21",
                "/report?week=2026-09-21",
                "/coaching",
                "/timesheet/export/client?from=2026-09-21&to=2026-09-21"):
        response = client.get(url)
        assert response.status_code == 200, url
    export = client.get(
        "/timesheet/export/client?from=2026-09-21&to=2026-09-21")
    assert export.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in export.headers["Content-Disposition"]
    lines = export.data.decode("utf-8").splitlines()
    # Phase 9: client CSV is safe by default -- manifest comment first, and
    # no app names / window titles in the header or body.
    assert lines[0].startswith("# manifest: ")
    assert "app" not in lines[1].split(",")
    assert "title" not in lines[1].split(",")
