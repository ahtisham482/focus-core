"""Focus session routes: start/end/abort, pomodoro cycles, cues.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Phase 11 (v1.13.0): SVG progress ring, Zen mode, depth gauge,
soundscapes, gamification, chronotype peak window.
Zero URL changes -- pure UI + feature additions.
"""
from html import escape

from flask import jsonify, redirect, request

from dashboard.app import app
from dashboard.app import (
    _fmt_countdown,
    layout,
)


# ------------------------------------------------------- SVG ring helper ---

def _svg_ring(remaining, total, mode="remaining", size=140, depth="surface"):
    """Inline SVG progress ring. Pure SVG+CSS, zero libraries.

    mode: 'remaining' (classic/pomodoro countdown) or 'elapsed'
    (flowtime counts up toward the soft target).
    """
    colors = {"flow": "#4caf50", "deep": "#ffb300", "surface": "#9e9e9e"}
    color = colors.get(depth, "#9e9e9e")
    return (
        "<div class='fc-ring' data-fc-ring data-total='%s' "
        "data-remaining='%s' data-mode='%s'>"
        "<svg width='%d' height='%d' viewBox='0 0 120 120'>"
        "<circle class='fc-ring-bg' cx='60' cy='60' r='54'/>"
        "<circle class='fc-ring-fg' cx='60' cy='60' r='54' "
        "style='stroke:%s'/>"
        "</svg>"
        "<div class='fc-ring-label'>%s</div>"
        "</div>"
        % (total, remaining, mode, size, size, color,
           _fmt_countdown(remaining) if mode == "remaining" else
           "%s elapsed" % _fmt_countdown(total - remaining))
    )


def _cycle_dots(done, target, on_break=False):
    dots = []
    for i in range(target):
        cls = "dot"
        if i < done:
            cls += " done"
        elif i == done and not on_break:
            cls += " now"
        dots.append("<span class='%s'></span>" % cls)
    return "<div class='fc-cycle-dots'>%s</div>" % "".join(dots)


def _depth_pill(session_id, depth):
    labels = {"flow": "🟢 Flow State", "deep": "🟡 Deep Work",
              "surface": "⚪ Surface Focus"}
    colors = {"flow": "#4caf50", "deep": "#ffb300", "surface": "#9e9e9e"}
    st = depth.get("state", "surface")
    widths = {"flow": "100%", "deep": "62%", "surface": "30%"}
    return (
        "<div><span id='fc-depth-pill' data-session-id='%s' "
        "style='border-color:%s'>%s</span>"
        "<div class='fc-depth-meter-track'>"
        "<div id='fc-depth-meter' style='width:%s;background:%s'></div>"
        "</div>"
        "<p class='note'>%d app switch(es) in 15 min &middot; "
        "%.0f min uninterrupted</p></div>"
        % (session_id, colors.get(st, "#9e9e9e"),
           labels.get(st, labels["surface"]),
           widths.get(st, "30%"), colors.get(st, "#9e9e9e"),
           depth.get("switches_15m", 0),
           depth.get("uninterrupted_min", 0.0))
    )


def _soundscape_controls():
    return (
        "<div class='card'><h3>Ambient sound</h3>"
        "<div class='fc-sounds'>"
        "<button type='button' data-fc-sound='rain'>Brownian Rain</button>"
        "<button type='button' data-fc-sound='pink'>Pink Noise</button>"
        "<button type='button' data-fc-sound='gamma'>40Hz Gamma Beat</button>"
        "<button type='button' data-fc-sound-stop>Stop</button>"
        "</div>"
        "<div class='fc-vol'><label>Volume "
        "<input type='range' min='0' max='1' step='0.01' data-fc-vol>"
        "</label><span id='fc-sound-status'>Sound off</span></div>"
        "<p class='note'>Synthesized live in your browser -- no downloads, "
        "works offline. Best with headphones for the gamma beat.</p>"
        "<script src='/static/soundscapes.js'></script>"
        "</div>"
    )


def _zen_button():
    return ("<button type='button' data-fc-zen>Enter Zen mode (z)</button> "
            "<span class='note'>Full-screen timer. Press z or Esc to "
            "exit.</span>")


def _focus_scripts():
    return "<script src='/static/focus.js'></script>"


# ------------------------------------------------------------- gamify UI ---

