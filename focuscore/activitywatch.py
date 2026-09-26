"""ActivityWatch detection: is it running, installed, or missing?

Focus Core reads tracked activity from ActivityWatch's local REST API
(http://localhost:5600/api/0/). A stranger's first question is always
"why is nothing showing up?" -- these helpers answer it in plain states:

- ``"running"``               -- the server answers; tracking works.
- ``"installed_not_running"`` -- aw-qt.exe exists but the server is silent.
- ``"not_installed"``         -- nothing found; the user needs the installer.

Probes use a short timeout so dashboard pages never hang waiting.
"""

import os
import sys

import requests

API_BASE = "http://localhost:5600/api/0/"
PROBE_TIMEOUT = 2.0

# Official releases page: always points at the newest release, whose
# Windows asset is a normal .exe installer (per ActivityWatch's own
# getting-started docs).
DOWNLOAD_URL = "https://github.com/ActivityWatch/activitywatch/releases/latest"

RUNNING = "running"
INSTALLED_NOT_RUNNING = "installed_not_running"
NOT_INSTALLED = "not_installed"


def server_status(timeout=PROBE_TIMEOUT):
    """Probe ActivityWatch's /api/0/info endpoint.

    Returns ``{"running": bool, "version": str|None,
    "hostname": str|None}``. Never raises -- an unreachable server simply
    means ``{"running": False, ...}``.
    """
    try:
        response = requests.get(API_BASE + "info", timeout=timeout)
        response.raise_for_status()
        info = response.json() or {}
    except Exception:
        return {"running": False, "version": None, "hostname": None}
    version = info.get("version")
    return {
        "running": True,
        "version": str(version) if version else None,
        "hostname": info.get("hostname"),
    }


def _candidate_install_paths():
    """Best-effort aw-qt.exe locations on Windows (empty elsewhere)."""
    if sys.platform != "win32":
        return []
    env = os.environ
    candidates = []
    for base in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"),
                 env.get("LOCALAPPDATA")):
        if base:
            candidates.append(
                os.path.join(base, "ActivityWatch", "aw-qt.exe"))
    # A per-user unzip install sometimes lands directly under the profile.
    for base in (env.get("USERPROFILE"),):
        if base:
            candidates.append(
                os.path.join(base, "ActivityWatch", "aw-qt.exe"))
            candidates.append(
                os.path.join(base, "activitywatch", "aw-qt.exe"))
    # A Start Menu shortcut proves an installer ran, even if the exe moved.
    appdata = env.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(
            appdata, "Microsoft", "Windows", "Start Menu", "Programs",
            "ActivityWatch.lnk"))
    return candidates


def likely_installed(candidates=None):
    """Path of the found ActivityWatch install, or None.

    ``candidates`` overrides the default search list (used by tests).
    """
    paths = (candidates if candidates is not None
             else _candidate_install_paths())
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def detection_state(status=None, installed=None):
    """One of RUNNING / INSTALLED_NOT_RUNNING / NOT_INSTALLED.

    ``status`` and ``installed`` are injectable for tests; when omitted
    they are probed live.
    """
    if status is None:
        status = server_status()
    if status.get("running"):
        return RUNNING
    if installed is None:
        installed = likely_installed()
    return INSTALLED_NOT_RUNNING if installed else NOT_INSTALLED
