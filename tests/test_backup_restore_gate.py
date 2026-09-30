"""Roadmap 1.9: confirmation gate for legacy backup restores.

The dashboard route classifies a backup server-side (via
focuscore.backup.classify_backup) before restoring:

* verified backup  -> restores in one POST, as before;
* TAMPERED backup (sidecar present, digest mismatch) -> hard-stop
  page, no consent form, never restores -- not even with a forged
  confirm field;
* LEGACY backup (no sidecar) -> first POST restores nothing and
  returns a 200 confirmation page; only a second POST with the exact
  checkbox consent restores.

Engine semantics (focuscore.backup.restore_backup) are untouched and
remain covered by test_backup_hardening.py. Everything here runs
against a TMP db + TMP backup dir only -- never the repo dev DB.
"""

import sqlite3

import pytest

from focuscore import backup, paths, store
from focuscore import backupcrypto


PASSPHRASE = "correct horse battery staple"


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
    return {"client": client, "db": db, "bdir": bdir, "tmp": tmp_path}


def _make_backup(env, sidecar="keep"):
    """Backup of the 'original' db, then make the live db 'changed'.

    sidecar: "keep" (verified), "drop" (legacy), "break" (tampered).
    """
    path = backup.create_backup(db_path=env["db"], dest_dir=env["bdir"])
    if sidecar == "drop":
        (path.parent / (path.name + ".sha256")).unlink()
    elif sidecar == "break":
        with open(path, "ab") as f:
            f.write(b"tampered-bytes")
    _make_db(env["db"], "changed")
    return path


# ------------------------------------------------------- helper unit ----

def test_classify_backup_verified_legacy_tampered(dash_env):
    good = _make_backup(dash_env, sidecar="keep")
    result = backup.classify_backup(good.name, dest_dir=dash_env["bdir"])
    assert result == {"name": good.name, "ok": True,
                      "reason": "checksum matches"}

    legacy = _make_backup(dash_env, sidecar="drop")
    result = backup.classify_backup(legacy.name,
                                    dest_dir=dash_env["bdir"])
    assert result["ok"] is False
    assert result["reason"] == "legacy backup, integrity not verifiable"

    bad = _make_backup(dash_env, sidecar="break")
    result = backup.classify_backup(bad.name, dest_dir=dash_env["bdir"])
    assert result["ok"] is False
    assert result["reason"] == "checksum mismatch"


def test_classify_backup_unknown_and_bad_name(dash_env):
    with pytest.raises(FileNotFoundError):
        backup.classify_backup("focuscore-20200101-000000.db",
                               dest_dir=dash_env["bdir"])
    with pytest.raises(ValueError):
        backup.classify_backup("../evil.db", dest_dir=dash_env["bdir"])


def test_verify_all_backups_shares_classification(dash_env):
    good = _make_backup(dash_env, sidecar="keep")
    legacy = _make_backup(dash_env, sidecar="drop")
    results = {r["name"]: r
               for r in backup.verify_all_backups(dest_dir=dash_env["bdir"])}
    assert results[good.name] == backup.classify_backup(
        good.name, dest_dir=dash_env["bdir"])
    assert results[legacy.name]["ok"] is False
    assert "legacy" in results[legacy.name]["reason"]


# ------------------------------------------------------------ legacy ----

def test_legacy_restore_without_consent_confirms_and_restores_nothing(
        dash_env):
    path = _make_backup(dash_env, sidecar="drop")
    before = dash_env["db"].read_bytes()

    resp = dash_env["client"].post("/backup/restore",
                                    data={"name": path.name})
    assert resp.status_code == 200
    html = resp.data.decode()

    # It is the confirmation page, not the success page.
    assert "Backup restored" not in html
    assert "Before you restore this backup" in html
    assert path.name in html
    # The explanation: predates safety checks, what a checkfile proves
    # for other backups, replace-not-undo + safety copy is the way back.
    assert "made before safety checks were added" in html
    assert "checkfile" in html
    assert "cannot be undone" in html
    assert "safety copy" in html
    # A fresh POST form for the same backup, server-validated consent.
    assert "action='/backup/restore'" in html
    assert "novalidate" in html
    assert "name='confirm_legacy_restore'" in html
    assert ("I understand this backup could not be checked, because "
            "it was made before safety checks were added.") in html
    assert "value='Restore this backup anyway'" in html

    # Nothing was restored: DB bytes identical, no safety copy made.
    assert dash_env["db"].read_bytes() == before
    assert _marker(dash_env["db"]) == "changed"
    assert not list(dash_env["tmp"].glob("*.pre-restore-*"))


