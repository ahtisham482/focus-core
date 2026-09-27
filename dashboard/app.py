"""Local dashboard for focus-core Phase 1.

Run:  python -m dashboard.app        (from the focus-core folder)
Then open http://127.0.0.1:5000 in a browser.

Routes:
    /                    today's overview (Pulse, pinned goals, buckets,
                         categories, uncategorized queue, AFK note)
    /day/<YYYY-MM-DD>    overview for any stored day
    /goals               all goals with progress + add/delete/pin forms
    /alerts              alerts with add/delete/enable forms + recent firings
    /activities?day=...  activity table with per-row score override forms
    POST /override        save a per-activity score override and apply it
                         to that day immediately
    /timesheet?day=...    suggested blocks + entries + projects + lock/export
    /report?week=...      weekly summary (hours, Pulse trend, categories,
                         goal hit-rate, focus sessions)
    /coaching             best focus hours heatmap, best windows, warnings
"""

import sys
import json as _json
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, Response, abort, redirect, request  # noqa: E402

from focuscore import paths
from focuscore import store  # noqa: E402
from focuscore.home import pulse_band as _pulse_band  # noqa: E402
from focuscore.ingest import ActivityWatchError  # noqa: E402
from focuscore.pipeline import run_day  # noqa: E402
from focuscore.scoring import UI_LABELS, productivity_pulse  # noqa: E402
from focuscore.taxonomy import host_of  # noqa: E402

app = Flask(__name__)

ONBOARDED_FLAG = paths.onboarded_flag()

# Top navigation: (key, label, href). "Review" jumps to today's
# uncategorized activities when a day is known.
NAV_LINKS = [
    ("home", "Home", "/"),
    ("timesheet", "Timesheet", "/timesheet"),
    ("invoices", "Invoices", "/invoices"),
    ("report", "Report", "/report"),
    ("coaching", "Coaching", "/coaching"),
    ("intelligence", "Deep time", "/intelligence"),
    ("focus", "Focus", "/focus"),
    ("shield", "Shield", "/shield"),
    ("goals", "Goals", "/goals"),
    ("alerts", "Alerts", "/alerts"),
    ("backup", "Backup", "/backup"),
    ("review", "Review", "/activities"),
]


def is_onboarded():
    """True once the user finished (or skipped) the welcome tour."""
    return ONBOARDED_FLAG.exists()


def mark_onboarded():
    ONBOARDED_FLAG.write_text(date.today().isoformat())

STATUS_LABEL = {"on_track": "On track", "achieved": "Achieved",
                "behind": "Behind", "missed": "Missed"}

COLORS = {2: "#2e7d32", 1: "#81c784", 0: "#9e9e9e", -1: "#ffb74d", -2: "#e53935"}


def _hours(seconds):
    return float(seconds or 0) / 3600.0


def _parse_day(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except (ValueError, TypeError):
        return None


def layout(title, body, day=None, refresh=300, active="home", help_key=None):
    """Page shell: nav bar, shared stylesheet, footer. No inline CSS --
    everything visual lives in dashboard/static/style.css (offline).

    help_key selects the contextual /help/<key> article linked in the
    footer. When omitted it is derived from the nav key (review -> the
    activities article); pages outside the nav pass it explicitly, and
    the help pages themselves use active="help" so the footer falls
    back to the /help index.
    """
    links = []
    for key, label, href in NAV_LINKS:
        url = href
        if key == "review" and day:
            url = href + "?day=" + day
        cls = " class='active'" if key == active else ""
        links.append("<a href='%s'%s>%s</a>" % (url, cls, label))
    nav = "<nav class='topnav'>" + "".join(links) + "</nav>"
    if help_key is None:
        help_key = {"home": "home", "timesheet": "timesheet",
                    "invoices": "invoices",
                    "report": "report", "coaching": "coaching",
                    "intelligence": "intelligence",
                    "focus": "focus", "shield": "shield",
                    "goals": "goals", "alerts": "alerts",
                    "backup": "backup", "review": "activities"}.get(active)
    help_href = "/help/" + help_key if help_key else "/help"
    footer = (
        "<footer>Focus Core &middot; your data never leaves this PC "
        "&middot; <a href='%s'>Help</a> "
        "&middot; <a href='/welcome/restart'>Take the tour again</a></footer>"
        % help_href)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='refresh' content='%d'>"
        "<title>%s &middot; Focus Core</title>"
        "<link rel='stylesheet' href='/static/style.css'>"
        "<link rel='icon' href='/static/icon.png'></head>"        "<body>%s<h1>%s</h1>"
        "<div class='sub'>Focus Core &middot; Phase 6</div>%s%s</body></html>"
        % (refresh, escape(title), nav, escape(title), body, footer)
    )


def _goal_progress_html(ev):
    """One goal row: name, progress bar, current/target, status badge."""
    width = min(ev["pct"], 100.0)
    color = {"on_track": "#2e7d32", "achieved": "#2e7d32",
             "behind": "#f9a825", "missed": "#e53935"}[ev["status"]]
    if ev["target_type"] == "pulse":
        target_text = "Pulse %s %.0f pts" % (
            ">=" if ev["direction"] == "more_than" else "<=", ev["target"])
        current_text = "%.1f pts" % ev["current"]
    else:
        target_text = "%s %s %.0f min" % (
            escape(ev["target_name"] or ""),
            ">=" if ev["direction"] == "more_than" else "<=", ev["target"])
        current_text = "%.0f min" % ev["current"]
    return (
        "<div class='goal-row'><span class='gname'>%s</span> "
        "<span class='badge %s'>%s</span><br>"
        "<span class='gmeta'>%s of %s</span>"
        "<div class='progress'><div style='width:%.1f%%;background:%s'></div></div>"
        "</div>"
        % (escape(ev["name"]), ev["status"],
           STATUS_LABEL.get(ev["status"], ev["status"]),
           current_text, target_text, width, color)
    )


def pinned_goals_html(day):
    """Pinned goals with live progress for the day page (empty if none)."""
    from focuscore import goals as goals_mod

    summary = store.get_day_summary(day)
    pinned = [g for g in store.list_goals() if g["pinned"]]
    if not pinned:
        return ""
    rows = "".join(
        _goal_progress_html(goals_mod.evaluate_goal(g, summary)) for g in pinned)
    return ("<div class='card'><h3>Pinned goals</h3>%s"
            "<p class='note'><a href='/goals'>Manage all goals</a></p></div>"
            % rows)


def bucket_bar(seconds_by_level, total):
    parts = []
    for level in (2, 1, 0, -1, -2):
        seconds = seconds_by_level.get(level, 0)
        pct = (seconds / total * 100) if total > 0 else 0
        parts.append(
            '<div class="seg" style="width:%.1f%%;background:%s" '
            'title="%s: %.2fh"></div>'
            % (pct, COLORS[level], UI_LABELS[level], _hours(seconds))
        )
    return '<div class="bar">' + "".join(parts) + "</div>"


def legend(seconds_by_level):
    items = []
    for level in (2, 1, 0, -1, -2):
        items.append(
            '<span><span class="dot" style="background:%s"></span>'
            "%+d %s &middot; %.2fh</span>"
            % (COLORS[level], level, UI_LABELS[level],
               _hours(seconds_by_level.get(level, 0)))
        )
    return '<div class="legend">' + "".join(items) + "</div>"


def day_page(day):
    aw_note = ""
    if day == date.today().isoformat():
        # Live progress: refresh today's tracked data on every page load.
        try:
            run_day(date.today())
        except ActivityWatchError as exc:
            aw_note = (
                "<div class='card'><p><b>Tracker not running.</b></p>"
                "<p class='note'>Could not reach ActivityWatch: %s<br>"
                "Start ActivityWatch and reload this page -- or "
                "<a href='/setup/activitywatch'>set it up</a> if it isn't "
                "installed yet. "
                "Your saved data is still shown below.</p></div>"
                % escape(str(exc)))
    summary = store.get_day_summary(day)
    pulse = productivity_pulse(summary["seconds_by_level"])
    total = summary["total_seconds"]

    if total <= 0:
        if aw_note:
            # A stranger with no data and no tracker: plain words, not CLI.
            empty = (
                "<div class='card'><p>No tracked data for this day yet.</p>"
                "<p class='note'>ActivityWatch isn't running, so nothing "
                "was recorded. <a href='/setup/activitywatch'>Set up "
                "ActivityWatch</a> to start tracking.</p></div>")
        else:
            empty = (
                "<div class='card'><p>No tracked data for this day yet.</p>"
                "<p class='note'>Run <code>python -m focuscore.pipeline "
                "--day %s</code> (add <code>--demo</code> to try it without "
                "ActivityWatch).</p></div>" % day)
        return layout("Day " + day, aw_note + empty, day, help_key="day")

    cat_rows = "".join(
        "<tr><td>%s</td><td>%s (%+d)</td><td>%.2fh</td><td>%.1f%%</td></tr>"
        % (escape(name), UI_LABELS.get(score, "?"), score,
           _hours(seconds), seconds / total * 100 if total else 0)
        for name, seconds, score in sorted(
            ((n, s, _category_score(n, s)) for n, s in
             summary["seconds_by_category"].items()),
            key=lambda r: r[1], reverse=True)
    )
    uncat_rows = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%.2fh</td></tr>"
        % (escape(item["match_key"]), escape(item["app"] or "?"),
           _hours(item["seconds"]))
        for item in summary["uncategorized"][:10]
    ) or "<tr><td colspan='3' class='note'>Nothing waiting -- all clear.</td></tr>"

    body = (
        aw_note +
        pinned_goals_html(day) +
        "<div class='card'><div>Pulse for %s</div>"
        "<div class='pulse %s'>%.1f</div>"
        "<div class='note'>Weighted 0-100 score. Green 60+, amber 40-59, "
        "red below 40. %.2fh tracked.</div>"
        "%s%s</div>"
        "<div class='card'><h3>By category</h3>"
        "<table><tr><th>Category</th><th>Score</th><th>Time</th><th>Share</th></tr>"
        "%s</table></div>"
        "<div class='card'><h3>Uncategorized queue</h3>"
        "<p class='note'>These counted as Neutral in the Pulse. "
        "Review them on the <a href='/activities?day=%s'>Activities</a> page "
        "and set a score -- or fix their category for next time.</p>"
        "<table><tr><th>Activity</th><th>App</th><th>Time</th></tr>%s</table></div>"
        "<div class='card note'>AFK/idle time (%.2fh) is excluded from the Pulse.</div>"
        % (day, _pulse_band(pulse), pulse, _hours(total),
           bucket_bar(summary["seconds_by_level"], total),
           legend(summary["seconds_by_level"]), cat_rows, day, uncat_rows,
           _hours(summary["afk_seconds"]))
    )
    return layout("Day " + day, body, day, help_key="day")


