"""Acceptance tests for Roadmap 2.5: opt-in per-incident crash reports.

The deal (blackboard, binding): after an unclean shutdown the home page
may OFFER a tiny crash report -- app version, OS, and the exception TYPE
only. The app never sends anything: the user copies the shown text or
opens it in their own mail client and presses send themselves. No
message, no traceback, no path, no activity data can reach the stored
record or the report text, and a crashreport failure must never break
app startup.
"""

import json
import os
import platform
import sys
import threading
from pathlib import Path

import pytest

from focuscore import __version__, crashreport, paths, store

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Point the app data dir at a tmp folder; isolate global hooks."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(crashreport, "_hooks_installed", False)
    old_sys_hook = sys.excepthook
    old_thread_hook = threading.excepthook
    yield tmp_path
    sys.excepthook = old_sys_hook
    threading.excepthook = old_thread_hook


def _plant_leftover_marker(folder, crash_type=None):
    (folder / "session-mark.json").write_text(
        json.dumps({"pid": 4242, "started_at": "2026-09-30T10:00:00+00:00",
                    "version": "1.15.0"}), encoding="utf-8")
    if crash_type is not None:
        (folder / "last-crash.json").write_text(
            json.dumps({"type": crash_type, "version": "1.15.0",
                        "os": "Windows-10", "at": "2026-09-30T10:01:00+00:00"}),
            encoding="utf-8")


# ------------------------------------------------------------- marker --

def test_session_marker_write_and_clear(data_dir):
    assert crashreport.previous_run_unclean() is False
    crashreport.mark_session_start()
    marker = data_dir / "session-mark.json"
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["version"] == __version__
    assert payload["started_at"]
    assert crashreport.previous_run_unclean() is True
    crashreport.clear_session_marker()
    assert not marker.exists()
    assert crashreport.previous_run_unclean() is False


def test_begin_session_clean_start_offers_nothing(data_dir):
    crashreport.begin_session()
    assert crashreport.pending_report() is None
    assert not (data_dir / "crash-pending.json").exists()
    assert not list(data_dir.glob("crash-report-*.txt"))
    # ...but the fresh session marker is in place for next time.
    assert (data_dir / "session-mark.json").is_file()


def test_begin_session_unclean_creates_pending_and_report_file(data_dir):
    _plant_leftover_marker(data_dir, crash_type="RuntimeError")
    crashreport.begin_session()
    text = crashreport.pending_report()
    assert text is not None
    assert "RuntimeError" in text
    assert (data_dir / "crash-pending.json").is_file()
    saved = list(data_dir.glob("crash-report-*.txt"))
    assert len(saved) == 1
    assert saved[0].read_text(encoding="utf-8") == text
    # The recorded crash is consumed by the offer: one incident, one card.
    assert not (data_dir / "last-crash.json").exists()
    # A fresh marker replaced the leftover one.
    marker = json.loads(
        (data_dir / "session-mark.json").read_text(encoding="utf-8"))
    assert marker["pid"] == os.getpid()


def test_begin_session_unclean_without_crash_record_is_honest(data_dir):
    _plant_leftover_marker(data_dir)  # power cut / Windows closed it
    crashreport.begin_session()
    text = crashreport.pending_report()
    assert text is not None
    assert crashreport.NO_ERROR_LINE in text
    assert len(list(data_dir.glob("crash-report-*.txt"))) == 1


def test_handled_clears_offer_and_next_clean_start_stays_quiet(data_dir):
    _plant_leftover_marker(data_dir, crash_type="KeyError")
    crashreport.begin_session()
    assert crashreport.pending_report() is not None
    crashreport.mark_handled()
    assert crashreport.pending_report() is None
    assert not (data_dir / "crash-pending.json").exists()
    # Clean exit, then a normal start: nothing is offered again.
    crashreport.clear_session_marker()
    crashreport.begin_session()
    assert crashreport.pending_report() is None


# ------------------------------------------------------- exception hook --

def test_excepthook_records_type_only_and_chains(data_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(
        sys, "excepthook", lambda t, v, tb: seen.append((t, v, tb)))
    crashreport.install_crash_hooks()
    secret = "hunter2"
    poisoned = "blew up reading " + secret + " in C:\\Users\\fake\\diary.txt"
    try:
        raise ValueError(poisoned)
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()
    sys.excepthook(exc_type, exc_value, exc_tb)

    stored_text = (data_dir / "last-crash.json").read_text(encoding="utf-8")
    stored = json.loads(stored_text)
    assert set(stored) == {"type", "version", "os", "at"}
    assert stored["type"] == "ValueError"
    assert stored["version"] == __version__
    # The poisoned message (secret + path) reaches NOTHING we store/show.
    assert secret not in stored_text
    assert "Users" not in stored_text
    assert "diary" not in stored_text
    report = crashreport.build_report()
    assert "ValueError" in report
    assert secret not in report
    assert "diary" not in report
    # The previous hook still ran (we chain, never swallow silently).
    assert seen and seen[0][0] is ValueError


def test_threading_excepthook_records_type_only(data_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args))
    crashreport.install_crash_hooks()

    class _Args:
        exc_type = KeyError
        exc_value = KeyError("hidden-key-123")
        exc_traceback = None
        thread = None

    threading.excepthook(_Args())
    stored_text = (data_dir / "last-crash.json").read_text(encoding="utf-8")
    assert json.loads(stored_text)["type"] == "KeyError"
    assert "hidden-key-123" not in stored_text
    assert seen


