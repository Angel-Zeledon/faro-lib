"""
Supplier price breaks (feature 3.5).

Covers the CRUD endpoints (with permission pairs) and, above all, the
"is stepping up worth it?" criterion in price_break_service — with cases that
FIRE the recommendation and cases that deliberately do not, since a criterion
that always says yes is the exact failure mode this feature must avoid.
"""

import pytest
from uuid import uuid4

from backend.db.connection import query_one, query
from backend.inventory import price_break_service as pb


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(resp, code=200):
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"
    return resp.json()["data"]


def _make_supplier(client, headers, name=None):
    r = client.post("/api/v1/inventory/suppliers", headers=headers, json={
        "name": name or f"PriceBreak Supplier {uuid4().hex[:6]}",
        "lead_time_days": 10,
    })
    return _ok(r, 201)


def _breaks(*pairs):
    """Build price-break rows as the service consumes them."""
    return [{"min_qty": q, "unit_price": p, "supplier_name": "Acme"} for q, p in pairs]


# ── The criterion: cases that FIRE ────────────────────────────────────────────

class TestStepUpWorthIt:

    def test_clear_discount_with_fast_demand_is_worth_it(self):
        """Big unit-price drop, demand drains the extra units in days:
        gross saving dwarfs holding cost, so the step-up is recommended."""
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((150, 9.0)),
            daily_demand=20.0,      # 50 extra units = 2.5 extra days of cover
            current_stock=0, lead_time_days=15,
        )
        assert opp is not None
        assert opp["worth_it"] is True, opp["reason_code"]
        # gross = 150 * (10 - 9) = 150
        assert opp["gross_saving"] == pytest.approx(150.0)
        assert opp["holding_cost"] < opp["gross_saving"]
        assert opp["net_saving"] == pytest.approx(
            opp["gross_saving"] - opp["holding_cost"], abs=0.01
        )
        assert opp["step_quantity"] == 150
        assert opp["extra_units"] == 50

    def test_cheapest_unit_price_does_not_automatically_win(self):
        """Two rungs above the cart. The far one has the lower unit price but
        lands past the overstock limit, so the near rung is what gets
        recommended — the winner is the best QUALIFYING deal, not the cheapest
        sticker price."""
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((120, 9.5), (2000, 9.0)),
            daily_demand=30.0,       # 2000 units = 66.7 days vs a 60-day limit
            current_stock=0, lead_time_days=20,
        )
        assert opp["worth_it"] is True
        assert opp["step_quantity"] == 120, "Cheaper-but-overstocking rung must not win"

    def test_largest_net_saving_wins_among_qualifying_rungs(self):
        """When several rungs all clear the guardrails, the biggest net saving
        wins rather than the nearest one."""
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((120, 9.9), (400, 9.0)),
            daily_demand=30.0, current_stock=0, lead_time_days=20,
        )
        assert opp["worth_it"] is True
        assert opp["step_quantity"] == 400

    def test_reports_the_money_the_ui_needs(self):
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((150, 9.0)),
            daily_demand=20.0, current_stock=0, lead_time_days=15,
        )
        assert opp["unit_price_drop_pct"] == pytest.approx(0.10)
        # Cash out the door grows even though the unit price falls.
        assert opp["extra_cash_now"] == pytest.approx(150 * 9.0 - 100 * 10.0)
        assert opp["extra_coverage_days"] == pytest.approx(2.5)


# ── The criterion: cases that must NOT fire ───────────────────────────────────

