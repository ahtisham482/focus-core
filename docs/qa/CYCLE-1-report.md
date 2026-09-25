# QA Verification Report — Sprint 1, Cycle 1

**Ticket:** FC-008 (P1) · **QA owner:** Agent 9 · **Date:** 2026-09-25
**Scope:** Sprint 1 changes — `focuscore/backup.py` hardening,
`dashboard/app.py` security baseline, docs, `.github/workflows/ci.yml`
**Repo:** `/home/hatch/workspace/focus-core` (branch `master`, HEAD `e62feca`;
Sprint 1 work is uncommitted — see §5)

## QA verdict: ❌ GATE FAILED

The full suite is green (166/166 × 3 runs, no flakes), but **two acceptance
criteria fail against their written wording** and one has never been executed:

1. **FC-001 criterion 4 (FAIL)** — no `backup --verify` or equivalent exists to
   re-check all stored backups; checksum verification happens only at restore
   time via the private `_verify_checksum`.
2. **FC-002 criterion 2 (FAIL)** — the required `Content-Security-Policy`
   response header is not set anywhere; only 3 of the 4 named headers exist.
3. **FC-003 (PENDING, no executed evidence)** — the CI workflow was never run;
   additionally, as written it deviates from the ticket (compileall instead of
   ruff/flake8; no smoke test in the artifact job).

I am exercising the QA veto: **Sprint 1 is not verified for release.** The
fixes are small (add a public `verify_all_backups()`, add a CSP header or
rewrite the criterion, run CI once), but the gate stays red until they land.

---

## 1. Test evidence

Full suite, 3 consecutive runs (`/tmp/fc-venv/bin/python -m pytest -q`):

| Run | Result |
|-----|--------|
| 1 | 166 passed in 2.72s |
| 2 | 166 passed in 2.73s |
| 3 (verbose, archived) | 166 passed in 3.00s |

- Flaky tests: **none observed** — see `docs/qa/flaky-tests.md`.
- Full output of run 3: `docs/qa/evidence/pytest-cycle1.log`
- New Sprint 1 test files: `tests/test_backup_hardening.py` (10 tests),
  `tests/test_security_baseline.py` (17 tests incl. parametrized).
- One stable, pre-existing warning (not a failure):
  `test_phase5.py::test_tray_icon_image_draws_in_code` —
  `DeprecationWarning` on `Image.Image.getdata` (removed in Pillow 14,
  2027-10-15). Recommend fixing before the Pillow 14 upgrade.

## 2. Acceptance-criteria matrix

Verdict key: **PASS** = verified against evidence · **FAIL** = evidence shows
the criterion is not met · **PENDING** = not verifiable in this environment
(CI not run, Windows-only) · **PARTIAL** = substantially done with a concrete gap.

### FC-001 — Harden backup/restore (P0)

| # | Criterion (from `docs/backlog.md`) | Verdict | Evidence |
|---|-------------------------------------|---------|----------|
| 1 | Backups written to temp file, atomically renamed via `os.replace`; a backup killed mid-write is never listed as valid | **PASS** | `focuscore/backup.py:204` (`create_backup`: write `<name>.db.tmp` → `os.replace`); `:326` (restore swap). Tests `test_create_leaves_no_temp_files`, `test_interrupted_create_leaves_no_partial_backup`, `test_list_backups_ignores_partial_temp_and_sidecars` pass. `list_backups` globs `focuscore-*.db`, skips `.tmp`/sidecars (`:215`). |
| 2 | Every backup records a SHA256 checksum (sidecar/manifest); `restore` verifies checksum **before** touching the live DB | **PASS** | `_write_checksum` (`:137`), `_sha256_of` (`:113`), sidecar `<name>.db.sha256` (`:108`). `restore_backup` calls `_verify_checksum(src)` at `:314`, before the safety copy and atomic swap (`:318-326`). Mismatch raises `ValueError`; missing sidecar (legacy) restores with logged `UserWarning` — documented trade-off. Tests `test_create_records_sha256_sidecar`, `test_restore_rejects_tampered_backup`, `test_restore_rejects_empty_sidecar`, `test_restore_works_when_checksum_sidecar_missing_legacy`, `test_restore_valid_checksum_warns_nothing` pass. |
| 3 | Interrupted restore leaves the original DB byte-identical (restore works on a copy, verified, swapped atomically) | **PASS** | Restore copies backup → temp file → `os.replace` over live DB (`:322-326`); leftover `.tmp` removed in `finally`. Safety copy `focuscore.db.pre-restore-<ts>` taken before swap (`:318-320`). Test `test_interrupted_restore_leaves_old_db_intact` (monkeypatched kill) passes. |
| 4 | `backup --verify` (or equivalent) exists to re-check all stored backups | **FAIL** | No public `verify_all_backups`/`--verify` exists. `grep` over `focuscore/`, `dashboard/`, tests finds only the private `_verify_checksum`, invoked solely at restore time. The planned test `test_verify_all_backups_reports_bad_file` has no actual equivalent in `tests/test_backup_hardening.py`. Checksum staleness is therefore only discovered at restore time, not proactively. |

