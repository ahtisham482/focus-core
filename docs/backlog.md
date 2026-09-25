# Focus Core — Sprint 1 Backlog (Track A + stability)

Cycle 1 · 2026-09-25. Owner: Product Lead (Agent 1).
Baseline: 111/111 pytest tests pass on Python 3.12. Windows 10/11 is the deploy target;
this Linux build environment cannot verify Windows-only features, so PC-dependent
acceptance gates are marked `pending-pc`.

Status values: `todo` → `in-progress` → `in-review` → `done`.
Only the docs/product files in this list may be changed in this cycle unless a ticket names code files.

---

## FC-000 (standing) — Fix any P0/P1 bug that surfaces
- **ID:** FC-000 · **Owner:** triaged per incident (default: Agent 9 QA first)
- **Priority:** P0 · **Status:** todo (nothing open right now) · **Dependencies:** none
- **Files likely touched:** depends on the bug
- **User story:** As the product lead, I want a standing P0 slot so any critical regression found
  during Sprint 1 (failing suite, broken launcher, data-loss path) jumps the queue instead of
  waiting for a new ticket.
- **Acceptance criteria:** (1) Suite is green at close of cycle: 111 + new tests all pass.
  (2) No known P0/P1 issue without an owning ticket and an expected-date.
- **QA test names (planned):** existing suite stays green (all tests in `tests/`).
- **Security considerations:** n/a (standing).
- **Data-migration risk:** none.
- **UX impact:** none unless a bug ships — this ticket exists to prevent that.
- **Definition of Done:** Cycle closes with zero open P0/P1; or every open one has a dated owner.
- **Current state:** none open — suite green, no PC-reported bugs from the Phase 5 install.

---

## FC-001 — Harden backup/restore (atomic writes, checksums, crash safety)
- **ID:** FC-001 · **Owner:** Agent 2 · **Priority:** P0 · **Status:** todo · **Dependencies:** none
- **Files likely touched:** `focuscore/backup.py`, new `tests/test_backup_hardening.py`
- **User story:** As a user, I want backups and restores that cannot corrupt my database,
  so that even if the power fails mid-backup my history is safe.
- **Acceptance criteria (testable):**
  1. Backups are written to a temp file and atomically renamed into place (`os.replace`); a
     backup killed mid-write is never listed as a valid backup.
  2. Every backup records a SHA256 checksum of the source DB at backup time (sidecar file or
     manifest entry) and `restore` verifies the checksum before touching the live DB.
  3. An interrupted restore leaves the original DB byte-identical (restore works on a copy,
     verified, then swapped in atomically).
  4. `backup --verify` (or equivalent) exists to re-check all stored backups.
- **QA test names (planned):** `tests/test_backup_hardening.py` —
  `test_backup_killed_mid_write_not_listed`, `test_restore_verifies_checksum_before_swap`,
  `test_interrupted_restore_keeps_original_db_intact`, `test_checksum_mismatch_refuses_restore`,
  `test_verify_all_backups_reports_bad_file`.
- **Security considerations:** Restore is a privileged operation — only accept backup files from
  the user's own backup folder; never auto-restore from an untrusted path. Checksums detect
  accidental corruption, not malicious tampering (no code-signing of backups).
- **Data-migration risk:** Medium — old backups without checksums must remain restorable (verify
  skipped with a warning for legacy files), and the live DB format does not change.
- **UX impact:** Low — user-visible change is a "verify backups" option and clearer error text
  on failure; backup list gains a validity indicator.
- **Definition of Done:** All new QA tests pass on this Linux env; documented manual steps added
  to the PC-verify checklist for a restore run on a real Windows PC (`pending-pc`).

---

## FC-002 — Flask local-security baseline (Host/Origin checks, secure headers)
- **ID:** FC-002 · **Owner:** Agent 3 · **Priority:** P1 · **Status:** todo · **Dependencies:** none
- **Files likely touched:** `dashboard/app.py`, new `tests/test_security_baseline.py`
- **User story:** As a user, I want the local dashboard to reject cross-site requests,
  so a malicious webpage cannot change my data through my browser.
