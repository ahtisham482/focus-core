"""Acceptance tests for Roadmap 2.1: update rollback + failure recovery.

Two halves, both proven at the Python level (the bat itself is Windows
evidence -- only its TEXT is asserted here, never executed):

- Previous-installer slot: downloads are tracked (version + path) when a
  download completes; once the app is actually RUNNING a downloaded
  version, that version's installer is copied into the data-dir slot.
  The /update page then offers "Revert to previous version", which
  queues the slot installer through the normal pending-install path.
  A download that was never applied must never fabricate a "previous".
- Bat failure recovery: the update bat runs the installer attached so
  its exit code is real; on non-zero it writes a failure marker into the
  data dir and relaunches the old app. /update shows the marker once
  ("The update didn't finish...") and clears it.

The 1.21 kill-switch keeps pinning every path: revert refuses under the
``updates_disabled`` machine policy, exactly like /update/start.
"""

import json
import os
from pathlib import Path

import pytest

from focuscore import config, paths, store, updater


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    """Hermetic config state: fake machine tier + tmp TOML + clean env.

    Mirrors tests/test_update_kill_switch_121.py.
    """
    for var in [v for v in os.environ if v.startswith("FOCUSCORE_")]:
        monkeypatch.delenv(var, raising=False)
    state = {"machine": {}, "toml": tmp_path / "config.toml"}
    monkeypatch.setattr(
        config, "_machine_reader", lambda: state["machine"])
    monkeypatch.setattr(
        config, "config_file_path", lambda: state["toml"])
    return state


def policy_on(cfg):
    cfg["machine"]["features"] = {"updates_disabled": 1}


@pytest.fixture()
def app_root(tmp_path, monkeypatch):
    """Point paths.APP_ROOT at an empty folder (portable layout)."""
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    return tmp_path


def write_update_info(app_root, repo="someone/focus-core",
                      version="1.3.0"):
    (app_root / "update-info.json").write_text(
        json.dumps({"repo": repo, "version": version}))


@pytest.fixture()
def installed(app_root):
    """An installed copy: update-info.json present (not a dev copy)."""
    write_update_info(app_root)
    return app_root


@pytest.fixture()
def net(monkeypatch):
    """Fake the updater's release lookup; trap any real socket use."""
    calls = []

    def fake_latest_release(repo):
        calls.append(("latest_release", repo))
        return ("v9.9.9", "FocusCore-Setup-9.9.9.exe",
                "https://example.invalid/FocusCore-Setup-9.9.9.exe",
                12345, "https://example.invalid/SHA256SUMS")

    def trapped_urlopen(*args, **kwargs):
        calls.append(("urlopen", args[0] if args else None))
        raise AssertionError(
            "urlopen reached: a test touched the real network")

    monkeypatch.setattr(updater, "latest_release", fake_latest_release)
    monkeypatch.setattr("urllib.request.urlopen", trapped_urlopen)
    return calls


def make_installer(tmp_path, version, payload):
    """A fake downloaded installer file, named like a real release."""
    path = tmp_path / "downloads" / f"FocusCore-Setup-{version}.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def seed_previous(app_root, current="1.4.0", previous="1.3.0",
                  payload=b"OLD-INSTALLER"):
    """Run the honest dance: download ``previous`` while on an older
    version, run it, then download ``current`` -- the slot settles with
    the ``previous`` installer, and the app is left running ``current``.
    Returns the slot dict from ``updater.previous_installer()``.
    """
    write_update_info(app_root, version="1.2.0")
    first = make_installer(app_root, previous, payload)
    updater.track_download(first, "v" + previous)
    write_update_info(app_root, version=previous)
    second = make_installer(app_root, current, b"NEW-INSTALLER")
    updater.track_download(second, "v" + current)
    write_update_info(app_root, version=current)
    return updater.previous_installer()


# --- previous-installer rotation ----------------------------------------

def test_two_downloads_settle_previous_slot(app_root):
    write_update_info(app_root, version="1.2.0")
    first = make_installer(app_root, "1.3.0", b"INSTALLER-A")
    updater.track_download(first, "v1.3.0")
    # Downloaded but not yet running: nothing may settle yet.
    assert updater.previous_installer() is None

    write_update_info(app_root, version="1.3.0")  # app now runs A
    second = make_installer(app_root, "1.4.0", b"INSTALLER-B")
    updater.track_download(second, "v1.4.0")

    previous = updater.previous_installer()
    assert previous is not None
    assert previous["version"] == "v1.3.0"
    slot = Path(previous["installer"])
    assert slot.exists()
    assert slot.read_bytes() == b"INSTALLER-A"  # byte-identical copy
    assert slot.name == "FocusCore-Setup-1.3.0.exe"
    assert slot.parent.parent == paths.data_dir()


def test_download_never_applied_no_fabricated_previous(app_root):
    write_update_info(app_root, version="1.2.0")
    first = make_installer(app_root, "1.3.0", b"INSTALLER-A")
    updater.track_download(first, "v1.3.0")
    second = make_installer(app_root, "1.4.0", b"INSTALLER-B")
    updater.track_download(second, "v1.4.0")
    # Still running 1.2.0: neither download ever produced this version.
    assert updater.previous_installer() is None


