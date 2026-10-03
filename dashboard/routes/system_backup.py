"""Backup routes: page, erase, diagnostics, manual run, encryption, restore.

Split from dashboard/routes/system.py (roadmap 3.5) -- pure code
move, zero behavior change.
"""
import logging
from html import escape

from flask import Blueprint, redirect, request

from dashboard.app import layout
from focuscore import store

bp = Blueprint("system_backup", __name__)

logger = logging.getLogger(__name__)


@bp.route("/backup/erase", methods=["POST"])
def backup_erase():
    """Erase ALL user data (Roadmap 2.9: the "I'm leaving" button).

    Two-step by design: the Backup page makes the user type ERASE, and
    this route refuses anything but the exact word. Wipes every user
    table except the schema itself -- activity, daily totals, money
    records, gamification, settings (back to defaults). Not reversible.
    """
    from focuscore import retention as retention_mod
    if request.form.get("confirmation", "") != "ERASE":
        return redirect("/backup?erase_error=1")
    retention_mod.erase_all_data()
    return redirect("/backup?erased=1")


def _retention_card_html():
    """Data-retention setting card (Roadmap 2.9).

    Plain form POST like /settings/theme. Shows which window is active
    and states the two guarantees in plain English: daily totals are
    kept forever, money records are never auto-deleted.
    """
    from focuscore import retention as retention_mod
    months = retention_mod.get_retention_months()
    current = "forever" if months is None else str(months)
    options = []
    for value, label in (("3", "3 months"), ("6", "6 months"),
                         ("12", "12 months"), ("24", "24 months"),
                         ("forever", "Keep forever")):
        selected = " selected" if value == current else ""
        options.append(f"<option value='{value}'{selected}>{label}</option>")
    note = ""
    if request.args.get("retention_saved"):
        note = ("<p class='note' role='status'>Saved. Old detail will "
                "be removed the next time Focus Core starts.</p>")
    elif request.args.get("retention_error"):
        note = ("<p class='note' role='alert'>That value isn't allowed "
                "&mdash; nothing changed.</p>")
    window_label = "Keep forever" if months is None else f"{months} months"
    return (
        "<section class='wk-section' aria-label='Data retention'>"
        "<h2>Data retention</h2>"
        "<p>Focus Core keeps detailed activity for "
        f"<b>{window_label}</b>. Anything older is deleted when the app "
        "starts.</p>"
        "<form method='post' action='/settings/retention'>"
        "<label for='retention-months'>Delete activity detail older than"
        "</label> "
        f"<select name='retention_months' id='retention-months'>"
        f"{''.join(options)}</select> "
        f"<button type='submit'>Save</button></form>{note}"
        "<p class='note'>Your daily totals are kept forever, and invoices, "
        "timesheets and other money records are never auto-deleted "
        "&mdash; only the day-to-day detail goes. Totals like lifetime XP "
        "on the Focus page cover your kept history: pruning old detail "
        "also trims the XP tied to those days.</p></section>"
    )


def _erase_all_card_html():
    """The "erase all my data" two-step (Roadmap 2.9).

    Step 1 (button) reveals the confirmation; step 2 (form) requires
    typing ERASE. The server refuses anything but the exact word.
    """
    error = ""
    if request.args.get("erase_error"):
        error = ("<p class='note' role='alert'>Type <b>ERASE</b> exactly "
                 "as shown &mdash; nothing was deleted.</p>")
    done = ""
    if request.args.get("erased"):
        done = ("<p class='note' role='status'><b>Done.</b> All your "
                "Focus Core data has been erased. The app is starting "
                "fresh.</p>")
    # The delete list is rendered from retention.ERASE_BULLETS, whose
    # table sets must union to exactly ERASE_TABLES (pinned by test) --
    # the list can never silently omit a wiped table.
    from focuscore import retention as retention_mod
    bullets = "".join(
        f"<li>{label}</li>" for label, _tables in retention_mod.ERASE_BULLETS
    )
    return (
        "<section class='wk-section' aria-label='Erase all my data'>"
        "<h2>Erase all my data</h2>"
        "<p>Delete <b>everything</b> Focus Core knows about you, right "
        "here in the app &mdash; no need to uninstall.</p>"
        "<button type='button' class='danger' id='erase-show-btn'>"
        "Erase all my data</button>"
        "<div id='erase-confirm' hidden>"
        "<p><b>This will permanently delete:</b></p>"
        f"<ul>{bullets}</ul>"
        "<p class='note'>Also deleted: any emergency pass records "
        "(written only when the database was unreachable) and crash "
        "reports.</p>"
        "<p>There is no undo. If you might want this data later, "
        "<a href='/backup'>back it up</a> first.</p>"
        "<form method='post' action='/backup/erase'>"
        "<label for='erase-confirm-input'>Type <b>ERASE</b> to confirm"
        "</label> "
        "<input name='confirmation' id='erase-confirm-input' "
        "autocomplete='off' placeholder='ERASE'> "
        "<button type='submit' class='danger'>"
        "Yes, erase everything</button>"
        f"</form>{error}</div>{done}"
        "<script>"
        "document.getElementById('erase-show-btn').addEventListener("
        "'click', function () {"
        "var c = document.getElementById('erase-confirm');"
        "c.hidden = !c.hidden;"
        "if (!c.hidden) {"
        "document.getElementById('erase-confirm-input').focus();"
        "}});"
        "</script></section>"
    )


