"""Automatic local backups (data durability + portability).

The point of this module is simple: keep the user's data safe long-term
and movable to a new laptop. No cloud API, no accounts -- just database
files copied to a safe folder.

Where backups go (in this order):
    1. If Google Drive for Desktop is installed on Windows, backups go to
       "<Drive folder>/Focus Core Backups", so they ride along to the
       cloud automatically and are there waiting on a new laptop.
    2. Otherwise they go to "<project>/backups".

Durability guarantees (FC-001):
    * Atomic backup writes. A backup is first written to a temporary
      file (``<name>.db.tmp``) inside the backup folder and then moved
      into place with ``os.replace``, which is an atomic rename. The
      final ``focuscore-<timestamp>.db`` name therefore never points at
      a partially written file. If the write is interrupted, the temp
      file is cleaned up (and any leftover ``*.tmp`` file is ignored by
      ``list_backups()`` -- it is never listed as a valid backup).
    * SHA256 integrity checks. Every backup created by
      ``create_backup()`` gets a sidecar file
      ``<name>.db.sha256`` holding the backup's SHA256 hex digest.
      ``restore_backup()`` verifies the digest BEFORE touching the
      live database and refuses (``ValueError``) to restore a backup
      whose checksum is missing-but-present or mismatched -- i.e. a
      corrupt or tampered file.
    * Backward compatibility. Backups created before checksums existed
      (e.g. the ``focuscore-20260925-*.db`` files) have no sidecar.
      Those still restore, but a warning is recorded (``logging`` +
      ``warnings.warn``) that integrity could not be verified.
    * Atomic restores. ``restore_backup()`` first copies the CURRENT
      database to ``focuscore.db.pre-restore-<timestamp>`` (the safety
      copy), then copies the backup to a temp file next to the live
      database and atomically renames it over the old one. A crash
      mid-restore cannot leave the live database half-overwritten, and
      the pre-restore state is always recoverable from the safety copy.
    * ``prune_backups()`` deletes a backup's ``.sha256`` sidecar along
      with the backup itself so no orphan sidecars accumulate.
    * ``verify_all_backups()`` re-verifies the SHA256 integrity of
      EVERY stored backup on demand and reports per-backup results
      (``{"name", "ok", "reason"}``) without raising -- the way to
      audit the whole backup folder at once, e.g. before trusting a
      restore. A missing sidecar is reported as a legacy backup whose
      integrity is not verifiable; a digest that does not match is
      reported as a checksum mismatch.

All functions are dependency-free (stdlib only). ``dest_dir`` parameters
exist so tests can point at a temporary folder; normal use never passes
it. This module never changes the database schema -- it only copies
whole database files.
"""

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import warnings
from datetime import datetime
from pathlib import Path

from . import paths
from . import store

logger = logging.getLogger(__name__)

BACKUP_NAME_PATTERN = re.compile(r"^focuscore-\d{8}-\d{6}(-\d+)?\.db$")
BACKUP_FOLDER_NAME = "Focus Core Backups"
PRUNE_KEEP_DEFAULT = 30  # how many backups prune_backups() keeps
STALE_AFTER_HOURS = 24  # backup_if_stale() backs up when newest is older
ATTENTION_AFTER_DAYS = 7  # home page nags when the newest backup is older

TMP_SUFFIX = ".tmp"  # temp files during create/restore; never listed
CHECKSUM_SUFFIX = ".sha256"  # sidecar holding the backup's SHA256 digest


def find_drive_folder():
    """Google Drive for Desktop's local folder, or None.

    Drive for Desktop keeps a local mirror folder inside the user's home
    directory, usually called "Google Drive" (older installs) or
    "My Drive". Both are checked. Returns None on any platform when
    neither exists -- callers must handle that gracefully.
    """
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    for name in ("Google Drive", "My Drive"):
        candidate = home / name
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def backup_dir(dest_dir=None):
    """Folder backups are written to (created on demand)."""
    if dest_dir is not None:
        folder = Path(dest_dir)
    else:
        drive = find_drive_folder()
        folder = (drive / BACKUP_FOLDER_NAME) if drive else (
            paths.backups_dir())
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _timestamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _checksum_path(backup_path):
    """Sidecar path holding the SHA256 digest of a backup file."""
    return Path(str(backup_path) + CHECKSUM_SUFFIX)


