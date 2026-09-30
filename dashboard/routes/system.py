"""System routes: backup, update, report, coaching, shield, intelligence.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import re

from flask import redirect, request

from focuscore import store
from dashboard.app import app
from dashboard.app import (
    _SCORE_CELL_COLORS,
    _WEEKDAY_NAMES,
    _cat_color,
    _parse_day,
    _pulse_cell_color,
    _rule_schedule_text,
    layout,
)


# ─────────────────────────────────────────────────────────────────────────────
# Theme setting (server-side, no JS required)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/settings/theme", methods=["POST"])
def settings_theme():
    """Toggle light / dark / system theme via a simple form POST.
    Stored in the settings table; read by layout() on every page render.
    """
    theme = request.form.get("theme", "system")
    if theme not in ("light", "dark", "system"):
        theme = "system"
    store.set_setting("ui_theme", theme)
    referrer = request.referrer or "/"
    return redirect(referrer)


@app.route("/settings/calendar", methods=["POST"])
def settings_calendar():
    """Save or clear the Google Calendar secret iCal URL.

    The URL is entered once (Home -> Rhythm section) and stored in the
    settings table; Focus Core fetches and caches the ICS locally.
    An empty value disconnects the calendar.
    """
    from focuscore import calendar_feed as cal_mod
    url = request.form.get("ical_url", "")
    try:
        cal_mod.set_ical_url(url)
    except ValueError:
        # Tell Home why the URL was rejected instead of failing silently.
        return redirect("/?cal_error=1")
    return redirect("/")


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
        last_html = "<p>Last backup: <b>%s</b> (%d %s kept).</p>" % (
            last, len(backups), "backup" if len(backups) == 1 else "backups")
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

    # Craft pass (work batch): the backup action is the hero; restore
    # second; everything else folds away. Same POST contracts.
    body = (
        "<section class='wk-backup-hero'>"
        "<div class='wk-backup-hero-state' role='status'>%s</div>"
        "<form method='post' action='/backup/now'>"
        "<button type='submit' class='wk-big'>Back up now</button></form>"
        "</section>"
        "<section class='wk-section'><h2>Restore a backup</h2>%s"
        "<p class='note'>Restoring first copies your current data to a "
        "safety file, so nothing is lost.</p></section>"
        "<section class='wk-section'><h2>Where your backups go</h2>%s</section>"
        "<p class='how-it-works'>Focus Core also backs up by itself every "
        "day when you start it (only if the last backup is older than 24 "
        "hours).</p>"
        "<section class='wk-section'><h2>Something not working?</h2>"
        "<p>Export a small diagnostics file and send it when you ask for "
        "help. It holds your Focus Core version, a short recent log, and "
        "your settings with secrets hidden. Your database and your "
        "activity data are never included.</p>"
        "<p><a class='btn' href='/backup/diagnostics'>"
        "<svg width='14' height='14' aria-hidden='true'>"
        "<use href='/static/icons.svg#icon-download'/></svg> "
        "Export diagnostics</a></p></section>"
        "<details class='wk-more'><summary>"
        "Your data &middot; Moving to a new laptop</summary>"
        "<h3>Your data</h3>"
        "<p class='note'>Everything lives on this PC in "
        "<code>focuscore.db</code>. Export: any timesheet day can be "
        "saved as CSV from the Timesheet page; a full copy is any backup "
        "from this page. Delete: to remove all your data, delete "
        "<code>focuscore.db</code> (make a backup first).</p>"
        "<h3>Moving to a new laptop</h3>"
        "<p class='note'>1. On the new laptop, install Focus Core and "
        "Google Drive, and let Drive finish syncing.<br>"
        "2. Copy the newest <code>focuscore-*.db</code> file from the "
        "\"Focus Core Backups\" folder into the Focus Core folder and "
        "rename it to <code>focuscore.db</code>. Done &mdash; all your history "
        "is back.</p></details>"
        % (last_html, table, where_html)
    )
    return layout("Backup", body, active="backup")


@app.route("/backup/diagnostics")
def backup_diagnostics():
    """Roadmap 0.4: one-click diagnostics export.

    A small zip with version/OS info, an anonymized log tail, settings
    with secrets redacted, and a data-folder listing (names + sizes).
    The database and activity data are never included -- see
    focuscore/diagnostics.py for the hard privacy rules.
    """
    from flask import Response
    from focuscore import diagnostics
    return Response(
        diagnostics.build_diagnostics_zip(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=%s"
                 % diagnostics.zip_filename()})


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
        % (escape(name), legacy_note, escape(str(safety) if safety else "none — "
               "there was no previous database")))
    return layout("Backup restored", body, active="backup")


def _update_toggle_card_html():
    """The automatic-update-checks toggle card shown on the Updates page.

    Explains in plain English what's on/off and that manual checks always
    work. Submits to /update/check-toggle like any other settings form.
    """
    enabled = store.get_setting("update_check_enabled", "1") == "1"
    state = "on" if enabled else "off"
    next_value = "0" if enabled else "1"
    action = "Turn off" if enabled else "Turn on"
    return (
        "<div class='card'><h3>Automatic update checks</h3>"
        "<p>Automatic update checks are <b>%s</b>. Focus Core asks the "
        "GitHub releases page once a day whether a newer version exists. "
        "The only thing sent is your IP address and a \"User-Agent\" "
        "label naming Focus Core.</p>"
        "<form method='post' action='/update/check-toggle' "
        "class='update-check-toggle'>"
        "<input type='hidden' name='update_check_enabled' value='%s'>"
        "<button type='submit'>%s automatic checks</button></form>"
        "<p class='note'>This only stops the automatic check. The "
        "\"Check again\" button above always works.</p></div>"
        % (state, next_value, action))


def _cached_update_status(updater_mod):
    """Last known update-check state, read from the cache -- never the
    network.

    With automatic checks off, a plain /update page load must make ZERO
    updater network calls (roadmap 1.21), even when the 24h cache has gone
    stale. So instead of ``check_for_update()`` (which refreshes a stale
    cache by asking GitHub), read the cache directly and translate it into
    the shape ``update_page`` already renders. The explicit "Check again"
    link (``?refresh=1``) remains the manual way to ask.
    """
    info = updater_mod.get_update_info()
    if info is None:
        return {"status": "dev-copy"}
    cached = updater_mod.read_cached_check()
    if cached and isinstance(cached, dict) \
            and cached.get("current") == info["version"]:
        if cached.get("status") in ("ok", "error"):
            return cached
    return {"status": "not-checked", "current": info["version"]}


@app.route("/update")
def update_page():
    from focuscore import updater as updater_mod

    refresh = request.args.get("refresh") == "1"
    if refresh:
        # Explicit "Check again" click: a deliberate action, always
        # allowed -- the toggle governs automatic checks, not this.
        status = updater_mod.check_for_update(force=True)
    elif store.get_setting("update_check_enabled", "1") == "1":
        status = updater_mod.check_for_update()
    else:
        # Automatic checks off: render the last known state (or "not
        # checked yet") without touching the network (roadmap 1.21).
        status = _cached_update_status(updater_mod)

    if status["status"] == "dev-copy":
        body = (
            "<div class='card'><h3>Updates</h3>"
            "<p>This is a developer copy of Focus Core, so it doesn't "
            "update itself. Pull the newest code (or grab the newest zip) "
            "the way you usually do.</p>"
            "<p class='note'>One-click updates are for installed copies "
            "only.</p></div>")
        return layout("Updates", body, help_key="update")

    if status["status"] == "not-checked":
        body = (
            "<div class='card'><h3>Updates</h3>"
            "<p>You're on <b>%s</b>.</p>"
            "<p><b>Not checked yet.</b> Automatic checks are off, so "
            "Focus Core hasn't asked about new versions. Use \"Check "
            "again\" whenever you want to look.</p>"
            "<p><a class='btn' href='/update?refresh=1'>Check again</a></p>"
            "</div>"
            % escape(status["current"]))
        return layout("Updates", body + _update_toggle_card_html(),
                      help_key="update")

    if status["status"] == "error":
        body = (
            "<div class='card'><h3>Updates</h3>"
            "<p><b>Couldn't check for updates:</b> %s</p>"
            "<p class='note'>This usually means no internet, or the releases "
            "page isn't public. Nothing changed — you're still on %s.</p>"
            "<p><a class='btn' href='/update?refresh=1'>Check again</a></p>"
            "</div>"
            % (escape(status["error"]), escape(status["current"])))
        return layout("Updates", body + _update_toggle_card_html(),
                      help_key="update")

    head = ("<div class='card'><h3>Updates</h3>"
            "<p>You're on <b>%s</b>.</p>"
            % escape(status["current"]))
    if not status["update_available"]:
        auto_on = store.get_setting("update_check_enabled", "1") == "1"
        check_hint = (" Focus Core checks once a day by itself." if auto_on
                      else " Automatic checks are off, so this page only "
                           "updates when you check.")
        body = (head +
                "<p><b>You're up to date.</b>" + check_hint + "</p>"
                "<p><a class='btn' href='/update?refresh=1'>Check again</a>"
                "</p></div>")
        return layout("Updates", body + _update_toggle_card_html(),
                      help_key="update")

    body = (
        head +
        "<p><b>Version %s is available.</b></p>"
        "<form method='post' action='/update/start' onsubmit=\"return "
        "confirm('Update to %s now? A safety backup is made first, then "
        "Focus Core closes, updates, and reopens by itself.');\">"
        "<button type='submit'>Update to %s now</button></form>"
        "<p class='note'>Your data is never touched by the update — and a "
        "safety backup is made first anyway. The download is about 25 MB."
        "</p></div>"
        % (escape(status["latest"]), escape(status["latest"]),
           escape(status["latest"])))
    return layout("Updates", body + _update_toggle_card_html(),
                  help_key="update")


@app.route("/update/check-toggle", methods=["POST"])
def update_check_toggle():
    """Turn the automatic daily update check on or off.

    Same shape as /settings/theme: a plain form POST + redirect. The
    setting governs only the automatic background check -- the manual
    "Check again" button on the Updates page always works. Garbage input
    fails safe to off (fewer internet calls, never more) -- a missing
    field resolves to off too, never on.
    """
    enabled = request.form.get("update_check_enabled", "0") == "1"
    store.set_setting("update_check_enabled", "1" if enabled else "0")
    referrer = request.referrer or "/update"
    return redirect(referrer)


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
                                       asset["size"],
                                       asset.get("checksums_url"))
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
        "itself — about a minute.</p>"
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
    cats_chart = (
        "<div class='card'><h3>Top categories</h3>%s%s</div>"
        % (cats_caption, cat_bars or "<p class='note'>No data.</p>"))

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
    pulse_chart = (
        "<div class='card'><h3>Pulse through the week</h3>"
        "<p class='note'>Daily Pulse, 0-100.</p>%s%s</div>"
        % (pulse_caption, day_bars))

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

    # The on/off state is the hero: big, unmistakable, with the toggle
    # as the primary action right inside it.
    on = bool(daemon and not killed)
    if killed:
        state_word, state_sub = "off", "The kill switch is on."
    elif daemon:
        state_word, state_sub = "on", "Watching for distractions."
    else:
        state_word, state_sub = "off", "Turn it on to block distractions."
    n_blocks, n_rules = len(blocks), len(rules)
    blocks_word = "distraction" if n_blocks == 1 else "distractions"
    rules_word = "rule" if n_rules == 1 else "rules"
    hero_html = (
        "<div class='page-hero gd-hero gd-hero--%s'>"
        "<div class='page-hero-text'>"
        "<h1 class='page-title gd-hero-title'>Shield is %s</h1>"
        "<p class='page-sub gd-hero-sub'>%s"
        "<span class='page-sub-dot'>&middot;</span>"
        "<span><b>%d</b> %s blocked today</span>"
        "<span class='page-sub-dot'>&middot;</span>"
        "<span><b>%d</b> %s</span>"
        "</p></div>"
        "<div class='gd-hero-actions'>"
        "<form method='post' action='/shield/toggle' style='display:inline'>"
        "<button type='submit' name='on' value='%s' class='gd-hero-btn'>"
        "%s</button></form> "
        "<form method='post' action='/shield/hud' style='display:inline'>"
        "<button type='submit' name='enabled' value='%s' "
        "class='gd-hero-btn gd-hero-btn--quiet'>HUD: %s</button></form>"
        "</div>"
        "</div>"
        % (state_word, state_word, state_sub, n_blocks, blocks_word,
           n_rules, rules_word,
           "0" if on else "1", "Turn shield off" if on else "Turn shield on",
           "0" if hud_on else "1", "on" if hud_on else "off"))

    pass_note = ""
    if active_pass:
        try:
            until = (datetime.fromisoformat(active_pass["started_at"])
                     + timedelta(minutes=float(
                         active_pass["minutes"]))).strftime("%H:%M")
        except (ValueError, TypeError):
            until = "soon"
        pass_note = (
            "<p class='note gd-pass-note'>Emergency pass active until %s "
            "(%s).</p>" % (until,
                            escape(active_pass.get("reason") or "")))

    action_words = {"soft": "remind me", "firm": "remind + minimize",
                    "hardcore": "minimize + 30s lock"}
    rule_rows = []
    for r in rules:
        rule_rows.append(
            "<div class='gd-strip'>"
            "<div class='gd-strip-main'><b>%s</b>"
            "<span class='gd-strip-sub'>%s &middot; %s &middot; %s</span>"
            "</div>"
            "<span class='gd-state gd-state--%s'>%s</span>"
            "<form class='inline' method='post' "
            "action='/shield/rule/toggle'>"
            "<input type='hidden' name='id' value='%d'>"
            "<button type='submit' name='enabled' value='%s' "
            "class='gd-strip-btn'>%s</button></form>"
            "<form class='inline' method='post' "
            "action='/shield/rule/delete' "
            "onsubmit=\"return confirm('Delete this rule?')\">"
            "<input type='hidden' name='id' value='%d'>"
            "<button type='submit' class='gd-strip-btn'>Delete</button></form>"
            "</div>"
            % (escape(r["name"]), escape(r["key"]), _rule_schedule_text(r),
               action_words.get(r["action"], escape(r["action"] or "")),
               "on" if r["enabled"] else "off",
               "On" if r["enabled"] else "Off",
               r["id"], "0" if r["enabled"] else "1",
               "Pause" if r["enabled"] else "Resume", r["id"]))
    rules_html = "".join(rule_rows) or \
        "<p class='note'>No rules yet &mdash; add one below and Shield " \
        "will watch for it.</p>"

    # One-tap starter rules: the empty state as onboarding. Only shown
    # before the first rule exists; each chip posts the same fields as
    # the form below, so /shield/rule/add needs no changes.
    starters = []
    if not rule_rows:
        starters = [
            ("No social at work", "No social at work", "category",
             "social", "firm", "0,1,2,3,4", "09:00", "18:00"),
            ("No video rabbit holes", "No video rabbit holes", "app",
             "youtube.com", "soft", "all", "", ""),
            ("Quiet evenings", "Quiet evenings", "category",
             "entertainment", "soft", "all", "20:00", "23:00"),
        ]
    starters_html = "".join(
        "<form class='inline' method='post' action='/shield/rule/add'>"
        "<input type='hidden' name='name' value='%s'>"
        "<input type='hidden' name='rule_type' value='%s'>"
        "<input type='hidden' name='key' value='%s'>"
        "<input type='hidden' name='action' value='%s'>"
        "<input type='hidden' name='days' value='%s'>"
        "<input type='hidden' name='start_time' value='%s'>"
        "<input type='hidden' name='end_time' value='%s'>"
        "<button type='submit' class='chip'>%s</button></form>"
        % (escape(name), rtype, escape(key), action, escape(days),
           escape(start), escape(end), escape(label))
        for label, name, rtype, key, action, days, start, end in starters)
    starters_block = (
        "<p class='goal-starters-label'>Or start with one of these:</p>"
        "<div class='goal-starters'>%s</div>" % starters_html
    ) if starters_html else ""

    rule_form = (
        "<div class='card gd-create'><h3>Add a rule</h3>%s"
        "<form method='post' action='/shield/rule/add' "
        "class='sentence-form'>"
        "<p class='sentence'>Block "
        "<input type='text' name='key' required "
        "placeholder='youtube.com or social' size='20' "
        "aria-label='App, site or category'> "
        "<select name='rule_type' aria-label='Rule type'>"
        "<option value='app'>an app / website</option>"
        "<option value='category'>a category</option></select> "
        "<select name='days' aria-label='Days'>"
        "<option value='all'>every day</option>"
        "<option value='0,1,2,3,4'>weekdays</option>"
        "<option value='5,6'>weekends</option></select> "
        "<span class='nowrap'>from</span> "
        "<input type='text' name='start_time' placeholder='09:00' size='5' "
        "aria-label='From'> "
        "<span class='nowrap'>to</span> "
        "<input type='text' name='end_time' placeholder='18:00' size='5' "
        "aria-label='To'> "
        "<select name='action' aria-label='Action'>"
        "<option value='soft'>just remind me</option>"
        "<option value='firm'>remind + minimize</option>"
        "<option value='hardcore'>minimize + 30s lock</option></select>"
        "<span class='nowrap'>.</span></p>"
        "<p><label class='sentence-name'>Name it "
        "<input type='text' name='name' required "
        "placeholder='e.g. No social at work' size='24'></label></p>"
        "<p class='note'>Category keys: social, entertainment, news, "
        "shopping, other. Leave the times empty for all day.</p>"
        "<p><button type='submit'>Add rule</button></p></form></div>"
        % starters_block)

    pass_html = (
        "<div class='card'><h3>Emergency pass</h3>"
        "<p class='note'>Need 5 minutes for something urgent? A pass "
        "pauses the shield — it is always logged, so use it honestly."
        "</p>"
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
            prows.append(
                "<div class='gd-strip gd-strip--quiet'>"
                "<div class='gd-strip-main'><b>%s</b>"
                "<span class='gd-strip-sub'>%s min &middot; %s</span></div>"
                "</div>" % (when, p["minutes"],
                             escape(p.get("reason") or "")))
        pass_html += "".join(prows)
    pass_html += "</div>"

    if blocks:
        brows = []
        for b in blocks:
            try:
                when = datetime.fromisoformat(b["ts"]).strftime("%H:%M")
            except (ValueError, TypeError):
                when = "?"
            brows.append(
                "<div class='gd-strip gd-strip--quiet'>"
                "<div class='gd-strip-main'><b>%s</b>"
                "<span class='gd-strip-sub'>%s &middot; %s &middot; "
                "%s</span></div></div>"
                % (when, escape(b.get("app") or ""),
                   escape(b.get("title") or "")[:60],
                   escape(b.get("action_taken") or "")))
        blocks_html = "".join(brows)
    else:
        blocks_html = ("<p class='note'>Nothing blocked today yet.</p>")

    body = (
        "%s"
        "<section class='gd-list'><h3>Rules</h3>%s</section>"
        "%s"
        "%s"
        "<section class='gd-list'><h3>Blocked today</h3>%s</section>"
        "<p class='how-it-works'>Shield watches the apps and sites you use "
        "and steps in when a rule matches. A pass pauses it &mdash; every "
        "pass is logged, so use it honestly.</p>"
        % (pass_note, rules_html, rule_form, pass_html, blocks_html)
    )
    return layout("Shield", body, active="shield", help_key="shield",
                  hero=hero_html)


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
                      "<div class='card'><p><b>A reason is required</b> — "
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
    from focuscore import intelligence as intel_mod
    from focuscore import chronotype as chrono_mod

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
            "<p style='font-size:42px;font-weight:bold;margin:4px 0'>%d"
            "<span style='font-size:16px;color:var(--ink-muted)'>/100</span></p>"
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


@app.route("/intelligence/report")
def intelligence_report():
    """Standalone printable Deep Work Intelligence Report (Phase 12).

    Strict CSP, zero JavaScript, print CSS -- Phase 9/10 standard.
    """
    from focuscore import intelligence as intel_mod
    from focuscore import chronotype as chrono_mod

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
        flow_big = ("<p style='font-size:36px;font-weight:bold'>%d/100 · "
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
        ".caption{color:#57606a;font-style:italic;font-size:13px}"
        ".empty{border:1px dashed #d0d7de;border-radius:8px;padding:24px;"
        "margin:16px 0;text-align:center}"
        ".footnote{color:#57606a;font-size:13px;margin-top:24px}"
        "h1{font-size:26px}h3{margin-top:0}"
        ".note{color:#57606a;font-size:13px}"
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
