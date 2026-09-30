"""SQLite store. The database location is resolved by focuscore.paths
(portable: next to the code; installed: per-user data folder).

All paths are derived from this file's location, so the project works
wherever the folder is placed -- no absolute paths anywhere.
"""

import sqlite3
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from . import columncrypto, paths

# Roadmap 0.3: log failures that used to be swallowed silently.
logger = logging.getLogger(__name__)


def __getattr__(name):
    # Roadmap 1.7 (PEP 562, the 1.6 pattern): DEFAULT_DB_PATH is
    # resolved lazily on each access instead of being frozen at
    # import, so paths.data_dir() grandfathering (an existing
    # focuscore.db next to the code) is evaluated when the path is
    # actually needed, not when this module happened to be imported.
    # Anything that assigns store.DEFAULT_DB_PATH (tests monkeypatch
    # it) lands in the module dict and wins over this fallback.
    if name == "DEFAULT_DB_PATH":
        return paths.db_path()
    raise AttributeError(
        "module %r has no attribute %r" % (__name__, name))


def __dir__():
    return sorted(set(globals()) | {"DEFAULT_DB_PATH"})


def _resolve_db_path(path=None):
    """The DB path for this call: explicit arg, else an assigned
    DEFAULT_DB_PATH (monkeypatched), else paths.db_path() now."""
    if path is not None:
        return path
    assigned = globals().get("DEFAULT_DB_PATH")
    if assigned is not None:
        return assigned
    return paths.db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    ts TEXT,
    duration REAL,
    app TEXT,
    title TEXT,
    url TEXT,
    category TEXT,
    score INTEGER,
    override_score INTEGER,
    match_key TEXT,
    day TEXT
);
CREATE INDEX IF NOT EXISTS idx_activities_day ON activities(day);

CREATE TABLE IF NOT EXISTS overrides (
    match_key TEXT PRIMARY KEY,
    score INTEGER
);

CREATE TABLE IF NOT EXISTS categories (
    name TEXT PRIMARY KEY,
    parent TEXT,
    score INTEGER,
    custom INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS day_stats (
    day TEXT PRIMARY KEY,
    afk_seconds REAL DEFAULT 0,
    total_seconds REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    direction TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_name TEXT,
    threshold_minutes REAL,
    threshold_pulse REAL,
    pinned INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_name TEXT NOT NULL,
    threshold_minutes REAL NOT NULL,
    message TEXT DEFAULT '',
    cooldown_minutes REAL DEFAULT 60,
    enabled INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS alert_firings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    fired_at TEXT NOT NULL,
    current_minutes REAL
);
CREATE INDEX IF NOT EXISTS idx_firings_alert ON alert_firings(alert_id);

CREATE TABLE IF NOT EXISTS focus_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    planned_minutes REAL NOT NULL,
    started_at TEXT NOT NULL,
    planned_end_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    block_level TEXT NOT NULL DEFAULT 'strict'
);

CREATE TABLE IF NOT EXISTS focus_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    app TEXT,
    title TEXT,
    url TEXT,
    score INTEGER,
    category TEXT
);
CREATE INDEX IF NOT EXISTS idx_blocks_session ON focus_blocks(session_id);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    client TEXT DEFAULT '',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS timesheet_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL,
    minutes REAL NOT NULL,
    category TEXT NOT NULL,
    app TEXT DEFAULT '',
    title TEXT DEFAULT '',
    project_id INTEGER,
    task TEXT DEFAULT '',
    note TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'accepted',
    locked INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts_entries_day ON timesheet_entries(day);
