"""Roadmap 1.18: behavioral-honesty claims register.

Pins the claims in docs/claims.md to evidence:

* loopback-only serving (PRIVACY.md "not reachable from the internet"),
* ActivityWatch on the local loopback,
* the update flow's two honest behaviors (refuses during an active
  session; never touches user data),
* and the register's own format — no bare claims may ship.

Everything runs against TMP db / TMP backup dir only, never the repo dev DB.
"""

import re

import pytest

from focuscore import activitywatch, backup, launcher, paths, store


def _claims_path():
    import pathlib
    here = pathlib.Path(__file__).resolve()
    return here.parent.parent / "docs" / "claims.md"


def test_dashboard_binds_loopback_only():
    # PRIVACY.md: "It is not reachable from the internet — it only
    # listens on your PC itself."
    assert launcher.HOST == "127.0.0.1"


def test_activitywatch_api_base_is_loopback():
    # PRIVACY.md: "the tracker reads the activity-watcher through a
    # connection that never leaves your PC."
    base = activitywatch.API_BASE.lower()
    assert "localhost" in base or "127.0.0.1" in base
    assert not base.startswith("https://")


@pytest.fixture
def dash_env(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    db = tmp_path / "claims.db"
    store.init_db(str(db))
    bdir = tmp_path / "backups"
    bdir.mkdir()
    userdata = tmp_path / "userdata"
    userdata.mkdir()
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", str(db))
    monkeypatch.setattr(paths, "user_data_dir", lambda: userdata)
    monkeypatch.setattr(backup, "backup_dir", lambda dest_dir=None: bdir)
    import dashboard.app as dash_app

    dash_app.app.config["TESTING"] = True
    return {"client": dash_app.app.test_client(), "db": db}


def _mock_update_available(monkeypatch):
    import focuscore.updater as updater_mod

    monkeypatch.setattr(
        updater_mod, "check_for_update",
        lambda force=False: {
            "status": "ok",
            "update_available": True,
            "latest": "v9.9.9",
            "asset": {
                "name": "setup.exe",
                "url": "https://example.com/setup.exe",
                "size": 123,
                "checksums_url": "https://example.com/SHA256SUMS",
            },
        })


def test_update_refuses_during_active_session(dash_env, monkeypatch):
    # README/Updates page: "It never updates in the middle of a focus
    # session."
    from focuscore import focus as focus_mod

    focus_mod.start_session("Deep work", 60, db_path=str(dash_env["db"]))
    _mock_update_available(monkeypatch)
    resp = dash_env["client"].post("/update/start")
    assert resp.status_code == 400
    assert "Can't update right now" in resp.get_data(as_text=True)


def test_update_flow_never_touches_user_data(dash_env, monkeypatch):
    # Updates page: "Your data is never touched by the update."
    import focuscore.updater as updater_mod

    _mock_update_available(monkeypatch)
    monkeypatch.setattr(updater_mod, "download_installer",
                        lambda *a, **k: None)
    monkeypatch.setattr(updater_mod, "write_pending_install",
                        lambda *a, **k: None)
    before = dash_env["db"].read_bytes()
    resp = dash_env["client"].post("/update/start")
    assert resp.status_code == 200
    assert dash_env["db"].read_bytes() == before


_CLAIM_RE = re.compile(
    r"^-\s*\[(TESTED|MANUAL|HEDGED)"
    r"(\+(TESTED|MANUAL|HEDGED))*\]")


def _claim_lines():
    text = _claims_path().read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if ln.startswith("- [")]


def test_register_has_no_bare_claims():
    # Roadmap 1.18, decision 1: every claim line carries a status and
    # names its surface + evidence. This is the habit, enforced.
    lines = _claim_lines()
    assert lines, "claims.md has no claim lines at all"
    bad = [ln for ln in lines
           if not _CLAIM_RE.match(ln)
           or "| surface:" not in ln
           or "| evidence:" not in ln]
    assert not bad, f"bare claims without status/surface/evidence: {bad!r}"


def test_register_covers_minimum_claims():
    # The register must not shrink silently below the seeded inventory.
    assert len(_claim_lines()) >= 20
