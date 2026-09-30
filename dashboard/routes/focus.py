"""Focus session routes: start/end/abort, pomodoro cycles, cues.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Phase 11 (v1.13.0): SVG progress ring, Zen mode, depth gauge,
soundscapes, gamification, chronotype peak window.
Zero URL changes -- pure UI + feature additions.
Craft pass (v1.15.x): action-first layout, sentence forms, live strip,
starter chips. Same POST contracts, restructured HTML only.
"""
from html import escape
import logging

from flask import jsonify, redirect, request

from dashboard.app import app
from dashboard.app import (
    _fmt_countdown,
    _living_tabs,
    layout,
)

# Roadmap 0.3: log failures that used to be swallowed silently.
logger = logging.getLogger(__name__)

# Craft pass: per-page stylesheet (merged by the orchestrator).


def _fc_icon(name, size=14):
    """SVG sprite icon. No emoji icons."""
    return ("<svg width='%d' height='%d' aria-hidden='true'>"
            "<use href='/static/icons.svg#icon-%s'/></svg>"
            % (size, size, name))


# Depth state -> (plain-English label, meter width, color).
# Grey = unmeasured, never failure.
_FC_DEPTH = {
    "flow":    ("Flow state",    "100%", "#22c55e"),
    "deep":    ("Deep work",     "62%",  "#f59e0b"),
    "surface": ("Surface focus", "30%",  "#94a3b8"),
    "unmeasured": ("Measuring focus", "8%", "#64748b"),
}


def _fc_btn(action, label, cls="", confirm=False):
    """One POST button for the focus pages."""
    extra = (" onsubmit=\"return confirm('Abort this session? "
             "It will not count toward your streak.');\"" if confirm else "")
    return (
        "<form class='inline' method='post' action='%s'%s>"
        "<button type='submit' class='fc-btn%s'>%s</button></form>"
        % (action, extra, (" " + cls) if cls else "", label))


def _fc_depth_strip(session_id, depth, on_break=False):
    """Depth gauge as a quiet strip under the live timer. No emoji."""
    if on_break:
        return (
            "<div class='fc-depth' id='fc-depth-strip' "
            "data-session-id='%s'>"
            "<span class='fc-depth-label'>On break</span>"
            "<div class='fc-depth-track'><span id='fc-depth-meter' "
            "style='width:100%%;background:var(--warn)'></span></div>"
            "<p class='note'>Resting your focus rhythm &middot; "
            "distraction blocking paused</p></div>" % session_id)
    st = depth.get("state", "surface")
    label, width, color = _FC_DEPTH.get(st, _FC_DEPTH["unmeasured"])
    switches = depth.get("switches_15m", 0)
    switch_word = "app switch" if switches == 1 else "app switches"
    return (
        "<div class='fc-depth' id='fc-depth-strip' data-session-id='%s'>"
        "<span class='fc-depth-label'>%s</span>"
        "<div class='fc-depth-track'><span id='fc-depth-meter' "
        "style='width:%s;background:%s'></span></div>"
        "<p class='note'>%d %s in 15&thinsp;min &middot; "
        "%.0f&thinsp;min uninterrupted</p></div>"
        % (session_id, label, width, color,
           switches, switch_word,
           depth.get("uninterrupted_min", 0.0)))


