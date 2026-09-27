"""Phase 10: client invoicing (Qwen audit Q1-Q5, Q10-Q13, Q15, F1-F3).

Binding rules:
- Q1: sent/paid invoices are immutable. DB triggers (migration 8)
  enforce it; this module never attempts such writes. Drafts are the
  only editable state.
- Q2: invoice numbers are issued inside BEGIN IMMEDIATE at draft->sent,
  with an optimistic counter guard (UPDATE ... AND next_number = ?,
  rowcount must be 1). Drafts have number NULL.
- Q3: entry claiming happens in the same BEGIN IMMEDIATE transaction
  as draft creation; the claimed rowcount is checked (defence in
  depth). Voiding releases entries in the same transaction.
- Q4: balance = max(0, total - payments). Overpayments are recorded in
  full and surfaced as a UI warning; there is no 'overpaid' status.
- Q5: void + reissue is atomic; superseded_by_invoice_id is set once
  (only when currently NULL).
- Q11: invoice_lines rows are denormalized snapshots, never updated.
  Editing a timesheet entry after invoicing does not touch the line.
- Q12: tax/discount are integer percentages, snapshotted per invoice.
  Settings provide defaults for new drafts only.
- Q13: one currency per invoice; mixed-currency selections are
  rejected with a plain-English error.
- Q15: exactly 4 states (draft/sent/paid/void). The number is assigned
  only at draft->sent. paid and void are terminal.
- F1: all money math goes through focuscore.money (integer minor
  units). No float() anywhere in this module.
"""

from datetime import date, datetime, timedelta, timezone

from focuscore import money as money_mod
from focuscore import store

STATUSES = ("draft", "sent", "paid", "void")

# Sprint 4 (Qwen item 8): the invoice number format is INV-YYYY-NNNN --
# the sequence is 4 digits, so 9999 per year is the hard ceiling.
MAX_INVOICE_SEQ_PER_YEAR = 9999


class InvoiceError(Exception):
    """Base class for invoice errors (shown to the user as plain text)."""


class InvoiceLockedError(InvoiceError):
    """Raised when mutating a non-draft invoice."""


class DoubleBillingError(InvoiceError):
    """Raised when an entry is already linked to another invoice."""


class CurrencyMixError(InvoiceError):
    """Raised when selected entries span more than one currency."""


class CounterExhaustedError(InvoiceError):
    """Sprint 4: raised when a year's invoice sequence would exceed
    9999 (the INV-YYYY-NNNN format has 4 sequence digits)."""


class ClockJumpError(InvoiceError):
    """Sprint 4: raised when the system clock appears to have jumped
    backward (current year < highest year with issued invoices)."""


def _now_utc():
    return datetime.now().isoformat(timespec="seconds")


def _today():
    return date.today()


def _default_pct(key, path=None):
    """Integer 0..100 from settings; falls back to 0 on bad values."""
    try:
        value = int(store.get_setting(key, "0", path=path))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))


def _validate_pct(value, name):
    try:
        pct = int(value)
    except (TypeError, ValueError):
        raise InvoiceError("%s must be a whole number 0-100." % name)
    if pct < 0 or pct > 100:
        raise InvoiceError("%s must be between 0 and 100." % name)
    return pct