"""


def get_db(path=None):
    conn = sqlite3.connect(str(_resolve_db_path(path)))
    # Roadmap 1.7: FK enforcement is per-connection in SQLite. Safe
    # because migration 0011 (orphan quarantine) always runs via
    # init_db/apply_migrations before any write through here.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def run_integrity_check(path=None):
    """Nightly integrity probe (roadmap 1.7): ``(ok, problems)``.

    Runs PRAGMA integrity_check + foreign_key_check on a read-only
    connection. Never raises: any failure (missing/corrupt file,
    driver error) comes back as ``(False, [problem, ...])``.
    """
    try:
        target = _resolve_db_path(path)
        if str(target) == ":memory:":
            return False, ["no database file to check (in-memory)"]
        if not Path(str(target)).exists():
            return False, ["database file missing: %s" % target]
        conn = sqlite3.connect("file:%s?mode=ro" % target, uri=True)
        try:
            problems = []
            for row in conn.execute("PRAGMA integrity_check"):
                if str(row[0]).lower() != "ok":
                    problems.append("integrity_check: %s" % row[0])
            for row in conn.execute("PRAGMA foreign_key_check"):
                problems.append(
                    "foreign_key_check: %s row %s references "
                    "missing %s" % (row[0], row[1], row[2]))
            return (not problems), problems
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- probe must never raise
        return False, ["integrity check failed: %s" % exc]


def checkpoint_wal(path=None):
    """Sprint 4 (Qwen item 10): TRUNCATE-checkpoint the WAL on a
    dedicated connection. Called nightly by the supervisor (tray) and
    on graceful shutdown. Keeps the -wal file from growing unboundedly
    when a reader holds a long transaction. Returns the checkpoint
    result dict, or None on failure (never raises)."""
    try:
        conn = get_db(path)
        try:
            row = conn.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row is not None:
                return {"busy": row[0], "log": row[1],
                        "checkpointed": row[2]}
            return {"busy": 0, "log": 0, "checkpointed": 0}
        finally:
            conn.close()
    except Exception:
        return None


def init_db(path=None):
    from . import migrations

    resolved = _resolve_db_path(path)
    # Roadmap 1.7: when the migrations-applied flag says this process
    # already migrated this exact file, skip everything below --
    # nothing opens a connection just to re-check.
    _version, opened = migrations.apply_migrations_with_status(resolved)
    if not opened:
        return
    # Sprint 4 (Qwen item 12): idempotent index for the invoice_lines
    # -> timesheet_entries FK. No user_version bump (indexes don't
    # change semantics); IF NOT EXISTS makes it safe to run on every
    # startup.
    try:
        conn = get_db(resolved)
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_invoice_lines_entry "
                "ON invoice_lines(timesheet_entry_id)")
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Expected on very old DBs (the invoice_lines table may not
        # exist yet; migration 8 creates it, and the next init_db
        # adds the index) -- hence DEBUG, not silence.
        logger.debug("invoice-line index not added yet: %s", exc)



def save_events(day, events, path=None):
    """Store one day's categorized events (idempotent: replaces the day)."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("DELETE FROM activities WHERE day = ?", (day,))
        conn.executemany(
            "INSERT INTO activities "
            "(ts, duration, app, title, url, category, score, "
            " override_score, match_key, day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    e.get("ts"), e.get("duration", 0), e.get("app", ""),
                    columncrypto.protect_text(e.get("title", "")),
                    columncrypto.protect_text(e.get("url")),
                    e.get("category", "Uncategorized"),
                    e.get("score", 0), e.get("override_score"), e.get("match_key", ""),
                    day,
                )
                for e in events
            ],
        )
        conn.commit()
    finally:
        conn.close()


def save_day_stats(day, afk_seconds, total_seconds, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO day_stats (day, afk_seconds, total_seconds) "
            "VALUES (?, ?, ?)",
            (day, afk_seconds, total_seconds),
        )
        conn.commit()
    finally:
        conn.close()


def set_override(match_key, score, path=None):
    """Remember a per-activity score override for future pipeline runs."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO overrides (match_key, score) VALUES (?, ?)",
            (match_key, score),
        )
        conn.commit()
    finally:
        conn.close()


def get_overrides(path=None):
    init_db(path)
    conn = get_db(path)
    try:
        return {
            row["match_key"]: row["score"]
            for row in conn.execute("SELECT match_key, score FROM overrides")
        }
    finally:
        conn.close()


def apply_override_to_day(day, match_key, score, path=None):
    """Apply an override to already-stored events of one day (live update)."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE activities SET score = ?, override_score = ? "
            "WHERE day = ? AND match_key = ?",
            (score, score, day, match_key),
        )
        conn.commit()
    finally:
        conn.close()


