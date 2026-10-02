"""
Stock already on its way must not be ordered twice.

`get_inventory_status` read `inventory_stock.current_stock` and nothing else, so
everything in motion was invisible: a purchase order the buyer had sent, and a
transfer between two of their own warehouses. Every day until the goods
physically landed, StockAI asked for them again.

Multi-store turned one move into two false alarms. Measured before the fix, on a
tenant that owned 430 units of one SKU:

    200 units sent  principal -> Sucursal Cartago
    one second later:
        principal          PEDIR_YA   buy 250
        Sucursal Cartago   PEDIR_YA   buy  90

The origin had lost the stock at dispatch and the destination had not gained it
yet, so moving your own stock between your own stores manufactured demand for
340 units that were already sitting on a truck.

The rule these tests pin: the recommendation is computed against the inventory
POSITION (on hand + on its way), while the signal and coverage stay on physical
stock — what is on the shelf is what you can sell today, and a truck arriving
tomorrow does not change that. The rows carry `incoming_qty` so the UI can say
why the quantity dropped.
"""
from uuid import uuid4

import pytest


def _sku():
    return f"INC_{uuid4().hex[:8]}"


def _forecast(value: float, n: int = 14):
    return {"lightgbm": {"forecast": [{"value": value}] * n}}


@pytest.fixture
def scenario(client, auth_headers, test_tenant):
    """A SKU that needs ordering: 40 on hand against 10/day and a 7-day lead."""
    from backend.db import session_store
    from backend.inventory import service as inv_svc
    from backend.sessions.service import create_session

    tid = test_tenant["id"]
    sku = _sku()
    sid = create_session(tid, "usr_test", "incoming")["id"]
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": 40, "lead_time_days": 7, "moq": 1,
        "unit_cost": 5.0, "supplier": "Andina", "warehouse": "principal"})
    session_store.set_forecasts(tid, sid, {sku: _forecast(10.0)})
    return {"tid": tid, "sid": sid, "sku": sku}


def _row(scenario, warehouse=None):
    from backend.inventory import service as inv_svc
    s = scenario
    if warehouse is None:
        return next(i for i in inv_svc.get_inventory_status(s["tid"], s["sid"])
                    if i["sku"] == s["sku"])
    rows = inv_svc.get_inventory_status_by_warehouse(s["tid"], s["sid"])
    rows = rows["items"] if isinstance(rows, dict) else rows
    return next(i for i in rows if i["sku"] == s["sku"] and i["warehouse"] == warehouse)


