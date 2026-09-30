"""Phase 10: dashboard route smoke tests for invoicing + rollover UI."""

import sqlite3

from focuscore import invoices, store


def _seed(db):
    pid = store.add_project("Acme", client="Acme Corp", path=db)
    eid = store.create_entry("2026-09-20", "2026-09-20T09:00",
                             "2026-09-20T10:00", 60.0, "Work",
                             project_id=pid, task="Dev",
                             status="accepted", path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET hourly_rate_minor = 10000, "
            "rate_currency = 'USD', rate_status = 'confirmed' "
            "WHERE id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()
    return pid, eid


def test_invoice_routes_smoke(client):
    c, db = client
    pid, eid = _seed(db)

    r = c.get("/invoices")
    assert r.status_code == 200
    assert "Invoices" in r.data.decode()

    r = c.get("/invoices/new")
    assert r.status_code == 200

    r = c.post("/invoices/create", data={
        "project_id": str(pid),
        "from": "2026-09-20", "to": "2026-09-20",
        "entry_id": [str(eid)],
        "notes": "", "tax_pct": "10", "discount_pct": "0",
    }, follow_redirects=False)
    assert r.status_code == 302, r.data.decode()[:200]
    inv_id = invoices.list_invoices(path=db)[0]["id"]

    r = c.get(f"/invoices/{inv_id}")
    assert r.status_code == 200
    assert "DRAFT" in r.data.decode()

    r = c.get(f"/invoices/{inv_id}/print")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Content-Security-Policy" in html
    assert "<script" not in html.lower()

    # Draft actions
    r = c.post(f"/invoices/{inv_id}/tax-discount",
               data={"tax_pct": "5", "discount_pct": "0"},
               follow_redirects=False)
    assert r.status_code == 302

    r = c.post(f"/invoices/{inv_id}/send", follow_redirects=False)
    assert r.status_code == 302
    inv = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert inv["number"] == "INV-2026-0001"

    # Payment
    r = c.post(f"/invoices/{inv_id}/pay",
               data={"amount": "50.00", "paid_date": "2026-09-27",
                     "note": ""}, follow_redirects=False)
    assert r.status_code == 302

    # Void
    r = c.post(f"/invoices/{inv_id}/void",
               data={"reason": "Duplicate"}, follow_redirects=False)
    assert r.status_code == 302
    inv = invoices.get_invoice(inv_id, path=db)["invoice"]
    assert inv["status"] == "void"


def test_rollover_ui_present(client):
    c, db = client
    pid, _ = _seed(db)
    r = c.get("/timesheet?day=2026-09-20")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Rollover" in body or "rollover" in body

    # Toggle rollover off
    r = c.post("/timesheet/project/rollover",
               data={"id": str(pid), "enabled": "0",
                     "day": "2026-09-20"},
               follow_redirects=False)
    assert r.status_code == 302
    r = c.post("/timesheet/rollover-settings",
               data={"rollover_cap_pct": "50", "day": "2026-09-20"},
               follow_redirects=False)
    assert r.status_code == 302


def test_timesheet_invoice_badge(client):
    c, db = client
    pid, eid = _seed(db)
    inv_id = invoices.create_invoice(
        pid, "2026-09-20", "2026-09-20", entry_ids=[eid], path=db)
    invoices.send_invoice(inv_id, path=db)
    number = invoices.get_invoice(inv_id, path=db)["invoice"]["number"]
    r = c.get("/timesheet?day=2026-09-20")
    assert r.status_code == 200
    assert number in r.data.decode()
