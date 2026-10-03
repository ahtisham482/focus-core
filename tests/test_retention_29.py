"""Roadmap 2.9: data-retention controls + erasure path.

Owner policy (2026-10-02): keep 12 months of detailed activity by
default (options 3/6/12/24 months or keep forever); daily totals
(day_stats) kept forever; invoices/timesheets never auto-deleted;
manual erase-all button exists.

All databases are tmp files; the repo dev DB is never touched.
"""

import sqlite3
from datetime import date

from focuscore import retention, store


def _db(tmp_path, name="retention.db"):
    db = str(tmp_path / name)
    store.init_db(db)
    return db


def _row_count(db, table, where="", params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} {where}", params
        ).fetchone()[0]
    finally:
        conn.close()


def _seed_old_and_new(db):
    """Old detail (2024) + recent detail + a money record."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (ts, duration, app, title, day) "
            "VALUES ('2024-05-10 09:00:00', 600.0, 'code.exe', 't', "
            "'2024-05-10')"
        )
        conn.execute(
            "INSERT INTO activities (ts, duration, app, title, day) "
            "VALUES ('2026-09-20 09:00:00', 300.0, 'code.exe', 't', "
            "'2026-09-20')"
        )
        conn.execute(
            "INSERT INTO afk_intervals (day, start_ts, end_ts, "
            "duration_seconds, status) VALUES ('2024-05-10', "
            "'2024-05-10 12:00:00', '2024-05-10 12:30:00', 1800.0, 'afk')"
        )
        conn.execute(
            "INSERT INTO focus_sessions (label, planned_minutes, "
            "started_at, planned_end_at, ended_at, status) VALUES "
            "('old', 25.0, '2024-05-10 09:00:00', "
            "'2024-05-10 09:25:00', '2024-05-10 09:25:00', 'done')"
        )
        conn.execute(
            "INSERT INTO projects (name) VALUES ('Acme')"
        )
        conn.commit()
    finally:
        conn.close()


def test_prune_never_deletes_day_without_day_stats_row(tmp_path):
    """#1 correctness property: a day's detail rows are pruned ONLY if
    that day's day_stats row exists -- computed first when missing.
    Losing detail must never lose history the totals don't cover."""
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        # Old activities with NO day_stats row for that day.
        conn.execute(
            "INSERT INTO activities (ts, duration, app, title, day) "
            "VALUES ('2024-05-10 09:00:00', 600.0, 'code.exe', 't', "
            "'2024-05-10')"
        )
        conn.execute(
            "INSERT INTO afk_intervals (day, start_ts, end_ts, "
            "duration_seconds, status) VALUES ('2024-05-10', "
            "'2024-05-10 12:00:00', '2024-05-10 12:30:00', 1800.0, "
            "'afk')"
        )
        conn.commit()
    finally:
        conn.close()
    assert _row_count(db, "day_stats") == 0

    retention.prune_older_than(12, path=db, today=date(2026, 10, 3))

    # Detail gone...
    assert _row_count(db, "activities") == 0
    assert _row_count(db, "afk_intervals") == 0
    # ...but the day's totals were computed first and kept.
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT afk_seconds, total_seconds FROM day_stats "
            "WHERE day = '2024-05-10'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "day_stats row must exist after prune"
    assert row[0] == 1800.0
    assert row[1] == 600.0


def test_prune_deletes_old_detail_keeps_recent_and_counts(tmp_path):
    db = _db(tmp_path)
    _seed_old_and_new(db)
    # day_stats row exists for the old day (normal pipeline case).
    store.save_day_stats("2024-05-10", 1800.0, 600.0, path=db)

    counts = retention.prune_older_than(12, path=db,
                                       today=date(2026, 10, 3))

    assert counts["activities"] == 1
    assert counts["afk_intervals"] == 1
    assert counts["focus_sessions"] == 1
    # Recent detail kept.
    assert _row_count(db, "activities") == 1
    conn = sqlite3.connect(db)
    try:
        day = conn.execute("SELECT day FROM activities").fetchone()[0]
    finally:
        conn.close()
    assert day == "2026-09-20"
    # Cutoff day itself is kept ("older than", not "older-or-equal").
    assert _row_count(db, "day_stats") == 1


