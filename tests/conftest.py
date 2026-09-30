"""Suite-wide test isolation for the emergency-pass JSONL fallback (Qwen R7).

`focuscore.shield.fallback_passes_path()` points at the REAL user data dir
(~/.focus-core/passes.fallback.jsonl). When SQLite is unreachable during a
test (locked DB, full disk, missing dir), `store.create_pass()` appends the
pass to that real file -- and `shield.pass_active()` reads it back on every
call, including in the real app. A stale test pass can therefore disable the
user's real shield and break unrelated tests that assert "no active pass".

This autouse fixture redirects the fallback file into pytest's tmp dir for
the whole suite, so no test can ever touch the real one.
"""

import pytest

from focuscore import shield, store


@pytest.fixture(autouse=True)
def _isolate_pass_fallback_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        shield,
        "fallback_passes_path",
        lambda: str(tmp_path / "passes.fallback.jsonl"),
    )


# ------------------------------------------------------------ dashboard ---
# Roadmap 1.8: fixtures that were copy-pasted identically (or near-
# identically) across test files live here now. Fixtures with the same
# name but different behavior stay local to their file -- a local
# definition shadows the conftest one, so nothing changes for them.


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Dashboard test client + tmp DB path, yielded as ``(client, db)``.

    Hoisted from test_fk_safe_deletes_17.py and test_invoice_routes.py,
    whose copies differed only in the tmp DB filename. TESTING is on
    (real error handlers are test_error_pages.py's separate concern;
    that file and test_security_baseline.py keep their own ``client``).
    """
    db = str(tmp_path / "client.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    # dashboard.app reads store.DEFAULT_DB_PATH at request time
    from dashboard import app as app_mod
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c, db


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    """Flask test client with an isolated settings DB.

    Hoisted from test_diagnostics_export.py and
    test_privacy_update_toggle.py (byte-identical copies).
    """
    pytest.importorskip("flask")
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    import dashboard.app as dash_app
    dash_app.app.config["TESTING"] = True
    return dash_app.app.test_client()
