"""Roadmap 0.4: one-click Export diagnostics (TDD).

The diagnostics zip is a support artifact. Privacy is the hard rule:
it must NEVER contain the database, activity data, unredacted secrets,
or the user's home paths / username.
"""

import io
import os
import sqlite3
import zipfile

import pytest

from focuscore import store


SECRET_KEY_VALUE = "super-secret-token-9f8e7d6c5b4a3210feed"
INNOCENT_TOKEN_VALUE = "aK9xQ2mZ7pL4wR8tY3nB6vC1dF5gH0jK"
DB_MARKER = "private-activity-marker-xyz"


def _zip_members(blob):
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return {name: zf.read(name).decode("utf-8", "replace")
            for name in zf.namelist()}


def _all_text(blob):
    return "\n".join(_zip_members(blob).values())


@pytest.fixture()
def env(tmp_path):
    """An isolated data dir + db + log for the diagnostics builder."""
    db = str(tmp_path / "focuscore.db")
    store.init_db(db)
    store.set_setting("currency", "USD", path=db)
    store.set_setting("api_token", SECRET_KEY_VALUE, path=db)
    # A token-like value hiding under an innocent key name.
    store.set_setting("greeting", INNOCENT_TOKEN_VALUE, path=db)
    # Direct DB content that must never leak into the bundle.
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS marker (secret TEXT)")
    conn.execute("INSERT INTO marker VALUES (?)", (DB_MARKER,))
    conn.commit()
    conn.close()
    # Fake app log: 300 lines, plus lines naming the home dir + user.
    log_file = tmp_path / "focuscore.log"
    lines = ["2026-01-01 INFO focuscore: line %d" % i for i in range(300)]
    lines.append("2026-01-01 ERROR focuscore: failed to open %s"
                 % (tmp_path,))
    log_file.write_text("\n".join(lines), encoding="utf-8")
    return {"db": db, "log": str(log_file),
            "data_dir": str(tmp_path), "root": str(tmp_path)}


def _build(env):
    from focuscore import diagnostics
    return diagnostics.build_diagnostics_zip(
        db_path=env["db"], log_file=env["log"],
        data_dir=env["data_dir"], app_root=env["root"])


def test_zip_contains_expected_sections(env):
    members = _zip_members(_build(env))
    for name in ("README.txt", "system.txt", "settings.txt",
                 "data-dir.txt", "log-tail.txt", "installer.txt"):
        assert name in members, members.keys()


def test_zip_never_includes_the_database(env):
    blob = _build(env)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    assert not any(name.endswith(".db") for name in zf.namelist())
    assert DB_MARKER not in _all_text(blob)


def test_secret_setting_values_never_appear(env):
    text = _all_text(_build(env))
    assert SECRET_KEY_VALUE not in text
    assert INNOCENT_TOKEN_VALUE not in text


def test_benign_setting_still_shows_and_redaction_is_marked(env):
    settings = _zip_members(_build(env))["settings.txt"]
    assert "currency" in settings and "USD" in settings
    assert "REDACTED" in settings


def test_log_tail_is_anonymized_and_limited(env):
    tail = _zip_members(_build(env))["log-tail.txt"]
    home = os.path.expanduser("~")
    if os.sep in str(env["data_dir"]):
        assert str(env["data_dir"]) not in tail
    if home and home != os.sep:
        assert home not in tail
    assert "line 50" not in tail          # only the last 200 lines
    assert "line 299" in tail


def test_system_section_has_version_and_integrity(env):
    system = _zip_members(_build(env))["system.txt"]
    from focuscore import __version__
    assert __version__ in system
    assert "integrity_check" in system
    assert "ok" in system.lower()


def test_data_dir_listing_names_and_sizes_only(env):
    listing = _zip_members(_build(env))["data-dir.txt"]
    assert "focuscore.db" in listing      # name + size is fine
    assert DB_MARKER not in listing       # contents are not


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    import dashboard.app as dash_app
    dash_app.app.config["TESTING"] = True
    return dash_app.app.test_client()


def test_route_serves_a_zip(dash):
    resp = dash.get("/backup/diagnostics")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "focus-core-diagnostics" in resp.headers["Content-Disposition"]
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    assert "system.txt" in zf.namelist()


def test_backup_page_offers_export(dash):
    resp = dash.get("/backup")
    assert resp.status_code == 200
    assert b"Export diagnostics" in resp.data
    assert b"/backup/diagnostics" in resp.data


# ---------------------------------------------------------------------------
# Round 2 (critic objections): settings anonymization + wider secret shapes
# ---------------------------------------------------------------------------

def test_anonymize_scrubs_spaced_windows_username():
    """User profile names can contain spaces (``C:\\Users\\Spaced Name99``);
    the anonymizer must scrub the whole name, not just its first word."""
    from focuscore import diagnostics
    out = diagnostics.anonymize(
        r"failed to open C:\Users\Spaced Name99\notes.txt today")
    assert "Spaced Name99" not in out
    assert "Name99" not in out
    assert r"C:\Users\<user>" in out


def test_settings_value_with_spaced_username_is_anonymized(env):
    """Critic objection 1: settings.txt was never anonymized, so a spaced
    username inside a path value leaked verbatim."""
    store.set_setting(
        "drive_folder",
        r"C:\Users\Spaced Name99\Google Drive\Focus Core Backups",
        path=env["db"])
    blob = _build(env)
    assert "Spaced Name99" not in _all_text(blob)
    assert "Name99" not in _all_text(blob)
    settings = _zip_members(blob)["settings.txt"]
    # The benign value survives in anonymized form -- it does not vanish.
    assert "drive_folder" in settings
    assert "Focus Core Backups" in settings


def test_wide_secret_key_vocabulary(env):
    """Critic objection 2 (key names): passphrase / license / pin / key
    style setting names must redact their values."""
    store.set_setting("restore_phrase", "correct horse battery staple 42",
                      path=env["db"])
    store.set_setting("license_code", "XK9-22QM-88ZA", path=env["db"])
    store.set_setting("backup_pin", "9753102468", path=env["db"])
    store.set_setting("signing_key", "do-not-print-this-value",
                      path=env["db"])
    blob = _build(env)
    text = _all_text(blob)
    assert "correct horse battery staple" not in text
    assert "XK9-22QM-88ZA" not in text
    settings = _zip_members(blob)["settings.txt"]
    assert "9753102468" not in settings
    assert "do-not-print-this-value" not in settings


def test_secret_shapes_redacted_under_innocent_keys(env):
    """Critic objection 2 (value shapes): a multi-word passphrase and a
    dash-grouped code are secrets even under innocent key names."""
    store.set_setting("memo", "correct horse battery staple 42",
                      path=env["db"])
    store.set_setting("favorite_word", "XK9-22QM-88ZA", path=env["db"])
    text = _all_text(_build(env))
    assert "correct horse battery staple" not in text
    assert "XK9-22QM-88ZA" not in text


def test_benign_multiword_value_survives(env):
    """Over-redaction guard: a plain multi-word display value is not a
    secret and must stay visible."""
    store.set_setting("drive_label", "Focus Core Backups", path=env["db"])
    settings = _zip_members(_build(env))["settings.txt"]
    assert "Focus Core Backups" in settings
