"""Roadmap 1.7 repair 2: encoded integrity-check URI + tray loop guard.

Two critic fixes under test:

1. ``store.run_integrity_check`` and ``diagnostics._integrity`` build
   a ``file:...?mode=ro`` URI; a DB path containing ``#`` or ``?`` made
   SQLite silently open (and create) the WRONG file and report a false
   "ok". The path is now URL-quoted (``urllib.request.pathname2url``).
2. The tray nightly loop must survive ``store.run_integrity_check``
   raising: the call + failure notification sit inside try/except that
   logs and returns normally.

All DBs live under tmp_path (suite tmp-only discipline).
"""

import os
import sqlite3

import pytest

from focuscore import diagnostics, store, tray


def _raw(db):
    """Raw connection with FK enforcement OFF (the historical state)."""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _dir_listing(path):
    return sorted(os.listdir(path))


def _assert_no_stray_dbs(tmp_path, name, before):
    """Fail if anything beyond the real DB's WAL sidecars appeared.

    The pre-fix bug created stray files like ``my db `` / ``what``
    (the path prefix before ``#`` / ``?``). Opening a WAL DB read-only
    legitimately recreates its own ``-wal``/``-shm`` sidecars, so
    those -- and only those -- may be new.
    """
    after = _dir_listing(tmp_path)
    new = sorted(set(after) - set(before))
    assert new == [] or all(
        f in (name + "-wal", name + "-shm") for f in new)


def _seed_orphan(db):
    """Insert an xp_ledger row pointing at a missing session."""
    conn = _raw(db)
    try:
        conn.execute(
            "INSERT INTO xp_ledger (session_id, day, base_xp, "
            "total_xp, awarded_at) VALUES (987654, '2026-01-01', 1, "
            "1, '2026-01-01T00:00:00')")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["my db #1.db", "what?db.db"])
def test_integrity_check_orphan_at_uri_special_path(tmp_path, name):
    db = str(tmp_path / name)
    store.init_db(db)
    _seed_orphan(db)
    before = _dir_listing(tmp_path)
    ok, problems = store.run_integrity_check(db)
    assert ok is False
    assert any("xp_ledger" in p for p in problems)
    # No stray sibling files created by the URI bug.
    _assert_no_stray_dbs(tmp_path, name, before)


@pytest.mark.parametrize("name", ["my db #1.db", "what?db.db"])
def test_integrity_check_healthy_at_uri_special_path(tmp_path, name):
    db = str(tmp_path / name)
    store.init_db(db)
    before = _dir_listing(tmp_path)
    ok, problems = store.run_integrity_check(db)
    assert ok is True and problems == []
    _assert_no_stray_dbs(tmp_path, name, before)


@pytest.mark.parametrize("name", ["my db #1.db", "what?db.db"])
def test_diagnostics_integrity_at_uri_special_path(tmp_path, name):
    db = str(tmp_path / name)
    store.init_db(db)
    before = _dir_listing(tmp_path)
    assert diagnostics._integrity(db) == "ok"
    _assert_no_stray_dbs(tmp_path, name, before)


class _FakeIcon:
    def __init__(self):
        self.notes = []

    def notify(self, message, title=None):
        self.notes.append((message, title))


def test_tray_nightly_checks_survives_raising_helper(tmp_path,
                                                     monkeypatch):
    db = str(tmp_path / "tray.db")
    store.init_db(db)
    app = tray.TrayApp(db_path=db)
    icon = _FakeIcon()
    app.icon = icon
    monkeypatch.setattr(store, "checkpoint_wal", lambda path=None: None)

    def boom(path=None):
        raise RuntimeError("helper exploded")

    monkeypatch.setattr(store, "run_integrity_check", boom)
    app._run_nightly_checks()  # must return without raising
    assert icon.notes == []
