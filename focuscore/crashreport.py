"""Roadmap 2.5: opt-in per-incident crash reports.

Zero telemetry stays the law: Focus Core never sends anything itself.
What this module adds after an unclean shutdown is an OFFER on the home
page -- a tiny report the user can read in full, then copy or open in
their own mail client and send (or not) themselves. Three rules keep
it honest:

* The report holds exactly three facts: the app version, the OS
  description, and the exception TYPE name. Exception messages and
  tracebacks are never stored anywhere -- they can carry file paths
  and user data.
* Nothing leaves the machine from this code: it imports no
  network-sending library. The mailto link only opens the user's own
  mail client; the user presses send.
* Every function fails silent (log only). A crash-report bug must
  never break app startup, shutdown, or the home page.

Session bookkeeping: begin_session() runs at each app start, but
exactly ONE process per launch owns the session marker -- the tray, or
the dashboard when it runs standalone. The tray spawns the dashboard
as a child process carrying FOCUSCORE_DASHBOARD_CHILD=1; a child skips
the marker/pending logic entirely (it would otherwise read the owner's
fresh marker as a leftover and invent an offer on every clean launch --
critic B1) and only installs the exception hooks, so its own crashes
still record their type for the owner's next start. At start, a
leftover session-mark.json means the previous run did not close
properly (crash, power cut, Windows closed it -- the offer says
exactly that, never "crashed" as a fact). It is turned into a
one-time pending offer (crash-pending.json, plus the same text saved
as crash-report-<timestamp>.txt for manual attach; only the newest
five report files are kept), the recorded exception type is consumed,
a fresh marker is written for this session, and a clean exit clears
it via atexit. Marker I/O happens at start/exit only -- never on
enforcement paths (Invariant I-1).
"""

import atexit
import json
import logging
import os
import platform
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from . import __version__, paths

logger = logging.getLogger(__name__)

# The maintainer's inbox. One line to swap; the offer's email button
# opens the user's own mail client addressed here.
MAINTAINER_EMAIL = "muhammadahtisham482@gmail.com"

MARKER_NAME = "session-mark.json"
CRASH_NAME = "last-crash.json"
PENDING_NAME = "crash-pending.json"
# Set by the tray/launcher on every dashboard child it spawns: the
# child is not a session owner (see the module docstring / critic B1).
CHILD_ENV = "FOCUSCORE_DASHBOARD_CHILD"
NO_ERROR_LINE = (
    "no error was recorded (the app was closed by Windows or lost power)")

_hooks_installed = False


def _folder():
    return Path(paths.data_dir())


def _now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path, payload):
    try:
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    except OSError as exc:
        logger.debug("crash report: could not write %s: %s", path.name, exc)


def _unlink(path):
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("crash report: could not remove %s: %s", path.name, exc)


# ------------------------------------------------------------ marker --

def mark_session_start():
    """Write the running-session marker (pid, start time, version)."""
    try:
        _write_json(_folder() / MARKER_NAME, {
            "pid": os.getpid(),
            "started_at": _now_iso(),
            "version": __version__,
        })
    except Exception as exc:  # noqa: BLE001 -- never raise into startup
        logger.debug("crash report: session marker not written: %s", exc)


def clear_session_marker():
    """Remove the marker: this session ended the proper way."""
    try:
        _unlink(_folder() / MARKER_NAME)
    except Exception as exc:  # noqa: BLE001 -- never raise into shutdown
        logger.debug("crash report: session marker not cleared: %s", exc)


def previous_run_unclean():
    """True when a previous session's marker was left behind."""
    try:
        return (_folder() / MARKER_NAME).is_file()
    except Exception:  # noqa: BLE001 -- a broken check reads as "clean"
        return False


# --------------------------------------------------- exception capture --

def _record_crash(exc_type):
    """Store ONLY the exception type name for a later, optional report."""
    try:
        name = getattr(exc_type, "__name__", None) or "UnknownError"
        _write_json(_folder() / CRASH_NAME, {
            "type": str(name),
            "version": __version__,
            "os": platform.platform(),
            "at": _now_iso(),
        })
    except Exception as exc:  # noqa: BLE001 -- recording must never crash
        logger.debug("crash report: crash not recorded: %s", exc)


