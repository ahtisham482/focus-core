"""Roadmap 1.5a: store choke-point sealing + migration 0010.

The protector is swapped for a deterministic reverse-bytes fake so the
at-rest form is provably transformed (not just prefixed), while read
paths must hand back the original plaintext everywhere a title/URL
surfaces.
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from focuscore import columncrypto, migrations, store


def _today_str():
    """Local-date string matching store.get_today_blocks()'s own clock."""
    return datetime.now(UTC).astimezone().date().isoformat()


class _ReverseProtector:
    """Deterministic non-identity protector for Linux CI."""

    name = "reverse-test"

    def protect(self, data: bytes) -> bytes:
        return data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        return data[::-1]


class _ExplodingProtector:
    name = "exploding-test"

    def protect(self, data: bytes) -> bytes:
        raise RuntimeError("protect boom")

    def unprotect(self, data: bytes) -> bytes:
        raise RuntimeError("unprotect boom")


@pytest.fixture
def fake_protector(monkeypatch):
    fake = _ReverseProtector()
    monkeypatch.setattr(columncrypto, "_protector", fake)
    return fake


def _raw(db, sql, args=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _seed_activities(db, day="2026-10-01"):
    store.save_events(day, [{
        "ts": day + "T09:00:00", "duration": 600, "app": "Code.exe",
        "title": "Bank statement - Google Docs",
        "url": "https://docs.google.com/document/d/secret",
        "category": "Uncategorized", "score": 0,
        "override_score": None, "match_key": "app:code",
    }], path=db)


# ----------------------------------------------------------- activities ---

def test_activity_sealed_at_rest_and_opens_on_read(tmp_path, fake_protector):
    db = str(tmp_path / "a.db")
    store.init_db(db)
    _seed_activities(db)
    rows = _raw(db, "SELECT title, url FROM activities")
    assert rows[0]["title"].startswith(columncrypto.PREFIX)
    assert "Bank statement" not in rows[0]["title"]
    assert "secret" not in rows[0]["url"]

    day_rows = store.get_day_activities("2026-10-01", path=db)
    assert day_rows[0]["title"] == "Bank statement - Google Docs"
    assert day_rows[0]["url"] == "https://docs.google.com/document/d/secret"

    ranged = store.get_activities_range("2026-10-01T00:00:00",
                                        "2026-10-01T23:59:59", path=db)
    assert ranged[0]["title"] == "Bank statement - Google Docs"

    summary = store.get_day_summary("2026-10-01", path=db)
    titles = [u["title"] for u in summary["uncategorized"]]
    assert "Bank statement - Google Docs" in titles


def test_unopenable_value_shows_placeholder(tmp_path, fake_protector,
                                           monkeypatch):
    db = str(tmp_path / "b.db")
    store.init_db(db)
    _seed_activities(db)
    # Same DB, but this process cannot open its blobs (other user/PC).
    monkeypatch.setattr(columncrypto, "_protector", _ExplodingProtector())
    rows = store.get_day_activities("2026-10-01", path=db)
    assert rows[0]["title"] == columncrypto.PLACEHOLDER
    assert rows[0]["url"] == columncrypto.PLACEHOLDER


def test_focus_block_sealed(tmp_path, fake_protector):
    db = str(tmp_path / "c.db")
    store.init_db(db)
    store.record_block(1, _today_str() + "T10:00:00", "tube",
                       "Cats compilation", "https://youtube.com/watch?v=1",
                       -2, "Entertainment", path=db)
    raw = _raw(db, "SELECT title, url FROM focus_blocks")
    assert raw[0]["title"].startswith(columncrypto.PREFIX)
    assert "Cats compilation" not in raw[0]["title"]
    opened = store.get_today_blocks(path=db)
    assert opened[0]["title"] == "Cats compilation"
    assert opened[0]["url"] == "https://youtube.com/watch?v=1"


# ----------------------------------------------------------- timesheets ---

def test_timesheet_entry_sealed_round_trip_and_update(tmp_path,
                                                      fake_protector):
    db = str(tmp_path / "d.db")
    store.init_db(db)
    eid = store.create_entry("2026-10-01", "2026-10-01T09:00",
                             "2026-10-01T10:00", 60.0, "Work",
                             app="Code.exe", title="Secret roadmap doc",
                             status="accepted", path=db)
    raw = _raw(db, "SELECT title FROM timesheet_entries WHERE id = ?", (eid,))
    assert raw[0]["title"].startswith(columncrypto.PREFIX)
    assert "Secret roadmap" not in raw[0]["title"]

    assert store.get_entry(eid, path=db)["title"] == "Secret roadmap doc"
    assert store.list_entries(day="2026-10-01",
                              path=db)[0]["title"] == "Secret roadmap doc"

    store.update_entry(eid, {"title": "Edited secret"}, path=db)
    raw = _raw(db, "SELECT title FROM timesheet_entries WHERE id = ?", (eid,))
    assert raw[0]["title"].startswith(columncrypto.PREFIX)
    assert store.get_entry(eid, path=db)["title"] == "Edited secret"


def test_export_rows_decrypt_titles(tmp_path, fake_protector):
    from focuscore import exports
    db = str(tmp_path / "e.db")
    store.init_db(db)
    store.create_entry("2026-10-01", "2026-10-01T09:00",
                       "2026-10-01T10:00", 60.0, "Work",
                       app="Code.exe", title="Acme layoff plan",
                       status="accepted", path=db)
    rows = exports.build_export_rows("2026-10-01", "2026-10-01",
                                     include_app_details=True, path=db)
    titles = [r["title"] for r in rows if r["title"]]
    assert "Acme layoff plan" in titles
    rows_client = exports.build_export_rows("2026-10-01", "2026-10-01",
                                            path=db)
    assert all(r["title"] == "" for r in rows_client)


# --------------------------------------------------------- migration 10 ---

def _downgrade_to_v9_with_plaintext(db):
    """Simulate a pre-1.5a database: v9, all titles/URLs plaintext."""
    store.init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO activities (ts, duration, app, title, url,"
                     " category, score, override_score, match_key, day) "
                     "VALUES ('2026-10-01T09:00:00', 60, 'Code.exe',"
                     " 'Old plaintext title', 'https://example.com/x',"
                     " 'Work', 1, NULL, 'app:code', '2026-10-01')")
        conn.execute("INSERT INTO focus_blocks (session_id, ts, app, title,"
                     " url, score, category, action_taken, process_name,"
                     " window_handle) VALUES (1, ?,"
                     " 'tube', 'Old block title', NULL, -1, 'Fun', 'block',"
                     " '', 0)", (_today_str() + "T10:00:00",))
        conn.execute("INSERT INTO timesheet_entries (day, start_ts, end_ts,"
                     " minutes, category, app, title, project_id, task,"
                     " note, status, created_at) VALUES ('2026-10-01',"
                     " '2026-10-01T09:00', '2026-10-01T10:00', 60, 'Work',"
                     " 'Code.exe', 'Old entry title', NULL, '', '',"
                     " 'accepted', '2026-10-01T00:00:00')")
        conn.execute("DELETE FROM schema_migrations WHERE version = 10")
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
    finally:
        conn.close()


def test_migration_10_seals_existing_rows(tmp_path, fake_protector):
    db = str(tmp_path / "f.db")
    _downgrade_to_v9_with_plaintext(db)
    migrations.apply_migrations(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        title = conn.execute("SELECT title FROM activities").fetchone()[0]
        assert title.startswith(columncrypto.PREFIX)
        opened = store.get_day_activities("2026-10-01", path=db)
        assert opened[0]["title"] == "Old plaintext title"
        assert opened[0]["url"] == "https://example.com/x"
        blocks = store.get_today_blocks(path=db)
        assert "Old block title" in [b["title"] for b in blocks]
        entry = store.list_entries(day="2026-10-01", path=db)[0]
        assert entry["title"] == "Old entry title"
    finally:
        conn.close()


def test_migration_10_idempotent_and_resume(tmp_path, fake_protector):
    db = str(tmp_path / "g.db")
    _downgrade_to_v9_with_plaintext(db)
    # Resume case: one row already sealed before the pass runs.
    conn = sqlite3.connect(db)
    try:
        sealed = columncrypto.protect_text("Pre-sealed title")
        conn.execute("UPDATE activities SET title = ?", (sealed,))
        conn.commit()
    finally:
        conn.close()
    migrations.apply_migrations(db)
    migrations.apply_migrations(db)  # second pass: no-op
    opened = store.get_day_activities("2026-10-01", path=db)
    assert opened[0]["title"] == "Pre-sealed title"  # not double-sealed
    raw = _raw(db, "SELECT url FROM activities")
    assert raw[0]["url"].startswith(columncrypto.PREFIX)


def test_migration_10_protect_failure_keeps_plaintext(tmp_path, monkeypatch):
    db = str(tmp_path / "h.db")
    _downgrade_to_v9_with_plaintext(db)
    monkeypatch.setattr(columncrypto, "_protector", _ExplodingProtector())
    migrations.apply_migrations(db)  # must not raise / brick startup
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        title = conn.execute("SELECT title FROM activities").fetchone()[0]
        assert title == "Old plaintext title"
    finally:
        conn.close()
    # Rows keep displaying normally (legacy plaintext passes through).
    assert store.get_day_activities("2026-10-01",
                                    path=db)[0]["title"] == "Old plaintext title"
