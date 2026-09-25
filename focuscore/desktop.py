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

import threading

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
