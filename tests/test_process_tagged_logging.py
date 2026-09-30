"""Roadmap 1.3: process-tagged logs.

The app runs as several OS processes sharing one log file (dashboard
server, tray, shield daemon, launcher). Every log line now carries a
``%(processName)s`` tag so a failure in the shared file says WHICH
process hit it.

Covered here:
- the shared LOG_FORMAT carries the tag;
- naming the current process changes what emitted lines show;
- setup_logging writes tagged lines to the shared file and can re-tag
  mid-process (the launcher process becomes the tray process);
- each real entry point passes its own name to setup_logging.
"""

import ast
import logging
import multiprocessing
from pathlib import Path

import pytest

from focuscore import logging_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_log_format_carries_process_tag():
    assert "%(processName)s" in logging_config.LOG_FORMAT


@pytest.mark.parametrize("name", ["dashboard", "tray", "shield", "launcher"])
def test_process_name_shows_in_formatted_line(name):
    previous = multiprocessing.current_process().name
    try:
        logging_config.set_process_name(name)
        record = logging.LogRecord(
            "focuscore.probe", logging.WARNING, __file__, 1,
            "probe line", (), None)
        line = logging.Formatter(logging_config.LOG_FORMAT).format(record)
    finally:
        multiprocessing.current_process().name = previous
    assert " %s " % name in line


@pytest.fixture()
def file_logging(tmp_path, monkeypatch):
    """Run setup_logging against a throwaway file; restore root state."""
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


def test_setup_logging_writes_tagged_lines_and_retags(file_logging):
    logging_config.setup_logging(process_name="shield")
    logging.getLogger("focuscore.probe").warning("shield probe")
    # The launcher process turns into the tray process: re-tagging must
    # work even though handler setup is idempotent.
    logging_config.setup_logging(process_name="tray")
    logging.getLogger("focuscore.probe").warning("tray probe")
    text = file_logging.read_text(encoding="utf-8")
    assert " shield " in text
    assert " tray " in text


def _process_names_passed_to_setup_logging(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = func.attr if isinstance(func, ast.Attribute) else \
            func.id if isinstance(func, ast.Name) else ""
        if callee != "setup_logging":
            continue
        for keyword in node.keywords:
            if keyword.arg == "process_name" and \
                    isinstance(keyword.value, ast.Constant):
                names.add(keyword.value.value)
    return names


@pytest.mark.parametrize("relpath, expected", [
    ("dashboard/app.py", "dashboard"),
    ("focuscore/tray.py", "tray"),
    ("focuscore/shield.py", "shield"),
    ("focuscore/launcher.py", "launcher"),
])
def test_entry_point_tags_its_own_process(relpath, expected):
    names = _process_names_passed_to_setup_logging(REPO_ROOT / relpath)
    assert expected in names, (
        "%s must call setup_logging(process_name=%r); found %s"
        % (relpath, expected, names or "no tagged setup_logging call"))


# ----------------------------------------------------------------------
# Roadmap 0.3 leftover, closed here: one 500 must produce ONE logged
# traceback. Flask logs every unhandled exception itself ("Exception on
# /path [GET]") and then calls the app's 500 handler, which logged the
# same traceback again with the request id. dashboard/app.py now
# filters Flask's duplicate; this test is the proof it stays fixed.
# ----------------------------------------------------------------------

import dashboard.app as _dash_app  # noqa: E402,F401  (registers routes)
from dashboard.app import app as _flask_app  # noqa: E402


@_flask_app.route("/_test_500_boom_13")
def _boom_13():
    raise RuntimeError("simulated 500 failure")


def test_one_500_logs_a_single_traceback(caplog):
    _flask_app.config["TESTING"] = False  # real error handlers
    with caplog.at_level(logging.ERROR):
        with _flask_app.test_client() as client:
            response = client.get("/_test_500_boom_13")
    assert response.status_code == 500
    traced = [rec for rec in caplog.records
              if rec.levelno >= logging.ERROR and rec.exc_info]
    assert len(traced) == 1, (
        "one 500 should log exactly one traceback, got %d: %s"
        % (len(traced), [rec.getMessage() for rec in traced]))
    assert "Unhandled exception" in traced[0].getMessage()

