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

LATEST_VERSION = 9


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
    except sqlite3.OperationalError as exc:
        # Can fire per connection under lock contention; DEBUG so a
        # locked database doesn't become a log flood.
        logger.debug("could not enable WAL journal mode: %s", exc)
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


def _migration_0007_finance(conn: sqlite3.Connection) -> None:
    """Migration 7: financial-adjacent schema for Phase 9 (Qwen audit M1/M2/E/G).

    Binding rules from the audit:
    - Money is NEVER REAL: integer minor units everywhere.
    - Time budgets are seconds, not fractional hours.
    - Historical timesheet entries are NEVER silently backfilled with a
      newly set project rate: new snapshot columns start NULL/'unknown'.
    - Budget caps live in the append-only project_budget_ledger; the
      projects.* cache columns are current-state only.
    - Financial changes are recorded in finance_audit_events.
    Legacy migration-4 REAL columns (hourly_rate, weekly_budget_hours)
    are migrated once into the new integer columns and then left alone.
    """
    # -- 1. Rate snapshot on timesheet entries (M1) ---------------------
    add_column_if_missing(
        conn, "timesheet_entries", "hourly_rate_minor", "INTEGER"
    )
    add_column_if_missing(
        conn, "timesheet_entries", "rate_currency", "TEXT"
    )
    add_column_if_missing(
        conn,
        "timesheet_entries",
        "rate_status",
        "TEXT NOT NULL DEFAULT 'unknown' "
        "CHECK (rate_status IN ('unknown', 'confirmed', 'estimated'))",
    )
    add_column_if_missing(
        conn, "timesheet_entries", "rate_confirmed_at_utc", "TEXT"
    )

    # -- 2. Project finance cache columns (M2: cache only) -------------
    add_column_if_missing(
        conn, "projects", "hourly_rate_minor", "INTEGER"
    )
    add_column_if_missing(conn, "projects", "rate_currency", "TEXT")
    add_column_if_missing(
        conn, "projects", "current_weekly_cap_seconds", "INTEGER"
    )
    add_column_if_missing(
        conn, "projects", "current_monthly_cap_seconds", "INTEGER"
    )
    add_column_if_missing(
        conn, "projects", "current_weekly_cap_amount_minor", "INTEGER"
    )
    add_column_if_missing(
        conn, "projects", "current_monthly_cap_amount_minor", "INTEGER"
    )
    add_column_if_missing(conn, "projects", "budget_currency", "TEXT")

    # -- 3. Append-only budget ledger (M2) ------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_budget_ledger (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            period_type TEXT NOT NULL
                CHECK (period_type IN ('week', 'month')),
            period_start TEXT NOT NULL,
            cap_seconds INTEGER,
            cap_amount_minor INTEGER,
            currency TEXT,
            effective_from_utc TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at_utc TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_budget_ledger_lookup "
        "ON project_budget_ledger "
        "(project_id, period_type, period_start, effective_from_utc)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_budget_ledger_project_effective "
        "ON project_budget_ledger (project_id, effective_from_utc)"
    )

    # -- 4. Finance audit events (E) ------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_audit_events (
            id INTEGER PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            event_type TEXT NOT NULL,
            payload_json TEXT DEFAULT '',
            created_at_utc TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_finance_audit_entity "
        "ON finance_audit_events (entity_type, entity_id)"
    )

    # -- 5. Export query indexes (G) ------------------------------------
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timesheet_project_day "
        "ON timesheet_entries(project_id, day)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timesheet_day_project "
        "ON timesheet_entries(day, project_id)"
    )

    # -- 6. One-time seeding from legacy migration-4 REAL columns ------
    _migration_0007_seed_legacy(conn)

    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES "
        "('currency', 'USD')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES "
        "('working_days', '0,1,2,3,4')"
    )


