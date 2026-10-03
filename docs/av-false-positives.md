# Focus Core — antivirus / endpoint false-positive guide

_For IT admins evaluating Focus Core for a fleet. Plain English on purpose._

## What this document is

Focus Core watches which app is on screen, and its focus-session shield
can hide or minimize distracting windows. Programs that watch windows and
touch other programs look suspicious to antivirus software — even when
they are harmless. This document lists **everything** Focus Core does
that an antivirus might flag, so you can pre-allow it and so any
detection can be filed as a false positive with facts, not guesses.

Status: **no antivirus detection has been recorded against Focus Core
as of this build.** If that changes, detections will be listed here
with dates.

## What Focus Core does that AVs notice

### 1. It watches the active window (all the time)

- Reads the foreground window title and process name every few seconds
  (`focuscore/win32.py`: `GetForegroundWindow`, `GetWindowThreadProcessId`,
  `OpenProcess`, `QueryFullProcessImageNameW`, lines 291–317).
- This is how it knows what you are working on. It is the core feature,
  not a side effect.

### 2. It minimizes distracting windows during focus sessions

- When a focus session is active and you open a blocked app, the shield
  **minimizes that window** (`ShowWindow`, `win32.py:352`) and shows a
  full-screen "back to work" reminder.
- **It never terminates, kills, or force-closes other programs.**
  The only processes Focus Core ever terminates are its own dashboard
  server child, and only when that child fails to start or when the
  user stops it (`focuscore/launcher.py:279,303`,
  `focuscore/tray.py:154,158`).
- Older descriptions of this project said "terminates processes via
  ctypes" — that is outdated and wrong for the current code.

### 3. It can swallow Alt+Tab and the Windows key (briefly, opt-in)

- During a "hardcore" focus lock, it installs a low-level keyboard hook
  (`SetWindowsHookExW` with `WH_KEYBOARD_LL`, `win32.py:552-594`) that
  swallows **only** Alt+Tab and the Win key, for the lock duration.
- **It never records keystrokes.** Nothing typed is logged, stored, or
  sent anywhere. The hook callback only checks two keys and passes
  everything else through untouched.

### 4. It runs a local web server

- The dashboard is a web page served from **127.0.0.1 only**
  (`focuscore/launcher.py:46`). It never binds to the network, so other
  computers cannot reach it.
- Incoming requests must come from the loopback address or they are
  refused (`dashboard/app.py:432`).
- Windows Firewall may ask about it on first run. The safe answer:
  **no network access needed** — it only talks to itself.

### 5. What it sends over the internet (short list)

| What | Where | When |
|---|---|---|
| Update check | `api.github.com` (Focus Core releases) | On launch / Updates page, can be turned off |
| Crash report | Opens **your own** mail app (mailto link) | Only if you click send after a crash |
| Backups | A local folder only; Google Drive for Desktop syncs it if you installed that | On schedule |

No analytics, no tracking, no telemetry leaves the PC except the three
rows above.

### 6. What the installer does

- **Per-user flavor:** installs to `%LOCALAPPDATA%\Programs\Focus Core`,
  writes only to HKCU, optional startup entry in the user's Startup
  folder (`installer/installer.iss:110`).
- **Per-machine flavor:** installs to `C:\Program Files\Focus Core`,
  writes the uninstall key to HKLM, optional startup entry in the
  all-users Startup folder (`installer/installer.iss:108`). Needs admin
  because it writes to Program Files and HKLM — that is normal.
- **SmartScreen warning is expected:** the installer is not
  code-signed yet (signing waits on our SignPath application), so
  Windows SmartScreen shows a reputation warning on first run. This is
  not a detection — it is the "unknown publisher" prompt every unsigned
  installer gets.

## The shield's process-target list (for pre-allow)

The shield acts on other programs **only** in these two ways, and
**only** for programs the user put on their own block list:

1. **Minimize** the window of a blocked app during an active focus
   session (user's block rules decide what is blocked).
2. **Show** the fullscreen reminder overlay.

It never touches a program the user did not list. There is no
autonomous targeting, no list of "bad" programs shipped with the app,
no behavior that reaches outside the user's own rules.

## If your AV flags Focus Core anyway

1. **Check what was flagged against the list above.** If it matches a
   documented behavior, it is a false positive.
2. **File with Microsoft first** (Defender/SmartScreen):
   https://www.microsoft.com/en-us/wdsi/filesubmission — submit the
   installer exe, choose "false positive", describe the flagged
   behavior in your own words using this document.
3. **Then other vendors** with the same facts (each has its own
   submission portal; all accept the same behavior description).
4. **Attach:** this document, the installer version number, and —
   once signing lands — the signed binary.

## Manual verification still to do (not faked)

These need real PCs and are tracked as MANUAL, not proven by CI:

- **24-hour run on stock Windows 10 and 11** with default Defender:
  install, run a full day with focus sessions active, record every
  SmartScreen prompt, firewall prompt, and quarantine event. (CI
  runners only offer Windows 11 and cap at ~6 hours, so CI cannot do
  this.) Record the results as a dated note under `docs/qa/`
  (e.g. `docs/qa/av-pilot-YYYY-MM-DD.md`).
- **Enterprise AV trial** (e.g. CrowdStrike / SentinelOne style):
  needs kernel drivers and reboots — impossible on hosted CI runners.
  Run as a pilot step with the same recording checklist before the
  fleet rollout, and file the dated note under `docs/qa/` as above.

## What CI proves (TESTED — from this build onward)

From this build onward, every push to `main` runs the installer smoke job on a real Windows
runner: it installs the app, launches it, exercises the dashboard,
restores a backup — and then queries Defender's threat history
(`Get-MpThreatDetection`) scoped to Focus Core paths. The job fails if
Defender logged any detection. That check is the automated part of
this gate; the manual runs above are the rest.
