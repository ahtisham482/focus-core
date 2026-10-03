# Enterprise deployment

Notes for IT administrators who deploy Focus Core to managed Windows
machines. This page covers silent install and uninstall, wrapping the
installer for Intune, the **offline installer** for machines with no
network, the **per-machine installer** for fleet installs, pinning the
fleet to a fixed version by disabling the app's self-updater, and what
happens to Focus Core if this project is ever abandoned.

## Per-machine install (fleet)

For fleets, IT installs once per machine instead of once per user.
Ship **`FocusCore-Setup-<version>-machine.exe`** (built with the
`/DPerMachine` Inno define):

- Installs to `C:\Program Files\Focus Core` (`{autopf}`) with
  `PrivilegesRequired=admin` — the installer asks for elevation.
  64-bit install mode (`ArchitecturesInstallIn64BitMode`): requires
  64-bit Windows 10/11, matching the amd64 embedded Python runtime.
- Carries its own application identity (distinct AppId), so it never
  fights a per-user install over "already installed" state. The two
  flavors can even coexist on one machine.
- Shortcuts and the optional startup entry go to the all-users
  locations (`{autoprograms}`, `{autodesktop}`, `{commonstartup}`).
- The machine and offline flavors compose:
  `FocusCore-Setup-<version>-machine-offline.exe` bundles the full
  WebView2 installer for air-gapped fleets.

### Intune (System context)

Wrap it exactly like the per-user installer (`.intunewin` via
`IntuneWinAppUtil.exe`), then set:

- **Install command:**
  `FocusCore-Setup-<version>-machine.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="%TEMP%\FocusCore-install.log"`
- **Uninstall command:**
  `"C:\Program Files\Focus Core\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`
- **Install behavior:** System. (The 2.2 section above documents the
  per-user installer with **Install behavior:** User — that stays
  correct for the per-user file; use System only with the `-machine`
  file.)
- **Detection rule:** Rule type **Registry** — Key path
  `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{FAFD1C6B-54C9-4A1A-AD3C-8BE1DD383D28}_is1`,
  Value name `DisplayVersion`, detection method **String comparison**,
  Operator **Equals**, Value `<version>`. (The per-user flavor's key
  lives under `HKEY_CURRENT_USER` with its own AppId — the two never
  collide.)
- **First-launch token:** the machine installer runs elevated, but its
  "start the app" entries use Inno's `runasoriginaluser`, so an
  interactive install — or a one-click update, which shows a UAC
  prompt — launches the app as the installing user, never as admin,
  keeping `%LOCALAPPDATA%` on the right profile. Under Intune
  **System** context there is no original-user token, so the flag is
  ignored there: a silent System install may still first-launch the
  app as SYSTEM, and it lands per-user only when a user actually
  opens it. (The interactive "Launch Focus Core now" entry already
  carries `skipifsilent`, so silent installs never offer it twice.)

### Data-dir policy

Default for **both** flavors: each user's data stays in their own
`%LOCALAPPDATA%\Focus Core` — the correct Windows pattern (standard
users cannot write to a shared folder without ACL surgery, so the
installer never forces a machine data dir).

To keep the fleet's data in one machine location instead, set the
machine-tier override (Group Policy, SCCM, or Intune):

- Key: `HKEY_LOCAL_MACHINE\Software\Focus Core`
- Value: `DataDir` (string), e.g. `C:\ProgramData\Focus Core`

