"""Tests for focuscore/updater.py (Sprint 2, Phase 3: one-click updates)."""

import hashlib
import io
import json
import time
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
                                        "version": "1.3.0",
                                        "flavor": "user"}


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
            {"name": "SHA256SUMS",
             "browser_download_url": "https://example.com/SHA256SUMS",
             "size": 100},
            {"name": "notes.txt",
             "browser_download_url": "https://example.com/notes.txt",
             "size": 10},
        ]
    return payload


def test_latest_release_picks_installer_asset(monkeypatch):
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: fake_release())
    tag, name, url, size, checksums_url = updater.latest_release(
        "someone/focus-core")
    assert tag == "v1.4.0"
    assert name == "FocusCore-Setup-1.4.0.exe"
    assert url == "https://example.com/setup.exe"
    assert size == 24200000
    assert checksums_url == "https://example.com/SHA256SUMS"


def test_latest_release_no_checksums_asset_gives_none(monkeypatch):
    payload = {"tag_name": "v1.4.0", "assets": [
        {"name": "FocusCore-Setup-1.4.0.exe",
         "browser_download_url": "https://example.com/setup.exe",
         "size": 24200000},
    ]}
    monkeypatch.setattr(updater, "_http_get_json", lambda url: payload)
    _tag, _name, _url, _size, checksums_url = updater.latest_release(
        "someone/focus-core")
    assert checksums_url is None


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


# (happy-path download is covered by test_download_hash_ok below —
#  checksums_url is now required for a successful download)


def test_download_size_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(b"x" * 10))
    with pytest.raises(updater.UpdateError, match="[Ii]ncomplete"):
        updater.download_installer("https://example.com/s.exe",
                                   tmp_path / "setup.exe",
                                   expected_size=1000)


def test_download_size_mismatch_deletes_partial_file(tmp_path, monkeypatch):
    """A size-rejected download must not litter the temp dir."""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(b"x" * 10))
    dest = tmp_path / "setup.exe"
    with pytest.raises(updater.UpdateError, match="[Ii]ncomplete"):
        updater.download_installer("https://example.com/s.exe", dest,
                                   expected_size=1000)
    assert not dest.exists()


# --- SHA-256 verification (roadmap 0.1) --------------------------------


def _sha256sums(entries):
    """Build a SHA256SUMS body from {filename: bytes}."""
    lines = []
    for name, data in entries.items():
        lines.append("%s  %s" % (hashlib.sha256(data).hexdigest(), name))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _fake_urlopen_files(files):
    """Route urlopen by URL to canned byte bodies."""
    def fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url not in files:
            raise urllib.error.URLError("unknown url: %s" % url)
        return _FakeResponse(files[url])
    return fake


def test_download_hash_ok(tmp_path, monkeypatch):
    data = b"x" * 1000
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_files({
            "https://example.com/s.exe": data,
            "https://example.com/SHA256SUMS":
                _sha256sums({"setup.exe": data}),
        }))
    dest = tmp_path / "setup.exe"
    assert updater.download_installer(
        "https://example.com/s.exe", dest, expected_size=1000,
        checksums_url="https://example.com/SHA256SUMS") == dest
    assert dest.read_bytes() == data  # verified file is kept


def test_download_hash_mismatch_raises_and_deletes(tmp_path, monkeypatch):
    good = b"x" * 1000
    bad = b"x" * 999 + b"y"  # one flipped byte, same size
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_files({
            "https://example.com/s.exe": bad,
            "https://example.com/SHA256SUMS":
                _sha256sums({"setup.exe": good}),
        }))
    dest = tmp_path / "setup.exe"
    with pytest.raises(updater.UpdateError, match="[Ii]ntegrity check"):
        updater.download_installer(
            "https://example.com/s.exe", dest, expected_size=1000,
            checksums_url="https://example.com/SHA256SUMS")
    assert not dest.exists()  # tampered/corrupt file is not left behind


def test_download_missing_checksums_url_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(b"x" * 1000))
    dest = tmp_path / "setup.exe"
    with pytest.raises(updater.UpdateError, match="[Cc]hecksum"):
        updater.download_installer("https://example.com/s.exe", dest,
                                   expected_size=1000,
                                   checksums_url=None)
    assert not dest.exists()