def _daily_ring_card(db_path=None):
    from focuscore import gamification as gami_mod
    ring = gami_mod.daily_ring(db_path=db_path)
    minutes, target = ring["minutes"], ring["target"]
    frac = ring["fraction"]
    r = 40
    circ = 2 * 3.14159 * r
    offset = circ * (1 - frac)
    earned = gami_mod.earned_badges(db_path=db_path)
    badges_html = "".join(
        "<span class='badge-chip'>🏅 %s</span>"
        % escape(gami_mod.BADGES[k]["name"]) for k in earned)
    return (
        "<div class='card'><h3>Today's focus ring</h3>"
        "<div class='fc-daily-ring'>"
        "<div class='fc-ring'><svg width='96' height='96' "
        "viewBox='0 0 96 96' style='transform:rotate(-90deg)'>"
        "<circle class='fc-ring-bg' cx='48' cy='48' r='40'/>"
        "<circle class='fc-ring-fg' cx='48' cy='48' r='40' "
        "stroke-dasharray='%.1f' stroke-dashoffset='%.1f'/>"
        "</svg><div class='fc-ring-label'>%.0f/%.0f</div></div>"
        "<div><p><b>%.0f</b> of <b>%d</b> focus minutes</p>"
        "<p class='note'>%d XP lifetime</p></div>"
        "</div>"
        "%s"
        "</div>"
        % (circ, offset, minutes, target, minutes, target,
           gami_mod.lifetime_xp(db_path=db_path),
           ("<div class='badge-list'>%s</div>" % badges_html)
           if badges_html else "")
    )


def _peak_launch_card(db_path=None):
    """1-click peak launch card, shown only inside the peak window."""
    from focuscore import chronotype
    if not chronotype.is_peak_now(db_path=db_path):
        return ""
    label = chronotype.window_label(db_path=db_path)
    return (
        "<div class='card peak-card'><h3>⚡ Peak energy window</h3>"
        "<p>You are in your peak window (%s) -- sessions now earn "
        "<b>+50%% XP</b> and blocking auto-escalates to Hardcore.</p>"
        "<form method='post' action='/focus/start'>"
        "<input type='hidden' name='label' value='Deep focus (peak)'>"
        "<input type='hidden' name='mode' value='classic'>"
        "<input type='hidden' name='preset' value='90'>"
        "<input type='hidden' name='block_level' value='strict'>"
        "<input type='hidden' name='enforcement_mode' value='hardcore'>"
        "<button type='submit'>1-Click Launch Deep Focus (90 min)</button>"
        "</form></div>"
        % escape(label)
    )


def _xp_summary_html(xp):
    if not xp or not xp.get("awarded"):
        return ""
    parts = ["<div class='card'><h3>Rewards</h3>"]
    parts.append(
        "<p class='xp-line'>Earned <b>%d XP</b> "
        "(base %d" % (xp["total_xp"], xp["base_xp"]))
    if xp.get("peak"):
        parts.append(" + peak bonus %d" % xp["peak_bonus"])
    if xp.get("clean"):
        parts.append(" + clean-run bonus %d" % xp["clean_bonus"])
    mult = xp.get("streak_mult_x10", 10) / 10.0
    parts.append(" &times; streak %.1fx)</p>" % mult)
    for badge_key in xp.get("new_badges") or []:
        from focuscore import gamification as gami_mod
        b = gami_mod.BADGES.get(badge_key, {})
        parts.append(
            "<div class='badge-toast'><div class='badge-name'>🏅 %s</div>"
            "<p>%s</p></div>"
            % (escape(b.get("name", badge_key)),
               escape(b.get("desc", ""))))
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------- routes ---

