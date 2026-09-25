# Focus Core — STATE.md (Orchestrator-owned)
Sprint 1 · Track A + stability · Cycle 2 · 2026-09-25 — **GATE PASSED**

## What exists (validated, not assumed)
- Repo: `/home/hatch/workspace/focus-core`, git, 4 commits (Phases 1–5 + Sprint 1).
- **170/170 pytest tests pass** (Python 3.12.3), 6 consecutive green runs, zero flakes.
- `ruff check --select E,F` clean (error-level lint gate; ~318 style nits parked as ROADMAP style debt).
- Backup/restore hardened: atomic writes, SHA256 sidecars, verify-before-restore,
  public `verify_all_backups()`; legacy backups restore with warning.
- Flask: 127.0.0.1 default + loud non-loopback warning, Host/Origin validation
  (403) on mutating routes, 4 secure headers incl. CSP.
- CI workflow (`.github/workflows/ci.yml`): ruff + pytest matrix (win+ubuntu) +
  artifact zip + smoke. Validated by local simulation; first GitHub run pending push.
- Docs: backlog (FC-000..FC-008), risk register, ROADMAP (3 Track B phases),
  CHANGELOG, PRIVACY, STRIDE threat model, security checklist 10/10,
  dependency audit (pip-audit 2.10.1 clean, secrets scan zero hits),
  troubleshooting, pc-verify checklist (9 items, all pending-pc),
  QA CYCLE-1 (veto) + CYCLE-2 (veto lifted) reports + evidence logs.

## What still needs the human (Windows PC)
- 9 pc-verify items: tray icon/menu, Drive detection, notifications, setup.bat
  idempotency, app-mode window, strict blocking overlay, restore e2e,
  install-from-zip history preservation → `docs/qa/pc-verify-checklist.md`.
- Push repo to GitHub → triggers first real CI run.
- Approve/reject UX copy proposals (restore-failure message, welcome-tour trust
  copy) → parked for Sprint 2.

## Retro
- What worked: sequential lanes (zero merge conflicts), evidence-based gates
  (QA veto caught 5 real gaps in Cycle 1), file-ownership discipline.
- What slipped: orchestrator did mechanical lint cleanups directly across
  Agent 2/3/9 files (one edit mangled an onsubmit line; caught and fixed by
  byte-check + full suite re-run). Lesson: even trivial cross-owner edits go
  through the owning agent next time.
- Fix-loop used: 1 of 3. Cycles used: 2 of 3.
- Next: human checkpoint (this report). Sprint 2 / Track B only on approval.

## Risks (open)
- R-03: Windows-only features unverifiable from Linux — mitigated by checklist.
- Requirements unpinned — re-run pip-audit on each bump (noted in audit doc).
- Pillow DeprecationWarning (`Image.getdata`) — fix before Pillow 14.
