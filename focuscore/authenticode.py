"""Authenticode verification for the updater's future execution gate.

Roadmap 1.4 (PARTIAL): this module is the *machinery* — a stdlib-only
ctypes mirror of the WinVerifyTrust API. It is built and tested now, but
it is NOT wired into the updater's launch path: there is no signing
identity yet (roadmap 0.5, SignPath application not submitted), so a hard
enforcement gate today would refuse every update. Enforcement wiring is
deferred until the identity arrives; see the marked wiring point in
``tray._apply_pending_update``.

Trust model, stated plainly: this verifier reports what Windows itself
thinks of the signature. It never invents trust — a self-signed or
untrusted-root certificate is refused. The throwaway certs in
``tests/test_authenticode_gate.py`` exist only to prove the machinery on
CI; they are never trusted by anything shipped.

ABI sources (each struct/constant below cites them):
- ``WinVerifyTrust`` signature, ``WINTRUST_DATA`` / ``WINTRUST_FILE_INFO``
  layouts, and the ``WTD_*`` constants: Microsoft Docs, "WinVerifyTrust
  function" and the ``wintrust.h`` / ``WinTrust.h`` header reference.
- ``WINTRUST_ACTION_GENERIC_VERIFY_V2`` GUID value
  ``{00AAC56B-CD44-11D0-8CC2-00C04FC295EE}``: ``softpub.h`` /
  Microsoft Docs "WINTRUST_ACTION_GENERIC_VERIFY_V2".
- HRESULT trust codes (``TRUST_E_*``): ``WinError.h``.

Public API: ``verify_authenticode(exe_path) -> (ok, reason)``.
Fail-closed: ``ok`` is True only when WinVerifyTrust returns
``ERROR_SUCCESS``. Every other outcome — no signature, tampered file,
untrusted chain, missing file, non-Windows platform, API failure —
returns ``(False, <human-readable reason>)``.
"""

import ctypes
import logging
import os
import sys

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ABI mirror of wintrust.h. All integer fields use fixed-width ctypes types
# (DWORD -> c_uint32, WORD -> c_uint16) so the layout is identical on every
# host — including Linux, where c_ulong would be 8 bytes instead of 4 and
# would silently corrupt the struct. Pointers are c_void_p / c_wchar_p,
# which are 8 bytes on 64-bit Windows and 64-bit Linux alike.
# ---------------------------------------------------------------------------


class _GUID(ctypes.Structure):
    """16-byte GUID, as in guiddef.h."""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# softpub.h: the action GUID that selects the default software-publisher
# trust provider (Authenticode for PE files).
WINTRUST_ACTION_GENERIC_VERIFY_V2 = _GUID(
    0x00AAC56B, 0xCD44, 0x11D0,
    (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))


class _WINTRUST_FILE_INFO(ctypes.Structure):
    """wintrust.h: identifies the single file being verified."""

    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pcwszFilePath", ctypes.c_wchar_p),   # LPCWSTR
        ("hFile", ctypes.c_void_p),            # HANDLE, optional
        ("pgKnownSubject", ctypes.POINTER(_GUID)),  # optional
    ]


class _WINTRUST_DATA_UNION(ctypes.Union):
    """wintrust.h: the per-subject union inside WINTRUST_DATA.

    All five arms are pointers, so the union is pointer-sized (8 bytes).
    Only pFile is ever populated here; the rest are declared so the size
    is right.
    """

    _fields_ = [
        ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)),
        ("pCatalog", ctypes.c_void_p),
        ("pBlob", ctypes.c_void_p),
        ("pSgnr", ctypes.c_void_p),
        ("pCert", ctypes.c_void_p),
    ]


class _WINTRUST_DATA(ctypes.Structure):
    """wintrust.h: the control block passed to WinVerifyTrust.

    Field order is exactly the header's; ctypes computes the Windows x64
    padding (cbStruct is followed by 4 pad bytes before the first pointer).
    """

    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", ctypes.c_uint32),
        ("fdwRevocationChecks", ctypes.c_uint32),
        ("dwUnionChoice", ctypes.c_uint32),
        ("u", _WINTRUST_DATA_UNION),
        ("dwStateAction", ctypes.c_uint32),
        ("hWVTStateData", ctypes.c_void_p),
        ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", ctypes.c_uint32),
        ("dwUIContext", ctypes.c_uint32),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


