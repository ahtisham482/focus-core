"""SQLite store. The database location is resolved by focuscore.paths
(portable: next to the code; installed: per-user data folder).

All paths are derived from this file's location, so the project works
wherever the folder is placed -- no absolute paths anywhere.
"""

import sqlite3

from . import paths

DEFAULT_DB_PATH = paths.db_path()

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
    conn = sqlite3.connect(str(path or DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path=None):
    conn = get_db(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


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
                    e.get("title", ""), e.get("url"),
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
        return [
            dict(row)
            for row in conn.execute(
                "SELECT ts, duration, app, title, url, category, score, "
                "       override_score, match_key "
                "FROM activities WHERE day = ? ORDER BY ts",
                (day,),
            )
        ]
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
                entry = uncat.setdefault(
                    key, {"match_key": key, "app": row["app"],
                          "title": row["title"], "seconds": 0.0})
                entry["seconds"] += seconds
                if not entry["title"] and row["title"]:
                    entry["title"] = row["title"]

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
    return {
        "id": row["id"],
        "label": row["label"],
        "planned_minutes": row["planned_minutes"],
        "started_at": row["started_at"],
        "planned_end_at": row["planned_end_at"],
        "ended_at": row["ended_at"],
        "status": row["status"],
        "block_level": row["block_level"],
    }


def create_session(label, planned_minutes, started_at, planned_end_at,
                   block_level, path=None):
    """Insert a new focus session; returns its new id."""
    init_db(path)
    conn = get_db(path)
    try:
        cur = conn.execute(
            "INSERT INTO focus_sessions (label, planned_minutes, started_at, "
            "planned_end_at, ended_at, status, block_level) "
            "VALUES (?, ?, ?, ?, NULL, 'active', ?)",
            (label, float(planned_minutes), started_at, planned_end_at,
             block_level),
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


def record_block(session_id, ts, app, title, url, score, category, path=None):
    """Remember one blocked distraction inside a focus session."""
    init_db(path)
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT INTO focus_blocks "
            "(session_id, ts, app, title, url, score, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, ts, app, title, url, score, category),
        )
        conn.commit()
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
    return {
        "id": row["id"],
        "name": row["name"],
        "client": row["client"] or "",
        "created_at": row["created_at"],
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
        "title": row["title"] or "",
        "project_id": row["project_id"],
        "project_name": row["project_name"] or "",
        "client": row["client"] or "",
        "task": row["task"] or "",
        "note": row["note"] or "",
        "status": row["status"],
        "locked": bool(row["locked"]),
        "created_at": row["created_at"],
    }


_ENTRY_SELECT = (
    "SELECT e.id, e.day, e.start_ts, e.end_ts, e.minutes, e.category, "
    "e.app, e.title, e.project_id, p.name AS project_name, "
    "p.client AS client, e.task, e.note, e.status, e.locked, e.created_at "
    "FROM timesheet_entries e LEFT JOIN projects p ON p.id = e.project_id "
)


def create_entry(day, start_ts, end_ts, minutes, category, app="",
                 title="", project_id=None, task="", note="",
                 status="accepted", path=None):
    """Insert a timesheet entry; returns its new id."""
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
             title or "", project_id, task or "", note or "", status,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid
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
