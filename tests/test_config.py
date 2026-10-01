"""Tests for focuscore/config.py (Roadmap 1.12) and its three wired
consumers: the launcher port scan, setup_logging's level, and
paths.data_dir()'s installed-mode override.

Hermetic by construction: every test runs against a fake machine
reader, a TOML file inside tmp_path, and a FOCUSCORE_*-scrubbed
environment. Nothing here writes a real config.toml into the repo,
depends on the host env, or touches the repo dev DB.
"""

import logging
import os

import pytest

from focuscore import config, launcher, logging_config, paths

# Captured before any fixture can monkeypatch it: the location tests
# need to exercise the real function against patched paths values.
_real_config_file_path = config.config_file_path


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    """Hermetic config state: fake machine tier + tmp TOML + clean env.

    Returns {"machine": dict, "toml": Path}; mutate cfg["machine"] to
    fake the HKLM tier, write cfg["toml"] to fake the user tier.
    """
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


# ------------------------------------------------------- precedence ---

@pytest.mark.parametrize("machine,toml,env,expected", [
    (5441, 5442, "5443", 5441),  # machine beats toml and env
    (5441, None, "5443", 5441),  # machine beats env
    (5441, None, None, 5441),    # machine beats defaults
    (None, 5442, "5443", 5442),  # TOML beats env (pinned, deliberate)
    (None, 5442, None, 5442),    # toml beats defaults
    (None, None, "5443", 5443),  # env beats defaults
])
def test_port_precedence_matrix(cfg, monkeypatch, machine, toml, env,
                                expected):
    if machine is not None:
        cfg["machine"]["port"] = machine
    if toml is not None:
        _write_toml(cfg, "port = %d\n" % toml)
    if env is not None:
        monkeypatch.setenv("FOCUSCORE_PORT", env)
    assert config.get_port() == expected


def test_unset_port_uses_caller_default(cfg):
    assert config.get_port() is None
    assert config.get("port", 5000) == 5000


def test_toml_beats_env_is_deliberate(cfg, monkeypatch):
    """The roadmap pins TOML > env. Nobody 'fixes' this later."""
    _write_toml(cfg, "port = 5444\n")
    monkeypatch.setenv("FOCUSCORE_PORT", "5445")
    assert config.get_port() == 5444


def test_log_level_precedence(cfg, monkeypatch):
    cfg["machine"]["log_level"] = "ERROR"
    _write_toml(cfg, 'log_level = "DEBUG"\n')
    monkeypatch.setenv("FOCUSCORE_LOG_LEVEL", "WARNING")
    assert config.get_log_level() == "ERROR"
    cfg["machine"].clear()
    assert config.get_log_level() == "DEBUG"  # toml beats env again


def test_log_level_is_normalized(cfg, monkeypatch):
    monkeypatch.setenv("FOCUSCORE_LOG_LEVEL", "debug")
    assert config.get_log_level() == "DEBUG"


def test_data_dir_from_env(cfg, monkeypatch):
    monkeypatch.setenv("FOCUSCORE_DATA_DIR", "/somewhere/else")
    assert config.get_data_dir() == "/somewhere/else"


def test_get_rereads_tiers_every_call(cfg):
    """No caching: a fixed file takes effect on the very next read."""
    _write_toml(cfg, "port = 5451\n")
    assert config.get_port() == 5451
    _write_toml(cfg, "port = 5452\n")
    assert config.get_port() == 5452


# ------------------------------------------------- invalid / broken ---

@pytest.mark.parametrize("bad", ['"abc"', "70000", "0", "-1", "true", "5.5"])
def test_invalid_toml_port_falls_through_to_env(cfg, monkeypatch,
                                                 caplog, bad):
    _write_toml(cfg, "port = %s\n" % bad)
    monkeypatch.setenv("FOCUSCORE_PORT", "5453")
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_port() == 5453
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("port" in r.getMessage() for r in warnings)


