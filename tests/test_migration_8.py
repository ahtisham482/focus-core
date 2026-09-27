"""Phase 10: migration 8 (invoicing schema). Qwen audit M1/M2/E/G."""

import sqlite3

import pytest

from focuscore import migrations, store


def _connect(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _cols(db, table):
    conn = _connect(db)
    try:
        return {r["name"] for r in conn.execute(
            "PRAGMA table_info(%s)" % table)}
    finally:
        conn.close()


def _downgrade_to_v7(db):
    """Surgically remove migration-8 objects to simulate a pre-v10 DB."""
    conn = _connect(db)
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version >= 8")
        conn.execute("PRAGMA user_version = 7")
        for t in ("invoice_payments", "invoice_lines", "invoices",
                  "invoice_counters"):
            conn.execute("DROP TABLE IF EXISTS %s" % t)
        conn.execute("DROP INDEX IF EXISTS idx_invoices_status")
        conn.execute("DROP INDEX IF EXISTS idx_invoices_project")
        conn.execute("DROP INDEX IF EXISTS idx_invoice_lines_invoice")
        conn.execute("DROP INDEX IF EXISTS idx_invoice_lines_entry")
        conn.execute("DROP INDEX IF EXISTS idx_invoice_payments_invoice")
        conn.execute("DROP INDEX IF EXISTS idx_timesheet_invoice_id")
        for trig in ("trg_invoices_immutable",
                     "trg_invoice_lines_immutable_upd",
                     "trg_invoice_lines_immutable_del",
                     "trg_invoice_lines_no_add_sent",
                     "trg_invoice_payments_guard",
                     "trg_timesheet_invoice_link_guard"):
            conn.execute("DROP TRIGGER IF EXISTS %s" % trig)
        # Best-effort: remove the added columns if SQLite supports it.
        for col in ("invoice_id",):
            try:
                conn.execute(
                    "ALTER TABLE timesheet_entries DROP COLUMN %s" % col)
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute("ALTER TABLE projects DROP COLUMN rollover_enabled")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def test_migration_8_applies_cleanly_and_is_idempotent(tmp_path):
    db = str(tmp_path / "m8.db")
    migrations.apply_migrations(db)
    migrations.apply_migrations(db)  # second run must not raise
    conn = _connect(db)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 9
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for t in ("invoices", "invoice_lines", "invoice_counters",
                  "invoice_payments"):
            assert t in tables
        triggers = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        for trig in ("trg_invoices_immutable",
                     "trg_invoice_lines_immutable_upd",
                     "trg_invoice_lines_immutable_del",
                     "trg_invoice_lines_no_add_sent",
                     "trg_invoice_payments_guard",
                     "trg_timesheet_invoice_link_guard"):
            assert trig in triggers
    finally:
        conn.close()


def test_migration_8_schema_columns(tmp_path):
    db = str(tmp_path / "m8b.db")
    migrations.apply_migrations(db)
    assert {"id", "number", "project_id", "client", "status",
            "tax_pct", "discount_pct", "currency", "subtotal_minor",
            "discount_amount_minor", "tax_amount_minor", "total_minor",
            "notes",
            "void_reason", "issued_at", "due_date", "superseded_by_invoice_id",
            "supersedes_invoice_id", "created_at",
            "updated_at"} <= _cols(db, "invoices")
    assert {"id", "invoice_id", "timesheet_entry_id", "entry_date",
            "description", "hours_minor_units", "rate_minor_units",
            "amount_minor_units", "currency", "sort_order"
            } <= _cols(db, "invoice_lines")
    assert {"year", "next_number"} <= _cols(db, "invoice_counters")
    assert {"id", "invoice_id", "paid_date", "amount_minor", "note",
            "created_at_utc"} <= _cols(db, "invoice_payments")
    assert "invoice_id" in _cols(db, "timesheet_entries")
    assert "rollover_enabled" in _cols(db, "projects")


def test_migration_8_seeds_settings(tmp_path):
    db = str(tmp_path / "m8c.db")
    migrations.apply_migrations(db)
    assert store.get_setting("rollover_cap_pct", path=db) == "50"
    assert store.get_setting("invoice_due_days", path=db) == "14"
    assert store.get_setting("default_tax_pct", path=db) == "0"
    assert store.get_setting("default_discount_pct", path=db) == "0"


def test_migration_8_upgrade_from_v7_preserves_data(tmp_path):
    db = str(tmp_path / "m8d.db")
    migrations.apply_migrations(db)
    _downgrade_to_v7(db)
    # Simulate a pre-Phase-10 row.
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO timesheet_entries (day, start_ts, end_ts, minutes,"
            " category, status) VALUES ('2026-01-01','2026-01-01 09:00',"
            "'2026-01-01 10:00',60,'Work','accepted')")
        conn.commit()
    finally:
        conn.close()
    migrations.apply_migrations(db)
    conn = _connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        row = conn.execute(
            "SELECT invoice_id FROM timesheet_entries").fetchone()
        assert row["invoice_id"] is None
        # Draft numbers stay nullable; unique constraint exists.
        conn.execute("INSERT INTO projects (name) VALUES ('P')")
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO invoices (project_id, status, currency,"
                     " created_at, updated_at) "
                     "VALUES (?, 'draft', 'USD', '2026-01-01', '2026-01-01')",
                     (pid,))
        conn.execute("INSERT INTO invoices (project_id, status, currency,"
                     " created_at, updated_at) "
                     "VALUES (?, 'draft', 'USD', '2026-01-01', '2026-01-01')",
                     (pid,))
        conn.commit()
        try:
            conn.execute("INSERT INTO invoices (project_id, status, number,"
                         " currency, created_at, updated_at) VALUES (?,"
                         " 'sent', 'INV-2026-0001', 'USD', '2026-01-01',"
                         " '2026-01-01')", (pid,))
            conn.execute("INSERT INTO invoices (project_id, status, number,"
                         " currency, created_at, updated_at) VALUES (?,"
                         " 'sent', 'INV-2026-0001', 'USD', '2026-01-01',"
                         " '2026-01-01')", (pid,))
            conn.commit()
            raise AssertionError("duplicate invoice number was allowed")
        except sqlite3.IntegrityError:
            conn.rollback()
    finally:
        conn.close()


def test_migration_8_failure_leaves_version_at_7(tmp_path, monkeypatch):
    # Atomicity: if migration 8 fails partway, user_version must stay 7
    # and no invoice tables may exist.
    from focuscore import migrations as mig_mod
    db = str(tmp_path / "fail8.db")
    # Build a real v8 DB, then downgrade to v7 to simulate pre-Phase-10.
    mig_mod.apply_migrations(db)
    _downgrade_to_v7(db)

    def boom(conn):
        # Run half the migration, then fail.
        conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY)")
        raise RuntimeError("simulated migration failure")

    patched = [
        mig_mod.Migration(m.version, m.name,
                          boom if m.version == 9 else m.apply)
        for m in mig_mod.MIGRATIONS
    ]
    monkeypatch.setattr(mig_mod, "MIGRATIONS", patched)
    with pytest.raises(mig_mod.MigrationError):
        mig_mod.apply_migrations(db)

    conn = _connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "invoices" not in tables
        assert "invoice_lines" not in tables
    finally:
        conn.close()
