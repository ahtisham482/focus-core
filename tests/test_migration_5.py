"""Phase 7: migration 5 (block_rules, block_passes, settings)."""

import sqlite3

from focuscore import migrations, store


def _tables(db):
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_migration_5_creates_tables_and_defaults(tmp_path):
    db = str(tmp_path / "m5.db")
    migrations.apply_migrations(db)
    tables = _tables(db)
    assert "block_rules" in tables
    assert "block_passes" in tables
    assert "settings" in tables
    assert migrations.LATEST_VERSION == 5
    # defaults seeded
    assert store.get_setting("hud_enabled", path=db) == "1"
    assert store.get_setting("shield_enabled", path=db) == "1"
    # version recorded
    conn = sqlite3.connect(db)
    try:
        vers = {r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations")}
    finally:
        conn.close()
    assert 5 in vers


def test_migration_5_idempotent_and_preserves_data(tmp_path):
    db = str(tmp_path / "m5b.db")
    migrations.apply_migrations(db)
    rid = store.create_block_rule("No social at work", "app",
                                  "twitter.com", "firm",
                                  days="0,1,2,3,4",
                                  start_time="09:00", end_time="18:00",
                                  path=db)
    store.set_setting("hud_enabled", "0", path=db)
    # Re-run: nothing duplicated, nothing lost.
    migrations.apply_migrations(db)
    rules = store.get_block_rules(path=db)
    assert len(rules) == 1 and rules[0]["id"] == rid
    assert rules[0]["days"] == "0,1,2,3,4"
    assert store.get_setting("hud_enabled", path=db) == "0"  # kept


def test_migration_5_upgrades_v4_database(tmp_path):
    # A v1.7.0 database (migrated to 4) gains the new tables on upgrade.
    db = str(tmp_path / "m5c.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER "
                     "PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT "
                     "NOT NULL)")
        for v, name in [(1, "0001_afk_intervals"),
                        (2, "0002_shield_columns"),
                        (3, "0003_flowtime_pomodoro"),
                        (4, "0004_projects_budget_billable")]:
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?)",
                (v, name, "2026-01-01T00:00:00"))
        conn.execute("CREATE TABLE block_rules_old_marker (id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    migrations.apply_migrations(db)
    tables = _tables(db)
    assert "block_rules" in tables
    assert "block_passes" in tables


def test_block_rule_crud_validation(tmp_path):
    import pytest
    db = str(tmp_path / "m5d.db")
    migrations.apply_migrations(db)
    with pytest.raises(ValueError):
        store.create_block_rule("", "app", "x.exe", "soft", path=db)
    with pytest.raises(ValueError):
        store.create_block_rule("n", "bogus", "x.exe", "soft", path=db)
    with pytest.raises(ValueError):
        store.create_block_rule("n", "app", "x.exe", "nuke", path=db)
    with pytest.raises(ValueError):
        store.create_block_rule("n", "app", "x.exe", "soft",
                                start_time="25:00", path=db)
    rid = store.create_block_rule("Evening games", "category", "gaming",
                                  "hardcore", path=db)
    assert store.set_block_rule_enabled(rid, False, path=db)
    assert store.get_block_rules(path=db)[0]["enabled"] == 0
    assert store.get_block_rules(path=db, only_enabled=True) == []
    assert store.set_block_rule_enabled(rid, True, path=db)
    assert store.delete_block_rule(rid, path=db)
    assert store.get_block_rules(path=db) == []
    assert not store.delete_block_rule(999999, path=db)
