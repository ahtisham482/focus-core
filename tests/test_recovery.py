"""Roadmap 1.13: corrupt-database safe mode.

``focuscore.recovery.ensure_working_db`` wraps the two app-start
entries. On a garbage database it must offer recovery (restore the
latest verified, non-encrypted backup / start empty / quit) instead
of crashing -- and it must never change a byte without a choice:

* the corrupt file is moved aside (with its -wal/-shm sidecars
  travelling under the same suffix), never deleted;
* a failed restore moves the corrupt set back byte-identical;
* quit, an unexpected chooser answer, or an unshowable dialog all
  leave the live file untouched and return False.

Everything here runs on tmp DBs/dirs with fake choosers; the real
tkinter dialog is never created. The scanned backup folder is the
Drive-aware ``backup.backup_dir(None)``, monkeypatched to a tmp dir.
"""

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from focuscore import backup, migrations, recovery, store

GARBAGE = bytes(range(256)) * 4 + b"this is not a sqlite database"
PASSPHRASE = "correct horse battery staple"


class RecordingChooser:
    """Fake chooser: records what it was offered, returns a script."""

    def __init__(self, answer):
        self.answer = answer
        self.seen_options = None
        self.seen_context = None

    def __call__(self, options, context):
        self.seen_options = list(options)
        self.seen_context = dict(context)
        return self.answer


def _scan_folder(monkeypatch, tmp_path):
    """Point the Drive-aware backup scan at a tmp folder."""
    folder = tmp_path / "backups"
    folder.mkdir()
    monkeypatch.setattr(
        backup, "backup_dir", lambda dest_dir=None: folder)
    return folder


def _good_db(tmp_path, name="good.db"):
    db = tmp_path / name
    store.init_db(str(db))
    return db


def _verified_backup(tmp_path, folder, marker="from-backup"):
    """A real plaintext backup (checksum sidecar included)."""
    good = _good_db(tmp_path)
    store.set_setting("recovery_marker", marker, path=str(good))
    return backup.create_backup(db_path=str(good), dest_dir=str(folder))


def _live_garbage(tmp_path):
    live = tmp_path / "focuscore.db"
    live.write_bytes(GARBAGE)
    return live


def _plant_real_sidecars(live, tmp_path):
    """Plant REAL -wal/-shm files next to the garbage DB.

    Bogus sidecar bytes are deleted by SQLite itself while the failed
    ``init_db`` open is still in flight, so they could never test the
    recovery-time rename. Real sidecars (copied out of a live WAL-mode
    database while its connection is open) survive the failed open --
    verified: init_db then raises MigrationError and leaves them in
    place -- so recovery must move them aside with the corrupt file.
    Returns the planted (wal_bytes, shm_bytes).
    """
    import shutil

    src = tmp_path / "_sidecar_src.db"
    store.init_db(str(src))
    conn = sqlite3.connect(str(src))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sidecar_probe(a)")
    conn.execute("INSERT INTO sidecar_probe VALUES (1)")
    shutil.copy(str(src) + "-wal", str(live) + "-wal")
    shutil.copy(str(src) + "-shm", str(live) + "-shm")
    conn.close()
    return (Path(str(live) + "-wal").read_bytes(),
            Path(str(live) + "-shm").read_bytes())


def _asides(tmp_path):
    """The moved-aside main corrupt files (not their sidecars)."""
    return sorted(
        p for p in tmp_path.glob("focuscore.db.corrupt-*")
        if not p.name.endswith(("-wal", "-shm")))


def _user_version(db):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------ happy ---

def test_healthy_db_returns_true_chooser_never_called(tmp_path):
    db = _good_db(tmp_path, "live.db")

    def forbidden(options, context):
        raise AssertionError("chooser must not run for a healthy DB")

    assert recovery.ensure_working_db(str(db), choose=forbidden) is True


def test_fresh_path_initializes_true_chooser_never_called(tmp_path):
    db = tmp_path / "new.db"

    def forbidden(options, context):
        raise AssertionError("chooser must not run for a fresh DB")

    assert recovery.ensure_working_db(str(db), choose=forbidden) is True
    assert db.exists()


# ------------------------------------------------------------- quit ---

def test_quit_leaves_live_file_byte_identical(tmp_path, monkeypatch,
                                              caplog):
    _scan_folder(monkeypatch, tmp_path)
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("quit")

    with caplog.at_level(logging.ERROR, logger="focuscore.recovery"):
        assert recovery.ensure_working_db(str(live), choose=chooser) is False

    assert live.read_bytes() == GARBAGE
    assert _asides(tmp_path) == []
    assert chooser.seen_options == ["empty", "quit"]
    # The original failure is on the record at ERROR, with traceback.
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and errors[0].exc_info is not None


