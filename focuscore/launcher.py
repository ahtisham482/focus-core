"""App-mode launcher for Focus Core (Windows).

"Start Focus Core.bat" runs ``pythonw -m focuscore.launcher`` -- no
console window. This module then:

    0. Takes the single-instance mutex (Roadmap 1.11). A second
       instance focuses the first one's window and exits, starting
       nothing -- so two updaters can never race either.
    1. Makes sure the dashboard server is running on 127.0.0.1
       (the configured port -- config key ``port``, default 5000 -- if
       it is free, otherwise the next free port in a ten-port window
       starting there; a port already held by a real Focus Core is
       attached to, never duplicated).
    2. Runs a quiet backup if the newest backup is stale
       (never crashes the app if the backup fails).
    3. Opens the dashboard in "app mode" -- its own window, no address
       bar -- which feels like a desktop app.

If the optional tray dependencies (pystray + Pillow) are installed, the
system tray app (tray.py) is used instead: it owns the server process
and adds quick actions. When pystray is missing, the launcher falls back
to starting the server directly and opening the window.

Every helper here is a small pure/testable function; the .bat file
itself is just two lines.
"""

import json
import logging
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import config
from . import single_instance

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 5000
WAIT_TIMEOUT = 20  # seconds to wait for the server to answer
APP_URL = f"http://{HOST}:{PORT}/"
HEALTHZ_PATH = "/healthz"
PROBE_TIMEOUT = 0.5  # seconds; identity probes must stay snappy
PORT_SCAN_COUNT = 10  # candidate ports: base .. base + 9
# Windows AppUserModelID: the OS-level identity of the app, in the
# conventional Publisher.Product form. Without an explicit ID, Windows
# groups our window under the Python interpreter (pythonw.exe): the
# taskbar, Alt+Tab and title bar show Python's icon and name, and the
# app feels like "a Python file" instead of installed software.
APP_ID = "FocusCore.FocusCore"


