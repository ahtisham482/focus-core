"""Invoice routes: list, create, send, pay, void, reissue, print.

Sprint 4 (Qwen item 11): split from dashboard/app.py.
Zero URL changes, zero HTML changes -- pure code move.
"""
from datetime import date
from html import escape

from flask import Blueprint, Response, abort, redirect, request

from focuscore import store
from dashboard.app import (
    _invoice_action_redirect,
    _invoice_detail_body,
    _invoice_status_badge,
    _parse_day,
    layout,
)

bp = Blueprint("invoices", __name__)

@bp.route("/invoices")
def invoices_page():
    """List invoices with derived balance/overdue info (Q4/Q15)."""
    from focuscore import invoices as inv_mod
    from focuscore import money as money_mod

    status = request.args.get("status") or ""
    msg = request.args.get("msg") or ""
    invoices = inv_mod.list_invoices(
        status=status if status in inv_mod.STATUSES else None)
    outstanding = 0
    draft_cards = []
    history_rows = []
    for inv in invoices:
        currency = (inv.get("currency") or "USD").upper()
        if inv["status"] == "sent":
            outstanding += inv["balance_minor"]
        title = inv["number"] or "DRAFT #%d" % inv["id"]
        if inv["status"] == "draft":
            # Draft totals are computed live on the detail page; the stored
            # row is only filled in at send time, so compute here for an
            # honest figure (read-only, no backend change).
            try:
                dt = inv_mod.get_invoice(inv["id"])
                draft_total = dt["invoice"]["totals"]["total_minor"]
            except Exception:
                draft_total = inv.get("total_minor") or 0
            draft_cards.append(
                "<div class='wk-inv-draft'>"
                "<div class='wk-inv-draft-info'>"
                "<b><a href='/invoices/%d'>%s</a></b>"
                "<span>%s &middot; %s &middot; "
                "<span data-financial>%s</span></span></div>"
                "<div class='wk-inv-draft-actions'>"
                "<form class='inline' method='post' "
                "action='/invoices/%d/send' "
                "onsubmit=\"return confirm('Send this invoice? It will be "
                "numbered and frozen.');\">"
                "<button type='submit'>Send</button></form> "
                "<a class='btn secondary' href='/invoices/%d'>"
                "Keep editing</a></div></div>"
                % (inv["id"], escape(title),
                   escape(inv.get("project_name") or ""),
                   escape(inv.get("client") or ""),
                   money_mod.format_minor(draft_total, currency),
                   inv["id"], inv["id"]))
            continue
        overdue = " <strong>OVERDUE</strong>" if inv["overdue"] else ""
        history_rows.append(
            "<tr><td><a href='/invoices/%d'>%s</a></td>"
            "<td>%s</td><td>%s</td><td>%s</td>"
            "<td style='text-align:right' data-financial>%s</td>"
            "<td style='text-align:right' data-financial>%s</td><td>%s</td></tr>"
            % (inv["id"], escape(title),
               escape(inv.get("project_name") or ""),
               escape(inv.get("client") or ""),
               _invoice_status_badge(inv["status"]),
               money_mod.format_minor(inv.get("total_minor") or 0, currency),
               money_mod.format_minor(inv["balance_minor"], currency),
               overdue))
    drafts_html = (
        "<section class='wk-section'><h2>Finish these first</h2>%s</section>"
        % "".join(draft_cards)
        if draft_cards else
        "<p class='wk-empty'>No drafts waiting. Start a new invoice when "
        "you have billable hours to send.</p>")
    history_html = (
        "<table class='tbl'><tr><th>Invoice</th><th>Project</th>"
        "<th>Client</th><th>Status</th><th style='text-align:right'>Total</th>"
        "<th style='text-align:right'>Balance</th><th></th></tr>%s</table>"
        % ("".join(history_rows)
           or "<tr><td colspan='7' class='note'>Nothing sent yet.</td></tr>"))
    body = (
        "<div class='wk-inv-hero'>"
        "<div><span class='note'>Outstanding on sent invoices</span><br>"
        "<b data-financial>%s</b></div>"
        "<a class='btn' href='/invoices/new'>New invoice</a></div>"
        "%s%s"
        "<section class='wk-section'><h2>History</h2>"
        "<p class='wk-filters'><a href='/invoices'>All</a>"
        " &middot; <a href='/invoices?status=draft'>Drafts</a>"
        " &middot; <a href='/invoices?status=sent'>Sent</a>"
        " &middot; <a href='/invoices?status=paid'>Paid</a>"
        " &middot; <a href='/invoices?status=void'>Void</a></p>"
        "%s</section>"
        "<p class='how-it-works'>Invoices are numbered when sent; sent "
        "invoices are frozen and can only be voided, never edited. Drafts "
        "can be changed freely until you send them.</p>"
        % (money_mod.format_minor(outstanding),
           "<p class='msg'>%s</p>" % escape(msg) if msg else "",
           drafts_html, history_html)
    )
    return layout("Invoices", body, active="invoices")


