"""Acceptance tests for Roadmap 1.21: the update kill-switch.

The 1.12 machine policy ``updates_disabled`` (HKLM Features tier) pins
a fleet to one version. With the policy on, EVERY self-update path
must refuse before reaching the network: the direct check (plain and
forced), the launcher background check, the /update page (plain,
``?refresh=1``, and the offline cached-status branch), /update/start,
the installer download itself, and the tray's pending-install
application. A spy on the updater's network seams proves zero traffic;
positive controls with the policy off prove the same spy DOES fire, so
the negative result is real evidence, not a dead spy.
"""

import json
import os
import subprocess

import pytest

from focuscore import config, launcher, paths, store, tray, updater


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    """Hermetic config state: fake machine tier + tmp TOML + clean env.

    Mirrors tests/test_config.py: mutate ``cfg["machine"]`` to fake the
    HKLM tier, write ``cfg["toml"]`` to fake the user tier.
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


def policy_off(cfg):
    cfg["machine"].pop("features", None)


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
    """Spy on the updater's network seams; records instead of sending.

    ``latest_release``/``_http_get_json`` return canned data so a
    policy-OFF control can complete a full check. ``urlopen`` is the
    deep trap: reaching it means a real socket would have opened.
    """
    calls = []

    def fake_latest_release(repo):
        calls.append(("latest_release", repo))
        return ("v9.9.9", "FocusCore-Setup-9.9.9.exe",
                "https://example.invalid/FocusCore-Setup-9.9.9.exe",
                12345, "https://example.invalid/SHA256SUMS")

    def fake_http_get_json(url):
        calls.append(("_http_get_json", url))
        return {"tag_name": "v9.9.9", "assets": []}

    def trapped_urlopen(*args, **kwargs):
        calls.append(("urlopen", args[0] if args else None))
        raise AssertionError(
            "urlopen reached: a test touched the real network")

    monkeypatch.setattr(updater, "latest_release", fake_latest_release)
    monkeypatch.setattr(updater, "_http_get_json", fake_http_get_json)
    monkeypatch.setattr("urllib.request.urlopen", trapped_urlopen)
    return calls


def _spy_install(monkeypatch):
    """Record download_installer / write_pending_install calls."""
    calls = {"download": [], "pending": []}

    def fake_download(url, dest, *args, **kwargs):
        calls["download"].append((url, dest))
        return dest

    def fake_pending(installer, version):
        calls["pending"].append((installer, version))

    monkeypatch.setattr(updater, "download_installer", fake_download)
    monkeypatch.setattr(updater, "write_pending_install", fake_pending)
    return calls


def _tray_spies(monkeypatch):
    """Record the tray's apply seams: launcher writer, Popen, quit."""
    calls = {"launcher": [], "popen": [], "quit": []}
    monkeypatch.setattr(
        updater, "write_update_launcher",
        lambda exe: calls["launcher"].append(exe) or "C:\\T\\u.bat")

    class FakePopen:
        def __init__(self, *args, **kwargs):
            calls["popen"].append(args[0])

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        tray.TrayApp, "on_quit",
        lambda self: calls["quit"].append(True))
    return calls


# --- the check itself ---------------------------------------------------

def test_check_for_update_refused_under_policy(cfg, installed, net):
    policy_on(cfg)
    result = updater.check_for_update()
    assert result["status"] == "disabled-by-policy"
    assert result["current"] == "1.3.0"
    assert net == []
    # The refusal is never written to the check cache.
    assert updater.read_cached_check() is None


def test_check_for_update_forced_refused_under_policy(cfg, installed,
                                                      net):
    policy_on(cfg)
    result = updater.check_for_update(force=True)
    assert result["status"] == "disabled-by-policy"
    assert net == []


def test_policy_beats_fresh_cache(cfg, installed, net):
    # Policy OFF: a real check runs and caches an "update available".
    result = updater.check_for_update()
    assert result["status"] == "ok" and result["update_available"]
    assert net  # the spy fired -- the check really happened
    net.clear()

    policy_on(cfg)
    # A fresh cached "ok" is never served while the pin is on.
    assert updater.check_for_update()["status"] == "disabled-by-policy"
    assert updater.check_for_update(force=True)["status"] == (
        "disabled-by-policy")
    assert net == []


def test_policy_knob_is_the_112_feature_flag(cfg, installed, net):
    # The same refusal via the [features] TOML tier proves the
    # kill-switch is the 1.12 mechanism, not a parallel switch.
    cfg["toml"].write_text("[features]\nupdates_disabled = true\n",
                           encoding="utf-8")
    assert updater.check_for_update()["status"] == "disabled-by-policy"
    assert net == []


def test_positive_control_check_reaches_network_when_policy_off(
        cfg, installed, net):
    result = updater.check_for_update()
    assert result["status"] == "ok"
    assert result["update_available"] is True
    assert result["latest"] == "v9.9.9"
    assert [name for name, _ in net] == ["latest_release"]


# --- the launcher background check --------------------------------------

def test_background_check_makes_no_network_under_policy(
        cfg, installed, net, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_DB_PATH",
                        str(tmp_path / "bg.db"))
    policy_on(cfg)
    launcher._background_update_check()
    assert net == []


def test_background_check_reaches_network_when_policy_off(
        cfg, installed, net, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_DB_PATH",
                        str(tmp_path / "bg.db"))
    launcher._background_update_check()
    assert [name for name, _ in net] == ["latest_release"]


# --- the /update page ----------------------------------------------------

