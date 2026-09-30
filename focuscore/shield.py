"""focuscore/shield.py - Hardcore distraction shield.

Two halves, kept strictly apart (the blocker.py precedent):

1. PURE POLICY (unit-testable, no OS calls): is_protected(),
   rule_is_active(), pass_active(), matching_rules(), resolve_action().
2. DAEMON (Windows threads): an event-driven worker thread
   (SetWinEventHook + message pump + policy engine) and a main-thread
   tkinter UI owner (HUD + overlays), talking over queue.Queue.

Qwen audit remediations implemented: event-driven detection (R1),
elevation fail-open (R2), named-mutex singleton (R3), fullscreen
bypass + multi-monitor + scoped keyboard swallow (R4), tkinter
threading split (R5), expanded protected list (R6), pass resilience
(R7).

Sprint 4 (v1.12.0) hardening -- Qwen binding verdict:
INVARIANT I-1 (Enforcement Independence): enforcement operates
exclusively against in-memory state. The worker thread performs ZERO
SQLite calls on its critical path; it reads from an immutable
RulesSnapshot refreshed by a background task. Telemetry persistence is
a separate, failure-tolerant writer thread. If the write path is
blocked, slow, or dead, enforcement continues unaffected.

Usage:
    python -m focuscore.shield --run            # shield daemon
    python -m focuscore.shield --run --session-only   # session guard
"""

import argparse
import dataclasses
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from . import paths
from .win32 import ELEVATED_UNKNOWN, FALLBACK_POLL_SECONDS

# Roadmap 0.3: log failures that used to be swallowed silently.
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (documented, Phase 6 precedent)
# ---------------------------------------------------------------------------

POLL_SECONDS = 5             # classify cadence only when the hook is down
NOTIFY_DEDUPE_SECONDS = 60   # one notification/overlay per app per minute
LOCK_SECONDS = 30            # hardcore overlay lock duration
PASS_DEFAULT_MINUTES = 5     # emergency pass length
HUD_REFRESH_MS = 2000        # HUD redraw cadence
UI_DRAIN_MS = 100            # UI thread queue poll (Qwen R5)
WORKER_IDLE_SECONDS = 0.5    # worker loop granularity

# Enforcement actions, weakest -> strongest.
ACTIONS = ("allow", "soft", "firm", "hardcore")
ACTION_RANK = {"allow": 0, "soft": 1, "firm": 2, "hardcore": 3}

# Session enforcement_mode -> action mapping.
SESSION_ACTION = {"strict": "soft", "hardcore": "hardcore"}

# Sentinel session_id for rule-only blocks (focus_blocks.session_id is
# NOT NULL by schema; 0 means "no session -- the always-on shield").
RULE_ONLY_SESSION_ID = 0

# Processes the shield must never touch (Qwen R6). Matched
# case-insensitively against the lowercased exe basename. Includes the
# dynamic ELEVATED_UNKNOWN sentinel (R2: unidentifiable -> fail open).
PROTECTED_PROCESSES = frozenset({
    "explorer.exe", "taskmgr.exe", "dwm.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lockapp.exe", "logonui.exe",
    "searchui.exe", "startmenuexperiencehost.exe",
    "sihost.exe", "shellexperiencehost.exe",
    "focuscore.exe", "focus core.exe", "python.exe", "pythonw.exe",
    "aw-qt.exe", "aw-qt.exe",
    "unins000.exe", "setup.exe", "consent.exe",
    ELEVATED_UNKNOWN.lower(),
})

# In-memory emergency passes (R7: used when SQLite is unreachable).
_MEMORY_PASSES = []
_MEMORY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Sprint 4 (v1.12.0): Enforcement Independence (Qwen I-1).
#
# The worker thread NEVER touches SQLite. All enforcement inputs live in
# an immutable RulesSnapshot, refreshed by a background task every
# SNAPSHOT_REFRESH_SECONDS via a single-pointer atomic swap (the GIL
# makes the swap atomic). On refresh failure the worker keeps using the
# stale snapshot -- enforcement degrades gracefully, never stops.
# ---------------------------------------------------------------------------

SNAPSHOT_REFRESH_SECONDS = 45  # background refresh cadence (30-60s)
TELEMETRY_QUEUE_MAXSIZE = 10000  # bounded telemetry queue (item 3)
TELEMETRY_BATCH_SIZE = 500  # writer flush batch
TELEMETRY_SPILL_MAX_FILES = 3  # rotated JSONL spill files
TELEMETRY_SPILL_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per spill file
WRITER_LIVENESS_TIMEOUT = 180  # restart writer if silent this long
WORKER_LIVENESS_CHECK_SECONDS = 30  # UI supervisor check cadence
WORKER_LIVENESS_SILENCE_SECONDS = 90  # restart worker after this
UI_QUEUE_MAXSIZE = 256  # ui_q cap with drop-oldest (item 4)


@dataclasses.dataclass(frozen=True)
class RulesSnapshot:
    """Immutable enforcement inputs. Built by _build_snapshot() on the
    refresher thread; read by the worker thread. Frozen so the worker
    can never mutate what the refresher built."""
    rules: tuple  # block rule dicts (enabled, already filtered)
    overrides: tuple  # (app_lower, category) override pairs
    session: object  # active session dict or None
    cycle: object  # active pomodoro cycle dict or None
    active_pass: object  # emergency pass dict or None
    hud_enabled: bool
    force_hardcore: bool = False  # Phase 11: peak-window escalation,
    # baked in by the refresher (I-1 safe: worker only reads it).
    generated_at_monotonic: float = 0.0  # council remediation: worker
    # fail-safes force_hardcore=False when the snapshot is older than
    # 120s or the peak window has ended (purely in-memory, 0 SQLite).
    peak_window_end_iso: str = ""  # today's peak end, ISO; "" = none
    fetched_at_mono: float = 0.0  # time.monotonic() when built
    # (kept for Sprint 4 test compat; mirrors generated_at_monotonic)


@dataclasses.dataclass(frozen=True)
class UICommand:
    """Frozen UI message: plain data only. No Tcl/Tk objects, no locks,
    no callables -- the UI thread must never touch worker-owned objects
    (Qwen item 4)."""
    cmd: str
    label: str = ""
    app: str = ""
    locked: bool = False
    session_id: int = 0
    db_path: str = ""
    payload: object = None