def _category_score(name, _seconds):
    # Score shown next to a category: its default (custom-aware) score.
    from focuscore.taxonomy import get_category_score
    return get_category_score(name, store.get_categories())


# ------------------------------------------------------------- security ---
# Local security baseline. This is a single-user dashboard with no
# authentication, bound to the loopback interface by default, so any web
# page open in the user's browser could send cross-origin requests to it.
# These hooks block loopback-CSRF: a malicious local page cannot make a
# state-changing request here because it cannot forge the Host/Origin
# headers to look loopback.
#
# No debug mode, no CORS, no authentication (out of scope by design:
# loopback-only, single user).

from urllib.parse import urlsplit  # noqa: E402

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_MUTATING_METHODS = frozenset(("POST", "PUT", "DELETE", "PATCH"))


def _host_is_loopback(host_value):
    """True if a Host header names this machine (optional :port allowed)."""
    h = (host_value or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):
        # "[::1]:5000" style: take what is between the brackets.
        end = h.find("]")
        if end == -1:
            return False
        rest = h[end + 1:]
        if rest and not rest.startswith(":"):
            return False
        h = h[1:end]
    elif h == "::1":
        pass  # bracketless IPv6 loopback is fine as-is
    elif h.count(":") == 1:
        # "127.0.0.1:5000" style: drop the port.
        h = h.rsplit(":", 1)[0]
    elif h.count(":") > 1:
        # Raw IPv6 (other than ::1) is not an allowed loopback host.
        return False
    return h in _LOOPBACK_HOSTS


def _origin_is_loopback(origin_value):
    """True if an Origin header is http(s) on a loopback host."""
    try:
        parts = urlsplit((origin_value or "").strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if not parts.netloc:
        return False
    return _host_is_loopback(parts.hostname or "")


_BLOCKED_PAGE = (
    "<h1>Focus Core</h1>"
    "<p><b>Forbidden:</b> this action was blocked because the request did "
    "not come from Focus Core itself.</p>"
    "<p>Please go back and use the button on the Focus Core page. If you "
    "clicked a button inside Focus Core and still see this, a browser "
    "extension may be changing the request -- try the Focus Core desktop "
    "window instead.</p>"
    "<p><a href='/'>Back to Home</a></p>")


@app.before_request
def _reject_loopback_csrf():
    # Only state-changing requests need protection; GET/HEAD/OPTIONS
    # are read-only in this app and must keep working.
    if request.method not in _MUTATING_METHODS:
        return None
    if not _host_is_loopback(request.headers.get("Host", "")):
        return (_BLOCKED_PAGE, 403)
    origin = request.headers.get("Origin")
    if origin and not _origin_is_loopback(origin):
        return (_BLOCKED_PAGE, 403)
    return None


@app.after_request
def _add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # 'unsafe-inline' is needed because the dashboard is a server-rendered
    # single-file app (no template files to split): it uses inline
    # onsubmit="return confirm(...)" handlers on the delete/abort/restore
    # forms and inline style="" attributes on the home-page bar charts.
    # No external content is ever loaded and the server binds 127.0.0.1
    # only, so allowing inline does not widen the trust boundary.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'"
    )
    return response


@app.route("/")
def index():
    if not is_onboarded():
        return redirect("/welcome")
    return home_page()


def home_page():
    """Command center: today's Pulse, key stats, and one attention card
    per thing that needs the user -- each with exactly one button."""
    from focuscore import home as home_mod
    from focuscore import activitywatch as aw_mod

    today = date.today().isoformat()
    try:
        run_day(date.today())
    except ActivityWatchError:
        pass  # the "ActivityWatch isn't running" card covers this

    aw_status = aw_mod.server_status()
    aw_state = aw_mod.detection_state(status=aw_status)

    summary = store.get_day_summary(today)
    pulse = productivity_pulse(summary["seconds_by_level"])
    total = summary["total_seconds"]
    seconds_by_level = summary["seconds_by_level"]
    focus_hours = _hours(seconds_by_level.get(2, 0)
                         + seconds_by_level.get(1, 0))

    if total > 0 and pulse is not None:
        band = home_mod.pulse_band(pulse)
        pulse_html = ("<div class='card stat'><div class='lbl'>Today's "
                      "Pulse</div><div class='pulse %s'>%.1f</div>"
                      "<div class='note'>0-100. Green 60+, amber 40-59, "
                      "red below 40.</div></div>" % (band, pulse))
    else:
        pulse_html = ("<div class='card stat'><div class='lbl'>Today's "
                      "Pulse</div><div class='pulse none'>--</div>"
                      "<div class='note'>No tracked time yet today.</div>"
                      "</div>")

    stats_html = (
        "<div class='grid'>"
        "<div class='card stat'><div class='num'>%.1f</div>"
        "<div class='lbl'>tracked hours</div></div>"
        "<div class='card stat'><div class='num'>%.1f</div>"
        "<div class='lbl'>focused hours</div></div>"
        "</div>" % (_hours(total), focus_hours))

    cards = home_mod.attention_cards(aw_state=aw_state)
    if cards:
        card_html = "".join(
            "<div class='card attention'><h3>%s</h3><p>%s</p>"
            "<p><a class='btn' href='%s'>%s</a></p></div>"
            % (escape(c["title"]), escape(c["detail"]),
               escape(c["button_href"]), escape(c["button_text"]))
            for c in cards)
        attention_html = "<h2>What needs your attention</h2>" + card_html
    else:
        attention_html = (
            "<div class='card attention ok'><h3>All clear -- you're on "
            "track.</h3><p class='note'>Nothing needs you right now.</p>"
            "</div>")

    body = (pulse_html + stats_html + attention_html
            + pinned_goals_html(today)
            + "<div class='card'><p><a href='/day/%s'>See today's full "
              "details</a> &middot; <a href='/timesheet?day=%s'>Today's "
              "timesheet</a></p></div>" % (today, today))
    return layout("Home", body, day=today, active="home")


@app.route("/collect")
def collect():
    """Re-run today's collection, then show the day page (which explains
    clearly when ActivityWatch is not reachable)."""
    today = date.today().isoformat()
    return redirect("/day/" + today)


# ------------------------------------------------------------ welcome ---

WELCOME_STEPS = [
    {"emoji": "\U0001f512",
     "title": "Your data stays on this computer.",
     "text": "Focus Core has no account, no sign-in, and sends nothing "
             "to the internet -- there is no server that can see your "
             "data. Everything lives in one file (focuscore.db) on this "
             "PC. If you turn on Drive backups, a copy of that file is "
             "placed in your own Google Drive folder and nowhere else.",
     "check": None},
    {"emoji": "\U0001f440",
     "title": "Your activity is tracked by ActivityWatch; Focus Core scores it.",
     "text": "Focus Core does not watch anything itself. It reads from "
             "ActivityWatch, a free, open-source tracker that records "
             "which app or website you were using. That is why "
             "ActivityWatch must be running -- without it, there is "
             "nothing for Focus Core to score.",
     "check": "Look at the bottom-right of your Windows taskbar, near "
              "the clock. You should see the ActivityWatch icon. If it "
              "is missing, open ActivityWatch from the Start menu -- it "
              "should start with Windows by itself."},
    {"emoji": "\U0001f4ca",
     "title": "It scores your time from -2 to +2.",
     "text": "Very productive work is +2, productive +1, neutral 0, "
             "personal -1, very distracting -2. Your Pulse (0-100) is "
             "the weighted mix of everything you did. Green (60+) is a "
             "good day.",
     "check": None},
    {"emoji": "\U0001f3af",
     "title": "Teach it once: review uncategorized activities.",
     "text": "New apps start as Neutral. When you tell Focus Core what "
             "an activity really is, it remembers -- so your score gets "
             "more accurate every day.",
     "check": None},
]


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
        ONBOARDED_FLAG.unlink()
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


# ---------------------------------------------------------------- goals ---

def _goal_manage_buttons(goal):
    pin_label = "Unpin" if goal["pinned"] else "Pin"
    return (
        "<form class='inline' method='post' action='/goals/pin'>"
        "<input type='hidden' name='id' value='%d'>"
        "<input type='hidden' name='pinned' value='%d'>"
        "<button type='submit'>%s</button></form> "
        "<form class='inline' method='post' action='/goals/delete' "
        "onsubmit=\"return confirm('Delete this goal?');\">"
        "<input type='hidden' name='id' value='%d'>"
        "<button type='submit'>Delete</button></form>"
        % (goal["id"], 0 if goal["pinned"] else 1, pin_label, goal["id"])
    )


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


# --------------------------------------------------------------- alerts ---

def _alert_target_text(alert):
    if alert["target_type"] == "category":
        return "Category: %s" % escape(alert["target_name"])
    return "Activity: <code>%s</code>" % escape(alert["target_name"])


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


# ---------------------------------------------------------------- focus ---

def _fmt_countdown(total_seconds):
    total = int(max(0, total_seconds))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


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


def _session_buttons():
    return (
        "<form class='inline' method='post' action='/focus/end'>"
        "<button type='submit'>End session</button></form> "
        "<form class='inline' method='post' action='/focus/abort' "
        "onsubmit=\"return confirm('Abort this session? "
        "It will not count toward your streak.');\">"
        "<button type='submit'>Abort</button></form>")


