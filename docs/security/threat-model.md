# Focus Core — Threat Model (STRIDE)

Date: 2026-09-25. Scope: local-first desktop app, single user, Windows 10/11
deploy target. No network accounts, no cloud API, no authentication by
design. The user explicitly wants zero-setup: double-click and it works.

## Assets
1. `focuscore.db` — the SQLite database with all tracked activity.
2. `backups/focuscore-*.db` (+ `.sha256` sidecars) — backup copies.
3. The dashboard process (Flask on loopback) and the ActivityWatch data
   it ingests.

## Trust boundaries
- **T1:** This PC vs. the local network / internet (the `--host 0.0.0.0`
  flag deliberately crosses it — with a printed warning).
- **T2:** The user's browser vs. the loopback dashboard (any web page the
  user opens can reach 127.0.0.1:5000 — loopback CSRF is the real threat).
- **T3:** Backup folder vs. Google Drive sync (backups may leave the PC).
- **T4:** ActivityWatch (separate process, `http://localhost:5600`) vs.
  Focus Core ingest.

---

### A. Flask loopback dashboard (`dashboard/app.py`)

- **Spoofing / Tampering (T2):** A malicious web page cannot forge
  requests from the user's browser: mutating routes (POST/PUT/PATCH/DELETE)
  require a loopback `Host` header, and a present `Origin` must also be
  loopback (`_reject_loopback_csrf`, tested in
  `tests/test_security_baseline.py`). Residual: none known; DNS-rebinding
  style tricks are blocked by the same Host check.
- **Information disclosure:** Binds `127.0.0.1` by default; `--host`
  override prints an explicit warning. The DB file is never served
  (no `send_file`/`send_from_directory` routes; `dashboard/static/`
  contains only `style.css`).
- **Denial of service:** Loopback-only, single user; no rate limiting.
  Accepted: a local page could spam requests, but it cannot mutate, and
  the user can kill the server window.
- **Elevation:** No auth; anyone logged into this Windows account can use
  the dashboard. Accepted by design (same as any local file).

### B. SQLite DB file (`focuscore.db`)

- **Tampering:** Any process running as the user can rewrite the file —
  same privilege as the app itself, so out of scope.
- **Information disclosure (T1):** If the user runs with `--host 0.0.0.0`,
  anyone on the LAN can read the dashboard (and thereby all data). The
  launch path warns loudly; the default stays loopback-only.
- **Integrity in code:** All SQL goes through `focuscore/store.py` and is
  parameterized (`?` placeholders); grep confirms no f-string/`%`/concat
  SQL and no `.execute` in `dashboard/app.py`. Not exploitable via the
  UI because user input never reaches raw SQL.

### C. Backup / restore (`focuscore/backup.py`)

- **Tampering (T3):** Backups may sync to Google Drive; a tampered or
  corrupt `.db` is caught by the SHA256 sidecar — `restore_backup()`
  verifies **before** touching the live DB and raises `ValueError` on
  mismatch. Legacy backups without sidecars restore with a logged warning
  (documented trade-off; the sidecar scheme postdates them).
- **Tampering via crash (TOCTOU):** Writes go to `<name>.db.tmp` then
  `os.replace()` — the final name never points at a partial file;
  interrupted restores rename a temp copy over the live DB atomically,
  after a pre-restore safety copy is taken. Residual TOCTOU (file changed
  between verify and restore) needs write access to the backup folder,
  which already implies full compromise — accepted as low.
- **Path traversal:** `restore_backup(name)` only accepts names matching
  `^focuscore-\d{8}-\d{6}(-\d+)?\.db$` — no separators possible; the
  dashboard route also `escape()`s the name in error output. No traversal
  found.

### D. ActivityWatch local API ingest (`focuscore/ingest.py`)

- **Spoofing (T4):** Ingest reads from `http://localhost:5600/api/0/`
  with no authentication — anything on the loopback interface could pose
  as ActivityWatch and feed fake events. Impact is limited to polluting
  the user's own tracking data (no privilege gain, no exfiltration);
  ActivityWatch itself offers no auth, so this is inherent to the
  integration. Accepted, documented.