- **Acceptance criteria (testable):**
  1. All POST (mutating) routes return 403 when `Host` is not loopback (`127.0.0.1`/`localhost`
     + port) and when `Origin`/`Referer` (if present) is not the dashboard itself.
  2. Responses include secure headers: `X-Content-Type-Options: nosniff`,
     `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a restrictive `Content-Security-Policy`.
  3. Default bind stays `127.0.0.1`; non-loopback bind keeps the loud warning (unchanged).
  4. No `debug=True`, no reloader, no new network listeners introduced.
- **QA test names (planned):** `tests/test_security_baseline.py` —
  `test_post_rejected_with_foreign_host`, `test_post_rejected_with_foreign_origin`,
  `test_post_accepted_with_local_origin`, `test_get_pages_still_render`,
  `test_security_headers_present_on_all_responses`.
- **Security considerations:** Core threat addressed is CSRF/DNS-rebinding against a loopback
  service; this ticket does NOT add authentication (single-user local app — deliberate).
- **Data-migration risk:** none.
- **UX impact:** none — legitimate same-origin use is unaffected; foreign-origin mutating requests
  get a plain 403 page.
- **Definition of Done:** All new QA tests pass; manual check on Windows PC that the dashboard
  still opens from the launcher and all mutating actions work (`pending-pc`).

---

## FC-003 — CI workflow (lint + pytest + artifact build, Windows + Linux)
- **ID:** FC-003 · **Owner:** Agent 6 · **Priority:** P1 · **Status:** todo · **Dependencies:** none
- **Files likely touched:** new `.github/workflows/ci.yml` (no code files)
- **User story:** As a maintainer, I want every commit tested on both Windows and Linux,
  so Windows-only breakage is caught before release, not by the user.
- **Acceptance criteria (testable):**
  1. Workflow runs on push and pull request: lint (ruff or flake8), full pytest suite,
     and a `focus-core.zip` artifact build.
  2. Matrix covers `windows-latest` and `ubuntu-latest`, Python 3.12.
  3. The built artifact on the Windows runner passes a smoke test (import core modules,
     start Flask briefly against 127.0.0.1, HTTP 200 on `/`).
  4. Failing lint or tests blocks merge (branch protection noted as a follow-up if not set).
- **QA test names (planned):** n/a — the workflow itself is the test; evidence is green CI runs.
- **Security considerations:** CI must not print secrets; no deployment keys in this workflow;
  artifact is built, not published, in Sprint 1.
- **Data-migration risk:** none.
- **UX impact:** none directly; prevents regressions that would hurt users.
- **Definition of Done:** First green run on both runners visible in the repo's Actions tab;
  README or docs note the CI badge expectation (badge optional).

---

## FC-004 — PC-verify checklist (human steps on a real Windows PC)
- **ID:** FC-004 · **Owner:** Agent 5 · **Priority:** P1 · **Status:** todo · **Dependencies:** none
- **Files likely touched:** new `docs/qa/pc-verify-checklist.md`
- **User story:** As the product lead, I want exact, click-by-click verification steps for
  Windows-only features, so the human (or his on-PC agent) can prove tray, Drive detection,
  notifications, and launchers actually work on the real deploy target.
- **Acceptance criteria (testable):**
  1. `docs/qa/pc-verify-checklist.md` exists and covers, each as its own item: tray icon
     appears + every menu action works; Google Drive folder auto-detection; Windows
     notification appears; `setup.bat` idempotency (run twice, no errors, data kept);
     `Start Focus Core.bat` launcher; app-mode window opens with no address bar.
  2. Every item has: numbered steps, expected result, and a `[ ] pass / [ ] fail` box.
  3. Every item is marked `pending-pc` until run on real hardware.
- **QA test names (planned):** n/a — human checklist, not automated.
- **Security considerations:** n/a.
- **Data-migration risk:** The setup.bat idempotency item must explicitly check the DB and
  settings survive a second run.
- **UX impact:** This ticket produces the checklist itself; running it catches UX breakage.
- **Definition of Done:** Checklist merged; all items `pending-pc`; item for FC-001 restore
  verification cross-referenced.

---

## FC-005 — Troubleshooting guide
- **ID:** FC-005 · **Owner:** Agent 5 · **Priority:** P2 · **Status:** todo · **Dependencies:** none
- **Files likely touched:** new `docs/support/troubleshooting.md`
- **User story:** As a non-technical user, when something breaks I want a plain-language page
  that tells me exactly what to try, before I have to ask for help.
- **Acceptance criteria (testable):**
  1. Covers at minimum: ActivityWatch not running / stale data; port 5000 already in use;
     "backup stale" warning; dashboard won't start; tray icon missing; Google Drive folder
     not found.
  2. Each entry has: symptom (what you see), cause (one line), recovery steps (numbered,
     no jargon), and "if it still fails" (send a screenshot to Merlin).
  3. Written in very simple English, consistent with README/USER_GUIDE tone.
- **QA test names (planned):** n/a — doc review; each recovery step cross-checked against
  actual code paths (e.g. the port-5000 conflict message the app prints).
- **Security considerations:** n/a.
- **Data-migration risk:** none.
- **UX impact:** Reduces support load; must be linked from the dashboard (footer or help link)
  — wiring that link is a one-line change tracked as part of this ticket's DoD.
- **Definition of Done:** Guide merged, linked from dashboard, spot-checked against code.

---

## FC-006 — Threat model (STRIDE) + security checklist
- **ID:** FC-006 · **Owner:** Agent 6 · **Priority:** P2 · **Status:** todo · **Dependencies:** FC-002 (checklist validates against the implemented baseline)
- **Files likely touched:** new `docs/security/threat-model.md`, new `docs/security/checklist.md`
- **User story:** As the product lead, I want the security assumptions written down,
  so future changes don't silently undo the Sprint 1 hardening.
- **Acceptance criteria (testable):**
  1. `threat-model.md` covers a local-only Flask app with STRIDE: Spoofing, Tampering,
     Repudiation, Information disclosure, Denial of service, Elevation of privilege —
     with what's in scope (loopback service, local DB, Drive-synced backups) and what's
     explicitly out of scope (multi-user, remote access).
  2. `checklist.md` is a concrete baseline: loopback-only default bind, no debug mode,
     Host/Origin validation on POST routes, secure headers, atomic backups, no secrets in
     repo, dependency audit cadence — each item checkable yes/no.
  3. Checklist is consistent with what FC-002 and FC-001 actually implement.
- **QA test names (planned):** n/a — doc review against implemented code.
- **Security considerations:** This ticket IS the security documentation.
- **Data-migration risk:** none.
- **UX impact:** none.
- **Definition of Done:** Both docs merged; checklist re-verified after FC-002 lands.

---

## FC-007 — Dependency audit + secrets scan, with recorded evidence
- **ID:** FC-007 · **Owner:** Agent 6 · **Priority:** P1 · **Status:** todo · **Dependencies:** none
- **Files likely touched:** new `docs/security/dependency-audit.md` (evidence); possibly
  `requirements.txt` (only if an upgrade is needed — flag, don't silently upgrade)
- **User story:** As the product lead, I want proof the third-party code we ship has no known
  critical vulnerabilities and the repo contains no leaked secrets.
- **Acceptance criteria (testable):**
  1. `pip-audit` runs clean on `requirements.txt`; any finding gets a written waiver
     (why it's acceptable) or a version bump.
  2. A secrets scan of the repo (e.g. gitleaks/trufflehog or documented manual pass) finds
     nothing; evidence recorded with tool name, version, and date.
  3. `docs/security/dependency-audit.md` records: audit date, tool versions, full output or
     summary, waivers with justification, and the next-audit cadence (each release).
- **QA test names (planned):** n/a — evidence file is the deliverable.
- **Security considerations:** This ticket IS the audit; waivers must be explicit, not silent.
- **Data-migration risk:** A dependency upgrade could change behavior — if any upgrade is
  required, the full test suite must re-run green before merge.
- **UX impact:** none.
- **Definition of Done:** Evidence file merged, clean or waived, dated 2026-09-25 or later.

---

## FC-008 — QA CYCLE-1 report + evidence bundle + flaky-test check
- **ID:** FC-008 · **Owner:** Agent 9 · **Priority:** P1 · **Status:** todo · **Dependencies:** all other Sprint 1 tickets (report covers the whole cycle)
- **Files likely touched:** new `docs/qa/CYCLE-1-report.md`, new `docs/qa/evidence/*`
- **User story:** As the product lead, I want a single report proving what was verified,
  how, and what remains `pending-pc`, so release decisions are based on evidence, not memory.
- **Acceptance criteria (testable):**
  1. `docs/qa/CYCLE-1-report.md` lists every Sprint 1 ticket with status, evidence links,
     and any `pending-pc` items explicitly called out.
  2. `docs/qa/evidence/` contains the raw evidence: pytest output, pip-audit output,
     CI run links/screenshots, checklist results.
  3. Flaky-test check: full suite run 3×; any test failing intermittently is named, quarantined
     or fixed — "flaky" is not an acceptable closing state.
  4. Final suite count and pass rate recorded (baseline entering cycle: 111/111).
- **QA test names (planned):** the full suite, run 3×.
- **Security considerations:** Evidence must not contain secrets or personal data.
- **Data-migration risk:** none.
- **UX impact:** none.
- **Definition of Done:** Report merged, evidence present, zero unexplained flakes,
  `pending-pc` list complete and handed to the human.
