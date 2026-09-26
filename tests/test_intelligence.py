"""Tests for focuscore/intelligence.py. Synthetic events in tmp_path
DBs -- no Flask, no network."""
from datetime import date, timedelta

import pytest

from focuscore import intelligence as intel
from focuscore import store


def _ev(ts, minutes, app="Code", title="work", score=2):
    return {"ts": ts, "duration": minutes * 60.0, "app": app,
            "title": title, "url": None, "category": "Work",
            "score": score, "override_score": None,
            "match_key": "app:%s" % app}


def _save(db, day, events):
    store.save_events(day, events, path=db)


def _daterange(days=28, end="2026-09-26"):
    end_d = date.fromisoformat(end)
    start = end_d - timedelta(days=days - 1)
    return start.isoformat(), end_d.isoformat()


def test_chronotype_curves_shape(tmp_path):
    db = str(tmp_path / "i.db")
    start, end = _daterange(7)
    _save(db, "2026-09-21",  # a Monday
          [_ev("2026-09-21T09:00:00", 60, score=2)])
    curves = intel.chronotype_curves(start, end, db_path=db)
    assert set(curves.keys()) == set(range(7))
    assert set(curves[0].keys()) == set(range(24))
    assert curves[0][9]["focus_minutes"] == 60.0
    assert curves[0][9]["days"] == 1
    assert curves[0][9]["pulse"] == 100.0
    assert curves[1][9]["focus_minutes"] == 0.0  # Tuesday empty
    assert curves[1][9]["pulse"] is None


def test_classify_morning(tmp_path):
    db = str(tmp_path / "i.db")
    start, end = _daterange(14)
    day = date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        # 2h productive mornings, 30 min distracted evenings
        d = day.isoformat()
        _save(db, d, [_ev(d + "T09:00:00", 120, score=2),
                      _ev(d + "T20:00:00", 30, app="YouTube",
                          title="vids", score=-2)])
        day += timedelta(days=1)
    curves = intel.chronotype_curves(start, end, db_path=db)
    result = intel.classify_chronotype(curves)
    assert result["type"] == "morning"
    assert result["morning_share"] > 0.8
    assert result["peak_hour"] == 9


def test_classify_evening(tmp_path):
    db = str(tmp_path / "i.db")
    start, end = _daterange(14)
    day = date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        d = day.isoformat()
        _save(db, d, [_ev(d + "T21:00:00", 120, score=2)])
        day += timedelta(days=1)
    curves = intel.chronotype_curves(start, end, db_path=db)
    result = intel.classify_chronotype(curves)
    assert result["type"] == "evening"
    assert result["evening_share"] > 0.9
    assert result["peak_hour"] == 21


def test_classify_no_data(tmp_path):
    db = str(tmp_path / "i.db")
    start, end = _daterange(7)
    curves = intel.chronotype_curves(start, end, db_path=db)
    result = intel.classify_chronotype(curves)
    assert result == {"type": "balanced", "morning_share": 0.0,
                      "evening_share": 0.0, "peak_hour": None}


def test_weekday_peak_windows(tmp_path):
    db = str(tmp_path / "i.db")
    start, end = _daterange(28)
    # Heavy Monday 09:00-11:00 focus across 4 Mondays.
    day = date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        if day.weekday() == 0:
            d = day.isoformat()
            _save(db, d, [_ev(d + "T09:00:00", 120, score=2),
                          _ev(d + "T15:00:00", 30, score=1)])
        day += timedelta(days=1)
    curves = intel.chronotype_curves(start, end, db_path=db)
    peaks = intel.weekday_peak_windows(curves)
    assert 0 in peaks  # Monday has data
    assert peaks[0][0]["start_hour"] == 9
    assert peaks[0][0]["end_hour"] == 11
    assert peaks[0][0]["focus_minutes"] == 480.0  # 4 x 120
    # n=2 windows must not overlap.
    peaks2 = intel.weekday_peak_windows(curves, n=2)
    spans = [(w["start_hour"], w["end_hour"]) for w in peaks2[0]]
    assert not (set(range(*spans[0])) & set(range(*spans[1])))


def test_focus_stretches_merge_and_split(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-26",
          [_ev("2026-09-26T09:00:00", 20, score=2),
           _ev("2026-09-26T09:22:00", 18, score=2),   # 2-min gap: merge
           _ev("2026-09-26T10:00:00", 30, score=2)])  # 22-min gap: split
    stretches = intel.focus_stretches("2026-09-26", db_path=db)
    assert [s["minutes"] for s in stretches] == [38.0, 30.0]
    assert stretches[0]["start"] == "09:00"


def test_focus_stretches_neutral_breaks(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-26",
          [_ev("2026-09-26T09:00:00", 20, score=2),
           _ev("2026-09-26T09:20:00", 5, app="Mail", score=0),
           _ev("2026-09-26T09:25:00", 20, score=2)])
    stretches = intel.focus_stretches("2026-09-26", db_path=db)
    assert len(stretches) == 2


