"""Roadmap 1.4 (partial): tests for the Authenticode execution-gate verifier.

The verifier machinery (focuscore/authenticode.py) is built and tested now;
enforcement wiring in the updater is deferred until the signing identity
arrives (roadmap 0.5) — see the blackboard. Nothing here trusts a
self-signed certificate; the throwaway certs below prove the machinery only.

Structure:
- Platform-independent tests: ABI layout, error-code mapping, fail-closed
  API contract. These run everywhere, including Linux CI.
- Windows-only fixture tests: a real self-signed cert + signtool prove the
  positive path and the three negative paths (tampered, unsigned,
  untrusted root). These skip on non-Windows and run on windows-latest CI.
"""

import ctypes
import os
import shutil
import subprocess
import sys

import pytest

from focuscore import authenticode


# ---------------------------------------------------------------------------
# Platform-independent: the wintrust ABI we mirror
# ---------------------------------------------------------------------------

def test_guid_layout_matches_win32():
    g = authenticode._GUID
    assert ctypes.sizeof(g) == 16
    assert g.Data1.offset == 0
    assert g.Data2.offset == 4
    assert g.Data3.offset == 6
    assert g.Data4.offset == 8


def test_generic_verify_v2_guid_bytes():
    raw = bytes(authenticode.WINTRUST_ACTION_GENERIC_VERIFY_V2)
    # WINTRUST_ACTION_GENERIC_VERIFY_V2 =
    #   {0x00AAC56B, 0xCD44, 0x11D0,
    #    {0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE}}
    assert raw == bytes.fromhex("6bc5aa0044cdd0118cc200c04fc295ee")


def test_wintrust_file_info_layout():
    f = authenticode._WINTRUST_FILE_INFO
    assert f.cbStruct.offset == 0
    assert f.pcwszFilePath.offset == 8
    assert f.hFile.offset == 16
    assert f.pgKnownSubject.offset == 24
    assert ctypes.sizeof(f) == 32


def test_wintrust_data_layout():
    d = authenticode._WINTRUST_DATA
    assert d.cbStruct.offset == 0
    assert d.dwUIChoice.offset == 24
    assert d.fdwRevocationChecks.offset == 28
    assert d.dwUnionChoice.offset == 32
    assert d.u.offset == 40
    assert d.u.offset % 8 == 0  # pointer-aligned union
    assert d.dwStateAction.offset == 48
    assert d.hWVTStateData.offset == 56
    assert d.pwszURLReference.offset == 64
    assert d.dwProvFlags.offset == 72
    assert d.dwUIContext.offset == 76
    assert d.pSignatureSettings.offset == 80
    assert ctypes.sizeof(d) == 88


def test_wintrust_data_union_is_pointer_sized():
    assert ctypes.sizeof(authenticode._WINTRUST_DATA_UNION) == 8


def test_error_reasons_cover_the_key_failures():
    reasons = authenticode._ERROR_REASONS
    for code in (0x800B0100, 0x800B0111, 0x800B0004,
                 0x800B0109, 0x80096010, 0x80092026):
        assert isinstance(reasons[code], str) and reasons[code]


def test_unknown_hresult_still_reports_the_code():
    reason = authenticode._reason_for(0x800B010C)
    assert "0x800B010C" in reason


def test_missing_file_is_refused(tmp_path):
    ok, reason = authenticode.verify_authenticode(
        tmp_path / "no-such-file.exe")
    assert ok is False
    assert reason


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behavior")
def test_non_windows_platform_is_refused(tmp_path):
    target = tmp_path / "whatever.exe"
    target.write_bytes(b"MZ")
    ok, reason = authenticode.verify_authenticode(target)
    assert ok is False
    assert "Windows" in reason


# ---------------------------------------------------------------------------
# Windows-only: real signtool fixtures
# ---------------------------------------------------------------------------

needs_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Authenticode verification is Windows-only")


def _powershell_available():
    return shutil.which("powershell") is not None


def _run_ps(script, cwd):
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, cwd=str(cwd), timeout=300)
    if completed.returncode != 0:
        raise RuntimeError(
            "powershell failed:\n%s\n%s"
            % (completed.stdout, completed.stderr))
    return completed.stdout.strip().splitlines()


