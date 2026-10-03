"""Update routes: status page, check toggle, start, revert.

Split from dashboard/routes/system.py (roadmap 3.5) -- pure code
move, zero behavior change.
"""
from html import escape
from pathlib import Path

from flask import Blueprint, redirect, request

from dashboard.app import layout
from focuscore import store

bp = Blueprint("system_update", __name__)


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


def _update_failure_html(updater_mod):
    """The calm "update didn't finish" card, from the bat's marker.

    Shown exactly once: the marker is cleared as soon as it is read,
    so the next page load is clean (roadmap 2.1). The running version
    is named from update-info.json -- never a guess -- and the message
    never claims the update half-worked: Inno either replaced the app
    or it didn't.
    """
    failure = updater_mod.read_update_failure()
    if not failure:
        return ""
    updater_mod.clear_update_failure()
    info = updater_mod.get_update_info()
    if not info:
        return ""
    attempted = failure.get("version")
    tried = ("The update to <b>" + escape(str(attempted))
             + "</b> didn't finish. ") if attempted else (
                 "The update didn't finish. ")
    return (
        "<div class='card'><h3>Update didn't finish</h3>"
        "<p>" + tried + "You're still on <b>"
        + escape(str(info["version"])) + "</b>. Nothing was changed.</p>"
        "<p class='note'>Your data and settings are exactly as they "
        "were. You can try the update again whenever you're ready.</p>"
        "</div>")


def _revert_card_html(updater_mod, current_version):
    """The "go back to the previous version" card (roadmap 2.1).

    Rendered only when the data dir really holds the previous
    installer and it names a different version than the running one --
    otherwise the feature is absent, never greyed-out theater.
    """
    previous = updater_mod.previous_installer()
    if not previous:
        return ""
    if updater_mod.parse_version(previous["version"]) == \
            updater_mod.parse_version(str(current_version)):
        return ""
    previous_version = escape(str(previous["version"]))
    return (
        "<div class='card'><h3>Previous version</h3>"
        "<p>Something wrong since the last update? You can go back to "
        "<b>" + previous_version + "</b>, the version you had before "
        "this one. Focus Core kept its installer for exactly this.</p>"
        "<form method='post' action='/update/revert' onsubmit=\"return "
        "confirm('Go back to " + previous_version + "? A safety backup "
        "is made first, then Focus Core closes, reinstalls the previous "
        "version, and reopens by itself.');\">"
        "<button type='submit'>Revert to previous version</button>"
        "</form>"
        "<p class='note'>Your data is never touched — and a safety "
        "backup is made first anyway.</p></div>")


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


