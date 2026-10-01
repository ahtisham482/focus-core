"""Roadmap 1.19: format-stability contract (FORMAT.md, aged fixture,
export round-trip).

Roadmap 1.7 proved forward migration *mechanics*; this file judges the
contract *across time*:

* the checked-in schema-version-1 fixture (``tests/fixtures/
  aged_schema_v1.db``, frozen bytes, sentinel rows) opens through the
  app's normal store path and migrates to the current version with its
  rows intact;
* the three timesheet export formats re-import without loss on the
  fields that matter — JSON exactly, CSV/HTML up to the rounding
  FORMAT.md declares (never silently);
* FORMAT.md documents the real on-disk schema (every table) and the
  stability policy.

Everything runs against TMP copies only, never the repo dev DB.
"""

import csv
import json
import shutil
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import pytest

from focuscore import backup, columncrypto, exports, migrations, store

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "aged_schema_v1.db"
FORMAT_PATH = REPO_ROOT / "FORMAT.md"

AGED_SCHEMA_VERSION = 1
AGED_DAY = "2026-01-15"
SENTINEL_TITLE = "Sentinel Title — café ☕ (Alpha)"
SENTINEL_URL = "https://sentinel.example/path?q=1&x=2"


# --------------------------------------------------------- aged fixture --


def test_aged_fixture_file_is_schema_version_1():
    """The checked-in fixture really is an old (pre-0010, pre-0002)
    database: user_version 1, only migration 0001 recorded, plaintext
    titles, and no tables that arrived in later versions."""
    assert FIXTURE_PATH.exists(), f"aged fixture missing: {FIXTURE_PATH}"
    conn = sqlite3.connect(f"file:{FIXTURE_PATH}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == (
            AGED_SCHEMA_VERSION
        )
        recorded = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert recorded == [(1, "0001_afk_intervals")]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # Tables added by migrations 0007-0011 must NOT exist yet.
        for later_table in ("invoices", "badges", "orphaned_rows",
                            "block_rules", "session_cycles", "settings"):
            assert later_table not in tables
        (raw_title,) = conn.execute(
            "SELECT title FROM activities"
        ).fetchone()
        assert raw_title == SENTINEL_TITLE  # still plaintext at v1
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()


@pytest.fixture()
def aged_copy(tmp_path, monkeypatch):
    """A disposable copy of the aged fixture + contained backups dir."""
    target = tmp_path / "aged.db"
    shutil.copyfile(FIXTURE_PATH, target)
    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(backup, "backup_dir", lambda dest_dir=None: bdir)
    return target


