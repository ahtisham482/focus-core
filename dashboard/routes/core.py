"""Core routes: home, onboarding, help, activities, goals, alerts.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
from datetime import date
from html import escape

from flask import abort, redirect, request

from focuscore import store
from focuscore.scoring import UI_LABELS
from focuscore.taxonomy import host_of
from dashboard.app import app
import dashboard.app as _app_mod  # for monkeypatch-compatible access
from dashboard.app import (
    WELCOME_STEPS,
    _alert_target_text,
    _goal_manage_buttons,
    _goal_progress_html,
    _parse_day,
    day_page,
    home_page,
    is_onboarded,
    layout,
    mark_onboarded,
)

@app.route("/")
def index():
    if not is_onboarded():
        return redirect("/welcome")
    return home_page()


@app.route("/collect")
def collect():
    """Re-run today's collection, then show the day page (which explains
    clearly when ActivityWatch is not reachable)."""
    today = date.today().isoformat()
    return redirect("/day/" + today)


@app.route("/welcome")
def welcome():
    try:
        step = int(request.args.get("step", "1"))
    except (TypeError, ValueError):
        step = 1
    step = max(1, min(len(WELCOME_STEPS), step))
    info = WELCOME_STEPS[step - 1]

    dots = "".join(
        "<div class='step%s'>%d</div>" % (" now" if i == step else "", i)
        for i in range(1, len(WELCOME_STEPS) + 1))

    if step < len(WELCOME_STEPS):
        action = ("<p><a class='btn' href='/welcome?step=%d'>Next</a></p>"
                  % (step + 1))
    else:
        action = (
            "<form method='post' action='/welcome/finish' class='inline'>"
            "<input type='hidden' name='next' value='/activities'>"
            "<button type='submit'>Review my activities</button></form> "
            "<form method='post' action='/welcome/finish' class='inline'>"
            "<input type='hidden' name='next' value='/'>"
            "<button type='submit' class='secondary'>Skip for now</button>"
            "</form>")

    check_html = ("<p class='note'><b>Check:</b> %s</p>" % escape(info["check"])
                  if info["check"] else "")
    extra_html = ""
    if step == 2:
        # The ActivityWatch step adapts to reality: a green confirmation
        # when it's running, a pointer to the setup page when it's not.
        from focuscore import activitywatch as aw_mod
        from focuscore import onboarding as ob_mod
        aw_status = aw_mod.server_status()
        aw_state = aw_mod.detection_state(status=aw_status)
        extra_html = ob_mod.welcome_step_html(
            aw_state, version=aw_status.get("version"))
    body = (
        "<div class='card'><div class='steps'>%s</div>"
        "<div class='big-emoji'>%s</div><h2>%s</h2><p>%s</p>%s%s%s</div>"
        % (dots, info["emoji"], escape(info["title"]),
           escape(info["text"]), check_html, extra_html, action))
    return layout("Welcome", body, refresh=3600, help_key="welcome")


@app.route("/setup/activitywatch")
def setup_activitywatch():
    """Stranger onboarding: detect ActivityWatch and walk the user through
    installing/starting it. The page re-probes on every load, so the
    'Check again' button is just a link back here."""
    from focuscore import activitywatch as aw_mod
    from focuscore import onboarding as ob_mod
    aw_status = aw_mod.server_status()
    aw_state = aw_mod.detection_state(status=aw_status)
    body = ob_mod.setup_page_html(aw_state, version=aw_status.get("version"))
    return layout("Set up ActivityWatch", body, active="home",
                  help_key="setup")


@app.route("/help")
def help_index():
    """Index of all in-app help articles."""
    from focuscore import help as help_mod
    return layout("Help", help_mod.index_html(), active="help")


@app.route("/help/<key>")
def help_article(key):
    """One help article. Unknown keys show the index with a short note."""
    from focuscore import help as help_mod
    article = help_mod.get_article(key)
    if article is None:
        body = ("<div class='card'><p>There's no help article for "
                "'%s' yet -- here is everything we have:</p></div>"
                % escape(key)) + help_mod.index_html()
        return layout("Help", body, active="help")
    return layout(article["title"] + " - Help", help_mod.article_html(key),
                  active="help")


@app.route("/welcome/finish", methods=["POST"])
def welcome_finish():
    mark_onboarded()
    dest = request.form.get("next") or "/"
    if dest not in ("/", "/activities"):
        dest = "/"
    return redirect(dest)


@app.route("/welcome/restart")
def welcome_restart():
    try:
        # Via module attribute so tests can monkeypatch
        # dashboard.app.ONBOARDED_FLAG.
        _app_mod.ONBOARDED_FLAG.unlink()
    except OSError:
        pass
    return redirect("/welcome")


@app.route("/day/<day>")
def day_view(day):
    if not _parse_day(day):
        abort(404)
    return day_page(day)


@app.route("/activities")
def activities():
    day = _parse_day(request.args.get("day")) or date.today().isoformat()
    rows = store.get_day_activities(day)
    body_rows = []
    for row in rows:
        options = "".join(
            '<option value="%d"%s>%+d %s</option>'
            % (level, " selected" if row["score"] == level else "",
               level, UI_LABELS[level])
            for level in (2, 1, 0, -1, -2)
        )
        host = host_of(row["url"]) if row["url"] else ""
        body_rows.append(
            "<tr><td>%s</td>"
            "<td class='title-cell' title='%s'>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%.1fm</td>"
            "<td><form method='post' action='/override' style='margin:0'>"
            "<input type='hidden' name='day' value='%s'>"
            "<input type='hidden' name='match_key' value='%s'>"
            "<select name='score'>%s</select> "
            "<button type='submit'>Set</button></form></td></tr>"
            % (escape((row["ts"] or "")[11:16]),
               escape(row["title"] or ""), escape((row["title"] or "")[:60]),
               escape(row["app"] or ""), escape(host or "-"),
               escape(row["category"] or ""),
               (row["duration"] or 0) / 60.0,
               day, escape(row["match_key"] or ""), options)
        )
    body = (
        "<div class='card'><h3>Activities -- %s</h3>"
        "<p class='note'>Setting a score here overrides the category default "
        "for this activity, today and on future days.</p>"
        "<table><tr><th>Time</th><th>Title</th><th>App</th><th>Site</th>"
        "<th>Category</th><th>Min</th><th>Score override</th></tr>%s</table></div>"
        % (day, "".join(body_rows)
           or "<tr><td colspan='7' class='note'>"
              "No activities stored for this day.</td></tr>")
    )
    return layout("Activities " + day, body, day, active="review")


@app.route("/override", methods=["POST"])
def override():
    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    match_key = (request.form.get("match_key") or "").strip()
    try:
        score = int(request.form.get("score"))
    except (TypeError, ValueError):
        score = None
    if match_key and score in (2, 1, 0, -1, -2):
        store.set_override(match_key, score)
        store.apply_override_to_day(day, match_key, score)
    return redirect("/activities?day=" + day)


@app.route("/goals")
def goals_page():
    from focuscore import goals as goals_mod

    day = date.today().isoformat()
    summary = store.get_day_summary(day)
    rows = []
    for goal in store.list_goals():
        ev = goals_mod.evaluate_goal(goal, summary)
        rows.append(
            "<div class='goal-row'>%s<div class='gmeta'>%s</div></div>"
            % (_goal_progress_html(ev), _goal_manage_buttons(goal)))
    goals_html = "".join(rows) or \
        "<p class='note'>No goals yet -- add your first one below.</p>"

    categories = sorted(store.get_categories().keys())
    target_options = (
        '<option value="pulse">Productivity Pulse (0-100)</option>' +
        "".join('<option value="category:%s">%s</option>'
                % (escape(c), escape(c)) for c in categories
                if c != "Uncategorized"))

    body = (
        "<div class='card'><h3>Today's progress</h3>%s"
        "<p class='note'>Progress is live: this page refreshes every 5 minutes "
        "and re-reads today's tracked data on every load.</p></div>"
        "<div class='card'><h3>Add a goal</h3>"
        "<form method='post' action='/goals/add'>"
        "<p><label>Name <input type='text' name='name' required "
        "placeholder='e.g. Deep work' size='24'></label></p>"
        "<p><label><input type='radio' name='direction' value='more_than' "
        "checked> More than</label> "
        "<label><input type='radio' name='direction' value='less_than'> "
        "Less than</label></p>"
        "<p><label>Target <select name='target'>%s</select></label> "
        "<label>Amount <input type='number' name='threshold' min='0' "
        "step='0.5' required style='width:90px'></label> "
        "<span class='note'>minutes for a category, 0-100 for Pulse</span></p>"
        "<p><label><input type='checkbox' name='pinned' value='1'> "
        "Pin to the top of the Today page</label></p>"
        "<p><button type='submit'>Add goal</button></p>"
        "</form></div>"
        % (goals_html, target_options)
    )
    return layout("Goals", body, active="goals")


@app.route("/goals/add", methods=["POST"])
def goals_add():
    from focuscore import goals as goals_mod

    name = (request.form.get("name") or "").strip()
    direction = request.form.get("direction") or "more_than"
    target = request.form.get("target") or "pulse"
    try:
        threshold = float(request.form.get("threshold"))
    except (TypeError, ValueError):
        threshold = None
    pinned = request.form.get("pinned") == "1"

    error = None
    if not name:
        error = "Please give the goal a name."
    elif threshold is None or threshold < 0:
        error = "Please enter a valid amount (>= 0)."
    else:
        try:
            if target == "pulse":
                goals_mod.add_goal(name, direction, "pulse",
                                   threshold_pulse=threshold, pinned=pinned)
            else:
                cat = target.split("category:", 1)[1] if \
                    target.startswith("category:") else target
                goals_mod.add_goal(name, direction, "category",
                                   target_name=cat,
                                   threshold_minutes=threshold, pinned=pinned)
        except ValueError as exc:
            error = str(exc)
    if error:
        return layout("Goals",
                      "<div class='card'><p><b>Could not add goal:</b> %s</p>"
                      "<p><a href='/goals'>Back to goals</a></p></div>"
                      % escape(error), help_key="goals"), 400
    return redirect("/goals")


@app.route("/goals/delete", methods=["POST"])
def goals_delete():
    try:
        store.delete_goal(int(request.form.get("id")))
    except (TypeError, ValueError):
        pass
    return redirect("/goals")


@app.route("/goals/pin", methods=["POST"])
def goals_pin():
    try:
        store.set_pinned(int(request.form.get("id")),
                         request.form.get("pinned") == "1")
    except (TypeError, ValueError):
        pass
    return redirect("/goals")


@app.route("/alerts")
def alerts_page():
    alert_rows = []
    for alert in store.list_alerts():
        alert_rows.append(
            "<tr><td><b>%s</b><br><span class='note'>%s</span></td>"
            "<td>%s</td><td>%.0f min</td><td>%.0f min</td>"
            "<td><form class='inline' method='post' action='/alerts/toggle'>"
            "<input type='hidden' name='id' value='%d'>"
            "<input type='hidden' name='enabled' value='%d'>"
            "<button type='submit'>%s</button></form></td>"
            "<td><form class='inline' method='post' action='/alerts/delete' "
            "onsubmit=\"return confirm('Delete this alert?');\">"
            "<input type='hidden' name='id' value='%d'>"
            "<button type='submit'>Delete</button></form></td></tr>"
            % (escape(alert["name"]), escape(alert["message"] or "-"),
               _alert_target_text(alert), alert["threshold_minutes"],
               alert["cooldown_minutes"], alert["id"],
               0 if alert["enabled"] else 1,
               "Disable" if alert["enabled"] else "Enable", alert["id"]))
    alerts_table = (
        "<table><tr><th>Alert</th><th>Watches</th><th>Threshold</th>"
        "<th>Cooldown</th><th>Status</th><th></th></tr>%s</table>"
        % ("".join(alert_rows)
           or "<tr><td colspan='6' class='note'>No alerts yet.</td></tr>"))

    firing_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%.1f min</td></tr>"
        % (escape(f["name"] or "deleted alert"), escape(f["fired_at"][:16]),
           f["current_minutes"] or 0)
        for f in store.recent_firings())
    firings_table = (
        "<table><tr><th>Alert</th><th>Fired at</th><th>Time on target</th></tr>"
        "%s</table>"
        % (firing_rows
           or "<tr><td colspan='3' class='note'>Nothing fired yet.</td></tr>"))

    categories = sorted(store.get_categories().keys())
    cat_options = "".join(
        '<option value="category:%s">%s</option>' % (escape(c), escape(c))
        for c in categories if c != "Uncategorized")

    body = (
        "<div class='card'><h3>Alerts</h3>%s"
        "<p class='note'>Alerts are checked against today's data. For "
        "real-time desktop pop-ups, run <code>watch-alerts.bat</code> "
        "(double-click it on Windows) -- it checks every 5 minutes.</p></div>"
        "<div class='card'><h3>Add an alert</h3>"
        "<form method='post' action='/alerts/add'>"
        "<p><label>Name <input type='text' name='name' required "
        "placeholder='e.g. Too much social media' size='28'></label></p>"
        "<p><label>Watch <select name='target_type'>"
        "<option value='category'>a category</option>"
        "<option value='activity'>one activity</option>"
        "</select></label> "
        "<label>Which <input type='text' name='target_name' list='catlist' "
        "required placeholder='Entertainment or app:chrome' size='28'></label>"
        "<datalist id='catlist'>%s</datalist><br>"
        "<span class='note'>Category: pick from the list. Activity: use the "
        "key from the Activities page, e.g. <code>app:chrome</code> or "
        "<code>domain:youtube.com</code>.</span></p>"
        "<p><label>Warn me at <input type='number' name='threshold_minutes' "
        "min='1' step='1' required style='width:80px'> minutes</label> "
        "<label>Don't repeat for <input type='number' name='cooldown_minutes' "
        "min='0' step='5' value='60' style='width:80px'> minutes</label></p>"
        "<p><label>Message <input type='text' name='message' size='40' "
        "placeholder='e.g. Time to get back to work!'></label></p>"
        "<p><button type='submit'>Add alert</button></p>"
        "</form></div>"
        "<div class='card'><h3>Recent firings</h3>%s</div>"
        % (alerts_table, cat_options, firings_table)
    )
    return layout("Alerts", body, active="alerts")


@app.route("/alerts/add", methods=["POST"])
def alerts_add():
    from focuscore import alerts as alerts_mod

    name = request.form.get("name") or ""
    target_type = request.form.get("target_type") or "category"
    target_name = request.form.get("target_name") or ""
    message = request.form.get("message") or ""
    try:
        threshold = float(request.form.get("threshold_minutes"))
        cooldown = float(request.form.get("cooldown_minutes") or 60)
    except (TypeError, ValueError):
        threshold, cooldown = None, 60

    error = None
    if threshold is None:
        error = "Please enter a valid threshold in minutes."
    else:
        try:
            alerts_mod.add_alert(name, target_type, target_name,
                                 threshold, message, cooldown)
        except ValueError as exc:
            error = str(exc)
    if error:
        return layout("Alerts",
                      "<div class='card'><p><b>Could not add alert:</b> %s</p>"
                      "<p><a href='/alerts'>Back to alerts</a></p></div>"
                      % escape(error), help_key="alerts"), 400
    return redirect("/alerts")


@app.route("/alerts/delete", methods=["POST"])
def alerts_delete():
    try:
        store.delete_alert(int(request.form.get("id")))
    except (TypeError, ValueError):
        pass
    return redirect("/alerts")


@app.route("/alerts/toggle", methods=["POST"])
def alerts_toggle():
    try:
        store.set_alert_enabled(int(request.form.get("id")),
                                request.form.get("enabled") == "1")
    except (TypeError, ValueError):
        pass
    return redirect("/alerts")


