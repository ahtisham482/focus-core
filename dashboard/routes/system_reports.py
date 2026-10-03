"""Report routes: weekly report and coaching pages.

Split from dashboard/routes/system.py (roadmap 3.5) -- pure code
move, zero behavior change.
"""
from datetime import date, datetime, timedelta
from html import escape

from flask import Blueprint, request

from dashboard.app import (
    _cat_color,
    _parse_day,
    _pulse_cell_color,
    chart_table,
    layout,
)

bp = Blueprint("system_reports", __name__)


@bp.route("/report")
def report_page():
    from focuscore import reports as rep_mod

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_arg = _parse_day(request.args.get("week"))
    week_start = (datetime.strptime(week_arg, "%Y-%m-%d").date()
                  if week_arg else monday)
    # Snap any date to its Monday.
    week_start -= timedelta(days=week_start.weekday())
    rep = rep_mod.weekly_report(week_start)
    prev_week = (week_start - timedelta(days=7)).isoformat()
    next_week = (week_start + timedelta(days=7)).isoformat()
    # Read-only second report for the "vs last week" delta.
    prev = rep_mod.weekly_report(week_start - timedelta(days=7))
    pulse_delta = (rep["avg_pulse"] - prev["avg_pulse"]
                   if rep["avg_pulse"] is not None
                   and prev["avg_pulse"] is not None else None)
    hours_delta = rep["total_hours"] - prev["total_hours"]
    if pulse_delta is None:
        delta_txt = "No data last week to compare against yet."
    else:
        arrow = ("▲" if pulse_delta > 0 else
                 "▼" if pulse_delta < 0 else "=")
        delta_txt = ("%s %.1f Pulse, %s%.1f h vs last week"
                     % (arrow, abs(pulse_delta),
                        "+" if hours_delta >= 0 else "\u2212",
                        abs(hours_delta)))

    avg_pulse = ("%.1f" % rep["avg_pulse"]
                 if rep["avg_pulse"] is not None else "--")
    fs = rep["focus_sessions"]
    hero = (
        "<div class='in-hero'><p class='in-kicker'>Weekly report · "
        "%s to %s</p>"
        "<p class='in-big'>%.1f <span>h tracked</span> · %s "
        "<span>Pulse</span></p>"
        "<p class='in-caption'>%s · %d focus sessions (%.0f min, "
        "%d blocks)</p>"
        "<p class='in-weeknav'><a href='/report?week=%s'>"
        "&larr; Previous week</a> &middot; "
        "<a href='/report?week=%s'>Next week &rarr;</a></p></div>"
        % (rep["week_start"], rep["week_end"], rep["total_hours"],
           avg_pulse, delta_txt, fs["count"], fs["focus_minutes"],
           fs["blocks"], prev_week, next_week))
    footnote = (
        "<p class='in-footnote'>Reports are built from your tracked "
        "time. This week fills in as you track &mdash; the full picture "
        "lands on Sunday.</p>")

    if rep["total_hours"] == 0:
        body = (hero +
                "<div class='in-empty'><p><b>Nothing here yet.</b> "
                "Your first report lands after a full day of tracking."
                "</p></div>" + footnote)
        return layout("Weekly report %s" % rep["week_start"], body,
                      active="report")

    day_rows = "".join(
        "<tr><td>%s</td><td>%.2f</td><td>%s</td><td>%.2f</td></tr>"
        % (d["date"], d["total_hours"],
           ("%.1f" % d["pulse"]) if d["pulse"] is not None else "--",
           d["focus_hours"])
        for d in rep["days"])
    days_table = (
        "<div class='card'><h3>Days</h3>"
        "<div class='in-grid-scroll'>"
        "<table><tr><th>Date</th><th>Tracked hours</th><th>Pulse</th>"
        "<th>Focus hours</th></tr>%s</table></div></div>" % day_rows)

    # CSS-only bar charts (no JavaScript): category hours and daily Pulse.
    max_cat = max([h for _, h in rep["top_categories"]] or [0])
    cat_bars = "".join(
        "<div class='hbar'><span class='lbl'>%s</span>"
        "<span class='track'><span class='fill' style='display:block;"
        "width:%.1f%%;background:%s'></span></span>"
        "<span class='val'>%.2f h</span></div>"
        % (escape(name), (hours / max_cat * 100) if max_cat else 0,
           _cat_color(name), hours)
        for name, hours in rep["top_categories"])
    if rep["top_categories"]:
        top_name, top_hours = rep["top_categories"][0]
        top_share = (top_hours / rep["total_hours"] * 100
                     if rep["total_hours"] else 0)
        cats_caption = (
            "<p class='in-caption'>%s leads with %.1f h &mdash; %.0f%% of "
            "your tracked time.</p>"
            % (escape(top_name), top_hours, top_share))
    else:
        cats_caption = ""
    cats_table = chart_table(
        ("Category", "Hours"),
        [(name, f"{hours:.2f} h") for name, hours in rep["top_categories"]]
    ) if rep["top_categories"] else ""
    cats_chart = (
        "<div class='card'><h3>Top categories</h3>%s%s%s</div>"
        % (cats_caption, cat_bars or "<p class='note'>No data.</p>",
           cats_table))

    day_bars = "".join(
        "<div class='hbar'><span class='lbl'>%s</span>"
        "<span class='track'><span class='fill' style='display:block;"
        "width:%.1f%%;background:%s'></span></span>"
        "<span class='val'>%s</span></div>"
        % (d["date"][5:],
           (d["pulse"] if d["pulse"] is not None else 0),
           "#2e7d32" if (d["pulse"] or 0) >= 60 else
           ("#f9a825" if (d["pulse"] or 0) >= 40 else "#e53935"),
           ("%.0f" % d["pulse"]) if d["pulse"] is not None else "--")
        for d in rep["days"])
    pulse_days = [d for d in rep["days"] if d["pulse"] is not None]
    if pulse_days:
        best = max(pulse_days, key=lambda d: d["pulse"])
        best_name = datetime.strptime(
            best["date"], "%Y-%m-%d").strftime("%a")
        pulse_caption = (
            "<p class='in-caption'>Your best day was %s (Pulse %.0f). "
            "Do more of whatever that day looked like.</p>"
            % (best_name, best["pulse"]))
    else:
        pulse_caption = ""
    pulse_table = chart_table(
        ("Day", "Pulse"),
        [(d["date"][5:],
          f"{d['pulse']:.0f}" if d["pulse"] is not None else "--")
         for d in rep["days"]])
    pulse_chart = (
        "<div class='card'><h3>Pulse through the week</h3>"
        "<p class='note'>Daily Pulse, 0-100.</p>%s%s%s</div>"
        % (pulse_caption, day_bars, pulse_table))

    goal_rows = []
    for goal in rep["goals"]:
        hit, total = goal["days_hit"], goal["days_total"]
        pct = (hit / total * 100) if total else 0
        color = "#2e7d32" if pct >= 80 else ("#f9a825" if pct >= 50
                                            else "#e53935")
        goal_rows.append(
            "<tr><td>%s</td><td>%d / %d days</td>"
            "<td><div class='progress'><div style='width:%.0f%%;"
            "background:%s'></div></div></td></tr>"
            % (escape(goal["name"]), hit, total, pct, color))
    goals_table = (
        "<div class='card'><h3>Goal hit-rate</h3>"
        "<p class='note'>Days the goal was hit, out of days with tracked "
        "data.</p>"
        "<div class='in-grid-scroll'>"
        "<table><tr><th>Goal</th><th>Hit</th><th></th></tr>%s</table>"
        "</div></div>"
        % ("".join(goal_rows)
           or "<tr><td colspan='3' class='note'>No goals yet.</td></tr>"))

    body = (hero + pulse_chart + cats_chart + days_table + goals_table
            + footnote)
    return layout("Weekly report %s" % rep["week_start"], body,
                  active="report")


