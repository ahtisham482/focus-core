# Flaky Tests — Sprint 1 (Cycle 1)

**Date:** 2026-09-25 · **QA:** Agent 9 · **Ticket:** FC-008

## Method
Full suite run 3× back-to-back on Linux:
`cd /home/hatch/workspace/focus-core && /tmp/fc-venv/bin/python -m pytest -q`

| Run | Result |
|-----|--------|
| 1 | 166 passed |
| 2 | 166 passed |
| 3 (verbose, saved) | 166 passed |

Full output of run 3: `docs/qa/evidence/pytest-cycle1.log`

## Verdict
**None observed.** All 166 tests passed on all three consecutive runs; no test
failed intermittently, no ordering dependence seen.

## Non-flake note (not a failure)
One pre-existing warning, stable across all runs, unrelated to Sprint 1:
`tests/test_phase5.py::test_tray_icon_image_draws_in_code` —
`DeprecationWarning: Image.Image.getdata is deprecated and will be removed in
Pillow 14 (2027-10-15). Use get_flattened_data instead.`
Test passes; recommend replacing `getdata()` before the Pillow 14 upgrade.
Not logged as a defect for this cycle.