def _fc_ring_card(db_path=None):
    """Today's focus ring + badges, restyled. Badges use the SVG sprite."""
    from focuscore import gamification as gami_mod
    ring = gami_mod.daily_ring(db_path=db_path)
    minutes, target = ring["minutes"], ring["target"]
    frac = ring["fraction"]
    r = 40
    circ = 2 * 3.14159 * r
    offset = circ * (1 - frac)
    earned = gami_mod.earned_badges(db_path=db_path)
    badges_html = "".join(
        "<span class='fc-badge'>%s %s</span>"
        % (_fc_icon("target", 13), escape(gami_mod.BADGES[k]["name"]))
        for k in earned)
    return (
        "<div class='card fc-ring-card'><h2>Today's focus ring</h2>"
        "<div class='fc-ring-row'>"
        "<svg width='96' height='96' viewBox='0 0 96 96' "
        "style='transform:rotate(-90deg)' role='img' "
        "aria-label='%.0f of %d focus minutes'>"
        "<circle cx='48' cy='48' r='40' class='fc-track'/>"
        "<circle cx='48' cy='48' r='40' class='fc-arc' "
        "stroke-dasharray='%.1f' stroke-dashoffset='%.1f'/>"
        "</svg>"
        "<div><p><b>%.0f</b> of <b>%d</b> focus minutes</p>"
        "<p class='note'>%d XP lifetime</p></div>"
        "</div>%s</div>"
        % (minutes, target, circ, offset, minutes, target,
           gami_mod.lifetime_xp(db_path=db_path),
           ("<div class='fc-badges'>%s</div>" % badges_html)
           if badges_html else ""))


def _fc_peak_card(db_path=None):
    """1-click peak launch card, shown only inside the peak window."""
    from focuscore import chronotype
    if not chronotype.is_peak_now(db_path=db_path):
        return ""
    label = chronotype.window_label(db_path=db_path)
    return (
        "<div class='card fc-peak'><h2>%s Peak energy window</h2>"
        "<p>You are in your peak window (%s) &mdash; sessions now earn "
        "<b>+50%% XP</b> and blocking auto-escalates to Hardcore.</p>"
        "<form method='post' action='/focus/start'>"
        "<input type='hidden' name='label' value='Deep focus (peak)'>"
        "<input type='hidden' name='mode' value='classic'>"
        "<input type='hidden' name='preset' value='90'>"
        "<input type='hidden' name='block_level' value='strict'>"
        "<input type='hidden' name='enforcement_mode' value='hardcore'>"
        "<button type='submit'>Start 90-minute deep focus</button>"
        "</form></div>"
        % (_fc_icon("timer", 15), escape(label)))


def _fc_soundscapes():
    return (
        "<div class='card fc-sounds-card'><h2>Ambient sound</h2>"
        "<div class='fc-sounds'>"
        "<button type='button' data-fc-sound='rain'>Brownian Rain</button>"
        "<button type='button' data-fc-sound='pink'>Pink Noise</button>"
        "<button type='button' data-fc-sound='gamma'>40Hz Gamma Beat</button>"
        "<button type='button' data-fc-sound-stop>Stop</button>"
        "</div>"
        "<div class='fc-vol'><label>Volume "
        "<input type='range' min='0' max='1' step='0.01' data-fc-vol>"
        "</label><span id='fc-sound-status'>Sound off</span></div>"
        "<p class='note'>Synthesized live in your browser &mdash; no "
        "downloads, works offline. Best with headphones for the gamma "
        "beat.</p>"
        "<script src='/static/soundscapes.js'></script>"
        "</div>")


# Idle-page interactivity: minutes stepper, mode chips, cycle stepper.
_FC_IDLE_JS = """<script>
(function () {
  "use strict";
  var mins = document.getElementById("fc-minutes");
  var num = document.getElementById("fc-step-num");
  var cur = parseInt(mins ? mins.value : "25", 10) || 25;
  function setM(v) {
    cur = Math.max(15, Math.min(120, v));
    if (mins) mins.value = String(cur);
    if (num) num.textContent = String(cur);
  }
  function bind(id, fn) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  }
  bind("fc-minus", function () { setM(cur - 5); });
  bind("fc-plus", function () { setM(cur + 5); });
  var cyc = document.getElementById("fc-cycles-val");
  var cnum = document.getElementById("fc-cnum");
  var ccur = 4;
  function setC(v) {
    ccur = Math.max(1, Math.min(8, v));
    if (cyc) cyc.value = String(ccur);
    if (cnum) cnum.textContent = String(ccur);
  }
  bind("fc-cminus", function () { setC(ccur - 1); });
  bind("fc-cplus", function () { setC(ccur + 1); });
  var note = document.getElementById("fc-mode-note");
  var cycles = document.getElementById("fc-cycles");
  document.querySelectorAll("input[name='mode']").forEach(function (r) {
    r.addEventListener("change", function () {
      if (note) note.innerHTML = r.getAttribute("data-fc-mode-note") || "";
      if (cycles) cycles.hidden = (r.value !== "pomodoro");
    });
  });
})();
</script>"""