def _build_snapshot(db_path):
    """Read everything enforcement needs from SQLite. Runs ONLY on the
    refresher thread -- never on the worker's critical path."""
    from . import focus as focus_mod
    from . import store
    # Settle pomodoro cycles first so the snapshot sees clean state.
    try:
        focus_mod.settle_session(db_path=db_path)
    except Exception:
        # Roadmap 0.3: refresher-thread settle used to fail with no trace.
        logger.exception("refresher settle_session failed")
    try:
        rules = store.get_block_rules(path=db_path, only_enabled=True)
    except Exception:
        rules = []
    try:
        overrides = store.get_overrides(path=db_path)
    except Exception:
        overrides = {}
    try:
        session = focus_mod.get_active_session(db_path=db_path)
    except Exception:
        session = None
    cycle = None
    if session and session.get("status") == "active" \
            and session.get("session_type") == "pomodoro":
        try:
            cycle = store.get_active_cycle(session.get("id"),
                                           path=db_path)
        except Exception:
            cycle = None
    try:
        active_pass = pass_active(now=datetime.now(), db_path=db_path)
    except Exception:
        # Roadmap 0.3: the pass check used to fail with no trace.
        logger.exception("snapshot pass_active lookup failed")
        active_pass = None
    try:
        hud_enabled = store.get_setting("hud_enabled", "1",
                                        path=db_path) == "1"
    except Exception:
        hud_enabled = True
    # Freeze overrides as sorted tuples for the frozen dataclass.
    overrides_t = tuple(sorted(
        (str(k).lower(), str(v)) for k, v in (overrides or {}).items()))
    # Phase 11: peak-window escalation. Read on the refresher thread
    # (one settings read, off the critical path); the worker only ever
    # reads the baked-in flag -- I-1 holds.
    try:
        from . import chronotype
        force_hardcore = chronotype.is_peak_now(db_path=db_path)
        peak_end_iso = chronotype.peak_window_end_iso(db_path=db_path)
    except Exception:
        force_hardcore = False
        peak_end_iso = ""
    now_mono = time.monotonic()
    return RulesSnapshot(
        rules=tuple(rules or []),
        overrides=overrides_t,
        session=session,
        cycle=cycle,
        active_pass=active_pass,
        hud_enabled=hud_enabled,
        force_hardcore=force_hardcore,
        generated_at_monotonic=now_mono,
        peak_window_end_iso=peak_end_iso,
        fetched_at_mono=now_mono,
    )


def _snapshot_force_hardcore(snapshot):
    """Resolve the effective peak-window escalation from a snapshot.

    Council remediation: fail-safe to False when the snapshot is stale
    (>120s old) or the peak window has ended. Purely in-memory
    evaluation -- 0 SQLite, I-1 holds.
    """
    if snapshot is None or not getattr(snapshot, "force_hardcore", False):
        return False
    gen = getattr(snapshot, "generated_at_monotonic", 0.0) or 0.0
    fresh = (time.monotonic() - gen) <= 120.0
    end_iso = getattr(snapshot, "peak_window_end_iso", "") or ""
    not_expired = (not end_iso) or (
        datetime.now().isoformat(timespec="seconds") < end_iso)
    return bool(fresh and not_expired)


def _empty_snapshot():
    """Fail-open starting snapshot before the first refresh lands."""
    now_mono = time.monotonic()
    return RulesSnapshot(rules=(), overrides=(), session=None,
                         cycle=None, active_pass=None, hud_enabled=True,
                         generated_at_monotonic=now_mono,
                         fetched_at_mono=now_mono)


class SnapshotHolder:
    """Single-pointer atomic swap holder. The GIL makes attribute
    assignment atomic; readers never block."""

    def __init__(self):
        self.current = _empty_snapshot()

    def get(self):
        return self.current

    def swap(self, snapshot):
        self.current = snapshot


def _snapshot_refresher(stop_event, holder, db_path,
                        interval=SNAPSHOT_REFRESH_SECONDS):
    """Background thread: rebuild the snapshot every ``interval``
    seconds. Any failure keeps the stale snapshot -- enforcement never
    stops because a refresh failed."""
    while not stop_event.is_set():
        try:
            holder.swap(_build_snapshot(db_path))
        except Exception as exc:
            print("shield snapshot refresh failed: %s" % exc,
                  file=sys.stderr)
        stop_event.wait(interval)


# ---------------------------------------------------------------------------
# Sprint 4 (v1.12.0): Telemetry writer thread (Qwen item 3).
#
# Enforcement never writes to SQLite. Block events go onto a bounded
# queue; a dedicated writer thread batches them (~500) into the DB. On
# saturation or write failure, events spill to ephemeral rotated JSONL
# (shield_spill.jsonl, 3 files x 5 MB). The spill is best-effort
# telemetry -- losing it never affects enforcement.
# ---------------------------------------------------------------------------

def _spill_dir():
    try:
        d = paths.user_data_dir()
    except Exception:
        d = os.path.expanduser("~")
    return str(d)


def _spill_path(index):
    return os.path.join(_spill_dir(),
                        "shield_spill.%d.jsonl" % index)


def _spill_event(event):
    """Append one telemetry event to the rotated spill files."""
    try:
        # Find the first spill file under the size cap.
        target = None
        for i in range(TELEMETRY_SPILL_MAX_FILES):
            p = _spill_path(i)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            if size < TELEMETRY_SPILL_MAX_BYTES:
                target = p
                break
        if target is None:
            # All full: rotate (drop oldest, shift down).
            for i in range(TELEMETRY_SPILL_MAX_FILES - 1):
                try:
                    os.replace(_spill_path(i + 1), _spill_path(i))
                except OSError as exc:
                    # A missing slot mid-rotation is normal; DEBUG.
                    logger.debug("telemetry spill rotation step "
                                 "failed: %s", exc)
            target = _spill_path(TELEMETRY_SPILL_MAX_FILES - 1)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except Exception:
        # Spill is best-effort (never raise), but a lost event should
        # leave a trace: telemetry silently vanishing is undebuggable.
        logger.exception("telemetry spill write failed; event lost")


