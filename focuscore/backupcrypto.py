"""Passphrase-encrypted backup payloads (roadmap 1.5b).

Container format ``FCBENC1``::

    magic (7 bytes) | iterations (u32 BE) | salt (16) | nonce (12) | ct

The key is PBKDF2-HMAC-SHA256 (600,000 iterations, stored in the
header so future bumps keep old files openable) deriving 32 bytes
for AES-256-GCM. The header bytes are the GCM additional
authenticated data, so tampering with magic/iterations/salt/nonce is
detected exactly like tampering with the ciphertext.

The passphrase itself is never stored here. Callers keep only a
PBKDF2 verifier (see :func:`make_passphrase_verifier`) in settings
and, optionally, a DPAPI-wrapped cache of the passphrase on the same
PC (see ``focuscore.backup``) -- the backup file itself opens with
the passphrase on any machine.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct

MAGIC = b"FCBENC1"
DEFAULT_ITERATIONS = 600_000
_SALT_SIZE = 16
_NONCE_SIZE = 12
_KEY_SIZE = 32
_HEADER_SIZE = len(MAGIC) + 4 + _SALT_SIZE + _NONCE_SIZE


class BackupCryptoError(Exception):
    """Encryption/decryption failed (wrong passphrase or tampering)."""


def _passphrase_bytes(passphrase) -> bytes:
    if isinstance(passphrase, bytes):
        return passphrase
    if isinstance(passphrase, str):
        return passphrase.encode("utf-8")
    raise BackupCryptoError("Passphrase must be text.")


def _derive_key(passphrase, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", _passphrase_bytes(passphrase), salt, iterations,
        dklen=_KEY_SIZE)


def encrypt_bytes(plaintext: bytes, passphrase,
                  iterations: int = DEFAULT_ITERATIONS) -> bytes:
    """Encrypt ``plaintext`` with a passphrase-derived key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not _passphrase_bytes(passphrase):
        raise BackupCryptoError(
            "A backup cannot be encrypted with an empty passphrase.")
    salt = os.urandom(_SALT_SIZE)
    nonce = os.urandom(_NONCE_SIZE)
    header = MAGIC + struct.pack(">I", iterations) + salt + nonce
    key = _derive_key(passphrase, salt, iterations)
    ct = AESGCM(key).encrypt(nonce, plaintext, header)
    return header + ct


def decrypt_bytes(blob: bytes, passphrase) -> bytes:
    """Decrypt a ``FCBENC1`` blob. Raises BackupCryptoError on failure."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not is_encrypted_bytes(blob):
        raise BackupCryptoError("This backup is not encrypted.")
    if len(blob) <= _HEADER_SIZE:
        raise BackupCryptoError(
            "This backup could not be opened. It may be damaged, "
            "or the passphrase is wrong.")
    iterations = struct.unpack(
        ">I", blob[len(MAGIC):len(MAGIC) + 4])[0]
    if iterations < 1 or iterations > 10_000_000:
        raise BackupCryptoError(
            "This backup could not be opened. It may be damaged, "
            "or the passphrase is wrong.")
    salt_start = len(MAGIC) + 4
    salt = blob[salt_start:salt_start + _SALT_SIZE]
    nonce = blob[salt_start + _SALT_SIZE:_HEADER_SIZE]
    header = blob[:_HEADER_SIZE]
    ct = blob[_HEADER_SIZE:]
    key = _derive_key(passphrase, salt, iterations)
    try:
        return AESGCM(key).decrypt(nonce, ct, header)
    except Exception as exc:
        # AESGCM raises InvalidTag for a wrong key or tampering; any
        # other crypto error here also means "cannot open".
        raise BackupCryptoError(
            "This backup could not be opened. It may be damaged, "
            "or the passphrase is wrong.") from exc


def is_encrypted_bytes(data: bytes) -> bool:
    return isinstance(data, (bytes, bytearray)) and bytes(data[:len(MAGIC)]) == MAGIC


def is_encrypted_file(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def make_passphrase_verifier(passphrase,
                               iterations: int = DEFAULT_ITERATIONS) -> str:
    """A check-only verifier: ``pbkdf2$<iters>$<salt_hex>$<digest_hex>``.

    Never the passphrase and never a file key -- just enough to tell
    whether a typed passphrase is the right one.
    """
    salt = os.urandom(_SALT_SIZE)
    digest = hashlib.pbkdf2_hmac(
        "sha256", _passphrase_bytes(passphrase), salt, iterations,
        dklen=_KEY_SIZE)
    return "pbkdf2$%d$%s$%s" % (iterations, salt.hex(), digest.hex())


def verify_passphrase(passphrase, verifier) -> bool:
    """True when ``passphrase`` matches the stored verifier.

    Malformed verifiers fail closed: this returns False (never
    raises, never hangs) for bad field counts, bad hex, non-string
    input, or an iteration count outside 1..10,000,000 -- the same
    bound :func:`decrypt_bytes` applies to backup file headers.
    """
    if not verifier or not isinstance(verifier, str):
        return False
    try:
        scheme, iters_s, salt_hex, digest_hex = verifier.split("$", 3)
        if scheme != "pbkdf2":
            return False
        iterations = int(iters_s)
        if iterations < 1 or iterations > 10_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", _passphrase_bytes(passphrase), salt, iterations,
            dklen=len(expected) or _KEY_SIZE)
    except (ValueError, TypeError, BackupCryptoError):
        return False
    return hmac.compare_digest(actual, expected)
