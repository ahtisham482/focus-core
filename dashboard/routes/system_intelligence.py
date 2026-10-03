"""Intelligence routes: analytics page and report.

Split from dashboard/routes/system.py (roadmap 3.5) -- pure code
move, zero behavior change.
"""
import re
from datetime import date, datetime, timedelta
from html import escape

from flask import Blueprint, request

from dashboard.app import (
    _SCORE_CELL_COLORS,
    _WEEKDAY_NAMES,
    _pulse_cell_color,
    layout,
)
from focuscore import store

bp = Blueprint("system_intelligence", __name__)


@bp.route("/intelligence")
def intelligence_page():
    from focuscore import intelligence as intel_mod

    today = date.today()
    day_from = today - timedelta(days=intel_mod.CHRONOTYPE_DAYS - 1)

    curves = intel_mod.chronotype_curves(day_from, today)
    chrono = intel_mod.classify_chronotype(curves)
    peaks = intel_mod.weekday_peak_windows(curves)
    depth = intel_mod.depth_summary(day_from, today)
    ttf = intel_mod.median_time_to_focus(day_from, today)
    anatomy = intel_mod.distraction_anatomy(day_from, today)

    rates = []
    d = day_from
    while d <= today:
        rate = intel_mod.switch_rate(d.isoformat())["per_hour"]
        if rate is not None:
            rates.append(rate)
        d += timedelta(days=1)
    avg_switch = round(sum(rates) / len(rates), 1) if rates else None

    sel_day = request.args.get("day", today.isoformat())
    try:
        date.fromisoformat(sel_day)
    except ValueError:
        sel_day = today.isoformat()
    timeline = intel_mod.day_timeline(sel_day)

    # --- card 1: chronotype ---
    type_labels = {"morning": "a morning person",
                   "evening": "a night owl",
                   "balanced": "balanced — no strong pattern yet"}
    if chrono["peak_hour"] is None:
        chrono_html = ("<p class='note'>Not enough data yet — keep "
                       "tracking and your rhythm will appear here.</p>")
    else:
        if chrono["type"] == "morning":
            advice = ("You do %.0f%% of your focused work before noon — "
                      "schedule your hardest work in the morning."
                      % (chrono["morning_share"] * 100))
        elif chrono["type"] == "evening":
            advice = ("You do %.0f%% of your focused work after 6pm — "
                      "protect your evenings for deep work."
                      % (chrono["evening_share"] * 100))
        else:
            advice = ("Your focus is spread through the day — watch the "
                      "rhythm grid below for your personal peaks.")
        chrono_html = (
            "<p>You are <b>%s</b>. Your peak hour is "
            "<b>%02d:00</b>.</p><p class='note'>%s</p>"
            % (type_labels[chrono["type"]], chrono["peak_hour"], advice))
    chrono_card = (
        "<div class='card in-insight'><h3>Your chronotype</h3>%s"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>We add up your focused minutes (scores +1/+2) per "
        "hour over the last %d days, separately for each weekday. If 55%% "
        "or more fall between 05:00-12:00 you are a morning person; "
        "18:00-24:00, a night owl.</p></details></div>"
        % (chrono_html, intel_mod.CHRONOTYPE_DAYS))

    # --- card 2: rhythm by weekday ---
    rhythm_rows = []
    for weekday in range(7):
        cells = []
        for hour in range(24):
            cell = curves[weekday][hour]
            pulse = cell["pulse"]
            bg, fg = _pulse_cell_color(pulse, cell["minutes"])
            text = ("%.0f" % pulse) if pulse is not None else "--"
            title = "%s %02d:00 -- %.0f min over %d day(s)" % (
                _WEEKDAY_NAMES[weekday], hour, cell["minutes"], cell["days"])
            cells.append(
                "<td style='background:%s;color:%s;text-align:center' "
                "title='%s'>%s</td>" % (bg, fg, title, text))
        rhythm_rows.append(
            "<tr><td><b>%s</b></td>%s</tr>"
            % (_WEEKDAY_NAMES[weekday], "".join(cells)))
    rhythm_card = (
        "<div class='card'><h3>Rhythm by weekday</h3>"
        "<p class='in-caption'>Rows that glow green at the same hours are "
        "your natural rhythm — schedule hard work there.</p>"
        "<p class='note'>Your Pulse per hour, one row per weekday. Green = "
        "focused, red = distracted. Last %d days.</p>"
        "<div class='in-grid-scroll'><table>%s</table></div>"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>Each cell is the weighted Pulse for that "
        "weekday-hour across the last %d days. Hover a cell for the "
        "minutes and days behind it.</p></details></div>"
        % (intel_mod.CHRONOTYPE_DAYS, "".join(rhythm_rows),
           intel_mod.CHRONOTYPE_DAYS))

    # --- card 3: peak windows (identical days folded together) ---
    if peaks:
        seen = {}
        for w in sorted(peaks):
            key = (peaks[w][0]["start_hour"], peaks[w][0]["end_hour"])
            seen.setdefault(key, []).append(_WEEKDAY_NAMES[w][:3])
        peak_items = "".join(
            "<li><b>%02d:00 - %02d:00</b> &mdash; %s</li>"
            % (s, e, ", ".join(days)) for (s, e), days in seen.items())
    else:
        peak_items = ("<li class='note'>Your peak hours appear here "
                      "after a few days of tracking.</li>")
    peaks_card = (
        "<div class='card'><h3>Protect these hours</h3>"
        "<p class='in-caption'>Your 2-hour peak blocks. Guard these "
        "like meetings.</p>"
        "<ul>%s</ul>"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>For each weekday, the 2-hour window with the "
        "most focused minutes (+1/+2) over the last %d days.</p>"
        "</details></div>" % (peak_items, intel_mod.CHRONOTYPE_DAYS))

    # --- card 4: focus depth ---
    if depth["avg_longest"] is None:
        depth_html = ("<p class='note'>No productive stretches yet — "
                      "your longest focused runs will appear here.</p>")
    else:
        ttf_str = ("%.0f min" % ttf) if ttf is not None else "--"
        switch_str = ("%.1f / hour" % avg_switch) \
            if avg_switch is not None else "--"
        depth_html = (
            "<ul><li>Average longest stretch: "
            "<b>%.0f min</b></li><li>Best day: <b>%s</b> (%.0f min)</li>"
            "<li>Median time to first focus: <b>%s</b></li>"
            "<li>App switches: <b>%s</b></li></ul>"
            % (depth["avg_longest"], depth["best_day"],
               depth["best_minutes"], ttf_str, switch_str))
    depth_card = (
        "<div class='card in-insight'><h3>Focus depth</h3>%s"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>A 'stretch' is unbroken productive time (scores "
        "+1/+2); gaps under 5 minutes don't break it. Time-to-first-focus "
        "is measured from your first tracked activity to your first "
        "25-minute stretch.</p></details></div>" % depth_html)

    # --- card 5: distraction anatomy ---
    if anatomy["top"]:
        max_min = anatomy["top"][0]["minutes"]
        distractor_rows = "".join(
            "<li><b>%s</b> &mdash; %.1fh (%.0f%%)<br>"
            "<div style='background:#eee;height:8px;width:220px'>"
            "<div style='background:#e53935;height:8px;width:%d%%'>"
            "</div></div><span class='note'>e.g. %s</span></li>"
            % (escape(item["app"]), item["minutes"] / 60.0,
               item["share"],
               int(item["minutes"] / max_min * 100) if max_min else 0,
               escape(item["example"][:60]))
            for item in anatomy["top"])
    else:
        distractor_rows = "<li class='note'>No distracting time recorded.</li>"
    if anatomy["entry_points"]:
        entry_rows = "".join(
            "<li><b>%s</b> pulled you in %d time(s)</li>"
            % (escape(e["app"]), e["count"])
            for e in anatomy["entry_points"])
    else:
        entry_rows = "<li class='note'>No entry pattern found.</li>"
    if anatomy["top"]:
        top_app = anatomy["top"][0]
        anatomy_caption = (
            "<p class='in-caption'>%s is your biggest distractor — "
            "%.0f%% of your distracting time goes there.</p>"
            % (escape(top_app["app"]), top_app["share"]))
    else:
        anatomy_caption = ""
    anatomy_card = (
        "<div class='card'><h3>What breaks your focus</h3>%s"
        "<p class='note'>Where your distracting time (-1/-2) goes, and "
        "which app you were using right before each distraction "
        "started.</p><ul>%s</ul><ul>%s</ul>"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>Distractors are apps scoring -1/-2. An 'entry "
        "point' is the app you were using right before a distraction "
        "block started.</p></details></div>"
        % (anatomy_caption, distractor_rows, entry_rows))

    # --- card 7: interactive day timeline ---
    hour_blocks = []
    for info in timeline["hours"]:
        hour = info["hour"]
        if info["minutes"] <= 0:
            hour_blocks.append(
                "<div class='note'>%02d:00 -- no activity</div>" % hour)
            continue
        quarters = "".join(
            "<span style='display:inline-block;width:14px;height:14px;"
            "background:%s' title='%s'></span>"
            % (_SCORE_CELL_COLORS[q][0] if q is not None else "#f0f0f0",
               ("score %+d" % q) if q is not None else "no activity")
            for q in info["quarters"])
        pulse_str = ("Pulse %.0f" % info["pulse"]) \
            if info["pulse"] is not None else "no score"
        act_rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%.0f min</td>"
            "<td style='background:%s;color:%s;text-align:center'>%+d</td>"
            "</tr>"
            % (escape(a["app"]), escape(a["title"][:50]), a["minutes"],
               _SCORE_CELL_COLORS[a["score"]][0],
               _SCORE_CELL_COLORS[a["score"]][1], a["score"])
            for a in info["activities"])
        hour_blocks.append(
            "<details><summary>%s <b>%02d:00</b> -- %.0f min, %s"
            "</summary><table><tr><th>App</th><th>Title</th><th>Time</th>"
            "<th>Score</th></tr>%s</table></details>"
            % (quarters, hour, info["minutes"], pulse_str, act_rows))
    if any(info["minutes"] > 0 for info in timeline["hours"]):
        timeline_body = ("".join(hour_blocks))
        timeline_note = ("Click any hour to see the activities inside it.")
    else:
        timeline_body = (
            "<div class='in-empty'><p><b>Nothing tracked on this day "
            "yet.</b></p></div>")
        timeline_note = ""
    timeline_card = (
        "<div class='card'><h3>Day timeline</h3>%s%s"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>Each hour splits into 15-minute blocks, colored "
        "by the dominant score (green = productive, red = distracting). "
        "Expanding an hour lists the apps and titles in it.</p>"
        "</details></div>"
        % (("<p class='note'>%s</p>" % timeline_note) if timeline_note
           else "", timeline_body))

    flow_res = intel_mod.flow_index(sel_day)
    ratio_buckets = intel_mod.day_ratio_buckets(sel_day)
    switches_res = intel_mod.switch_rate(sel_day)

    tot_sec = sum(ratio_buckets.values())
    deep_pct = (
        (ratio_buckets.get("deep", 0.0) / tot_sec * 100.0) if tot_sec > 0 else 0.0
    )
    sw_hr = switches_res.get("per_hour")
    sw_str = ("%.1f / hr" % sw_hr) if sw_hr is not None else "--"
    prev_day = (date.fromisoformat(sel_day) - timedelta(days=1)).isoformat()

    if flow_res.get("score") is None:
        # FLOW-1: Insufficient data (< 15 min) -> tri-state neutral, NEVER "Flow: 0"
        flow_big = (
            "<span class='in-flow-none'>&mdash; (tracking begins now)</span>"
        )
    else:
        flow_big = ("<b>%d</b><span class='in-flow-max'>/100</span> "
                    "<span class='in-flow-label'>%s</span>"
                    % (flow_res["score"], escape(flow_res["label"])))

    hero_html = (
        "<div class='page-hero in-hero-page'>"
        "<div class='page-hero-text'>"
        "<h1 class='page-title'>Deep Time</h1>"
        "<p class='in-big in-hero-num'>Flow Index %s</p>"
        "<p class='page-sub'>"
        "<span><b>%.0f%%</b> deep work</span>"
        "<span class='page-sub-dot'>&middot;</span>"
        "<span><b>%s</b> context switches</span>"
        "<span class='page-sub-dot'>&middot;</span>"
        "<span>Showing <b>%s</b> — "
        "<a href='/intelligence?day=%s'>previous day</a> | "
        "<a href='/intelligence'>today</a></span>"
        "</p>"
        "</div>"
        "</div>" % (flow_big, deep_pct, sw_str, escape(sel_day), prev_day)
    )


    p12 = _phase12_cards(sel_day)
    footnote = (
        "<p class='in-footnote'>Deep Time reads your tracked activity "
        "and turns it into one number — the Flow Index. The cards below "
        "show the pieces it is made of.</p>")
    body = (p12["coach"] + chrono_card + p12["flow"]
            + p12["depth_timeline"] + p12["donut"] + p12["recovery"]
            + depth_card + anatomy_card + peaks_card + rhythm_card
            + p12["trends"] + timeline_card + p12["report_link"]
            + footnote)
    return layout("Deep time", body, active="intelligence", hero=hero_html)