**FC-001 verdict: FAIL (3/4).** One missing public API + its test.

### FC-002 — Flask local-security baseline (P1)

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | All POST (mutating) routes return 403 when `Host` is not loopback (`127.0.0.1`/`localhost` + port) and when `Origin`/`Referer` (if present) is not the dashboard itself | **PASS** (note) | `@app.before_request _reject_loopback_csrf` (`dashboard/app.py:302-313`): rejects non-mutating methods untouched; 403 on non-loopback `Host`; 403 on non-loopback `Origin` when present. 12 tests: evil host, host-with-port, subdomain spoof (`localhost.` tricks), evil/opaque origin, all mutating methods (POST/PUT/PATCH/DELETE), valid loopback host+origin allowed, absent Origin allowed, GET/HEAD unaffected. **Note:** `Referer` is never inspected (criterion names Origin/Referer). In practice browsers send `Origin` on POSTs and the `Host` check blocks the DNS-rebinding vector, so the security intent holds — but the letter of the criterion is not fully implemented. |
| 2 | Responses include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a restrictive `Content-Security-Policy` | **FAIL** | `@app.after_request _add_security_headers` (`:316-321`) sets only the first three headers. `grep -i "Content-Security" dashboard/app.py` → **zero matches**; no CSP in headers or `<meta>` anywhere. The tests (`test_secure_headers_on_get/_on_403/_on_redirect`) pass because `_assert_secure_headers` only asserts the three implemented headers — the suite does not test the CSP the ticket requires. |
| 3 | Default bind stays `127.0.0.1`; non-loopback bind keeps the loud warning | **PASS** | `--host` default `"127.0.0.1"` (`:1659`); non-loopback bind prints explicit exposure warning (`:1665-1668`). Test `test_default_bind_host_is_loopback` passes. |
| 4 | No `debug=True`, no reloader, no new network listeners | **PASS** | Single `app.run(host=args.host, port=args.port)` (`:1669`); `grep` finds no `debug=True`, no `use_reloader`. |

Definition of Done adds: "manual check on Windows PC that the dashboard still
opens from the launcher and all mutating actions work" → **pending-pc**
(also `docs/qa/pc-verify-checklist.md`, 9 items, all `pending-pc`).

**FC-002 verdict: FAIL (3/4 + 1 pending-pc).** Missing CSP header.

### FC-003 — CI workflow (P1)

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Workflow runs on push/PR: lint (ruff or flake8), full pytest, `focus-core.zip` artifact build | **PENDING** (never executed) — and deviates on paper | `.github/workflows/ci.yml` exists (untracked). Triggers: push + PR ✔. But the "lint" job is `python -m compileall` ("stdlib only, no new deps"), **not ruff or flake8** as the ticket requires. Test job: `pip install -r requirements.txt` + `pytest -q` ✔. Artifact job zips the repo ✔. |
| 2 | Matrix covers `windows-latest` + `ubuntu-latest`, Python 3.12 | **PENDING** | Matrix is declared correctly on paper; zero runs observed. |
| 3 | Built artifact on the Windows runner passes a smoke test (import core modules, start Flask briefly on 127.0.0.1, HTTP 200 on `/`) | **PENDING** — and missing on paper | The artifact job only zips + `upload-artifact`; **no smoke-test step exists** in the workflow as written. |
| 4 | Failing lint/tests blocks merge (branch protection noted as follow-up) | **PENDING** | No runs, no branch-protection evidence. |

**FC-003 verdict: PENDING.** No GitHub run exists; the workflow file itself
needs a lint-tool and smoke-test fix before its first run can satisfy the ticket.

