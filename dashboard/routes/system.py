"""System routes: backup, update, report, coaching, shield, intelligence.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from flask import redirect, request

from focuscore import store
from dashboard.app import app
from dashboard.app import (
    _SCORE_CELL_COLORS,
    _WEEKDAY_NAMES,
    _cat_color,
    _delta_str,
    _parse_day,
    _pulse_cell_color,
    _rule_schedule_text,
    layout,
)

@app.route("/backup")
def backup_page():
    from focuscore import backup as backup_mod

    drive = backup_mod.find_drive_folder()
    folder = backup_mod.backup_dir()
    backups = backup_mod.list_backups()

    if drive:
        where_html = ("<p><b>Google Drive detected: yes.</b><br>"
                      "<span class='note'>Backups go to:<br><code>%s</code>"
                      "<br>They sync to your Google account automatically, "
                      "so they are waiting for you on a new laptop.</span>"
                      "</p>" % escape(str(folder)))
    else:
        where_html = ("<p><b>Google Drive detected: no.</b><br>"
                      "<span class='note'>Backups go to:<br><code>%s</code>"
                      "<br>Install Google Drive for Desktop and they will "
                      "move there automatically.</span></p>"
                      % escape(str(folder)))

    if backups:
        last = backups[0]["modified"].strftime("%Y-%m-%d %H:%M")
        last_html = "<p>Last backup: <b>%s</b> (%d backups kept).</p>" % (
            last, len(backups))
    else:
        last_html = "<p><b>No backups yet.</b> Make your first one now.</p>"

    rows = []
    for b in backups:
        size_kb = b["size_bytes"] / 1024.0
        rows.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%.0f KB</td>"
            "<td><form class='inline' method='post' "
            "action='/backup/restore' onsubmit=\"return confirm('Restore "
            "this backup? Your current data is first copied to a safety "
            "file, so nothing is lost.');\">"
            "<input type='hidden' name='name' value='%s'>"
            "<button type='submit' class='secondary'>Restore</button>"
            "</form></td></tr>"
            % (escape(b["name"]),
               b["modified"].strftime("%Y-%m-%d %H:%M"),
               size_kb, escape(b["name"])))
    table = (
        "<table><tr><th>Backup</th><th>Made</th><th>Size</th><th></th></tr>"
        "%s</table>"
        % ("".join(rows)
           or "<tr><td colspan='4' class='note'>No backups yet.</td></tr>"))

    body = (
        "<div class='card'><h3>Where your backups go</h3>%s%s"
        "<form method='post' action='/backup/now'>"
        "<button type='submit'>Back up now</button></form>"
        "<p class='note'>Focus Core also backs up by itself every day when "
        "you start it (only if the last backup is older than 24 hours).</p>"
        "</div>"
        "<div class='card'><h3>Your backups</h3>%s</div>"
        "<div class='card'><h3>Your data</h3>"
        "<p class='note'>Everything lives on this PC in "
        "<code>focuscore.db</code>. Export: any timesheet day can be "
        "saved as CSV from the Timesheet page; a full copy is any backup "
        "from this page. Delete: to remove all your data, delete "
        "<code>focuscore.db</code> (make a backup first).</p></div>"
        "<div class='card'><h3>Moving to a new laptop</h3>"
        "<p class='note'>1. On the new laptop, install Focus Core and "
        "Google Drive, and let Drive finish syncing.<br>"
        "2. Copy the newest <code>focuscore-*.db</code> file from the "
        "\"Focus Core Backups\" folder into the Focus Core folder and "
        "rename it to <code>focuscore.db</code>. Done -- all your history "
        "is back.</p></div>"
        % (where_html, last_html, table)
    )
    return layout("Backup", body, active="backup")


@app.route("/backup/now", methods=["POST"])
def backup_now():
    from focuscore import backup as backup_mod

    try:
        backup_mod.create_backup()
    except FileNotFoundError as exc:
        return layout("Backup",
                      "<div class='card'><p><b>Could not back up:</b> %s</p>"
                      "<p><a href='/backup'>Back</a></p></div>"
                      % escape(str(exc)), help_key="backup"), 400
    return redirect("/backup")


@app.route("/backup/restore", methods=["POST"])
def backup_restore():
    import warnings
    from focuscore import backup as backup_mod

    name = (request.form.get("name") or "").strip()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            safety = backup_mod.restore_backup(name)
    except (ValueError, FileNotFoundError) as exc:
        return layout("Backup",
                      "<div class='card'><p><b>Could not restore:</b> %s</p>"
                      "<p><a href='/backup'>Back</a></p></div>"
                      % escape(str(exc))), 400
    legacy_note = ""
    if any("could not be verified" in str(w.message) for w in caught):
        legacy_note = (
            "<p class='note'>Note: this backup was made before safety "
            "checks were added, so it could not be verified. Your data "
            "was restored normally.</p>")
    body = (
        "<div class='card'><h3>Backup restored</h3>"
        "<p>Your data was restored from <code>%s</code>.</p>"
        "%s"
        "<p class='note'>Safety copy of your previous data: "
        "<code>%s</code></p>"
        "<p><a class='btn' href='/'>Go to Home</a></p></div>"
        % (escape(name), legacy_note, escape(str(safety) if safety else "none -- "
               "there was no previous database")))
    return layout("Backup restored", body, active="backup")


@app.route("/update")
def update_page():
    from focuscore import updater as updater_mod

    refresh = request.args.get("refresh") == "1"
    status = updater_mod.check_for_update(force=refresh)

    if status["status"] == "dev-copy":
        body = (
            "<div class='card'><h3>Updates</h3>"
            "<p>This is a developer copy of Focus Core, so it doesn't "
            "update itself. Pull the newest code (or grab the newest zip) "
            "the way you usually do.</p>"
            "<p class='note'>One-click updates are for installed copies "
            "only.</p></div>")
        return layout("Updates", body, help_key="update")

    if status["status"] == "error":
        body = (
            "<div class='card'><h3>Updates</h3>"
            "<p><b>Couldn't check for updates:</b> %s</p>"
            "<p class='note'>This usually means no internet, or the releases "
            "page isn't public. Nothing changed -- you're still on %s.</p>"
            "<p><a class='btn' href='/update?refresh=1'>Check again</a></p>"
            "</div>"
            % (escape(status["error"]), escape(status["current"])))
        return layout("Updates", body, help_key="update")

    head = ("<div class='card'><h3>Updates</h3>"
            "<p>You're on <b>%s</b>.</p>"
            % escape(status["current"]))
    if not status["update_available"]:
        body = (head +
                "<p><b>You're up to date.</b> Focus Core checks once a day "
                "by itself.</p>"
                "<p><a class='btn' href='/update?refresh=1'>Check again</a>"
                "</p></div>")
        return layout("Updates", body, help_key="update")

    body = (
        head +
        "<p><b>Version %s is available.</b></p>"
        "<form method='post' action='/update/start' onsubmit=\"return "
        "confirm('Update to %s now? A safety backup is made first, then "
        "Focus Core closes, updates, and reopens by itself.');\">"
        "<button type='submit'>Update to %s now</button></form>"
        "<p class='note'>Your data is never touched by the update -- and a "
        "safety backup is made first anyway. The download is about 25 MB."
        "</p></div>"
        % (escape(status["latest"]), escape(status["latest"]),
           escape(status["latest"])))
    return layout("Updates", body, help_key="update")


@app.route("/update/start", methods=["POST"])
def update_start():
    import tempfile
    from focuscore import backup as backup_mod
    from focuscore import updater as updater_mod

    status = updater_mod.check_for_update(force=True)
    if status["status"] != "ok" or not status["update_available"]:
        return layout(
            "Updates",
            "<div class='card'><p><b>Nothing to update.</b> "
            "<a href='/update'>Back</a></p></div>",
            help_key="update"), 400

    active = store.get_active_session()
    if active:
        return layout(
            "Updates",
            "<div class='card'><p><b>Can't update right now:</b> a focus "
            "session (%s) is in progress. Finish or stop it first, then "
            "come back.</p><p><a href='/update'>Back</a></p></div>"
            % escape(active.get("label") or "untitled"),
            help_key="update"), 400

    try:
        backup_mod.create_backup()
    except Exception as exc:  # noqa: BLE001 -- backup must not be skipped
        return layout(
            "Updates",
            "<div class='card'><p><b>Update stopped:</b> the safety backup "
            "failed (%s). Nothing was downloaded.</p>"
            "<p><a href='/update'>Back</a></p></div>"
            % escape(str(exc)),
            help_key="update"), 500

    asset = status["asset"]
    dest = Path(tempfile.gettempdir()) / asset["name"]
    try:
        updater_mod.download_installer(asset["url"], dest,
                                       asset["size"])
    except updater_mod.UpdateError as exc:
        return layout(
            "Updates",
            "<div class='card'><p><b>Update stopped:</b> %s Nothing was "
            "changed.</p><p><a href='/update'>Back</a></p></div>"
            % escape(str(exc)),
            help_key="update"), 500

    updater_mod.write_pending_install(dest, status["latest"])
    body = (
        "<div class='card'><h3>Updating to %s...</h3>"
        "<p>The new version is downloaded and a safety backup is made. "
        "Focus Core will now close, install the update, and reopen by "
        "itself -- about a minute.</p>"
        "<p class='note'>If it doesn't reopen by itself, start it from "
        "the desktop icon as usual.</p></div>"
        % escape(status["latest"]))
    return layout("Updating", body, help_key="update")


@app.route("/report")
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

    avg_pulse = ("%.1f" % rep["avg_pulse"]
                 if rep["avg_pulse"] is not None else "--")
    fs = rep["focus_sessions"]
    cards = (
        "<div class='card'><h3>Week %s to %s</h3>"
        "<table><tr>"
        "<td><div class='pulse' style='font-size:40px'>%.1f</div>"
        "<div class='note'>tracked hours</div></td>"
        "<td><div class='pulse' style='font-size:40px'>%s</div>"
        "<div class='note'>average Pulse</div></td>"
        "<td><div class='pulse' style='font-size:40px'>%d</div>"
        "<div class='note'>focus sessions (%.0f min, %d blocks)</div></td>"
        "</tr></table>"
        "<p><a href='/report?week=%s'>&larr; Previous week</a> &middot; "
        "<a href='/report?week=%s'>Next week &rarr;</a></p></div>"
        % (rep["week_start"], rep["week_end"], rep["total_hours"],
           avg_pulse, fs["count"], fs["focus_minutes"], fs["blocks"],
           prev_week, next_week))

    day_rows = "".join(
        "<tr><td>%s</td><td>%.2f</td><td>%s</td><td>%.2f</td></tr>"
        % (d["date"], d["total_hours"],
           ("%.1f" % d["pulse"]) if d["pulse"] is not None else "--",
           d["focus_hours"])
        for d in rep["days"])
    days_table = (
        "<div class='card'><h3>Days</h3>"
        "<table><tr><th>Date</th><th>Tracked hours</th><th>Pulse</th>"
        "<th>Focus hours</th></tr>%s</table></div>" % day_rows)

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
    cats_chart = (
        "<div class='card'><h3>Top categories</h3>%s</div>"
        % (cat_bars or "<p class='note'>No data.</p>"))

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
    pulse_chart = (
        "<div class='card'><h3>Pulse through the week</h3>"
        "<p class='note'>Daily Pulse, 0-100.</p>%s</div>" % day_bars)

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
        "<table><tr><th>Goal</th><th>Hit</th><th></th></tr>%s</table></div>"
        % ("".join(goal_rows)
           or "<tr><td colspan='3' class='note'>No goals yet.</td></tr>"))

    body = cards + days_table + cats_chart + pulse_chart + goals_table
    return layout("Weekly report %s" % rep["week_start"], body,
                  active="report")


@app.route("/coaching")
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
    heatmap = (
        "<div class='card'><h3>Your week by hour</h3>"
        "<p class='note'>Each cell is the Pulse for that hour (0-100). "
        "Green = focused, red = distracted. Last 7 days.</p>"
        "<table><tr><th>Hour</th>%s</tr>%s</table></div>"
        % (header, "".join(rows)))

    # Average-day summary row: best hours overall.
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
        "<table><tr>%s</tr></table></div>"
        % "".join("<tr><td><b>%02d</b></td>%s</tr>"
                  % (h, avg_cells[h]) for h in range(24)))

    window_items = "".join(
        "<li><b>%02d:00 - %02d:00</b> -- %.0f focus minutes"
        "%s</li>"
        % (w["start_hour"], w["end_hour"], w["focus_minutes"],
           (", Pulse %.0f" % w["avg_pulse"])
           if w["avg_pulse"] is not None else "")
        for w in windows)
    windows_html = (
        "<div class='card'><h3>Your best focus windows</h3>"
        "<p class='note'>The %d-hour blocks where you do your most focused "
        "work. Try to protect these hours.</p>"
        "<ul>%s</ul></div>"
        % (2, window_items or "<li class='note'>Not enough data yet.</li>"))

    if warnings:
        warn_items = "".join(
            "<li><b>%s</b><br><span class='note'>%s</span></li>"
            % (escape(w["message"]), escape(w["detail"]))
            for w in warnings)
        warnings_html = (
            "<div class='card'><h3>Warnings</h3><ul>%s</ul></div>"
            % warn_items)
    else:
        warnings_html = (
            "<div class='card'><h3>Warnings</h3>"
            "<p>No warnings -- looking good.</p></div>")

    body = heatmap + avg_row + windows_html + warnings_html
    return layout("Coaching", body, active="coaching")


@app.route("/shield")
def shield_page():
    from focuscore import shield as shield_mod

    daemon = shield_mod.shield_daemon_running()
    killed = shield_mod.shield_killswitch_on()
    active_pass = shield_mod.pass_active()
    hud_on = store.get_setting("hud_enabled", "1") == "1"
    rules = store.get_block_rules()
    passes = store.get_recent_passes(limit=10)
    blocks = store.get_today_blocks(limit=30)

    if killed:
        status_html = ("<p><b>Status:</b> Shield is <b>off</b> (kill "
                       "switch is on).</p>")
    elif daemon:
        status_html = ("<p><b>Status:</b> Shield is <b>running</b> -- "
                       "watching for distractions.</p>")
    else:
        status_html = ("<p><b>Status:</b> Shield is <b>not running</b>. "
                       "Turn it on below.</p>")
    if active_pass:
        try:
            until = (datetime.fromisoformat(active_pass["started_at"])
                     + timedelta(minutes=float(
                         active_pass["minutes"]))).strftime("%H:%M")
        except (ValueError, TypeError):
            until = "soon"
        status_html += ("<p class='note'>Emergency pass active until %s "
                        "(%s).</p>" % (until,
                                        escape(active_pass.get("reason")
                                               or "")))
    status_html += (
        "<form method='post' action='/shield/toggle' style='display:inline'>"
        "<button type='submit' name='on' value='%s'>%s</button></form> "
        "<form method='post' action='/shield/hud' style='display:inline'>"
        "<button type='submit' name='enabled' value='%s'>HUD: %s</button>"
        "</form>"
        % ("0" if (daemon and not killed) else "1",
           "Turn shield off" if (daemon and not killed)
           else "Turn shield on",
           "0" if hud_on else "1", "on" if hud_on else "off"))

    if rules:
        rows = []
        for r in rules:
            sched = _rule_schedule_text(r)
            rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td>"
                "<td><form method='post' action='/shield/rule/toggle' "
                "style='display:inline'>"
                "<input type='hidden' name='id' value='%d'>"
                "<button type='submit' name='enabled' value='%s'>%s</button>"
                "</form> "
                "<form method='post' action='/shield/rule/delete' "
                "style='display:inline' "
                "onsubmit=\"return confirm('Delete this rule?')\">"
                "<input type='hidden' name='id' value='%d'>"
                "<button type='submit'>Delete</button></form></td></tr>"
                % (escape(r["name"]), escape(r["rule_type"]),
                   escape(r["key"]), escape(r["action"]), sched,
                   "on" if r["enabled"] else "off", r["id"],
                   "0" if r["enabled"] else "1",
                   "Disable" if r["enabled"] else "Enable", r["id"]))
        rules_html = ("<table><tr><th>Name</th><th>Type</th><th>Key</th>"
                      "<th>Action</th><th>Schedule</th><th>On</th>"
                      "<th></th></tr>%s</table>" % "".join(rows))
    else:
        rules_html = ("<p class='note'>No rules yet. Add one below -- "
                      "for example, block <i>twitter.com</i> every "
                      "weekday 09:00-18:00.</p>")
    rules_html += (
        "<h3>Add a rule</h3>"
        "<form method='post' action='/shield/rule/add'>"
        "<p><label>Name <input type='text' name='name' required "
        "placeholder='e.g. No social at work' size='24'></label></p>"
        "<p><label>Type <select name='rule_type'>"
        "<option value='app'>App / website</option>"
        "<option value='category'>Category</option></select></label> "
        "<label>Key <input type='text' name='key' required "
        "placeholder='chrome.exe or twitter.com or social' size='24'>"
        "</label></p>"
        "<p class='note'>Key: for an app, the program name or website "
        "(e.g. chrome.exe, youtube.com). For a category, one of: social, "
        "entertainment, news, shopping, other.</p>"
        "<p><label>Action <select name='action'>"
        "<option value='soft'>Soft -- remind me</option>"
        "<option value='firm'>Firm -- remind + minimize</option>"
        "<option value='hardcore'>Hardcore -- minimize + 30 s lock"
        "</option></select></label></p>"
        "<p><label>Days <input type='text' name='days' value='all' "
        "size='14'></label> <span class='note'>all, or e.g. 0,1,2,3,4 "
        "for Mon-Fri (Mon=0, Sun=6)</span></p>"
        "<p><label>From <input type='text' name='start_time' "
        "placeholder='09:00' size='6'></label> "
        "<label>To <input type='text' name='end_time' "
        "placeholder='18:00' size='6'></label> "
        "<span class='note'>24-hour HH:MM; empty = all day</span></p>"
        "<p><button type='submit'>Add rule</button></p></form>")

    pass_html = (
        "<form method='post' action='/shield/pass'>"
        "<p><label>Minutes <input type='number' name='minutes' value='5' "
        "min='1' max='120' style='width:70px'></label> "
        "<label>Reason <input type='text' name='reason' required "
        "placeholder='e.g. waiting for a delivery call' size='30'>"
        "</label></p>"
        "<p><button type='submit'>Start emergency pass</button></p></form>")
    if passes:
        prows = []
        for p in passes:
            try:
                when = datetime.fromisoformat(
                    p["started_at"]).strftime("%H:%M")
            except (ValueError, TypeError):
                when = "?"
            prows.append("<tr><td>%s</td><td>%s min</td><td>%s</td></tr>"
                         % (when, p["minutes"],
                            escape(p.get("reason") or "")))
        pass_html += ("<table><tr><th>Started</th><th>Length</th>"
                      "<th>Reason</th></tr>%s</table>" % "".join(prows))

    if blocks:
        brows = []
        for b in blocks:
            try:
                when = datetime.fromisoformat(b["ts"]).strftime("%H:%M")
            except (ValueError, TypeError):
                when = "?"
            brows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (when, escape(b.get("app") or ""),
                   escape(b.get("title") or "")[:60],
                   escape(b.get("action_taken") or "")))
        blocks_html = ("<table><tr><th>Time</th><th>App</th><th>Window</th>"
                       "<th>What happened</th></tr>%s</table>"
                       % "".join(brows))
    else:
        blocks_html = ("<p class='note'>Nothing blocked today yet.</p>")

    body = (
        "<div class='card'><h3>Shield status</h3>%s</div>"
        "<div class='card'><h3>Always-on rules</h3>%s</div>"
        "<div class='card'><h3>Emergency pass</h3>"
        "<p class='note'>Need 5 minutes for something urgent? A pass "
        "pauses the shield -- it is always logged, so use it honestly."
        "</p>%s</div>"
        "<div class='card'><h3>Blocked today</h3>%s</div>"
        % (status_html, rules_html, pass_html, blocks_html)
    )
    return layout("Shield", body, active="shield", help_key="shield")


@app.route("/shield/rule/add", methods=["POST"])
def shield_rule_add():
    try:
        store.create_block_rule(
            request.form.get("name"), request.form.get("rule_type"),
            request.form.get("key"), request.form.get("action"),
            days=request.form.get("days") or "all",
            start_time=request.form.get("start_time") or "",
            end_time=request.form.get("end_time") or "")
    except ValueError as exc:
        return layout("Shield",
                      "<div class='card'><p><b>Could not add rule:</b> %s"
                      "</p><p><a href='/shield'>Back</a></p></div>"
                      % escape(str(exc)), active="shield",
                      help_key="shield"), 400
    return redirect("/shield")


@app.route("/shield/rule/toggle", methods=["POST"])
def shield_rule_toggle():
    try:
        rule_id = int(request.form.get("id"))
    except (TypeError, ValueError):
        return redirect("/shield")
    store.set_block_rule_enabled(
        rule_id, request.form.get("enabled") == "1")
    return redirect("/shield")


@app.route("/shield/rule/delete", methods=["POST"])
def shield_rule_delete():
    try:
        rule_id = int(request.form.get("id"))
    except (TypeError, ValueError):
        return redirect("/shield")
    store.delete_block_rule(rule_id)
    return redirect("/shield")


@app.route("/shield/pass", methods=["POST"])
def shield_pass():
    try:
        minutes = float(request.form.get("minutes") or 5)
    except (TypeError, ValueError):
        minutes = 5
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        return layout("Shield",
                      "<div class='card'><p><b>A reason is required</b> -- "
                      "that is the whole point of the pass.</p>"
                      "<p><a href='/shield'>Back</a></p></div>",
                      active="shield", help_key="shield"), 400
    try:
        store.create_pass(minutes, reason)
    except ValueError as exc:
        return layout("Shield",
                      "<div class='card'><p><b>Could not start pass:</b> "
                      "%s</p><p><a href='/shield'>Back</a></p></div>"
                      % escape(str(exc)), active="shield",
                      help_key="shield"), 400
    return redirect("/shield")


@app.route("/shield/toggle", methods=["POST"])
def shield_toggle():
    import os as _os
    from focuscore import shield as shield_mod

    if request.form.get("on") == "1":
        shield_mod.shield_on()
        if _os.name == "nt":
            shield_mod.ensure_shield_running()
    else:
        shield_mod.shield_off()
    return redirect("/shield")


@app.route("/shield/hud", methods=["POST"])
def shield_hud():
    store.set_setting("hud_enabled",
                      "1" if request.form.get("enabled") == "1" else "0")
    return redirect("/shield")


@app.route("/intelligence")
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
    trends = intel_mod.week_trends()

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
                   "balanced": "balanced -- no strong pattern yet"}
    if chrono["peak_hour"] is None:
        chrono_html = ("<p class='note'>Not enough data yet -- keep "
                       "tracking and your rhythm will appear here.</p>")
    else:
        if chrono["type"] == "morning":
            advice = ("You do %.0f%% of your focused work before noon -- "
                      "schedule your hardest work in the morning."
                      % (chrono["morning_share"] * 100))
        elif chrono["type"] == "evening":
            advice = ("You do %.0f%% of your focused work after 6pm -- "
                      "protect your evenings for deep work."
                      % (chrono["evening_share"] * 100))
        else:
            advice = ("Your focus is spread through the day -- watch the "
                      "rhythm grid below for your personal peaks.")
        chrono_html = (
            "<p>You are <b>%s</b>. Your peak hour is "
            "<b>%02d:00</b>.</p><p class='note'>%s</p>"
            % (type_labels[chrono["type"]], chrono["peak_hour"], advice))
    chrono_card = (
        "<div class='card'><h3>Your chronotype</h3>%s"
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
        "<p class='note'>Your Pulse per hour, one row per weekday. Green = "
        "focused, red = distracted. Last %d days.</p>"
        "<table>%s</table>"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>Each cell is the weighted Pulse for that "
        "weekday-hour across the last %d days. Hover a cell for the "
        "minutes and days behind it.</p></details></div>"
        % (intel_mod.CHRONOTYPE_DAYS, "".join(rhythm_rows),
           intel_mod.CHRONOTYPE_DAYS))

    # --- card 3: peak windows ---
    if peaks:
        peak_items = "".join(
            "<li><b>%s:</b> %02d:00 - %02d:00 (%.0f focus minutes)</li>"
            % (_WEEKDAY_NAMES[w], peaks[w][0]["start_hour"],
               peaks[w][0]["end_hour"], peaks[w][0]["focus_minutes"])
            for w in sorted(peaks))
    else:
        peak_items = "<li class='note'>Not enough data yet.</li>"
    peaks_card = (
        "<div class='card'><h3>Protect these hours</h3>"
        "<p class='note'>The 2-hour block where each weekday does its "
        "best focused work. Guard these like meetings.</p>"
        "<ul>%s</ul>"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>For each weekday, the 2-hour window with the "
        "most focused minutes (+1/+2) over the last %d days.</p>"
        "</details></div>" % (peak_items, intel_mod.CHRONOTYPE_DAYS))

    # --- card 4: focus depth ---
    if depth["avg_longest"] is None:
        depth_html = ("<p class='note'>No productive stretches yet -- "
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
        "<div class='card'><h3>Focus depth</h3>%s"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>A 'stretch' is unbroken productive time (scores "
        "+1/+2); gaps under 5 minutes don't break it. Time-to-first-focus "
        "is measured from your first tracked activity to your first "
        "25-minute stretch.</p></details></div>" % depth_html)

    # --- card 5: distraction anatomy ---
    if anatomy["top"]:
        max_min = anatomy["top"][0]["minutes"]
        distractor_rows = "".join(
            "<li><b>%s</b> -- %.1fh (%.0f%%)<br>"
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
    anatomy_card = (
        "<div class='card'><h3>What breaks your focus</h3>"
        "<p class='note'>Where your distracting time (-1/-2) goes, and "
        "which app you were using right before each distraction "
        "started.</p><ul>%s</ul><ul>%s</ul>"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>Distractors are apps scoring -1/-2. An 'entry "
        "point' is the app you were using right before a distraction "
        "block started.</p></details></div>"
        % (distractor_rows, entry_rows))

    # --- card 6: week trends ---
    tw, lw, dl = (trends["this_week"], trends["last_week"],
                  trends["deltas"])
    def _fmt(value, suffix=""):
        if value is None:
            return "--"
        return "%.1f%s" % (value, suffix)
    trend_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (label, _fmt(lw[key], suffix), _fmt(tw[key], suffix),
           _delta_str(dl[key]))
        for label, key, suffix in [
            ("Tracked hours", "hours", "h"),
            ("Average Pulse", "avg_pulse", ""),
            ("Focus minutes", "focus_minutes", ""),
            ("Switches / hour", "switches_per_hour", ""),
            ("Avg longest stretch (min)", "longest_stretch_avg", "")])
    trends_card = (
        "<div class='card'><h3>This week vs last week</h3>"
        "<p class='note'>%s vs %s.</p>"
        "<table><tr><th></th><th>Last week</th><th>This week</th>"
        "<th>Change</th></tr>%s</table>"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>This week runs Monday to today; last week is "
        "Monday to Sunday. Change is this week minus last week.</p>"
        "</details></div>"
        % (trends["this_label"], trends["last_label"], trend_rows))

    # --- card 7: interactive day timeline ---
    prev_day = (date.fromisoformat(sel_day) - timedelta(days=1)).isoformat()
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
    timeline_card = (
        "<div class='card'><h3>Day timeline</h3>"
        "<p class='note'>Showing <b>%s</b> -- "
        "<a href='/intelligence?day=%s'>previous day</a> | "
        "<a href='/intelligence'>today</a>. Click any hour to see the "
        "activities inside it.</p>%s"
        "<details class='how'><summary>How we compute this</summary>"
        "<p class='note'>Each hour splits into 15-minute blocks, colored "
        "by the dominant score (green = productive, red = distracting). "
        "Expanding an hour lists the apps and titles in it.</p>"
        "</details></div>"
        % (sel_day, prev_day, "".join(hour_blocks)))

    body = (chrono_card + rhythm_card + peaks_card + depth_card
            + anatomy_card + trends_card + timeline_card)
    return layout("Deep time", body, active="intelligence")


