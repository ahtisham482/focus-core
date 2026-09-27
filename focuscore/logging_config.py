"""Sprint 4 (v1.12.0, Qwen item 10): bounded application logging.

Flask's default logging is unbounded -- a long-running dashboard can
fill the disk. This module configures a RotatingFileHandler (10 MB x 5
backups = 50 MB max) for the Focus Core application log. Call
setup_logging() once at process startup (dashboard __main__, tray,
shield daemon).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
LOG_BACKUP_COUNT = 5  # 5 backups -> 50 MB max total
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_configured = False


def log_path():
    """%LOCALAPPDATA%/Focus Core/focuscore.log (or ~/.focuscore)."""
    try:
        from . import paths
        d = paths.user_data_dir()
    except Exception:
        d = os.path.expanduser("~")
    return os.path.join(str(d), "focuscore.log")


def setup_logging(level=logging.INFO):
    """Idempotent: configure the root logger with a rotating file
    handler. Safe to call multiple times (second call is a no-op)."""
    global _configured
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
    except Exception:
        # Logging must never break startup.
        pass
