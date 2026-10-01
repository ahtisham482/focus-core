"""Roadmap 1.10: THIRD-PARTY-LICENSES.txt is generated at build time.

The generator (installer/third_party_licenses.py) reads only the exact
files that ship: *.dist-info/METADATA (+ license text files) from the
staging site-packages, and the PSF text from the embedded Python's
LICENSE.txt. Failure policy (blackboard decision 8): FAIL CLOSED -- a
dist-info with a missing/malformed METADATA ships but cannot be
attributed, so generation raises and the build fails; a missing
CPython LICENSE.txt raises too. No network use anywhere below.
"""

import importlib.util
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "installer" / "third_party_licenses.py"
BUILD_PATH = ROOT / "installer" / "build.py"
LOCK_PATH = ROOT / "requirements-lock.txt"

spec = importlib.util.spec_from_file_location(
    "third_party_licenses", GENERATOR_PATH
)
licenses = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(licenses)

build_spec = importlib.util.spec_from_file_location("installer_build", BUILD_PATH)
build = importlib.util.module_from_spec(build_spec)
assert build_spec.loader is not None
build_spec.loader.exec_module(build)


def _normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def make_python_dir(tmp_path, license_text="FAKE PSF LICENSE TEXT"):
    python_dir = tmp_path / "python"
    python_dir.mkdir(exist_ok=True)
    if license_text is not None:
        (python_dir / "LICENSE.txt").write_text(license_text + "\n")
    return python_dir


def make_dist_info(site_packages, name, version, extra_headers=(),
                   license_texts=None, record_paths=(), metadata=None):
    """Create a fake <name>-<version>.dist-info tree.

    license_texts: {path-relative-to-dist-info: text}.
    record_paths: installed-file paths for a RECORD file (first path
    component is the top-level package location).
    """
    dist_info = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    if metadata is None:
        lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
        lines += list(extra_headers)
        metadata = "\n".join(lines) + "\n"
    (dist_info / "METADATA").write_text(metadata)
    for rel, text in (license_texts or {}).items():
        path = dist_info / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    if record_paths:
        record = "".join(f"{p},,\n" for p in record_paths)
        (dist_info / "RECORD").write_text(record)
    return dist_info


@pytest.fixture()
def site_packages(tmp_path):
    path = tmp_path / "site-packages"
    path.mkdir()
    return path