# Active-page interactivity: countdown ticker, depth polling (plain
# labels, no emoji), zen toggle. Server re-renders on the 5-minute
# refresh, so the ticker only needs to be locally consistent.
_FC_LIVE_JS = """<script>
(function () {
  "use strict";
  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60),
        s = sec % 60;
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return h > 0 ? h + ":" + p(m) + ":" + p(s) : m + ":" + p(s);
  }
  var t = document.querySelector(".fc-live-time");
  if (t) {
    var total = parseFloat(t.getAttribute("data-total") || "1");
    var mode = t.getAttribute("data-mode") || "remaining";
    var endAt = Date.now() +
        parseFloat(t.getAttribute("data-remaining") || "0") * 1000;
    var bar = document.getElementById("fc-live-bar");
    var times = document.querySelectorAll(".fc-live-time");
    var tick = setInterval(function () {
      var left = Math.max(0, (endAt - Date.now()) / 1000);
      var txt, frac;
      if (mode === "remaining") {
        txt = fmt(left);
        frac = total > 0 ? 1 - left / total : 0;
        if (left <= 0) clearInterval(tick);
      } else {
        var el = total - left;
        txt = fmt(Math.max(0, el));
        frac = total > 0 ? Math.max(0, el) / total : 0;
      }
      times.forEach(function (e) { e.textContent = txt; });
      if (bar) bar.style.width =
          (100 * Math.max(0, Math.min(1, frac))) + "%";
    }, 1000);
  }
  var pill = document.getElementById("fc-depth-strip");
  if (pill) {
    var sid = pill.getAttribute("data-session-id");
    var LABELS = { flow: "Flow state", deep: "Deep work",
                   surface: "Surface focus" };
    var WIDTHS = { flow: "100%", deep: "62%", surface: "30%" };
    var COLORS = { flow: "#22c55e", deep: "#f59e0b", surface: "#94a3b8" };
    function ref() {
      fetch("/focus/depth?session_id=" + encodeURIComponent(sid))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var st = d.state || "surface";
          var lbl = pill.querySelector(".fc-depth-label");
          if (lbl) lbl.textContent = LABELS[st] || st;
          var m = document.getElementById("fc-depth-meter");
          if (m) { m.style.width = WIDTHS[st]; m.style.background = COLORS[st]; }
          var b = document.getElementById("fc-live-bar");
          if (b) b.style.background = COLORS[st];
        })
        .catch(function () { /* keep last known state */ });
    }
    if (sid) setInterval(ref, 30000);
  }
  function zen() { document.body.classList.toggle("zen-mode"); }
  document.querySelectorAll("[data-fc-zen]").forEach(function (b) {
    b.addEventListener("click", zen);
  });
  document.addEventListener("keydown", function (e) {
    var tag = document.activeElement ? document.activeElement.tagName : "";
    if ((e.key === "z" || e.key === "Z") && !/INPUT|TEXTAREA/.test(tag)) zen();
    if (e.key === "Escape") document.body.classList.remove("zen-mode");
  });
})();
</script>"""