class TestStepUpRejected:

    def test_cheaper_unit_price_but_creates_overstock_is_rejected(self):
        """The headline case the plan warns about: the unit price genuinely
        drops 20%, but the quantity is a year of stock for this SKU. Faro's own
        semáforo would paint it SOBRESTOCK, so it must not be recommended."""
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=50, base_cost=10.0,
            breaks=_breaks((2000, 8.0)),
            daily_demand=5.0,       # 2000 units = 400 days of coverage
            current_stock=0, lead_time_days=10,
        )
        assert opp is not None
        assert opp["worth_it"] is False
        assert opp["reason_code"] == "would_overstock"
        # The discount is real and still reported — the UI explains, not hides.
        assert opp["unit_price_drop_pct"] == pytest.approx(0.20)
        assert opp["total_coverage_days"] == pytest.approx(400.0)

    def test_holding_cost_exceeding_saving_is_rejected(self):
        """A token discount on a slow mover: the units sit long enough that
        carrying them costs more than the discount saves."""
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((160, 9.999)),   # a 0.01% discount
            daily_demand=2.0,               # 60 extra units = 30 extra days
            current_stock=0, lead_time_days=30,
        )
        assert opp["worth_it"] is False
        assert opp["reason_code"] == "holding_exceeds_saving"
        assert opp["holding_cost"] > opp["gross_saving"]
        assert opp["net_saving"] < 0

    def test_no_demand_never_advances_a_purchase(self):
        """Zero forecast demand: the 'we'd buy them later anyway' premise that
        justifies the gross saving collapses. Never recommended."""
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((200, 5.0)),   # a 50% discount
            daily_demand=0.0, current_stock=0, lead_time_days=10,
        )
        assert opp["worth_it"] is False
        assert opp["reason_code"] == "no_demand"

    def test_immaterial_saving_is_rejected(self):
        """A net saving far below 1% of the order is not worth a nudge."""
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.00,
            breaks=_breaks((105, 9.998)),
            daily_demand=50.0, current_stock=0, lead_time_days=15,
        )
        assert opp["worth_it"] is False
        assert opp["reason_code"] == "saving_immaterial"

    def test_rung_with_no_discount_is_rejected(self):
        opp = pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((200, 10.0)),
            daily_demand=50.0, current_stock=0, lead_time_days=15,
        )
        assert opp["worth_it"] is False
        assert opp["reason_code"] == "no_discount"

    def test_existing_stock_counts_toward_the_overstock_guardrail(self):
        """Same order, same demand — but a warehouse already full. The coverage
        the guardrail measures is stock + order, not the order alone."""
        kwargs = dict(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((200, 9.0)),
            daily_demand=10.0, lead_time_days=20,
        )
        empty = pb.evaluate_step_up(current_stock=0, **kwargs)
        full  = pb.evaluate_step_up(current_stock=500, **kwargs)
        assert empty["worth_it"] is True
        assert full["worth_it"] is False
        assert full["reason_code"] == "would_overstock"

    def test_no_rung_above_current_quantity_returns_none(self):
        """Already at or above the top rung: there is nothing to suggest."""
        assert pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=500, base_cost=10.0,
            breaks=_breaks((100, 9.0), (200, 8.5)),
            daily_demand=10.0, current_stock=0, lead_time_days=15,
        ) is None

    def test_missing_cost_returns_none(self):
        """No unit_cost means no way to price the trade — stay silent
        rather than guess."""
        assert pb.evaluate_step_up(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=None,
            breaks=_breaks((200, 9.0)),
            daily_demand=10.0, current_stock=0, lead_time_days=15,
        ) is None


class TestEffectiveUnitPrice:

    def test_all_units_semantics_picks_highest_reached_rung(self):
        breaks = _breaks((50, 9.0), (100, 8.0), (500, 7.0))
        assert pb.effective_unit_price(breaks, 10, 10.0) == 10.0    # below rung 1
        assert pb.effective_unit_price(breaks, 50, 10.0) == 9.0     # exactly rung 1
        assert pb.effective_unit_price(breaks, 120, 10.0) == 8.0
        assert pb.effective_unit_price(breaks, 10_000, 10.0) == 7.0

    def test_no_breaks_falls_back_to_base_cost(self):
        assert pb.effective_unit_price([], 100, 4.25) == 4.25
        assert pb.effective_unit_price([], 100, None) is None


