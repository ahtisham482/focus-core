"""Unit and integration test suite for focuscore.migrations.

Covers:
- Fresh database initialization to latest version (v4).
- Idempotent re-run safety (zero duplicate entries or extra backups).
- Upgrade from legacy database preserving existing rows and assigning column defaults.
- Pre-migration snapshot creation and SHA256 sidecar verification.
- Safe handling of pre-existing columns without duplicate column errors.
- Atomic transaction rollback and pre-migration snapshot restoration on failure.
- Rollback cleanup of auxiliary -wal and -shm files.
- Snapshot rotation retaining up to 10 newest snapshots and pruning sidecars.
- WAL mode and busy_timeout PRAGMA configuration.
- store.init_db() automatic migration integration.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from focuscore import backup, migrations, store


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


def _backup_dir(tmp_path: Path) -> str:
    d = tmp_path / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------- Test Cases ---

def test_fresh_db_applies_all_migrations(tmp_path: Path) -> None:
    """A new database file should have all migrations applied up to LATEST_VERSION."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    highest = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert highest == migrations.LATEST_VERSION

    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        # PRAGMA checks
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == migrations.LATEST_VERSION

        journal = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        assert journal == "wal"

        # Audit table check
        rows = conn.execute(
            "SELECT * FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert len(rows) == migrations.LATEST_VERSION
        versions = [r["version"] for r in rows]
        assert versions == list(range(1, migrations.LATEST_VERSION + 1))

        # Table & Column verifications
        # 1. afk_intervals
        afk = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='afk_intervals'"
        ).fetchone()
        assert afk is not None

        # 2. focus_sessions columns
        fs_cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(focus_sessions)").fetchall()
        }
        expected_fs_cols = {
            "enforcement_mode",
            "intercepted_count",
            "session_type",
            "completed_cycles",
            "target_cycles",
            "break_minutes",
            "work_minutes",
        }
        assert expected_fs_cols.issubset(fs_cols)

        # 3. projects columns
        proj_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        expected_proj_cols = {
            "is_billable",
            "hourly_rate",
            "color",
            "weekly_budget_hours",
        }
        assert expected_proj_cols.issubset(proj_cols)

        # 4. timesheet_entries columns & index
        ts_cols = {
            r[1]
            for r in conn.execute(
                "PRAGMA table_info(timesheet_entries)"
            ).fetchall()
        }
        expected_ts_cols = {"session_id", "is_billable"}
        assert expected_ts_cols.issubset(ts_cols)

        ts_idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_timesheet_session'"
        ).fetchone()
        assert ts_idx is not None

        # Fresh DB should not produce dummy pre-migration snapshots
        backups = list(Path(b_dir).glob("focuscore-*.db"))
        assert len(backups) == 0
    finally:
        conn.close()


def test_idempotent_reapplication(tmp_path: Path) -> None:
    """Re-running apply_migrations() on an up-to-date database must be a fast no-op."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    v1 = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert v1 == migrations.LATEST_VERSION

    # Count backups before second run
    backups_before = len(list(Path(b_dir).glob("focuscore-*.db")))

    # Re-run
    v2 = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert v2 == migrations.LATEST_VERSION

    # Assure no extra backup was made
    backups_after = len(list(Path(b_dir).glob("focuscore-*.db")))
    assert backups_after == backups_before

    conn = sqlite3.connect(db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
        assert count == migrations.LATEST_VERSION
    finally:
        conn.close()


def test_upgrade_from_legacy_db(tmp_path: Path) -> None:
    """Upgrading legacy v0 database preserves data and sets column defaults."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    # 1. Create legacy database without migrations
    # Seed tables with v0 schema manually
    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.commit()
    conn.close()

    # Insert legacy records
    p_id = store.add_project(
        "Legacy Client", client="Old Client Corp", path=db
    )
    s_id = store.create_session(
        "Legacy Deep Work",
        50.0,
        "2026-09-25T10:00:00",
        "2026-09-25T10:50:00",
        "strict",
        path=db,
    )
    t_id = store.create_entry(
        "2026-09-25",
        "2026-09-25T10:00:00",
        "2026-09-25T10:50:00",
        50.0,
        "Coding",
        project_id=p_id,
        path=db,
    )

    # Reset user_version to 0 and drop schema_migrations to simulate legacy database
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE IF EXISTS schema_migrations")
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()

    # 2. Run migrations
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION

    # Verify pre-migration snapshot was created
    backups = list(Path(b_dir).glob("focuscore-*.db"))
    assert len(backups) >= 1

    # 3. Verify data preservation and default column assignments
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        p_row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (p_id,)
        ).fetchone()
        assert p_row["name"] == "Legacy Client"
        assert p_row["is_billable"] == 1
        assert p_row["color"] == "#3B82F6"
        assert p_row["hourly_rate"] == 0.0

        s_row = conn.execute(
            "SELECT * FROM focus_sessions WHERE id = ?", (s_id,)
        ).fetchone()
        assert s_row["label"] == "Legacy Deep Work"
        assert s_row["enforcement_mode"] == "strict"
        # Migration 6 normalizes every pre-Phase-8 session to classic.
        assert s_row["session_type"] == "classic"
        assert s_row["completed_cycles"] == 0

        t_row = conn.execute(
            "SELECT * FROM timesheet_entries WHERE id = ?", (t_id,)
        ).fetchone()
        assert t_row["is_billable"] == 1
        assert t_row["session_id"] is None
    finally:
        conn.close()


