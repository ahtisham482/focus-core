"""Unified configuration for Focus Core (Roadmap 1.12).

ONE reader for deployment-time settings. Values resolve through four
tiers, highest precedence first:

    1. Machine  -- HKLM\\Software\\Focus Core (Windows registry; lets IT
       pre-configure a whole fleet before any user signs in)
    2. User     -- config.toml (lets a user override port / log level /
       data dir without touching the app)
    3. Env      -- FOCUSCORE_* environment variables
    4. Defaults -- whatever the caller passes as ``default``

Note the deliberate order: the env tier LOSES to config.toml. The file
is the user's considered, persistent choice; an env var is often a
stray leftover from some other tool's session. This is pinned by
tests/test_config.py so nobody "fixes" it later.

Keys on day one: ``port`` (int, 1..65535), ``log_level`` (a logging
level name), ``data_dir`` (str). Feature flags live in a ``[features]``
subtable and are read with :func:`feature_enabled`.

Roadmap 1.20 adds ``capture_exclusions``: a list of process names
whose activity the ingest pipeline drops before storage, so an
excluded app's window titles and URLs never reach the database (read
once per pipeline run via :func:`get_capture_exclusions`). Values may
be a list of names (TOML array / REG_MULTI_SZ) or one string of
``;``- or ``,``-separated names (REG_SZ / env var). Because config is
uncached, an IT policy change takes effect on the next ingest tick --
no app restart.

Machine tier shape (registry values under HKLM\\Software\\Focus Core):
``Port`` (DWORD or SZ), ``LogLevel`` (SZ), ``DataDir`` (SZ),
``CaptureExclusions`` (REG_MULTI_SZ or ``;``-separated SZ), and a
``Features`` subkey whose values are named after the flag (DWORD or
SZ). The reader is injectable (``_machine_reader``) so Linux tests can
exercise the tier with a fake; off Windows it returns {}.

Environment mapping (documented here -- this docstring is the map):

    FOCUSCORE_PORT            -> port
    FOCUSCORE_LOG_LEVEL       -> log_level
    FOCUSCORE_DATA_DIR        -> data_dir
    FOCUSCORE_CAPTURE_EXCLUSIONS -> capture_exclusions
                                 (``;``- or ``,``-separated names)
    FOCUSCORE_FEATURE_<NAME>  -> [features] entry <NAME>, where <NAME>
                                 is the flag name upper-cased with
                                 non-alphanumerics turned into "_"
                                 (1/true/yes/on = True, 0/false/no/off
                                 = False, case-insensitive)

Failure honesty: config never raises into app startup. A TOML parse
error logs a WARNING and the file counts as empty -- a typo must never
brick the app. A value that fails validation at one tier (port not in
1..65535, log_level not a real level name) logs a WARNING and falls
through to the next tier. Nothing is cached: every lookup re-reads the
tiers, which keeps tests hermetic and lets a fixed file take effect on
the next read.

This is deployment config, NOT user settings: the SQLite settings
table (per-user runtime preferences) is a separate world and no key
is mirrored between the two.

The TOML lives where paths says, and paths never consults this module
to find it (no recursion): installed mode reads
``user_data_dir()/config.toml``, portable/dev reads
``APP_ROOT/config.toml``.
"""

import logging
import os
import sys
import tomllib

from . import paths

logger = logging.getLogger(__name__)

MACHINE_REGISTRY_PATH = r"Software\Focus Core"

_MISSING = object()

_ENV_VARS = {
    "port": "FOCUSCORE_PORT",
    "log_level": "FOCUSCORE_LOG_LEVEL",
    "data_dir": "FOCUSCORE_DATA_DIR",
}

_TRUE_SPELLINGS = {"1", "true", "yes", "on"}
_FALSE_SPELLINGS = {"0", "false", "no", "off"}
_LEVEL_NAMES = {
    "CRITICAL", "FATAL", "ERROR", "WARN", "WARNING",
    "INFO", "DEBUG", "NOTSET",
}


# ---------------------------------------------------------- machine ---

def _read_machine_winreg():
    """Read HKLM\\Software\\Focus Core via winreg. Windows only.

    Absent key, absent values, or a non-Windows platform all mean {}
    (the tier simply has no opinion). Read-only: this never writes
    the registry.
    """
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    values = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             MACHINE_REGISTRY_PATH)
    except OSError:
        return {}
    with key:
        for name, value_name in (("port", "Port"),
                                 ("log_level", "LogLevel"),
                                 ("data_dir", "DataDir"),
                                 ("capture_exclusions",
                                  "CaptureExclusions")):
            try:
                raw, _reg_type = winreg.QueryValueEx(key, value_name)
            except OSError:
                continue
            values[name] = raw
        try:
            features_key = winreg.OpenKey(key, "Features")
        except OSError:
            features_key = None
        if features_key is not None:
            with features_key:
                features = {}
                index = 0
                while True:
                    try:
                        vname, vraw, _vtype = winreg.EnumValue(
                            features_key, index)
                    except OSError:
                        break
                    features[vname] = vraw
                    index += 1
                if features:
                    values["features"] = features
    return values


# The injectable machine reader. Tests swap this out with a fake so
# the machine tier is exercisable on any platform.
_machine_reader = _read_machine_winreg


def machine_values():
    """The machine tier as a plain dict ({} when it has no opinion).

    A broken reader must never brick startup: warn and treat the
    tier as empty.
    """
    try:
        values = _machine_reader()
    except Exception as exc:  # noqa: BLE001 -- config never raises
        logger.warning("machine config unreadable; ignoring it: %s", exc)
        return {}
    return values if isinstance(values, dict) else {}


# ------------------------------------------------------------- user ---

