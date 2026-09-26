"""focuscore/hud.py - Visual HUD + block overlay windows (tkinter).

The HUD is a small always-on-top window showing shield state. The block
overlay is the fullscreen "back to work" reminder, now spanning all
monitors and topmost (Qwen R4).

Threading (Qwen R5): every function here runs on the tkinter owner
thread (the shield daemon's main thread). Nothing here is called from
the worker thread -- the worker only posts commands into a queue.

All tkinter work fails silent when tkinter or a display is unavailable.
"""

from datetime import datetime

SCORE_COLORS = {2: "#2e7d32", 1: "#66bb6a", 0: "#9e9e9e",
                -1: "#ffa726", -2: "#e53935"}


def hud_snapshot(db_path=None):
    """Pure data for the HUD (also reused by the /shield page).

    Returns {"state", "state_label", "app", "score", "score_color",
    "blocks_today", "session_label", "session_elapsed",
    "pass_active"}. Never raises -- unknown parts become None.
    """
    snap = {"state": "off", "state_label": "Shield off", "app": None,
            "score": None, "score_color": "#9e9e9e", "blocks_today": 0,
            "session_label": None, "session_elapsed": None,
            "pass_active": False}
    try:
        from . import shield, store

        try:
            daemon = shield.shield_daemon_running()
        except Exception:
            daemon = False
        try:
            session = store.get_active_session(path=db_path)
        except Exception:
            session = None
        try:
            blocks = store.count_blocks_today(path=db_path)
        except Exception:
            blocks = 0
        try:
            active_pass = shield.pass_active(db_path=db_path)
        except Exception:
            active_pass = None

        snap["blocks_today"] = blocks or 0
        snap["pass_active"] = bool(active_pass)
        if active_pass:
            snap["state"] = "pass"
            snap["state_label"] = "Emergency pass"
        elif session:
            snap["state"] = "session"
            snap["state_label"] = "Session: %s" % session.get("label")
            snap["session_label"] = session.get("label")
            try:
                started = datetime.fromisoformat(
                    session.get("started_at"))
                delta = datetime.now() - started
                mins = int(delta.total_seconds()) // 60
                snap["session_elapsed"] = "%d:%02d" % (
                    mins // 60, mins % 60)
            except Exception:
                pass
        elif daemon:
            snap["state"] = "protected"
            snap["state_label"] = "Shield on"
        # Current app, best-effort (never blocks the snapshot).
        try:
            from . import win32
            fg = win32.get_foreground_info()
            if fg:
                snap["app"] = fg.get("process_name") or None
        except Exception:
            pass
        return snap
    except Exception:
        return snap


class HudWindow:
    """Small always-on-top HUD. Created on the tkinter owner thread."""

    WIDTH, HEIGHT = 240, 96

    def __init__(self, root, db_path=None):
        import tkinter as tk
        self._tk = tk
        self.db_path = db_path
        self.visible = True
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.88)
        except Exception:
            pass
        self.win.configure(bg="#1a1a1a")
        # Top-right of the primary screen.
        try:
            sw = self.win.winfo_screenwidth()
            self.win.geometry("%dx%d+%d+%d" % (
                self.WIDTH, self.HEIGHT, sw - self.WIDTH - 12, 12))
        except Exception:
            pass
        self._build()
        self._make_draggable()

    def _build(self):
        tk = self._tk
        self.dot = tk.Label(self.win, text="\u25cf", font=("Segoe UI", 14),
                            bg="#1a1a1a", fg="#9e9e9e")
        self.dot.pack(side="left", padx=(10, 4), anchor="n", pady=8)
        right = tk.Frame(self.win, bg="#1a1a1a")
        right.pack(side="left", fill="both", expand=True, pady=6)
        self.state_lbl = tk.Label(right, text="Shield", bg="#1a1a1a",
                                  fg="#ffffff", font=("Segoe UI", 10,
                                                       "bold"),
                                  anchor="w")
        self.state_lbl.pack(fill="x")
        self.app_lbl = tk.Label(right, text="", bg="#1a1a1a",
                                fg="#bdbdbd", font=("Segoe UI", 9),
                                anchor="w")
        self.app_lbl.pack(fill="x")
        self.meta_lbl = tk.Label(right, text="", bg="#1a1a1a",
                                 fg="#757575", font=("Segoe UI", 9),
                                 anchor="w")
        self.meta_lbl.pack(fill="x")
        close = tk.Label(self.win, text="\u00d7", bg="#1a1a1a",
                         fg="#757575", font=("Segoe UI", 12),
                         cursor="hand2")
        close.place(relx=1.0, x=-6, y=0, anchor="ne")
        close.bind("<Button-1>", lambda _e: self.hide())

    def _make_draggable(self):
        def start(e):
            self.win._dx, self.win._dy = e.x, e.y

        def move(e):
            self.win.geometry("+%d+%d" % (
                self.win.winfo_x() + e.x - self.win._dx,
                self.win.winfo_y() + e.y - self.win._dy))
        self.win.bind("<Button-1>", start)
        self.win.bind("<B1-Motion>", move)

    def update_snapshot(self, snap):
        """Refresh labels from a hud_snapshot() dict."""
        try:
            colors = {"protected": "#66bb6a", "session": "#ffa726",
                      "pass": "#29b6f6", "off": "#757575"}
            state = snap.get("state") or "off"
            self.dot.configure(fg=colors.get(state, "#757575"))
            self.state_lbl.configure(
                text=snap.get("state_label") or "Shield")
            app = snap.get("app")
            if app:
                self.app_lbl.configure(
                    text="%s" % app,
                    fg=snap.get("score_color") or "#bdbdbd")
            else:
                self.app_lbl.configure(text="", fg="#bdbdbd")
            bits = []
            if snap.get("session_elapsed"):
                bits.append(snap["session_elapsed"])
            bits.append("blocked today: %d" % (
                snap.get("blocks_today") or 0))
            self.meta_lbl.configure(text="  \u00b7  ".join(bits))
        except Exception:
            pass

    def show(self):
        try:
            self.win.deiconify()
            self.visible = True
        except Exception:
            pass

    def hide(self):
        try:
            self.win.withdraw()
            self.visible = False
        except Exception:
            pass

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def show_block_overlay(root, label, app, locked=False, session_id=None,
                       db_path=None, lock_seconds=30):
    """Fullscreen-per-monitor "back to work" overlay (Qwen R4).

    One borderless topmost window per monitor. ``locked`` hides the
    dismiss button for ``lock_seconds`` (countdown shown); "End
    session" is always available. Returns the list of windows (the
    caller destroys them). Never raises.
    """
    wins = []
    try:
        import tkinter as tk
        from . import win32
        monitors = win32.iter_monitors() or [(0, 0, 800, 600)]
        for (mx, my, mw, mh) in monitors:
            win = tk.Toplevel(root)
            wins.append(win)
            win.overrideredirect(True)
            win.geometry("%dx%d+%d+%d" % (mw, mh, mx, my))
            win.configure(bg="#1a1a1a")
            try:
                win.attributes("-topmost", True)
            except Exception:
                pass
            tk.Label(win, text="Focus session in progress"
                     if session_id else "Shield block",
                     font=("Segoe UI", 28), bg="#1a1a1a",
                     fg="#ffffff").pack(pady=60)
            tk.Label(win, text=label or "", font=("Segoe UI", 20),
                     bg="#1a1a1a", fg="#9e9e9e").pack()
            tk.Label(win, text="Blocked: %s" % (app or "this app"),
                     font=("Segoe UI", 24), bg="#1a1a1a",
                     fg="#e53935").pack(pady=30)

            btn_frame = tk.Frame(win, bg="#1a1a1a")
            btn_frame.pack(pady=20)

            def _destroy_all(wins=wins):
                for w in wins:
                    try:
                        w.destroy()
                    except Exception:
                        pass
                wins.clear()

            if locked:
                lock_var = tk.StringVar()
                lock_lbl = tk.Label(win, textvariable=lock_var,
                                    font=("Segoe UI", 16), bg="#1a1a1a",
                                    fg="#ffa726")
                lock_lbl.pack(pady=10)
                dismiss_btn = tk.Button(
                    btn_frame, text="Back to work",
                    font=("Segoe UI", 16), padx=30, pady=10,
                    command=_destroy_all, state="disabled")
                dismiss_btn.pack(side="left", padx=10)

                def tick(remaining=[lock_seconds]):
                    if remaining[0] <= 0:
                        try:
                            dismiss_btn.configure(state="normal")
                            lock_var.set("You can go back now.")
                        except Exception:
                            pass
                        return
                    lock_var.set("Locked for %d more seconds -- "
                                 "breathe." % remaining[0])
                    remaining[0] -= 1
                    try:
                        win.after(1000, tick)
                    except Exception:
                        pass
                tick()
            else:
                tk.Button(btn_frame, text="Back to work",
                          font=("Segoe UI", 16), padx=30, pady=10,
                          command=_destroy_all).pack(side="left",
                                                     padx=10)

            def end_session(wins=wins):
                try:
                    from . import focus as focus_mod
                    focus_mod.end_session(db_path=db_path)
                except Exception:
                    pass
                _destroy_all()

            if session_id:
                tk.Button(btn_frame, text="End session",
                          font=("Segoe UI", 14), padx=20, pady=8,
                          command=end_session).pack(side="left",
                                                    padx=10)
    except Exception:
        for w in wins:
            try:
                w.destroy()
            except Exception:
                pass
        return []
    return wins