### FC-006 — Threat model (STRIDE) + security checklist (P2)

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | `threat-model.md` covers a local-only Flask app with STRIDE: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege — in scope (loopback service, local DB, Drive-synced backups) and explicit out-of-scope (multi-user, remote access) | **PARTIAL** | `docs/security/threat-model.md` exists: assets, trust boundaries, per-component analysis (A. Flask loopback, B. SQLite DB, C. backup/restore, D. ActivityWatch ingest, E. tray/launcher), accepted risks, out-of-scope section (3 "out of scope" mentions ✔). But it is organized **by component, not by the six STRIDE categories**, and the words "Repudiation" (0 hits) and "Elevation of privilege" (0 hits) never appear; privilege is only mentioned in passing ("no privilege gain", "same privilege as the app itself, so out of scope"). Repudiation is not addressed at all. In-scope/out-of-scope split is good; STRIDE coverage is incomplete. |
| 2 | `checklist.md` is a concrete baseline, each item checkable yes/no | **PASS** | `docs/security/checklist.md`: 10 numbered yes/no items (loopback bind, no debug, Host/Origin validation, secure headers, no static DB serving, parameterized SQL, atomic backups + checksums, no path traversal, no secrets, dependency audit) with per-item evidence. |
| 3 | Checklist is consistent with what FC-002 and FC-001 actually implement | **PASS** | Checklist item 4 documents exactly the three headers implemented (no CSP claim — consistent with code); item 7 documents atomic writes + checksums (no verify-all claim — consistent with code). It accurately describes the implementation rather than the ticket's aspirations. The CSP and verify-all gaps are therefore FC-002/FC-001 defects, not FC-006 defects. |

**FC-006 verdict: PARTIAL (2.5/3).** Threat model needs an explicit
Repudiation section and per-category STRIDE treatment to meet criterion 1.

### FC-007 — Dependency audit + secrets scan (P1)

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | `pip-audit` runs clean on `requirements.txt`; findings get a written waiver or version bump | **PASS** | `docs/qa/evidence/dependency-audit.md`: `pip-audit -r requirements.txt` → "No known vulnerabilities found" (exit 0). Full-venv scan flagged only the venv's own `pip 24.0` (build tool, not shipped) — waived in writing with justification. `requirements.txt` unchanged (git status). |
| 2 | Secrets scan (gitleaks/trufflehog or documented manual pass) finds nothing; evidence recorded with tool name, version, date | **PASS** (with note) | Documented manual pass: `git grep -inE` over tracked files for `api_key`, `apikey`, `aws_access`, `aws_secret`, `-----BEGIN`, `client_secret`, `password = …`, `token = "…"` → zero matches. Note: pip-audit **version number is not recorded** (method says "was installable", no version). |
| 3 | `docs/security/dependency-audit.md` records: audit date, tool versions, full output/summary, waivers with justification, next-audit cadence (each release) | **PARTIAL** | The evidence file exists at **`docs/qa/evidence/dependency-audit.md`, not `docs/security/dependency-audit.md`** as the ticket specifies. Contents: audit date 2026-09-25 ✔, output summary ✔, waiver with justification ✔, cadence ("re-run on each dependency bump" / each release) ✔, pip-audit version ✘ (missing). |

**FC-007 verdict: PARTIAL.** Move/duplicate the evidence to the ticketed path
and record the pip-audit version.

## 3. Docs-vs-behavior spot checks

### PRIVACY.md vs reality
- **Substantive claim holds:** nothing leaves the machine. `requests` is used
  only by `focuscore/ingest.py` against `DEFAULT_BASE_URL =
  "http://localhost:5600/api/0/"` (ActivityWatch local API) ✔.
  `socket.create_connection` in `focuscore/launcher.py` targets only
  `HOST="127.0.0.1", PORT=5000` (liveness probe for the local dashboard) ✔.
  `webbrowser.open` opens the local dashboard URL only. The `https://…` URLs
  in `focuscore/pipeline.py` are demo/seed event data, never fetched.
  No telemetry/analytics/crash-reporting imports anywhere (`requirements.txt`:
  flask, requests, pytest, plyer, pystray, Pillow).
- **Wording issue (minor):** PRIVACY.md says "makes no network calls… it does
  not… contact any server." Literally, the app *does* make loopback-only
  network calls (ActivityWatch API, launcher port probe). Recommend rewording
  to "no network calls beyond your own computer" to stay literally accurate.
