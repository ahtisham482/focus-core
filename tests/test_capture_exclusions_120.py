"""Roadmap 1.20: capture-scope blocklist (HKLM exclusion at ingest).

The blocklist lives in the Roadmap 1.12 config reader as the
``capture_exclusions`` key (machine/HKLM tier > config.toml >
FOCUSCORE_CAPTURE_EXCLUSIONS env) and is applied by
``pipeline.run_day`` at ingest: events whose ``app`` exactly matches
an excluded process name (case-insensitive, ``.exe`` suffix ignored)
are dropped before scoring and storage, so the excluded app's window
titles and URLs never reach the database.

Hermetic by construction: fake machine reader + tmp config.toml
(pattern from tests/test_config.py), a fake ActivityWatch client,
and a tmp DB passed explicitly. Nothing touches the repo dev DB,
the host registry, or the network.
"""

import logging
import os
import sqlite3
from datetime import date

import pytest

from focuscore import config, pipeline

DAY_ONE = date(2026, 1, 5)
DAY_TWO = date(2026, 1, 6)

VAULT_APP = "1Password.exe"
VAULT_TITLE = "1Password Vault - Personal Bank Login"
VAULT_URL = "https://my.1password.example/vault"


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    """Hermetic 1.12 config state (fake HKLM tier, tmp TOML, clean env)."""
    for var in [v for v in os.environ if v.startswith("FOCUSCORE_")]:
        monkeypatch.delenv(var, raising=False)
    state = {"machine": {}, "toml": tmp_path / "config.toml"}
    monkeypatch.setattr(
        config, "_machine_reader", lambda: state["machine"])
    monkeypatch.setattr(
        config, "config_file_path", lambda: state["toml"])
    return state


def _write_toml(cfg, text):
    cfg["toml"].write_text(text, encoding="utf-8")


# ------------------------------------------------- config resolution ---

def test_exclusions_unset_by_default(cfg):
    assert config.get_capture_exclusions() == []


def test_exclusions_from_toml_array(cfg):
    _write_toml(cfg, 'capture_exclusions = ["1Password.exe", "KeePassXC"]\n')
    assert config.get_capture_exclusions() == ["1Password.exe", "KeePassXC"]


def test_exclusions_machine_beats_toml(cfg):
    """Higher tier wins, same as every other 1.12 knob."""
    cfg["machine"]["capture_exclusions"] = ["MachineApp.exe"]
    _write_toml(cfg, 'capture_exclusions = ["TomlApp.exe"]\n')
    assert config.get_capture_exclusions() == ["MachineApp.exe"]


def test_exclusions_toml_beats_env(cfg, monkeypatch):
    _write_toml(cfg, 'capture_exclusions = ["TomlApp.exe"]\n')
    monkeypatch.setenv("FOCUSCORE_CAPTURE_EXCLUSIONS", "EnvApp.exe")
    assert config.get_capture_exclusions() == ["TomlApp.exe"]


def test_exclusions_from_env_string(cfg, monkeypatch):
    monkeypatch.setenv(
        "FOCUSCORE_CAPTURE_EXCLUSIONS", "1Password.exe; KeePassXC, bank.exe")
    assert config.get_capture_exclusions() == [
        "1Password.exe", "KeePassXC", "bank.exe"]


def test_exclusions_machine_string_is_split(cfg):
    """A REG_SZ machine value is one ;-separated string."""
    cfg["machine"]["capture_exclusions"] = "1Password.exe;KeePassXC"
    assert config.get_capture_exclusions() == ["1Password.exe", "KeePassXC"]


def test_exclusions_machine_multi_sz_list(cfg):
    """A REG_MULTI_SZ machine value arrives as a list already."""
    cfg["machine"]["capture_exclusions"] = ["1Password.exe", "KeePassXC"]
    assert config.get_capture_exclusions() == ["1Password.exe", "KeePassXC"]


def test_exclusions_entries_stripped_and_empties_dropped(cfg):
    _write_toml(cfg, 'capture_exclusions = ["  1Password.exe ", ""]\n')
    assert config.get_capture_exclusions() == ["1Password.exe"]


def test_exclusions_invalid_machine_value_falls_through(cfg, caplog):
    cfg["machine"]["capture_exclusions"] = 42
    _write_toml(cfg, 'capture_exclusions = ["TomlApp.exe"]\n')
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_capture_exclusions() == ["TomlApp.exe"]
    assert "capture_exclusions" in caplog.text


def test_exclusions_list_with_non_string_falls_through(cfg, monkeypatch):
    _write_toml(cfg, 'capture_exclusions = [42]\n')
    monkeypatch.setenv("FOCUSCORE_CAPTURE_EXCLUSIONS", "EnvApp.exe")
    assert config.get_capture_exclusions() == ["EnvApp.exe"]


# ------------------------------------------------------ ingest filter ---

def _event(minute, app, title, url=None):
    return {
        "ts": f"2026-01-05T09:{minute:02d}:00",
        "duration": 60.0,
        "app": app,
        "title": title,
        "url": url,
    }


class _FakeClient:
    """Stands in for ActivityWatchClient with a fixed event feed."""

    def __init__(self, events):
        self._events = events

    def fetch_day(self, day):
        return list(self._events), 0.0


def _feed():
    """One hour in the excluded vault app + kept neighbours."""
    events = [
        _event(m, VAULT_APP, VAULT_TITLE, VAULT_URL) for m in range(60)
    ]
    events.append(_event(0, "not1password.exe", "Unrelated notes app"))
    events.append(_event(1, "keepass", "KeePassXC - passwords.kdbx"))
    events.append(_event(2, "code", "store.py - Visual Studio Code"))
    events.append(_event(3, "code", "config.py - Visual Studio Code"))
    return events