@bp.route("/backup")
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
        last_html = "<p>Last backup: <b>%s</b> (%d %s kept).</p>" % (
            last, len(backups), "backup" if len(backups) == 1 else "backups")
    else:
        last_html = "<p><b>No backups yet.</b> Make your first one now.</p>"

    rows = []
    for row_idx, b in enumerate(backups):
        size_kb = b["size_bytes"] / 1024.0
        marker = " <span class='note'>Encrypted</span>" if b.get("encrypted") else ""
        rows.append(
            "<tr><td><code>%s</code>%s</td><td>%s</td><td>%.0f KB</td>"
            "<td><form class='inline' method='post' "
            "action='/backup/restore' onsubmit=\"return confirm('Restore "
            "this backup? Your current data is first copied to a safety "
            "file, so nothing is lost.');\">"
            "<input type='hidden' name='name' value='%s'>"
            "<label class='sr-only' for='restore-pass-%d'>"
            "Passphrase for backup %s, if encrypted</label>"
            "<input type='password' name='passphrase' "
            "id='restore-pass-%d' "
            "placeholder='Passphrase if encrypted' autocomplete='off'>"
            "<button type='submit' class='secondary'>Restore</button>"
            "</form></td></tr>"
            % (escape(b["name"]), marker,
               b["modified"].strftime("%Y-%m-%d %H:%M"),
               size_kb, escape(b["name"]), row_idx,
               escape(b["name"]), row_idx))
    table = (
        "<table><tr><th>Backup</th><th>Made</th><th>Size</th><th></th></tr>"
        "%s</table>"
        % ("".join(rows)
           or "<tr><td colspan='4' class='note'>No backups yet.</td></tr>"))

    encryption_html = _backup_encryption_card_html(backup_mod)
    retention_html = _retention_card_html()
    erase_html = _erase_all_card_html()

    # Craft pass (work batch): the backup action is the hero; restore
    # second; everything else folds away. Same POST contracts.
    body = (
        "<section class='wk-backup-hero'>"
        "<div class='wk-backup-hero-state' role='status'>%s</div>"
        "<form method='post' action='/backup/now'>"
        "<button type='submit' class='wk-big'>Back up now</button></form>"
        "</section>"
        "<section class='wk-section'><h2>Restore a backup</h2>%s"
        "<p class='note'>Restoring first copies your current data to a "
        "safety file, so nothing is lost. If a backup is marked "
        "Encrypted, type the passphrase it was made with.</p></section>"
        "%s"
        "%s"
        "%s"
        "<section class='wk-section'><h2>Where your backups go</h2>%s</section>"
        "<p class='how-it-works'>Focus Core also backs up by itself every "
        "day when you start it (only if the last backup is older than 24 "
        "hours).</p>"
        "<section class='wk-section'><h2>Something not working?</h2>"
        "<p>Export a small diagnostics file and send it when you ask for "
        "help. It holds your Focus Core version, a short recent log, and "
        "your settings with secrets hidden. Your database and your "
        "activity data are never included.</p>"
        "<p><a class='btn' href='/backup/diagnostics'>"
        "<svg width='14' height='14' aria-hidden='true'>"
        "<use href='/static/icons.svg#icon-download'/></svg> "
        "Export diagnostics</a></p></section>"
        "<details class='wk-more'><summary>"
        "Your data &middot; Moving to a new laptop</summary>"
        "<h3>Your data</h3>"
        "<p class='note'>Everything lives on this PC in "
        "<code>focuscore.db</code>. Export: any timesheet day can be "
        "saved as CSV from the Timesheet page; a full copy is any backup "
        "from this page. Delete: to remove all your data, delete "
        "<code>focuscore.db</code> (make a backup first).</p>"
        "<h3>Moving to a new laptop</h3>"
        "<p class='note'>1. On the new laptop, install Focus Core and "
        "Google Drive, and let Drive finish syncing.<br>"
        "2. Open this Backup page on the new laptop and restore the "
        "newest backup from the list. If it is marked Encrypted, type "
        "the passphrase it was made with. Done &mdash; all your history "
        "is back.</p></details>"
        % (last_html, table, encryption_html, retention_html, erase_html,
           where_html)
    )
    return layout("Backup", body, active="backup")


