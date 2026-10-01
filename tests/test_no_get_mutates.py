"""Roadmap 1.14: /welcome/restart is POST-only, and no GET route
mutates state (the invariant the loopback CSRF guard relies on).

Layer 1 (pin): GET /welcome/restart answers 405 and leaves the
onboarded flag alone; POST removes the flag and redirects to
/welcome; a served page's footer carries a POST form (not an
anchor) with the "Take the tour again" label.

Layer 2 (sweep): every route that allows GET is requested against
a throwaway DB, and the full state -- every table's rows plus the
onboarded flag's existence -- must be identical before and after
each request. State is snapshotted as a logical dump (rows per
table, order-insensitive) rather than raw file bytes because the
DB runs in WAL mode: checkpointing moves file bytes without
changing content. 404/500 answers are fine; only mutation fails.
"""
import sqlite3

import pytest

from focuscore import store

# Sample values for filling path params in the sweep. Keep this map
# exhaustive: a route whose params cannot be filled fails the sweep
# test on purpose, so new parametrized GET routes get coverage by
# extending this map, never by silent omission.
_SAMPLE_VALUES = {
    # A fixed past day: the day page re-runs today's collection only
    # when the day IS today, so a past date takes the read path.
    "day": "2026-09-24",
    # A help article key that exists.
    "key": "setup",
    # Invoice 1 does not exist in a fresh DB; the route answering
    # 404 is fine -- only a state change would fail the sweep.
    "invoice_id": 1,
    # A static asset that ships with the app.
    "filename": "style.css",
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh create_app() instance + test client on a tmp DB.

    Mirrors the Roadmap 1.6/1.7 isolation rules: the repo dev DB
    is never touched; ``store.DEFAULT_DB_PATH`` is redirected and
    the onboarded flag is a tmp file. TESTING stays off so handler
    errors surface as the app's real 500 pages (the sweep treats
    those as acceptable answers) instead of raising mid-sweep.
    """
    db = str(tmp_path / "noget.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    import dashboard.app as dash_app
    flag = tmp_path / ".onboarded"
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    app = dash_app.create_app({"TESTING": False})
    with app.test_client() as client:
        yield client, db, flag


def test_welcome_restart_is_post_only(env):
    client, _db, flag = env
    flag.write_text("2026-09-24")
    resp = client.get("/welcome/restart")
    assert resp.status_code == 405
    assert flag.exists()

    resp = client.post("/welcome/restart")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/welcome")
    assert not flag.exists()


def test_footer_restart_is_a_post_form(env):
    client, _db, flag = env
    flag.write_text("2026-09-24")
    html = client.get("/help").data.decode("utf-8")
    assert "href='/welcome/restart'" not in html
    assert "<form method='post' action='/welcome/restart'" in html
    assert "Take the tour again" in html


def _get_urls(app):
    """Concrete URLs for every GET-able route, plus any route whose
    path params have no sample value (a coverage hole to fix)."""
    urls, unfilled = [], []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods:
            continue
        values = {}
        for arg in rule.arguments:
            if arg not in _SAMPLE_VALUES:
                unfilled.append(rule.rule)
                break
            values[arg] = _SAMPLE_VALUES[arg]
        else:
            built = rule.build(values)
            # werkzeug may return (domain, path); keep the path.
            urls.append(built[-1] if isinstance(built, tuple) else built)
    return sorted(set(urls)), unfilled


def _state(db_path, flag):
    """Logical state snapshot: schema, every table's rows as a
    sorted multiset, and the onboarded flag's existence.

    Includes sqlite_sequence (dumped as a normal table), so a GET
    that inserts-then-deletes into an AUTOINCREMENT table still
    shows up. Read-only: this connection only SELECTs.
    """
    conn = sqlite3.connect(db_path)
    try:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "ORDER BY name")]
        tables = {}
        for name in names:
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
            tables[name] = tuple(
                sorted(repr(tuple(row)) for row in rows))
        schema = tuple(row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
            "ORDER BY name"))
    finally:
        conn.close()
    return {"tables": tables, "schema": schema,
            "onboarded": flag.exists()}


def test_no_get_route_mutates_state(env):
    client, db, flag = env
    flag.write_text("2026-09-24")
    urls, unfilled = _get_urls(client.application)
    assert urls, "sweep discovered no GET routes at all"
    assert not unfilled, (
        "GET routes with no sample value in _SAMPLE_VALUES: "
        + ", ".join(unfilled))
    # Warm-up: the first request against a fresh DB creates the
    # schema (init_db); one pass over every route settles that and
    # any other first-hit work before state is measured.
    for url in urls:
        client.get(url)
    offenders = []
    for url in urls:
        before = _state(db, flag)
        client.get(url)
        after = _state(db, flag)
        if before != after:
            changed = sorted(
                name for name in before["tables"]
                if before["tables"][name] != after["tables"].get(name))
            if before["onboarded"] != after["onboarded"]:
                changed.append("<onboarded flag>")
            if before["schema"] != after["schema"]:
                changed.append("<schema>")
            offenders.append(f"{url} changed {', '.join(changed)}")
    assert not offenders, "GET mutated state: " + "; ".join(offenders)
    assert flag.exists()
