"""Roadmap 1.13: corrupt-database safe mode.

The two app-start entries (``tray.run`` and the dashboard ``__main__``)
wrap database startup in :func:`ensure_working_db` instead of calling
``store.init_db`` bare. When the database file is corrupt, startup no
longer crashes unguarded: the user is offered "restore the latest
verified backup" (only when one actually exists and is not encrypted)
or "start with an empty database" -- or they can quit, in which case
nothing on disk is touched.

Safety rules (binding, from the 1.13 blackboard):

* Recovery NEVER auto-picks restore or empty. No choice, no change:
  an unreadable chooser answer, a chooser that blows up, or a dialog
  that cannot be shown all fail closed to "quit".
* The corrupt file is never deleted. It is renamed aside to
  ``<db>.corrupt-<YYYYmmdd-HHMMSS>`` (counter suffix on collision),
  and its ``-wal``/``-shm`` sidecars travel with the SAME suffix --
  a stale WAL left at the live path would be replayed by SQLite into
  whatever database lands there next.
* Restore moves the corrupt set aside BEFORE calling
  ``backup.restore_backup``: restore safety-copies the live DB via
  VACUUM INTO, which raises on a garbage file. On ANY restore
  failure the corrupt set is moved back byte-identical and startup
  aborts.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from . import backup, backupcrypto, migrations, store

logger = logging.getLogger(__name__)

CHOICE_RESTORE = "restore"
CHOICE_EMPTY = "empty"
CHOICE_QUIT = "quit"

_SIDECARS = ("-wal", "-shm")


def _move_aside(db):
    """Rename the corrupt DB (and its -wal/-shm sidecars) aside.

    Returns the aside Path. The stamp suffix computed for the main
    file is applied verbatim to the sidecars so the whole set shares
    one name. The corrupt bytes are never deleted. If a rename fails
    partway, the files already moved are rolled back to the live
    path (best effort, logged exactly) before the OSError re-raises.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = db.parent / ("%s.corrupt-%s" % (db.name, stamp))
    counter = 2
    while aside.exists():
        aside = db.parent / (
            "%s.corrupt-%s-%d" % (db.name, stamp, counter))
        counter += 1
    moved = []  # (original, aside) pairs, in the order they moved
    try:
        db.rename(aside)
        moved.append((db, aside))
        for ext in _SIDECARS:
            side = Path(str(db) + ext)
            if side.exists():
                target = Path(str(aside) + ext)
                side.rename(target)
                moved.append((side, target))
    except OSError:
        # A rename failed partway: some files may already sit under
        # the aside names while the rest are still live. Roll back
        # whatever moved so the set is whole again, and log exactly
        # what happened instead of claiming nothing changed.
        rollback_failures = []
        for original, moved_to in reversed(moved):
            try:
                moved_to.rename(original)
            except OSError as rb_exc:
                rollback_failures.append((original, moved_to, rb_exc))
        if not moved:
            logger.warning(
                "Moving %s aside failed before any file moved; "
                "the original files are untouched.", db)
        elif not rollback_failures:
            logger.warning(
                "Moving %s aside failed partway; rolled back %d "
                "file(s), so the original files are back at the "
                "live path.", db, len(moved))
        else:
            logger.error(
                "Moving %s aside failed partway and %d of %d "
                "already-moved file(s) could not be rolled back "
                "(%s). The corrupt bytes are preserved, split "
                "between the live path and %s; startup is aborted.",
                db, len(rollback_failures), len(moved),
                "; ".join(
                    f"{orig} stays at {dest} ({exc})"
                    for orig, dest, exc in rollback_failures),
                aside)
        raise
    return aside


def _move_back(aside, db):
    """Undo :func:`_move_aside` after a failed restore.

    Anything the failed restore left at the live path is removed
    first (restore_backup's own staging file is already cleaned by
    restore_backup itself), then the corrupt set returns under its
    original names.
    """
    for ext in _SIDECARS:
        live_side = Path(str(db) + ext)
        if live_side.exists():
            live_side.unlink()
    if db.exists():
        db.unlink()
    aside.rename(db)
    for ext in _SIDECARS:
        side = Path(str(aside) + ext)
        if side.exists():
            side.rename(Path(str(db) + ext))


