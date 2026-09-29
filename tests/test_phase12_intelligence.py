"""Phase 12 (v1.14.0): Deep Time Visual Analytics & Executive Reports.

Tests Flow Index bounds/labels, recovery cost math, donut buckets,
coaching cards, heatmap shape, week trends, the new SVG cards on
/intelligence, the printable report's CSP/zero-JS, and bounded queries.
"""

from datetime import date, datetime, timedelta

from focuscore import chronotype
from focuscore import intelligence as intel_mod
from focuscore import store


# ---------------------------------------------------------------- helpers ---

def _add(db, day, ts, duration, app, score):
    conn = store.get_db(db)
    conn.execute(
        "INSERT INTO activities (ts, duration, app, title, url, category, "
        "score, override_score, match_key, day) VALUES (?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?)",
        (ts, duration, app, app, None,
         "Work" if score > 0 else "Distraction", score, None, app, day))
    conn.commit()
    conn.close()


def _seed_day(db, day, focus_min=60, distract_min=0):
    """Seed `focus_min` of +2 and `distract_min` of -2, chunked by 5m."""
    start = datetime.fromisoformat(day + "T09:00:00")
    step = 0
    while step < focus_min:
        chunk = min(5, focus_min - step)
        ts = (start + timedelta(minutes=step)).isoformat(
            timespec="seconds")
        _add(db, day, ts, chunk * 60, "CodeEditor", 2)
        step += chunk
    dstart = start + timedelta(minutes=focus_min)
    step = 0
    while step < distract_min:
        chunk = min(5, distract_min - step)
        ts = (dstart + timedelta(minutes=step)).isoformat(
            timespec="seconds")
        _add(db, day, ts, chunk * 60, "SocialApp", -2)
        step += chunk


# ------------------------------------------------------------- flow index ---

