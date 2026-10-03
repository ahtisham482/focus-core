"""Roadmap 1.2: CI safety net -- weekly schedule, pip-audit, ruff ratchet."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_YML_PATH = ROOT / ".github" / "workflows" / "ci.yml"
RATCHET_PATH = ROOT / "installer" / "check_ruff_ratchet.py"
BASELINE_PATH = ROOT / ".ruff-baseline"

spec = importlib.util.spec_from_file_location("check_ruff_ratchet", RATCHET_PATH)
ratchet = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ratchet)

EXACT_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==(\d+\.\d+\.\d+)$")


def _top_level_section(text, header):
    """Lines of one indent-0 YAML section ('on:', 'jobs:'), minus the header."""
    lines = []
    in_section = False
    for raw in text.splitlines():
        if raw and not raw[0].isspace():
            in_section = raw.split(":")[0].strip() == header
            continue
        if in_section:
            lines.append(raw)
    return lines


def _job_names(text):
    names = []
    for raw in _top_level_section(text, "jobs"):
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", raw)
        if match:
            names.append(match.group(1))
    return names


def _ci_text():
    return CI_YML_PATH.read_text()


def test_ci_yml_has_a_weekly_schedule_trigger():
    on_lines = _top_level_section(_ci_text(), "on")
    assert any(line.strip() == "schedule:" for line in on_lines)

    crons = [
        re.search(r"cron:\s*[\"']?([^\"'\n]+)", line).group(1).strip()
        for line in on_lines
        if "cron:" in line
    ]
    # Exactly one weekly slot: Monday 06:00 UTC (chosen roadmap 1.2).
    assert crons == ["0 6 * * 1"]


def test_ci_yml_has_a_pip_audit_job_covering_both_requirements_files():
    text = _ci_text()
    assert "pip-audit" in _job_names(text)
    assert "pip-audit -r requirements.txt" in text
    # The lock names Windows wheel hashes; the audit checks its pinned
    # versions as written instead of resolving on the Linux runner.
    assert "pip-audit --disable-pip -r requirements-lock.txt" in text


def _pinned_tool_versions():
    """{'ruff': 'x.y.z', 'pip-audit': 'x.y.z', 'mypy': 'x.y.z'} from ci.yml."""
    pins = {}
    for raw in _ci_text().splitlines():
        stripped = raw.strip()
        if "pip install" not in stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split("pip install", 1)[1].split()
        for token in tokens:
            match = EXACT_PIN.match(token)
            if match and match.group(1) in ("ruff", "pip-audit", "mypy"):
                assert match.group(1) not in pins, "tool pinned twice"
                pins[match.group(1)] = match.group(2)
    return pins


def test_ci_yml_installs_ci_tools_at_exact_pinned_versions():
    text = _ci_text()
    pins = _pinned_tool_versions()
    assert set(pins) == {"ruff", "pip-audit", "mypy"}
    # No floating install of either tool anywhere in the workflow.
    for raw in text.splitlines():
        stripped = raw.strip()
        if "pip install" not in stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split("pip install", 1)[1].split()
        for token in tokens:
            if token.split("==")[0] in ("ruff", "pip-audit"):
                assert EXACT_PIN.match(token), stripped


def test_ruff_pin_in_ci_matches_the_ratchet_baseline():
    baseline = ratchet.parse_baseline(BASELINE_PATH.read_text())
    assert _pinned_tool_versions()["ruff"] == baseline.ruff_version
    assert baseline.count > 0


def _baseline_text(count=100, version="0.16.9"):
    return "# test baseline\nruff_version: %s\ncount: %d\n" % (version, count)


def test_parse_baseline_reads_count_and_ruff_version():
    baseline = ratchet.parse_baseline(_baseline_text(count=42, version="1.2.3"))
    assert baseline.count == 42
    assert baseline.ruff_version == "1.2.3"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# only a comment\n",
        "count: 100\n",
        "ruff_version: 0.16.9\n",
        "ruff_version: 0.16.9\ncount: lots\n",
        "ruff_version: 0.16.9\ncount: -5\n",
        "ruff_version 0.16.9\ncount: 100\n",
        "ruff_version: \ncount: 100\n",
    ],
)
def test_parse_baseline_rejects_malformed_text(text):
    with pytest.raises(ValueError):
        ratchet.parse_baseline(text)


def test_count_violations_counts_ruff_json_diagnostics():
    payload = json.dumps([{"code": "UP031"}, {"code": "I001"}, {"code": "F401"}])
    assert ratchet.count_violations(payload) == 3
    assert ratchet.count_violations("[]") == 0
    with pytest.raises(ValueError):
        ratchet.count_violations("not json")


def test_evaluate_passes_at_and_below_baseline():
    baseline = ratchet.parse_baseline(_baseline_text(count=100))
    code, _msg = ratchet.evaluate(99, baseline, "0.16.9")
    assert code == 0
    code, _msg = ratchet.evaluate(100, baseline, "0.16.9")
    assert code == 0


def test_evaluate_fails_above_baseline_with_refresh_instructions():
    baseline = ratchet.parse_baseline(_baseline_text(count=100))
    code, message = ratchet.evaluate(101, baseline, "0.16.9")
    assert code == 1
    assert "101" in message and "100" in message
    assert "check_ruff_ratchet.py --refresh" in message


def test_evaluate_rejects_a_ruff_version_mismatch():
    baseline = ratchet.parse_baseline(_baseline_text())
    code, message = ratchet.evaluate(0, baseline, "9.9.9")
    assert code == 2
    assert "9.9.9" in message and "0.16.9" in message


def _run_ratchet_cli(tmp_path, violations, baseline_text):
    ruff_json = tmp_path / "ruff.json"
    ruff_json.write_text(json.dumps([{"code": "X001"}] * violations))
    baseline_file = tmp_path / "baseline.txt"
    baseline_file.write_text(baseline_text)
    return subprocess.run(
        [
            sys.executable,
            str(RATCHET_PATH),
            "--baseline",
            str(baseline_file),
            "--ruff-json",
            str(ruff_json),
            "--ruff-version",
            "0.16.9",
        ],
        capture_output=True,
        text=True,
    )


def test_ratchet_cli_exit_codes(tmp_path):
    baseline = _baseline_text(count=3)
    assert _run_ratchet_cli(tmp_path, 2, baseline).returncode == 0
    assert _run_ratchet_cli(tmp_path, 3, baseline).returncode == 0

    over = _run_ratchet_cli(tmp_path, 4, baseline)
    assert over.returncode == 1
    assert "check_ruff_ratchet.py --refresh" in over.stderr


def test_ratchet_cli_rejects_a_malformed_baseline(tmp_path):
    result = _run_ratchet_cli(tmp_path, 0, "count: nope\n")
    assert result.returncode == 2
    assert "baseline" in result.stderr.lower()


# Local-only guard: GitHub's test job does not install ruff, so this test
# skips there by design; CI enforcement lives in the lint job's ratchet step.
@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
def test_committed_baseline_matches_a_fresh_ruff_measurement():
    baseline = ratchet.parse_baseline(BASELINE_PATH.read_text())
    result = subprocess.run(
        [shutil.which("ruff"), "--version"],
        capture_output=True,
        text=True,
    )
    running_version = result.stdout.strip().rsplit(" ", 1)[-1]
    if running_version != baseline.ruff_version:
        pytest.skip(
            "local ruff %s != baseline ruff %s"
            % (running_version, baseline.ruff_version)
        )
    code, message = ratchet.evaluate(
        ratchet.measure_violations(ratchet.DEFAULT_PATHS),
        baseline,
        running_version,
    )
    assert code == 0, message


# --- Roadmap 3.4: mypy ratchet -------------------------------------------

MYPY_RATCHET_PATH = ROOT / "installer" / "check_mypy_ratchet.py"
MYPY_BASELINE_PATH = ROOT / ".mypy-baseline"

_mypy_spec = importlib.util.spec_from_file_location(
    "check_mypy_ratchet", MYPY_RATCHET_PATH
)
mypy_ratchet = importlib.util.module_from_spec(_mypy_spec)
assert _mypy_spec.loader is not None
_mypy_spec.loader.exec_module(mypy_ratchet)


def test_mypy_pin_in_ci_matches_the_ratchet_baseline():
    baseline = mypy_ratchet.parse_baseline(MYPY_BASELINE_PATH.read_text())
    assert _pinned_tool_versions()["mypy"] == baseline.mypy_version
    assert baseline.count == 0


def test_ci_yml_runs_the_mypy_ratchet_step():
    text = _ci_text()
    assert "python installer/check_mypy_ratchet.py" in text
    assert "Mypy ratchet" in text


def _mypy_baseline_text(count=0, version="2.4.0"):
    return f"# test baseline\nmypy_version: {version}\ncount: {count}\n"


def test_mypy_parse_baseline_reads_count_and_version():
    baseline = mypy_ratchet.parse_baseline(
        _mypy_baseline_text(count=7, version="1.2.3")
    )
    assert baseline.count == 7
    assert baseline.mypy_version == "1.2.3"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# only a comment\n",
        "count: 0\n",
        "mypy_version: 2.4.0\n",
        "mypy_version: 2.4.0\ncount: lots\n",
        "mypy_version: 2.4.0\ncount: -5\n",
        "mypy_version 2.4.0\ncount: 0\n",
        "mypy_version: \ncount: 0\n",
    ],
)
def test_mypy_parse_baseline_rejects_malformed_text(text):
    with pytest.raises(ValueError):
        mypy_ratchet.parse_baseline(text)


def test_mypy_count_errors_counts_only_in_scope_files():
    output = (
        "focuscore/store.py:10: error: Something  [code]\n"
        "focuscore/money.py:20: error: Else  [code]\n"
        "focuscore/migrations.py:30: error: Out of scope  [code]\n"
        "note: unrelated\n"
    )
    assert mypy_ratchet.count_errors(output) == 2
    assert mypy_ratchet.count_errors("") == 0


def test_mypy_evaluate_passes_at_and_below_baseline():
    baseline = mypy_ratchet.parse_baseline(_mypy_baseline_text(count=3))
    code, _msg = mypy_ratchet.evaluate(2, baseline, "2.4.0")
    assert code == 0
    code, _msg = mypy_ratchet.evaluate(3, baseline, "2.4.0")
    assert code == 0


def test_mypy_evaluate_fails_above_baseline_with_refresh_instructions():
    baseline = mypy_ratchet.parse_baseline(_mypy_baseline_text(count=3))
    code, message = mypy_ratchet.evaluate(4, baseline, "2.4.0")
    assert code == 1
    assert "4" in message and "3" in message
    assert "check_mypy_ratchet.py --refresh" in message


def test_mypy_evaluate_rejects_a_version_mismatch():
    baseline = mypy_ratchet.parse_baseline(_mypy_baseline_text())
    code, message = mypy_ratchet.evaluate(0, baseline, "9.9.9")
    assert code == 2
    assert "9.9.9" in message and "2.4.0" in message


def _run_mypy_ratchet_cli(tmp_path, output, baseline_text):
    mypy_out = tmp_path / "mypy.txt"
    mypy_out.write_text(output)
    baseline_file = tmp_path / "baseline.txt"
    baseline_file.write_text(baseline_text)
    return subprocess.run(
        [
            sys.executable,
            str(MYPY_RATCHET_PATH),
            "--baseline",
            str(baseline_file),
            "--mypy-output",
            str(mypy_out),
            "--mypy-version",
            "2.4.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_mypy_ratchet_cli_exit_codes(tmp_path):
    baseline = _mypy_baseline_text(count=1)
    ok_output = "focuscore/store.py:10: error: X  [code]\n"
    assert _run_mypy_ratchet_cli(tmp_path, "", baseline).returncode == 0
    assert _run_mypy_ratchet_cli(tmp_path, ok_output, baseline).returncode == 0

    over = _run_mypy_ratchet_cli(
        tmp_path, ok_output * 2, baseline
    )
    assert over.returncode == 1
    assert "check_mypy_ratchet.py --refresh" in over.stderr


def test_mypy_ratchet_cli_rejects_a_malformed_baseline(tmp_path):
    result = _run_mypy_ratchet_cli(tmp_path, "", "count: nope\n")
    assert result.returncode == 2
    assert "baseline" in result.stderr.lower()


# Local-only guard: GitHub's test job does not install mypy, so this test
# skips there by design; CI enforcement lives in the lint job's ratchet step.
@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed")
def test_committed_mypy_baseline_matches_a_fresh_measurement():
    baseline = mypy_ratchet.parse_baseline(MYPY_BASELINE_PATH.read_text())
    running_version = mypy_ratchet.detect_mypy_version()
    if running_version != baseline.mypy_version:
        pytest.skip(
            f"local mypy {running_version} != baseline mypy "
            f"{baseline.mypy_version}"
        )
    code, message = mypy_ratchet.evaluate(
        mypy_ratchet.measure_errors(mypy_ratchet.DEFAULT_PATHS),
        baseline,
        running_version,
    )
    assert code == 0, message
