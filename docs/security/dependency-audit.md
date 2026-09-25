# Dependency audit — 2026-09-25

## Method
- `pip-audit` was installable in this environment and **did** reach the
  vulnerability database (network worked). Ran twice:
  1. `pip-audit -r requirements.txt` — audits exactly what the app ships.
  2. `pip-audit` (full venv) — audits the whole installed environment.
- Secrets scan: `git grep -inE` on tracked files for `api_key`,
  `apikey`, `aws_access`, `aws_secret`, `-----BEGIN`, `client_secret`,
  `password = ...`, and `token = "..."` literals.
- `pip-audit` was installed only into the throwaway venv at
  `/tmp/fc-venv`; it was **not** added to `requirements.txt`.
- Tool version: **pip-audit 2.10.1** (verified 2026-09-25 via
  `pip show pip-audit`).

## Results

### pip-audit against requirements.txt
**No known vulnerabilities found.** (exit 0)

### pip-audit against the full venv
Only findings were against `pip 24.0` itself (the old pip bundled in
this throwaway venv, e.g. PYSEC-2026-2875 / PYSEC-2026-2876 /
PYSEC-2026-196 / PYSEC-2026-1795 / PYSEC-2026-1796 / PYSEC-2026-3721).
pip is a build tool, not a shipped dependency, and CI installs with a
fresh `setup-python` pip, so this is not an app risk. None of the app
dependencies had findings.

### Secrets scan
**Zero matches.** No API keys, tokens, passwords, AWS credentials, or
PEM private keys in tracked files.

## Installed dependency versions (pip freeze, relevant rows)

```
Flask==3.1.3
pillow==12.3.0
plyer==2.1.0
pystray==0.19.5
pytest==9.1.1
requests==2.34.2
```

`requirements.txt` is unpinned (`flask`, `requests`, `pytest`, `plyer`,
`pystray`, `Pillow`). Today's audit is against the versions above.
Recommendation (not done here): pin minimum versions in requirements.txt
so a future `pip install` cannot silently resolve to a vulnerable old
release; re-run this audit on each dependency bump.

## Verdict
**PASS** — no known CVEs in shipped dependencies; no secrets in the repo.