def _section(text, name):
    """The output block for one component (for scoped assertions)."""
    pattern = re.compile(
        r"^Name: " + re.escape(name) + r"\n(.*?)(?=^Name: |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"no section for {name}"
    return match.group(0)


# --- declared-license precedence -------------------------------------------


def test_license_expression_beats_license_field_and_classifier(site_packages, tmp_path):
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=(
            "License-Expression: BSD-3-Clause",
            "License: MIT",
            "Classifier: License :: OSI Approved :: Apache Software License",
        ),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "License: BSD-3-Clause" in _section(text, "Demo")


def test_license_field_beats_classifier(site_packages, tmp_path):
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=(
            "License: MIT",
            "Classifier: License :: OSI Approved :: Apache Software License",
        ),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "License: MIT" in _section(text, "Demo")


def test_classifier_used_when_no_license_fields(site_packages, tmp_path):
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=(
            "Classifier: License :: OSI Approved :: Apache Software License",
        ),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "License: Apache Software License" in _section(text, "Demo")


def test_unknown_license_field_falls_through_to_classifier(site_packages, tmp_path):
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=(
            "License: UNKNOWN",
            "Classifier: License :: OSI Approved :: MIT License",
        ),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "License: MIT License" in _section(text, "Demo")


def test_multiple_license_classifiers_are_all_kept(site_packages, tmp_path):
    # Classifier-only metadata declaring several distinct licenses
    # renders them all (deterministic sorted order), not just the first.
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=(
            "Classifier: License :: OSI Approved :: MIT License",
            "Classifier: License :: OSI Approved :: Apache Software License",
        ),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    section = _section(text, "Demo")
    assert "MIT License" in section
    assert "Apache Software License" in section
    assert "License: Apache Software License / MIT License" in section


# --- license text locations -------------------------------------------------


def test_license_text_found_in_licenses_subdir(site_packages, tmp_path):
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=("License-Expression: MIT",),
        license_texts={"licenses/LICENSE.txt": "THE MIT TEXT"},
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    section = _section(text, "Demo")
    assert "THE MIT TEXT" in section
    assert "licenses/LICENSE.txt" in section


def test_output_paths_use_forward_slashes_only(site_packages, tmp_path):
    # Regression (CI windows-latest failure): the generator used to
    # render each license-text source path with str(Path), which uses
    # OS-native separators -- so a Windows build shipped
    # "Demo-1.0.dist-info\licenses\LICENSE.txt" while a Linux build
    # shipped the forward-slash form, and the artifact bytes differed
    # by build OS. This pins the invariant without a Windows machine:
    # with separator-free fixture texts, no backslash may appear
    # anywhere in the generated output, and every source label must
    # use forward slashes.
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=("License-Expression: MIT",),
        license_texts={"licenses/LICENSE.txt": "THE MIT TEXT"},
    )
    package_dir = site_packages / "demopkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "LICENSE.txt").write_text("TOP LEVEL LICENSE TEXT")
    make_dist_info(
        site_packages, "demopkg", "2.0",
        extra_headers=("License-Expression: Apache-2.0",),
        record_paths=("demopkg/__init__.py", "demopkg/LICENSE.txt"),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "\\" not in text
    assert "licenses/LICENSE.txt" in text
    assert "License text (Demo-1.0.dist-info/licenses/LICENSE.txt):" in text
    assert "License text (demopkg/LICENSE.txt):" in text
    assert "License text (python/LICENSE.txt):" in text


def test_license_text_found_via_license_file_header(site_packages, tmp_path):
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=("License-Expression: MIT", "License-File: LICENSE.txt"),
        license_texts={"LICENSE.txt": "HEADER POINTED TEXT"},
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "HEADER POINTED TEXT" in _section(text, "Demo")


def test_license_text_found_at_package_top_level(site_packages, tmp_path):
    package_dir = site_packages / "demopkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "LICENSE.txt").write_text("TOP LEVEL LICENSE TEXT")
    make_dist_info(
        site_packages, "demopkg", "2.0",
        extra_headers=("License-Expression: Apache-2.0",),
        record_paths=("demopkg/__init__.py", "demopkg/LICENSE.txt"),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "TOP LEVEL LICENSE TEXT" in _section(text, "demopkg")


def test_missing_license_text_gets_the_plain_note(site_packages, tmp_path):
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=("License-Expression: MIT",),
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    section = _section(text, "Demo")
    assert licenses.MISSING_TEXT_NOTE in section
    assert "license text not present in the installed package files" in section


def test_license_file_header_with_legacy_licenses_subdir_placement(
        site_packages, tmp_path):
    # Older setuptools metadata (e.g. boolean.py) declares a bare
    # "License-File: LICENSE.txt" for a file shipped under licenses/;
    # that resolves and must not fail closed -- the text is the
    # package's real license text, harvested from licenses/ either way.
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=("License: BSD-2-Clause", "License-File: LICENSE.txt"),
        license_texts={"licenses/LICENSE.txt": "LEGACY PLACED TEXT"},
    )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    assert "LEGACY PLACED TEXT" in _section(text, "Demo")


def test_dangling_license_file_header_fails_closed(site_packages, tmp_path):
    # A License-File header (PEP 639) naming a file that does not exist
    # fails closed, like malformed METADATA: the generator raises
    # instead of silently falling through to the missing-text note.
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=(
            "License-Expression: MIT",
            "License-File: licenses/NOPE.txt",
        ),
    )
    with pytest.raises(ValueError, match="License-File"):
        licenses.generate_licenses_text(
            site_packages, make_python_dir(tmp_path), "3.12.7")


def test_dangling_license_file_raises_even_with_other_text(site_packages, tmp_path):
    # The raise happens even when another license text was harvested.
    make_dist_info(
        site_packages, "Demo", "1.0",
        extra_headers=(
            "License-Expression: MIT",
            "License-File: licenses/NOPE.txt",
        ),
        license_texts={"licenses/LICENSE.txt": "THE MIT TEXT"},
    )
    with pytest.raises(ValueError, match="License-File"):
        licenses.generate_licenses_text(
            site_packages, make_python_dir(tmp_path), "3.12.7")


# --- CPython entry and fail-closed behavior ---------------------------------


def test_cpython_entry_uses_the_python_dir_license(site_packages, tmp_path):
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path, "REAL PSF TEXT"), "3.12.7")
    section = _section(text, "CPython")
    assert "Version: 3.12.7" in section
    assert "REAL PSF TEXT" in section


def test_missing_cpython_license_fails_closed(site_packages, tmp_path):
    with pytest.raises(RuntimeError, match="CPython license file is missing"):
        licenses.generate_licenses_text(
            site_packages, make_python_dir(tmp_path, None), "3.12.7")


def test_malformed_metadata_fails_closed(site_packages, tmp_path):
    # Decision 8: a package that ships but cannot be attributed fails
    # the build -- it is never silently skipped.
    make_dist_info(site_packages, "Broken", "1.0", metadata="Version: 1.0\n")
    with pytest.raises(ValueError, match="malformed METADATA"):
        licenses.generate_licenses_text(
            site_packages, make_python_dir(tmp_path), "3.12.7")


def test_missing_metadata_file_fails_closed(site_packages, tmp_path):
    dist_info = site_packages / "ghost-1.0.dist-info"
    dist_info.mkdir()
    with pytest.raises(ValueError, match="METADATA file is missing"):
        licenses.generate_licenses_text(
            site_packages, make_python_dir(tmp_path), "3.12.7")


def test_missing_site_packages_dir_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="site-packages directory not found"):
        licenses.generate_licenses_text(
            tmp_path / "nope", make_python_dir(tmp_path), "3.12.7")


# --- determinism and ordering ------------------------------------------------


