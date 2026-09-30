"""Native desktop window for Focus Core.

Opens the dashboard in a real native window (pywebview, backed by
WebView2 on Windows) instead of a browser --app window, so Focus Core
looks and behaves like installed software: its own window with a real
title, taskbar entry, minimize/restore, and no address bar.

pywebview is OPTIONAL. When it cannot be imported (or the window cannot
be created, e.g. WebView2 missing), callers fall back to the browser
window in launcher.open_app_window -- the app always still works.

Threading model (pywebview rules):
- webview.start() MUST run on the main thread and blocks until the last
  window is closed/destroyed.
- pystray runs detached in its own thread; window.hide()/show()/destroy()
  are safe to call from that thread.
- The Flask server stays a subprocess owned by the tray, exactly as before.
"""

import logging
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

APP_TITLE = "Focus Core"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
WINDOW_MIN_SIZE = (940, 620)


def available():
    """True when the pywebview package can be imported."""
    try:
        import webview  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 -- any import problem means "no native window"
        return False


def build_window_config(url):
    """Pure description of the main window; testable without a display."""
    return {
        "title": APP_TITLE,
        "url": url,
        "width": WINDOW_WIDTH,
        "height": WINDOW_HEIGHT,
        "min_size": WINDOW_MIN_SIZE,
    }


def create_window(url):
    """Create (but do not yet show) the native window.

    The window appears when start_loop() runs the GUI event loop.
    """
    import webview
    cfg = build_window_config(url)
    return webview.create_window(
        cfg["title"], cfg["url"],
        width=cfg["width"], height=cfg["height"],
        min_size=cfg["min_size"],
    )


def start_loop():
    """Run the GUI event loop on the main thread.

    Blocks until the last window is closed/destroyed.
    """
    import webview
    webview.start()


def hide_window(window):
    """Hide the window, hopping to a background thread first.

    Hiding synchronously inside the closing event can hang the app, so
    this never hides on the calling thread. Returns the thread.
    """
    thread = threading.Thread(target=window.hide, daemon=True)
    thread.start()
    return thread


def make_closing_handler(window, is_quitting, hide=None):
    """Build a handler for ``window.events.closing``.

    - User closes the window -> hide to the tray, cancel the close.
    - Real quit (tray menu -> Quit) -> let the close proceed.

    Returns True to allow closing, False to cancel it.
    """
    hide = hide or hide_window

    def _on_closing():
        if is_quitting():
            return True
        hide(window)
        return False

    return _on_closing


def find_app_icon(search_dirs):
    """Locate the .ico file for the native window; None when absent.

    Installed copies ship icon.ico next to the app folder. Dev
    checkouts only have dashboard/static/icon.png, which Pillow
    converts on the fly into a temp .ico (best effort).
    """
    for d in search_dirs:
        ico = Path(d) / "icon.ico"
        if ico.is_file():
            return str(ico)
    try:
        from PIL import Image
        for d in search_dirs:
            png = Path(d) / "dashboard" / "static" / "icon.png"
            if png.is_file():
                tmp = Path(tempfile.gettempdir()) / "focuscore-icon.ico"
                with Image.open(png) as im:
                    im.save(tmp, sizes=[(16, 16), (32, 32), (48, 48)])
                return str(tmp)
    except Exception as exc:  # noqa: BLE001 -- icon is cosmetic, never fatal
        logger.warning("could not build the app icon: %s", exc)
    return None


def set_window_icon(window_title, icon_path):
    """Apply the Focus Core icon to the native window (Windows only).

    pywebview does not expose window-icon setting on its Windows
    backend, so this goes straight to the OS: find our top-level
    window by title, load the .ico, send WM_SETICON (small + big).
    Without it the title bar, taskbar and Alt+Tab show the Python
    interpreter's icon -- the "feels like a Python file" problem.
    Returns True when the icon was applied.
    """
    import os
    if os.name != "nt" or not icon_path:
        return False
    if not os.path.isfile(icon_path):
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, window_title)
        if not hwnd:
            return False
        LR_LOADFROMFILE = 0x10
        IMAGE_ICON = 1
        WM_SETICON = 0x80
        ICON_SMALL, ICON_BIG = 0, 1
        applied = False
        for size, which in ((16, ICON_SMALL), (32, ICON_BIG)):
            hicon = user32.LoadImageW(None, icon_path, IMAGE_ICON,
                                     size, size, LR_LOADFROMFILE)
            if hicon:
                user32.SendMessageW(hwnd, WM_SETICON, which, hicon)
                applied = True
        return applied
    except Exception:  # noqa: BLE001 -- cosmetic, never fatal
        return False
