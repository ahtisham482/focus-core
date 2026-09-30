"""Roadmap 1.1: installer dependencies are locked and hash-checked."""

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_PATH = ROOT / "installer" / "build.py"
LOCK_PATH = ROOT / "requirements-lock.txt"

spec = importlib.util.spec_from_file_location("installer_build", BUILD_PATH)
build = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(build)


def _normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_entries():
    entries = {}
    for raw in LOCK_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9_.-]+)==([^\s;]+)\s+--hash=sha256:([0-9a-f]{64})",
            line,
        )
        assert match, "lock line is not an exact pin with a sha256 hash: %r" % raw
        entries[_normalized(match.group(1))] = (match.group(2), match.group(3))
    return entries


def test_lock_file_has_exact_pins_and_hashes_for_installer_runtime():
    entries = _lock_entries()

    # Direct runtime requirements from requirements.txt.
    for name in (
        "flask",
        "requests",
        "plyer",
        "pystray",
        "pillow",
        "pywebview",
        "tzdata",
    ):
        assert name in entries

    # The transitive closure needed on Windows/CP312.
    for name in (
        "blinker",
        "click",
        "itsdangerous",
        "jinja2",
        "markupsafe",
        "werkzeug",
        "charset-normalizer",
        "idna",
        "urllib3",
        "certifi",
        "six",
        "bottle",
        "proxy-tools",
        "pythonnet",
        "clr-loader",
        "cffi",
        "pycparser",
        "typing-extensions",
    ):
        assert name in entries

    # Dev-only and Linux-only packages must not ship in the Windows installer.
    assert "pytest" not in entries
    assert "python-xlib" not in entries


def test_verify_sha256_accepts_a_matching_file(tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"known bytes")
    expected = hashlib.sha256(b"known bytes").hexdigest()

    assert build.verify_sha256(artifact, expected, "test artifact") == expected
    assert artifact.exists()


@pytest.mark.parametrize("label", ["Python embed zip", "get-pip.py"])
def test_verify_sha256_rejects_and_deletes_a_mismatched_file(tmp_path, label):
    artifact = tmp_path / "download.bin"
    artifact.write_bytes(b"tampered bytes")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build.verify_sha256(artifact, "0" * 64, label)

    assert not artifact.exists()


def test_download_verified_deletes_a_bad_download(tmp_path, monkeypatch):
    dest = tmp_path / "get-pip.py"

    def fake_download(_url, downloaded_dest):
        downloaded_dest.write_bytes(b"not the pinned bootstrap")

    monkeypatch.setattr(build, "download", fake_download)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build.download_verified(
            "https://example.invalid/get-pip.py", dest, "0" * 64, "get-pip.py"
        )

    assert not dest.exists()


def test_install_locked_dependencies_uses_the_lock_and_require_hashes(
    tmp_path, monkeypatch
):
    python_dir = tmp_path / "python"
    staging = tmp_path / "staging"
    python_dir.mkdir()
    staging.mkdir()
    calls = []

    def fake_run(_python_dir, *args):
        calls.append(args)

    monkeypatch.setattr(build, "run_embedded_python", fake_run)

    build.install_locked_dependencies(python_dir, ROOT, staging)

    assert len(calls) == 2
    bootstrap_call, runtime_call = calls
    assert "--require-hashes" in bootstrap_call
    assert "--require-hashes" in runtime_call
    assert "--no-build-isolation" in runtime_call
    assert str(LOCK_PATH) in runtime_call
    assert not (staging / "_bootstrap-requirements.txt").exists()


def test_installer_workflow_installs_pillow_from_the_lock():
    workflow = (ROOT / ".github" / "workflows" / "installer.yml").read_text()

    assert "pip install pillow" not in workflow
    assert "requirements-lock.txt" in workflow
