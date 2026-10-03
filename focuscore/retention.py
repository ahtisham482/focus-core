"""Roadmap 2.9: data-retention pruning + erase-all.

One job: enforce the owner's retention policy and wipe everything on
explicit request.

Policy (Ahtisham, 2026-10-02): keep N months of detailed activity
(default 12; options 3/6/12/24 or keep forever). Daily totals
(day_stats) are kept forever. Invoices/timesheets/money records are
never auto-deleted. A manual "erase all my data" path wipes every user
table except the schema itself.

Pruning runs at app startup only (launcher.maybe_prune), never on the
shield/enforcement path (Invariant I-1: no SQLite reads/writes on
enforcement-critical paths).
"""

import calendar
import logging
from datetime import date

logger = logging.getLogger("focuscore.retention")

RETENTION_SETTING_KEY = "retention_months"
DEFAULT_RETENTION_MONTHS = 12
FOREVER_VALUE = "forever"
# Options offered in the settings UI; None means "keep forever".
RETENTION_OPTIONS = (3, 6, 12, 24, None)

# Table -> SQL expression yielding the row's 'YYYY-MM-DD' day.
# activities/afk_intervals carry a day column; the rest derive it from
# their timestamp (stored ISO 'YYYY-MM-DD HH:MM:SS', which DATE()
# handles; anything unparseable yields NULL and is never pruned).
PRUNE_TABLES = {
    "activities": "day",
    "afk_intervals": "day",
    "focus_blocks": "DATE(ts)",
    # session_cycles BEFORE focus_sessions: session_cycles.session_id
    # REFERENCES focus_sessions(id) with no ON DELETE action, so the
    # parent cannot be deleted while its cycles exist (2026-10-03:
    # this ordering bug made startup pruning silently never run).
    "session_cycles": "DATE(started_at)",
    "focus_sessions": "DATE(started_at)",
    "alert_firings": "DATE(fired_at)",
    "block_passes": "DATE(started_at)",
}

# Every user table except the schema itself. Erase-all wipes these in
# dependency order (children before parents), so it holds with or
# without the ON DELETE CASCADE clauses:
# - invoice_lines before timesheet_entries (RESTRICT on entry_id);
# - timesheet_entries before invoices (RESTRICT on invoice_id);
# - project_budget_ledger before projects;
# - focus_blocks/session_cycles before focus_sessions.
ERASE_TABLES = (
    "invoice_lines",
    "invoice_payments",
    "timesheet_entries",
    "invoices",
    "project_budget_ledger",
    "projects",
    "focus_blocks",
    "session_cycles",
    "focus_sessions",
    "activities",
    "afk_intervals",
    "alert_firings",
    "alerts",
    "badges",
    "block_passes",
    "block_rules",
    "categories",
    "day_stats",
    "finance_audit_events",
    "goals",
    "invoice_counters",
    "orphaned_rows",
    "overrides",
    "settings",
    "xp_ledger",
)

# What the erase-confirmation screen lists, as (plain-English label,
# tables it covers). The union of the table sets MUST equal
# ERASE_TABLES exactly -- test_erase_confirmation_list_covers_every_
# wiped_table pins this, so adding a wiped table without listing it
# (or listing one that isn't wiped) fails the suite.
ERASE_BULLETS = (
    ("All activity history and daily totals",
     ("activities", "afk_intervals", "day_stats", "focus_blocks",
      "focus_sessions", "session_cycles", "block_passes")),
    ("All invoices, timesheets and money records",
     ("invoices", "invoice_lines", "invoice_payments",
      "invoice_counters", "timesheet_entries", "projects",
      "project_budget_ledger", "finance_audit_events")),
    ("Goals, badges, XP and all settings",
     ("goals", "badges", "xp_ledger", "settings", "overrides",
      "categories")),
    ("Blocking rules, alerts and quarantine records",
     ("block_rules", "alerts", "alert_firings", "orphaned_rows")),
)


def _months_ago(today, months):
    """Subtract calendar months, clamping to the month's last day
    (e.g. 2026-03-31 minus 1 month -> 2026-02-28)."""
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(today.day, last_day))


def get_retention_months(path=None):
    """Retention window in months, or None for keep-forever.

    Reads the ``retention_months`` setting; missing or invalid values
    fall back to the 12-month default. Never raises.
    """
    from . import store

    raw = store.get_setting(
        RETENTION_SETTING_KEY, str(DEFAULT_RETENTION_MONTHS), path=path
    )
    if raw == FOREVER_VALUE:
        return None
    try:
        months = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_MONTHS
    return months if months in RETENTION_OPTIONS else DEFAULT_RETENTION_MONTHS