def set_windows_app_identity(app_id=APP_ID):
    """Tell Windows this process is Focus Core, not pythonw.

    Sets the process's explicit AppUserModelID so the taskbar groups
    the app under its own identity with its own name. Cosmetic only:
    never raises, returns True when the identity was applied (Windows
    only; a no-op everywhere else).
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            app_id)
        return True
    except Exception:  # noqa: BLE001 -- cosmetic, never fatal
        return False


# ----------------------------------------------------------- active URL ---

_active_port = None


def active_port():
    """The port the dashboard is (or will be) served on in this process."""
    return _active_port if _active_port is not None else PORT


def app_url():
    """Base URL of the running dashboard, following the chosen port.

    Before any port selection has happened this is the plain port-5000
    URL, exactly what the old APP_URL constant always produced.
    """
    return f"http://{HOST}:{active_port()}/"


def _set_active_port(port):
    global _active_port
    _active_port = int(port)


# ------------------------------------------------------- identity probe ---

def is_focus_core(port, host=HOST, timeout=None):
    """True only when port answers GET /healthz as Focus Core.

    The probe is how a busy port is told apart from a foreign app:
    HTTP 200 AND a JSON body whose ``app`` is "focus-core". Any error,
    timeout, wrong status, or wrong body means "not Focus Core".
    ``timeout=None`` resolves to PROBE_TIMEOUT at call time.
    """
    if timeout is None:
        timeout = PROBE_TIMEOUT
    url = f"http://{host}:{port}{HEALTHZ_PATH}"
    try:
        request = urllib.request.Request(
            url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False
            body = response.read(4096)
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 -- any failure means "not Focus Core"
        return False
    return isinstance(data, dict) and data.get("app") == "focus-core"


def _port_bindable(port, host=HOST):
    """True when this process could bind host:port right now."""
    sock = socket.socket()
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _base_port():
    """First candidate port: the configured port, else PORT (5000).

    Roadmap 1.12: unified config's ``port`` key moves the whole scan
    window, so a user who cannot have 5000 scans 5100..5109 instead.
    """
    configured = config.get_port()
    return configured if configured is not None else PORT


def _candidate_ports():
    base = _base_port()
    return list(range(base, base + PORT_SCAN_COUNT))


def _select_port():
    """Pick the dashboard port. Returns (port, already_running).

    A port that already serves Focus Core wins first (attach; never
    start a duplicate). Otherwise the first bindable-free port is
    where a new server will start. Ten busy foreign ports raise a
    plain-English RuntimeError.
    """
    candidates = _candidate_ports()
    for port in candidates:
        if is_focus_core(port):
            _set_active_port(port)
            return port, True
    for port in candidates:
        if _port_bindable(port):
            _set_active_port(port)
            return port, False
    raise RuntimeError(
        f"Focus Core could not start because ports {candidates[0]} to "
        f"{candidates[-1]} are all being used by other programs. Close "
        "one of those programs, then open Focus Core again.")


def focus_existing_window(title=None, _user32=None):
    """Best-effort: bring the first instance's window to the front.

    Windows only; never raises. Returns True when a window was found
    and foregrounded. ``_user32`` injects the API for tests.
    """
    if title is None:
        from .desktop import APP_TITLE
        title = APP_TITLE
    if _user32 is None:
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            _user32 = ctypes.windll.user32
        except Exception:  # noqa: BLE001 -- cosmetic, never fatal
            return False
    try:
        hwnd = _user32.FindWindowW(None, title)
        if not hwnd:
            return False
        sw_restore = 9
        try:
            _user32.ShowWindow(hwnd, sw_restore)
        except Exception:  # noqa: BLE001 -- restore is cosmetic
            logger.debug("restoring the existing window failed")
        return bool(_user32.SetForegroundWindow(hwnd))
    except Exception:  # noqa: BLE001 -- best effort, never fatal
        logger.debug("focusing the existing window failed")
        return False


def port_open(host=HOST, port=PORT, timeout=1.0):
    """True when something is listening on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(host=HOST, port=PORT, timeout=WAIT_TIMEOUT,
                  poll=0.5):
    """Wait until host:port answers; True on success, False on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(host, port):
            return True
        time.sleep(poll)
    return port_open(host, port)


def _no_window_kwargs():
    # On Windows, hide the child console window entirely.
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def start_server(port=PORT):
    """Start the dashboard server quietly in the background.

    Returns the Popen handle so the owner (tray) can stop it later.
    """
    return subprocess.Popen(
        [sys.executable, "-m", "dashboard.app", "--port", str(port)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_no_window_kwargs())


def ensure_server():
    """Make sure the server is up. Returns (process_or_None, already_running).

    process is the Popen handle when WE started the server, else None.
    The chosen port is remembered (see app_url()) so the tray and the
    app window open the server that is actually running.
    """
    port, already_running = _select_port()
    if already_running:
        return None, True
    proc = start_server(port)
    if not wait_for_port(port=port):
        try:
            proc.terminate()
        except OSError as exc:
            logger.warning("could not stop the server process that "
                           "failed to start: %s", exc)
        raise RuntimeError(
            "The Focus Core server did not start. Please double-click "
            "setup.bat again, and if it still fails send a screenshot "
            "to Merlin.")
    # Roadmap 1.11 repair (MINOR-1): close the probe->bind race.
    # wait_for_port only proved *something* listens on the chosen
    # port; a foreign app could have grabbed the port in the gap
    # between _select_port's bindability check and the child
    # binding. Re-probe the Focus Core identity (/healthz). If the
    # listener is not Focus Core, it is a hard start failure -- not
    # a signal to scan more ports, because the spawn already
    # happened on the chosen port and falling through would leave
    # the foreign listener masquerading as our server.
    if not is_focus_core(port):
        logger.warning(
            "port %s conflict: another program took the port while "
            "Focus Core was starting (post-start identity probe "
            "failed); not treating the foreign listener as Focus Core",
            port)
        try:
            proc.terminate()
        except OSError as exc:
            logger.warning("could not stop the server process that "
                           "failed to start: %s", exc)
        raise RuntimeError(
            "The Focus Core server did not start. Please double-click "
            "setup.bat again, and if it still fails send a screenshot "
            "to Merlin.")
    return proc, False


def maybe_backup():
    """Quiet auto-backup on startup. Never raises."""
    try:
        from . import backup
        path = backup.backup_if_stale()
        if path:
            print("Focus Core: backup written to %s" % path)
    except Exception as exc:  # noqa: BLE001 -- backup must never break launch
        print("Focus Core: backup skipped (%s)" % exc)


def open_app_window(url=None):
    """Open the dashboard in app mode (own window, no address bar).

    Tries Chrome, then Edge, then the default browser. Returns the
    method used: "chrome", "edge", or "browser". With no explicit URL
    the active URL (chosen port) is used.
    """
    if url is None:
        url = app_url()
    candidates = [
        ("chrome", [
            "chrome.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]),
        ("edge", [
            "msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]),
    ]
    for name, paths in candidates:
        for exe in paths:
            try:
                subprocess.Popen(
                    [exe, "--app=%s" % url], **_no_window_kwargs())
                # Give the browser a moment; if the exe didn't exist,
                # Popen raises immediately and we try the next one.
                time.sleep(0.4)
                return name
            except OSError:
                continue
    webbrowser.open(url)
    return "browser"


def _background_update_check():
    """Quietly check for a newer release once per day. Never raises.

    Honors the user's automatic-checks toggle (Updates page -> setting
    ``update_check_enabled``). When off, no network traffic happens at all;
    the manual "Check again" on the Updates page still works because it is
    the user's own action. (The machine ``updates_disabled`` policy,
    roadmap 1.21, beats both: ``check_for_update()`` refuses.)
    """
    try:
        from . import store
        if store.get_setting("update_check_enabled", "1") != "1":
            return
        from . import updater
        updater.check_for_update()
    except Exception:  # noqa: BLE001 -- updates must never break launch
        logger.exception("background update check failed")


def main():
    # Roadmap 1.11: the single-instance mutex comes FIRST, before the
    # update-check thread, the tray, the server, the backup, and the
    # shield -- a second instance starts NOTHING. Holding the only
    # instance by construction also means the tray's updater can never
    # race a second updater.
    if single_instance.acquire() is None:
        logger.info("another Focus Core instance is already running; "
                    "focusing it and exiting")
        focus_existing_window()
        return
    # OS identity first: before any window exists, tell Windows this
    # process is Focus Core (not the Python interpreter), so the
    # taskbar/Alt+Tab show the app's own name and icon.
    set_windows_app_identity()
    # Roadmap 1.3: tag this process "launcher" in the shared log; the
    # tray re-tags it "tray" when it takes over in-process.
    from . import logging_config
    logging_config.setup_logging(process_name="launcher")
    # Make sure the data folder exists before anything writes to it
    # (matters for installed copies, where it lives outside the app).
    from . import paths
    paths.ensure_data_dir()
    # Check for updates in the background; the home page shows a card
    # when a newer release is waiting.
    import threading
    threading.Thread(target=_background_update_check, daemon=True,
                     name="focuscore-update-check").start()
    # Prefer the tray app when its dependencies are installed: it owns
    # the server process and adds quick actions.
    try:
        from . import tray as tray_mod
        if tray_mod.available():
            tray_mod.run()
            return
    except Exception as exc:  # noqa: BLE001 -- fall back, don't crash
        print("Focus Core: tray unavailable (%s); using simple mode." % exc)

    try:
        ensure_server()
    except RuntimeError as exc:
        _tell_user(str(exc))
        return
    maybe_backup()
    # Phase 7: one shield daemon covers sessions and always-on rules.
    try:
        import os as _os
        if _os.name == "nt":
            from . import shield as _shield
            _shield.ensure_shield_running()
    except Exception:  # noqa: BLE001 -- shield is best-effort
        # Best-effort, but the user believes they are protected, so
        # the failure must leave a trace.
        logger.exception("could not start the shield daemon")
    open_app_window()


def _tell_user(message):
    """Last-resort plain-English message box on Windows."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "Focus Core", 0x40)
    except Exception:
        print(message)


if __name__ == "__main__":
    main()