@app.route("/focus")
def focus_page():
    from focuscore import adaptive
    from focuscore import focus as focus_mod

    # Phase 8: settle pomodoro cycles on every view (idempotent).
    try:
        focus_mod.settle_session()
    except Exception:
        pass
    active = focus_mod.get_active_session()
    streak = focus_mod.current_streak()
    streak_html = (
        "<div class='streak'>%d-day focus streak</div>" % streak if streak
        else "<p class='note'>No focus streak yet -- "
             "complete a session to start one.</p>")

    cues_on = focus_mod.cue_enabled()
    cues_form = (
        "<form class='inline' method='post' action='/focus/cues'>"
        "<label><input type='checkbox' name='audio_cues' value='1'%s "
        "onchange='this.form.submit()'> Sound cues</label>"
        "<noscript><button type='submit'>Save</button></noscript></form>"
        % (" checked" if cues_on else ""))

    if active:
        mode = active.get("session_type") or "classic"
        if mode == "pomodoro":
            return _pomodoro_active_page_v11(active, streak_html, cues_form,
                                             focus_mod)
        if mode == "flowtime":
            return _flowtime_active_page_v11(active, streak_html, cues_form,
                                             focus_mod)
        remaining = focus_mod.remaining_seconds(active)
        try:
            depth = focus_mod.depth_state(active["id"])
        except Exception:
            depth = {"state": "surface", "switches_15m": 0,
                     "uninterrupted_min": 0.0}
        status_line = ("Time is up -- finish the session to see your summary."
                       if remaining <= 0 else "Stay focused.")
        body = (
            "<div class='card zen-visible'>"
            "<div class='focus-label'>%s</div>"
            "<div class='fc-ring-wrap'>"
            "%s"
            "<div>%s<p class='note'>%s</p></div>"
            "</div>"
            "<p class='note'>%.0f planned minutes &middot; %s blocking</p>"
            "<p>%s</p>"
            "<form class='inline' method='post' action='/focus/end'>"
            "<button type='submit'>End session</button></form> "
            "<form class='inline' method='post' action='/focus/abort' "
            "onsubmit=\"return confirm('Abort this session? "
            "It will not count toward your streak.');\">"
            "<button type='submit'>Abort</button></form> "
            "<span class='zen-only'><br><br></span>"
            "</div>"
            "<div class='card'><h3>Preferences</h3><p>%s</p><p>%s</p></div>"
            "%s"
            "%s"
            % (escape(active["label"]),
               _svg_ring(remaining, active["planned_minutes"] * 60,
                         depth=depth["state"]),
               _depth_pill(active["id"], depth), status_line,
               active["planned_minutes"], escape(active["block_level"]),
               _zen_button(), cues_form, streak_html, _soundscape_controls(),
               _focus_scripts())
        )
        return layout("Focus session", body, active="focus")

    past_rows = []
    for session in focus_mod.list_sessions(limit=10):
        summary = focus_mod.session_summary(session["id"])
        past_rows.append(
            "<tr><td>%s<br><span class='note'>%s</span></td>"
            "<td>%s</td><td>%.0f / %.1f min</td><td>%.1f min</td>"
            "<td>%d</td><td>%.1f</td></tr>"
            % (escape(session["label"]), escape(session["started_at"][:16]),
               escape(session["status"]),
               summary["planned_minutes"], summary["actual_minutes"],
               summary["focus_minutes"], summary["blocks_count"],
               summary["pulse"]))
    past_table = (
        "<table><tr><th>Session</th><th>Status</th><th>Planned / Actual</th>"
        "<th>Focus work</th><th>Blocks</th><th>Pulse</th></tr>%s</table>"
        % ("".join(past_rows)
           or "<tr><td colspan='6' class='note'>No sessions yet.</td></tr>"))

    try:
        work_min, work_reason = adaptive.suggest_work_minutes()
        flow_min, flow_reason = adaptive.suggest_flow_target()
        tired, tired_msg = adaptive.fatigue_check()
    except Exception:
        work_min, work_reason = 25, ""
        flow_min, flow_reason = 50, ""
        tired, tired_msg = False, ""
    suggestion_card = (
        "<div class='card'><h3>Suggestion for today</h3>"
        "<p>Pomodoro work block: <b>%d min</b> -- %s</p>"
        "<p>Flowtime soft target: <b>%d min</b> -- %s</p>"
        "%s</div>"
        % (work_min, escape(work_reason), flow_min, escape(flow_reason),
           ("<p><b>%s</b></p>" % escape(tired_msg)) if tired else ""))

    body = (
        "%s"
        "%s"
        "%s"
        "<div class='card'><h3>Start a focus session</h3>"
        "<form method='post' action='/focus/start'>"
        "<p><label>Label <input type='text' name='label' required "
        "placeholder='e.g. Deep work' size='28'></label></p>"
        "<p><label><input type='radio' name='mode' value='classic' "
        "checked> Classic -- fixed timer</label><br>"
        "<label><input type='radio' name='mode' value='flowtime'> "
        "Flowtime -- no fixed end, work until a natural break</label><br>"
        "<label><input type='radio' name='mode' value='pomodoro'> "
        "Smart Pomodoro -- work/break cycles that adapt to you</label></p>"
        "<p><label><input type='radio' name='preset' value='25'> 25 min</label> "
        "<label><input type='radio' name='preset' value='50' checked> 50 min</label> "
        "<label><input type='radio' name='preset' value='90'> 90 min</label> "
        "<label><input type='radio' name='preset' value='custom'> custom "
        "<input type='number' name='custom_minutes' min='1' max='480' "
        "style='width:70px' placeholder='min'></label>"
        "<span class='note'>For Pomodoro this is the work-block length; "
        "for Flowtime it is a soft target, not an alarm.</span></p>"
        "<p><label>Work blocks (Pomodoro): "
        "<input type='number' name='target_cycles' value='4' min='1' "
        "max='24' style='width:60px'></label></p>"
        "<p><label><input type='radio' name='block_level' value='strict' "
        "checked> Strict -- block Personal (-1) and Distracting (-2)</label><br>"
        "<label><input type='radio' name='block_level' value='lenient'> "
        "Lenient -- block only Distracting (-2)</label></p>"
        "<p><label><input type='radio' name='enforcement_mode' "
        "value='strict' checked> Standard -- pop-up reminder and a "
        "dismissible full-screen note</label><br>"
        "<label><input type='radio' name='enforcement_mode' "
        "value='hardcore'> Hardcore -- minimize the window and lock the "
        "note for 30 seconds (no Alt+Tab). Choose this only if you mean "
        "it.</label></p>"
        "<p><button type='submit'>Start session</button></p>"
        "</form>"
        "<p class='note'>Blocking starts automatically when the session "
        "starts -- no extra step needed.</p></div>"
        "%s"
        "<div class='card'><h3>Preferences</h3><p>%s</p>"
        "<p><label>Daily focus target (min) "
        "<input type='number' name='daily_target' value='%d' min='15' "
        "max='960' style='width:70px' form='target-form'></label></p>"
        "<form id='target-form' class='inline' method='post' "
        "action='/focus/target'><button type='submit'>Save</button></form>"
        "<p><label>Peak window "
        "<input type='time' name='peak_start' value='%s' form='peak-form'> "
        "to <input type='time' name='peak_end' value='%s' "
        "form='peak-form'></label></p>"
        "<form id='peak-form' class='inline' method='post' "
        "action='/focus/peak'><button type='submit'>Save</button></form>"
        "</div>"
        "<div class='card'><h3>Past sessions</h3>%s</div>"
        % (streak_html, _daily_ring_card(), _peak_launch_card(),
           suggestion_card, cues_form, _daily_target_value(),
           _peak_start_value(), _peak_end_value(), past_table)
    )
    return layout("Focus sessions", body, active="focus")


