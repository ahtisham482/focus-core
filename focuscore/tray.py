"""System tray icon for Focus Core (Windows).

Quick actions without opening the window:

    - Open Focus Core  -> the dashboard in its own app window
    - Start 25-min focus session
    - Today's Pulse (a label showing the current score)
    - Back up now
    - Quit -> stops the server this tray started, then exits

The tray OWNS the server subprocess: it starts the server on launch
(when not already running) and terminates it on Quit, so no orphan
``pythonw`` processes are left behind.

``python -m focuscore.tray`` runs it. pystray and Pillow are optional:
``available()`` reports whether the GUI can start, and every action
below is a plain function so tests can exercise them without a GUI.
"""

import sys
from datetime import date
from io import BytesIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    import pystray  # noqa: F401
    from PIL import Image, ImageDraw
    _DEPS_OK = True
    _IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    _DEPS_OK = False
    _IMPORT_ERROR = exc


def available():
    """True when pystray + Pillow imported cleanly."""
    return _DEPS_OK


def import_error():
    return _IMPORT_ERROR


# ------------------------------------------------------------- actions ---

def make_icon_image(size=64):
    """Draw the tray icon in code (no binary asset files).

    A dark rounded square with a green pulse bar -- simple and
    recognizable at 16x16 too.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    draw.rounded_rectangle([pad, pad, size - pad, size - pad],
                           radius=size // 4, fill=(27, 60, 38, 255))
    # A little "pulse" polyline.
    bar = size // 16
    mid = size // 2
    points = [
        (size * 0.22, mid),
        (size * 0.36, mid),
        (size * 0.44, mid - size * 0.22),
        (size * 0.54, mid + size * 0.22),
        (size * 0.62, mid),
        (size * 0.78, mid),
    ]
    draw.line(points, fill=(129, 199, 132, 255), width=bar, joint="curve")
    return img


def open_app(db_path=None):
    """Open the dashboard in app mode. Returns the method used."""
    from .launcher import open_app_window
    return open_app_window()


def start_quick_session(db_path=None):
    """Start a 25-minute focus session from the tray.

    Returns {"session": ...} or {"error": ...}; never raises, so the
    tray menu can show the message in a notification instead.
    """
    from . import focus
    try:
        result = focus.start_session("Quick focus", 25,
                                     block_level="strict", db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        return {"error": "Could not start: %s" % exc}
    return result


def today_pulse_text(db_path=None):
    """'Today's Pulse: 63.5' -- used for the tray menu label."""
    from . import store
    from .scoring import productivity_pulse
    try:
        summary = store.get_day_summary(date.today().isoformat(),
                                        path=db_path)
        pulse = productivity_pulse(summary["seconds_by_level"])
        if summary["total_seconds"] <= 0 or pulse is None:
            return "Today's Pulse: -- (no data yet)"
        return "Today's Pulse: %.1f" % pulse
    except Exception:  # noqa: BLE001 -- tray must never crash on this
        return "Today's Pulse: --"


def backup_now(db_path=None):
    """Create a backup right away. Returns path str or {"error": ...}."""
    from . import backup
    try:
        return str(backup.create_backup(db_path=db_path))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ----------------------------------------------------------------- run ---

class TrayApp:
    """Owns the server subprocess and the pystray icon."""

    def __init__(self, db_path=None):
        self.db_path = db_path
        self.server_proc = None
        self.icon = None

    # -- server lifecycle -------------------------------------------------
    def ensure_server(self):
        from . import launcher
        proc, already = launcher.ensure_server()
        self.server_proc = proc  # None when the server was already running
        return already

    def stop_server(self):
        """Terminate the server ONLY if we started it."""
        proc, self.server_proc = self.server_proc, None
        if proc is None:
            return False
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return True

    # -- menu actions ------------------------------------------------------
    def _notify(self, message, title="Focus Core"):
        if self.icon is not None:
            try:
                self.icon.notify(message, title)
            except Exception:  # noqa: BLE001
                pass

    def on_open(self, icon=None, item=None):
        open_app(self.db_path)

    def on_quick_session(self, icon=None, item=None):
        result = start_quick_session(self.db_path)
        if "error" in result:
            self._notify(result["error"], "Could not start session")
        else:
            self._notify("25-minute session %r started. Stay focused!"
                         % result["label"])

    def on_backup(self, icon=None, item=None):
        result = backup_now(self.db_path)
        if isinstance(result, dict) and "error" in result:
            self._notify("Backup failed: %s" % result["error"])
        else:
            self._notify("Backup saved: %s" % result)

    def on_quit(self, icon=None, item=None):
        self.stop_server()
        if self.icon is not None:
            self.icon.stop()

    def build_menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem("Open Focus Core", self.on_open,
                             default=True),
            pystray.MenuItem("Start 25-min focus session",
                             self.on_quick_session),
            pystray.MenuItem(lambda text: today_pulse_text(self.db_path),
                             None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Back up now", self.on_backup),
            pystray.MenuItem("Quit", self.on_quit),
        )

    def run(self):
        """Start server (if needed), open the window, run the tray loop."""
        import pystray
        from . import launcher

        self.ensure_server()
        launcher.maybe_backup()
        open_app(self.db_path)
        self.icon = pystray.Icon("focus-core", make_icon_image(),
                                 "Focus Core", self.build_menu())
        self.icon.run()


def run(db_path=None):
    """Entry point used by the launcher."""
    if not available():
        raise RuntimeError(
            "Tray needs pystray and Pillow. Run setup.bat again, or "
            "start the dashboard with run-dashboard.bat. "
            "(%s)" % (import_error(),))
    TrayApp(db_path=db_path).run()


def main():
    run()


if __name__ == "__main__":
    main()
