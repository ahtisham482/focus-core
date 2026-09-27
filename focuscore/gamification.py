"""Balanced gamification engine (Phase 11).

- Daily focus rings: SVG completion ring vs configurable target.
- Productivity XP: integer-only math, awarded once per completed session.
- Milestone badges: Centurion, Iron Will, Peak Master.

XP formula (all integers):
    base        = floor(focus_minutes)
    peak_bonus  = base // 2   (only if session overlapped peak window)
    clean_bonus = base // 4   (only if blocks_count == 0)
    subtotal    = base + peak_bonus + clean_bonus
    total       = (subtotal * streak_mult_x10) // 10
    streak_mult_x10 = min(20, 10 + consecutive_active_days)  # 1.0x..2.0x

Writes happen once, in end_session(), a single transaction -- off every
hot path. No floats anywhere in the ledger.
"""

from datetime import datetime

from . import store


# ------------------------------------------------------------- settings ---

def daily_target_minutes(db_path=None):
    try:
        return max(1, int(store.get_setting(
            "daily_focus_target_min", "120", path=db_path)))
    except (TypeError, ValueError):
        return 120


def set_daily_target_minutes(minutes, db_path=None):
    store.set_setting("daily_focus_target_min", str(max(1, int(minutes))),
                      path=db_path)


# ---------------------------------------------------------- daily ring ---

def daily_minutes(day, db_path=None):
    """Sum of focus minutes for completed sessions on `day`
    (YYYY-MM-DD)."""
    from . import focus as focus_mod
    total = 0.0
    conn = store.get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM focus_sessions WHERE status = 'completed' "
            "AND substr(started_at, 1, 10) = ?", (day,)).fetchall()
    finally:
        conn.close()
    for (sid,) in rows:
        try:
            summary = focus_mod.session_summary(sid, db_path=db_path)
            total += summary.get("focus_minutes", 0.0)
        except Exception:
            continue
    return total