# -------------------------------------------------------------- report --

def test_report_exact_content_with_recorded_crash(data_dir):
    (data_dir / "last-crash.json").write_text(
        json.dumps({"type": "KeyError", "version": "0.0.0",
                    "os": "SomeOS", "at": "whenever"}), encoding="utf-8")
    expected = (
        "Focus Core crash report\n"
        "Focus Core version: " + __version__ + "\n"
        "Operating system: " + platform.platform() + "\n"
        "What went wrong: KeyError\n")
    assert crashreport.build_report() == expected


def test_report_exact_content_without_recorded_crash(data_dir):
    expected = (
        "Focus Core crash report\n"
        "Focus Core version: " + __version__ + "\n"
        "Operating system: " + platform.platform() + "\n"
        "What went wrong: " + crashreport.NO_ERROR_LINE + "\n")
    assert crashreport.build_report() == expected
    assert crashreport.NO_ERROR_LINE == (
        "no error was recorded (the app was closed by Windows "
        "or lost power)")


def test_mailto_carries_the_same_text(data_dir):
    _plant_leftover_marker(data_dir, crash_type="ValueError")
    crashreport.begin_session()
    text = crashreport.pending_report()
    url = crashreport.mailto_url(text)
    assert url.startswith("mailto:" + crashreport.MAINTAINER_EMAIL)
    assert "Focus%20Core%20crash%20report" in url  # subject
    assert "ValueError" in url


# ------------------------------------------------------------ defensive --

def test_crashreport_failures_never_raise(monkeypatch):
    def _boom():
        raise RuntimeError("data dir exploded")

    monkeypatch.setattr(paths, "data_dir", _boom)
    # None of these may raise: startup must survive a broken crashreport.
    crashreport.begin_session()
    crashreport.mark_session_start()
    crashreport.clear_session_marker()
    crashreport.mark_handled()
    assert crashreport.pending_report() is None
    assert crashreport.previous_run_unclean() is False
    assert "Focus Core version: " in crashreport.build_report()


def test_crashreport_module_cannot_send_anything():
    src = Path(crashreport.__file__).read_text(encoding="utf-8")
    for banned in ("urllib.request", "smtplib", "socket", "import requests"):
        assert banned not in src


def test_crashreport_reuses_canonical_version():
    src = Path(crashreport.__file__).read_text(encoding="utf-8")
    assert __version__ not in src  # no second version literal


def test_entry_points_begin_a_crash_session():
    tray_src = (REPO_ROOT / "focuscore" / "tray.py").read_text(encoding="utf-8")
    app_src = (REPO_ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "crashreport" in tray_src and "begin_session()" in tray_src
    assert "crashreport" in app_src and "begin_session()" in app_src


# ------------------------------------------------------------ home card --

@pytest.fixture()
def dash_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", str(tmp_path / "crash.db"))
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(crashreport, "_hooks_installed", False)
    import dashboard.app as dash_app
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-10-01")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    return dash_app, tmp_path


def test_home_shows_card_and_report_file_when_pending(dash_env):
    dash_app, folder = dash_env
    _plant_leftover_marker(folder, crash_type="RuntimeError")
    crashreport.begin_session()
    assert len(list(folder.glob("crash-report-*.txt"))) == 1
    html = dash_app.app.test_client().get("/").data.decode()
    assert "close properly last time" in html
    assert "no activity data, ever" in html
    text = crashreport.pending_report()
    assert text.strip() in html  # the exact report, shown verbatim
    assert "RuntimeError" in html
    assert "mailto:" + crashreport.MAINTAINER_EMAIL in html
    assert "Copy report" in html
    assert "No thanks" in html


def test_home_has_no_card_when_nothing_pending(dash_env):
    dash_app, _folder = dash_env
    html = dash_app.app.test_client().get("/").data.decode()
    assert "close properly last time" not in html


def test_handled_route_dismisses_the_card_for_good(dash_env):
    dash_app, folder = dash_env
    _plant_leftover_marker(folder)
    crashreport.begin_session()
    assert crashreport.pending_report() is not None
    client = dash_app.app.test_client()
    resp = client.post("/crash-report/handled")
    assert resp.status_code in (200, 204)
    assert crashreport.pending_report() is None
    assert "close properly last time" not in client.get("/").data.decode()
    # Idempotent: pressing again with nothing pending is not an error.
    assert client.post("/crash-report/handled").status_code in (200, 204)
