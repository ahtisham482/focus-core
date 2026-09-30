"""Ruff ratchet: the full default-ruleset violation count may only shrink.

Roadmap 1.2. The lint job's E,F check is the hard gate (zero real
errors). This ratchet guards everything else ruff checks by default --
the parked style nits (UP031 %-formatting, blind excepts, naive
datetimes, ...) -- so cleanup can only move one way.

The measurement is deliberately config-free so the pyproject E,F
selection cannot narrow it:

    ruff check --isolated --target-version py311 \
        --output-format json focuscore dashboard tests installer

with the ruff version pinned in .github/workflows/ci.yml. The committed
baseline at the repo root (.ruff-baseline) records that count and the
ruff version that produced it; the two must move together.

Usage (from the repo root):
    python installer/check_ruff_ratchet.py            # CI check
    python installer/check_ruff_ratchet.py --refresh  # after a cleanup

Exit codes: 0 = count at or below baseline; 1 = count grew (CI fails);
2 = baseline missing/malformed, ruff missing, or ruff version differs
from the one that measured the baseline (re-measure with --refresh).
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / ".ruff-baseline"
DEFAULT_PATHS = ("focuscore", "dashboard", "tests", "installer")
TARGET_VERSION = "py311"
REFRESH_HINT = "python installer/check_ruff_ratchet.py --refresh"
_VERSION_LINE = re.compile(r"^ruff\s+(\S+)$")


class Baseline:
    def __init__(self, count, ruff_version):
        self.count = count
        self.ruff_version = ruff_version

    def __repr__(self):
        return "Baseline(count=%r, ruff_version=%r)" % (
            self.count,
            self.ruff_version,
        )


def parse_baseline(text, baseline_label=".ruff-baseline"):
    """Parse baseline text into a Baseline, or raise ValueError."""
    count = None
    version = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(
                "malformed line in %s: %r (expected 'key: value')"
                % (baseline_label, raw)
            )
        key = key.strip()
        value = value.strip()
        if key == "count":
            if count is not None:
                raise ValueError("duplicate 'count' in %s" % baseline_label)
            try:
                count = int(value)
            except ValueError:
                raise ValueError(
                    "baseline count in %s is not an integer: %r"
                    % (baseline_label, value)
                ) from None
            if count < 0:
                raise ValueError(
                    "baseline count in %s is negative: %d"
                    % (baseline_label, count)
                )
        elif key == "ruff_version":
            if version is not None:
                raise ValueError(
                    "duplicate 'ruff_version' in %s" % baseline_label
                )
            if not value:
                raise ValueError(
                    "baseline ruff_version in %s is empty" % baseline_label
                )
            version = value
        else:
            raise ValueError(
                "unknown key %r in %s (expected 'count' or 'ruff_version')"
                % (key, baseline_label)
            )
    if count is None:
        raise ValueError("no 'count' line in %s" % baseline_label)
    if version is None:
        raise ValueError("no 'ruff_version' line in %s" % baseline_label)
    return Baseline(count=count, ruff_version=version)


def count_violations(ruff_json_text):
    """Count diagnostics in `ruff check --output-format json` output."""
    try:
        diagnostics = json.loads(ruff_json_text)
    except json.JSONDecodeError as exc:
        raise ValueError("ruff output is not valid JSON: %s" % exc) from None
    if not isinstance(diagnostics, list):
        raise ValueError(
            "ruff JSON output is not a list of diagnostics: %r"
            % (type(diagnostics).__name__,)
        )
    return len(diagnostics)


def evaluate(current_count, baseline, running_ruff_version):
    """Return (exit_code, message) for a measured count vs the baseline."""
    if running_ruff_version != baseline.ruff_version:
        return (
            2,
            "ruff version mismatch: the baseline was measured with ruff "
            "%s but ruff %s is running. Rule counts are only comparable "
            "for the same ruff version -- re-measure deliberately with: "
            "%s" % (baseline.ruff_version, running_ruff_version, REFRESH_HINT),
        )
    if current_count > baseline.count:
        return (
            1,
            "ruff ratchet FAILED: %d violations in the full default "
            "ruleset, above the committed baseline of %d (.ruff-baseline, "
            "ruff %s). New violations are not allowed -- fix them, or, "
            "after an intentional cleanup that changes the count, refresh "
            "the baseline with: %s (and commit .ruff-baseline)."
            % (current_count, baseline.count, baseline.ruff_version,
               REFRESH_HINT),
        )
    if current_count < baseline.count:
        return (
            0,
            "ruff ratchet OK: %d violations, below the baseline of %d. "
            "Consider lowering the baseline with: %s"
            % (current_count, baseline.count, REFRESH_HINT),
        )
    return (
        0,
        "ruff ratchet OK: %d violations, exactly at the baseline "
        "(ruff %s)." % (current_count, baseline.ruff_version),
    )


def _ruff_binary():
    binary = shutil.which("ruff")
    if binary is None:
        raise RuntimeError(
            "ruff is not installed (CI installs the pinned ruff; locally: "
            "pip install ruff==<version in .github/workflows/ci.yml>)"
        )
    return binary


def detect_ruff_version():
    result = subprocess.run(
        [_ruff_binary(), "--version"], capture_output=True, text=True
    )
    match = _VERSION_LINE.match(result.stdout.strip())
    if result.returncode != 0 or not match:
        raise RuntimeError(
            "could not read the ruff version: %s%s"
            % (result.stdout, result.stderr)
        )
    return match.group(1)


def measure_violations(paths=DEFAULT_PATHS):
    """Run the ratchet measurement and return the violation count."""
    result = subprocess.run(
        [
            _ruff_binary(),
            "check",
            "--isolated",
            "--target-version",
            TARGET_VERSION,
            "--output-format",
            "json",
            *paths,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            "ruff failed (exit %d): %s" % (result.returncode, result.stderr)
        )
    return count_violations(result.stdout)


def _refresh(baseline_path, count, ruff_version):
    header = []
    if baseline_path.exists():
        for raw in baseline_path.read_text().splitlines():
            if raw.strip().startswith("#") or not raw.strip():
                header.append(raw)
            else:
                break
    if not header:
        header = [
            "# Ruff ratchet baseline (roadmap 1.2). See",
            "# installer/check_ruff_ratchet.py for the refresh procedure.",
        ]
    body = "\n".join(header).rstrip("\n") + "\n"
    body += "ruff_version: %s\ncount: %d\n" % (ruff_version, count)
    baseline_path.write_text(body)
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE),
        help="Baseline file (default: .ruff-baseline at the repo root)",
    )
    parser.add_argument(
        "--ruff-json",
        help="Read captured ruff JSON output instead of running ruff "
        "(used by tests)",
    )
    parser.add_argument(
        "--ruff-version",
        help="Ruff version to compare against (default: detect from the "
        "installed ruff binary; used by tests)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rewrite the baseline with the current measured count and "
        "ruff version (run after an intentional cleanup, then commit "
        ".ruff-baseline)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_PATHS),
        help="Paths to measure (default: %s)" % " ".join(DEFAULT_PATHS),
    )
    args = parser.parse_args(argv)

    baseline_path = Path(args.baseline)
    try:
        running_version = args.ruff_version or detect_ruff_version()
        if args.ruff_json:
            current_count = count_violations(Path(args.ruff_json).read_text())
        else:
            current_count = measure_violations(tuple(args.paths))
    except (OSError, RuntimeError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.refresh:
        _refresh(baseline_path, current_count, running_version)
        print(
            "ruff ratchet baseline refreshed: %d violations with ruff %s "
            "written to %s. Commit that file."
            % (current_count, running_version, baseline_path)
        )
        return 0

    try:
        baseline = parse_baseline(
            baseline_path.read_text(), str(baseline_path)
        )
    except OSError as exc:
        print(
            "error: cannot read the ruff baseline %s: %s"
            % (baseline_path, exc),
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print("error: malformed ruff baseline: %s" % exc, file=sys.stderr)
        return 2

    code, message = evaluate(current_count, baseline, running_version)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
