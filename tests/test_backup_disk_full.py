"""Roadmap 1.8: disk-full (ENOSPC) behavior of backup creation + prune.

Disk exhaustion is simulated at a REAL seam: the atomic publish step
(``os.replace``) is monkeypatched to raise ``OSError(ENOSPC)`` -- the
exact failure a full disk produces at the end of a backup. Nothing
about ``create_backup`` itself is mocked, so the test exercises its
real staging / verify / cleanup paths. Live-verified 2026-10-01:

* the raw ``OSError`` propagates with ``errno == ENOSPC``
  (create_backup deliberately does not wrap publish failures),
* no new ``focuscore-*.db`` backup or ``.sha256`` sidecar is published,
* no ``backup_staging-*`` staging file is left in the folder,
* a pre-existing backup (and its sidecar) stays byte-intact,
* pruning is a separate success-path step (callers invoke it
  explicitly): a failed create deletes nothing, and prune on its own
  removes a backup together with its sidecar.
"""

import errno
import hashlib
import os

import pytest

from focuscore import backup, store


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _listing(folder):
    return sorted((p.name, _digest(p)) for p in folder.iterdir())


def _enospc(src, dst, *args, **kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "live.db"
    store.init_db(str(db))
    store.set_setting("diskfull_probe", "1", path=str(db))
    bdir = tmp_path / "backups"
    bdir.mkdir()
    good = backup.create_backup(db_path=str(db), dest_dir=str(bdir))
    return {"db": db, "bdir": bdir, "good": good}


def test_enospc_publish_publishes_nothing_and_keeps_existing(env, monkeypatch):
    before = _listing(env["bdir"])
    assert len(before) == 2  # the good backup + its .sha256 sidecar

    monkeypatch.setattr(os, "replace", _enospc)
    with pytest.raises(OSError) as excinfo:
        backup.create_backup(db_path=str(env["db"]), dest_dir=str(env["bdir"]))
    # The existing error type surfaces unwrapped, with the OS reason.
    assert excinfo.value.errno == errno.ENOSPC

    # No partial backup published, no new sidecar, and the folder is
    # exactly what it was: pre-existing backup byte-intact, no
    # staging file left behind by the interrupted write.
    assert _listing(env["bdir"]) == before
    assert not [name for name, _ in _listing(env["bdir"])
                if "staging" in name]
    names = [b["name"] for b in backup.list_backups(dest_dir=env["bdir"])]
    assert names == [env["good"].name]


def test_failed_create_does_not_prune_anything(env, monkeypatch):
    # Prune only runs on success paths (callers invoke prune_backups
    # explicitly); a failed create must not delete existing backups.
    monkeypatch.setattr(os, "replace", _enospc)
    with pytest.raises(OSError):
        backup.create_backup(db_path=str(env["db"]), dest_dir=str(env["bdir"]))
    names = [b["name"] for b in backup.list_backups(dest_dir=env["bdir"])]
    assert names == [env["good"].name]


def test_prune_removes_backup_and_sidecar_together(env):
    deleted = backup.prune_backups(keep=0, dest_dir=env["bdir"])
    assert deleted == 1
    assert list(env["bdir"].iterdir()) == []