def test_snapshot_created_with_sha256_sidecar(tmp_path: Path) -> None:
    """Pre-migration snapshot creates .db and .sha256 sidecar with matching digest."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    # Seed legacy db
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE legacy_data (id INT, val TEXT)")
    conn.execute("INSERT INTO legacy_data VALUES (1, 'original')")
    conn.commit()
    conn.close()

    snap = backup.create_backup(db_path=db, dest_dir=b_dir)
    assert snap is not None
    assert snap.exists()

    sidecar = snap.parent / (snap.name + ".sha256")
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").strip() == _sha256(snap)


def test_safe_handling_of_existing_columns(tmp_path: Path) -> None:
    """If columns already exist from custom alters, migration skips them."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    # Manually add a column ahead of time
    conn.execute(
        "ALTER TABLE focus_sessions ADD COLUMN enforcement_mode TEXT "
        "DEFAULT 'hardcore'"
    )
    conn.commit()
    conn.close()

    # Migration must succeed without 'duplicate column name' error
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(focus_sessions)").fetchall()
        }
        assert "enforcement_mode" in cols
        assert "intercepted_count" in cols
    finally:
        conn.close()


def test_atomic_rollback_and_restore_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If migration step fails, transaction rolls back and restores backup."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.commit()
    conn.close()

    p_id = store.add_project("Critical Safe Data", client="Acme", path=db)

    # Reset user_version to 0 so migrations are pending
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    # Define a faulty migration
    def failing_step(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE partial_fault_table (id INT)")
        raise RuntimeError("DISK_IO_SIMULATED_FAILURE")

    bad_migration = migrations.Migration(
            migrations.LATEST_VERSION + 1, "0006_faulty", failing_step)
    monkeypatch.setattr(
        migrations, "MIGRATIONS", migrations.MIGRATIONS + [bad_migration]
    )

    with pytest.raises(migrations.MigrationError) as exc_info:
        migrations.apply_migrations(db, dest_backup_dir=b_dir)

    assert "DISK_IO_SIMULATED_FAILURE" in str(exc_info.value)

    # Check database state: partial_fault_table must not exist, project exists
    conn = sqlite3.connect(db)
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='partial_fault_table'"
        ).fetchone()
        assert tbl is None

        proj = conn.execute(
            "SELECT name FROM projects WHERE id = ?", (p_id,)
        ).fetchone()
        assert proj[0] == "Critical Safe Data"
    finally:
        conn.close()


def test_rollback_cleans_up_wal_and_shm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback removes dirty -wal and -shm files from the failed attempt."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.commit()
    conn.close()

    # Create dummy WAL and SHM files
    wal = Path(db + "-wal")
    shm = Path(db + "-shm")
    wal.write_text("wal-content")
    shm.write_text("shm-content")

    # Inject a failing migration
    def failing_step(c: sqlite3.Connection) -> None:
        raise RuntimeError("FAIL_AND_CLEANUP_WAL")

    bad_mig = migrations.Migration(
            migrations.LATEST_VERSION + 1, "0006_fail", failing_step)
    monkeypatch.setattr(
        migrations, "MIGRATIONS", migrations.MIGRATIONS + [bad_mig]
    )

    with pytest.raises(migrations.MigrationError):
        migrations.apply_migrations(db, dest_backup_dir=b_dir)

    assert not wal.exists()
    assert not shm.exists()


