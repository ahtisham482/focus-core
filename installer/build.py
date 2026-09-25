"""Assemble the Focus Core installer staging folder (Windows only).

What it does:
  1. Downloads the Python embeddable zip and extracts it to staging/python.
  2. Enables site-packages (uncomments `import site` in python3XX._pth).
  3. Bootstraps pip with get-pip.py.
  4. Installs requirements.txt into the embedded Python (dev-only
     packages like pytest are skipped).
  5. Copies the focuscore/ and dashboard/ packages into staging/.
  6. Writes the .installed marker (tells the app to use the per-user
     data folder instead of writing next to the code).
  7. Downloads the WebView2 Evergreen bootstrapper (run by the installer
     only when WebView2 is missing).
  8. Builds icon.ico from the app icon (needs Pillow on the BUILD machine:
     `pip install pillow`).

Run from the repo root:
    python installer/build.py --version 1.3.0

Then compile installer/installer.iss with Inno Setup 6
(ISCC.exe installer\\installer.iss /DAppVersion="1.3.0").

Only the standard library is used, except Pillow for the icon step.
"""

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_PYTHON_VERSION = "3.12.7"  # must exist on python.org FTP
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
DEV_ONLY_PACKAGES = ("pytest",)  # never shipped inside the installer


def download(url, dest):
    print("Downloading %s ..." % url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    print("  -> %s (%d bytes)" % (dest, dest.stat().st_size))


def enable_site_packages(python_dir):
    """Uncomment `import site` in python3XX._pth (disabled by default)."""
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
    pth.write_text("".join(out))
    print("Patched %s (enabled import site)" % pth.name)


def run_embedded_python(python_dir, *args):
    exe = python_dir / "python.exe"
    cmd = [str(exe)] + list(args)
    print("Running: %s" % " ".join(cmd))
    subprocess.run(cmd, check=True)


def build_icon(repo_root, staging_dir):
    """Build icon.ico from the dashboard icon (needs Pillow)."""
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError(
            "Pillow is needed on the build machine: pip install pillow")
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
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    staging = Path(args.staging) if args.staging else repo_root / "installer" / "staging"
    python_dir = staging / "python"

    if staging.exists():
        print("Removing old staging dir %s" % staging)
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    pyver = args.python_version
    embed_url = ("https://www.python.org/ftp/python/%s/"
                 "python-%s-embed-amd64.zip" % (pyver, pyver))
    embed_zip = staging / "_embed.zip"
    download(embed_url, embed_zip)
    print("Extracting embedded Python ...")
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(python_dir)
    embed_zip.unlink()

    enable_site_packages(python_dir)

    # Add the app root to sys.path so focuscore/dashboard are importable.
    # The embedded .pth only enables site-packages by default; without this
    # line every new install silently fails to import our packages.
    pth_files = sorted(python_dir.glob("python3*._pth"))
    if pth_files:
        pth = pth_files[0]
        with open(pth, 'a') as f:
            f.write('..\n')
        print("Patched %s (added app root '..' to path)" % pth.name)
    else:
        print("WARNING: no ._pth file found — imports may fail in installed copy!")

    get_pip = staging / "_get-pip.py"
    download(GET_PIP_URL, get_pip)
    run_embedded_python(python_dir, str(get_pip))
    get_pip.unlink()

    # The embeddable Python ships without setuptools/wheel; packages that
    # have no wheel for this Python need them at install time.
    run_embedded_python(python_dir, "-m", "pip", "install", "--quiet", "setuptools", "wheel")

    # Install runtime deps (skip dev-only packages).
    req_src = repo_root / "requirements.txt"
    req_tmp = staging / "_requirements.txt"
    kept = [ln for ln in req_src.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
            and not any(ln.strip().lower().startswith(p)
                        for p in DEV_ONLY_PACKAGES)]
    req_tmp.write_text("\n".join(kept) + "\n")
    run_embedded_python(python_dir, "-m", "pip", "install", "--quiet",
                        "-r", str(req_tmp))
    req_tmp.unlink()

    for pkg in ("focuscore", "dashboard"):
        src = repo_root / pkg
        print("Copying %s ..." % pkg)
        shutil.copytree(src, staging / pkg,
                        ignore=shutil.ignore_patterns("__pycache__"))

    (staging / ".installed").write_text(
        "Focus Core %s -- installed copy; user data lives in the per-user "
        "data folder (see focuscore.paths).\n" % args.version)

    download(WEBVIEW2_BOOTSTRAPPER_URL,
             staging / "webview2bootstrapper.exe")

    build_icon(repo_root, staging)

    total = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
    print("\nStaging complete: %s (%.1f MB)" % (staging, total / 1e6))
    print("Next: compile installer/installer.iss with Inno Setup 6:")
    print('  ISCC.exe installer\\installer.iss /DAppVersion="%s"' % args.version)


if __name__ == "__main__":
    sys.exit(main())
