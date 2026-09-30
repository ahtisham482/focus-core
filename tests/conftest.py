"""Suite-wide test isolation for the emergency-pass JSONL fallback (Qwen R7).

`focuscore.shield.fallback_passes_path()` points at the REAL user data dir
(~/.focus-core/passes.fallback.jsonl). When SQLite is unreachable during a
test (locked DB, full disk, missing dir), `store.create_pass()` appends the
pass to that real file -- and `shield.pass_active()` reads it back on every
call, including in the real app. A stale test pass can therefore disable the
user's real shield and break unrelated tests that assert "no active pass".

This autouse fixture redirects the fallback file into pytest's tmp dir for
the whole suite, so no test can ever touch the real one.
"""

import pytest

from focuscore import shield


@pytest.fixture(autouse=True)
def _isolate_pass_fallback_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        shield,
        "fallback_passes_path",
        lambda: str(tmp_path / "passes.fallback.jsonl"),
    )