# ============================================ Phase 12 (v1.14.0) SVGs ---
# Pure server-rendered inline SVG builders. No JS, no external libs.

_SVG_SCORE_COLORS = {2: "#1a7f37", 1: "#4ac26b", 0: "#d0d7de",
                     -1: "#fb8500", -2: "#da3633"}


def _svg_depth_timeline(hours, peak=None, sessions=()):
    """24h stacked depth bars. `hours`: {h: {score: seconds}}.
    `peak`: (start_hour, end_hour) overlay band. `sessions`:
    [(start_hour_float, end_hour_float, label)].

    Fixed binning: exactly 24 hour bins, at most 5 stacked rects per
    bin, so the DOM is bounded at ~120 <rect> regardless of how many
    activity events the day has (GLM P12-6 invariant: never >300).
    """
    W, bar_w, gap = 960, 34, 6
    top, bar_h, label_h = 10, 170, 24
    parts = ["<svg viewBox='0 0 %d %d' width='100%%' role='img' "
             "aria-label='24-hour depth timeline'>" % (W, top + bar_h
                                                       + label_h)]
    max_s = max((sum(h.values()) for h in hours.values()), default=1) \
        or 1
    if peak:
        ps, pe = peak
        x0 = ps * (bar_w + gap)
        x1 = min(pe, 24) * (bar_w + gap) - gap
        parts.append(
            "<rect x='%.1f' y='%d' width='%.1f' height='%d' "
            "fill='#fff8c5' opacity='0.85'><title>Peak energy window"
            "</title></rect>" % (x0, top, x1 - x0, bar_h))
    for h in range(24):
        x = h * (bar_w + gap)
        y = top + bar_h
        segs = []
        for score in (2, 1, 0, -1, -2):
            s = hours[h][score]
            if s <= 0:
                continue
            sh = s / max_s * bar_h
            y -= sh
            segs.append(
                "<rect x='%.1f' y='%.1f' width='%d' height='%.1f' "
                "fill='%s'><title>%02d:00 score %+d: %.0f min</title>"
                "</rect>" % (x, y, bar_w, sh,
                             _SVG_SCORE_COLORS[score], h, score,
                             s / 60.0))
        parts.append("".join(segs))
        if h % 3 == 0:
            parts.append(
                "<text x='%.1f' y='%d' font-size='11' fill='#57606a'>"
                "%02d:00</text>" % (x, top + bar_h + 16, h))
    for s0, s1, label in sessions:
        x0 = s0 * (bar_w + gap)
        x1 = min(s1, 24) * (bar_w + gap) - gap
        parts.append(
            "<rect x='%.1f' y='%d' width='%.1f' height='%d' "
            "fill='none' stroke='#0969da' stroke-width='2' "
            "stroke-dasharray='4,2'><title>Focus session: %s</title>"
            "</rect>" % (x0, top + bar_h - 22, max(x1 - x0, 4), 22,
                         escape(label)))
    parts.append("</svg>")
    return "".join(parts)


