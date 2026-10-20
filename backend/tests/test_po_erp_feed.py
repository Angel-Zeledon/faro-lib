"""Purchase orders out to the ERP (backend/po_erp/): the cursor feed, the import
files and the ERP's own document number written back.

Pins what costs money when wrong: an order exported before approval, an order
skipped or repeated between two reads, a cancellation the ERP never hears, a
reference silently re-pointed, and a viewer writing one.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.po_erp import core

FEED = "/api/v1/purchase-orders/export"


@pytest.fixture(autouse=True)
def _no_holdback(monkeypatch):
    # The holdback protects a reader from a transaction that commits late; a test
    # writes and reads in order, so it would only make every read wait ten seconds.
    monkeypatch.setattr(core, "FEED_HOLDBACK_SECONDS", 0)


def _supplier(tid, name, erp_code=None):
    return query_one(
        "INSERT INTO suppliers (tenant_id, name, erp_code) VALUES (%s, %s, %s) RETURNING id",
        (tid, f"{name}-{uuid4().hex[:5]}", erp_code))["id"]


def _po(tid, lines, *, generated_days_ago=0):
    """lines: [(sku, qty, supplier_id|None, supplier_name|None, cost)]"""
    po = query_one(
        "INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units, "
        "generated_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (tid, "sess-" + uuid4().hex[:8], len(lines), sum(l[1] for l in lines),
         datetime.now(timezone.utc) - timedelta(days=generated_days_ago)))
    for sku, qty, sup_id, sup_name, cost in lines:
        execute(
            "INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, supplier, supplier_id, "
            "recommended_qty, final_qty, unit_cost, status, warehouse) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'approved', 'principal')",
            (po["id"], tid, sku, sup_name, sup_id, qty, qty, cost))
    return po["id"]


def _feed(client, headers, since=None, limit=None):
    params = {}
    if since:
        params["since"] = since
    if limit:
        params["limit"] = limit
    resp = client.get(FEED, params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _ids(page):
    return [o["id"] for o in page["orders"]]


class TestFeed:

    def test_orders_come_with_stable_ids_and_the_mapped_supplier_code(
            self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Acme", erp_code="V-100")
        po = _po(tid, [("A-1", 10, sup, "Acme", 2.0), ("A-2", 5, sup, "Acme", None)])
        page = _feed(client, viewer_headers)
        order = next(o for o in page["orders"] if o["id"] == po)
        doc = order["documents"][0]
        assert doc["document_id"] == f"{po}:{sup}"
        assert doc["supplier"]["erp_code"] == "V-100" and doc["supplier"]["mapped"] is True
        assert {ln["sku"] for ln in doc["lines"]} == {"A-1", "A-2"}
        line_ids = {ln["line_id"] for ln in doc["lines"]}
        again = _feed(client, viewer_headers)
        same = next(o for o in again["orders"] if o["id"] == po)
        assert {ln["line_id"] for ln in same["documents"][0]["lines"]} == line_ids
        assert next(ln for ln in doc["lines"] if ln["sku"] == "A-2")["total"] is None

    def test_unmapped_supplier_is_flagged_not_dropped(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "NoCode")
        po = _po(tid, [("B-1", 4, sup, "NoCode", 1.0)])
        doc = next(o for o in _feed(client, viewer_headers)["orders"]
                   if o["id"] == po)["documents"][0]
        assert doc["supplier"]["erp_code"] is None and doc["supplier"]["mapped"] is False

    def test_one_order_with_two_suppliers_is_two_documents(
            self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        s1, s2 = _supplier(tid, "One"), _supplier(tid, "Two")
        po = _po(tid, [("C-1", 1, s1, "One", 1.0), ("C-2", 1, s2, "Two", 1.0)])
        order = next(o for o in _feed(client, viewer_headers)["orders"] if o["id"] == po)
        assert {d["document_id"] for d in order["documents"]} == {f"{po}:{s1}", f"{po}:{s2}"}

    def test_cursor_never_skips_or_repeats(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Pager")
        made = [_po(tid, [(f"P-{i}", 1, sup, "Pager", 1.0)]) for i in range(5)]
        seen, cursor = [], None
        for _ in range(10):
            page = _feed(client, viewer_headers, since=cursor, limit=2)
            seen += [i for i in _ids(page) if i in made]
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert seen == made
        # Resuming from the last cursor returns nothing new, and a later order
        # arrives exactly once.
        assert [i for i in _ids(_feed(client, viewer_headers, since=cursor)) if i in made] == []
        later = _po(tid, [("P-9", 1, sup, "Pager", 1.0)])
        assert _ids(_feed(client, viewer_headers, since=cursor)).count(later) == 1

    def test_a_foreign_cursor_is_refused(self, client, viewer_headers):
        resp = client.get(FEED, params={"since": "garbage"}, headers=viewer_headers)
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "feed_cursor_invalid"

    def test_cancellation_reaches_the_feed(self, client, analyst_headers, viewer_headers,
                                           test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Cancel")
        po = _po(tid, [("X-1", 3, sup, "Cancel", 1.0)])
        cursor = _feed(client, viewer_headers)["next_cursor"]
        assert client.post(f"/api/v1/inventory/po/{po}/cancel", json={"reason": "no"},
                           headers=analyst_headers).status_code == 200
        order = next(o for o in _feed(client, viewer_headers, since=cursor)["orders"]
                     if o["id"] == po)
        assert order["state"] == "cancelled" and order["cancelled_at"]

    def test_nothing_leaves_before_approval_and_appears_when_approved(
            self, client, viewer_headers, test_tenant, registered_user):
        from backend.inventory import po_approval_service as approvals
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        approvals.set_approver(tid, uid, True)
        approvals.create_rule(tid, uid, {"threshold": 10})
        sup = _supplier(tid, "Gate")
        po = _po(tid, [("G-1", 100, sup, "Gate", 5.0)])      # 500 > 10: needs approval
        page = _feed(client, viewer_headers)
        assert po not in _ids(page) and page["held_back"] >= 1
        cursor = page["next_cursor"]
        execute("UPDATE inventory_po_log SET approval_status = 'approved', "
                "approved_amount = 500 WHERE id = %s", (po,))
        assert po in _ids(_feed(client, viewer_headers, since=cursor))

    def test_viewer_cannot_export_a_cancelled_or_unapproved_file(
            self, client, analyst_headers, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "File")
        po = _po(tid, [("F-1", 3, sup, "File", 1.0)])
        resp = client.get("/api/v1/purchase-orders/export/file",
                          params={"po_ids": po, "format": "csv"}, headers=viewer_headers)
        assert resp.status_code == 200 and f"{po}:{sup}" in resp.text
        client.post(f"/api/v1/inventory/po/{po}/cancel", headers=analyst_headers)
        resp = client.get("/api/v1/purchase-orders/export/file",
                          params={"po_ids": po, "format": "csv"}, headers=viewer_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "export_order_cancelled"

    def test_unknown_format_is_refused(self, client, viewer_headers, test_tenant):
        po = _po(test_tenant["id"], [("U-1", 1, None, None, 1.0)])
        resp = client.get("/api/v1/purchase-orders/export/file",
                          params={"po_ids": po, "format": "cobol"}, headers=viewer_headers)
        assert resp.status_code == 400 and resp.json()["error_code"] == "erp_format_unknown"


class TestAcknowledgement:

    def _url(self, po):
        return f"/api/v1/purchase-orders/{po}/erp-reference"

    def test_viewer_cannot_write_a_reference(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Ack")
        po = _po(tid, [("K-1", 1, sup, "Ack", 1.0)])
        resp = client.post(self._url(po), json={"reference": "4500001"}, headers=viewer_headers)
        assert resp.status_code == 403
        assert query("SELECT 1 FROM po_erp_references WHERE po_log_id = %s", (po,)) == []

    def test_analyst_writes_it_once_and_the_feed_shows_it(
            self, client, analyst_headers, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Ack")
        po = _po(tid, [("K-1", 1, sup, "Ack", 1.0)])
        cursor = _feed(client, viewer_headers)["next_cursor"]
        resp = client.post(self._url(po), json={"reference": " 4500002 "},
                           headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] is True
        row = query_one("SELECT reference, supplier_key, created_by FROM po_erp_references "
                        "WHERE po_log_id = %s", (po,))
        assert row["reference"] == "4500002" and row["supplier_key"] == sup and row["created_by"]
        order = next(o for o in _feed(client, viewer_headers, since=cursor)["orders"]
                     if o["id"] == po)
        assert order["documents"][0]["erp_reference"] == "4500002"
        assert order["documents"][0]["acknowledged"] is True

    def test_same_reference_twice_is_idempotent(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Idem")
        po = _po(tid, [("K-2", 1, sup, "Idem", 1.0)])
        client.post(self._url(po), json={"reference": "R1"}, headers=analyst_headers)
        resp = client.post(self._url(po), json={"reference": "R1"}, headers=analyst_headers)
        assert resp.status_code == 200 and resp.json()["data"]["changed"] is False
        assert query_one("SELECT COUNT(*) AS n FROM po_erp_references WHERE po_log_id = %s",
                         (po,))["n"] == 1

    def test_a_different_reference_is_refused_not_overwritten(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Conf")
        po = _po(tid, [("K-3", 1, sup, "Conf", 1.0)])
        client.post(self._url(po), json={"reference": "R-OLD"}, headers=analyst_headers)
        resp = client.post(self._url(po), json={"reference": "R-NEW"}, headers=analyst_headers)
        assert resp.status_code == 409 and resp.json()["error_code"] == "erp_reference_conflict"
        assert query_one("SELECT reference FROM po_erp_references WHERE po_log_id = %s",
                         (po,))["reference"] == "R-OLD"

    def test_one_reference_cannot_belong_to_two_orders(self, client, analyst_headers,
                                                      test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Dup")
        a = _po(tid, [("K-4", 1, sup, "Dup", 1.0)])
        b = _po(tid, [("K-5", 1, sup, "Dup", 1.0)])
        client.post(self._url(a), json={"reference": "SAME"}, headers=analyst_headers)
        resp = client.post(self._url(b), json={"reference": "SAME"}, headers=analyst_headers)
        assert resp.status_code == 409 and resp.json()["error_code"] == "erp_reference_in_use"
        assert query("SELECT 1 FROM po_erp_references WHERE po_log_id = %s", (b,)) == []

    def test_an_order_with_several_suppliers_must_say_which_document(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        s1, s2 = _supplier(tid, "M1"), _supplier(tid, "M2")
        po = _po(tid, [("M-1", 1, s1, "M1", 1.0), ("M-2", 1, s2, "M2", 1.0)])
        resp = client.post(self._url(po), json={"reference": "R"}, headers=analyst_headers)
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "erp_reference_supplier_required"
        assert query("SELECT 1 FROM po_erp_references WHERE po_log_id = %s", (po,)) == []
        ok = client.post(self._url(po), json={"reference": "R", "supplier_id": s2},
                         headers=analyst_headers)
        assert ok.status_code == 200
        assert query_one("SELECT supplier_key FROM po_erp_references WHERE po_log_id = %s",
                         (po,))["supplier_key"] == s2

    def test_a_supplier_the_order_does_not_have_is_refused(self, client, analyst_headers,
                                                          test_tenant):
        tid = test_tenant["id"]
        sup = _supplier(tid, "Real")
        po = _po(tid, [("K-6", 1, sup, "Real", 1.0)])
        resp = client.post(self._url(po), json={"reference": "R", "supplier_id": "nope"},
                           headers=analyst_headers)
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "erp_reference_unknown_supplier"

    def test_another_tenants_order_is_not_found(self, client, analyst_headers, test_tenant):
        from backend.tenants.service import create_tenant
        other = create_tenant("erp-other")["id"]
        try:
            po = _po(other, [("Z-1", 1, None, None, 1.0)])
            resp = client.post(self._url(po), json={"reference": "R"}, headers=analyst_headers)
            assert resp.status_code in (403, 404)
            assert query("SELECT 1 FROM po_erp_references WHERE po_log_id = %s", (po,)) == []
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other,))

    def test_unacknowledged_lists_only_documents_without_a_number(
            self, client, analyst_headers, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        s1, s2 = _supplier(tid, "U1"), _supplier(tid, "U2")
        po = _po(tid, [("Q-1", 1, s1, "U1", 1.0), ("Q-2", 1, s2, "U2", 1.0)])
        client.post(self._url(po), json={"reference": "ONLY1", "supplier_id": s1},
                    headers=analyst_headers)
        resp = client.get("/api/v1/purchase-orders/unacknowledged", headers=viewer_headers)
        assert resp.status_code == 200
        order = next(o for o in resp.json()["data"] if o["id"] == po)
        assert [d["document_id"] for d in order["documents"]] == [f"{po}:{s2}"]


class TestSupplierMapping:

    def test_erp_code_is_stored_through_the_supplier_card(self, client, analyst_headers,
                                                          viewer_headers, test_tenant):
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": f"Mapped-{uuid4().hex[:5]}", "erp_code": "V-777"},
                           headers=analyst_headers)
        assert resp.status_code in (200, 201), resp.text
        sid = resp.json()["data"]["id"]
        assert query_one("SELECT erp_code FROM suppliers WHERE id = %s", (sid,))["erp_code"] == "V-777"
        denied = client.patch(f"/api/v1/inventory/suppliers/{sid}", json={"erp_code": "HACK"},
                              headers=viewer_headers)
        assert denied.status_code == 403
        assert query_one("SELECT erp_code FROM suppliers WHERE id = %s", (sid,))["erp_code"] == "V-777"
