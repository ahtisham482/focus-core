"""Print the exact pinned requirement line for one package from the lock.

The pin extraction lives here, tested once. Both installer workflows
install their build-machine tools via installer/install_locked_pin.py,
which builds on extract_lock_pin() below to install the pin with hash
checking.

Usage (from the repo root):
    python installer/extract_lock_pin.py Pillow

Prints the lock line exactly as written, e.g.:
    Pillow==12.3.0 --hash=sha256:a2b55d...

Package names match case- and separator-insensitively (``typing-extensions``
finds the ``typing_extensions`` entry), per PEP 503 normalization. Exit
code is 0 on success and 2 with an explanation on stderr when the lock has
zero entries for the package, more than one entry, or an entry that is not
an exact ``name==version --hash=sha256:<64 hex>`` pin.
"""

import argparse
import re
import sys
from pathlib import Path

DEFAULT_LOCK = Path(__file__).resolve().parent.parent / "requirements-lock.txt"
_NAME = r"[A-Za-z0-9_.-]+"
PIN_LINE = re.compile(
    r"^(%s)==([^\s;]+)\s+--hash=sha256:([0-9a-f]{64})$" % _NAME
)
LEADING_NAME = re.compile(r"^(%s)" % _NAME)


def _normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def extract_lock_pin(lock_text, package, lock_label="requirements-lock.txt"):
    """Return the one pinned line for `package`, or raise ValueError."""
    wanted = _normalized(package)
    candidates = []
    malformed = []
    for raw in lock_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name_match = LEADING_NAME.match(line)
        if not name_match or _normalized(name_match.group(1)) != wanted:
            continue
        if PIN_LINE.match(line):
            candidates.append(line)
        else:
            malformed.append(line)

    if malformed:
        raise ValueError(
            "malformed lock line for '%s' in %s: %r (expected "
            "'name==version --hash=sha256:<64 hex>')"
            % (package, lock_label, malformed[0])
        )
    if not candidates:
        raise ValueError(
            "no pinned entry for '%s' in %s" % (package, lock_label)
        )
    if len(candidates) > 1:
        raise ValueError(
            "multiple pinned entries for '%s' in %s: %s"
            % (package, lock_label, "; ".join(candidates))
        )
    return candidates[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", help="Package name, e.g. Pillow")
    parser.add_argument(
        "--lock",
        default=str(DEFAULT_LOCK),
        help="Lock file to read (default: requirements-lock.txt at repo root)",
    )
    args = parser.parse_args(argv)

    lock_path = Path(args.lock)
    try:
        lock_text = lock_path.read_text()
    except OSError as exc:
        print("error: cannot read lock file %s: %s" % (lock_path, exc),
              file=sys.stderr)
        return 2

    try:
        line = extract_lock_pin(lock_text, args.package, str(lock_path))
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