def _latest_verified_backup():
    """Newest verified, NON-encrypted backup, or None.

    Returns ``(name, folder, encrypted_only)``: ``folder`` is the
    Drive-aware backup folder (``backup.backup_dir(None)`` -- the
    same folder the /backup page looks in, Google Drive mirror when
    present), and ``encrypted_only`` is True when verified backups
    exist but every one of them is passphrase-encrypted (those need
    the Backup page's passphrase prompt, so safe mode cannot offer
    them -- it only mentions them in the dialog).
    """
    folder = backup.backup_dir(None)
    usable = []
    encrypted_seen = False
    for result in backup.verify_all_backups(dest_dir=folder):
        if not result["ok"]:
            continue
        path = folder / result["name"]
        if backupcrypto.is_encrypted_file(path):
            encrypted_seen = True
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        usable.append((mtime, result["name"]))
    if not usable:
        return None, folder, encrypted_seen
    # Newest by mtime; name tiebreak keeps the pick deterministic.
    usable.sort()
    return usable[-1][1], folder, False


def _restore_flow(db, db_path, name, folder):
    """Move the corrupt set aside, restore ``name``, re-init.

    On any failure the corrupt set is moved back to the live path
    and False is returned -- the user keeps exactly what they had.
    """
    try:
        aside = _move_aside(db)
    except OSError:
        logger.exception(
            "Could not move the corrupt database %s aside; "
            "startup aborted.", db)
        return False
    try:
        backup.restore_backup(name, db_path=str(db), dest_dir=folder)
        store.init_db(db_path)
    except Exception:
        logger.exception(
            "Restoring backup %s failed; putting the original "
            "database file back.", name)
        try:
            _move_back(aside, db)
        except OSError:
            logger.exception(
                "Could not move the corrupt database back from %s; "
                "it is preserved at that path.", aside)
        return False
    logger.info(
        "Restored backup %s over the corrupt database; the corrupt "
        "file is preserved at %s.", name, aside)
    return True


def _empty_flow(db, db_path):
    """Move the corrupt set aside and let init_db create a fresh DB."""
    try:
        aside = _move_aside(db)
    except OSError:
        logger.exception(
            "Could not move the corrupt database %s aside; "
            "startup aborted.", db)
        return False
    try:
        store.init_db(db_path)
    except Exception:
        logger.exception(
            "Could not create a fresh database at %s; the corrupt "
            "file is preserved at %s.", db, aside)
        return False
    logger.info(
        "Started with a fresh database at %s; the corrupt file is "
        "preserved at %s.", db, aside)
    return True


