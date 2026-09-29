"""Tests for the Windows app identity (the "feels like a Python file"
fix): Focus Core must present itself to the OS as Focus Core -- its own
AppUserModelID and its own window icon -- instead of falling back to
the Python interpreter's identity. All headless-safe: the win32 API is
never touched; guards are exercised by faking the platform.
"""

import sys
import types
from pathlib import Path

import pytest

from focuscore import desktop, launcher


def test_app_id_uses_publisher_product_form():
    assert launcher.APP_ID == "FocusCore.FocusCore"


def test_identity_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert launcher.set_windows_app_identity() is False


def test_identity_applies_on_windows(monkeypatch):
    """On win32 the AppUserModelID call happens exactly once."""
    calls = []
    fake_shell32 = types.SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=calls.append
    )
    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(shell32=fake_shell32)
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    assert launcher.set_windows_app_identity() is True
    assert calls == ["FocusCore.FocusCore"]


def test_identity_never_raises_when_api_missing(monkeypatch):
    fake_ctypes = types.SimpleNamespace()  # no windll at all
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    assert launcher.set_windows_app_identity() is False


def test_set_window_icon_noop_off_windows():
    assert desktop.set_window_icon("Focus Core", "C:\\x\\icon.ico") is False


def test_set_window_icon_rejects_missing_file(tmp_path):
    assert desktop.set_window_icon("Focus Core", str(tmp_path / "nope.ico")) is False
    assert desktop.set_window_icon("Focus Core", None) is False


def test_find_app_icon_prefers_real_ico(tmp_path):
    ico = tmp_path / "icon.ico"
    ico.write_bytes(b"fake-ico")
    assert desktop.find_app_icon([tmp_path]) == str(ico)


def test_find_app_icon_returns_none_when_absent(tmp_path):
    assert desktop.find_app_icon([tmp_path]) is None


def test_find_app_icon_converts_png_fallback(tmp_path):
    pytest.importorskip("PIL")
    png_dir = tmp_path / "dashboard" / "static"
    png_dir.mkdir(parents=True)
    from PIL import Image

    Image.new("RGBA", (64, 64), (27, 60, 38, 255)).save(png_dir / "icon.png")
    found = desktop.find_app_icon([tmp_path])
    assert found is not None
    assert Path(found).suffix == ".ico"
    assert Path(found).is_file()


def test_tray_wires_loaded_event_for_icon():
    """tray.run() must attach the icon handler to window.events.loaded."""
    src = Path(__file__).resolve().parent.parent / "focuscore" / "tray.py"
    text = src.read_text()
    assert "events.loaded += self._on_window_loaded" in text
    assert "def _on_window_loaded" in text