def test_depth_summary(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-25", [_ev("2026-09-25T09:00:00", 40, score=2)])
    _save(db, "2026-09-26", [_ev("2026-09-26T09:00:00", 60, score=2)])
    result = intel.depth_summary("2026-09-25", "2026-09-26", db_path=db)
    assert result["avg_longest"] == 50.0
    assert result["best_day"] == "2026-09-26"
    assert result["days"] == 2


def test_switch_rate(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-26",
          [_ev("2026-09-26T09:00:00", 30, app="Code"),
           _ev("2026-09-26T09:30:00", 30, app="Code"),
           _ev("2026-09-26T10:00:00", 30, app="Chrome"),
           _ev("2026-09-26T10:30:00", 30, app="Code")])
    result = intel.switch_rate("2026-09-26", db_path=db)
    assert result["switches"] == 2  # Code->Chrome, Chrome->Code
    assert result["per_hour"] == 1.0  # 2 switches / 2 hours


def test_time_to_first_focus(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-26",
          [_ev("2026-09-26T08:00:00", 30, app="Mail", score=0),
           _ev("2026-09-26T08:30:00", 30, score=2)])
    assert intel.time_to_first_focus("2026-09-26", db_path=db) == 30.0


def test_time_to_first_focus_none(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-26",
          [_ev("2026-09-26T08:00:00", 10, score=2),   # too short
           _ev("2026-09-26T09:00:00", 10, score=2)])
    assert intel.time_to_first_focus("2026-09-26", db_path=db) is None


def test_distraction_anatomy(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-26",
          [_ev("2026-09-26T09:00:00", 60, app="Code", score=2),
           _ev("2026-09-26T10:00:00", 60, app="YouTube", title="cats",
               score=-2),
           _ev("2026-09-26T11:00:00", 30, app="Twitter", title="feed",
               score=-1)])
    result = intel.distraction_anatomy("2026-09-26", "2026-09-26",
                                       db_path=db)
    assert result["top"][0]["app"] == "YouTube"
    assert result["top"][0]["minutes"] == 60.0
    assert result["top"][0]["example"] == "cats"
    # YouTube's block started right after Code -> Code is the entry.
    assert result["entry_points"][0] == {"app": "Code", "count": 1}


def test_week_trends(tmp_path):
    db = str(tmp_path / "i.db")
    # Last week (2026-09-14..20): 2h/day at +2. This week (21..26): 1h/day.
    day = date.fromisoformat("2026-09-14")
    while day <= date.fromisoformat("2026-09-26"):
        d = day.isoformat()
        minutes = 120 if day < date.fromisoformat("2026-09-21") else 60
        _save(db, d, [_ev(d + "T09:00:00", minutes, score=2)])
        day += timedelta(days=1)
    result = intel.week_trends(db_path=db, today=date.fromisoformat(
        "2026-09-26"))
    assert result["this_week"]["hours"] == 6.0   # 6 days x 1h
    assert result["last_week"]["hours"] == 14.0  # 7 days x 2h
    assert result["deltas"]["hours"] == -8.0
    assert result["this_week"]["avg_pulse"] == 100.0


def test_day_timeline_quarters(tmp_path):
    db = str(tmp_path / "i.db")
    _save(db, "2026-09-26",
          [_ev("2026-09-26T09:05:00", 10, app="Code", title="x",
               score=2),                 # quarter 0 of hour 9
           _ev("2026-09-26T09:50:00", 20, app="Chrome", title="y",
               score=-1)])               # spans hour 9 q3 + hour 10 q0
    tl = intel.day_timeline("2026-09-26", db_path=db)
    h9 = tl["hours"][9]
    assert h9["quarters"][0] == 2
    assert h9["quarters"][3] == -1
    assert tl["hours"][10]["quarters"][0] == -1
    assert tl["hours"][10]["quarters"][1] is None
    apps = [a["app"] for a in h9["activities"]]
    assert "Code" in apps and "Chrome" in apps


def test_intelligence_page_renders(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    db = str(tmp_path / "page.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    import dashboard.app as dash_app

    day = date.fromisoformat("2026-08-30")
    while day <= date.fromisoformat("2026-09-26"):
        d = day.isoformat()
        _save(db, d, [_ev(d + "T09:00:00", 120, score=2),
                      _ev(d + "T20:00:00", 30, app="YouTube", title="v",
                          score=-2)])
        day += timedelta(days=1)

    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    resp = client.get("/intelligence")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Your chronotype" in html
    assert "morning person" in html
    assert "Protect these hours" in html
    assert "Day timeline" in html
    assert "/help/intelligence" in html
    # An invalid ?day= falls back to today instead of 500ing.
    resp2 = client.get("/intelligence?day=not-a-day")
    assert resp2.status_code == 200


def test_empty_inputs_never_crash(tmp_path):
    db = str(tmp_path / "i.db")
    assert intel.focus_stretches("2026-09-26", db_path=db) == []
    assert intel.switch_rate("2026-09-26", db_path=db) == {
        "switches": 0, "per_hour": None}
    assert intel.depth_summary("2026-09-26", "2026-09-26",
                               db_path=db)["avg_longest"] is None
    assert intel.distraction_anatomy("2026-09-26", "2026-09-26",
                                     db_path=db) == {
                                         "top": [], "entry_points": []}
    tl = intel.day_timeline("2026-09-26", db_path=db)
    assert len(tl["hours"]) == 24
    assert all(h["minutes"] == 0 for h in tl["hours"])
