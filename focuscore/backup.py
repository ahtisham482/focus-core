"""Automatic local backups (data durability + portability).

The point of this module is simple: keep the user's data safe long-term
and movable to a new laptop. No cloud API, no accounts -- just database
files copied to a safe folder.

Where backups go (in this order):
    1. If Google Drive for Desktop is installed on Windows, backups go to
       "<Drive folder>/Focus Core Backups", so they ride along to the
       cloud automatically and are there waiting on a new laptop.
    2. Otherwise they go to "<project>/backups".

All functions are dependency-free (stdlib only). ``dest_dir`` parameters
exist so tests can point at a temporary folder; normal use never passes
it.
"""

import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import store

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BACKUP_NAME_PATTERN = re.compile(r"^focuscore-\d{8}-\d{6}(-\d+)?\.db$")
BACKUP_FOLDER_NAME = "Focus Core Backups"
PRUNE_KEEP_DEFAULT = 30  # how many backups prune_backups() keeps
STALE_AFTER_HOURS = 24  # backup_if_stale() backs up when newest is older
ATTENTION_AFTER_DAYS = 7  # home page nags when the newest backup is older


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
            PROJECT_ROOT / "backups")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _timestamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_backup(db_path=None, dest_dir=None):
    """Copy the database to a timestamped backup file.

    Uses SQLite's backup API (not a raw file copy) so it is safe even
    while the dashboard is writing to the database. Returns the Path of
    the new backup file. Raises FileNotFoundError when there is no
    database to back up yet.
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
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(target))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return target


def list_backups(dest_dir=None):
    """Backup files, newest first: [{name, path, size_bytes, modified}]."""
    folder = backup_dir(dest_dir)
    backups = []
    for path in folder.glob("focuscore-*.db"):
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
    """Delete all but the newest ``keep`` backups. Returns deleted count."""
    backups = list_backups(dest_dir)
    doomed = backups[keep:]
    for backup in doomed:
        try:
            Path(backup["path"]).unlink()
        except OSError:
            continue
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

    Safety first: the CURRENT database is copied to
    ``focuscore.db.pre-restore-<timestamp>`` next to the database before
    anything is overwritten. Returns the safety-copy Path.

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

    db = Path(db_path or store.DEFAULT_DB_PATH)
    safety = db.parent / ("focuscore.db.pre-restore-%s" % _timestamp())
    if db.exists():
        shutil.copy2(str(db), str(safety))
    else:
        safety = None
    shutil.copy2(str(src), str(db))
    return safety
