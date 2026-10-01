# Data stewardship — Focus Core

This page is the project's promise sheet about your data: what Focus Core
collects, who can see it, how long it is kept, and how you export or delete
it. It is written in plain words, not legal language. Every promise below
names the mechanism that keeps it; where a mechanism does not exist yet,
this page says so instead of hiding it. `PRIVACY.md` has the same facts in
user-facing form, and `docs/claims.md` holds the register that ties each
promise to its test or manual check.

## The promises

- **Your data is processed on your own computer only.** The developer
  never receives, aggregates, or sells user data. Focus Core has no
  accounts, no analytics, no telemetry, no crash reporting, and no ads.
  Nothing is sold or shared. (PRIVACY.md, "What Focus Core never does";
  registered in docs/claims.md with its tests.)
- **The complete list of internet uses is three** — the same three
  PRIVACY.md names: a once-a-day update check (it sends your IP address
  and the label `FocusCore-Updater`, never your activity data, and you can
  turn the automatic check off on the Updates page), an installer download
  only when you click "Update now" (SHA-256 checked before it runs), and
  an optional calendar (ICS) feed that is off by default and only fetches
  a link you paste in. The evidence is the transmission inventory in
  tests/test_privacy_update_toggle.py. The one other copy of your data is
  the backup described below — a file copy into your own Google Drive
  folder on this PC, not an internet call by the app.
- **You can take your data out and you can delete it.** How, per data
  type, is in the table below.

## Your data, type by type

| Data | What it is | Who can see it | Kept until | Export | Delete |
|------|------------|----------------|------------|--------|--------|
| Activity | Which apps and windows you used, with times. Window titles and website addresses are stored encrypted with Windows DPAPI inside `focuscore.db`, in the Focus Core data folder (installed copies: the `Focus Core` folder in `%LOCALAPPDATA%`; portable copies: next to the app). | Only this PC. | Until you delete it. | Through the timesheets and reports you generate. | Goals, alerts, shield rules and timesheet rows can be deleted in the app; individual tracked-activity records cannot — they go only with the whole database; see the gap below. |
| Focus & shield | Focus sessions, shield rules and passes, session history. Same file as above. | Only this PC. | Until you delete it. | Included in backups. | Shield rules can be deleted in the app; past sessions are history and are removed only with the whole database — see the gap below. |
| Scores & reports (derived analytics) | Scores, Pulse, reports and other numbers Focus Core works out from your activity. Computed on this PC and stored in the same file. | Only this PC. | Until you delete it. | Through the timesheets and reports you generate and save where you choose. | With the database; see the gap below. |
| Timesheets & exports | The timesheets and export files you generate. | Whoever you give the files to. | As long as you keep the files. | They are the export: you save them where you choose. | Delete the files. |
| Backups | A copy of the whole `focuscore.db`. Made on the Backup page, at startup when the last one is older than 24 hours, and before updates and database upgrades. Stored in the data folder's `backups` folder, or — automatically whenever Google Drive for Desktop is installed — in "Focus Core Backups" in your own Drive folder. Optional passphrase encryption is available on the Backup page and is off by default. | This PC, and your own Google account for the Drive copies. We have no access to them. | Until you delete them. | A backup is itself a copy you can restore from the Backup page on any PC (type the passphrase if it is encrypted). | Delete the backup files. |
| Diagnostics bundle | A zip you generate only when you click "Export diagnostics": Focus Core and Windows versions, a database health-check result, settings with secrets hidden, names and sizes of files in the data folder (never their contents), the last 200 log lines with user names and home paths replaced, and the app's own memory self-report. | Whoever you send it to — you choose. | The file is yours once exported. | It is the export; a README inside the zip lists exactly what is inside. | Delete the file. It never contains the database, tracked activities, window titles, URLs, scores, goals, or timesheet entries (tests/test_diagnostics_export.py). |
| Wearables & health (heart rate, sleep, etc.) | **Not collected today.** Nothing in the app reads a wearable or health source. A future roadmap item (3.1.1) may add it; before anything is collected, this page will name the data type, who sees it, retention, and export/delete here first. | — | — | — | — |

## Honest gaps, named

- **There is no one-click "delete everything" button in the app today.**
  To erase everything: uninstall Focus Core, then delete the data folder
  (installed copies: the `Focus Core` folder in `%LOCALAPPDATA%`;
  portable copies: the app folder itself). Uninstalling deliberately
  leaves the data folder behind so history is never deleted by accident —
  see `[UninstallDelete]` in `installer/installer.iss`. Per-item deletes
  (goals, alerts, shield rules, timesheet entries and projects) do exist
  in the app.
- **Wearables are future, not present.** Until a version ships them and
  this page names them, Focus Core collects no health or wearable data.

If a promise above ever stops being true, the fix is to change this page
and `PRIVACY.md` in the same commit as the code — the claims register in
`docs/claims.md` exists so the words and the evidence cannot quietly
drift apart.