The app reads this first in installed mode; make sure the folder
grants the Users group write access. The uninstaller's data wipe
(`/DELETEDATA`, Roadmap 2.4) follows the same rule in the machine
flavor: it wipes the `DataDir` value when set, else
`%PROGRAMDATA%\Focus Core` when it exists — and it never touches
per-user profile folders (`C:\Users\*\AppData\Local\Focus Core` are
IT's to wipe with their own tooling).

### Updates on machine installs

The one-click updater is flavor-aware: a machine install downloads
the `-machine` setup exe, never the per-user one. Because the
machine installer needs admin rights, applying the update shows a
**UAC prompt** — that is the honest behavior (explicit elevation,
not a silent privilege grab); the app never tries to auto-elevate.
Fleets that manage updates centrally should pin the version instead
(the machine policy below): with `updates_disabled` set, the app
never checks, downloads, or applies anything.

Honest status: the machine flavor compiles and its silent
install/uninstall runs in CI on every push
(`installer-smoke.yml`, job `machine-installer`); no customer fleet
has deployed it yet — treat the first pilot group as the proof run.

## For IT: antivirus pre-allow list

Before rolling out, read `docs/av-false-positives.md` and pre-allow
accordingly. The short version:

- The shield **minimizes** distracting windows during focus sessions —
  it never terminates other programs. The only processes Focus Core
  ever kills are its own dashboard server child.
- The dashboard listens on **127.0.0.1 only** — no inbound network
  access needed; a firewall prompt can be safely answered "no".
- It phones home to exactly three places: `api.github.com` (update
  checks, can be disabled), your own mail app (crash reports, only if
  the user clicks send), and a local backup folder.
- A low-level keyboard hook swallows **only** Alt+Tab/Win during
  hardcore focus locks — no keystroke logging of any kind.
- The installer is **unsigned** until our code-signing application
  completes, so SmartScreen shows a reputation warning on first run.
  That is expected, not a detection.

Every push to `main` runs a Defender threat-history check in CI
(`installer-smoke.yml`, step "Check Defender threat history"); the
24-hour Windows 10/11 run and the enterprise-AV pilot are MANUAL
steps documented in the same guide.

## Install and uninstall silently

The installer (`FocusCore-Setup-<version>.exe`, built with Inno Setup
6) is fully scriptable. It installs **per user, without admin
rights** into `%LOCALAPPDATA%\Programs\Focus Core`, puts the Focus
Core shortcut on the desktop, and bundles everything it needs
(Python included) — target machines need nothing pre-installed
except, on the rare machine that lacks it, the WebView2 runtime,
which Setup installs only when missing.

### Install

```
FocusCore-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="C:\Logs\FocusCore-install.log"
```

- `/VERYSILENT` — no wizard, ever. (Plain `/SILENT` shows a progress
  window; use it only for attended runs.)
- `/SUPPRESSMSGBOXES` — no dialog can pause the run. Problems go to
  the log and the exit code instead of the screen.
- `/NORESTART` — Setup never reboots the machine. If a file is ever
  locked, the log records it; Focus Core does not need a reboot to
  start working.
- `/LOG="<path>"` — a full Setup log at the path you name. Keep it;
  it is the first thing to check when an exit code is non-zero.

Two behaviors to plan around:

- **A silent install starts Focus Core once at the end.** That is
  deliberate: the one-click update flow reuses this installer, and
  the app must reopen after the update. On a fleet rollout the app
  simply appears for the signed-in user. If a rollout must not put
  anything on a user's screen, deploy during a maintenance window.
- **Running the same command again upgrades in place.** Files are
  replaced, user data is untouched, and the desktop shortcut stays.
  The exit code stays 0.

Exit codes (Setup's, inherited from Inno Setup):

| Code | Meaning |
|---|---|
| 0 | Installed (or upgraded) successfully. **This is the only success.** |
| 1 | Setup failed to initialize — e.g. a corrupted installer file. Verify the download before retrying. |
| 2 | Cancelled before the install began. With the silent flags above nothing asks the user, so treat this as an anomaly and read the log. |
| 3 | Fatal error while preparing the install. |
| 4 | Fatal error during the install itself (disk full and file-write failures land here). |
| 5 | Cancelled during the install, or Abort chosen at an error prompt. As with 2, it should not happen silently. |
| 7 | Setup determined it cannot proceed on this machine. |
| 8 | Cannot proceed, and a restart is needed first. |

Treat every non-zero code as "not installed" and keep the `/LOG=`
file with the deployment report (any code not listed above means the
same: not installed — read the log). **This exact command is what CI
runs** on every push (`.github/workflows/installer-smoke.yml`,
windows-latest runner): silent install, launch check, smoke tests,
then the silent uninstall below.

### Uninstall

```
"%LOCALAPPDATA%\Programs\Focus Core\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="C:\Logs\FocusCore-uninstall.log"
```

Same flags, same meaning; exit code 0 means the program is gone.
Uninstall removes the program files and shortcuts and
**deliberately leaves the user's data folder alone**
(`%LOCALAPPDATA%\Focus Core` — the database, backups, and
settings), so reinstalling later brings the person's history back.
That is a product decision, not an oversight; if enterprise
offboarding requires a complete wipe, delete the data folder after
the uninstall:

```
rd /s /q "%LOCALAPPDATA%\Focus Core"
```

## Removing Focus Core completely (offboarding)

The default uninstall above deliberately keeps the person's data.
When someone leaves and the machine must be wiped clean, Focus Core
can delete the data folder itself as part of the uninstall:

- **Interactive:** start the uninstaller without any silent flags
  (for example from Settings → Apps). The uninstall shows one extra
  option — **Also delete my Focus Core data (history, settings,
  backups)** — unticked by default. Tick it and the whole data
  folder (`%LOCALAPPDATA%\Focus Core`) is deleted after the program
  itself is removed.
- **Silent:** add `/DELETEDATA` to the documented uninstall
  command:

  ```
  "%LOCALAPPDATA%\Programs\Focus Core\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DELETEDATA
  ```

Honest notes: without `/DELETEDATA` the silent uninstall keeps the
data folder exactly as before — the switch is the only silent way
to wipe. Deleting the data folder by hand after a default uninstall
(the `rd /s /q` above) reaches the same result. A wipe that cannot
delete some file is written to the uninstall log (`/LOG=`) and
never makes the uninstall itself fail.

## Wrap for Intune (.intunewin)

Focus Core is a per-user installer, so it deploys as a standard
Win32 app in user context — no admin rights, no custom wrapper
scripts.

1. Put `FocusCore-Setup-<version>.exe` alone in a clean folder
   (for example `C:\Intune\FocusCore\`).
2. Wrap it with Microsoft's Win32 Content Prep Tool
   (`IntuneWinAppUtil.exe`, from Microsoft's
   `microsoft/Microsoft-Win32-Content-Prep-Tool` GitHub releases):

   ```
   IntuneWinAppUtil.exe -c C:\Intune\FocusCore -s FocusCore-Setup-<version>.exe -o C:\Intune\Output
   ```

   This produces `FocusCore-Setup-<version>.intunewin`.
3. Upload the `.intunewin` file as a new Win32 app and set:

   - **Install command:**
     `FocusCore-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="%TEMP%\FocusCore-install.log"`
   - **Uninstall command:**
     `"%LOCALAPPDATA%\Programs\Focus Core\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`
   - **Install behavior:** User. (The app installs into
     `%LOCALAPPDATA%` and keeps its data there; installing in System
     context would put it in the wrong profile.)
   - **Return codes:** 0 = Success. Anything else = Failed; the
     table above says what each code means and the log at
     `%TEMP%\FocusCore-install.log` says what happened.
   - **Detection rule:** Rule type **File** — Path
     `%LocalAppData%\Programs\Focus Core\python`, File or folder
     `pythonw.exe`, detection method **File or folder exists**.
     (Equivalently, the per-user uninstall key
     `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\{C7A3F2E1-8B4D-4F6A-9E2C-1D5A7B3F9E2C4}_is1`
     exists once Focus Core is installed; its `DisplayVersion`
     value carries the installed version.)

Honest status: this wrapping path is documented, not fleet-proven —
no Intune tenant has wrapped Focus Core yet, so treat the first
pilot group as the proof run. The installer itself is the same one
consumers download, and its silent mode is exercised by CI on every
push (above); the `.intunewin` layer is standard Intune packaging
around it.

## Offline installer (no network at install time)

The default installer is small (~25 MB) but needs the network for
one thing: if the target PC lacks the WebView2 runtime, Setup
downloads it on the spot via a tiny bootstrapper. On air-gapped
machines, locked-down VLANs, or PCs behind a proxy that blocks
Microsoft's download servers, that step fails and the app window
never opens.

The **offline flavor** fixes this by bundling the full WebView2
standalone installer inside Setup — about 200 MB extra, and the
install works with the cable unplugged. Everything else is the same
installer: same per-user install, same silent flags, same update
flow, same data-folder behavior.

- **Which file to ship:** `FocusCore-Setup-<version>-offline.exe`
  (the `-offline` suffix in the file name is the only difference
  that matters; the window says "Focus Core" either way).
- **Build it** (on a Windows machine with Inno Setup 6, from the
  repo root):

  ```
  python installer\build.py --version <version> --webview2-offline
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss /DAppVersion="<version>" /DWebView2Offline
  ```

- **Provenance:** the bundled runtime comes from Microsoft's
  official "Evergreen Standalone Installer" link
  (`go.microsoft.com/fwlink/?linkid=2124701`, x64). `build.py`
  verifies it against a pinned SHA-256 and refuses to build on any
  mismatch — exactly the fail-closed rule the Python and
  bootstrapper downloads follow.
- **When WebView2 installs:** only when missing. Setup checks the
  WebView2 registration in both HKLM and HKCU (a per-user install
  leaves no HKLM trace), so repeat silent installs and one-click
  updates never reinstall it pointlessly.
- **x64 only,** matching the embedded Python the installer ships.

Honest status: CI builds and compiles the offline flavor on every
push (the `offline-installer` job in `installer-smoke.yml` asserts
the standalone installer is staged and the artifact is named
`-offline`); installing it on a real offline PC is a manual proof —
the Windows PC gate in the release checklist covers it.

## Pin the fleet: disable self-updates (machine policy)

> "IT can pin the fleet to version X and the app's self-updater will
> never fight SCCM/Intune."

### Set the policy

Create this registry value on each managed machine (or push it with
Group Policy, SCCM, or Intune):

- Key: `HKEY_LOCAL_MACHINE\Software\Focus Core\Features`
- Value: `updates_disabled` (DWORD) = `1`

For evaluation on a single machine, the same switch exists as
`updates_disabled = true` in the `[features]` table of `config.toml`,
or as the `FOCUSCORE_FEATURE_UPDATES_DISABLED=1` environment variable.
On managed machines the registry value is the one that counts: the
machine tier beats the user's config file and the in-app automatic-
checks toggle, so a user cannot turn updates back on.

The policy is read fresh at every update check — set it before or
after install, no reinstall needed.

### What the app does while the policy is on

- The daily background update check never happens.
- The Updates page shows **"Updates are disabled by your IT
  policy"** and offers the user no way to check or update.
- The manual "Check again" action is refused the same way — it goes
  through the same code path as the automatic check.
- Starting an update (the "Update now" flow) is refused before
  anything is downloaded or staged.
- "Revert to previous version" (the rollback the Updates page offers
  after an update) is refused too — while the pin is on, the app does
  not change versions in either direction.
- An update that was downloaded and queued before the pin is held,
  not applied. If the policy is later removed, normal behavior
  resumes, including that queued update.
- Nothing about the rest of the app changes. Focus Core keeps
  working locally exactly as before; the policy touches only
  self-updates.

### Acceptance procedure (run by IT)

1. Deploy the chosen version with the policy set as above.
2. Confirm the pin took effect: open the Updates page on one
   machine — it shows "Updates are disabled by your IT policy."
3. Run the fleet normally for 7 days.
4. Review the firewall or proxy log for those machines.
5. **Pass:** zero update-check traffic from Focus Core in the log.
   **Fail:** any traffic to the update endpoints — treat it as a
   defect and report it.

For reference, Focus Core's only update endpoints are the GitHub
Releases API (`https://api.github.com/repos/<repo>/releases/latest`,
user agent `FocusCore-Updater`) and the release-asset download used
when a user clicks "Update now". Under the policy, neither is
contacted.

### Honest limits of this proof

The 7-day firewall log is evidence produced at your site; no test
running on our side can generate it for you. What ships with the app
is the code-level proof underneath it: the acceptance test suite
(`tests/test_update_kill_switch_121.py`) drives every self-update
path with the policy on and asserts the app never reaches a network
function, and a positive control with the policy off proves the test
would catch real traffic. Those tests exercise the registry tier
through a test double — the live HKLM read is the same code path the
app itself runs, and step 2 above confirms it on a real machine.

## If this project is abandoned (continuity)

Focus Core is local-first: **there is no server, no account system,
no license check, and no phone-home.** (The only server involved is
the dashboard the app runs on the user's own machine at
127.0.0.1; optional features that use the internet — the update
check you can pin off above, and backups a user points at their own
Google Drive — are exactly that: optional, and neither is a license
or activation gate.)

So an installed copy keeps working indefinitely even if this project
disappears tomorrow. Everything it needs already sits on the machine,
and the data is one SQLite file
(`%LOCALAPPDATA%\Focus Core\focuscore.db`) whose format is
documented in `FORMAT.md` at the top of this repository. The public
repository is MIT-licensed (`LICENSE`), and it contains everything
needed to keep shipping without the maintainer: run the app from
source (README, "For developers") or rebuild the installer exactly
as CI does (`installer/README.md`). IT keeps every option either
way: pin the fleet to the last good version (above), uninstall with
the standard command (above), or keep running as-is — none of those
needs the author's permission or any service staying online.
