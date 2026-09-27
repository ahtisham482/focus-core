"""Phase 10: invoicing engine. Qwen audit Q1-Q5, Q10-Q13, Q15."""

import sqlite3
import threading

import pytest

from focuscore import invoices, store


def _project(db, name="Acme"):
    return store.add_project(name, client="Acme Corp", path=db)


def _entry(db, pid, day, minutes, rate_minor=10000, currency="USD"):
    """Billable entry with a confirmed rate snapshot."""
    eid = store.create_entry(day, day + "T09:00", day + "T10:00",
                             minutes, "Work", project_id=pid,
                             task="Dev", status="accepted", path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET hourly_rate_minor = ?, "
            "rate_currency = ?, rate_status = 'confirmed' WHERE id = ?",
            (rate_minor, currency, eid))
        conn.commit()
    finally:
        conn.close()
    return eid


def _draft(db, pid, day="2026-09-20", minutes=60.0, **kw):
    eid = _entry(db, pid, day, minutes)
    return invoices.create_invoice(pid, day, day, entry_ids=[eid], path=db,
                                   **kw)


# ------------------------------------------------------------ totals ---

def test_draft_totals_integer_math(tmp_path):
    db = str(tmp_path / "i.db")
    pid = _project(db)
    inv_id = _draft(db, pid, tax_pct=20, discount_pct=10)
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    # 1h @ $100 = 10000; discount 10% = 1000; taxable 9000; tax 20% = 1800.
    assert d["totals"] == {"subtotal_minor": 10000, "discount_minor": 1000,
                           "tax_minor": 1800, "total_minor": 10800}
    assert d["tax_pct"] == 20 and d["discount_pct"] == 10


def test_tax_discount_integer_pct_not_basis_points(tmp_path):
    db = str(tmp_path / "i2.db")
    pid = _project(db)
    # 50% tax on $100 -> 5000, proving pct is whole percent.
    inv_id = _draft(db, pid, tax_pct=50)
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["totals"]["tax_minor"] == 5000


def test_tax_default_change_does_not_alter_existing_invoice(tmp_path):
    db = str(tmp_path / "i3.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    store.set_setting("default_tax_pct", "25", path=db)
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["tax_pct"] == 0
    assert d["totals"]["total_minor"] == 10000


# ------------------------------------------------------------ sending ---

def test_send_assigns_number_and_freezes(tmp_path):
    db = str(tmp_path / "i4.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    number = invoices.send_invoice(inv_id, path=db)
    assert number.startswith("INV-")
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["number"] == number
    assert d["status"] == "sent"
    assert d["is_frozen"]
    assert d["due_date"]  # due date derived from invoice_due_days


def test_draft_has_no_number(tmp_path):
    db = str(tmp_path / "i5.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["number"] is None
    assert d["status"] == "draft"


def test_sent_invoice_is_immutable(tmp_path):
    db = str(tmp_path / "i6.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    invoices.send_invoice(inv_id, path=db)
    with pytest.raises(invoices.InvoiceLockedError):
        invoices.set_tax_discount(inv_id, 10, 0, path=db)
    with pytest.raises(invoices.InvoiceLockedError):
        invoices.add_manual_line(inv_id, "Extra", "1", "50", path=db)
    line = invoices.get_invoice(inv_id, path=db)["lines"][0]
    with pytest.raises(invoices.InvoiceLockedError):
        invoices.remove_line(inv_id, line["id"], path=db)
    # DB layer also rejects direct mutation.
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE invoice_lines SET amount_minor_units = 1 "
                         "WHERE invoice_id = ?", (inv_id,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE invoices SET total_minor = 1 WHERE id = ?",
                         (inv_id,))
    finally:
        conn.close()


def test_status_machine_rejects_bad_transitions(tmp_path):
    db = str(tmp_path / "i7.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    invoices.send_invoice(inv_id, path=db)
    invoices.record_payment(inv_id, "2026-09-27", 10000, path=db)
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["status"] == "paid"
    # paid is terminal: void is rejected.
    with pytest.raises(invoices.InvoiceLockedError):
        invoices.void_invoice(inv_id, "Other", path=db)
    with pytest.raises(invoices.InvoiceLockedError):
        invoices.mark_paid(inv_id, path=db)


# ------------------------------------------------------ double bill ---

def test_double_billing_rejected(tmp_path):
    db = str(tmp_path / "i8.db")
    pid = _project(db)
    eid = _entry(db, pid, "2026-09-20", 60.0)
    invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                            entry_ids=[eid], path=db)
    with pytest.raises(invoices.DoubleBillingError):
        invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                                entry_ids=[eid], path=db)


def test_void_releases_entries_for_reinvoicing(tmp_path):
    db = str(tmp_path / "i9.db")
    pid = _project(db)
    eid = _entry(db, pid, "2026-09-20", 60.0)
    inv_id = invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                                     entry_ids=[eid], path=db)
    invoices.void_invoice(inv_id, "Duplicate", path=db)
    entries = store.list_entries(day="2026-09-20", path=db)
    assert entries[0]["invoice_id"] is None
    # The released entry can be invoiced again.
    inv2 = invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                                   entry_ids=[eid], path=db)
    assert inv2 != inv_id


# --------------------------------------------------------- snapshot ---

def test_snapshot_integrity_after_source_entry_edit(tmp_path):
    db = str(tmp_path / "i10.db")
    pid = _project(db)
    eid = _entry(db, pid, "2026-09-20", 60.0)
    inv_id = invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                                     entry_ids=[eid], path=db)
    # Edit the source entry afterwards.
    store.update_entry(eid, {"task": "CHANGED", "minutes": 1.0}, path=db)
    d = invoices.get_invoice(inv_id, path=db)
    line = d["lines"][0]
    assert line["description"] == "Dev"
    assert line["amount_minor_units"] == 10000
    assert d["invoice"]["totals"]["total_minor"] == 10000