def _pomodoro_active_page(active, streak_html, cues_form, focus_mod):
    from focuscore import adaptive
    from focuscore import store as store_mod
    cycle = store_mod.get_active_cycle(active["id"])
    done = active.get("completed_cycles") or 0
    target = active.get("target_cycles") or 4
    try:
        tired, tired_msg = adaptive.fatigue_check()
    except Exception:
        tired, tired_msg = False, ""
    if cycle:
        remaining = focus_mod.cycle_remaining_seconds(cycle)
        if cycle["kind"] == "work":
            headline = "Work block %d of %d" % (done + 1, target)
            controls = (
                "<form class='inline' method='post' "
                "action='/focus/cycle/break/start'>"
                "<button type='submit'>Start break</button></form> ")
            note = ("Blocking is on. The page refreshes every 10 seconds; "
                    "a sound cue plays when the block ends.")
        else:
            headline = "Break -- relax"
            controls = (
                "<form class='inline' method='post' "
                "action='/focus/cycle/break/end'>"
                "<button type='submit'>End break early</button></form> "
                "<form class='inline' method='post' "
                "action='/focus/cycle/break/skip'>"
                "<button type='submit'>Skip break</button></form> ")
            note = ("Blocking is resting too (only gentle reminders). "
                    "Breaks end by themselves after 30 minutes at most.")
        cycle_html = (
            "<div class='countdown'>%s</div>"
            "<p class='note'>%s &middot; %s</p>"
            "<p>%s%s</p>"
            % (_fmt_countdown(remaining), headline, note, controls,
               _session_buttons()))
    else:
        cycle_html = (
            "<p><b>Target reached: %d work blocks.</b> End the session "
            "whenever you are ready -- well done.</p>"
            "<p>%s</p>" % _session_buttons())
    tired_card = (("<div class='card'><p><b>%s</b></p></div>"
                   % escape(tired_msg)) if tired else "")
    body = (
        "<div class='card'><div class='focus-label'>%s</div>%s</div>"
        "%s"
        "<div class='card'><h3>Preferences</h3><p>%s</p></div>"
        "%s"
        % (escape(active["label"]), cycle_html, tired_card, cues_form,
           streak_html))
    return layout("Focus session", body, refresh=10, active="focus")


def _flowtime_deferral_offered(session_id, db_path=None):
    """True once the one-time 10-minute deferral has been shown (R4)."""
    from focuscore import store
    return store.get_setting(
        "flow_deferral_shown_%s" % session_id, path=db_path) == "1"


def _mark_flowtime_deferral_offered(session_id, db_path=None):
    from focuscore import store
    store.set_setting("flow_deferral_shown_%s" % session_id, "1",
                      path=db_path)


def _flowtime_active_page(active, streak_html, cues_form, focus_mod,
                          db_path=None):
    from datetime import datetime
    start = focus_mod._to_naive(active["started_at"])
    elapsed_min = max(0.0, (datetime.now() - start).total_seconds() / 60.0)
    target = active["planned_minutes"] or 50
    deferral = ""
    if elapsed_min >= target:
        # R4: at most one gentle 10-minute deferral suggestion per
        # session — shown once, then never again for this session.
        if not _flowtime_deferral_offered(active["id"], db_path=db_path):
            deferral = (
                "<p class='note'>You passed your soft target of %.0f min. "
                "Take 10 more minutes, or end at a natural break -- "
                "your call.</p>" % target)
            _mark_flowtime_deferral_offered(active["id"], db_path=db_path)
    body = (
        "<div class='card'><div class='focus-label'>%s</div>"
        "<div class='countdown'>%s elapsed</div>"
        "<p class='note'>Soft target %.0f min (no alarm) &middot; %s "
        "blocking &middot; page refreshes every 10 seconds</p>"
        "%s"
        "<p>%s</p>"
        "<p class='note'>Blocking is on for the whole session -- "
        "there is no timer to beat, just your natural stopping point.</p>"
        "</div>"
        "<div class='card'><h3>Preferences</h3><p>%s</p></div>"
        "%s"
        % (escape(active["label"]), focus_mod._fmt_hms(elapsed_min * 60),
           target, escape(active["block_level"]), deferral,
           _session_buttons(), cues_form, streak_html))
    return layout("Focus session", body, refresh=10, active="focus")


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


def _cat_color(category):
    """Bar color for a category, from its score (grey when unknown)."""
    try:
        score = store.get_categories().get(category, {}).get("score", 0)
    except Exception:  # noqa: BLE001
        score = 0
    return COLORS.get(score, "#9e9e9e")


def _suggestion_timeline(suggestions):
    """Visual vertical timeline of suggested blocks.

    Each block links to its table row (``#sug-<n>``), which the CSS
    highlights via ``:target`` -- clicking a block highlights its row.
    Block height is illustrative only: 2px per minute, clamped to
    30-140px so one long block doesn't swallow the page.
    """
    if not suggestions:
        return "<p class='note'>No blocks to show on the timeline.</p>"
    parts = []
    for i, block in enumerate(suggestions):
        height = max(30, min(140, int(block["minutes"] * 2)))
        parts.append(
            "<a class='tl-block' href='#sug-%d' "
            "style='height:%dpx;background:%s' "
            "title='%s - %s, %.0f min'>"
            "<span class='tl-time'>%s</span>"
            "<b>%s</b> &middot; %.0f min<br>%s</a>"
            % (i, height, _cat_color(block["category"]),
               escape(block["start_ts"][11:]), escape(block["end_ts"][11:]),
               block["minutes"], escape(block["start_ts"][11:]),
               escape(block["category"]), block["minutes"],
               escape(block["app"])))
    return ("<div class='timeline'>" + "".join(parts) + "</div>"
            "<p class='note'>Click a block to highlight its row below.</p>")


# ----------------------------------------------------------- timesheet ---

def _project_options(selected_id=None):
    options = ["<option value=''>-- no project --</option>"]
    for project in store.list_projects():
        selected = " selected" if selected_id == project["id"] else ""
        label = project["name"]
        if project["client"]:
            label += " (%s)" % project["client"]
        options.append("<option value='%d'%s>%s</option>"
                       % (project["id"], selected, escape(label)))
    return "".join(options)


def _category_options(selected=None):
    options = []
    for name in sorted(store.get_categories().keys()):
        options.append("<option value='%s'%s>%s</option>"
                       % (escape(name),
                          " selected" if selected == name else "",
                          escape(name)))
    return "".join(options)


@app.route("/timesheet")
def timesheet_page():
    from focuscore import timesheet as ts_mod

    day = _parse_day(request.args.get("day")) or date.today().isoformat()
    suggestions = ts_mod.suggest_blocks(day)
    entries = store.list_entries(day=day)
    projects = store.list_projects()
    locked = store.day_is_locked(day)

    # --- suggested blocks (only suggest; accepted ones live below) ---
    sug_rows = []
    for i, block in enumerate(suggestions):
        sug_rows.append(
            "<tr id='sug-%d'><td>%s - %s</td><td>%.1f</td><td>%s</td><td>%s</td>"
            "<td class='title-cell' title='%s'>%s</td>"
            "<td><form class='inline' method='post' "
            "action='/timesheet/accept'>"
            "<input type='hidden' name='day' value='%s'>"
            "<input type='hidden' name='start_ts' value='%s'>"
            "<input type='hidden' name='end_ts' value='%s'>"
            "<input type='hidden' name='minutes' value='%.1f'>"
            "<input type='hidden' name='category' value='%s'>"
            "<input type='hidden' name='app' value='%s'>"
            "<input type='hidden' name='title_hint' value='%s'>"
            "<select name='project_id'>%s</select> "
            "<input type='text' name='task' placeholder='task' size='10'> "
            "<button type='submit'>Accept</button></form></td></tr>"
            % (i, escape(block["start_ts"][11:]), escape(block["end_ts"][11:]),
               block["minutes"], escape(block["category"]),
               escape(block["app"]), escape(block["title_hint"]),
               escape(block["title_hint"][:50]),
               day, escape(block["start_ts"]), escape(block["end_ts"]),
               block["minutes"], escape(block["category"]),
               escape(block["app"]), escape(block["title_hint"]),
               _project_options()))
    sug_table = (
        "<table><tr><th>Time</th><th>Min</th><th>Category</th><th>App</th>"
        "<th>Title</th><th>Accept as</th></tr>%s</table>"
        % ("".join(sug_rows)
           or "<tr><td colspan='6' class='note'>"
              "No blocks to suggest for this day. (Blocks shorter than 5 "
              "minutes are skipped.)</td></tr>"))
    timeline_html = _suggestion_timeline(suggestions)

    # --- my entries ---
    entry_rows = []
    for entry in entries:
        # Phase 10 (Q11): invoiced entries show which invoice claimed them.
        # Editing the entry never changes the invoice line snapshot.
        inv_badge = ""
        if entry.get("invoice_id"):
            inv_label = entry.get("invoice_number") or (
                "draft #%d" % entry["invoice_id"])
            inv_badge = (
                " <span class='pill' title='This entry is invoiced. Editing "
                "it will not change the invoice -- the invoice keeps its "
                "own snapshot of the line.'>Invoiced on %s -- locked</span>"
                % escape(inv_label))
        if entry["locked"]:
            actions = "<span class='note'>locked</span>"
            row_form = (
                "<td>%s - %s<br><span class='note'>%.1f min</span></td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='title-cell' title='%s'>%s</td>"
                "<td>%s%s</td><td>%s</td>"
                % (escape(entry["start_ts"][11:]),
                   escape(entry["end_ts"][11:]), entry["minutes"],
                   escape(entry["category"]), escape(entry["app"]),
                   escape(entry["project_name"] or "-"),
                   escape(entry["task"] or "-"),
                   escape(entry["note"]), escape(entry["note"][:40]),
                   escape(entry["status"]), inv_badge, actions))
        else:
            # Inputs use form="edit-<id>" so the row stays valid HTML
            # (a <form> directly inside <tr> would be moved by browsers).
            actions = (
                "<form id='edit-%d' method='post' action='/timesheet/edit'>"
                "<input type='hidden' name='id' value='%d'>"
                "<input type='hidden' name='day' value='%s'>"
                "<button type='submit'>Save</button></form> "
                "<form class='inline' method='post' "
                "action='/timesheet/delete' "
                "onsubmit=\"return confirm('Delete this entry?');\">"
                "<input type='hidden' name='id' value='%d'>"
                "<input type='hidden' name='day' value='%s'>"
                "<button type='submit'>Delete</button></form>"
                % (entry["id"], entry["id"], day, entry["id"], day))
            row_form = (
                "<td><input type='text' name='start_ts' form='edit-%d' "
                "value='%s' size='16'> - "
                "<input type='text' name='end_ts' form='edit-%d' "
                "value='%s' size='16'></td>"
                "<td><select name='category' form='edit-%d'>%s</select></td>"
                "<td>%s</td>"
                "<td><select name='project_id' form='edit-%d'>%s</select></td>"
                "<td><input type='text' name='task' form='edit-%d' "
                "value='%s' size='10'></td>"
                "<td><input type='text' name='note' form='edit-%d' "
                "value='%s' size='14'></td>"
                "<td>%s%s</td><td>%s</td>"
                % (entry["id"], escape(entry["start_ts"]),
                   entry["id"], escape(entry["end_ts"]),
                   entry["id"], _category_options(entry["category"]),
                   escape(entry["app"]),
                   entry["id"], _project_options(entry["project_id"]),
                   entry["id"], escape(entry["task"]),
                   entry["id"], escape(entry["note"]),
                   escape(entry["status"]), inv_badge, actions))
        entry_rows.append("<tr>" + row_form + "</tr>")
    entries_table = (
        "<table><tr><th>Time</th><th>Category</th><th>App</th>"
        "<th>Project</th><th>Task</th><th>Note</th><th>Status</th>"
        "<th></th></tr>%s</table>"
        % ("".join(entry_rows)
           or "<tr><td colspan='8' class='note'>No entries yet -- accept a "
              "suggestion above or add one manually below.</td></tr>"))

    # --- projects (Phase 9: rate + budget cards) ---
    projects_html = _project_cards_html(day)

    # --- client export panel (Phase 9) ---
    export_html = _export_panel_html(day, projects)

    msg = escape(request.args.get("msg") or "")
    msg_html = ("<div class='card'><p><b>%s</b></p></div>" % msg) if msg else ""

    lock_html = (
        "<p class='note'>This day is locked -- entries cannot be changed. "
        "Locking is permanent.</p>" if locked else
        "<form method='post' action='/timesheet/lock' "
        "onsubmit=\"return confirm('Lock this day? Entries cannot be edited "
        "afterwards.');\">"
        "<input type='hidden' name='day' value='%s'>"
        "<button type='submit'>Lock day</button></form>" % day)

    body = (
        "%s"
        "<div class='card'><h3>Timesheet -- %s</h3>"
        "<form class='inline' method='get' action='/timesheet'>"
        "<label>Day <input type='date' name='day' value='%s'></label> "
        "<button type='submit'>Show</button></form> "
        "<a href='/timesheet/export/client?from=%s&to=%s'>Export this day as CSV</a>"
        "<p class='note'>Suggested blocks merge consecutive tracked "
        "activities of the same category (gaps over 5 minutes split a "
        "block). Accept one to add it to your timesheet.</p></div>"
        "<div class='card'><h3>Suggested blocks</h3>%s%s</div>"
        "<div class='card'><h3>My entries</h3>%s%s</div>"
        "<div class='card'><h3>Add entry manually</h3>"
        "<form method='post' action='/timesheet/add'>"
        "<input type='hidden' name='day' value='%s'>"
        "<p><label>Start <input type='text' name='start_ts' required "
        "placeholder='2026-09-25T09:00' size='18'></label> "
        "<label>End <input type='text' name='end_ts' required "
        "placeholder='2026-09-25T10:30' size='18'></label></p>"
        "<p><label>Category <select name='category'>%s</select></label> "
        "<label>App <input type='text' name='app' size='12'></label> "
        "<label>Title <input type='text' name='title' size='18'></label></p>"
        "<p><label>Project <select name='project_id'>%s</select></label> "
        "<label>Task <input type='text' name='task' size='14'></label> "
        "<label>Note <input type='text' name='note' size='18'></label></p>"
        "<p><button type='submit'>Add entry</button></p>"
        "</form></div>"
        "<div class='card'><h3>Projects, rates &amp; budgets</h3>"
        "<p class='note'>Set an hourly rate per project; new time entries "
        "use it automatically. Budgets are advisory only and every change "
        "is kept in history.</p>%s</div>"
        "<div class='card'><h3>Client exports</h3>%s</div>"
        % (msg_html, day, day, day, day, timeline_html, sug_table, entries_table,
           lock_html, day,
           _category_options(), _project_options(), projects_html,
           export_html)
    )
    return layout("Timesheet " + day, body, day, active="timesheet")


