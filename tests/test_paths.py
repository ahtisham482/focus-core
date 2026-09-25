"""Tests for focuscore/paths.py: where user data lives in portable vs
installed mode. APP_ROOT is monkeypatched to temp dirs so nothing
touches the real filesystem layout.
"""

from pathlib import Path

import pytest

from focuscore import paths


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    monkeypatch.setattr(paths, "INSTALLED_MARKER", tmp_path / ".installed")
    return tmp_path


def test_portable_mode_uses_app_root(tmp_root):
    assert paths.is_installed() is False
    assert paths.data_dir() == tmp_root
    assert paths.db_path() == tmp_root / "focuscore.db"
    assert paths.backups_dir() == tmp_root / "backups"
    assert paths.onboarded_flag() == tmp_root / ".onboarded"


def test_legacy_db_is_grandfathered(tmp_path, monkeypatch):
    """A db next to the code keeps being used even in installed mode."""
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    monkeypatch.setattr(paths, "INSTALLED_MARKER", tmp_path / ".installed")
    (tmp_path / ".installed").write_text("1.3.0")
    (tmp_path / "focuscore.db").write_text("fake-db")
    assert paths.is_installed() is True
    assert paths.data_dir() == tmp_path
    assert paths.db_path() == tmp_path / "focuscore.db"


def test_installed_mode_uses_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    monkeypatch.setattr(paths, "INSTALLED_MARKER", tmp_path / ".installed")
    (tmp_path / ".installed").write_text("1.3.0")
    assert paths.data_dir() == paths.user_data_dir()
    assert paths.db_path() == paths.user_data_dir() / "focuscore.db"


def test_ensure_data_dir_creates_it(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    monkeypatch.setattr(paths, "INSTALLED_MARKER", tmp_path / ".installed")
    (tmp_path / ".installed").write_text("1.3.0")
    target = tmp_path / "fakehome" / "Focus Core"
    monkeypatch.setattr(paths, "user_data_dir", lambda: target)
    created = paths.ensure_data_dir()
    assert created == target
    assert target.is_dir()


def test_store_default_db_comes_from_paths():
    from focuscore import store
    assert Path(store.DEFAULT_DB_PATH) == paths.db_path()
