"""Stocking a warehouse that has no stock yet, and moving part of a shortfall.

Both come from driving multi-warehouse as a Professional tenant:

- Creating a second warehouse left the buyer at "Sin datos en esta bodega" with
  no way forward. A `warehouse` COLUMN in the CSV worked, but the UI never
  mentioned it, so the flagship feature dead-ended at its first step. `/bulk`
  now takes the destination as a form field, which is what the per-warehouse tab
  sends.

- A donor that can only cover part of the gap used to be dropped in silence.
  The purchase is still the recommendation, but the units next door are real, so
  they are offered as an option.
"""

import io

from backend.db.connection import query_one
from backend.inventory.warehouse_service import DEFAULT_WAREHOUSE
from backend.inventory.service import _network_transfer_pass


def _csv(*rows: str) -> bytes:
    header = "sku,current_stock,lead_time_days\n"
    return (header + "\n".join(rows) + "\n").encode()


def _post_bulk(client, headers, content: bytes, **form):
    return client.post(
        "/api/v1/inventory/bulk",
        files={"file": ("stock.csv", io.BytesIO(content), "text/csv")},
        data=form,
        headers=headers,
    )


class TestImportDestination:
    def test_rows_without_a_warehouse_land_in_the_one_asked_for(
        self, client, analyst_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        r = _post_bulk(client, analyst_headers, _csv("SKU-D1,120,10"),
                       warehouse="Bodega Heredia")
        assert r.status_code == 200, r.text

        row = query_one(
            "SELECT warehouse, current_stock FROM inventory_stock "
            "WHERE tenant_id=%s AND sku=%s", (tid, "SKU-D1"),
        )
        assert row["warehouse"] == "Bodega Heredia"
        assert row["current_stock"] == 120
        # The location has to exist afterwards, or the tab the user is looking at
        # would still read "no data".
        assert query_one(
            "SELECT 1 AS ok FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, "Bodega Heredia"),
        ) is not None

    def test_a_warehouse_column_still_wins_over_the_destination(
        self, client, analyst_headers, test_tenant,
    ):
        """A multi-warehouse sheet must import as written, not be flattened."""
        tid = test_tenant["id"]
        content = (
            b"sku,current_stock,lead_time_days,warehouse\n"
            b"SKU-D2,50,5,Bodega Cartago\n"
            b"SKU-D3,70,5,\n"
        )
        r = _post_bulk(client, analyst_headers, content, warehouse="Bodega Heredia")
        assert r.status_code == 200, r.text

        named = query_one(
            "SELECT warehouse FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, "SKU-D2"))
        blank = query_one(
            "SELECT warehouse FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, "SKU-D3"))
        assert named["warehouse"] == "Bodega Cartago", "the file's own column lost"
        assert blank["warehouse"] == "Bodega Heredia", "the blank row was not filled"

    def test_without_a_destination_nothing_changes(
        self, client, analyst_headers, test_tenant,
    ):
        """Omitting the field must behave exactly as before the parameter existed."""
        tid = test_tenant["id"]
        r = _post_bulk(client, analyst_headers, _csv("SKU-D4,10,5"))
        assert r.status_code == 200, r.text
        row = query_one(
            "SELECT warehouse FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, "SKU-D4"))
        assert row["warehouse"] == DEFAULT_WAREHOUSE

    def test_viewer_cannot_import(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        r = _post_bulk(client, viewer_headers, _csv("SKU-D5,10,5"),
                       warehouse="Bodega Heredia")
        assert r.status_code == 403
        assert query_one(
            "SELECT 1 AS ok FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, "SKU-D5"),
        ) is None, "a denied import still wrote stock"


def _row(warehouse, *, stock, daily, signal, qty, moq=1.0):
    return {
        "sku": "SKU-P", "warehouse": warehouse, "current_stock": stock,
        "daily_demand": daily, "reorder_point": 0.0, "signal": signal,
        "recommended_qty": qty, "moq": moq, "recommended_action": None,
        "transfer_suggestion": None, "transfer_rejected_reason": None,
        "partial_transfer": None,
    }


class TestPartialTransferIsOffered:
    def test_it_offers_the_part_and_leaves_the_rest_to_buy(self):
        needy = _row("Cartago", stock=0, daily=20, signal="PEDIR_YA", qty=400)
        needy["lead_time_days"] = 10
        donor = _row("principal", stock=100, daily=0, signal="SOBRESTOCK", qty=0)
        lanes = {("principal", "Cartago"):
                 {"lead_time_days": 2, "cost_per_unit": 0.0, "fixed_cost": 0.0}}
        _network_transfer_pass([needy, donor], "daily", lanes=lanes)

        # The RECOMMENDATION is untouched: this is an option beside it.
        assert needy["recommended_action"] == "order"
        assert needy["recommended_qty"] == 400
        assert needy["transfer_suggestion"] is None

        pt = needy["partial_transfer"]
        assert pt is not None, "100 units next door and nothing offered"
        assert pt["from_warehouse"] == "principal"
        assert pt["qty"] == 100
        assert pt["remaining_qty"] == 300, "the rest of the gap must be stated"

    def test_a_bad_lane_is_not_offered_either(self):
        """The partial goes through the same lane rules as a full transfer."""
        needy = _row("Cartago", stock=0, daily=20, signal="PEDIR_YA", qty=400)
        needy["lead_time_days"] = 3
        donor = _row("principal", stock=100, daily=0, signal="SOBRESTOCK", qty=0)
        # 30 transit days against a 3-day purchase: slower than buying.
        lanes = {("principal", "Cartago"):
                 {"lead_time_days": 30, "cost_per_unit": 0.0, "fixed_cost": 0.0}}
        _network_transfer_pass([needy, donor], "daily", lanes=lanes)
        assert needy["partial_transfer"] is None

    def test_a_full_transfer_leaves_no_partial_behind(self):
        needy = _row("Cartago", stock=0, daily=20, signal="PEDIR_YA", qty=100)
        needy["lead_time_days"] = 30
        donor = _row("principal", stock=5000, daily=1, signal="SOBRESTOCK", qty=0)
        lanes = {("principal", "Cartago"):
                 {"lead_time_days": 2, "cost_per_unit": 0.0, "fixed_cost": 0.0}}
        _network_transfer_pass([needy, donor], "daily", lanes=lanes)
        assert needy["recommended_action"] == "transfer"
        assert needy["partial_transfer"] is None