@bp.route("/update")
def update_page():
    from focuscore import config
    from focuscore import updater as updater_mod

    # Roadmap 2.1: a failed update's marker is shown once, above
    # whatever else this page renders -- policy pin included.
    failure_html = _update_failure_html(updater_mod)
    refresh = request.args.get("refresh") == "1"
    if config.feature_enabled("updates_disabled"):
        # Roadmap 1.21: the machine policy beats every path into this
        # page, including the offline cached-status branch below -- a
        # stale "update available" must never render under a pin.
        info = updater_mod.get_update_info()
        status = {"status": "disabled-by-policy",
                  "current": info["version"] if info else None}
    elif refresh:
        # Explicit "Check again" click: a deliberate action, allowed
        # unless the machine policy above forbids it -- the per-user
        # toggle governs automatic checks, not this.
        status = updater_mod.check_for_update(force=True)
    elif store.get_setting("update_check_enabled", "1") == "1":
        status = updater_mod.check_for_update()
    else:
        # Automatic checks off: render the last known state (or "not
        # checked yet") without touching the network (roadmap 1.21).
        status = _cached_update_status(updater_mod)

    if status["status"] == "disabled-by-policy":
        current = ("You're on <b>" + escape(status["current"]) + "</b>. "
                   if status.get("current") else "")
        body = (
            "<div class='card'><h3>Updates</h3>"
            "<p><b>Updates are disabled by your IT policy.</b></p>"
            "<p>" + current + "Your IT team manages Focus Core "
            "updates on this device, so the app won't check for new "
            "versions here &mdash; not even when you ask it to. Your "
            "own update setting can't override this.</p></div>")
        return layout("Updates", failure_html + body, help_key="update")

    if status["status"] == "dev-copy":
        body = (
            "<div class='card'><h3>Updates</h3>"
            "<p>This is a developer copy of Focus Core, so it doesn't "
            "update itself. Pull the newest code (or grab the newest zip) "
            "the way you usually do.</p>"
            "<p class='note'>One-click updates are for installed copies "
            "only.</p></div>")
        return layout("Updates", failure_html + body, help_key="update")

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
        return layout("Updates", failure_html + body
                      + _update_toggle_card_html()
                      + _revert_card_html(updater_mod,
                                          status["current"]),
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
        return layout("Updates", failure_html + body
                      + _update_toggle_card_html()
                      + _revert_card_html(updater_mod,
                                          status["current"]),
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
        return layout("Updates", failure_html + body
                      + _update_toggle_card_html()
                      + _revert_card_html(updater_mod,
                                          status["current"]),
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
    return layout("Updates", failure_html + body
                  + _update_toggle_card_html()
                  + _revert_card_html(updater_mod, status["current"]),
                  help_key="update")


@bp.route("/update/check-toggle", methods=["POST"])
def update_check_toggle():
    """Turn the automatic daily update check on or off.

    Same shape as /settings/theme: a plain form POST + redirect. The
    setting governs only the automatic background check -- the manual
    "Check again" button on the Updates page works too, unless the
    machine ``updates_disabled`` policy is on (roadmap 1.21), which
    beats this setting everywhere. Garbage input
    fails safe to off (fewer internet calls, never more) -- a missing
    field resolves to off too, never on.
    """
    enabled = request.form.get("update_check_enabled", "0") == "1"
    store.set_setting("update_check_enabled", "1" if enabled else "0")
    referrer = request.referrer or "/update"
    return redirect(referrer)


@bp.route("/update/start", methods=["POST"])
def update_start():
    import tempfile

    from focuscore import backup as backup_mod
    from focuscore import updater as updater_mod

    status = updater_mod.check_for_update(force=True)
    if status["status"] == "disabled-by-policy":
        # Roadmap 1.21: refuse plainly -- the generic "Nothing to
        # update" below would mislead on a policy-pinned machine.
        return layout(
            "Updates",
            "<div class='card'><p><b>Updates are disabled by your IT "
            "policy.</b> Your IT team manages updates on this device, "
            "so Focus Core can't update itself here.</p>"
            "<p><a href='/update'>Back</a></p></div>",
            help_key="update"), 403
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

    # Roadmap 2.1: track the completed download (settles the
    # previous-installer slot for the version running right now).
    updater_mod.track_download(dest, status["latest"])
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


@bp.route("/update/revert", methods=["POST"])
def update_revert():
    """Queue the kept previous installer for the tray to apply.

    Roadmap 2.1. Mirrors /update/start's gates exactly -- the 1.21
    machine policy pin (403, IT's pin is a pin), an active focus
    session, and the safety backup -- then hands the previous
    installer to the normal pending-install path so the tray applies
    it like any update. With no honestly-kept previous installer the
    refusal is plain and nothing is queued.
    """
    from focuscore import backup as backup_mod
    from focuscore import config
    from focuscore import updater as updater_mod

    if config.feature_enabled("updates_disabled"):
        return layout(
            "Updates",
            "<div class='card'><p><b>Updates are disabled by your IT "
            "policy.</b> Your IT team manages updates on this device, "
            "so Focus Core can't change versions here — not even back "
            "to the previous one.</p>"
            "<p><a href='/update'>Back</a></p></div>",
            help_key="update"), 403

    info = updater_mod.get_update_info()
    previous = updater_mod.previous_installer()
    if not info or not previous or \
            updater_mod.parse_version(previous["version"]) == \
            updater_mod.parse_version(str(info["version"])):
        return layout(
            "Updates",
            "<div class='card'><p><b>No previous version to go back "
            "to.</b> Focus Core only offers this when it kept the "
            "installer of the version you had before — this copy "
            "doesn't have one.</p>"
            "<p><a href='/update'>Back</a></p></div>",
            help_key="update"), 400

    active = store.get_active_session()
    if active:
        return layout(
            "Updates",
            "<div class='card'><p><b>Can't go back right now:</b> a "
            "focus session (" + escape(active.get("label") or "untitled")
            + ") is in progress. Finish or stop it first, then come "
            "back.</p><p><a href='/update'>Back</a></p></div>",
            help_key="update"), 400

    try:
        backup_mod.create_backup()
    except Exception as exc:  # noqa: BLE001 -- backup must not be skipped
        return layout(
            "Updates",
            "<div class='card'><p><b>Revert stopped:</b> the safety "
            "backup failed (" + escape(str(exc)) + "). Nothing was "
            "changed.</p><p><a href='/update'>Back</a></p></div>",
            help_key="update"), 500

    updater_mod.record_download(previous["installer"],
                                previous["version"])
    updater_mod.write_pending_install(previous["installer"],
                                      previous["version"])
    body = (
        "<div class='card'><h3>Reverting to "
        + escape(str(previous["version"])) + "...</h3>"
        "<p>The previous version's installer is kept on this computer "
        "and a safety backup is made. Focus Core will now close, "
        "reinstall the previous version, and reopen by itself — about "
        "a minute.</p>"
        "<p class='note'>If it doesn't reopen by itself, start it "
        "from the desktop icon as usual.</p></div>")
    return layout("Reverting", body, help_key="update")
