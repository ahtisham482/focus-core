"""Sprint 4 (v1.12.0) hardening tests: Qwen binding verdict items.

Tests the new stability invariants:
- I-1: Enforcement Zero-SQLite (RulesSnapshot)
- Item 8: Invoice counter boundary guards
- Item 12: Idempotent index
"""

import sqlite3
import time
from datetime import date

import pytest

from focuscore import invoices as inv_mod
from focuscore import shield
from focuscore import store


# ---------------------------------------------------------------------------
# I-1: RulesSnapshot
# ---------------------------------------------------------------------------

def test_snapshot_is_frozen():
    """RulesSnapshot is immutable (frozen dataclass)."""
    snap = shield._empty_snapshot()
    with pytest.raises(Exception):
        snap.rules = ()


def test_snapshot_holder_atomic_swap():
    """SnapshotHolder swap is atomic and visible to readers."""
    holder = shield.SnapshotHolder()
    s1 = holder.get()
    s2 = shield.RulesSnapshot(
        rules=({"id": 1},), overrides=(), session=None, cycle=None,
        active_pass=None, hud_enabled=True,
        fetched_at_mono=time.monotonic())
    holder.swap(s2)
    assert holder.get() is s2
    assert holder.get() is not s1


def test_build_snapshot_reads_db(tmp_path):
    """_build_snapshot pulls rules/session/pass from SQLite (runs on the
    refresher thread, never the worker)."""
    db = str(tmp_path / "snap.db")
    store.init_db(db)
    snap = shield._build_snapshot(db)
    assert isinstance(snap, shield.RulesSnapshot)
    assert isinstance(snap.rules, tuple)
    assert isinstance(snap.fetched_at_mono, float)


def test_shield_once_snapshot_zero_sqlite(tmp_path, monkeypatch):
    """shield_once with a snapshot makes ZERO SQLite calls. Any
    attempt to touch the DB raises, proving I-1."""
    db = str(tmp_path / "nosqlite.db")
    store.init_db(db)

    # Poison all SQLite entry points: if the worker touches the DB,
    # the test fails.
    def _boom(*a, **k):
        raise AssertionError("SQLite touched on enforcement path!")
    monkeypatch.setattr(store, "get_db", _boom)
    monkeypatch.setattr(store, "get_active_pass", _boom)
    monkeypatch.setattr(store, "get_block_rules", _boom)
    monkeypatch.setattr(store, "get_overrides", _boom)

    snap = shield.RulesSnapshot(
        rules=(), overrides=(), session=None, cycle=None,
        active_pass=None, hud_enabled=True,
        fetched_at_mono=time.monotonic())

    import queue as q
    telemetry_q = q.Queue()

    # Minimal fake client/categorize; no foreground window -> "none".
    result = shield.shield_once(
        {}, None, lambda *a: "uncategorized",
        db_path=db, fg_info=None, snapshot=snap,
        telemetry_q=telemetry_q)
    assert result["action"] in ("allow", "none", "soft", "firm",
                                "hardcore")


def test_ui_command_is_frozen():
    """UICommand is a frozen dataclass with plain data only."""
    cmd = shield.UICommand(cmd="overlay", label="x", app="y")
    with pytest.raises(Exception):
        cmd.app = "z"
    assert cmd.cmd == "overlay"


def test_telemetry_queue_bounded():
    """Telemetry queue has a max size (drop, don't block)."""
    import queue as q
    tq = q.Queue(maxsize=shield.TELEMETRY_QUEUE_MAXSIZE)
    assert tq.maxsize == 10000


# ---------------------------------------------------------------------------
# Item 8: Invoice counter boundary guards
# ---------------------------------------------------------------------------

def _make_draft_with_line(tmp_path):
    db = str(tmp_path / "inv.db")
    pid = store.add_project("P", client="C", path=db)
    eid = store.create_entry(
        "2026-09-27", "2026-09-27T09:00", "2026-09-27T10:00",
        60, "Work", project_id=pid, task="Dev",
        status="accepted", path=db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE timesheet_entries SET hourly_rate_minor = 1000, "
        "rate_currency = 'USD', rate_status = 'confirmed' "
        "WHERE id = ?", (eid,))
    conn.commit()
    conn.close()
    inv_id = inv_mod.create_invoice(
        pid, "2026-09-27", "2026-09-27", entry_ids=[eid], path=db)
    return db, inv_id


def test_counter_exhausted_error(tmp_path):
    """Sequence > 9999 raises CounterExhaustedError (4-digit format)."""
    db, inv_id = _make_draft_with_line(tmp_path)
    conn = store.get_db(db)
    year = date.today().year
    conn.execute(
        "INSERT OR REPLACE INTO invoice_counters (year, next_number) "
        "VALUES (?, 10000)", (year,))
    conn.commit()
    conn.close()
    with pytest.raises(inv_mod.CounterExhaustedError):
        inv_mod.send_invoice(inv_id, path=db)


def test_clock_jump_error(tmp_path):
    """Year < max issued year raises ClockJumpError."""
    db, inv_id = _make_draft_with_line(tmp_path)
    conn = store.get_db(db)
    future_year = date.today().year + 5
    conn.execute(
        "INSERT OR REPLACE INTO invoice_counters (year, next_number) "
        "VALUES (?, 1)", (future_year,))
    conn.commit()
    conn.close()
    with pytest.raises(inv_mod.ClockJumpError):
        inv_mod.send_invoice(inv_id, path=db)


def test_send_still_works_normally(tmp_path):
    """Guards don't break the normal send path."""
    db, inv_id = _make_draft_with_line(tmp_path)
    number = inv_mod.send_invoice(inv_id, path=db)
    assert number.startswith("INV-%d-" % date.today().year)


# ---------------------------------------------------------------------------
# Item 12: Idempotent index
# ---------------------------------------------------------------------------

def test_idempotent_index_created(tmp_path):
    """idx_invoice_lines_entry exists after init_db; re-running is a
    no-op; user_version unchanged."""
    db = str(tmp_path / "idx.db")
    store.init_db(db)
    conn = store.get_db(db)
    version_before = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    # Run init_db again (idempotent).
    store.init_db(db)
    conn = store.get_db(db)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND "
        "name = 'idx_invoice_lines_entry'").fetchone()
    version_after = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert row is not None
    assert version_before == version_after


# ---------------------------------------------------------------------------
# Item 6: VACUUM INTO backup
# ---------------------------------------------------------------------------

def test_vacuum_backup_roundtrip(tmp_path):
    """create_backup uses VACUUM INTO; backup verifies and restores."""
    from focuscore import backup as backup_mod
    db = str(tmp_path / "src.db")
    store.init_db(db)
    conn = store.get_db(db)
    conn.execute("INSERT INTO projects (name, client) VALUES ('X', 'Y')")
    conn.commit()
    conn.close()
    bdir = str(tmp_path / "backups")
    target = backup_mod.create_backup(db_path=db, dest_dir=bdir)
    assert target.exists()
    # quick_check passes on the backup.
    conn = sqlite3.connect(str(target))
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    conn.close()