def _get_project(conn, project_id):
    row = conn.execute(
        "SELECT id, name, client, hourly_rate_minor, rate_currency "
        "FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise InvoiceError("Project not found.")
    return row


def _get_invoice(conn, invoice_id):
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        raise InvoiceError("Invoice not found.")
    return row


def _lines(conn, invoice_id):
    return conn.execute(
        "SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY sort_order, id",
        (invoice_id,),
    ).fetchall()


def _payments(conn, invoice_id):
    return conn.execute(
        "SELECT * FROM invoice_payments WHERE invoice_id = ? ORDER BY paid_date, id",
        (invoice_id,),
    ).fetchall()


def _paid_total(conn, invoice_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS s FROM invoice_payments "
        "WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()
    return int(row["s"] or 0)


def _default_currency(project_row, path=None):
    return (
        (project_row["rate_currency"] or "").upper()
        or (store.get_setting("currency", "USD", path=path) or "USD").upper()
    )


def _audit(entity_id, event_type, payload, path=None):
    from focuscore import budgets as budgets_mod

    budgets_mod.log_finance_event(
        "invoice", entity_id, event_type, payload, path=path
    )


# ------------------------------------------------------------- queries ---

def uninvoiced_entries(project_id, day_from, day_to, path=None):
    """Billable, accepted, uninvoiced entries that HAVE a rate (Q13: a
    line needs a single known currency; unrated entries are reported
    separately so the user can rate them first)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        rows = conn.execute(
            "SELECT id, day, start_ts, minutes, category, task, "
            "hourly_rate_minor, rate_currency, rate_status "
            "FROM timesheet_entries "
            "WHERE project_id = ? AND day >= ? AND day <= ? "
            "AND status = 'accepted' "
            "AND (is_billable IS NULL OR is_billable = 1) "
            "AND invoice_id IS NULL "
            "AND hourly_rate_minor IS NOT NULL "
            "ORDER BY day, start_ts",
            (project_id, day_from, day_to),
        ).fetchall()
        unrated = conn.execute(
            "SELECT COUNT(*) AS n FROM timesheet_entries "
            "WHERE project_id = ? AND day >= ? AND day <= ? "
            "AND status = 'accepted' "
            "AND (is_billable IS NULL OR is_billable = 1) "
            "AND invoice_id IS NULL "
            "AND hourly_rate_minor IS NULL",
            (project_id, day_from, day_to),
        ).fetchone()["n"]
        return [dict(r) for r in rows], int(unrated or 0)
    finally:
        conn.close()


def list_invoices(status=None, path=None):
    """Invoices newest first, each with derived balance/overdue info."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        query = (
            "SELECT i.*, p.name AS project_name, "
            "COALESCE((SELECT SUM(amount_minor) FROM invoice_payments "
            "WHERE invoice_id = i.id), 0) AS paid_minor "
            "FROM invoices i LEFT JOIN projects p ON p.id = i.project_id "
        )
        params = []
        if status in STATUSES:
            query += "WHERE i.status = ? "
            params.append(status)
        query += "ORDER BY i.id DESC"
        out = []
        for row in conn.execute(query, params).fetchall():
            out.append(_with_derived(dict(row)))
        return out
    finally:
        conn.close()


def _with_derived(inv):
    """Attach computed (never stored) fields: balance, overpaid, overdue."""
    total = int(inv.get("total_minor") or 0)
    paid = int(inv.get("paid_minor") or 0)
    inv["paid_minor"] = paid
    inv["balance_minor"] = max(0, total - paid)  # Q4: capped at zero
    inv["overpaid_minor"] = max(0, paid - total)
    inv["overdue"] = bool(
        inv.get("status") == "sent"
        and inv.get("due_date")
        and inv["due_date"] < _today().isoformat()
        and inv["balance_minor"] > 0
    )
    return inv


def get_invoice(invoice_id, path=None):
    """Full detail: header + lines + payments + derived totals/balance."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        inv = dict(_get_invoice(conn, invoice_id))
        proj = conn.execute(
            "SELECT name FROM projects WHERE id = ?", (inv["project_id"],)
        ).fetchone()
        inv["project_name"] = proj["name"] if proj else ""
        inv["paid_minor"] = _paid_total(conn, invoice_id)
        inv = _with_derived(inv)
        lines = [dict(r) for r in _lines(conn, invoice_id)]
        payments = [dict(r) for r in _payments(conn, invoice_id)]
        if inv["status"] == "draft":
            totals = compute_totals_for_lines(
                lines, inv["discount_pct"], inv["tax_pct"]
            )
        else:
            # Frozen at send time (Q1/Q12): the row is the record.
            totals = {
                "subtotal_minor": int(inv["subtotal_minor"] or 0),
                "discount_minor": int(inv["discount_amount_minor"] or 0),
                "tax_minor": int(inv["tax_amount_minor"] or 0),
                "total_minor": int(inv["total_minor"] or 0),
            }
        inv["totals"] = totals
        inv["is_frozen"] = inv["status"] != "draft"
        return {"invoice": inv, "lines": lines, "payments": payments}
    finally:
        conn.close()


def compute_totals_for_lines(lines, discount_pct, tax_pct):
    """Q12 formula. Pure integer math via money.pct_of_minor (F1)."""
    subtotal = sum(int(line["amount_minor_units"] or 0) for line in lines)
    discount_amount = money_mod.pct_of_minor(subtotal, discount_pct)
    taxable = subtotal - discount_amount
    tax_amount = money_mod.pct_of_minor(taxable, tax_pct)
    return {
        "subtotal_minor": subtotal,
        "discount_minor": discount_amount,
        "tax_minor": tax_amount,
        "total_minor": taxable + tax_amount,
    }


def compute_totals(invoice_id, path=None):
    """Live totals for a draft (display only; frozen on send)."""
    detail = get_invoice(invoice_id, path=path)
    return detail["invoice"]["totals"]


# ------------------------------------------------------------ creation ---

def _line_snapshot(entry, sort_order):
    """Denormalized frozen snapshot of one timesheet entry (Q11)."""
    seconds = int(round((entry["minutes"] or 0) * 60))
    rate_minor = entry["hourly_rate_minor"]
    amount = money_mod.amount_minor_for(seconds, rate_minor) or 0
    task = (entry.get("task") or "").strip()
    description = task or "%s — %s" % (entry.get("category") or "Work",
                                       entry.get("day") or "")
    return {
        "entry_id": entry["id"],
        "entry_date": entry.get("day") or "",
        "description": description,
        "seconds": seconds,
        "rate_minor": rate_minor,
        "amount_minor": amount,
        "currency": (entry.get("rate_currency") or "USD").upper(),
        "sort_order": sort_order,
    }


def _insert_line(conn, invoice_id, snap):
    cur = conn.execute(
        "INSERT INTO invoice_lines (invoice_id, timesheet_entry_id, "
        "entry_date, description, hours_minor_units, rate_minor_units, "
        "amount_minor_units, currency, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            invoice_id,
            snap["entry_id"],
            snap["entry_date"],
            snap["description"],
            snap["seconds"],
            snap["rate_minor"],
            snap["amount_minor"],
            snap["currency"],
            snap["sort_order"],
        ),
    )
    return cur.lastrowid


def _create_draft(conn, project_row, entry_rows, notes, tax_pct, discount_pct,
                  currency, supersedes_id=None):
    """Insert a draft invoice + snapshot lines. Caller owns the txn."""
    now = _now_utc()
    cur = conn.execute(
        "INSERT INTO invoices (project_id, status, currency, client, notes, "
        "discount_pct, tax_pct, supersedes_invoice_id, created_at, updated_at)"
        " VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_row["id"],
            currency,
            (project_row["client"] or "").strip(),
            (notes or "").strip(),
            discount_pct,
            tax_pct,
            supersedes_id,
            now,
            now,
        ),
    )
    invoice_id = cur.lastrowid
    for order, entry in enumerate(entry_rows):
        _insert_line(conn, invoice_id, _line_snapshot(entry, order))
    return invoice_id


def create_invoice(project_id, day_from, day_to, entry_ids=None, notes="",
                   tax_pct=None, discount_pct=None, path=None):
    """Create a DRAFT invoice (no number yet, Q15). Claims the entries
    inside BEGIN IMMEDIATE; rowcount mismatch -> DoubleBillingError and
    full rollback (Q3)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        project = _get_project(conn, project_id)
        if entry_ids is None:
            entries, _unrated = uninvoiced_entries(
                project_id, day_from, day_to, path=path
            )
            entry_rows = entries
        else:
            entry_rows = []
            for eid in entry_ids:
                row = conn.execute(
                    "SELECT * FROM timesheet_entries WHERE id = ?",
                    (eid,),
                ).fetchone()
                if row is None:
                    raise InvoiceError("Entry %s not found." % eid)
                row = dict(row)
                if (row.get("project_id") or 0) != int(project_id):
                    raise InvoiceError("Entry %s is not on this project." % eid)
                if row.get("status") != "accepted":
                    raise InvoiceError("Entry %s is not accepted." % eid)
                billable = row.get("is_billable")
                if billable is not None and billable != 1:
                    raise InvoiceError("Entry %s is not billable." % eid)
                if row.get("invoice_id") is not None:
                    raise DoubleBillingError(
                        "Entry %s is already on another invoice." % eid
                    )
                if row.get("hourly_rate_minor") is None:
                    raise InvoiceError(
                        "Entry %s has no rate yet; set a rate first." % eid
                    )
                entry_rows.append(row)
        if not entry_rows:
            raise InvoiceError(
                "No billable entries to invoice in that range."
            )
        # Q13: single currency per invoice.
        currencies = {((e.get("rate_currency") or "USD").upper())
                      for e in entry_rows}
        if len(currencies) > 1:
            raise CurrencyMixError(
                "Selected entries include multiple currencies (%s). "
                "Please create separate invoices per currency."
                % ", ".join(sorted(currencies))
            )
        currency = sorted(currencies)[0]
        tax_pct = (_default_pct("default_tax_pct", path) if tax_pct is None
                   else _validate_pct(tax_pct, "Tax"))
        discount_pct = (
            _default_pct("default_discount_pct", path)
            if discount_pct is None
            else _validate_pct(discount_pct, "Discount")
        )

        conn.execute("BEGIN IMMEDIATE")
        try:
            invoice_id = _create_draft(
                conn, project, entry_rows, notes, tax_pct, discount_pct,
                currency,
            )
            # Q3: claim inside the same transaction; verify every row.
            claimed = conn.execute(
                "UPDATE timesheet_entries SET invoice_id = ? "
                "WHERE id IN (%s) AND invoice_id IS NULL"
                % ",".join("?" * len(entry_rows)),
                (invoice_id, *[e["id"] for e in entry_rows]),
            ).rowcount
            if claimed != len(entry_rows):
                raise DoubleBillingError(
                    "One or more entries are already linked to an invoice."
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    _audit(invoice_id, "invoice_created",
           {"project_id": project_id, "lines": len(entry_rows),
            "currency": currency, "from": day_from, "to": day_to}, path=path)
    return invoice_id


# ---------------------------------------------------------- draft edits ---

def _require_draft(conn, invoice_id):
    inv = _get_invoice(conn, invoice_id)
    if inv["status"] != "draft":
        raise InvoiceLockedError(
            "Invoice is %s and can no longer be edited. "
            "Void it and reissue to make corrections." % inv["status"]
        )
    return inv


def add_manual_line(invoice_id, description, hours_text, rate_text, path=None):
    """Manual fixed-fee line on a draft (Q11: entry_id NULL)."""
    description = (description or "").strip()
    if not description:
        raise InvoiceError("Description is required.")
    seconds = money_mod.parse_hours_to_seconds(hours_text)
    if seconds is None or seconds <= 0:
        raise InvoiceError("Hours must be a positive number.")
    rate_minor = money_mod.parse_rate_to_minor(rate_text)
    if rate_minor is None or rate_minor <= 0:
        raise InvoiceError("Rate must be a positive number.")
    store.init_db(path)
    conn = store.get_db(path)
    try:
        inv = _require_draft(conn, invoice_id)
        currency = (inv["currency"] or "USD").upper()  # Q13
        snap = {
            "entry_id": None,
            "entry_date": _today().isoformat(),
            "description": description,
            "seconds": seconds,
            "rate_minor": rate_minor,
            "amount_minor": money_mod.amount_minor_for(seconds, rate_minor) or 0,
            "currency": currency,
            "sort_order": 9999,
        }
        conn.execute("BEGIN IMMEDIATE")
        try:
            line_id = _insert_line(conn, invoice_id, snap)
            conn.execute(
                "UPDATE invoices SET updated_at = ? WHERE id = ?",
                (_now_utc(), invoice_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    _audit(invoice_id, "invoice_line_changed",
           {"action": "add_manual_line", "description": description}, path=path)
    return line_id


def remove_line(invoice_id, line_id, path=None):
    """Remove a draft line; releases a linked entry (Q3)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        _require_draft(conn, invoice_id)
        line = conn.execute(
            "SELECT * FROM invoice_lines WHERE id = ? AND invoice_id = ?",
            (line_id, invoice_id),
        ).fetchone()
        if line is None:
            raise InvoiceError("Line not found.")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if line["timesheet_entry_id"] is not None:
                conn.execute(
                    "UPDATE timesheet_entries SET invoice_id = NULL "
                    "WHERE id = ? AND invoice_id = ?",
                    (line["timesheet_entry_id"], invoice_id),
                )
            conn.execute("DELETE FROM invoice_lines WHERE id = ?", (line_id,))
            conn.execute(
                "UPDATE invoices SET updated_at = ? WHERE id = ?",
                (_now_utc(), invoice_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    _audit(invoice_id, "invoice_line_changed",
           {"action": "remove_line", "line_id": line_id}, path=path)


def set_tax_discount(invoice_id, tax_pct, discount_pct, path=None):
    """Set integer-percentage tax/discount on a draft (Q12)."""
    tax_pct = _validate_pct(tax_pct, "Tax")
    discount_pct = _validate_pct(discount_pct, "Discount")
    store.init_db(path)
    conn = store.get_db(path)
    try:
        _require_draft(conn, invoice_id)
        conn.execute(
            "UPDATE invoices SET tax_pct = ?, discount_pct = ?, "
            "updated_at = ? WHERE id = ?",
            (tax_pct, discount_pct, _now_utc(), invoice_id),
        )
        conn.commit()
    finally:
        conn.close()
    _audit(invoice_id, "invoice_line_changed",
           {"action": "set_tax_discount", "tax_pct": tax_pct,
            "discount_pct": discount_pct}, path=path)


# ------------------------------------------------------------------ send ---

def _next_number(conn, year):
    """Q2: read the counter inside the caller's BEGIN IMMEDIATE txn."""
    row = conn.execute(
        "SELECT next_number FROM invoice_counters WHERE year = ?", (year,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO invoice_counters (year, next_number) VALUES (?, 1)",
            (year,),
        )
        return 1
    return int(row["next_number"])


def send_invoice(invoice_id, path=None):
    """draft -> sent: assign the sequential number (Q2/Q15), freeze
    totals, set issued/due dates. Atomic or not at all.

    Sprint 4 (Qwen item 8): the year is captured INSIDE the BEGIN
    IMMEDIATE transaction via a single datetime.now(timezone.utc) call
    (no TOCTOU across the year boundary); CounterExhaustedError when
    the sequence would exceed 9999; ClockJumpError when the clock
    jumped backward past the highest issued year.

    Sprint 4 (Qwen item 9): the transaction commits in milliseconds and
    commits BEFORE any HTML is rendered (the dashboard redirects after
    this returns)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            inv = _get_invoice(conn, invoice_id)
            if inv["status"] != "draft":
                raise InvoiceLockedError("Only drafts can be sent.")
            lines = [dict(r) for r in _lines(conn, invoice_id)]
            if not lines:
                raise InvoiceError("Cannot send an invoice with no lines.")
            currency = (inv["currency"] or "USD").upper()
            if any((line["currency"] or "USD").upper() != currency
                   for line in lines):
                raise CurrencyMixError(
                    "Line currencies do not match the invoice currency."
                )
            totals = compute_totals_for_lines(
                lines, inv["discount_pct"], inv["tax_pct"]
            )
            # Sprint 4: single UTC timestamp captured INSIDE the
            # transaction -- the year cannot change mid-send.
            now_utc = datetime.now(timezone.utc)
            year = now_utc.year
            # Clock-jump guard: never issue into a year older than the
            # highest year that already has numbers.
            max_year_row = conn.execute(
                "SELECT MAX(year) FROM invoice_counters").fetchone()
            max_year = (int(max_year_row[0])
                        if max_year_row and max_year_row[0] is not None
                        else year)
            if year < max_year:
                raise ClockJumpError(
                    "System clock appears to have moved backward "
                    "(year %d < %d). Fix the clock and try again."
                    % (year, max_year))
            seq = _next_number(conn, year)
            # Format guard: INV-YYYY-NNNN has 4 sequence digits.
            if seq > MAX_INVOICE_SEQ_PER_YEAR:
                raise CounterExhaustedError(
                    "Invoice numbers for %d are exhausted (limit %d). "
                    "Contact support." % (year, MAX_INVOICE_SEQ_PER_YEAR))
            number = "INV-%d-%04d" % (year, seq)
            due_days = 14
            try:
                due_days = int(store.get_setting(
                    "invoice_due_days", "14", path=path))
            except (TypeError, ValueError):
                due_days = 14
            due_date = (_today() + timedelta(days=max(0, due_days))).isoformat()
            updated = conn.execute(
                "UPDATE invoices SET number = ?, status = 'sent', "
                "subtotal_minor = ?, discount_amount_minor = ?, "
                "tax_amount_minor = ?, total_minor = ?, "
                "issued_at = ?, due_date = ?, updated_at = ? "
                "WHERE id = ? AND status = 'draft'",
                (
                    number, totals["subtotal_minor"],
                    totals["discount_minor"], totals["tax_minor"],
                    totals["total_minor"], _today().isoformat(), due_date,
                    _now_utc(), invoice_id,
                ),
            ).rowcount
            if updated != 1:
                raise InvoiceError("Invoice could not be sent.")
            # Q2 optimistic guard: exactly one counter row must advance.
            bumped = conn.execute(
                "UPDATE invoice_counters SET next_number = ? "
                "WHERE year = ? AND next_number = ?",
                (seq + 1, year, seq),
            ).rowcount
            if bumped != 1:
                raise InvoiceError(
                    "Invoice number conflict; please try again."
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    _audit(invoice_id, "invoice_sent",
           {"number": number, "total_minor": totals["total_minor"],
            "currency": currency, "lines": len(lines)}, path=path)
    return number


# -------------------------------------------------------------- payments ---

def record_payment(invoice_id, paid_date, amount_minor, note="", path=None):
    """Append-only payment (Q4). Auto-flips sent->paid when covered.
    Returns (became_paid, balance_minor, overpaid_minor)."""
    try:
        amount_minor = int(amount_minor)
    except (TypeError, ValueError):
        raise InvoiceError("Payment amount is invalid.")
    if amount_minor <= 0:
        raise InvoiceError("Payment amount must be positive.")
    try:
        paid_day = date.fromisoformat((paid_date or "").strip())
    except ValueError:
        raise InvoiceError("Payment date must be YYYY-MM-DD.")
    if paid_day > _today():
        raise InvoiceError("Payment date cannot be in the future.")
    store.init_db(path)
    conn = store.get_db(path)
    try:
        inv = _get_invoice(conn, invoice_id)
        if inv["status"] not in ("sent", "paid"):
            raise InvoiceLockedError(
                "Payments can only be recorded on sent invoices."
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO invoice_payments (invoice_id, paid_date, "
                "amount_minor, note, created_at_utc) "
                "VALUES (?, ?, ?, ?, ?)",
                (invoice_id, paid_day.isoformat(), amount_minor,
                 (note or "").strip(), _now_utc()),
            )
            paid_total = _paid_total(conn, invoice_id)
            total = int(inv["total_minor"] or 0)
            became_paid = False
            if inv["status"] == "sent" and paid_total >= total:
                # Q15: the ONLY auto-transition. Trigger allows sent->paid.
                changed = conn.execute(
                    "UPDATE invoices SET status = 'paid', updated_at = ? "
                    "WHERE id = ? AND status = 'sent'",
                    (_now_utc(), invoice_id),
                ).rowcount
                became_paid = changed == 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    balance = max(0, total - paid_total)  # Q4: capped at zero
    overpaid = max(0, paid_total - total)
    _audit(invoice_id, "payment_recorded",
           {"amount_minor": amount_minor, "paid_date": paid_day.isoformat(),
            "balance_minor": balance, "overpaid_minor": overpaid,
            "became_paid": became_paid}, path=path)
    return became_paid, balance, overpaid


def mark_paid(invoice_id, path=None):
    """Manual paid flip for zero-total sent invoices (100% discount)."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        inv = _get_invoice(conn, invoice_id)
        if inv["status"] != "sent":
            raise InvoiceLockedError("Only sent invoices can be marked paid.")
        if int(inv["total_minor"] or 0) != 0:
            raise InvoiceError(
                "Only zero-total invoices can be marked paid manually; "
                "record a payment otherwise."
            )
        conn.execute(
            "UPDATE invoices SET status = 'paid', updated_at = ? "
            "WHERE id = ? AND status = 'sent'",
            (_now_utc(), invoice_id),
        )
        conn.commit()
    finally:
        conn.close()
    _audit(invoice_id, "payment_recorded",
           {"amount_minor": 0, "manual_zero_total": True,
            "became_paid": True}, path=path)


# ------------------------------------------------------------------ void ---

def _void_in_txn(conn, invoice_id, reason):
    """Assumes caller holds BEGIN IMMEDIATE. Returns the old row."""
    if not (reason or "").strip():
        raise InvoiceError("A reason is required to void an invoice.")
    inv = _get_invoice(conn, invoice_id)
    if inv["status"] not in ("draft", "sent"):
        raise InvoiceLockedError(
            "Only drafts and sent invoices can be voided."
        )
    changed = conn.execute(
        "UPDATE invoices SET status = 'void', void_reason = ?, "
        "updated_at = ? WHERE id = ? AND status IN ('draft', 'sent')",
        (reason.strip(), _now_utc(), invoice_id),
    ).rowcount
    if changed != 1:
        raise InvoiceError("Invoice could not be voided.")
    # Q3: release entries in the same transaction (lines stay as history).
    conn.execute(
        "UPDATE timesheet_entries SET invoice_id = NULL WHERE invoice_id = ?",
        (invoice_id,),
    )
    return inv


def void_invoice(invoice_id, reason, path=None):
    store.init_db(path)
    conn = store.get_db(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _void_in_txn(conn, invoice_id, reason)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    _audit(invoice_id, "invoice_voided", {"reason": reason.strip()},
           path=path)


def void_and_reissue(invoice_id, reason, path=None):
    """Q5: void the sent invoice AND create its replacement draft in ONE
    transaction. The old invoice points at the new one exactly once."""
    store.init_db(path)
    conn = store.get_db(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = _void_in_txn(conn, invoice_id, reason)
            if old["status"] != "sent":
                raise InvoiceError("Only sent invoices can be reissued.")
            if old["superseded_by_invoice_id"] is not None:
                raise InvoiceError(
                    "This invoice was already reissued once."
                )
            # Re-snapshot the released entries into a fresh draft.
            entry_rows = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM timesheet_entries WHERE id IN "
                    "(SELECT timesheet_entry_id FROM invoice_lines "
                    "WHERE invoice_id = ? AND timesheet_entry_id IS NOT NULL)",
                    (invoice_id,),
                ).fetchall()
            ]
            project = _get_project(conn, old["project_id"])
            new_id = _create_draft(
                conn, project, entry_rows, old["notes"],
                old["tax_pct"], old["discount_pct"], old["currency"],
                supersedes_id=invoice_id,
            )
            # Manual (non-entry) lines are copied verbatim: their
            # snapshots are the record, there is nothing to re-snapshot.
            order = len(entry_rows)
            for mline in conn.execute(
                "SELECT description, hours_minor_units, rate_minor_units, "
                "amount_minor_units, currency FROM invoice_lines "
                "WHERE invoice_id = ? AND timesheet_entry_id IS NULL "
                "ORDER BY sort_order, id",
                (invoice_id,),
            ).fetchall():
                conn.execute(
                    "INSERT INTO invoice_lines (invoice_id, "
                    "timesheet_entry_id, entry_date, description, "
                    "hours_minor_units, rate_minor_units, "
                    "amount_minor_units, currency, sort_order) "
                    "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id, _today().isoformat(), mline["description"],
                        mline["hours_minor_units"],
                        mline["rate_minor_units"],
                        mline["amount_minor_units"], mline["currency"],
                        order,
                    ),
                )
                order += 1
            if entry_rows:
                claimed = conn.execute(
                    "UPDATE timesheet_entries SET invoice_id = ? "
                    "WHERE id IN (%s) AND invoice_id IS NULL"
                    % ",".join("?" * len(entry_rows)),
                    (new_id, *[e["id"] for e in entry_rows]),
                ).rowcount
                if claimed != len(entry_rows):
                    raise DoubleBillingError(
                        "Entries changed during reissue; aborted."
                    )
            linked = conn.execute(
                "UPDATE invoices SET superseded_by_invoice_id = ? "
                "WHERE id = ? AND superseded_by_invoice_id IS NULL",
                (new_id, invoice_id),
            ).rowcount
            if linked != 1:
                raise InvoiceError("Reissue link could not be recorded.")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    _audit(invoice_id, "invoice_voided",
           {"reason": reason.strip(), "reissued_as": new_id}, path=path)
    _audit(new_id, "invoice_created",
           {"reissue_of": invoice_id, "project_id": old["project_id"]},
           path=path)
    return new_id


# ------------------------------------------------------------------ print ---

_CSP_META = (
    '<meta http-equiv="Content-Security-Policy" '
    'content="default-src \'none\'; style-src \'unsafe-inline\'; '
    'img-src data:;">'
)

_INVOICE_TEMPLATE = """\
<!DOCTYPE html>
<!-- SECURITY: This template must remain script-free. Do not add JavaScript.
     User content is auto-escaped by Jinja2; URLs are never rendered as
     links (plain text only), so no scheme validation is needed. -->
<html lang="en">
<head>
<meta charset="utf-8">
{{ csp_meta | safe }}
<title>Invoice {{ inv.number }}</title>
<style>
  body { font-family: Georgia, serif; color: #111; margin: 40px; }
  h1 { font-size: 28px; margin-bottom: 4px; }
  .meta { color: #555; margin-bottom: 24px; }
  table { width: 100%; border-collapse: collapse; margin: 16px 0; }
  th, td { border: 1px solid #999; padding: 8px; text-align: left; }
  th { background: #eee; }
  td.num, th.num { text-align: right; }
  .totals td { border: none; }
  .totals tr.total td { font-weight: bold; border-top: 2px solid #111; }
  .void { color: #a00; font-weight: bold; }
  .fine { color: #666; font-size: 12px; }
  @media print { body { margin: 0; } }
</style>
</head>
<body>
<h1>Invoice {{ inv.number }}</h1>
{% if inv.status == 'void' %}<p class="void">VOID — {{ inv.void_reason }}</p>{% endif %}
<div class="meta">
  <div>Bill to: {{ inv.client or '—' }}</div>
  <div>Project: {{ inv.project_name }}</div>
  <div>Issued: {{ inv.issued_at or '—' }} &middot; Due: {{ inv.due_date or '—' }}</div>
  <div>Status: {{ inv.status }}</div>
</div>
<table>
  <tr><th>Date</th><th>Description</th><th class="num">Hours</th>
      <th class="num">Rate</th><th class="num">Amount</th></tr>
  {% for l in lines %}
  <tr><td>{{ l.entry_date }}</td><td>{{ l.description }}</td>
      <td class="num">{{ l.hours_text }}</td>
      <td class="num">{{ l.rate_text }}</td>
      <td class="num">{{ l.amount_text }}</td></tr>
  {% endfor %}
</table>
<table class="totals">
  <tr><td>Subtotal</td><td class="num">{{ totals.subtotal_text }}</td></tr>
  {% if inv.discount_pct %}<tr><td>Discount ({{ inv.discount_pct }}%)</td>
      <td class="num">−{{ totals.discount_text }}</td></tr>{% endif %}
  {% if inv.tax_pct %}<tr><td>Tax ({{ inv.tax_pct }}%)</td>
      <td class="num">{{ totals.tax_text }}</td></tr>{% endif %}
  <tr class="total"><td>Total ({{ inv.currency }})</td>
      <td class="num">{{ totals.total_text }}</td></tr>
  <tr><td>Payments received</td><td class="num">{{ paid_text }}</td></tr>
  <tr class="total"><td>Balance due</td><td class="num">{{ balance_text }}</td></tr>
</table>
{% if inv.notes %}<p><strong>Notes:</strong> {{ inv.notes }}</p>{% endif %}
<p class="fine">Generated by Focus Core {{ app_version }} on {{ generated_at }}.
Standalone document: no external resources, no scripts.</p>
</body>
</html>
"""


def render_invoice_html(invoice_id, path=None):
    """Standalone printable invoice HTML (Q10): Jinja2 autoescape, CSP
    meta, zero JS, inline CSS only. Q9: this never includes forecasts."""
    from jinja2 import Environment

    detail = get_invoice(invoice_id, path=path)
    inv = detail["invoice"]
    currency = (inv["currency"] or "USD").upper()
    lines = []
    for line in detail["lines"]:
        lines.append({
            "entry_date": line["entry_date"] or "",
            "description": line["description"] or "",
            "hours_text": money_mod.format_hours(
                line["hours_minor_units"] or 0),
            "rate_text": (money_mod.format_minor(line["rate_minor_units"],
                                                 currency) + "/hr"
                          if line["rate_minor_units"] else "—"),
            "amount_text": money_mod.format_minor(line["amount_minor_units"],
                                                  currency),
        })
    t = inv["totals"]
    totals = {
        "subtotal_text": money_mod.format_minor(t["subtotal_minor"], currency),
        "discount_text": money_mod.format_minor(t["discount_minor"], currency),
        "tax_text": money_mod.format_minor(t["tax_minor"], currency),
        "total_text": money_mod.format_minor(t["total_minor"], currency),
    }
    env = Environment(autoescape=True)  # Q10: auto-escape, not hand-rolled
    template = env.from_string(_INVOICE_TEMPLATE)
    return template.render(
        csp_meta=_CSP_META,
        inv=inv,
        lines=lines,
        totals=totals,
        paid_text=money_mod.format_minor(inv["paid_minor"], currency),
        balance_text=money_mod.format_minor(inv["balance_minor"], currency),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        app_version="1.11.0",
    )
