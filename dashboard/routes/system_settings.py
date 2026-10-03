"""Settings routes: theme, text size, calendar, retention.

Split from dashboard/routes/system.py (roadmap 3.5) -- pure code
move, zero behavior change.
"""
from flask import Blueprint, redirect, request

from focuscore import store

bp = Blueprint("system_settings", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Theme setting (server-side, no JS required)
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/settings/theme", methods=["POST"])
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


# ─────────────────────────────────────────────────────────────────────────────
# Text-size setting (roadmap 3.1, server-side like the theme toggle)
# ─────────────────────────────────────────────────────────────────────────────

TEXT_SIZES = ("small", "default", "large")


@bp.route("/settings/text-size", methods=["POST"])
def settings_text_size():
    """Save the Small / Default / Large text-size preference.

    Stored in the settings table; read by layout() on every page render,
    which emits data-text-size on <html>. Invalid values are ignored
    (the current setting stands).
    """
    size = request.form.get("text_size", "default")
    if size not in TEXT_SIZES:
        size = store.get_setting("ui_text_size", "default") or "default"
    store.set_setting("ui_text_size", size)
    referrer = request.referrer or "/"
    return redirect(referrer)


@bp.route("/settings/calendar", methods=["POST"])
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


# ─────────────────────────────────────────────────────────────────────────────
# Data retention (Roadmap 2.9)
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/settings/retention", methods=["POST"])
def settings_retention():
    """Save the activity-detail retention window.

    Options: 3/6/12/24 months or "forever". Invalid values are ignored
    (the current setting stands). Old detail is pruned at the next app
    startup; daily totals and money records are never auto-deleted.
    """
    from focuscore import retention as retention_mod
    raw = request.form.get("retention_months", "")
    if raw == retention_mod.FOREVER_VALUE:
        months = None
    else:
        try:
            months = int(raw)
        except (TypeError, ValueError):
            months = -1  # sentinel: not a valid option, rejected below
    if not retention_mod.set_retention_months(months):
        # Unknown value: leave the setting exactly as it was.
        return redirect("/backup?retention_error=1")
    return redirect("/backup?retention_saved=1")
