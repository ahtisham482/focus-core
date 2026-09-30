"""Roadmap 1.7 repair: FK-safe deletes.

Before 1.7, FK enforcement was off, so deleting a timesheet entry
referenced by an ``invoice_lines`` row (or a project that has
invoices) silently succeeded and orphaned rows. With enforcement
live (``store.get_db`` sets ``PRAGMA foreign_keys = ON``) those
deletes raise ``sqlite3.IntegrityError``; the service layer and the
routes must turn that into the normal 400 "Could not delete" card,
never a 500. All databases are tmp files; the repo dev DB is never
touched.
"""

import sqlite3

from focuscore import invoices, store, timesheet


def _seed_invoiced(db):
    """Project + entry, with the entry placed on a draft invoice."""
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
    invoices.create_invoice(pid, "2026-09-20", "2026-09-20",
                            entry_ids=[eid], path=db)
    return pid, eid


def test_delete_entry_on_invoice_returns_error_dict(tmp_path):
    db = str(tmp_path / "fk_service.db")
    _pid, eid = _seed_invoiced(db)
    result = timesheet.delete_entry(eid, db_path=db)
    assert result == {
        "error": "This entry is on an invoice, so it cannot be deleted."}
    assert store.get_entry(eid, path=db) is not None


def test_delete_uninvoiced_entry_still_succeeds(tmp_path):
    db = str(tmp_path / "fk_free.db")
    pid = store.add_project("Solo", client="", path=db)
    eid = store.create_entry("2026-09-20", "2026-09-20T09:00",
                             "2026-09-20T10:00", 60.0, "Work",
                             project_id=pid, task="Dev",
                             status="accepted", path=db)
    assert timesheet.delete_entry(eid, db_path=db) == {}
    assert store.get_entry(eid, path=db) is None


def test_route_delete_invoiced_entry_is_400_not_500(client):
    c, db = client
    _pid, eid = _seed_invoiced(db)
    r = c.post("/timesheet/delete",
               data={"id": str(eid), "day": "2026-09-20"},
               follow_redirects=False)
    assert r.status_code == 400
    assert "on an invoice" in r.data.decode()
    assert store.get_entry(eid, path=db) is not None


def test_route_delete_uninvoiced_entry_redirects(client):
    c, db = client
    pid = store.add_project("Solo", client="", path=db)
    eid = store.create_entry("2026-09-20", "2026-09-20T09:00",
                             "2026-09-20T10:00", 60.0, "Work",
                             project_id=pid, task="Dev",
                             status="accepted", path=db)
    r = c.post("/timesheet/delete",
               data={"id": str(eid), "day": "2026-09-20"},
               follow_redirects=False)
    assert r.status_code == 302
    assert store.get_entry(eid, path=db) is None


def test_route_delete_project_with_invoices_is_400_not_500(client):
    c, db = client
    pid, _eid = _seed_invoiced(db)
    r = c.post("/timesheet/project/delete",
               data={"id": str(pid), "day": "2026-09-20"},
               follow_redirects=False)
    assert r.status_code == 400
    assert "has invoices" in r.data.decode()
    assert any(p["id"] == pid for p in store.list_projects(path=db))


def test_route_delete_project_without_invoices_redirects(client):
    c, db = client
    pid = store.add_project("Empty", client="", path=db)
    r = c.post("/timesheet/project/delete",
               data={"id": str(pid), "day": "2026-09-20"},
               follow_redirects=False)
    assert r.status_code == 302
    assert all(p["id"] != pid for p in store.list_projects(path=db))