def test_download_checksums_missing_entry_fails_closed(tmp_path, monkeypatch):
    data = b"x" * 1000
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_files({
            "https://example.com/s.exe": data,
            "https://example.com/SHA256SUMS":
                _sha256sums({"other-file.exe": data}),
        }))
    dest = tmp_path / "setup.exe"
    with pytest.raises(updater.UpdateError, match="[Cc]hecksum"):
        updater.download_installer(
            "https://example.com/s.exe", dest, expected_size=1000,
            checksums_url="https://example.com/SHA256SUMS")
    assert not dest.exists()


def test_download_checksums_fetch_failure_fails_closed(tmp_path, monkeypatch):
    data = b"x" * 1000

    def fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url == "https://example.com/SHA256SUMS":
            raise urllib.error.URLError("nope")
        return _FakeResponse(data)

    monkeypatch.setattr("urllib.request.urlopen", fake)
    dest = tmp_path / "setup.exe"
    with pytest.raises(updater.UpdateError, match="[Cc]hecksum"):
        updater.download_installer(
            "https://example.com/s.exe", dest, expected_size=1000,
            checksums_url="https://example.com/SHA256SUMS")
    assert not dest.exists()


def test_parse_checksums_tolerates_format_variants():
    data = b"hello"
    digest = hashlib.sha256(data).hexdigest()
    body = (
        "# a comment line\n"
        "\n"
        "%s *setup.exe\n"        # binary-mode marker
        "%s  other.exe\n"        # text-mode marker
        % (digest, digest)
    )
    entries = updater.parse_checksums(body)
    assert entries == {"setup.exe": digest, "other.exe": digest}


def test_parse_checksums_ignores_garbage_lines():
    entries = updater.parse_checksums(
        b"not a checksum line\nzzzz  bad.exe\n")
    assert entries == {}


def test_check_carries_checksums_url(app_root, monkeypatch):
    write_update_info(app_root, version="1.3.0")
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: fake_release("v1.4.0"))
    result = updater.check_for_update()
    assert result["asset"]["checksums_url"] == \
        "https://example.com/SHA256SUMS"


def _write_cache(app_root, payload):
    (app_root / updater.CHECK_CACHE_NAME).write_text(json.dumps(payload))


def test_check_old_cache_without_checksums_url_is_stale(
        app_root, monkeypatch):
    """A cache written before 0.1 must be re-checked, not trusted."""
    write_update_info(app_root, version="1.3.0")
    _write_cache(app_root, {
        "status": "ok", "current": "1.3.0", "latest": "v1.4.0",
        "update_available": True,
        "asset": {"name": "FocusCore-Setup-1.4.0.exe",
                  "url": "https://example.com/setup.exe",
                  "size": 24200000},  # no checksums_url: old format
        "checked_at": time.time(),
    })
    calls = []
    monkeypatch.setattr(
        updater, "_http_get_json",
        lambda url: calls.append(url) or fake_release("v1.4.0"))
    result = updater.check_for_update()
    assert calls  # stale -> the API was hit again
    assert result["asset"]["checksums_url"] == \
        "https://example.com/SHA256SUMS"


def test_check_asset_null_cache_is_stale_not_crash(
        app_root, monkeypatch):
    """A hand-corrupted cache ("asset": null) must not TypeError."""
    write_update_info(app_root, version="1.3.0")
    _write_cache(app_root, {
        "status": "ok", "current": "1.3.0", "latest": "v1.4.0",
        "update_available": True, "asset": None,
        "checked_at": time.time(),
    })
    calls = []
    monkeypatch.setattr(
        updater, "_http_get_json",
        lambda url: calls.append(url) or fake_release("v1.4.0"))
    result = updater.check_for_update()
    assert calls  # stale -> re-checked instead of crashing
    assert result["asset"]["checksums_url"] == \
        "https://example.com/SHA256SUMS"


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


# --------------------------------------- Roadmap 2.6: flavor-aware updates --