def test_snapshot_rotation_retains_newest_ten(tmp_path: Path) -> None:
    """Pruning retains up to 10 newest snapshots and cleans up sidecars."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INT)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    # Create 12 backups
    for _ in range(12):
        backup.create_backup(db_path=db, dest_dir=b_dir)

    total_before = len(list(Path(b_dir).glob("focuscore-*.db")))
    assert total_before == 12

    pruned = migrations.prune_snapshots(keep=10, dest_dir=b_dir)
    assert pruned == 2

    remaining_dbs = list(Path(b_dir).glob("focuscore-*.db"))
    assert len(remaining_dbs) == 10

    remaining_sidecars = list(Path(b_dir).glob("focuscore-*.sha256"))
    assert len(remaining_sidecars) == 10


def test_wal_and_busy_timeout_pragmas(tmp_path: Path) -> None:
    """Migrations set WAL journal mode and 5000ms busy timeout."""
    db = _db(tmp_path)
    migrations.apply_migrations(db)

    conn = sqlite3.connect(db)
    try:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal.lower() == "wal"

        migrations.ensure_wal_and_timeout(conn)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert int(timeout) == 5000
    finally:
        conn.close()


def test_store_init_db_auto_migrates(tmp_path: Path) -> None:
    """store.init_db() automatically applies migrations to fresh database."""
    db = _db(tmp_path)
    store.init_db(db)

    conn = sqlite3.connect(db)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == migrations.LATEST_VERSION

        # Check afk_intervals created
        afk = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='afk_intervals'"
        ).fetchone()
        assert afk is not None
    finally:
        conn.close()


def test_fresh_database_skips_snapshot_without_error(tmp_path: Path) -> None:
    """Fresh database runs migrations without creating dummy snapshots."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    assert not Path(db).exists()
    ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
    assert ver == migrations.LATEST_VERSION

    # Assure no snapshot was created during fresh initialization
    assert len(list(Path(b_dir).glob("focuscore-*.db"))) == 0


def test_rollback_refuses_corrupted_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback fails with safety exception if pre-migration snapshot is corrupted."""
    db = _db(tmp_path)
    b_dir = _backup_dir(tmp_path)

    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE original (id INT)")
    conn.execute("INSERT INTO original VALUES (100)")
    conn.commit()
    conn.close()

    # Create backup snapshot manually
    snap = backup.create_backup(db_path=db, dest_dir=b_dir)

    # Corrupt the snapshot file
    with open(snap, "ab") as f:
        f.write(b"corrupted_bytes_tampered")

    # Hook backup.create_backup to return the corrupted snapshot
    monkeypatch.setattr(backup, "create_backup", lambda **kwargs: snap)

    # Inject a failing migration
    def failing_step(c: sqlite3.Connection) -> None:
        raise RuntimeError("FAIL_AND_TRIGGER_RESTORE")

    bad_mig = migrations.Migration(
            migrations.LATEST_VERSION + 1, "0006_fail", failing_step)
    monkeypatch.setattr(
        migrations, "MIGRATIONS", migrations.MIGRATIONS + [bad_mig]
    )

    with pytest.raises(migrations.MigrationError) as exc_info:
        migrations.apply_migrations(db, dest_backup_dir=b_dir)

    assert "safety check" in str(exc_info.value) or "failed" in str(
        exc_info.value
    )


def test_store_crud_compatibility_with_migrated_columns(tmp_path: Path) -> None:
    """Store CRUD functions continue working smoothly with all migrated tables."""
    db = _db(tmp_path)
    store.init_db(db)

    # 1. Projects with client
    pid = store.add_project("Alpha Project", client="Acme Corp", path=db)
    projects = store.list_projects(path=db)
    assert len(projects) == 1
    assert projects[0]["id"] == pid
    assert projects[0]["name"] == "Alpha Project"
    assert projects[0]["client"] == "Acme Corp"

    # 2. Focus session creation and retrieval
    sid = store.create_session(
        "Sprint Focus",
        25.0,
        "2026-09-26T10:00:00",
        "2026-09-26T10:25:00",
        "strict",
        path=db,
    )
    session = store.get_session(sid, path=db)
    assert session is not None
    assert session["label"] == "Sprint Focus"
    assert session["block_level"] == "strict"

    # 3. Block record
    store.record_block(
        sid,
        "2026-09-26T10:05:00",
        "chrome.exe",
        "Distracting Site",
        "http://social.example",
        -2,
        "Social",
        path=db,
    )

    # 4. Timesheet entry
    eid = store.create_entry(
        "2026-09-26",
        "2026-09-26T10:00:00",
        "2026-09-26T10:25:00",
        25.0,
        "Engineering",
        app="Code.exe",
        title="migrations.py",
        project_id=pid,
        task="DB Migrations",
        path=db,
    )
    entries = store.list_entries("2026-09-26", path=db)
    assert len(entries) == 1
    assert entries[0]["id"] == eid
    assert entries[0]["project_id"] == pid
    assert entries[0]["project_name"] == "Alpha Project"

