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
    _living_nav,
    _living_tabs,
    layout,
)


# Living Instrument redesign: per-page assets, scoped by body.living.
# Only the Focus pages opt in via layout(extra_css=..., extra_js=...).
_LIVING_CSS = "<link rel='stylesheet' href='/static/living.css'>"
_LIVING_JS = "<script src='/static/living-focus.js'></script>"

# ------------------------------------------------------- SVG ring helper ---

import math as _math

# UI-10: Depth state → (hue color, luminance-boosted color for dual-encoding)
# Dual-encode: hue AND luminance so depth is never hue-only (deuteranopia guard).
# Grey = unmeasured (FLOW-1 tri-state), not failure.
_DEPTH_COLORS = {
    "flow":      ("#22c55e", "#4ade80"),   # green base + bright (luminance ↑)
    "deep":      ("#fbbf24", "#fde68a"),   # amber base + bright
    "surface":   ("#94a3b8", "#94a3b8"),   # slate (no luminance boost needed)
    "unmeasured":("#64748b", "#64748b"),   # darker slate = no data yet
}
# UI-10: depth → numeric score for JS pulse-inversion amplitude
_DEPTH_SCORES = {"flow": 1.0, "deep": 0.5, "surface": 0.0, "unmeasured": 0.0}

# UI-10: depth → human-readable ARIA label component
_DEPTH_ARIA = {
    "flow":      "Flow state",
    "deep":      "Deep work",
    "surface":   "Surface focus",
    "unmeasured":"Focus not yet measured",
}