def _daily_target_value(db_path=None):
    from focuscore import gamification as gami_mod
    return gami_mod.daily_target_minutes(db_path=db_path)


def _peak_start_value(db_path=None):
    from focuscore import chronotype
    _, start, _ = chronotype.get_window(db_path=db_path)
    return "%02d:%02d" % (start.hour, start.minute)


def _peak_end_value(db_path=None):
    from focuscore import chronotype
    _, _, end = chronotype.get_window(db_path=db_path)
    return "%02d:%02d" % (end.hour, end.minute)


def _pomodoro_active_page_v11(active, streak_html, cues_form, focus_mod):
    """Pomodoro active page with ring + depth + sounds (Phase 11)."""
    from focuscore import store as store_mod
    cycle = store_mod.get_active_cycle(active["id"])
    done = active.get("completed_cycles") or 0
    target = active.get("target_cycles") or 4
    try:
        depth = focus_mod.depth_state(active["id"])
    except Exception:
        depth = {"state": "surface", "switches_15m": 0,
                 "uninterrupted_min": 0.0}
    if cycle:
        remaining = focus_mod.cycle_remaining_seconds(cycle)
        total = (cycle.get("planned_seconds") or remaining or 1)
        if cycle["kind"] == "work":
            headline = "Work block %d of %d" % (done + 1, target)
            controls = (
                "<form class='inline' method='post' "
                "action='/focus/cycle/break/start'>"
                "<button type='submit'>Start break</button></form> ")
            note = "Blocking is on."
        else:
            headline = "Break -- relax"
            controls = (
                "<form class='inline' method='post' "
                "action='/focus/cycle/break/end'>"
                "<button type='submit'>End break early</button></form> "
                "<form class='inline' method='post' "
                "action='/focus/cycle/break/skip'>"
                "<button type='submit'>Skip break</button></form> ")
            note = "Blocking is resting too."
        ring_html = (
            "<div class='fc-ring-wrap'>%s<div>"
            "<p><b>%s</b></p><p class='note'>%s</p>%s</div></div>"
            "%s"
            % (_svg_ring(remaining, total, depth=depth["state"]),
               headline, note, _depth_pill(active["id"], depth),
               _cycle_dots(done, target,
                           on_break=cycle["kind"] != "work")))
        controls_html = "<p>%s</p>" % (controls + _session_buttons_v11())
    else:
        ring_html = (
            "<p><b>Target reached: %d work blocks.</b> End the session "
            "whenever you are ready -- well done.</p>"
            "%s" % _cycle_dots(done, target))
        controls_html = "<p>%s</p>" % _session_buttons_v11()
    body = (
        "<div class='card zen-visible'><div class='focus-label'>%s</div>"
        "%s%s<p>%s</p></div>"
        "%s"
        "<div class='card'><h3>Preferences</h3><p>%s</p></div>"
        "%s%s"
        % (escape(active["label"]), ring_html, controls_html,
           _zen_button(), _soundscape_controls(), cues_form, streak_html,
           _focus_scripts())
    )
    return layout("Focus session", body, active="focus")