def _flush_telemetry_batch(batch, db_path):
    """Write one batch of telemetry events to SQLite. Raises on
    failure so the caller can spill instead."""
    from . import store
    conn = store.get_db(db_path)
    try:
        for event in batch:
            kind = event.get("kind")
            if kind == "block":
                store.record_block(
                    event.get("session_id")
                    if event.get("session_id") else RULE_ONLY_SESSION_ID,
                    event.get("at"), event.get("app"),
                    event.get("title"), event.get("url"),
                    event.get("score"), event.get("category"),
                    action_taken=event.get("action_taken"),
                    process_name=event.get("process_name"),
                    window_handle=event.get("hwnd"), path=db_path,
                    _conn=conn)
            elif kind == "intercept":
                store.increment_intercepted(event.get("session_id"),
                                            path=db_path, _conn=conn)
        conn.commit()
    finally:
        conn.close()


def _telemetry_writer(stop_event, telemetry_q, db_path, liveness_event):
    """Dedicated writer thread: batch telemetry into SQLite, spill to
    JSONL on saturation/failure. Sets liveness_event every loop."""
    batch = []
    while not stop_event.is_set():
        liveness_event.set()
        try:
            event = telemetry_q.get(timeout=5)
            batch.append(event)
            while len(batch) < TELEMETRY_BATCH_SIZE:
                try:
                    batch.append(telemetry_q.get_nowait())
                except queue.Empty:
                    break
            if len(batch) >= TELEMETRY_BATCH_SIZE:
                try:
                    _flush_telemetry_batch(batch, db_path)
                except Exception:
                    for e in batch:
                        _spill_event(e)
                batch = []
        except queue.Empty:
            if batch:
                try:
                    _flush_telemetry_batch(batch, db_path)
                except Exception:
                    for e in batch:
                        _spill_event(e)
                batch = []
        except Exception:
            # Never let the writer die on a bad event.
            batch = []
    # Final flush on shutdown.
    if batch:
        try:
            _flush_telemetry_batch(batch, db_path)
        except Exception:
            for e in batch:
                _spill_event(e)
    liveness_event.set()


# ---------------------------------------------------------------------------
# Pure policy
# ---------------------------------------------------------------------------

def is_protected(process_name):
    """True when the shield must never touch this process.

    Case-insensitive. Unknown/empty names are NOT protected here --
    callers treat missing identity as "do nothing" separately; but
    ELEVATED_UNKNOWN (R2) is always protected (fail open).
    """
    name = (process_name or "").strip().lower()
    if not name:
        return False
    if name == ELEVATED_UNKNOWN.lower():
        return True
    return name in PROTECTED_PROCESSES


def rule_is_active(rule, now):
    """True when a block rule's schedule covers ``now``.

    rule: mapping with "days" ('all' or CSV of 0-6, Mon=0),
    "start_time"/"end_time" ('HH:MM' or empty/None = all day).
    Overnight windows (22:00-06:00) wrap past midnight. Pure.
    """
    days = str(rule.get("days") or "all").strip().lower()
    if days != "all":
        try:
            wanted = {int(d) for d in days.split(",")
                      if d.strip() != ""}
        except ValueError:
            return False
        if now.weekday() not in wanted:
            return False
    start = str(rule.get("start_time") or "").strip()
    end = str(rule.get("end_time") or "").strip()
    if not start or not end:
        return True
    try:
        sh, sm = int(start[0:2]), int(start[3:5])
        eh, em = int(end[0:2]), int(end[3:5])
    except (ValueError, IndexError):
        return False
    cur = now.hour * 60 + now.minute
    s, e = sh * 60 + sm, eh * 60 + em
    if s == e:
        return True  # degenerate: treat as all day
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e  # overnight wrap


def _pass_covers(p, now):
    try:
        start = datetime.fromisoformat(p["started_at"])
        minutes = float(p.get("minutes") or 0)
    except (ValueError, TypeError, KeyError):
        return False
    return start <= now < start + timedelta(minutes=minutes)


def pass_active(now=None, db_path=None):
    """The active emergency pass, or None.

    Checks SQLite, then the in-memory fallback, then the JSONL fallback
    file (R7). A pass must NEVER fail closed: any readable valid pass
    counts.
    """
    from . import store
    now = now or datetime.now()
    try:
        p = store.get_active_pass(now=now, path=db_path)
        if p:
            return p
    except Exception:
        # Roadmap 0.3: primary pass lookup failure was silent before the
        # in-memory fallback.
        logger.exception("get_active_pass failed; trying memory fallback")
        pass
    with _MEMORY_LOCK:
        mem = list(_MEMORY_PASSES)
    for p in mem:
        if _pass_covers(p, now):
            return p
    try:
        for p in _read_fallback_passes():
            if _pass_covers(p, now):
                return p
    except Exception:
        # Roadmap 0.3: JSONL fallback failure was silent.
        logger.exception("fallback pass file read failed")
        pass
    return None


def fallback_passes_path():
    """Where emergency passes go when SQLite is locked (R7)."""
    try:
        d = paths.user_data_dir()
    except Exception:
        d = os.path.expanduser("~")
    return os.path.join(str(d), "passes.fallback.jsonl")


def _read_fallback_passes():
    path = fallback_passes_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def remember_memory_pass(started_at_iso, minutes, reason):
    """Keep an emergency pass in memory (R7 fallback path)."""
    with _MEMORY_LOCK:
        _MEMORY_PASSES.append({"started_at": started_at_iso,
                               "minutes": minutes, "reason": reason})
        # Prune expired ones so the list cannot grow forever.
        now = datetime.now()
        _MEMORY_PASSES[:] = [p for p in _MEMORY_PASSES
                             if _pass_covers(p, now)]


def matching_rules(rules, app, process_name, category, now):
    """Active rules whose key matches this window. Pure."""
    app_l = (app or "").lower()
    proc_l = (process_name or "").lower()
    cat_l = (category or "").lower()
    out = []
    for r in rules or []:
        if not r.get("enabled", 1):
            continue
        if not rule_is_active(r, now):
            continue
        rtype = str(r.get("rule_type") or "").lower()
        key = str(r.get("key") or "").lower()
        if rtype == "app" and key and key in (app_l, proc_l):
            out.append(r)
        elif rtype == "category" and key and key == cat_l:
            out.append(r)
    return out


