"""
Two money-losing defects found by mobile QA on 2026-10-01.

1. A second tap on "Descargar orden de compra" wrote a second, identical
   purchase order (OC-000003 and OC-000004, 5 s apart, same SKU and quantity).
   The cart stayed full and the button stayed live, and the endpoint had no way
   to tell a replay from a new order. Fix: an `Idempotency-Key` per cart
   submission, backed by a partial unique index, so a replay — sequential or
   racing — resolves to the first order.

2. An order created that way did not count as stock on its way: only POs
   stamped `sent_at` by the in-app send did, and the download path never stamps
   it. 426 units pending on OC-000001/2 and the panel still said "order 63".
   Fix: one definition (`service.get_incoming_detail`) — every open PO line,
   minus what was received — read by the semáforo, the optimizer and the
   alerts alike.

Every assertion below reads the database, not the response echo.
"""
import threading
import uuid

import pytest

from backend.db.connection import query, query_one


def _po_count(tenant_id: str) -> int:
    return int(query_one(
        "SELECT COUNT(*)::int AS n FROM inventory_po_log WHERE tenant_id = %s",
        (tenant_id,))["n"])


def _cart(sku: str, qty: float = 120) -> dict:
    return {"items": [{
        "sku": sku, "display_name": f"Prod {sku}", "supplier": "Andina",
        "signal": "PEDIR_PRONTO", "recommended_qty": qty, "final_qty": qty,
        "unit_cost": 4.0, "status": "approved",
    }]}


def _post(client, headers, body, key=None, session_id="sess-idem"):
    h = dict(headers)
    if key is not None:
        h["Idempotency-Key"] = key
    return client.post("/api/v1/inventory/log-po",
                       params={"session_id": session_id}, json=body, headers=h)


# ── Defect 1: one cart submission = one purchase order ──────────────────────

