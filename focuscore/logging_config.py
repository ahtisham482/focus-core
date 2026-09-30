"""Sprint 4 (v1.12.0, Qwen item 10): bounded application logging.

Flask's default logging is unbounded -- a long-running dashboard can
fill the disk. This module configures a RotatingFileHandler (10 MB x 5
backups = 50 MB max) for the Focus Core application log. Call
setup_logging() once at process startup (dashboard __main__, tray,
shield daemon, launcher).

Roadmap 1.3: every line carries a %(processName)s tag. The app runs
as several OS processes sharing the one log file, and a failure in a
shared file is useless without knowing WHICH process hit it. Call
setup_logging(process_name=...) at each entry point (dashboard /
tray / shield / launcher); set_process_name() re-tags mid-process
(the launcher process becomes the tray process in-process).
"""

import logging
import multiprocessing
import os
from logging.handlers import RotatingFileHandler

LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
LOG_BACKUP_COUNT = 5  # 5 backups -> 50 MB max total
LOG_FORMAT = "%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s"

_configured = False


def set_process_name(name):
    """Give this OS process the name shown in the log tag.

    logging reads multiprocessing.current_process().name when it
    builds each record, so renaming the (single) main process is all
    it takes -- this app is subprocess-based, not multiprocess-based;
    each OS process tags itself at startup.
    """
    multiprocessing.current_process().name = name


def log_path():
    """%LOCALAPPDATA%/Focus Core/focuscore.log (or ~/.focuscore)."""
    try:
        from . import paths
        d = paths.user_data_dir()
    except Exception:
        d = os.path.expanduser("~")
    return os.path.join(str(d), "focuscore.log")


def setup_logging(level=logging.INFO, process_name=None):
    """Idempotent: configure the root logger with a rotating file
    handler. Safe to call multiple times (second call is a no-op).

    process_name tags every record from this process; it is applied
    even when the handler is already configured, so a process whose
    role changes (launcher -> tray) can re-tag itself.
    """
    global _configured
    if process_name:
        set_process_name(process_name)
    if _configured:
        return
    _configured = True
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root = logging.getLogger()
        root.setLevel(level)
        # Avoid duplicate handlers if something else configured logging.
        for h in list(root.handlers):
            if isinstance(h, RotatingFileHandler) and \
                    getattr(h, "baseFilename", "") == path:
                return
        root.addHandler(handler)
    except Exception as exc:
        # Logging must never break startup. At this point no file
        # handler is guaranteed to exist, so this reaches the console
        # of whoever launched us (or nowhere under pythonw) -- which
        # is exactly where a startup environment problem gets seen.
        logging.getLogger(__name__).warning(
            "logging setup failed; continuing without the app log "
            "file: %s", exc)