@app.route("/timesheet/accept", methods=["POST"])
def timesheet_accept():
    from focuscore import timesheet as ts_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        block = {
            "start_ts": request.form.get("start_ts"),
            "end_ts": request.form.get("end_ts"),
            "minutes": float(request.form.get("minutes") or 0),
            "category": request.form.get("category") or "",
            "app": request.form.get("app") or "",
            "title_hint": request.form.get("title_hint") or "",
        }
        project_id = request.form.get("project_id")
        project_id = int(project_id) if project_id else None
    except (TypeError, ValueError):
        project_id, block = None, None
    if block and block["start_ts"] and block["end_ts"] and block["category"]:
        ts_mod.accept_suggestion(
            block, day, project_id=project_id,
            task=(request.form.get("task") or "").strip())
    return redirect("/timesheet?day=" + day)


@app.route("/timesheet/add", methods=["POST"])
def timesheet_add():
    from focuscore import timesheet as ts_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    project_id = request.form.get("project_id")
    try:
        project_id = int(project_id) if project_id else None
    except (TypeError, ValueError):
        project_id = None
    result = ts_mod.add_entry(
        day, (request.form.get("start_ts") or "").strip(),
        (request.form.get("end_ts") or "").strip(),
        request.form.get("category") or "",
        app=(request.form.get("app") or "").strip(),
        title=(request.form.get("title") or "").strip(),
        project_id=project_id,
        task=(request.form.get("task") or "").strip(),
        note=(request.form.get("note") or "").strip())
    if "error" in result:
        return layout("Timesheet",
                      "<div class='card'><p><b>Could not add entry:</b> %s</p>"
                      "<p><a href='/timesheet?day=%s'>Back</a></p></div>"
                      % (escape(result["error"]), day), help_key="timesheet"), 400
    return redirect("/timesheet?day=" + day)


@app.route("/timesheet/edit", methods=["POST"])
def timesheet_edit():
    from focuscore import timesheet as ts_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        entry_id = int(request.form.get("id"))
    except (TypeError, ValueError):
        return redirect("/timesheet?day=" + day)
    project_id = request.form.get("project_id")
    try:
        project_id = int(project_id) if project_id else None
    except (TypeError, ValueError):
        project_id = None
    result = ts_mod.edit_entry(
        entry_id, project_id=project_id,
        task=(request.form.get("task") or "").strip(),
        note=(request.form.get("note") or "").strip(),
        start_ts=(request.form.get("start_ts") or "").strip(),
        end_ts=(request.form.get("end_ts") or "").strip(),
        category=request.form.get("category") or "")
    if "error" in result:
        return layout("Timesheet",
                      "<div class='card'><p><b>Could not edit entry:</b> %s</p>"
                      "<p><a href='/timesheet?day=%s'>Back</a></p></div>"
                      % (escape(result["error"]), day)), 400
    return redirect("/timesheet?day=" + day)


@app.route("/timesheet/delete", methods=["POST"])
def timesheet_delete():
    from focuscore import timesheet as ts_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        result = ts_mod.delete_entry(int(request.form.get("id")))
    except (TypeError, ValueError):
        result = {}
    if "error" in result:
        return layout("Timesheet",
                      "<div class='card'><p><b>Could not delete:</b> %s</p>"
                      "<p><a href='/timesheet?day=%s'>Back</a></p></div>"
                      % (escape(result["error"]), day)), 400
    return redirect("/timesheet?day=" + day)


@app.route("/timesheet/lock", methods=["POST"])
def timesheet_lock():
    from focuscore import timesheet as ts_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    ts_mod.lock_day(day)
    return redirect("/timesheet?day=" + day)


@app.route("/timesheet/project/add", methods=["POST"])
def timesheet_project_add():
    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    name = (request.form.get("name") or "").strip()
    client = (request.form.get("client") or "").strip()
    if name:
        store.add_project(name, client)
    return redirect("/timesheet?day=" + day)


@app.route("/timesheet/project/delete", methods=["POST"])
def timesheet_project_delete():
    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        store.delete_project(int(request.form.get("id")))
    except (TypeError, ValueError):
        pass
    return redirect("/timesheet?day=" + day)


@app.route("/timesheet/export/old")
def timesheet_export_old():
    """Legacy CSV kept for old bookmarks: redirects to the safe client
    export (M4.1 -- the old app/title columns no longer leak by default)."""
    from urllib.parse import urlencode

    day = _parse_day(request.args.get("day"))
    day_from = _parse_day(request.args.get("from")) or day
    day_to = _parse_day(request.args.get("to")) or day
    if not day_from or not day_to:
        abort(404)
    qs = urlencode({"from": day_from, "to": day_to})
    return redirect("/timesheet/export/client?%s" % qs, code=302)


# ------------------------------------------------ Phase 9: budgets ---

def _budget_hbar(label, consumed_text, cap_text, pct, band):
    """One horizontal budget bar; pct is 0..1+ (clamped for display)."""
    width = max(0, min(100, int(round(pct * 100))))
    band_label = {"on_track": "On track", "watch": "Watch",
                  "warning": "Warning", "over": "Over budget"}.get(
                      band, band)
    return (
        "<div class='hbar'><span class='lbl'>%s</span>"
        "<span class='track'><span class='fill %s' style='width:%d%%'>"
        "</span></span>"
        "<span class='val'>%s of %s</span> "
        "<span class='budget-band %s'>%s</span></div>"
        % (escape(label), escape(band), width, escape(consumed_text),
           escape(cap_text), escape(band), escape(band_label)))


