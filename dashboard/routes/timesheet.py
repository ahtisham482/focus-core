"""Timesheet routes: day view, entry CRUD, exports, statement.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
import logging
from datetime import date
from html import escape

from flask import Response, abort, redirect, request

from focuscore import store

from dashboard.app import app
from dashboard.app import (
    _category_options,
    _export_filters,
    _export_panel_html,
    _parse_day,
    _project_cards_html,
    _project_options,
    _suggestion_timeline,
    json_dumps,
    layout,
)

logger = logging.getLogger(__name__)


@app.route("/timesheet")
def timesheet_page():
    from focuscore import timesheet as ts_mod

    day = _parse_day(request.args.get("day")) or date.today().isoformat()
    suggestions = ts_mod.suggest_blocks(day)
    entries = store.list_entries(day=day)
    projects = store.list_projects()
    locked = store.day_is_locked(day)

    # --- suggested blocks: the page's primary action, one-tap accept rows ---
    sug_items = []
    for i, block in enumerate(suggestions):
        if locked:
            accept_form = ("<span class='note'>Day locked</span>")
        else:
            accept_form = (
                "<form class='inline wk-accept' method='post' "
                "action='/timesheet/accept'>"
                "<input type='hidden' name='day' value='%s'>"
                "<input type='hidden' name='start_ts' value='%s'>"
                "<input type='hidden' name='end_ts' value='%s'>"
                "<input type='hidden' name='minutes' value='%.1f'>"
                "<input type='hidden' name='category' value='%s'>"
                "<input type='hidden' name='app' value='%s'>"
                "<input type='hidden' name='title_hint' value='%s'>"
                "<select name='project_id' aria-label='Project'>%s</select> "
                "<input type='text' name='task' placeholder='task' size='10' "
                "aria-label='Task'> "
                "<button type='submit'>Accept</button></form>"
                % (day, escape(block["start_ts"]), escape(block["end_ts"]),
                   block["minutes"], escape(block["category"]),
                   escape(block["app"]), escape(block["title_hint"]),
                   _project_options()))
        sug_items.append(
            "<div class='wk-sug' id='sug-%d'>"
            "<div class='wk-sug-info'><b>%s</b>"
            "<span>%s&ndash;%s &middot; %.0f min &middot; %s</span></div>"
            "%s</div>"
            % (i, escape(block["category"]),
               escape(block["start_ts"][11:]), escape(block["end_ts"][11:]),
               block["minutes"], escape(block["app"]), accept_form))
    sug_block = (
        "<div class='wk-sug-list'>%s</div>" % "".join(sug_items)
        if sug_items else
        "<p class='wk-empty'>Nothing to suggest for this day yet. Tracked "
        "activity shows up here as blocks you can accept with one tap.</p>")
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
                "it will not change the invoice — the invoice keeps its "
                "own snapshot of the line.'>Invoiced on %s — locked</span>"
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
    if entry_rows:
        entries_table = (
            "<div class='wk-strip'><table>"
            "<tr><th>Time</th><th>Category</th><th>App</th>"
            "<th>Project</th><th>Task</th><th>Note</th><th>Status</th>"
            "<th></th></tr>%s</table></div>" % "".join(entry_rows))
    else:
        entries_table = (
            "<p class='wk-empty'>No entries yet &mdash; accept a suggestion "
            "above or add one yourself.</p>")

    # --- projects (Phase 9: rate + budget cards) ---
    projects_html = _project_cards_html(day)

    # --- client export panel (Phase 9) ---
    export_html = _export_panel_html(day, projects)

    msg = escape(request.args.get("msg") or "")
    msg_html = ("<div class='card'><p><b>%s</b></p></div>" % msg) if msg else ""

    # Day-lock state: unmistakable either way.
    lock_html = (
        "<div class='wk-locked' role='status'>"
        "<b>This day is locked.</b> Entries can't be changed any more. "
        "Locking is permanent.</div>" if locked else
        "<form method='post' action='/timesheet/lock' class='wk-lock-form' "
        "onsubmit=\"return confirm('Lock this day? Entries cannot be edited "
        "afterwards.');\">"
        "<input type='hidden' name='day' value='%s'>"
        "<button type='submit' class='secondary'>Lock day</button> "
        "<span class='note'>Locking is permanent.</span></form>" % day)

    body = (
        "%s"
        "<div class='wk-daybar'>"
        "<form method='get' action='/timesheet'>"
        "<label>Day <input type='date' name='day' value='%s'></label> "
        "<button type='submit' class='secondary'>Show</button></form> "
        "<a href='/timesheet/export/client?from=%s&to=%s'>"
        "Export this day as CSV</a></div>"
        "<section class='wk-section'><h2>Suggested blocks</h2>%s%s</section>"
        "<section class='wk-section wk-create'><h2>Add it yourself</h2>"
        "<form method='post' action='/timesheet/add' class='sentence-form'>"
        "<input type='hidden' name='day' value='%s'>"
        "<p class='sentence'>I worked "
        "<input type='text' name='start_ts' required size='16' "
        "placeholder='2026-09-25T09:00' aria-label='Start'> &ndash; "
        "<input type='text' name='end_ts' required size='16' "
        "placeholder='2026-09-25T10:30' aria-label='End'> "
        "on <select name='category' aria-label='Category'>%s</select>"
        "<span class='nowrap'>, doing "
        "<input type='text' name='task' size='14' placeholder='what was it' "
        "aria-label='Task'>.</span></p>"
        "<details class='wk-details'><summary>More details "
        "(optional)</summary>"
        "<p><label>Project <select name='project_id'>%s</select></label> "
        "<label>App <input type='text' name='app' size='12'></label> "
        "<label>Title <input type='text' name='title' size='18'></label> "
        "<label>Note <input type='text' name='note' size='18'></label></p>"
        "</details>"
        "<p><button type='submit'>Add entry</button></p>"
        "</form></section>"
        "<section class='wk-section'><h2>Your entries</h2>%s%s</section>"
        "<details class='wk-more'><summary>"
        "Projects, rates &amp; budgets &middot; Client exports</summary>"
        "<h3>Projects, rates &amp; budgets</h3>"
        "<p class='note'>Set an hourly rate per project; new time entries "
        "use it automatically. Budgets are advisory only and every change "
        "is kept in history.</p>%s"
        "<h3>Client exports</h3>%s</details>"
        "<p class='how-it-works'>Suggested blocks are built from today's "
        "tracked activity &mdash; consecutive time in the same category "
        "becomes one block. Accepting copies a block into your timesheet; "
        "anything odd can be added by hand instead.</p>"
        % (msg_html, day, day, day, timeline_html, sug_block, day,
           _category_options(), _project_options(),
           lock_html, entries_table, projects_html, export_html)
    )

    total_minutes = sum(float(e.get("minutes", 0)) for e in entries)
    total_hours = total_minutes / 60.0
    hero_html = (
        "<div class='page-hero'>"
        "<div class='page-hero-text'>"
        "<h1 class='page-title'>Timesheet &middot; %s</h1>"
        "<p class='page-sub'>"
        "<span><b>%.1f</b> hours logged</span>"
        "<span class='page-sub-dot'>&middot;</span>"
        "<span><b>%d</b> entries</span>"
        "<span class='page-sub-dot'>&middot;</span>"
        "<span>Status: <b>%s</b></span>"
        "</p>"
        "</div>"
        "</div>" % (
            escape(day), total_hours, len(entries),
            "Locked" if locked else "Editable"
        )
    )

    return layout("Timesheet " + day, body, day, active="timesheet", hero=hero_html)




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
        logger.warning("timesheet project delete: invalid id, "
                       "nothing deleted")
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