def test_unexpected_chooser_answer_is_quit(tmp_path, monkeypatch):
    _scan_folder(monkeypatch, tmp_path)
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("banana")

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert live.read_bytes() == GARBAGE
    assert _asides(tmp_path) == []


def test_restore_answer_without_offered_backup_is_quit(tmp_path,
                                                       monkeypatch):
    # No backups at all: "restore" is not offered, and a chooser that
    # answers it anyway must not trigger any change.
    _scan_folder(monkeypatch, tmp_path)
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("restore")

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert live.read_bytes() == GARBAGE
    assert _asides(tmp_path) == []
    assert chooser.seen_options == ["empty", "quit"]
    assert chooser.seen_context["backup_name"] is None


def test_chooser_that_raises_fails_closed(tmp_path, monkeypatch):
    _scan_folder(monkeypatch, tmp_path)
    live = _live_garbage(tmp_path)

    def boom(options, context):
        raise RuntimeError("dialog exploded")

    assert recovery.ensure_working_db(str(live), choose=boom) is False
    assert live.read_bytes() == GARBAGE
    assert _asides(tmp_path) == []


# ------------------------------------------------------------ empty ---

def test_empty_starts_fresh_and_preserves_corrupt(tmp_path, monkeypatch):
    _scan_folder(monkeypatch, tmp_path)
    live = _live_garbage(tmp_path)
    wal_bytes, shm_bytes = _plant_real_sidecars(live, tmp_path)
    chooser = RecordingChooser("empty")

    assert recovery.ensure_working_db(str(live), choose=chooser) is True

    asides = _asides(tmp_path)
    assert len(asides) == 1
    assert asides[0].read_bytes() == GARBAGE
    # The planted sidecars travelled with the same suffix...
    aside_wal = tmp_path / (asides[0].name + "-wal")
    aside_shm = tmp_path / (asides[0].name + "-shm")
    # -wal frames are the safety-critical payload (they would be
    # replayed into a fresh DB at the live path); the -shm is a
    # rebuildable index SQLite touches during the failed open, so it
    # must travel with the set but its bytes may legitimately differ.
    assert aside_wal.read_bytes() == wal_bytes
    assert aside_shm.exists() and aside_shm.stat().st_size > 0
    # ...and nothing stale remains at the live path.
    assert not (tmp_path / "focuscore.db-wal").exists()
    assert not (tmp_path / "focuscore.db-shm").exists()
    # The fresh DB is fully migrated and usable.
    assert _user_version(live) == migrations.LATEST_VERSION
    store.set_setting("after_empty", "ok", path=str(live))
    assert store.get_setting("after_empty", path=str(live)) == "ok"


# ---------------------------------------------------------- restore ---

def test_restore_brings_backup_data_live(tmp_path, monkeypatch):
    folder = _scan_folder(monkeypatch, tmp_path)
    made = _verified_backup(tmp_path, folder)
    live = _live_garbage(tmp_path)
    wal_bytes, shm_bytes = _plant_real_sidecars(live, tmp_path)
    chooser = RecordingChooser("restore")

    assert recovery.ensure_working_db(str(live), choose=chooser) is True

    assert chooser.seen_options == ["restore", "empty", "quit"]
    assert chooser.seen_context["backup_name"] == made.name
    # Backup data is live afterwards.
    assert store.get_setting("recovery_marker", path=str(live)) == "from-backup"
    assert _user_version(live) == migrations.LATEST_VERSION
    # Corrupt bytes preserved aside, sidecars travelled with them.
    asides = _asides(tmp_path)
    assert len(asides) == 1
    assert asides[0].read_bytes() == GARBAGE
    assert (tmp_path / (asides[0].name + "-wal")).read_bytes() == wal_bytes
    assert (tmp_path / (asides[0].name + "-shm")).exists()
    assert not (tmp_path / "focuscore.db-wal").exists()
    assert not (tmp_path / "focuscore.db-shm").exists()


def test_restore_picks_newest_verified_backup(tmp_path, monkeypatch):
    folder = _scan_folder(monkeypatch, tmp_path)
    older = _verified_backup(tmp_path, folder, marker="older")
    newer = _verified_backup(tmp_path, folder, marker="newer")
    # Deterministic mtimes, independent of creation-second resolution.
    import os
    os.utime(folder / older.name, (1_000_000, 1_000_000))
    os.utime(folder / newer.name, (2_000_000, 2_000_000))
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("restore")

    assert recovery.ensure_working_db(str(live), choose=chooser) is True
    assert chooser.seen_context["backup_name"] == newer.name
    assert store.get_setting("recovery_marker", path=str(live)) == "newer"