def _project_cards_html(day):
    """Project cards with rate, budget bars, and budget/rate forms."""
    from focuscore import budgets as budgets_mod
    from focuscore import money as money_mod

    cards = []
    for p in store.list_projects():
        pid = p["id"]
        week = budgets_mod.budget_status(pid, "week", path=None)
        month = budgets_mod.budget_status(pid, "month", path=None)
        bars = []
        for status, label in ((week, "This week"), (month, "This month")):
            if status["hours"]:
                h = status["hours"]
                bars.append(_budget_hbar(
                    label + " (time)",
                    money_mod.format_duration(h["consumed_seconds"]),
                    money_mod.format_duration(h["cap_seconds"]),
                    h["pct"], h["band"]))
            if status["amount"]:
                a = status["amount"]
                bars.append(_budget_hbar(
                    label + " (billed)",
                    money_mod.format_minor(a["consumed_minor"],
                                           a["currency"]),
                    money_mod.format_minor(a["cap_minor"], a["currency"]),
                    a["pct"], a["band"]))
        bars_html = "".join(bars) or (
            "<p class='fine'>No budget set. Budgets are advisory only -- "
            "they never block your work.</p>")
        # Pacing note (working-day aware, suppressed on rest days).
        pace_notes = []
        for status in (week, month):
            pacing = status.get("pacing")
            if pacing and not pacing.get("suppressed") and pacing.get("band") \
                    not in (None, "none"):
                pace_notes.append(escape(pacing.get("explain", "")))
        pace_html = ("<p class='fine'>%s</p>" % " ".join(pace_notes)
                     if pace_notes else "")
        # Rollover + forecast (Phase 10): computed only, hours only,
        # use-it-or-lose-it. Advisory, never blocking.
        from focuscore import forecast as forecast_mod
        roll_notes = []
        fc_notes = []
        roll_enabled = forecast_mod.project_rollover_enabled(pid)
        for status, ptype in (("This week", "week"),
                               ("This month", "month")):
            roll = forecast_mod.compute_rollover(pid, ptype, date.today())
            if roll["rolled_in_seconds"]:
                roll_notes.append(
                    "%s: base %s + rolled in %s (%d%% cap) = effective %s%s"
                    % (status,
                       money_mod.format_duration(
                           roll["base_cap_seconds"]),
                       money_mod.format_duration(
                           roll["rolled_in_seconds"]),
                       roll["cap_pct"],
                       money_mod.format_duration(
                           roll["effective_cap_seconds"]),
                       " (cap-limited)" if roll["capped"] else ""))
            elif roll["base_cap_seconds"]:
                roll_notes.append(
                    "%s: base %s, no rollover (%s)"
                    % (status, money_mod.format_duration(
                        roll["base_cap_seconds"]),
                       escape(roll["reason"]) if roll["reason"]
                       else "rollover off"))
            fc = forecast_mod.forecast_period(pid, ptype)
            if fc["has_cap"] and fc["verdict"] not in ("no_cap",):
                verdict_colors = {"on_track": "#2e7d32",
                                  "likely_over": "#f9a825",
                                  "over": "#e53935",
                                  "unknown": "#616161"}
                fc_notes.append(
                    "%s forecast: %s <span style='color:%s'>%s</span>"
                    % (ptype, escape(fc["explain"]),
                       verdict_colors.get(fc["verdict"], "#616161"),
                       escape(fc["verdict"].replace("_", " "))))
        roll_html = ("<p class='fine'>Rollover (hours only, computed): %s</p>"
                     % "; ".join(roll_notes) if roll_notes else "")
        fc_html = ("<p class='fine'>%s</p>" % "<br>".join(fc_notes)
                   if fc_notes else "")
        roll_toggle = (
            "<form class='inline' method='post' "
            "action='/timesheet/project/rollover'>"
            "<input type='hidden' name='id' value='%d'>"
            "<input type='hidden' name='day' value='%s'>"
            "<label><input type='checkbox' name='enabled' value='1'%s "
            "onchange='this.form.submit()'> Rollover unused hours into "
            "this project</label></form>"
            % (pid, day, " checked" if roll_enabled else ""))
        rate_minor = p.get("hourly_rate_minor")
        rate_text = (money_mod.format_minor(rate_minor, p.get("rate_currency"))
                     + "/hr" if rate_minor else "no rate set")
        unrated = budgets_mod.count_unrated_entries(pid)
        backfill_html = ""
        if unrated and rate_minor:
            backfill_html = (
                "<form class='inline' method='post' "
                "action='/timesheet/project/backfill-rate' "
                "onsubmit=\"return confirm('Apply the current rate (%s/hr) "
                "to %d unrated entries? This will be recorded.');\">"
                "<input type='hidden' name='id' value='%d'>"
                "<input type='hidden' name='day' value='%s'>"
                "<button type='submit'>Apply rate to %d unrated</button>"
                "</form> "
                % (escape(money_mod.format_minor(rate_minor,
                                                 p.get("rate_currency"))),
                   unrated, pid, day, unrated))

        cards.append(
            "<div class='proj-card'><h4>%s%s</h4>"
            "<p class='fine'>Client: %s &middot; Rate: %s &middot; %s</p>"
            "%s%s"
            "<form method='post' action='/timesheet/project/rate'>"
            "<input type='hidden' name='id' value='%d'>"
            "<input type='hidden' name='day' value='%s'>"
            "<label>Rate/hr <input type='text' name='rate' size='8' "
            "placeholder='95.50'></label>"
            "<button type='submit'>Set rate</button>"
            "<span class='fine'>New entries use it; old entries keep "
            "their rate.</span></form>"
            "<form method='post' action='/timesheet/project/budget'>"
            "<input type='hidden' name='id' value='%d'>"
            "<input type='hidden' name='day' value='%s'>"
            "<label>Week hrs <input type='text' name='week_hours' size='6' "
            "placeholder='20'></label>"
            "<label>Month hrs <input type='text' name='month_hours' size='6' "
            "placeholder='80'></label>"
            "<label>Week %s <input type='text' name='week_amount' size='8' "
            "placeholder='2000'></label>"
            "<label>Month %s <input type='text' name='month_amount' size='8' "
            "placeholder='8000'></label>"
            "<button type='submit'>Set budget</button>"
            "<span class='fine'>Blank = no cap. Every change is kept in "
            "history.</span></form>"
            "%s"
            "<form class='inline' method='post' "
            "action='/timesheet/project/delete' "
            "onsubmit=\"return confirm('Delete this project? Its entries keep "
            "their time but lose the project link.');\">"
            "<input type='hidden' name='id' value='%d'>"
            "<input type='hidden' name='day' value='%s'>"
            "<button type='submit'>Delete project</button></form>"
            "</div>"
            % (escape(p["name"]),
               " (%s)" % escape(p["client"]) if p["client"] else "",
               escape(p["client"] or "-"), escape(rate_text),
               escape(week["summary"]),
               bars_html, pace_html + roll_html + fc_html + roll_toggle,
               pid, day, pid, day,
               escape(money_mod.CURRENCY_SYMBOLS.get(
                   (store.get_setting("currency", "USD") or "USD").upper(),
                   "$")),
               escape(money_mod.CURRENCY_SYMBOLS.get(
                   (store.get_setting("currency", "USD") or "USD").upper(),
                   "$")),
               backfill_html, pid, day))
    add_form = (
        "<form method='post' action='/timesheet/project/add' "
        "style='margin-top:10px'>"
        "<input type='hidden' name='day' value='%s'>"
        "<label>Name <input type='text' name='name' required "
        "size='20'></label> "
        "<label>Client <input type='text' name='client' size='20'></label> "
        "<button type='submit'>Add project</button></form>" % day)
    from focuscore import forecast as _fc
    cap_pct = _fc.get_rollover_cap_pct()
    settings_form = (
        "<form class='inline' method='post' "
        "action='/timesheet/rollover-settings'>"
        "<input type='hidden' name='day' value='%s'>"
        "<label>Rollover cap %% <input type='text' name='rollover_cap_pct' "
        "size='4' value='%d'></label> "
        "<button type='submit'>Save</button> "
        "<span class='fine'>Max unused hours that roll into a project's "
        "next period, as %% of its base hour cap. Hours only -- money never "
        "rolls over. Changing this re-computes every rollover display "
        "because rollover is computed, not stored.</span></form>"
        % (day, cap_pct))
    return "".join(cards) + add_form + settings_form


def _export_panel_html(day, projects):
    """Client export panel: safe defaults, explicit internal opt-in."""
    currency = (store.get_setting("currency", "USD") or "USD").upper()
    proj_opts = ["<option value=''>All projects</option>"] + [
        "<option value='%d'>%s</option>" % (p["id"], escape(p["name"]))
        for p in projects]
    clients = sorted({p["client"] for p in projects if p["client"]})
    client_opts = ["<option value=''>All clients</option>"] + [
        "<option value='%s'>%s</option>" % (escape(c), escape(c))
        for c in clients]
    return (
        "<form method='get' action='/timesheet/export/client'>"
        "<p><label>From <input type='date' name='from' required></label> "
        "<label>To <input type='date' name='to' required></label></p>"
        "<p><label>Project <select name='project_id'>%s</select></label> "
        "<label>Client <select name='client'>%s</select></label></p>"
        "<p><label><input type='checkbox' name='billable_only' value='1'> "
        "Billable only</label> "
        "<label><input type='checkbox' name='include_notes' value='1'> "
        "Include my notes</label> "
        "<label><input type='checkbox' name='show_estimates' value='1'> "
        "Show estimate for unrated time</label></p>"
        "<p><button type='submit'>Download client CSV</button> "
        "<span class='fine'>Safe by default: no app names, window titles, "
        "or URLs.</span></p></form>"
        "<form method='get' action='/timesheet/export.json'>"
        "<p class='fine'>Same filters as above work here too "
        "(from, to, project_id, client, billable_only).</p>"
        "<p><button type='submit' formaction='/timesheet/export.json'>"
        "Download JSON</button> "
        "<button type='submit' formaction='/timesheet/statement'>"
        "Printable statement</button></p></form>"
        "<p class='fine'><a href='/timesheet/export/client?detail=internal' "
        "onclick=\"return confirm('The detailed export includes app names "
        "and window titles, which may contain sensitive information. Use "
        "for your own analysis only. Continue?');\">"
        "Detailed internal CSV (includes app names &amp; window titles)"
        "</a> &mdash; for your own analysis only, never send to clients.</p>"
        "<p class='fine'>Currency: %s. Amounts use confirmed rates only; "
        "unrated time is listed separately.</p>"
        % ("".join(proj_opts), "".join(client_opts),
           escape(currency)))


@app.route("/timesheet/project/rate", methods=["POST"])
def timesheet_project_rate():
    from focuscore import budgets as budgets_mod
    from focuscore import money as money_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    msg = ""
    try:
        pid = int(request.form.get("id"))
        rate_minor = money_mod.parse_rate_to_minor(request.form.get("rate"))
        if rate_minor is None:
            msg = "That rate was not understood -- use a number like 95.50."
        else:
            ok, msg = budgets_mod.set_project_rate(pid, rate_minor)
    except (TypeError, ValueError):
        msg = "Could not save the rate."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/project/budget", methods=["POST"])
