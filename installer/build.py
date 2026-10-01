"""Assemble the Focus Core installer staging folder (Windows only).

What it does:
  1. Downloads the Python embeddable zip and extracts it to staging/python.
  2. Enables site-packages (uncomments `import site` in python3XX._pth).
  3. Bootstraps pip with get-pip.py.
  4. Installs requirements-lock.txt into the embedded Python (exact pins
     with SHA-256 hashes; dev-only packages like pytest are not in the
     lock and are never shipped).
  5. Copies the focuscore/ and dashboard/ packages into staging/.
  6. Writes the .installed marker (tells the app to use the per-user
     data folder instead of writing next to the code).
  7. Generates THIRD-PARTY-LICENSES.txt in the staging root from the
     staged packages' own metadata and license files (any failure
     fails the build; see installer/third_party_licenses.py).
  8. Downloads the WebView2 Evergreen bootstrapper (SHA-256 verified;
     run by the installer only when WebView2 is missing).
  9. Builds icon.ico from the app icon (needs Pillow on the BUILD machine:
     `python -m pip install --require-hashes -r requirements-lock.txt`).

Run from the repo root:
    python installer/build.py --version 1.3.0

Then compile installer/installer.iss with Inno Setup 6
(ISCC.exe installer\\installer.iss /DAppVersion="1.3.0").

Only the standard library is used, except Pillow for the icon step.
"""

import argparse
import hashlib
import hmac
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_PYTHON_VERSION = "3.12.7"  # must exist on python.org FTP
# SHA-256 of python-<version>-embed-amd64.zip from python.org. Refuse to
# build with an embedded Python we cannot verify byte-for-byte.
PYTHON_EMBED_SHA256 = {
    "3.12.7": "0d57bb6cb078b74d23dbfe91f77d6780d45bed328911609f1f7ee2ba1606bf44",
}
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
# SHA-256 of the get-pip.py served by GET_PIP_URL when this pin was made.
# get-pip.py is unversioned upstream, so when PyPA publishes a new one this
# build fails closed until the new file is reviewed and this hash refreshed.
GET_PIP_SHA256 = "fb24e693bab954209a063d90953621412ccad4a500905a726286e038f508ddf6"
# Build tools are installed before the runtime lock: the embeddable Python
# ships without setuptools/wheel, and the one source-only lock entry
# (proxy-tools) must build with these pinned tools, not floating latest.
BOOTSTRAP_REQUIREMENTS = (
    ("setuptools==84.0.0 "
     "--hash=sha256:51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670"),
    ("wheel==0.48.0 "
     "--hash=sha256:3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab"),
    ("packaging==26.3 "
     "--hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"),
)
LOCK_FILE_NAME = "requirements-lock.txt"
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
# SHA-256 of the WebView2 bootstrapper served by WEBVIEW2_BOOTSTRAPPER_URL
# when this pin was made. The bootstrapper is evergreen upstream, so when
# Microsoft publishes a new one this build fails closed until the new file
# is reviewed and this hash refreshed.
WEBVIEW2_BOOTSTRAPPER_SHA256 = (
    "81c01751c8cc385a5991abb104205d42ac70094350ee8fb9e8ea580b51bb9554")


