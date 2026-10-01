"""Generate THIRD-PARTY-LICENSES.txt from an installed site-packages tree.

Roadmap 1.10. Permissive licenses still require attribution, and
enterprise procurement asks for the list, so the installer ships a
license file covering the embedded CPython and every pip package in
staging. The file is generated at installer build time from the exact
metadata and license texts that ship -- never from the build machine's
own environment, and never from license texts pasted in from memory.

Only the Python standard library is used.

Usage (from the repo root):
    python installer/third_party_licenses.py \
        --site-packages installer/staging/python/Lib/site-packages \
        --python-dir installer/staging/python \
        --python-version 3.12.7 \
        --output installer/staging/THIRD-PARTY-LICENSES.txt

installer/build.py runs this as a build step once the locked
dependencies and the app code are in staging; any failure here fails
the build.

Failure policy (fail closed): a dist-info whose METADATA is missing,
unparseable, or lacks a Name/Version ships in the installer but cannot
be attributed, so generation raises instead of silently skipping it. A
License-File header naming a file that does not exist raises too.
The CPython license is read from <python-dir>/LICENSE.txt; if that
file is absent, generation raises -- the PSF text is never hardcoded.
"""

import argparse
import re
import sys
from pathlib import Path

MISSING_TEXT_NOTE = "license text not present in the installed package files"
_TEXT_FILE_PREFIXES = ("license", "copying", "notice")
_UNKNOWN_LICENSES = ("", "unknown")
_SEPARATOR = "=" * 72


