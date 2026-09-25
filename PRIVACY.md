# Privacy — Focus Core

Plain-language privacy statement. Last updated 2026-09-25.

## The short version
**All of your data stays on your own computer.** Focus Core has no accounts, no
analytics, no telemetry, and makes no internet calls. Nobody — not us, not anyone
else — can see your activity data. (The app does talk to itself inside your own
PC: the dashboard is served from your own computer, and the tracker reads the
activity-watcher through a connection that never leaves your PC. Nothing ever
goes to the internet.)

## What lives where
- Your tracked activity, scores, goals, timesheets, and settings live in one file,
  `focuscore.db`, in your Focus Core folder on your PC. Only you (and anyone with
  access to your PC) can read it.
- The dashboard runs on your own computer at `http://127.0.0.1:5000`. It is not
  reachable from the internet — it only listens on your PC itself.

## Google Drive backup (optional)
- If you turn on Drive backups, Focus Core copies the `focuscore.db` file into a
  "Focus Core Backups" folder **in your own Google Drive**. That is a plain file
  copy — the same as you dragging the file there yourself.
- It is your Drive, your account, your folder. We have no access to it and never
  request access. Turning it off stops the copies immediately; nothing is sent
  anywhere else, ever.

## What Focus Core never does
- No user accounts, no sign-in, no passwords stored anywhere.
- No analytics, no telemetry, no crash reporting, no usage statistics.
- No internet calls: it does not phone home, check for updates over the network,
  or contact any outside server. (The version notice planned for a later release will only
  compare against a version file you download yourself — still no telemetry.)

## Optional diagnostics bundle (planned for a later release)
A future update will add an option to generate a diagnostics bundle from the
dashboard if something breaks and you ask for help. It will contain **only**:
- The app version and Windows version.
- Recent error messages from the app's log (no activity titles, no URLs, no scores).
- Backup status (when the last backup ran, whether it succeeded).

It will never contain your tracked activities, window titles, website URLs, scores,
goals, or timesheet entries. You will see exactly what is in the bundle before you send it.