def config_file_path():
    """Where config.toml lives. Never consults config (no recursion)."""
    if paths.is_installed():
        return paths.user_data_dir() / "config.toml"
    return paths.APP_ROOT / "config.toml"


def _load_toml():
    """Parse config.toml. Broken TOML warns and counts as empty."""
    path = None
    try:
        path = config_file_path()
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 -- a typo never bricks us
        if path is not None:
            logger.warning("ignoring config file %s: %s", path, exc)
        else:
            logger.warning("ignoring config file: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


# -------------------------------------------------------------- env ---

def _env_name_for(key):
    return _ENV_VARS.get(key) or "FOCUSCORE_" + _env_suffix(key)


def _env_suffix(name):
    stripped = name.strip() if isinstance(name, str) else name
    return "".join(
        ch if ch.isalnum() else "_" for ch in str(stripped).upper())


# -------------------------------------------------------- validation ---

def _validate_port(value):
    if isinstance(value, bool):
        return False, None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return False, None
    if 1 <= number <= 65535:
        return True, number
    return False, None


def _validate_log_level(value):
    if isinstance(value, str):
        name = value.strip().upper()
        if name in _LEVEL_NAMES:
            return True, name
    return False, None


def _validate_data_dir(value):
    if isinstance(value, str) and value.strip():
        return True, value.strip()
    return False, None


def _validate_capture_exclusions(value):
    """Normalize a blocklist value to a clean list of process names.

    Accepts a list/tuple of strings (TOML array, REG_MULTI_SZ) or one
    string of ``;``- or ``,``-separated names (REG_SZ, env var).
    Names are stripped and empties dropped. Anything else -- a bare
    number, a mapping, a list holding a non-string -- is invalid, so
    the caller's tier falls through with a warning like every other
    1.12 value.
    """
    if isinstance(value, str):
        names = value.replace(",", ";").split(";")
    elif isinstance(value, (list, tuple)):
        if not all(isinstance(item, str) for item in value):
            return False, None
        names = list(value)
    else:
        return False, None
    cleaned = [name.strip() for name in names]
    return True, [name for name in cleaned if name]


_VALIDATORS = {
    "port": _validate_port,
    "log_level": _validate_log_level,
    "data_dir": _validate_data_dir,
    "capture_exclusions": _validate_capture_exclusions,
}


# ------------------------------------------------------------ lookup ---

def _candidates(key):
    """Yield (tier label, raw value) for key, highest precedence first."""
    machine = machine_values()
    if key in machine:
        yield "machine settings", machine[key]
    toml = _load_toml()
    if key in toml:
        yield "config.toml", toml[key]
    env_name = _env_name_for(key)
    raw = os.environ.get(env_name)
    if raw is not None:
        yield env_name, raw


def get(key, default=None):
    """Resolve key through machine > config.toml > env > default.

    Known keys are validated per tier: an invalid value warns and
    falls through instead of winning. Unknown keys pass through raw.
    Returns ``default`` when no tier sets the key.
    """
    validator = _VALIDATORS.get(key)
    for tier, raw in _candidates(key):
        if validator is None:
            return raw
        ok, value = validator(raw)
        if ok:
            return value
        logger.warning(
            "ignoring invalid %s value from %s: %r", key, tier, raw)
    return default


def get_port():
    """Configured dashboard port (int), or None when unset."""
    return get("port")


def get_log_level():
    """Configured log level name (e.g. "DEBUG"), or None when unset."""
    return get("log_level")


def get_data_dir():
    """Configured data dir (str), or None when unset."""
    return get("data_dir")


def get_capture_exclusions():
    """Capture-scope blocklist (Roadmap 1.20): process names to drop.

    Resolved machine > config.toml > env like every other key; an
    invalid value at one tier warns and falls through. Returns the
    names as configured (stripped; case and any ``.exe`` suffix are
    the matching layer's concern), or [] when no tier sets the key.
    """
    value = get("capture_exclusions", None)
    return list(value) if value else []


# ----------------------------------------------------- feature flags ---

def _coerce_bool(value):
    """True/False for a flag value, or None when it is not a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        spelling = value.strip().lower()
        if spelling in _TRUE_SPELLINGS:
            return True
        if spelling in _FALSE_SPELLINGS:
            return False
    return None


def _lookup_feature(features, name):
    if not isinstance(features, dict):
        return _MISSING
    if not isinstance(name, str):
        return _MISSING
    lowered = name.strip().lower()
    if not lowered:
        return _MISSING
    for flag, value in features.items():
        if isinstance(flag, str) and flag.strip().lower() == lowered:
            return value
    return _MISSING


def feature_enabled(name, default=False):
    """Resolve feature flag ``name`` through the same four tiers.

    Machine Features subkey > config.toml [features] > the
    FOCUSCORE_FEATURE_<NAME> env var > ``default``. An unparseable
    value warns and falls through, like any other config value.
    Leading/trailing whitespace is insignificant: ``" dark_mode "``
    and ``"dark_mode"`` name the same flag, and an empty-after-strip
    name resolves straight to ``default``.
    """
    if not isinstance(name, str):
        return bool(default)
    name = name.strip()
    if not name:
        return bool(default)
    env_name = "FOCUSCORE_FEATURE_" + _env_suffix(name)
    env_raw = os.environ.get(env_name)
    tiers = (
        ("machine settings",
         _lookup_feature(machine_values().get("features"), name)),
        ("config.toml",
         _lookup_feature(_load_toml().get("features"), name)),
        (env_name, _MISSING if env_raw is None else env_raw),
    )
    for tier, raw in tiers:
        if raw is _MISSING:
            continue
        coerced = _coerce_bool(raw)
        if coerced is not None:
            return coerced
        logger.warning(
            "ignoring invalid feature flag %r from %s: %r",
            name, tier, raw)
    return bool(default)
