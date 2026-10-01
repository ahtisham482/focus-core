"""Core routes: home, onboarding, help, activities, goals, alerts.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
import logging
from datetime import date
from html import escape

from flask import Blueprint, abort, redirect, request

from focuscore import store
from focuscore.scoring import UI_LABELS
from focuscore.taxonomy import host_of

import dashboard.app as _app_mod  # for monkeypatch-compatible access
from dashboard.app import (
    WELCOME_STEPS,
    _alert_target_text,
    _goal_manage_buttons,
    _goal_progress_html,
    _living_tabs,
    _parse_day,
    day_page,
    home_page,
    is_onboarded,
    layout,
    mark_onboarded,
)

bp = Blueprint("core", __name__)

logger = logging.getLogger(__name__)


@bp.route("/")
def index():
    if not is_onboarded():
        return redirect("/welcome")
    return home_page()


@bp.route("/collect")
def collect():
    """Re-run today's collection, then show the day page (which explains
    clearly when ActivityWatch is not reachable)."""
    today = date.today().isoformat()
    return redirect("/day/" + today)


@bp.route("/welcome")
def welcome():
    try:
        step = int(request.args.get("step", "1"))
    except (TypeError, ValueError):
        step = 1
    step = max(1, min(len(WELCOME_STEPS), step))
    info = WELCOME_STEPS[step - 1]

    dots = "".join(
        "<span class='hm-dot%s' aria-hidden='true'>%d</span>"
        % (" on" if i == step else "", i)
        for i in range(1, len(WELCOME_STEPS) + 1))

    if step < len(WELCOME_STEPS):
        action = ("<p class='hm-welcome-actions'>"
                  "<a class='hm-btn' href='/welcome?step=%d'>Next</a></p>"
                  % (step + 1))
    else:
        action = (
            "<div class='hm-welcome-actions'>"
            "<form method='post' action='/welcome/finish' class='inline'>"
            "<input type='hidden' name='next' value='/activities'>"
            "<button type='submit' class='hm-btn'>Review my activities</button>"
            "</form> "
            "<form method='post' action='/welcome/finish' class='inline'>"
            "<input type='hidden' name='next' value='/'>"
            "<button type='submit' class='hm-btn-ghost'>Skip for now</button>"
            "</form></div>")

    check_html = ("<p class='hm-check'><b>Check:</b> %s</p>" % escape(info["check"])
                  if info["check"] else "")
    todo_html = ("<p class='hm-todo'>%s</p>" % escape(info["todo"])
                 if info.get("todo") else "")
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
    icon_html = (
        "<svg class='hm-wicon' width='44' height='44' aria-hidden='true'>"
        "<use href='/static/icons.svg#icon-%s'/></svg>" % info["icon"])
    body = (
        "<div class='hm-wrap'><div class='hm-welcome'>"
        "<div class='hm-dots' aria-label='Step %d of %d'>%s</div>"
        "%s<h2 class='hm-wtitle'>%s</h2><p class='hm-wtext'>%s</p>"
        "%s%s%s%s"
        "</div></div>"
        % (step, len(WELCOME_STEPS), dots, icon_html, escape(info["title"]),
           escape(info["text"]), check_html, todo_html, extra_html, action))
    return layout("Welcome", body, refresh=3600, help_key="welcome", hero="")


@bp.route("/setup/activitywatch")
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


@bp.route("/help")
def help_index():
    """Index of all in-app help articles."""
    from focuscore import help as help_mod
    return layout("Help", help_mod.index_html(), active="help")


@bp.route("/help/<key>")
def help_article(key):
    """One help article. Unknown keys show the index with a short note."""
    from focuscore import help as help_mod
    article = help_mod.get_article(key)
    if article is None:
        body = ("<div class='card'><p>There's no help article for "
                "'%s' yet — here is everything we have:</p></div>"
                % escape(key)) + help_mod.index_html()
        return layout("Help", body, active="help")
    return layout(article["title"] + " - Help", help_mod.article_html(key),
                  active="help")


@bp.route("/welcome/finish", methods=["POST"])
def welcome_finish():
    mark_onboarded()
    dest = request.form.get("next") or "/"
    if dest not in ("/", "/activities"):
        dest = "/"
    return redirect(dest)


@bp.route("/welcome/restart", methods=["POST"])
def welcome_restart():
    try:
        # Via module attribute so tests can monkeypatch
        # dashboard.app.ONBOARDED_FLAG.
        _app_mod.ONBOARDED_FLAG.unlink()
    except OSError as exc:
        logger.warning("could not remove the onboarding flag: %s", exc)
    return redirect("/welcome")


@bp.route("/day/<day>")
def day_view(day):
    if not _parse_day(day):
        abort(404)
    return day_page(day)


@bp.route("/activities")
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
    # --- today's story: one human sentence from the raw rows ---
    story_html = ""
    if rows:
        mins = {2: 0.0, 1: 0.0, 0: 0.0, -1: 0.0, -2: 0.0}
        for row in rows:
            mins[row.get("score") or 0] += (row.get("duration") or 0) / 60.0

        def _h(m):
            return ("%.1f hours" % (m / 60.0)) if m >= 90 \
                else ("%d minutes" % round(m))

        parts = []
        if mins[2]:
            parts.append("%s of focused work" % _h(mins[2]))
        if mins[1]:
            parts.append("%s of other work" % _h(mins[1]))
        if mins[-1] + mins[-2]:
            parts.append("%s of personal and distracting time"
                         % _h(mins[-1] + mins[-2]))
        if mins[0] and not parts:
            parts.append("%s of neutral time" % _h(mins[0]))
        total = sum(mins.values())
        story_html = (
            "<div class='wk-story' role='status'>Today you tracked "
            "<b>%s</b>%s.</div>"
            % (_h(total), (": " + ", ".join(parts)) if parts else ""))
    else:
        story_html = (
            "<p class='wk-empty'>Nothing tracked this day yet. Once "
            "ActivityWatch records your day, every app and site shows up "
            "here with a score you can correct.</p>")

    body = (
        "%s"
        "<form class='wk-daybar' method='get' action='/activities'>"
        "<label>Day <input type='date' name='day' value='%s'></label> "
        "<button type='submit' class='secondary'>Show</button></form>"
        "<section class='wk-section'><h2>What you did</h2>"
        "<div class='wk-strip'><table>"
        "<tr><th>Time</th><th>Title</th><th>App</th><th>Site</th>"
        "<th>Category</th><th>Min</th><th>Score override</th></tr>%s</table>"
        "</div></section>"
        "<p class='how-it-works'>Setting a score here overrides the "
        "category default for this activity, today and on future days. "
        "Teach Focus Core once and it remembers.</p>"
        % (story_html, day, "".join(body_rows)
           if body_rows else
           "<tr><td colspan='7' class='note'>"
           "No activities stored for this day.</td></tr>")
    )
    return layout("Activities " + day, body, day, active="review")


@bp.route("/override", methods=["POST"])
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


@bp.route("/goals")
def goals_page():
    from focuscore import goals as goals_mod

    day = date.today().isoformat()
    summary = store.get_day_summary(day)
    rows = []
    for goal in store.list_goals():
        ev = goals_mod.evaluate_goal(goal, summary)
        rows.append(
            "<div class='goal-row'>%s<div class='gmeta'>%s</div></div>"
            % (_goal_progress_html(ev, wrap=False),
               _goal_manage_buttons(goal)))
    goals_html = "".join(rows) or \
        "<p class='note'>Nothing here yet &mdash; your goals will appear " \
        "here as today unfolds.</p>"

    categories = sorted(store.get_categories().keys())
    target_options = (
        '<option value="pulse">Productivity Pulse (0-100)</option>' +
        "".join('<option value="category:%s">%s</option>'
                % (escape(c), escape(c)) for c in categories
                if c != "Uncategorized"))

    # One-tap starter goals: the empty state as onboarding. Only shown
    # before the first goal exists; each chip posts the same fields as
    # the form below, so /goals/add needs no changes.
    starters = []
    if not rows:
        def _starter_cat(*words):
            for c in categories:
                cl = c.lower()
                if any(w in cl for w in words):
                    return c
            return None
        dev_cat = _starter_cat("develop", "design", "business", "writ")
        social_cat = _starter_cat("social", "entertain", "news", "video")
        if dev_cat:
            starters.append(("4h deep work", "Deep work", "more_than",
                              "category:" + dev_cat, "240"))
        if social_cat:
            starters.append(("Under 1h social", "Less social media",
                              "less_than", "category:" + social_cat, "60"))
        starters.append(("Pulse above 70", "Strong day", "more_than",
                          "pulse", "70"))
    starters_html = "".join(
        "<form class='inline' method='post' action='/goals/add'>"
        "<input type='hidden' name='name' value='%s'>"
        "<input type='hidden' name='direction' value='%s'>"
        "<input type='hidden' name='target' value='%s'>"
        "<input type='hidden' name='threshold' value='%s'>"
        "<button type='submit' class='chip'>%s</button></form>"
        % (escape(name), direction, escape(target), threshold, escape(label))
        for label, name, direction, target, threshold in starters)
    starters_block = (
        "<p class='goal-starters-label'>Or start with one of these:</p>"
        "<div class='goal-starters'>%s</div>" % starters_html
    ) if starters_html else ""

    body = (
        "<div class='card goal-create'><h3>Add a goal</h3>%s"
        "<form method='post' action='/goals/add' class='sentence-form'>"
        "<p class='sentence'>I want "
        "<select name='direction' aria-label='Direction'>"
        "<option value='more_than' selected>more than</option>"
        "<option value='less_than'>less than</option></select> "
        "<input type='number' name='threshold' min='0' step='0.5' required "
        "placeholder='e.g. 240' aria-label='Amount'> "
        "<select name='target' aria-label='Target'>%s</select> "
        "<span class='nowrap'>each day.</span></p>"
        "<p><label class='sentence-name'>Name it "
        "<input type='text' name='name' required "
        "placeholder='e.g. Deep work' size='24'></label></p>"
        "<p class='note'>Minutes for a category, 0&ndash;100 for Pulse.</p>"
        "<p><button type='submit'>Add goal</button></p>"
        "</form></div>"
        "<section class='progress-strip'><h3>Today's progress</h3>%s"
        "<p class='note'>Live: this page re-reads today's tracked data on "
        "every load.</p></section>"
        "<p class='how-it-works'>Goals watch today's tracked time and update "
        "by themselves. Pin one to see it on the Today page.</p>"
        % (starters_block, target_options, goals_html)
    )
    body += ("<div class='hm-tabspace' aria-hidden='true'></div>"
             + _living_tabs("goals"))
    return layout("Goals", body, active="goals")


@bp.route("/goals/add", methods=["POST"])
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


@bp.route("/goals/delete", methods=["POST"])
def goals_delete():
    try:
        store.delete_goal(int(request.form.get("id")))
    except (TypeError, ValueError):
        # A malformed id means the action silently did not happen;
        # say so in the log without echoing raw form values.
        logger.warning("goals/delete: invalid id, nothing deleted")
    return redirect("/goals")


@bp.route("/goals/pin", methods=["POST"])
def goals_pin():
    try:
        store.set_pinned(int(request.form.get("id")),
                         request.form.get("pinned") == "1")
    except (TypeError, ValueError):
        logger.warning("goals/pin: invalid id, nothing changed")
    return redirect("/goals")


@bp.route("/alerts")
def alerts_page():
    alert_rows = []
    for alert in store.list_alerts():
        alert_rows.append(
            "<div class='gd-strip'>"
            "<div class='gd-strip-main'><b>%s</b>"
            "<span class='gd-strip-sub'>%s &middot; past %.0f min "
            "&middot; repeats every %.0f min</span></div>"
            "<span class='gd-state gd-state--%s'>%s</span>"
            "<form class='inline' method='post' action='/alerts/toggle'>"
            "<input type='hidden' name='id' value='%d'>"
            "<input type='hidden' name='enabled' value='%d'>"
            "<button type='submit' class='gd-strip-btn'>%s</button></form>"
            "<form class='inline' method='post' action='/alerts/delete' "
            "onsubmit=\"return confirm('Delete this alert?');\">"
            "<input type='hidden' name='id' value='%d'>"
            "<button type='submit' class='gd-strip-btn'>Delete</button></form>"
            "</div>"
            % (escape(alert["name"] or "Unnamed alert"),
               _alert_target_text(alert), alert["threshold_minutes"],
               alert["cooldown_minutes"],
               "on" if alert["enabled"] else "off",
               "On" if alert["enabled"] else "Off",
               alert["id"], 0 if alert["enabled"] else 1,
               "Pause" if alert["enabled"] else "Resume", alert["id"]))
    alerts_html = "".join(alert_rows) or \
        "<p class='note'>No alerts yet &mdash; nothing to watch, nothing " \
        "to miss.</p>"

    firing_rows = "".join(
        "<div class='gd-strip gd-strip--quiet'>"
        "<div class='gd-strip-main'><b>%s</b>"
        "<span class='gd-strip-sub'>fired %s &middot; %.1f min on "
        "target</span></div>"
        "</div>"
        % (escape(f["name"] or "deleted alert"),
           escape((f["fired_at"][:16] or "").replace("T", " ")),
           f["current_minutes"] or 0)
        for f in store.recent_firings())
    firings_html = firing_rows or \
        "<p class='note'>Nothing fired yet &mdash; you'll see it here " \
        "when an alert goes off.</p>"

    # One-tap starter alerts: the empty state as onboarding. Only shown
    # before the first alert exists; each chip posts the same fields as
    # the form below, so /alerts/add needs no changes.
    starters = []
    if not alert_rows:
        cats = {c.lower(): c for c in store.get_categories().keys()}
        for label, cat_word, threshold, cooldown, message in (
                ("Social apps over 60 min", "social", "60", "60",
                 "Time to get back to work!"),
                ("Entertainment over 90 min", "entertain", "90", "120",
                 "Evening plans are waiting."),
                ("Shopping over 45 min", "shop", "45", "60",
                 "Do you really need it?")):
            cat = next((c for cl, c in cats.items() if cat_word in cl),
                       None)
            if cat:
                starters.append((label, label, "category", cat, threshold,
                                 cooldown, message))
    starters_html = "".join(
        "<form class='inline' method='post' action='/alerts/add'>"
        "<input type='hidden' name='name' value='%s'>"
        "<input type='hidden' name='target_type' value='%s'>"
        "<input type='hidden' name='target_name' value='%s'>"
        "<input type='hidden' name='threshold_minutes' value='%s'>"
        "<input type='hidden' name='cooldown_minutes' value='%s'>"
        "<input type='hidden' name='message' value='%s'>"
        "<button type='submit' class='chip'>%s</button></form>"
        % (escape(name), ttype, escape(tname), thr, cd, escape(msg),
           escape(label))
        for label, name, ttype, tname, thr, cd, msg in starters)
    starters_block = (
        "<p class='goal-starters-label'>Or start with one of these:</p>"
        "<div class='goal-starters'>%s</div>" % starters_html
    ) if starters_html else ""

    cat_options = "".join(
        '<option value="%s">%s</option>' % (escape(c), escape(c))
        for c in sorted(store.get_categories().keys())
        if c != "Uncategorized")

    body = (
        "<div class='card gd-create'><h3>Add an alert</h3>%s"
        "<form method='post' action='/alerts/add' class='sentence-form'>"
        "<p class='sentence'>Warn me when "
        "<input type='text' name='target_name' list='catlist' required "
        "placeholder='Social Networking or app:chrome' size='24' "
        "aria-label='What to watch'> "
        "<span class='nowrap'>passes</span> "
        "<input type='number' name='threshold_minutes' min='1' step='1' "
        "placeholder='60' required style='width:80px' aria-label='Minutes'> "
        "<span class='nowrap'>minutes.</span></p>"
        "<p class='gd-form-row'><label>Watching "
        "<select name='target_type' aria-label='Watch type'>"
        "<option value='category'>a category</option>"
        "<option value='activity'>one activity</option></select></label> "
        "<label>Don't repeat for "
        "<input type='number' name='cooldown_minutes' min='0' step='5' "
        "value='60' style='width:70px' aria-label='Cooldown minutes'> "
        "min</label></p>"
        "<p><label class='sentence-name'>Name it "
        "<input type='text' name='name' "
        "placeholder='e.g. Too much social media' size='24'></label> "
        "<label>Message (optional) "
        "<input type='text' name='message' size='30' "
        "placeholder='e.g. Time to get back to work!'></label></p>"
        "<p class='note'>Category: pick from the list. Activity: the key "
        "from the Activities page, e.g. <code>app:chrome</code> or "
        "<code>domain:youtube.com</code>.</p>"
        "<p><button type='submit'>Add alert</button></p>"
        "<datalist id='catlist'>%s</datalist></form></div>"
        "<section class='gd-list'><h3>Your alerts</h3>%s</section>"
        "<section class='gd-list'><h3>Recent firings</h3>%s</section>"
        "<p class='how-it-works'>Alerts watch today's tracked time and go "
        "off when you cross a threshold. For desktop pop-ups on Windows, "
        "double-click <code>watch-alerts.bat</code> &mdash; it checks "
        "every 5 minutes.</p>"
        % (starters_block, cat_options, alerts_html, firings_html)
    )
    return layout("Alerts", body, active="alerts")


@bp.route("/alerts/add", methods=["POST"])
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


@bp.route("/alerts/delete", methods=["POST"])
def alerts_delete():
    try:
        store.delete_alert(int(request.form.get("id")))
    except (TypeError, ValueError):
        logger.warning("alerts/delete: invalid id, nothing deleted")
    return redirect("/alerts")


@bp.route("/alerts/toggle", methods=["POST"])
def alerts_toggle():
    try:
        store.set_alert_enabled(int(request.form.get("id")),
                                request.form.get("enabled") == "1")
    except (TypeError, ValueError):
        logger.warning("alerts/toggle: invalid id, nothing changed")
    return redirect("/alerts")