def add_category(name, parent, score, path=None):
    """Add a custom sub-category (score=None inherits the parent's score)."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO categories (name, parent, score, custom) "
            "VALUES (?, ?, ?, 1)",
            (name, parent, score),
        )
        conn.commit()
    finally:
        conn.close()


def get_categories(path=None):
    """Default categories merged with custom ones (custom wins on name)."""
    from .taxonomy import DEFAULT_CATEGORIES

    merged = {c["name"]: {"score": c["default_score"], "parent": None}
              for c in DEFAULT_CATEGORIES}
    init_db(path)
    conn = get_db(path)
    try:
        for row in conn.execute("SELECT name, parent, score FROM categories"):
            merged[row["name"]] = {"score": row["score"], "parent": row["parent"]}
    finally:
        conn.close()
    return merged


def get_day_activities(day, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        rows = []
        for row in conn.execute(
            "SELECT ts, duration, app, title, url, category, score, "
            "       override_score, match_key "
            "FROM activities WHERE day = ? ORDER BY ts",
            (day,),
        ):
            opened = dict(row)
            opened["title"] = columncrypto.unprotect_text(opened["title"])
            opened["url"] = columncrypto.unprotect_text(opened["url"])
            rows.append(opened)
        return rows
    finally:
        conn.close()


def get_activities_range(start_ts, end_ts, path=None):
    """Bounded time-range scan (Phase 11, council remediation).

    Uses idx_activities_ts -- no full-day scans, no unbounded LIMIT.
    start_ts/end_ts are ISO strings; returns rows ORDER BY ts ASC.
    """
    init_db(path)
    conn = get_db(path)
    try:
        rows = []
        for row in conn.execute(
            "SELECT ts, duration, app, title, category, score "
            "FROM activities WHERE ts >= ? AND ts <= ? "
            "ORDER BY ts ASC",
            (start_ts, end_ts),
        ):
            opened = dict(row)
            opened["title"] = columncrypto.unprotect_text(opened["title"])
            rows.append(opened)
        return rows
    finally:
        conn.close()


def get_day_summary(day, path=None):
    """Aggregate one day.

    Returns {"seconds_by_level": {-2:.., -1:.., 0:.., 1:.., 2:..},
             "seconds_by_category": {name: seconds},
             "uncategorized": [{match_key, app, title, seconds}...],
             "afk_seconds": float, "total_seconds": float}
    """
    init_db(path)
    conn = get_db(path)
    try:
        seconds_by_level = {2: 0.0, 1: 0.0, 0: 0.0, -1: 0.0, -2: 0.0}
        seconds_by_category = {}
        uncat = {}
        for row in conn.execute(
            "SELECT score, category, duration, match_key, app, title "
            "FROM activities WHERE day = ?",
            (day,),
        ):
            seconds = float(row["duration"] or 0)
            score = row["score"] if row["score"] in seconds_by_level else 0
            seconds_by_level[score] += seconds
            seconds_by_category[row["category"]] = (
                seconds_by_category.get(row["category"], 0.0) + seconds
            )
            if row["category"] == "Uncategorized":
                key = row["match_key"] or "app:unknown"
                title = columncrypto.unprotect_text(row["title"])
                entry = uncat.setdefault(
                    key, {"match_key": key, "app": row["app"],
                          "title": title, "seconds": 0.0})
                entry["seconds"] += seconds
                if not entry["title"] and title:
                    entry["title"] = title

        stats = conn.execute(
            "SELECT afk_seconds, total_seconds FROM day_stats WHERE day = ?",
            (day,),
        ).fetchone()
        return {
            "seconds_by_level": seconds_by_level,
            "seconds_by_category": seconds_by_category,
            "uncategorized": sorted(
                uncat.values(), key=lambda e: e["seconds"], reverse=True),
            "afk_seconds": float(stats["afk_seconds"]) if stats else 0.0,
            "total_seconds": float(stats["total_seconds"]) if stats else 0.0,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------- goals ---

def _goal_row(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "direction": row["direction"],
        "target_type": row["target_type"],
        "target_name": row["target_name"],
        "threshold_minutes": row["threshold_minutes"],
        "threshold_pulse": row["threshold_pulse"],
        "pinned": bool(row["pinned"]),
        "created_at": row["created_at"],
    }


def add_goal(name, direction, target_type, target_name=None,
             threshold_minutes=None, threshold_pulse=None, pinned=False,
             path=None):
    """Create a goal; returns its new id."""
    from datetime import datetime

    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO goals (name, direction, target_type, target_name, "
            "threshold_minutes, threshold_pulse, pinned, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, direction, target_type, target_name,
             threshold_minutes, threshold_pulse, int(bool(pinned)),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_goals(path=None):
    init_db(path)
    conn = get_db(path)
    try:
        return [_goal_row(r) for r in
                conn.execute("SELECT * FROM goals ORDER BY id")]
    finally:
        conn.close()


def delete_goal(goal_id, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        conn.commit()
    finally:
        conn.close()


def set_pinned(goal_id, pinned, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("UPDATE goals SET pinned = ? WHERE id = ?",
                     (int(bool(pinned)), goal_id))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------- alerts ---

def _alert_row(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "target_type": row["target_type"],
        "target_name": row["target_name"],
        "threshold_minutes": row["threshold_minutes"],
        "message": row["message"] or "",
        "cooldown_minutes": row["cooldown_minutes"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


def add_alert(name, target_type, target_name, threshold_minutes,
              message="", cooldown_minutes=60, enabled=True, path=None):
    """Create an alert; returns its new id."""
    from datetime import datetime

    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO alerts (name, target_type, target_name, "
            "threshold_minutes, message, cooldown_minutes, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, target_type, target_name, threshold_minutes, message,
             cooldown_minutes, int(bool(enabled)),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_alerts(path=None):
    init_db(path)
    conn = get_db(path)
    try:
        return [_alert_row(r) for r in
                conn.execute("SELECT * FROM alerts ORDER BY id")]
    finally:
        conn.close()


def delete_alert(alert_id, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        conn.execute("DELETE FROM alert_firings WHERE alert_id = ?",
                     (alert_id,))
        conn.commit()
    finally:
        conn.close()


def set_alert_enabled(alert_id, enabled, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("UPDATE alerts SET enabled = ? WHERE id = ?",
                     (int(bool(enabled)), alert_id))
        conn.commit()
    finally:
        conn.close()


def record_firing(alert_id, fired_at, current_minutes, path=None):
    """Remember that an alert fired (drives the cooldown)."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT INTO alert_firings (alert_id, fired_at, current_minutes) "
            "VALUES (?, ?, ?)",
            (alert_id, fired_at.isoformat(timespec="seconds"),
             float(current_minutes)),
        )
        conn.commit()
    finally:
        conn.close()


