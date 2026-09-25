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
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, Response, abort, redirect, request  # noqa: E402

from focuscore import store  # noqa: E402
from focuscore.home import pulse_band as _pulse_band  # noqa: E402
from focuscore.ingest import ActivityWatchError  # noqa: E402
from focuscore.pipeline import run_day  # noqa: E402
from focuscore.scoring import UI_LABELS, productivity_pulse  # noqa: E402
from focuscore.taxonomy import host_of  # noqa: E402

app = Flask(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ONBOARDED_FLAG = PROJECT_ROOT / ".onboarded"

# Top navigation: (key, label, href). "Review" jumps to today's
# uncategorized activities when a day is known.
NAV_LINKS = [
    ("home", "Home", "/"),
    ("timesheet", "Timesheet", "/timesheet"),
    ("report", "Report", "/report"),
    ("coaching", "Coaching", "/coaching"),
    ("focus", "Focus", "/focus"),
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


def layout(title, body, day=None, refresh=300, active="home"):
    """Page shell: nav bar, shared stylesheet, footer. No inline CSS --
    everything visual lives in dashboard/static/style.css (offline)."""
    links = []
    for key, label, href in NAV_LINKS:
        url = href
        if key == "review" and day:
            url = href + "?day=" + day
        cls = " class='active'" if key == active else ""
        links.append("<a href='%s'%s>%s</a>" % (url, cls, label))
    nav = "<nav class='topnav'>" + "".join(links) + "</nav>"
    footer = (
        "<footer>Focus Core &middot; your data never leaves this PC "
        "&middot; <a href='/welcome/restart'>Take the tour again</a></footer>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='refresh' content='%d'>"
        "<title>%s &middot; Focus Core</title>"
        "<link rel='stylesheet' href='/static/style.css'>"
        "<link rel='icon' href='/static/icon.png'></head>"        "<body>%s<h1>%s</h1>"
        "<div class='sub'>Focus Core &middot; Phase 5</div>%s%s</body></html>"
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
                "Start ActivityWatch and reload this page. "
                "Your saved data is still shown below.</p></div>"
                % escape(str(exc)))
    summary = store.get_day_summary(day)
    pulse = productivity_pulse(summary["seconds_by_level"])
    total = summary["total_seconds"]

    if total <= 0:
        body = (
            aw_note +
            "<div class='card'><p>No tracked data for this day yet.</p>"
            "<p class='note'>Run <code>python -m focuscore.pipeline --day %s</code> "
            "(add <code>--demo</code> to try it without ActivityWatch).</p></div>" % day
        )
        return layout("Day " + day, body, day)

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
    return layout("Day " + day, body, day)


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

    today = date.today().isoformat()
    try:
        run_day(date.today())
    except ActivityWatchError:
        pass  # the "tracker isn't sending data" card covers this

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

    cards = home_mod.attention_cards()
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
    body = (
        "<div class='card'><div class='steps'>%s</div>"
        "<div class='big-emoji'>%s</div><h2>%s</h2><p>%s</p>%s%s</div>"
        % (dots, info["emoji"], escape(info["title"]),
           escape(info["text"]), check_html, action))
    return layout("Welcome", body, refresh=3600)


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
                      % escape(error)), 400
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
                      % escape(error)), 400
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
    from focuscore import focus as focus_mod

    active = focus_mod.get_active_session()
    streak = focus_mod.current_streak()
    streak_html = (
        "<div class='streak'>%d-day focus streak</div>" % streak if streak
        else "<p class='note'>No focus streak yet -- "
             "complete a session to start one.</p>")

    if active:
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
            "%s"
            % (escape(active["label"]), _fmt_countdown(remaining),
               status_line, active["planned_minutes"],
               escape(active["block_level"]), streak_html)
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

    body = (
        "%s"
        "<div class='card'><h3>Start a focus session</h3>"
        "<form method='post' action='/focus/start'>"
        "<p><label>Label <input type='text' name='label' required "
        "placeholder='e.g. Deep work' size='28'></label></p>"
        "<p><label><input type='radio' name='preset' value='25'> 25 min</label> "
        "<label><input type='radio' name='preset' value='50' checked> 50 min</label> "
        "<label><input type='radio' name='preset' value='90'> 90 min</label> "
        "<label><input type='radio' name='preset' value='custom'> custom "
        "<input type='number' name='custom_minutes' min='1' max='480' "
        "style='width:70px' placeholder='min'></label></p>"
        "<p><label><input type='radio' name='block_level' value='strict' "
        "checked> Strict -- block Personal (-1) and Distracting (-2)</label><br>"
        "<label><input type='radio' name='block_level' value='lenient'> "
        "Lenient -- block only Distracting (-2)</label></p>"
        "<p><button type='submit'>Start session</button></p>"
        "</form>"
        "<p class='note'>Blocking starts automatically when the session "
        "starts -- no extra step needed.</p></div>"
        "<div class='card'><h3>Past sessions</h3>%s</div>"
        % (streak_html, past_table)
    )
    return layout("Focus sessions", body, active="focus")


