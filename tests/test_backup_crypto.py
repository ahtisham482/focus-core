"""Roadmap 1.5b: passphrase-encrypted backup payloads.

TDD suite covering the blackboard completion criteria.
Everything runs against tmp dirs -- the real DB/backups are never touched.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys

import pytest

from focuscore import backup, columncrypto, paths, store
from focuscore import backupcrypto


PASSPHRASE = "correct horse battery staple"
WRONG = "wrong passphrase entirely"


def _make_db(path, marker):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES (?)", (marker,))
    conn.commit()
    conn.close()


def _marker(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "live.db"
    _make_db(db, "original")
    bdir = tmp_path / "backups"
    bdir.mkdir()
    userdata = tmp_path / "userdata"
    userdata.mkdir()
    monkeypatch.setattr(paths, "user_data_dir", lambda: userdata)
    return {"db": db, "bdir": bdir, "userdata": userdata, "tmp": tmp_path}


def _enable_encryption(db, passphrase=PASSPHRASE):
    store.set_setting("backup_encryption_enabled", "1", path=str(db))
    verifier = backupcrypto.make_passphrase_verifier(passphrase)
    store.set_setting("backup_passphrase_verifier", verifier, path=str(db))
    return verifier


# ------------------------------------------------------------- crypto ----

def test_crypto_round_trip():
    blob = backupcrypto.encrypt_bytes(b"hello backup bytes", PASSPHRASE)
    assert blob.startswith(backupcrypto.MAGIC)
    assert backupcrypto.decrypt_bytes(blob, PASSPHRASE) == b"hello backup bytes"


def test_crypto_wrong_passphrase_raises_dedicated():
    blob = backupcrypto.encrypt_bytes(b"secret", PASSPHRASE)
    with pytest.raises(backupcrypto.BackupCryptoError):
        backupcrypto.decrypt_bytes(blob, WRONG)


def test_crypto_tampered_ciphertext_raises():
    blob = bytearray(backupcrypto.encrypt_bytes(b"secret data", PASSPHRASE))
    blob[-1] ^= 0x01
    with pytest.raises(backupcrypto.BackupCryptoError):
        backupcrypto.decrypt_bytes(bytes(blob), PASSPHRASE)


def test_crypto_tampered_header_raises():
    blob = bytearray(backupcrypto.encrypt_bytes(b"secret data", PASSPHRASE))
    # Flip a salt byte (header is AAD, so tamper must be detected).
    blob[len(backupcrypto.MAGIC) + 4] ^= 0x01
    with pytest.raises(backupcrypto.BackupCryptoError):
        backupcrypto.decrypt_bytes(bytes(blob), PASSPHRASE)


def test_crypto_per_file_salt_nonce_unique():
    a = backupcrypto.encrypt_bytes(b"same plaintext", PASSPHRASE)
    b = backupcrypto.encrypt_bytes(b"same plaintext", PASSPHRASE)
    assert a != b
    assert a[: len(backupcrypto.MAGIC)] == b[: len(backupcrypto.MAGIC)]
    # salt+nonce region differs
    assert a[len(backupcrypto.MAGIC):] != b[len(backupcrypto.MAGIC):]


def test_crypto_iterations_stored_in_header():
    blob = backupcrypto.encrypt_bytes(b"x", PASSPHRASE)
    import struct

    hdr = blob[len(backupcrypto.MAGIC):len(backupcrypto.MAGIC) + 4]
    iters = struct.unpack(">I", hdr)[0]
    assert iters == 600000


def test_verifier_format_and_check():
    verifier = backupcrypto.make_passphrase_verifier(PASSPHRASE)
    parts = verifier.split("$")
    assert parts[0] == "pbkdf2"
    assert int(parts[1]) >= 1
    bytes.fromhex(parts[2])
    bytes.fromhex(parts[3])
    assert backupcrypto.verify_passphrase(PASSPHRASE, verifier) is True
    assert backupcrypto.verify_passphrase(WRONG, verifier) is False
    assert PASSPHRASE not in verifier


def test_is_encrypted_detection():
    blob = backupcrypto.encrypt_bytes(b"data", PASSPHRASE)
    assert backupcrypto.is_encrypted_bytes(blob) is True
    assert backupcrypto.is_encrypted_bytes(b"SQLite format 3\x00rest") is False
    assert backupcrypto.is_encrypted_bytes(b"") is False


# -------------------------------------------------------- columncrypto ----

def test_columncrypto_bytes_round_trip():
    blob = columncrypto.protect_bytes(b"passphrase bytes")
    assert columncrypto.unprotect_bytes(blob) == b"passphrase bytes"


def test_columncrypto_bytes_raises_on_failure(monkeypatch):
    class Exploding:
        name = "exploding"

        def protect(self, data):
            raise RuntimeError("boom")

        def unprotect(self, data):
            raise RuntimeError("boom")

    monkeypatch.setattr(columncrypto, "_protector", Exploding())
    with pytest.raises(columncrypto.ColumnCryptoError):
        columncrypto.protect_bytes(b"x")
    with pytest.raises(columncrypto.ColumnCryptoError):
        columncrypto.unprotect_bytes(b"x")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists only on Windows")
def test_real_dpapi_protects_bytes_via_public_api():
    blob = columncrypto.protect_bytes(b"window title bytes")
    assert blob != b"window title bytes"
    assert columncrypto.unprotect_bytes(blob) == b"window title bytes"


# ------------------------------------------------------------- backup ----

def test_create_when_enabled_publishes_encrypted(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    path = backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    data = path.read_bytes()
    assert data.startswith(backupcrypto.MAGIC)
    # sqlite cannot open ciphertext as a database
    with pytest.raises(sqlite3.DatabaseError):
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("SELECT v FROM t").fetchone()
        finally:
            conn.close()
    # sidecar matches stored (ciphertext) bytes
    sidecar = path.parent / (path.name + ".sha256")
    assert sidecar.read_text(encoding="utf-8").strip() == _sha256(path)
    # list_backups flags it
    listed = backup.list_backups(dest_dir=bdir)
    assert listed[0]["encrypted"] is True


def test_create_plaintext_when_disabled_has_no_magic(env):
    db, bdir = env["db"], env["bdir"]
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    assert not path.read_bytes().startswith(backupcrypto.MAGIC)
    listed = backup.list_backups(dest_dir=bdir)
    assert listed[0]["encrypted"] is False
    assert _marker(path) == "original"


def test_create_via_cache_when_enabled(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    backup.write_cached_passphrase(PASSPHRASE, db_path=db)
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    assert path.read_bytes().startswith(backupcrypto.MAGIC)


def test_enabled_but_locked_create_writes_nothing(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    # no cache, no explicit passphrase
    assert backup.read_cached_passphrase(db_path=db) is None
    with pytest.raises(backup.BackupLockedError):
        backup.create_backup(db_path=db, dest_dir=bdir)
    assert backup.list_backups(dest_dir=bdir) == []
    assert list(bdir.iterdir()) == []


def test_restore_with_passphrase_round_trips(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    path = backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    _make_db(db, "changed")
    safety = backup.restore_backup(
        path.name, db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    assert _marker(db) == "original"
    assert safety is not None and _marker(safety) == "changed"


def test_restore_via_cache(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    backup.write_cached_passphrase(PASSPHRASE, db_path=db)
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    _make_db(db, "changed")
    backup.restore_backup(path.name, db_path=db, dest_dir=bdir)
    assert _marker(db) == "original"


def test_failed_restore_leaves_live_db_identical(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    path = backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    _make_db(db, "changed")
    before = db.read_bytes()
    with pytest.raises(ValueError):
        backup.restore_backup(path.name, db_path=db, dest_dir=bdir, passphrase=WRONG)
    assert db.read_bytes() == before
    assert _marker(db) == "changed"
    # no safety copy littered on failure before modification
    assert not list(db.parent.glob("*.pre-restore-*"))


def test_failed_restore_no_passphrase_leaves_db_identical(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    path = backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    _make_db(db, "changed")
    before = db.read_bytes()
    with pytest.raises(ValueError):
        backup.restore_backup(path.name, db_path=db, dest_dir=bdir)
    assert db.read_bytes() == before


def test_legacy_plaintext_backup_still_restores(env):
    db, bdir = env["db"], env["bdir"]
    path = backup.create_backup(db_path=db, dest_dir=bdir)
    _make_db(db, "changed")
    backup.restore_backup(path.name, db_path=db, dest_dir=bdir)
    assert _marker(db) == "original"


def test_force_plaintext_snapshot_stays_plaintext_when_enabled(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    backup.write_cached_passphrase(PASSPHRASE, db_path=db)
    path = backup.create_backup(db_path=db, dest_dir=bdir, force_plaintext=True)
    assert not path.read_bytes().startswith(backupcrypto.MAGIC)
    assert backup.list_backups(dest_dir=bdir)[0]["encrypted"] is False
    assert _marker(path) == "original"


def test_encrypted_restore_rejects_tampered_checksum_first(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    path = backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    with open(path, "ab") as f:
        f.write(b"tamper")
    before = db.read_bytes()
    with pytest.raises(ValueError):
        backup.restore_backup(
            path.name, db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    assert db.read_bytes() == before


def test_backup_if_stale_locked_does_not_raise(env):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    result = backup.backup_if_stale(db_path=db, dest_dir=bdir)
    assert result is None
    assert backup.list_backups(dest_dir=bdir) == []


def test_passphrase_absent_from_settings_db_and_logs(env, caplog):
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    backup.write_cached_passphrase(PASSPHRASE, db_path=db)
    with caplog.at_level(logging.DEBUG):
        path = backup.create_backup(db_path=db, dest_dir=bdir)
        backup.restore_backup(
            path.name, db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    # settings values in DB must not contain passphrase
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    for _k, v in rows:
        assert PASSPHRASE not in (v or "")
    # DB file bytes must not contain passphrase (backup is ciphertext; live DB restored)
    # cache file is under userdata, not inside DB folder's db file
    assert PASSPHRASE.encode() not in db.read_bytes()
    # logs must not contain passphrase
    assert PASSPHRASE not in caplog.text
    # cache file exists under userdata and is not plaintext passphrase
    cache = backup.cache_path()
    assert cache.parent == env["userdata"]
    assert cache.exists()
    if sys.platform == "win32":
        # Real DPAPI wrapping must hide the passphrase on Windows.
        assert PASSPHRASE.encode() not in cache.read_bytes()


def test_cache_round_trip_and_clear(env):
    db = env["db"]
    _enable_encryption(db)
    backup.write_cached_passphrase(PASSPHRASE, db_path=db)
    assert backup.read_cached_passphrase(db_path=db) == PASSPHRASE
    backup.clear_cached_passphrase()
    assert backup.read_cached_passphrase(db_path=db) is None


def test_cache_wrong_verifier_is_locked(env):
    db = env["db"]
    _enable_encryption(db, passphrase=PASSPHRASE)
    # Write a cache for a different passphrase directly via protector
    raw = columncrypto.protect_bytes(WRONG.encode("utf-8"))
    backup.cache_path().parent.mkdir(parents=True, exist_ok=True)
    backup.cache_path().write_bytes(raw)
    assert backup.read_cached_passphrase(db_path=db) is None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists only on Windows")
def test_win32_dpapi_cache_round_trip(env):
    db = env["db"]
    _enable_encryption(db)
    backup.write_cached_passphrase(PASSPHRASE, db_path=db)
    cache_bytes = backup.cache_path().read_bytes()
    assert PASSPHRASE.encode() not in cache_bytes
    assert backup.read_cached_passphrase(db_path=db) == PASSPHRASE


# -------------------------------------------------------------- routes ----

@pytest.fixture
def dash_env(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    db = tmp_path / "t.db"
    _make_db(db, "original")
    store.init_db(str(db))
    bdir = tmp_path / "backups"
    bdir.mkdir()
    userdata = tmp_path / "userdata"
    userdata.mkdir()
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", str(db))
    monkeypatch.setattr(paths, "user_data_dir", lambda: userdata)
    monkeypatch.setattr(backup, "backup_dir", lambda dest_dir=None: bdir)
    import dashboard.app as dash_app

    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    return {"client": client, "db": db, "bdir": bdir,
            "userdata": userdata, "tmp": tmp_path}


def _setting(db, key):
    return store.get_setting(key, path=str(db))


def test_backup_page_shows_encrypted_marker(dash_env):
    _enable_encryption(dash_env["db"])
    backup.write_cached_passphrase(PASSPHRASE, db_path=dash_env["db"])
    backup.create_backup(db_path=dash_env["db"], dest_dir=dash_env["bdir"])
    html = dash_env["client"].get("/backup").data.decode()
    assert "Encrypted" in html


def test_backup_page_enable_flow(dash_env):
    client = dash_env["client"]
    html = client.get("/backup").data.decode()
    assert "passphrase" in html.lower()
    low = html.lower()
    assert "unrecoverable" in low or "no recovery" in low
    resp = client.post(
        "/backup/encryption/enable",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE},
    )
    assert resp.status_code in (302, 200)
    assert _setting(dash_env["db"], "backup_encryption_enabled") == "1"
    verifier = _setting(dash_env["db"], "backup_passphrase_verifier")
    assert verifier and backupcrypto.verify_passphrase(PASSPHRASE, verifier)
    assert PASSPHRASE not in verifier
    # cache written after verifier check
    assert backup.read_cached_passphrase(db_path=dash_env["db"]) == PASSPHRASE


def test_enable_rejects_short_and_mismatch(dash_env):
    client = dash_env["client"]
    resp = client.post(
        "/backup/encryption/enable",
        data={"passphrase": "short", "passphrase_confirm": "short"},
    )
    assert resp.status_code == 400
    assert _setting(dash_env["db"], "backup_encryption_enabled") != "1"
    resp2 = client.post(
        "/backup/encryption/enable",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": WRONG},
    )
    assert resp2.status_code == 400
    assert _setting(dash_env["db"], "backup_encryption_enabled") != "1"


def test_disable_requires_correct_passphrase(dash_env):
    client = dash_env["client"]
    client.post(
        "/backup/encryption/enable",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE},
    )
    # wrong passphrase cannot disable
    resp = client.post("/backup/encryption/disable", data={"passphrase": WRONG})
    assert resp.status_code == 400
    assert _setting(dash_env["db"], "backup_encryption_enabled") == "1"
    resp = client.post("/backup/encryption/disable", data={"passphrase": PASSPHRASE})
    assert resp.status_code in (302, 200)
    assert _setting(dash_env["db"], "backup_encryption_enabled") == "0"


def test_unlock_flow_when_locked(dash_env):
    client = dash_env["client"]
    client.post(
        "/backup/encryption/enable",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE},
    )
    backup.clear_cached_passphrase()
    html = client.get("/backup").data.decode()
    assert "unlock" in html.lower()
    resp = client.post("/backup/encryption/unlock", data={"passphrase": WRONG})
    assert resp.status_code == 400
    assert backup.read_cached_passphrase(db_path=dash_env["db"]) is None
    resp = client.post("/backup/encryption/unlock", data={"passphrase": PASSPHRASE})
    assert resp.status_code in (302, 200)
    assert backup.read_cached_passphrase(db_path=dash_env["db"]) == PASSPHRASE


def test_route_restore_with_passphrase(dash_env):
    client = dash_env["client"]
    _enable_encryption(dash_env["db"])
    path = backup.create_backup(
        db_path=dash_env["db"], dest_dir=dash_env["bdir"], passphrase=PASSPHRASE
    )
    _make_db(dash_env["db"], "changed")
    resp = client.post(
        "/backup/restore", data={"name": path.name, "passphrase": PASSPHRASE}
    )
    assert resp.status_code == 200
    assert _marker(dash_env["db"]) == "original"


def test_route_restore_wrong_passphrase_no_change(dash_env):
    client = dash_env["client"]
    _enable_encryption(dash_env["db"])
    path = backup.create_backup(
        db_path=dash_env["db"], dest_dir=dash_env["bdir"], passphrase=PASSPHRASE
    )
    _make_db(dash_env["db"], "changed")
    before = dash_env["db"].read_bytes()
    resp = client.post(
        "/backup/restore", data={"name": path.name, "passphrase": WRONG}
    )
    assert resp.status_code == 400
    assert dash_env["db"].read_bytes() == before


# ------------------------------------------- 1.5b repair (critic notes) ----

@pytest.mark.parametrize("verifier", [
    "pbkdf2$0$00$00",
    "pbkdf2$-3$00$00",
    "pbkdf2$2000000000$00$00",
    "pbkdf2$10000001$00$00",
    "pbkdf2$abc$00$00",
    "pbkdf2$100$zz$00",
    "pbkdf2$100$00$zz",
    "pbkdf2$100$00",
    "pbkdf2$100$00$00$extra",
    "sha256$100$00$00",
    "",
    "not-a-verifier",
], ids=["zero-iters", "negative-iters", "huge-iters", "over-cap-iters",
        "bad-int", "bad-salt-hex", "bad-digest-hex", "too-few-fields",
        "too-many-fields", "wrong-scheme", "empty", "garbage"])
def test_verify_passphrase_malformed_returns_false_fast(verifier):
    # Critic obj.1: a malformed verifier must fail closed -- return
    # False, never raise, never hang on an absurd iteration count.
    import time

    start = time.monotonic()
    assert backupcrypto.verify_passphrase(PASSPHRASE, verifier) is False
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("verifier", [
    None, 123, b"pbkdf2$1$00$00", ["pbkdf2$1$00$00"], {"v": 1},
], ids=["none", "int", "bytes", "list", "dict"])
def test_verify_passphrase_nonstring_verifier_returns_false(verifier):
    assert backupcrypto.verify_passphrase(PASSPHRASE, verifier) is False


def test_verify_passphrase_bad_passphrase_types_return_false():
    verifier = backupcrypto.make_passphrase_verifier(PASSPHRASE)
    assert backupcrypto.verify_passphrase(None, verifier) is False
    assert backupcrypto.verify_passphrase(123, verifier) is False


def test_malformed_verifier_engine_fails_closed(env):
    # Critic obj.1 at engine level: with a malformed verifier stored in
    # settings and a cache file present, "locked" must surface as
    # locked -- is_locked returns True and create writes nothing.
    db, bdir = env["db"], env["bdir"]
    store.set_setting("backup_encryption_enabled", "1", path=str(db))
    store.set_setting(
        "backup_passphrase_verifier", "pbkdf2$0$00$00", path=str(db))
    # A cache file must exist for the verifier to be consulted at all.
    cache = backup.cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(columncrypto.protect_bytes(PASSPHRASE.encode("utf-8")))
    assert backup.is_locked(db_path=db) is True
    with pytest.raises(backup.BackupLockedError):
        backup.create_backup(db_path=db, dest_dir=bdir)
    with pytest.raises(backup.BackupLockedError):
        backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    assert list(bdir.iterdir()) == []


def test_create_roundtrip_verify_fault_injection(env, monkeypatch):
    # Critic obj.2a: if the in-memory encrypt->decrypt round-trip in
    # create_backup reproduced the wrong bytes, publishing must not
    # happen -- no backup file, no sidecar.
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    monkeypatch.setattr(
        backupcrypto, "decrypt_bytes",
        lambda blob, passphrase: b"not the plaintext")
    with pytest.raises(backupcrypto.BackupCryptoError):
        backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    assert list(bdir.iterdir()) == []
    assert backup.list_backups(dest_dir=bdir) == []


def test_restore_checksum_gate_runs_before_decrypt(env, monkeypatch):
    # Critic obj.2b: a corrupted sidecar must stop the restore at the
    # checksum gate -- decryption is never attempted.
    db, bdir = env["db"], env["bdir"]
    _enable_encryption(db)
    path = backup.create_backup(db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    sidecar = path.parent / (path.name + ".sha256")
    sidecar.write_text("0" * 64 + "\n", encoding="utf-8")
    calls = []
    real_decrypt = backupcrypto.decrypt_bytes

    def recording_decrypt(blob, passphrase):
        calls.append(1)
        return real_decrypt(blob, passphrase)

    monkeypatch.setattr(backupcrypto, "decrypt_bytes", recording_decrypt)
    before = db.read_bytes()
    with pytest.raises(ValueError, match="safety check"):
        backup.restore_backup(
            path.name, db_path=db, dest_dir=bdir, passphrase=PASSPHRASE)
    assert calls == []
    assert db.read_bytes() == before


def test_crypto_tampered_nonce_raises():
    # Critic obj.2c: the nonce lives in the authenticated header
    # region; flipping one byte must make decrypt fail closed.
    blob = bytearray(backupcrypto.encrypt_bytes(b"secret data", PASSPHRASE))
    nonce_offset = len(backupcrypto.MAGIC) + 4 + 16
    blob[nonce_offset] ^= 0x01
    with pytest.raises(backupcrypto.BackupCryptoError):
        backupcrypto.decrypt_bytes(bytes(blob), PASSPHRASE)


def test_encrypt_empty_passphrase_rejected():
    # Critic obj.3: the engine itself refuses an empty passphrase.
    with pytest.raises(backupcrypto.BackupCryptoError):
        backupcrypto.encrypt_bytes(b"data", "")
    with pytest.raises(backupcrypto.BackupCryptoError):
        backupcrypto.encrypt_bytes(b"data", b"")


def test_create_with_empty_passphrase_publishes_nothing(env):
    db, bdir = env["db"], env["bdir"]
    with pytest.raises(backupcrypto.BackupCryptoError):
        backup.create_backup(db_path=db, dest_dir=bdir, passphrase="")
    assert list(bdir.iterdir()) == []


def test_enable_cache_write_failure_lands_on_backup_locked(dash_env, monkeypatch):
    # Critic obj.5: if the cache write fails after the flags flip, the
    # enable request must not 500 -- encryption is on, the state is
    # the coherent locked state, and /backup renders it.
    def boom(passphrase, db_path=None):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "write_cached_passphrase", boom)
    client = dash_env["client"]
    resp = client.post(
        "/backup/encryption/enable",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/backup")
    assert _setting(dash_env["db"], "backup_encryption_enabled") == "1"
    assert backup.is_locked(db_path=dash_env["db"]) is True
    html = client.get("/backup").data.decode()
    assert "unlock" in html.lower()
