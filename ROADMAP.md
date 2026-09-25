# Focus Core — Roadmap (after Sprint 1)

Sprint 1 is **Track A + stability** (hardening, security baseline, CI, QA evidence).
Everything below is **Track B: productization** — turning a working personal tracker into
software a stranger can install, trust, and pay for. Ordered by priority; sizes are
relative (S ≈ days, M ≈ 1–2 weeks, L ≈ a month of focused work).

**Explicit rule: no AI features in early phases.** The product's promise is local,
private, deterministic tracking. AI/ML ideas (auto-categorization models, coaching
agents, natural-language queries) are parked until the product is installable,
documented, and released reliably.

## Phase 6 — Installer & first-run polish (M, priority 1)
Make installing feel like real software, not a zip of scripts.
- One real installer (e.g. Inno Setup / WiX): desktop icon, Start-menu entry,
  clean uninstall that keeps the user's DB.
- First-run wizard: detects/installs ActivityWatch, explains the tray icon,
  offers the welcome tour, sets the backup folder.
- Replace `setup.bat` idempotency hacks with a proper install/upgrade path;
  signed binaries if budget allows.
- Success metric: a non-technical stranger installs and sees their first Pulse
  within 10 minutes, unaided.

## Phase 7 — Onboarding & docs for strangers (S–M, priority 2)
The current docs assume Merlin is one message away. Strangers don't have Merlin.
- Rewrite README/USER_GUIDE for a first-time user: what it does, what it costs
  (ActivityWatch is free), privacy in one paragraph, FAQ.
- In-app help: every dashboard page links to the right troubleshooting section.
- Video or GIF walkthrough of install → first focus session.
- Public support channel decision (email vs. forum) and a support SLA for ourselves.

## Phase 8 — Release engineering (M, priority 3)
Ship updates without expiring links and without fear.
- Versioned GitHub Releases with auto-generated changelog from CHANGELOG.md.
- CI builds the installer artifact on every tag; checksums published next to downloads.
- In-app "a new version is available" notice (checking a static version file —
  no telemetry, just a version comparison).
- Documented rollback: keep the last known-good installer + one-click DB restore.
- Revisit the dependency-audit cadence (FC-007) as a release gate.

## Explicitly parked (not in these 3 phases)
- AI/ML features (smart categorization, AI coaching, chat) — only after the
  product is installable, documented, and shipping reliably.
- Mobile companion, cloud sync beyond the user's own Drive folder, team/family plans.
- Network-level blocking, phone enforcement, invoicing — deliberately out of scope
  unless real users ask.
