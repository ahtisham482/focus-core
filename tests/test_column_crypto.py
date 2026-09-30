"""Roadmap 1.5a: column-level protection for window titles / URLs.

Unit tests for focuscore.columncrypto: real DPAPI on Windows, identity
protector elsewhere, deterministic fakes injected for the mechanics.
"""

from __future__ import annotations

import sys

import pytest

from focuscore import columncrypto


class _ReverseProtector:
    """Deterministic stand-in: reversible, proves the plumbing."""

    name = "reverse-test"

    def protect(self, data: bytes) -> bytes:
        return data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        return data[::-1]


class _ExplodingProtector:
    name = "exploding-test"

    def protect(self, data: bytes) -> bytes:
        raise RuntimeError("no crypto here")

    def unprotect(self, data: bytes) -> bytes:
        raise RuntimeError("no crypto here")


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(columncrypto, "_protector", _ReverseProtector())
    return columncrypto


def test_round_trip_through_prefix(fake):
    sealed = fake.protect_text("Resume — Q3 plan — Google Docs")
    assert sealed.startswith(fake.PREFIX)
    assert "Resume" not in sealed
    assert fake.unprotect_text(sealed) == "Resume — Q3 plan — Google Docs"


def test_protect_is_idempotent(fake):
    once = fake.protect_text("Bank statement")
    assert fake.protect_text(once) == once


def test_empty_and_none_pass_through(fake):
    assert fake.protect_text(None) is None
    assert fake.protect_text("") == ""
    assert fake.unprotect_text(None) is None
    assert fake.unprotect_text("") == ""


def test_legacy_plaintext_reads_back_unchanged(fake):
    assert fake.unprotect_text("plain old title") == "plain old title"


def test_undecryptable_garbage_shows_placeholder(fake):
    assert fake.unprotect_text(
        fake.PREFIX + "!!! not base64 !!!") == fake.PLACEHOLDER


def test_undecryptable_blob_shows_placeholder(monkeypatch, fake):
    sealed = fake.protect_text("secret title")
    monkeypatch.setattr(columncrypto, "_protector", _ExplodingProtector())
    assert columncrypto.unprotect_text(sealed) == columncrypto.PLACEHOLDER


def test_protect_failure_raises_column_crypto_error(fake, monkeypatch):
    monkeypatch.setattr(columncrypto, "_protector", _ExplodingProtector())
    with pytest.raises(columncrypto.ColumnCryptoError):
        columncrypto.protect_text("anything")


def test_active_protector_round_trips_on_this_platform():
    sealed = columncrypto.protect_text("some window title")
    assert sealed.startswith(columncrypto.PREFIX)
    assert columncrypto.unprotect_text(sealed) == "some window title"


@pytest.mark.skipif(sys.platform != "win32",
                    reason="DPAPI exists only on Windows")
def test_real_dpapi_protects_bytes():
    protector = columncrypto._DpapiProtector()
    blob = protector.protect(b"window title bytes")
    assert blob != b"window title bytes"
    assert protector.unprotect(blob) == b"window title bytes"