@pytest.fixture(scope="module")
def signed_fixtures(tmp_path_factory):
    """Build throwaway signing fixtures on real Windows.

    Creates two ephemeral self-signed code-signing certs: one anchored in
    the current user's Trusted Root store (trusted), one left only in the
    personal store (untrusted). Signs copies of a real unsigned PE with
    signtool. Everything is deleted afterwards; nothing trusts self-signed
    material outside this fixture.
    """
    if not _powershell_available():
        pytest.skip("powershell is not available")
    work = tmp_path_factory.mktemp("authenticode")
    subject = work / "subject.exe"
    shutil.copy(sys.executable, subject)

    locate = r"""
$ErrorActionPreference = 'Stop'
$kit = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Directory `
    -ErrorAction SilentlyContinue | Sort-Object Name -Descending `
    | Select-Object -First 1
if (-not $kit) { throw 'no Windows Kits installation found' }
$st = Join-Path $kit.FullName 'x64\signtool.exe'
if (-not (Test-Path $st)) { throw 'signtool.exe not found in Windows Kits' }
$st
"""
    try:
        signtool = _run_ps(locate, work)[-1]
    except RuntimeError as exc:
        pytest.skip("signtool unavailable: %s" % exc)

    # Strip any signature the copied interpreter already carries (the
    # python.org/CI binaries are PSF-signed) so every fixture below starts
    # from a genuinely unsigned PE. Failure here only means "there was no
    # signature to strip", which is the state we want anyway.
    subprocess.run(
        [signtool, "remove", "/s", str(subject)],
        capture_output=True, text=True, timeout=300)

    make_cert = r"""
$ErrorActionPreference = 'Stop'
$cert = New-SelfSignedCertificate -Type CodeSigningCert `
    -Subject 'CN=FocusCore Ephemeral Test (never trust)' `
    -CertStoreLocation 'Cert:\CurrentUser\My' `
    -NotAfter (Get-Date).AddDays(2) `
    -KeyExportPolicy Exportable
$cert.Thumbprint
"""
    trusted_thumb = _run_ps(make_cert, work)[-1].strip()
    untrusted_thumb = _run_ps(make_cert, work)[-1].strip()

    trust = (
        "$ErrorActionPreference = 'Stop';"
        "$c = Get-ChildItem Cert:\\CurrentUser\\My\\%s;"
        "$s = New-Object System.Security.Cryptography.X509Certificates."
        "X509Store('Root','CurrentUser');"
        "$s.Open('ReadWrite'); $s.Add($c); $s.Close()"
    ) % trusted_thumb
    _run_ps(trust, work)

    def sign(thumb, dest):
        shutil.copy(subject, dest)
        out = subprocess.run(
            [signtool, "sign", "/fd", "SHA256", "/sha1", thumb, str(dest)],
            capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            raise RuntimeError("signtool failed:\n%s\n%s"
                               % (out.stdout, out.stderr))
        return dest

    signed = sign(trusted_thumb, work / "signed.exe")
    untrusted = sign(untrusted_thumb, work / "untrusted.exe")
    unsigned = work / "unsigned.exe"
    shutil.copy(subject, unsigned)

    tampered = work / "tampered.exe"
    shutil.copy(signed, tampered)
    # Flip a byte deep inside the signed image (not the signature table
    # itself): verification must fail on digest mismatch.
    with open(tampered, "r+b") as handle:
        size = os.path.getsize(tampered)
        handle.seek(size // 2)
        byte = handle.read(1)
        handle.seek(size // 2)
        handle.write(bytes([byte[0] ^ 0xFF]))

    yield {"signed": signed, "tampered": tampered,
           "unsigned": unsigned, "untrusted": untrusted}

    cleanup = r"""
$ErrorActionPreference = 'SilentlyContinue'
foreach ($t in @('%s', '%s')) {
    Remove-Item "Cert:\CurrentUser\My\$t" -ErrorAction SilentlyContinue
}
Remove-Item "Cert:\CurrentUser\Root\%s" -ErrorAction SilentlyContinue
""" % (trusted_thumb, untrusted_thumb, trusted_thumb)
    try:
        _run_ps(cleanup, work)
    except RuntimeError:
        pass  # best-effort cleanup on an ephemeral CI runner


@needs_windows
def test_signed_file_passes(signed_fixtures):
    ok, reason = authenticode.verify_authenticode(
        signed_fixtures["signed"])
    assert ok is True, reason


@needs_windows
def test_tampered_file_is_refused(signed_fixtures):
    ok, reason = authenticode.verify_authenticode(
        signed_fixtures["tampered"])
    assert ok is False
    assert reason


@needs_windows
def test_unsigned_file_is_refused(signed_fixtures):
    ok, reason = authenticode.verify_authenticode(
        signed_fixtures["unsigned"])
    assert ok is False
    assert "no Authenticode signature" in reason


@needs_windows
def test_untrusted_root_is_refused(signed_fixtures):
    ok, reason = authenticode.verify_authenticode(
        signed_fixtures["untrusted"])
    assert ok is False
    # It IS signed — refusal must be about trust, not absence.
    assert "no Authenticode signature" not in reason
    assert reason