class TestReplayingASubmissionCreatesOneOrder:

    def test_the_same_key_twice_writes_one_po_and_returns_it(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        key = str(uuid.uuid4())
        body = _cart("IDEM-1")

        first = _post(client, analyst_headers, body, key)
        assert first.status_code == 201, first.text
        second = _post(client, analyst_headers, body, key)
        assert second.status_code == 200, second.text

        assert _po_count(tid) == 1
        a, b = first.json()["data"], second.json()["data"]
        assert a["id"] == b["id"] and a["po_number"] == b["po_number"]
        assert a["replayed"] is False and b["replayed"] is True
        # Only the first write's lines exist — the replay added none.
        lines = query("SELECT sku, final_qty FROM inventory_po_items "
                      "WHERE tenant_id = %s", (tid,))
        assert [(r["sku"], float(r["final_qty"])) for r in lines] == [("IDEM-1", 120.0)]
        # The activity log shows the order generated once, not twice.
        events = query("SELECT id FROM activity_logs WHERE tenant_id = %s "
                       "AND action = 'purchase.order_generated'", (tid,))
        assert len(events) == 1
        # The fingerprint is internal and never leaks into the response.
        assert "idempotency_fingerprint" not in a

    def test_a_different_key_is_a_different_order(
            self, client, analyst_headers, test_tenant):
        """The key — not the content — is what makes a replay. A buyer who
        genuinely orders the same thing again tomorrow gets a second PO."""
        tid = test_tenant["id"]
        body = _cart("IDEM-2")
        assert _post(client, analyst_headers, body, str(uuid.uuid4())).status_code == 201
        assert _post(client, analyst_headers, body, str(uuid.uuid4())).status_code == 201
        assert _po_count(tid) == 2

    def test_a_key_reused_for_a_different_order_is_refused(
            self, client, analyst_headers, test_tenant):
        """Answering it with the first order would tell the buyer an order
        exists that was never written."""
        tid = test_tenant["id"]
        key = str(uuid.uuid4())
        assert _post(client, analyst_headers, _cart("IDEM-3", 10), key).status_code == 201
        resp = _post(client, analyst_headers, _cart("IDEM-3", 99), key)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_idempotency_key_reused"
        assert _po_count(tid) == 1
        row = query_one("SELECT final_qty FROM inventory_po_items WHERE tenant_id = %s", (tid,))
        assert float(row["final_qty"]) == 10.0

    def test_a_malformed_key_is_refused_and_writes_nothing(
            self, client, analyst_headers, test_tenant):
        resp = _post(client, analyst_headers, _cart("IDEM-4"), "x" * 200)
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "po_idempotency_key_invalid"
        assert _po_count(test_tenant["id"]) == 0

    def test_keys_are_scoped_per_tenant(
            self, client, analyst_headers, test_tenant, make_tenant_user_headers):
        """Another tenant sending the same key value must get its OWN order,
        never a replay of this one."""
        key = str(uuid.uuid4())
        assert _post(client, analyst_headers, _cart("IDEM-5"), key).status_code == 201
        other_headers = make_tenant_user_headers("analyst")
        resp = _post(client, other_headers, _cart("IDEM-5"), key)
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["replayed"] is False
        assert _po_count(test_tenant["id"]) == 1

    def test_the_no_body_export_path_replays_too(
            self, client, analyst_headers, test_tenant):
        """That path re-derives the lines from the current semáforo, which the
        first order already changed — so the replay is resolved before it
        re-derives, instead of reporting a false 'key reused'."""
        tid = test_tenant["id"]
        key = str(uuid.uuid4())
        first = _post(client, analyst_headers, None, key, session_id="sess-none")
        assert first.status_code == 201, first.text
        second = _post(client, analyst_headers, None, key, session_id="sess-none")
        assert second.status_code == 200, second.text
        assert second.json()["data"]["id"] == first.json()["data"]["id"]
        assert _po_count(tid) == 1

    def test_manual_po_replays_too(self, client, analyst_headers, test_tenant):
        from backend.inventory import supplier_service as sup_svc
        tid = test_tenant["id"]
        sup = sup_svc.create_supplier(tid, {"name": f"Prov-{uuid.uuid4().hex[:6]}"})
        body = {"supplier_id": sup["id"], "lines": [{"sku": "MAN-1", "qty": 7}]}
        key = str(uuid.uuid4())
        h = {**analyst_headers, "Idempotency-Key": key}
        r1 = client.post("/api/v1/inventory/po", json=body, headers=h)
        r2 = client.post("/api/v1/inventory/po", json=body, headers=h)
        assert r1.status_code == 201, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["data"]["id"] == r2.json()["data"]["id"]
        assert _po_count(tid) == 1


class TestPermissionPair:

    def test_viewer_is_refused_and_no_po_is_written(
            self, client, viewer_headers, test_tenant):
        resp = _post(client, viewer_headers, _cart("PERM-1"), str(uuid.uuid4()))
        assert resp.status_code == 403
        assert _po_count(test_tenant["id"]) == 0

    def test_analyst_creates_exactly_one(self, client, analyst_headers, test_tenant):
        resp = _post(client, analyst_headers, _cart("PERM-2"), str(uuid.uuid4()))
        assert resp.status_code == 201, resp.text
        row = query_one("SELECT idempotency_key FROM inventory_po_log WHERE tenant_id = %s",
                        (test_tenant["id"],))
        assert row["idempotency_key"] is not None
        assert _po_count(test_tenant["id"]) == 1


class TestConcurrentDoubleTap:

    def test_two_simultaneous_identical_submissions_write_one_po(self, test_tenant):
        """Both requests pass the fast-path read before either commits; the
        partial unique index is what makes the loser resolve to the winner."""
        from backend.inventory.roi_service import log_po_generation

        tid = test_tenant["id"]
        key = str(uuid.uuid4())
        items = _cart("RACE-1")["items"]
        barrier = threading.Barrier(4)
        results, errors = [], []

        def submit():
            try:
                barrier.wait(timeout=10)
                results.append(log_po_generation(tid, "sess-race", items,
                                                 idempotency_key=key))
            except Exception as exc:          # surfaced by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=submit) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        assert len(results) == 4
        assert len({r["id"] for r in results}) == 1
        assert sum(1 for r in results if not r.get("replayed")) == 1
        assert _po_count(tid) == 1
        n_lines = query_one("SELECT COUNT(*)::int AS n FROM inventory_po_items "
                            "WHERE tenant_id = %s", (tid,))["n"]
        assert n_lines == 1


# ── Defect 2: what was ordered is netted everywhere ─────────────────────────

@pytest.fixture
def needs_ordering(test_tenant):
    """40 on the shelf against 10/day and a 7-day lead — must reorder."""
    from backend.db import session_store
    from backend.inventory import service as inv_svc
    from backend.sessions.service import create_session

    tid = test_tenant["id"]
    sku = f"ONORD_{uuid.uuid4().hex[:6]}"
    sid = create_session(tid, "usr_test", "on-order")["id"]
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": 40, "lead_time_days": 7, "moq": 1,
        "unit_cost": 5.0, "supplier": "Andina", "warehouse": "principal"})
    session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
        {"date": f"2026-01-{i + 1:02d}", "value": 10.0, "lower": 10.0, "upper": 10.0}
        for i in range(20)]}}})
    return {"tid": tid, "sid": sid, "sku": sku}


