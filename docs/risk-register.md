# Focus Core — Risk Register

Sprint 1 · 2026-09-25. Owner: Product Lead (Agent 1).
Likelihood / Impact scale: Low · Medium · High.

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|------|-----------|--------|------------|-------|
| R-01 | **Data loss from interrupted backup/restore.** Writes are not atomic and restore has no checksum validation (validated gap). A crash mid-write can leave a corrupt DB. | Medium | High | FC-001: atomic write (temp + rename), SHA256 recorded at backup time and verified before restore, interrupted backup never listed, interrupted restore leaves old DB intact. | Agent 2 |
| R-02 | **CSRF / DNS-rebinding against the loopback dashboard.** Flask has no Host/Origin validation or secure headers; a malicious page could POST to `127.0.0.1:5000` from the user's browser. | Low | High | FC-002: Host/Origin validation on all mutating routes + secure response headers; keep 127.0.0.1 default, no debug. | Agent 3 |
| R-03 | **Windows-only features untested in this build env.** Tray, notifications, `.bat` launchers, Drive auto-detection cannot be verified on Linux; silent breakage ships to the real user. | High | Medium | FC-004: exact human PC-verify checklist, every item `pending-pc` until run on real hardware; CI matrix includes `windows-latest` (FC-003) for what automation can cover. | Agent 5 |
| R-04 | **Dependency vulnerability in shipped third-party code.** Flask and friends ship to a non-technical user who will not patch themselves. | Low | Medium | FC-007: pip-audit clean or written waivers, evidence recorded; re-audit cadence at each release. | Agent 6 |
| R-05 | **Secrets accidentally committed.** API keys or tokens in the repo would ship inside `focus-core.zip`. | Low | High | FC-007: secrets scan of the repo with recorded evidence; no credentials are part of the design (local-only, no accounts). | Agent 6 |
| R-06 | **Scope creep into Track B (productization).** Sprint 1 is fixed as Track A + stability; installer polish, onboarding-for-strangers, and release engineering belong to later phases and would derail the hardening work. | Medium | Medium | Hard gate in docs/STATE.md; this register; roadmap explicitly defers Track B. Any Track B request becomes a roadmap item, not a Sprint 1 ticket. | Agent 1 |
| R-07 | **Flaky tests masking regressions.** Intermittent passes hide real breakage and erode trust in the suite. | Low | Medium | FC-008: full suite run 3× in the QA cycle; flakes named, quarantined or fixed before close. | Agent 9 |
| R-08 | **User runs an outdated version and reports already-fixed bugs.** Zip links previously expired; without a clear update channel the human may run stale code. | Medium | Low | Update path documented in README (GitHub Releases, re-run `setup.bat`); release engineering is a Track B phase on the roadmap. | Agent 1 |
| R-09 | **Google Drive sync conflicts on the backup DB.** Drive may upload a partially-written DB or create conflict copies the restore logic misreads. | Low | Medium | Atomic writes (FC-001) mean Drive only ever sees complete files; restore verifies checksum before swapping; prune-to-30 keeps the folder clean. Document in troubleshooting (FC-005). | Agent 2 |
| R-10 | **Regression from a hardening change.** Backup or security changes could break the launcher flow or the dashboard for the real user. | Medium | Medium | Full suite green before/after each ticket; sequential builds (no parallel writes); PC-verify checklist re-run on changed flows; FC-000 standing P0 catches anything that slips. | Agent 1 |

## Retired / accepted
- Flask binds 127.0.0.1 with a loud warning on other binds; no debug mode — accepted as baseline, hardened further by FC-002.
- No authentication on the dashboard — accepted: single-user local app, loopback-only by default; documented in the threat model (FC-006).
