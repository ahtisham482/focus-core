"""Roadmap 0.3: friendly 404/500 pages + logging foundation (TDD).

- 404 renders a branded plain-English page with a recovery link; no
  traceback or internals leak.
- 500 renders the friendly page AND logs the exception server-side.
- A sample of the top-20 silent-swallow sites logs on failure.
"""

import logging
from datetime import datetime

import pytest

import dashboard.app as dash_app  # noqa: F401  (import registers routes)
from dashboard.app import app


@app.route("/_test_500_boom")
def _boom():
    raise RuntimeError("simulated 500 failure")


@pytest.fixture()
def client():
    app.config["TESTING"] = False  # real error handlers, not propagation
    with app.test_client() as c:
        yield c


def test_404_page_is_friendly(client):
    r = client.get("/no-such-page-here-xyz")
    assert r.status_code == 404
    html = r.get_data(as_text=True)
    assert "This page" in html
    assert "Back to Home" in html
    assert "href='/'" in html
    assert "Traceback" not in html
    assert "RuntimeError" not in html


def test_404_keeps_brand_chrome(client):
    # Branded: the normal page shell (nav + footer), not a bare string.
    r = client.get("/no-such-page-here-xyz")
    html = r.get_data(as_text=True)
    assert "Focus Core" in html
    assert "topnav" in html


def test_500_page_is_friendly_and_logs(client, caplog):
    with caplog.at_level(logging.ERROR):
        r = client.get("/_test_500_boom")
    assert r.status_code == 500
    html = r.get_data(as_text=True)
    assert "Something went wrong" in html
    assert "Back to Home" in html
    assert "href='/'" in html
    # No internals leak to the user.
    assert "Traceback" not in html
    assert "simulated 500 failure" not in html
    # ...but the exception IS logged server-side.
    assert any("Unhandled exception" in rec.getMessage()
               for rec in caplog.records)


def test_500_page_shows_request_id(client):
    import re
    r = client.get("/_test_500_boom")
    assert r.status_code == 500
    html = r.get_data(as_text=True)
    m = re.search(r"req-[0-9a-f]{8}", html)
    assert m, "500 page should show a quotable request id"


def test_500_log_line_carries_same_request_id(client, caplog):
    import re
    with caplog.at_level(logging.ERROR):
        r = client.get("/_test_500_boom")
    html = r.get_data(as_text=True)
    m = re.search(r"req-[0-9a-f]{8}", html)
    assert m, "500 page should show a quotable request id"
    rid = m.group(0)
    assert any(rid in rec.getMessage() for rec in caplog.records), (
        "the same request id must appear in the logged traceback line")


def test_focus_page_logs_settle_failure(client, monkeypatch, caplog):
    """Sample site 1: /focus settle_session failure must leave a trace."""
    from focuscore import focus as focus_mod

    def _boom_settle(db_path=None, **_kw):
        raise RuntimeError("settle blew up")

    monkeypatch.setattr(focus_mod, "settle_session", _boom_settle)
    with caplog.at_level(logging.ERROR):
        r = client.get("/focus")
    assert r.status_code == 200
    assert any("settle_session" in rec.getMessage()
               for rec in caplog.records)


def test_cues_toggle_logs_setting_failure(client, monkeypatch, caplog):
    """Sample site 2: POST /focus/cues drop must leave a trace."""
    from focuscore import store as store_mod

    def _boom_set(key, value, path=None):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store_mod, "set_setting", _boom_set)
    with caplog.at_level(logging.ERROR):
        r = client.post("/focus/cues", data={"audio_cues": "1"})
    assert r.status_code in (200, 302)
    assert any("set_setting" in rec.getMessage()
               for rec in caplog.records)


def test_close_active_cycle_logs(monkeypatch, caplog):
    """Sample site 4: _close_active_cycle failure must leave a trace."""
    from focuscore import focus as focus_mod
    from focuscore import store as store_mod

    def _boom_cycle(session_id, path=None):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store_mod, "get_active_cycle", _boom_cycle)
    with caplog.at_level(logging.ERROR):
        focus_mod._close_active_cycle("sess-x", "completed",
                                     datetime.now(), db_path=":memory:")
    assert any("close_active_cycle" in rec.getMessage()
               for rec in caplog.records)


def test_set_setting_logs(monkeypatch, caplog):
    """Sample site 3: set_setting failure must leave a trace."""
    from focuscore import store as store_mod

    def _boom_db(path=None):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store_mod, "get_db", _boom_db)
    with caplog.at_level(logging.ERROR):
        store_mod.set_setting("audio_cues", "1", path=":memory:")
    assert any("set_setting" in rec.getMessage()
               for rec in caplog.records)


def test_pass_active_logs_primary_failure(monkeypatch, caplog):
    """Sample site 15: pass_active's primary lookup failure must be
    logged (it silently fell back to in-memory/JSONL before)."""
    from focuscore import shield as shield_mod
    from focuscore import store as store_mod

    def _boom_pass(now=None, path=None):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store_mod, "get_active_pass", _boom_pass)
    with caplog.at_level(logging.ERROR):
        shield_mod.pass_active(now=datetime.now())
    assert any("get_active_pass" in rec.getMessage()
               or "pass_active" in rec.getMessage()
               for rec in caplog.records)


def test_backup_row_count_logs_table_failure(caplog):
    """Sample site 6: backup row-count per-table failure must be logged."""
    import sqlite3

    import focuscore.backup as backup_mod

    conn = sqlite3.connect(":memory:")
    conn.execute('CREATE TABLE "we""ird" (a TEXT)')  # COUNT(*) below
    # becomes: SELECT COUNT(*) FROM "we"ird" -> syntax error -> logged
    with caplog.at_level(logging.ERROR):
        total = backup_mod._total_rowcount(conn)
    assert total == 0
    assert any("row count" in rec.getMessage() or "table" in rec.getMessage()
               for rec in caplog.records)
