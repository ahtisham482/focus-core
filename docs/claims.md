# Claims register — Focus Core behavioral honesty (Roadmap 1.18)

Every behavioral promise the product makes — in PRIVACY.md, README.md,
USER_GUIDE.md, and the dashboard's own copy — is registered here with its
evidence. No claim may be bare. Statuses: **[TESTED]** (automated test, id
given) · **[MANUAL]** (exact steps a human runs) · **[HEDGED]** (copy
rewritten to the honest weaker claim; the diff location is given).

Audit method (honest coverage note): keyword sweep of PRIVACY.md,
README.md, USER_GUIDE.md, and the dashboard's Python-rendered copy for
promise language ("always", "never", "guaranteed", "automatically",
"blocks", "restore", "accurate", "works"), plus the four roadmap 1.18
example claims. Each hit was read in context and either proved or hedged.
Code comments are not claims and are not registered.

## Privacy (PRIVACY.md — corrected in 0.2)

- [TESTED] No accounts, no analytics, no telemetry, no crash reporting — the three internet uses listed under "Internet use", plus the optional Drive backup, are the complete list; the short-version sentence points at the Drive backup instead of implying only three flows exist. | surface: PRIVACY.md "The short version", "What Focus Core never does" | evidence: tests/test_privacy_update_toggle.py::test_privacy_documents_the_three_outbound_flows, ::test_privacy_no_longer_claims_no_internet_calls, ::test_privacy_blanket_claims_qualify_drive_backup
- [TESTED] The dashboard is not reachable from the internet — it only listens on this PC (127.0.0.1). | surface: PRIVACY.md "What lives where" | evidence: tests/test_claims_register.py::test_dashboard_binds_loopback_only
- [TESTED] The tracker reads ActivityWatch through a connection that never leaves your PC (local loopback). | surface: PRIVACY.md "The short version" | evidence: tests/test_claims_register.py::test_activitywatch_api_base_is_loopback
- [TESTED] The update check runs at most once a day and sends only your IP address and the User-Agent label "FocusCore-Updater" — never the app version, never any activity data. | surface: PRIVACY.md "Internet use" #1, Updates page toggle card | evidence: tests/test_privacy_update_toggle.py::test_privacy_does_not_claim_version_is_sent, ::test_toggle_card_copy_matches_what_is_sent, ::test_background_check_runs_when_enabled
- [TESTED] With the automatic update check turned off, normal page loads make no network request; the manual "Check again" button always works. | surface: PRIVACY.md "Internet use" #1, Updates page | evidence: tests/test_privacy_update_toggle.py::test_update_page_load_checks_off_fresh_cache_no_network, ::test_update_page_load_checks_off_stale_cache_no_network, ::test_update_page_load_checks_off_no_cache_no_network, ::test_update_page_refresh_forces_check_when_toggle_off
- [TESTED] The installer SHA-256 checksum is verified before anything runs — a corrupted download, or a release with no usable checksum record, is refused instead of installed. | surface: PRIVACY.md "Internet use" #2 | evidence: tests/test_updater.py::test_download_hash_ok, ::test_download_hash_mismatch_raises_and_deletes, ::test_download_missing_checksums_url_fails_closed
- [TESTED] Drive backup is automatic whenever Google Drive for Desktop is installed; there is no in-app on/off switch, and quitting Drive for Desktop stops the copies. | surface: PRIVACY.md "Google Drive backup (automatic when Drive is installed)" | evidence: tests/test_privacy_update_toggle.py::test_privacy_drive_backup_described_truthfully
- [TESTED] A backup copy holds the whole focuscore.db; window titles and website addresses stay DPAPI-encrypted inside the copy; restoring the file on a different Windows user or PC brings back everything else (times, scores, goals, timesheets) while titles and addresses show as unreadable placeholders. | surface: PRIVACY.md "What is copied" | evidence: tests/test_backup_crypto.py (passphrase round-trip suite), tests/test_column_crypto.py::test_undecryptable_blob_shows_placeholder, ::test_undecryptable_garbage_shows_placeholder
- [TESTED] Optional passphrase backup encryption: an encrypted backup cannot be recovered without the passphrase — not even by us — and enabling it never puts the passphrase inside the database or a backup. | surface: PRIVACY.md "Optional backup encryption (off by default)" | evidence: tests/test_backup_crypto.py::test_crypto_wrong_passphrase_raises_dedicated, ::test_restore_with_passphrase_round_trips, ::test_passphrase_absent_from_settings_db_and_logs
- [TESTED] The diagnostics export never contains your tracked activity: no database, no window titles, no URLs, no scores, no goals, no timesheet entries; log tails are anonymized and size-limited. | surface: PRIVACY.md "Optional diagnostics bundle (only when you export it)" | evidence: tests/test_diagnostics_export.py::test_zip_never_includes_the_database, ::test_log_tail_is_anonymized_and_limited, ::test_secret_setting_values_never_appear

## Focus & blocking (roadmap 1.18 examples)