@pytest.fixture()
def feed(monkeypatch):
    monkeypatch.setattr(
        pipeline, "ActivityWatchClient",
        lambda: _FakeClient(_feed()))


def _count(db, sql, args=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _assert_vault_nowhere(db):
    """Zero vault rows in every table holding captured titles/URLs.

    Exact app equality (not LIKE): the kept near-miss app
    'not1password.exe' must never trip these checks.
    """
    assert _count(db, "SELECT COUNT(*) FROM activities WHERE app = ?",
                  (VAULT_APP,)) == 0
    for table in ("activities", "focus_blocks", "timesheet_entries"):
        assert _count(
            db, f"SELECT COUNT(*) FROM {table} WHERE app = ?",
            (VAULT_APP,)) == 0
        assert _count(
            db, f"SELECT COUNT(*) FROM {table} "
                "WHERE title LIKE '%1Password Vault%'") == 0
    for table in ("activities", "focus_blocks"):
        assert _count(
            db, f"SELECT COUNT(*) FROM {table} "
                "WHERE url LIKE '%my.1password.example%'") == 0


def test_hklm_exclusion_zero_rows_in_db(cfg, feed, tmp_path):
    """The roadmap acceptance test: HKLM exclusion -> DB query -> zero."""
    cfg["machine"]["capture_exclusions"] = ["1Password.exe"]
    db = str(tmp_path / "day.db")
    pipeline.run_day(DAY_ONE, db_path=db)
    _assert_vault_nowhere(db)
    # Kept neighbours: near-miss process, other apps.
    assert _count(db, "SELECT COUNT(*) FROM activities") == 4
    assert _count(db, "SELECT COUNT(*) FROM activities WHERE app = ?",
                  ("not1password.exe",)) == 1


def test_user_level_toml_exclusion_zero_rows(cfg, feed, tmp_path):
    _write_toml(cfg, 'capture_exclusions = ["1Password.exe"]\n')
    db = str(tmp_path / "day.db")
    pipeline.run_day(DAY_ONE, db_path=db)
    _assert_vault_nowhere(db)


def test_matching_is_case_and_exe_insensitive(cfg, feed, tmp_path):
    """'1password' (no suffix, lower) matches app '1Password.exe';
    'KEEPASS.EXE' matches app 'keepass'."""
    cfg["machine"]["capture_exclusions"] = ["1password", "KEEPASS.EXE"]
    db = str(tmp_path / "day.db")
    pipeline.run_day(DAY_ONE, db_path=db)
    _assert_vault_nowhere(db)
    assert _count(db, "SELECT COUNT(*) FROM activities WHERE app = ?",
                  ("keepass",)) == 0
    assert _count(db, "SELECT COUNT(*) FROM activities") == 3


def test_no_exclusions_stores_everything(cfg, feed, tmp_path):
    db = str(tmp_path / "day.db")
    pipeline.run_day(DAY_ONE, db_path=db)
    assert _count(db, "SELECT COUNT(*) FROM activities") == 64
    assert _count(db, "SELECT COUNT(*) FROM activities WHERE app = ?",
                  (VAULT_APP,)) == 60


def test_policy_change_applies_on_next_tick(cfg, feed, tmp_path):
    """Config is re-read per run: no restart needed for new policy."""
    db = str(tmp_path / "day.db")
    pipeline.run_day(DAY_ONE, db_path=db)
    assert _count(db, "SELECT COUNT(*) FROM activities WHERE app = ?",
                  (VAULT_APP,)) == 60
    cfg["machine"]["capture_exclusions"] = ["1Password.exe"]
    pipeline.run_day(DAY_TWO, db_path=db)
    assert _count(
        db, "SELECT COUNT(*) FROM activities WHERE day = ? AND app = ?",
        (DAY_TWO.isoformat(), VAULT_APP)) == 0


def test_dropped_count_is_logged(cfg, feed, tmp_path, caplog):
    cfg["machine"]["capture_exclusions"] = ["1Password.exe"]
    db = str(tmp_path / "day.db")
    with caplog.at_level(logging.DEBUG, logger="focuscore.pipeline"):
        pipeline.run_day(DAY_ONE, db_path=db)
    messages = [r.getMessage() for r in caplog.records]
    assert any("dropped 60" in m for m in messages), messages


def test_degenerate_exe_entry_is_ignored_and_appless_events_kept(
        cfg, monkeypatch, tmp_path):
    """Repair (critic M2): an exclusion entry of ".exe" normalizes to
    the empty string at the matching layer, so before this guard it
    acted as an exclusion for every app-less event. Entries that
    normalize to empty must be ignored -- they name no process."""
    cfg["machine"]["capture_exclusions"] = [".exe", "  .EXE  "]
    assert config.get_capture_exclusions() == []
    events = [
        _event(0, "", "Window with no app name recorded"),
        _event(1, "code", "store.py - Visual Studio Code"),
    ]
    monkeypatch.setattr(
        pipeline, "ActivityWatchClient",
        lambda: _FakeClient(events))
    db = str(tmp_path / "day.db")
    pipeline.run_day(DAY_ONE, db_path=db)
    assert _count(
        db, "SELECT COUNT(*) FROM activities WHERE app = ''") == 1
    assert _count(db, "SELECT COUNT(*) FROM activities") == 2