def resolve_action(session_blocked, session_enforcement, rule_actions,
                   process_name):
    """Pure priority: protected > session > rules > allow.

    Returns (action, source) where source is one of "protected",
    "session", "rule", "none".
    """
    if is_protected(process_name):
        return ("allow", "protected")
    if session_blocked:
        return (SESSION_ACTION.get(session_enforcement, "soft"),
                "session")
    if rule_actions:
        best = max(rule_actions,
                   key=lambda a: ACTION_RANK.get(a, 0))
        return (best, "rule")
    return ("allow", "none")


# ---------------------------------------------------------------------------
# One engine cycle (injectable sides, unit-testable)
# ---------------------------------------------------------------------------

_LAST_NOTIFIED = {}


def shield_once(state, client, categorize_fn, db_path=None, now=None,
                fg_info=None, notify_fn=None, minimize_fn=None,
                overlay_fn=None, fullscreen_fn=None,
                last_notified=None, session_only=False,
                foreground_fn=None, now_mono=None,
                resume_grace_until=None, snapshot=None,
                telemetry_q=None):
    """Run one enforcement cycle.

    Pure decision flow with injected side effects. ``session_only``
    skips always-on rules (the old per-session guard behavior).
    Returns a dict with "action" in ACTIONS plus context. Never raises:
    any unexpected failure returns {"action": "none", "reason": ...} --
    the shield fails open, never closed.

    Sprint 4 (Qwen I-1): when ``snapshot`` (a RulesSnapshot) is given,
    ZERO SQLite calls are made -- all enforcement inputs come from the
    snapshot. Telemetry (block records, intercept counts) goes onto
    ``telemetry_q`` instead of direct SQLite writes. When ``snapshot``
    is None, the legacy SQLite path runs (used by unit tests).
    """
    from . import blocker, focus as focus_mod, store
    from . import win32

    if last_notified is None:
        last_notified = _LAST_NOTIFIED
    try:
        now = now or datetime.now()

        # 1. Foreground identity (fast path).
        fg = fg_info
        if fg is None:
            try:
                fg = win32.get_foreground_info()
            except Exception:
                fg = None
        if not fg or not fg.get("hwnd"):
            return {"action": "none", "reason": "no foreground info"}
        process_name = fg.get("process_name") or ""
        hwnd = fg["hwnd"]

        # 2. Classify the current window via ActivityWatch.
        try:
            window = blocker.get_current_window(client, now=now)
        except Exception:
            window = None
        if not window:
            return {"action": "none", "reason": "no current window"}
        if is_protected(process_name):
            return {"action": "allow", "reason": "protected process",
                    "app": window["app"]}

        # 3. Emergency pass wins over everything (R7).
        # Sprint 4 (I-1): snapshot mode reads the pre-fetched pass;
        # legacy mode queries SQLite.
        try:
            if snapshot is not None:
                active_pass = snapshot.active_pass
            else:
                active_pass = pass_active(now=now, db_path=db_path)
        except Exception:
            # Roadmap 0.3: the decide-path pass check used to fail with
            # no trace (fail-open by design; now fail-open AND logged).
            logger.exception("decide pass lookup failed")
            active_pass = None
        if active_pass:
            return {"action": "allow", "reason": "emergency pass",
                    "app": window["app"]}

        # 3b. Resume grace (Phase 8 R2): for 60 s after a sleep resume,
        # enforcement stays paused. Fail open on any error.
        try:
            mono_now = time.monotonic() if now_mono is None else now_mono
            if resume_grace_until and mono_now < resume_grace_until:
                return {"action": "allow", "reason": "resume grace",
                        "app": window["app"]}
        except Exception:
            # Fail open (fall through to enforcement) -- but say so.
            logger.exception("resume-grace check failed; enforcing "
                             "normally")

        # 4. Session + rules.
        # Sprint 4 (I-1): snapshot mode uses pre-fetched session, cycle,
        # rules, and overrides. Zero SQLite on the enforcement path.
        try:
            if snapshot is not None:
                session = snapshot.session
            else:
                session = focus_mod.get_active_session(db_path=db_path)
        except Exception:
            session = None
        try:
            if snapshot is not None:
                overrides = dict(snapshot.overrides)
            else:
                overrides = store.get_overrides(path=db_path)
        except Exception:
            overrides = {}
        try:
            if snapshot is not None:
                rules = [] if session_only else list(snapshot.rules)
            else:
                rules = [] if session_only else store.get_block_rules(
                    path=db_path, only_enabled=True)
        except Exception:
            rules = []

        session_blocked = False
        session_enforcement = "strict"
        session_id = None
        # Phase 8 R2: pomodoro break soft stand-down. Session-driven
        # enforcement pauses during a break; global soft rules keep
        # applying; firm/hardcore rules are capped at soft.
        on_break = False
        if session and session.get("status") == "active" \
                and session.get("session_type") == "pomodoro":
            try:
                if snapshot is not None:
                    cycle = snapshot.cycle
                else:
                    cycle = store.get_active_cycle(session.get("id"),
                                                   path=db_path)
                on_break = bool(cycle and cycle.get("kind") == "break")
            except Exception:
                on_break = False
        if session and session.get("status") == "active" and not on_break:
            session_id = session.get("id")
            session_enforcement = session.get("enforcement_mode") \
                or "strict"
            # Phase 11: peak-window escalation. The refresher baked
            # force_hardcore into the snapshot (in-memory read only).
            # _snapshot_force_hardcore applies the staleness fail-safe
            # (council remediation): 0 SQLite, I-1 holds.
            if _snapshot_force_hardcore(snapshot):
                session_enforcement = "hardcore"
            try:
                blocked, score, category = blocker.is_blocked(
                    window["app"], window["title"], window["url"],
                    session.get("block_level") or "strict",
                    categorize_fn, overrides=overrides)
            except Exception:
                return {"action": "none",
                        "reason": "classification failed"}
            session_blocked = blocked
        else:
            try:
                _b, score, category = blocker.is_blocked(
                    window["app"], window["title"], window["url"],
                    "strict", categorize_fn, overrides=overrides)
            except Exception:
                return {"action": "none",
                        "reason": "classification failed"}

        matched = matching_rules(rules, window["app"], process_name,
                                 category, now)
        rule_actions = [r.get("action") or "soft" for r in matched
                        if (r.get("action") or "soft") in ACTION_RANK]
        if on_break:
            # Soft stand-down: firm/hardcore rules drop to soft.
            rule_actions = ["soft" if a in ("firm", "hardcore") else a
                            for a in rule_actions]

        action, source = resolve_action(
            session_blocked, session_enforcement, rule_actions,
            process_name)

        base = {"app": window["app"], "title": window["title"],
                "url": window["url"], "score": score,
                "category": category, "source": source,
                "process_name": process_name, "hwnd": hwnd,
                "break_standdown": on_break}
        if action == "allow":
            return {"action": "allow", **base}

        # 5. Dedupe: one notification/overlay per app per minute.
        app_key = (window["app"] or "unknown").lower()
        last = last_notified.get(app_key)
        if last is not None and \
                (now - last).total_seconds() < NOTIFY_DEDUPE_SECONDS:
            return {"action": action, "notified": False,
                    "reason": "deduped", **base}

        # 6. Execute, weakest side effect first.
        action_taken = "notified"
        label = (session or {}).get("label") or "Shield"
        if notify_fn is None:
            notify_fn = blocker._desktop_notify_block
        try:
            notify_fn(window["app"], window["title"], label)
        except Exception as exc:
            print("shield notification failed: %s" % exc,
                  file=sys.stderr)

        fullscreen = False
        if fullscreen_fn is None:
            fullscreen_fn = win32.is_fullscreen_window
        try:
            fullscreen = bool(fullscreen_fn(hwnd))
        except Exception:
            fullscreen = False

        if not fullscreen:
            # The overlay command goes to the UI thread in the real
            # daemon; in tests overlay_fn is a fake.
            if overlay_fn is None:
                overlay_fn = _queue_overlay
            try:
                overlay_fn(label=label, app=window["app"],
                           locked=(action == "hardcore"),
                           session_id=session_id, db_path=db_path)
                action_taken = "overlay"
            except Exception as exc:
                print("shield overlay failed: %s" % exc,
                      file=sys.stderr)
        else:
            print("shield: fullscreen window, overlay suspended "
                  "(minimize still applies for firm+)", file=sys.stderr)

        if action in ("firm", "hardcore"):
            # Double-check protection at the call site (R6) and never
            # punish a window the user already left.
            if not is_protected(process_name):
                if foreground_fn is None:
                    foreground_fn = win32.window_still_foreground
                still_there = True
                try:
                    still_there = bool(foreground_fn(hwnd))
                except Exception:
                    still_there = True
                if still_there:
                    if minimize_fn is None:
                        minimize_fn = win32.minimize_window
                    try:
                        if minimize_fn(hwnd):
                            action_taken = "minimized"
                    except Exception as exc:
                        print("shield minimize failed: %s" % exc,
                              file=sys.stderr)

        # 7. Record + counters.
        # Sprint 4 (I-1): telemetry NEVER goes to SQLite on the
        # enforcement path. Snapshot mode queues it for the writer
        # thread; legacy mode writes directly (unit tests).
        try:
            if snapshot is not None and telemetry_q is not None:
                try:
                    telemetry_q.put_nowait({
                        "kind": "block",
                        "session_id": session_id,
                        "at": now.isoformat(timespec="seconds"),
                        "app": window["app"], "title": window["title"],
                        "url": window["url"], "score": score,
                        "category": category,
                        "action_taken": action_taken,
                        "process_name": process_name, "hwnd": hwnd,
                    })
                    if session_id:
                        telemetry_q.put_nowait({
                            "kind": "intercept",
                            "session_id": session_id,
                        })
                except queue.Full:
                    # Bounded queue; drop rather than block. DEBUG:
                    # under load this is a flood, not an event.
                    logger.debug("intercept telemetry queue full; "
                                 "event dropped")
            else:
                store.record_block(
                    session_id if session_id else RULE_ONLY_SESSION_ID,
                    now.isoformat(timespec="seconds"),
                    window["app"], window["title"], window["url"],
                    score, category, action_taken=action_taken,
                    process_name=process_name, window_handle=hwnd,
                    path=db_path)
                if session_id:
                    store.increment_intercepted(session_id, path=db_path)
        except Exception as exc:
            print("shield record failed: %s" % exc, file=sys.stderr)

        last_notified[app_key] = now
        return {"action": action, "notified": True,
                "action_taken": action_taken,
                "fullscreen": fullscreen, **base}
    except Exception as exc:  # fail OPEN, never closed
        print("shield_once failed open: %s" % exc, file=sys.stderr)
        return {"action": "none", "reason": "internal error"}