def _svg_donut(buckets):
    """Deep/shallow/neutral/distraction donut. `buckets`: minutes."""
    total = sum(buckets.values()) or 1
    r, cx, cy = 60, 80, 80
    circ = 2 * 3.14159265 * r
    order = [("deep", "#1a7f37"), ("shallow", "#4ac26b"),
             ("neutral", "#d0d7de"), ("distraction", "#da3633")]
    parts = ["<svg viewBox='0 0 320 160' width='100%%' role='img' "
             "aria-label='Deep versus shallow work donut'>"]
    offset = 0
    for key, color in order:
        frac = buckets.get(key, 0) / total
        dash = frac * circ
        parts.append(
            "<circle cx='%d' cy='%d' r='%d' fill='none' stroke='%s' "
            "stroke-width='28' stroke-dasharray='%.1f %.1f' "
            "stroke-dashoffset='%.1f' transform='rotate(-90 %d %d)'>"
            "<title>%s: %.0f min (%.0f%%)</title></circle>"
            % (cx, cy, r, color, dash, circ - dash, -offset, cx, cy,
               key.capitalize(), buckets.get(key, 0), frac * 100))
        offset += dash
    deep_pct = buckets.get("deep", 0) / total * 100
    parts.append(
        "<text x='%d' y='%d' text-anchor='middle' font-size='22' "
        "font-weight='bold' fill='#1f2328'>%.0f%%</text>"
        "<text x='%d' y='%d' text-anchor='middle' font-size='11' "
        "fill='#57606a'>deep work</text>" % (cx, cy - 2, deep_pct, cx,
                                             cy + 18))
    lx = 170
    for i, (key, color) in enumerate(order):
        y = 30 + i * 30
        parts.append(
            "<rect x='%d' y='%d' width='14' height='14' fill='%s'/>"
            "<text x='%d' y='%d' font-size='12' fill='#1f2328'>%s "
            "· %.0f min</text>"
            % (lx, y, color, lx + 20, y + 12, key.capitalize(),
               buckets.get(key, 0)))
    parts.append("</svg>")
    return "".join(parts)