def timesheet_project_budget():
    from focuscore import budgets as budgets_mod
    from focuscore import money as money_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    msg = "Budget saved."
    try:
        pid = int(request.form.get("id"))
        week_s = money_mod.parse_hours_to_seconds(
            request.form.get("week_hours"))
        month_s = money_mod.parse_hours_to_seconds(
            request.form.get("month_hours"))
        week_a = money_mod.parse_rate_to_minor(request.form.get("week_amount"))
        month_a = money_mod.parse_rate_to_minor(
            request.form.get("month_amount"))
        # Blank fields mean "leave unchanged"; an explicit 0 removes a cap.
        for period_type, cap_s, cap_a in (("week", week_s, week_a),
                                         ("month", month_s, month_a)):
            if cap_s is None and cap_a is None:
                continue
            ok, m = budgets_mod.set_budget(pid, period_type, cap_s, cap_a)
            if not ok:
                msg = m
    except (TypeError, ValueError):
        msg = "Could not save the budget."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/project/backfill-rate", methods=["POST"])
def timesheet_project_backfill_rate():
    """Explicit, user-confirmed backfill (Qwen M1.5). The confirm() dialog
    in the form IS the explicit confirmation; the action is audit-logged."""
    from focuscore import budgets as budgets_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        pid = int(request.form.get("id"))
        rate_minor, currency = budgets_mod.get_project_rate(pid)
        if not rate_minor:
            msg = "Set a project rate first."
        else:
            n = budgets_mod.backfill_rate(pid, rate_minor, currency)
            msg = "%d entries marked as confirmed at the current rate." % n
    except (TypeError, ValueError):
        msg = "Could not apply the rate."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/project/rollover", methods=["POST"])
def timesheet_project_rollover():
    """Per-project rollover toggle (Phase 10, Q6: hours only)."""
    from focuscore import forecast as forecast_mod

    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        pid = int(request.form.get("id"))
        forecast_mod.set_rollover_enabled(
            pid, request.form.get("enabled") == "1")
        msg = "Rollover preference saved."
    except (TypeError, ValueError):
        msg = "Could not save the rollover preference."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


@app.route("/timesheet/rollover-settings", methods=["POST"])
def timesheet_rollover_settings():
    """Global rollover cap percentage (Phase 10, Q7: integer 0-100)."""
    day = _parse_day(request.form.get("day")) or date.today().isoformat()
    try:
        pct = int((request.form.get("rollover_cap_pct") or "").strip())
        if 0 <= pct <= 100:
            store.set_setting("rollover_cap_pct", str(pct))
            msg = "Rollover cap set to %d%%." % pct
        else:
            msg = "Use a whole number from 0 to 100."
    except (TypeError, ValueError):
        msg = "Use a whole number from 0 to 100."
    return redirect("/timesheet?day=" + day + "&msg=" + msg.replace(" ", "+"))


def _export_filters():
    """Shared query-string parsing for the three export endpoints."""
    day_from = _parse_day(request.args.get("from"))
    day_to = _parse_day(request.args.get("to"))
    try:
        project_id = request.args.get("project_id")
        project_id = int(project_id) if project_id else None
    except (TypeError, ValueError):
        project_id = None
    client = (request.args.get("client") or "").strip() or None
    billable_only = request.args.get("billable_only") == "1"
    include_notes = request.args.get("include_notes") == "1"
    show_estimates = request.args.get("show_estimates") == "1"
    return (day_from, day_to, project_id, client, billable_only,
            include_notes, show_estimates)


def json_dumps(payload):
    """Deterministic JSON for exports (Qwen constraint F)."""
    return _json.dumps(payload, sort_keys=True, separators=(",", ":"))


@app.route("/timesheet/export/client")
def timesheet_export_client():
    from focuscore import exports as exports_mod

    (day_from, day_to, project_id, client, billable_only,
     include_notes, _show) = _export_filters()
    if not day_from or not day_to or day_from > day_to:
        abort(404)
    internal = request.args.get("detail") == "internal"
    rows = exports_mod.build_export_rows(
        day_from, day_to, project_id=project_id, client=client,
        billable_only=billable_only, include_app_details=internal,
        include_notes=include_notes)
    manifest = exports_mod.redaction_manifest(
        include_app_details=internal, include_notes=include_notes)
    if internal:
        text = exports_mod.rows_to_detailed_csv(rows)
        kind = "detailed-csv"
        filename = "focuscore-timesheet-internal-%s-to-%s.csv"
    else:
        text = exports_mod.rows_to_csv(rows, manifest)
        kind = "client-csv"
        filename = "focuscore-timesheet-%s-to-%s.csv"
    exports_mod.log_export_generated(
        kind,
        {"from": day_from, "to": day_to, "project_id": project_id,
         "client": client, "billable_only": billable_only},
        manifest, len(rows))
    return Response(
        text, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=%s"
                 % (filename % (day_from, day_to))})


@app.route("/timesheet/export.json")
def timesheet_export_json():
    from focuscore import budgets as budgets_mod
    from focuscore import exports as exports_mod

    (day_from, day_to, project_id, client, billable_only,
     include_notes, show_estimates) = _export_filters()
    if not day_from or not day_to or day_from > day_to:
        abort(404)
    currency = (store.get_setting("currency", "USD") or "USD").upper()
    rows = exports_mod.build_export_rows(
        day_from, day_to, project_id=project_id, client=client,
        billable_only=billable_only, include_app_details=False,
        include_notes=include_notes)
    manifest = exports_mod.redaction_manifest(include_notes=include_notes)
    totals = exports_mod.compute_totals(
        rows, project_id=project_id, currency=currency,
        include_estimates=show_estimates)
    budget_decl = None
    if project_id:
        start_day, _ = budgets_mod.period_bounds("month", day_from)
        cap = budgets_mod.get_cap_for_period(project_id, "month", start_day)
        if cap:
            budget_decl = cap
    payload = exports_mod.build_json_payload(
        rows, totals,
        {"from": day_from, "to": day_to, "project_id": project_id,
         "client": client, "billable_only": billable_only},
        manifest, budget_decl=budget_decl, currency=currency)
    exports_mod.log_export_generated(
        "json",
        {"from": day_from, "to": day_to, "project_id": project_id,
         "client": client, "billable_only": billable_only},
        manifest, len(rows))
    return Response(
        json_dumps(payload), mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=%s"
                 % ("focuscore-timesheet-%s-to-%s.json"
                    % (day_from, day_to))})


@app.route("/timesheet/statement")
def timesheet_statement():
    from focuscore import budgets as budgets_mod
    from focuscore import exports as exports_mod

    (day_from, day_to, project_id, client, billable_only,
     include_notes, show_estimates) = _export_filters()
    if not day_from or not day_to or day_from > day_to:
        abort(404)
    currency = (store.get_setting("currency", "USD") or "USD").upper()
    rows = exports_mod.build_export_rows(
        day_from, day_to, project_id=project_id, client=client,
        billable_only=billable_only, include_app_details=False,
        include_notes=include_notes)
    manifest = exports_mod.redaction_manifest(include_notes=include_notes)
    totals = exports_mod.compute_totals(
        rows, project_id=project_id, currency=currency,
        include_estimates=show_estimates)
    budget_decl = None
    project_name = ""
    if project_id:
        start_day, _ = budgets_mod.period_bounds("month", day_from)
        cap = budgets_mod.get_cap_for_period(project_id, "month", start_day)
        if cap:
            budget_decl = cap
        for p in store.list_projects():
            if p["id"] == project_id:
                project_name = p["name"]
                break
    page_html = exports_mod.rows_to_statement_html(
        rows, totals,
        {"from": day_from, "to": day_to, "project_id": project_id,
         "client": client, "billable_only": billable_only},
        manifest, budget_decl=budget_decl, currency=currency,
        project_name=project_name,
        client_name=client or "")
    exports_mod.log_export_generated(
        "statement",
        {"from": day_from, "to": day_to, "project_id": project_id,
         "client": client, "billable_only": billable_only},
        manifest, len(rows))
    return Response(page_html, mimetype="text/html")


# ------------------------------------------------ Phase 10: invoicing ---

def _invoice_status_badge(status):
    colors = {"draft": "#616161", "sent": "#1565c0", "paid": "#2e7d32",
              "void": "#c62828"}
    return ("<span class='pill' style='background:%s'>%s</span>"
            % (colors.get(status, "#616161"), escape(status.upper())))


@app.route("/invoices")
def invoices_page():
    """List invoices with derived balance/overdue info (Q4/Q15)."""
    from focuscore import invoices as inv_mod
    from focuscore import money as money_mod

    status = request.args.get("status") or ""
    msg = request.args.get("msg") or ""
    invoices = inv_mod.list_invoices(
        status=status if status in inv_mod.STATUSES else None)
    rows = []
    outstanding = 0
    for inv in invoices:
        currency = (inv.get("currency") or "USD").upper()
        if inv["status"] == "sent":
            outstanding += inv["balance_minor"]
        title = inv["number"] or "DRAFT #%d" % inv["id"]
        overdue = " <strong>OVERDUE</strong>" if inv["overdue"] else ""
        rows.append(
            "<tr><td><a href='/invoices/%d'>%s</a></td>"
            "<td>%s</td><td>%s</td><td>%s</td>"
            "<td style='text-align:right'>%s</td>"
            "<td style='text-align:right'>%s</td><td>%s</td></tr>"
            % (inv["id"], escape(title),
               escape(inv.get("project_name") or ""),
               escape(inv.get("client") or ""),
               _invoice_status_badge(inv["status"]),
               money_mod.format_minor(inv.get("total_minor") or 0, currency),
               money_mod.format_minor(inv["balance_minor"], currency),
               overdue))
    body = (
        "<div class='card'><h3>Invoices</h3>"
        "%s"
        "<p><a class='btn' href='/invoices/new'>New invoice</a> "
        "&nbsp;<a href='/invoices'>All</a>"
        " &middot; <a href='/invoices?status=draft'>Drafts</a>"
        " &middot; <a href='/invoices?status=sent'>Sent</a>"
        " &middot; <a href='/invoices?status=paid'>Paid</a>"
        " &middot; <a href='/invoices?status=void'>Void</a></p>"
        "<p class='fine'>Outstanding on sent invoices: <strong>%s</strong>. "
        "Invoices are numbered when sent; sent invoices are frozen and "
        "can only be voided, never edited.</p>"
        "<table class='tbl'><tr><th>Invoice</th><th>Project</th>"
        "<th>Client</th><th>Status</th><th style='text-align:right'>Total</th>"
        "<th style='text-align:right'>Balance</th><th></th></tr>%s</table>"
        "</div>"
        % ("<p class='msg'>%s</p>" % escape(msg) if msg else "",
           money_mod.format_minor(outstanding),
           "".join(rows) or "<tr><td colspan='7'>No invoices yet.</td></tr>"))
    return layout("Invoices", body, active="invoices")