def _queue_overlay(label, app, locked, session_id, db_path):
    """Default overlay_fn: hand the command to the UI thread.

    Sprint 4 (Qwen item 4): frozen UICommand, plain data only; the
    bounded queue drops the oldest on saturation (never blocks the
    worker)."""
    cmd = UICommand(cmd="overlay", label=label or "", app=app or "",
                    locked=bool(locked),
                    session_id=int(session_id or 0),
                    db_path=db_path or "")
    try:
        _UI_QUEUE.put_nowait(cmd)
    except queue.Full:
        try:
            _UI_QUEUE.get_nowait()  # drop oldest
        except queue.Empty:
            logger.debug("ui queue raced empty while making room")
        try:
            _UI_QUEUE.put_nowait(cmd)
        except queue.Full:
            logger.debug("ui queue still full; overlay dropped")


def _queue_ui_command(cmd):
    """Put a UICommand on the bounded UI queue (drop-oldest)."""
    try:
        _UI_QUEUE.put_nowait(cmd)
    except queue.Full:
        try:
            _UI_QUEUE.get_nowait()
        except queue.Empty:
            logger.debug("ui queue raced empty while making room")
        try:
            _UI_QUEUE.put_nowait(cmd)
        except queue.Full:
            logger.debug("ui queue still full; command dropped: %s",
                         cmd.cmd)


# Module-level UI queue, set by run_shield() before the worker starts.
# Sprint 4: bounded at UI_QUEUE_MAXSIZE with drop-oldest semantics.
_UI_QUEUE = queue.Queue(maxsize=UI_QUEUE_MAXSIZE)


# ---------------------------------------------------------------------------
# Daemon: worker thread (hook + pump + engine) + main UI thread (R5)
# ---------------------------------------------------------------------------

def kill_switch_path():
    """%LOCALAPPDATA%\\Focus Core\\shield.off -- stops the daemon."""
    try:
        d = paths.user_data_dir()
    except Exception:
        d = os.path.expanduser("~")
    return os.path.join(str(d), "shield.off")