def test_restore_failure_moves_corrupt_back(tmp_path, monkeypatch):
    folder = _scan_folder(monkeypatch, tmp_path)
    _verified_backup(tmp_path, folder)
    live = _live_garbage(tmp_path)

    def exploding_restore(*args, **kwargs):
        raise RuntimeError("restore blew up mid-flow")

    monkeypatch.setattr(backup, "restore_backup", exploding_restore)
    chooser = RecordingChooser("restore")

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert live.read_bytes() == GARBAGE
    assert _asides(tmp_path) == []


def test_restore_failure_when_backup_vanishes(tmp_path, monkeypatch):
    folder = _scan_folder(monkeypatch, tmp_path)
    made = _verified_backup(tmp_path, folder)
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("restore")

    real_restore = backup.restore_backup

    def vanishing_restore(name, **kwargs):
        (folder / made.name).unlink()
        return real_restore(name, **kwargs)

    monkeypatch.setattr(backup, "restore_backup", vanishing_restore)

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert live.read_bytes() == GARBAGE
    assert _asides(tmp_path) == []


# ------------------------------------------- when restore is hidden ---

def test_no_backups_restore_not_offered(tmp_path, monkeypatch):
    _scan_folder(monkeypatch, tmp_path)
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("quit")

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert chooser.seen_options == ["empty", "quit"]


def test_tampered_backup_restore_not_offered(tmp_path, monkeypatch):
    folder = _scan_folder(monkeypatch, tmp_path)
    made = _verified_backup(tmp_path, folder)
    with open(folder / made.name, "ab") as f:
        f.write(b"tampered")
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("quit")

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert chooser.seen_options == ["empty", "quit"]


def test_legacy_backup_restore_not_offered(tmp_path, monkeypatch):
    folder = _scan_folder(monkeypatch, tmp_path)
    _verified_backup(tmp_path, folder)
    for sidecar in folder.glob("*.sha256"):
        sidecar.unlink()
    assert not list(folder.glob("*.sha256"))
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("quit")

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert chooser.seen_options == ["empty", "quit"]


def test_encrypted_only_backup_restore_not_offered(tmp_path, monkeypatch):
    folder = _scan_folder(monkeypatch, tmp_path)
    good = _good_db(tmp_path)
    store.set_setting("backup_encryption_enabled", "1", path=str(good))
    made = backup.create_backup(
        db_path=str(good), dest_dir=str(folder), passphrase=PASSPHRASE)
    from focuscore import backupcrypto
    assert backupcrypto.is_encrypted_file(folder / made.name)
    live = _live_garbage(tmp_path)
    chooser = RecordingChooser("quit")

    assert recovery.ensure_working_db(str(live), choose=chooser) is False
    assert chooser.seen_options == ["empty", "quit"]
    # The dialog is told to mention the passphrase-protected backups.
    assert chooser.seen_context["encrypted_only"] is True
    assert chooser.seen_context["backup_name"] is None


# ------------------------------------------------- default chooser ----

def test_default_chooser_without_tkinter_quits(monkeypatch):
    monkeypatch.setitem(sys.modules, "tkinter", None)
    context = {"db_path": "x.db", "backup_name": None,
               "encrypted_only": False}
    assert recovery._tkinter_choice(["empty", "quit"], context) == "quit"


# --------------------------------------------------------- wiring -----

def test_tray_run_aborts_when_recovery_fails(monkeypatch, tmp_path):
    from focuscore import logging_config, single_instance, tray

    monkeypatch.setattr(
        single_instance, "acquire", lambda **kw: object())
    monkeypatch.setattr(logging_config, "setup_logging", lambda **kw: None)
    monkeypatch.setattr(
        recovery, "ensure_working_db", lambda path=None: False)
    started = []
    monkeypatch.setattr(
        tray, "TrayApp",
        lambda **kw: started.append(kw))

    assert tray.run(db_path=str(tmp_path / "t.db")) is None
    assert started == []


def test_dashboard_main_exits_nonzero_when_recovery_fails(monkeypatch,
                                                          capsys):
    import runpy

    monkeypatch.setattr(
        recovery, "ensure_working_db", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["focus-core-dashboard"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("dashboard.app", run_name="__main__")

    assert excinfo.value.code == 1
    assert "did not start" in capsys.readouterr().out