def test_invalid_machine_port_falls_through_to_toml(cfg, caplog):
    cfg["machine"]["port"] = "not-a-port"
    _write_toml(cfg, "port = 5454\n")
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_port() == 5454
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_invalid_log_level_everywhere_gives_default(cfg, monkeypatch,
                                                    caplog):
    _write_toml(cfg, 'log_level = "LOUD"\n')
    monkeypatch.setenv("FOCUSCORE_LOG_LEVEL", "CHATTY")
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_log_level() is None
        assert config.get("log_level", "INFO") == "INFO"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 4  # both tiers, both lookups


def test_broken_toml_is_empty_with_warning(cfg, monkeypatch, caplog):
    _write_toml(cfg, "port = [\n")
    monkeypatch.setenv("FOCUSCORE_PORT", "5455")
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_port() == 5455
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_broken_toml_and_nothing_else_is_unset(cfg, caplog):
    _write_toml(cfg, "this is = = not toml\n")
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_port() is None


def test_missing_toml_is_silently_empty(cfg, caplog):
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_port() is None
    assert not caplog.records  # no config file is the normal case


def test_machine_reader_failure_falls_through(cfg, monkeypatch, caplog):
    def boom():
        raise RuntimeError("registry gone")

    monkeypatch.setattr(config, "_machine_reader", boom)
    monkeypatch.setenv("FOCUSCORE_PORT", "5456")
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.get_port() == 5456
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_real_machine_reader_returns_a_dict():
    """The real reader only ever READs; off Windows it has no opinion."""
    assert isinstance(config._read_machine_winreg(), dict)


# ---------------------------------------------------- feature flags ---

@pytest.mark.parametrize("spelling", ["1", "true", "TRUE", "Yes", "on"])
def test_feature_env_true_spellings(cfg, monkeypatch, spelling):
    monkeypatch.setenv("FOCUSCORE_FEATURE_BETA_HOME", spelling)
    assert config.feature_enabled("beta_home") is True


@pytest.mark.parametrize("spelling", ["0", "false", "FALSE", "No", "off"])
def test_feature_env_false_spellings(cfg, monkeypatch, spelling):
    monkeypatch.setenv("FOCUSCORE_FEATURE_BETA_HOME", spelling)
    assert config.feature_enabled("beta_home", default=True) is False


def test_feature_invalid_env_spelling_uses_default(cfg, monkeypatch,
                                                   caplog):
    monkeypatch.setenv("FOCUSCORE_FEATURE_BETA_HOME", "maybe")
    with caplog.at_level(logging.WARNING, logger="focuscore.config"):
        assert config.feature_enabled("beta_home", default=True) is True
        assert config.feature_enabled("beta_home") is False
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_feature_toml_table(cfg, monkeypatch):
    _write_toml(cfg, "[features]\nbeta_home = true\n")
    monkeypatch.setenv("FOCUSCORE_FEATURE_BETA_HOME", "0")
    assert config.feature_enabled("beta_home") is True  # toml beats env


def test_feature_machine_beats_toml(cfg):
    cfg["machine"]["features"] = {"Beta_Home": 0}  # DWORD-style, any case
    _write_toml(cfg, "[features]\nbeta_home = true\n")
    assert config.feature_enabled("beta_home") is False


def test_feature_env_name_maps_flag_name(cfg, monkeypatch):
    monkeypatch.setenv("FOCUSCORE_FEATURE_NEW_HOME", "on")
    assert config.feature_enabled("new-home") is True


def test_feature_unset_uses_default(cfg):
    assert config.feature_enabled("never_heard_of_it") is False
    assert config.feature_enabled("never_heard_of_it", default=True) is True


# --------------------------------------------------- TOML location ---

def test_config_file_path_portable_uses_app_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    monkeypatch.setattr(paths, "INSTALLED_MARKER", tmp_path / ".installed")
    assert _real_config_file_path() == tmp_path / "config.toml"


def test_config_file_path_installed_uses_user_data_dir(tmp_path,
                                                       monkeypatch):
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    monkeypatch.setattr(paths, "INSTALLED_MARKER", tmp_path / ".installed")
    (tmp_path / ".installed").write_text("1.0")
    user_dir = tmp_path / "user-data"
    monkeypatch.setattr(paths, "user_data_dir", lambda: user_dir)
    assert _real_config_file_path() == user_dir / "config.toml"


