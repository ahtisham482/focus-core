"""Tests for focuscore/updater.py (Sprint 2, Phase 3: one-click updates)."""

import io
import json
import urllib.error

import pytest

from focuscore import paths, updater


@pytest.fixture()
def app_root(tmp_path, monkeypatch):
    """Point paths.APP_ROOT at an empty folder (portable layout)."""
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    return tmp_path


def write_update_info(app_root, repo="someone/focus-core",
                      version="1.3.0"):
    (app_root / "update-info.json").write_text(
        json.dumps({"repo": repo, "version": version}))


# --- version parsing -------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("v1.4.0", (1, 4, 0)),
    ("1.4.0", (1, 4, 0)),
    ("V2.0", (2, 0)),
    ("1.3.0", (1, 3, 0)),
    ("v10.2.11", (10, 2, 11)),
    ("1", None),
    ("", None),
    ("v1.x.0", None),
    ("latest", None),
    (None, None),
    (123, None),
])
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


@pytest.mark.parametrize("current,latest,expected", [
    ("1.3.0", "v1.4.0", True),
    ("1.3.0", "1.3.0", False),
    ("1.4.0", "1.3.0", False),
    ("1.3.0", "v1.3.1", True),
    ("1.9.9", "v2.0.0", True),
    ("garbage", "v1.4.0", False),
    ("1.3.0", "garbage", False),
])
def test_is_newer(current, latest, expected):
    assert updater.is_newer(current, latest) is expected


# --- update-info -----------------------------------------------------


def test_get_update_info_ok(app_root):
    write_update_info(app_root)
    assert updater.get_update_info() == {"repo": "someone/focus-core",
                                        "version": "1.3.0"}


def test_get_update_info_missing_is_dev_copy(app_root):
    assert updater.get_update_info() is None


def test_get_update_info_bad_json_is_dev_copy(app_root):
    (app_root / "update-info.json").write_text("not json{")
    assert updater.get_update_info() is None


def test_get_update_info_empty_repo_is_dev_copy(app_root):
    write_update_info(app_root, repo="")
    assert updater.get_update_info() is None


# --- latest release --------------------------------------------------


def fake_release(tag="v1.4.0", assets=True):
    payload = {"tag_name": tag, "assets": []}
    if assets:
        payload["assets"] = [
            {"name": "FocusCore-Setup-1.4.0.exe",
             "browser_download_url": "https://example.com/setup.exe",
             "size": 24200000},
            {"name": "notes.txt",
             "browser_download_url": "https://example.com/notes.txt",
             "size": 10},
        ]
    return payload


def test_latest_release_picks_installer_asset(monkeypatch):
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: fake_release())
    tag, name, url, size = updater.latest_release("someone/focus-core")
    assert tag == "v1.4.0"
    assert name == "FocusCore-Setup-1.4.0.exe"
    assert url == "https://example.com/setup.exe"
    assert size == 24200000


def test_latest_release_no_asset_raises(monkeypatch):
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: fake_release(assets=False))
    with pytest.raises(updater.UpdateError):
        updater.latest_release("someone/focus-core")


def test_latest_release_private_repo_raises(monkeypatch):
    def boom(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(updater, "_http_get_json", boom)
    with pytest.raises(updater.UpdateError, match="private"):
        updater.latest_release("someone/focus-core")


def test_latest_release_offline_raises(monkeypatch):
    def boom(url):
        raise urllib.error.URLError("nope")
    monkeypatch.setattr(updater, "_http_get_json", boom)
    with pytest.raises(updater.UpdateError, match="[Ii]nternet"):
        updater.latest_release("someone/focus-core")


# --- check_for_update ------------------------------------------------


def test_check_dev_copy_never_touches_network(app_root, monkeypatch):
    def boom(url):
        raise AssertionError("network should not be used")
    monkeypatch.setattr(updater, "_http_get_json", boom)
    assert updater.check_for_update() == {"status": "dev-copy"}


def test_check_update_available(app_root, monkeypatch):
    write_update_info(app_root, version="1.3.0")
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: fake_release("v1.4.0"))
    result = updater.check_for_update()
    assert result["status"] == "ok"
    assert result["update_available"] is True
    assert result["latest"] == "v1.4.0"
    assert result["asset"]["name"] == "FocusCore-Setup-1.4.0.exe"


def test_check_up_to_date(app_root, monkeypatch):
    write_update_info(app_root, version="1.4.0")
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: fake_release("v1.4.0"))
    result = updater.check_for_update()
    assert result["status"] == "ok"
    assert result["update_available"] is False


def test_check_caches_result(app_root, monkeypatch):
    write_update_info(app_root, version="1.3.0")
    calls = []
    monkeypatch.setattr(
        updater, "_http_get_json",
        lambda url: calls.append(url) or fake_release("v1.4.0"))
    first = updater.check_for_update()
    second = updater.check_for_update()
    assert calls and len(calls) == 1  # second call served from cache
    assert first == second


def test_check_force_refreshes(app_root, monkeypatch):
    write_update_info(app_root, version="1.3.0")
    calls = []
    monkeypatch.setattr(
        updater, "_http_get_json",
        lambda url: calls.append(url) or fake_release("v1.4.0"))
    updater.check_for_update()
    updater.check_for_update(force=True)
    assert len(calls) == 2


def test_check_error_is_graceful(app_root, monkeypatch):
    write_update_info(app_root, version="1.3.0")
    def boom(url):
        raise urllib.error.URLError("nope")
    monkeypatch.setattr(updater, "_http_get_json", boom)
    result = updater.check_for_update()
    assert result["status"] == "error"
    assert result["current"] == "1.3.0"
    assert result["error"]


# --- download --------------------------------------------------------


class _FakeResponse:
    def __init__(self, data):
        self._buf = io.BytesIO(data)

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_installer_ok(tmp_path, monkeypatch):
    data = b"x" * 1000
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResponse(data))
    dest = tmp_path / "setup.exe"
    assert updater.download_installer("https://example.com/s.exe", dest,
                                      expected_size=1000) == dest
    assert dest.read_bytes() == data


def test_download_size_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(b"x" * 10))
    with pytest.raises(updater.UpdateError, match="[Ii]ncomplete"):
        updater.download_installer("https://example.com/s.exe",
                                   tmp_path / "setup.exe",
                                   expected_size=1000)


# --- pending flag ----------------------------------------------------


def test_pending_flag_roundtrip(app_root):
    assert updater.take_pending_install() is None
    updater.write_pending_install(r"C:\Temp\setup.exe", "v1.4.0")
    pending = updater.take_pending_install()
    assert pending["installer"] == r"C:\Temp\setup.exe"
    assert pending["version"] == "v1.4.0"
    # taking clears it
    assert updater.take_pending_install() is None


def test_update_launcher_bat(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    bat = updater.write_update_launcher(r"C:\Temp\FocusCore-Setup.exe")
    text = bat.read_text(encoding="utf-8")
    assert "FocusCore-Setup.exe" in text
    assert "/SILENT" in text
    assert "timeout" in text