def test_aged_v1_database_migrates_through_init_db(aged_copy):
    """The across-time contract: store.init_db (the app's normal open
    path) migrates the v1 fixture to the current schema and every
    sentinel row survives — including titles sealed by migration 0010
    and decrypted back by the normal read paths."""
    store.init_db(str(aged_copy))

    conn = sqlite3.connect(str(aged_copy))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == (
            migrations.LATEST_VERSION
        )
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        assert versions == list(range(1, migrations.LATEST_VERSION + 1))
        (raw_title,) = conn.execute(
            "SELECT title FROM activities"
        ).fetchone()
        (raw_block_title,) = conn.execute(
            "SELECT title FROM focus_blocks"
        ).fetchone()
        (project_row,) = conn.execute(
            "SELECT name, client FROM projects"
        ).fetchall()
        (session_row,) = conn.execute(
            "SELECT label, planned_minutes, enforcement_mode, session_type"
            " FROM focus_sessions"
        ).fetchall()
        (afk_seconds,) = conn.execute(
            "SELECT duration_seconds FROM afk_intervals"
        ).fetchone()
        (stats_row,) = conn.execute(
            "SELECT afk_seconds, total_seconds FROM day_stats"
        ).fetchall()
        (goal_name,) = conn.execute("SELECT name FROM goals").fetchone()
        (firing,) = conn.execute(
            "SELECT current_minutes FROM alert_firings"
        ).fetchone()
    finally:
        conn.close()

    ok, problems = store.run_integrity_check(str(aged_copy))
    assert (ok, problems) == (True, [])

    # Migration 0010 sealed the plaintext titles at rest...
    assert columncrypto.is_protected(raw_title)
    assert raw_title != SENTINEL_TITLE
    assert columncrypto.is_protected(raw_block_title)
    assert columncrypto.unprotect_text(raw_block_title) == (
        "Distractor Title"
    )

    # ...and the normal read paths hand the original values back.
    activities = store.get_day_activities(AGED_DAY, path=str(aged_copy))
    assert len(activities) == 1
    act = activities[0]
    assert act["title"] == SENTINEL_TITLE
    assert act["url"] == SENTINEL_URL
    assert act["app"] == "SentinelApp.exe"
    assert act["duration"] == 900.0
    assert act["score"] == 2

    assert project_row == ("Sentinel Project Alpha", "Sentinel Client")
    # enforcement_mode/session_type arrived via later migrations with
    # their documented defaults for pre-existing rows.
    assert session_row == ("Sentinel Session", 25.0, "strict", "classic")
    assert afk_seconds == 720.0
    assert stats_row == (600.0, 3600.0)
    assert goal_name == "Sentinel Goal"
    assert firing == 20.5
    assert store.get_overrides(path=str(aged_copy)) == {"sentinelapp": 2}

    rows = exports.build_export_rows(
        AGED_DAY,
        AGED_DAY,
        include_app_details=True,
        path=str(aged_copy),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["duration_seconds"] == 5400  # 90 recorded minutes
    assert row["project"] == "Sentinel Project Alpha"
    assert row["client"] == "Sentinel Client"
    assert row["task"] == "Sentinel task"
    assert row["title"] == SENTINEL_TITLE
    # Finance columns did not exist at v1: honest "unknown", never a
    # silently invented amount.
    assert row["billable"] is True
    assert row["rate_status"] == "unknown"
    assert row["amount_minor"] is None


# ------------------------------------------------------- export round trip --

EXPORT_DAY = "2026-02-03"

# (start, end, minutes, category, app, title, task, note, billable,
#  hourly_rate_minor, rate_status)
SEED_ENTRIES = [
    ("2026-02-03T09:00:00", "2026-02-03T10:30:00", 90.0, "Dev",
     "Code.exe", "Quarterly plan — café ☕", 'Write report, "final" draft',
     "note with <angle> & ampersand", 1, 6000, "confirmed"),
    ("2026-02-03T10:45:00", "2026-02-03T11:10:00", 25.0, "Dev",
     "Code.exe", "", "=SUM(A1:A9)", "", 1, 6000, "estimated"),
    ("2026-02-03T11:15:00", "2026-02-03T11:35:00", 20.0, "Support",
     "Mail.exe", "", "Support call <script> alert", "", 1, None,
     "unknown"),
    ("2026-02-03T12:00:00", "2026-02-03T12:45:00", 45.0, "Dev",
     "Mail.exe", "", "Internal admin", "", 0, 6000, "confirmed"),
]
# Integer seconds / amounts a faithful re-import must reproduce.
EXPECTED_SECONDS = [5400, 1500, 1200, 2700]
EXPECTED_AMOUNTS = [9000, 2500, None, None]


@pytest.fixture()
def export_db(tmp_path):
    db = str(tmp_path / "export.db")
    store.init_db(db)
    conn = store.get_db(db)
    try:
        conn.execute(
            "INSERT INTO projects (id, name, client, created_at) VALUES "
            "(1, 'Project Alpha', 'Alpha Corp', '2026-02-01T08:00:00')"
        )
        for (start_ts, end_ts, minutes, category, app, title, task, note,
             billable, rate_minor, rate_status) in SEED_ENTRIES:
            conn.execute(
                "INSERT INTO timesheet_entries (day, start_ts, end_ts, "
                "minutes, category, app, title, project_id, task, note, "
                "status, locked, created_at, is_billable, "
                "hourly_rate_minor, rate_currency, rate_status) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'accepted', 0, ?, ?, ?, "
                "'USD', ?)",
                (EXPORT_DAY, start_ts, end_ts, minutes, category, app,
                 title, task, note, end_ts, billable, rate_minor,
                 rate_status),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def _build_rows(export_db, **kwargs):
    rows = exports.build_export_rows(
        EXPORT_DAY, EXPORT_DAY, path=export_db, **kwargs
    )
    assert [r["duration_seconds"] for r in rows] == EXPECTED_SECONDS
    assert [r["amount_minor"] for r in rows] == EXPECTED_AMOUNTS
    return rows


def test_export_roundtrip_json_is_exact(export_db):
    """JSON (schema focuscore.timesheet/v1) is the lossless format:
    integer seconds and integer minor units re-import identically,
    including text that other formats must sanitize."""
    rows = _build_rows(export_db, include_notes=True)
    totals = exports.compute_totals(rows)
    filters = {"from": EXPORT_DAY, "to": EXPORT_DAY}
    manifest = exports.redaction_manifest(include_notes=True)
    payload = exports.build_json_payload(rows, totals, filters, manifest)
    parsed = json.loads(json.dumps(payload))

    assert parsed["schema"] == exports.SCHEMA_VERSION == (
        "focuscore.timesheet/v1"
    )
    assert parsed["totals"]["seconds_total"] == 10800
    assert parsed["totals"]["seconds_billable"] == 8100
    assert parsed["totals"]["confirmed_minor"] == 9000
    assert parsed["totals"]["estimated_minor"] == 2500
    assert parsed["totals"]["unknown_billable_seconds"] == 1200
    assert parsed["totals"]["entries_without_rate"] == 1

    json_keys = {
        "day", "start_ts", "end_ts", "duration_seconds", "project",
        "client", "task", "note", "category", "billable",
        "hourly_rate_minor", "rate_currency", "rate_status",
        "amount_minor",
    }
    assert len(parsed["entries"]) == len(SEED_ENTRIES)
    for entry, seed, seconds, amount in zip(
        parsed["entries"], SEED_ENTRIES, EXPECTED_SECONDS, EXPECTED_AMOUNTS
    ):
        (start_ts, end_ts, _minutes, category, _app, _title, task,
         note, billable, rate_minor, rate_status) = seed
        assert set(entry) == json_keys  # no app/title in JSON, ever
        assert entry["day"] == EXPORT_DAY
        assert entry["start_ts"] == start_ts
        assert entry["end_ts"] == end_ts
        assert entry["duration_seconds"] == seconds
        assert isinstance(entry["duration_seconds"], int)
        assert entry["project"] == "Project Alpha"
        assert entry["client"] == "Alpha Corp"
        assert entry["task"] == task  # raw, unsanitized
        assert entry["note"] == note  # opted in above
        assert entry["category"] == category
        assert entry["billable"] is bool(billable)
        assert entry["hourly_rate_minor"] == rate_minor
        assert entry["rate_currency"] == "USD"
        assert entry["rate_status"] == rate_status
        assert entry["amount_minor"] == amount


def test_export_roundtrip_csv_matches_declared_rendering(export_db):
    """Client CSV: exact identity/dates/money; duration is hours with
    two decimals (FORMAT.md declares the rounding — max error 18s, so
    re-import lands within that, never silently exact)."""
    rows = _build_rows(export_db)
    manifest = exports.redaction_manifest()
    text = exports.rows_to_csv(rows, manifest)

    first_line, _, body = text.partition("\n")
    assert first_line.startswith("# manifest: ")
    assert json.loads(first_line[len("# manifest: "):]) == manifest

    parsed = list(csv.reader(body.splitlines()))
    assert parsed[0] == [
        "date", "start", "end", "duration_hours", "project", "client",
        "task", "billable", "hourly_rate", "rate_status", "amount",
    ]
    body_rows = parsed[1:]
    assert len(body_rows) == len(SEED_ENTRIES)
    for cells, seed, seconds in zip(
        body_rows, SEED_ENTRIES, EXPECTED_SECONDS
    ):
        (start_ts, end_ts, _minutes, _category, _app, _title, task,
         _note, billable, _rate, rate_status) = seed
        assert cells[0] == EXPORT_DAY
        assert cells[1] == start_ts
        assert cells[2] == end_ts
        # Declared loss: hours at two decimals, re-import within 18s.
        assert abs(float(cells[3]) * 3600 - seconds) <= 18
        assert cells[4] == "Project Alpha"
        assert cells[5] == "Alpha Corp"
        expected_task = "'" + task if task[:1] in "=+-@" else task
        assert cells[6] == expected_task
        assert cells[7] == ("yes" if billable else "no")
        assert cells[9] == rate_status
    # Money is the format_minor rendering; absent money stays empty,
    # never "0" and never the em-dash placeholder.
    assert [r[8] for r in body_rows] == ["$60.00", "$60.00", "", "$60.00"]
    assert [r[10] for r in body_rows] == ["$90.00", "$25.00", "", ""]
    assert [r[3] for r in body_rows] == ["1.50", "0.42", "0.33", "0.75"]
    # The 0.42h row proves the declared rounding is real (1500s = 0.4166h).
    assert float(body_rows[1][3]) * 3600 != 1500
    # Category, note, app: never in the client CSV.
    assert "category" not in parsed[0]
    assert "note with <angle> & ampersand" not in text


def test_export_roundtrip_detailed_csv_carries_opt_in_fields(export_db):
    """Detailed CSV (explicit internal opt-in): adds app + decrypted
    window title; category and note are still excluded (declared)."""
    rows = _build_rows(
        export_db, include_app_details=True, include_notes=True
    )
    text = exports.rows_to_detailed_csv(rows)
    parsed = list(csv.reader(text.splitlines()))
    assert parsed[0][-2:] == ["app", "window_title"]
    assert "category" not in parsed[0]
    assert parsed[1][-2:] == ["Code.exe", "Quarterly plan — café ☕"]
    assert parsed[3][-2:] == ["Mail.exe", ""]
    assert "note with <angle> & ampersand" not in text


class _TableParser(HTMLParser):
    """Collects table rows as lists of cell texts."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def test_export_roundtrip_html_matches_declared_rendering(export_db):
    """HTML statement: a print rendering. Dates/start/task/hours(2dp)/
    billable/rate/amount survive as text; end time, category, note and
    entry ids are declared omissions, and money that does not exist is
    an em dash, never a fabricated zero."""
    rows = _build_rows(export_db)
    totals = exports.compute_totals(rows)
    manifest = exports.redaction_manifest()
    filters = {"from": EXPORT_DAY, "to": EXPORT_DAY, "client": "Alpha Corp"}
    page = exports.rows_to_statement_html(
        rows, totals, filters, manifest, currency="USD",
        project_name="Project Alpha", client_name="Alpha Corp",
    )
    parser = _TableParser()
    parser.feed(page)
    table = parser.rows

    header = [
        "Date", "Start", "Task", "Hours", "Billable", "Rate", "Amount",
    ]
    assert header in table
    i = table.index(header)
    assert table[i + 1] == [
        EXPORT_DAY, SEED_ENTRIES[0][0], SEED_ENTRIES[0][6], "1.50",
        "yes", "$60.00", "$90.00",
    ]
    assert table[i + 2] == [
        EXPORT_DAY, SEED_ENTRIES[1][0], "=SUM(A1:A9)", "0.42", "yes",
        "$60.00", "$25.00",
    ]
    assert table[i + 3] == [
        EXPORT_DAY, SEED_ENTRIES[2][0], "Support call <script> alert",
        "0.33", "yes", "—", "—",
    ]
    assert table[i + 4] == [
        EXPORT_DAY, SEED_ENTRIES[3][0], "Internal admin", "0.75", "no",
        "$60.00", "—",
    ]
    day_total = table[i + 5]
    assert day_total[0] == "Day total — 2026-02-03"
    assert day_total[1] == "3.00"
    assert day_total[-1] == "$115.00"

    assert ["Tracked time", "3h 00m"] in table
    assert ["Confirmed billable", "$90.00"] in table
    assert ["Estimated (marked)", "$25.00"] in table
    assert ["Unrated time (excluded from totals)", "20m"] in table

    # All user text is escaped; nothing is silently dropped that HTML
    # declares it carries, and declared omissions really are absent.
    assert "&lt;script&gt;" in page
    assert "<script> alert" not in page
    assert SEED_ENTRIES[0][1] not in page  # end time: not rendered
    assert "note with <angle> & ampersand" not in page


# ------------------------------------------------------------- FORMAT.md --


def test_format_md_documents_the_real_schema_and_policy(tmp_path):
    """FORMAT.md (repo root) names the current schema version, carries
    the stability policy, describes the aged fixture and the export
    schema, and mentions every real table — a stranger could find
    each table's home from the document alone."""
    assert FORMAT_PATH.exists(), "FORMAT.md missing at repo root"
    text = FORMAT_PATH.read_text(encoding="utf-8")

    assert f"user_version = {migrations.LATEST_VERSION}" in text
    assert "remain readable by every later version" in text
    assert "ships with a converter" in text
    assert "focuscore.timesheet/v1" in text
    assert "aged_schema_v1.db" in text
    assert "user_version reads 1" in text
    assert "dpapi:v1:" in text
    assert "two decimal places" in text

    db = str(tmp_path / "fresh.db")
    store.init_db(db)
    conn = sqlite3.connect(db)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name != 'sqlite_sequence' ORDER BY name"
            )
        ]
    finally:
        conn.close()
    assert tables, "fresh DB has no tables (test setup broken)"
    missing = [name for name in tables if name not in text]
    assert not missing, f"FORMAT.md does not document tables: {missing!r}"
    for migration in migrations.MIGRATIONS:
        assert migration.name in text, (
            f"FORMAT.md omits migration {migration.name}"
        )
