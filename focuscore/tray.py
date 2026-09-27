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

from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    import pystray  # noqa: F401
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
    if "error" not in result:
        # Blocking starts with the session -- no second manual step.
        from .blocker import ensure_guard_running
        ensure_guard_running()
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
        self.db_path = db_path
        self.server_proc = None
        self.icon = None
        # Native window (pywebview) when available; None in browser fallback.
        self.window = None
        # True only while the tray menu's Quit action is running, so the
        # window's closing handler can tell "hide to tray" from "really quit".
        self._quitting = False

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
        from . import desktop, launcher
        window = self.window
        if window is not None:
            # Native window already exists: just bring it back.
            try:
                window.show()
                window.restore()
            except Exception:  # noqa: BLE001
                pass
            return
        if desktop.available():
            # Should not normally happen (run() creates the window), but
            # recover gracefully instead of doing nothing.
            self.window = self._create_window(desktop, launcher)
            if self.window is not None:
                self.window.events.closing += self._on_window_closing
                return
        open_app(self.db_path)

    def on_check_updates(self, icon=None, item=None):
        import webbrowser
        from . import launcher
        webbrowser.open(launcher.APP_URL + "/update")

    def _apply_pending_update(self, pending):
        """Install a downloaded update, then quit so files can be replaced.

        Called by the update watcher when the dashboard flags a pending
        install. Spawns a small bat (waits a few seconds, runs the setup
        silently, deletes itself) and then quits the app.
        """
        import subprocess
        from . import launcher, updater
        try:
            bat = updater.write_update_launcher(pending["installer"])
            subprocess.Popen(["cmd", "/c", str(bat)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             **launcher._no_window_kwargs())
        except Exception:  # noqa: BLE001 -- never break the tray on update
            return
        self.on_quit()

    def _watch_for_update(self):
        """Background: apply a pending one-click update when flagged."""
        import time
        from . import updater
        while not self._quitting:
            time.sleep(2)
            try:
                pending = updater.take_pending_install()
            except Exception:  # noqa: BLE001
                continue
            if pending:
                self._apply_pending_update(pending)
                return

    def _nightly_wal_checkpoint(self):
        """Sprint 4 (Qwen item 10): TRUNCATE-checkpoint the WAL once a
        day on a dedicated connection. The supervisor (tray) owns this
        because it's the longest-lived process."""
        import time
        from . import store
        while not self._quitting:
            # Sleep in small increments so quit is responsive.
            for _ in range(24 * 60):  # ~24 h in 1-minute slices
                if self._quitting:
                    return
                time.sleep(60)
            if self._quitting:
                return
            try:
                store.checkpoint_wal(self.db_path)
            except Exception:  # noqa: BLE001 -- best-effort
                pass

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

    def _on_window_closing(self):
        """pywebview closing event: hide to the tray on user close.

        Returns True to allow the close (real quit), False to cancel it.
        """
        if self._quitting:
            return True
        from . import desktop
        desktop.hide_window(self.window)
        return False

    def on_quit(self, icon=None, item=None):
        self._quitting = True
        window, self.window = self.window, None
        if window is not None:
            # Native mode: destroying the window ends start_loop(); run()
            # then stops the server and the tray icon.
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass
        else:
            # Browser-fallback mode: clean up directly, as before.
            self.stop_server()
            if self.icon is not None:
                try:
                    self.icon.stop()
                except Exception:  # noqa: BLE001
                    pass

    def on_shield_toggle(self, icon=None, item=None):
        from . import shield as shield_mod
        if shield_mod.shield_daemon_running() \
                and not shield_mod.shield_killswitch_on():
            shield_mod.shield_off()
            self._notify("Shield turned off.")
        else:
            shield_mod.shield_on()
            shield_mod.ensure_shield_running()
            self._notify("Shield turned on.")

    def on_emergency_pass(self, icon=None, item=None):
        import webbrowser
        from . import launcher
        webbrowser.open(launcher.APP_URL + "/shield")

    def on_hud_toggle(self, icon=None, item=None):
        from . import store
        current = store.get_setting("hud_enabled", "1") == "1"
        store.set_setting("hud_enabled", "0" if current else "1")
        self._notify("HUD %s." % ("hidden" if current else "shown"))

    def build_menu(self):
        import pystray
        from . import shield as shield_mod
        from . import store

        def shield_label(text):
            if shield_mod.shield_daemon_running() \
                    and not shield_mod.shield_killswitch_on():
                return "Turn shield off"
            return "Turn shield on"

        def hud_label(text):
            on = store.get_setting("hud_enabled", "1") == "1"
            return "Hide HUD" if on else "Show HUD"

        return pystray.Menu(
            pystray.MenuItem("Open Focus Core", self.on_open,
                             default=True),
            pystray.MenuItem("Start 25-min focus session",
                             self.on_quick_session),
            pystray.MenuItem(lambda text: today_pulse_text(self.db_path),
                             None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(shield_label, self.on_shield_toggle),
            pystray.MenuItem("Emergency pass...", self.on_emergency_pass),
            pystray.MenuItem(hud_label, self.on_hud_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Back up now", self.on_backup),
            pystray.MenuItem("Check for updates...", self.on_check_updates),
            pystray.MenuItem("Quit", self.on_quit),
        )

    def _create_window(self, desktop, launcher):
        """Create the native window, or None when it is unavailable.

        Never raises: pywebview missing, or unusable (e.g. WebView2 not
        installed), simply means the browser fallback.
        """
        if not desktop.available():
            return None
        try:
            return desktop.create_window(launcher.APP_URL)
        except Exception:  # noqa: BLE001 -- fall back to the browser window
            return None

    def run(self):
        """Start server (if needed), open the window, run the tray loop."""
        import pystray
        import threading
        from . import launcher, desktop, updater

        # Safety net: a pending one-click update that never got applied
        # (e.g. the app was quit by hand right after clicking Update).
        pending = updater.take_pending_install()
        if pending:
            self._apply_pending_update(pending)
            return

        self.ensure_server()
        launcher.maybe_backup()
        # Phase 7: one shield daemon covers sessions and always-on rules.
        try:
            import os as _os
            if _os.name == "nt":
                from . import shield as _shield
                _shield.ensure_shield_running()
        except Exception:  # noqa: BLE001 -- shield is best-effort
            pass
        threading.Thread(target=self._watch_for_update, daemon=True,
                         name="focuscore-update-watch").start()
        # Sprint 4 (Qwen item 10): the tray is the long-running
        # supervisor -- it TRUNCATE-checkpoints the WAL nightly on a
        # dedicated connection so the -wal file stays bounded.
        threading.Thread(target=self._nightly_wal_checkpoint,
                         daemon=True,
                         name="focuscore-wal-checkpoint").start()
        self.icon = pystray.Icon("focus-core", make_icon_image(),
                                 "Focus Core", self.build_menu())
        self.window = self._create_window(desktop, launcher)
        if self.window is None:
            # Browser fallback: unchanged behavior.
            open_app(self.db_path)
            self.icon.run()
            return
        # Native window: tray runs detached, GUI loop owns the main thread.
        self.window.events.closing += self._on_window_closing
        self.icon.run_detached()
        try:
            desktop.start_loop()  # blocks until the window is destroyed
        finally:
            self.window = None
            self.stop_server()
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass


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
