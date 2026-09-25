# Local Security Baseline — pass/fail checklist

Date: 2026-09-25. Verified by reading code + grepping (see evidence per
item). Suite: 166/166 green via `/tmp/fc-venv/bin/python -m pytest -q`.

| # | Requirement | Result | Evidence |
|---|-------------|--------|----------|
| 1 | Dashboard binds loopback (127.0.0.1) by default | **PASS** | `dashboard/app.py`: `--host` default is `"127.0.0.1"`; non-loopback bind prints an explicit warning. Test `test_default_bind_host_is_loopback` passes. |
| 2 | No debug mode | **PASS** | `app.run(host=args.host, port=args.port)` — no `debug=True` anywhere; grep for `debug` finds only a category seed string in `seed_data.py`. |
| 3 | Host/Origin validation on mutating routes | **PASS** | `_reject_loopback_csrf` (`@app.before_request`) 403s POST/PUT/PATCH/DELETE with non-loopback Host, and with non-loopback Origin when present. 12 tests in `test_security_baseline.py` cover evil host, port, subdomain spoof, evil/opaque origin, all mutating methods, valid-loopback allowed, no-Origin allowed, GET/HEAD unaffected. |
| 4 | Secure headers on all responses | **PASS** | `_add_security_headers` (`@app.after_request`) sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`. Tests assert them on 200, 403, and redirect responses. |
| 5 | No DB served statically | **PASS** | No `send_file` / `send_from_directory` in `dashboard/app.py` or `focuscore/`; `dashboard/static/` contains only `style.css`; `focuscore.db` lives at project root, outside the static dir. |
| 6 | Parameterized SQL everywhere | **PASS** | All 42 `.execute` calls live in `focuscore/store.py` and use `?` placeholders; zero `.execute` in `dashboard/app.py`; grep found no `execute(f"…")`, `execute("…" % …)`, or string-concat SQL. |
| 7 | Backup integrity: atomic writes + checksums | **PASS** | `create_backup` writes `<name>.db.tmp` then `os.replace()`; SHA256 sidecar written per backup; `restore_backup` verifies **before** touching live DB, raises `ValueError` on mismatch; 10 tests in `test_backup_hardening.py`. |
| 8 | No path traversal in restore | **PASS** | `restore_backup(name)` rejects anything not matching `^focuscore-\d{8}-\d{6}(-\d+)?\.db$` — separators impossible; dashboard error path `escape()`s the name. |
| 9 | No secrets in repo | **PASS** | `git grep -inE` for `api_key`/`apikey`/`aws_access`/`aws_secret`/`-----BEGIN`/`client_secret`/`password =`/`token = "…"` → zero hits. |
| 10 | No known CVEs in dependencies | **PASS** | `pip-audit -r requirements.txt` → "No known vulnerabilities found"; full-venv scan flags only the venv's own `pip 24.0` (build tool, not shipped). See `docs/security/dependency-audit.md`. |

## Diff review verdict — FC-001 (`focuscore/backup.py`) and FC-002 (`dashboard/app.py` security section)

Reviewed via `git diff` on 2026-09-25. **No security flaws found. Safe to keep.**

What was checked:
- **Path traversal (FC-001):** `restore_backup` gates on the strict
  `BACKUP_NAME_PATTERN` regex before any filesystem use; `..`, `/`, and
  `\` cannot match. `list_backups` globs only `focuscore-*.db` and skips
  `.tmp`/sidecars. PASS.
- **TOCTOU (FC-001):** Temp file + `os.replace()` in the same folder
  (same volume → atomic rename on Windows and POSIX); checksum verified
  before the live DB is touched; safety copy taken before the atomic
  swap; leftover `.tmp` cleaned in `finally`. A verify→restore race
  needs backup-folder write access, which already equals full compromise
  — accepted, fail-closed on mismatch anyway. No actionable flaw.
- **Header parsing (FC-002):** `_host_is_loopback` handles
  `[::1]:port`, `127.0.0.1:port`, bare `::1`, strips whitespace,
  lowercases, and rejects raw non-loopback IPv6 and `localhost.`-style
  suffix tricks (fail-closed). `_origin_is_loopback` requires
  http/https scheme and a loopback hostname; `ValueError` from
  `urlsplit` is caught. The `before_request` hook covers exactly the
  mutating methods and leaves GET/HEAD/OPTIONS alone. PASS.

Notes (not flaws):
- A sidecar that exists but is empty/unparseable fails verification
  (fail-closed) — correct behavior.
- Legacy backups without sidecars still restore, with `logging` +
  `warnings.warn` — documented trade-off in the module docstring and
  covered by `test_restore_works_when_checksum_sidecar_missing_legacy`.
- `prune_backups` deletes each backup's sidecar with it (no orphans).

**Overall: 10/10 PASS. No changes requested to `focuscore/backup.py` or
`dashboard/app.py`.**