def test_flow_index_bounds_and_label(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    fi = intel_mod.flow_index(day, db_path=db)
    assert fi["score"] is None
    assert fi["label"] == "Insufficient Data"
    assert fi["wow_delta"] is None
    assert "15 minutes" in (fi["note"] or "")


def test_flow_index_insufficient_data_threshold(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    # 14 tracked minutes -> None; 15 minutes -> a real score.
    _seed_day(db, day, focus_min=14)
    assert intel_mod.flow_index(day, db_path=db)["score"] is None
    _seed_day(db, day, focus_min=1)  # now 15 total
    fi = intel_mod.flow_index(day, db_path=db)
    assert isinstance(fi["score"], int)
    assert 0 <= fi["score"] <= 100


def test_flow_index_zero_focus_floor(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    # 60 minutes of pure neutral (score 0), one app -> no switches,
    # but zero focus minutes must force the score to 0.
    start = datetime.fromisoformat(day + "T09:00:00")
    for i in range(12):
        ts = (start + timedelta(minutes=5 * i)).isoformat(
            timespec="seconds")
        _add(db, day, ts, 300, "SameApp", 0)
    fi = intel_mod.flow_index(day, db_path=db)
    assert fi["score"] == 0
    assert fi["label"] == "Scattered"


def test_flow_score_strict_int_clamp(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    _seed_day(db, day, focus_min=240)
    score, _ = intel_mod._flow_score_for_day(day, db_path=db)
    assert isinstance(score, int)
    assert 0 <= score <= 100
    assert score == max(0, min(100, score))


def test_flow_index_all_deep_scores_high(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    # 4h of pure +2: deep ratio = 1.0 -> 40 pts; no switches -> 30 pts.
    _seed_day(db, day, focus_min=240)
    fi = intel_mod.flow_index(day, db_path=db)
    assert 70 <= fi["score"] <= 100
    assert fi["label"] in ("Strong Focus", "Optimal Flow")
    assert fi["components"]["deep_ratio_pts"] == 40


def test_flow_index_deep_ratio_math(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    # 50/50 focus/distraction: deep ratio 0.5 -> 20 pts.
    _seed_day(db, day, focus_min=60, distract_min=60)
    fi = intel_mod.flow_index(day, db_path=db)
    assert fi["components"]["deep_ratio_pts"] == 20


def test_flow_index_label_thresholds():
    assert intel_mod._flow_label(80) == "Optimal Flow"
    assert intel_mod._flow_label(79) == "Strong Focus"
    assert intel_mod._flow_label(60) == "Strong Focus"
    assert intel_mod._flow_label(59) == "Moderate Focus"
    assert intel_mod._flow_label(40) == "Moderate Focus"
    assert intel_mod._flow_label(39) == "Fragmented"
    assert intel_mod._flow_label(20) == "Fragmented"
    assert intel_mod._flow_label(19) == "Scattered"
    assert intel_mod._flow_label(0) == "Scattered"


def test_flow_index_wow_delta(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    today = date.today()
    # Baseline: 3 weak days (10 min focus + 110 min distraction).
    for i in range(1, 4):
        d = (today - timedelta(days=i)).isoformat()
        _seed_day(db, d, focus_min=10, distract_min=110)
    # Today: strong day.
    _seed_day(db, today.isoformat(), focus_min=240)
    fi = intel_mod.flow_index(today.isoformat(), db_path=db)
    assert fi["wow_delta"] is not None
    assert fi["wow_delta"] > 0


# ---------------------------------------------------------- recovery cost ---

def test_recovery_cost_arithmetic(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    # Alternating apps => switches; one -2 block => 1 block.
    start = datetime.fromisoformat(day + "T09:00:00")
    for i in range(6):
        ts = (start + timedelta(minutes=5 * i)).isoformat(
            timespec="seconds")
        _add(db, day, ts, 300, "App%d" % (i % 2), 2)
    ts = (start + timedelta(minutes=30)).isoformat(timespec="seconds")
    _add(db, day, ts, 600, "SocialApp", -2)
    rec = intel_mod.recovery_cost(day, db_path=db)
    assert rec["switches"] == 6  # 6 transitions across 7 events
    assert rec["distraction_blocks"] == 1
    assert rec["recovery_minutes"] == 6 * 1 + 1 * 10
    assert rec["top_friction"][0]["app"] == "SocialApp"


# ------------------------------------------------------------------ donut ---

def test_day_ratio_buckets_sum(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    _seed_day(db, day, focus_min=60, distract_min=30)
    b = intel_mod.day_ratio_buckets(day, db_path=db)
    assert b["deep"] == 60
    assert b["distraction"] == 30
    assert b["shallow"] == 0
    assert b["neutral"] == 0


def test_day_hourly_depth_single_bounded_query(tmp_path, monkeypatch):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    _seed_day(db, day, focus_min=60)
    calls = []
    real = store.get_activities_range

    def counting(start_ts, end_ts, path=None):
        calls.append((start_ts, end_ts))
        return real(start_ts, end_ts, path=path)

    monkeypatch.setattr(store, "get_activities_range", counting)
    hours = intel_mod.day_hourly_depth(day, db_path=db)
    assert len(calls) == 1
    # Wide bounds: one day each side, so offset-carrying timestamps near
    # midnight are fetched and then filtered by local date in Python.
    wide_start = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    wide_end = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    assert calls[0][0] == wide_start + "T00:00:00"
    assert calls[0][1] == wide_end + "T23:59:59"
    assert hours[9][2] == 3600.0


# ---------------------------------------------------------- coaching cards ---

def test_coaching_cards_peak_and_meeting(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    chronotype.set_window("09:00", "11:30", db_path=db)
    # Seed 4 weeks of weekday focus at 09:00-11:00.
    today = date.today()
    d = today - timedelta(days=27)
    while d <= today:
        if d.weekday() < 5:
            _seed_day(db, d.isoformat(), focus_min=120)
        d += timedelta(days=1)
    cards = intel_mod.coaching_cards(db_path=db, today=today)
    assert 1 <= len(cards) <= 3
    assert "09:00" in cards[0]["title"] or "peak" in cards[0]["title"] \
        .lower()


def test_coaching_cards_empty_db_fallback(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    chronotype.set_window("09:00", "11:30", enabled=False, db_path=db)
    cards = intel_mod.coaching_cards(db_path=db)
    assert len(cards) == 1
    assert "tracked" in cards[0]["body"].lower()


# ------------------------------------------------------------ week trends ---

def test_switch_heatmap_shape(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    today = date.today()
    day_from = today - timedelta(days=6)
    grid = intel_mod.switch_heatmap_7x24(day_from, today, db_path=db)
    assert set(grid.keys()) == set(range(7))
    for wd in grid:
        assert set(grid[wd].keys()) == set(range(24))
        assert all(c >= 0 for c in grid[wd].values())


def test_switch_heatmap_counts(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    start = datetime.fromisoformat(day + "T10:00:00")
    for i in range(4):
        ts = (start + timedelta(minutes=5 * i)).isoformat(
            timespec="seconds")
        _add(db, day, ts, 300, "App%d" % i, 1)
    grid = intel_mod.switch_heatmap_7x24(date.today(), date.today(),
                                        db_path=db)
    assert grid[date.today().weekday()][10] == 3


def test_week_flow_trends_delta(tmp_path):
    db = str(tmp_path / "i.db")
    store.init_db(db)
    today = date.today()
    # Strong baseline 2 weeks ago, weak this week.
    base_monday = today - timedelta(days=today.weekday() + 14)
    for i in range(5):
        d = (base_monday + timedelta(days=i)).isoformat()
        _seed_day(db, d, focus_min=240)
    for i in range(today.weekday() + 1):
        d = (today - timedelta(days=i)).isoformat()
        _seed_day(db, d, focus_min=10, distract_min=110)
    trends = intel_mod.week_flow_trends(db_path=db, today=today)
    assert trends["baseline_mean"] is not None
    assert trends["delta"] is not None
    assert trends["delta"] < 0  # this week weaker than baseline
    assert len(trends["daily"]) == today.weekday() + 1


# ------------------------------------------------------------------ routes ---

def test_intelligence_page_has_phase12_cards(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    _seed_day(db, date.today().isoformat(), focus_min=60)
    client = dash_app.app.test_client()
    resp = client.get("/intelligence")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Flow Index" in html
    assert "24-hour depth timeline" in html
    assert "Deep vs shallow" in html
    assert "Distraction recovery cost" in html
    assert "Coaching for tomorrow" in html
    assert "Week trends" in html
    assert "intelligence/report" in html
    assert "<svg" in html


def test_intelligence_report_csp_and_no_js(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    _seed_day(db, date.today().isoformat(), focus_min=60)
    client = dash_app.app.test_client()
    resp = client.get("/intelligence/report")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "default-src 'none'" in html
    assert "style-src 'unsafe-inline'" in html
    assert "<script" not in html.lower()
    assert "Deep Work Intelligence Report" in html
    assert "<svg" in html


def test_intelligence_report_bad_day_falls_back(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    client = dash_app.app.test_client()
    resp = client.get("/intelligence/report?day=not-a-day")
    assert resp.status_code == 200
    assert date.today().isoformat() in resp.data.decode()


# --------------------------------------- council remediation regression ---

def test_timeline_svg_rect_bound_on_high_churn(tmp_path, monkeypatch):
    """GLM P12-6: fixed binning -- DOM never inflates past 300 rects."""
    import dashboard.routes.system as sys_routes
    db = str(tmp_path / "i.db")
    store.init_db(db)
    day = date.today().isoformat()
    # 1,440 one-minute events across 5 apps and all score levels.
    start = datetime.fromisoformat(day + "T00:00:00")
    conn = store.get_db(db)
    scores = [2, 1, 0, -1, -2]
    for i in range(1440):
        ts = (start + timedelta(minutes=i)).isoformat(timespec="seconds")
        sc = scores[i % 5]
        app = "App%d" % (i % 5)
        conn.execute(
            "INSERT INTO activities (ts, duration, app, title, url, "
            "category, score, override_score, match_key, day) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, 60, app, app, None, "x", sc, None, app, day))
    conn.commit()
    conn.close()
    hours = intel_mod.day_hourly_depth(day, db_path=db)
    svg = sys_routes._svg_depth_timeline(hours, peak=(9.0, 11.5),
                                         sessions=[(9.0, 10.0, "s")])
    assert svg.count("<rect") <= 300


def test_heatmap_buckets_local_timezone(tmp_path):
    """GLM P12-4: hour/weekday buckets follow the local timezone."""
    db = str(tmp_path / "i.db")
    store.init_db(db)
    # An offset-aware timestamp; bucket must match its LOCAL hour.
    ts = "2026-09-25T00:30:00+05:00"
    local = datetime.fromisoformat(ts).astimezone().replace(tzinfo=None)
    day = local.date().isoformat()
    _add(db, day, ts, 600, "AppA", 1)
    _add(db, day, (local + timedelta(minutes=10)).isoformat(
        timespec="seconds"), 600, "AppB", 1)
    grid = intel_mod.switch_heatmap_7x24(
        local.date(), local.date(), db_path=db)
    assert grid[local.weekday()][local.hour] == 1


def test_schema_version_stays_9(tmp_path):
    """Council item 5: Phase 12 adds zero schema changes."""
    db = str(tmp_path / "i.db")
    store.init_db(db)
    conn = store.get_db(db)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert ver == 9


def test_report_redacts_titles_and_urls(tmp_path, monkeypatch):
    """Qwen Q2 / GLM P12-5: no window titles, no full URLs in report."""
    import dashboard.app as dash_app
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    day = date.today().isoformat()
    conn = store.get_db(db)
    conn.execute(
        "INSERT INTO activities (ts, duration, app, title, url, category,"
        " score, override_score, match_key, day) VALUES (?, ?, ?, ?, ?,"
        " ?, ?, ?, ?, ?)",
        (day + "T09:00:00", 3600, "SecretBrowser",
         "SECRET-TITLE-XYZ My Private Doc",
         "https://example.com/org/private-repo/pull/123",
         "x", -2, None, "k", day))
    conn.commit()
    conn.close()
    client = dash_app.app.test_client()
    html = client.get("/intelligence/report?day=" + day).data.decode()
    assert "SECRET-TITLE-XYZ" not in html
    assert "example.com/org/private-repo" not in html
    assert "SecretBrowser" in html  # app name itself is fine


def test_report_footer_metadata(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    from focuscore import __version__
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    client = dash_app.app.test_client()
    html = client.get("/intelligence/report").data.decode()
    assert "Generated:" in html
    assert "Focus Core v%s" % __version__ in html
    assert "Schema v9" in html


def test_report_csp_font_src_and_no_handlers(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    db = str(tmp_path / "w.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    client = dash_app.app.test_client()
    html = client.get("/intelligence/report").data.decode()
    assert "font-src data:" in html
    assert "<script" not in html.lower()
    assert "onclick" not in html.lower()
