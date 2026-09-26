"""focuscore/migrations.py - High-Assurance SQLite Migration Runner.

Provides versioned, transactional, idempotent database schema migrations
with automatic pre-migration snapshotting, snapshot rotation, and
auto-rollback on failure.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Set

from . import backup, paths, store

logger = logging.getLogger(__name__)

LATEST_VERSION = 6


class MigrationError(Exception):
    """Raised when a database migration step fails."""
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


# ----------------------------------------------------------- DDL Helpers ---

def get_table_columns(conn: sqlite3.Connection, table_name: str) -> Set[str]:
    """Return set of existing column names for table_name."""
    cur = conn.execute(f"PRAGMA table_info({table_name})")
    return {row[1].lower() for row in cur.fetchall()}


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if table_name exists in SQLite master."""
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_def: str,
) -> bool:
    """Safely adds a column to a table if it does not already exist.

    Returns True if column was added, False if already present.
    """
    if not table_exists(conn, table_name):
        return False
    columns = get_table_columns(conn, table_name)
    if column_name.lower() not in columns:
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
        )
        logger.info(
            "Added column %s.%s (%s)", table_name, column_name, column_def
        )
        return True
    return False


def get_current_user_version(conn: sqlite3.Connection) -> int:
    """Return SQLite PRAGMA user_version as integer."""
    cur = conn.execute("PRAGMA user_version")
    row = cur.fetchone()
    return int(row[0]) if row else 0


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Set SQLite PRAGMA user_version."""
    conn.execute(f"PRAGMA user_version = {int(version)}")


def ensure_wal_and_timeout(conn: sqlite3.Connection) -> None:
    """Set WAL journal mode and busy timeout to avoid write deadlocks."""
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA busy_timeout = 5000")


# ---------------------------------------------------- Migration Definitions ---

def _migration_0001_afk_intervals(conn: sqlite3.Connection) -> None:
    """Migration 1: Raw AFK tracking intervals table and indexes (M1)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_intervals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            start_ts TEXT NOT NULL,
            end_ts TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'afk',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_afk_intervals_day "
        "ON afk_intervals(day)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_afk_intervals_start "
        "ON afk_intervals(start_ts)"
    )


def _migration_0002_shield_columns(conn: sqlite3.Connection) -> None:
    """Migration 2: Hardcore shield mode & interception tracking columns (M2)."""
    add_column_if_missing(
        conn,
        "focus_sessions",
        "enforcement_mode",
        "TEXT NOT NULL DEFAULT 'strict'",
    )
    add_column_if_missing(
        conn,
        "focus_sessions",
        "intercepted_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(
        conn, "focus_blocks", "action_taken", "TEXT DEFAULT 'blocked'"
    )
    add_column_if_missing(
        conn, "focus_blocks", "process_name", "TEXT DEFAULT ''"
    )
    add_column_if_missing(
        conn, "focus_blocks", "window_handle", "INTEGER DEFAULT 0"
    )


def ensure_base_schema(conn: sqlite3.Connection) -> None:
    """Ensure baseline v0 tables exist before incremental migrations."""
    conn.executescript(store.SCHEMA)


def _migration_0003_flowtime_pomodoro(conn: sqlite3.Connection) -> None:
    """Migration 3: Flowtime & Pomodoro cycle tracking columns (M3)."""
    add_column_if_missing(
        conn,
        "focus_sessions",
        "session_type",
        "TEXT NOT NULL DEFAULT 'pomodoro'",
    )
    add_column_if_missing(
        conn,
        "focus_sessions",
        "completed_cycles",
        "INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(
        conn,
        "focus_sessions",
        "target_cycles",
        "INTEGER NOT NULL DEFAULT 1",
    )
    add_column_if_missing(
        conn,
        "focus_sessions",
        "break_minutes",
        "REAL NOT NULL DEFAULT 0.0",
    )
    add_column_if_missing(
        conn,
        "focus_sessions",
        "work_minutes",
        "REAL NOT NULL DEFAULT 0.0",
    )
    add_column_if_missing(
        conn, "timesheet_entries", "session_id", "INTEGER DEFAULT NULL"
    )
    if table_exists(conn, "timesheet_entries"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timesheet_session "
            "ON timesheet_entries(session_id)"
        )