def test_update_page_shows_policy_card_and_no_network(
        dash, cfg, installed, net):
    policy_on(cfg)
    resp = dash.get("/update")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "disabled by your IT policy" in body
    # No actionable control may be rendered: no "Check again" link, no
    # update form, no automatic-checks toggle card.
    assert "/update?refresh=1" not in body
    assert "/update/start" not in body
    assert "Automatic update checks" not in body
    assert net == []


def test_update_page_refresh_refused_under_policy(dash, cfg, installed,
                                                  net):
    policy_on(cfg)
    resp = dash.get("/update?refresh=1")
    assert resp.status_code == 200
    assert "disabled by your IT policy" in resp.data.decode()
    assert net == []


def test_update_page_cached_branch_refused_under_policy(
        dash, cfg, installed, net):
    # Policy OFF, automatic checks on: a normal load checks + caches.
    resp = dash.get("/update")
    assert "Version v9.9.9 is available" in resp.data.decode()
    net.clear()
    # IT pins the fleet; the user turns automatic checks off. The page
    # must not render the stale "update available" card from cache.
    policy_on(cfg)
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update")
    body = resp.data.decode()
    assert "disabled by your IT policy" in body
    assert "Version v9.9.9 is available" not in body
    assert net == []


def test_positive_control_update_page_refresh_checks(
        dash, cfg, installed, net):
    resp = dash.get("/update?refresh=1")
    assert resp.status_code == 200
    assert "Version v9.9.9 is available" in resp.data.decode()
    assert "latest_release" in [name for name, _ in net]


# --- /update/start --------------------------------------------------------

def test_update_start_refused_under_policy(dash, cfg, installed, net,
                                           monkeypatch):
    policy_on(cfg)
    calls = _spy_install(monkeypatch)
    resp = dash.post("/update/start")
    assert resp.status_code == 403
    assert "disabled by your IT policy" in resp.data.decode()
    assert calls == {"download": [], "pending": []}
    assert net == []


def test_update_start_proceeds_when_policy_off(dash, cfg, installed,
                                               net, monkeypatch,
                                               tmp_path):
    from focuscore import backup
    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(backup, "backup_dir",
                        lambda dest_dir=None: bdir)
    store.init_db()
    calls = _spy_install(monkeypatch)
    resp = dash.post("/update/start")
    assert resp.status_code == 200
    assert "Updating to v9.9.9" in resp.data.decode()
    assert len(calls["download"]) == 1
    assert len(calls["pending"]) == 1
    assert calls["pending"][0][1] == "v9.9.9"
    assert "latest_release" in [name for name, _ in net]


# --- the download primitive ---------------------------------------------

def test_download_installer_refuses_under_policy(cfg, net, tmp_path):
    policy_on(cfg)
    dest = tmp_path / "FocusCore-Setup-9.9.9.exe"
    with pytest.raises(updater.UpdateError):
        updater.download_installer(
            "https://example.invalid/FocusCore-Setup-9.9.9.exe", dest,
            12345, "https://example.invalid/SHA256SUMS")
    assert not dest.exists()
    assert net == []


# --- the pending install queue -------------------------------------------

def test_take_pending_install_held_under_policy(cfg, app_root):
    updater.write_pending_install("C:\\T\\setup.exe", "v9.9.9")
    policy_on(cfg)
    assert updater.take_pending_install() is None
    # The flag is left in place: lifting the policy resumes normally.
    policy_off(cfg)
    pending = updater.take_pending_install()
    assert pending["version"] == "v9.9.9"


def test_tray_apply_pending_skips_under_policy(cfg, monkeypatch):
    policy_on(cfg)
    calls = _tray_spies(monkeypatch)
    app = tray.TrayApp()
    app._apply_pending_update({"installer": "C:\\T\\setup.exe",
                               "version": "v9.9.9"})
    assert calls == {"launcher": [], "popen": [], "quit": []}


def test_tray_apply_pending_runs_when_policy_off(cfg, monkeypatch):
    calls = _tray_spies(monkeypatch)
    app = tray.TrayApp()
    app._apply_pending_update({"installer": "C:\\T\\setup.exe",
                               "version": "v9.9.9"})
    assert calls["launcher"] == ["C:\\T\\setup.exe"]
    assert calls["popen"] and "u.bat" in str(calls["popen"][0][-1])
    assert calls["quit"] == [True]


def test_home_update_card_suppressed_under_policy(cfg, installed, net,
                                                  tmp_path):
    # Seed a fresh cached "update available" with the policy off.
    assert updater.check_for_update()["update_available"]
    from focuscore import home
    db = str(tmp_path / "home.db")
    store.init_db(db)
    codes_off = [c["code"] for c in home.attention_cards(db_path=db)]
    assert "update" in codes_off

    net.clear()
    policy_on(cfg)
    codes_on = [c["code"] for c in home.attention_cards(db_path=db)]
    assert "update" not in codes_on
    assert net == []


def test_tray_watcher_skips_pending_under_policy(cfg, monkeypatch):
    policy_on(cfg)
    calls = _tray_spies(monkeypatch)
    taken = []

    def fake_take():
        taken.append(True)
        return {"installer": "C:\\T\\setup.exe", "version": "v9.9.9"}

    monkeypatch.setattr(updater, "take_pending_install", fake_take)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    app = tray.TrayApp()
    app._watch_for_update()
    assert taken == [True]
    assert calls == {"launcher": [], "popen": [], "quit": []}
