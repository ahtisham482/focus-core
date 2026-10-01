"""Where Focus Core keeps its files.

Two modes:

- Portable / dev (default): everything lives next to the code, exactly
  as before. This is also how the developer's own folder works.
- Installed (Inno Setup): the install folder can be read-only, so user
  data (database, backups, flags) goes to %LOCALAPPDATA%\\Focus Core
  on Windows (or ~/.focus-core elsewhere).

Installed mode is detected by a `.installed` marker file at the app root,
which the installer drops in. A database that already exists next to the
code is always honored first, so existing portable installs keep working
unchanged after an upgrade.

In installed mode only, unified config (focuscore.config) may override
the data dir with its `data_dir` key -- that is how IT pre-configures
where fleet machines keep their data. Portable/dev mode never consults
config here, so a stray FOCUSCORE_DATA_DIR in some shell can never
relocate a dev or portable database.
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent
INSTALLED_MARKER = APP_ROOT / ".installed"
DB_NAME = "focuscore.db"
USER_DATA_DIR_NAME = "Focus Core"


def is_installed():
    """True when running from an installed copy (marker dropped by installer)."""
    return INSTALLED_MARKER.exists()


def user_data_dir():
    """Per-user writable folder used in installed mode."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / USER_DATA_DIR_NAME
    return Path.home() / ".focus-core"


def _configured_data_dir():
    """data_dir from unified config, or None. Installed mode only.

    The import is lazy on purpose: focuscore.config finds its TOML
    through this module, so importing it at module level would cycle.

    A configured value that cannot be expanded (e.g. a ``~user``
    typo whose home cannot be determined) must never brick startup:
    warn and fall through to the per-user data dir, exactly like
    every other invalid config value falls through tiers.
    """
    from . import config
    value = config.get_data_dir()
    if not value:
        return None
    try:
        return Path(value).expanduser()
    except Exception as exc:  # noqa: BLE001 -- never raise into startup
        logger.warning(
            "ignoring unresolvable data_dir %r from config: %s",
            value, exc)
        return None


def data_dir():
    """Folder holding the database, backups, and flags.

    Pure: computes the location without creating anything.
    """
    if (APP_ROOT / DB_NAME).exists():
        return APP_ROOT  # grandfather existing portable installs
    if is_installed():
        configured = _configured_data_dir()
        if configured is not None:
            return configured
        return user_data_dir()
    return APP_ROOT


def ensure_data_dir():
    """Create the data folder if needed. Call once at app startup."""
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def db_path():
    """Full path of the SQLite database."""
    return data_dir() / DB_NAME


def backups_dir():
    """Folder local backups go to when Google Drive is not available."""
    return data_dir() / "backups"


def onboarded_flag():
    """File marking that the welcome tour was finished or skipped."""
    return data_dir() / ".onboarded"