@app.route("/focus/start", methods=["POST"])
def focus_start():
    from focuscore import focus as focus_mod

    label = (request.form.get("label") or "").strip()
    preset = request.form.get("preset") or "50"
    block_level = request.form.get("block_level") or "strict"
    if preset == "custom":
        minutes = request.form.get("custom_minutes")
    else:
        minutes = preset
    result = focus_mod.start_session(label, minutes, block_level=block_level)
    if "error" not in result:
        # Blocking starts with the session -- no second manual step.
        from focuscore.blocker import ensure_guard_running
        ensure_guard_running()
    if "error" in result:
        return layout("Focus sessions",
                      "<div class='card'><p><b>Could not start:</b> %s</p>"
                      "<p><a href='/focus'>Back</a></p></div>"
                      % escape(result["error"])), 400
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
    return layout("Session summary", body)


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
        if entry["locked"]:
            actions = "<span class='note'>locked</span>"
            row_form = (
                "<td>%s - %s<br><span class='note'>%.1f min</span></td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='title-cell' title='%s'>%s</td>"
                "<td>%s</td><td>%s</td>"
                % (escape(entry["start_ts"][11:]),
                   escape(entry["end_ts"][11:]), entry["minutes"],
                   escape(entry["category"]), escape(entry["app"]),
                   escape(entry["project_name"] or "-"),
                   escape(entry["task"] or "-"),
                   escape(entry["note"]), escape(entry["note"][:40]),
                   escape(entry["status"]), actions))
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
                "<td>%s</td><td>%s</td>"
                % (entry["id"], escape(entry["start_ts"]),
                   entry["id"], escape(entry["end_ts"]),
                   entry["id"], _category_options(entry["category"]),
                   escape(entry["app"]),
                   entry["id"], _project_options(entry["project_id"]),
                   entry["id"], escape(entry["task"]),
                   entry["id"], escape(entry["note"]),
                   escape(entry["status"]), actions))
        entry_rows.append("<tr>" + row_form + "</tr>")
    entries_table = (
        "<table><tr><th>Time</th><th>Category</th><th>App</th>"
        "<th>Project</th><th>Task</th><th>Note</th><th>Status</th>"
        "<th></th></tr>%s</table>"
        % ("".join(entry_rows)
           or "<tr><td colspan='8' class='note'>No entries yet -- accept a "
              "suggestion above or add one manually below.</td></tr>"))

    # --- projects ---
    proj_rows = "".join(
        "<tr><td>%s</td><td>%s</td>"
        "<td><form class='inline' method='post' "
        "action='/timesheet/project/delete' "
        "onsubmit=\"return confirm('Delete this project? Its entries keep "
        "their time but lose the project link.');\">"
        "<input type='hidden' name='id' value='%d'>"
        "<input type='hidden' name='day' value='%s'>"
        "<button type='submit'>Delete</button></form></td></tr>"
        % (escape(p["name"]), escape(p["client"] or "-"), p["id"], day)
        for p in projects
    )
    projects_html = (
        "<table><tr><th>Project</th><th>Client</th><th></th></tr>%s</table>"
        "<form method='post' action='/timesheet/project/add' "
        "style='margin-top:10px'>"
        "<input type='hidden' name='day' value='%s'>"
        "<label>Name <input type='text' name='name' required "
        "size='20'></label> "
        "<label>Client <input type='text' name='client' size='20'></label> "
        "<button type='submit'>Add project</button></form>"
        % ("".join(proj_rows)
           or "<tr><td colspan='3' class='note'>No projects yet.</td></tr>",
           day))

    lock_html = (
        "<p class='note'>This day is locked -- entries cannot be changed. "
        "Locking is permanent.</p>" if locked else
        "<form method='post' action='/timesheet/lock' "
        "onsubmit=\"return confirm('Lock this day? Entries cannot be edited "
        "afterwards.');\">"
        "<input type='hidden' name='day' value='%s'>"
        "<button type='submit'>Lock day</button></form>" % day)

    body = (
        "<div class='card'><h3>Timesheet -- %s</h3>"
        "<form class='inline' method='get' action='/timesheet'>"
        "<label>Day <input type='date' name='day' value='%s'></label> "
        "<button type='submit'>Show</button></form> "
        "<a href='/timesheet/export?day=%s'>Export this day as CSV</a>"
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
        "<div class='card'><h3>Projects</h3>%s</div>"
        % (day, day, day, timeline_html, sug_table, entries_table, lock_html, day,
           _category_options(), _project_options(), projects_html)
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
                      % (escape(result["error"]), day)), 400
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


@app.route("/timesheet/export")
def timesheet_export():
    from focuscore import timesheet as ts_mod
    import io

    day = _parse_day(request.args.get("day"))
    day_from = _parse_day(request.args.get("from")) or day
    day_to = _parse_day(request.args.get("to")) or day
    if not day_from or not day_to:
        abort(404)
    buf = io.StringIO()
    ts_mod.write_csv_rows(
        store.list_entries(day_from=day_from, day_to=day_to), buf)
    filename = ("timesheet-%s.csv" % day_from if day_from == day_to
                else "timesheet-%s-to-%s.csv" % (day_from, day_to))
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 "attachment; filename=%s" % filename})


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
                      % escape(str(exc))), 400
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


# -------------------------------------------------------------- report ---

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