@bp.route("/invoices/new")
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
        "<form method='get' action='/invoices/new' class='sentence-form'>"
        "<p class='sentence'>I want to bill "
        "<label class='sr-only' for='inv-project'>Project</label>"
        "<select name='project_id' id='inv-project' aria-label='Project'>"
        "<option value=''>-- choose --</option>%s</select> "
        "for work from "
        "<label class='sr-only' for='inv-from'>From</label>"
        "<input type='date' name='from' id='inv-from' value='%s' aria-label='From'> "
        "to <label class='sr-only' for='inv-to'>To</label>"
        "<input type='date' name='to' id='inv-to' value='%s' aria-label='To'>"
        "<span class='nowrap'>.</span></p>"
        "<p><button type='submit'>Show entries</button></p></form>"
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
                    "<label class='wk-pick'>"
                    "<input type='checkbox' name='entry_id' "
                    "value='%d' checked>"
                    "<span class='wk-pick-info'><b>%s</b>"
                    "<span>%s%s &middot; %s</span></span>"
                    "<b data-financial>%s</b></label>"
                    % (e["id"], escape(e["day"]), escape(label), badge,
                       money_mod.format_duration(seconds),
                       money_mod.format_minor(amount, currency)))
            entries_html += (
                "<form method='post' action='/invoices/create' "
                "class='wk-create-form'>"
                "<input type='hidden' name='project_id' value='%d'>"
                "<input type='hidden' name='from' value='%s'>"
                "<input type='hidden' name='to' value='%s'>"
                "<h3>Tick what goes on the invoice</h3>"
                "<div class='wk-pick-list'>%s</div>"
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
            entries_html = ("<p class='wk-empty'>No uninvoiced billable "
                            "entries with a rate in that range. Log time on "
                            "the Timesheet page first, or widen the dates.</p>")

    body = ("<section class='wk-section wk-create'>"
            "<p class='note'>A draft is created first: no invoice number "
            "until you send it, and drafts can be edited freely. One "
            "currency per invoice.</p>"
            "%s%s</section>"
            "<p class='how-it-works'>Pick the entries to bill, then a draft "
            "invoice is made from them. You send it when it looks right."
            "</p>" % (filter_form, entries_html))
    return layout("New invoice", body, active="invoices")


@bp.route("/invoices/create", methods=["POST"])
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


@bp.route("/invoices/<int:invoice_id>")
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


@bp.route("/invoices/<int:invoice_id>/add-line", methods=["POST"])
def invoice_add_line(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.add_manual_line, invoice_id,
        request.form.get("description"), request.form.get("hours"),
        request.form.get("rate"))


@bp.route("/invoices/<int:invoice_id>/remove-line", methods=["POST"])
def invoice_remove_line(invoice_id):
    from focuscore import invoices as inv_mod

    try:
        line_id = int(request.form.get("line_id"))
    except (TypeError, ValueError):
        return redirect("/invoices/%d?msg=%s"
                        % (invoice_id, "Bad+line+id."))
    return _invoice_action_redirect(
        invoice_id, inv_mod.remove_line, invoice_id, line_id)


@bp.route("/invoices/<int:invoice_id>/tax-discount", methods=["POST"])
def invoice_tax_discount(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.set_tax_discount, invoice_id,
        request.form.get("tax_pct"), request.form.get("discount_pct"))


@bp.route("/invoices/<int:invoice_id>/send", methods=["POST"])
def invoice_send(invoice_id):
    from focuscore import invoices as inv_mod

    try:
        number = inv_mod.send_invoice(invoice_id)
        msg = "Sent as %s." % number
    except inv_mod.InvoiceError as exc:
        msg = str(exc)
    return redirect("/invoices/%d?msg=%s"
                    % (invoice_id, msg.replace(" ", "+")))


@bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
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


@bp.route("/invoices/<int:invoice_id>/mark-paid", methods=["POST"])
def invoice_mark_paid(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.mark_paid, invoice_id)


@bp.route("/invoices/<int:invoice_id>/void", methods=["POST"])
def invoice_void(invoice_id):
    from focuscore import invoices as inv_mod

    return _invoice_action_redirect(
        invoice_id, inv_mod.void_invoice, invoice_id,
        request.form.get("reason") or "Other")


@bp.route("/invoices/<int:invoice_id>/reissue", methods=["POST"])
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


@bp.route("/invoices/<int:invoice_id>/print")
def invoice_print(invoice_id):
    """Standalone printable invoice (Q10: CSP, zero JS, auto-escaped)."""
    from focuscore import invoices as inv_mod

    try:
        page_html = inv_mod.render_invoice_html(invoice_id)
    except inv_mod.InvoiceError:
        abort(404)
    return Response(page_html, mimetype="text/html")