def set_retention_months(months, path=None):
    """Store the retention window (3/6/12/24, or None for forever).

    Returns True when stored; False for anything else (nothing written).
    """
    from . import store

    if months is None:
        value = FOREVER_VALUE
    elif months in RETENTION_OPTIONS:
        value = str(months)
    else:
        return False
    store.set_setting(RETENTION_SETTING_KEY, value, path=path)
    return True


def _ensure_day_stats(conn, day):
    """Make sure a day_stats row exists for ``day``.

    day_stats rows are normally written by the daily pipeline run; if
    one is missing (e.g. the app crashed between saving events and
    saving totals), compute it from the detail rows so pruning the
    detail never loses history the totals don't cover. Returns True
    when a row was computed.
    """
    exists = conn.execute(
        "SELECT 1 FROM day_stats WHERE day = ?", (day,)
    ).fetchone()
    if exists:
        return False
    total_seconds = conn.execute(
        "SELECT COALESCE(SUM(duration), 0) FROM activities WHERE day = ?",
        (day,),
    ).fetchone()[0]
    afk_seconds = conn.execute(
        "SELECT COALESCE(SUM(duration_seconds), 0) FROM afk_intervals "
        "WHERE day = ? AND status = 'afk'",
        (day,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO day_stats (day, afk_seconds, total_seconds) "
        "VALUES (?, ?, ?)",
        (day, afk_seconds, total_seconds),
    )
    return True


def prune_older_than(months, path=None, today=None):
    """Delete detail rows older than ``months``; return per-table counts.

    "Older than N months" means day < (today - N calendar months), at
    day granularity. Every distinct old day gets its day_stats row
    first (computed when missing). day_stats itself, invoices,
    timesheets and all other non-detail tables are never touched.
    ``months=None`` (keep forever) is a no-op returning {}.
    """
    from . import store

    if months is None:
        return {}
    today = today or date.today()  # noqa: DTZ011 -- day-granularity
    # retention cutoff is inherently local-time; the activities `day`
    # column the cutoff compares against is local too.
    cutoff = _months_ago(today, months).isoformat()
    store.init_db(path)
    conn = store.get_db(path)
    try:
        # 1. day_stats guard: no day's detail is deleted unless its
        #    totals row exists (computed first when missing).
        old_days = set()
        for table, day_expr in PRUNE_TABLES.items():
            query = (
                f"SELECT DISTINCT {day_expr} FROM {table} "
                f"WHERE {day_expr} < ? AND {day_expr} IS NOT NULL"
            )
            for (day,) in conn.execute(query, (cutoff,)):
                old_days.add(day)
        for day in sorted(old_days):
            _ensure_day_stats(conn, day)
        # 2. delete the old detail rows.
        counts = {}
        for table, day_expr in PRUNE_TABLES.items():
            cur = conn.execute(
                f"DELETE FROM {table} WHERE {day_expr} < ?",
                (cutoff,),
            )
            counts[table] = cur.rowcount
        conn.commit()
        return counts
    finally:
        conn.close()


def maybe_prune(path=None):
    """Prune per the retention setting. For app startup; never raises."""
    try:
        months = get_retention_months(path=path)
        return prune_older_than(months, path=path)
    except Exception:  # pruning must never break launch
        logger.exception("retention prune failed")
        return {}


def erase_all_data(path=None):
    """Wipe every user table (schema kept). One transaction.

    This is the "I'm leaving" button: activity, day_stats, money
    records, gamification, and settings (back to defaults -- deleted
    rows read back as each setting's code default). Not reversible.

    Also removes the shield emergency pass file
    (passes.fallback.jsonl): it holds real activity data written when
    SQLite was unreachable, so "erase everything" must not leave it
    behind. Crash diagnostics (last-crash.json, crash-pending.json) are
    removed too -- the typed ERASE confirmation supersedes any pending
    crash offer. Local backups intentionally survive -- the UI tells users
    to back up first.
    """
    import os

    from . import crashreport as crash_mod
    from . import shield as shield_mod
    from . import store

    store.init_db(path)
    conn = store.get_db(path)
    try:
        for table in ERASE_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    try:
        os.unlink(shield_mod.fallback_passes_path())
    except FileNotFoundError:
        # Expected: usually nothing was ever written there.
        logger.debug("erase-all: no fallback pass file to remove")
    except OSError:
        logger.exception("erase-all could not remove fallback pass file")
    crash_dir = crash_mod._folder()
    for name in (crash_mod.CRASH_NAME, crash_mod.PENDING_NAME):
        try:
            os.unlink(crash_dir / name)
        except FileNotFoundError:
            logger.debug("erase-all: no %s to remove", name)
        except OSError:
            logger.exception("erase-all could not remove %s", name)