def _backup_encryption_card_html(backup_mod):
    """Roadmap 1.5b: enable / disable / unlock card for /backup."""
    enabled = backup_mod.is_encryption_enabled()
    if not enabled:
        return (
            "<section class='wk-section'><h2>Backup encryption</h2>"
            "<p>Encryption is <b>off</b>. You can protect backups that "
            "leave this PC (for example the Google Drive copy) with a "
            "passphrase. Without your passphrase, nobody can open an "
            "encrypted backup.</p>"
            "<form method='post' action='/backup/encryption/enable'>"
            "<p><label>Passphrase (at least 8 characters)<br>"
            "<input type='password' name='passphrase' "
            "autocomplete='new-password'></label></p>"
            "<p><label>Type the passphrase again<br>"
            "<input type='password' name='passphrase_confirm' "
            "autocomplete='new-password'></label></p>"
            "<button type='submit' class='secondary'>"
            "Turn on backup encryption</button></form>"
            "<p class='note'>Only new backups are encrypted. Old "
            "backups stay as they are.</p>"
            "<p class='note'><b>Important:</b> if you forget your "
            "passphrase, your encrypted backups are unrecoverable. "
            "There is no recovery &mdash; not even by us. Please keep "
            "it somewhere safe.</p>"
            "<p class='note'>Changing your passphrase later does not "
            "change old backups: each backup keeps the passphrase it "
            "was made with.</p></section>")
    locked = backup_mod.is_locked()
    if locked:
        return (
            "<section class='wk-section'><h2>Backup encryption</h2>"
            "<p>Encryption is <b>on</b>, but this PC is locked: "
            "automatic backups cannot run until you unlock.</p>"
            "<form method='post' action='/backup/encryption/unlock'>"
            "<p><label>Passphrase<br>"
            "<input type='password' name='passphrase' "
            "autocomplete='current-password'></label></p>"
            "<button type='submit' class='secondary'>Unlock</button>"
            "</form>"
            "<form method='post' action='/backup/encryption/disable'>"
            "<p><label>Or turn encryption off &mdash; type your "
            "passphrase to confirm<br>"
            "<input type='password' name='passphrase' "
            "autocomplete='current-password'></label></p>"
            "<button type='submit' class='secondary'>"
            "Turn off backup encryption</button></form>"
            "<p class='note'>If you forget your passphrase, your "
            "encrypted backups are unrecoverable. There is no "
            "recovery &mdash; not even by us.</p>"
            "<p class='note'>Turning encryption off does not decrypt "
            "old backups: they stay encrypted with the passphrase "
            "they were made with. Changing your passphrase later "
            "does not change old backups either.</p></section>")
    return (
        "<section class='wk-section'><h2>Backup encryption</h2>"
        "<p>Encryption is <b>on</b>. New backups are protected with "
        "your passphrase, and automatic backups on this PC can run.</p>"
        "<form method='post' action='/backup/encryption/disable'>"
        "<p><label>Type your passphrase to confirm turning it off<br>"
        "<input type='password' name='passphrase' "
        "autocomplete='current-password'></label></p>"
        "<button type='submit' class='secondary'>"
        "Turn off backup encryption</button></form>"
        "<p class='note'>Turning encryption off does not decrypt "
        "old backups: they stay encrypted.</p>"
        "<p class='note'>If you forget your passphrase, your "
        "encrypted backups are unrecoverable. There is no recovery "
        "&mdash; not even by us.</p>"
        "<p class='note'>To use a different passphrase, turn "
        "encryption off and turn it on again with the new one. "
        "Changing your passphrase does not change old backups: each "
        "backup keeps the passphrase it was made with.</p></section>")


