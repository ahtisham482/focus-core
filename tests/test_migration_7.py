"""Phase 9: migration 7 (finance schema). Qwen audit M1/M2/E/G."""

import sqlite3

from focuscore import migrations


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


def _downgrade_to_v6(db):
    """Surgically remove migration-7 objects to simulate a pre-Phase-9 DB."""
    conn = _connect(db)
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version >= 7")
        conn.execute("PRAGMA user_version = 6")
        conn.execute("DROP TABLE IF EXISTS project_budget_ledger")
        conn.execute("DROP TABLE IF EXISTS finance_audit_events")
        conn.execute("DROP INDEX IF EXISTS idx_budget_ledger_lookup")
        conn.execute("DROP INDEX IF EXISTS idx_budget_ledger_project_effective")
        conn.execute("DROP INDEX IF EXISTS idx_finance_audit_entity")
        conn.execute("DROP INDEX IF EXISTS idx_timesheet_project_day")
        conn.execute("DROP INDEX IF EXISTS idx_timesheet_day_project")
        conn.commit()
    finally:
        conn.close()


def test_migration_7_applies_cleanly_and_is_idempotent(tmp_path):
    db = str(tmp_path / "m7.db")
    migrations.apply_migrations(db)
    migrations.apply_migrations(db)  # second run must not raise
    conn = _connect(db)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 8
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "project_budget_ledger" in tables
        assert "finance_audit_events" in tables
        entry_cols = _cols(db, "timesheet_entries")
        for col in ("hourly_rate_minor", "rate_currency", "rate_status",
                    "rate_confirmed_at_utc"):
            assert col in entry_cols
        proj_cols = _cols(db, "projects")
        for col in ("hourly_rate_minor", "rate_currency",
                    "current_weekly_cap_seconds",
                    "current_monthly_cap_seconds",
                    "current_weekly_cap_amount_minor",
                    "current_monthly_cap_amount_minor",
                    "budget_currency"):
            assert col in proj_cols
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        for idx in ("idx_budget_ledger_lookup",
                    "idx_budget_ledger_project_effective",
                    "idx_finance_audit_entity",
                    "idx_timesheet_project_day",
                    "idx_timesheet_day_project"):
            assert idx in indexes
        # Default settings seeded.
        cur = conn.execute(
            "SELECT value FROM settings WHERE key = 'currency'").fetchone()
        assert cur["value"] == "USD"
        cur = conn.execute(
            "SELECT value FROM settings WHERE key = 'working_days'").fetchone()
        assert cur["value"] == "0,1,2,3,4"
    finally:
        conn.close()


def test_migration_7_does_not_backfill_entry_rates(tmp_path):
    """M1.1/M1.2: pre-existing entries keep rate_status='unknown' even
    when the project already has a rate when v7 applies."""
    db = str(tmp_path / "m7b.db")
    migrations.apply_migrations(db)
    _downgrade_to_v6(db)
    conn = _connect(db)
    try:
        # Simulate pre-Phase-9 rows with raw SQL (store.* would re-apply v7).
        conn.execute("INSERT INTO projects (name, client, created_at) "
                     "VALUES ('Old Client', 'Acme', '2026-09-01T00:00:00')")
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("UPDATE projects SET hourly_rate_minor = 9550, "
                     "rate_currency = 'USD' WHERE id = ?", (pid,))
        conn.execute(
            "INSERT INTO timesheet_entries (day, start_ts, end_ts, minutes, "
            "category, project_id, status) VALUES ('2026-09-20', "
            "'2026-09-20T09:00', '2026-09-20T10:00', 60.0, 'Work', ?, "
            "'accepted')", (pid,))
        conn.execute("UPDATE timesheet_entries SET hourly_rate_minor = NULL,"
                     " rate_currency = NULL, rate_status = 'unknown', "
                     "rate_confirmed_at_utc = NULL")
        conn.commit()
    finally:
        conn.close()
    migrations.apply_migrations(db)  # re-applies v7 only
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT hourly_rate_minor, rate_status FROM timesheet_entries"
        ).fetchone()
        assert row["hourly_rate_minor"] is None
        assert row["rate_status"] == "unknown"
    finally:
        conn.close()


def test_migration_7_seeds_legacy_real_values(tmp_path):
    """Legacy migration-4 REAL columns migrate once into integer columns."""
    db = str(tmp_path / "m7c.db")
    migrations.apply_migrations(db)
    _downgrade_to_v6(db)
    conn = _connect(db)
    try:
        # Raw SQL: store.* would re-apply v7 via init_db.
        conn.execute("INSERT INTO projects (name, client, created_at) "
                     "VALUES ('Legacy', '', '2026-09-01T00:00:00')")
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("UPDATE projects SET hourly_rate = 95.5, "
                     "weekly_budget_hours = 20 WHERE id = ?", (pid,))
        conn.execute("UPDATE projects SET hourly_rate_minor = NULL, "
                     "rate_currency = NULL, "
                     "current_weekly_cap_seconds = NULL WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()
    migrations.apply_migrations(db)
    conn = _connect(db)
    try:
        row = conn.execute(
            "SELECT hourly_rate_minor, rate_currency, "
            "current_weekly_cap_seconds FROM projects WHERE id = ?",
            (pid,)).fetchone()
        assert row["hourly_rate_minor"] == 9550
        assert row["rate_currency"] == "USD"
        assert row["current_weekly_cap_seconds"] == 20 * 3600
        led = conn.execute(
            "SELECT cap_seconds, cap_amount_minor, currency "
            "FROM project_budget_ledger WHERE project_id = ?",
            (pid,)).fetchall()
        assert len(led) == 1
        assert led[0]["cap_seconds"] == 20 * 3600
        assert led[0]["cap_amount_minor"] is None
        assert led[0]["currency"] == "USD"
    finally:
        conn.close()


def test_migration_7_no_real_money_columns(tmp_path):
    """Constraint A: no REAL columns for money in the new schema."""
    db = str(tmp_path / "m7d.db")
    migrations.apply_migrations(db)
    conn = _connect(db)
    try:
        for table in ("project_budget_ledger",):
            for r in conn.execute("PRAGMA table_info(%s)" % table):
                if "amount" in r["name"] or "minor" in r["name"]:
                    assert r["type"].upper() == "INTEGER", r["name"]
        for r in conn.execute("PRAGMA table_info(timesheet_entries)"):
            if r["name"] == "hourly_rate_minor":
                assert r["type"].upper() == "INTEGER"
    finally:
        conn.close()