def daily_ring(day=None, db_path=None):
    """Return {'minutes', 'target', 'fraction'} for the ring UI."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    minutes = daily_minutes(day, db_path=db_path)
    target = daily_target_minutes(db_path=db_path)
    return {"minutes": minutes, "target": target,
            "fraction": min(1.0, minutes / target) if target else 0.0}


# ------------------------------------------------------------------ XP ---

def _streak_mult_x10(db_path=None):
    from . import focus as focus_mod
    try:
        days = focus_mod.current_streak(db_path=db_path) or 0
    except Exception:
        days = 0
    return min(20, 10 + max(0, int(days)))


def preview_xp(session_id, db_path=None):
    """Compute the XP award for a session without writing. Returns the
    full breakdown dict."""
    from . import chronotype, focus as focus_mod
    summary = focus_mod.session_summary(session_id, db_path=db_path)
    if "error" in summary:
        return {"error": summary["error"]}
    session = store.get_session(session_id, path=db_path) or {}

    base = int(summary.get("focus_minutes", 0.0))  # floor
    peak = chronotype.session_overlaps_peak(
        session.get("started_at"), session.get("ended_at"),
        db_path=db_path)
    clean = (summary.get("blocks_count", 0) or 0) == 0

    peak_bonus = base // 2 if peak else 0
    clean_bonus = base // 4 if clean else 0
    subtotal = base + peak_bonus + clean_bonus
    mult_x10 = _streak_mult_x10(db_path=db_path)
    total = (subtotal * mult_x10) // 10
    return {"base_xp": base, "peak": bool(peak), "peak_bonus": peak_bonus,
            "clean": bool(clean), "clean_bonus": clean_bonus,
            "streak_mult_x10": mult_x10, "total_xp": total}


def award_session_xp(session_id, db_path=None):
    """Award XP for a completed session.

    Council remediation (Phase 11 audit):
    - BEGIN IMMEDIATE serializes concurrent end_session() calls.
    - The session row is verified completed (rowcount == 1) BEFORE any
      XP math or commit -- anything else aborts.
    - Anti-farming: sessions with < 5 focus minutes earn nothing.
    - INSERT OR IGNORE + uq_xp_ledger_session makes double-clicks
      strictly idempotent.
    """
    conn = store.get_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Guard: the session must exist and be completed. rowcount == 1
        # proves exactly one row qualified -- verified before any XP
        # calculation or commit.
        cur = conn.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE id = ? "
            "AND status = 'completed'", (session_id,))
        if cur.fetchone()[0] != 1:
            conn.rollback()
            return {"awarded": False, "reason": "not_completed"}
        # Session is completed: calculate the breakdown (reads only).
        breakdown = preview_xp(session_id, db_path=db_path)
        if "error" in breakdown:
            conn.rollback()
            return breakdown
        # Anti-farming: minimum 5 minutes (300s) of focus work.
        if breakdown["base_xp"] < 5:
            conn.rollback()
            breakdown = dict(breakdown)
            breakdown["awarded"] = False
            breakdown["reason"] = "below_minimum"
            return breakdown
        session = store.get_session(session_id, path=db_path) or {}
        day = str(session.get("started_at") or "")[:10]
        now_iso = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT OR IGNORE INTO xp_ledger (session_id, day, base_xp, "
            "peak_bonus, clean_bonus, streak_mult_x10, total_xp, "
            "awarded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, day, breakdown["base_xp"],
             breakdown["peak_bonus"], breakdown["clean_bonus"],
             breakdown["streak_mult_x10"], breakdown["total_xp"],
             now_iso))
        if cur.rowcount != 1:
            # Already awarded (UNIQUE session_id) -- idempotent no-op.
            conn.rollback()
            breakdown = dict(breakdown)
            breakdown["awarded"] = False
            breakdown["reason"] = "already_awarded"
            return breakdown
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"awarded": False, "reason": "db_error"}
    finally:
        conn.close()
    breakdown["awarded"] = True
    # Badge check after every award.
    new_badges = check_badges(session_id, db_path=db_path)
    breakdown["new_badges"] = new_badges
    return breakdown


def lifetime_xp(db_path=None):
    conn = store.get_db(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_xp), 0) FROM xp_ledger").fetchone()
        return int(row[0])
    finally:
        conn.close()


def lifetime_focus_hours(db_path=None):
    """Total focus hours across completed sessions (for Centurion)."""
    from . import focus as focus_mod
    conn = store.get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM focus_sessions "
            "WHERE status = 'completed'").fetchall()
    finally:
        conn.close()
    total = 0.0
    for (sid,) in rows:
        try:
            total += focus_mod.session_summary(
                sid, db_path=db_path).get("focus_minutes", 0.0)
        except Exception:
            continue
    return total / 60.0


def peak_session_count(db_path=None):
    """Completed sessions overlapping the peak window (for Peak Master)."""
    from . import chronotype
    conn = store.get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT started_at, ended_at FROM focus_sessions "
            "WHERE status = 'completed'").fetchall()
    finally:
        conn.close()
    count = 0
    for started_at, ended_at in rows:
        try:
            if chronotype.session_overlaps_peak(
                    started_at, ended_at, db_path=db_path):
                count += 1
        except Exception:
            continue
    return count


# --------------------------------------------------------------- badges ---

BADGES = {
    "centurion": {
        "name": "Centurion",
        "desc": "100 lifetime focus hours. Unstoppable.",
    },
    "iron_will": {
        "name": "Iron Will",
        "desc": "A hardcore session with zero distractions blocked — "
                "because there were none to block.",
    },
    "peak_master": {
        "name": "Peak Master",
        "desc": "10 focus sessions in your peak energy window.",
    },
}


def earned_badges(db_path=None):
    conn = store.get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT badge_key, awarded_at, meta FROM badges").fetchall()
        return {r[0]: {"awarded_at": r[1], "meta": r[2]} for r in rows}
    finally:
        conn.close()


def _grant_badge(badge_key, db_path=None, meta="{}", session_id=None):
    conn = store.get_db(db_path)
    try:
        try:
            conn.execute(
                "INSERT INTO badges (badge_key, session_id, awarded_at, "
                "meta) VALUES (?, ?, ?, ?)",
                (badge_key, session_id,
                 datetime.now().isoformat(timespec="seconds"), meta))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False  # already earned
    finally:
        conn.close()


def check_badges(session_id, db_path=None):
    """Evaluate badge conditions after a session. Returns list of newly
    awarded badge keys."""
    from . import focus as focus_mod
    newly = []
    earned = earned_badges(db_path=db_path)

    if "centurion" not in earned and lifetime_focus_hours(
            db_path=db_path) >= 100:
        if _grant_badge("centurion", db_path=db_path,
                        session_id=session_id):
            newly.append("centurion")

    if "peak_master" not in earned and peak_session_count(
            db_path=db_path) >= 10:
        if _grant_badge("peak_master", db_path=db_path,
                        session_id=session_id):
            newly.append("peak_master")

    if "iron_will" not in earned:
        session = store.get_session(session_id, path=db_path) or {}
        summary = focus_mod.session_summary(session_id, db_path=db_path)
        hardcore = (session.get("enforcement_mode") == "hardcore")
        clean = (summary.get("blocks_count", 0) or 0) == 0
        completed = session.get("status") == "completed"
        if hardcore and clean and completed:
            if _grant_badge("iron_will", db_path=db_path,
                            session_id=session_id):
                newly.append("iron_will")
    return newly
