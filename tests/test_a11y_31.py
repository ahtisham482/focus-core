"""Roadmap 3.1: accessibility deep items.

- Every chart ships a data-table alternative ("View as table" <details>).
- rem-based type scale + Small/Default/Large text-size setting.
- Keyboard shortcuts documented in Help.
"""

import re
import sqlite3
from pathlib import Path

import pytest

import dashboard.app as dash_app
from focuscore import budgets as budgets_mod
from focuscore import help as help_mod
from focuscore import store

ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = (ROOT / "dashboard/static/style.css").read_text(encoding="utf-8")
LIVING_CSS = (ROOT / "dashboard/static/living.css").read_text(encoding="utf-8")

SEEDED_DAY = "2026-09-29"


@pytest.fixture()
def seeded_client(tmp_path, monkeypatch):
    """Test client over a tmp DB seeded with chart-relevant rows."""
    db = str(tmp_path / "a31.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)

    pid = store.add_project("Acme", client="Acme Corp", path=db)
    store.create_entry(
        SEEDED_DAY, SEEDED_DAY + "T09:00", SEEDED_DAY + "T10:00", 60.0,
        "Work", project_id=pid, task="Dev", status="accepted", path=db)
    # A budget so the project card renders budget bars.
    budgets_mod.set_budget(pid, "week", cap_seconds=20 * 3600, path=db)
    # A week of categorized activity + day_stats so /report renders its
    # charts (weekly_report reads total_seconds from day_stats, which
    # run_day normally writes; run_day is mocked out in tests).
    from datetime import date, timedelta
    monday = date(2026, 9, 28)
    week_days = [(monday + timedelta(days=i)).isoformat() for i in range(5)]
    for day in week_days:
        store.save_events(day, [{
            "app": "Code", "title": "main.py", "duration": 3600,
            "score": 2, "category": "Work",
        }], path=db)
    conn = sqlite3.connect(db)
    try:
        for day in week_days:
            conn.execute(
                "INSERT OR REPLACE INTO day_stats (day, total_seconds) "
                "VALUES (?, ?)", (day, 3600))
        conn.commit()
    finally:
        conn.close()

    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    monkeypatch.setattr(dash_app, "run_day", lambda *a, **kw: None)
    dash_app.app.config["TESTING"] = True
    return dash_app.app.test_client()


# ------------------------------------------------- chart data tables ---

def test_weekly_top_categories_chart_has_table(seeded_client):
    html = seeded_client.get("/report?week=2026-09-28").get_data(as_text=True)
    assert "Top categories" in html
    assert "<summary>View as table</summary>" in html
    assert "<th>Category</th>" in html and "<th>Hours</th>" in html
    assert "Work" in html


def test_weekly_pulse_chart_has_table(seeded_client):
    html = seeded_client.get("/report?week=2026-09-28").get_data(as_text=True)
    assert "Pulse through the week" in html
    assert "<th>Day</th>" in html and "<th>Pulse</th>" in html


def test_project_budget_bars_have_table(seeded_client):
    html = seeded_client.get("/timesheet?day=" + SEEDED_DAY).get_data(as_text=True)
    assert "hbar" in html  # budget bars rendered
    assert "<th>Period</th>" in html
    assert "On track" in html or "Watch" in html


def test_activity_mix_bar_has_table(seeded_client):
    # The day page renders the mix bar unconditionally (home gates on
    # today's day_stats, which run_day writes in production).
    html = seeded_client.get("/day/" + SEEDED_DAY).get_data(as_text=True)
    assert 'class="bar"' in html or "class='bar'" in html
    assert "<th>Score level</th>" in html and "<th>Hours</th>" in html


# ------------------------------------------------------- text size ---

def test_text_size_setting_roundtrip(tmp_path, monkeypatch):
    db = str(tmp_path / "tsize.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()

    res = client.post("/settings/text-size", data={"text_size": "large"},
                      follow_redirects=False)
    assert res.status_code == 302
    assert store.get_setting("ui_text_size") == "large"

    res = client.post("/settings/text-size", data={"text_size": "small"},
                      follow_redirects=False)
    assert store.get_setting("ui_text_size") == "small"

    # Invalid value: the current setting stands.
    res = client.post("/settings/text-size", data={"text_size": "huge"},
                      follow_redirects=False)
    assert res.status_code == 302
    assert store.get_setting("ui_text_size") == "small"


def test_layout_emits_text_size_attribute(tmp_path, monkeypatch):
    db = str(tmp_path / "tsize2.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    store.set_setting("ui_text_size", "large")
    html = dash_app.layout("T", "<p>x</p>")
    assert "data-text-size='large'" in html
    store.set_setting("ui_text_size", "default")
    html = dash_app.layout("T", "<p>x</p>")
    assert "data-text-size" not in html


def test_no_px_font_sizes_in_stylesheets():
    """The type scale is rem-based: no px font-size may remain."""
    for name, css in (("style.css", STYLE_CSS), ("living.css", LIVING_CSS)):
        for m in re.finditer(r"font-size\s*:[^;}]+", css):
            decl = m.group(0)
            assert not re.search(r"\dpx", decl), (name, decl.strip())


def test_text_size_control_in_nav(seeded_client):
    html = seeded_client.get("/").get_data(as_text=True)
    assert "action='/settings/text-size'" in html
    assert "name='text_size'" in html
    # Living nav (Home page) carries its own copy with a unique id.
    assert "id='text-size-select-lv'" in html
    # Classic topnav keeps the plain id; each appears exactly once.
    assert html.count("id='text-size-select'") == 1
    assert html.count("id='text-size-select-lv'") == 1


# ------------------------------------------------- keyboard shortcuts ---

def test_shortcuts_help_article_exists():
    article = help_mod.get_article("shortcuts")
    assert article is not None
    assert article["title"]
    assert article["href"].startswith("/")


def test_shortcuts_article_documents_real_shortcuts():
    body = help_mod.article_html("shortcuts")
    assert "Z" in body  # zen toggle
    assert "Escape" in body
    assert "arrow" in body.lower()