# -------------------------------------------------- consumer: port ---

def test_launcher_scan_starts_at_config_port(cfg, monkeypatch):
    _write_toml(cfg, "port = 5461\n")
    candidates = launcher._candidate_ports()
    assert candidates[0] == 5461
    assert candidates == list(
        range(5461, 5461 + launcher.PORT_SCAN_COUNT))
    monkeypatch.setattr(launcher, "_active_port", None)
    monkeypatch.setattr(launcher, "is_focus_core", lambda port: False)
    monkeypatch.setattr(launcher, "_port_bindable", lambda port: True)
    port, already_running = launcher._select_port()
    assert (port, already_running) == (5461, False)


def test_launcher_scan_defaults_to_port_constant(cfg):
    assert launcher._candidate_ports()[0] == launcher.PORT == 5000


# ---------------------------------------------- consumer: log level ---

@pytest.fixture()
def log_setup(tmp_path, monkeypatch):
    """setup_logging against a throwaway file; restore root state."""
    log_file = tmp_path / "focuscore.log"
    monkeypatch.setattr(logging_config, "log_path",
                        lambda: str(log_file))
    monkeypatch.setattr(logging_config, "_configured", False)
    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_level = root.level
    yield log_file
    for handler in list(root.handlers):
        if handler not in before_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(before_level)
    logging_config._configured = False


def test_setup_logging_level_comes_from_config(cfg, log_setup):
    _write_toml(cfg, 'log_level = "DEBUG"\n')
    logging_config.setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_explicit_level_beats_config(cfg, log_setup):
    _write_toml(cfg, 'log_level = "DEBUG"\n')
    logging_config.setup_logging(level=logging.ERROR)
    assert logging.getLogger().level == logging.ERROR


def test_setup_logging_no_config_defaults_to_info(cfg, log_setup):
    logging_config.setup_logging()
    assert logging.getLogger().level == logging.INFO


# ---------------------------------------------- consumer: data dir ---

def _paths_at(tmp_path, monkeypatch, installed):
    monkeypatch.setattr(paths, "APP_ROOT", tmp_path)
    monkeypatch.setattr(paths, "INSTALLED_MARKER", tmp_path / ".installed")
    if installed:
        (tmp_path / ".installed").write_text("1.0")
    user_dir = tmp_path / "user-data"
    monkeypatch.setattr(paths, "user_data_dir", lambda: user_dir)
    return user_dir


def test_paths_installed_honors_env_data_dir(cfg, tmp_path, monkeypatch):
    _paths_at(tmp_path, monkeypatch, installed=True)
    configured = tmp_path / "configured-data"
    monkeypatch.setenv("FOCUSCORE_DATA_DIR", str(configured))
    assert paths.data_dir() == configured


def test_paths_installed_honors_toml_data_dir(cfg, tmp_path, monkeypatch):
    _paths_at(tmp_path, monkeypatch, installed=True)
    configured = tmp_path / "toml-data"
    _write_toml(cfg, 'data_dir = "%s"\n' % configured.as_posix())
    assert paths.data_dir() == configured


def test_paths_installed_without_config_uses_user_dir(cfg, tmp_path,
                                                      monkeypatch):
    user_dir = _paths_at(tmp_path, monkeypatch, installed=True)
    assert paths.data_dir() == user_dir


def test_paths_portable_ignores_config_data_dir(cfg, tmp_path,
                                                monkeypatch):
    """A stray env var must never relocate a dev/portable database."""
    _paths_at(tmp_path, monkeypatch, installed=False)
    monkeypatch.setenv("FOCUSCORE_DATA_DIR", str(tmp_path / "elsewhere"))
    _write_toml(cfg, 'data_dir = "%s"\n' % (tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path


def test_paths_grandfathered_db_beats_config(cfg, tmp_path, monkeypatch):
    _paths_at(tmp_path, monkeypatch, installed=True)
    (tmp_path / "focuscore.db").write_text("fake-db")
    monkeypatch.setenv("FOCUSCORE_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path