def _migration_0007_seed_legacy(conn: sqlite3.Connection) -> None:
    """Copy legacy REAL money/hours into the new integer columns once.

    hourly_rate (REAL $/hr) -> hourly_rate_minor (int cents) +
    rate_currency. weekly_budget_hours (REAL) -> current_weekly_cap_seconds
    + one ledger row so the cap has a history from day one. Existing
    timesheet entries are deliberately NOT touched (M1.1).
    """
    from datetime import date, datetime, timedelta

    now_utc = datetime.now().isoformat(timespec="seconds")
    try:
        for row in conn.execute(
            "SELECT id, hourly_rate, weekly_budget_hours FROM projects"
        ).fetchall():
            pid = row["id"]
            legacy_rate = row["hourly_rate"] or 0.0
            legacy_hours = row["weekly_budget_hours"] or 0.0
            rate_minor = (
                int(round(legacy_rate * 100)) if legacy_rate > 0 else None
            )
            if rate_minor:
                conn.execute(
                    "UPDATE projects SET hourly_rate_minor = ?, "
                    "rate_currency = 'USD' WHERE id = ?",
                    (rate_minor, pid),
                )
            if legacy_hours > 0:
                cap_seconds = int(round(legacy_hours * 3600))
                conn.execute(
                    "UPDATE projects SET current_weekly_cap_seconds = ? "
                    "WHERE id = ?",
                    (cap_seconds, pid),
                )
                monday = date.today() - timedelta(days=date.today().weekday())
                conn.execute(
                    "INSERT INTO project_budget_ledger "
                    "(project_id, period_type, period_start, cap_seconds, "
                    "cap_amount_minor, currency, effective_from_utc, note, "
                    "created_at_utc) VALUES (?, 'week', ?, ?, NULL, 'USD', "
                    "?, 'seeded from legacy weekly_budget_hours', ?)",
                    (pid, monday.isoformat(), cap_seconds, now_utc, now_utc),
                )
    except Exception:
        # Roadmap 0.3: seed failures on exotic DBs were silent.
        logger.exception("legacy budget-ledger seeding failed")
        # Legacy columns may not exist on exotic DBs; seeding is best-effort.
        pass