class TestASentPurchaseOrderStopsTheRepeatOrder:
    def test_the_quantity_drops_by_what_is_already_coming(self, scenario, auth_headers, client):
        from backend.db.connection import execute, query_one

        before = _row(scenario)
        assert before["recommended_qty"] > 0, "the fixture must need ordering"
        assert before["incoming_qty"] == 0

        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, reception_status, sent_at)
               VALUES (%s, 'pending', NOW()) RETURNING id""",
            (scenario["tid"],))
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, final_qty, status, warehouse)
               VALUES (%s, %s, %s, %s, 'approved', 'principal')""",
            (po["id"], scenario["tid"], scenario["sku"], before["recommended_qty"]))

        after = _row(scenario)
        assert after["incoming_qty"] == before["recommended_qty"]
        assert after["recommended_qty"] == 0, (
            "StockAI asked again for units it had already been told were ordered: "
            f"{after['recommended_qty']}")

    def test_a_downloaded_order_never_sent_through_stockai_counts(self, scenario):
        """Reversed 2026-10-01. This test used to pin the opposite ("an unsent
        PO is a draft"), but the everyday path is "Descargar orden de compra":
        the buyer mails the CSV from their own phone and `sent_at` is never
        stamped. Mobile QA had 426 units pending on OC-000001/2 and was told to
        order 63 more. A PO the buyer generated is a commitment — /pedidos and
        the overdue list already treated it as one."""
        from backend.db.connection import execute, query_one

        before = _row(scenario)
        assert before["recommended_qty"] > 0
        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, reception_status, sent_at, po_number)
               VALUES (%s, 'pending', NULL, 9001) RETURNING id""",
            (scenario["tid"],))
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, final_qty, status, warehouse)
               VALUES (%s, %s, %s, 500, 'approved', 'principal')""",
            (po["id"], scenario["tid"], scenario["sku"]))

        after = _row(scenario)
        assert after["incoming_qty"] == 500
        assert after["recommended_qty"] == 0
        # The quantity explains itself: which order the units are on.
        assert after["incoming_sources"] == [
            {"kind": "po", "reference": "OC-009001", "qty": 500.0}]

    def test_an_order_reported_as_not_received_still_counts(self, scenario):
        """'not_received' means "nothing had arrived when I looked", not "this
        will never arrive" — dropping it would re-order the same units."""
        from backend.db.connection import execute, query_one

        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, reception_status)
               VALUES (%s, 'not_received') RETURNING id""",
            (scenario["tid"],))
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, final_qty, received_qty, status, warehouse)
               VALUES (%s, %s, %s, 80, 0, 'approved', 'principal')""",
            (po["id"], scenario["tid"], scenario["sku"]))

        assert _row(scenario)["incoming_qty"] == 80

    def test_a_line_the_buyer_rejected_does_not_count(self, scenario):
        """A rejected line is on the PO for adoption tracking only; nothing was
        ordered, so nothing is coming."""
        from backend.db.connection import execute, query_one

        before = _row(scenario)
        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, reception_status)
               VALUES (%s, 'pending') RETURNING id""",
            (scenario["tid"],))
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, recommended_qty, final_qty, status, warehouse)
               VALUES (%s, %s, %s, 300, 300, 'rejected', 'principal')""",
            (po["id"], scenario["tid"], scenario["sku"]))

        after = _row(scenario)
        assert after["incoming_qty"] == 0
        assert after["recommended_qty"] == before["recommended_qty"]

    def test_an_over_received_line_does_not_cancel_another_orders_remainder(self, scenario):
        """Per-line clamp: 120 received on a 100-unit line is not "-20 coming"
        that eats the 50 still open on a different order."""
        from backend.db.connection import execute, query_one

        tid, sku = scenario["tid"], scenario["sku"]
        a = query_one("INSERT INTO inventory_po_log (tenant_id, reception_status) "
                      "VALUES (%s, 'partial') RETURNING id", (tid,))
        execute("""INSERT INTO inventory_po_items
                       (po_log_id, tenant_id, sku, final_qty, received_qty, status, warehouse)
                   VALUES (%s, %s, %s, 100, 120, 'approved', 'principal'),
                          (%s, %s, %s, 10, 0, 'approved', 'principal')""",
                (a["id"], tid, sku, a["id"], tid, f"{sku}_other"))
        b = query_one("INSERT INTO inventory_po_log (tenant_id, reception_status) "
                      "VALUES (%s, 'pending') RETURNING id", (tid,))
        execute("""INSERT INTO inventory_po_items
                       (po_log_id, tenant_id, sku, final_qty, status, warehouse)
                   VALUES (%s, %s, %s, 50, 'approved', 'principal')""",
                (b["id"], tid, sku))

        assert _row(scenario)["incoming_qty"] == 50

    def test_a_received_order_stops_counting(self, scenario):
        """Otherwise the units would be counted twice: once as incoming and
        again as the stock they became."""
        from backend.db.connection import execute, query_one

        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, reception_status, sent_at)
               VALUES (%s, 'received', NOW()) RETURNING id""",
            (scenario["tid"],))
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, final_qty, received_qty, status, warehouse)
               VALUES (%s, %s, %s, 100, 100, 'approved', 'principal')""",
            (po["id"], scenario["tid"], scenario["sku"]))

        assert _row(scenario)["incoming_qty"] == 0

    def test_a_partial_reception_counts_only_the_remainder(self, scenario):
        from backend.db.connection import execute, query_one

        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, reception_status, sent_at)
               VALUES (%s, 'partial', NOW()) RETURNING id""",
            (scenario["tid"],))
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, final_qty, received_qty, status, warehouse)
               VALUES (%s, %s, %s, 100, 70, 'approved', 'principal')""",
            (po["id"], scenario["tid"], scenario["sku"]))

        assert _row(scenario)["incoming_qty"] == 30


