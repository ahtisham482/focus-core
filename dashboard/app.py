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
import logging
import uuid
from datetime import date, datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, g, redirect, request  # noqa: E402

from focuscore import paths
from focuscore import store  # noqa: E402
from focuscore.home import pulse_band as _pulse_band  # noqa: E402
from focuscore.ingest import ActivityWatchError  # noqa: E402
from focuscore.pipeline import run_day  # noqa: E402
from focuscore.scoring import UI_LABELS, productivity_pulse  # noqa: E402

# Roadmap 0.3: module logger for the error handlers below.
logger = logging.getLogger(__name__)

ONBOARDED_FLAG = paths.onboarded_flag()

# Top navigation: (key, label, href). "Review" jumps to today's
# uncategorized activities when a day is known.
NAV_LINKS = [
    ("home",         "Home",       "/",             "home"),
    ("timesheet",    "Timesheet",  "/timesheet",    "timesheet"),
    ("invoices",     "Invoices",   "/invoices",     "invoice"),
    ("report",       "Report",     "/report",       "report"),
    ("coaching",     "Coaching",   "/coaching",     "coaching"),
    ("intelligence", "Deep time",  "/intelligence", "brain"),
    ("focus",        "Focus",      "/focus",        "timer"),
    ("shield",       "Shield",     "/shield",       "shield"),
    ("goals",        "Goals",      "/goals",        "target"),
    ("alerts",       "Alerts",     "/alerts",       "bell"),
    ("backup",       "Backup",     "/backup",       "report"),
    ("review",       "Review",     "/activities",   "timesheet"),
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


def layout(title, body, day=None, refresh=300, active="home",
           help_key=None, hero=None, body_class="", extra_css="", extra_js=""):
    """Page shell: nav bar, design-system stylesheet, footer.

    hero: optional HTML string for the context-aware page header.
          When None, a plain h1 page-hero block is rendered.
    body_class: extra CSS classes added to <body> (e.g. 'zen-mode').
    """
    # ── Resolve theme setting ──────────────────────────────────────
    try:
        from focuscore import store as _store
        theme = _store.get_setting("ui_theme", "system") or "system"
    except Exception:
        theme = "system"
    theme_attr = "" if theme == "system" else " data-theme='%s'" % theme

    # ── Build nav with icon + label ────────────────────────────────
    links = []
    for nav_item in NAV_LINKS:
        key, label, href, icon = nav_item
        url = href
        if key == "review" and day:
            url = href + "?day=" + day
        cls = " class='active'" if key == active else ""
        svg = ("<svg width='15' height='15' aria-hidden='true'>"
               "<use href='/static/icons.svg#icon-%s'/></svg>" % icon)
        links.append("<a href='%s'%s>%s%s</a>" % (url, cls, svg, label))

    # Theme toggle buttons (server-side POST, no JS required)
    theme_toggle = (
        "<form class='theme-toggle' method='post' action='/settings/theme'>"
        "<button type='submit' name='theme' value='light' title='Light mode'>"
        "<svg width='14' height='14'><use href='/static/icons.svg#icon-sun'/></svg>"
        "</button>"
        "<button type='submit' name='theme' value='dark' title='Dark mode'>"
        "<svg width='14' height='14'><use href='/static/icons.svg#icon-moon'/></svg>"
        "</button>"
        "</form>"
    )

    nav = ("<nav class='topnav' id='topnav'>"
           + "".join(links)
           + theme_toggle
           + "</nav>")

    # ── Help / footer ──────────────────────────────────────────────
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
        "<footer class='page-footer'>Focus Core &middot; "
        "your data never leaves this PC "
        "&middot; <a href='%s'>Help</a> "
        "&middot; <a href='/welcome/restart'>Take the tour again</a></footer>"
        % help_href)


    # ── Page hero / title ─────────────────────────────────────────
    if hero is None:
        hero_html = (
            "<div class='page-hero'>"
            "<div class='page-hero-text'>"
            "<h1 class='page-title'>%s</h1>"
            "</div></div>" % escape(title))
    else:
        hero_html = hero

    if body_class:
        body_tag = "<body class='%s'>" % escape(body_class)
    else:
        body_tag = "<body>"
    return (
        "<!doctype html><html lang='en'%s><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='refresh' content='%d'>"
        "<title>%s &middot; Focus Core</title>"
        "<link rel='preload' href='/static/fonts/GeistVF.woff2' "
        "as='font' type='font/woff2' crossorigin>"
        "<link rel='stylesheet' href='/static/style.css'>"
        "%s"
        "<link rel='icon' href='/static/icon.png'>"
        "</head>"
        "%s%s<main class='page-main'>%s%s</main>%s"
        "<script src='/static/nav.js'></script>"
        "%s"
        "</body></html>"
        % (theme_attr, refresh, escape(title), extra_css,
           body_tag, nav, hero_html, body, footer, extra_js)
    )