def last_firing_at(alert_id, path=None):
    """Datetime of the most recent firing, or None if never fired."""
    from datetime import datetime

    init_db(path)
    conn = get_db(path)
    try:
        row = conn.execute(
            "SELECT fired_at FROM alert_firings WHERE alert_id = ? "
            "ORDER BY fired_at DESC LIMIT 1",
            (alert_id,),
        ).fetchone()
        return datetime.fromisoformat(row["fired_at"]) if row else None
    finally:
        conn.close()


def recent_firings(limit=20, path=None):
    """Newest firings first, with the alert name attached."""
    init_db(path)
    conn = get_db(path)
    try:
        return [
            {"alert_id": r["alert_id"], "name": r["name"],
             "fired_at": r["fired_at"],
             "current_minutes": r["current_minutes"]}
            for r in conn.execute(
                "SELECT f.alert_id, a.name, f.fired_at, f.current_minutes "
                "FROM alert_firings f LEFT JOIN alerts a "
                "ON a.id = f.alert_id "
                "ORDER BY f.fired_at DESC LIMIT ?",
                (limit,),
            )
        ]
    finally:
        conn.close()


# -------------------------------------------------------- focus sessions ---

def _session_row(row):
    keys = set(row.keys())
    return {
        "id": row["id"],
        "label": row["label"],
        "planned_minutes": row["planned_minutes"],
        "started_at": row["started_at"],
        "planned_end_at": row["planned_end_at"],
        "ended_at": row["ended_at"],
        "status": row["status"],
        "block_level": row["block_level"],
        # M0 migration-2 columns; default defensively for old readers.
        "enforcement_mode": row["enforcement_mode"]
        if "enforcement_mode" in keys else "strict",
        "intercepted_count": row["intercepted_count"]
        if "intercepted_count" in keys else 0,
        # Phase 8 session modes (migration 6); old rows read as classic.
        "session_type": row["session_type"]
        if "session_type" in keys else "classic",
        "completed_cycles": row["completed_cycles"]
        if "completed_cycles" in keys else 0,
        "target_cycles": row["target_cycles"]
        if "target_cycles" in keys else 1,
        "break_minutes": row["break_minutes"]
        if "break_minutes" in keys else 0.0,
        "work_minutes": row["work_minutes"]
        if "work_minutes" in keys else 0.0,
        "suggested_minutes": row["suggested_minutes"]
        if "suggested_minutes" in keys else None,
    }