@bp.route("/backup/diagnostics")
def backup_diagnostics():
    """Roadmap 0.4: one-click diagnostics export.

    A small zip with version/OS info, an anonymized log tail, settings
    with secrets redacted, and a data-folder listing (names + sizes).
    The database and activity data are never included -- see
    focuscore/diagnostics.py for the hard privacy rules.
    """
    from flask import Response

    from focuscore import diagnostics
    return Response(
        diagnostics.build_diagnostics_zip(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=%s"
                 % diagnostics.zip_filename()})


@bp.route("/backup/now", methods=["POST"])
def backup_now():
    from focuscore import backup as backup_mod
    from focuscore.backupcrypto import BackupCryptoError

    try:
        backup_mod.create_backup()
    except (FileNotFoundError, backup_mod.BackupLockedError,
            BackupCryptoError) as exc:
        return layout("Backup",
                      "<div class='card'><p><b>Could not back up:</b> %s</p>"
                      "<p><a href='/backup'>Back</a></p></div>"
                      % escape(str(exc)), help_key="backup"), 400
    return redirect("/backup")


@bp.route("/backup/encryption/enable", methods=["POST"])
def backup_encryption_enable():
    from focuscore import backup as backup_mod
    from focuscore import backupcrypto

    passphrase = request.form.get("passphrase") or ""
    confirm = request.form.get("passphrase_confirm") or ""
    if len(passphrase) < 8:
        return layout("Backup",
                      "<div class='card'><p><b>Could not turn on "
                      "encryption:</b> Please use a passphrase of at "
                      "least 8 characters.</p>"
                      "<p><a href='/backup'>Back</a></p></div>"), 400
    if passphrase != confirm:
        return layout("Backup",
                      "<div class='card'><p><b>Could not turn on "
                      "encryption:</b> The two passphrases do not "
                      "match. Please try again.</p>"
                      "<p><a href='/backup'>Back</a></p></div>"), 400
    verifier = backupcrypto.make_passphrase_verifier(passphrase)
    store.set_setting(backup_mod.SETTING_PASSPHRASE_VERIFIER, verifier)
    store.set_setting(backup_mod.SETTING_ENCRYPTION_ENABLED, "1")
    # Cache only after the verifier is stored (write re-checks it).
    # A cache-write failure must not 500: encryption is already on
    # and the state is coherent (locked), so land on /backup, which
    # renders the locked card with the unlock form.
    try:
        backup_mod.write_cached_passphrase(passphrase)
    except Exception:
        logger.exception("could not cache the backup passphrase")
    return redirect("/backup")


@bp.route("/backup/encryption/disable", methods=["POST"])
def backup_encryption_disable():
    from focuscore import backup as backup_mod
    from focuscore import backupcrypto

    passphrase = request.form.get("passphrase") or ""
    verifier = store.get_setting(backup_mod.SETTING_PASSPHRASE_VERIFIER)
    if not verifier or not backupcrypto.verify_passphrase(passphrase, verifier):
        return layout("Backup",
                      "<div class='card'><p><b>Could not turn off "
                      "encryption:</b> That passphrase is not correct.</p>"
                      "<p><a href='/backup'>Back</a></p></div>"), 400
    store.set_setting(backup_mod.SETTING_ENCRYPTION_ENABLED, "0")
    store.set_setting(backup_mod.SETTING_PASSPHRASE_VERIFIER, "")
    backup_mod.clear_cached_passphrase()
    return redirect("/backup")