def _goal_progress_html(ev, wrap=True):
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
    inner = (
        "<span class='gname'>%s</span> "
        "<span class='badge %s'>%s</span><br>"
        "<span class='gmeta'>%s of %s</span>"
        "<div class='progress'><div style='width:%.1f%%;background:%s'></div></div>"
        % (escape(ev["name"]), ev["status"],
           STATUS_LABEL.get(ev["status"], ev["status"]),
           current_text, target_text, width, color)
    )
    if wrap:
        return "<div class='goal-row'>%s</div>" % inner
    return inner


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



def hm_target_strips(day):
    """Pinned goals as thin live-strip rows.

    Craft treatment: progress gets a strip treatment, structurally
    different from creation cards. Empty string when nothing is pinned.
    """
    from focuscore import goals as goals_mod

    summary = store.get_day_summary(day)
    pinned = [g for g in goals_mod.evaluate_all(summary) if g["pinned"]]
    if not pinned:
        return ""
    _STATUS_WORD = {"on_track": "On track", "behind": "Behind pace",
                    "achieved": "Achieved", "missed": "Missed"}
    rows = []
    for goal in pinned[:3]:
        rows.append(
            "<div class='hm-strip-row'>"
            "<div class='hm-strip-top'>"
            "<span class='hm-strip-name'>%s</span>"
            "<span class='hm-strip-status'>%s</span></div>"
            "<div class='hm-strip-bar'><div style='width:%.1f%%'></div></div>"
            "<p class='hm-strip-val'>%s of %s %s</p>"
            "</div>"
            % (escape(goal["name"]),
               escape(_STATUS_WORD.get(goal["status"], goal["status"])),
               min(100.0, goal["pct"] or 0),
               "%.0f" % (goal["current"] or 0),
               "%.0f" % (goal["target"] or 0), escape(goal["unit"])))
    return "".join(rows)


def day_page(day):
    """Today's story first (Pulse + pinned targets), details second."""
    today = date.today().isoformat()
    is_today = (day == today)
    aw_note = ""
    if is_today:
        # Live progress: refresh today's tracked data on every page load.
        try:
            run_day(date.today())
        except ActivityWatchError:
            # Plain words, never a raw exception dump.
            aw_note = (
                "<div class='hm-wrap'><div class='hm-alert'><p><b>"
                "ActivityWatch isn't running.</b></p>"
                "<p>Start ActivityWatch and reload this page &mdash; or "
                "<a href='/setup/activitywatch'>set it up</a> if it isn't "
                "installed yet. Your saved data is still shown below.</p>"
                "</div></div>")
    summary = store.get_day_summary(day)
    pulse = productivity_pulse(summary["seconds_by_level"])
    total = summary["total_seconds"]

    try:
        dateline = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d")
    except ValueError:
        dateline = day
    head = (
        "<div class='hm-wrap'><p class='hm-dateline'>%s</p>"
        "<h1 class='hm-h1'>%s</h1></div>"
        % (dateline, "Today so far" if is_today else "Day in review"))

    if total <= 0:
        if is_today:
            empty = (
                "<div class='hm-wrap'><div class='hm-empty'>"
                "<p class='hm-empty-title'>Nothing tracked yet today.</p>"
                "<p>Your day's story appears here as time gets tracked "
                "&mdash; or start it yourself right now:</p>"
                "<form class='hm-start' method='post' action='/focus/start'>"
                "<input type='hidden' name='preset' value='25'>"
                "<button type='submit' class='hm-btn'>"
                "Start a 25-minute session</button></form>"
                "<p class='hm-quiet'><a href='/setup/activitywatch'>"
                "Set up ActivityWatch</a> and your whole day is tracked "
                "by itself.</p></div></div>")
        else:
            empty = (
                "<div class='hm-wrap'><div class='hm-empty'>"
                "<p class='hm-empty-title'>No tracked data for this day.</p>"
                "<p>Days with tracked time show their full story here.</p>"
                "</div></div>")
        return layout("Day " + day, aw_note + head + empty, day,
                      help_key="day", hero="")

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
    ) or ("<tr><td colspan='3' class='hm-quiet'>Nothing waiting "
           "&mdash; all clear.</td></tr>")

    pulse_html = (
        "<section class='hm-wrap' aria-label='Pulse'>"
        "<div class='hm-pulse'>"
        "<div class='hm-pulse-left'>"
        "<p class='hm-label'>Productivity Pulse</p>"
        "<p class='hm-pulse-num hm-%s'>%.0f</p>"
        "<p class='hm-quiet'>Weighted 0&ndash;100 score of everything you "
        "did. Green 60+, amber 40&ndash;59, red below 40. %.2fh tracked.</p>"
        "</div>"
        "<div class='hm-pulse-right'>%s%s</div>"
        "</div></section>"
        % (_pulse_band(pulse), pulse, _hours(total),
           bucket_bar(summary["seconds_by_level"], total),
           legend(summary["seconds_by_level"])))

    strips = hm_target_strips(day)
    targets_html = (
        "<section class='hm-wrap' aria-label='Today&rsquo;s targets'>"
        "<h2 class='hm-sec'>Pinned targets</h2>"
        "<div class='hm-strip'>%s</div></section>" % strips) if strips else ""

    detail_html = (
        "<section class='hm-wrap'>"
        "<div class='hm-detail'>"
        "<h2 class='hm-sec'>By category</h2>"
        "<div class='hm-tablewrap'><table class='hm-table'>"
        "<tr><th>Category</th><th>Score</th><th>Time</th><th>Share</th></tr>"
        "%s</table></div></div>"
        "<div class='hm-detail'>"
        "<h2 class='hm-sec'>Uncategorized queue</h2>"
        "<p class='hm-quiet'>These counted as Neutral in the Pulse. "
        "Review them on the <a href='/activities?day=%s'>Activities</a> page "
        "and set a score &mdash; or fix their category for next time.</p>"
        "<div class='hm-tablewrap'><table class='hm-table'>"
        "<tr><th>Activity</th><th>App</th><th>Time</th></tr>%s</table></div>"
        "</div>"
        "<p class='hm-footnote'>AFK/idle time (%.2fh) is excluded from the "
        "Pulse. This page re-reads today's tracked data on every load "
        "&mdash; the numbers above are always live.</p>"
        "</section>"
        % (cat_rows, day, uncat_rows, _hours(summary["afk_seconds"])))

    body = aw_note + head + pulse_html + targets_html + detail_html
    return layout("Day " + day, body, day, help_key="day", hero="")



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
    "extension may be changing the request — try the Focus Core desktop "
    "window instead.</p>"
    "<p><a href='/'>Back to Home</a></p>")


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