def _flowtime_active_page_v11(active, streak_html, cues_form, focus_mod):
    """Flowtime active page with ring + depth + sounds (Phase 11)."""
    from datetime import datetime
    start = focus_mod._to_naive(active["started_at"])
    elapsed = max(0.0, (datetime.now() - start).total_seconds())
    target = (active["planned_minutes"] or 50) * 60
    try:
        depth = focus_mod.depth_state(active["id"])
    except Exception:
        depth = {"state": "surface", "switches_15m": 0,
                 "uninterrupted_min": 0.0}
    body = (
        "<div class='card zen-visible'><div class='focus-label'>%s</div>"
        "<div class='fc-ring-wrap'>%s<div>%s"
        "<p class='note'>Soft target %.0f min (no alarm) &middot; %s "
        "blocking</p></div></div>"
        "<p>%s</p><p>%s</p></div>"
        "%s"
        "<div class='card'><h3>Preferences</h3><p>%s</p></div>"
        "%s%s"
        % (escape(active["label"]),
           _svg_ring(elapsed, target, mode="elapsed", depth=depth["state"]),
           _depth_pill(active["id"], depth), target / 60,
           escape(active["block_level"]), _session_buttons_v11(),
           _zen_button(), _soundscape_controls(), cues_form, streak_html,
           _focus_scripts())
    )
    return layout("Focus session", body, active="focus")


def _session_buttons_v11():
    return (
        "<form class='inline' method='post' action='/focus/end'>"
        "<button type='submit'>End session</button></form> "
        "<form class='inline' method='post' action='/focus/abort' "
        "onsubmit=\"return confirm('Abort this session? "
        "It will not count toward your streak.');\">"
        "<button type='submit'>Abort</button></form>")