@bp.route("/backup/encryption/unlock", methods=["POST"])
def backup_encryption_unlock():
    from focuscore import backup as backup_mod
    from focuscore import backupcrypto

    passphrase = request.form.get("passphrase") or ""
    verifier = store.get_setting(backup_mod.SETTING_PASSPHRASE_VERIFIER)
    if not verifier or not backupcrypto.verify_passphrase(passphrase, verifier):
        return layout("Backup",
                      "<div class='card'><p><b>Could not unlock:</b> "
                      "That passphrase is not correct.</p>"
                      "<p><a href='/backup'>Back</a></p></div>"), 400
    backup_mod.write_cached_passphrase(passphrase)
    return redirect("/backup")


def _legacy_restore_confirm_html(name):
    """Roadmap 1.9: the confirmation step for a legacy backup.

    Shown when a restore is asked for a backup with no checksum
    sidecar (made before safety checks existed). The first POST
    restores nothing; only a second POST carrying the checkbox
    consent below restores. ``novalidate`` because the server, not
    the browser, decides whether consent was given.
    """
    return (
        "<div class='card'><h3>Before you restore this backup</h3>"
        "<p>You asked to restore <code>%s</code>.</p>"
        "<p>This backup was made before safety checks were added, so "
        "Focus Core cannot check it. Newer backups carry a small "
        "checkfile: if one of those is changed after it is made, the "
        "checkfile no longer matches and the restore is stopped. "
        "This older backup has no checkfile, so there is no way to "
        "tell whether it is still exactly as it was made.</p>"
        "<p>Restoring replaces your current data with the data in "
        "this backup, and that cannot be undone. First, Focus Core "
        "saves a safety copy of your current data &mdash; that safety "
        "copy is the only way back.</p>"
        "<form method='post' action='/backup/restore' novalidate>"
        "<input type='hidden' name='name' value='%s'>"
        "<p><label><input type='checkbox' "
        "name='confirm_legacy_restore'> "
        "I understand this backup could not be checked, because it "
        "was made before safety checks were added.</label></p>"
        "<p><input type='submit' value='Restore this backup anyway'>"
        "</p></form>"
        "<p><a href='/backup'>Back</a></p></div>"
        % (escape(name), escape(name)))


def _tampered_restore_html(name):
    """Roadmap 1.9: hard stop for a backup whose checkfile disagrees.

    A backup that looks damaged or changed is never offered a way
    through this page -- no consent form, no override. The words
    mirror the engine's own refusal (focuscore/backup.py), which is
    the backstop if this page is ever bypassed.
    """
    return (
        "<div class='card'><h3>Restore stopped</h3>"
        "<p>The backup <code>%s</code> failed its safety check: it "
        "looks damaged or was changed after it was made, so restoring "
        "it was stopped to protect your data.</p>"
        "<p>Please pick a different backup from the list.</p>"
        "<p><a href='/backup'>Back</a></p></div>"
        % escape(name))


@bp.route("/backup/restore", methods=["POST"])
def backup_restore():
    import warnings

    from focuscore import backup as backup_mod

    name = (request.form.get("name") or "").strip()
    passphrase = request.form.get("passphrase") or None
    # Roadmap 1.9: consent is exactly what the confirmation form's
    # checkbox submits ("on"); anything else -- absent, "false",
    # any other value -- is not consent. Classification is always
    # server-side (classify_backup); no client flag is trusted.
    consent = request.form.get("confirm_legacy_restore") == "on"

    classification = None
    if backup_mod.BACKUP_NAME_PATTERN.match(name):
        try:
            classification = backup_mod.classify_backup(name)
        except FileNotFoundError:
            classification = None  # restore below keeps today's 400
    if classification is not None and not classification["ok"]:
        if classification["reason"].startswith("legacy"):
            if not consent:
                return layout("Restore backup",
                              _legacy_restore_confirm_html(name),
                              active="backup"), 200
        else:
            # Tampered (or unreadable): hard stop, never a form.
            # restore_backup would refuse too -- this page is the
            # plain-language surface of that same refusal.
            return layout("Backup", _tampered_restore_html(name),
                          active="backup"), 400
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            safety = backup_mod.restore_backup(name, passphrase=passphrase)
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
        % (escape(name), legacy_note, escape(str(safety) if safety else "none — "
               "there was no previous database")))
    return layout("Backup restored", body, active="backup")