def _add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # same-origin (not no-referrer): Chromium sends "Origin: null" on form
    # POSTs from no-referrer pages, which our own _reject_loopback_csrf
    # would reject with 403 — breaking every form in a real browser.
    # same-origin still never leaks a Referer off this machine.
    response.headers["Referrer-Policy"] = "same-origin"
    # 'unsafe-inline' is needed because the dashboard is a server-rendered
    # single-file app (no template files to split): it uses inline
    # onsubmit="return confirm(...)" handlers on the delete/abort/restore
    # forms and inline style="" attributes on the home-page bar charts.
    # No external content is ever loaded and the server binds 127.0.0.1
    # only, so allowing inline does not widen the trust boundary.
    # Qwen RV-4: /intelligence/report route isolation (zero script, strict locked CSP)
    if request.path == "/intelligence/report":
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
            "font-src data:"
        )
    else:
        # Qwen RV-5: CSP permits 'self' for nav.js and local Geist fonts
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
            "img-src 'self' data:"
        )
    return response


# --------------------------------------------------- friendly errors ---
# Roadmap 0.3: branded 404/500 pages. Plain English, calm, with a
# recovery link -- never a traceback or internals. The 500 handler
# still logs the exception server-side so field failures leave a trace.

def _error_page(code, headline, detail, request_id=None):
    """Branded error page inside the normal page shell."""
    ref = ""
    if request_id:
        ref = ("<p>If you ask for help, quote this code: "
               "<code>%s</code></p>" % escape(request_id))
    body = (
        "<div class='hm-wrap'><div class='hm-empty'>"
        "<p class='hm-empty-title'>%s</p>"
        "<p>%s</p>"
        "%s"
        "<p><a href='/'>Back to Home</a></p>"
        "</div></div>"
        % (escape(headline), detail, ref))
    return layout("%s (%d)" % (headline, code), body, active="home")


def _assign_request_id():
    g.request_id = "req-" + uuid.uuid4().hex[:8]


def _page_not_found(_err):
    return _error_page(
        404,
        "This page doesn't exist.",
        "The link may be old, or the address may have a typo. "
        "Your data is untouched."), 404


def _internal_error(_err):
    rid = getattr(g, "request_id", "req-unknown")
    logger.exception("Unhandled exception while serving %s [%s]",
                     request.path, rid)
    return _error_page(
        500,
        "Something went wrong on our side.",
        "Your data is safe. Head back home and carry on &mdash; if this "
        "keeps happening, the details are in the app log.",
        request_id=rid), 500