def _migration_0004_projects_budget_billable(conn: sqlite3.Connection) -> None:
    """Migration 4: Project budget caps, colors, and billable flags (M4)."""
    add_column_if_missing(
        conn, "projects", "is_billable", "INTEGER NOT NULL DEFAULT 1"
    )
    add_column_if_missing(
        conn, "projects", "hourly_rate", "REAL NOT NULL DEFAULT 0.0"
    )
    add_column_if_missing(
        conn, "projects", "color", "TEXT NOT NULL DEFAULT '#3B82F6'"
    )
    add_column_if_missing(
        conn, "projects", "weekly_budget_hours", "REAL NOT NULL DEFAULT 0.0"
    )
    add_column_if_missing(
        conn, "timesheet_entries", "is_billable", "INTEGER NOT NULL DEFAULT 1"
    )


def _migration_0005_shield_rules(conn: sqlite3.Connection) -> None:
    """Migration 5: always-on block rules, emergency passes, settings.

    New tables only (no ALTER of existing tables), so the migration is
    inherently additive and idempotent. Store schedule times as HH:MM
    wall-clock strings; the shield evaluates them in local wall-clock
    time (Phase 6 timezone logic).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS block_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rule_type TEXT NOT NULL,      -- 'app' or 'category'
            key TEXT NOT NULL,            -- exe basename or category
            action TEXT NOT NULL,         -- 'soft' | 'firm' | 'hardcore'
            days TEXT NOT NULL DEFAULT 'all',  -- 'all' or CSV 0-6 (Mon=0)
            start_time TEXT NOT NULL DEFAULT '',  -- 'HH:MM' wall clock
            end_time TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS block_passes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            minutes REAL NOT NULL,
            reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_block_rules_enabled "
        "ON block_rules(enabled)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_block_passes_started "
        "ON block_passes(started_at)"
    )
    # Sensible defaults (INSERT OR IGNORE: keep user values on re-run).
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES "
        "('hud_enabled', '1'), "
        "('shield_enabled', '1')"
    )


def _migration_0006_session_modes(conn: sqlite3.Connection) -> None:
    """Migration 6: Phase 8 session modes + pomodoro cycle tracking.

    Reuses the migration-3 columns (session_type, completed_cycles,
    target_cycles, break_minutes, work_minutes): every pre-Phase-8
    session was a classic fixed timer, so old rows are normalized to
    'classic'. New table session_cycles holds work/break cycles; a
    partial unique index guarantees at most one active cycle per
    session (R3). Idempotent: UPDATE and CREATE IF NOT EXISTS are
    safe to re-run.

    Defensive: a database merely *marked* as v5 may not have the
    migration-3 columns (e.g. hand-built fixtures). Never crash the
    UPDATE when the table or column is absent.
    """
    cols = (get_table_columns(conn, "focus_sessions")
            if table_exists(conn, "focus_sessions") else set())
    if "session_type" in cols:
        conn.execute(
            "UPDATE focus_sessions SET session_type = 'classic' "
            "WHERE session_type = 'pomodoro'"
        )
    add_column_if_missing(
        conn, "focus_sessions", "suggested_minutes", "INTEGER")
    # New columns for already-created tables (idempotent).
    add_column_if_missing(
        conn, "session_cycles", "last_tick_wall", "TEXT")
    add_column_if_missing(
        conn, "session_cycles", "last_tick_mono", "REAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL
                REFERENCES focus_sessions(id),
            kind TEXT NOT NULL,              -- 'work' | 'break'
            planned_minutes REAL NOT NULL,
            started_at TEXT NOT NULL,        -- UTC wall clock
            started_monotonic REAL,          -- monotonic() at start
            last_tick_wall TEXT,             -- wall clock at previous settle
            last_tick_mono REAL,             -- monotonic() at previous settle
            elapsed_offset_seconds REAL NOT NULL DEFAULT 0,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_cycle "
        "ON session_cycles(session_id) WHERE status = 'active'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cycles_session "
        "ON session_cycles(session_id)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES "
        "('audio_cues', '1')"
    )


MIGRATIONS: List[Migration] = [
    Migration(1, "0001_afk_intervals", _migration_0001_afk_intervals),
    Migration(2, "0002_shield_columns", _migration_0002_shield_columns),
    Migration(3, "0003_flowtime_pomodoro", _migration_0003_flowtime_pomodoro),
    Migration(
        4,
        "0004_projects_budget_billable",
        _migration_0004_projects_budget_billable,
    ),
    Migration(
        5,
        "0005_shield_rules",
        _migration_0005_shield_rules,
    ),
    Migration(
        6,
        "0006_session_modes",
        _migration_0006_session_modes,
    ),
]


# ------------------------------------------------------------ Runner Core ---

def _init_migrations_table(conn: sqlite3.Connection) -> None:
    """Ensure schema_migrations tracking table exists."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def get_applied_versions(conn: sqlite3.Connection) -> Set[int]:
    """Return set of applied migration versions from schema_migrations."""
    _init_migrations_table(conn)
    cur = conn.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def prune_snapshots(
    keep: int = 10,
    dest_dir: Optional[str | Path] = None,
) -> int:
    """Retain up to `keep` newest snapshots in the backup directory."""
    folder = Path(dest_dir) if dest_dir is not None else paths.backups_dir()
    if not folder.exists():
        return 0

    pruned = backup.prune_backups(keep=keep, dest_dir=folder)

    # Also clean up any legacy or custom named pre-migration snapshots
    custom_snaps = []
    for path in folder.glob("*pre-migration*.db"):
        if path.name.endswith(".tmp"):
            continue
        try:
            stat = path.stat()
            custom_snaps.append((stat.st_mtime, path))
        except OSError:
            continue
    custom_snaps.sort(key=lambda x: x[0], reverse=True)
    for _, path in custom_snaps[keep:]:
        try:
            path.unlink(missing_ok=True)
            pruned += 1
        except OSError:
            pass
        try:
            sidecar = Path(str(path) + ".sha256")
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass

    return pruned