def test_legacy_restore_with_consent_restores(dash_env):
    path = _make_backup(dash_env, sidecar="drop")

    resp = dash_env["client"].post(
        "/backup/restore",
        data={"name": path.name, "confirm_legacy_restore": "on"})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Backup restored" in html
    # The pre-existing post-restore legacy note stays.
    assert "made before safety checks" in html
    assert "could not be verified" in html
    assert _marker(dash_env["db"]) == "original"
    safeties = list(dash_env["tmp"].glob("*.pre-restore-*"))
    assert len(safeties) == 1 and _marker(safeties[0]) == "changed"


@pytest.mark.parametrize("bogus", ["false", "true", "1", "yes", ""])
def test_legacy_restore_bogus_consent_values_confirm_again(dash_env, bogus):
    path = _make_backup(dash_env, sidecar="drop")
    before = dash_env["db"].read_bytes()

    resp = dash_env["client"].post(
        "/backup/restore",
        data={"name": path.name, "confirm_legacy_restore": bogus})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Backup restored" not in html
    assert "Before you restore this backup" in html
    assert dash_env["db"].read_bytes() == before
    assert _marker(dash_env["db"]) == "changed"


# ----------------------------------------------------------- tampered ---

def test_tampered_restore_hard_stop_no_form(dash_env):
    path = _make_backup(dash_env, sidecar="break")
    before = dash_env["db"].read_bytes()

    resp = dash_env["client"].post("/backup/restore",
                                    data={"name": path.name})
    assert resp.status_code == 400
    html = resp.data.decode()
    assert "Restore stopped" in html
    assert "looks damaged or was changed after it was made" in html
    assert "stopped to protect your data" in html
    # No consent form is ever rendered for a tampered backup.
    assert "confirm_legacy_restore" not in html
    assert "Restore this backup anyway" not in html
    assert "Backup restored" not in html
    assert dash_env["db"].read_bytes() == before
    assert _marker(dash_env["db"]) == "changed"
    assert not list(dash_env["tmp"].glob("*.pre-restore-*"))


def test_tampered_restore_forged_consent_still_stopped(dash_env):
    path = _make_backup(dash_env, sidecar="break")
    before = dash_env["db"].read_bytes()

    resp = dash_env["client"].post(
        "/backup/restore",
        data={"name": path.name, "confirm_legacy_restore": "on"})
    assert resp.status_code == 400
    html = resp.data.decode()
    assert "Restore stopped" in html
    assert "confirm_legacy_restore" not in html
    assert dash_env["db"].read_bytes() == before
    assert _marker(dash_env["db"]) == "changed"
    assert not list(dash_env["tmp"].glob("*.pre-restore-*"))


# ---------------------------------------------------- unchanged paths ---

def test_verified_restore_in_one_post(dash_env):
    path = _make_backup(dash_env, sidecar="keep")

    resp = dash_env["client"].post("/backup/restore",
                                    data={"name": path.name})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Backup restored" in html
    assert "Before you restore this backup" not in html
    assert "could not be verified" not in html
    assert _marker(dash_env["db"]) == "original"


def test_encrypted_restore_unaffected(dash_env):
    store.set_setting("backup_encryption_enabled", "1",
                      path=str(dash_env["db"]))
    verifier = backupcrypto.make_passphrase_verifier(PASSPHRASE)
    store.set_setting("backup_passphrase_verifier", verifier,
                      path=str(dash_env["db"]))
    path = backup.create_backup(db_path=dash_env["db"],
                                dest_dir=dash_env["bdir"],
                                passphrase=PASSPHRASE)
    _make_db(dash_env["db"], "changed")

    resp = dash_env["client"].post(
        "/backup/restore",
        data={"name": path.name, "passphrase": PASSPHRASE})
    assert resp.status_code == 200
    assert "Backup restored" in resp.data.decode()
    assert _marker(dash_env["db"]) == "original"


def test_restore_unknown_name_still_400(dash_env):
    resp = dash_env["client"].post(
        "/backup/restore", data={"name": "focuscore-20200101-000000.db"})
    assert resp.status_code == 400
    assert "Could not restore" in resp.data.decode()


def test_restore_bad_name_still_400(dash_env):
    resp = dash_env["client"].post("/backup/restore",
                                    data={"name": "../evil.db"})
    assert resp.status_code == 400
    assert "Could not restore" in resp.data.decode()