class _DropFlaskDuplicateTraceback(logging.Filter):
    """Roadmap 1.3: drop Flask's own copy of the 500 traceback.

    Flask logs "Exception on <path> [METHOD]" with exc_info for the
    same failure the handler above already logged via
    logger.exception(...). Both records come from this logger and
    both carry a traceback, so a single failure reads as two.
    Keep ours (it carries the request id); drop Flask's copy.
    """

    def filter(self, record):
        return not (record.exc_info
                    and isinstance(record.msg, str)
                    and record.msg.startswith("Exception on "))


_TARGET_SVG = (
    "<svg width='26' height='26' viewBox='0 0 26 26' fill='none' "
    "stroke='currentColor' stroke-width='2' aria-hidden='true'>"
    "<circle cx='13' cy='13' r='9'/>"
    "<circle cx='13' cy='13' r='3.5' fill='currentColor' stroke='none'/>"
    "<path d='M13 1v5M13 20v5M1 13h5M20 13h5'/></svg>"
)
"""Brand mark for the Living Instrument nav: an inline SVG target."""


_LIVING_TAB_ICONS = {
    "home": ("<svg width='22' height='22' viewBox='0 0 24 24' fill='none' "
             "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
             "stroke-linejoin='round' aria-hidden='true'>"
             "<path d='M4 11.5 12 4l8 7.5'/><path d='M6 10.5V20h12v-9.5'/>"
             "</svg>"),
    "focus": ("<svg width='22' height='22' viewBox='0 0 24 24' fill='none' "
              "stroke='currentColor' stroke-width='1.8' aria-hidden='true'>"
              "<circle cx='12' cy='12' r='8'/>"
              "<circle cx='12' cy='12' r='2.6' fill='currentColor' "
              "stroke='none'/></svg>"),
    "goals": ("<svg width='22' height='22' viewBox='0 0 24 24' fill='none' "
              "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
              "stroke-linejoin='round' aria-hidden='true'>"
              "<path d='M6 21V4'/><path d='M6 5h11l-2.6 3.5L17 12H6'/></svg>"),
}
"""Inline SVG icons for the mobile tab bar (zero icon-font dependency)."""


def _living_nav(active, shield_cls, shield_label):
    """Shared living top bar: brand, Home/Focus/Goals, shield pill."""
    links = []
    for key, label, href in (("home", "Home", "/"),
                             ("focus", "Focus", "/focus"),
                             ("goals", "Goals", "/goals")):
        cur = " aria-current='page'" if key == active else ""
        links.append("<a href='%s'%s>%s</a>" % (href, cur, label))
    return (
        "<nav class='lv-nav st' style='--d:0ms' aria-label='Primary'>"
        "<div class='lv-nav-inner'>"
        "<a class='lv-brand' href='/' aria-label='Focus Core home'>%s"
        "<span>Focus Core</span></a>"
        "<div class='lv-links'>%s</div>"
        "<a class='lv-shield %s' href='/shield'>"
        "<span class='dot' aria-hidden='true'></span>%s</a>"
        "</div></nav>" % (_TARGET_SVG, "".join(links), shield_cls,
                          shield_label))


def _living_tabs(active):
    """Fixed bottom tab bar for living pages (mobile only; CSS-gated)."""
    items = []
    for key, label, href in (("home", "Home", "/"),
                             ("focus", "Focus", "/focus"),
                             ("goals", "Goals", "/goals")):
        cur = " aria-current='page'" if key == active else ""
        cls = " class='on'" if key == active else ""
        items.append(
            "<a href='%s'%s%s>%s<span>%s</span></a>"
            % (href, cur, cls, _LIVING_TAB_ICONS[key], label))
    return ("<nav class='lv-tabs' aria-label='Primary'>%s</nav>"
            % "".join(items))