- **❌ Docs describe a non-existent feature:** PRIVACY.md's "Optional
  diagnostics bundle" section says "you can generate a diagnostics bundle from
  the dashboard" containing version/error/backup status. `grep -ri
  "diagnostic" focuscore/ dashboard/` → **zero matches**: the feature is not
  implemented. Either implement it or reword the section as planned/future.

### Troubleshooting vs actual error text
- `docs/support/troubleshooting.md` §"Restore failures / checksum mismatch"
  documents: Restore fails with an error about the backup being "corrupt",
  "modified", or a SHA256 checksum problem; current data untouched; safety
  copy `focuscore.db.pre-restore-<date-time>` saved.
- Actual `ValueError` in `focuscore/backup.py:166-169`: *"Backup %s failed its
  integrity check (SHA256 mismatch): the file may be **corrupt or tampered
  with**; restore refused."* — **matches** the documented symptom and the
  safety-copy behavior (`:318-320`). ✔ Consistent.

## 4. Release-blocking items (QA veto)

| # | Blocker | Owner per backlog | Suggested fix |
|---|---------|-------------------|---------------|
| 1 | FC-001/4: no `backup --verify` equivalent | Agent 2 | Add public `verify_all_backups()` in `focuscore/backup.py` (iterate `list_backups()`, reuse `_verify_checksum`, report bad files) + the planned `test_verify_all_backups_reports_bad_file`; re-run suite. |
| 2 | FC-002/2: no `Content-Security-Policy` header | Agent 3 | Add a restrictive CSP in `_add_security_headers` (e.g. `default-src 'self'`) and extend `_assert_secure_headers` in the tests; or rewrite the criterion if CSP is deliberately dropped. |
| 3 | FC-003: CI never executed; workflow deviates (compileall ≠ ruff/flake8; no smoke test) | Agent 6 | Fix the two job gaps, push, and record the first green run on both runners. |
| 4 | FC-006/1: threat model lacks Repudiation + per-category STRIDE | Agent 6 | Add explicit STRIDE-category treatment (at minimum Repudiation). |
| 5 | FC-007/3: audit evidence at wrong path, pip-audit version unrecorded | Agent 6 | Move/copy to `docs/security/dependency-audit.md`, add tool version. |
| 6 | PRIVACY.md documents a non-existent diagnostics bundle | docs owner | Implement or reword as future/planned. |
| 7 | Windows-only verification pending | user (PC) | `docs/qa/pc-verify-checklist.md` (9 items, all pending-pc), incl. FC-002's manual dashboard check. Cannot be cleared from Linux. |

## 5. Change inventory (final `git status --short` + `git diff --stat`)

```
 M dashboard/app.py
 M focuscore/backup.py
?? .github/
?? CHANGELOG.md
?? PRIVACY.md
?? ROADMAP.md
?? docs/
?? tests/test_backup_hardening.py
?? tests/test_security_baseline.py
```

- `git diff --stat`: `dashboard/app.py` +77; `focuscore/backup.py` +175/−15
  (237 insertions, 15 deletions). Only the two intended code files modified.
- Untracked additions are all expected Sprint 1 deliverables: the CI workflow,
  4 doc files, the whole `docs/` tree (backlog, risk-register, security/,
  support/, qa/, STATE.md, state.json), and the 2 new test files.
  (Note: `docs/` was never committed in earlier phases, so it appears as
  untracked in full — nothing unexpected inside it beyond Sprint 1 docs.)
- **No stray files committed or staged:** `git ls-files` shows no `.db`,
  no `__pycache__`, no `.pyc`. `.gitignore` covers `backups/`, `*.db`,
  `__pycache__/`, `.pytest_cache/` (all confirmed ignored via `git check-ignore`).
  Local `backups/*.db` files on disk are dev/test artifacts, ignored, not committed.
- Test files are **added only** (`??`), never modified — per the rules, no
  code or tests were touched by QA.

## 6. What is green

- Full suite: 166/166 × 3 runs, no flakes (`docs/qa/flaky-tests.md`).
- FC-001 criteria 1–3 fully implemented and tested (atomic writes, SHA256
  sidecars, verify-before-restore, interrupted-restore safety, legacy-backup
  warning path).
- FC-002 criteria 1, 3, 4 implemented and tested (Host/Origin 403s,
  loopback-only default bind + warning, no debug/reloader).
- FC-006 checklist and FC-007 audit/secrets-scan evidence exist and are
  substantially complete.
- Docs-vs-behavior: troubleshooting checksum section matches the real error text.

## 7. Files produced by this QA cycle (allowed set only)

- `docs/qa/CYCLE-1-report.md` (this file)
- `docs/qa/flaky-tests.md`
- `docs/qa/evidence/pytest-cycle1.log` (full verbose output of run 3)

No code or test files were modified.
