"""Roadmap 1.6: Blueprint migration + app factory proofs.

New tests only. They prove:
- each routes module imports standalone in a fresh interpreter
  (the old ``dashboard.app`` <-> ``dashboard.routes`` circular
  import is gone), and ``dashboard.app`` imports without any
  routes module being imported first;
- ``create_app()`` returns a fresh, independent instance whose
  route map equals the global app's, and which serves a real page;
- calling ``create_app()`` twice does not error (blueprints can
  be registered on more than one app).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

ROUTE_MODULES = [
    "budgets",
    "core",
    "focus",
    "invoices",
    "system",
    "timesheet",
]


def _route_map(flask_app):
    """Sorted (path, methods) set, ignoring HEAD/OPTIONS and
    endpoint names (blueprint prefixes are expected to differ
    in name only, never in path or methods)."""
    return sorted(
        (
            rule.rule,
            tuple(
                sorted(m for m in rule.methods
                       if m not in ("HEAD", "OPTIONS"))
            ),
        )
        for rule in flask_app.url_map.iter_rules()
    )


@pytest.mark.parametrize("module", ROUTE_MODULES)
def test_routes_module_cold_import(module):
    """Each routes module must import standalone in a fresh
    interpreter (no circular import back through the factory)."""
    result = subprocess.run(
        [sys.executable, "-c", "import dashboard.routes.%s" % module],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_dashboard_app_cold_import_without_routes_first():
    """``dashboard.app`` must import even when no routes module
    was imported first (registration is internal to the factory)."""
    result = subprocess.run(
        [sys.executable, "-c", "import dashboard.app"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_create_app_fresh_instance_route_map_and_serving():
    from dashboard.app import app as global_app
    from dashboard.app import create_app

    fresh = create_app({"TESTING": True})
    assert fresh is not global_app
    assert fresh.config["TESTING"] is True

    # The global app in this pytest process may carry ephemeral
    # routes added by other test modules ("/_test_..."); ignore
    # those when comparing against a fresh instance.
    global_map = [
        entry for entry in _route_map(global_app)
        if not entry[0].startswith("/_test")
    ]
    assert _route_map(fresh) == global_map

    client = fresh.test_client()
    response = client.get("/help")
    assert response.status_code == 200


def test_create_app_twice_does_not_error():
    from dashboard.app import create_app

    first = create_app()
    second = create_app()
    assert first is not second
    assert _route_map(first) == _route_map(second)