def home_page():
    """Home: one clear action first (start a 25-minute session), then
    today's live story as a strip -- never a wall of identical cards."""
    from focuscore import home as home_mod
    from focuscore import activitywatch as aw_mod
    from focuscore import chronotype, focus as focus_mod

    today = date.today().isoformat()
    try:
        run_day(date.today())
    except ActivityWatchError:
        # Expected whenever ActivityWatch is off -- the "ActivityWatch
        # isn't running" card on this very page covers it. DEBUG only:
        # this fires on every home load while AW is down.
        logger.debug("home page: ActivityWatch day sync unavailable")

    aw_status = aw_mod.server_status()
    aw_state = aw_mod.detection_state(status=aw_status)

    summary = store.get_day_summary(today)
    total = summary["total_seconds"]
    seconds_by_level = summary["seconds_by_level"]
    focus_hours = _hours(seconds_by_level.get(2, 0)
                         + seconds_by_level.get(1, 0))
    streak = focus_mod.current_streak()
    peak_lbl = chronotype.window_label()

    # ── Session totals: completed focus sessions today (H:MM) ──
    day_sessions = store.get_day_sessions(today)
    n_sessions = len(day_sessions)
    longest_min = 0
    focus_seconds = 0
    for _s in day_sessions:
        try:
            _secs = (datetime.fromisoformat(_s["ended_at"])
                     - datetime.fromisoformat(_s["started_at"])).total_seconds()
        except Exception:
            _secs = ((_s.get("planned_minutes") or 0) * 60)
        longest_min = max(longest_min, _secs / 60)
        focus_seconds += int(max(0, _secs))
    pulse_hmm = "%d:%02d" % (focus_seconds // 3600, (focus_seconds % 3600) // 60)
    if n_sessions:
        pulse_sub = ("%d %s &middot; longest %d min"
                     % (n_sessions, "session" if n_sessions == 1 else "sessions",
                        int(round(longest_min))))
    else:
        pulse_sub = "No sessions yet &mdash; your first one starts the story."

    # Shield pill: armed exactly when Shield enforcement is actually on
    # (daemon mutex, in-memory -- Invariant I-1).
    from focuscore import shield as shield_mod
    _shield_armed = (shield_mod.shield_daemon_running()
                     and not shield_mod.shield_killswitch_on())
    shield_cls = "armed" if _shield_armed else "ready"
    shield_label = "Shield armed" if _shield_armed else "Shield ready"

    # ── Focus ring only in the hero (pinned goals live in the strip) ──
    from focuscore import gamification as gam_mod
    _ring = gam_mod.daily_ring(today)

    def _num(v):
        return "%.0f" % (v or 0)
    _frac = min(max(_ring["fraction"] or 0.0, 0.0), 1.0)
    rings_html = (
        "<div class='lv-ring'>"
        "<svg viewBox='0 0 120 120' role='img' aria-label='%s'>"
        "<circle class='track' cx='60' cy='60' r='52'></circle>"
        "<circle class='fill' cx='60' cy='60' r='52' "
        "style='stroke-dasharray:326.7;stroke-dashoffset:%.1f;--d:560ms'></circle>"
        "</svg>"
        "<p class='lv-ring-name'>Focus</p>"
        "<p class='lv-ring-val'>%s / %s min</p>"
        "</div>"
        % (escape("Focus: %s of %s min" % (_num(_ring["minutes"]),
                                          _num(_ring["target"]))),
           326.7 * (1 - _frac),
           _num(_ring["minutes"]), _num(_ring["target"]))
    )

    # ── Living rhythm: today's calendar events ──
    from focuscore import calendar_feed as cal_mod
    try:
        _cal_events, _cal_state = cal_mod.today_events(today)
    except Exception:
        _cal_events, _cal_state = [], "unreachable"
    _cal_note = ""
    if request.args.get("cal_error"):
        _cal_note = ("<p class='lv-cal-error'>That URL doesn't look right "
                     "&mdash; it must start with http:// or https://.</p>")
    if _cal_state == "unconfigured":
        rhythm_inner = (
            "%s"
            "<p class='hm-quiet'>Connect your calendar to see "
            "today's rhythm here.</p>"
            "<form class='hm-cal-form' method='post' "
            "action='/settings/calendar'>"
            "<input type='url' name='ical_url' required "
            "placeholder='Paste your Google Calendar secret iCal URL' "
            "autocomplete='off'>"
            "<button type='submit' class='hm-btn-sm'>Connect</button>"
            "</form>"
            "<p class='hm-hint'>Google Calendar &rarr; Settings &rarr; "
            "Integrate calendar &rarr; Secret address in iCal format. "
            "It never leaves this PC.</p>" % _cal_note
        )
    else:
        if _cal_state == "unreachable":
            _cal_list = ("<p class='hm-quiet'>Couldn't reach your "
                         "calendar right now.</p>")
        elif _cal_events:
            _cal_list = ("<ul class='hm-cal'>%s</ul>" % "".join(
                "<li class='lv-cal-ev lv-cal-%s'>"
                "<span class='hm-cal-time'>%s</span>"
                "<span class='hm-cal-name'>%s</span></li>"
                % (e.get("state", "next"), escape(e["time"]),
                   escape(e["summary"]))
                for e in _cal_events[:3]))
        else:
            _cal_list = ("<p class='hm-quiet'>Nothing on the calendar "
                         "today.</p>")
        if _cal_state == "stale":
            _cal_list = ("<p class='lv-cal-stale'>Couldn't refresh your "
                         "calendar &mdash; showing last synced data.</p>"
                         + _cal_list)
        rhythm_inner = (
            _cal_note + "%s"
            "<form class='hm-cal-change' method='post' "
            "action='/settings/calendar'>"
            "<input type='hidden' name='ical_url' value=''>"
            "<button type='submit' class='hm-linkbtn'>"
            "Disconnect calendar</button>"
            "</form>" % _cal_list
        )
    rhythm_html = (
        "<section class='hm-wrap' aria-label='Rhythm'>"
        "<h2 class='hm-sec'>Rhythm</h2>%s"
        "</section>" % rhythm_inner
    )

    cold_start = total <= 0 and streak == 0
    dateline = date.today().strftime("%A, %B") + " %d" % date.today().day
    if cold_start:
        h1_lines = (
            "<span class='mask'><span class='line' style='--d:120ms'>"
            "Day one.</span></span>"
            "<span class='mask'><span class='line' style='--d:200ms'>"
            "Your focus story</span></span>"
            "<span class='mask'><span class='line' style='--d:280ms'>"
            "starts now.</span></span>"
        )
        subcopy = ("Work normally today. Focus Core is learning your rhythm "
                   "&mdash; tomorrow you'll see your first Flow Index, your peak "
                   "hours, and your streak.")
    else:
        h1_lines = (
            "<span class='mask'><span class='line' style='--d:120ms'>"
            "Your day,</span></span>"
            "<span class='mask'><span class='line' style='--d:200ms'>"
            "in <em class='lv-ember-i'>focus</em>.</span></span>"
        )
        if chronotype.is_peak_now():
            subcopy = ("You're in your peak window (%s) &mdash; a good moment "
                       "to begin." % escape(peak_lbl))
        else:
            subcopy = ("Your peak window is %s &mdash; your hardest work "
                       "belongs there." % escape(peak_lbl))

    # The north-star action: one tap starts a real 25-minute session.
    # Existing /focus/start contract (preset=25); label left blank.
    cta_html = (
        "<form class='hm-start' method='post' action='/focus/start'>"
        "<input type='hidden' name='preset' value='25'>"
        "<button type='submit' class='lv-btn lv-magnet'>"
        "Begin focus session</button></form>"
        "<a class='lv-ghost lv-magnet' href='/day/%s'>Today's plan</a>"
        % today
    )

    nav_html = _living_nav("home", shield_cls, shield_label)
    hero_html = (
        "<section class='lv-wrap' aria-label='Today at a glance'><div class='lv-hero'>"
        "<div>"
        "<p class='lv-dateline st' style='--d:60ms'>%s</p>"
        "<h1 class='lv-h1'>%s</h1>"
        "<p class='lv-sub st' style='--d:340ms'>%s</p>"
        "<div class='lv-ctas st' style='--d:420ms'>%s</div>"
        "</div>"
        "<div class='lv-pulse st' style='--d:500ms'>"
        "<p class='lv-pulse-label'><span class='lv-live-dot' aria-hidden='true'></span>"
        "Deep focus today</p>"
        "<p class='lv-pulse-num' data-countup data-seconds='%d'>%s</p>"
        "<p class='lv-pulse-sub'>%s</p>"
        "<div class='lv-rings'>%s</div>"
        "</div>"
        "</div></section>"
        % (dateline, h1_lines, subcopy, cta_html,
           focus_seconds, pulse_hmm, pulse_sub, rings_html)
    )
    foot_html = (
        "<footer class='lv-wrap'><div class='lv-foot st' style='--d:900ms'>"
        "<span>Focus Core</span><span>%s</span>"
        "</div></footer>" % (("%d-day streak" % streak) if streak else "Day 1")
    )

    # ── Today's progress: pinned goals as a live strip ──
    strips = hm_target_strips(today)
    if strips:
        targets_html = (
            "<section class='hm-wrap' aria-label='Today&rsquo;s progress'>"
            "<h2 class='hm-sec'>Today's progress</h2>"
            "<div class='hm-strip'>%s</div></section>" % strips)
    else:
        targets_html = (
            "<section class='hm-wrap' aria-label='Today&rsquo;s progress'>"
            "<h2 class='hm-sec'>Today's progress</h2>"
            "<p class='hm-empty-inline'>Nothing here yet &mdash; your targets "
            "will appear here as today unfolds. "
            "<a href='/goals'>Pin a goal</a> to start.</p></section>")

    cards = home_mod.attention_cards(aw_state=aw_state)
    if cards:
        banner_rows = "".join(
            "<li><b>%s</b> &mdash; %s "
            "<a class='btn btn-sm' href='%s'>%s</a></li>"
            % (escape(c["title"]), escape(c["detail"]),
               escape(c["button_href"]), escape(c["button_text"]))
            for c in cards)
        attention_html = (
            "<section class='hm-wrap'><div class='hm-attention'>"
            "<h2 class='hm-sec'>What needs your attention</h2><ul>%s</ul>"
            "</div></section>" % banner_rows)
    else:
        attention_html = (
            "<section class='hm-wrap'><div class='hm-attention'>"
            "<h2 class='hm-sec'>All clear &mdash; you're on track.</h2>"
            "<p class='hm-quiet'>Nothing needs you right now.</p>"
            "</div></section>")

    if total > 0:
        mix_html = bucket_bar(seconds_by_level, total) + legend(seconds_by_level)
    else:
        mix_html = ("<p class='hm-quiet'>No tracked time yet today &mdash; your "
                    "productivity mix will appear here.</p>")
    today_html = (
        "<section class='hm-wrap'><div class='hm-today'>"
        "<h2 class='hm-sec'>Today</h2>"
        "<div class='hm-stats'>"
        "<div class='hm-stat'><div class='hm-stat-num' data-live "
        "data-tabular>%.1f</div>"
        "<div class='hm-stat-lbl'>tracked hours</div></div>"
        "<div class='hm-stat'><div class='hm-stat-num' data-live "
        "data-tabular>%.1f</div>"
        "<div class='hm-stat-lbl'>focused hours</div></div>"
        "</div>%s"
        "<p class='hm-links'><a href='/day/%s'>See today's full details</a> "
        "&middot; <a href='/timesheet?day=%s'>Today's timesheet</a></p>"
        "</div></section>"
        % (_hours(total), focus_hours, mix_html, today, today))

    footnote_html = (
        "<section class='hm-wrap'>"
        "<p class='hm-footnote'>Focus Core scores your time by itself. "
        "Start a session and everything above fills in on its own.</p>"
        "</section>")

    body = (nav_html + hero_html + targets_html + rhythm_html
            + attention_html + today_html + footnote_html
            + foot_html
            + "<div class='hm-tabspace' aria-hidden='true'></div>"
            + _living_tabs("home"))
    return layout("Home", body, day=today, active="home",
                  body_class="living",
                  extra_css="<link rel='stylesheet' href='/static/living.css'>",
                  extra_js="<script src='/static/living-home.js'></script>")


# ------------------------------------------------------------ welcome ---

WELCOME_STEPS = [
    {"icon": "shield",
     "title": "Your data stays on this computer.",
     "text": "No account, no sign-in, nothing sent to the internet. "
             "Everything lives in one file (focuscore.db) on this PC. "
             "If you turn on Drive backups, a copy of that file is "
             "placed in your own Google Drive folder and nowhere else.",
     "check": None,
     "todo": "Nothing to do here — press Next."},
    {"icon": "timesheet",
     "title": "ActivityWatch does the watching.",
     "text": "Focus Core records nothing by itself. It reads ActivityWatch, "
             "a free tracker that notes which app or website you were using. "
             "Keep it running and your days score themselves.",
     "check": "Look at the bottom-right of your Windows taskbar, near "
              "the clock. You should see the ActivityWatch icon. If it "
              "is missing, open ActivityWatch from the Start menu.",
     "todo": "Make sure ActivityWatch is running, then press Next."},
    {"icon": "report",
     "title": "Your time gets a score from -2 to +2.",
     "text": "Real work is +2, good work +1, neutral 0, personal time -1, "
             "distractions -2. Your Pulse (0-100) mixes it all together. "
             "Above 60 is a good day.",
     "check": None,
     "todo": "Nothing to do here — press Next."},
    {"icon": "target",
     "title": "Teach it once — it remembers.",
     "text": "New apps start as neutral. Tell Focus Core what an app really "
             "is, and it remembers forever, so your scores get sharper "
             "every day.",
     "check": None,
     "todo": "Press the button below to review what was tracked."},
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
            headline = "Break — relax"
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
            "whenever you are ready — well done.</p>"
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
        "<span class='val' data-financial>%s of %s</span> "
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
        "next period, as %% of its base hour cap. Hours only &mdash; money never "
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
    colors = {"draft": "var(--ink-faint)", "sent": "var(--accent)",
              "paid": "var(--success)", "void": "var(--danger)"}
    return ("<span class='pill' style='background:%s'>%s</span>"
            % (colors.get(status, "var(--ink-faint)"), escape(status.upper())))


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
            "<td style='text-align:right' data-financial>%s</td>"
            "<td style='text-align:right' data-financial>%s</td><td>%s</td></tr>"
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
        "<td style='text-align:right' data-financial>%s</td></tr>"
        "<tr><td>Discount (%d%%)</td>"
        "<td style='text-align:right' data-financial>%s</td></tr>"
        "<tr><td>Tax (%d%%)</td>"
        "<td style='text-align:right' data-financial>%s</td></tr>"
        "<tr><th>Total (%s)</th>"
        "<th style='text-align:right' data-financial>%s</th></tr>"
        "<tr><td>Payments received</td>"
        "<td style='text-align:right' data-financial>%s</td></tr>"
        "<tr><th>Balance due</th>"
        "<th style='text-align:right' data-financial>%s</th></tr>"
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
            "<p class='msg'>Payments exceed the invoice total by "
            "<span data-financial>%s</span>. "
            "Consider issuing a credit note or refund. (Informational only.)"
            "</p>"
            % money_mod.format_minor(inv["overpaid_minor"], currency))
    if inv["overdue"]:
        totals_html += ("<p class='msg'><strong>Overdue</strong> since %s.</p>"
                          % escape(inv["due_date"] or ""))

    pay_rows = "".join(
        "<tr><td>%s</td>"
        "<td style='text-align:right' data-financial>%s</td>"
        "<td>%s</td></tr>"
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
    # Craft pass (work batch): totals hero first, lines second, payments,
    # actions last. All forms keep their contracts; only HTML is reordered.
    return (
        "<section class='wk-inv-hero'>"
        "<div class='wk-inv-hero-top'><h2>%s</h2>%s</div>"
        "<div class='wk-inv-hero-nums'>"
        "<div><span class='note'>Total</span><br>"
        "<b data-financial>%s</b></div>"
        "<div><span class='note'>Balance due</span><br>"
        "<b data-financial>%s</b></div></div>"
        "<p class='note'>Project: %s &middot; Client: %s &middot; "
        "Issued: %s &middot; Due: %s</p>%s</section>"
        "<section class='wk-section'><h2>Lines</h2>"
        "<div class='wk-strip'><table class='tbl'>"
        "<tr><th>Date</th><th>Description</th>"
        "<th style='text-align:right'>Hours</th>"
        "<th style='text-align:right'>Rate</th>"
        "<th style='text-align:right'>Amount</th><th></th></tr>%s"
        "</table></div></section>"
        "<section class='wk-section'><h2>Totals</h2>%s</section>"
        "<section class='wk-section'><h2>Payments</h2>%s</section>"
        "<section class='wk-section wk-create'><h2>Actions</h2>%s"
        "<p><a class='btn secondary' href='/invoices/%d/print' "
        "target='_blank'>Print / save PDF</a></p>%s</section>"
        % (escape(title), _invoice_status_badge(inv["status"]),
           money_mod.format_minor(t["total_minor"], currency),
           money_mod.format_minor(inv["balance_minor"], currency),
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
           actions, inv["id"],
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


def create_app(config=None):
    """Application factory (Roadmap 1.6).

    Builds a Flask app, applies ``config`` overrides when given,
    attaches the app-level hooks/handlers defined above, registers
    the six route blueprints in one place, and returns the app.
    Helpers stay in this module; routes import them from here.
    """
    app = Flask(__name__)
    if config is not None:
        app.config.update(config)
    app.before_request(_reject_loopback_csrf)
    app.before_request(_assign_request_id)
    app.after_request(_add_security_headers)
    app.register_error_handler(404, _page_not_found)
    app.register_error_handler(500, _internal_error)
    # The app logger is a shared per-name singleton, so attach the
    # filter only once (Roadmap 1.6 repair, critic Objection 2).
    if not any(
        isinstance(f, _DropFlaskDuplicateTraceback)
        for f in app.logger.filters
    ):
        app.logger.addFilter(_DropFlaskDuplicateTraceback())
    from dashboard.routes import (
        budgets,
        core,
        focus,
        invoices,
        system,
        timesheet,
    )

    # Registration happens only here (Roadmap 1.6). ``getattr`` guards
    # the circular-import case: when a routes module is imported first,
    # it triggers this factory while still loading (no ``bp`` yet), and
    # Flask forbids adding routes to an already-registered blueprint.
    # Skipping the still-loading module lets its import finish; a fresh
    # ``create_app()`` (or the normal app-first import) registers all six.
    for _module in (budgets, core, focus, invoices, system, timesheet):
        _bp = getattr(_module, "bp", None)
        if _bp is not None:
            app.register_blueprint(_bp)
    return app


def __getattr__(name):
    # PEP 562 lazy module attribute (Roadmap 1.6 repair): the compat
    # global ``app`` is created on first access, never at import
    # time, so a routes-first import finishes defining its
    # blueprint before the factory runs and the global app always
    # carries the complete route map in every import order.
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__():
    return sorted(set(globals()) | {"app"})


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
    # Roadmap 1.3: tag this process "dashboard" in the shared log.
    from focuscore import logging_config
    logging_config.setup_logging(process_name="dashboard")
    from dashboard.app import app as application
    application.run(host=args.host, port=args.port)
