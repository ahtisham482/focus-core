"""Roadmap 1.8: corrupt-database startup behavior (test-only pins).

Pointing startup at a garbage-bytes file where the database should be
must fail loudly WITHOUT destroying the evidence: roadmap 1.13 adds the
recovery UX later, and it needs the corrupt file intact to offer
"restore from backup". Live-verified 2026-10-01 against
``store.init_db`` / ``migrations.apply_migrations``:

* both raise ``migrations.MigrationError`` ("Database access error
  during fast check: file is not a database"),
* the file's bytes stay untouched (never deleted, truncated, or
  silently recreated), and
* no ``-wal``/``-shm`` sidecar or replacement file appears next to it.
"""

import hashlib

import pytest

from focuscore import migrations, store

GARBAGE = bytes(range(256)) * 4 + b"this is not a sqlite database"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_init_db_on_garbage_raises_and_preserves_bytes(tmp_path):
    db = tmp_path / "live.db"
    db.write_bytes(GARBAGE)
    digest = _sha(db)

    with pytest.raises(migrations.MigrationError) as excinfo:
        store.init_db(str(db))
    assert "not a database" in str(excinfo.value)

    # Byte-identical, at the same path, and still the only file there.
    assert db.read_bytes() == GARBAGE
    assert _sha(db) == digest
    assert [p.name for p in tmp_path.iterdir()] == ["live.db"]


def test_apply_migrations_on_garbage_raises_and_preserves_bytes(tmp_path):
    db = tmp_path / "live.db"
    db.write_bytes(GARBAGE)

    with pytest.raises(migrations.MigrationError):
        migrations.apply_migrations(str(db))

    assert db.read_bytes() == GARBAGE
    assert [p.name for p in tmp_path.iterdir()] == ["live.db"]


def test_repeated_init_on_garbage_keeps_failing_clearly(tmp_path):
    # Never cached into a false success: the migrations "applied"
    # registry must not claim this file was migrated, so a second
    # startup attempt fails exactly like the first.
    db = tmp_path / "live.db"
    db.write_bytes(GARBAGE)

    for _ in range(2):
        with pytest.raises(migrations.MigrationError):
            store.init_db(str(db))
    assert db.read_bytes() == GARBAGE


def test_fresh_path_still_initializes(tmp_path):
    # Control: the corrupt pins are meaningful only if a good path
    # initializes normally.
    db = tmp_path / "fresh.db"
    store.init_db(str(db))
    assert db.exists()
    assert store.get_setting("anything", path=str(db)) is None