def _sha256_of(path):
    """Hex SHA256 digest of a file, streamed so big backups stay cheap."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_stored_checksum(backup_path):
    """Digest recorded in the sidecar, or None when there is no sidecar.

    A sidecar that exists but is empty/unparseable yields "" -- it is
    present-but-broken and will fail verification (never treated as a
    legacy backup).
    """
    try:
        text = _checksum_path(backup_path).read_text(encoding="utf-8")
    except OSError:
        return None  # no sidecar: legacy backup, integrity unverifiable
    text = text.strip()
    return text.split()[0] if text else ""


def _write_checksum(backup_path):
    """Record the SHA256 sidecar for a finished backup file."""
    _checksum_path(backup_path).write_text(
        _sha256_of(backup_path) + "\n", encoding="utf-8")


def _verify_checksum(backup_path):
    """Check a backup's integrity against its sidecar before restoring.

    Returns True when the checksum verifies. Returns False -- after
    recording a warning -- when there is no sidecar at all (legacy
    backup from before integrity checks existed); the caller may then
    proceed at its own risk. Raises ValueError when a sidecar exists
    but the digest does not match: the file is corrupt or tampered
    with and must not be restored.
    """
    stored = _read_stored_checksum(backup_path)
    if stored is None:
        message = (
            "Backup %s has no SHA256 checksum sidecar (created before "
            "integrity checks were added); its integrity could not be "
            "verified." % backup_path.name)
        logger.warning(message)
        warnings.warn(message, UserWarning)
        return False
    actual = _sha256_of(backup_path)
    if stored != actual:
        raise ValueError(
            "This backup failed its safety check: it looks damaged or "
            "was changed after it was made, so restoring it was stopped "
            "to protect your data. Please pick a different backup from "
            "the list.")
    return True


def create_backup(db_path=None, dest_dir=None):
    """Copy the database to a timestamped backup file.

    Uses SQLite's backup API (not a raw file copy) so it is safe even
    while the dashboard is writing to the database. The backup is
    written to a temp file and atomically renamed into place, then a
    SHA256 sidecar is recorded. Returns the Path of the new backup
    file. Raises FileNotFoundError when there is no database to back
    up yet.
    """
    src = Path(db_path or store.DEFAULT_DB_PATH)
    if not src.exists():
        raise FileNotFoundError(
            "No database found at %s -- nothing to back up yet." % src)
    folder = backup_dir(dest_dir)
    target = folder / ("focuscore-%s.db" % _timestamp())
    # Two backups inside the same second must not overwrite each other.
    counter = 2
    while target.exists():
        target = folder / ("focuscore-%s-%d.db" % (_timestamp(), counter))
        counter += 1
    tmp = Path(str(target) + TMP_SUFFIX)
    try:
        src_conn = sqlite3.connect(str(src))
        try:
            dst_conn = sqlite3.connect(str(tmp))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        # Atomic publish: the final name never points at a partial file.
        os.replace(str(tmp), str(target))
    finally:
        # An interrupted write must not leave a partial backup behind.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    _write_checksum(target)
    return target


def list_backups(dest_dir=None):
    """Backup files, newest first: [{name, path, size_bytes, modified}].

    Only finished backups are listed: temp/interrupted files
    (``*.tmp``) and checksum sidecars (``*.sha256``) are ignored, as is
    anything not matching the strict backup name pattern.
    """
    folder = backup_dir(dest_dir)
    backups = []
    for path in folder.glob("focuscore-*.db"):
        if path.name.endswith(TMP_SUFFIX):
            continue
        if not BACKUP_NAME_PATTERN.match(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        backups.append({
            "name": path.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime),
        })
    backups.sort(key=lambda b: b["name"], reverse=True)
    return backups


def newest_backup(dest_dir=None):
    """The newest backup dict, or None when there are no backups."""
    backups = list_backups(dest_dir)
    return backups[0] if backups else None


def prune_backups(keep=PRUNE_KEEP_DEFAULT, dest_dir=None):
    """Delete all but the newest ``keep`` backups. Returns deleted count.

    Each backup's SHA256 sidecar is deleted with it so no orphan
    sidecars accumulate.
    """
    backups = list_backups(dest_dir)
    doomed = backups[keep:]
    for backup in doomed:
        try:
            Path(backup["path"]).unlink()
        except OSError:
            continue
        try:
            _checksum_path(backup["path"]).unlink(missing_ok=True)
        except OSError:
            pass
    return len(doomed)


def backup_if_stale(max_age_hours=STALE_AFTER_HOURS, db_path=None,
                    dest_dir=None):
    """Create a backup only when the newest one is older than the limit.

    Returns the new backup Path, or None when the newest backup is still
    fresh (or there is no database yet). Never raises for a missing
    database -- the launcher calls this on every start and must not
    crash because of it.
    """
    src = Path(db_path or store.DEFAULT_DB_PATH)
    if not src.exists():
        return None
    newest = newest_backup(dest_dir)
    if newest is not None:
        age_hours = (datetime.now() - newest["modified"]).total_seconds() \
            / 3600.0
        if age_hours < max_age_hours:
            return None
    return create_backup(db_path=db_path, dest_dir=dest_dir)


def restore_backup(name, db_path=None, dest_dir=None):
    """Restore a backup over the current database.

    Safety first: the backup's SHA256 checksum is verified before
    anything is touched -- a corrupt or tampered backup is refused with
    ``ValueError``. Backups without a checksum sidecar (created before
    integrity checks existed) still restore, with a warning that their
    integrity could not be verified. Then the CURRENT database is copied
    to ``focuscore.db.pre-restore-<timestamp>`` next to the database,
    and the backup is copied to a temp file and atomically renamed over
    the live database, so an interrupted restore can never leave the
    database half-overwritten. Returns the safety-copy Path.

    ``name`` must be a plain backup file name (e.g.
    ``focuscore-20260925-120000.db``); path separators are rejected so a
    crafted name cannot read or write outside the backup folder.
    """
    if not BACKUP_NAME_PATTERN.match(name or ""):
        raise ValueError("Not a valid backup name: %r" % (name,))
    folder = backup_dir(dest_dir)
    src = folder / name
    if not src.exists():
        raise FileNotFoundError("Backup not found: %s" % name)

    _verify_checksum(src)

    db = Path(db_path or store.DEFAULT_DB_PATH)
    safety = db.parent / ("focuscore.db.pre-restore-%s" % _timestamp())
    if db.exists():
        shutil.copy2(str(db), str(safety))
    else:
        safety = None
    tmp = db.parent / (db.name + TMP_SUFFIX)
    try:
        shutil.copy2(str(src), str(tmp))
        # Atomic swap: the live database is never half-overwritten.
        os.replace(str(tmp), str(db))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return safety


def verify_all_backups(dest_dir=None):
    """Re-verify the SHA256 integrity of every stored backup.

    Scans ``list_backups()`` and returns one dict per backup::

        {"name": ..., "ok": True/False, "reason": ...}

    ``ok`` is True and ``reason`` is ``"checksum matches"`` when the
    backup's SHA256 sidecar exists and matches the file. ``ok`` is False
    with an explanatory ``reason`` otherwise:

    * ``"legacy backup, integrity not verifiable"`` -- no sidecar at
      all (backup created before integrity checks existed);
    * ``"checksum mismatch"`` -- a sidecar exists but its digest does
      not match the file (corrupt or tampered backup);
    * a ``"could not read ..."`` reason -- the backup or its sidecar
      could not be read from disk.

    This function never raises on a bad file: problems are reported in
    the returned dicts, never thrown. It is the on-demand way to audit
    the whole backup folder at once, while ``restore_backup()`` checks
    one backup at restore time.
    """
    results = []
    for entry in list_backups(dest_dir=dest_dir):
        path = Path(entry["path"])
        try:
            stored = _read_stored_checksum(path)
        except OSError:
            results.append({
                "name": entry["name"],
                "ok": False,
                "reason": "could not read checksum sidecar",
            })
            continue
        if stored is None:
            results.append({
                "name": entry["name"],
                "ok": False,
                "reason": "legacy backup, integrity not verifiable",
            })
            continue
        try:
            actual = _sha256_of(path)
        except OSError:
            results.append({
                "name": entry["name"],
                "ok": False,
                "reason": "could not read backup file",
            })
            continue
        if stored != actual:
            results.append({
                "name": entry["name"],
                "ok": False,
                "reason": "checksum mismatch",
            })
        else:
            results.append({
                "name": entry["name"],
                "ok": True,
                "reason": "checksum matches",
            })
    return results
