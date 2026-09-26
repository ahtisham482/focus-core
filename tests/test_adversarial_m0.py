"""Adversarial stress test suite for Milestone M0.

Covers focuscore/migrations.py and focuscore/store.py.

Testing dimensions:
1. Multiple rapid concurrent connections attempting migrations simultaneously.
2. Simulated disk failures or mid-migration exceptions to verify
   pre-migration snapshot restoration and -wal/-shm cleanup.
3. Pruning behavior with varying backup quantities (0, 1, 10, 25 backups).
4. Corrupted or partial databases before migration.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import List

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from focuscore import backup, migrations, store


def _db(tmp_path: Path, name: str = "test.db") -> str:
    return str(tmp_path / name)


def _backup_dir(tmp_path: Path, name: str = "backups") -> str:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# ============================================================================
# 1. Concurrent Migrations Stress Tests
# ============================================================================

def test_concurrent_migrations_fresh_db(tmp_path: Path) -> None:
    """Multiple threads attempting migrations simultaneously on a fresh database."""
    db = _db(tmp_path, "concurrent_fresh.db")
    b_dir = _backup_dir(tmp_path, "backups_fresh")

    num_threads = 10
    results: List[int] = []
    errors: List[Exception] = []

    def worker(idx: int) -> int:
        try:
            ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
            return ver
        except Exception as exc:
            errors.append(exc)
            raise exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass

    # Empirical check:
    assert len(errors) == 0, f"Concurrent workers encountered errors: {errors}"
    assert len(results) == num_threads
    assert all(v == migrations.LATEST_VERSION for v in results)

    # Database integrity check
    conn = sqlite3.connect(db)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == migrations.LATEST_VERSION
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
    finally:
        conn.close()


def test_concurrent_migrations_legacy_db(tmp_path: Path) -> None:
    """Concurrent threads attempting migrations on existing legacy DB."""
    db = _db(tmp_path, "concurrent_legacy.db")
    b_dir = _backup_dir(tmp_path, "backups_legacy")

    # Initialize legacy database with data
    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.execute(
        "INSERT INTO projects (name, client) VALUES ('Proj Alpha', 'Client Alpha')"
    )
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    num_threads = 10
    results: List[int] = []
    errors: List[Exception] = []

    def worker(idx: int) -> int:
        try:
            ver = migrations.apply_migrations(db, dest_backup_dir=b_dir)
            return ver
        except Exception as exc:
            errors.append(exc)
            raise exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass

    assert len(errors) == 0, f"Concurrent legacy migration errors: {errors}"
    assert len(results) == num_threads
    assert all(v == migrations.LATEST_VERSION for v in results)

    # Verify data preserved
    conn = sqlite3.connect(db)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == migrations.LATEST_VERSION
        p_row = conn.execute(
            "SELECT name, client, is_billable FROM projects"
        ).fetchone()
        assert p_row[0] == "Proj Alpha"
        assert p_row[1] == "Client Alpha"
        assert p_row[2] == 1  # Migrated default
    finally:
        conn.close()


def test_concurrent_readers_and_migrator(tmp_path: Path) -> None:
    """Readers and writers interact with store while migrations are executing."""
    db = _db(tmp_path, "concurrent_rw.db")
    b_dir = _backup_dir(tmp_path, "backups_rw")

    # Start with legacy DB
    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.execute("INSERT INTO projects (name, client) VALUES ('Base', 'Base')")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    stop_flag = False
    errors: List[Exception] = []

    def reader_worker():
        while not stop_flag:
            try:
                c = sqlite3.connect(db)
                c.execute("PRAGMA busy_timeout = 5000")
                c.execute("SELECT COUNT(*) FROM projects").fetchone()
                c.close()
            except sqlite3.OperationalError:
                # Busy timeout or lock contention is acceptable under extreme
                # load, but no corruptions
                pass
            except Exception as e:
                errors.append(e)

    def writer_worker():
        while not stop_flag:
            try:
                store.init_db(db)
            except Exception as e:
                errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        readers = [executor.submit(reader_worker) for _ in range(3)]
        writers = [executor.submit(writer_worker) for _ in range(2)]
        migrator = executor.submit(
            migrations.apply_migrations, db, dest_backup_dir=b_dir
        )

        try:
            mig_res = migrator.result(timeout=10)
        finally:
            stop_flag = True

        for f in readers + writers:
            f.result(timeout=5)

    assert mig_res == migrations.LATEST_VERSION
    assert len(errors) == 0, f"Errors in concurrent RW workload: {errors}"


# ============================================================================
# 2. Mid-Migration Failures, Snapshot Restoration & WAL Cleanup
# ============================================================================

def test_mid_migration_exception_restores_snapshot_and_cleans_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mid-migration failure on step 3 must restore pre-migration snapshot
    and remove WAL/SHM.
    """
    db = _db(tmp_path, "mid_migration.db")
    b_dir = _backup_dir(tmp_path, "backups_mid")

    # Initialize legacy db with specific state
    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.execute(
        "INSERT INTO projects (name, client) "
        "VALUES ('Original Project', 'Client X')"
    )
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    # Create monkeypatched migration sequence where migration 3 throws mid-execution
    def faulty_migration_0003(c: sqlite3.Connection):
        # Do a partial schema change
        c.execute(
            "ALTER TABLE focus_sessions ADD COLUMN session_type "
            "TEXT DEFAULT 'broken'"
        )
        raise sqlite3.OperationalError("DISK_FULL_SIMULATION_ERROR")

    custom_migrations = [
        migrations.MIGRATIONS[0],
        migrations.MIGRATIONS[1],
        migrations.Migration(3, "0003_flowtime_pomodoro", faulty_migration_0003),
        migrations.MIGRATIONS[3],
    ]
    monkeypatch.setattr(migrations, "MIGRATIONS", custom_migrations)

    with pytest.raises(migrations.MigrationError) as exc_info:
        migrations.apply_migrations(db, dest_backup_dir=b_dir)

    assert "DISK_FULL_SIMULATION_ERROR" in str(exc_info.value)

    # Verify WAL and SHM files are absent
    wal_file = Path(db + "-wal")
    shm_file = Path(db + "-shm")
    assert not wal_file.exists(), "WAL file was not unlinked on migration failure"
    assert not shm_file.exists(), "SHM file was not unlinked on migration failure"

    # Verify database state was restored to pre-migration condition
    conn = sqlite3.connect(db)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 0, f"Expected user_version 0 after rollback, got {ver}"

        # afk_intervals (migration 1) should NOT exist because whole DB was restored
        afk = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='afk_intervals'"
        ).fetchone()
        assert afk is None, (
            "afk_intervals should not exist after restoration to v0 snapshot"
        )

        # Check projects content is intact
        p = conn.execute("SELECT name, client FROM projects").fetchone()
        assert p[0] == "Original Project"
        assert p[1] == "Client X"
    finally:
        conn.close()


