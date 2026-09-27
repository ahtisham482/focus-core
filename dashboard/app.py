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
from datetime import date, datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, redirect, request  # noqa: E402

from focuscore import paths
from focuscore import store  # noqa: E402
from focuscore.home import pulse_band as _pulse_band  # noqa: E402
from focuscore.ingest import ActivityWatchError  # noqa: E402
from focuscore.pipeline import run_day  # noqa: E402
from focuscore.scoring import UI_LABELS, productivity_pulse  # noqa: E402

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


# --------------------------------------------------------------- alerts ---

def _alert_target_text(alert):
    if alert["target_type"] == "category":
        return "Category: %s" % escape(alert["target_name"])
    return "Activity: <code>%s</code>" % escape(alert["target_name"])


# ---------------------------------------------------------------- focus ---

def _fmt_countdown(total_seconds):
    total = int(max(0, total_seconds))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


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


# ------------------------------------------------ Phase 10: invoicing ---

def _invoice_status_badge(status):
    colors = {"draft": "#616161", "sent": "#1565c0", "paid": "#2e7d32",
              "void": "#c62828"}
    return ("<span class='pill' style='background:%s'>%s</span>"
            % (colors.get(status, "#616161"), escape(status.upper())))


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


# -------------------------------------------------------------- backup ---


# -------------------------------------------------------------- update ---


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


# Sprint 4 (Qwen item 11): route handlers live in
# dashboard/routes/*.py. Importing them registers the routes
# on the `app` object above. Zero URL/HTML/schema changes.
from dashboard.routes import (  # noqa: E402,F401
    budgets,
    core,
    focus,
    invoices,
    system,
    timesheet,
)


if __name__ == "__main__":
    import argparse

    # Sprint 4 (Qwen item 10): bounded rotating logs.
    from focuscore import logging_config
    logging_config.setup_logging()

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
    from dashboard.app import app as application
    application.run(host=args.host, port=args.port)