class TestHoldingCostSensitivity:

    def test_higher_holding_cost_can_flip_the_verdict(self):
        """The same deal judged under a business that carries inventory cheaply
        vs. expensively — proves holding_cost_pct is actually wired through and
        is not decorative."""
        kwargs = dict(
            sku="SKU-1", supplier_name="Acme",
            current_quantity=100, base_cost=10.0,
            breaks=_breaks((880, 9.15)),
            daily_demand=12.0, current_stock=0, lead_time_days=30,
        )
        cheap = pb.evaluate_step_up(holding_cost_pct=0.05, **kwargs)
        dear  = pb.evaluate_step_up(holding_cost_pct=1.20, **kwargs)

        # Identical deal, identical gross saving — only the carrying rate moves.
        assert cheap["gross_saving"] == dear["gross_saving"]
        assert dear["holding_cost"] > cheap["holding_cost"]
        assert cheap["worth_it"] is True
        assert dear["worth_it"] is False
        assert dear["reason_code"] == "holding_exceeds_saving"


# ── CRUD endpoints + permission pairs ─────────────────────────────────────────

class TestPriceBreakCRUD:

    def test_analyst_can_create_price_break_and_it_persists(self, client, auth_headers, analyst_headers):
        supplier = _make_supplier(client, auth_headers)
        sku = f"SKU-{uuid4().hex[:6].upper()}"

        r = client.post(
            f"/api/v1/inventory/suppliers/{supplier['id']}/price-breaks",
            headers=analyst_headers,
            json={"sku": sku, "min_qty": 100, "unit_price": 8.5},
        )
        data = _ok(r, 201)

        row = query_one(
            "SELECT sku, min_qty, unit_price, supplier_id FROM supplier_price_breaks WHERE id = %s",
            (data["id"],),
        )
        assert row is not None, "Price break was not persisted"
        assert row["sku"] == sku
        assert row["min_qty"] == 100
        assert row["unit_price"] == 8.5
        assert row["supplier_id"] == supplier["id"]

    def test_viewer_cannot_create_price_break_and_nothing_is_written(
        self, client, auth_headers, viewer_headers,
    ):
        supplier = _make_supplier(client, auth_headers)
        sku = f"SKU-{uuid4().hex[:6].upper()}"

        r = client.post(
            f"/api/v1/inventory/suppliers/{supplier['id']}/price-breaks",
            headers=viewer_headers,
            json={"sku": sku, "min_qty": 100, "unit_price": 8.5},
        )
        assert r.status_code == 403

        rows = query(
            "SELECT id FROM supplier_price_breaks WHERE supplier_id = %s AND sku = %s",
            (supplier["id"], sku),
        )
        assert rows == [], "Viewer's rejected request still wrote a price break"

    def test_resending_same_min_qty_updates_instead_of_duplicating(
        self, client, auth_headers, analyst_headers,
    ):
        supplier = _make_supplier(client, auth_headers)
        sku = f"SKU-{uuid4().hex[:6].upper()}"
        url = f"/api/v1/inventory/suppliers/{supplier['id']}/price-breaks"

        _ok(client.post(url, headers=analyst_headers,
                        json={"sku": sku, "min_qty": 100, "unit_price": 9.0}), 201)
        _ok(client.post(url, headers=analyst_headers,
                        json={"sku": sku, "min_qty": 100, "unit_price": 7.5}), 201)

        rows = query(
            "SELECT unit_price FROM supplier_price_breaks WHERE supplier_id = %s AND sku = %s",
            (supplier["id"], sku),
        )
        assert len(rows) == 1, "Same rung was duplicated instead of updated"
        assert rows[0]["unit_price"] == 7.5

    def test_analyst_can_delete_price_break_and_row_is_gone(
        self, client, auth_headers, analyst_headers,
    ):
        supplier = _make_supplier(client, auth_headers)
        created = _ok(client.post(
            f"/api/v1/inventory/suppliers/{supplier['id']}/price-breaks",
            headers=analyst_headers,
            json={"sku": f"SKU-{uuid4().hex[:6].upper()}", "min_qty": 50, "unit_price": 9.0},
        ), 201)

        r = client.delete(f"/api/v1/inventory/price-breaks/{created['id']}", headers=analyst_headers)
        assert r.status_code == 204
        assert query_one(
            "SELECT id FROM supplier_price_breaks WHERE id = %s", (created["id"],),
        ) is None

    def test_viewer_cannot_delete_price_break_and_row_survives(
        self, client, auth_headers, analyst_headers, viewer_headers,
    ):
        supplier = _make_supplier(client, auth_headers)
        created = _ok(client.post(
            f"/api/v1/inventory/suppliers/{supplier['id']}/price-breaks",
            headers=analyst_headers,
            json={"sku": f"SKU-{uuid4().hex[:6].upper()}", "min_qty": 50, "unit_price": 9.0},
        ), 201)

        r = client.delete(f"/api/v1/inventory/price-breaks/{created['id']}", headers=viewer_headers)
        assert r.status_code == 403
        assert query_one(
            "SELECT id FROM supplier_price_breaks WHERE id = %s", (created["id"],),
        ) is not None, "Viewer's rejected DELETE still removed the row"

    def test_create_for_unknown_supplier_returns_404(self, client, analyst_headers):
        r = client.post(
            f"/api/v1/inventory/suppliers/{uuid4().hex}/price-breaks",
            headers=analyst_headers,
            json={"sku": "SKU-X", "min_qty": 10, "unit_price": 1.0},
        )
        assert r.status_code == 404

    def test_list_is_scoped_to_the_supplier_and_sku_asked_for(
        self, client, auth_headers, analyst_headers,
    ):
        supplier = _make_supplier(client, auth_headers)
        sku_a = f"SKU-{uuid4().hex[:6].upper()}"
        sku_b = f"SKU-{uuid4().hex[:6].upper()}"
        url = f"/api/v1/inventory/suppliers/{supplier['id']}/price-breaks"
        _ok(client.post(url, headers=analyst_headers,
                        json={"sku": sku_a, "min_qty": 100, "unit_price": 9.0}), 201)
        _ok(client.post(url, headers=analyst_headers,
                        json={"sku": sku_b, "min_qty": 100, "unit_price": 8.0}), 201)

        data = _ok(client.get(
            f"/api/v1/inventory/price-breaks?supplier_id={supplier['id']}&sku={sku_a}",
            headers=auth_headers,
        ))
        assert [d["sku"] for d in data] == [sku_a]

    def test_deleting_a_supplier_is_soft_and_hides_its_scales(
        self, client, auth_headers, analyst_headers,
    ):
        """Suppliers are soft-deleted (active = FALSE); the scale must stop
        showing up rather than keep pricing orders for a dead supplier."""
        supplier = _make_supplier(client, auth_headers)
        sku = f"SKU-{uuid4().hex[:6].upper()}"
        _ok(client.post(
            f"/api/v1/inventory/suppliers/{supplier['id']}/price-breaks",
            headers=analyst_headers, json={"sku": sku, "min_qty": 100, "unit_price": 9.0},
        ), 201)

        assert client.delete(
            f"/api/v1/inventory/suppliers/{supplier['id']}", headers=analyst_headers,
        ).status_code == 204

        data = _ok(client.get(f"/api/v1/inventory/price-breaks?sku={sku}", headers=auth_headers))
        assert data == []


class TestEvaluateEndpoint:

    def test_evaluate_requires_auth(self, client):
        r = client.post("/api/v1/inventory/price-breaks/evaluate?session_id=abc", json={"items": []})
        assert r.status_code in (401, 403)

    def test_viewer_may_evaluate_it_is_read_only(self, client, viewer_headers, completed_session):
        """Evaluation writes nothing, so a viewer is allowed to run it."""
        r = client.post(
            f"/api/v1/inventory/price-breaks/evaluate?session_id={completed_session}",
            headers=viewer_headers, json={"items": []},
        )
        assert r.status_code == 200
        assert "opportunities" in r.json()["data"]