def _svg_heatmap(grid):
    """7x24 switch-count heatmap. `grid`: {weekday: {hour: count}}."""
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cw, chh, ox, oy = 34, 22, 44, 8
    W = ox + 24 * cw + 10
    H = oy + 7 * chh + 26
    max_c = max((c for wd in grid.values() for c in wd.values()),
                default=0)

    def _color(c):
        if max_c == 0 or c == 0:
            return "#eaeef2"
        t = c / max_c
        # green -> yellow -> red
        r = int(26 + t * (218 - 26))
        g = int(127 + t * (54 - 127))
        b = int(55 + t * (51 - 55))
        return "#%02x%02x%02x" % (r, g, b)

    parts = ["<svg viewBox='0 0 %d %d' width='100%%' role='img' "
             "aria-label='Weekly switch heatmap'>" % (W, H)]
    for wd in range(7):
        y = oy + wd * chh
        parts.append(
            "<text x='%d' y='%d' font-size='11' fill='#57606a'>%s</text>"
            % (ox - 8, y + 15, names[wd]))
        for h in range(24):
            c = grid[wd][h]
            parts.append(
                "<rect x='%d' y='%d' width='%d' height='%d' "
                "fill='%s'><title>%s %02d:00 -- %d switches</title>"
                "</rect>" % (ox + h * cw, y, cw - 2, chh - 3,
                             _color(c), names[wd], h, c))
    for h in range(0, 24, 3):
        parts.append(
            "<text x='%d' y='%d' font-size='10' fill='#57606a'>%02d</text>"
            % (ox + h * cw, H - 8, h))
    parts.append("</svg>")
    return "".join(parts)