- **Information disclosure:** Only the user's own data flows over
  loopback; nothing leaves the PC.

### E. Tray icon / launcher (`focuscore/tray.py`, `focuscore/launcher.py`, `*.bat`)

- **Tampering:** The `.bat` files live next to the code and do nothing
  but `cd` to their own folder and start `pythonw -m focuscore.launcher`;
  they download nothing, fetch no remote content, and take no arguments
  from the network. A modified `.bat` is equivalent to a modified `.py`
  — same privilege, out of scope.
- **Spoofing:** No network listeners in tray/launcher; the dashboard
  opened in `--app` browser mode is still the same loopback server.
- **Denial of service:** Closing the focus-guard window intentionally
  stops enforcement (documented in the `.bat` text) — a feature, not a bug.

### F. Repudiation (who changed what, and when)

This is the weakest STRIDE leg for this app, and it matters because the
DB is the user's timesheet evidence (freelance hours, invoice support).

- **Timesheet locks:** A locked day can be edited or re-locked by anyone
  with local access to the dashboard — there is no per-action audit log
  recording who locked/changed a day or when. Since the app deliberately
  has no accounts, "who" can only ever mean "this Windows user", but the
  **when** is still lost. If a locked day's entries change, the user
  cannot tell whether they did it themselves or someone else did it on
  their unlocked PC. Accepted: single-user, single-machine by design.
  Cheap partial mitigation (not implemented): timestamped lock history
  would at least answer "when".
- **Log integrity:** `focuscore.db` is writable by any process running as
  the user, with no signatures or hash chain — there is no mechanism to
  detect post-hoc edits to tracked hours in the live DB. The only
  integrity evidence is external: backup snapshots (`backups/`) record
  the DB state at backup time, so a tampered live DB diverges from the
  last backup. Backups are the de-facto audit trail.
- **Dispute relevance:** In any billing dispute, this app's logs are
  self-recorded and self-editable. The threat model does not claim they
  are tamper-evident.

### G. Elevation of privilege (what the app must NOT become)

The local user already owns the machine, so the real elevation risk is
not the user gaining more — it is the app acquiring privileges it does
not need, or being tricked into acting with more authority than the
user intended.

- **No privilege escalation anywhere:** The app never requests admin/
  UAC elevation, never installs services or scheduled tasks (nothing
  running as SYSTEM), writes nothing to protected locations
  (`Program Files`, HKLM), makes no firewall rule changes, and never
  binds a privileged port (<1024). Deliverable rule: it must keep
  working from a plain user account on first run.
- **The focus blocker must not become a keylogger-class tool:** The
  blocking feature uses a pop-up window + fullscreen overlay for
  `-1`/`-2` apps during focus sessions. It does **not** install
  system-wide keyboard/mouse hooks, global shortcuts, or window-content
  scrapers. If a future feature ever needs input-level interception, it
  is a new trust boundary and needs its own threat review — do not add
  one casually.
- **Ingest stays low-authority:** ActivityWatch ingest only reads from
  `http://localhost:5600`; it never executes content from that feed —
  events are stored as data, never `eval`'d or shelled out. A spoofed
  feed can pollute tracking data (accepted, §D) but cannot turn the
  ingest path into code execution.
- **Launcher/`.bat` files:** Run as the interactive user only; no
  `runas`, no credential storage anywhere (there are no credentials in
  this app at all).

## Accepted risks (explicit)
1. No authentication — anyone with local Windows access can use the app.
2. `--host 0.0.0.0` exposes tracked data to the LAN (warned at startup).
3. ActivityWatch ingest is unauthenticated loopback HTTP (ActivityWatch
   limitation; impact = own-data pollution only).
4. Restore TOCTOU between checksum verify and rename — requires backup-
   folder write access; fail-closed on mismatch anyway.

## Out of scope
Malware already running as the user, physical theft of the PC, Google
Drive account compromise (backups are plaintext by design so Drive sync
"just works").
