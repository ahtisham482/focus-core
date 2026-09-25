"""Tests for the dashboard local security baseline (ticket FC-002).

The dashboard is a single-user app bound to the loopback interface with no
authentication, so any web page open in the user's browser could reach it.
These tests lock in two protections:

1. before_request hook: mutating requests (POST/PUT/DELETE/PATCH) are
   rejected with 403 unless the Host header is loopback and any Origin
   header is a loopback origin. GET requests are unaffected.
2. after_request hook: secure response headers on every response.

Everything below uses Flask's test client -- no network, no server.
"""

import ast
from pathlib import Path

import pytest

from dashboard.app import (
    app,
    _host_is_loopback,
    _origin_is_loopback,
)
from focuscore import store


# ------------------------------------------------------------------ helpers ---

def _seed_day(db):
    store.save_events("2026-09-21", [{
        "ts": "2026-09-21T09:00:00", "duration": 3600, "app": "Code",
        "title": "t", "category": "Software Development", "score": 2,
        "match_key": "app:Code",
    }], path=db)
    store.save_day_stats("2026-09-21", 0, 3600, path=db)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "sec.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    _seed_day(db)
    return app.test_client()


POST_URL = "/timesheet/project/add"  # simple DB-backed POST used for probes


# ------------------------------------------------------ loopback-CSRF hook ---

def test_post_evil_host_rejected(client):
    r = client.post(POST_URL, headers={"Host": "evil.com"},
                    data={"name": "x"})
    assert r.status_code == 403
    assert "Forbidden" in r.get_data(as_text=True)


def test_post_evil_host_with_port_rejected(client):
    r = client.post(POST_URL, headers={"Host": "attacker.example:5000"},
                    data={"name": "x"})
    assert r.status_code == 403


def test_post_subdomain_spoof_of_localhost_rejected(client):
    # "localhost.evil.com" is NOT localhost.
    r = client.post(POST_URL, headers={"Host": "localhost.evil.com"},
                    data={"name": "x"})
    assert r.status_code == 403


def test_post_evil_origin_rejected(client):
    r = client.post(POST_URL, headers={"Origin": "http://evil.com"},
                    data={"name": "x"})
    assert r.status_code == 403


def test_post_opaque_origin_rejected(client):
    r = client.post(POST_URL, headers={"Origin": "null"},
                    data={"name": "x"})
    assert r.status_code == 403


def test_all_mutating_methods_guarded(client):
    for method in ("put", "patch", "delete"):
        r = getattr(client, method)(POST_URL, headers={"Host": "evil.com"})
        # before_request runs before routing, so 403 even without a route.
        assert r.status_code == 403, method


def test_post_valid_loopback_host_and_origin_allowed(client):
    r = client.post(POST_URL,
                    headers={"Host": "127.0.0.1:5000",
                             "Origin": "http://127.0.0.1:5000"},
                    data={"name": "x"}, follow_redirects=True)
    assert r.status_code == 200
    # The project was actually added to the DB.
    assert any(p["name"] == "x" for p in store.list_projects(
        path=store.DEFAULT_DB_PATH))


def test_post_valid_localhost_origin_allowed(client):
    r = client.post(POST_URL,
                    headers={"Host": "localhost:5000",
                             "Origin": "http://localhost:8080"},
                    data={"name": "y"}, follow_redirects=True)
    assert r.status_code == 200


def test_post_no_origin_header_allowed(client):
    # Plain form posts (no Origin) from the dashboard itself keep working.
    r = client.post(POST_URL, data={"name": "z"})
    assert r.status_code == 302


def test_get_unaffected_by_evil_headers(client):
    r = client.get("/", headers={"Host": "evil.com",
                                 "Origin": "http://evil.com"},
                   follow_redirects=True)  # / may 302 to /welcome
    assert r.status_code == 200


def test_head_unaffected_by_evil_headers(client):
    r = client.head("/", headers={"Host": "evil.com",
                                  "Origin": "http://evil.com"},
                    follow_redirects=True)
    assert r.status_code == 200


# ------------------------------------------------------------- host helper ---

@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True),
    ("127.0.0.1:5000", True),
    ("localhost", True),
    ("localhost:5000", True),
    ("LOCALHOST", True),
    ("::1", True),
    ("[::1]:5000", True),
    ("evil.com", False),
    ("evil.com:5000", False),
    ("127.0.0.1.evil.com", False),
    ("localhost.evil.com", False),
    ("192.168.1.5", False),
    ("10.0.0.2", False),
    ("::2", False),
    ("", False),
    (" 127.0.0.1 ", True),  # whitespace tolerated
])
def test_host_is_loopback(host, expected):
    assert _host_is_loopback(host) is expected


@pytest.mark.parametrize("origin,expected", [
    ("http://127.0.0.1:5000", True),
    ("http://localhost:5000", True),
    ("https://localhost:5000", True),
    ("http://[::1]:5000", True),
    ("http://127.0.0.1", True),
    ("http://evil.com", False),
    ("https://evil.com", False),
    ("http://127.0.0.1.evil.com", False),
    ("http://localhost.evil.com:5000", False),
    ("file://127.0.0.1", False),
    ("ftp://127.0.0.1", False),
    ("null", False),
    ("", False),
])
def test_origin_is_loopback(origin, expected):
    assert _origin_is_loopback(origin) is expected


# --------------------------------------------------------- security headers ---

def _assert_secure_headers(response):
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    csp = response.headers["Content-Security-Policy"]
    assert csp and "default-src 'self'" in csp


def test_secure_headers_on_get(client):
    _assert_secure_headers(client.get("/"))


def test_secure_headers_on_403(client):
    r = client.post(POST_URL, headers={"Host": "evil.com"})
    assert r.status_code == 403
    _assert_secure_headers(r)


def test_secure_headers_on_redirect(client):
    r = client.post(POST_URL, data={"name": "redir"})
    assert r.status_code == 302
    _assert_secure_headers(r)


# --------------------------------------------------------------- smoke test ---

SMOKE_PAGES = ("/", "/backup", "/timesheet", "/report",
               "/coaching", "/focus", "/goals")


def test_dashboard_pages_still_render(client):
    for url in SMOKE_PAGES:
        r = client.get(url, follow_redirects=True)  # / may 302 to /welcome
        assert r.status_code == 200, url
        _assert_secure_headers(r)


# ---------------------------------------------------------- bind host check ---

def test_default_bind_host_is_loopback():
    """The argparse --host default in dashboard/app.py must be 127.0.0.1.

    The parser lives inside `if __name__ == "__main__"`, so parse the
    source statically instead of importing it.
    """
    source = Path(__file__).resolve().parent.parent / "dashboard" / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    defaults = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            args = [a.value for a in node.args
                    if isinstance(a, ast.Constant)]
            for kw in node.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                    for a in args:
                        defaults[a] = kw.value.value
    assert defaults.get("--host") == "127.0.0.1"
