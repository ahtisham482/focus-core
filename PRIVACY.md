# Privacy — Focus Core

Plain-language privacy statement. Last updated 2026-10-01.

## The short version
**All of your data stays on your own computer.** Focus Core has no accounts, no
analytics, and no telemetry. Nobody — not us, not anyone else — can see your
activity data. The app does talk to itself inside your own PC: the dashboard is
served from your own computer, and the tracker reads the activity-watcher
through a connection that never leaves your PC.

Besides the Google Drive backup described below (a plain file copy into your
own Drive folder, made automatically whenever Drive is installed), the only
time Focus Core touches the internet is the three things listed under
**Internet use** below. Each one is described there in plain words, and you can
turn off the automatic update check on the Updates page.

## What lives where
- Your tracked activity, scores, goals, timesheets, and settings live in one file,
  `focuscore.db`, in your Focus Core folder on your PC. Window titles and website
  addresses — the most revealing text in there — are stored encrypted with
  Windows DPAPI, tied to your Windows user account: another Windows user, or a
  copy of the file taken to another PC, cannot read them. App names, times and
  scores stay readable; this is a guard on the sensitive text, not full-database
  encryption.
- The dashboard runs on your own computer at `http://127.0.0.1:5000`. It is not
  reachable from the internet — it only listens on your PC itself.

## Internet use (only what you allow)
Focus Core contacts the internet only in these three ways:

1. **Update check (automatic, once a day).** Installed copies of Focus Core
   ask `api.github.com` once a day whether a newer version exists. The request
   sends your IP address (that's how the internet works — every website sees
   it) and a "User-Agent" label naming Focus Core (`FocusCore-Updater`).
   Nothing else is sent. The answer is saved for 24 hours, so the check doesn't
   run more often. **You can turn off this automatic check** on the Updates
   page — the manual "Check again" button keeps working.
2. **Installer download (only when you click "Update now").** When you start
   an update, the new installer downloads from the GitHub releases page. One
   file, only when you ask for it. Before anything runs, Focus Core checks
   the file's SHA-256 checksum against the record published with the release,
   so a corrupted download is refused instead of installed.
3. **Calendar feed (optional, off by default).** If you paste a calendar
   (ICS) link into Focus Core, it fetches that feed so your schedule can show
   on the Home page. This only happens if you set it up, and clearing the
   link stops it.

That is the whole list of internet calls. None of these ever carry your
activity data: no window titles, no website URLs, no scores, no goals, no
timesheet entries. Your activity data leaves the PC one other way — the
Google Drive backup below — but that is Focus Core copying a file into a
folder on this PC, not an internet call by the app.

## Google Drive backup (automatic when Drive is installed)
Focus Core never talks to Google directly. It copies your database file into
a folder on this PC, and Google Drive for Desktop syncs that folder to your
Google account.

- **When it happens.** If Google Drive for Desktop is installed on this PC,
  backups are copied into a "Focus Core Backups" folder in your Drive
  **automatically** — there is no in-app on/off switch. Every backup lands
  there: the ones you make from the Backup page or the tray menu, the
  automatic backup at startup when the last one is older than 24 hours,
  the pre-update safety backup made before an update runs, and the safety
  backup made before a database upgrade.
- **What is copied.** Your whole `focuscore.db` — tracked activity, scores,
  goals, timesheets, and settings, all in one file. The encryption described
  above travels with it: window titles and website addresses in the copy stay
  readable only under your Windows user. Restoring the file on a *different*
  Windows user or PC still brings back everything else (times, scores, goals,
  timesheets); titles and addresses then show as unreadable. By default the
  backup copy itself is not separately password-protected — see the next
  bullet for the optional protection.
- **Optional backup encryption (off by default).** On the Backup page you
  can turn on backup encryption with a passphrase you choose. New backups
  are then encrypted with that passphrase before they are copied, so the
  Drive copy is unreadable without it, and a backup can be restored on a
  new PC by typing the passphrase on the Backup page. This only protects
  new backups; older backups stay as they are. If you forget the
  passphrase, encrypted backups cannot be recovered — there is no
  recovery, not even by us. To let automatic backups keep running on
  this PC, Focus Core keeps a check-value (stored, by design, in the
  settings table inside the database) plus a copy of the passphrase
  protected by your Windows user account on this PC only; the
  passphrase copy is never put inside the database or a backup. The
  small safety copy Focus Core makes on this PC before a database
  upgrade stays on this PC and is not passphrase-encrypted.
- **Your account only.** It is your Drive, your account, your folder. We have
  no access to it and never request access.
- **Turning it off.** Because the copies are automatic whenever Drive is
  installed, there is no switch in the app: quit Google Drive for Desktop and
  the copies stop. Deleting the "Focus Core Backups" folder alone is not
  enough — the next backup will create it again while Drive is running.

## What Focus Core never does
- No user accounts and no sign-in. If you turn on backup encryption, the
  only passphrase material kept is described above — a check-value and a
  Windows-protected copy on this PC — and it is never sent anywhere.
- No analytics, no telemetry, no crash reporting, no usage statistics.
- No ads, no tracking, nothing sold or shared. The three internet uses
  above, plus the optional Drive backup described in its own section, are
  the complete list.

## Optional diagnostics bundle (only when you export it)
From the Backup page you can export a diagnostics bundle if something breaks
and you ask for help. It contains **only**:
- Which version of Focus Core you run, which version of Windows, and a
  database health-check result.
- Your settings, with secrets (passwords, tokens, calendar links) hidden.
- The names and sizes of the files in your data folder — never their contents.
- Recent entries from the app's log (the last 200 lines), with your
  user name and home-folder paths replaced by placeholders — no activity
  titles, no URLs, no scores.
- A short self-report of the app's own memory use.

It never contains the database itself, your tracked activities, window titles,
website URLs, scores, goals, or timesheet entries. A README inside the bundle
lists exactly what is in it, so you can see what you would be sending before
you send it.