def test_output_is_byte_deterministic(site_packages, tmp_path):
    make_dist_info(site_packages, "Zeta", "1.0",
                   extra_headers=("License-Expression: MIT",),
                   license_texts={"licenses/LICENSE.txt": "Z TEXT"})
    make_dist_info(site_packages, "alpha-pkg", "2.0",
                   extra_headers=("License: BSD",))
    python_dir = make_python_dir(tmp_path)
    first = licenses.generate_licenses_text(site_packages, python_dir, "3.12.7")
    second = licenses.generate_licenses_text(site_packages, python_dir, "3.12.7")
    assert first == second
    # Sorted by normalized name: alpha-pkg, CPython, Zeta.
    names = re.findall(r"^Name: (.+)$", first, re.MULTILINE)
    assert names == ["alpha-pkg", "CPython", "Zeta"]


# --- lock coverage ------------------------------------------------------------


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
        assert match, f"lock line is not an exact pin with a hash: {raw!r}"
        entries[_normalized(match.group(1))] = match.group(2)
    return entries


def test_every_lock_package_and_cpython_appear(site_packages, tmp_path):
    entries = _lock_entries()
    for name, version in entries.items():
        make_dist_info(
            site_packages, name, version,
            extra_headers=("License-Expression: MIT",),
            license_texts={"licenses/LICENSE.txt": f"LICENSE OF {name}"},
        )
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    found = {_normalized(n) for n in re.findall(r"^Name: (.+)$", text, re.MULTILINE)}
    assert set(entries) <= found
    assert "cpython" in found
    assert f"Components: {len(entries) + 1} (including CPython)" in text
    for name in entries:
        assert f"LICENSE OF {name}" in text


# --- real-environment smoke ---------------------------------------------------


def test_real_site_packages_smoke(tmp_path):
    candidates = [Path(sysconfig.get_paths()["purelib"]),
                  Path(sysconfig.get_paths()["platlib"])]
    site_packages = next((p for p in candidates if p.is_dir()), None)
    assert site_packages is not None
    text = licenses.generate_licenses_text(
        site_packages, make_python_dir(tmp_path), "3.12.7")
    flask = _section(text, "Flask")
    assert re.search(r"^Version: \S+", flask, re.MULTILINE)
    assert "License:" in flask
    assert licenses.MISSING_TEXT_NOTE not in flask
    assert len(flask) > 200  # Flask's real license text is included


# --- CLI ----------------------------------------------------------------------


def test_cli_writes_the_output_file(site_packages, tmp_path):
    make_dist_info(site_packages, "Demo", "1.0",
                   extra_headers=("License-Expression: MIT",))
    output = tmp_path / "staging" / "THIRD-PARTY-LICENSES.txt"
    output.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH),
         "--site-packages", str(site_packages),
         "--python-dir", str(make_python_dir(tmp_path)),
         "--python-version", "3.12.7",
         "--output", str(output)],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "Name: Demo" in output.read_text()


def test_cli_fails_closed_on_malformed_metadata(site_packages, tmp_path):
    make_dist_info(site_packages, "Broken", "1.0", metadata="Name: Broken\n")
    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH),
         "--site-packages", str(site_packages),
         "--python-dir", str(make_python_dir(tmp_path)),
         "--python-version", "3.12.7",
         "--output", str(tmp_path / "out.txt")],
        capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "malformed METADATA" in result.stderr
    assert not (tmp_path / "out.txt").exists()


# --- build.py wiring ------------------------------------------------------------


def test_build_py_invokes_the_generator_at_the_staging_root():
    source = BUILD_PATH.read_text()
    assert "generate_third_party_licenses(staging, python_dir, pyver)" in source
    assert 'staging / "THIRD-PARTY-LICENSES.txt"' in source
    assert "third_party_licenses.py" in source


def test_build_py_runs_the_generator_with_the_build_machine_python(tmp_path):
    staging = tmp_path / "staging"
    python_dir = staging / "python"
    python_dir.mkdir(parents=True)
    seen = {}

    def fake_run(cmd, check):
        seen["cmd"] = cmd
        seen["check"] = check

    real_run = build.subprocess.run
    build.subprocess.run = fake_run
    try:
        build.generate_third_party_licenses(staging, python_dir, "3.12.7")
    finally:
        build.subprocess.run = real_run

    cmd = seen["cmd"]
    assert seen["check"] is True
    assert cmd[0] == sys.executable  # build machine Python, not embedded
    assert cmd[1].endswith("third_party_licenses.py")
    assert str(python_dir / "Lib" / "site-packages") in cmd
    assert str(python_dir) in cmd
    assert "3.12.7" in cmd
    assert str(staging / "THIRD-PARTY-LICENSES.txt") in cmd


def test_build_py_generator_failure_fails_the_build(tmp_path):
    staging = tmp_path / "staging"
    python_dir = staging / "python"
    python_dir.mkdir(parents=True)

    def failing_run(cmd, check):
        raise subprocess.CalledProcessError(2, cmd)

    real_run = build.subprocess.run
    build.subprocess.run = failing_run
    try:
        with pytest.raises(subprocess.CalledProcessError):
            build.generate_third_party_licenses(staging, python_dir, "3.12.7")
    finally:
        build.subprocess.run = real_run
