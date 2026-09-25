# UX/UI Sign-off — Sprint 1 (Track A + stability)

- **Scope:** Sprint 1 code changes: `focuscore/backup.py` (atomic writes + SHA256 sidecar
  checksums) and `dashboard/app.py` (Host/Origin loopback-CSRF validation + secure response
  headers). Docs only: backlog, risk register, roadmap, changelog, PRIVACY.md, threat model,
  troubleshooting, pc-verify checklist.
- **Reviewer:** Agent 8 (UX/UI designer)
- **Date:** 2026-09-25
- **Rule compliance:** no code or tests were modified; this file is the only artifact created.

## Area 1 — Restore error message (checksum mismatch)

**Verdict: PASS WITH COPY PROPOSAL**

How the message reaches the user (verified by reading `dashboard/app.py` lines 1421–1441 and
`focuscore/backup.py` lines 143–168): `backup_restore()` catches `(ValueError,
FileNotFoundError)` and renders `<b>Could not restore:</b> %s`, where `%s` is the raw exception
string, HTML-escaped. The surrounding flow is unchanged from Sprint 0 (backup page layout,
confirm dialog on the Restore button, safety-copy note on success — all identical).

The new mismatch message raised by `_verify_checksum`:

> `Backup focuscore-20260925-120000.db failed its integrity check (SHA256 mismatch): the file may be corrupt or tampered with; restore refused.`

Assessment: the structure ("what happened + why it matters + what to do next") is right, and
"may be corrupt or tampered with" plus "restore refused" are understandable. But
**"integrity check (SHA256 mismatch)" is cryptic jargon** for the stated user (non-technical,
non-English speaker) — it names an algorithm he has never heard of. It also gives **no next
step** (pick a different backup from the list).

Exact proposed replacement (drop-in for the `ValueError` text in `_verify_checksum`):

> `This backup failed its safety check: it looks damaged or was changed after it was made, so restoring it was stopped to protect your data. Please pick a different backup from the list.`

Shown in the existing UI, the user would read:

> **Could not restore:** This backup failed its safety check: it looks damaged or was changed
> after it was made, so restoring it was stopped to protect your data. Please pick a different
> backup from the list.

**Also noted (not a failure):** backups made before checksums existed restore with only a
server-side log/`warnings.warn` — the user sees a plain success card with no hint that the
integrity could not be verified. Proposed copy for that case, to show on the restore-success
card when a legacy backup is restored:

> Note: this backup was made before safety checks were added, so it could not be verified.
> Your data was restored normally.

## Area 2 — First-run trust message / onboarding

**Verdict: PASS WITH COPY PROPOSAL**

Read: `WELCOME_STEPS` (3 steps) and the `/welcome` route in `dashboard/app.py` (lines 400–479),
plus all attention cards in `focuscore/home.py` (lines 56–163).

The tour covers *what the app does* well: ActivityWatch check on the taskbar, the −2..+2 scoring
scale, and teaching it uncategorized activities. The attention cards are also strong UX: one
clear title + detail + single action button each ("They counted as Neutral in your Pulse. Teach
Focus Core once and it remembers."). However, the plain-language trust questions the ticket
asks about are **not answered anywhere in the app**:

1. **What data stays local.** The dashboard never says "your data stays on this PC." The new
   PRIVACY.md states it clearly, but PRIVACY.md is a repo doc — **nothing in the app links to
   it or shows it.**
2. **What ActivityWatch is and why it is needed.** The tour calls it "The tracker
   (ActivityWatch)" — a name, not an explanation. A first-time user does not learn it is a
   separate, open-source app that records which app/website you were using, and that Focus Core
   does no tracking of its own.
3. **How to export/delete data.** There is no user-facing answer. (Facts: export exists only
   as a per-day timesheet CSV; full backup = copying `focuscore.db`; there is no in-app
   "delete all my data" — deleting means removing the db file.)

Proposed copy additions:

- **New welcome step (recommended) or add to step 1:**

  > Title: "Your data stays on this computer."
  >
  > Text: "Focus Core has no account, no sign-in, and sends nothing to the internet — there is
  > no server to see your data. Everything lives in one file (focuscore.db) on this PC. If you
  > turn on Drive backups, a copy of that file is placed in *your own* Google Drive folder and
  > nowhere else."

- **Revise welcome step 1 to explain ActivityWatch (replace its current text):**

  > Text: "Focus Core does not watch anything itself. It reads from **ActivityWatch**, a free,
  > open-source tracker that records which app or website you were using. That is why
  > ActivityWatch must be running — without it, there is nothing for Focus Core to score."

- **Add a short privacy/export card to the Backup page (or a "Your data" link in the footer),
  using copy along these lines:**

  > "Your data: everything lives on this PC in focuscore.db. Export: any timesheet day can be
  > saved as CSV from the Timesheet page; a full copy is any backup from this page. Delete: to
  > remove all your data, delete focuscore.db (make a backup first)."

## Area 3 — Dark patterns, color-only communication, visual/flow changes

**Verdict: PASS** — no heuristic re-evaluation needed for Sprint 1.

Evidence (verified, not assumed):

- `git diff --stat` (working tree) shows exactly two modified files: `dashboard/app.py` (+77)
  and `focuscore/backup.py`. `git status` confirms all tests and docs are untracked new files.
- The `dashboard/app.py` diff is **100% security hooks**: `_LOOPBACK_HOSTS`/`_MUTATING_METHODS`
  constants, `_host_is_loopback`/`_origin_is_loopback` helpers, one `@app.before_request`
  CSRF rejection, one `@app.after_request` security-headers setter. Zero changes to any route
  body, template string, CSS, label, or button.
- `focuscore/backup.py` is backend-only: atomic `os.replace` writes, SHA256 sidecar helpers,
  and the two changed/added error messages. No template or rendering code lives there.
- The backup page rendering ("Could not restore" card, restore confirm dialog, "Moving to a
  new laptop" instructions) is byte-identical to the Sprint 0 version — confirmed by comparing
  the current code against `git show HEAD:dashboard/app.py`.
- Welcome tour templates and home attention cards are untouched by Sprint 1 (no diff in those
  sections; only the security block was added to `app.py`).
- **No new dark patterns:** no new prompts, upsells, confirm-shaming, or defaults that favor
  the app over the user. The restore confirm dialog retains its honest safety-copy explanation
  ("Your current data is first copied to a safety file, so nothing is lost.").
- **No new color-only communication:** no visual changes at all were introduced. (Pre-existing
  color cues like Pulse bands are out of Sprint 1 scope; the step dots in the tour use
  numbered text plus color, so they were already fine.)

## Overall sign-off

Sprint 1 is **UX-approved with the copy proposals above** — all marked pass-with-copy-proposal
or pass. The proposals in Areas 1 and 2 are microcopy/docs-placement only; no layout, flow,
color, or interaction changes exist in Sprint 1, so no full heuristic evaluation is required.
Nothing in Sprint 1 introduced dark patterns, changed what the user sees, or moved a button.