def _fc_idle_page(focus_mod):
    """No active session: the start action is the hero."""
    from focuscore import adaptive

    streak = focus_mod.current_streak()
    streak_html = (
        "<p class='fc-streak'>%d-day focus streak</p>" % streak if streak
        else "<p class='note'>No focus streak yet &mdash; complete a "
             "session to start one.</p>")

    try:
        work_min, work_reason = adaptive.suggest_work_minutes()
        tired, tired_msg = adaptive.fatigue_check()
    except Exception:
        work_min, work_reason = 25, ""
        tired, tired_msg = False, ""
    sug_min = max(15, min(120, int(round(work_min / 5.0) * 5)))

    suggest_html = (
        "<p class='fc-suggest'>Suggested <b>%d min</b>%s</p>"
        % (sug_min,
           (" &mdash; %s" % escape(work_reason)) if work_reason else ""))
    tired_html = (("<p class='fc-tired'>%s</p>" % escape(tired_msg))
                  if tired else "")

    # One-tap starter sessions: post the same fields as the form below,
    # so /focus/start needs no changes.
    starters = [
        ("Deep work 50", "Deep work", "50", "classic"),
        ("Quick sprint 25", "Quick sprint", "25", "classic"),
        ("Flow session", "Flow", "50", "flowtime"),
    ]
    starters_html = "".join(
        "<form class='inline' method='post' action='/focus/start'>"
        "<input type='hidden' name='label' value='%s'>"
        "<input type='hidden' name='preset' value='%s'>"
        "<input type='hidden' name='mode' value='%s'>"
        "<input type='hidden' name='block_level' value='strict'>"
        "<input type='hidden' name='enforcement_mode' value='strict'>"
        "<button type='submit' class='chip'>%s</button></form>"
        % (escape(label), preset, mode, escape(chip))
        for chip, label, preset, mode in starters)

    # Field names must stay in sync with /focus/start.
    hero = (
        "<div class='card fc-hero'><h2>Start a focus session</h2>"
        "%s%s"
        "<p class='fc-starters-label'>Or start in one tap:</p>"
        "<div class='fc-starters'>%s</div>"
        "<form method='post' action='/focus/start' id='fc-begin' "
        "class='sentence-form'>"
        "<p class='sentence'>I want to focus for "
        "<span class='fc-stepper'>"
        "<button type='button' id='fc-minus' "
        "aria-label='Shorter session'>&minus;</button>"
        "<span class='fc-step-val'><b id='fc-step-num'>%d</b> min</span>"
        "<button type='button' id='fc-plus' "
        "aria-label='Longer session'>+</button>"
        "</span> on "
        "<input type='text' name='label' value='Deep work' maxlength='80' "
        "autocomplete='off' aria-label='What are you working on?' "
        "size='18'>."
        "</p>"
        "<div class='fc-modes' role='radiogroup' aria-label='Session mode'>"
        "<label class='fc-mode'><input type='radio' name='mode' "
        "value='classic' checked "
        "data-fc-mode-note='Fixed timer &mdash; it ends when the minutes "
        "run out.'><span>Classic</span></label>"
        "<label class='fc-mode'><input type='radio' name='mode' "
        "value='pomodoro' "
        "data-fc-mode-note='Work blocks with short breaks between "
        "them.'><span>Pomodoro</span></label>"
        "<label class='fc-mode'><input type='radio' name='mode' "
        "value='flowtime' "
        "data-fc-mode-note='No alarm &mdash; stop when the work feels "
        "done.'><span>Flowtime</span></label>"
        "</div>"
        "<div class='fc-cycles' id='fc-cycles' hidden>"
        "<span>Work blocks</span>"
        "<span class='fc-stepper small'>"
        "<button type='button' id='fc-cminus' "
        "aria-label='Fewer work blocks'>&minus;</button>"
        "<span class='fc-step-val'><b id='fc-cnum'>4</b></span>"
        "<button type='button' id='fc-cplus' "
        "aria-label='More work blocks'>+</button>"
        "</span>"
        "<input type='hidden' name='target_cycles' id='fc-cycles-val' "
        "value='4'>"
        "</div>"
        "<p class='note' id='fc-mode-note'>Fixed timer &mdash; it ends when "
        "the minutes run out.</p>"
        "<input type='hidden' name='preset' value='custom'>"
        "<input type='hidden' name='custom_minutes' id='fc-minutes' "
        "value='%d'>"
        "<details class='fc-shield'><summary>Shield settings</summary>"
        "<div class='fc-shield-row'><span>Blocking</span>"
        "<label><input type='radio' name='block_level' value='strict' "
        "checked> Strict</label>"
        "<label><input type='radio' name='block_level' value='lenient'> "
        "Lenient</label></div>"
        "<p class='note'>Strict blocks personal and distracting apps; "
        "lenient blocks only distracting ones.</p>"
        "<div class='fc-shield-row'><span>Enforcement</span>"
        "<label><input type='radio' name='enforcement_mode' value='strict' "
        "checked> Standard</label>"
        "<label><input type='radio' name='enforcement_mode' "
        "value='hardcore'> Hardcore</label></div>"
        "<p class='note'>Hardcore minimizes the window and locks the note "
        "for 30 seconds.</p>"
        "</details>"
        "<p><button type='submit' class='fc-begin'>Begin session</button></p>"
        "</form>"
        "<p class='note'>Shield arms automatically when you begin.</p>"
        "</div>"
        % (suggest_html, tired_html, starters_html, sug_min, sug_min))

    # Recent sessions as quiet rows; current state first, history second.
    sess_data = []
    max_focus = 0.0
    for session in focus_mod.list_sessions(limit=8):
        summary = focus_mod.session_summary(session["id"])
        sess_data.append((session, summary))
        max_focus = max(max_focus, summary["focus_minutes"])
    sess_rows = []
    for session, summary in sess_data:
        frac = (summary["focus_minutes"] / max_focus) if max_focus else 0
        sess_rows.append(
            "<li><span class='fc-sess-main'>"
            "<span class='fc-sess-label'>%s</span>"
            "<span class='fc-sess-meta'>%s &middot; %.0f min focus</span>"
            "</span>"
            "<span class='fc-sess-bar' aria-hidden='true'>"
            "<span style='width:%.0f%%'></span></span></li>"
            % (escape(session["label"] or "Untitled"),
               escape(session["started_at"][:10]),
               summary["focus_minutes"], frac * 100))
    recent_html = (
        "<section class='fc-recent'><h2>Recent sessions</h2>"
        "%s</section>"
        % ("<ul class='fc-sessions'>%s</ul>" % "".join(sess_rows)
           if sess_rows else
           "<p class='note'>No sessions yet &mdash; your first one starts "
           "the story."))

    # Preferences: sentence forms over database forms.
    cues_on = focus_mod.cue_enabled()
    prefs = (
        "<div class='card fc-prefs'><h2>Preferences</h2>"
        "<form method='post' action='/focus/target' class='sentence-form'>"
        "<p class='sentence'>I want to focus for "
        "<input type='number' name='daily_target' value='%d' min='15' "
        "max='960' aria-label='Daily focus target in minutes'> "
        "minutes each day. <button type='submit'>Save</button></p>"
        "</form>"
        "<form method='post' action='/focus/peak' class='sentence-form'>"
        "<p class='sentence'>My peak window is "
        "<input type='time' name='peak_start' value='%s' "
        "aria-label='Peak window start'> to "
        "<input type='time' name='peak_end' value='%s' "
        "aria-label='Peak window end'>. "
        "<button type='submit'>Save</button></p>"
        "</form>"
        "<p class='fc-cues-label'>Sound cues are %s:</p>"
        "<div class='fc-cues'>"
        "<form class='inline' method='post' action='/focus/cues'>"
        "<input type='hidden' name='audio_cues' value='1'>"
        "<button type='submit' class='chip%s'>Cues on</button></form>"
        "<form class='inline' method='post' action='/focus/cues'>"
        "<button type='submit' class='chip%s'>Cues off</button></form>"
        "</div></div>"
        % (_daily_target_value(), _peak_start_value(), _peak_end_value(),
           "on" if cues_on else "off",
           " on" if cues_on else "", "" if cues_on else " on"))

    footnote = (
        "<p class='how-it-works'>Focus sessions block distracting apps "
        "while you work and count toward your daily ring and streak. "
        "Everything is saved on this device.</p>")

    return (hero + recent_html + streak_html + _fc_ring_card()
            + _fc_peak_card() + prefs + footnote)


