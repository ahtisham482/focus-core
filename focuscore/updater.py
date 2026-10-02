"""One-click updates for installed copies (Sprint 2, Phase 3).

Installed copies carry ``update-info.json`` (stamped by installer/build.py):
``{"repo": "owner/name", "version": "1.4.0", "flavor": "user"}``.
``flavor`` is ``"machine"`` for per-machine installs (Roadmap 2.6); the
updater then picks the ``-machine`` setup exe from the release, never
the per-user one. They check the GitHub
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
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import config, paths

logger = logging.getLogger(__name__)

UPDATE_INFO_NAME = "update-info.json"
CHECK_CACHE_NAME = ".update-check.json"
PENDING_NAME = ".update-pending.json"
# Roadmap 2.1 rollback state, all in the data dir: the newest tracked
# download (its installer kept as a durable copy under downloads/,
# because the download itself lands in %TEMP% and Temp cleanup would
# otherwise delete it before the slot can settle), the one-deep
# previous-installer slot (+ its marker), and the failure marker the
# update bat writes when an installer exits non-zero.
LAST_DOWNLOAD_NAME = ".update-last-download.json"
DOWNLOADS_DIR_NAME = "downloads"
PREVIOUS_DIR_NAME = "previous-installer"
PREVIOUS_MARKER_NAME = ".previous-installer.json"
FAILURE_NAME = ".update-failed.json"
API_URL = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "FocusCore-Updater"
REQUEST_TIMEOUT = 20
CHECK_TTL_SECONDS = 24 * 3600
INSTALLER_PREFIX = "FocusCore-Setup-"
CHECKSUMS_NAME = "SHA256SUMS"


class UpdateError(Exception):
    """The update check or download failed (network, API, bad data)."""


def get_update_info():
    """``{"repo": ..., "version": ..., "flavor": ...}`` or None for
    dev/portable copies.

    ``flavor`` is ``"machine"`` for per-machine installs, ``"user"``
    otherwise; installs stamped before Roadmap 2.6 have no flavor key
    and read as ``"user"`` (they were all per-user).
    """
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
    return {"repo": info["repo"], "version": info["version"],
            "flavor": info.get("flavor") or "user"}


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


def _asset_flavor(name):
    """``"machine"`` for per-machine installer assets, else ``"user"``.

    Roadmap 2.6: the machine flavor's setup exe carries a ``-machine``
    segment (``FocusCore-Setup-<ver>-machine[-offline].exe``); the
    ``-offline`` segment is orthogonal and stays ``"user"``.
    """
    stem = name[:-4] if name.lower().endswith(".exe") else name
    return "machine" if "machine" in stem.split("-") else "user"


def latest_release(repo, flavor="user"):
    """``(tag, asset_name, asset_url, asset_size, checksums_url)``.

    ``checksums_url`` is the download URL of the release's ``SHA256SUMS``
    asset, or None when the release has none. The updater refuses to
    install from a release without one (fail closed).

    Roadmap 2.6: ``flavor`` selects the installer matching this install
    (``"machine"`` or ``"user"``) -- a per-machine install must never
    download the per-user setup exe and end up with a second copy.

    Raises UpdateError when the API can't be reached, the repo is
    private, or no installer asset for this flavor is attached.
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
        elif name.startswith(INSTALLER_PREFIX) and name.endswith(".exe") \
                and _asset_flavor(name) == flavor:
            installer = (data["tag_name"], name,
                         asset.get("browser_download_url"),
                         asset.get("size") or 0)
    if installer is None:
        if flavor == "machine":
            raise UpdateError(
                "The newest release has no per-machine installer attached "
                "yet.")
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

    Returns a dict: ``status`` is ``"ok"``, ``"dev-copy"``, ``"error"``
    or ``"disabled-by-policy"``; on ``"ok"`` it also carries
    ``current``, ``latest``, ``update_available`` and ``asset``
    (name/url/size).
    """
    info = get_update_info()
    if config.feature_enabled("updates_disabled"):
        # Roadmap 1.21: the machine policy pins this install to its
        # current version. Refuse at the choke point every check path
        # flows through (launcher background thread, /update page
        # including "?refresh=1", /update/start): before the cache,
        # before any network seam -- and the refusal is never cached.
        return {"status": "disabled-by-policy",
                "current": info["version"] if info else None}
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
            latest_release(info["repo"], flavor=info.get("flavor", "user"))
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
    if config.feature_enabled("updates_disabled"):
        # Roadmap 1.21: the download primitive itself refuses while
        # the machine policy pins this install -- defense in depth
        # behind the check_for_update gate.
        raise UpdateError(
            "Updates are disabled by your IT policy, so nothing was "
            "downloaded.")
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
    """Read and clear the pending-update flag; None when absent.

    Roadmap 1.21: while the ``updates_disabled`` machine policy is on,
    the flag is LEFT IN PLACE and None is returned -- a pinned install
    must not change version, and lifting the policy resumes normal
    behavior with the queued update intact. Both tray callers (the
    startup safety net and the watcher) therefore never see a pending
    install to apply while the pin is on.
    """
    if config.feature_enabled("updates_disabled"):
        return None
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


# --- rollback state (roadmap 2.1) ---------------------------------------
#
# The honest core of "Revert to previous version": the slot may only
# ever name the installer that produced the version the app is
# *currently running*. Downloads are tracked when they complete; when a
# later download arrives and the app is by then running the previously
# downloaded version, that installer is copied into the slot. A
# download that was never applied settles nothing -- no fabricated
# previous, ever.


def _read_json_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _durable_download_copy(installer_path):
    """Best-effort durable copy of a completed download in the data dir.

    Downloads land in %TEMP% (see dashboard/routes/system.py), where
    Temp cleanup can delete them before the previous-installer slot
    ever settles -- silently losing the Revert button exactly when a
    bad update is discovered weeks later. The tracked-download record
    therefore aims at a one-deep ``downloads/`` copy in the data dir.
    Returns the durable path, or None when no copy applies (source
    missing, or already durable inside the data dir) or can be made;
    the caller then records the original path. Never raises.
    """
    source = Path(installer_path)
    try:
        data_dir = paths.data_dir()
        if not source.is_file() or data_dir in source.resolve().parents:
            return None  # nothing to copy, or already durable
        dest = data_dir / DOWNLOADS_DIR_NAME / source.name
        if source.resolve() != dest.resolve():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
            for old in dest.parent.glob(INSTALLER_PREFIX + "*.exe"):
                if old != dest:
                    old.unlink(missing_ok=True)  # one-deep: newest only
        return dest
    except OSError as exc:
        logger.warning("could not keep a durable download copy: %s", exc)
        return None


def record_download(installer_path, version):
    """Remember the newest installer this copy got (version + path).

    Written when a download completes (and when a revert is queued).
    The recorded path aims at the durable data-dir copy (see
    _durable_download_copy), falling back to the given path when no
    copy could be made. On its own this changes nothing visible; it
    only lets track_download() later settle the previous-installer slot
    for the version the app is actually running. Best effort -- a
    failed write must never break an update.
    """
    durable = _durable_download_copy(installer_path)
    recorded = durable if durable is not None else installer_path
    try:
        paths.ensure_data_dir()
        (paths.data_dir() / LAST_DOWNLOAD_NAME).write_text(json.dumps({
            "installer": str(recorded),
            "version": version,
            "at": time.time(),
        }), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not record the update download: %s", exc)


def _settle_previous_slot():
    """Copy the running version's producer into the previous slot."""
    info = get_update_info()
    if not info:
        return
    running = parse_version(info.get("version"))
    if running is None:
        return
    record = _read_json_file(paths.data_dir() / LAST_DOWNLOAD_NAME)
    if not record or parse_version(record.get("version")) != running:
        return
    marker_path = paths.data_dir() / PREVIOUS_MARKER_NAME
    marker = _read_json_file(marker_path)
    if marker and parse_version(marker.get("version")) == running:
        return  # the slot already names this version's installer
    source = Path(record.get("installer") or "")
    if not source.is_file():
        return
    dest = paths.data_dir() / PREVIOUS_DIR_NAME / source.name
    try:
        if source.resolve() != dest.resolve():
            dest.parent.mkdir(parents=True, exist_ok=True)
            for old in dest.parent.glob(INSTALLER_PREFIX + "*.exe"):
                if old != dest:
                    old.unlink(missing_ok=True)  # one-deep slot
            dest.write_bytes(source.read_bytes())
        marker_path.write_text(json.dumps({
            "version": record["version"],
            "installer": str(dest),
            "at": time.time(),
        }), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not keep the previous installer: %s", exc)


def track_download(installer_path, version):
    """A download just completed: settle the slot, then record it.

    Settling uses the *previous* record -- the installer that produced
    the version running right now -- so the slot is one update behind
    by design: while you run version N it holds N's producer, ready to
    become the revert target the moment N+1 lands.

    A path that isn't a real file on disk is not a completed download
    and tracks nothing -- the same honesty rule as the slot itself.
    """
    if not Path(installer_path).is_file():
        return
    _settle_previous_slot()
    record_download(installer_path, version)


def previous_installer():
    """The kept previous installer: {"version", "installer"} or None.

    None unless the marker exists, its version parses, and the
    installer file is really on disk -- the Revert button is only as
    honest as this function.
    """
    marker = _read_json_file(paths.data_dir() / PREVIOUS_MARKER_NAME)
    if not marker:
        return None
    installer = marker.get("installer")
    if parse_version(marker.get("version")) is None or not installer:
        return None
    if not Path(installer).is_file():
        return None
    return {"version": marker["version"], "installer": installer}


def read_update_failure():
    """The update bat's failure marker {"version", "installer"} / None."""
    return _read_json_file(paths.data_dir() / FAILURE_NAME)


def clear_update_failure():
    try:
        (paths.data_dir() / FAILURE_NAME).unlink()
    except OSError as exc:
        # Expected/cosmetic: the marker may already be gone. DEBUG is
        # enough -- the page simply renders without the card.
        logger.debug("could not clear the update-failure marker: %s",
                     exc)


def attempted_version_from_name(name):
    """Best-effort version from an installer file name.

    Release installers are FocusCore-Setup-<version>[-machine][-offline].exe;
    the flavor suffixes (Roadmap 2.6) are stripped so the version still
    parses. Anything else falls back to the bare file stem (still names
    what ran).
    """
    stem = name[:-4] if name.lower().endswith(".exe") else name
    if stem.startswith(INSTALLER_PREFIX):
        candidate = stem[len(INSTALLER_PREFIX):]
        for suffix in ("-machine-offline", "-offline-machine",
                       "-machine", "-offline"):
            if candidate.endswith(suffix):
                candidate = candidate[:-len(suffix)]
                break
        if parse_version(candidate):
            return candidate
    return stem


def write_update_launcher(installer_path):
    """Write a small bat that installs after we exit.

    The installer can't replace files while we're still running, so the
    bat waits a few seconds, then runs the setup silently -- attached,
    not via ``start``, so the exit code is real. On success Inno's own
    post-install step reopens the app, exactly as before. On a
    non-zero exit the old app is still installed (a failed Inno run
    leaves it in place), so the bat drops a failure marker into the
    data dir for the /update page and relaunches the old app the same
    way installer.iss launches it (``pythonw.exe -m
    focuscore.launcher``; roadmap 2.1) -- the app never just vanishes.
    The caller launches the bat (detached) and then quits the app.

    Roadmap 2.6: for the machine flavor the bat runs the per-machine
    installer, and Windows shows a UAC prompt -- the honest behavior
    (explicit elevation). The app never tries to auto-elevate.

    Returns the bat path.
    """
    bat = Path(tempfile.gettempdir()) / (
        "focuscore-update-%s.bat" % datetime.now().strftime("%Y%m%d%H%M%S"))
    installer_name = str(installer_path).replace(
        "\\", "/").rsplit("/", 1)[-1]
    bat.write_text(
        "@echo off\r\n"
        "timeout /t 5 /nobreak >nul\r\n"
        '"%s" /SILENT\r\n'
        "if not errorlevel 1 goto focuscore_updated\r\n"
        'echo {"version": "%s", "installer": "%s"}> "%s"\r\n'
        'start "" /d "%s" "%s" -m focuscore.launcher\r\n'
        ":focuscore_updated\r\n"
        'del "%%~f0"\r\n'
        % (installer_path,
           attempted_version_from_name(installer_name), installer_name,
           paths.data_dir() / FAILURE_NAME, paths.APP_ROOT,
           sys.executable),
        encoding="utf-8",
    )
    return bat
