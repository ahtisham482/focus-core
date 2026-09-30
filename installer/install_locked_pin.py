"""Install one build-machine tool from requirements-lock.txt, hash-checked.

Both installer workflows use this to install their build-machine Pillow:
the pin is extracted from requirements-lock.txt (see extract_lock_pin.py),
written to a temporary requirements file, and installed with
``pip install --require-hashes -r <file>``.

Why the temporary file: pip only accepts ``--hash`` inside a requirements
file. Passing the pin as command-line arguments
(``pip install --require-hashes Pillow==X --hash=sha256:...``) makes pip
exit with "no such option: --hash" — that exact form broke the installer
smoke workflow in CI. The ``-r`` form is the only supported one.

Usage (from the repo root):
    python installer/install_locked_pin.py Pillow [--lock PATH]

Exit code is 0 on success, 2 for lock-file problems (unreadable, missing
or malformed pin), and the pip exit code when pip itself fails.
"""

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _load_sibling():
    path = Path(__file__).resolve().parent / "extract_lock_pin.py"
    sibling_spec = importlib.util.spec_from_file_location(
        "extract_lock_pin", path
    )
    mod = importlib.util.module_from_spec(sibling_spec)
    assert sibling_spec.loader is not None
    sibling_spec.loader.exec_module(mod)
    return mod


_sibling = _load_sibling()
DEFAULT_LOCK = _sibling.DEFAULT_LOCK
extract_lock_pin = _sibling.extract_lock_pin


def pip_command(req_path):
    """The pip argv for a hash-checked install from a requirements file."""
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--require-hashes",
        "-r",
        str(req_path),
    ]


def install(package, lock_path=None, runner=None):
    """Install `package`'s locked pin; raise on any failure.

    `runner` is the subprocess callable (default: subprocess.check_call);
    tests inject a fake. The temporary requirements file is removed in
    all cases.
    """
    lock_path = Path(lock_path) if lock_path is not None else DEFAULT_LOCK
    line = extract_lock_pin(lock_path.read_text(), package, str(lock_path))
    if runner is None:
        runner = subprocess.check_call
    fd, tmp_name = tempfile.mkstemp(prefix="fc-locked-pin-", suffix=".txt")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(line + "\n")
        runner(pip_command(tmp_name))
    finally:
        os.unlink(tmp_name)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", help="Package name, e.g. Pillow")
    parser.add_argument(
        "--lock",
        default=None,
        help="Lock file to read (default: requirements-lock.txt at repo root)",
    )
    args = parser.parse_args(argv)
    try:
        return install(args.package, args.lock)
    except OSError as exc:
        print("error: cannot read lock file: %s" % exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
