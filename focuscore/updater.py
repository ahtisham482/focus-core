"""One-click updates for installed copies (Sprint 2, Phase 3).

Installed copies carry ``update-info.json`` (stamped by installer/build.py):
``{"repo": "owner/name", "version": "1.4.0"}``. They check the GitHub
Releases API for a newer version, download the new
``FocusCore-Setup-<ver>.exe``, make a safety backup, and hand off to the
installer, which upgrades in place and reopens the app.

Portable/dev copies (no update-info.json) don't self-update: the update
page says so plainly.

Notes:
- The check is quiet and never blocks startup: launcher.py runs it in a
  background thread and caches the result in the data dir.
- The actual handoff (spawn installer, quit app) happens in the tray
  process: the dashboard writes a ``.update-pending.json`` flag and the
  tray's watcher thread picks it up within seconds.
- GitHub's API answers without authentication only for PUBLIC repos.
  For a private repo the check fails gracefully ("couldn't check") and
  the update page says so.
"""

import hashlib
import hmac
import json
import logging
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import paths

logger = logging.getLogger(__name__)

UPDATE_INFO_NAME = "update-info.json"
CHECK_CACHE_NAME = ".update-check.json"
PENDING_NAME = ".update-pending.json"
API_URL = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "FocusCore-Updater"
REQUEST_TIMEOUT = 20
CHECK_TTL_SECONDS = 24 * 3600
INSTALLER_PREFIX = "FocusCore-Setup-"
CHECKSUMS_NAME = "SHA256SUMS"


class UpdateError(Exception):
    """The update check or download failed (network, API, bad data)."""