def shield_off():
    """Engage the kill switch; the daemon exits within seconds."""
    try:
        with open(kill_switch_path(), "w", encoding="utf-8") as fh:
            fh.write("off")
        return True
    except Exception:
        return False


def shield_on():
    """Release the kill switch (caller re-spawns the daemon)."""
    try:
        if os.path.exists(kill_switch_path()):
            os.remove(kill_switch_path())
        return True
    except Exception:
        return False


def shield_killswitch_on():
    """True when the kill-switch file exists (daemon told to stop)."""
    try:
        return os.path.exists(kill_switch_path())
    except Exception:
        return False


def shield_daemon_running():
    """True when another live process holds the shield mutex."""
    try:
        from . import win32
        return bool(win32.mutex_held())
    except Exception:
        return False


def _worker_main(stop_event, event_q, ui_q, db_path, session_only,
                 snapshot_holder=None, telemetry_q=None,
                 worker_alive=None):
    """Worker thread: hook events + message pump + policy engine.

    Sprint 4 (Qwen I-1): the worker performs ZERO SQLite calls. It
    reads enforcement inputs from ``snapshot_holder`` (atomic swap) and
    queues telemetry onto ``telemetry_q``. ``worker_alive`` (a
    threading.Event) is set every loop iteration for the UI
    supervisor's liveness check (item 5).
    """
    from . import win32
    from .ingest import ActivityWatchClient
    from .taxonomy import categorize as categorize_fn

    global _UI_QUEUE
    _UI_QUEUE = ui_q

    def _on_foreground(hwnd):
        try:
            event_q.put_nowait(int(hwnd))
        except Exception as exc:
            # Win32 hook callback thread: never raise, and a dropped
            # event is covered by the worker's fallback poll. DEBUG.
            logger.debug("foreground event hand-off failed: %s", exc)

    hook = win32.install_foreground_hook(_on_foreground)
    client = ActivityWatchClient()
    state = {"last_hwnd": None}
    last_notified = {}
    last_fallback = 0.0
    swallow_handle = None
    swallow_until = 0.0
    resume_grace_until = 0.0  # Phase 8 R2: monotonic deadline

    def _engine_cycle():
        nonlocal swallow_handle, swallow_until, resume_grace_until
        # Sprint 4: snapshot mode -- zero SQLite. The refresher thread
        # already settled pomodoro cycles before building the snapshot.
        snapshot = (snapshot_holder.get()
                    if snapshot_holder is not None else None)
        # Resume-grace: derive from snapshot freshness when available.
        # (Legacy path keeps the old monotonic computation.)
        result = shield_once(
            state, client, categorize_fn, db_path=db_path,
            last_notified=last_notified, session_only=session_only,
            resume_grace_until=resume_grace_until,
            snapshot=snapshot, telemetry_q=telemetry_q)
        # An emergency pass dismisses any open overlay immediately.
        if result.get("reason") == "emergency pass":
            _queue_ui_command(UICommand(cmd="overlay_close"))
        # Hardcore lock: swallow Alt+Tab / Win key for LOCK_SECONDS.
        # Installed on THIS thread (it pumps messages). try/finally
        # discipline; failure just means no swallow (fail-safe).
        if result.get("action") == "hardcore" \
                and result.get("notified"):
            if swallow_handle is None:
                try:
                    swallow_handle = \
                        win32.install_keyboard_swallow()
                except Exception:
                    swallow_handle = None
                swallow_until = time.time() + LOCK_SECONDS
        return result

    try:
        while not stop_event.is_set():
            # Liveness: set every iteration (Qwen item 5).
            if worker_alive is not None:
                try:
                    worker_alive.set()
                except Exception as exc:
                    logger.debug("worker liveness signal failed: %s",
                                 exc)
            if os.path.exists(kill_switch_path()):
                break
            if session_only:
                # Old guard behavior: stop when the session ends.
                # Sprint 4: read from snapshot, not SQLite.
                try:
                    snap = (snapshot_holder.get()
                            if snapshot_holder is not None else None)
                    active = (snap.session if snap is not None
                              else None)
                    if active is None:
                        from . import focus as focus_mod
                        active = focus_mod.get_active_session(
                            db_path=db_path)
                    if not active:
                        break
                except Exception as exc:
                    # Session ended-check failed: keep enforcing (the
                    # loop retries next iteration). WARNING without a
                    # traceback -- a locked DB would repeat this.
                    logger.warning("could not check whether the "
                                   "session ended: %s", exc)
            # Keep Win32 hooks alive.
            try:
                win32.pump_messages_once()
            except Exception as exc:
                logger.debug("message pump step failed: %s", exc)
            # Expire the keyboard swallow.
            if swallow_handle is not None and \
                    time.time() >= swallow_until:
                try:
                    win32.uninstall_keyboard_swallow(swallow_handle)
                except Exception as exc:
                    # win32 logs the uninstall failure itself; this is
                    # the belt to its braces.
                    logger.debug("keyboard swallow expiry failed: %s",
                                 exc)
                swallow_handle = None
            # Event-driven: hook events first...
            try:
                hwnd = event_q.get(timeout=WORKER_IDLE_SECONDS)
                state["last_hwnd"] = hwnd
                _engine_cycle()
                continue
            except queue.Empty:
                # Normal idle path (no foreground event within
                # WORKER_IDLE_SECONDS); falls through to the poll.
                logger.debug("worker idle tick; polling instead")
            # ...2 s fallback poll in case the hook was dropped (R1).
            now_t = time.time()
            if now_t - last_fallback >= FALLBACK_POLL_SECONDS:
                last_fallback = now_t
                try:
                    fg = win32.get_foreground_info()
                except Exception:
                    fg = None
                if fg and fg.get("hwnd") != state.get("last_hwnd"):
                    state["last_hwnd"] = fg.get("hwnd")
                    _engine_cycle()
    finally:
        if swallow_handle is not None:
            try:
                win32.uninstall_keyboard_swallow(swallow_handle)
            except Exception as exc:
                logger.debug("final keyboard swallow removal failed: "
                             "%s", exc)
        try:
            win32.uninstall_foreground_hook(hook)
        except Exception as exc:
            logger.debug("final foreground hook removal failed: %s",
                         exc)
        # Tell the UI thread to quit its mainloop.
        _queue_ui_command(UICommand(cmd="quit"))


