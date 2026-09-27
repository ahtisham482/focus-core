"""Focus session routes: start/end/abort, pomodoro cycles, cues.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
from html import escape

from flask import redirect, request

from dashboard.app import app
from dashboard.app import (
    _flowtime_active_page,
    _fmt_countdown,
    _pomodoro_active_page,
    layout,
)

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
            return _pomodoro_active_page(active, streak_html, cues_form,
                                         focus_mod)
        if mode == "flowtime":
            return _flowtime_active_page(active, streak_html, cues_form,
                                        focus_mod)
        remaining = focus_mod.remaining_seconds(active)
        status_line = ("Time is up -- finish the session to see your summary."
                       if remaining <= 0 else "Stay focused. The page refreshes "
                       "every 10 seconds.")
        body = (
            "<div class='card'><div class='focus-label'>%s</div>"
            "<div class='countdown'>%s</div>"
            "<p class='note'>%s of %.0f planned minutes &middot; %s blocking</p>"
            "<form class='inline' method='post' action='/focus/end'>"
            "<button type='submit'>End session</button></form> "
            "<form class='inline' method='post' action='/focus/abort' "
            "onsubmit=\"return confirm('Abort this session? "
            "It will not count toward your streak.');\">"
            "<button type='submit'>Abort</button></form>"
            "<p class='note'>Blocking starts automatically with the session -- "
            "open a distracting app or site and you will get a pop-up plus a "
            "fullscreen reminder.</p></div>"
            "<div class='card'><h3>Preferences</h3><p>%s</p></div>"
            "%s"
            % (escape(active["label"]), _fmt_countdown(remaining),
               status_line, active["planned_minutes"],
               escape(active["block_level"]), cues_form, streak_html)
        )
        return layout("Focus session", body, refresh=10, active="focus")

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
        "<div class='card'><h3>Preferences</h3><p>%s</p></div>"
        "<div class='card'><h3>Past sessions</h3>%s</div>"
        % (streak_html, suggestion_card, cues_form, past_table)
    )
    return layout("Focus sessions", body, active="focus")


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
        "<p><a href='/focus'>Back to focus sessions</a></p></div>"
        % (escape(s["label"]), s["pulse"], s["focus_minutes"],
           s["neutral_minutes"], s["distracting_minutes"], s["blocks_count"],
           s["planned_minutes"], s["actual_minutes"])
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