def test_prune_never_touches_totals_or_money(tmp_path):
    db = _db(tmp_path)
    _seed_old_and_new(db)
    store.save_day_stats("2024-05-10", 1800.0, 600.0, path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO timesheet_entries (day, start_ts, end_ts, "
            "minutes, category, project_id) VALUES ('2024-05-10', "
            "'2024-05-10T09:00', '2024-05-10T10:00', 60.0, 'Work', 1)"
        )
        conn.commit()
    finally:
        conn.close()

    retention.prune_older_than(3, path=db, today=date(2026, 10, 3))

    assert _row_count(db, "day_stats") == 1
    assert _row_count(db, "timesheet_entries") == 1
    assert _row_count(db, "projects") == 1


def test_prune_forever_is_noop_and_invalid_setting_defaults(tmp_path):
    db = _db(tmp_path)
    _seed_old_and_new(db)

    assert retention.prune_older_than(None, path=db,
                                      today=date(2026, 10, 3)) == {}
    assert _row_count(db, "activities") == 2

    assert retention.get_retention_months(path=db) == 12  # default
    assert retention.set_retention_months(None, path=db) is True
    assert retention.get_retention_months(path=db) is None
    assert retention.set_retention_months(6, path=db) is True
    assert retention.get_retention_months(path=db) == 6
    assert retention.set_retention_months(99, path=db) is False
    assert retention.get_retention_months(path=db) == 6  # unchanged
    store.set_setting("retention_months", "bogus", path=db)
    assert retention.get_retention_months(path=db) == 12  # default


def test_cutoff_clamps_end_of_month(tmp_path):
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO activities (ts, duration, app, title, day) "
            "VALUES ('2026-02-27 09:00:00', 60.0, 'a', 't', '2026-02-27')"
        )
        conn.execute(
            "INSERT INTO activities (ts, duration, app, title, day) "
            "VALUES ('2026-02-28 09:00:00', 60.0, 'a', 't', '2026-02-28')"
        )
        conn.commit()
    finally:
        conn.close()
    # 2026-03-31 minus 1 month -> 2026-02-28 (clamped, not Mar 3).
    counts = retention.prune_older_than(1, path=db,
                                       today=date(2026, 3, 31))
    assert counts["activities"] == 1
    assert _row_count(db, "activities") == 1


