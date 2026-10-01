"""Roadmap 1.14: /welcome/restart is POST-only, and GET requests
never change user data (the invariant the loopback CSRF guard in
dashboard/app.py relies on when it lets safe methods through).

Layer 1 (pin): GET /welcome/restart answers 405 and leaves the
onboarded flag alone; POST removes the flag and redirects to
/welcome; a served page's footer carries a POST form (not an
anchor) with the "Take the tour again" label.

Layer 2 (sweep): every route that allows GET is requested against
a throwaway DB, with the query string the UI actually sends where
one matters (see _QUERY_SAMPLES), and state must be unchanged:

* every table EXCEPT finance_audit_events -- row multisets must be
  byte-identical before and after each request;
* finance_audit_events -- identical after every route EXCEPT the
  three named export GETs below, where append-only growth is
  allowed (existing rows unchanged, count may only grow);
* the DB schema, the onboarded flag's existence, and the set of
  file NAMES in the tmp data directory (new or removed files fail
  unless named in _FILE_EXCEPTIONS with a reason).

State is snapshotted as a logical dump (rows per table,
order-insensitive) rather than raw file bytes because the DB runs
in WAL mode: checkpointing moves file bytes without changing
content. 404/500 answers are fine; only mutation fails -- except
the three export routes, which must answer 200 so the audit
exercise is real (a bare URL 404s before the logging call, which
is exactly the hole the first version of this sweep had).

Exception registry -- the ONE named exception to "GET never
changes state": the financial-export GETs append an audit row to
finance_audit_events on every download. That append-only audit
stamp is deliberate (finance downloads are audited by design);
converting downloads to POST was considered and rejected.
    /timesheet/export.json
    /timesheet/export/client
    /timesheet/statement
No other route may change anything; any other user-data change
fails this test and is a stop-and-report, not a new exception.

Known design notes (pre-existing, deliberate, documented here so
they are not silently passed over -- NOT converted and NOT
exceptions granted by this test): / and /day/<day> re-run the day
pipeline on read (live refresh), /focus settles pomodoro cycles on
every view, the home page refreshes a stale calendar cache back
into settings when a calendar is configured, and /update can
write its check-cache file on installed copies. In the sweep
environment (no ActivityWatch, no active session, no configured
calendar, dev copy) these settle during warm-up and are read-
stable afterwards; anything they changed in user tables beyond
the audit exception above would still fail this sweep.

Known limitations, stated plainly:
* Warm-up: the first pass over every route creates the schema and
  settles first-hit work before measurement. A mutation that
  happens ONLY on a route's very first hit (one-shot) would be
  masked by that warm-up and pass unseen.
* The comparison is logical: a GET that rewrites a row with the
  same value is invisible by construction.
* The file check covers file names in the tmp data directory only
  -- not the contents of files that already exist, and not paths
  outside that directory.
* The sweep issues GET; HEAD and OPTIONS are Flask's automatic
  companions to the same handlers.
"""
import sqlite3
from collections import Counter
from pathlib import Path

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

# Query strings the UI actually submits, keyed by path. Without
# these the export routes 404 on missing from/to before ever
# reaching their audit call, so the exercise would not be real.
_QUERY_SAMPLES = {
    "/timesheet/export.json": "from=2026-09-01&to=2026-09-24",
    "/timesheet/export/client": "from=2026-09-01&to=2026-09-24",
    "/timesheet/statement": "from=2026-09-01&to=2026-09-24",
}

# The one named exception (see module docstring): these GETs may
# append rows to finance_audit_events; existing audit rows must
# survive unchanged and the count may only grow. Every other table
# must stay byte-identical for them too -- an export route that
# also wrote settings would fail this sweep.
_AUDIT_APPEND_PATHS = frozenset(_QUERY_SAMPLES)

# File names in the tmp data directory that may appear or disappear
# without failing the sweep, each with its reason. Any OTHER new or
# removed file fails the sweep.
_FILE_EXCEPTIONS = {
    # WAL sidecars of the sweep DB (noget.db in the env fixture):
    # SQLite creates and removes them as connections open, close
    # and checkpoint. They are volatile plumbing, not user state.
    "noget.db-wal": "SQLite WAL sidecar; volatile by design",
    "noget.db-shm": "SQLite WAL shared-memory file; volatile",
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
    path params have no sample value (a coverage hole to fix).

    Routes listed in _QUERY_SAMPLES get the query string the UI
    sends appended; every other route is requested bare.
    """
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
            path = built[-1] if isinstance(built, tuple) else built
            query = _QUERY_SAMPLES.get(path)
            urls.append(path + "?" + query if query else path)
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


def _file_names(root):
    """Sorted file names under the tmp data directory (relative).

    Names only: the content of a file that already exists is out of
    scope (see the module docstring's stated limitations).
    """
    root = Path(root)
    return tuple(sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*") if p.is_file()))


def test_no_get_route_mutates_state(env):
    client, db, flag = env
    flag.write_text("2026-09-24")
    data_dir = Path(flag).parent
    urls, unfilled = _get_urls(client.application)
    assert urls, "sweep discovered no GET routes at all"
    assert not unfilled, (
        "GET routes with no sample value in _SAMPLE_VALUES: "
        + ", ".join(unfilled))
    # Warm-up: the first request against a fresh DB creates the
    # schema (init_db); one pass over every route settles that and
    # any other first-hit work before state is measured. Known
    # limitation (see docstring): a mutation that happens only on
    # a route's very first hit would be masked by this warm-up.
    for url in urls:
        client.get(url)
    offenders = []
    for url in urls:
        path = url.split("?", 1)[0]
        before = _state(db, flag)
        before_files = _file_names(data_dir)
        resp = client.get(url)
        after = _state(db, flag)
        after_files = _file_names(data_dir)
        changed = []
        # (a) Every table except finance_audit_events must be
        # identical -- for the export routes too.
        table_names = set(before["tables"]) | set(after["tables"])
        for name in sorted(table_names):
            if name == "finance_audit_events":
                continue
            if before["tables"].get(name) != after["tables"].get(name):
                changed.append(name)
        if before["onboarded"] != after["onboarded"]:
            changed.append("<onboarded flag>")
        if before["schema"] != after["schema"]:
            changed.append("<schema>")
        # (b) finance_audit_events: identical everywhere except the
        # three named export GETs, where append-only growth is the
        # one named exception (existing rows must survive).
        audit_before = before["tables"].get("finance_audit_events", ())
        audit_after = after["tables"].get("finance_audit_events", ())
        if path in _AUDIT_APPEND_PATHS:
            if resp.status_code != 200:
                changed.append(
                    f"answered {resp.status_code}, expected 200 "
                    "(audit exercise not real)")
            if Counter(audit_before) - Counter(audit_after):
                changed.append(
                    "finance_audit_events (audit rows lost or "
                    "changed)")
        elif audit_before != audit_after:
            changed.append("finance_audit_events")
        # (c) File names in the tmp data directory: a new or
        # removed file fails unless excepted above with a reason.
        new_files = sorted(
            set(after_files) - set(before_files) - set(_FILE_EXCEPTIONS))
        gone_files = sorted(
            set(before_files) - set(after_files) - set(_FILE_EXCEPTIONS))
        if new_files:
            changed.append("new files: " + ", ".join(new_files))
        if gone_files:
            changed.append("removed files: " + ", ".join(gone_files))
        if changed:
            offenders.append(f"{url} changed {', '.join(changed)}")
    assert not offenders, "GET mutated state: " + "; ".join(offenders)
    assert flag.exists()