def _svg_ring(remaining, total, mode="remaining", size=220, depth="surface"):
    """Inline SVG progress ring. Pure SVG+CSS, zero libraries.

    UI-10 compliant:
    - 220px default (up from 140px)
    - Tick marks at 25%, 50%, 75% of the ring
    - Dual-encoded depth: hue + luminance (not hue-only, deuteranopia safe)
    - data-depth-score for JS pulse-inversion amplitude
    - ARIA label on the SVG for screen readers
    - Grey = unmeasured (FLOW-1 tri-state), never = failure
    - mode: 'remaining' (countdown) or 'elapsed' (flowtime count-up)
    """
    cx, cy, r = 100, 100, 86          # viewBox 200×200
    circ = 2 * _math.pi * r

    # Progress fraction (0.0 → 1.0)
    if total > 0:
        frac = max(0.0, min(1.0, remaining / total))
    else:
        frac = 0.0
    elapsed_frac = 1.0 - frac

    stroke_offset = circ * (1.0 - (frac if mode == "remaining" else elapsed_frac))

    d_key = depth if depth in _DEPTH_COLORS else "surface"
    base_color, bright_color = _DEPTH_COLORS[d_key]
    depth_score = _DEPTH_SCORES.get(d_key, 0.0)
    aria_depth  = _DEPTH_ARIA.get(d_key, "Focus")

    # Time label
    if mode == "remaining":
        time_label = _fmt_countdown(remaining)
        aria_time  = "%s remaining" % time_label
        sub_label  = "remaining"
    else:
        elapsed = total - remaining
        time_label = _fmt_countdown(elapsed)
        aria_time  = "%s elapsed" % time_label
        sub_label  = "elapsed"

    full_aria = "%s \u00b7 %s" % (aria_depth, aria_time)

    # Tick marks at 25%, 50%, 75% (rotated back since SVG is rotated -90deg)
    # In the rotated SVG coordinate space (-90deg), 0% = top.
    tick_html = ""
    for pct in (0.25, 0.50, 0.75):
        angle = 2 * _math.pi * pct  # angle in rotated frame
        x1 = cx + (r - 8) * _math.cos(angle)
        y1 = cy + (r - 8) * _math.sin(angle)
        x2 = cx + (r + 4) * _math.cos(angle)
        y2 = cy + (r + 4) * _math.sin(angle)
        tick_html += (
            "<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' "
            "stroke='var(--border)' stroke-width='2' stroke-linecap='round'/>"
            % (x1, y1, x2, y2)
        )

    return (
        "<div class='fc-ring' data-fc-ring "
        "data-total='%(total)s' data-remaining='%(remaining)s' "
        "data-mode='%(mode)s' data-depth-score='%(score).1f' "
        "role='img' aria-label='%(aria)s'>"
        "<svg width='%(sz)d' height='%(sz)d' viewBox='0 0 200 200' "
        "style='transform:rotate(-90deg)' aria-hidden='true'>"
        # Background track
        "<circle class='fc-ring-bg' cx='%(cx)d' cy='%(cy)d' r='%(r)d'/>"
        # Progress arc — color uses both base (fill) and bright at deep/flow
        "<circle class='fc-ring-fg' cx='%(cx)d' cy='%(cy)d' r='%(r)d' "
        "style='stroke:%(color)s;"
        "stroke-dasharray:%(circ).1f;"
        "stroke-dashoffset:%(offset).1f'/>"
        # Tick marks
        "%(ticks)s"
        "</svg>"
        # Center label
        "<div class='fc-ring-label'>"
        "<span class='fc-ring-time' data-pulse "
        "style='color:%(bright)s'>%(time)s</span>"
        "<span class='fc-ring-sublabel'>%(sub)s</span>"
        "</div>"
        "</div>"
        % dict(
            total=total, remaining=remaining, mode=mode,
            score=depth_score, aria=full_aria,
            sz=size, cx=cx, cy=cy, r=r,
            color=base_color, bright=bright_color,
            circ=circ, offset=stroke_offset,
            ticks=tick_html,
            time=time_label, sub=sub_label,
        )
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


def _depth_pill(session_id, depth, on_break=False):
    """Depth state pill with token-based colors and CSS class targeting.
    UI-10: grey = surface (not failure), dual-encode via CSS class + border.
    Merlin: on break, pill shows '☕ On Break' without a distracting numerical score.
    """
    if on_break:
        return (
            "<div>"
            "<span id='fc-depth-pill' class='is-break' data-session-id='%s' "
            "style='border-color:var(--warn);color:var(--warn);"
            "background:var(--warn-soft)'>"
            "<span aria-hidden='true'>\u2615</span> On Break</span>"
            "<div class='fc-depth-meter-track'>"
            "<div id='fc-depth-meter' style='width:100%%;background:var(--warn)'></div>"
            "</div>"
            "<p class='note'>Resting your focus rhythm &middot; "
            "distraction blocking paused</p>"
            "</div>" % session_id
        )


    labels = {
        "flow":    "\U0001f7e2 Flow State",
        "deep":    "\U0001f7e1 Deep Work",
        "surface": "\u26aa Surface Focus",
    }
    # Use CSS token colors via inline style for border — matches the ring color
    base_colors, _ = zip(*[_DEPTH_COLORS.get(k, ("#94a3b8","#94a3b8"))
                            for k in ("flow","deep","surface")])
    color_map = dict(zip(("flow","deep","surface"), base_colors))
    st = depth.get("state", "surface")
    widths = {"flow": "100%", "deep": "62%", "surface": "30%"}
    color = color_map.get(st, "#94a3b8")
    return (
        "<div>"
        "<span id='fc-depth-pill' data-session-id='%s' "
        "style='border-color:%s;--depth-c:%s'>%s</span>"
        "<div class='fc-depth-meter-track'>"
        "<div id='fc-depth-meter' style='width:%s;background:%s'></div>"
        "</div>"
        "<p class='note'>%d app switch(es) in 15&thinsp;min &middot; "
        "%.0f&thinsp;min uninterrupted</p>"
        "</div>"
        % (session_id, color, color,
           labels.get(st, labels["surface"]),
           widths.get(st, "30%"), color,
           depth.get("switches_15m", 0),
           depth.get("uninterrupted_min", 0.0))
    )




_LV_ORB_CIRC = 942.48  # 2*pi*150: the living orb ring


def _lv_active_orb(*, phase, ring_mode, total, value, caption, depth,
                   session_id, static=False, time_up=False):
    """Living in-session orb (slice 7).

    phase: 'work' | 'break' | 'done'. ring_mode: 'remaining' | 'elapsed'.
    The JS tick in living-focus.js reads data-total / data-value /
    data-ring-mode; data-depth drives the halo glow (polled live).
    """
    total = max(1.0, float(total or 1))
    value = max(0.0, float(value or 0))
    frac = max(0.0, min(1.0, value / total))
    # remaining: full -> empty; elapsed: empty -> full. Same formula.
    offset = _LV_ORB_CIRC * (1.0 - frac)
    t0 = "Done" if static else _fmt_countdown(value)
    aria = "%s, %s" % (caption, t0)
    cls = "lv-orb lv-orb-live st"
    if time_up:
        cls += " lv-done"
    data_static = " data-lv-static" if static else ""
    return (
        "<div class='%s' style='--d:80ms' id='lv-orb' data-lv-orb "
        "data-ring-mode='%s' data-total='%.0f' data-value='%.0f' "
        "data-depth='%s' data-phase='%s' data-session-id='%s'%s "
        "role='img' aria-label='%s'>"
        "<div class='lv-halo' aria-hidden='true'></div>"
        "<svg class='lv-orb-svg' viewBox='0 0 340 340' aria-hidden='true'>"
        "<circle class='lv-orb-track' cx='170' cy='170' r='150'/>"
        "<circle class='lv-orb-prog' id='lv-orb-prog' cx='170' cy='170' "
        "r='150' style='stroke-dashoffset:%.2f'/>"
        "</svg>"
        "<div class='lv-orb-core'>"
        "<p class='lv-orb-time' id='lv-orb-time' aria-live='off'>%s</p>"
        "<p class='lv-orb-state' id='lv-orb-state'>%s</p>"
        "</div></div>"
        "<p class='lv-visually-hidden' role='status' id='lv-orb-status'></p>"
        % (cls, ring_mode, total, value, depth, phase, session_id,
           data_static, escape(aria), offset, t0, escape(caption)))


def _lv_form_btn(action, label, cls="", confirm_abort=False):
    """One living-styled POST button."""
    extra = ""
    if confirm_abort:
        extra = (" onsubmit=\"return confirm('Abort this session? "
                 "It will not count toward your streak.');\"")
    return (
        "<form class='lv-act-form' method='post' action='%s'%s>"
        "<button type='submit' class='lv-act-btn%s'>%s</button></form>"
        % (action, extra, (" " + cls) if cls else "", label))


def _lv_end_abort(primary_label="End session"):
    return (_lv_form_btn("/focus/end", primary_label, "primary")
            + _lv_form_btn("/focus/abort", "Abort", "ghost",
                           confirm_abort=True))


def _lv_active_shell(title, orb_html, shield_text, buttons_html, mid_html,
                     subnote, legacy_html):
    """Shared skeleton for the three in-session pages."""
    return (
        _living_nav("focus", "armed", "Shield armed")
        + "<div class='lv-wrap'>"
        + "<section class='lv-instrument lv-active' "
          "aria-label='Focus session in progress'>"
        + "<h1 class='lv-sess-title st' style='--d:40ms'>%s</h1>"
          % escape(title)
        + orb_html
        + "<p class='lv-orb-shield lv-armed st' style='--d:160ms'>"
          "<span class='dot' aria-hidden='true'></span>%s</p>"
          % escape(shield_text)
        + "<div class='lv-act-row st' style='--d:220ms'>%s</div>"
          % buttons_html
        + mid_html
        + "<p class='lv-subnote st' style='--d:300ms'>%s</p>" % subnote
        + "</section>"
        + "<div class='lv-legacy'>%s</div>" % legacy_html
        + "</div>"
        + _living_tabs("focus"))


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
        caption = ("Time is up" if remaining <= 0 else "In session")
        orb_html = _lv_active_orb(
            phase="work", ring_mode="remaining",
            total=active["planned_minutes"] * 60,
            value=max(0, remaining), caption=caption,
            depth=depth["state"], session_id=active["id"],
            time_up=remaining <= 0)
        mid_html = (
            "<div class='lv-depth st' style='--d:260ms'>%s</div>"
            % _depth_pill(active["id"], depth))
        subnote = ("%s &middot; %.0f planned minutes &middot; %s blocking"
                   % (status_line, active["planned_minutes"],
                      escape(active["block_level"])))
        legacy_html = (
            "<div class='card'><h3>Session</h3><p>%s</p><p>%s</p>%s</div>"
            % (_zen_button(), cues_form, streak_html)
            + _soundscape_controls())
        body = _lv_active_shell(
            active["label"], orb_html, "Shield armed \u2014 blocking is on",
            _lv_end_abort(), mid_html, subnote,
            legacy_html + _focus_scripts())
        return layout("Focus session", body, active="focus",
                      body_class="living",
                      extra_css=_LIVING_CSS, extra_js=_LIVING_JS)

    # ── Living instrument: recent sessions as quiet rows ──
    _sess_data = []
    _max_focus = 0.0
    for session in focus_mod.list_sessions(limit=8):
        summary = focus_mod.session_summary(session["id"])
        _sess_data.append((session, summary))
        _max_focus = max(_max_focus, summary["focus_minutes"])
    sess_rows = []
    for session, summary in _sess_data:
        _frac = (summary["focus_minutes"] / _max_focus) if _max_focus else 0
        sess_rows.append(
            "<li><div class='lv-sess-main'>"
            "<span class='lv-sess-label'>%s</span>"
            "<span class='lv-sess-meta'>%s &middot; %.0f min focus</span>"
            "</div>"
            "<div class='lv-sess-bar' aria-hidden='true'>"
            "<span style='width:%.0f%%'></span></div></li>"
            % (escape(session["label"] or "Untitled"),
               escape(session["started_at"][:10]),
               summary["focus_minutes"], _frac * 100))
    recent_html = (
        "<section class='lv-wrap' aria-label='Recent sessions'>"
        "<div class='lv-recent st' style='--d:560ms'>"
        "<h2 class='lv-sec-title'>Recent sessions</h2>"
        "%s</div></section>"
        % ("<ul class='lv-sessions'>%s</ul>" % "".join(sess_rows)
           if sess_rows else
           "<p class='lv-quiet'>No sessions yet &mdash; your first one "
           "starts the story.</p>"))

    try:
        work_min, work_reason = adaptive.suggest_work_minutes()
        tired, tired_msg = adaptive.fatigue_check()
    except Exception:
        work_min, work_reason = 25, ""
        tired, tired_msg = False, ""

    # ── Living instrument (slice 6): the adaptive suggestion IS the default
    # action — stepper starts at the suggested minutes, one click begins.
    # Field names must stay in sync with /focus/start.
    _sug_min = max(15, min(120, int(round(work_min / 5.0) * 5)))
    _orb_time = "%d:00" % _sug_min

    tired_line = (("<p class='lv-tired'>%s</p>" % escape(tired_msg))
                  if tired else "")
    suggest_html = (
        "<p class='lv-suggest st lv-dissolve' style='--d:480ms'>"
        "<span aria-hidden='true'>&#10022;</span> Suggested "
        "<b>%d min</b>%s</p>%s"
        % (_sug_min,
           (" &mdash; %s" % escape(work_reason)) if work_reason else "",
           tired_line))

    prefs_card = (
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
        % (cues_form, _daily_target_value(),
           _peak_start_value(), _peak_end_value()))

    instrument_html = (
        _living_nav("focus", "ready", "Shield ready")
        + "<div class='lv-wrap'><section class='lv-instrument' "
          "aria-label='Start a focus session'>"
        "<div class='lv-orb st' style='--d:80ms' id='lv-orb'>"
        "<div class='lv-halo' aria-hidden='true'></div>"
        "<svg class='lv-orb-svg' viewBox='0 0 340 340' aria-hidden='true'>"
        "<circle class='lv-orb-track' cx='170' cy='170' r='150'/>"
        "<circle class='lv-orb-prog' cx='170' cy='170' r='150'/>"
        "</svg>"
        "<div class='lv-orb-core'>"
        "<p class='lv-orb-time' id='lv-orb-time'>%s</p>"
        "<p class='lv-orb-state' id='lv-orb-state'>Ready</p>"
        "</div></div>"
        "<p class='lv-orb-shield st' style='--d:160ms'>"
        "<span class='dot' aria-hidden='true'></span>"
        "Shield arms automatically when you begin</p>"
        "<form method='post' action='/focus/start' id='lv-begin-form' "
        "class='lv-controls'>"
        "<div class='lv-label-row st lv-dissolve' style='--d:220ms'>"
        "<label for='lv-label'>What are you working on?</label>"
        "<input type='text' id='lv-label' name='label' value='Deep work' "
        "maxlength='80' autocomplete='off'>"
        "</div>"
        "<div class='lv-chips st lv-dissolve' style='--d:280ms' "
        "role='radiogroup' aria-label='Session mode'>"
        "<span class='lv-glide' aria-hidden='true'></span>"
        "<button type='button' class='lv-chip on' data-mode='classic' "
        "role='radio' aria-checked='true'>Classic</button>"
        "<button type='button' class='lv-chip' data-mode='pomodoro' "
        "role='radio' aria-checked='false'>Pomodoro</button>"
        "<button type='button' class='lv-chip' data-mode='flowtime' "
        "role='radio' aria-checked='false'>Flowtime</button>"
        "</div>"
        "<input type='hidden' name='mode' id='lv-mode' value='classic'>"
        "<input type='hidden' name='preset' value='custom'>"
        "<input type='hidden' name='custom_minutes' id='lv-minutes' "
        "value='%d'>"
        "<div class='lv-stepper st lv-dissolve' style='--d:340ms'>"
        "<button type='button' id='lv-minus' "
        "aria-label='Shorter session'>&minus;</button>"
        "<span class='lv-step-val'><b id='lv-step-num'>%d</b> min</span>"
        "<button type='button' id='lv-plus' "
        "aria-label='Longer session'>+</button>"
        "</div>"
        "<p class='lv-step-note st lv-dissolve' style='--d:380ms' "
        "id='lv-step-note'>Fixed timer.</p>"
        "<div class='lv-cycles st lv-dissolve' id='lv-cycles' hidden>"
        "<span id='lv-cycles-label'>Work blocks</span>"
        "<div class='lv-stepper small'>"
        "<button type='button' id='lv-cminus' "
        "aria-label='Fewer work blocks'>&minus;</button>"
        "<span class='lv-step-val'><b id='lv-cnum'>4</b></span>"
        "<button type='button' id='lv-cplus' "
        "aria-label='More work blocks'>+</button>"
        "</div>"
        "<input type='hidden' name='target_cycles' id='lv-cycles-val' "
        "value='4'>"
        "</div>"
        "<details class='lv-shield-more st lv-dissolve' style='--d:400ms'>"
        "<summary>Shield settings</summary>"
        "<div class='lv-shield-row'>"
        "<span>Blocking</span>"
        "<label><input type='radio' name='block_level' value='strict' "
        "checked> Strict</label>"
        "<label><input type='radio' name='block_level' value='lenient'> "
        "Lenient</label>"
        "</div>"
        "<p class='lv-note'>Strict blocks Personal and Distracting apps; "
        "Lenient blocks only Distracting.</p>"
        "<div class='lv-shield-row'>"
        "<span>Enforcement</span>"
        "<label><input type='radio' name='enforcement_mode' value='strict' "
        "checked> Standard</label>"
        "<label><input type='radio' name='enforcement_mode' "
        "value='hardcore'> Hardcore</label>"
        "</div>"
        "<p class='lv-note'>Hardcore minimizes the window and locks the "
        "note for 30 seconds.</p>"
        "</details>"
        "<button type='submit' class='lv-begin lv-magnet st' "
        "style='--d:440ms' id='lv-begin'>Begin session</button>"
        "</form>"
        "%s"
        "</section></div>"
        % (_orb_time, _sug_min, _sug_min, suggest_html))

    body = (
        instrument_html
        + recent_html
        + "<div class='lv-wrap'><div class='lv-legacy'>"
        + streak_html
        + "<div class='rhythm-row'>" + _daily_ring_card()
        + _peak_launch_card() + "</div>"
        + prefs_card
        + "</div></div>"
        + _living_tabs("focus")
    )
    return layout("Focus sessions", body, active="focus",
                  body_class="living",
                  extra_css=_LIVING_CSS, extra_js=_LIVING_JS)


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
    legacy_html = (
        "<div class='card'><h3>Session</h3><p>%s</p><p>%s</p>%s</div>"
        % (_zen_button(), cues_form, streak_html)
        + _soundscape_controls() + _focus_scripts())
    if cycle:
        remaining = focus_mod.cycle_remaining_seconds(cycle)
        total = (cycle.get("planned_seconds") or remaining or 1)
        on_brk = (cycle["kind"] != "work")
        if not on_brk:
            caption = "Work block %d of %d" % (done + 1, target)
            buttons = (_lv_form_btn("/focus/cycle/break/start",
                                    "Start break", "primary")
                       + _lv_end_abort())
            shield_text = "Shield armed \u2014 blocking is on"
            note = "Blocking is on."
            depth_state = depth["state"]
        else:
            caption = "Break \u2014 relax"
            buttons = (_lv_form_btn("/focus/cycle/break/end",
                                    "End break early", "primary")
                       + _lv_form_btn("/focus/cycle/break/skip",
                                      "Skip break")
                       + _lv_end_abort())
            shield_text = "Shield resting \u2014 blocking paused"
            note = "Blocking is resting too."
            depth_state = "surface"
        orb_html = _lv_active_orb(
            phase=("break" if on_brk else "work"), ring_mode="remaining",
            total=total, value=max(0, remaining), caption=caption,
            depth=depth_state, session_id=active["id"],
            time_up=(remaining <= 0 and not on_brk))
        mid_html = (
            "<div class='lv-dots st' style='--d:240ms'>%s</div>"
            % _cycle_dots(done, target, on_break=on_brk)
            + "<div class='lv-depth st' style='--d:260ms'>%s</div>"
            % _depth_pill(active["id"], depth, on_break=on_brk))
        subnote = "%s &middot; work block %d of %d" % (note, done + 1, target)
        body = _lv_active_shell(active["label"], orb_html, shield_text,
                                buttons, mid_html, subnote, legacy_html)
    else:
        orb_html = _lv_active_orb(
            phase="done", ring_mode="remaining", total=1, value=1,
            caption="Target reached", depth="surface",
            session_id=active["id"], static=True)
        mid_html = (
            "<div class='lv-dots st' style='--d:240ms'>%s</div>"
            % _cycle_dots(done, target))
        subnote = ("Target reached: %d work blocks. End the session "
                   "whenever you are ready \u2014 well done." % target)
        body = _lv_active_shell(active["label"], orb_html,
                                "Shield armed \u2014 blocking is on",
                                _lv_end_abort(), mid_html, subnote,
                                legacy_html)
    return layout("Focus session", body, active="focus",
                  body_class="living",
                  extra_css=_LIVING_CSS, extra_js=_LIVING_JS)




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
    orb_html = _lv_active_orb(
        phase="work", ring_mode="elapsed", total=target, value=elapsed,
        caption="Flowing", depth=depth["state"], session_id=active["id"])
    mid_html = (
        "<div class='lv-depth st' style='--d:260ms'>%s</div>"
        % _depth_pill(active["id"], depth))
    subnote = ("Soft target %.0f min (no alarm) &middot; %s blocking"
               % (target / 60, escape(active["block_level"])))
    legacy_html = (
        "<div class='card'><h3>Session</h3><p>%s</p><p>%s</p>%s</div>"
        % (_zen_button(), cues_form, streak_html)
        + _soundscape_controls() + _focus_scripts())
    body = _lv_active_shell(
        active["label"], orb_html, "Shield armed \u2014 blocking is on",
        _lv_end_abort(), mid_html, subnote, legacy_html)
    return layout("Focus session", body, active="focus",
                  body_class="living",
                  extra_css=_LIVING_CSS, extra_js=_LIVING_JS)


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
