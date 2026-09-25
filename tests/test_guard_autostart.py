"""Guard auto-start: creating a focus session must also start blocking.

Regression test for the real-world report "the 25-minute session does not
work -- YouTube is not blocked": starting a session only recorded it in
the DB, while the enforcement guard (focus-watch.bat) was a second manual
step most users never took. Now ensure_guard_running() is called whenever
a session is newly created, from both the dashboard and the tray.
"""

import os
import subprocess
import sys

import dashboard.app as dash_app
from focuscore import blocker
from focuscore import tray as tray_mod


def test_ensure_guard_running_spawns_blocker(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            calls.append((cmd, kwargs))

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    assert blocker.ensure_guard_running() is True
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd == [sys.executable, "-m", "focuscore.blocker", "--enforce"]
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    if os.name == "nt":
        assert kwargs.get("creationflags") == getattr(
            subprocess, "CREATE_NO_WINDOW", 0)


def test_ensure_guard_running_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(subprocess, "Popen", boom)
    assert blocker.ensure_guard_running() is False


def test_tray_quick_session_starts_guard(monkeypatch):
    monkeypatch.setattr("focuscore.focus.start_session",
                        lambda *a, **k: {"id": 7, "label": "Quick focus"})
    started = []
    monkeypatch.setattr("focuscore.blocker.ensure_guard_running",
                        lambda: started.append(True))
    result = tray_mod.start_quick_session()
    assert result["id"] == 7
    assert started == [True]


def test_tray_quick_session_no_guard_on_error(monkeypatch):
    monkeypatch.setattr("focuscore.focus.start_session",
                        lambda *a, **k: {"error": "already active"})
    started = []
    monkeypatch.setattr("focuscore.blocker.ensure_guard_running",
                        lambda: started.append(True))
    result = tray_mod.start_quick_session()
    assert "error" in result
    assert started == []


def test_dashboard_focus_start_starts_guard(monkeypatch):
    monkeypatch.setattr("focuscore.focus.start_session",
                        lambda *a, **k: {"id": 9, "label": "Deep work"})
    started = []
    monkeypatch.setattr("focuscore.blocker.ensure_guard_running",
                        lambda: started.append(True))
    client = dash_app.app.test_client()
    resp = client.post("/focus/start",
                       data={"label": "Deep work", "preset": "25",
                             "block_level": "strict"})
    assert resp.status_code == 302
    assert started == [True]


def test_dashboard_focus_start_no_guard_on_error(monkeypatch):
    monkeypatch.setattr("focuscore.focus.start_session",
                        lambda *a, **k: {"error": "already active"})
    started = []
    monkeypatch.setattr("focuscore.blocker.ensure_guard_running",
                        lambda: started.append(True))
    client = dash_app.app.test_client()
    resp = client.post("/focus/start",
                       data={"label": "Deep work", "preset": "25",
                             "block_level": "strict"})
    assert resp.status_code == 400
    assert started == []


def test_layout_includes_favicon():
    html = dash_app.layout("T", "<p>x</p>")
    assert "<link rel='icon' href='/static/icon.png'>" in html


def test_favicon_file_exists():
    path = os.path.join(os.path.dirname(dash_app.__file__),
                        "static", "icon.png")
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0