def _normalized(name):
    """PEP 503 normalized name, used for sorting and comparisons."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_metadata(text, source_label):
    """Parse an RFC 822 style METADATA file into {lower-name: [values]}.

    Raises ValueError when the header block is malformed or the
    mandatory Name/Version headers are missing or empty.
    """
    headers = {}
    current = None
    in_body = False
    for line in text.splitlines():
        if in_body:
            break
        if line == "":
            in_body = True
            continue
        if line[0] in " \t":
            if current is None:
                raise ValueError(
                    f"malformed METADATA in {source_label}: "
                    "continuation line before any header")
            headers[current][-1] += " " + line.strip()
            continue
        if ":" not in line:
            raise ValueError(
                f"malformed METADATA in {source_label}: "
                f"header line without a colon: {line!r}")
        name, _, value = line.partition(":")
        name = name.strip().lower()
        if not name:
            raise ValueError(
                f"malformed METADATA in {source_label}: "
                f"empty header name in line: {line!r}")
        headers.setdefault(name, []).append(value.strip())
        current = name

    for field in ("name", "version"):
        values = headers.get(field)
        if not values or not values[0]:
            raise ValueError(
                f"malformed METADATA in {source_label}: "
                f"missing or empty {field!r} header")
    return headers


def _declared_license(headers):
    """License-Expression > License field > OSI classifiers."""
    for field in ("license-expression", "license"):
        for value in headers.get(field, []):
            # Fold continuation whitespace; a real METADATA License field
            # is a single line, but stay safe.
            value = " ".join(value.split())
            if value.lower() not in _UNKNOWN_LICENSES:
                return value
    prefix = "License :: OSI Approved ::"
    classifiers = []
    for classifier in headers.get("classifier", []):
        if classifier.startswith(prefix):
            name = classifier[len(prefix):].strip()
            if name and name not in classifiers:
                classifiers.append(name)
    if classifiers:
        # A distribution may declare several licenses via classifiers;
        # keep them all, in a deterministic (sorted) order.
        return " / ".join(sorted(classifiers))
    return "not declared in the installed package metadata"


def _is_license_text_file(path):
    return path.is_file() and path.name.lower().startswith(_TEXT_FILE_PREFIXES)


def _package_top_level_dirs(site_packages, dist_info):
    """Top-level package dirs of this distribution, from its RECORD."""
    record = dist_info / "RECORD"
    tops = set()
    if record.is_file():
        for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
            first = line.split(",", 1)[0].strip().split("/")[0]
            if not first or first.endswith(".dist-info"):
                continue
            candidate = site_packages / first
            if candidate.is_dir():
                tops.add(candidate)
    return sorted(tops)


def _find_license_texts(site_packages, dist_info, headers):
    """License text files shipped with this distribution, in fixed order.

    Sources, in the order their texts are emitted: the dist-info
    ``licenses/`` directory, ``License-File`` headers, LICENSE*/
    COPYING*/NOTICE* files directly in the dist-info, then directly in
    each top-level package directory. Duplicate paths are emitted once.
    """
    found = []
    seen = set()

    def add(path):
        key = path.resolve()
        if key not in seen and path.is_file():
            seen.add(key)
            found.append(path)

    licenses_dir = dist_info / "licenses"
    if licenses_dir.is_dir():
        for path in sorted(licenses_dir.iterdir(), key=lambda p: p.name):
            suffix = path.suffix.lower()
            if _is_license_text_file(path) or suffix in (".txt", ".md", ".rst"):
                add(path)
    for rel in headers.get("license-file", []):
        # A License-File header is an explicit promise that the text
        # ships; a referenced-but-absent file fails closed instead of
        # silently falling through to the missing-text note. PEP 639
        # paths are relative to the dist-info; older setuptools wrote
        # bare names for files placed under licenses/ (e.g. boolean.py
        # declares "License-File: LICENSE.txt" for licenses/LICENSE.txt),
        # so accept that placement too -- it is harvested above anyway.
        path = dist_info / rel
        legacy = dist_info / "licenses" / rel
        if path.is_file():
            add(path)
        elif legacy.is_file():
            add(legacy)
        else:
            raise ValueError(
                f"malformed dist-info {dist_info.name}: License-File "
                f"{rel!r} does not exist")
    for path in sorted(dist_info.iterdir(), key=lambda p: p.name):
        if _is_license_text_file(path):
            add(path)
    for top in _package_top_level_dirs(site_packages, dist_info):
        for path in sorted(top.iterdir(), key=lambda p: p.name):
            if _is_license_text_file(path):
                add(path)
    return found


class Component:
    def __init__(self, name, version, license_declared, license_texts):
        self.name = name
        self.version = version
        self.license_declared = license_declared
        # [(display source path, text), ...]; empty means no text found.
        self.license_texts = license_texts

    @property
    def sort_key(self):
        return (_normalized(self.name), self.version)


def _component_from_dist_info(site_packages, dist_info):
    metadata_path = dist_info / "METADATA"
    if not metadata_path.is_file():
        raise ValueError(
            f"malformed dist-info {dist_info.name}: METADATA file is missing")
    try:
        text = metadata_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(
            f"malformed dist-info {dist_info.name}: cannot read METADATA: {exc}")
    headers = _parse_metadata(text, dist_info.name)
    texts = []
    for path in _find_license_texts(site_packages, dist_info, headers):
        body = path.read_bytes().decode("utf-8", errors="replace").strip()
        texts.append((str(path.relative_to(site_packages)), body))
    return Component(
        name=headers["name"][0],
        version=headers["version"][0],
        license_declared=_declared_license(headers),
        license_texts=texts,
    )


def _cpython_component(python_dir, python_version):
    license_path = python_dir / "LICENSE.txt"
    if not license_path.is_file():
        raise RuntimeError(
            f"CPython license file is missing: {license_path}; "
            "the embedded Python tree is incomplete -- refusing to "
            "generate a license file without the PSF text")
    body = license_path.read_bytes().decode("utf-8", errors="replace").strip()
    return Component(
        name="CPython",
        version=python_version,
        license_declared="PSF (Python Software Foundation)",
        license_texts=[("python/LICENSE.txt", body)],
    )


def collect_components(site_packages, python_dir, python_version):
    """All shipped components (pip packages + CPython), sorted."""
    site_packages = Path(site_packages)
    if not site_packages.is_dir():
        raise ValueError(f"site-packages directory not found: {site_packages}")
    components = [
        _component_from_dist_info(site_packages, dist_info)
        for dist_info in sorted(site_packages.glob("*.dist-info"))
        if dist_info.is_dir()
    ]
    components.append(_cpython_component(Path(python_dir), python_version))
    components.sort(key=lambda c: c.sort_key)
    return components


def generate_licenses_text(site_packages, python_dir, python_version):
    """The deterministic THIRD-PARTY-LICENSES.txt content."""
    components = collect_components(site_packages, python_dir, python_version)
    lines = [
        "THIRD-PARTY LICENSES",
        "=" * 20,
        "",
        "Focus Core is distributed with the third-party components",
        "listed below. This file is generated at installer build time",
        "from the metadata and license files of the exact packages",
        "installed into the shipped tree; it is not edited by hand.",
        "",
        f"Components: {len(components)} (including CPython)",
    ]
    for component in components:
        lines += [
            "",
            _SEPARATOR,
            f"Name: {component.name}",
            f"Version: {component.version}",
            f"License: {component.license_declared}",
            "",
        ]
        if component.license_texts:
            for source, body in component.license_texts:
                lines.append(f"License text ({source}):")
                lines.append("")
                lines.append(body)
                lines.append("")
        else:
            lines.append(f"License text: {MISSING_TEXT_NOTE}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site-packages", required=True,
                        help="Staging site-packages dir to scan")
    parser.add_argument("--python-dir", required=True,
                        help="Embedded Python dir (holds LICENSE.txt)")
    parser.add_argument("--python-version", required=True,
                        help="Embedded Python version, e.g. 3.12.7")
    parser.add_argument("--output", required=True,
                        help="Where to write THIRD-PARTY-LICENSES.txt")
    args = parser.parse_args(argv)

    try:
        text = generate_licenses_text(
            args.site_packages, args.python_dir, args.python_version)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output = Path(args.output)
    output.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {output} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