def get_update_info():
    """``{"repo": ..., "version": ...}`` or None for dev/portable copies."""
    info_file = paths.APP_ROOT / UPDATE_INFO_NAME
    if not info_file.exists():
        return None
    try:
        info = json.loads(info_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not info.get("repo") \
            or not info.get("version"):
        return None
    return {"repo": info["repo"], "version": info["version"]}


def parse_version(text):
    """``"v1.4.0"`` -> ``(1, 4, 0)``. Returns None for garbage."""
    if not isinstance(text, str):
        return None
    text = text.strip().lstrip("vV")
    parts = text.split(".")
    if not 2 <= len(parts) <= 4:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def is_newer(current, latest):
    """True when ``latest`` is a higher version than ``current``."""
    cur, lat = parse_version(current), parse_version(latest)
    if cur is None or lat is None:
        return False
    return lat > cur


def _http_get_json(url):
    """GET JSON with a browser-like user agent. Separated for tests."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT,
                 "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def latest_release(repo):
    """``(tag, asset_name, asset_url, asset_size, checksums_url)``.

    ``checksums_url`` is the download URL of the release's ``SHA256SUMS``
    asset, or None when the release has none. The updater refuses to
    install from a release without one (fail closed).

    Raises UpdateError when the API can't be reached, the repo is
    private, or no installer asset is attached.
    """
    try:
        data = _http_get_json(API_URL.format(repo=repo))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "Couldn't reach the releases page (repo private or "
                "renamed?).") from exc
        if exc.code == 403:
            raise UpdateError(
                "GitHub refused the check (rate limit?). Try again later."
            ) from exc
        raise UpdateError(
            "GitHub answered with an error (%s)." % exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(
            "No internet connection or GitHub is unreachable.") from exc
    except ValueError as exc:
        raise UpdateError("GitHub answered with bad data.") from exc
    if not isinstance(data, dict) or "tag_name" not in data:
        raise UpdateError("GitHub answered with bad data.")
    installer = None
    checksums_url = None
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        if name == CHECKSUMS_NAME:
            checksums_url = asset.get("browser_download_url")
        elif name.startswith(INSTALLER_PREFIX) and name.endswith(".exe"):
            installer = (data["tag_name"], name,
                         asset.get("browser_download_url"),
                         asset.get("size") or 0)
    if installer is None:
        raise UpdateError("The newest release has no installer attached yet.")
    return installer + (checksums_url,)


def _cache_file():
    return paths.data_dir() / CHECK_CACHE_NAME


def read_cached_check():
    """Last check result, or None. Never touches the network."""
    try:
        return json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_for_update(force=False):
    """Check for a newer release; result is cached for a day.

    Returns a dict: ``status`` is ``"ok"``, ``"dev-copy"`` or
    ``"error"``; on ``"ok"`` it also carries ``current``, ``latest``,
    ``update_available`` and ``asset`` (name/url/size).
    """
    info = get_update_info()
    if info is None:
        return {"status": "dev-copy"}
    if not force:
        cached = read_cached_check()
        # A cache written before SHA-256 verification existed has no
        # checksums_url in the asset dict — treat it as stale.
        if cached and isinstance(cached, dict) and \
                time.time() - cached.get("checked_at", 0) < CHECK_TTL_SECONDS \
                and cached.get("current") == info["version"] and (
                    cached.get("status") != "ok"
                    or "checksums_url" in (cached.get("asset") or {})):
            return cached
    try:
        tag, asset_name, asset_url, asset_size, checksums_url = \
            latest_release(info["repo"])
        result = {
            "status": "ok",
            "current": info["version"],
            "latest": tag,
            "update_available": is_newer(info["version"], tag),
            "asset": {"name": asset_name, "url": asset_url,
                      "size": asset_size, "checksums_url": checksums_url},
            "checked_at": time.time(),
        }
    except UpdateError as exc:
        result = {"status": "error", "error": str(exc),
                  "current": info["version"], "checked_at": time.time()}
    try:
        _cache_file().write_text(json.dumps(result), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not cache the update check result: %s",
                       exc)
    return result


def parse_checksums(body):
    """Parse a ``SHA256SUMS`` file body into ``{filename: hex_digest}``.

    Tolerates the standard variants: the ``*`` binary-mode marker, blank
    lines, and ``#`` comments. Garbage lines are skipped; returns {} for
    garbage input. ``body`` may be bytes or str.
    """
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    entries = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        bits = line.split(None, 1)
        if len(bits) != 2:
            continue
        digest, fname = bits
        fname = fname.lstrip(" *")
        if len(digest) != 64 or not fname:
            continue
        try:
            int(digest, 16)
        except ValueError:
            continue
        entries[fname] = digest.lower()
    return entries


def _fetch_checksums(checksums_url):
    """Fetch + parse the release's SHA256SUMS file. Raises UpdateError."""
    req = urllib.request.Request(checksums_url,
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req,
                                    timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(
            "Couldn't fetch the release's checksum file (%s)." % exc
        ) from exc
    entries = parse_checksums(body)
    if not entries:
        raise UpdateError(
            "The release's checksum file had no usable entries.")
    return entries


def _sha256_file(path):
    """SHA-256 hex digest of a file, read in chunks. Stdlib only."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discard(path):
    """Best-effort delete of an unverified download."""
    try:
        Path(path).unlink()
    except OSError as exc:
        logger.debug("could not delete unverified download %s: %s",
                     path, exc)


def download_installer(asset_url, dest_path, expected_size=0,
                       checksums_url=None):
    """Download the installer exe and verify its SHA-256. Raises UpdateError.

    Honest scope (roadmap 0.1): this detects accidental corruption and
    truncation only. It does NOT stop a compromised release — an attacker
    who owns the release replaces the exe AND the SHA256SUMS file together,
    and the hash check passes happily. The Authenticode verifier for that
    path exists (focuscore/authenticode.py, roadmap 1.4 partial); it is
    not enforced yet because no signing identity exists (roadmap 0.5).
    """
    dest_path = Path(dest_path)
    req = urllib.request.Request(asset_url,
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req,
                                    timeout=REQUEST_TIMEOUT) as resp, \
                open(dest_path, "wb") as out:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("The download failed (%s)." % exc) from exc
    got = dest_path.stat().st_size
    if got == 0:
        raise UpdateError("The download came back empty.")
    if expected_size and got != expected_size:
        _discard(dest_path)
        raise UpdateError(
            "The download looks incomplete (got %d of %d bytes)."
            % (got, expected_size))
    # Fail closed: no checksum file, no install — never silently skip the
    # check. The downloaded file is deleted, not left lying around.
    if not checksums_url:
        _discard(dest_path)
        raise UpdateError(
            "The release is missing its checksum file (SHA256SUMS), so "
            "the download can't be verified. Nothing was installed.")
    try:
        entries = _fetch_checksums(checksums_url)
    except UpdateError:
        _discard(dest_path)
        raise
    expected = entries.get(dest_path.name)
    if expected is None:
        _discard(dest_path)
        raise UpdateError(
            "The release's checksum file has no entry for %s, so the "
            "download can't be verified. Nothing was installed."
            % dest_path.name)
    if not hmac.compare_digest(_sha256_file(dest_path), expected):
        _discard(dest_path)
        raise UpdateError(
            "The download failed its integrity check (SHA-256 mismatch) "
            "— it may be corrupted. Nothing was installed.")
    return dest_path


def _pending_file():
    return paths.data_dir() / PENDING_NAME


def write_pending_install(installer_path, version):
    """Flag a downloaded update for the tray watcher to apply."""
    _pending_file().write_text(json.dumps({
        "installer": str(installer_path),
        "version": version,
        "at": time.time(),
    }), encoding="utf-8")


def take_pending_install():
    """Read and clear the pending-update flag; None when absent."""
    path = _pending_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("could not clear the pending-update marker: "
                       "%s", exc)
    if not isinstance(data, dict) or not data.get("installer"):
        return None
    return data


def write_update_launcher(installer_path):
    """Write a small bat that installs after we exit.

    The installer can't replace files while we're still running, so the
    bat waits a few seconds, runs the setup silently, then deletes
    itself. The caller launches it (detached) and then quits the app.
    Returns the bat path.
    """
    bat = Path(tempfile.gettempdir()) / (
        "focuscore-update-%s.bat" % datetime.now().strftime("%Y%m%d%H%M%S"))
    bat.write_text(
        "@echo off\r\n"
        "timeout /t 5 /nobreak >nul\r\n"
        'start "" "%s" /SILENT\r\n'
        'del "%%~f0"\r\n' % installer_path,
        encoding="utf-8",
    )
    return bat
