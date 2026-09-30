"""Roadmap 1.7: SQLite workstream.

Covers migration 0011 (orphan quarantine), FK enforcement in
``store.get_db``, the migrations-applied flag, lazy DB-path
resolution, the nightly integrity helper + tray wiring, and a
threaded write-burst discipline check. All databases are tmp files;
the repo dev DB is never touched.
"""

import json
import os
import sqlite3
import threading

import pytest

from focuscore import migrations, paths, store, tray


# ------------------------------------------------------------- helpers ---

def _raw(db):
    """Raw connection with FK enforcement OFF (the historical state)."""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _fk_violations(db):
    conn = _raw(db)
    try:
        return conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def _make_v10_db(db, backups):
    """A fully migrated v11 DB rewound to look exactly like v10."""
    migrations.apply_migrations(db, dest_backup_dir=backups)
    conn = _raw(db)
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version = 11")
        conn.execute("DROP TABLE IF EXISTS orphaned_rows")
        conn.execute("PRAGMA user_version = 10")
        conn.commit()
    finally:
        conn.close()


def _dump(db, sql):
    conn = _raw(db)
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


# ------------------------------------------------- 0011: quarantine ---

def test_0011_quarantines_orphans_and_recomputes_draft_totals(tmp_path):
    db = str(tmp_path / "wild.db")
    backups = str(tmp_path / "backups")
    _make_v10_db(db, backups)
    conn = _raw(db)
    try:
        # Legitimate rows.
        conn.execute(
            "INSERT INTO projects (id, name, client, created_at) "
            "VALUES (1, 'Acme', 'Client', '2026-01-01T00:00:00')")
        conn.execute(
            "INSERT INTO focus_sessions (id, label, planned_minutes, "
            "started_at, planned_end_at, status, block_level) VALUES "
            "(1, 'Deep', 25, '2026-01-01T09:00:00', "
            "'2026-01-01T09:25:00', 'completed', 'strict')")
        conn.execute(
            "INSERT INTO timesheet_entries (id, day, start_ts, end_ts, "
            "minutes, category, status, created_at) VALUES (1, "
            "'2026-01-01', '2026-01-01T09:00', '2026-01-01T10:00', 60, "
            "'Work', 'accepted', '2026-01-01T00:00:00')")
        # Draft invoice with deliberately stale stored totals.
        conn.execute(
            "INSERT INTO invoices (id, project_id, status, currency, "
            "subtotal_minor, discount_pct, discount_amount_minor, "
            "tax_pct, tax_amount_minor, total_minor, created_at, "
            "updated_at) VALUES (1, 1, 'draft', 'USD', 9999, 10, 0, 20, "
            "0, 9999, '2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        conn.execute(
            "INSERT INTO invoice_lines (id, invoice_id, "
            "timesheet_entry_id, amount_minor_units) "
            "VALUES (1, 1, 1, 1000)")
        conn.execute(
            "INSERT INTO xp_ledger (id, session_id, day, base_xp, "
            "total_xp, awarded_at) VALUES (1, 1, '2026-01-01', 10, "
            "10, '2026-01-01T10:00:00')")
        conn.execute(
            "INSERT INTO badges (id, badge_key, session_id, "
            "awarded_at) VALUES (1, 'good', 1, '2026-01-01T10:00:00')")
        conn.execute(
            "INSERT INTO session_cycles (id, session_id, kind, "
            "planned_minutes, started_at) VALUES (1, 1, 'work', 25, "
            "'2026-01-01T09:00:00')")
        conn.execute(
            "INSERT INTO project_budget_ledger (id, project_id, "
            "period_type, period_start, effective_from_utc, "
            "created_at_utc) VALUES (1, 1, 'week', '2026-01-05', "
            "'2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        # Sent invoice: frozen record (totals must NOT be recomputed).
        # Seeded as draft and flipped to sent AFTER its lines exist:
        # the no_add_sent trigger would (correctly) reject adding
        # a line to an already-sent invoice.
        conn.execute(
            "INSERT INTO invoices (id, project_id, status, currency, "
            "subtotal_minor, discount_pct, discount_amount_minor, "
            "tax_pct, tax_amount_minor, total_minor, created_at, "
            "updated_at) VALUES (2, 1, 'draft', 'USD', 5000, 0, 0, 0, "
            "0, 5000, '2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        # Orphans (inserted with enforcement off, as in the wild).
        conn.execute(  # line -> missing entry (draft parent)
            "INSERT INTO invoice_lines (id, invoice_id, "
            "timesheet_entry_id, amount_minor_units) "
            "VALUES (2, 1, 999999, 2000)")
        conn.execute(  # line -> missing invoice
            "INSERT INTO invoice_lines (id, invoice_id, "
            "timesheet_entry_id, amount_minor_units) "
            "VALUES (3, 888888, 1, 500)")
        conn.execute(  # line of SENT invoice -> missing entry
            # (seeded via draft: the no_add_sent trigger guards sent)
            "INSERT INTO invoice_lines (id, invoice_id, "
            "timesheet_entry_id, amount_minor_units) "
            "VALUES (4, 2, 999998, 700)")
        conn.execute(  # xp -> missing session
            "INSERT INTO xp_ledger (id, session_id, day, base_xp, "
            "total_xp, awarded_at) VALUES (2, 777777, '2026-01-01', "
            "5, 5, '2026-01-01T10:00:00')")
        conn.execute(  # badge -> missing session
            "INSERT INTO badges (id, badge_key, session_id, "
            "awarded_at) VALUES (2, 'orphan', 777777, "
            "'2026-01-01T10:00:00')")
        conn.execute(  # cycle -> missing session
            "INSERT INTO session_cycles (id, session_id, kind, "
            "planned_minutes, started_at) VALUES (2, 777777, 'work', "
            "25, '2026-01-01T09:00:00')")
        conn.execute(  # ledger -> missing project
            "INSERT INTO project_budget_ledger (id, project_id, "
            "period_type, period_start, effective_from_utc, "
            "created_at_utc) VALUES (2, 666666, 'week', '2026-01-05', "
            "'2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        conn.execute(  # invoice -> missing project (parent orphan)
            "INSERT INTO invoices (id, project_id, status, currency, "
            "created_at, updated_at) VALUES (3, 666666, 'draft', "
            "'USD', '2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        conn.execute(  # grandchild: line of the orphan invoice
            "INSERT INTO invoice_lines (id, invoice_id, "
            "timesheet_entry_id, amount_minor_units) "
            "VALUES (5, 3, 1, 700)")
        conn.execute(  # entry -> missing invoice
            "INSERT INTO timesheet_entries (id, day, start_ts, "
            "end_ts, minutes, category, status, created_at, "
            "invoice_id) VALUES (2, '2026-01-02', '2026-01-02T09:00', "
            "'2026-01-02T10:00', 30, 'Work', 'accepted', "
            "'2026-01-02T00:00:00', 888888)")
        conn.execute(
            "UPDATE invoices SET status = 'sent' WHERE id = 2")
        conn.commit()
    finally:
        conn.close()
    legit_before = {
        "projects": _dump(db, "SELECT * FROM projects WHERE id = 1"),
        "entries": _dump(
            db, "SELECT * FROM timesheet_entries WHERE id = 1"),
        "line1": _dump(db, "SELECT * FROM invoice_lines WHERE id = 1"),
        "xp": _dump(db, "SELECT * FROM xp_ledger WHERE id = 1"),
        "sent_invoice": _dump(db, "SELECT * FROM invoices WHERE id = 2"),
    }

    ver = migrations.apply_migrations(db, dest_backup_dir=backups)

    assert ver == 11
    assert _fk_violations(db) == []
    quarantined = _dump(db, "SELECT * FROM orphaned_rows ORDER BY id")
    by_table = {}
    for row in quarantined:
        by_table.setdefault(row["source_table"], []).append(row)
    assert len(by_table["invoice_lines"]) == 4  # ids 2, 3, 4, 5
    assert len(by_table["invoices"]) == 1
    assert len(by_table["xp_ledger"]) == 1
    assert len(by_table["badges"]) == 1
    assert len(by_table["session_cycles"]) == 1
    assert len(by_table["project_budget_ledger"]) == 1
    assert len(by_table["timesheet_entries"]) == 1
    payload = json.loads(by_table["invoice_lines"][0]["row_json"])
    assert payload["amount_minor_units"] in (2000, 500, 700)
    assert by_table["invoice_lines"][0]["parent_table"] in (
        "timesheet_entries", "invoices")
    # Legitimate rows untouched; sent invoice totals stay frozen.
    assert _dump(db, "SELECT * FROM projects WHERE id = 1") == (
        legit_before["projects"])
    assert _dump(db, "SELECT * FROM timesheet_entries WHERE id = 1") == (
        legit_before["entries"])
    assert _dump(db, "SELECT * FROM invoice_lines WHERE id = 1") == (
        legit_before["line1"])
    assert _dump(db, "SELECT * FROM xp_ledger WHERE id = 1") == (
        legit_before["xp"])
    assert _dump(db, "SELECT * FROM invoices WHERE id = 2") == (
        legit_before["sent_invoice"])
    # Draft totals recomputed from the one remaining line:
    # subtotal 1000, discount 10% -> 100, tax 20% of 900 -> 180.
    draft = _dump(db, "SELECT * FROM invoices WHERE id = 1")[0]
    assert draft["subtotal_minor"] == 1000
    assert draft["discount_amount_minor"] == 100
    assert draft["tax_amount_minor"] == 180
    assert draft["total_minor"] == 1080
    # Re-running is a no-op: nothing new is quarantined.
    migrations.apply_migrations(db, dest_backup_dir=backups)
    assert _dump(db, "SELECT * FROM orphaned_rows ORDER BY id") == (
        quarantined)


def test_0011_clean_db_quarantines_nothing(tmp_path):
    db = str(tmp_path / "clean.db")
    migrations.apply_migrations(db, dest_backup_dir=str(tmp_path / "b"))
    assert _dump(db, "SELECT * FROM orphaned_rows") == []
    assert _fk_violations(db) == []


# --------------------------------------------------- FK enforcement ---

def _parents(db):
    conn = store.get_db(db)
    try:
        conn.execute(
            "INSERT INTO projects (id, name, client, created_at) "
            "VALUES (1, 'P', '', '2026-01-01T00:00:00')")
        conn.execute(
            "INSERT INTO invoices (id, project_id, status, currency, "
            "created_at, updated_at) VALUES (1, 1, 'draft', 'USD', "
            "'2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        conn.execute(
            "INSERT INTO timesheet_entries (id, day, start_ts, "
            "end_ts, minutes, category, status, created_at) VALUES "
            "(1, '2026-01-01', '2026-01-01T09:00', "
            "'2026-01-01T10:00', 60, 'Work', 'accepted', "
            "'2026-01-01T00:00:00')")
        conn.commit()
    finally:
        conn.close()


def test_get_db_enforces_foreign_keys(tmp_path):
    db = str(tmp_path / "fk.db")
    store.init_db(db)
    _parents(db)
    conn = store.get_db(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO invoice_lines (invoice_id, "
                "timesheet_entry_id, amount_minor_units) "
                "VALUES (1, 424242, 100)")
        conn.rollback()
        conn.execute(
            "INSERT INTO invoice_lines (invoice_id, "
            "timesheet_entry_id, amount_minor_units) "
            "VALUES (1, 1, 100)")
        conn.commit()
        # ON DELETE RESTRICT is live too.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM timesheet_entries WHERE id = 1")
        conn.rollback()
    finally:
        conn.close()


# ------------------------------------------- migrations-applied flag ---

def test_second_apply_opens_no_connection(tmp_path, monkeypatch):
    db = str(tmp_path / "flag.db")
    backups = str(tmp_path / "backups")
    assert migrations.apply_migrations(
        db, dest_backup_dir=backups) == 11
    real_connect = sqlite3.connect
    calls = []

    def counting(*args, **kwargs):
        calls.append(args)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", counting)
    assert migrations.apply_migrations(
        db, dest_backup_dir=backups) == 11
    assert calls == []
    monkeypatch.undo()
    # Replacing the file at the same path invalidates the flag.
    other = str(tmp_path / "other.db")
    migrations.apply_migrations(other, dest_backup_dir=backups)
    os.replace(other, db)
    monkeypatch.setattr(sqlite3, "connect", counting)
    calls.clear()
    assert migrations.apply_migrations(
        db, dest_backup_dir=backups) == 11
    assert calls != []


# ------------------------------------------------ lazy DB-path (1.7) ---

def test_lazy_default_db_path_resolution(tmp_path, monkeypatch):
    # (monkeypatch.delattr cannot see a PEP 562 attribute on some
    # pytest versions; pop the real dict entry instead.)
    store.__dict__.pop("DEFAULT_DB_PATH", None)
    lazy = tmp_path / "lazy.db"
    monkeypatch.setattr(paths, "db_path", lambda: lazy)
    store.init_db()
    assert lazy.exists()
    assert store.DEFAULT_DB_PATH == lazy
    # Monkeypatched module attribute still wins for get_db/init_db.
    monkeypatch.undo()
    store.__dict__.pop("DEFAULT_DB_PATH", None)
    patched = str(tmp_path / "patched.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", patched)
    store.init_db()
    assert os.path.exists(patched)


# ------------------------------------------------ integrity helpers ---

def test_run_integrity_check_ok_orphan_corrupt(tmp_path):
    db = str(tmp_path / "ok.db")
    store.init_db(db)
    ok, problems = store.run_integrity_check(db)
    assert ok is True and problems == []

    conn = _raw(db)
    try:
        conn.execute(
            "INSERT INTO xp_ledger (session_id, day, base_xp, "
            "total_xp, awarded_at) VALUES (987654, '2026-01-01', 1, "
            "1, '2026-01-01T00:00:00')")
        conn.commit()
    finally:
        conn.close()
    ok, problems = store.run_integrity_check(db)
    assert ok is False
    assert any("xp_ledger" in p for p in problems)

    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite database" * 16)
    ok, problems = store.run_integrity_check(str(bad))
    assert ok is False and problems

    ok, problems = store.run_integrity_check(str(tmp_path / "gone.db"))
    assert ok is False and problems


class _FakeIcon:
    def __init__(self):
        self.notes = []

    def notify(self, message, title=None):
        self.notes.append((message, title))


def test_tray_nightly_checks_notify_on_failure(tmp_path, monkeypatch):
    db = str(tmp_path / "tray.db")
    store.init_db(db)
    app = tray.TrayApp(db_path=db)
    icon = _FakeIcon()
    app.icon = icon
    monkeypatch.setattr(store, "checkpoint_wal", lambda path=None: None)
    monkeypatch.setattr(
        store, "run_integrity_check",
        lambda path=None: (False, ["foreign_key_check: xp_ledger"]))
    app._run_nightly_checks()
    assert icon.notes and "Backup" in icon.notes[0][0]
    icon.notes.clear()
    monkeypatch.setattr(
        store, "run_integrity_check", lambda path=None: (True, []))
    app._run_nightly_checks()
    assert icon.notes == []
    app.icon = None  # absent icon must never raise
    monkeypatch.setattr(
        store, "run_integrity_check",
        lambda path=None: (False, ["integrity_check: broken"]))
    app._run_nightly_checks()


# ------------------------------------------------- threaded write burst ---

def test_threaded_write_burst(tmp_path):
    db = str(tmp_path / "burst.db")
    store.init_db(db)
    threads_n, writes = 6, 20
    errors = []

    def worker(tid):
        day = "2026-10-%02d" % (tid + 1)
        try:
            for i in range(writes):
                store.create_entry(
                    day, day + "T09:00", day + "T10:00", 30, "Work",
                    path=db)
                store.record_block(
                    0, day + "T10:00:00", "app.exe", "t", "", -1,
                    "Fun", path=db)
                store.save_events(day, [{
                    "ts": day + "T09:00:00", "duration": 60,
                    "app": "Code.exe", "title": "t", "url": "",
                    "category": "Work", "score": 1,
                    "match_key": "app:code",
                }], path=db)
        except Exception as exc:  # noqa: BLE001 - collected + asserted
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,))
               for t in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    conn = _raw(db)
    try:
        entries = conn.execute(
            "SELECT COUNT(*) FROM timesheet_entries").fetchone()[0]
        blocks = conn.execute(
            "SELECT COUNT(*) FROM focus_blocks").fetchone()[0]
        acts = conn.execute(
            "SELECT COUNT(*) FROM activities").fetchone()[0]
    finally:
        conn.close()
    assert entries == threads_n * writes
    assert blocks == threads_n * writes
    assert acts == threads_n  # one live event per thread-day