def install_crash_hooks():
    """Record exception TYPE names from uncaught exceptions, then chain.

    The previous hooks always run afterwards, so normal interpreter
    behaviour (stderr traceback, logging) is unchanged -- we only add
    the type-name record beside it.
    """
    global _hooks_installed
    if _hooks_installed:
        return
    try:
        previous = sys.excepthook

        def _main_hook(exc_type, exc_value, exc_tb):
            _record_crash(exc_type)
            try:
                previous(exc_type, exc_value, exc_tb)
            except Exception:  # noqa: BLE001 -- never lose the chain
                logger.debug("crash report: chained excepthook failed")

        sys.excepthook = _main_hook

        previous_thread = threading.excepthook

        def _thread_hook(args):
            _record_crash(getattr(args, "exc_type", None))
            try:
                previous_thread(args)
            except Exception:  # noqa: BLE001 -- never lose the chain
                logger.debug("crash report: chained thread hook failed")

        threading.excepthook = _thread_hook
        _hooks_installed = True
    except Exception as exc:  # noqa: BLE001 -- hooks are best-effort only
        logger.debug("crash report: hooks not installed: %s", exc)


# -------------------------------------------------------------- report --

def build_report(crash=None):
    """The exact report text: version, OS, exception type -- nothing else."""
    recorded = ""
    try:
        if crash is None:
            crash = _read_json(_folder() / CRASH_NAME)
        if crash:
            recorded = str(crash.get("type") or "")
    except Exception:  # noqa: BLE001 -- the honest line is the fallback
        recorded = ""
    what = recorded if recorded else NO_ERROR_LINE
    return (
        "Focus Core crash report\n"
        f"Focus Core version: {__version__}\n"
        f"Operating system: {platform.platform()}\n"
        f"What went wrong: {what}\n")


def _prune_report_files(folder, keep=5):
    """Retire old saved reports: keep only the newest few (critic N1).

    File names carry the timestamp, so name order is age order. A
    per-incident .txt is a few hundred bytes, but untouched they would
    still pile up for years.
    """
    try:
        saved = sorted(folder.glob("crash-report-*.txt"))
        for old in saved[:-keep]:
            _unlink(old)
    except Exception as exc:  # noqa: BLE001 -- housekeeping is optional
        logger.debug("crash report: old reports not pruned: %s", exc)


def _create_pending(crash):
    text = build_report(crash)
    folder = _folder()
    _write_json(folder / PENDING_NAME,
                {"created_at": _now_iso(), "report": text})
    # The same text as a plain file, for users who attach it manually.
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    try:
        (folder / f"crash-report-{stamp}.txt").write_text(
            text, encoding="utf-8")
    except OSError as exc:
        logger.debug("crash report: report file not saved: %s", exc)
    _prune_report_files(folder)
    return text


def pending_report():
    """The offered report text, or None when no incident is waiting."""
    try:
        data = _read_json(_folder() / PENDING_NAME)
    except Exception:  # noqa: BLE001 -- no offer beats a broken home page
        return None
    if data and data.get("report"):
        return str(data["report"])
    return None


def mark_handled():
    """The user copied, emailed, or declined: never offer it again."""
    try:
        _unlink(_folder() / PENDING_NAME)
    except Exception as exc:  # noqa: BLE001 -- failing to clear is cosmetic
        logger.debug("crash report: pending offer not cleared: %s", exc)


def mailto_url(report_text):
    """mailto: link opening the user's OWN mail client, prefilled."""
    subject = quote("Focus Core crash report")
    body = quote(report_text)
    return f"mailto:{MAINTAINER_EMAIL}?subject={subject}&body={body}"


# --------------------------------------------------------------- start --

def begin_session():
    """Start-of-app bookkeeping: detect, offer, mark, hook. Never raises.

    Exactly one process per launch owns the session: the tray, or the
    dashboard when it runs standalone. Called by both app-start entries
    (tray and dashboard), but a dashboard child spawned by the tray
    carries FOCUSCORE_DASHBOARD_CHILD and skips the marker/pending
    logic -- it only installs the exception hooks, so a child crash
    still records its type for the owner's next start (critic B1).
    The owner reads a leftover marker from the previous run, turns it
    into a one-time offer, consumes the recorded exception type,
    writes this session's marker (cleared on clean exit via atexit),
    and installs the exception-type hooks.
    """
    try:
        if os.environ.get(CHILD_ENV):
            # Dashboard child: hooks only, never session bookkeeping.
            install_crash_hooks()
            return
        try:
            crash = _read_json(_folder() / CRASH_NAME)
        except Exception:  # noqa: BLE001 -- unreadable record = no record
            crash = None
        if previous_run_unclean():
            _create_pending(crash)
        _unlink(_folder() / CRASH_NAME)  # consumed: one incident, one offer
        mark_session_start()
        try:
            atexit.register(clear_session_marker)
        except Exception as exc:  # noqa: BLE001 -- marker hygiene optional
            logger.debug("crash report: atexit not registered: %s", exc)
        install_crash_hooks()
    except Exception as exc:  # noqa: BLE001 -- startup never depends on us
        logger.debug("crash report: session start skipped: %s", exc)
