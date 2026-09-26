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

Usage:
    python -m focuscore.shield --run            # shield daemon
    python -m focuscore.shield --run --session-only   # session guard
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from . import paths
from .win32 import ELEVATED_UNKNOWN, FALLBACK_POLL_SECONDS

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
                resume_grace_until=None):
    """Run one enforcement cycle.

    Pure decision flow with injected side effects. ``session_only``
    skips always-on rules (the old per-session guard behavior).
    Returns a dict with "action" in ACTIONS plus context. Never raises:
    any unexpected failure returns {"action": "none", "reason": ...} --
    the shield fails open, never closed.
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
        try:
            active_pass = pass_active(now=now, db_path=db_path)
        except Exception:
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
            pass

        # 4. Session + rules.
        try:
            session = focus_mod.get_active_session(db_path=db_path)
        except Exception:
            session = None
        try:
            overrides = store.get_overrides(path=db_path)
        except Exception:
            overrides = {}
        try:
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
                cycle = store.get_active_cycle(session.get("id"),
                                               path=db_path)
                on_break = bool(cycle and cycle.get("kind") == "break")
            except Exception:
                on_break = False
        if session and session.get("status") == "active" and not on_break:
            session_id = session.get("id")
            session_enforcement = session.get("enforcement_mode") \
                or "strict"
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
        try:
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
    """Default overlay_fn: hand the command to the UI thread."""
    _UI_QUEUE.put(("overlay", {"label": label, "app": app,
                               "locked": locked, "session_id": session_id,
                               "db_path": db_path}))


# Module-level UI queue, set by run_shield() before the worker starts.
_UI_QUEUE = queue.Queue()


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


def _worker_main(stop_event, event_q, ui_q, db_path, session_only):
    """Worker thread: hook events + message pump + policy engine."""
    from . import win32
    from .ingest import ActivityWatchClient
    from .taxonomy import categorize as categorize_fn

    global _UI_QUEUE
    _UI_QUEUE = ui_q

    def _on_foreground(hwnd):
        try:
            event_q.put_nowait(int(hwnd))
        except Exception:
            pass

    hook = win32.install_foreground_hook(_on_foreground)
    client = ActivityWatchClient()
    state = {"last_hwnd": None}
    last_notified = {}
    last_fallback = 0.0
    swallow_handle = None
    swallow_until = 0.0
    resume_grace_until = 0.0  # Phase 8 R2: monotonic deadline

    def _engine_cycle():
        from . import focus as focus_mod
        nonlocal swallow_handle, swallow_until, resume_grace_until
        # Phase 8: settle pomodoro cycles first (hybrid timer, R1).
        # Idempotent and cheap; never raises out of here.
        try:
            settled = focus_mod.settle_session(db_path=db_path)
            if settled.get("suspend_detected"):
                resume_grace_until = (time.monotonic()
                                      + focus_mod.RESUME_GRACE_SECONDS)
        except Exception:
            pass
        result = shield_once(
            state, client, categorize_fn, db_path=db_path,
            last_notified=last_notified, session_only=session_only,
            resume_grace_until=resume_grace_until)
        # An emergency pass dismisses any open overlay immediately.
        if result.get("reason") == "emergency pass":
            try:
                ui_q.put(("overlay_close", {}))
            except Exception:
                pass
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
            if os.path.exists(kill_switch_path()):
                break
            if session_only:
                # Old guard behavior: stop when the session ends.
                try:
                    from . import focus as focus_mod
                    if not focus_mod.get_active_session(
                            db_path=db_path):
                        break
                except Exception:
                    pass
            # Keep Win32 hooks alive.
            try:
                win32.pump_messages_once()
            except Exception:
                pass
            # Expire the keyboard swallow.
            if swallow_handle is not None and \
                    time.time() >= swallow_until:
                try:
                    win32.uninstall_keyboard_swallow(swallow_handle)
                except Exception:
                    pass
                swallow_handle = None
            # Event-driven: hook events first...
            try:
                hwnd = event_q.get(timeout=WORKER_IDLE_SECONDS)
                state["last_hwnd"] = hwnd
                _engine_cycle()
                continue
            except queue.Empty:
                pass
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
            except Exception:
                pass
        try:
            win32.uninstall_foreground_hook(hook)
        except Exception:
            pass
        # Tell the UI thread to quit its mainloop.
        try:
            ui_q.put(("quit", {}))
        except Exception:
            pass


def _ui_main(stop_event, ui_q, db_path):
    """Main thread: tkinter owner for HUD + overlays (R5).

    No tkinter/display -> wait on stop_event; enforcement keeps
    running headless (notifications only).
    """
    try:
        import tkinter as tk
    except Exception:
        stop_event.wait()
        return

    from . import hud as hud_mod
    from . import store

    root = tk.Tk()
    root.withdraw()  # we only ever show Toplevels
    hud = None
    try:
        if store.get_setting("hud_enabled", "1", path=db_path) == "1":
            hud = hud_mod.HudWindow(root, db_path=db_path)
    except Exception:
        hud = None
    overlays = []
    last_setting_check = 0.0

    def _close_overlays():
        for win in overlays:
            try:
                win.destroy()
            except Exception:
                pass
        overlays.clear()

    def drain():
        nonlocal last_setting_check
        if stop_event.is_set():
            _close_overlays()
            try:
                if hud is not None:
                    hud.destroy()
            except Exception:
                pass
            root.quit()
            return
        try:
            while True:
                cmd, payload = ui_q.get_nowait()
                if cmd == "overlay":
                    _close_overlays()
                    try:
                        wins = hud_mod.show_block_overlay(
                            root, payload.get("label"),
                            payload.get("app"),
                            locked=payload.get("locked", False),
                            session_id=payload.get("session_id"),
                            db_path=payload.get("db_path"))
                        overlays.extend(wins)
                    except Exception as exc:
                        print("overlay failed: %s" % exc,
                              file=sys.stderr)
                elif cmd == "overlay_close":
                    _close_overlays()
                elif cmd == "hud_update" and hud is not None:
                    try:
                        hud.update_snapshot(payload)
                    except Exception:
                        pass
                elif cmd == "hud_hide" and hud is not None:
                    try:
                        hud.hide()
                    except Exception:
                        pass
                elif cmd == "hud_show":
                    try:
                        if hud is None:
                            hud = hud_mod.HudWindow(root,
                                                    db_path=db_path)
                        else:
                            hud.show()
                    except Exception:
                        pass
                elif cmd == "quit":
                    stop_event.set()
        except queue.Empty:
            pass
        # Periodic HUD refresh from a fresh snapshot, plus a slow
        # re-read of the hud_enabled setting (web/tray toggles apply
        # within ~5 s without restarting the daemon).
        try:
            if hud is not None and hud.visible:
                hud.update_snapshot(
                    hud_mod.hud_snapshot(db_path=db_path))
            now_t = time.time()
            if now_t - last_setting_check > 5:
                last_setting_check = now_t
                want = store.get_setting("hud_enabled", "1",
                                         path=db_path) == "1"
                if want and (hud is None or not hud.visible):
                    if hud is None:
                        hud = hud_mod.HudWindow(root, db_path=db_path)
                    else:
                        hud.show()
                elif not want and hud is not None and hud.visible:
                    hud.hide()
        except Exception:
            pass
        root.after(UI_DRAIN_MS, drain)

    root.after(UI_DRAIN_MS, drain)
    try:
        root.mainloop()
    except Exception:
        pass


def run_shield(db_path=None, session_only=False):
    """Run the shield daemon (blocks until stopped).

    Single instance via the named kernel mutex (R3). Main thread owns
    tkinter; the worker thread owns the Win32 hook + engine (R5).
    """
    from . import win32
    if not win32.is_windows():
        print("Shield daemon needs Windows; not starting.")
        return
    mutex = win32.acquire_singleton_mutex()
    if mutex is None:
        print("Shield is already running; not starting a second copy.")
        return
    stop_event = threading.Event()
    event_q = queue.Queue()
    ui_q = queue.Queue()
    worker = threading.Thread(
        target=_worker_main,
        args=(stop_event, event_q, ui_q, db_path, session_only),
        name="shield-worker", daemon=True)
    worker.start()
    try:
        _ui_main(stop_event, ui_q, db_path)
    finally:
        stop_event.set()
        worker.join(timeout=10)
        win32.release_singleton_mutex(mutex)


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