def _migration_0008_invoicing(conn: sqlite3.Connection) -> None:
    """Migration 8: Phase 10 invoicing + rollover (Qwen audit Q1-Q15).

    Binding rules from the audit:
    - Q1: sent/paid invoices are immutable at the DB layer (triggers);
      only sent->void and sent->paid transitions are permitted, and the
      frozen financial columns must be unchanged by them.
    - Q2: invoice_counters(year INTEGER PK, next_number from 1);
      numbers are issued inside BEGIN IMMEDIATE with an optimistic guard.
    - Q3: timesheet_entries.invoice_id REFERENCES invoices(id)
      ON DELETE RESTRICT; a trigger forbids moving the link from one
      non-NULL invoice to another.
    - Q5: invoices.superseded_by_invoice_id + void_reason.
    - Q11: invoice_lines carries a denormalized snapshot; entry FK is
      ON DELETE RESTRICT; lines survive voiding as an audit record.
    - Q12: tax/discount are integer percentages (snapshots per invoice).
    - Q13: single currency per invoice (enforced in application code).
    - Q14: rollover is hours-only -> projects.rollover_enabled toggle.
    - Q15: status CHECK is exactly draft|sent|paid|void; number is
      UNIQUE but nullable so drafts (pre-numbering) coexist.
    """
    # -- 1. invoices (Qwen mandatory schema + client/due_date/notes +
    #    supersedes back-pointer snapshots) ---------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY,
            number TEXT UNIQUE,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','sent','paid','void')),
            currency TEXT NOT NULL,
            client TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            due_date TEXT,
            subtotal_minor INTEGER NOT NULL DEFAULT 0,
            discount_pct INTEGER NOT NULL DEFAULT 0,
            discount_amount_minor INTEGER NOT NULL DEFAULT 0,
            tax_pct INTEGER NOT NULL DEFAULT 0,
            tax_amount_minor INTEGER NOT NULL DEFAULT 0,
            total_minor INTEGER NOT NULL DEFAULT 0,
            void_reason TEXT,
            superseded_by_invoice_id INTEGER REFERENCES invoices(id),
            supersedes_invoice_id INTEGER REFERENCES invoices(id),
            issued_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoices_project "
        "ON invoices(project_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(number)"
    )

    # -- 2. invoice_lines (Q11) ----------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_lines (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER NOT NULL
                REFERENCES invoices(id) ON DELETE CASCADE,
            timesheet_entry_id INTEGER
                REFERENCES timesheet_entries(id) ON DELETE RESTRICT,
            entry_date TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            hours_minor_units INTEGER NOT NULL DEFAULT 0,
            rate_minor_units INTEGER,
            amount_minor_units INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoice_lines_invoice "
        "ON invoice_lines(invoice_id)"
    )

    # -- 3. invoice_counters (Q2) --------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_counters (
            year INTEGER NOT NULL PRIMARY KEY,
            next_number INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    # -- 4. invoice_payments (append-only) ------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_payments (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER NOT NULL
                REFERENCES invoices(id) ON DELETE CASCADE,
            paid_date TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at_utc TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoice_payments_invoice "
        "ON invoice_payments(invoice_id)"
    )

    # -- 5. entry -> invoice link (Q3) ---------------------------------
    add_column_if_missing(
        conn,
        "timesheet_entries",
        "invoice_id",
        "INTEGER REFERENCES invoices(id) ON DELETE RESTRICT",
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timesheet_invoice_id "
        "ON timesheet_entries(invoice_id)"
    )

    # -- 6. rollover toggle (Q14: hours only) ---------------------------
    add_column_if_missing(
        conn, "projects", "rollover_enabled", "INTEGER NOT NULL DEFAULT 1"
    )

    # -- 7. immutability triggers (Q1) ----------------------------------
    # A sent/paid invoice row may only change via sent->void or
    # sent->paid, and the frozen financial columns must be identical.
    # (void_reason / superseded_by_invoice_id / updated_at may change on
    # void; updated_at may change on the paid flip.)
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_invoices_immutable
        BEFORE UPDATE ON invoices
        FOR EACH ROW
        WHEN OLD.status IN ('sent', 'paid')
        BEGIN
            SELECT CASE
                WHEN OLD.status = 'paid' THEN
                    RAISE(ABORT,
                        'paid invoices are immutable')
                WHEN NEW.status NOT IN ('void', 'paid') THEN
                    RAISE(ABORT,
                        'sent invoices are immutable: only void or paid')
                WHEN NEW.number IS NOT OLD.number
                  OR NEW.project_id IS NOT OLD.project_id
                  OR NEW.currency IS NOT OLD.currency
                  OR NEW.client IS NOT OLD.client
                  OR NEW.notes IS NOT OLD.notes
                  OR NEW.due_date IS NOT OLD.due_date
                  OR NEW.subtotal_minor IS NOT OLD.subtotal_minor
                  OR NEW.discount_pct IS NOT OLD.discount_pct
                  OR NEW.discount_amount_minor
                        IS NOT OLD.discount_amount_minor
                  OR NEW.tax_pct IS NOT OLD.tax_pct
                  OR NEW.tax_amount_minor IS NOT OLD.tax_amount_minor
                  OR NEW.total_minor IS NOT OLD.total_minor
                  OR NEW.issued_at IS NOT OLD.issued_at
                  OR NEW.supersedes_invoice_id
                        IS NOT OLD.supersedes_invoice_id
                THEN RAISE(ABORT,
                    'sent invoice financial fields are immutable')
            END;
        END
        """
    )
    # Lines of a sent/paid invoice can never be updated or deleted.
    # (SQLite does not allow BEFORE UPDATE OR DELETE in one trigger;
    #  separate triggers are required.)
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_invoice_lines_immutable_upd
        BEFORE UPDATE ON invoice_lines
        FOR EACH ROW
        WHEN (SELECT status FROM invoices
              WHERE id = OLD.invoice_id) IN ('sent', 'paid')
        BEGIN
            SELECT RAISE(ABORT,
                'invoice lines are immutable once the invoice is sent');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_invoice_lines_immutable_del
        BEFORE DELETE ON invoice_lines
        FOR EACH ROW
        WHEN (SELECT status FROM invoices
              WHERE id = OLD.invoice_id) IN ('sent', 'paid')
        BEGIN
            SELECT RAISE(ABORT,
                'invoice lines are immutable once the invoice is sent');
        END
        """
    )
    # Lines can never be added to a sent/paid invoice.
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_invoice_lines_no_add_sent
        BEFORE INSERT ON invoice_lines
        FOR EACH ROW
        WHEN (SELECT status FROM invoices
              WHERE id = NEW.invoice_id) IN ('sent', 'paid')
        BEGIN
            SELECT RAISE(ABORT,
                'cannot add lines to a sent invoice');
        END
        """
    )
    # Q3 (SHOULD, enforced at DB layer): an entry's invoice link may go
    # NULL -> id (claim) or id -> NULL (void release), but never directly
    # from one non-NULL invoice to a different one.
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_timesheet_invoice_link_guard
        BEFORE UPDATE OF invoice_id ON timesheet_entries
        FOR EACH ROW
        WHEN OLD.invoice_id IS NOT NULL
         AND NEW.invoice_id IS NOT NULL
         AND OLD.invoice_id IS NOT NEW.invoice_id
        BEGIN
            SELECT RAISE(ABORT,
                'timesheet entry is already linked to an invoice');
        END
        """
    )
    # Payments may only be recorded on sent/paid invoices (Q4 allows
    # overpayment on paid invoices; drafts and voids reject payments).
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_invoice_payments_guard
        BEFORE INSERT ON invoice_payments
        FOR EACH ROW
        WHEN (SELECT status FROM invoices
              WHERE id = NEW.invoice_id) NOT IN ('sent', 'paid')
        BEGIN
            SELECT RAISE(ABORT,
                'payments can only be recorded on sent invoices');
        END
        """
    )

    # -- 8. settings (Q7/Q12/Q15) ---------------------------------------
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES "
        "('rollover_cap_pct', '50'), "
        "('invoice_due_days', '14'), "
        "('default_tax_pct', '0'), "
        "('default_discount_pct', '0')"
    )


def _migration_0009_gamification(conn: sqlite3.Connection) -> None:
    """Migration 9: Phase 11 gamification (XP ledger + badges).

    Integer-only XP math (bonuses as integer deltas, streak multiplier
    as x10 integer). Chronotype peak window lives in the settings
    table (no new table). Idempotent: IF NOT EXISTS everywhere.

    Council remediation (Phase 11 audit):
    - xp_ledger.session_id FK -> focus_sessions(id) ON DELETE CASCADE
      (phantom XP purged if a session is deleted).
    - badges.session_id FK -> focus_sessions(id) ON DELETE SET NULL
      (earned badges survive session archival).
    - uq_xp_ledger_session partial unique index: double-award proof.
    - idx_activities_ts: bounded range scans for the depth gauge.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS xp_ledger (
            id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL
                REFERENCES focus_sessions(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            base_xp INTEGER NOT NULL,
            peak_bonus INTEGER NOT NULL DEFAULT 0,
            clean_bonus INTEGER NOT NULL DEFAULT 0,
            streak_mult_x10 INTEGER NOT NULL DEFAULT 10,
            total_xp INTEGER NOT NULL,
            awarded_at TEXT NOT NULL
        )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_xp_ledger_day ON xp_ledger(day)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_xp_ledger_session "
        "ON xp_ledger(session_id) WHERE session_id IS NOT NULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS badges (
            id INTEGER PRIMARY KEY,
            badge_key TEXT NOT NULL UNIQUE,
            session_id INTEGER
                REFERENCES focus_sessions(id) ON DELETE SET NULL,
            awarded_at TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{}'
        )
        """)
    # idx_activities_ts: bounded range scans for the depth gauge.
    # Defensive: adversarial/partial base schemas may lack the column.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(activities)")}
    if "ts" in cols:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activities_ts "
            "ON activities(ts)")


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
    Migration(
        7,
        "0007_finance",
        _migration_0007_finance,
    ),
    Migration(
        8,
        "0008_invoicing",
        _migration_0008_invoicing,
    ),
    Migration(
        9,
        "0009_gamification",
        _migration_0009_gamification,
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
        except OSError as exc:
            logger.warning("could not prune old migration snapshot "
                           "%s: %s", path, exc)
        try:
            sidecar = Path(str(path) + ".sha256")
            sidecar.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not prune snapshot checksum %s: %s",
                           sidecar, exc)

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
                    # The step failure is logged just above; this is
                    # the distinct "rollback ALSO failed" signal.
                    logger.exception(
                        "Migration %d rollback also failed",
                        migration.version)
                raise step_err

        return current_version
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception as close_exc:
                logger.debug("closing the failed migration connection "
                             "failed: %s", close_exc)
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
            except OSError as exc:
                logger.warning("could not remove stale WAL files "
                               "before snapshot restore: %s", exc)

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
            except OSError as exc:
                logger.warning("could not remove stale WAL files "
                               "after snapshot restore: %s", exc)
        elif is_fresh and len(applied_by_this_call) > 0:
            try:
                target_path.unlink(missing_ok=True)
                Path(str(target_path) + "-wal").unlink(missing_ok=True)
                Path(str(target_path) + "-shm").unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not remove the half-migrated "
                               "fresh database: %s", exc)

        if isinstance(exc, MigrationError):
            raise exc
        raise MigrationError(
            f"Migration aborted and rolled back: {exc}"
        ) from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                logger.debug("closing the migration connection "
                             "failed: %s", exc)