@app.route("/invoices/new")
def invoice_new_page():
    """Pick uninvoiced entries for a new draft invoice."""
    from focuscore import invoices as inv_mod
    from focuscore import money as money_mod

    try:
        project_id = int(request.args.get("project_id") or 0) or None
    except (TypeError, ValueError):
        project_id = None
    day_from = _parse_day(request.args.get("from")) or (
        date.today().replace(day=1).isoformat())
    day_to = _parse_day(request.args.get("to")) or date.today().isoformat()
    projects = store.list_projects()

    filter_form = (
        "<form method='get' action='/invoices/new' class='inline'>"
        "<label>Project <select name='project_id'>"
        "<option value=''>-- choose --</option>%s</select></label> "
        "<label>From <input type='date' name='from' value='%s'></label> "
        "<label>To <input type='date' name='to' value='%s'></label> "
        "<button type='submit'>Show entries</button></form>"
        % ("".join(
            "<option value='%d'%s>%s</option>"
            % (p["id"], " selected" if p["id"] == project_id else "",
               escape(p["name"]))
            for p in projects),
           escape(day_from), escape(day_to)))

    entries_html = ""
    if project_id:
        entries, unrated = inv_mod.uninvoiced_entries(
            project_id, day_from, day_to)
        if unrated:
            entries_html += (
                "<p class='fine'>%d billable entr%s ha%s no rate yet and "
                "cannot be invoiced. Set a project rate first (Timesheet "
                "page), then come back.</p>"
                % (unrated, "y" if unrated == 1 else "ies",
                   "s" if unrated == 1 else "ve"))
        if entries:
            rows = []
            for e in entries:
                seconds = int(round((e["minutes"] or 0) * 60))
                amount = money_mod.amount_minor_for(
                    seconds, e["hourly_rate_minor"]) or 0
                currency = (e["rate_currency"] or "USD").upper()
                label = (e["task"] or e["category"] or "Work")
                badge = (" <span class='pill'>%s</span>"
                         % escape(e["rate_status"] or ""))
                rows.append(
                    "<tr><td><input type='checkbox' name='entry_id' "
                    "value='%d' checked></td><td>%s</td><td>%s%s</td>"
                    "<td style='text-align:right'>%s</td>"
                    "<td style='text-align:right'>%s</td></tr>"
                    % (e["id"], escape(e["day"]), escape(label), badge,
                       money_mod.format_duration(seconds),
                       money_mod.format_minor(amount, currency)))
            entries_html += (
                "<form method='post' action='/invoices/create'>"
                "<input type='hidden' name='project_id' value='%d'>"
                "<input type='hidden' name='from' value='%s'>"
                "<input type='hidden' name='to' value='%s'>"
                "<table class='tbl'><tr><th></th><th>Day</th><th>Entry</th>"
                "<th style='text-align:right'>Time</th>"
                "<th style='text-align:right'>Amount</th></tr>%s</table>"
                "<p><label>Notes<br><textarea name='notes' rows='2' "
                "cols='60'></textarea></label></p>"
                "<p><label>Tax %% <input type='text' name='tax_pct' size='4' "
                "placeholder='0'></label> "
                "<label>Discount %% <input type='text' name='discount_pct' "
                "size='4' placeholder='0'></label> "
                "<span class='fine'>Blank = project defaults.</span></p>"
                "<button type='submit'>Create draft invoice</button></form>"
                % (project_id, escape(day_from), escape(day_to),
                   "".join(rows)))
        else:
            entries_html = ("<p class='fine'>No uninvoiced billable entries "
                            "with a rate in that range.</p>")

    body = ("<div class='card'><h3>New invoice</h3>"
            "<p class='fine'>A draft is created first: no invoice number "
            "until you send it, and drafts can be edited freely. One "
            "currency per invoice.</p>"
            "%s%s</div>" % (filter_form, entries_html))
    return layout("New invoice", body, active="invoices")


@app.route("/invoices/create", methods=["POST"])
def invoice_create():
    from focuscore import invoices as inv_mod

    try:
        pid = int(request.form.get("project_id"))
        day_from = _parse_day(request.form.get("from")) or ""
        day_to = _parse_day(request.form.get("to")) or ""
        entry_ids = [int(v) for v in request.form.getlist("entry_id")]
        notes = request.form.get("notes") or ""
        tax_raw = (request.form.get("tax_pct") or "").strip()
        disc_raw = (request.form.get("discount_pct") or "").strip()
        tax_pct = int(tax_raw) if tax_raw else None
        disc_pct = int(disc_raw) if disc_raw else None
        invoice_id = inv_mod.create_invoice(
            pid, day_from, day_to, entry_ids or None, notes=notes,
            tax_pct=tax_pct, discount_pct=disc_pct)
        return redirect("/invoices/%d" % invoice_id)
    except inv_mod.InvoiceError as exc:
        msg = str(exc)
    except (TypeError, ValueError):
        msg = "Could not create the invoice."
    return redirect("/invoices?msg=" + msg.replace(" ", "+"))


def _invoice_detail_body(detail):
    """HTML for one invoice (shared by the detail page)."""
    from focuscore import money as money_mod

    inv = detail["invoice"]
    lines = detail["lines"]
    payments = detail["payments"]
    t = inv["totals"]
    currency = (inv["currency"] or "USD").upper()
    title = inv["number"] or "DRAFT #%d" % inv["id"]

    line_rows = []
    for line in lines:
        rate_text = (money_mod.format_minor(line["rate_minor_units"], currency)
                     + "/hr" if line["rate_minor_units"] else "--")
        remove = ""
        if inv["status"] == "draft":
            remove = (
                "<form class='inline' method='post' "
                "action='/invoices/%d/remove-line' "
                "onsubmit=\"return confirm('Remove this line?');\">"
                "<input type='hidden' name='line_id' value='%d'>"
                "<button type='submit'>Remove</button></form>"
                % (inv["id"], line["id"]))
        locked = ""
        if line["timesheet_entry_id"]:
            locked = (" <span class='pill' title='Editing this timesheet "
                      "entry will not change this invoice line.'>"
                      "locked</span>")
        line_rows.append(
            "<tr><td>%s</td><td>%s%s</td>"
            "<td style='text-align:right'>%s</td>"
            "<td style='text-align:right'>%s</td>"
            "<td style='text-align:right'>%s</td><td>%s</td></tr>"
            % (escape(line["entry_date"] or ""),
               escape(line["description"] or ""),
               locked,
               money_mod.format_hours(line["hours_minor_units"] or 0),
               rate_text,
               money_mod.format_minor(line["amount_minor_units"] or 0,
                                      currency),
               remove))

    totals_html = (
        "<table class='tbl'><tr><td>Subtotal</td>"
        "<td style='text-align:right'>%s</td></tr>"
        "<tr><td>Discount (%d%%)</td><td style='text-align:right'>%s</td></tr>"
        "<tr><td>Tax (%d%%)</td><td style='text-align:right'>%s</td></tr>"
        "<tr><th>Total (%s)</th><th style='text-align:right'>%s</th></tr>"
        "<tr><td>Payments received</td><td style='text-align:right'>%s</td></tr>"
        "<tr><th>Balance due</th><th style='text-align:right'>%s</th></tr>"
        "</table>"
        % (money_mod.format_minor(t["subtotal_minor"], currency),
           inv["discount_pct"],
           money_mod.format_minor(t["discount_minor"], currency),
           inv["tax_pct"],
           money_mod.format_minor(t["tax_minor"], currency),
           escape(currency),
           money_mod.format_minor(t["total_minor"], currency),
           money_mod.format_minor(inv["paid_minor"], currency),
           money_mod.format_minor(inv["balance_minor"], currency)))
    if inv["overpaid_minor"]:
        totals_html += (
            "<p class='msg'>Payments exceed the invoice total by %s. "
            "Consider issuing a credit note or refund. (Informational only.)"
            "</p>"
            % money_mod.format_minor(inv["overpaid_minor"], currency))
    if inv["overdue"]:
        totals_html += ("<p class='msg'><strong>Overdue</strong> since %s.</p>"
                          % escape(inv["due_date"] or ""))

    pay_rows = "".join(
        "<tr><td>%s</td><td style='text-align:right'>%s</td><td>%s</td></tr>"
        % (escape(p["paid_date"] or ""),
           money_mod.format_minor(p["amount_minor"] or 0, currency),
           escape(p["note"] or ""))
        for p in payments)

    actions = ""
    if inv["status"] == "draft":
        actions = (
            "<h4>Edit draft</h4>"
            "<form class='inline' method='post' "
            "action='/invoices/%d/tax-discount'>"
            "<label>Tax %% <input type='text' name='tax_pct' size='4' "
            "value='%d'></label> "
            "<label>Discount %% <input type='text' name='discount_pct' "
            "size='4' value='%d'></label> "
            "<button type='submit'>Save</button></form> "
            "<form class='inline' method='post' "
            "action='/invoices/%d/add-line'>"
            "<label>Description <input type='text' name='description' "
            "size='24'></label> "
            "<label>Hours <input type='text' name='hours' size='6'></label> "
            "<label>Rate <input type='text' name='rate' size='8'></label> "
            "<button type='submit'>Add manual line</button></form>"
            "<form class='inline' method='post' "
            "action='/invoices/%d/send' "
            "onsubmit=\"return confirm('Send this invoice? "
            "It will be numbered and frozen.');\">"
            "<button type='submit'>Send invoice</button></form>"
            % (inv["id"], inv["discount_pct"], inv["tax_pct"],
               inv["id"], inv["id"]))
    if inv["status"] == "sent":
        actions = (
            "<h4>Record payment</h4>"
            "<form class='inline' method='post' "
            "action='/invoices/%d/pay'>"
            "<label>Date <input type='date' name='paid_date' value='%s'></label> "
            "<label>Amount <input type='text' name='amount' size='10'></label> "
            "<label>Note <input type='text' name='note' size='20'></label> "
            "<button type='submit'>Record</button></form> "
            % (inv["id"], date.today().isoformat()))
        if inv["balance_minor"] == 0 and t["total_minor"] == 0:
            actions += (
                "<form class='inline' method='post' "
                "action='/invoices/%d/mark-paid'>"
                "<button type='submit'>Mark paid (zero total)</button></form> "
                % inv["id"])
        actions += (
            "<form class='inline' method='post' "
            "action='/invoices/%d/reissue' "
            "onsubmit=\"return confirm('Void this invoice and create "
            "a corrected draft?');\">"
            "<label>Reason <select name='reason'>"
            "<option>Reissued with corrections</option>"
            "<option>Client dispute</option><option>Duplicate</option>"
            "<option>Other</option></select></label> "
            "<button type='submit'>Void &amp; reissue</button></form>"
            % inv["id"])
    if inv["status"] in ("draft", "sent"):
        actions += (
            "<form class='inline' method='post' "
            "action='/invoices/%d/void' "
            "onsubmit=\"return confirm('Void this invoice? This cannot be undone.');\">"
            "<label>Reason <select name='reason'>"
            "<option>Reissued with corrections</option>"
            "<option>Client dispute</option><option>Duplicate</option>"
            "<option>Other</option></select></label> "
            "<button type='submit'>Void invoice</button></form>"
            % inv["id"])
    if inv["status"] == "void":
        note = "Reason: %s." % escape(inv["void_reason"] or "")
        if inv["superseded_by_invoice_id"]:
            note += (" Reissued as <a href='/invoices/%d'>invoice #%d</a>."
                     % (inv["superseded_by_invoice_id"],
                        inv["superseded_by_invoice_id"]))
        actions = "<p class='fine'>%s</p>" % note
    if inv.get("supersedes_invoice_id"):
        actions += ("<p class='fine'>Reissue of "
                    "<a href='/invoices/%d'>invoice #%d</a>.</p>"
                    % (inv["supersedes_invoice_id"],
                       inv["supersedes_invoice_id"]))

    frozen_note = ("<p class='fine'>Sent invoices are frozen: totals, lines, "
                   "tax and discount cannot be changed. Void and reissue to "
                   "correct.</p>" if inv["is_frozen"] and inv["status"] != "void"
                   else "")
    return (
        "<div class='card'><h3>%s %s</h3>"
        "<p class='fine'>Project: %s &middot; Client: %s &middot; "
        "Issued: %s &middot; Due: %s</p>"
        "%s"
        "<table class='tbl'><tr><th>Date</th><th>Description</th>"
        "<th style='text-align:right'>Hours</th>"
        "<th style='text-align:right'>Rate</th>"
        "<th style='text-align:right'>Amount</th><th></th></tr>%s</table>"
        "%s"
        "<h4>Payments</h4>%s"
        "<p><a class='btn' href='/invoices/%d/print' target='_blank'>"
        "Print / save PDF</a></p>"
        "%s%s</div>"
        % (escape(title), _invoice_status_badge(inv["status"]),
           escape(inv.get("project_name") or ""),
           escape(inv.get("client") or ""),
           escape(inv.get("issued_at") or "--"),
           escape(inv.get("due_date") or "--"),
           frozen_note,
           "".join(line_rows) or "<tr><td colspan='6'>No lines yet.</td></tr>",
           totals_html,
           ("<table class='tbl'><tr><th>Date</th>"
            "<th style='text-align:right'>Amount</th><th>Note</th></tr>%s</table>"
            % pay_rows) if pay_rows else "<p class='fine'>No payments yet.</p>",
           inv["id"], actions,
           ("<p class='fine'>Notes: %s</p>" % escape(inv["notes"])
            if inv.get("notes") else "")))


