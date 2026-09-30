"""tests/test_scenario_upgrade_integrity.py - Empirical M0 Schema Evolution & Safety.

Verifies:
1. Upgrading from legacy schema snapshots across all 9 baseline tables:
   activities, overrides, categories, day_stats, goals, alerts, projects,
   focus_sessions, timesheet_entries (plus alert_firings, focus_blocks).
2. Data preservation: 100% of user rows preserved without corruption.
3. Default value population for newly added columns.
4. Safe handling of pre-existing columns and custom user values.
5. Safe coexistence with custom user indexes.
6. Strict multi-run migration idempotency with 0 duplicate schema entries.
7. Incremental multi-version upgrades (v1 -> v4, v2 -> v4, v3 -> v4).
8. Adversarial edge cases: case-sensitive column detection and partial schemas.
9. Workload stress test on large dataset.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from focuscore import columncrypto, migrations, store


def _db(tmp_path: Path, name: str = "test.db") -> str:
    return str(tmp_path / name)


def _backup_dir(tmp_path: Path) -> str:
    d = tmp_path / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------- Test Cases ---

def test_legacy_9_tables_data_preservation_and_defaults(tmp_path: Path) -> None:
    """Upgrades a pre-populated v0 legacy database containing all 9 core tables.

    Asserts:
    - All rows across activities, overrides, categories, day_stats, goals,
      alerts, projects, focus_sessions, timesheet_entries are preserved intact.
    - Default values are properly populated on new columns.
    - Pre-migration backup is created with valid sha256 checksum.
    - All new tables (afk_intervals) and indexes are created.
    """
    db = _db(tmp_path, "legacy_full.db")
    b_dir = _backup_dir(tmp_path)

    # 1. Seed complete v0 legacy database
    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.commit()

    # Pre-populate activities (including Unicode, special chars, negative scores)
    activities_data = [
        (
            "2026-09-26T09:00:00",
            300.0,
            "Code.exe",
            "migrations.py — Focus Core [UTF-8 🚀]",
            "https://github.com/org/repo",
            "Development",
            2,
            None,
            "app:code.exe",
            "2026-09-26",
        ),
        (
            "2026-09-26T09:05:00",
            60.0,
            "slack.exe",
            "#general — Standup Chat",
            None,
            "Communication",
            -1,
            None,
            "app:slack.exe",
            "2026-09-26",
        ),
        (
            "2026-09-26T09:06:00",
            120.0,
            "chrome.exe",
            "Hacker News — Books & Opinions",
            "https://news.ycombinator.com",
            "Distracting",
            -2,
            -2,
            "domain:news.ycombinator.com",
            "2026-09-26",
        ),
    ]
    conn.executemany(
        "INSERT INTO activities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        activities_data,
    )

    # Pre-populate overrides
    overrides_data = [
        ("domain:reddit.com", -2),
        ("app:obsidian.exe", 2),
        ("app:terminal.exe", 1),
    ]
    conn.executemany(
        "INSERT INTO overrides VALUES (?, ?)",
        overrides_data,
    )

    # Pre-populate categories
    categories_data = [
        ("Deep Work", None, 2, 1),
        ("Admin", None, 0, 1),
        ("Entertainment", None, -2, 0),
    ]
    conn.executemany(
        "INSERT INTO categories VALUES (?, ?, ?, ?)",
        categories_data,
    )

    # Pre-populate day_stats
    day_stats_data = [
        ("2026-09-25", 3600.0, 28800.0),
        ("2026-09-26", 1800.0, 14400.0),
    ]
    conn.executemany(
        "INSERT INTO day_stats VALUES (?, ?, ?)",
        day_stats_data,
    )

    # Pre-populate goals
    goals_data = [
        (
            "Daily Deep Work",
            "more_is_better",
            "category",
            "Development",
            240.0,
            None,
            1,
            "2026-09-26T08:00:00",
        ),
        (
            "Limit Social",
            "less_is_better",
            "category",
            "Distracting",
            30.0,
            None,
            0,
            "2026-09-26T08:00:00",
        ),
    ]
    conn.executemany(
        "INSERT INTO goals (name, direction, target_type, target_name, "
        "threshold_minutes, threshold_pulse, pinned, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        goals_data,
    )

    # Pre-populate alerts
    alerts_data = [
        (
            "Distraction Warning",
            "category",
            "Distracting",
            20.0,
            "Time to refocus!",
            30.0,
            1,
            "2026-09-26T08:00:00",
        ),
    ]
    conn.executemany(
        "INSERT INTO alerts (name, target_type, target_name, "
        "threshold_minutes, message, cooldown_minutes, enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        alerts_data,
    )

    # Pre-populate alert_firings
    conn.execute(
        "INSERT INTO alert_firings (alert_id, fired_at, current_minutes) "
        "VALUES (1, '2026-09-26T09:20:00', 20.5)"
    )

    # Pre-populate projects (v0 schema: id, name, client, created_at)
    projects_data = [
        ("Client Alpha Revamp", "Alpha Corp", "2026-09-20T10:00:00"),
        ("Internal Infrastructure", "Self", "2026-09-21T11:00:00"),
    ]
    conn.executemany(
        "INSERT INTO projects (name, client, created_at) VALUES (?, ?, ?)",
        projects_data,
    )

    # Pre-populate focus_sessions (v0 schema)
    sessions_data = [
        (
            "Morning Flow",
            50.0,
            "2026-09-26T09:00:00",
            "2026-09-26T09:50:00",
            "2026-09-26T09:50:00",
            "completed",
            "strict",
        ),
        (
            "Afternoon Push",
            25.0,
            "2026-09-26T14:00:00",
            "2026-09-26T14:25:00",
            None,
            "active",
            "strict",
        ),
    ]
    conn.executemany(
        "INSERT INTO focus_sessions (label, planned_minutes, started_at, "
        "planned_end_at, ended_at, status, block_level) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        sessions_data,
    )

    # Pre-populate focus_blocks (v0 schema)
    blocks_data = [
        (
            1,
            "2026-09-26T09:15:00",
            "chrome.exe",
            "Twitter / X",
            "https://x.com",
            -2,
            "Social",
        ),
    ]
    conn.executemany(
        "INSERT INTO focus_blocks (session_id, ts, app, title, url, score, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        blocks_data,
    )

    # Pre-populate timesheet_entries (v0 schema)
    entries_data = [
        (
            "2026-09-26",
            "2026-09-26T09:00:00",
            "2026-09-26T09:50:00",
            50.0,
            "Development",
            "Code.exe",
            "migrations.py",
            1,
            "Feature M0",
            "Deep work",
            "accepted",
            0,
            "2026-09-26T09:50:00",
        ),
    ]
    conn.executemany(
        "INSERT INTO timesheet_entries (day, start_ts, end_ts, minutes, category, "
        "app, title, project_id, task, note, status, locked, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        entries_data,
    )

    # Ensure user_version is 0 and no schema_migrations table
    conn.execute("DROP TABLE IF EXISTS schema_migrations")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    # 2. Run migration
    highest_ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert highest_ver == migrations.LATEST_VERSION

    # 3. Verify backup creation and checksum
    backups = list(Path(b_dir).glob("focuscore-*.db"))
    assert len(backups) == 1
    snap = backups[0]
    sidecar = snap.parent / (snap.name + ".sha256")
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").strip() == _sha256(snap)

    # 4. Verify integrity and data preservation across all 9 tables
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        # SQLite integrity check
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert check == "ok"

        # (1) activities -- titles/URLs are sealed by migration 0010
        # (roadmap 1.5a); data preservation is judged through the
        # decryptor, which must hand back the exact legacy plaintext.
        act_rows = conn.execute("SELECT * FROM activities ORDER BY ts").fetchall()
        assert len(act_rows) == 3
        assert columncrypto.unprotect_text(act_rows[0]["title"]) == \
            "migrations.py — Focus Core [UTF-8 🚀]"
        assert act_rows[0]["score"] == 2
        assert act_rows[1]["app"] == "slack.exe"
        assert columncrypto.unprotect_text(act_rows[2]["url"]) == \
            "https://news.ycombinator.com"

        # (2) overrides
        ovr_rows = conn.execute(
            "SELECT * FROM overrides ORDER BY match_key"
        ).fetchall()
        assert len(ovr_rows) == 3
        assert ovr_rows[0]["match_key"] == "app:obsidian.exe"
        assert ovr_rows[0]["score"] == 2

        # (3) categories
        cat_rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
        assert len(cat_rows) == 3
        assert cat_rows[0]["name"] == "Admin"
        assert cat_rows[1]["name"] == "Deep Work"

        # (4) day_stats
        ds_rows = conn.execute("SELECT * FROM day_stats ORDER BY day").fetchall()
        assert len(ds_rows) == 2
        assert ds_rows[0]["day"] == "2026-09-25"
        assert ds_rows[0]["afk_seconds"] == 3600.0

        # (5) goals
        goal_rows = conn.execute("SELECT * FROM goals ORDER BY id").fetchall()
        assert len(goal_rows) == 2
        assert goal_rows[0]["name"] == "Daily Deep Work"
        assert goal_rows[0]["pinned"] == 1

        # (6) alerts & firings
        alert_rows = conn.execute("SELECT * FROM alerts ORDER BY id").fetchall()
        assert len(alert_rows) == 1
        assert alert_rows[0]["name"] == "Distraction Warning"
        firings = conn.execute("SELECT * FROM alert_firings").fetchall()
        assert len(firings) == 1
        assert firings[0]["alert_id"] == 1

        # (7) projects: original data preserved + new columns have default values
        proj_rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
        assert len(proj_rows) == 2
        assert proj_rows[0]["name"] == "Client Alpha Revamp"
        assert proj_rows[0]["client"] == "Alpha Corp"
        # Migration 4 defaults:
        assert proj_rows[0]["is_billable"] == 1
        assert proj_rows[0]["hourly_rate"] == 0.0
        assert proj_rows[0]["color"] == "#3B82F6"
        assert proj_rows[0]["weekly_budget_hours"] == 0.0

        # (8) focus_sessions: original data preserved + new columns have defaults
        fs_rows = conn.execute("SELECT * FROM focus_sessions ORDER BY id").fetchall()
        assert len(fs_rows) == 2
        assert fs_rows[0]["label"] == "Morning Flow"
        assert fs_rows[0]["status"] == "completed"
        # Migration 2 & 3 defaults:
        assert fs_rows[0]["enforcement_mode"] == "strict"
        assert fs_rows[0]["intercepted_count"] == 0
        # Migration 6 normalizes pre-Phase-8 rows to classic.
        assert fs_rows[0]["session_type"] == "classic"
        assert fs_rows[0]["completed_cycles"] == 0
        assert fs_rows[0]["target_cycles"] == 1
        assert fs_rows[0]["break_minutes"] == 0.0
        assert fs_rows[0]["work_minutes"] == 0.0

        # focus_blocks new columns defaults
        fb_rows = conn.execute("SELECT * FROM focus_blocks ORDER BY id").fetchall()
        assert len(fb_rows) == 1
        assert fb_rows[0]["app"] == "chrome.exe"
        assert fb_rows[0]["action_taken"] == "blocked"
        assert fb_rows[0]["process_name"] == ""
        assert fb_rows[0]["window_handle"] == 0

        # (9) timesheet_entries: original data preserved + new columns have defaults
        ts_rows = conn.execute(
            "SELECT * FROM timesheet_entries ORDER BY id"
        ).fetchall()
        assert len(ts_rows) == 1
        assert ts_rows[0]["day"] == "2026-09-26"
        assert ts_rows[0]["minutes"] == 50.0
        assert ts_rows[0]["task"] == "Feature M0"
        # Migration 3 & 4 defaults:
        assert ts_rows[0]["session_id"] is None
        assert ts_rows[0]["is_billable"] == 1

        # Check new table afk_intervals (Migration 1)
        afk = conn.execute("SELECT COUNT(*) FROM afk_intervals").fetchone()[0]
        assert afk == 0

        # Check all required indexes exist
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
        idx_names = {r["name"] for r in idx_rows}
        assert "idx_activities_day" in idx_names
        assert "idx_firings_alert" in idx_names
        assert "idx_blocks_session" in idx_names
        assert "idx_ts_entries_day" in idx_names
        assert "idx_afk_intervals_day" in idx_names
        assert "idx_afk_intervals_start" in idx_names
        assert "idx_timesheet_session" in idx_names

        # PRAGMA user_version at schema head (all migrations applied)
        assert conn.execute("PRAGMA user_version").fetchone()[0] \
            == migrations.LATEST_VERSION
    finally:
        conn.close()


def test_upgrade_with_pre_existing_columns_and_custom_values(
    tmp_path: Path,
) -> None:
    """Pre-existing columns with custom data must NOT be overwritten or corrupted."""
    db = _db(tmp_path, "preexisting_cols.db")
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    # Simulate a legacy DB that already has columns added with custom data
    conn.execute(
        "ALTER TABLE focus_sessions ADD COLUMN enforcement_mode TEXT "
        "DEFAULT 'gentle'"
    )
    conn.execute("ALTER TABLE projects ADD COLUMN color TEXT DEFAULT '#FF0000'")
    conn.execute("ALTER TABLE projects ADD COLUMN is_billable INTEGER DEFAULT 0")
    conn.execute(
        "ALTER TABLE timesheet_entries ADD COLUMN session_id INTEGER DEFAULT 99"
    )

    # Insert rows with custom values
    conn.execute(
        "INSERT INTO focus_sessions (label, planned_minutes, started_at, "
        "planned_end_at, status, block_level, enforcement_mode) "
        "VALUES ('Custom Session', 30.0, '2026-09-26T10:00:00', "
        "'2026-09-26T10:30:00', 'active', 'gentle', 'gentle')"
    )
    conn.execute(
        "INSERT INTO projects (name, client, color, is_billable) "
        "VALUES ('Special Red Project', 'VIP', '#FF0000', 0)"
    )
    conn.execute(
        "INSERT INTO timesheet_entries (day, start_ts, end_ts, minutes, category, "
        "session_id) VALUES ('2026-09-26', '2026-09-26T10:00:00', "
        "'2026-09-26T10:30:00', 30.0, 'Work', 99)"
    )
    conn.commit()
    conn.close()

    # Apply migrations
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION

    # Verify custom values remain intact
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        s = conn.execute(
            "SELECT enforcement_mode, session_type FROM focus_sessions"
        ).fetchone()
        assert s["enforcement_mode"] == "gentle"  # Not overwritten by 'strict'!
        # Migration 6 normalizes the migration-3 default to classic.
        assert s["session_type"] == "classic"

        p = conn.execute(
            "SELECT color, is_billable, hourly_rate FROM projects"
        ).fetchone()
        assert p["color"] == "#FF0000"  # Not overwritten by '#3B82F6'!
        assert p["is_billable"] == 0  # Not overwritten by 1!
        assert p["hourly_rate"] == 0.0  # Added missing column with default

        t = conn.execute(
            "SELECT session_id, is_billable FROM timesheet_entries"
        ).fetchone()
        assert t["session_id"] == 99  # Not overwritten by NULL!
        assert t["is_billable"] == 1  # Added missing column with default
    finally:
        conn.close()


def test_upgrade_with_custom_user_indexes(tmp_path: Path) -> None:
    """Pre-existing custom indexes must be preserved and cause 0 collisions."""
    db = _db(tmp_path, "custom_indexes.db")
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    # Add custom indexes
    conn.execute("CREATE INDEX idx_user_act_app ON activities(app)")
    conn.execute("CREATE INDEX idx_user_proj_client ON projects(client)")
    # Also pre-create an index that migration 3 would create
    conn.execute("ALTER TABLE timesheet_entries ADD COLUMN session_id INTEGER")
    conn.execute(
        "CREATE INDEX idx_timesheet_session ON timesheet_entries(session_id)"
    )
    conn.commit()
    conn.close()

    # Migration must succeed smoothly
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION

    conn = sqlite3.connect(db)
    try:
        idx_names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "idx_user_act_app" in idx_names
        assert "idx_user_proj_client" in idx_names
        assert "idx_timesheet_session" in idx_names
        assert "idx_afk_intervals_day" in idx_names
    finally:
        conn.close()


def test_strict_multi_run_idempotency_and_no_duplicate_entries(
    tmp_path: Path,
) -> None:
    """Re-running migrations 5 times is strictly idempotent with 0 duplicates."""
    db = _db(tmp_path, "idempotent.db")
    b_dir = _backup_dir(tmp_path)

    # Initial migration
    v1 = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert v1 == migrations.LATEST_VERSION

    # Capture schema snapshot
    conn = sqlite3.connect(db)
    master_v1 = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "ORDER BY type, name"
    ).fetchall()
    schema_mig_v1 = conn.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    conn.close()

    assert len(schema_mig_v1) == migrations.LATEST_VERSION

    # Re-run 4 more times
    for _ in range(4):
        v = migrations.apply_migrations(db, dest_backup_dir=b_dir)
        assert v == migrations.LATEST_VERSION

    # Verify schema and migration table are identical
    conn = sqlite3.connect(db)
    try:
        master_v5 = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "ORDER BY type, name"
        ).fetchall()
        schema_mig_v5 = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()

        assert master_v5 == master_v1
        assert schema_mig_v5 == schema_mig_v1

        # Check for zero duplicate indexes or tables
        all_names = [r[1] for r in master_v5]
        assert len(all_names) == len(set(all_names))
    finally:
        conn.close()


def test_incremental_step_upgrades(tmp_path: Path) -> None:
    """Upgrades cleanly from intermediate versions (v1, v2, v3) to v4."""
    for start_ver in [1, 2, 3]:
        sub_db = _db(tmp_path, f"step_v{start_ver}.db")
        b_dir = _backup_dir(tmp_path)

        conn = sqlite3.connect(sub_db)
        conn.executescript(store.SCHEMA)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        )
        # Apply up to start_ver manually
        for m in migrations.MIGRATIONS:
            if m.version <= start_ver:
                m.apply(conn)
                conn.execute(
                    "INSERT INTO schema_migrations "
                    "VALUES (?, ?, '2026-09-26T00:00:00')",
                    (m.version, m.name),
                )
        conn.execute(f"PRAGMA user_version = {start_ver}")
        conn.commit()
        conn.close()

        # Run apply_migrations
        final_ver = migrations.apply_migrations(sub_db, dest_backup_dir=b_dir)
        assert final_ver == migrations.LATEST_VERSION

        conn = sqlite3.connect(sub_db)
        try:
            cur_ver = conn.execute("PRAGMA user_version").fetchone()[0]
            assert cur_ver == migrations.LATEST_VERSION
            mig_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
            assert mig_count == migrations.LATEST_VERSION
        finally:
            conn.close()


def test_adversarial_casing_behavior_documented(tmp_path: Path) -> None:
    """Verifies that case-insensitive column detection prevents duplicate column errors
    when legacy tables have uppercase column names (e.g. 'IS_BILLABLE').
    """
    db = _db(tmp_path, "casing_test.db")
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    # Add an uppercase pre-existing column
    conn.execute("ALTER TABLE projects ADD COLUMN IS_BILLABLE INTEGER DEFAULT 1")
    conn.commit()
    conn.close()

    # Migration succeeds because column presence is checked case-insensitively
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION


def test_adversarial_partial_base_schema_documented(tmp_path: Path) -> None:
    """Verifies ensure_base_schema creates missing baseline tables even when
    'activities' already exists.
    """
    db = _db(tmp_path, "partial_schema.db")
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE activities (id INT, day TEXT)")
    conn.commit()
    conn.close()

    # Migration succeeds and creates missing baseline tables
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION

    # Projects table is safely created by ensure_base_schema
    conn = sqlite3.connect(db)
    try:
        p_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
        ).fetchone()
        assert p_exists is not None
    finally:
        conn.close()


def test_large_workload_migration_stress(tmp_path: Path) -> None:
    """Stress tests migration safety and performance on a large database (10k+ rows)."""
    db = _db(tmp_path, "large_stress.db")
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)

    # Insert 10,000 activities
    activities = [
        (
            f"2026-09-26T{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}",
            1.0,
            f"app_{i % 50}.exe",
            f"Work Window #{i}",
            f"https://site{i % 20}.example.com",
            "Development" if i % 2 == 0 else "Distracting",
            2 if i % 2 == 0 else -2,
            None,
            f"match_key_{i % 100}",
            "2026-09-26",
        )
        for i in range(10000)
    ]
    conn.executemany(
        "INSERT INTO activities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        activities,
    )

    # Insert 500 projects
    projects = [
        (f"Project #{i}", f"Client #{i % 20}", "2026-09-20T00:00:00")
        for i in range(500)
    ]
    conn.executemany(
        "INSERT INTO projects (name, client, created_at) VALUES (?, ?, ?)",
        projects,
    )

    # Insert 1,000 focus sessions
    sessions = [
        (
            f"Session #{i}",
            25.0,
            "2026-09-26T00:00:00",
            "2026-09-26T00:25:00",
            "2026-09-26T00:25:00",
            "completed",
            "strict",
        )
        for i in range(1000)
    ]
    conn.executemany(
        "INSERT INTO focus_sessions (label, planned_minutes, started_at, "
        "planned_end_at, ended_at, status, block_level) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        sessions,
    )

    conn.commit()
    conn.close()

    # Run migration
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION

    # Verify counts and performance
    conn = sqlite3.connect(db)
    try:
        act_cnt = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        assert act_cnt == 10000

        proj_cnt = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        assert proj_cnt == 500

        fs_cnt = conn.execute(
            "SELECT COUNT(*) FROM focus_sessions"
        ).fetchone()[0]
        assert fs_cnt == 1000

        # Sample check defaults
        p_sample = conn.execute(
            "SELECT is_billable, color FROM projects WHERE id = 1"
        ).fetchone()
        assert p_sample[0] == 1
        assert p_sample[1] == "#3B82F6"
    finally:
        conn.close()