@app.route("/focus/depth")
def focus_depth():
    """JSON depth gauge for live polling (Phase 11)."""
    from focuscore import focus as focus_mod
    session_id = request.args.get("session_id", type=int)
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    try:
        depth = focus_mod.depth_state(session_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(depth)


@app.route("/focus/target", methods=["POST"])
def focus_target():
    from focuscore import gamification as gami_mod
    try:
        minutes = int(request.form.get("daily_target") or 120)
    except (TypeError, ValueError):
        minutes = 120
    gami_mod.set_daily_target_minutes(minutes)
    return redirect("/focus")


@app.route("/focus/peak", methods=["POST"])
def focus_peak():
    from focuscore import chronotype
    start = request.form.get("peak_start") or "09:00"
    end = request.form.get("peak_end") or "11:30"
    try:
        chronotype.set_window(start, end)
    except ValueError:
        pass
    return redirect("/focus")


@app.route("/focus/start", methods=["POST"])
def focus_start():
    from focuscore import adaptive
    from focuscore import focus as focus_mod

    label = (request.form.get("label") or "").strip()
    preset = request.form.get("preset") or "50"
    block_level = request.form.get("block_level") or "strict"
    enforcement_mode = request.form.get("enforcement_mode") or "strict"
    mode = request.form.get("mode") or "classic"
    if mode not in ("classic", "flowtime", "pomodoro"):
        mode = "classic"
    try:
        target_cycles = int(request.form.get("target_cycles") or 4)
    except (TypeError, ValueError):
        target_cycles = 4
    if preset == "custom":
        minutes = request.form.get("custom_minutes")
    else:
        minutes = preset
    try:
        suggested = adaptive.suggest_work_minutes(
            mode=mode)[0] if mode == "pomodoro" else \
            adaptive.suggest_flow_target()[0] if mode == "flowtime" \
            else None
    except Exception:
        suggested = None
    result = focus_mod.start_session(label, minutes, block_level=block_level,
                                     enforcement_mode=enforcement_mode,
                                     session_type=mode,
                                     target_cycles=target_cycles,
                                     suggested_minutes=suggested)
    if "error" not in result:
        # Blocking starts with the session -- no second manual step.
        from focuscore.blocker import ensure_guard_running
        ensure_guard_running()
    if "error" in result:
        return layout("Focus sessions",
                      "<div class='card'><p><b>Could not start:</b> %s</p>"
                      "<p><a href='/focus'>Back</a></p></div>"
                      % escape(result["error"]), help_key="focus"), 400
    return redirect("/focus")


@app.route("/focus/cycle/break/start", methods=["POST"])
def focus_cycle_break_start():
    from focuscore import focus as focus_mod
    result = focus_mod.start_break_now()
    if "error" in result:
        return layout("Focus sessions",
                      "<div class='card'><p><b>Could not start break:</b> %s</p>"
                      "<p><a href='/focus'>Back</a></p></div>"
                      % escape(result["error"]), help_key="focus"), 400
    return redirect("/focus")


@app.route("/focus/cycle/break/end", methods=["POST"])
def focus_cycle_break_end():
    from focuscore import focus as focus_mod
    result = focus_mod.end_break_now()
    if "error" in result:
        return layout("Focus sessions",
                      "<div class='card'><p><b>Could not end break:</b> %s</p>"
                      "<p><a href='/focus'>Back</a></p></div>"
                      % escape(result["error"]), help_key="focus"), 400
    return redirect("/focus")


@app.route("/focus/cycle/break/skip", methods=["POST"])
def focus_cycle_break_skip():
    from focuscore import focus as focus_mod
    result = focus_mod.end_break_now(skipped=True)
    if "error" in result:
        return layout("Focus sessions",
                      "<div class='card'><p><b>Could not skip break:</b> %s</p>"
                      "<p><a href='/focus'>Back</a></p></div>"
                      % escape(result["error"]), help_key="focus"), 400
    return redirect("/focus")


@app.route("/focus/cues", methods=["POST"])
def focus_cues_toggle():
    from focuscore import store as store_mod
    enabled = "1" if request.form.get("audio_cues") else "0"
    try:
        store_mod.set_setting("audio_cues", enabled)
    except Exception:
        pass
    return redirect("/focus")


@app.route("/focus/end", methods=["POST"])
def focus_end():
    from focuscore import focus as focus_mod

    result = focus_mod.end_session()
    if "error" in result:
        return layout("Focus sessions",
                      "<div class='card'><p><b>Could not end:</b> %s</p>"
                      "<p><a href='/focus'>Back</a></p></div>"
                      % escape(result["error"])), 400
    s = result["summary"]
    body = (
        "<div class='card'><h3>Session complete: %s</h3>"
        "<div class='pulse'>%.1f</div>"
        "<p class='note'>Session Pulse (0-100)</p>"
        "<table>"
        "<tr><td>Focus work (+2/+1)</td><td><b>%.1f min</b></td></tr>"
        "<tr><td>Neutral</td><td>%.1f min</td></tr>"
        "<tr><td>Distracting (-1/-2)</td><td>%.1f min</td></tr>"
        "<tr><td>Distractions blocked</td><td><b>%d</b></td></tr>"
        "<tr><td>Planned vs actual</td><td>%.0f vs %.1f min</td></tr>"
        "</table>"
        "%s"
        "<p><a href='/focus'>Back to focus sessions</a></p></div>"
        % (escape(s["label"]), s["pulse"], s["focus_minutes"],
           s["neutral_minutes"], s["distracting_minutes"], s["blocks_count"],
           s["planned_minutes"], s["actual_minutes"],
           _xp_summary_html(result.get("xp")))
    )
    return layout("Session summary", body, help_key="focus")


@app.route("/focus/abort", methods=["POST"])
def focus_abort():
    from focuscore import focus as focus_mod

    result = focus_mod.abort_session()
    if "error" in result:
        return layout("Focus sessions",
                      "<div class='card'><p><b>Could not abort:</b> %s</p>"
                      "<p><a href='/focus'>Back</a></p></div>"
                      % escape(result["error"])), 400
    return redirect("/focus")