def test_get_update_info_surfaces_flavor_machine(app_root):
    (app_root / "update-info.json").write_text(
        json.dumps({"repo": "someone/focus-core", "version": "1.3.0",
                    "flavor": "machine"}))
    assert updater.get_update_info() == {"repo": "someone/focus-core",
                                        "version": "1.3.0",
                                        "flavor": "machine"}


def test_get_update_info_defaults_flavor_user(app_root):
    # Installs stamped before 2.6 have no flavor key: they are user
    # installs and must keep updating exactly as before.
    write_update_info(app_root)
    assert updater.get_update_info()["flavor"] == "user"


def _flavored_release():
    return {"tag_name": "v1.4.0", "assets": [
        {"name": "FocusCore-Setup-1.4.0.exe",
         "browser_download_url": "https://example.com/setup-user.exe",
         "size": 24200000},
        {"name": "FocusCore-Setup-1.4.0-machine.exe",
         "browser_download_url": "https://example.com/setup-machine.exe",
         "size": 24300000},
        {"name": "FocusCore-Setup-1.4.0-offline.exe",
         "browser_download_url": "https://example.com/setup-offline.exe",
         "size": 24200001},
        {"name": "SHA256SUMS",
         "browser_download_url": "https://example.com/SHA256SUMS"},
    ]}


def test_latest_release_picks_machine_asset_for_machine_flavor(monkeypatch):
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: _flavored_release())
    _tag, name, url, _size, _sums = updater.latest_release(
        "someone/focus-core", flavor="machine")
    assert name == "FocusCore-Setup-1.4.0-machine.exe"
    assert url == "https://example.com/setup-machine.exe"


def test_latest_release_picks_user_asset_for_user_flavor(monkeypatch):
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: _flavored_release())
    _tag, name, _url, _size, _sums = updater.latest_release(
        "someone/focus-core", flavor="user")
    # Never the machine installer for a user install (which of the
    # two user assets wins is the pre-existing last-match order).
    assert name in ("FocusCore-Setup-1.4.0.exe",
                    "FocusCore-Setup-1.4.0-offline.exe")


def test_latest_release_offline_asset_counts_as_user_flavor(monkeypatch):
    payload = {"tag_name": "v1.4.0", "assets": [
        {"name": "FocusCore-Setup-1.4.0-offline.exe",
         "browser_download_url": "https://example.com/setup-offline.exe",
         "size": 1},
        {"name": "SHA256SUMS",
         "browser_download_url": "https://example.com/SHA256SUMS"},
    ]}
    monkeypatch.setattr(updater, "_http_get_json", lambda url: payload)
    _tag, name, _u, _s, _c = updater.latest_release(
        "someone/focus-core", flavor="user")
    assert name == "FocusCore-Setup-1.4.0-offline.exe"


def test_latest_release_machine_flavor_without_machine_asset_raises(
        monkeypatch):
    payload = {"tag_name": "v1.4.0", "assets": [
        {"name": "FocusCore-Setup-1.4.0.exe",
         "browser_download_url": "https://example.com/setup.exe",
         "size": 1},
    ]}
    monkeypatch.setattr(updater, "_http_get_json", lambda url: payload)
    with pytest.raises(updater.UpdateError):
        updater.latest_release("someone/focus-core", flavor="machine")


def test_check_for_update_uses_machine_asset_for_machine_install(
        monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        updater, "get_update_info",
        lambda: {"repo": "r", "version": "1.3.0", "flavor": "machine"})
    monkeypatch.setattr(updater, "_http_get_json",
                        lambda url: _flavored_release())
    result = updater.check_for_update(force=True)
    assert result["status"] == "ok"
    assert result["asset"]["name"] == "FocusCore-Setup-1.4.0-machine.exe"


@pytest.mark.parametrize("name,expected", [
    ("FocusCore-Setup-1.4.0.exe", "1.4.0"),
    ("FocusCore-Setup-1.4.0-machine.exe", "1.4.0"),
    ("FocusCore-Setup-1.4.0-offline.exe", "1.4.0"),
    ("FocusCore-Setup-1.4.0-machine-offline.exe", "1.4.0"),
    ("weird-name.exe", "weird-name"),
])
def test_attempted_version_from_name_strips_flavor_suffixes(name, expected):
    assert updater.attempted_version_from_name(name) == expected
