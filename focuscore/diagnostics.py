"""Roadmap 0.4: one-click "Export diagnostics" support bundle.

Builds a small zip the user can hand to support when something breaks.
The bundle is diagnostics ONLY -- the privacy rules are hard:

* NEVER the database, and never activity / timesheet content.
* Settings are listed with secrets redacted (key-name rules plus
  token-shaped value rules).
* The log tail is anonymized: home paths and the Windows/POSIX user
  name are replaced with placeholders before anything leaves the app.
* The data folder is listed by name + size only, never contents.

Each section collector is defensive: if one part fails, that section
says so in plain words instead of failing the whole export -- a partial
bundle is still useful to support.
"""

import configparser
import getpass
import io
import logging
import os
import platform
import re
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import pathname2url

from . import __version__ as APP_VERSION

logger = logging.getLogger(__name__)

LOG_TAIL_LINES = 200
REDACTED = "REDACTED"

_SECRET_KEY = re.compile(
    r"token|secret|password|passwd|passphrase|passcode|phrase"
    r"|api[_-]?key|auth|credential|ical|url|private|licen[cs]e"
    r"|(?:^|[_-])(?:pin|key)(?:$|[_-])", re.IGNORECASE)
# User profile names can contain spaces ("C:\Users\Spaced Name99"), so
# the name part runs up to the next path separator, spaces included.
_USER_DIR_PATTERNS = (
    (re.compile(r"[A-Za-z]:\\Users\\[^\\]+"), "C:\\Users\\<user>"),
    (re.compile(r"/home/[^/]+"), "/home/<user>"),
    (re.compile(r"/Users/[^/]+"), "/Users/<user>"),
)

_CODE_SHAPE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+")


def _replacement(text):
    """re.sub replacement via a function, so backslashes in the
    replacement (Windows paths) are taken literally."""
    return lambda _m: text


def _looks_code(value):
    """Dash-grouped code shape, e.g. XK9-22QM-88ZA (license keys, short
    tokens). Needs at least one letter AND one digit so plain dates
    (2026-09-30) and words (utf-8) are left alone."""
    if " " in value or not _CODE_SHAPE.fullmatch(value):
        return False
    return bool(re.search(r"[A-Za-z]", value)
                and re.search(r"\d", value))


def _looks_passphrase(value):
    """Multi-word passphrase shape: 4+ plain tokens, at least 3 of them
    lowercase words. Short display names (Focus Core Backups) stay."""
    tokens = value.split()
    if len(tokens) < 4 or len(value) < 15:
        return False
    if not all(re.fullmatch(r"[A-Za-z0-9]+", t) for t in tokens):
        return False
    words = [t for t in tokens if t.isalpha() and t.islower()]
    return len(words) >= 3


def _looks_secret(value):
    """Secret-shaped value detection (independent of the setting name)."""
    v = (value or "").strip()
    if not v:
        return False
    if "://" in v:  # any URL may carry credentials (e.g. secret iCal)
        return True
    if len(v) >= 24 and " " not in v and re.search(r"[A-Za-z]", v) \
            and re.search(r"\d", v):
        return True
    return _looks_code(v) or _looks_passphrase(v)


def redact_setting(key, value):
    if _SECRET_KEY.search(key or "") or _looks_secret(value):
        return REDACTED
    return value if value is not None else ""


def anonymize(text):
    """Scrub home paths and user names from diagnostic text."""
    if not text:
        return text or ""
    home = os.path.expanduser("~")
    if home and home not in (os.sep, "."):
        text = text.replace(home, "~")
    for pattern, repl in _USER_DIR_PATTERNS:
        text = pattern.sub(_replacement(repl), text)
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if user:
        text = re.sub(r"\b%s\b" % re.escape(user), "<user>", text)
    return text


def _section(name, fn):
    try:
        return fn()
    except Exception:
        logger.exception("diagnostics: %s section failed", name)
        return "(could not collect this section)\n"


def _system_section(db_path):
    lines = ["Focus Core diagnostics", "=" * 22, ""]
    lines.append("Focus Core version: %s" % APP_VERSION)
    lines.append("Exported: %s" % datetime.now().isoformat(timespec="seconds"))
    lines.append("OS: %s" % platform.platform())
    win = platform.win32_ver()
    if win and win[0]:
        lines.append("Windows version: %s (build %s)" % (win[0], win[2]))
    lines.append("Python: %s" % sys.version.split()[0])
    lines.append("WebView2: %s" % _webview2_version())
    try:
        from . import paths
        lines.append("Install mode: %s"
                     % ("installed" if paths.is_installed() else
                        "portable / developer copy"))
    except Exception:
        logger.exception("diagnostics: could not work out the "
                         "install mode")
    lines.append("")
    lines.append("Database integrity_check: %s" % _integrity(db_path))
    return "\n".join(lines) + "\n"


def _webview2_version():
    if sys.platform != "win32":
        return "not applicable (WebView2 is Windows-only)"
    try:
        import winreg
        key = (r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
               r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, key) as k:
                    return winreg.QueryValueEx(k, "pv")[0]
            except OSError:
                continue
    except Exception:
        logger.exception("diagnostics: WebView2 detection failed")
    return "not detected"


def _integrity(db_path):
    if not db_path or not os.path.exists(str(db_path)):
        return "no database file yet"
    try:
        conn = sqlite3.connect(
            "file:%s?mode=ro" % pathname2url(str(db_path)), uri=True)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
        return "; ".join(str(r[0]) for r in rows) or "ok"
    except Exception as exc:
        return "could not run (%s)" % exc.__class__.__name__


