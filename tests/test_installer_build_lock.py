"""Roadmap 1.1: installer dependencies are locked and hash-checked."""

import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_PATH = ROOT / "installer" / "build.py"
LOCK_PATH = ROOT / "requirements-lock.txt"
HELPER_PATH = ROOT / "installer" / "extract_lock_pin.py"
PIN_LINE = re.compile(r"[A-Za-z0-9_.-]+==[^\s;]+ --hash=sha256:[0-9a-f]{64}")

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
    captured = {}

    def fake_run(_python_dir, *args):
        calls.append(args)
        # Read the requirements file NOW: install_locked_dependencies
        # deletes the bootstrap file as soon as this call returns.
        if "-r" in args:
            req_path = Path(args[args.index("-r") + 1])
            if req_path.name == "_bootstrap-requirements.txt":
                captured["bootstrap"] = req_path.read_text()

    monkeypatch.setattr(build, "run_embedded_python", fake_run)

    build.install_locked_dependencies(python_dir, ROOT, staging)

    assert len(calls) == 2
    bootstrap_call, runtime_call = calls
    assert "--require-hashes" in bootstrap_call
    assert "--require-hashes" in runtime_call
    assert "--no-build-isolation" in runtime_call
    assert str(LOCK_PATH) in runtime_call
    assert not (staging / "_bootstrap-requirements.txt").exists()

    # The bootstrap file itself must carry only exact == pins, each with
    # exactly one valid sha256: hash -- --require-hashes is meaningless
    # if the file it points at floats or drops the hashes.
    bootstrap_lines = [
        line for line in captured["bootstrap"].splitlines() if line.strip()
    ]
    assert {line.split("==")[0] for line in bootstrap_lines} == {
        "setuptools",
        "wheel",
        "packaging",
    }
    for line in bootstrap_lines:
        assert PIN_LINE.fullmatch(line), line


def test_installer_workflows_install_pillow_from_the_lock():
    # Both installer workflows must get their build-machine Pillow from
    # the hash-locked install helper, never a floating install. (The
    # smoke workflow's `pip install playwright` is a CI test harness,
    # not a shipped dependency, and stays out of scope.)
    # The pip invocation must live in the tested helper, not inline in
    # the YAML: pip rejects `--hash` on the command line ("no such
    # option: --hash"), which is how the inline form broke CI.
    for name in ("installer.yml", "installer-smoke.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text()
        assert "pip install pillow" not in workflow, name
        assert "installer/install_locked_pin.py" in workflow, name
        assert "line.split()" not in workflow, name
        assert "extract_lock_pin.py" not in workflow, name


def _run_helper(*args):
    return subprocess.run(
        [sys.executable, str(HELPER_PATH), *args],
        capture_output=True,
        text=True,
    )


def test_extract_lock_pin_prints_the_real_pillow_pin():
    result = _run_helper("Pillow")

    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert PIN_LINE.fullmatch(line), line
    version, digest = _lock_entries()["pillow"]
    assert line == "Pillow==%s --hash=sha256:%s" % (version, digest)


def test_extract_lock_pin_matches_names_case_and_separator_insensitively():
    result = _run_helper("typing-extensions")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("typing_extensions==")


def test_extract_lock_pin_rejects_a_package_missing_from_the_lock(tmp_path):
    lock = tmp_path / "lock.txt"
    lock.write_text("flask==3.1.0 --hash=sha256:" + "a" * 64 + "\n")

    result = _run_helper("Pillow", "--lock", str(lock))

    assert result.returncode != 0
    assert "no pinned entry" in result.stderr
    assert "Pillow" in result.stderr


def test_extract_lock_pin_rejects_duplicate_lines(tmp_path):
    pin = "Pillow==12.3.0 --hash=sha256:" + "b" * 64 + "\n"
    lock = tmp_path / "lock.txt"
    lock.write_text(pin + pin)

    result = _run_helper("Pillow", "--lock", str(lock))

    assert result.returncode != 0
    assert "multiple" in result.stderr


def test_extract_lock_pin_rejects_a_line_without_a_hash(tmp_path):
    lock = tmp_path / "lock.txt"
    lock.write_text("Pillow==12.3.0\n")

    result = _run_helper("Pillow", "--lock", str(lock))

    assert result.returncode != 0
    assert "malformed" in result.stderr


def test_webview2_bootstrapper_sha256_is_pinned():
    assert re.fullmatch(r"[0-9a-f]{64}", build.WEBVIEW2_BOOTSTRAPPER_SHA256)


def test_webview2_bootstrapper_rejects_and_deletes_a_mismatch(
    tmp_path, monkeypatch
):
    dest = tmp_path / "webview2bootstrapper.exe"
    monkeypatch.setattr(
        build, "download", lambda _url, d: d.write_bytes(b"tampered bytes")
    )

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build.download_verified(
            build.WEBVIEW2_BOOTSTRAPPER_URL,
            dest,
            build.WEBVIEW2_BOOTSTRAPPER_SHA256,
            "WebView2 bootstrapper",
        )

    assert not dest.exists()


def test_main_verifies_each_download_before_using_it(tmp_path, monkeypatch):
    events = []

    def fake_download_verified(_url, dest, expected, label):
        events.append(("verify", label, expected))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"placeholder")

    def fake_download(url, _dest):
        events.append(("plain-download", url))

    class FakeZipFile:
        def __init__(self, path):
            self._path = path

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extractall(self, dest):
            events.append(("extract", Path(dest).name))
            Path(dest).mkdir(parents=True, exist_ok=True)

    def fake_run(_python_dir, *args):
        events.append(("run", args))

    monkeypatch.setattr(build, "download_verified", fake_download_verified)
    monkeypatch.setattr(build, "download", fake_download)
    monkeypatch.setattr(build.zipfile, "ZipFile", FakeZipFile)
    monkeypatch.setattr(
        build,
        "enable_site_packages",
        lambda _python_dir: events.append(("enable-site",)),
    )
    monkeypatch.setattr(build, "run_embedded_python", fake_run)
    monkeypatch.setattr(
        build,
        "install_locked_dependencies",
        lambda *_a: events.append(("install-deps",)),
    )
    monkeypatch.setattr(
        build.shutil,
        "copytree",
        lambda *_a, **_k: events.append(("copy",)),
    )
    monkeypatch.setattr(
        build, "build_icon", lambda *_a: events.append(("icon",))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build.py",
            "--version",
            "9.9.9",
            "--staging",
            str(tmp_path / "staging"),
        ],
    )

    build.main()

    verify_embed = next(
        i
        for i, e in enumerate(events)
        if e[0] == "verify" and "embed" in e[1]
    )
    extract = next(i for i, e in enumerate(events) if e[0] == "extract")
    assert verify_embed < extract

    verify_getpip = next(
        i for i, e in enumerate(events) if e[0] == "verify" and e[1] == "get-pip.py"
    )
    run_getpip = next(
        i
        for i, e in enumerate(events)
        if e[0] == "run" and any("_get-pip.py" in str(a) for a in e[1])
    )
    assert verify_getpip < run_getpip

    # The WebView2 bootstrapper is hash-verified against its pin; the
    # unverified plain download() must not be used for anything.
    assert any(
        e[0] == "verify"
        and e[1] == "WebView2 bootstrapper"
        and e[2] == build.WEBVIEW2_BOOTSTRAPPER_SHA256
        for e in events
    )
    assert not any(e[0] == "plain-download" for e in events)


