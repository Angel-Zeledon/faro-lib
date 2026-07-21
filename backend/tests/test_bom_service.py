"""
BOM (Bill of Materials) service and MRP Level 1 explosion.

Focused on the money math in explode_requirements' raw_material_summary:
`estimated_cost` must distinguish "we don't know the cost" (None) from
"the cost is known and happens to be zero" (0.0) — the same distinction the
rest of this codebase is careful about (see cash_service's
payment_terms_days handling and roi_service's managed_purchase_value).
"""

from uuid import uuid4

from backend.inventory import bom_service


class TestExplodeRequirementsEstimatedCost:

    def test_estimated_cost_is_zero_not_none_when_unit_cost_is_zero(
        self, client, monkeypatch, test_tenant,
    ):
        """
        A component whose unit_cost is genuinely 0 (e.g. a bundled freebie)
        still has a computable estimated_cost of 0 — not an unknown one.
        `if cost and shortage > 0` treats 0.0 as falsy and wrongly reports
        None, hiding a real (zero) number behind an "unavailable" state.
        """
        from backend.inventory import service

        tid = test_tenant["id"]
        parent = f"FG-{uuid4().hex[:6]}"
        child = f"RM-{uuid4().hex[:6]}"

        bom_service.upsert_bom_item(tid, parent, child, {"quantity": 2.0})

        monkeypatch.setattr(
            service, "get_inventory_status",
            lambda t, s: [
                {"sku": parent, "product_type": "finished_good",
                 "daily_demand": 10.0, "current_stock": 0,
                 "display_name": "Widget", "signal": "PEDIR_YA"},
                {"sku": child, "product_type": "raw_material",
                 "current_stock": 0, "unit_cost": 0.0,
                 "display_name": "Free Component"},
            ],
        )

        result = bom_service.explode_requirements(tid, "sess-1", horizon_days=10)

        row = next(r for r in result["raw_material_summary"] if r["sku"] == child)
        assert row["shortage"] > 0, "Test setup must produce a real shortage"
        assert row["estimated_cost"] == 0.0, (
            "unit_cost=0 is a known cost, not missing data — must not report None"
        )

    def test_estimated_cost_is_none_when_cost_truly_unknown(
        self, client, monkeypatch, test_tenant,
    ):
        """Contrast case: no unit_cost captured at all must still say None."""
        from backend.inventory import service

        tid = test_tenant["id"]
        parent = f"FG-{uuid4().hex[:6]}"
        child = f"RM-{uuid4().hex[:6]}"

        bom_service.upsert_bom_item(tid, parent, child, {"quantity": 2.0})

        monkeypatch.setattr(
            service, "get_inventory_status",
            lambda t, s: [
                {"sku": parent, "product_type": "finished_good",
                 "daily_demand": 10.0, "current_stock": 0,
                 "display_name": "Widget", "signal": "PEDIR_YA"},
                {"sku": child, "product_type": "raw_material",
                 "current_stock": 0, "unit_cost": None,
                 "display_name": "No Cost Component"},
            ],
        )

        result = bom_service.explode_requirements(tid, "sess-1", horizon_days=10)

        row = next(r for r in result["raw_material_summary"] if r["sku"] == child)
        assert row["shortage"] > 0
        assert row["estimated_cost"] is None

    def test_estimated_cost_is_none_when_no_shortage(self, client, monkeypatch, test_tenant):
        """No shortage means nothing to price, regardless of cost."""
        from backend.inventory import service

        tid = test_tenant["id"]
        parent = f"FG-{uuid4().hex[:6]}"
        child = f"RM-{uuid4().hex[:6]}"

        bom_service.upsert_bom_item(tid, parent, child, {"quantity": 1.0})

        monkeypatch.setattr(
            service, "get_inventory_status",
            lambda t, s: [
                {"sku": parent, "product_type": "finished_good",
                 "daily_demand": 1.0, "current_stock": 1000,
                 "display_name": "Widget", "signal": "OK"},
                {"sku": child, "product_type": "raw_material",
                 "current_stock": 1000, "unit_cost": 5.0,
                 "display_name": "Plenty Component"},
            ],
        )

        result = bom_service.explode_requirements(tid, "sess-1", horizon_days=10)

        row = next(r for r in result["raw_material_summary"] if r["sku"] == child)
        assert row["shortage"] == 0.0
        assert row["estimated_cost"] is None


class TestBomCrud:

    def test_upsert_rejects_self_reference(self, client, test_tenant):
        tid = test_tenant["id"]
        sku = f"SKU-{uuid4().hex[:6]}"
        try:
            bom_service.upsert_bom_item(tid, sku, sku, {"quantity": 1.0})
            assert False, "Expected ValueError for self-reference"
        except ValueError:
            pass

    def test_resending_same_pair_updates_instead_of_duplicating(self, client, test_tenant):
        tid = test_tenant["id"]
        parent = f"FG-{uuid4().hex[:6]}"
        child = f"RM-{uuid4().hex[:6]}"

        bom_service.upsert_bom_item(tid, parent, child, {"quantity": 1.0})
        bom_service.upsert_bom_item(tid, parent, child, {"quantity": 4.0})

        rows = bom_service.list_bom(tid, parent)
        assert len(rows) == 1
        assert rows[0]["quantity"] == 4.0

    def test_delete_removes_the_row(self, client, test_tenant):
        tid = test_tenant["id"]
        parent = f"FG-{uuid4().hex[:6]}"
        child = f"RM-{uuid4().hex[:6]}"

        bom_service.upsert_bom_item(tid, parent, child, {"quantity": 1.0})
        bom_service.delete_bom_item(tid, parent, child)

        assert bom_service.list_bom(tid, parent) == []