def _ui_main(stop_event, ui_q, db_path, worker_alive=None,
             restart_requested=None, snapshot_holder=None):
    """Main thread: tkinter owner for HUD + overlays (R5).

    No tkinter/display -> wait on stop_event; enforcement keeps
    running headless (notifications only).

    Sprint 4 (Qwen items 4-5): the drain() reschedule is in a
    ``finally`` block (unconditional); UI messages are frozen
    UICommands; the worker liveness event is checked every 30 s and a
    restart is requested after 90 s of silence. Returns "worker_silent"
    when the worker died, "stopped" otherwise.
    """
    try:
        import tkinter as tk
    except Exception:
        stop_event.wait()
        return "stopped"

    from . import hud as hud_mod
    from . import store

    root = tk.Tk()
    root.withdraw()  # we only ever show Toplevels
    hud = None
    try:
        hud_enabled = True
        if snapshot_holder is not None:
            hud_enabled = bool(snapshot_holder.get().hud_enabled)
        else:
            hud_enabled = store.get_setting(
                "hud_enabled", "1", path=db_path) == "1"
        if hud_enabled:
            hud = hud_mod.HudWindow(root, db_path=db_path)
    except Exception:
        hud = None
    overlays = []
    last_setting_check = 0.0
    last_liveness_check = 0.0
    worker_dead = False

    def _close_overlays():
        for win in overlays:
            try:
                win.destroy()
            except Exception as exc:
                # A half-dead tkinter window is routine at shutdown.
                logger.debug("closing a block overlay failed: %s", exc)
        overlays.clear()

    def _handle_command(cmd):
        """Handle one UICommand. Plain data only -- never touches
        worker-owned objects."""
        nonlocal hud
        if not isinstance(cmd, UICommand):
            return  # ignore legacy/unknown payloads
        if cmd.cmd == "overlay":
            _close_overlays()
            try:
                wins = hud_mod.show_block_overlay(
                    root, cmd.label, cmd.app, locked=cmd.locked,
                    session_id=cmd.session_id, db_path=cmd.db_path)
                overlays.extend(wins)
            except Exception as exc:
                print("overlay failed: %s" % exc, file=sys.stderr)
        elif cmd.cmd == "overlay_close":
            _close_overlays()
        elif cmd.cmd == "hud_update" and hud is not None:
            try:
                hud.update_snapshot(cmd.payload)
            except Exception as exc:
                logger.debug("hud update failed: %s", exc)
        elif cmd.cmd == "hud_hide" and hud is not None:
            try:
                hud.hide()
            except Exception as exc:
                logger.debug("hud hide failed: %s", exc)
        elif cmd.cmd == "hud_show":
            try:
                if hud is None:
                    hud = hud_mod.HudWindow(root, db_path=db_path)
                else:
                    hud.show()
            except Exception as exc:
                logger.debug("hud show failed: %s", exc)
        elif cmd.cmd == "quit":
            stop_event.set()

    def drain():
        nonlocal last_setting_check, last_liveness_check, worker_dead
        nonlocal hud
        try:
            if stop_event.is_set():
                _close_overlays()
                try:
                    if hud is not None:
                        hud.destroy()
                except Exception as exc:
                    logger.debug("hud destroy on stop failed: %s", exc)
                root.quit()
                return
            try:
                while True:
                    _handle_command(ui_q.get_nowait())
            except queue.Empty:
                # Expected: the command queue is empty again (this
                # fires on most 100 ms drain ticks). DEBUG only.
                logger.debug("ui command queue drained")
            # Worker liveness: check every 30 s (Qwen item 5).
            now_t = time.time()
            if worker_alive is not None and \
                    now_t - last_liveness_check >= \
                    WORKER_LIVENESS_CHECK_SECONDS:
                last_liveness_check = now_t
                try:
                    last_beat = worker_alive.last_beat
                except Exception:
                    last_beat = time.monotonic()
                if time.monotonic() - last_beat >= \
                        WORKER_LIVENESS_SILENCE_SECONDS:
                    print("shield: worker silent for 90s, "
                          "requesting restart", file=sys.stderr)
                    worker_dead = True
                    if restart_requested is not None:
                        restart_requested.set()
                    stop_event.set()
                    root.quit()
                    return
            # Periodic HUD refresh from a fresh snapshot, plus a slow
            # re-read of the hud_enabled setting (web/tray toggles apply
            # within ~5 s without restarting the daemon).
            try:
                if hud is not None and hud.visible:
                    hud.update_snapshot(
                        hud_mod.hud_snapshot(db_path=db_path))
                if now_t - last_setting_check > 5:
                    last_setting_check = now_t
                    if snapshot_holder is not None:
                        want = bool(
                            snapshot_holder.get().hud_enabled)
                    else:
                        want = store.get_setting(
                            "hud_enabled", "1",
                            path=db_path) == "1"
                    if want and (hud is None or not hud.visible):
                        if hud is None:
                            hud = hud_mod.HudWindow(root,
                                                    db_path=db_path)
                        else:
                            hud.show()
                    elif not want and hud is not None and hud.visible:
                        hud.hide()
            except Exception as exc:
                # HUD sync runs on the 100 ms drain tick: DEBUG, or a
                # broken HUD would flood the log ten times a second.
                logger.debug("hud sync step failed: %s", exc)
        finally:
            # Sprint 4 (Qwen item 4): the reschedule is UNCONDITIONAL.
            # No exception in drain() can ever stop the UI loop.
            try:
                if not stop_event.is_set():
                    root.after(UI_DRAIN_MS, drain)
            except Exception as exc:
                # Fires once -- after this, nothing reschedules drain.
                logger.warning("UI loop reschedule failed; shield UI "
                               "is stopping: %s", exc)

    root.after(UI_DRAIN_MS, drain)
    try:
        root.mainloop()
    except Exception:
        logger.exception("shield UI mainloop ended with an error")
    return "worker_silent" if worker_dead else "stopped"


