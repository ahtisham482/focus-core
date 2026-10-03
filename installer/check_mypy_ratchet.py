"""Mypy ratchet: the error count on the annotated modules may only shrink.

Roadmap 3.4. mypy findings NEVER fail CI on their own (non-blocking):
there is no "zero errors" gate. This ratchet is the only mypy-related
CI signal -- it fails only when the error count on
focuscore/store.py + focuscore/money.py rises above the committed
baseline, exactly like the ruff ratchet (roadmap 1.2). Ratchet upward
over time: as annotations spread, refresh the baseline down.

The measurement is deliberately config-free (no mypy.ini) so a config
file cannot narrow it:

    mypy --ignore-missing-imports --no-error-summary \
        focuscore/store.py focuscore/money.py

Only error lines for those two files are counted -- mypy follows
imports, and dependency noise (e.g. focuscore/migrations.py) is out of
3.4's scope and must not pollute the ratchet.

The committed baseline at the repo root (.mypy-baseline) records that
count and the mypy version that produced it; the two must move
together.

Usage (from the repo root):
    python installer/check_mypy_ratchet.py            # CI check
    python installer/check_mypy_ratchet.py --refresh  # after a cleanup

Exit codes: 0 = count at or below baseline; 1 = count grew (CI fails);
2 = baseline missing/malformed, mypy missing, or mypy version differs
from the one that measured the baseline (re-measure with --refresh).
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / ".mypy-baseline"
DEFAULT_PATHS = ("focuscore/store.py", "focuscore/money.py")
REFRESH_HINT = "python installer/check_mypy_ratchet.py --refresh"
_VERSION_LINE = re.compile(r"^mypy\s+(\S+)")
_ERROR_LINE = re.compile(
    r"^(focuscore/store\.py|focuscore/money\.py):\d+: error:"
)


class Baseline:
    def __init__(self, count, mypy_version):
        self.count = count
        self.mypy_version = mypy_version

    def __repr__(self):
        return f"Baseline(count={self.count!r}, mypy_version={self.mypy_version!r})"


def parse_baseline(text, baseline_label=".mypy-baseline"):
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
                f"malformed line in {baseline_label}: {raw!r} (expected 'key: value')"
            )
        key = key.strip()
        value = value.strip()
        if key == "count":
            if count is not None:
                raise ValueError(f"duplicate 'count' in {baseline_label}")
            try:
                count = int(value)
            except ValueError:
                raise ValueError(
                    f"baseline count in {baseline_label} is not an integer: {value!r}"
                ) from None
            if count < 0:
                raise ValueError(
                    f"baseline count in {baseline_label} is negative: {count}"
                )
        elif key == "mypy_version":
            if version is not None:
                raise ValueError(
                    f"duplicate 'mypy_version' in {baseline_label}"
                )
            if not value:
                raise ValueError(
                    f"baseline mypy_version in {baseline_label} is empty"
                )
            version = value
        else:
            raise ValueError(
                f"unknown key {key!r} in {baseline_label} "
                "(expected 'count' or 'mypy_version')"
            )
    if count is None:
        raise ValueError(f"no 'count' line in {baseline_label}")
    if version is None:
        raise ValueError(f"no 'mypy_version' line in {baseline_label}")
    return Baseline(count=count, mypy_version=version)


def count_errors(mypy_text):
    """Count error lines for the two in-scope modules in mypy output."""
    return sum(
        1 for line in mypy_text.splitlines() if _ERROR_LINE.match(line)
    )


def evaluate(current_count, baseline, running_mypy_version):
    """Return (exit_code, message) for a measured count vs the baseline."""
    if running_mypy_version != baseline.mypy_version:
        return (
            2,
            "mypy version mismatch: the baseline was measured with mypy "
            + f"{baseline.mypy_version} but mypy {running_mypy_version} is "
            + "running. Error counts are only comparable for the same mypy "
            + f"version -- re-measure deliberately with: {REFRESH_HINT}",
        )
    if current_count > baseline.count:
        return (
            1,
            f"mypy ratchet FAILED: {current_count} errors on the annotated "
            + f"modules, above the committed baseline of {baseline.count} "
            + f"(.mypy-baseline, mypy {baseline.mypy_version}). New type "
            + "errors are not allowed -- fix them, or, after an intentional "
            + "cleanup that changes the count, refresh the baseline with: "
            + f"{REFRESH_HINT} (and commit .mypy-baseline).",
        )
    if current_count < baseline.count:
        return (
            0,
            f"mypy ratchet OK: {current_count} errors, below the baseline "
            + f"of {baseline.count}. Consider lowering the baseline with: "
            + f"{REFRESH_HINT}",
        )
    return (
        0,
        f"mypy ratchet OK: {current_count} errors, exactly at the baseline "
        + f"(mypy {baseline.mypy_version}).",
    )


def _mypy_binary():
    binary = shutil.which("mypy")
    if binary is None:
        raise RuntimeError(
            "mypy is not installed (CI installs the pinned mypy; locally: "
            "pip install mypy==<version in .github/workflows/ci.yml>)"
        )
    return binary


def detect_mypy_version():
    result = subprocess.run(
        [_mypy_binary(), "--version"], capture_output=True, text=True,
        check=False,
    )
    match = _VERSION_LINE.match(result.stdout.strip())
    if result.returncode != 0 or not match:
        raise RuntimeError(
            f"could not read the mypy version: {result.stdout}{result.stderr}"
        )
    return match.group(1)


def measure_errors(paths=DEFAULT_PATHS):
    """Run the ratchet measurement and return the error count."""
    result = subprocess.run(
        [
            _mypy_binary(),
            "--ignore-missing-imports",
            "--no-error-summary",
            *paths,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # mypy exits 1 when it reports errors -- that is a measurement,
    # not a failure. Anything else is a real failure.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"mypy failed (exit {result.returncode}): {result.stderr}"
        )
    return count_errors(result.stdout)


def _refresh(baseline_path, count, mypy_version):
    header = []
    if baseline_path.exists():
        for raw in baseline_path.read_text().splitlines():
            if raw.strip().startswith("#") or not raw.strip():
                header.append(raw)
            else:
                break
    if not header:
        header = [
            "# Mypy ratchet baseline (roadmap 3.4). See",
            "# installer/check_mypy_ratchet.py for the refresh procedure.",
        ]
    body = "\n".join(header).rstrip("\n") + "\n"
    body += f"mypy_version: {mypy_version}\ncount: {count}\n"
    baseline_path.write_text(body)
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE),
        help="Baseline file (default: .mypy-baseline at the repo root)",
    )
    parser.add_argument(
        "--mypy-output",
        help="Read captured mypy output instead of running mypy "
        "(used by tests)",
    )
    parser.add_argument(
        "--mypy-version",
        help="Mypy version to compare against (default: detect from the "
        "installed mypy binary; used by tests)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rewrite the baseline with the current measured count and "
        "mypy version (run after an intentional cleanup, then commit "
        ".mypy-baseline)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_PATHS),
        help="Paths to measure (default: {})".format(" ".join(DEFAULT_PATHS)),
    )
    args = parser.parse_args(argv)

    baseline_path = Path(args.baseline)
    try:
        running_version = args.mypy_version or detect_mypy_version()
        if args.mypy_output:
            current_count = count_errors(
                Path(args.mypy_output).read_text()
            )
        else:
            current_count = measure_errors(tuple(args.paths))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.refresh:
        _refresh(baseline_path, current_count, running_version)
        print(
            f"mypy ratchet baseline refreshed: {current_count} errors with "
            f"mypy {running_version} written to {baseline_path}. Commit "
            "that file."
        )
        return 0

    try:
        baseline = parse_baseline(
            baseline_path.read_text(), str(baseline_path)
        )
    except OSError as exc:
        print(
            f"error: cannot read the mypy baseline {baseline_path}: {exc}",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print(f"error: malformed mypy baseline: {exc}", file=sys.stderr)
        return 2

    code, message = evaluate(current_count, baseline, running_version)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
