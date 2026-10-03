"""Shield routes: rules, passes, toggles, HUD.

Split from dashboard/routes/system.py (roadmap 3.5) -- pure code
move, zero behavior change.
"""
from datetime import datetime, timedelta
from html import escape

from flask import Blueprint, redirect, request

from dashboard.app import _rule_schedule_text, layout
from focuscore import store

bp = Blueprint("system_shield", __name__)


@bp.route("/shield")
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
        "<label class='sr-only' for='shield-key'>"
        "App, site or category</label>"
        "<input type='text' name='key' id='shield-key' required "
        "placeholder='youtube.com or social' size='20' "
        "aria-label='App, site or category'> "
        "<label class='sr-only' for='shield-rule-type'>Rule type</label>"
        "<select name='rule_type' id='shield-rule-type' aria-label='Rule type'>"
        "<option value='app'>an app / website</option>"
        "<option value='category'>a category</option></select> "
        "<label class='sr-only' for='shield-days'>Days</label>"
        "<select name='days' id='shield-days' aria-label='Days'>"
        "<option value='all'>every day</option>"
        "<option value='0,1,2,3,4'>weekdays</option>"
        "<option value='5,6'>weekends</option></select> "
        "<span class='nowrap'>from</span> "
        "<label class='sr-only' for='shield-from'>From</label>"
        "<input type='text' name='start_time' id='shield-from' "
        "placeholder='09:00' size='5' "
        "aria-label='From'> "
        "<span class='nowrap'>to</span> "
        "<label class='sr-only' for='shield-to'>To</label>"
        "<input type='text' name='end_time' id='shield-to' "
        "placeholder='18:00' size='5' "
        "aria-label='To'> "
        "<label class='sr-only' for='shield-action'>Action</label>"
        "<select name='action' id='shield-action' aria-label='Action'>"
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


@bp.route("/shield/rule/add", methods=["POST"])
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


@bp.route("/shield/rule/toggle", methods=["POST"])
def shield_rule_toggle():
    try:
        rule_id = int(request.form.get("id"))
    except (TypeError, ValueError):
        return redirect("/shield")
    store.set_block_rule_enabled(
        rule_id, request.form.get("enabled") == "1")
    return redirect("/shield")


@bp.route("/shield/rule/delete", methods=["POST"])
def shield_rule_delete():
    try:
        rule_id = int(request.form.get("id"))
    except (TypeError, ValueError):
        return redirect("/shield")
    store.delete_block_rule(rule_id)
    return redirect("/shield")


@bp.route("/shield/pass", methods=["POST"])
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


@bp.route("/shield/toggle", methods=["POST"])
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


@bp.route("/shield/hud", methods=["POST"])
def shield_hud():
    store.set_setting("hud_enabled",
                      "1" if request.form.get("enabled") == "1" else "0")
    return redirect("/shield")