def test_erase_all_wipes_everything_but_schema(tmp_path):
    db = _db(tmp_path)
    _seed_old_and_new(db)
    store.save_day_stats("2024-05-10", 1800.0, 600.0, path=db)
    store.set_setting("ui_theme", "dark", path=db)
    from focuscore import invoices as invoices_mod
    pid = store.add_project("Acme", path=db)
    eid = store.create_entry("2024-05-10", "2024-05-10T09:00",
                             "2024-05-10T10:00", 60.0, "Work",
                             project_id=pid, task="Dev",
                             status="accepted", path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET hourly_rate_minor = 10000, "
            "rate_currency = 'USD', rate_status = 'confirmed' "
            "WHERE id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()
    invoices_mod.create_invoice(pid, "2024-05-10", "2024-05-24",
                                entry_ids=[eid], path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO goals (name, direction, target_type, "
            "target_name) VALUES ('g', 'at_least', 'minutes', 'x')"
        )
        sid = conn.execute(
            "SELECT id FROM focus_sessions LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO xp_ledger (session_id, day, base_xp, total_xp, "
            "awarded_at) VALUES (?, '2024-05-10', 10, 10, "
            "'2024-05-10 09:00:00')", (sid,)
        )
        conn.commit()
    finally:
        conn.close()

    retention.erase_all_data(path=db)

    for table in retention.ERASE_TABLES:
        assert _row_count(db, table) == 0, table
    # Cascade-covered tables too.
    for table in ("invoice_lines", "invoice_payments",
                  "project_budget_ledger", "focus_blocks",
                  "session_cycles"):
        assert _row_count(db, table) == 0, table
    # Schema itself survives.
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert "activities" in tables
    assert "settings" in tables
    # Settings read back as defaults.
    assert store.get_setting("ui_theme", "system", path=db) == "system"
    assert retention.get_retention_months(path=db) == 12


def test_maybe_prune_never_raises(tmp_path):
    # A path that cannot be a database (a directory) must not break
    # startup: the error is swallowed and reported as no work done.
    assert retention.maybe_prune(path=str(tmp_path)) == {}
    db = _db(tmp_path)
    store.set_setting("retention_months", "forever", path=db)
    assert retention.maybe_prune(path=db) == {}


def test_prune_classification_block_passes_pruned_config_kept(tmp_path):
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO block_passes (started_at, minutes, reason) "
            "VALUES ('2024-05-10 09:00:00', 15.0, 'break')"
        )
        conn.execute(
            "INSERT INTO alerts (name, target_type, target_name, "
            "threshold_minutes, created_at) VALUES ('a', 'app', 'x', "
            "60.0, '2024-05-10 09:00:00')"
        )
        conn.execute(
            "INSERT INTO block_rules (name, rule_type, key, action) "
            "VALUES ('r', 'app', 'x.exe', 'soft')"
        )
        conn.execute(
            "INSERT INTO orphaned_rows (source_table, row_json, "
            "quarantined_at) VALUES ('t', '{}', '2024-05-10 09:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    retention.prune_older_than(12, path=db, today=date(2026, 10, 3))

    assert _row_count(db, "block_passes") == 0  # raw detail: pruned
    assert _row_count(db, "alerts") == 1  # user config: kept
    assert _row_count(db, "block_rules") == 1  # user config: kept
    # Quarantine is integrity evidence, not activity detail: kept.
    assert _row_count(db, "orphaned_rows") == 1


# ------------------------------------------------------------ routes ---

def test_retention_setting_route_saves_valid_values(client):
    c, db = client
    for value, expected in (("3", 3), ("6", 6), ("12", 12), ("24", 24),
                            ("forever", None)):
        resp = c.post("/settings/retention",
                      data={"retention_months": value})
        assert resp.status_code in (301, 302, 303)
        assert retention.get_retention_months(path=db) == expected


def test_retention_setting_route_rejects_invalid_values(client):
    c, db = client
    assert retention.get_retention_months(path=db) == 12
    for bad in ("99", "never", "", "-3"):
        resp = c.post("/settings/retention",
                      data={"retention_months": bad})
        assert resp.status_code in (301, 302, 303)
        # Unchanged: still the default.
        assert retention.get_retention_months(path=db) == 12


def test_erase_route_requires_exact_confirmation_word(client):
    c, db = client
    store.set_setting("ui_theme", "dark", path=db)
    for bad in ("erase", "ERASE ", "DELETE", ""):
        resp = c.post("/backup/erase", data={"confirmation": bad})
        assert resp.status_code in (301, 302, 303)
        # Nothing wiped.
        assert store.get_setting("ui_theme", "system", path=db) == "dark"


def test_erase_route_wipes_on_exact_confirmation(client):
    c, db = client
    store.set_setting("ui_theme", "dark", path=db)
    store.save_day_stats("2026-09-20", 10.0, 100.0, path=db)
    resp = c.post("/backup/erase", data={"confirmation": "ERASE"})
    assert resp.status_code in (301, 302, 303)
    assert _row_count(db, "day_stats") == 0
    assert store.get_setting("ui_theme", "system", path=db) == "system"


def test_backup_page_shows_retention_and_erase_sections(client):
    c, _db = client
    html = c.get("/backup").data.decode()
    assert "Data retention" in html
    assert "retention_months" in html
    assert "Erase all my data" in html
    assert "ERASE" in html


def test_prune_session_with_cycles_deletes_children_first(tmp_path):
    """Objection 1 (blocking): a real completed focus session HAS
    session_cycles, and session_cycles.session_id REFERENCES
    focus_sessions(id) with no ON DELETE action. Deleting
    focus_sessions first violates the FK, prune_older_than raises,
    and maybe_prune() swallows it -- so startup pruning silently never
    runs for real users. The realistic shape must prune cleanly."""
    db = _db(tmp_path)
    store.save_day_stats("2024-05-10", 1800.0, 600.0, path=db)
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO focus_sessions (label, planned_minutes, "
            "started_at, planned_end_at, ended_at, status) VALUES "
            "('old', 25.0, '2024-05-10 09:00:00', "
            "'2024-05-10 09:25:00', '2024-05-10 09:25:00', 'done')"
        )
        sid = cur.lastrowid
        conn.execute(
            "INSERT INTO session_cycles (session_id, kind, "
            "planned_minutes, started_at) VALUES (?, 'work', 25.0, "
            "'2024-05-10 09:00:00')", (sid,)
        )
        conn.execute(
            "INSERT INTO xp_ledger (session_id, day, base_xp, total_xp, "
            "awarded_at) VALUES (?, '2024-05-10', 10, 10, "
            "'2024-05-10 09:00:00')", (sid,)
        )
        conn.commit()
    finally:
        conn.close()

    # Must not raise sqlite3.IntegrityError.
    counts = retention.prune_older_than(12, path=db,
                                       today=date(2026, 10, 3))

    assert counts["focus_sessions"] == 1
    assert counts["session_cycles"] == 1
    assert _row_count(db, "focus_sessions") == 0
    assert _row_count(db, "session_cycles") == 0
    # xp_ledger rows are session detail: CASCADE drops them with the
    # session (documented on the backup page, not changed here).
    assert _row_count(db, "xp_ledger") == 0
    # And the day's totals survive the prune.
    assert _row_count(db, "day_stats") == 1


def test_erase_all_data_clears_fallback_pass_file(tmp_path, monkeypatch):
    """Objection 2: 'Erase all my data' must not leave the emergency
    pass file behind -- it holds real activity data written when
    SQLite was unreachable."""
    from focuscore import shield as shield_mod

    db = _db(tmp_path)
    fake_pass_file = tmp_path / "passes.fallback.jsonl"
    fake_pass_file.write_text(
        '{"started_at": "2024-05-10 09:00:00", "minutes": 15.0, '
        '"reason": "db locked"}\n'
    )
    monkeypatch.setattr(shield_mod, "fallback_passes_path",
                        lambda: str(fake_pass_file))

    retention.erase_all_data(path=db)

    assert not fake_pass_file.exists()
    for table in retention.ERASE_TABLES:
        assert _row_count(db, table) == 0, table


def test_erase_all_data_without_pass_file_does_not_raise(tmp_path,
                                                         monkeypatch):
    """The fallback file usually doesn't exist -- erasing must still
    succeed."""
    from focuscore import shield as shield_mod

    db = _db(tmp_path)
    missing = tmp_path / "passes.fallback.jsonl"
    assert not missing.exists()
    monkeypatch.setattr(shield_mod, "fallback_passes_path",
                        lambda: str(missing))

    retention.erase_all_data(path=db)  # must not raise


def test_erase_confirmation_list_covers_every_wiped_table():
    """Objection 3: the 'This will permanently delete' list must be
    exhaustive -- every table erase_all_data() wipes has to appear in
    exactly one bullet, so the UI can never silently omit a wiped
    table (or list one that isn't wiped)."""
    bullets = retention.ERASE_BULLETS
    covered = [t for _label, tables in bullets for t in tables]
    assert sorted(covered) == sorted(retention.ERASE_TABLES)
    assert len(covered) == len(set(covered)), "a table is listed twice"


def test_erase_confirmation_renders_all_bullets(client):
    """The confirmation screen shows every bullet from ERASE_BULLETS."""
    c, _db = client
    html = c.get("/backup").data.decode()
    for label, _tables in retention.ERASE_BULLETS:
        assert label in html, label


def test_retention_card_explains_lifetime_xp_covers_kept_history(client):
    """Objection 4: pruning old focus_sessions CASCADE-drops their
    xp_ledger rows, so the Focus page's 'lifetime XP' number can
    shrink after the first prune. The retention card must say so
    plainly instead of letting the user discover it by surprise."""
    c, _db = client
    html = c.get("/backup").data.decode()
    assert "lifetime XP" in html
    assert "kept history" in html


def test_startup_paths_call_maybe_prune():
    """Objection 5: the 'at app startup' claim needs a pin on the
    wiring -- the two lines that trigger pruning could otherwise be
    deleted without any test failing."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    launcher_src = (root / "focuscore" / "launcher.py").read_text()
    tray_src = (root / "focuscore" / "tray.py").read_text()
    # The wrapper exists...
    assert "def maybe_prune():" in launcher_src
    # ...the no-tray startup path calls it before the server starts...
    main_body = launcher_src.split("def main(")[1]
    assert "maybe_prune()" in main_body
    assert (main_body.index("maybe_prune()")
            < main_body.index("ensure_server"))
    # ...and the tray startup path does the same.
    assert "launcher.maybe_prune()" in tray_src
    i = tray_src.index("launcher.maybe_prune()")
    assert i < tray_src.index("self.ensure_server()", i)


def test_erase_all_data_clears_crash_files(tmp_path, monkeypatch):
    """Product call: 'erase all my data' is complete -- crash diagnostics
    (last-crash.json, crash-pending.json) are data too, and the typed
    ERASE confirmation supersedes any pending crash offer."""
    from focuscore import crashreport as crash_mod

    db = _db(tmp_path)
    crash_dir = tmp_path / "crash"
    crash_dir.mkdir()
    (crash_dir / crash_mod.CRASH_NAME).write_text('{"t": 1}')
    (crash_dir / crash_mod.PENDING_NAME).write_text('{"t": 2}')
    monkeypatch.setattr(crash_mod, "_folder", lambda: crash_dir)

    retention.erase_all_data(path=db)

    assert not (crash_dir / crash_mod.CRASH_NAME).exists()
    assert not (crash_dir / crash_mod.PENDING_NAME).exists()