def run_shield(db_path=None, session_only=False):
    """Run the shield daemon (blocks until stopped).

    Single instance via the named kernel mutex (R3). Main thread owns
    tkinter; the worker thread owns the Win32 hook + engine (R5).

    Sprint 4 (Qwen items 1-5, 13):
    - Worker reads RulesSnapshot (zero SQLite), refreshed every 45 s.
    - Telemetry writer thread batches block events into SQLite.
    - Worker liveness supervised; silent 90 s -> worker restart.
    - Graceful shutdown: drain queues, stop threads, join, checkpoint.
    """
    from . import win32
    if not win32.is_windows():
        print("Shield daemon needs Windows; not starting.")
        return
    mutex = win32.acquire_singleton_mutex()
    if mutex is None:
        print("Shield is already running; not starting a second copy.")
        return

    # Roadmap 1.3: this process owns the "shield" tag in the shared
    # log file. (Placed after the singleton check so a duplicate
    # starter doesn't re-tag or re-configure anything.)
    from . import logging_config
    logging_config.setup_logging(process_name="shield")

    # Roadmap 1.7: migrations before the snapshot prime, so the
    # telemetry writer (a direct store.get_db caller, now with FK
    # enforcement) never writes into an unmigrated schema. Startup
    # only -- the enforcement path stays SQLite-free (Invariant I-1).
    from . import store
    store.init_db(db_path)

    # Shared infrastructure (created once, reused across restarts).
    snapshot_holder = SnapshotHolder()
    try:
        # Prime the snapshot synchronously so the first enforcement
        # cycle has real rules, not the empty fail-open snapshot.
        snapshot_holder.swap(_build_snapshot(db_path))
    except Exception as exc:
        print("shield: initial snapshot failed: %s" % exc,
              file=sys.stderr)
    telemetry_q = queue.Queue(maxsize=TELEMETRY_QUEUE_MAXSIZE)
    ui_q = queue.Queue(maxsize=UI_QUEUE_MAXSIZE)
    event_q = queue.Queue()
    global _UI_QUEUE
    _UI_QUEUE = ui_q

    stop_event = threading.Event()
    restart_requested = threading.Event()
    worker_alive = _LivenessEvent()
    writer_alive = _LivenessEvent()

    # Background threads (snapshot refresher + telemetry writer).
    refresher = threading.Thread(
        target=_snapshot_refresher,
        args=(stop_event, snapshot_holder, db_path),
        name="shield-snapshot", daemon=True)
    refresher.start()
    writer = threading.Thread(
        target=_telemetry_writer,
        args=(stop_event, telemetry_q, db_path, writer_alive),
        name="shield-telemetry", daemon=True)
    writer.start()

    def _start_worker():
        worker_alive.clear()
        worker_alive.beat()  # fresh timestamp
        w = threading.Thread(
            target=_worker_main,
            args=(stop_event, event_q, ui_q, db_path, session_only,
                  snapshot_holder, telemetry_q, worker_alive),
            name="shield-worker", daemon=True)
        w.start()
        return w

    worker = _start_worker()
    try:
        # UI loop; restarts the worker if it went silent (item 5).
        while True:
            restart_requested.clear()
            reason = _ui_main(stop_event, ui_q, db_path,
                              worker_alive=worker_alive,
                              restart_requested=restart_requested,
                              snapshot_holder=snapshot_holder)
            # Worker finished; join it before deciding.
            worker.join(timeout=10)
            if stop_event.is_set() and not restart_requested.is_set():
                break  # clean stop (kill switch, quit, session end)
            if reason == "worker_silent" or restart_requested.is_set():
                print("shield: restarting silent worker",
                      file=sys.stderr)
                # Drain stale UI commands before the new worker starts.
                _drain_queue(ui_q)
                stop_event.clear()
                worker = _start_worker()
                continue
            break
    finally:
        # Sprint 4 (Qwen item 13): graceful shutdown order.
        # 1. Drain queues (process what's already queued).
        _drain_queue(ui_q)
        _drain_telemetry(telemetry_q, db_path)
        # 2. Signal stop.
        stop_event.set()
        # 3. Join worker threads (bounded wait).
        worker.join(timeout=10)
        writer.join(timeout=15)
        refresher.join(timeout=5)
        # 4. Checkpoint SQLite (truncate WAL).
        try:
            from . import store
            conn = store.get_db(db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("final WAL checkpoint failed: %s", exc)
        # 5. Release the singleton mutex.
        win32.release_singleton_mutex(mutex)


class _LivenessEvent(threading.Event):
    """threading.Event with a beat timestamp. Workers call beat() (or
    set(), which also beats); supervisors read ``last_beat``."""

    def __init__(self):
        super().__init__()
        self._beat = time.monotonic()

    def set(self):
        self._beat = time.monotonic()
        super().set()

    def beat(self):
        self._beat = time.monotonic()

    @property
    def last_beat(self):
        return self._beat


def _drain_queue(q):
    """Remove all pending items from a queue (shutdown drain)."""
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        logger.debug("queue fully drained")
    except Exception as exc:
        logger.warning("queue drain failed: %s", exc)


def _drain_telemetry(telemetry_q, db_path):
    """Flush remaining telemetry to SQLite on shutdown (best-effort)."""
    batch = []
    try:
        while True:
            batch.append(telemetry_q.get_nowait())
    except queue.Empty:
        logger.debug("telemetry queue fully drained")
    except Exception as exc:
        logger.warning("telemetry drain failed: %s", exc)
    if batch:
        try:
            _flush_telemetry_batch(batch, db_path)
        except Exception:
            for e in batch:
                _spill_event(e)


def ensure_shield_running(db_path=None):
    """Start the shield daemon in the background; never raises.

    Call once at app startup and after a fresh session starts. The
    daemon covers sessions AND always-on rules, so one daemon is
    enough. Returns True when the launch was attempted.
    """
    try:
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        args = [sys.executable, "-m", "focuscore.shield", "--run"]
        if db_path:
            args += ["--db", db_path]
        kwargs = {"cwd": root, "stdin": subprocess.DEVNULL,
                  "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(args, **kwargs)
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Focus Core shield daemon: hardcore distraction "
                    "blocking.")
    parser.add_argument("--run", action="store_true",
                        help="Run the shield daemon until stopped.")
    parser.add_argument("--session-only", action="store_true",
                        help="Only enforce the active focus session.")
    parser.add_argument("--db", default=None,
                        help="SQLite file to use.")
    parser.add_argument("--off", action="store_true",
                        help="Engage the kill switch (stop the daemon).")
    args = parser.parse_args()
    if args.off:
        print("Shield off." if shield_off() else "Could not stop it.")
        return
    if not args.run:
        parser.error("use --run to start the shield daemon")
    run_shield(db_path=args.db, session_only=args.session_only)


if __name__ == "__main__":
    main()