def apply_migrations(
    db_path: Optional[str] = None,
    dest_backup_dir: Optional[str] = None,
    backups_dir: Optional[str] = None,
) -> int:
    """Apply any pending SQLite migrations inside atomic transactions.

    Guarantees:
    - WAL journal mode and busy_timeout=5000 enabled on the database.
    - Pre-migration snapshot taken if database exists and has pending migrations.
    - Snapshot rotation: retains up to 10 newest pre-migration snapshots.
    - Each migration runs in an isolated transaction (BEGIN IMMEDIATE / COMMIT).
    - If a migration fails, the transaction is rolled back, the connection closed,
      transient WAL/SHM files unlinked, and the pre-migration snapshot restored.
    - Strictly idempotent: re-running does nothing if already up-to-date.

    Returns:
        int: Highest schema version applied.
    """
    target_path = Path(db_path or store.DEFAULT_DB_PATH)
    backup_folder = dest_backup_dir or backups_dir

    if str(target_path) != ":memory:":
        target_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Determine if this is a fresh database (file absent or 0 bytes)
    is_fresh = not target_path.exists() or target_path.stat().st_size == 0

    # 2. Fast check if database already exists and user_version is up-to-date
    if not is_fresh:
        try:
            quick_conn = sqlite3.connect(str(target_path))
            try:
                ensure_wal_and_timeout(quick_conn)
                cur_ver = get_current_user_version(quick_conn)
                if cur_ver >= LATEST_VERSION:
                    return cur_ver
            finally:
                quick_conn.close()
        except (sqlite3.DatabaseError, OSError) as exc:
            raise MigrationError(
                f"Database access error during fast check: {exc}"
            ) from exc

    # 3. Determine pending migrations
    try:
        conn = sqlite3.connect(str(target_path))
        conn.row_factory = sqlite3.Row
        try:
            ensure_wal_and_timeout(conn)
            ensure_base_schema(conn)
            _init_migrations_table(conn)
            conn.commit()

            applied = get_applied_versions(conn)
            pending = [m for m in MIGRATIONS if m.version not in applied]

            if not pending:
                max_ver = max(applied) if applied else 0
                set_user_version(conn, max_ver)
                conn.commit()
                return max_ver
        finally:
            conn.close()
    except (sqlite3.DatabaseError, OSError) as exc:
        raise MigrationError(
            f"Database access error during schema initialization: {exc}"
        ) from exc

    # 4. Create pre-migration backup snapshot if database exists and was not fresh
    backup_path: Optional[Path] = None
    if not is_fresh and target_path.exists() and target_path.stat().st_size > 0:
        try:
            backup_path = backup.create_backup(
                db_path=str(target_path), dest_dir=backup_folder
            )
            prune_snapshots(keep=10, dest_dir=backup_folder)
            logger.info("Created pre-migration snapshot: %s", backup_path)
        except Exception as e:
            logger.error("Failed to create pre-migration snapshot: %s", e)
            raise MigrationError(f"Pre-migration backup failed: {e}") from e

    # 5. Execute pending migrations one by one in atomic transactions
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    applied_by_this_call: List[int] = []
    try:
        ensure_wal_and_timeout(conn)
        ensure_base_schema(conn)
        current_version = get_current_user_version(conn)

        for migration in pending:
            logger.info(
                "Applying migration %d: %s", migration.version, migration.name
            )
            max_busy_retries = 10
            for attempt in range(max_busy_retries):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as op_err:
                    if (
                        "locked" in str(op_err).lower()
                        or "busy" in str(op_err).lower()
                    ) and attempt < max_busy_retries - 1:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    raise

            try:
                cur = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (migration.version,),
                )
                if cur.fetchone() is not None:
                    conn.commit()
                    current_version = max(current_version, migration.version)
                    continue

                migration.apply(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (
                        migration.version,
                        migration.name,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                set_user_version(conn, migration.version)
                conn.commit()
                current_version = migration.version
                applied_by_this_call.append(migration.version)
            except Exception as step_err:
                logger.error(
                    "Migration %d (%s) failed: %s. Rolling back transaction.",
                    migration.version,
                    migration.name,
                    step_err,
                )
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise step_err

        return current_version
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            conn = None

        # 6. On failure, restore pre-migration snapshot if available
        if (
            backup_path
            and backup_path.exists()
            and len(applied_by_this_call) > 0
        ):
            logger.warning(
                "Restoring database from pre-migration snapshot: %s",
                backup_path.name,
            )
            wal_file = Path(str(target_path) + "-wal")
            shm_file = Path(str(target_path) + "-shm")
            try:
                wal_file.unlink(missing_ok=True)
                shm_file.unlink(missing_ok=True)
            except OSError:
                pass

            try:
                backup.restore_backup(
                    backup_path.name,
                    db_path=str(target_path),
                    dest_dir=backup_path.parent,
                )
            except Exception as restore_err:
                logger.error(
                    "Failed to restore pre-migration snapshot: %s",
                    restore_err,
                )
                raise MigrationError(
                    f"Migration aborted ({exc}) and rollback failed: "
                    f"{restore_err}"
                ) from restore_err

            try:
                wal_file.unlink(missing_ok=True)
                shm_file.unlink(missing_ok=True)
            except OSError:
                pass
        elif is_fresh and len(applied_by_this_call) > 0:
            try:
                target_path.unlink(missing_ok=True)
                Path(str(target_path) + "-wal").unlink(missing_ok=True)
                Path(str(target_path) + "-shm").unlink(missing_ok=True)
            except OSError:
                pass

        if isinstance(exc, MigrationError):
            raise exc
        raise MigrationError(
            f"Migration aborted and rolled back: {exc}"
        ) from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