@bp.route("/coaching")
def coaching_page():
    from focuscore import coaching as coach_mod
    from focuscore.scoring import productivity_pulse as _pp

    today = date.today()
    day_from = today - timedelta(days=6)
    grid = coach_mod.daily_hourly(day_from, today)
    hourly = coach_mod.hourly_productivity(day_from, today)
    windows = coach_mod.best_windows(day_from, today)
    warnings = coach_mod.burnout_warnings(day_from, today)

    days = sorted(grid.keys())

    # The one insight: when focus peaks.
    scored_hours = [(h, hourly[h]["pulse"]) for h in range(24)
                    if hourly[h]["pulse"] is not None
                    and hourly[h]["minutes"] > 0]
    if scored_hours:
        best_h = max(scored_hours, key=lambda t: t[1])[0]
        best_pulse = max(scored_hours, key=lambda t: t[1])[1]
        hero = (
            "<div class='in-hero'><p class='in-kicker'>Coaching · last 7 "
            "days</p>"
            "<p class='in-big'>Your deep work peaks at %02d:00.</p>"
            "<p class='in-caption'>%02d:00-%02d:00 has your highest Pulse "
            "(%.0f) over the last 7 days. Protect that hour.</p></div>"
            % (best_h, best_h, (best_h + 2) % 24, best_pulse))
    else:
        best_h = None
        hero = (
            "<div class='in-hero'><p class='in-kicker'>Coaching · last 7 "
            "days</p>"
            "<p class='in-big'>No rhythm yet.</p>"
            "<p class='in-caption'>After a full day of tracking, your "
            "rhythm appears here. Come back tomorrow.</p></div>")

    # Lowest-focus 2h block between 09:00 and 17:00: the meeting window.
    focus_minutes = {}
    for h in range(24):
        fm = 0.0
        for day_str in days:
            sbl = grid[day_str][h]["seconds_by_level"]
            fm += (sbl.get(2, 0) + sbl.get(1, 0)) / 60.0
        focus_minutes[h] = fm
    meet_h = min(range(9, 16),
                 key=lambda h: focus_minutes[h] + focus_minutes[h + 1])

    header = "".join(
        "<th>%s</th>" % datetime.strptime(d, "%Y-%m-%d").strftime("%a %m-%d")
        for d in days)
    rows = []
    for hour in range(24):
        cells = []
        for day_str in days:
            bucket = grid[day_str][hour]
            minutes = bucket["minutes"]
            pulse = (_pp(bucket["seconds_by_level"])
                     if minutes > 0 else None)
            bg, fg = _pulse_cell_color(pulse, minutes)
            text = ("%.0f" % pulse) if pulse is not None else "--"
            title = "%s %02d:00 -- %.0f min" % (day_str, hour, minutes)
            cells.append(
                "<td style='background:%s;color:%s;text-align:center' "
                "title='%s'>%s</td>" % (bg, fg, title, text))
        rows.append("<tr><td><b>%02d:00</b></td>%s</tr>"
                    % (hour, "".join(cells)))
    if scored_hours:
        heat_caption = ("<p class='in-caption'>Your best hours glow green "
                        "— most days peak %02d:00-%02d:00.</p>"
                        % (best_h, (best_h + 2) % 24))
    else:
        heat_caption = ""
    heatmap = (
        "<div class='card'><h3>Your week by hour</h3>"
        "<p class='note'>Each cell is the Pulse for that hour (0-100). "
        "Green = focused, red = distracted. Last 7 days.</p>%s"
        "<div class='in-grid-scroll'>"
        "<table><tr><th>Hour</th>%s</tr>%s</table></div></div>"
        % (heat_caption, header, "".join(rows)))

    # Average-day summary: one horizontal strip of the 24 hours.
    avg_header = "".join("<th>%02d</th>" % h for h in range(24))
    avg_cells = []
    for hour in range(24):
        info = hourly[hour]
        bg, fg = _pulse_cell_color(info["pulse"], info["minutes"])
        text = ("%.0f" % info["pulse"]) if info["pulse"] is not None else "--"
        avg_cells.append(
            "<td style='background:%s;color:%s;text-align:center' "
            "title='%02d:00 -- %.0f min total'>%s</td>"
            % (bg, fg, hour, info["minutes"], text))
    avg_row = (
        "<div class='card'><h3>Average day</h3>"
        "<p class='note'>All 7 days combined: when your focus usually "
        "peaks.</p>"
        "<div class='in-grid-scroll'><table><tr>%s</tr><tr>%s</tr></table>"
        "</div></div>"
        % (avg_header, "".join(avg_cells)))

    real_windows = [w for w in windows if w["focus_minutes"] > 0]
    window_items = "".join(
        "<li><b>%02d:00 - %02d:00</b> · %.0f focus minutes"
        "%s</li>"
        % (w["start_hour"], w["end_hour"], w["focus_minutes"],
           (", Pulse %.0f" % w["avg_pulse"])
           if w["avg_pulse"] is not None else "")
        for w in real_windows)
    if real_windows:
        w0 = real_windows[0]
        actions = (
            "<div class='in-advice'><div class='in-advice-card'>"
            "<b>Protect your peak: %02d:00-%02d:00</b>"
            "<p>%.0f focused minutes landed in this window over the last "
            "7 days. Guard it for your hardest work.</p>"
            "<form class='inline' method='post' action='/focus/start'>"
            "<input type='hidden' name='label' value='Peak window work'>"
            "<input type='hidden' name='preset' value='50'>"
            "<button type='submit' class='chip'>Start a 50-min focus "
            "session</button></form></div>"
            "<div class='in-advice-card'>"
            "<b>Take meetings at %02d:00-%02d:00</b>"
            "<p>Your focus is naturally lowest here on workdays, so "
            "meetings cost you the least deep-work time.</p>"
            "<p class='note'>Next step: move one recurring meeting into "
            "this window.</p></div></div>"
            % (w0["start_hour"], w0["end_hour"], w0["focus_minutes"],
               meet_h, meet_h + 2))
        windows_html = (
            "<div class='card'><h3>Your best focus windows</h3>"
            "<p class='in-caption'>The 2-hour blocks where you do your "
            "most focused work. Guard these like meetings.</p>"
            "<ul>%s</ul></div>" % window_items)
    else:
        actions = ""
        windows_html = (
            "<div class='in-empty'><p><b>No focus windows yet.</b> "
            "After a full day of tracking, your best hours appear here."
            "</p></div>")

    if warnings:
        warn_items = "".join(
            "<li><b>%s</b><br><span class='note'>%s</span></li>"
            % (escape(w["message"]), escape(w["detail"]))
            for w in warnings)
        warnings_html = (
            "<section class='in-strip'><h3>Warnings</h3><ul>%s</ul>"
            "</section>" % warn_items)
    else:
        warnings_html = (
            "<section class='in-strip'><h3>Warnings</h3>"
            "<p>No warnings — looking good.</p></section>")

    footnote = (
        "<p class='in-footnote'>Coaching reads your last 7 days of "
        "tracked time. The more you track, the sharper it gets.</p>")
    body = (hero + actions + warnings_html + windows_html + heatmap
            + avg_row + footnote)
    return layout("Coaching", body, active="coaching")
