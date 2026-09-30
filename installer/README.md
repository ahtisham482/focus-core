# Focus Core installer (Phase 2)

One file, `FocusCore-Setup-<version>.exe`, that installs Focus Core on any
64-bit Windows 10/11 PC. The user needs nothing pre-installed: **Python and
all packages are bundled inside** (embedded Python), so it is truly
Download → Next → Next → desktop icon.

## How it works

`installer/build.py` assembles a staging folder:

| staging/… | what it is |
|---|---|
| `python/` | Embedded Python 3.12 + pip + all of `requirements-lock.txt` (exact pins with SHA-256 hashes; dev-only packages like pytest are not in the lock) |
| `focuscore/`, `dashboard/` | The app code |
| `.installed` | Marker: tells the app to keep user data in `%LOCALAPPDATA%\Focus Core` instead of next to the code |
| `webview2bootstrapper.exe` | Runs only if WebView2 is missing (most PCs already have it) |
| `icon.ico` | Built from `dashboard/static/icon.png` (needs Pillow on the build machine) |

`installer/installer.iss` (Inno Setup 6) wraps it into the setup exe:

- **Per-user install** (`PrivilegesRequired=lowest`) → no admin/UAC prompt,
  installs to `%LOCALAPPDATA%\Programs\Focus Core`.
- Shortcuts: Start Menu (always), desktop icon (default on), optional
  "start with Windows".
- **Uninstall never deletes user data** — `%LOCALAPPDATA%\Focus Core`
  (the database, backups) is left alone.

## Build it yourself (on Windows)

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) (free).
2. From the repo root:
   ```
   python -m pip install --require-hashes -r requirements-lock.txt
   python installer\build.py --version 1.3.0
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss /DAppVersion="1.3.0"
   ```
3. The exe appears as `installer\FocusCore-Setup-1.3.0.exe` (~25 MB).

## Locked installer inputs

Installer builds do not float on latest upstream releases:

- `requirements-lock.txt` pins every Python package installed into the
  embedded Python, with the SHA-256 of the CPython 3.12 / Windows x64
  artifact pip must download. (`requirements.txt` keeps the floating ranges
  for normal CI test runs.)
- `installer/build.py` verifies the Python embed zip, `get-pip.py`, and
  the WebView2 bootstrapper against pinned SHA-256 constants before using
  any of them, and deletes a download whose hash does not match.

To refresh the lock at release time, resolve the runtime requirements
(`requirements.txt` minus pytest) for CPython 3.12 / Windows x64, download
the selected artifacts, and replace each pin's version and hash with the
downloaded artifact's. The one source-only package is `proxy-tools` (a
pywebview dependency); the build installs pinned setuptools/wheel first so
it builds with those, not with floating latest. If `get-pip.py` changes
upstream, the build fails closed until `GET_PIP_SHA256` in
`installer/build.py` is refreshed after reviewing the new file. The same
fail-closed rule applies to the evergreen WebView2 bootstrapper: if
Microsoft publishes a new one, the build fails until
`WEBVIEW2_BOOTSTRAPPER_SHA256` is refreshed after reviewing the new file.

## CI build

`.github/workflows/installer.yml` does the same on a Windows runner:

- Push a tag like `v1.3.0` → CI stages, compiles, and **attaches the setup
  exe to the GitHub Release** automatically.
- Or run it manually (Actions → "Build installer" → Run workflow) with a
  version number; the exe is kept as a workflow artifact.

## Notes for strangers

- **SmartScreen:** the installer is unsigned, so Windows may show an
  "Unknown publisher" warning on first run. Click *More info → Run anyway*.
  (Code signing is a paid certificate — planned if the app sells.)
- **Antivirus:** we ship plain `.py` files with embedded Python, not a
  packed exe, which is the pattern AVs usually flag.
- **ActivityWatch:** Focus Core reads tracked time from ActivityWatch.
  The app's home page says what to do when no data is coming in; a guided
  ActivityWatch install is planned (Sprint 2, Phase 4: onboarding).
- **Moving PCs / keeping history:** make a backup first (Backup page →
  "Back up now"), then restore it on the new PC. Uninstalling never
  deletes `%LOCALAPPDATA%\Focus Core`.
