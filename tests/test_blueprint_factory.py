"""Roadmap 1.6: Blueprint migration + app factory proofs.

New tests only. They prove:
- each routes module imports standalone in a fresh interpreter
  (the old ``dashboard.app`` <-> ``dashboard.routes`` circular
  import is gone), ``dashboard.app`` imports without any routes
  module being imported first, and in every import order the
  module-global app carries the complete golden route map;
- ``create_app()`` returns a fresh, independent instance whose
  route map equals the golden map, and which serves a real page;
- calling ``create_app()`` twice does not error (blueprints can
  be registered on more than one app);
- repeated ``create_app()`` calls attach exactly one
  ``_DropFlaskDuplicateTraceback`` filter to the shared
  ``dashboard.app`` logger.
"""
import json
import logging
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

# Golden route map: the sorted (path, methods-minus-HEAD/OPTIONS)
# set captured from the verified app (Roadmap 1.6, commit 408650a,
# app-first import). A missing or renamed blueprint fails against
# this literal instead of comparing two equally-broken apps.
GOLDEN_ROUTE_MAP = [
    ('/', ('GET',)),
    ('/activities', ('GET',)),
    ('/alerts', ('GET',)),
    ('/alerts/add', ('POST',)),
    ('/alerts/delete', ('POST',)),
    ('/alerts/toggle', ('POST',)),
    ('/backup', ('GET',)),
    ('/backup/diagnostics', ('GET',)),
    ('/backup/encryption/disable', ('POST',)),
    ('/backup/encryption/enable', ('POST',)),
    ('/backup/encryption/unlock', ('POST',)),
    ('/backup/now', ('POST',)),
    ('/backup/restore', ('POST',)),
    ('/coaching', ('GET',)),
    ('/collect', ('GET',)),
    ('/crash-report/handled', ('POST',)),
    ('/day/<day>', ('GET',)),
    ('/focus', ('GET',)),
    ('/focus/abort', ('POST',)),
    ('/focus/cues', ('POST',)),
    ('/focus/cycle/break/end', ('POST',)),
    ('/focus/cycle/break/skip', ('POST',)),
    ('/focus/cycle/break/start', ('POST',)),
    ('/focus/depth', ('GET',)),
    ('/focus/end', ('POST',)),
    ('/focus/peak', ('POST',)),
    ('/focus/start', ('POST',)),
    ('/focus/target', ('POST',)),
    ('/goals', ('GET',)),
    ('/goals/add', ('POST',)),
    ('/goals/delete', ('POST',)),
    ('/goals/pin', ('POST',)),
    ('/healthz', ('GET',)),
    ('/help', ('GET',)),
    ('/help/<key>', ('GET',)),
    ('/intelligence', ('GET',)),
    ('/intelligence/report', ('GET',)),
    ('/invoices', ('GET',)),
    ('/invoices/<int:invoice_id>', ('GET',)),
    ('/invoices/<int:invoice_id>/add-line', ('POST',)),
    ('/invoices/<int:invoice_id>/mark-paid', ('POST',)),
    ('/invoices/<int:invoice_id>/pay', ('POST',)),
    ('/invoices/<int:invoice_id>/print', ('GET',)),
    ('/invoices/<int:invoice_id>/reissue', ('POST',)),
    ('/invoices/<int:invoice_id>/remove-line', ('POST',)),
    ('/invoices/<int:invoice_id>/send', ('POST',)),
    ('/invoices/<int:invoice_id>/tax-discount', ('POST',)),
    ('/invoices/<int:invoice_id>/void', ('POST',)),
    ('/invoices/create', ('POST',)),
    ('/invoices/new', ('GET',)),
    ('/override', ('POST',)),
    ('/report', ('GET',)),
    ('/settings/calendar', ('POST',)),
    ('/settings/theme', ('POST',)),
    ('/setup/activitywatch', ('GET',)),
    ('/shield', ('GET',)),
    ('/shield/hud', ('POST',)),
    ('/shield/pass', ('POST',)),
    ('/shield/rule/add', ('POST',)),
    ('/shield/rule/delete', ('POST',)),
    ('/shield/rule/toggle', ('POST',)),
    ('/shield/toggle', ('POST',)),
    ('/static/<path:filename>', ('GET',)),
    ('/timesheet', ('GET',)),
    ('/timesheet/accept', ('POST',)),
    ('/timesheet/add', ('POST',)),
    ('/timesheet/delete', ('POST',)),
    ('/timesheet/edit', ('POST',)),
    ('/timesheet/export.json', ('GET',)),
    ('/timesheet/export/client', ('GET',)),
    ('/timesheet/export/old', ('GET',)),
    ('/timesheet/lock', ('POST',)),
    ('/timesheet/project/add', ('POST',)),
    ('/timesheet/project/backfill-rate', ('POST',)),
    ('/timesheet/project/budget', ('POST',)),
    ('/timesheet/project/delete', ('POST',)),
    ('/timesheet/project/rate', ('POST',)),
    ('/timesheet/project/rollover', ('POST',)),
    ('/timesheet/rollover-settings', ('POST',)),
    ('/timesheet/statement', ('GET',)),
    ('/update', ('GET',)),
    ('/update/check-toggle', ('POST',)),
    ('/update/revert', ('POST',)),
    ('/update/start', ('POST',)),
    ('/welcome', ('GET',)),
    ('/welcome/finish', ('POST',)),
    ('/welcome/restart', ('POST',)),
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


def _cold_global_route_map(code):
    """Run ``code`` in a fresh interpreter; it must print the
    global app's route map as JSON on its last stdout line."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout.strip().splitlines()[-1])
    return sorted((path, tuple(methods)) for path, methods in printed)


_PRINT_MAP = (
    "import dashboard.app as da\n"
    "import json\n"
    "print(json.dumps([[r.rule, sorted(m for m in r.methods "
    "if m not in ('HEAD', 'OPTIONS'))] "
    "for r in da.app.url_map.iter_rules()]))\n"
)


@pytest.mark.parametrize("module", ROUTE_MODULES)
def test_routes_module_cold_import(module):
    """Each routes module must import standalone in a fresh
    interpreter (no circular import back through the factory),
    and the global app it triggers must still be complete."""
    code = "import dashboard.routes.%s\n" % module + _PRINT_MAP
    assert _cold_global_route_map(code) == GOLDEN_ROUTE_MAP


def test_dashboard_app_cold_import_without_routes_first():
    """``dashboard.app`` must import even when no routes module
    was imported first (registration is internal to the factory),
    and its global app must carry the complete route map."""
    assert _cold_global_route_map(_PRINT_MAP) == GOLDEN_ROUTE_MAP


def test_create_app_fresh_instance_route_map_and_serving(tmp_path,
                                                         monkeypatch):
    import dashboard.app as dash_app
    from dashboard.app import app as global_app
    from dashboard.app import create_app
    from focuscore import store

    # ``from dashboard.app import app`` returns the same cached
    # object as attribute access on the module.
    assert dash_app.app is global_app

    fresh = create_app({"TESTING": True})
    assert fresh is not global_app
    assert fresh.config["TESTING"] is True
    assert _route_map(fresh) == GOLDEN_ROUTE_MAP

    # The global app in this pytest process may carry ephemeral
    # routes added by other test modules ("/_test_..."); ignore
    # those when comparing against the golden map.
    global_map = [
        entry for entry in _route_map(global_app)
        if not entry[0].startswith("/_test")
    ]
    assert global_map == GOLDEN_ROUTE_MAP

    # Serving a real page runs route handlers that call
    # store.init_db(); route that at a throwaway DB (Roadmap 1.7
    # repair: the default path is the repo dev DB, which the suite
    # must never migrate). The route-map/cold-import assertions
    # above are unaffected.
    monkeypatch.setattr(store, "DEFAULT_DB_PATH",
                        str(tmp_path / "blueprint.db"))
    client = fresh.test_client()
    response = client.get("/help")
    assert response.status_code == 200


def test_create_app_twice_does_not_error():
    from dashboard.app import create_app

    first = create_app()
    second = create_app()
    assert first is not second
    assert _route_map(first) == _route_map(second)


def test_create_app_attaches_logger_filter_once():
    """The app logger is a shared per-name singleton, so repeated
    ``create_app()`` calls must not stack duplicate filters."""
    import dashboard.app as dash_app
    from dashboard.app import create_app

    create_app()
    create_app()
    create_app()
    filters = [
        f for f in logging.getLogger("dashboard.app").filters
        if isinstance(f, dash_app._DropFlaskDuplicateTraceback)
    ]
    assert len(filters) == 1
