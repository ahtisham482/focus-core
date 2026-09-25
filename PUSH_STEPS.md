# Publishing focus-core to GitHub

Do this once, on Ahtisham's Windows PC, with him present (one step needs
him to click in the browser). Keep every step small and separate --
AntiGravity runs them one at a time.

The folder already has git history (several commits). These steps only
connect it to GitHub and push.

## Step 1 — Create the empty repo (Ahtisham, in the browser)

1. Open https://github.com and sign in.
2. Click **+** (top-right) → **New repository**.
3. Name: `focus-core`. Description: "Local-first RescueTime-style
   productivity tracker (Python)". Visibility: **Private**.
4. Do NOT tick "Add a README". Click **Create repository**.
5. Copy the repo URL, e.g. `https://github.com/AHTISHAM/focus-core.git`.

## Step 2 — On the PC: log in to GitHub (one interactive step)

Run in the focus-core folder:

```
gh auth login
```

Choose: GitHub.com → HTTPS → Yes → **Login with a web browser**.
Ahtisham opens the shown code URL himself and clicks Authorize.
(If `gh` is missing: `winget install GitHub.cli`, then retry.)

## Step 3 — Connect and push (small benign steps, one at a time)

```
cd /d C:\Users\DELLL\Documents\focus-core
git remote add origin https://github.com/AHTISHAM/focus-core.git
git branch -M main
git push -u origin main
```

Replace `AHTISHAM` with his real GitHub username from Step 1.

## Step 4 — Make a Release (replaces the expiring zip links)

1. Zip the folder **without** `focuscore.db`, `*.bak*`, `backups/`,
   `.onboarded`, `__pycache__` (the `.gitignore` already excludes them
   from git, but zips are manual -- double-check).
2. On the repo page: **Releases** → **Draft a new release** →
   tag `v1.0.0`, title "Focus Core v1.0.0".
3. Upload the zip as a release asset. Publish.

Future updates: same flow with `v1.1.0`, etc. The README's "Update"
section already tells the user to download from Releases.

## Safety notes

- The repo is **private** -- the code is generic, but keep it that way.
- Never commit `focuscore.db`, `*.bak*`, `backups/`, or `.onboarded`:
  they hold personal activity history. `.gitignore` covers all of them.
- No secrets exist in this project (no API keys, no passwords).
