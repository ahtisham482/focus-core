"""Tests for the native desktop window (focuscore/desktop.py) and its
tray wiring. All headless-safe: pywebview is never really imported --
tests use a fake module instead, and closing/hide logic is exercised
through pure functions and fakes.
"""

import sys
import types

from focuscore import desktop


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def __isub__(self, fn):
        self.handlers.remove(fn)
        return self


class FakeWindow:
    def __init__(self):
        self.events = types.SimpleNamespace(closing=FakeEvent())
        self.hidden = False
        self.destroyed = False
        self.shown = False

    def hide(self):
        self.hidden = True

    def show(self):
        self.shown = True

    def restore(self):
        pass

    def destroy(self):
        self.destroyed = True


def _install_fake_webview(monkeypatch):
    """Pretend the pywebview package is installed."""
    created = {}
    fake = types.ModuleType("webview")

    def create_window(title, url, **kwargs):
        created["title"] = title
        created["url"] = url
        created["kwargs"] = kwargs
        window = FakeWindow()
        created["window"] = window
        return window

    started = []

    def start(*args, **kwargs):
        started.append((args, kwargs))

    fake.create_window = create_window
    fake.start = start
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake, created, started


def test_window_config_is_focus_core():
    cfg = desktop.build_window_config("http://127.0.0.1:5000/")
    assert cfg["title"] == "Focus Core"
    assert cfg["url"] == "http://127.0.0.1:5000/"
    assert cfg["width"] >= 940 and cfg["height"] >= 620
    assert cfg["min_size"][0] >= 200


def test_available_false_without_package(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview" or name.startswith("webview."):
            raise ImportError("no module named 'webview'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "webview", raising=False)
    assert desktop.available() is False


def test_available_true_with_package(monkeypatch):
    _install_fake_webview(monkeypatch)
    assert desktop.available() is True


def test_create_window_uses_config(monkeypatch):
    _, created, _ = _install_fake_webview(monkeypatch)
    window = desktop.create_window("http://127.0.0.1:5000/")
    assert isinstance(window, FakeWindow)
    assert created["title"] == "Focus Core"
    assert created["url"] == "http://127.0.0.1:5000/"
    assert created["kwargs"]["width"] == desktop.WINDOW_WIDTH


def test_closing_handler_hides_to_tray():
    window = FakeWindow()
    hidden = []
    handler = desktop.make_closing_handler(
        window, is_quitting=lambda: False, hide=lambda w: hidden.append(w))
    assert handler() is False  # close cancelled...
    assert hidden == [window]  # ...and the window was hidden instead


def test_closing_handler_allows_real_quit():
    window = FakeWindow()
    hidden = []
    handler = desktop.make_closing_handler(
        window, is_quitting=lambda: True, hide=lambda w: hidden.append(w))
    assert handler() is True  # close proceeds
    assert hidden == []


def test_hide_window_runs_off_calling_thread():
    window = FakeWindow()
    thread = desktop.hide_window(window)
    thread.join(timeout=5)
    assert window.hidden is True


# -- tray wiring ------------------------------------------------------------

def test_tray_quit_destroys_native_window():
    from focuscore import tray
    app = tray.TrayApp()
    window = FakeWindow()
    app.window = window
    app.on_quit()
    assert app._quitting is True
    assert window.destroyed is True
    assert app.window is None


def test_tray_window_closing_hides_unless_quitting(monkeypatch):
    from focuscore import tray
    app = tray.TrayApp()
    window = FakeWindow()
    app.window = window
    hidden = []
    monkeypatch.setattr("focuscore.desktop.hide_window",
                        lambda w: hidden.append(w))
    assert app._on_window_closing() is False
    assert hidden == [window]
    app._quitting = True
    assert app._on_window_closing() is True


def test_tray_create_window_none_when_unavailable(monkeypatch):
    from focuscore import tray
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview" or name.startswith("webview."):
            raise ImportError("no module named 'webview'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "webview", raising=False)

    from focuscore import launcher
    app = tray.TrayApp()
    assert app._create_window(desktop, launcher) is None


def test_tray_create_window_when_available(monkeypatch):
    from focuscore import tray
    from focuscore import launcher
    _install_fake_webview(monkeypatch)
    app = tray.TrayApp()
    window = app._create_window(desktop, launcher)
    assert isinstance(window, FakeWindow)