# ---------------------------------------------------------- payment ---

def test_overpayment_capped_at_zero_and_auto_paid(tmp_path):
    db = str(tmp_path / "i11.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    invoices.send_invoice(inv_id, path=db)
    became_paid, balance, overpaid = invoices.record_payment(
        inv_id, "2026-09-27", 15000, path=db)
    assert became_paid is True
    assert balance == 0
    assert overpaid == 5000
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["status"] == "paid"
    assert d["balance_minor"] == 0
    assert d["paid_minor"] == 15000  # actual amount retained


def test_partial_payment_keeps_sent(tmp_path):
    db = str(tmp_path / "i12.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    invoices.send_invoice(inv_id, path=db)
    became_paid, balance, overpaid = invoices.record_payment(
        inv_id, "2026-09-27", 4000, path=db)
    assert became_paid is False
    assert balance == 6000
    assert overpaid == 0
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["status"] == "sent"


def test_overdue_is_derived(tmp_path, monkeypatch):
    db = str(tmp_path / "i13.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    invoices.send_invoice(inv_id, path=db)
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["overdue"] is False
    # Time-travel past the due date: overdue is derived, not stored.
    # (The frozen sent row cannot be edited, even by this test.)
    monkeypatch.setattr(invoices, "_today",
                        lambda: invoices.date(2027, 1, 1))
    d = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert d["overdue"] is True


# ---------------------------------------------------------- currency ---

def test_mixed_currency_rejected(tmp_path):
    db = str(tmp_path / "i14.db")
    pid = _project(db)
    e1 = _entry(db, pid, "2026-09-20", 60.0, currency="USD")
    e2 = _entry(db, pid, "2026-09-20", 60.0, currency="EUR")
    with pytest.raises(invoices.CurrencyMixError) as exc:
        invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                                entry_ids=[e1, e2], path=db)
    assert "USD" in str(exc.value) and "EUR" in str(exc.value)


def test_manual_line_inherits_invoice_currency(tmp_path):
    db = str(tmp_path / "i15.db")
    pid = _project(db)
    eid = _entry(db, pid, "2026-09-20", 60.0, currency="EUR")
    inv_id = invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                                     entry_ids=[eid], path=db)
    # Manual lines always inherit the invoice currency, so they can
    # never mismatch (Q13).
    line_id = invoices.add_manual_line(inv_id, "Extra", "1", "50", path=db)
    d = invoices.get_invoice(inv_id, path=db)
    line = [line for line in d["lines"] if line["id"] == line_id][0]
    assert line["currency"] == "EUR"


# ----------------------------------------------------- void / reissue ---

def test_void_and_reissue_atomic_and_linked(tmp_path):
    db = str(tmp_path / "i16.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    n1 = invoices.send_invoice(inv_id, path=db)
    new_id = invoices.void_and_reissue(inv_id, "Reissued with corrections",
                                       path=db)
    old = invoices.get_invoice(inv_id, path=db)["invoice"]
    new = invoices.get_invoice(new_id, path=db)["invoice"]
    assert old["status"] == "void"
    assert old["void_reason"] == "Reissued with corrections"
    assert old["superseded_by_invoice_id"] == new_id
    assert new["status"] == "draft"
    assert new["supersedes_invoice_id"] == inv_id
    # Voided number is never reused.
    n2 = invoices.send_invoice(new_id, path=db)
    assert n2 != n1
    # The entry was released by the void and re-claimed by the draft.
    entries = store.list_entries(day="2026-09-20", path=db)
    assert entries[0]["invoice_id"] == new_id


def test_cannot_supersede_twice(tmp_path):
    db = str(tmp_path / "i17.db")
    pid = _project(db)
    inv_id = _draft(db, pid)
    invoices.send_invoice(inv_id, path=db)
    invoices.void_and_reissue(inv_id, "Other", path=db)
    with pytest.raises(invoices.InvoiceLockedError):
        invoices.void_invoice(inv_id, "Other", path=db)


# ------------------------------------------------------ concurrency ---

def test_concurrent_issuance_unique_numbers(tmp_path):
    db = str(tmp_path / "i18.db")
    pid = _project(db)
    drafts = []
    for i in range(4):
        eid = _entry(db, pid, "2026-09-2%d" % i, 30.0)
        drafts.append(invoices.create_invoice(
            pid, "2026-09-20", "2026-09-29", entry_ids=[eid], path=db))
    numbers, errors = [], []
    def _send(did):
        try:
            numbers.append(invoices.send_invoice(did, path=db))
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    threads = [threading.Thread(target=_send, args=(d,)) for d in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(numbers)) == 4


# ------------------------------------------------------------ html ---

def test_invoice_html_security(tmp_path):
    db = str(tmp_path / "i19.db")
    pid = _project(db)
    inv_id = _draft(db, pid, notes="<script>alert(1)</script>")
    invoices.send_invoice(inv_id, path=db)
    html = invoices.render_invoice_html(inv_id, path=db)
    assert "Content-Security-Policy" in html
    assert "<script" not in html.lower()
    assert "javascript:" not in html.lower()
    assert "onerror" not in html.lower()
    # The note is escaped, not executed.
    assert "&lt;script&gt;" in html
    # No forecast content leaks into the invoice.
    assert "forecast" not in html.lower()
    assert "rollover" not in html.lower()
