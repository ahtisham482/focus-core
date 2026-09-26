"""Phase 8: migration 6 (session modes + session_cycles)."""

import sqlite3

import pytest

from focuscore import migrations, store


def _tables(db):
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def _indexes(db):
    conn = sqlite3.connect(db)
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index'")}
    finally:
        conn.close()


def test_migration_6_normalizes_legacy_sessions(tmp_path):
    db = str(tmp_path / "m6b.db")
    migrations.apply_migrations(db)  # full schema, incl. v6
    conn = sqlite3.connect(db)
    try:
        # Simulate a pre-Phase-8 database: v6 not yet applied, and a
        # legacy session carrying migration-3's 'pomodoro' default.
        conn.execute("DELETE FROM schema_migrations WHERE version >= 6")
        conn.execute("PRAGMA user_version = 5")
        conn.execute("DROP TABLE IF EXISTS session_cycles")
        conn.execute("DROP INDEX IF EXISTS uq_one_active_cycle")
        conn.execute("DROP INDEX IF EXISTS idx_cycles_session")
        conn.execute(
            "INSERT INTO focus_sessions (label, planned_minutes, "
            "started_at, planned_end_at, status, block_level, "
            "enforcement_mode, session_type) VALUES "
            "('Old work', 50, '2026-09-01T09:00:00', "
            "'2026-09-01T09:50:00', 'completed', 'strict', 'strict', "
            "'pomodoro')")
        conn.commit()
    finally:
        conn.close()
    migrations.apply_migrations(db)  # re-applies v6 only
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT session_type FROM focus_sessions").fetchone()
    finally:
        conn.close()
    # Every old session was a classic fixed timer.
    assert row[0] == "classic"


def test_migration_6_idempotent_and_preserves(tmp_path):
    db = str(tmp_path / "m6c.db")
    migrations.apply_migrations(db)
    sid = store.create_session("Keep me", 25, "2026-09-26T09:00:00",
                               "2026-09-26T09:25:00", "strict",
                               path=db, session_type="pomodoro")
    cid = store.start_cycle(sid, "work", 25, "2026-09-26T09:00:00",
                            1000.0, path=db)
    migrations.apply_migrations(db)  # re-run: no-op
    assert store.get_session(sid, path=db)["label"] == "Keep me"
    assert store.get_active_cycle(sid, path=db)["id"] == cid
    assert store.get_setting("audio_cues", path=db) == "1"


def test_partial_unique_index_one_active_cycle(tmp_path):
    db = str(tmp_path / "m6d.db")
    migrations.apply_migrations(db)
    sid = store.create_session("Idx", 25, "2026-09-26T09:00:00",
                               "2026-09-26T09:25:00", "strict", path=db)
    store.start_cycle(sid, "work", 25, "2026-09-26T09:00:00", 1000.0,
                      path=db)
    with pytest.raises(sqlite3.IntegrityError):
        store.start_cycle(sid, "break", 5, "2026-09-26T09:25:00",
                          2000.0, path=db)
    # After ending the first, a new one is allowed.
    first = store.get_active_cycle(sid, path=db)
    store.end_cycle(first["id"], "completed", "2026-09-26T09:25:00",
                    path=db)
    second = store.start_cycle(sid, "break", 5, "2026-09-26T09:25:00",
                               2000.0, path=db)
    assert second != first["id"]
