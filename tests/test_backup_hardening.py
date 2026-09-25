"""FC-001: backup/restore hardening tests.

Covers atomic writes, SHA256 checksums, interrupted-backup/restore
safety, and legacy (no-checksum) backward compatibility. Everything
runs against tmp dirs -- the real backups/ folder is never touched.
"""

import hashlib
import os
import sqlite3
import warnings

import pytest

from focuscore import backup


def _make_db(path, marker):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES (?)", (marker,))
    conn.commit()
    conn.close()


def _marker(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "live.db"
    _make_db(db, "original")
    bdir = tmp_path / "backups"
    return db, bdir


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------ create -------

def test_create_records_sha256_sidecar(env):
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)

    sidecar = path.parent / (path.name + ".sha256")
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").strip() == _sha256(path)


def test_create_leaves_no_temp_files(env):
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)

    assert not list(bdir.glob("*.tmp"))
    # The finished backup is complete and readable.
    assert _marker(path) == "original"


def test_interrupted_create_leaves_no_partial_backup(env, monkeypatch):
    """A crash during the atomic publish leaves nothing behind."""
    db, bdir = env

    def boom(*args, **kwargs):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated crash"):
        backup.create_backup(db_path=db, dest_dir=bdir)

    # No file under a final backup name, and no leftover temp file.
    assert backup.list_backups(dest_dir=bdir) == []
    assert backup.newest_backup(dest_dir=bdir) is None
    assert not list(bdir.glob("*.tmp"))
    assert not list(bdir.glob("*.sha256"))


def test_list_backups_ignores_partial_temp_and_sidecars(env):
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)

    # Simulate a crashed backup: partial temp file + orphan sidecar.
    folder = backup.backup_dir(dest_dir=bdir)
    (folder / "focuscore-20200101-000000.db.tmp").write_bytes(b"partial")
    (folder / "focuscore-20200101-000000.db.sha256").write_text("junk\n")

    backups = backup.list_backups(dest_dir=bdir)
    assert [b["name"] for b in backups] == [path.name]


# ------------------------------------------------------------ restore ------

def test_restore_rejects_tampered_backup(env):
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)

    # Tamper with the backup after its checksum was recorded.
    with open(path, "ab") as f:
        f.write(b"tampered-bytes")

    with pytest.raises(ValueError, match="safety check"):
        backup.restore_backup(path.name, db_path=db, dest_dir=bdir)

    # The live database is untouched and no safety copy was littered.
    assert _marker(db) == "original"
    assert not list(db.parent.glob("*.pre-restore-*"))


def test_restore_rejects_empty_sidecar(env):
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    (path.parent / (path.name + ".sha256")).write_text("")

    with pytest.raises(ValueError, match="safety check"):
        backup.restore_backup(path.name, db_path=db, dest_dir=bdir)
    assert _marker(db) == "original"


def test_restore_works_when_checksum_sidecar_missing_legacy(env):
    """Legacy backups (no sidecar) still restore, with a warning."""
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    (path.parent / (path.name + ".sha256")).unlink()

    # Change the live db after the backup so restore has work to do.
    _make_db(db, "changed")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        safety = backup.restore_backup(path.name, db_path=db, dest_dir=bdir)

    assert any("could not be verified" in str(w.message) for w in caught)
    assert _marker(db) == "original"          # restored from backup
    assert safety is not None and _marker(safety) == "changed"  # safety copy


def test_restore_valid_checksum_warns_nothing(env):
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    _make_db(db, "changed")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        backup.restore_backup(path.name, db_path=db, dest_dir=bdir)

    assert not [w for w in caught if "could not be verified" in str(w.message)]
    assert _marker(db) == "original"


def test_interrupted_restore_leaves_old_db_intact(env, monkeypatch):
    """A crash during the atomic swap cannot half-overwrite the db."""
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    _make_db(db, "changed")
    before = db.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated crash"):
        backup.restore_backup(path.name, db_path=db, dest_dir=bdir)

    # Old database byte-identical, no temp file left behind.
    assert db.read_bytes() == before
    assert _marker(db) == "changed"
    assert not list(db.parent.glob("*.tmp"))
    # The safety copy was still made before the failed swap.
    safeties = list(db.parent.glob("*.pre-restore-*"))
    assert len(safeties) == 1 and _marker(safeties[0]) == "changed"


# -------------------------------------------------------------- prune ------

def test_prune_removes_sidecars_with_backups(env):
    db, bdir = env
    for _ in range(3):
        backup.create_backup(db_path=db, dest_dir=bdir)

    assert backup.prune_backups(keep=1, dest_dir=bdir) == 2
    remaining = backup.list_backups(dest_dir=bdir)
    assert len(remaining) == 1

    folder = backup.backup_dir(dest_dir=bdir)
    remaining_names = {b["name"] for b in remaining}
    orphans = [p for p in folder.glob("*.sha256")
               if p.name[:-len(".sha256")] not in remaining_names]
    assert orphans == []
    # Every sidecar on disk belongs to a listed backup, and vice versa.
    assert {p.name[:-len(".sha256")] for p in folder.glob("*.sha256")} \
        == remaining_names


# ------------------------------------------------ verify-all -------

def test_verify_all_backups_reports_bad_file(env):
    """A tampered backup is reported as failed, never raises."""
    db, bdir = env
    good = backup.create_backup(db_path=db, dest_dir=bdir)
    bad = backup.create_backup(db_path=db, dest_dir=bdir)
    # Tamper after the checksum was recorded.
    with open(bad, "ab") as f:
        f.write(b"tampered-bytes")

    results = backup.verify_all_backups(dest_dir=bdir)
    by_name = {r["name"]: r for r in results}
    assert set(by_name) == {good.name, bad.name}

    assert by_name[good.name]["ok"] is True
    assert by_name[bad.name]["ok"] is False
    assert "mismatch" in by_name[bad.name]["reason"].lower()
    # Every result carries the required shape.
    for r in results:
        assert set(r) == {"name", "ok", "reason"}
        assert isinstance(r["ok"], bool)
        assert isinstance(r["reason"], str)


def test_verify_all_backups_flags_legacy_backup(env):
    """A backup whose sidecar was deleted reports legacy/unverifiable."""
    db, bdir = env
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    (path.parent / (path.name + ".sha256")).unlink()

    results = backup.verify_all_backups(dest_dir=bdir)
    assert len(results) == 1
    result = results[0]
    assert result["name"] == path.name
    assert result["ok"] is False
    reason = result["reason"].lower()
    assert "legacy" in reason or "not verifiable" in reason or \
        "unverifiable" in reason


def test_verify_all_backups_all_good(env):
    """Every backup verifies cleanly when nothing is wrong."""
    db, bdir = env
    paths = [backup.create_backup(db_path=db, dest_dir=bdir)
             for _ in range(2)]

    results = backup.verify_all_backups(dest_dir=bdir)
    assert {r["name"] for r in results} == {p.name for p in paths}
    assert all(r["ok"] is True for r in results)


def test_verify_all_backups_empty_folder(env):
    db, bdir = env
    assert backup.verify_all_backups(dest_dir=bdir) == []