def download(url, dest):
    print("Downloading %s ..." % url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    print("  -> %s (%d bytes)" % (dest, dest.stat().st_size))


def verify_sha256(path, expected_sha256, label):
    """Return the file's SHA-256, or delete it and raise on mismatch."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if not hmac.compare_digest(actual, expected_sha256.lower()):
        path.unlink(missing_ok=True)
        raise RuntimeError(
            "SHA-256 mismatch for %s: expected %s, got %s; "
            "deleted the download" % (label, expected_sha256, actual))
    print("Verified %s SHA-256" % label)
    return actual


def download_verified(url, dest, expected_sha256, label):
    download(url, dest)
    verify_sha256(dest, expected_sha256, label)


def install_locked_dependencies(python_dir, repo_root, staging):
    """Install the pinned bootstrap tools, then the locked runtime deps."""
    lock_path = repo_root / LOCK_FILE_NAME
    if not lock_path.is_file():
        raise RuntimeError("Missing installer lock file: %s" % lock_path)

    bootstrap_path = staging / "_bootstrap-requirements.txt"
    bootstrap_path.write_text("\n".join(BOOTSTRAP_REQUIREMENTS) + "\n")
    try:
        run_embedded_python(python_dir, "-m", "pip", "install", "--quiet",
                            "--require-hashes", "-r", str(bootstrap_path))
    finally:
        bootstrap_path.unlink(missing_ok=True)

    # --no-build-isolation: the one source-only lock entry (proxy-tools)
    # builds with the pinned setuptools/wheel installed just above.
    run_embedded_python(python_dir, "-m", "pip", "install", "--quiet",
                        "--require-hashes", "--no-build-isolation",
                        "-r", str(lock_path))


def enable_site_packages(python_dir):
    """Uncomment `import site` in python3XX._pth and add the app root.

    The embeddable Python only sees its own folder by default, but the
    app code (focuscore/, dashboard/) lives one level above it, so we
    append `..` (relative to the ._pth file) to sys.path. Without this,
    `-m focuscore.launcher` fails with "No module named focuscore".
    (Mirrors PC commit 1225f0d, found during the v1.3.0 install test.)
    """
    matches = sorted(python_dir.glob("python3*._pth"))
    if not matches:
        raise RuntimeError("No python3*._pth found in %s" % python_dir)
    pth = matches[0]
    lines = pth.read_text().splitlines(keepends=True)
    changed = False
    out = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") and "import site" in stripped:
            out.append("import site\n")
            changed = True
        else:
            out.append(line)
    if not changed:
        raise RuntimeError("Could not find '#import site' in %s" % pth)
    out.append("..\n")
    pth.write_text("".join(out))
    print("Patched %s (enabled import site, added app root)" % pth.name)


def run_embedded_python(python_dir, *args):
    exe = python_dir / "python.exe"
    cmd = [str(exe)] + list(args)
    print("Running: %s" % " ".join(cmd))
    subprocess.run(cmd, check=True)


def generate_third_party_licenses(staging, python_dir, python_version):
    """Write staging/THIRD-PARTY-LICENSES.txt from the staged tree.

    Runs installer/third_party_licenses.py with the BUILD machine's
    Python (sys.executable), never the embedded one: generation only
    reads files. check=True means any generator failure fails the
    build -- shipping without attribution is not an option.
    """
    script = Path(__file__).resolve().parent / "third_party_licenses.py"
    cmd = [
        sys.executable,
        str(script),
        "--site-packages", str(python_dir / "Lib" / "site-packages"),
        "--python-dir", str(python_dir),
        "--python-version", python_version,
        "--output", str(staging / "THIRD-PARTY-LICENSES.txt"),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def build_icon(repo_root, staging_dir):
    """Build icon.ico from the dashboard icon (needs Pillow)."""
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError(
            "Pillow is needed on the build machine: "
            "python -m pip install --require-hashes -r requirements-lock.txt")
    src = repo_root / "dashboard" / "static" / "icon.png"
    dest = staging_dir / "icon.ico"
    img = Image.open(src)
    img.save(dest, sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                          (128, 128), (256, 256)])
    print("Wrote %s" % dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True,
                        help="App version, e.g. 1.3.0")
    parser.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION,
                        help="Embedded Python version (default %s)"
                             % DEFAULT_PYTHON_VERSION)
    parser.add_argument("--staging", default=None,
                        help="Staging dir (default installer/staging)")
    parser.add_argument("--repo", default="",
                        help="GitHub repo slug for updates, e.g. owner/name "
                             "(enables one-click updates; empty disables)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    staging = (Path(args.staging) if args.staging
               else repo_root / "installer" / "staging")
    python_dir = staging / "python"

    pyver = args.python_version
    if pyver not in PYTHON_EMBED_SHA256:
        raise RuntimeError(
            "No pinned SHA-256 for the Python %s embed zip; add its "
            "official python.org hash to PYTHON_EMBED_SHA256 before "
            "building" % pyver)
    embed_sha256 = PYTHON_EMBED_SHA256[pyver]

    if staging.exists():
        print("Removing old staging dir %s" % staging)
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    embed_url = ("https://www.python.org/ftp/python/%s/"
                 "python-%s-embed-amd64.zip" % (pyver, pyver))
    embed_zip = staging / "_embed.zip"
    download_verified(embed_url, embed_zip, embed_sha256,
                      "Python %s embed zip" % pyver)
    print("Extracting embedded Python ...")
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(python_dir)
    embed_zip.unlink()

    enable_site_packages(python_dir)

    get_pip = staging / "_get-pip.py"
    download_verified(GET_PIP_URL, get_pip, GET_PIP_SHA256, "get-pip.py")
    run_embedded_python(python_dir, str(get_pip))
    get_pip.unlink()

    install_locked_dependencies(python_dir, repo_root, staging)

    for pkg in ("focuscore", "dashboard"):
        src = repo_root / pkg
        print("Copying %s ..." % pkg)
        shutil.copytree(src, staging / pkg,
                        ignore=shutil.ignore_patterns("__pycache__"))

    (staging / ".installed").write_text(
        "Focus Core %s -- installed copy; user data lives in the per-user "
        "data folder (see focuscore.paths).\n" % args.version)

    generate_third_party_licenses(staging, python_dir, pyver)

    # Stamp the release source + version so installed copies can check
    # GitHub Releases for one-click updates (focuscore/updater.py).
    if args.repo:
        info = {"repo": args.repo, "version": args.version}
        (staging / "update-info.json").write_text(json.dumps(info) + "\n")
        print("Wrote update-info.json (repo %s)" % args.repo)

    download_verified(WEBVIEW2_BOOTSTRAPPER_URL,
                      staging / "webview2bootstrapper.exe",
                      WEBVIEW2_BOOTSTRAPPER_SHA256, "WebView2 bootstrapper")

    build_icon(repo_root, staging)

    total = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
    print("\nStaging complete: %s (%.1f MB)" % (staging, total / 1e6))
    print("Next: compile installer/installer.iss with Inno Setup 6:")
    print('  ISCC.exe installer\\installer.iss /DAppVersion="%s"' % args.version)


if __name__ == "__main__":
    sys.exit(main())
