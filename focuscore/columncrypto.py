"""Column-level protection for the most sensitive DB text (roadmap 1.5a).

Window titles and the URLs the tracker records are the most revealing
data Focus Core stores ("Bank statement — Google Docs" tells a story
that "chrome.exe ran for 30 minutes" does not).  On the shipped
platform (Windows) those columns are stored encrypted with DPAPI
(`CryptProtectData`, user scope), which transparently defeats the two
reads the roadmap names:

* a stolen laptop / copied ``focuscore.db`` file (no BitLocker needed
  for these columns to stay unreadable), and
* another Windows user account on the same machine.

**Scope is the local database only.**  DPAPI keys are bound to the
Windows user + machine, so this does nothing for backups — the Drive
copy is still the same database file (see PRIVACY.md: titles/URLs in a
backup stay ciphertext and come back unreadable on a *different*
Windows user; everything else restores normally).  Passphrase-based
backup encryption is roadmap 1.5b and must never rely on DPAPI.

Format: ``dpapi:v1:<base64 of the protected blob>`` written into the
same TEXT column.  The prefix makes every row self-describing, so the
upgrade migration (0010) is per-row resumable, reads can distinguish
legacy plaintext from ciphertext, and a future ``v2`` format can
coexist.

Rules of the road:

* :func:`protect_text` never fails silently-with-data-loss: it raises
  :class:`ColumnCryptoError`, so callers (migration, store writes)
  choose explicitly how to degrade.
* :func:`unprotect_text` never raises: legacy plaintext passes through
  unchanged, and a value that cannot be decrypted on this Windows user
  (DB moved, key gone) becomes :data:`PLACEHOLDER` instead of crashing
  the dashboard.  Bytes that were never ours are not our problem.
* The protector is chosen once, by platform, at import.  There is no
  setting to turn it off — a config flag here would be a silent
  "store plaintext" switch in a security feature.
"""

from __future__ import annotations

import base64
import logging
import sys

logger = logging.getLogger(__name__)

# Stored-format marker.  ``v1`` = DPAPI user-scope blob, base64.
PREFIX = "dpapi:v1:"

# What the UI shows for ciphertext this Windows user cannot open
# (database restored on another PC/user).  Plain, honest, and safe to
# render anywhere a title/URL would go.
PLACEHOLDER = "(unreadable — encrypted for a different Windows user)"


class ColumnCryptoError(Exception):
    """Protection (encrypt side) failed; the caller decides."""


# --------------------------------------------------------------- DPAPI ---

# CRYPTPROTECT_UI_FORBIDDEN: never pop a consent prompt from a library.
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _dpapi_crypt(data: bytes, *, protect: bool) -> bytes:
    """One CryptProtectData / CryptUnprotectData call (Windows only)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    in_buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(in_buf,
                                               ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise ColumnCryptoError(
            "Crypt{}Data failed (winerror {})".format(
                "Protect" if protect else "Unprotect",
                ctypes.get_last_error()))
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class _DpapiProtector:
    """Real Windows protector (CryptProtectData, current-user scope)."""

    name = "dpapi-user"

    def protect(self, data: bytes) -> bytes:
        if sys.platform != "win32":
            raise ColumnCryptoError("DPAPI exists only on Windows")
        return _dpapi_crypt(data, protect=True)

    def unprotect(self, data: bytes) -> bytes:
        if sys.platform != "win32":
            raise ColumnCryptoError("DPAPI exists only on Windows")
        return _dpapi_crypt(data, protect=False)


class _IdentityProtector:
    """DEV/TEST-ONLY stand-in for non-Windows platforms.

    Focus Core ships only on Windows, so DPAPI always exists in
    production.  Off Windows (this dev VM, Linux CI) the *mechanics*
    still need to run — migration, prefixing, placeholders — so the
    blob passes through unchanged while the surrounding format stays
    identical.  The value is still base64-wrapped, never labelled as
    protected.  Never selected on win32.
    """

    name = "identity-dev"

    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


def _default_protector():
    if sys.platform == "win32":
        return _DpapiProtector()
    return _IdentityProtector()


# The one active protector, picked by platform.  Tests monkeypatch this
# module attribute to inject deterministic fakes.
_protector = _default_protector()


# ------------------------------------------------------------ bytes API ---

def protect_bytes(data: bytes) -> bytes:
    """Seal raw bytes with the platform protector (roadmap 1.5b).

    Used for the backup-passphrase cache file. Raises
    :class:`ColumnCryptoError` on failure -- callers decide how to
    degrade. The text API below is unchanged.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ColumnCryptoError("protect_bytes needs bytes")
    try:
        return _protector.protect(bytes(data))
    except ColumnCryptoError:
        raise
    except Exception as exc:
        raise ColumnCryptoError(str(exc)) from exc


def unprotect_bytes(data: bytes) -> bytes:
    """Open bytes sealed by :func:`protect_bytes`. Raises on failure."""
    if not isinstance(data, (bytes, bytearray)):
        raise ColumnCryptoError("unprotect_bytes needs bytes")
    try:
        return _protector.unprotect(bytes(data))
    except ColumnCryptoError:
        raise
    except Exception as exc:
        raise ColumnCryptoError(str(exc)) from exc


# ------------------------------------------------------------ text API ---

def is_protected(value) -> bool:
    """True if ``value`` carries our ciphertext prefix."""
    return isinstance(value, str) and value.startswith(PREFIX)


def protect_text(value):
    """Seal one text value for storage; None/"" pass through.

    Idempotent: an already-sealed value is returned unchanged, which is
    what makes the upgrade migration safely re-runnable row by row.
    Raises :class:`ColumnCryptoError` if the protector fails — storing
    would otherwise need a decision the caller must make explicitly.
    """
    if value is None or value == "" or is_protected(value):
        return value
    if not isinstance(value, str):
        value = str(value)
    try:
        blob = _protector.protect(value.encode("utf-8"))
    except ColumnCryptoError:
        raise
    except Exception as exc:  # protector bugs surface as our error type
        raise ColumnCryptoError(str(exc)) from exc
    return PREFIX + base64.b64encode(blob).decode("ascii")


def unprotect_text(value):
    """Open one stored value for display; NEVER raises.

    Unprefixed values are legacy plaintext and pass through unchanged.
    Prefixed values that cannot be opened on this Windows user (or are
    corrupt) become :data:`PLACEHOLDER`.
    """
    if value is None or value == "" or not is_protected(value):
        return value
    try:
        blob = base64.b64decode(value[len(PREFIX):], validate=True)
        raw = _protector.unprotect(blob)
        return raw.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 -- never-raise contract: ANY
        # failure (bad base64, protector error, decode error) becomes the
        # placeholder so the dashboard can never crash on a stored value.
        logger.debug("stored value could not be unprotected "
                     "(%s); showing placeholder", exc)
        return PLACEHOLDER