def _svg_sparkline(values, labels=()):
    """Simple polyline sparkline for week trend values."""
    W, H, pad = 600, 120, 14
    n = len(values)
    parts = ["<svg viewBox='0 0 %d %d' width='100%%' role='img' "
             "aria-label='Week flow trend'>" % (W, H)]
    if n < 2:
        parts.append(
            "<text x='%d' y='%d' font-size='12' fill='#57606a'>Not "
            "enough days yet.</text>" % (pad, H // 2))
        parts.append("</svg>")
        return "".join(parts)
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = []
    for i, v in enumerate(values):
        x = pad + i * (W - 2 * pad) / (n - 1)
        y = H - pad - (v - lo) / span * (H - 2 * pad)
        pts.append("%.1f,%.1f" % (x, y))
    parts.append(
        "<polyline points='%s' fill='none' stroke='#0969da' "
        "stroke-width='2'/>" % " ".join(pts))
    for i, v in enumerate(values):
        x = pad + i * (W - 2 * pad) / (n - 1)
        y = H - pad - (v - lo) / span * (H - 2 * pad)
        lab = labels[i] if i < len(labels) else ""
        parts.append(
            "<circle cx='%.1f' cy='%.1f' r='4' fill='#0969da'>"
            "<title>%s: %d</title></circle>" % (x, y, escape(lab), v))
    parts.append("</svg>")
    return "".join(parts)


# ===================================== Phase 12 (v1.14.0) page cards ---

_KNOWN_DISTRACTORS = ("youtube.com", "facebook.com", "instagram.com",
                      "reddit.com", "twitter.com", "x.com", "tiktok.com",
                      "netflix.com")


def _coach_actions(title, body):
    """Concrete next steps for a coaching card.

    coaching_cards() returns only title/body, so actions are inferred
    from its fixed templates:
    - "Protect your peak: HH:MM-...": starter-style focus session.
    - "Take meetings at HH:00-HH:00": next-step text (no scheduling
      contract exists).
    - "Tame <app>": a Shield rule, but only when the app matches a
      known distractor domain.
    - Fallback: plain next-step text.
    """
    no_data = "About 0% of your deep work" in body
    if title.startswith("Protect your peak"):
        m = re.search(r"(\d{2}):00", title)
        hour = m.group(1) if m else None
        if no_data:
            return ("<p class='note'>Next step: this needs a few days of "
                    "tracked time before it means anything.</p>")
        return (
            "<form class='inline' method='post' action='/focus/start'>"
            "<input type='hidden' name='label' value='Peak window work'>"
            "<input type='hidden' name='preset' value='50'>"
            "<button type='submit' class='chip'>Start a 50-min focus "
            "session</button></form>"
            "<p class='note'>Next step: move one hard task into %s:00 "
            "today.</p>" % (hour or "your peak"))
    if title.startswith("Take meetings at"):
        m = re.search(r"(\d{2}):00-(\d{2}):00", title)
        if m:
            return ("<p class='note'>Next step: move one recurring "
                    "meeting into %s:00-%s:00.</p>"
                    % (m.group(1), m.group(2)))
        return ("<p class='note'>Next step: move one recurring meeting "
                "into this window.</p>")
    if title.startswith("Tame "):
        app = title[5:].strip().lower()
        if app and any(d in app for d in _KNOWN_DISTRACTORS):
            return (
                "<form class='inline' method='post' "
                "action='/shield/rule/add'>"
                "<input type='hidden' name='name' value='Tame %s'>"
                "<input type='hidden' name='rule_type' value='app'>"
                "<input type='hidden' name='key' value='%s'>"
                "<input type='hidden' name='action' value='soft'>"
                "<input type='hidden' name='days' value='0,1,2,3,4'>"
                "<input type='hidden' name='start_time' value='09:00'>"
                "<input type='hidden' name='end_time' value='18:00'>"
                "<button type='submit' class='chip'>Cap it in work "
                "hours</button></form>"
                % (escape(app), escape(app)))
        return ("<p class='note'>Next step: add it as a soft Shield rule "
                "in work hours — Shield, Add a rule.</p>")
    return ("<p class='note'>Next step: keep tracking for a few more days "
            "to sharpen this.</p>")


def _phase12_cards(sel_day):
    """Build the Phase 12 analytics cards HTML for /intelligence.

    Returns a dict of named cards so the page can order them by
    importance (advice first, detail after).
    """
    from focuscore import chronotype as chrono_mod
    from focuscore import intelligence as intel_mod

    flow = intel_mod.flow_index(sel_day)
    delta_txt = ""
    if flow["wow_delta"] is not None:
        arrow = ("▲" if flow["wow_delta"] > 0 else
                 "▼" if flow["wow_delta"] < 0 else "=")
        delta_txt = ("<p class='note'>%s %d vs last 7 days</p>"
                     % (arrow, abs(flow["wow_delta"])))
    if flow["score"] is None:
        flow_card = (
            "<div class='card in-flow'><h3>Flow Index · %s</h3>"
            "<p><b>Insufficient Data</b></p><p class='note'>%s</p></div>"
            % (escape(sel_day), escape(flow["note"] or "")))
    else:
        flow_card = (
            "<div class='card in-flow'><h3>Flow Index · %s</h3>"
            "<p style='font-size:2.625rem;font-weight:bold;margin:4px 0'>%d"
            "<span style='font-size:1rem;color:var(--ink-muted)'>/100</span></p>"
            "<p><b>%s</b></p>%s"
            "<p class='in-caption'>One number for the whole day: deep-work "
            "share, how fast you reach focus, and how little you switch."
            "</p>"
            "<p class='note'>Deep work %d pts + steadiness %d pts + "
            "low switching %d pts.</p>"
            "<details class='how'><summary>How we compute this</summary>"
            "<p class='note'>40 points for your share of +1/+2 minutes, "
            "30 for how fast you reach focus (median time-to-focus), 30 "
            "for few context switches per hour. Integer math, 0-100.</p>"
            "</details></div>"
            % (escape(sel_day), flow["score"], escape(flow["label"]),
               delta_txt, flow["components"]["deep_ratio_pts"],
               flow["components"]["ttf_pts"],
               flow["components"]["switch_pts"]))

    # 24h depth timeline with peak overlay + session annotations.
    hours = intel_mod.day_hourly_depth(sel_day)
    enabled, pstart, pend = chrono_mod.get_window()
    peak = (pstart.hour + pstart.minute / 60.0,
            pend.hour + pend.minute / 60.0) if enabled else None
    sessions = []
    for s in store.get_day_sessions(sel_day) or ():
        try:
            st = datetime.fromisoformat(
                s["started_at"].replace("Z", ""))
            en = datetime.fromisoformat(
                (s["ended_at"] or s["started_at"]).replace("Z", ""))
            sessions.append((st.hour + st.minute / 60.0,
                             en.hour + en.minute / 60.0,
                             s.get("label") or "session"))
        except (ValueError, TypeError, KeyError):
            continue
    timeline_card = (
        "<div class='card'><h3>24-hour depth timeline · %s</h3>%s"
        "<p class='in-caption'>The thickest bar is your deepest working "
        "hour.</p>"
        "<p class='note'>Green = deep/productive, grey = neutral, "
        "orange/red = distraction.</p></div>"
        % (escape(sel_day),
           _svg_depth_timeline(hours, peak=peak, sessions=sessions)))

    buckets = intel_mod.day_ratio_buckets(sel_day)
    _tot = sum(buckets.values())
    if _tot > 0:
        _deep_pct = buckets.get("deep", 0.0) / _tot * 100.0
        donut_caption = (
            "<p class='in-caption'>%.0f%% of the day was deep work.</p>"
            % _deep_pct)
    else:
        donut_caption = (
            "<p class='in-caption'>Nothing tracked for this day yet.</p>")
    donut_card = (
        "<div class='card'><h3>Deep vs shallow · %s</h3>%s%s</div>"
        % (escape(sel_day), donut_caption, _svg_donut(buckets)))

    rec = intel_mod.recovery_cost(sel_day)
    friction = "".join(
        "<li><b>%s</b> -- %.0f min of distraction</li>"
        % (escape(f["app"]), f["minutes"])
        for f in rec["top_friction"])
    recovery_card = (
        "<section class='in-strip'><h3>Distraction recovery cost · "
        "%s</h3>"
        "<p>About <b>%d minutes</b> lost to context recovery today "
        "(%d switches, %d distraction blocks).</p>"
        "<p class='note'>Rule of thumb: each switch costs ~1 minute, "
        "each distraction block ~10 minutes to get back into flow.</p>"
        "%s</section>"
        % (escape(sel_day), rec["recovery_minutes"], rec["switches"],
           rec["distraction_blocks"],
           ("<ul>%s</ul>" % friction) if friction
           else "<p class='note'>No friction apps today — a clean, "
                "focused day.</p>"))

    coach = intel_mod.coaching_cards()
    coach_items = "".join(
        "<div class='in-advice-card'><b>%s</b><p>%s</p>%s</div>"
        % (escape(c["title"]), escape(c["body"]),
           _coach_actions(c["title"], c["body"]))
        for c in coach)
    coach_card = (
        "<div class='in-advice-head'><h2>Coaching for tomorrow</h2>"
        "<p class='in-caption'>Advice from your last 28 days. Each card "
        "has a concrete next step.</p></div>"
        "<div class='in-advice'>%s</div>" % coach_items)

    trends = intel_mod.week_flow_trends()
    trend_txt = ("<p>This week: <b>%.1f</b>%s</p>"
                 % (trends["this_week"]["mean"],
                    (" (baseline %.1f, delta %+.1f)"
                     % (trends["baseline_mean"], trends["delta"]))
                    if trends["delta"] is not None
                    else " (no baseline yet)"))
    spark = _svg_sparkline([d["score"] for d in trends["daily"]],
                           [d["day"] for d in trends["daily"]])
    today_d = date.today()
    heat = intel_mod.switch_heatmap_7x24(
        today_d - timedelta(days=27), today_d)
    trends_card = (
        "<div class='card'><h3>Week trends</h3>"
        "<p class='in-caption'>Seven-day rolling trend: are you going "
        "deeper or shallower?</p>%s%s"
        "<h4>Context switches by weekday and hour (4 weeks)</h4>%s"
        "</div>" % (trend_txt, spark, _svg_heatmap(heat)))

    report_link = (
        "<div class='card'><h3>Executive report</h3>"
        "<p><a href='/intelligence/report?day=%s'>Open the printable "
        "Deep Work Intelligence Report</a> -- clean print layout, no "
        "scripts.</p></div>" % escape(sel_day))

    return {"flow": flow_card, "depth_timeline": timeline_card,
            "donut": donut_card, "recovery": recovery_card,
            "coach": coach_card, "trends": trends_card,
            "report_link": report_link}


@bp.route("/intelligence/report")
def intelligence_report():
    """Standalone printable Deep Work Intelligence Report (Phase 12).

    Strict CSP, zero JavaScript, print CSS -- Phase 9/10 standard.
    """
    from focuscore import chronotype as chrono_mod
    from focuscore import intelligence as intel_mod

    sel_day = request.args.get("day", date.today().isoformat())
    try:
        date.fromisoformat(sel_day)
    except ValueError:
        sel_day = date.today().isoformat()

    flow = intel_mod.flow_index(sel_day)
    if flow["score"] is None:
        flow_big = ("<p><b>Insufficient Data</b></p><p>%s</p>"
                    % escape(flow["note"] or ""))
    else:
        flow_big = ("<p style='font-size:2.25rem;font-weight:bold'>%d/100 · "
                    "%s</p>" % (flow["score"], escape(flow["label"])))
    buckets = intel_mod.day_ratio_buckets(sel_day)
    hours = intel_mod.day_hourly_depth(sel_day)
    enabled, pstart, pend = chrono_mod.get_window()
    peak = (pstart.hour + pstart.minute / 60.0,
            pend.hour + pend.minute / 60.0) if enabled else None
    rec = intel_mod.recovery_cost(sel_day)
    coach = intel_mod.coaching_cards()
    trends = intel_mod.week_flow_trends()

    def _print_step(card):
        # Print-safe next step: strip the form markup, keep the words.
        html = _coach_actions(card["title"], card["body"])
        text = re.sub(r"\s+", " ",
                      re.sub(r"<[^>]*>", " ", html)).strip()
        parts = [p.strip().rstrip(".") for p in text.split("Next step:")
                 if p.strip()]
        parts = [p[0].upper() + p[1:] if p else p for p in parts]
        return ". ".join(parts) + ("." if parts else "")

    coach_html = "".join(
        "<div class='coach'><h4>%s</h4><p>%s</p>"
        "<p class='note'>Next step: %s</p></div>"
        % (escape(c["title"]), escape(c["body"]),
           escape(_print_step(c)))
        for c in coach)
    friction = "".join(
        "<li>%s &mdash; %.0f min</li>" % (escape(f["app"]), f["minutes"])
        for f in rec["top_friction"])
    trend_line = ("Week mean %.1f%s"
                  % (trends["this_week"]["mean"],
                     (" (baseline %.1f, delta %+.1f)"
                      % (trends["baseline_mean"], trends["delta"]))
                     if trends["delta"] is not None else ""))

    from focuscore import __version__ as focuscore_version

    tot_sec = sum(buckets.values())
    if tot_sec > 0:
        donut_caption = (
            "<p class='caption'>%.0f%% of the day was deep work.</p>"
            % (buckets.get("deep", 0.0) / tot_sec * 100.0))
    else:
        donut_caption = ""
    if tot_sec == 0:
        main_body = (
            "<div class='empty'><p><b>Nothing tracked for this day "
            "yet.</b></p><p>Your first report lands after a full day of "
            "tracking. Come back tomorrow.</p></div>")
    else:
        main_body = (
            "<div class='hero'><h3>Flow Index · %s</h3>%s"
            "<p class='caption'>One number for the whole day: deep-work "
            "share, how fast you reach focus, and how little you "
            "switch.</p></div>"
            "<div class='card'><h3>24-hour depth timeline</h3>"
            "<p class='caption'>The thickest bar is your deepest working "
            "hour.</p>%s</div>"
            "<div class='card'><h3>Deep vs shallow</h3>%s%s</div>"
            "<div class='card'><h3>Recovery cost</h3>"
            "<p><b>%d minutes</b> lost to context recovery "
            "(%d switches, %d distraction blocks).</p>"
            "<p class='note'>Rule of thumb: each switch costs ~1 minute, "
            "each distraction block ~10 minutes to get back into "
            "flow.</p><ul>%s</ul></div>"
            "<div class='card'><h3>Coaching</h3>%s</div>"
            "<div class='card'><h3>Week trend</h3><p>%s</p>%s</div>"
            % (escape(sel_day), flow_big,
               _svg_depth_timeline(hours, peak=peak),
               donut_caption, _svg_donut(buckets),
               rec["recovery_minutes"], rec["switches"],
               rec["distraction_blocks"], friction, coach_html,
               escape(trend_line),
               _svg_sparkline([d["score"] for d in trends["daily"]],
                              [d["day"] for d in trends["daily"]])))

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta http-equiv=\"Content-Security-Policy\" content=\""
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "font-src data:;\">"
        "<title>Deep Work Intelligence Report &mdash; %s</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:900px;"
        "margin:24px auto;padding:0 16px;color:#1f2328}"
        ".card{border:1px solid #d0d7de;border-radius:8px;padding:16px;"
        "margin:16px 0;break-inside:avoid}"
        ".coach{background:#f6f8fa;border-radius:8px;padding:12px;"
        "margin:8px 0}"
        ".hero{border:1px solid #d0d7de;border-radius:8px;padding:20px;"
        "margin:16px 0;background:#f6f8fa;break-inside:avoid}"
        ".caption{color:#57606a;font-style:italic;font-size:0.8125rem}"
        ".empty{border:1px dashed #d0d7de;border-radius:8px;padding:24px;"
        "margin:16px 0;text-align:center}"
        ".footnote{color:#57606a;font-size:0.8125rem;margin-top:24px}"
        "h1{font-size:1.625rem}h3{margin-top:0}"
        ".note{color:#57606a;font-size:0.8125rem}"
        "@media print{.noprint{display:none}"
        "body{margin:0;max-width:none;-webkit-print-color-adjust:exact;"
        "print-color-adjust:exact}.card{box-shadow:none}.hero{box-shadow:none}}"
        "</style></head><body>"
        "<h1>Deep Work Intelligence Report</h1>"
        "<p class='noprint'><a href='/intelligence'>Back to Deep time"
        "</a> | Print via your browser (Ctrl+P).</p>"
        "%s"
        "<p class='footnote'>This report reads your tracked activity "
        "automatically — nothing to fill in. Details below explain how "
        "each part is computed.</p>"
        "<footer class='note'>Generated: %s · Focus Core v%s · "
        "Schema v9</footer>"
        "</body></html>"
        % (escape(sel_day), main_body,
           datetime.now().isoformat(timespec="seconds"),
           focuscore_version))