@app.route("/invoices/<int:invoice_id>")
def invoice_detail_page(invoice_id):
    from focuscore import invoices as inv_mod

    msg = request.args.get("msg") or ""
    try:
        detail = inv_mod.get_invoice(invoice_id)
    except inv_mod.InvoiceError:
        abort(404)
    body = ("<p><a href='/invoices'>&larr; All invoices</a></p>"
            + ("<p class='msg'>%s</p>" % escape(msg) if msg else "")
            + _invoice_detail_body(detail))
    return layout("Invoice", body, active="invoices")


def _invoice_action_redirect(invoice_id, fn, *args):
    """Run an invoice action; on error redirect back with the message."""
    from focuscore import invoices as inv_mod

    try:
        fn(*args)
        msg = "Done."
    except inv_mod.InvoiceError as exc:
        msg = str(exc)
    except (TypeError, ValueError):
        msg = "Could not complete the action."
    return redirect("/invoices/%d?msg=%s"
                    % (invoice_id, msg.replace(" ", "+")))


@app.route("/invoices/<int:invoice_id>/add-line", methods=["POST"])
def invoice_add_line(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.add_manual_line, invoice_id,
        request.form.get("description"), request.form.get("hours"),
        request.form.get("rate"))


@app.route("/invoices/<int:invoice_id>/remove-line", methods=["POST"])
def invoice_remove_line(invoice_id):
    from focuscore import invoices as inv_mod

    try:
        line_id = int(request.form.get("line_id"))
    except (TypeError, ValueError):
        return redirect("/invoices/%d?msg=%s"
                        % (invoice_id, "Bad+line+id."))
    return _invoice_action_redirect(
        invoice_id, inv_mod.remove_line, invoice_id, line_id)


@app.route("/invoices/<int:invoice_id>/tax-discount", methods=["POST"])
def invoice_tax_discount(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.set_tax_discount, invoice_id,
        request.form.get("tax_pct"), request.form.get("discount_pct"))


@app.route("/invoices/<int:invoice_id>/send", methods=["POST"])
def invoice_send(invoice_id):
    from focuscore import invoices as inv_mod

    try:
        number = inv_mod.send_invoice(invoice_id)
        msg = "Sent as %s." % number
    except inv_mod.InvoiceError as exc:
        msg = str(exc)
    return redirect("/invoices/%d?msg=%s"
                    % (invoice_id, msg.replace(" ", "+")))


@app.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
def invoice_pay(invoice_id):
    from focuscore import invoices as inv_mod
    from focuscore import money as money_mod

    try:
        amount_minor = money_mod.parse_rate_to_minor(
            request.form.get("amount"))
        if amount_minor is None:
            raise inv_mod.InvoiceError("Amount was not understood.")
        became_paid, balance, overpaid = inv_mod.record_payment(
            invoice_id, request.form.get("paid_date"), amount_minor,
            request.form.get("note") or "")
        if became_paid:
            msg = "Payment recorded. Invoice is now paid."
        elif overpaid:
            msg = ("Payment recorded. Overpaid by %s; consider a credit "
                   "note or refund."
                   % money_mod.format_minor(overpaid))
        else:
            msg = "Payment recorded."
    except inv_mod.InvoiceError as exc:
        msg = str(exc)
    return redirect("/invoices/%d?msg=%s"
                    % (invoice_id, msg.replace(" ", "+")))


@app.route("/invoices/<int:invoice_id>/mark-paid", methods=["POST"])
def invoice_mark_paid(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.mark_paid, invoice_id)


@app.route("/invoices/<int:invoice_id>/void", methods=["POST"])
def invoice_void(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.void_invoice, invoice_id,
        request.form.get("reason") or "Other")


@app.route("/invoices/<int:invoice_id>/reissue", methods=["POST"])
def invoice_reissue(invoice_id):
    from focuscore import invoices as inv_mod

    try:
        new_id = inv_mod.void_and_reissue(
            invoice_id, request.form.get("reason") or "Other")
        return redirect("/invoices/%d?msg=%s"
                        % (new_id, "Reissued+as+a+new+draft."))
    except inv_mod.InvoiceError as exc:
        msg = str(exc)
    return redirect("/invoices/%d?msg=%s"
                    % (invoice_id, msg.replace(" ", "+")))


@app.route("/invoices/<int:invoice_id>/print")
def invoice_print(invoice_id):
    """Standalone printable invoice (Q10: CSP, zero JS, auto-escaped)."""
    from focuscore import invoices as inv_mod

    try:
        page_html = inv_mod.render_invoice_html(invoice_id)
    except inv_mod.InvoiceError:
        abort(404)
    return Response(page_html, mimetype="text/html")


# -------------------------------------------------------------- backup ---

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


# -------------------------------------------------------------- update ---

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


# ------------------------------------------------------------ coaching ---

def _pulse_cell_color(pulse, minutes):
    """(background, text) color for a heatmap cell."""
    if minutes <= 0 or pulse is None:
        return "#f0f0f0", "#999"
    if pulse >= 70:
        return "#2e7d32", "#fff"
    if pulse >= 55:
        return "#81c784", "#222"
    if pulse >= 40:
        return "#ffb74d", "#222"
    return "#e53935", "#fff"


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


_SCORE_CELL_COLORS = {2: ("#2e7d32", "#fff"), 1: ("#81c784", "#222"),
                      0: ("#e0e0e0", "#222"), -1: ("#ef9a9a", "#222"),
                      -2: ("#e53935", "#fff")}

_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _delta_str(value):
    if value is None:
        return "--"
    if value > 0:
        return "&#9650; %.1f" % abs(value)
    if value < 0:
        return "&#9660; %.1f" % abs(value)
    return "= 0"


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


def _rule_schedule_text(rule):
    days = (rule.get("days") or "all").strip().lower()
    start = (rule.get("start_time") or "").strip()
    end = (rule.get("end_time") or "").strip()
    if days == "all" and not start:
        return "always"
    if start and end:
        span = "%s-%s" % (start, end)
    else:
        span = "all day"
    if days == "all":
        return span
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    try:
        ds = [names[int(d)] for d in days.split(",") if d.strip()]
        return "%s, %s" % (", ".join(ds), span)
    except (ValueError, IndexError):
        return span


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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Focus Core dashboard.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default 127.0.0.1: this PC only). "
                             "Use --host 0.0.0.0 to open it on your home "
                             "Wi-Fi (e.g. from your phone).")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: binding to %s exposes your tracked data to the "
              "local network. Anyone on your Wi-Fi could open the dashboard."
              % args.host)
    app.run(host=args.host, port=args.port)