def ensure_working_db(db_path=None, choose=None):
    """Make sure the database opens; offer safe-mode recovery if not.

    Happy path: ``store.init_db(db_path)`` succeeds and True is
    returned. Two failures route to the chooser instead: the init
    raising ``migrations.MigrationError`` /
    ``sqlite3.DatabaseError`` (the exception is logged at ERROR),
    and an existing zero-byte database file (corruption, not a
    first run; logged at ERROR as "database file is empty"). A
    missing file is always a first run and never sees the chooser.
    The backup scan itself can never raise out of here: a scan
    failure is logged at WARNING and treated as "no verified
    backup found". The chooser is offered:

    * "restore" -- only offered when a latest verified, non-
      encrypted backup exists;
    * "empty" -- move the corrupt file aside, create a fresh DB;
    * "quit" -- touch nothing, return False.

    Any other chooser return value is treated as "quit". ``choose``
    is an injectable ``choose(options, context) -> str``; the default
    is the tkinter dialog (:func:`_tkinter_choice`), which fails
    closed to "quit" when no dialog can be shown. True is returned
    only after a successful repair plus a second successful
    ``init_db``; False means "abort startup, DB untouched or fully
    restored to its corrupt-but-preserved state".
    """
    resolved = Path(store._resolve_db_path(db_path))
    # A zero-byte file is corruption, not a first run (parent
    # decision): SQLite would otherwise open it as an empty database
    # and migrate it in place with no offer. A MISSING file is the
    # true first run and goes straight to fresh init below. This
    # check lives here only; store.init_db keeps treating a 0-byte
    # file as fresh when called directly.
    try:
        is_empty_file = (
            resolved.is_file() and resolved.stat().st_size == 0)
    except OSError:
        is_empty_file = False
    if is_empty_file:
        logger.error(
            "Focus Core could not open its database: database file "
            "is empty (%s).", resolved)
    else:
        try:
            store.init_db(db_path)
            return True
        except (migrations.MigrationError, sqlite3.DatabaseError) as exc:
            logger.error(
                "Focus Core could not open its database: %s", exc,
                exc_info=True)

    # The backup scan must never escape: if the backup folder cannot
    # be listed/verified, log it and offer empty/quit only (fail
    # closed, exactly as if no verified backup existed).
    try:
        backup_name, folder, encrypted_only = _latest_verified_backup()
    except Exception as scan_exc:  # noqa: BLE001 -- never escape startup
        logger.warning(
            "Could not check for verified backups (%s); offering "
            "Start-empty/Quit only, with no backup to restore.",
            scan_exc)
        backup_name, folder, encrypted_only = None, None, False

    options = []
    if backup_name is not None:
        options.append(CHOICE_RESTORE)
    options.extend([CHOICE_EMPTY, CHOICE_QUIT])
    context = {
        "db_path": str(resolved),
        "backup_name": backup_name,
        "encrypted_only": encrypted_only,
    }

    chooser = choose if choose is not None else _tkinter_choice
    try:
        choice = chooser(list(options), context)
    except Exception:
        logger.exception(
            "The recovery choice could not be collected; quitting "
            "without changing anything.")
        return False

    if choice == CHOICE_RESTORE and backup_name is not None:
        return _restore_flow(resolved, db_path, backup_name, folder)
    if choice == CHOICE_EMPTY:
        return _empty_flow(resolved, db_path)
    if choice != CHOICE_QUIT:
        logger.warning(
            "Unexpected recovery choice %r; treating it as quit. "
            "The database was not changed.", choice)
    else:
        logger.info(
            "Recovery declined; the database at %s was not changed.",
            resolved)
    return False


def _tkinter_choice(options, context):
    """Default chooser: a small plain-English tkinter dialog.

    One button per offered option. When tkinter or a display is
    unavailable this logs a clear message and returns "quit" --
    fail closed, never guess.
    """
    try:
        import tkinter
    except Exception:
        logger.error(
            "The database is damaged and the recovery window could "
            "not be shown (no display available). Nothing was "
            "changed. Start Focus Core from a desktop session, or "
            "open the Backup page after a fresh start, to recover.")
        return CHOICE_QUIT

    backup_name = context.get("backup_name")
    lines = [
        "Focus Core could not open its database file:",
        str(context.get("db_path", "")),
        "",
        "The file may be damaged. It will be kept exactly as it is "
        "-- nothing is deleted, whichever you choose below.",
    ]
    if backup_name:
        lines += [
            "",
            "A backup that passed its safety check is "
            "available: %s" % backup_name,
        ]
    if context.get("encrypted_only"):
        lines += [
            "",
            "Note: your only checked backups are protected with a "
            "passphrase. They cannot be restored here. If you start "
            "with an empty database, you can restore one afterwards "
            "from the Backup page by typing its passphrase.",
        ]
    message = "\n".join(lines)
    labels = {
        CHOICE_RESTORE: "Restore my latest good backup",
        CHOICE_EMPTY: "Start with an empty database",
        CHOICE_QUIT: "Quit without changing anything",
    }

    result = {"choice": CHOICE_QUIT}
    try:
        root = tkinter.Tk()
    except Exception:
        logger.error(
            "The database is damaged and the recovery window could "
            "not be shown (no display available). Nothing was "
            "changed. Start Focus Core from a desktop session, or "
            "open the Backup page after a fresh start, to recover.")
        return CHOICE_QUIT
    try:
        root.title("Focus Core - database problem")
        tkinter.Label(
            root, text=message, justify="left", wraplength=520,
            padx=16, pady=12).pack()

        def pick(value):
            result["choice"] = value
            root.destroy()

        for option in options:
            tkinter.Button(
                root, text=labels.get(option, option),
                command=lambda value=option: pick(value),
            ).pack(fill="x", padx=16, pady=4)
        root.protocol(
            "WM_DELETE_WINDOW", lambda: pick(CHOICE_QUIT))
        root.mainloop()
    except Exception:
        logger.exception(
            "The recovery window failed; quitting without changing "
            "anything.")
        return CHOICE_QUIT
    return result["choice"]