class TestMovingYourOwnStockDoesNotCreateDemand:
    def test_neither_end_asks_to_buy_the_units_on_the_truck(
        self, scenario, client, auth_headers
    ):
        """The measured multi-store failure, end to end."""
        from backend.inventory import service as inv_svc
        from backend.inventory import transfer_service as tr_svc
        from backend.inventory import warehouse_service as wh_svc

        s = scenario
        wh_svc.create_warehouse(s["tid"], "principal")
        wh_svc.create_warehouse(s["tid"], "Sucursal Cartago")
        # A warehouse with no demand share carries no demand, so it gets no
        # recommendation to suppress in the first place. Split it like a real
        # two-store tenant would.
        wh_svc.set_demand_share(s["tid"], "principal", 50)
        wh_svc.set_demand_share(s["tid"], "Sucursal Cartago", 50)
        # Enough at the origin to donate without stranding itself.
        inv_svc.upsert_stock(s["tid"], s["sku"], {
            "current_stock": 400, "lead_time_days": 7, "moq": 1,
            "warehouse": "principal"})
        inv_svc.upsert_stock(s["tid"], s["sku"], {
            "current_stock": 10, "lead_time_days": 7, "moq": 1,
            "warehouse": "Sucursal Cartago"})

        tr_svc.create_transfer(
            s["tid"], "usr_test", "principal", "Sucursal Cartago",
            [{"sku": s["sku"], "qty": 200}])

        origin = _row(s, "principal")
        branch = _row(s, "Sucursal Cartago")

        # The origin physically lost them and has nothing coming back.
        assert origin["current_stock"] == 200.0
        assert origin["incoming_qty"] == 0

        # The destination has not received them yet, but they ARE coming, so it
        # must not order them a second time.
        assert branch["current_stock"] == 10.0
        assert branch["incoming_qty"] == 200.0
        assert branch["recommended_qty"] == 0, (
            "the branch asked to buy units already on the truck: "
            f"{branch['recommended_qty']}")

    def test_the_origin_is_never_credited_with_what_it_sent(self, scenario):
        """Crediting both ends would invent stock the company does not own."""
        from backend.inventory import service as inv_svc
        from backend.inventory import transfer_service as tr_svc
        from backend.inventory import warehouse_service as wh_svc

        s = scenario
        wh_svc.create_warehouse(s["tid"], "principal")
        wh_svc.create_warehouse(s["tid"], "Sucursal Cartago")
        inv_svc.upsert_stock(s["tid"], s["sku"], {
            "current_stock": 400, "lead_time_days": 7, "moq": 1,
            "warehouse": "principal"})
        tr_svc.create_transfer(
            s["tid"], "usr_test", "principal", "Sucursal Cartago",
            [{"sku": s["sku"], "qty": 50}])

        incoming = inv_svc.get_incoming_qty(s["tid"])
        assert incoming.get((s["sku"], "principal"), 0) == 0
        assert incoming.get((s["sku"], "Sucursal Cartago")) == 50.0


class TestWhatTheSignalStillMeans:
    def test_coverage_and_signal_stay_on_physical_stock(self, scenario):
        """A truck arriving tomorrow does not put units on today's shelf. The
        colour describes what the shop can actually sell; only the quantity to
        BUY accounts for what is coming."""
        from backend.db.connection import execute, query_one

        before = _row(scenario)
        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, reception_status, sent_at)
               VALUES (%s, 'pending', NOW()) RETURNING id""",
            (scenario["tid"],))
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, final_qty, status, warehouse)
               VALUES (%s, %s, %s, 5000, 'approved', 'principal')""",
            (po["id"], scenario["tid"], scenario["sku"]))

        after = _row(scenario)
        assert after["coverage_days"] == before["coverage_days"]
        assert after["signal"] == before["signal"]
        assert after["recommended_qty"] == 0