def create_session(label, planned_minutes, started_at, planned_end_at,
                   block_level, enforcement_mode="strict", path=None,
                   session_type="classic", suggested_minutes=None,
                   target_cycles=1):
    """Insert a new focus session; returns its new id."""
    if enforcement_mode not in ("strict", "hardcore"):
        enforcement_mode = "strict"
    if session_type not in ("classic", "flowtime", "pomodoro"):
        session_type = "classic"
    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO focus_sessions (label, planned_minutes, started_at, "
            "planned_end_at, ended_at, status, block_level, "
            "enforcement_mode, session_type, suggested_minutes, "
            "target_cycles) "
            "VALUES (?, ?, ?, ?, NULL, 'active', ?, ?, ?, ?, ?)",
            (label, float(planned_minutes), started_at, planned_end_at,
             block_level, enforcement_mode, session_type,
             suggested_minutes,
             int(target_cycles) if target_cycles else 1),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_active_session(path=None):
    """The currently active session, or None."""
    init_db(path)
    conn = get_db(path)
    try:
        row = conn.execute(
            "SELECT * FROM focus_sessions WHERE status = 'active' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return _session_row(row) if row else None
    finally:
        conn.close()


def get_session(session_id, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        row = conn.execute(
            "SELECT * FROM focus_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _session_row(row) if row else None
    finally:
        conn.close()


def end_session(session_id, status, ended_at, path=None):
    """Mark a session 'completed' or 'aborted' with its end time."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE focus_sessions SET status = ?, ended_at = ? WHERE id = ?",
            (status, ended_at, session_id),
        )
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------- Pomodoro cycle tracking ---

def _cycle_row(row):
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "kind": row["kind"],
        "planned_minutes": row["planned_minutes"],
        "started_at": row["started_at"],
        "started_monotonic": row["started_monotonic"],
        "last_tick_wall": row["last_tick_wall"]
        if "last_tick_wall" in row.keys() else None,
        "last_tick_mono": row["last_tick_mono"]
        if "last_tick_mono" in row.keys() else None,
        "elapsed_offset_seconds": row["elapsed_offset_seconds"] or 0.0,
        "ended_at": row["ended_at"],
        "status": row["status"],
    }


def start_cycle(session_id, kind, planned_minutes, started_at,
                started_monotonic, path=None):
    """Start a work/break cycle; returns its id.

    The partial unique index (R3) raises sqlite3.IntegrityError if the
    session already has an active cycle — callers end it first.
    """
    if kind not in ("work", "break"):
        raise ValueError("kind must be 'work' or 'break'")
    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO session_cycles (session_id, kind, planned_minutes, "
            "started_at, started_monotonic, last_tick_wall, last_tick_mono,"
            " status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            (session_id, kind, float(planned_minutes), started_at,
             started_monotonic, started_at, started_monotonic),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def end_cycle(cycle_id, status, ended_at, path=None):
    """Mark a cycle completed/skipped/aborted."""
    if status not in ("completed", "skipped", "aborted"):
        raise ValueError("bad cycle status: %r" % (status,))
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE session_cycles SET status = ?, ended_at = ? "
            "WHERE id = ?",
            (status, ended_at, cycle_id),
        )
        conn.commit()
    finally:
        conn.close()


def resync_cycle_monotonic(cycle_id, started_monotonic,
                           elapsed_offset_seconds, path=None):
    """Resync a cycle's monotonic clock (daemon restart / R1).

    Keeps previously credited time in elapsed_offset_seconds —
    conservative, never over-credits.
    """
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE session_cycles SET started_monotonic = ?, "
            "elapsed_offset_seconds = ? WHERE id = ?",
            (started_monotonic, float(elapsed_offset_seconds), cycle_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_active_cycle(session_id, path=None):
    """The session's active cycle, or None."""
    init_db(path)
    conn = get_db(path)
    try:
        row = conn.execute(
            "SELECT * FROM session_cycles WHERE session_id = ? "
            "AND status = 'active' ORDER BY started_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return _cycle_row(row) if row else None
    finally:
        conn.close()


def update_cycle_ticks(cycle_id, wall_iso, mono, focused_seconds, path=None):
    """Persist the latest settle tick and accumulated focus seconds."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE session_cycles SET last_tick_wall = ?,"
            " last_tick_mono = ?, elapsed_offset_seconds = ?"
            " WHERE id = ?",
            (wall_iso, mono, float(focused_seconds), cycle_id),
        )
        conn.commit()
    finally:
        conn.close()


def cycles_for_session(session_id, path=None):
    """All cycles for a session, oldest first."""
    init_db(path)
    conn = get_db(path)
    try:
        rows = conn.execute(
            "SELECT * FROM session_cycles WHERE session_id = ? "
            "ORDER BY started_at ASC",
            (session_id,),
        ).fetchall()
        return [_cycle_row(r) for r in rows]
    finally:
        conn.close()


def bump_completed_cycles(session_id, path=None):
    """Increment focus_sessions.completed_cycles; returns the new count."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE focus_sessions SET completed_cycles = "
            "completed_cycles + 1 WHERE id = ?",
            (session_id,),
        )
        row = conn.execute(
            "SELECT completed_cycles FROM focus_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        conn.commit()
        return int(row["completed_cycles"]) if row else 0
    finally:
        conn.close()


def work_cycles_completed_today(day, path=None):
    """Completed work cycles started on the given day (YYYY-MM-DD)."""
    init_db(path)
    conn = get_db(path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM session_cycles "
            "WHERE kind = 'work' AND status = 'completed' "
            "AND substr(started_at, 1, 10) = ?",
            (day,),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def list_sessions(limit=20, path=None):
    """Recent sessions, newest first."""
    init_db(path)
    conn = get_db(path)
    try:
        return [
            _session_row(r) for r in conn.execute(
                "SELECT * FROM focus_sessions ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        ]
    finally:
        conn.close()


def get_day_sessions(day_str, path=None):
    """Completed sessions that started on `day_str` (Phase 12 timeline
    annotations)."""
    init_db(path)
    conn = get_db(path)
    try:
        return [
            _session_row(r) for r in conn.execute(
                "SELECT * FROM focus_sessions WHERE started_at >= ? "
                "AND started_at < ? AND status = 'completed' "
                "ORDER BY started_at",
                (day_str + "T00:00:00", day_str + "T23:59:59"),
            )
        ]
    finally:
        conn.close()


def record_block(session_id, ts, app, title, url, score, category,
                 path=None, action_taken="blocked", process_name="",
                 window_handle=0, _conn=None):
    """Remember one blocked distraction (session or always-on shield).

    action_taken / process_name / window_handle fill the M0
    migration-2 columns; session_id 0 means "no session -- the shield".

    Sprint 4: ``_conn`` lets the telemetry writer batch many blocks
    into one transaction (caller owns commit/close). When ``_conn`` is
    None the function opens, commits, and closes its own connection
    (legacy behavior, unchanged).
    """
    if _conn is not None:
        _conn.execute(
            "INSERT INTO focus_blocks "
            "(session_id, ts, app, title, url, score, category, "
            "action_taken, process_name, window_handle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, ts, app, columncrypto.protect_text(title),
             columncrypto.protect_text(url), score, category,
             action_taken, process_name or "", window_handle or 0),
        )
        return
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT INTO focus_blocks "
            "(session_id, ts, app, title, url, score, category, "
            "action_taken, process_name, window_handle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, ts, app, columncrypto.protect_text(title),
             columncrypto.protect_text(url), score, category,
             action_taken, process_name or "", window_handle or 0),
        )
        conn.commit()
    finally:
        conn.close()


def increment_intercepted(session_id, path=None, _conn=None):
    """Bump the M0 intercepted_count on a focus session.

    Sprint 4: ``_conn`` batches into the caller's transaction (see
    record_block)."""
    if _conn is not None:
        _conn.execute(
            "UPDATE focus_sessions SET intercepted_count = "
            "COALESCE(intercepted_count, 0) + 1 WHERE id = ?",
            (session_id,),
        )
        return
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE focus_sessions SET intercepted_count = "
            "COALESCE(intercepted_count, 0) + 1 WHERE id = ?",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()


def count_blocks_today(path=None, day=None):
    """How many blocks were recorded today (HUD + /shield)."""
    init_db(path)
    conn = get_db(path)
    try:
        day = day or date.today().isoformat()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM focus_blocks "
            "WHERE substr(ts, 1, 10) = ?",
            (day,),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def get_today_blocks(limit=50, path=None):
    """Today's block log for /shield (newest first)."""
    init_db(path)
    conn = get_db(path)
    try:
        rows = conn.execute(
            "SELECT * FROM focus_blocks WHERE substr(ts, 1, 10) = ? "
            "ORDER BY ts DESC LIMIT ?",
            (date.today().isoformat(), limit),
        ).fetchall()
        opened_rows = []
        for r in rows:
            opened = dict(r)
            opened["title"] = columncrypto.unprotect_text(opened["title"])
            opened["url"] = columncrypto.unprotect_text(opened["url"])
            opened_rows.append(opened)
        return opened_rows
    finally:
        conn.close()


def count_blocks(session_id, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM focus_blocks WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def session_days_with_completion(path=None):
    """Set of 'YYYY-MM-DD' days that have at least one completed session."""
    init_db(path)
    conn = get_db(path)
    try:
        return {
            (r["started_at"] or "")[:10]
            for r in conn.execute(
                "SELECT started_at FROM focus_sessions "
                "WHERE status = 'completed'"
            )
        } - {""}
    finally:
        conn.close()


# ------------------------------------------------------------ timesheets ---

def _project_row(row):
    def _col(name, default=None):
        try:
            return row[name]
        except (IndexError, KeyError):
            return default

    return {
        "id": row["id"],
        "name": row["name"],
        "client": row["client"] or "",
        "created_at": row["created_at"],
        # Phase 9 finance cache columns (current-state only; the
        # project_budget_ledger is the historical truth).
        "hourly_rate_minor": _col("hourly_rate_minor"),
        "rate_currency": _col("rate_currency") or "USD",
        "current_weekly_cap_seconds": _col("current_weekly_cap_seconds"),
        "current_monthly_cap_seconds": _col("current_monthly_cap_seconds"),
        "current_weekly_cap_amount_minor":
            _col("current_weekly_cap_amount_minor"),
        "current_monthly_cap_amount_minor":
            _col("current_monthly_cap_amount_minor"),
        "budget_currency": _col("budget_currency") or "USD",
    }


def add_project(name, client="", path=None):
    """Create a project; returns its new id."""
    from datetime import datetime

    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO projects (name, client, created_at) "
            "VALUES (?, ?, ?)",
            (name.strip(), (client or "").strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_projects(path=None):
    init_db(path)
    conn = get_db(path)
    try:
        return [_project_row(r) for r in
                conn.execute("SELECT * FROM projects ORDER BY name")]
    finally:
        conn.close()


def delete_project(project_id, path=None):
    """Delete a project; its timesheet entries keep their time but lose
    the project link (project_id set to NULL)."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET project_id = NULL "
            "WHERE project_id = ?",
            (project_id,),
        )
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    finally:
        conn.close()


def _entry_row(row):
    return {
        "id": row["id"],
        "day": row["day"],
        "start_ts": row["start_ts"],
        "end_ts": row["end_ts"],
        "minutes": row["minutes"],
        "category": row["category"],
        "app": row["app"] or "",
        "title": columncrypto.unprotect_text(row["title"]) or "",
        "project_id": row["project_id"],
        "project_name": row["project_name"] or "",
        "client": row["client"] or "",
        "task": row["task"] or "",
        "note": row["note"] or "",
        "status": row["status"],
        "locked": bool(row["locked"]),
        "created_at": row["created_at"],
        # Phase 10 (Q11): invoicing link; entry edits never change the
        # invoice line snapshot, so this is a display flag only.
        "invoice_id": row["invoice_id"],
        "invoice_number": row["invoice_number"] or "",
    }


_ENTRY_SELECT = (
    "SELECT e.id, e.day, e.start_ts, e.end_ts, e.minutes, e.category, "
    "e.app, e.title, e.project_id, p.name AS project_name, "
    "p.client AS client, e.task, e.note, e.status, e.locked, e.created_at, "
    "e.invoice_id, i.number AS invoice_number "
    "FROM timesheet_entries e LEFT JOIN projects p ON p.id = e.project_id "
    "LEFT JOIN invoices i ON i.id = e.invoice_id "
)


def create_entry(day, start_ts, end_ts, minutes, category, app="",
                 title="", project_id=None, task="", note="",
                 status="accepted", path=None):
    """Insert a timesheet entry; returns its new id.

    Phase 9 (Qwen M1): when the entry is tagged to a project that has a
    current rate, the rate is snapshotted onto the entry as 'confirmed'.
    Later project rate changes never touch this row.
    """
    from datetime import datetime

    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO timesheet_entries "
            "(day, start_ts, end_ts, minutes, category, app, title, "
            " project_id, task, note, status, locked, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (day, start_ts, end_ts, float(minutes), category, app or "",
             columncrypto.protect_text(title or ""), project_id, task or "",
             note or "", status,
             datetime.now().isoformat(timespec="seconds")),
        )
        entry_id = cur.lastrowid
        if project_id is not None:
            proj = conn.execute(
                "SELECT hourly_rate_minor, rate_currency FROM projects "
                "WHERE id = ?", (project_id,)).fetchone()
            if proj and proj["hourly_rate_minor"]:
                conn.execute(
                    "UPDATE timesheet_entries SET hourly_rate_minor = ?, "
                    "rate_currency = ?, rate_status = 'confirmed' "
                    "WHERE id = ?",
                    (proj["hourly_rate_minor"],
                     proj["rate_currency"] or "USD", entry_id),
                )
        conn.commit()
        return entry_id
    finally:
        conn.close()


def get_entry(entry_id, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        row = conn.execute(
            _ENTRY_SELECT + "WHERE e.id = ?", (entry_id,)).fetchone()
        return _entry_row(row) if row else None
    finally:
        conn.close()


def list_entries(day=None, day_from=None, day_to=None, path=None):
    """Timesheet entries, optionally filtered to one day or a date range,
    ordered by day then start time."""
    init_db(path)
    conn = get_db(path)
    try:
        query = _ENTRY_SELECT
        args = []
        clauses = []
        if day is not None:
            clauses.append("e.day = ?")
            args.append(day)
        else:
            if day_from is not None:
                clauses.append("e.day >= ?")
                args.append(day_from)
            if day_to is not None:
                clauses.append("e.day <= ?")
                args.append(day_to)
        if clauses:
            query += "WHERE " + " AND ".join(clauses) + " "
        query += "ORDER BY e.day, e.start_ts"
        return [_entry_row(r) for r in conn.execute(query, args)]
    finally:
        conn.close()


def update_entry(entry_id, fields, path=None):
    """Update allowed columns of one entry. ``fields`` maps column name
    to new value; only whitelisted columns are written."""
    allowed = {"day", "start_ts", "end_ts", "minutes", "category", "app",
               "title", "project_id", "task", "note", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "title" in updates:
        updates["title"] = columncrypto.protect_text(updates["title"] or "")
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET %s WHERE id = ?"
            % ", ".join("%s = ?" % k for k in updates),
            list(updates.values()) + [entry_id],
        )
        conn.commit()
    finally:
        conn.close()


def delete_entry(entry_id, path=None):
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute("DELETE FROM timesheet_entries WHERE id = ?",
                     (entry_id,))
        conn.commit()
    finally:
        conn.close()


def lock_day(day, path=None):
    """Finalize a day: mark all its entries locked (no more edits)."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET locked = 1 WHERE day = ?", (day,))
        conn.commit()
    finally:
        conn.close()


def day_is_locked(day, path=None):
    """True when the day has at least one entry and all are locked."""
    entries = list_entries(day=day, path=path)
    return bool(entries) and all(e["locked"] for e in entries)


# ---------------------------------------------------------------------------
# Settings (Phase 7) -- simple key/value store, created by migration 5.
# ---------------------------------------------------------------------------

def get_setting(key, default=None, path=None):
    """Read a setting; returns default when missing. Never raises."""
    try:
        init_db(path)
        conn = get_db(path)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()
    except Exception:
        return default


def set_setting(key, value, path=None):
    """Write a setting (upsert). Never raises."""
    try:
        init_db(path)
        conn = get_db(path)
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Roadmap 0.3: a failed preference write used to vanish.
        logger.exception("set_setting(%r) failed", key)
        pass


# ---------------------------------------------------------------------------
# Block rules (Phase 7) -- always-on scheduled shield rules.
# ---------------------------------------------------------------------------

RULE_ACTIONS = ("soft", "firm", "hardcore")
RULE_TYPES = ("app", "category")


def create_block_rule(name, rule_type, key, action, days="all",
                      start_time="", end_time="", path=None):
    """Insert a block rule; returns its new id. Raises ValueError on bad input."""
    name = (name or "").strip()
    rule_type = (rule_type or "").strip().lower()
    key = (key or "").strip().lower()
    action = (action or "").strip().lower()
    if not name:
        raise ValueError("Rule needs a name.")
    if rule_type not in RULE_TYPES:
        raise ValueError("rule_type must be one of %s." % (RULE_TYPES,))
    if not key:
        raise ValueError("Rule needs a key (app/process name or category).")
    if action not in RULE_ACTIONS:
        raise ValueError("action must be one of %s." % (RULE_ACTIONS,))
    for label, val in (("start_time", start_time), ("end_time", end_time)):
        val = (val or "").strip()
        if val:
            try:
                h, m = int(val[0:2]), int(val[3:5])
                assert 0 <= h < 24 and 0 <= m < 60 and len(val) == 5
            except (ValueError, IndexError, AssertionError):
                raise ValueError("%s must be HH:MM." % label)
    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO block_rules (name, rule_type, key, action, days, "
            "start_time, end_time, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (name, rule_type, key, action,
             (days or "all").strip().lower(),
             (start_time or "").strip(), (end_time or "").strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_block_rules(path=None, only_enabled=False):
    """All block rules (dicts), newest first."""
    init_db(path)
    conn = get_db(path)
    try:
        sql = "SELECT * FROM block_rules"
        if only_enabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id DESC"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def set_block_rule_enabled(rule_id, enabled, path=None):
    """Enable/disable a rule; returns True when a row changed."""
    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "UPDATE block_rules SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, rule_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_block_rule(rule_id, path=None):
    """Delete a rule; returns True when a row was removed."""
    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "DELETE FROM block_rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Emergency passes (Phase 7) -- timed, logged, never fail closed.
# ---------------------------------------------------------------------------

def create_pass(minutes, reason, now=None, path=None):
    """Create an emergency pass; returns the pass dict.

    On SQLite failure (locked/busy) falls back to a JSONL sidecar file
    plus an in-memory copy (Qwen R7) -- a pass must NEVER fail closed.
    """
    now = now or datetime.now()
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        minutes = 0
    if not 0 < minutes <= 120:
        raise ValueError("Pass length must be 1-120 minutes.")
    reason = (reason or "").strip() or "no reason given"
    started_at = now.isoformat(timespec="seconds")
    row = {"started_at": started_at, "minutes": minutes,
           "reason": reason}
    try:
        init_db(path)
        conn = get_db(path)
        try:
            cur = conn.execute(
                "INSERT INTO block_passes (started_at, minutes, reason) "
                "VALUES (?, ?, ?)",
                (started_at, minutes, reason),
            )
            conn.commit()
            row["id"] = cur.lastrowid
        finally:
            conn.close()
        return row
    except Exception:
        # R7: SQLite unreachable -- memory + JSONL fallback.
        from . import shield as shield_mod
        shield_mod.remember_memory_pass(started_at, minutes, reason)
        try:
            import json
            with open(shield_mod.fallback_passes_path(), "a",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            # Last-resort path: SQLite AND the fallback file both
            # failed, so this pass exists only in memory. That is
            # worth a traceback -- the user may believe the shield
            # is paused when, after a restart, it is not.
            logger.exception("emergency pass fallback write failed; "
                             "pass is memory-only")
        return row


def get_active_pass(now=None, path=None):
    """The currently active pass, or None. Never raises."""
    now = now or datetime.now()
    try:
        init_db(path)
        conn = get_db(path)
        try:
            rows = conn.execute(
                "SELECT * FROM block_passes ORDER BY id DESC LIMIT 50"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            try:
                start = datetime.fromisoformat(r["started_at"])
                if start <= now < start + timedelta(
                        minutes=float(r["minutes"])):
                    return dict(r)
            except (ValueError, TypeError):
                continue
    except Exception:
        # Roadmap 0.3: a failed pass lookup used to vanish (pass silently
        # treated as inactive).
        logger.exception("get_active_pass failed")
        pass
    return None


def get_recent_passes(limit=20, path=None):
    """Recent passes (newest first) for the /shield audit list."""
    init_db(path)
    conn = get_db(path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM block_passes ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()]
    finally:
        conn.close()