# WTD_UI_* (wintrust.h): never show trust dialogs during verification.
WTD_UI_NONE = 2
# WTD_REVOKE_* (wintrust.h): revocation checking policy for the chain.
WTD_REVOKE_NONE = 0x00000000
WTD_REVOKE_WHOLECHAIN = 0x00000001
# WTD_CHOICE_* (wintrust.h): which union arm carries the subject.
WTD_CHOICE_FILE = 1
# WTD_STATEACTION_* (wintrust.h): IGNORE = stateless one-shot check.
WTD_STATEACTION_IGNORE = 0x00000000

# WinError.h trust HRESULTs, as unsigned 32-bit values. WinVerifyTrust
# returns a signed LONG; the caller masks to unsigned before lookup.
_ERROR_REASONS = {
    0x800B0100: "the file has no Authenticode signature",
    0x800B0111: "the signature is explicitly distrusted",
    0x800B0004: "the signer is not trusted",
    0x800B0109: "the certificate chain ends at an untrusted root",
    0x80096010: "the file was modified after signing (digest mismatch)",
    0x80092026: "the signature is valid but system policy forbids it",
}


def _reason_for(code):
    """Human-readable reason for an unsigned HRESULT."""
    if code in _ERROR_REASONS:
        return _ERROR_REASONS[code]
    return "WinVerifyTrust failed (HRESULT 0x%08X)" % (code & 0xFFFFFFFF)


def verify_authenticode(exe_path, check_revocation=False):
    """Check an exe's Authenticode signature. Returns ``(ok, reason)``.

    ``ok`` is True only when Windows' own WinVerifyTrust reports
    ERROR_SUCCESS. Everything else — missing file, unsigned file, tampered
    file, untrusted chain, API errors, non-Windows platform — returns
    ``(False, reason)``. Callers must treat False as "do not launch".

    ``check_revocation`` enables whole-chain revocation checking
    (WTD_REVOKE_WHOLECHAIN); it needs network access for CRL/OCSP and is
    off by default so offline verification stays deterministic. The
    enforcement wiring (when the signing identity exists) can turn it on.

    On non-Windows platforms this always returns ``(False, ...)`` — the
    gate is meaningless where there is no Authenticode.
    """
    path = os.fspath(exe_path)
    if not os.path.isfile(path):
        return False, "file not found: %s" % path
    if sys.platform != "win32":
        return False, "Authenticode verification is only available " \
                      "on Windows"
    # ctypes.WinDLL exists only on Windows; the import above is
    # platform-safe because nothing here touches WinDLL at module scope.
    wintrust = ctypes.WinDLL("wintrust.dll")
    wintrust.WinVerifyTrust.argtypes = [
        ctypes.c_void_p,              # HWND hwnd
        ctypes.POINTER(_GUID),        # GUID *pgActionID
        ctypes.POINTER(_WINTRUST_DATA),  # LPVOID pWVTData
    ]
    # LONG is 32-bit even on 64-bit Windows.
    wintrust.WinVerifyTrust.restype = ctypes.c_int32

    file_info = _WINTRUST_FILE_INFO()
    file_info.cbStruct = ctypes.sizeof(_WINTRUST_FILE_INFO)
    file_info.pcwszFilePath = path
    file_info.hFile = None
    file_info.pgKnownSubject = None

    data = _WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(_WINTRUST_DATA)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = (
        WTD_REVOKE_WHOLECHAIN if check_revocation else WTD_REVOKE_NONE)
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.u.pFile = ctypes.pointer(file_info)
    data.dwStateAction = WTD_STATEACTION_IGNORE

    result = wintrust.WinVerifyTrust(
        None,
        ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
        ctypes.byref(data))
    code = result & 0xFFFFFFFF
    if code == 0:  # ERROR_SUCCESS
        return True, "Authenticode signature verified"
    reason = _reason_for(code)
    logger.warning("Authenticode verification refused %s: %s", path, reason)
    return False, reason