def _settings_section(db_path):
    from . import store
    rows = []
    try:
        conn = store.get_db(db_path)
        try:
            rows = conn.execute(
                "SELECT key, value FROM settings ORDER BY key").fetchall()
        finally:
            conn.close()
    except Exception:
        rows = []
    if not rows:
        return "Settings\n========\n\n(none stored yet)\n"
    out = ["Settings (secrets hidden)", "=========================",
           "",
           "Values marked REDACTED are secrets (tokens, keys, calendar",
           "links) -- they are never included in this file.", ""]
    for row in rows:
        out.append("%s = %s" % (row[0], redact_setting(row[0], row[1])))
    # Values can hold paths (e.g. a backup folder) -- scrub user names
    # here too; the write step in build_diagnostics_zip anonymizes
    # again as a second net.
    return anonymize("\n".join(out)) + "\n"


def _data_dir_section(data_dir):
    root = Path(data_dir)
    out = ["Data folder (names and sizes only -- no contents)",
           "================================================", ""]
    if not root.exists():
        return "\n".join(out + ["(folder does not exist yet)"]) + "\n"
    entries = []
    for current, dirnames, filenames in os.walk(root):
        dirnames.sort()
        # Hidden folders (.git in a dev copy) are not support-relevant.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        depth = len(Path(current).relative_to(root).parts)
        if depth > 1:
            dirnames[:] = []
        for name in sorted(filenames):
            p = Path(current) / name
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            entries.append("%s  (%d bytes)"
                           % (p.relative_to(root), size))
    if not entries:
        out.append("(empty)")
    out.extend(entries)
    return anonymize("\n".join(out)) + "\n"


def _log_tail_section(log_file):
    out = ["Recent log (last %d lines, names and paths anonymized)"
           % LOG_TAIL_LINES,
           "=" * 55, ""]
    if not log_file or not os.path.exists(str(log_file)):
        return "\n".join(out + ["(no log file found)"]) + "\n"
    with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
        tail = fh.readlines()[-LOG_TAIL_LINES:]
    out.extend(line.rstrip("\n") for line in tail)
    return anonymize("\n".join(out)) + "\n"


def _installer_section(app_root, data_dir):
    out = ["Installer", "=========", ""]
    candidates = [Path(app_root) / "installer.ini",
                  Path(data_dir) / "installer.ini"]
    ini = next((p for p in candidates if p.exists()), None)
    if ini is not None:
        out.append("installer.ini found: %s" % ini.name)
        parser = configparser.ConfigParser()
        try:
            parser.read(str(ini), encoding="utf-8")
            for section in parser.sections():
                for key, value in parser.items(section):
                    out.append("[%s] %s = %s"
                               % (section, key,
                                  redact_setting(key, value)))
        except Exception:
            out.append("(could not read installer.ini)")
        return anonymize("\n".join(out)) + "\n"
    out.append("No installer.ini file (Focus Core is installed with "
               "Inno Setup; there is no separate ini to summarize).")
    try:
        from . import paths
        out.append("Install mode: %s"
                   % ("installed copy" if paths.is_installed()
                      else "portable / developer copy"))
    except Exception:
        logger.exception("diagnostics: could not work out the "
                         "install mode")
    return "\n".join(out) + "\n"


_README = """\
Focus Core -- diagnostics export
================================

What this is
------------
A small bundle of technical details about your copy of Focus Core.
If something is not working, send this whole zip when you ask for help.

What is inside
--------------
- system.txt    Focus Core version, your Windows and Python versions,
                and a database health check result.
- settings.txt  Your settings, with secrets (tokens, keys, calendar
                links) replaced by the word REDACTED, and your user
                name removed from any folder paths.
- data-dir.txt  The names and sizes of files in your data folder.
                Names and sizes only -- never the contents.
- log-tail.txt  The last 200 lines of the app log, with your user name
                and home folder paths replaced by placeholders.
- installer.txt Notes about how this copy was installed.

What is NOT inside -- on purpose
--------------------------------
- Your database (focuscore.db) is never included.
- No activity, timesheet, goal, or any other personal data.
- No passwords, tokens, or calendar links -- they are hidden first.
"""


def build_diagnostics_zip(db_path=None, log_file=None, data_dir=None,
                          app_root=None):
    """The diagnostics bundle as zip bytes. Never raises for a missing
    piece; a section that cannot be collected says so in plain words."""
    from . import logging_config, paths, store
    db_path = str(db_path or store.DEFAULT_DB_PATH)
    log_file = str(log_file or logging_config.log_path())
    data_dir = str(data_dir or paths.data_dir())
    app_root = str(app_root or paths.APP_ROOT)
    sections = {
        "README.txt": _README,
        "system.txt": _section("system", lambda: _system_section(db_path)),
        "settings.txt": _section("settings",
                                 lambda: _settings_section(db_path)),
        "data-dir.txt": _section("data dir",
                                 lambda: _data_dir_section(data_dir)),
        "log-tail.txt": _section("log", lambda: _log_tail_section(log_file)),
        "installer.txt": _section(
            "installer", lambda: _installer_section(app_root, data_dir)),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in sections.items():
            # Every member passes the anonymizer on the way out, so a
            # section that forgot to scrub itself cannot leak a name.
            zf.writestr(name, anonymize(text))
    return buf.getvalue()


def zip_filename(now=None):
    now = now or datetime.now()
    return "focus-core-diagnostics-%s.zip" % now.strftime("%Y%m%d-%H%M%S")
