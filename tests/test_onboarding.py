"""Tests for Phase 4 (v1.5.0): ActivityWatch detection + stranger onboarding.

Detection is plain localhost probing with injectable inputs, so every
expectation below is hand-checkable. The fake ActivityWatch server is a
real HTTP server on 127.0.0.1 -- the same path production uses.
"""

import json
import socket
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from focuscore import activitywatch as aw
from focuscore import home
from focuscore import onboarding as ob


# ------------------------------------------------- fake ActivityWatch ---

class _InfoHandler(BaseHTTPRequestHandler):
    """/api/0/info returns a fixed version; everything else 404s."""
    payload = {"version": "v0.14.0b6", "hostname": "TEST-PC"}

    def do_GET(self):
        if self.path == "/api/0/info":
            body = json.dumps(self.payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class _GarbageHandler(_InfoHandler):
    payload = None  # 200 OK with a body that is not JSON

    def do_GET(self):
        body = b"this is not json"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture()
def aw_server(monkeypatch):
    server = _serve(_InfoHandler)
    port = server.server_address[1]
    monkeypatch.setattr(
        aw, "API_BASE", "http://127.0.0.1:%d/api/0/" % port)
    yield port
    server.shutdown()


@pytest.fixture()
def garbage_server(monkeypatch):
    server = _serve(_GarbageHandler)
    port = server.server_address[1]
    monkeypatch.setattr(
        aw, "API_BASE", "http://127.0.0.1:%d/api/0/" % port)
    yield port
    server.shutdown()


def _closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def no_server(monkeypatch):
    monkeypatch.setattr(
        aw, "API_BASE",
        "http://127.0.0.1:%d/api/0/" % _closed_port())


# ------------------------------------------------------------ probing ---

def test_server_status_running(aw_server):
    status = aw.server_status()
    assert status == {"running": True, "version": "v0.14.0b6",
                      "hostname": "TEST-PC"}


def test_server_status_unreachable(no_server):
    assert aw.server_status(timeout=1) == {
        "running": False, "version": None, "hostname": None}


def test_server_status_garbage_json_is_not_running(garbage_server):
    assert aw.server_status()["running"] is False


def test_server_status_missing_version_field(aw_server, monkeypatch):
    _InfoHandler.payload = {"hostname": "TEST-PC"}  # no "version" key
    try:
        status = aw.server_status()
    finally:
        _InfoHandler.payload = {"version": "v0.14.0b6",
                                "hostname": "TEST-PC"}
    assert status["running"] is True
    assert status["version"] is None


# ---------------------------------------------------------- detection ---

def test_likely_installed_found(tmp_path):
    exe = tmp_path / "ActivityWatch" / "aw-qt.exe"
    exe.parent.mkdir()
    exe.write_text("fake")
    assert aw.likely_installed(
        candidates=[str(tmp_path / "nope" / "aw-qt.exe"), str(exe)]) == str(exe)


def test_likely_installed_missing(tmp_path):
    assert aw.likely_installed(candidates=[str(tmp_path / "aw-qt.exe")]) is None


def test_detection_state_running():
    state = aw.detection_state(status={"running": True, "version": "x"},
                               installed=None)
    assert state == aw.RUNNING


def test_detection_state_installed_not_running():
    state = aw.detection_state(status={"running": False, "version": None,
                                       "hostname": None},
                               installed="C:/fake/aw-qt.exe")
    assert state == aw.INSTALLED_NOT_RUNNING


def test_detection_state_not_installed(monkeypatch):
    monkeypatch.setattr(aw, "likely_installed", lambda: None)
    state = aw.detection_state(status={"running": False, "version": None,
                                       "hostname": None},
                               installed=None)
    assert state == aw.NOT_INSTALLED


# ------------------------------------------------------- attention card ---

def _noon(day):
    return datetime.strptime(day + "T12:00:00", "%Y-%m-%dT%H:%M:%S")


def _cards(db, now, aw_state):
    return {c["code"]: c for c in home.attention_cards(
        db_path=db, now=now, aw_state=aw_state)}


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "ob.db")


def test_aw_setup_card_not_installed(db):
    cards = _cards(db, _noon("2026-09-24"), aw.NOT_INSTALLED)
    assert "aw_setup" in cards
    card = cards["aw_setup"]
    assert card["button_text"] == "Set up ActivityWatch"
    assert card["button_href"] == "/setup/activitywatch"
    assert "isn't installed yet" in card["detail"]
    # One card per problem: the old "tracker" card stays quiet.
    assert "tracker" not in cards


def test_aw_setup_card_installed_not_running(db):
    cards = _cards(db, _noon("2026-09-24"), aw.INSTALLED_NOT_RUNNING)
    assert "aw_setup" in cards
    assert "Start menu" in cards["aw_setup"]["detail"]
    assert "tracker" not in cards


def test_no_aw_setup_card_when_running(db):
    cards = _cards(db, _noon("2026-09-24"), aw.RUNNING)
    assert "aw_setup" not in cards


def test_aw_setup_card_shows_at_night_too(db):
    night = datetime.strptime("2026-09-24T03:00:00", "%Y-%m-%dT%H:%M:%S")
    cards = _cards(db, night, aw.NOT_INSTALLED)
    assert "aw_setup" in cards


# ------------------------------------------------------ onboarding HTML ---

def test_setup_page_not_installed_has_download():
    html = ob.setup_page_html(aw.NOT_INSTALLED)
    assert aw.DOWNLOAD_URL in html
    assert "Download ActivityWatch for Windows" in html
    assert "Check again" in html


def test_setup_page_installed_not_running():
    html = ob.setup_page_html(aw.INSTALLED_NOT_RUNNING)
    assert "installed but not running" in html
    assert "Start menu" in html
    assert aw.DOWNLOAD_URL not in html  # no download needed


def test_setup_page_running():
    html = ob.setup_page_html(aw.RUNNING, version="v0.14.0b6")
    assert "is running" in html
    assert "v0.14.0b6" in html
    assert "Download ActivityWatch" not in html


def test_setup_page_escapes_version():
    html = ob.setup_page_html(aw.RUNNING, version="<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_setup_page_unknown_state_raises():
    with pytest.raises(ValueError):
        ob.setup_page_html("bogus")


def test_welcome_step_running():
    html = ob.welcome_step_html(aw.RUNNING, version="v0.14.0b6")
    assert "already see your activity" in html


def test_welcome_step_not_installed():
    html = ob.welcome_step_html(aw.NOT_INSTALLED)
    assert "/setup/activitywatch" in html
    assert "Check again" in html


def test_welcome_step_installed_not_running():
    html = ob.welcome_step_html(aw.INSTALLED_NOT_RUNNING)
    assert "installed but not running" in html