def _fc_active_page(active, focus_mod):
    """A session is running: thin live strip, not a card."""
    from focuscore import store as store_mod

    mode = active.get("session_type") or "classic"
    try:
        depth = focus_mod.depth_state(active["id"])
    except Exception:
        depth = {"state": "surface", "switches_15m": 0,
                 "uninterrupted_min": 0.0}

    mid = ""
    dots_html = ""
    if mode == "pomodoro":
        cycle = store_mod.get_active_cycle(active["id"])
        done = active.get("completed_cycles") or 0
        target = active.get("target_cycles") or 4
        dots_html = (
            "<div class='fc-dots' role='img' "
            "aria-label='Work block %d of %d'>%s</div>"
            % (done + 1, target, "".join(
                "<span class='%s'></span>"
                % ("on" if i < done else "now" if i == done else "")
                for i in range(target))))
        if cycle:
            remaining = focus_mod.cycle_remaining_seconds(cycle)
            total = cycle.get("planned_seconds") or remaining or 1
            on_brk = (cycle["kind"] != "work")
            if not on_brk:
                caption = "Work block %d of %d" % (done + 1, target)
                state = "In session"
                buttons = (_fc_btn("/focus/cycle/break/start",
                                   "Start break", "primary")
                           + _fc_btn("/focus/end", "End session", "")
                           + _fc_btn("/focus/abort", "Abort", "ghost",
                                     confirm=True))
                shield = "Shield armed &mdash; blocking is on"
                sub = "Work block %d of %d" % (done + 1, target)
                phase = "work"
            else:
                caption = "Break &mdash; relax"
                state = "On break"
                buttons = (_fc_btn("/focus/cycle/break/end",
                                   "End break early", "primary")
                           + _fc_btn("/focus/cycle/break/skip",
                                     "Skip break")
                           + _fc_btn("/focus/end", "End session", "")
                           + _fc_btn("/focus/abort", "Abort", "ghost",
                                     confirm=True))
                shield = "Shield resting &mdash; blocking paused"
                sub = "Break &mdash; back to work when you're ready"
                phase = "break"
            mid = dots_html + _fc_depth_strip(active["id"], depth,
                                              on_break=on_brk)
        else:
            caption = "Target reached"
            state = "Done"
            phase = "done"
            remaining, total = 1, 1
            buttons = (_fc_btn("/focus/end", "End session", "primary")
                       + _fc_btn("/focus/abort", "Abort", "ghost",
                                 confirm=True))
            shield = "Shield armed &mdash; blocking is on"
            sub = ("Target reached: %d work blocks. End the session "
                   "whenever you're ready &mdash; well done." % target)
            mid = dots_html + _fc_depth_strip(active["id"], depth)
        ring_mode = "remaining"
    elif mode == "flowtime":
        from datetime import datetime
        start = focus_mod._to_naive(active["started_at"])
        elapsed = max(0.0, (datetime.now() - start).total_seconds())
        total = (active["planned_minutes"] or 50) * 60
        remaining = total - elapsed
        ring_mode = "elapsed"
        caption = "Flowing"
        state = "In session"
        phase = "work"
        buttons = (_fc_btn("/focus/end", "End session", "primary")
                   + _fc_btn("/focus/abort", "Abort", "ghost",
                             confirm=True))
        shield = "Shield armed &mdash; blocking is on"
        sub = ("Soft target %.0f min (no alarm) &middot; %s blocking"
               % (total / 60, escape(active["block_level"])))
        mid = _fc_depth_strip(active["id"], depth)
    else:
        remaining = focus_mod.remaining_seconds(active)
        total = active["planned_minutes"] * 60
        ring_mode = "remaining"
        time_up = remaining <= 0
        caption = "Time is up" if time_up else "In session"
        state = caption
        phase = "timeup" if time_up else "work"
        buttons = (_fc_btn("/focus/end", "End session", "primary")
                   + _fc_btn("/focus/abort", "Abort", "ghost",
                             confirm=True))
        shield = "Shield armed &mdash; blocking is on"
        sub = ("Time is up &mdash; finish the session to see your summary."
               if time_up else "Stay focused.")
        sub += (" &middot; %.0f planned minutes &middot; %s blocking"
                % (active["planned_minutes"],
                   escape(active["block_level"])))
        mid = _fc_depth_strip(active["id"], depth)

    shown = (remaining if ring_mode == "remaining"
             else max(0.0, total - remaining))
    t0 = _fmt_countdown(shown)
    total = max(1.0, total)
    frac = max(0.0, min(1.0, (total - remaining) / total))

    strip = (
        "<div class='fc-live card zen-visible' data-phase='%s'>"
        "<div class='fc-live-top'>"
        "<div><h2 class='fc-live-label'>%s</h2>"
        "<p class='fc-live-state'>%s &middot; %s</p></div>"
        "<p class='fc-live-time' id='fc-live-time' aria-live='off' "
        "data-remaining='%.0f' data-total='%.0f' "
        "data-mode='%s'>%s</p>"
        "</div>"
        "<div class='fc-live-bar' role='progressbar' "
        "aria-label='%s'><span id='fc-live-bar' "
        "style='width:%.1f%%'></span></div>"
        "%s"
        "<div class='fc-live-actions'>%s</div>"
        "<p class='note'>%s</p>"
        "<p class='zen-only fc-live-time fc-zen-time'>%s</p>"
        "</div>"
        % (phase, escape(active["label"] or "Untitled"),
           escape(state), shield,
           max(0, remaining), total, ring_mode, t0, escape(caption),
           frac * 100, mid, buttons, sub, t0))

    tools = (
        "<div class='fc-tools'>"
        "<p><button type='button' class='fc-btn' data-fc-zen>"
        "Zen mode (z)</button> "
        "<span class='note'>Full-screen timer. Press z or Esc to "
        "exit.</span></p>"
        + _fc_soundscapes()
        + "</div>")

    footnote = (
        "<p class='how-it-works'>Stay with the timer. End the session to "
        "bank your minutes toward today's ring and your streak.</p>")

    return (strip + tools + footnote + _FC_LIVE_JS)


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


@app.route("/focus")
def focus_page():
    from focuscore import focus as focus_mod

    # Phase 8: settle pomodoro cycles on every view (idempotent).
    try:
        focus_mod.settle_session()
    except Exception:
        # Roadmap 0.3: a stuck session used to fail with no trace.
        logger.exception("settle_session failed on /focus page view")
    active = focus_mod.get_active_session()
    if active:
        body = _fc_active_page(active, focus_mod)
    else:
        body = _fc_idle_page(focus_mod) + _FC_IDLE_JS
    body += "<div class='hm-tabspace' aria-hidden='true'></div>" + _living_tabs("focus")
    return layout("Focus sessions", body, active="focus")


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
            "<div class='badge-toast'><div class='badge-name'>%s</div>"
            "<p>%s</p></div>"
            % (escape(b.get("name", badge_key)),
               escape(b.get("desc", ""))))
    parts.append("</div>")
    return "".join(parts)


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
        logger.warning("peak window rejected; keeping the previous "
                       "window")
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
        # Roadmap 0.3: the user's toggle choice was silently dropped.
        logger.exception("set_setting('audio_cues') failed on /focus/cues")
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
