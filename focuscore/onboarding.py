"""Stranger-facing ActivityWatch onboarding copy (pure HTML, no Flask).

dashboard/app.py wraps these bodies in its page layout. Everything here
is constants plus the AW version/hostname, which are HTML-escaped --
never trust a local server's strings blindly.
"""

import html

from .activitywatch import (
    DOWNLOAD_URL,
    INSTALLED_NOT_RUNNING,
    NOT_INSTALLED,
    RUNNING,
)

ACTIVITYWATCH_SITE = "https://activitywatch.net/"


def _esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _extension_note():
    return (
        "<div class='card'><h3>Optional: track websites too</h3>"
        "<p class='note'>The desktop app tracks apps and window titles. "
        "For per-website tracking, install the free "
        "<i>ActivityWatch Web Watcher</i> browser extension -- find it on "
        "<a href='%s'>activitywatch.net</a>.</p></div>"
        % ACTIVITYWATCH_SITE)


def setup_page_html(state, version=None):
    """Body HTML for /setup/activitywatch for one detection state."""
    if state == RUNNING:
        body = (
            "<div class='card attention ok'><h3>ActivityWatch is running%s."
            "</h3><p>Focus Core is reading your activity and scoring it. "
            "Nothing to do here.</p></div>"
            % (" (version %s)" % _esc(version) if version else ""))
    else:
        if state == INSTALLED_NOT_RUNNING:
            headline = "ActivityWatch is installed but not running."
            steps = (
                "<li><b>Start it:</b> open the Start menu, type "
                "<i>ActivityWatch</i>, and open it. Its icon appears near "
                "the clock in the taskbar.</li>")
        elif state == NOT_INSTALLED:
            headline = "ActivityWatch isn't installed yet."
            steps = (
                "<li><b>Download it:</b> "
                "<a class='btn' href='%s'>Download ActivityWatch for "
                "Windows</a><br><span class='note'>Free and open-source, "
                "from the official ActivityWatch releases page.</span></li>"
                "<li><b>Install it:</b> run the downloaded "
                "<i>-setup.exe</i> file and follow the steps.</li>"
                "<li><b>Start it:</b> open the Start menu, type "
                "<i>ActivityWatch</i>, and open it. Its icon appears near "
                "the clock in the taskbar.</li>" % DOWNLOAD_URL)
        else:
            raise ValueError("unknown ActivityWatch state: %r" % (state,))
        body = (
            "<div class='card attention'><h3>%s</h3>"
            "<p>Focus Core can't track anything until ActivityWatch is "
            "running -- it's the part that watches which app you're using."
            "</p><ol>%s"
            "<li><b>Check:</b> come back here and press the button "
            "below.</li></ol>"
            "<p><a class='btn' href='/setup/activitywatch'>Check again</a>"
            "</p>"
            "<p class='note'>Tip: keep ActivityWatch running while you work. "
            "You can make it start with Windows in its own settings, so you "
            "never have to think about it.</p></div>"
            % (headline, steps))
    return body + _extension_note()


def welcome_step_html(state, version=None):
    """Compact ActivityWatch status card for the welcome tour (step 2)."""
    if state == RUNNING:
        return (
            "<div class='card attention ok'><h3>ActivityWatch is running%s."
            "</h3><p>Focus Core can already see your activity. You're all "
            "set -- press <b>Next</b> to continue the tour.</p></div>"
            % (" (version %s)" % _esc(version) if version else ""))
    if state == INSTALLED_NOT_RUNNING:
        label = "installed but not running"
        detail = ("Open it from the Start menu (type "
                  "<i>ActivityWatch</i>), then come back and press "
                  "<b>Check again</b>.")
    elif state == NOT_INSTALLED:
        label = "not installed yet"
        detail = ("It's the free tracker Focus Core reads from, and it "
                  "takes about a minute to install.")
    else:
        raise ValueError("unknown ActivityWatch state: %r" % (state,))
    return (
        "<div class='card attention'><h3>ActivityWatch: %s.</h3><p>%s</p>"
        "<p><a class='btn' href='/setup/activitywatch'>Set up "
        "ActivityWatch</a> "
        "<a class='btn secondary' href='/welcome?step=2'>Check again</a></p>"
        "</div>" % (label, detail))