def test_previous_installer_none_without_marker(app_root):
    write_update_info(app_root, version="1.3.0")
    assert updater.previous_installer() is None


def test_previous_installer_none_when_file_deleted(app_root):
    previous = seed_previous(app_root)
    Path(previous["installer"]).unlink()
    assert updater.previous_installer() is None


# --- the Revert flow ------------------------------------------------------

def test_revert_queues_pending_for_previous(dash, cfg, installed):
    store.init_db()
    previous = seed_previous(installed)
    assert previous is not None

    resp = dash.post("/update/revert")
    assert resp.status_code == 200
    assert "Reverting to" in resp.data.decode()

    pending = updater.take_pending_install()
    assert pending is not None
    assert pending["installer"] == previous["installer"]
    assert pending["version"] == previous["version"]


def test_revert_without_previous_is_clean_refusal(dash, cfg, installed):
    store.init_db()
    resp = dash.post("/update/revert")
    assert resp.status_code == 400
    assert updater.take_pending_install() is None


def test_revert_refused_when_previous_is_running(dash, cfg, installed):
    store.init_db()
    # Settle the slot, but the app still runs the slot's own version:
    # there is nothing older to go back to.
    previous = seed_previous(installed, current="1.3.0",
                             previous="1.3.0", payload=b"SAME")
    assert previous is not None
    write_update_info(installed, version="1.3.0")
    resp = dash.post("/update/revert")
    assert resp.status_code == 400
    assert updater.take_pending_install() is None


def test_revert_refused_under_policy(dash, cfg, installed):
    store.init_db()
    previous = seed_previous(installed)
    assert previous is not None
    policy_on(cfg)
    resp = dash.post("/update/revert")
    assert resp.status_code == 403
    assert "disabled by your IT policy" in resp.data.decode()
    # No pending written: take it with the policy lifted to prove the
    # flag itself was never queued (the take is policy-gated too).
    cfg["machine"].pop("features", None)
    assert updater.take_pending_install() is None


def test_revert_refused_during_active_session(dash, cfg, installed):
    store.init_db()
    previous = seed_previous(installed)
    assert previous is not None
    from focuscore import focus as focus_mod
    focus_mod.start_session("Deep work", 60,
                            db_path=store.DEFAULT_DB_PATH)
    resp = dash.post("/update/revert")
    assert resp.status_code == 400
    assert updater.take_pending_install() is None


def test_revert_button_shown_only_with_valid_previous(dash, cfg,
                                                      installed, net):
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update")
    assert resp.status_code == 200
    assert "/update/revert" not in resp.data.decode()

    seed_previous(installed)
    resp = dash.get("/update")
    body = resp.data.decode()
    assert "/update/revert" in body
    assert "Revert to previous version" in body


# --- the update bat: failure recovery (content only) ---------------------

def test_update_launcher_bat_captures_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    bat = updater.write_update_launcher(
        r"C:\Temp\FocusCore-Setup-1.5.0.exe")
    text = bat.read_text(encoding="utf-8")
    # Happy path intact: wait for the app to exit, silent install.
    assert "timeout" in text
    assert "/SILENT" in text
    assert "FocusCore-Setup-1.5.0.exe" in text
    # Failure branch: real exit code, marker into the data dir naming
    # the attempted version, relaunch of the old app exactly the way
    # installer.iss launches it.
    assert "errorlevel" in text.lower()
    assert updater.FAILURE_NAME in text
    assert '"version": "1.5.0"' in text
    assert "-m focuscore.launcher" in text
    # The installer runs attached (no detached `start`): only then is
    # its exit code real.
    assert 'start "" "C:' not in text


# --- the failure marker on /update ---------------------------------------

def test_failure_marker_helpers_roundtrip(app_root):
    assert updater.read_update_failure() is None
    marker = paths.data_dir() / updater.FAILURE_NAME
    marker.write_text(json.dumps(
        {"version": "1.5.0", "installer": "FocusCore-Setup-1.5.0.exe"}),
        encoding="utf-8")
    failure = updater.read_update_failure()
    assert failure["version"] == "1.5.0"
    updater.clear_update_failure()
    assert updater.read_update_failure() is None
    assert not marker.exists()


def test_update_failure_marker_rendered_once(dash, cfg, installed):
    store.set_setting("update_check_enabled", "0")
    marker = paths.data_dir() / updater.FAILURE_NAME
    marker.write_text(json.dumps(
        {"version": "1.5.0", "installer": "FocusCore-Setup-1.5.0.exe"}),
        encoding="utf-8")

    resp = dash.get("/update")
    body = resp.data.decode()
    assert resp.status_code == 200
    assert "didn't finish" in body
    assert "1.3.0" in body  # the running version, named honestly
    assert "Nothing was changed" in body

    # Shown once: the marker is cleared, the second render is clean.
    assert not marker.exists()
    resp = dash.get("/update")
    assert "didn't finish" not in resp.data.decode()