- [TESTED] Focus sessions block distracting apps. Honest definition: Standard mode shows a desktop notification plus a full-screen "back to work" nudge; Hardcore mode minimizes the window and locks for 30 seconds. Apps are never force-closed — the copy on the Focus page now says so. | surface: Focus page footnote (clarified in 1.18) | evidence: tests/test_shield.py::test_once_session_soft_notifies_and_overlays, ::test_once_session_hardcore_minimizes_and_locks
- [TESTED] The focus timer is not fooled by sleep or hibernate: focused time is measured on the monotonic clock and a suspend gap rewinds the cycle, so sleep can never inflate or complete a session. | surface: Focus page countdown | evidence: tests/test_session_modes.py::test_sleep_never_completes_cycle
- [TESTED+MANUAL] A verified backup restores end-to-end in one POST — "Done — all your history is back." | surface: Backup page "Moving to a new laptop", README FAQ | evidence: tests/test_backup_restore_gate.py::test_verified_restore_in_one_post (TESTED); on a real second PC — install Focus Core and Google Drive for Desktop, let Drive finish syncing, open the Backup page, restore the newest backup (type the passphrase if it is marked Encrypted), confirm days, goals, sessions, and timesheets are back (MANUAL).
- [TESTED] ActivityWatch gaps are surfaced, not silently dropped: when the tracker isn't running the Home page says "ActivityWatch isn't running … Your saved data is still shown below"; stale data raises a tracker card; AFK/idle time is labeled and excluded from scores in the Home footnote. | surface: Home page | evidence: tests/test_phase5.py::test_card_tracker_stale

## README / USER_GUIDE / other UI copy

- [TESTED] Re-scoring an activity as productive always wins — it will never be blocked again. | surface: README FAQ "An app I need keeps getting blocked during focus sessions." | evidence: tests/test_focus.py::test_is_blocked_override_wins
- [TESTED] The Home page nudges you when your newest backup is older than 7 days. | surface: USER_GUIDE.md "Backup", Home page attention cards | evidence: focuscore/backup.py ATTENTION_AFTER_DAYS = 7; tests covering attention_cards in tests/test_phase5.py and tests/test_onboarding.py
- [TESTED] The app has a tiny performance footprint (cold start and steady-state under the roadmap ceilings). | surface: README FAQ "Does it slow down my PC?" | evidence: tests/test_performance_footprint.py::test_cold_start_is_under_the_ceiling, ::test_ceilings_are_the_roadmap_numbers
- [TESTED] Starting an update never runs in the middle of a focus session — the Updates page refuses with "Can't update right now" until the session ends. | surface: Updates page | evidence: tests/test_claims_register.py::test_update_refuses_during_active_session
- [TESTED] The update flow never touches your data — it makes a safety backup first (and stops if that fails) and only then downloads and stages the installer. | surface: Updates page ("Your data is never touched by the update") | evidence: tests/test_claims_register.py::test_update_flow_never_touches_user_data
- [MANUAL] Focus Core works fully offline — dashboard, tracker ingest, and focus sessions need no internet. | surface: README FAQ "Does it need internet?" | evidence: (MANUAL) disconnect the PC's network, use the dashboard, start a focus session, and confirm everything works; only the update check and the optional calendar feed report they cannot reach the network.

## Format stability (FORMAT.md, roadmap 1.19)

- [TESTED] Your history survives upgrades: a database written at schema version 1 opens in the current app with its records intact, and exports don't quietly change your numbers — JSON (focuscore.timesheet/v1) is exact, and the small roundings CSV and HTML use are written down in FORMAT.md. | surface: FORMAT.md "Stability policy" | evidence: tests/test_format_contract_119.py::test_aged_v1_database_migrates_through_init_db, ::test_export_roundtrip_json_is_exact, ::test_export_roundtrip_csv_matches_declared_rendering, ::test_format_md_documents_the_real_schema_and_policy

## IT policy (docs/enterprise-deploy.md, roadmap 1.21)

- [TESTED] IT can pin Focus Core to one version with a machine policy (HKLM Features updates_disabled); while it is on, no self-update path reaches the network — the background check, the Updates page, a manual "Check again", the installer download, and a queued pending install all refuse, the Home page shows no update card, and the user's own update toggle cannot override it | surface: docs/enterprise-deploy.md "Pin the fleet", Updates page policy card, Home page attention cards | evidence: tests/test_update_kill_switch_121.py::test_check_for_update_refused_under_policy + tests/test_update_kill_switch_121.py::test_policy_beats_fresh_cache + tests/test_update_kill_switch_121.py::test_update_page_shows_policy_card_and_no_network + tests/test_update_kill_switch_121.py::test_update_start_refused_under_policy + tests/test_update_kill_switch_121.py::test_tray_watcher_skips_pending_under_policy + tests/test_update_kill_switch_121.py::test_home_update_card_suppressed_under_policy + tests/test_update_kill_switch_121.py::test_positive_control_check_reaches_network_when_policy_off

## How to keep this register honest

A new behavioral promise ("always", "never", "guaranteed", "automatically",
"blocks", "restores") may not ship without a register entry in this file and
a status — TESTED with the test id, MANUAL with the exact steps, or HEDGED
with the copy rewritten. `tests/test_claims_register.py::test_register_has_no_bare_claims`
enforces the format; `::test_register_covers_minimum_claims` stops it
shrinking silently.
