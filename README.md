# Focus Core

**Know exactly where your computer time goes.** Focus Core watches which
apps and websites you use, scores them from -2 (very distracting) to +2
(very productive), and shows your day as one simple number: the
**Productivity Pulse** (0-100). Green (60+) is a good day, amber (40-59)
is okay, red (below 40) is a distracted day.

It also helps you fix it: daily goals with live progress, pop-up alerts
when you overdo a distraction, timed focus sessions that block
distractions, timesheets with CSV export, a weekly report, and coaching
that shows *when* you focus best.

## What it costs

Nothing. Focus Core is free, and so is
[ActivityWatch](https://activitywatch.net) (the open-source tracker it
reads from). No accounts, no subscriptions, no paid tiers.

## Privacy

Everything runs on your own computer. Your activity data never leaves
your PC -- there is no cloud account and nobody can see it but you.
Backups can optionally sync through *your own* Google Drive, so your
history survives a lost or replaced laptop. Read `PRIVACY.md` for the
full details.

## Install (Windows, 3 steps)

1. **Install Focus Core**: download the newest `FocusCore-Setup-<version>.exe`
   from the [Releases page](https://github.com/ahtisham482/focus-core/releases)
   and run it. No admin rights needed. It puts a
   **Focus Core** icon on your desktop.
2. **Open Focus Core** from the desktop icon and take the 3-screen
   welcome tour.
3. **Set up ActivityWatch**: the app will walk you through it --
   download it, run its installer, start it from the Start menu. Then
   press **Check again**. That's the tracker; without it running there
   is nothing to score.

From then on: double-click the **Focus Core** desktop icon whenever you
want to see your score. A small icon near the Windows clock gives quick
actions: open Focus Core, start a 25-minute focus session, see today's
Pulse, back up now, quit.

## Daily use (3 minutes)

**Morning -- glance at Home.** The home page tells you what needs you,
each item with exactly one button: review yesterday's uncategorized
activities (one click each), accept yesterday's timesheet blocks, start
a focus session.

**During work -- focus sessions.** Open the **Focus** page, type what
you're working on, pick 25/50/90 minutes, start. Distracting apps get a
pop-up and a "back to work" reminder. Ending the session shows your
summary and builds your streak.

**Evening -- timesheet (2 minutes).** Open the **Timesheet** page, accept
the suggested blocks on the visual timeline, tag them with project and
task, then **Lock day** when it looks right.

**Stuck on any page?** Click **Help** in the footer -- every page has
its own short guide. The full manual is `USER_GUIDE.md`.

## Code signing policy

Focus Core releases are built in public, by this repository's GitHub
Actions workflows, and attached to GitHub Releases. Free code signing
provided by [SignPath.io](https://signpath.io), certificate by
[SignPath Foundation](https://signpath.org). Focus Core is applying to
SignPath Foundation; until it is approved, published builds are
unsigned.

Only installer builds produced by these workflows from tagged releases
of this repository will ever be submitted for signing -- locally built
binaries are never signed. Signed installers will show **SignPath
Foundation** as the publisher.

- Committers and reviewers: the repository owner, `ahtisham482`.
- Signing approver: the repository owner, `ahtisham482`. Every signing
  request is approved manually before release.
- Privacy policy: [PRIVACY.md](PRIVACY.md)

## Updates

New versions install themselves: the app checks once a day, and the
**Updates** page (or the tray icon) lets you check any time. One click
backs up your data, installs the new version silently, and reopens the
app. It never updates in the middle of a focus session.

## FAQ

**Is it really free?**
Yes. No accounts, no subscriptions, no paid tiers. ActivityWatch (the
tracker) is free and open source too.

**Does it send my data anywhere?**
No. Everything stays on your PC. The only thing that ever leaves is a
backup copy, and only if you installed Google Drive for Desktop --
then it goes to *your own* Drive.

**My dashboard is empty. Why?**
Almost always: ActivityWatch isn't running. Look near the Windows clock
for its icon; if it's missing, open ActivityWatch from the Start menu.
Home will also tell you this directly, with a button to the setup page.

**Do I need the internet?**
No. Focus Core works fully offline. You only need internet to download
it (and ActivityWatch) the first time, and to check for updates.

**Will it slow down my computer?**
No. The tracker is tiny, and Focus Core itself only works when you open
it (plus a small tray icon).

**What is ActivityWatch and why do I need it?**
It's a free, open-source time tracker. Focus Core doesn't watch your
screen itself -- it reads ActivityWatch's local data (which app, which
website, for how long) and turns it into scores. Idle/away time is
excluded automatically.

**An app I need keeps getting blocked during focus sessions.**
Open the **Review** page, find the app, and score it as productive.
Your score always wins -- it will never be blocked again.

**Notifications aren't appearing.**
Windows Settings -> System -> Notifications: make sure notifications are
turned on. (Also check Windows isn't in Do Not Disturb mode -- it hides
all pop-ups.)

**I closed the window -- is it still running?**
Closing the window hides it to the tray (the small icon near the
clock); the server keeps running. Right-click the tray icon and choose
**Quit** to close everything.

**How do I move to a new laptop?**
Install Focus Core on the new laptop, copy your newest backup file
over, and restore it from the **Backup** page. Two steps.

**How do I uninstall?**
Windows Settings -> Apps -> Focus Core -> Uninstall. Your data
(`focuscore.db`) is kept, so reinstalling later brings your history
back.

## For developers

Needs Python 3.11 or newer (the source uses the standard-library
`tomllib`; the installer build pins Python 3.12). Set up a virtual
environment first — installing into a system Python fails on many
machines:

```
python -m venv .venv        # use python3 if `python` is not on your PATH (common on Linux/macOS)
.venv\Scripts\activate      # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m dashboard.app     # http://127.0.0.1:5000
python -m pytest            # full test suite
```

To build the Windows installer itself, see `installer/README.md`
(an offline flavor with the full WebView2 runtime is one flag away:
`python installer/build.py --version <v> --webview2-offline`).

How it works in one paragraph: `focuscore/ingest.py` reads ActivityWatch's
local API (window/web/AFK buckets); `taxonomy.py` + `seed_data.py` map
~100 apps/sites to categories; `scoring.py` applies the exact RescueTime
mechanics (+2/-2, category inheritance, per-activity overrides) and the
weighted Pulse formula `((vd*0 + d*1 + n*2 + p*3 + vp*4) / (total*4)) * 100`;
`store.py` keeps it all in SQLite. The dashboard is Flask on
`127.0.0.1:5000` with one shared stylesheet, no JavaScript required.
`focuscore/help.py` holds the in-app help articles (13 pages, pure HTML
builders); `focuscore/onboarding.py` the welcome tour and ActivityWatch
setup page; `focuscore/updater.py` the one-click update check.

Key docs: `USER_GUIDE.md` (user manual), `docs/support/troubleshooting.md`
(symptom -> cause -> fix), `docs/security/` (threat model), `PRIVACY.md`,
`installer/README.md` (build the Windows installer),
`docs/enterprise-deploy.md` (silent install, Intune, fleet policy),
`CHANGELOG.md`, `ROADMAP.md`.