def test_backup_creation_failure_aborts_without_modifying_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If backup snapshot creation fails (e.g. disk full), migration must
    abort immediately.
    """
    db = _db(tmp_path, "backup_fail.db")
    b_dir = _backup_dir(tmp_path, "backups_fail")

    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    conn.execute(
        "INSERT INTO projects (name, client) VALUES ('Immortal Proj', 'Client')"
    )
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    def mock_create_backup(**kwargs):
        raise OSError("Simulated disk write failure during backup")

    monkeypatch.setattr(backup, "create_backup", mock_create_backup)

    with pytest.raises(migrations.MigrationError) as exc_info:
        migrations.apply_migrations(db, dest_backup_dir=b_dir)

    assert "Pre-migration backup failed" in str(exc_info.value)

    # Verify DB was NOT migrated or modified
    conn = sqlite3.connect(db)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 0
        tbls = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "afk_intervals" not in tbls
    finally:
        conn.close()


# ============================================================================
# 3. Pruning Behavior Stress Tests (0, 1, 10, 25 backups)
# ============================================================================

@pytest.mark.parametrize("quantity,keep", [
    (0, 10),
    (0, 0),
    (1, 10),
    (1, 1),
    (1, 0),
    (10, 10),
    (10, 5),
    (10, 0),
    (25, 10),
    (25, 25),
    (25, 1),
    (25, 0),
])
def test_pruning_stress_matrix(tmp_path: Path, quantity: int, keep: int) -> None:
    """Verify prune_snapshots behavior across varying backup quantities
    and keep targets.
    """
    b_dir = tmp_path / f"backups_q{quantity}_k{keep}"
    b_dir.mkdir(parents=True, exist_ok=True)
    db_file = tmp_path / "dummy.db"

    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (id INT)")
    conn.commit()
    conn.close()

    # Generate `quantity` distinct backups with sidecars
    created_backups = []
    for i in range(quantity):
        # Create timestamped backup
        name = f"focuscore-20260926-{i:06d}.db"
        b_path = b_dir / name
        shutil.copy2(str(db_file), str(b_path))
        # Write sidecar
        sidecar = b_dir / f"{name}.sha256"
        sidecar.write_text(
            hashlib.sha256(b_path.read_bytes()).hexdigest(), encoding="utf-8"
        )
        # Ensure distinct mtime for sorting
        os.utime(str(b_path), (1700000000 + i * 10, 1700000000 + i * 10))
        created_backups.append(b_path)

    expected_pruned = max(0, quantity - keep)
    expected_remaining = min(quantity, keep)

    pruned_count = migrations.prune_snapshots(keep=keep, dest_dir=b_dir)
    assert pruned_count == expected_pruned

    remaining_dbs = list(b_dir.glob("focuscore-*.db"))
    remaining_sidecars = list(b_dir.glob("focuscore-*.sha256"))

    assert len(remaining_dbs) == expected_remaining
    assert len(remaining_sidecars) == expected_remaining

    # Verify all remaining backups have matching sidecars (no orphaned sidecars
    # or missing sidecars)
    for db_snap in remaining_dbs:
        sc = Path(str(db_snap) + ".sha256")
        assert sc.exists(), f"Sidecar missing for remaining backup: {db_snap.name}"
        expected_digest = hashlib.sha256(db_snap.read_bytes()).hexdigest()
        assert sc.read_text(encoding="utf-8").strip() == expected_digest


def test_pruning_with_legacy_and_tmp_files(tmp_path: Path) -> None:
    """Pruning correctly cleans up legacy pre-migration names and ignores tmp files."""
    b_dir = tmp_path / "backups_mixed"
    b_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create standard backups
    for i in range(5):
        p = b_dir / f"focuscore-20260926-00000{i}.db"
        p.write_bytes(b"data")
        (b_dir / f"{p.name}.sha256").write_text("dummy", encoding="utf-8")
        os.utime(str(p), (1000 + i, 1000 + i))

    # 2. Create custom pre-migration snapshots
    for i in range(5):
        p = b_dir / f"focuscore-legacy-pre-migration-{i}.db"
        p.write_bytes(b"legacy-data")
        (b_dir / f"{p.name}.sha256").write_text("dummy", encoding="utf-8")
        os.utime(str(p), (2000 + i, 2000 + i))

    # 3. Create .tmp files (should not be touched or listed)
    tmp_file = b_dir / "focuscore-20260926-999999.db.tmp"
    tmp_file.write_bytes(b"tmp-data")

    # Prune keeping 2
    pruned = migrations.prune_snapshots(keep=2, dest_dir=b_dir)
    assert pruned > 0
    assert tmp_file.exists(), "Tmp file should not be deleted by prune_snapshots"


# ============================================================================
# 4. Corrupted and Partial Databases Before Migration
# ============================================================================

def test_zero_byte_database_treated_as_fresh(tmp_path: Path) -> None:
    """A 0-byte database file is safely treated as a fresh DB and migrated
    to LATEST_VERSION.
    """
    db = _db(tmp_path, "empty.db")
    Path(db).write_bytes(b"")

    ver = migrations.apply_migrations(db)
    assert ver == migrations.LATEST_VERSION

    conn = sqlite3.connect(db)
    try:
        user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_ver == migrations.LATEST_VERSION
        tbls = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "activities" in tbls
        assert "afk_intervals" in tbls
    finally:
        conn.close()


def test_corrupted_garbage_bytes_database(tmp_path: Path) -> None:
    """Non-SQLite garbage bytes in db file must raise MigrationError without
    silent data corruption.
    """
    db = _db(tmp_path, "garbage.db")
    b_dir = _backup_dir(tmp_path, "backups_garbage")
    Path(db).write_bytes(b"NOT_A_SQLITE_FILE_GARBAGE_HEADER_1234567890\x00\xff\xfe")

    with pytest.raises(migrations.MigrationError) as exc_info:
        migrations.apply_migrations(db, dest_backup_dir=b_dir)

    err_msg = str(exc_info.value).lower()
    assert "file is not a database" in err_msg or "migration" in err_msg


def test_malformed_sqlite_page_corruption(tmp_path: Path) -> None:
    """Database with corrupted SQLite pages triggers malformed error cleanly."""
    db = _db(tmp_path, "malformed.db")
    b_dir = _backup_dir(tmp_path, "backups_malformed")

    # Create valid SQLite DB with tables
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE important (id INT, text TEXT)")
    conn.execute("INSERT INTO important VALUES (1, 'val')")
    conn.commit()
    conn.close()

    # Corrupt internal bytes after page header
    with open(db, "r+b") as f:
        f.seek(100)
        f.write(b"\xff" * 200)

    # Migration runner should catch malformed error and raise MigrationError
    with pytest.raises(migrations.MigrationError) as exc_info:
        migrations.apply_migrations(db, dest_backup_dir=b_dir)

    err_msg = str(exc_info.value).lower()
    assert "malformed" in err_msg or "database" in err_msg


def test_future_user_version_noop(tmp_path: Path) -> None:
    """If user_version is already >= LATEST_VERSION, migration runner safely no-ops."""
    db = _db(tmp_path, "future_version.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()

    ver = migrations.apply_migrations(db)
    assert ver == 99


def test_inconsistent_partial_schema_with_missing_columns(tmp_path: Path) -> None:
    """DB has tables created manually but missing some columns; migrations
    complete them idempotently.
    """
    db = _db(tmp_path, "partial.db")
    conn = sqlite3.connect(db)
    # Create partial focus_sessions missing all M2/M3 columns
    conn.execute("""
        CREATE TABLE focus_sessions (
            id INTEGER PRIMARY KEY,
            label TEXT,
            planned_minutes REAL,
            started_at TEXT,
            planned_end_at TEXT,
            ended_at TEXT,
            status TEXT,
            block_level TEXT
        )
    """)
    conn.commit()
    conn.close()

    ver = migrations.apply_migrations(db)
    assert ver == migrations.LATEST_VERSION

    conn = sqlite3.connect(db)
    try:
        cols = {
            r[1]
            for r in conn.execute(
                "PRAGMA table_info(focus_sessions)"
            ).fetchall()
        }
        assert "enforcement_mode" in cols
        assert "intercepted_count" in cols
        assert "session_type" in cols
        assert "completed_cycles" in cols
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-v", __file__]))
