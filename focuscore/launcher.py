"""App-mode launcher for Focus Core (Windows).

"Start Focus Core.bat" runs ``pythonw -m focuscore.launcher`` -- no
console window. This module then:

    1. Makes sure the dashboard server is running on 127.0.0.1:5000
       (starts it quietly if needed, waits until it answers).
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

import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 5000
WAIT_TIMEOUT = 20  # seconds to wait for the server to answer
APP_URL = "http://%s:%d/" % (HOST, PORT)


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


def start_server():
    """Start the dashboard server quietly in the background.

    Returns the Popen handle so the owner (tray) can stop it later.
    """
    return subprocess.Popen(
        [sys.executable, "-m", "dashboard.app"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_no_window_kwargs())


def ensure_server():
    """Make sure the server is up. Returns (process_or_None, already_running).

    process is the Popen handle when WE started the server, else None.
    """
    if port_open():
        return None, True
    proc = start_server()
    if not wait_for_port():
        try:
            proc.terminate()
        except OSError:
            pass
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


def open_app_window(url=APP_URL):
    """Open the dashboard in app mode (own window, no address bar).

    Tries Chrome, then Edge, then the default browser. Returns the
    method used: "chrome", "edge", or "browser".
    """
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


def main():
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