def test_build_py_docstring_points_at_the_locked_pillow_install():
    assert "pip install pillow" not in build.__doc__
    assert "--require-hashes" in build.__doc__


INSTALL_PATH = ROOT / "installer" / "install_locked_pin.py"


def _load_install_locked_pin():
    install_spec = importlib.util.spec_from_file_location(
        "install_locked_pin", INSTALL_PATH
    )
    mod = importlib.util.module_from_spec(install_spec)
    assert install_spec.loader is not None
    install_spec.loader.exec_module(mod)
    return mod


def test_install_locked_pin_installs_via_a_requirements_file():
    # pip only accepts --hash inside a requirements file; on the
    # command line it exits with "no such option: --hash" (this broke
    # the installer smoke workflow in CI). The helper must therefore
    # write the pin to a temp file and install with -r.
    mod = _load_install_locked_pin()
    seen = {}

    def fake_runner(cmd):
        seen["cmd"] = cmd
        req_path = Path(cmd[-1])
        seen["req_path"] = req_path
        # The requirements file must exist while pip runs.
        seen["req_text"] = req_path.read_text()

    assert mod.install("Pillow", runner=fake_runner) == 0

    cmd = seen["cmd"]
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--require-hashes" in cmd
    assert cmd[-2] == "-r"
    assert "--hash" not in cmd
    version, digest = _lock_entries()["pillow"]
    assert seen["req_text"] == "Pillow==%s --hash=sha256:%s\n" % (
        version,
        digest,
    )
    # Temp file cleaned up after the install.
    assert not seen["req_path"].exists()


def test_install_locked_pin_propagates_pip_failure_and_cleans_up():
    mod = _load_install_locked_pin()
    seen = {}

    def bad_runner(cmd):
        seen["req_path"] = Path(cmd[-1])
        raise subprocess.CalledProcessError(2, cmd)

    with pytest.raises(subprocess.CalledProcessError):
        mod.install("Pillow", runner=bad_runner)

    assert not seen["req_path"].exists()


def test_install_locked_pin_rejects_unknown_package_without_calling_pip():
    mod = _load_install_locked_pin()
    calls = []

    with pytest.raises(ValueError):
        mod.install(
            "definitely-not-a-package-zzz",
            runner=lambda cmd: calls.append(cmd),
        )

    assert calls == []


def test_install_locked_pin_main_maps_failures_to_exit_codes(monkeypatch, capsys):
    mod = _load_install_locked_pin()

    # Unknown package: error on stderr, exit 2, pip never invoked.
    assert mod.main(["definitely-not-a-package-zzz"]) == 2
    assert "no pinned entry" in capsys.readouterr().err

    # pip failure: the pip exit code comes back, not a traceback.
    # (7, deliberately unlike the lock-error code 2 above, so the two
    # exit paths cannot be confused.)
    def bad_check_call(cmd):
        raise subprocess.CalledProcessError(7, cmd)

    monkeypatch.setattr(mod.subprocess, "check_call", bad_check_call)
    assert mod.main(["Pillow"]) == 7