def _status_row(s):
    from backend.inventory import service as inv_svc
    return next(i for i in inv_svc.get_inventory_status(s["tid"], s["sid"])
                if i["sku"] == s["sku"])


class TestADownloadedOrderIsStockOnItsWay:

    def test_the_download_path_nets_the_order_in_semaforo_and_optimizer(
            self, client, analyst_headers, needs_ordering):
        """The QA scenario end to end: the cart's own request, no `sent_at`."""
        from backend.inventory import optimizer_service as opt_svc
        s = needs_ordering

        before = _status_row(s)
        assert before["recommended_qty"] > 0
        qty = before["recommended_qty"]

        resp = _post(client, analyst_headers, _cart(s["sku"], qty),
                     str(uuid.uuid4()), session_id=s["sid"])
        assert resp.status_code == 201, resp.text
        po = resp.json()["data"]
        row = query_one("SELECT sent_at, reception_status FROM inventory_po_log WHERE id = %s",
                        (po["id"],))
        assert row["sent_at"] is None and row["reception_status"] == "pending"

        after = _status_row(s)
        assert after["incoming_qty"] == pytest.approx(qty)
        assert after["recommended_qty"] == 0, (
            f"asked to order {after['recommended_qty']} again on top of {qty} pending")
        assert [src["reference"] for src in after["incoming_sources"]] == [
            f"OC-{int(po['po_number']):06d}"]

        # The optimizer's opening position reads the same definition (M18).
        inp = opt_svc.build_optimization_input(s["tid"], s["sid"], horizon_days=14)
        assert inp.stock0[(s["sku"], "principal")] == pytest.approx(40 + qty)

    def test_a_full_reception_stops_netting_and_a_partial_nets_the_remainder(
            self, client, analyst_headers, needs_ordering):
        s = needs_ordering
        po = _post(client, analyst_headers, _cart(s["sku"], 100),
                   str(uuid.uuid4()), session_id=s["sid"]).json()["data"]

        r = client.post(f"/api/v1/inventory/po/{po['id']}/receive",
                        json={"lines": [{"sku": s["sku"], "received_qty": 30}]},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        mid = _status_row(s)
        assert mid["incoming_qty"] == pytest.approx(70)
        assert mid["current_stock"] == pytest.approx(70)   # 40 + 30 on the shelf

        r = client.post(f"/api/v1/inventory/po/{po['id']}/receive",
                        json={"lines": [{"sku": s["sku"], "received_qty": 70}]},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert query_one("SELECT reception_status FROM inventory_po_log WHERE id = %s",
                         (po["id"],))["reception_status"] == "received"
        done = _status_row(s)
        assert done["incoming_qty"] == 0
        assert done["incoming_sources"] == []
        assert done["current_stock"] == pytest.approx(140)
