"""
What-if scenario simulation (PENDIENTES #7).

Covers persistence (DB asserts on the `scenarios` table), the permission pair
for every mutating endpoint, and REAL behavior of the run endpoint: a demand
multiplier must strictly increase the units to order, a supplier delay must
push SKUs into PEDIR_YA, and an empty scenario must produce zero deltas.

The session used here carries a hand-built, fully deterministic forecast
(constant 10 units/day) so every expected signal/quantity is arithmetic, not a
snapshot of random fixture data.
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db.connection import query, query_one

# Constant demand makes the semáforo arithmetic exact:
#   lead time 10 days → lead-time demand = 100 units
#   std per point = upper − value = 2 → safety (z=1.645) = 1.645·2·√10 ≈ 10.4
_DAILY = 10.0
_UPPER = 12.0
_LOWER = 8.0
_LEAD_TIME = 10
_FC_START = date(2030, 1, 1)
_FC_DAYS = 30


def _forecast_series() -> dict:
    return {
        "historical": [
            {"date": (_FC_START - timedelta(days=_FC_DAYS - i)).isoformat(), "value": _DAILY}
            for i in range(_FC_DAYS)
        ],
        "forecast": [
            {
                "date": (_FC_START + timedelta(days=i)).isoformat(),
                "value": _DAILY,
                "lower": _LOWER,
                "upper": _UPPER,
            }
            for i in range(_FC_DAYS)
        ],
    }


@pytest.fixture
def scenario_session(client, auth_headers, registered_user, test_session):
    """
    Session with deterministic forecasts + stock for three SKUs:

        SCN_A  stock  40 → coverage  4d → PEDIR_YA
        SCN_B  stock  90 → coverage  9d → PEDIR_PRONTO (→ PEDIR_YA if lead ×2)
        SCN_C  stock 250 → coverage 25d → OK
    """
    from backend.db import session_store
    from backend.inventory import service as inv_svc

    tenant_id = registered_user["tenant"]["id"]
    session_id = test_session["id"]

    forecasts = {sku: {"lightgbm": _forecast_series()} for sku in ("SCN_A", "SCN_B", "SCN_C")}
    session_store.set_forecasts(tenant_id, session_id, forecasts)

    stock = {"SCN_A": (40.0, "Bebidas"), "SCN_B": (90.0, "Abarrotes"), "SCN_C": (250.0, "Abarrotes")}
    for sku, (units, category) in stock.items():
        inv_svc.upsert_stock(tenant_id, sku, {
            "current_stock":  units,
            "lead_time_days": _LEAD_TIME,
            "moq":            1.0,
            "unit_cost":      2.0,
            "supplier":       "Proveedor Uno",
            "category":       category,
        })

    return {"tenant_id": tenant_id, "session_id": session_id}


def _run(client, headers, session_id, rules):
    resp = client.post(
        f"/api/v1/sessions/{session_id}/scenarios/preview",
        json={"rules": rules},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ── Baseline sanity: the fixture really produces the signals we reason about ──

class TestBaseline:
    def test_base_run_matches_hand_computed_semaforo(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"], [])
        base = data["base"]
        assert base["skus_evaluated"] == 3
        assert base["order_now"] == 1        # SCN_A
        assert base["order_soon"] == 1       # SCN_B
        assert base["ok"] == 1               # SCN_C
        # SCN_A: 100 lead-time demand + 10.4 safety − 40 stock = 70.4 → ceil(MOQ 1) = 71
        by_sku = {c["sku"]: c for c in data["changes"]}
        assert by_sku == {}, "an empty scenario must not report any change"
        assert base["total_units_to_order"] > 0


# ── Anti-false-positive guard ────────────────────────────────────────────────

class TestEmptyScenario:
    def test_no_rules_gives_zero_deltas(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"], [])
        assert data["base"] == data["scenario"]
        assert data["delta"] == {
            "skus_to_order": 0, "total_units_to_order": 0.0,
            "estimated_purchase_value": 0.0, "order_now": 0, "order_soon": 0,
            "ok": 0, "overstock": 0, "no_data": 0,
        }
        assert data["changes"] == []
        assert data["applied"]["series_adjusted"] == 0

    def test_multiplier_of_one_changes_nothing(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "demand_multiplier", "multiplier": 1.0}])
        assert data["applied"]["series_adjusted"] == 3   # rules really were applied…
        assert data["delta"]["total_units_to_order"] == 0.0   # …and ×1.0 is a no-op
        assert data["changes"] == []


# ── Real behavior of each rule type ──────────────────────────────────────────

class TestDemandMultiplier:
    def test_x15_strictly_increases_units_to_order(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "demand_multiplier", "multiplier": 1.5}])

        base, scenario = data["base"], data["scenario"]
        assert scenario["total_units_to_order"] > base["total_units_to_order"]
        assert data["delta"]["total_units_to_order"] > 0
        assert scenario["estimated_purchase_value"] > base["estimated_purchase_value"]

        # SCN_A: demand 15/day → 150 + 1.645·3·√10 (≈15.6) − 40 = 125.6 → 126
        row = next(c for c in data["changes"] if c["sku"] == "SCN_A")
        assert row["base_qty"] == 71.0
        assert row["scenario_qty"] == 126.0
        assert row["delta_qty"] == 55.0
        assert row["scenario_daily_demand"] == 15.0
        assert row["delta_value"] == 110.0     # 55 units × $2

    def test_lower_multiplier_decreases_units_to_order(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "demand_multiplier", "multiplier": 0.5}])
        assert data["delta"]["total_units_to_order"] < 0
        assert data["scenario"]["order_now"] <= data["base"]["order_now"]

    def test_sku_scoped_rule_touches_only_that_sku(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "demand_multiplier", "multiplier": 2.0, "sku": "SCN_A"}])
        assert data["applied"]["series_adjusted"] == 1
        assert [c["sku"] for c in data["changes"]] == ["SCN_A"]

    def test_category_scoped_rule_expands_to_its_skus(self, client, auth_headers, scenario_session):
        # ×3 is big enough to move BOTH Abarrotes SKUs off their base signal,
        # so the assertion proves the category expanded to two series and left
        # the Bebidas SKU alone.
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "demand_multiplier", "multiplier": 3.0, "category": "Abarrotes"}])
        assert data["applied"]["series_adjusted"] == 2      # SCN_B + SCN_C
        assert sorted(c["sku"] for c in data["changes"]) == ["SCN_B", "SCN_C"]
        assert next(c for c in data["changes"] if c["sku"] == "SCN_C")["scenario_daily_demand"] == 30.0

    def test_unknown_category_matches_nothing(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "demand_multiplier", "multiplier": 3.0, "category": "No Existe"}])
        assert data["applied"]["series_adjusted"] == 0
        assert data["delta"]["total_units_to_order"] == 0.0


class TestPromoWindow:
    def test_promo_outside_the_lead_time_window_has_no_effect(self, client, auth_headers, scenario_session):
        """The semáforo only averages the first `lead_time` forecast buckets, so a
        promo starting after them must not move a single unit — proof the date
        filter is real and not ignored."""
        data = _run(client, auth_headers, scenario_session["session_id"], [{
            "type": "promo", "multiplier": 2.0,
            "date_from": (_FC_START + timedelta(days=20)).isoformat(),
            "date_to":   (_FC_START + timedelta(days=29)).isoformat(),
        }])
        assert data["applied"]["series_adjusted"] == 3
        assert data["delta"]["total_units_to_order"] == 0.0

    def test_promo_inside_the_window_increases_orders(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"], [{
            "type": "promo", "multiplier": 2.0,
            "date_from": _FC_START.isoformat(),
            "date_to":   (_FC_START + timedelta(days=9)).isoformat(),
        }])
        assert data["delta"]["total_units_to_order"] > 0

    def test_promo_without_dates_is_rejected(self, client, auth_headers, scenario_session):
        resp = client.post(
            f"/api/v1/sessions/{scenario_session['session_id']}/scenarios/preview",
            json={"rules": [{"type": "promo", "multiplier": 2.0}]},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text


class TestSupplierDelay:
    def test_delay_pushes_more_skus_into_pedir_ya(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "supplier_delay", "extra_days": 10}])

        assert data["base"]["order_now"] == 1
        assert data["scenario"]["order_now"] > data["base"]["order_now"]
        assert data["delta"]["order_now"] >= 1
        # Doubling the lead time doubles the lead-time demand → more to order.
        assert data["delta"]["total_units_to_order"] > 0

        row = next(c for c in data["changes"] if c["sku"] == "SCN_B")
        assert row["base_signal"] == "PEDIR_PRONTO"
        assert row["scenario_signal"] == "PEDIR_YA"
        assert row["base_lead_time_days"] == _LEAD_TIME
        assert row["scenario_lead_time_days"] == _LEAD_TIME + 10

    def test_delay_scoped_to_another_supplier_is_a_no_op(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "supplier_delay", "extra_days": 30, "supplier": "Proveedor Fantasma"}])
        assert data["delta"]["total_units_to_order"] == 0.0
        assert data["changes"] == []

    def test_delay_scoped_to_the_right_supplier_applies(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "supplier_delay", "extra_days": 10, "supplier": "proveedor uno"}])
        assert data["delta"]["total_units_to_order"] > 0


class TestSafetyStock:
    def test_higher_service_level_orders_more(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "safety_stock", "service_level": 0.99}])
        # z goes 1.645 → 2.326, so the safety cushion (and the order) grows.
        assert data["delta"]["total_units_to_order"] > 0
        assert data["applied"]["service_level"] == 0.99
        row = next(c for c in data["changes"] if c["sku"] == "SCN_A")
        assert row["scenario_qty"] > row["base_qty"]
        assert row["base_signal"] == row["scenario_signal"] == "PEDIR_YA"

    def test_lower_service_level_orders_less(self, client, auth_headers, scenario_session):
        data = _run(client, auth_headers, scenario_session["session_id"],
                    [{"type": "safety_stock", "service_level": 0.90}])
        assert data["delta"]["total_units_to_order"] < 0


class TestCombinedRules:
    def test_demand_and_delay_compound(self, client, auth_headers, scenario_session):
        only_demand = _run(client, auth_headers, scenario_session["session_id"],
                           [{"type": "demand_multiplier", "multiplier": 1.5}])
        both = _run(client, auth_headers, scenario_session["session_id"], [
            {"type": "demand_multiplier", "multiplier": 1.5},
            {"type": "supplier_delay", "extra_days": 10},
        ])
        assert (both["scenario"]["total_units_to_order"]
                > only_demand["scenario"]["total_units_to_order"])


# ── Persistence + permissions ────────────────────────────────────────────────

def _count_scenarios(tenant_id: str) -> int:
    return query_one(
        "SELECT COUNT(*) AS n FROM scenarios WHERE tenant_id = %s", (tenant_id,)
    )["n"]


class TestScenarioCrud:
    def test_analyst_creates_scenario_row_with_its_rules(
        self, client, analyst_headers, scenario_session
    ):
        tenant_id, session_id = scenario_session["tenant_id"], scenario_session["session_id"]
        name = f"Black Friday {uuid4().hex[:6]}"
        rules = [
            {"type": "demand_multiplier", "multiplier": 1.4, "label": "BF"},
            {"type": "supplier_delay", "extra_days": 5},
        ]
        resp = client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": name, "rules": rules},
            headers=analyst_headers,
        )
        assert resp.status_code == 201, resp.text
        scenario_id = resp.json()["data"]["id"]

        row = query_one("SELECT * FROM scenarios WHERE id = %s", (scenario_id,))
        assert row is not None
        assert row["tenant_id"] == tenant_id
        assert row["session_id"] == session_id
        assert row["name"] == name
        assert row["rules"] == [
            {"type": "demand_multiplier", "multiplier": 1.4, "label": "BF"},
            {"type": "supplier_delay", "extra_days": 5},
        ]
        assert row["created_by"]

    def test_viewer_cannot_create_and_db_is_unchanged(
        self, client, viewer_headers, scenario_session
    ):
        tenant_id, session_id = scenario_session["tenant_id"], scenario_session["session_id"]
        before = _count_scenarios(tenant_id)
        resp = client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": "no debería guardarse", "rules": []},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert _count_scenarios(tenant_id) == before

    def test_viewer_cannot_delete_and_row_survives(
        self, client, analyst_headers, viewer_headers, scenario_session
    ):
        session_id = scenario_session["session_id"]
        created = client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": "para borrar", "rules": []},
            headers=analyst_headers,
        )
        scenario_id = created.json()["data"]["id"]

        resp = client.delete(f"/api/v1/scenarios/{scenario_id}", headers=viewer_headers)
        assert resp.status_code == 403, resp.text
        assert query_one("SELECT id FROM scenarios WHERE id = %s", (scenario_id,)) is not None

        resp = client.delete(f"/api/v1/scenarios/{scenario_id}", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        assert query_one("SELECT id FROM scenarios WHERE id = %s", (scenario_id,)) is None

    def test_list_is_scoped_to_tenant_and_session(
        self, client, analyst_headers, auth_headers, scenario_session
    ):
        tenant_id, session_id = scenario_session["tenant_id"], scenario_session["session_id"]
        client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": "mio", "rules": []},
            headers=analyst_headers,
        )
        # A scenario of the same tenant on ANOTHER session must not show up.
        client.post(
            "/api/v1/sessions/otra-sesion/scenarios",
            json={"name": "otra", "rules": []},
            headers=analyst_headers,
        )
        resp = client.get(f"/api/v1/sessions/{session_id}/scenarios", headers=auth_headers)
        assert resp.status_code == 200
        names = [s["name"] for s in resp.json()["data"]]
        assert "mio" in names and "otra" not in names

        rows = query("SELECT session_id FROM scenarios WHERE tenant_id = %s", (tenant_id,))
        assert {r["session_id"] for r in rows} == {session_id, "otra-sesion"}

    def test_get_unknown_scenario_is_404(self, client, auth_headers):
        resp = client.get("/api/v1/scenarios/no-existe", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_unknown_scenario_is_404(self, client, analyst_headers):
        resp = client.delete("/api/v1/scenarios/no-existe", headers=analyst_headers)
        assert resp.status_code == 404

    def test_invalid_rule_type_is_rejected_and_nothing_is_saved(
        self, client, analyst_headers, scenario_session
    ):
        tenant_id, session_id = scenario_session["tenant_id"], scenario_session["session_id"]
        before = _count_scenarios(tenant_id)
        resp = client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": "mala", "rules": [{"type": "teletransporte", "multiplier": 2}]},
            headers=analyst_headers,
        )
        assert resp.status_code == 422, resp.text
        assert _count_scenarios(tenant_id) == before


class TestRunSavedScenario:
    def test_saved_scenario_runs_with_its_own_rules(
        self, client, analyst_headers, auth_headers, scenario_session
    ):
        session_id = scenario_session["session_id"]
        created = client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": "Navidad +50%",
                  "rules": [{"type": "demand_multiplier", "multiplier": 1.5}]},
            headers=analyst_headers,
        )
        scenario_id = created.json()["data"]["id"]

        resp = client.post(
            f"/api/v1/sessions/{session_id}/scenarios/{scenario_id}/run",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["scenario_id"] == scenario_id
        assert data["name"] == "Navidad +50%"
        assert data["delta"]["total_units_to_order"] > 0

        inline = _run(client, auth_headers, session_id,
                      [{"type": "demand_multiplier", "multiplier": 1.5}])
        assert data["scenario"] == inline["scenario"]

    def test_running_a_scenario_of_another_session_is_404(
        self, client, analyst_headers, auth_headers, scenario_session
    ):
        session_id = scenario_session["session_id"]
        created = client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": "x", "rules": []},
            headers=analyst_headers,
        )
        scenario_id = created.json()["data"]["id"]
        resp = client.post(
            f"/api/v1/sessions/sesion-ajena/scenarios/{scenario_id}/run",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_run_is_a_pure_read(self, client, analyst_headers, auth_headers, scenario_session):
        """Running must not write anything — the scenario row and the stock rows
        stay byte-identical."""
        tenant_id, session_id = scenario_session["tenant_id"], scenario_session["session_id"]
        created = client.post(
            f"/api/v1/sessions/{session_id}/scenarios",
            json={"name": "solo lectura",
                  "rules": [{"type": "supplier_delay", "extra_days": 20},
                            {"type": "safety_stock", "service_level": 0.99}]},
            headers=analyst_headers,
        )
        scenario_id = created.json()["data"]["id"]

        before = query(
            "SELECT sku, lead_time_days, service_level, current_stock FROM inventory_stock "
            "WHERE tenant_id = %s ORDER BY sku", (tenant_id,))
        row_before = query_one("SELECT * FROM scenarios WHERE id = %s", (scenario_id,))

        resp = client.post(
            f"/api/v1/sessions/{session_id}/scenarios/{scenario_id}/run",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        after = query(
            "SELECT sku, lead_time_days, service_level, current_stock FROM inventory_stock "
            "WHERE tenant_id = %s ORDER BY sku", (tenant_id,))
        assert after == before
        assert query_one("SELECT * FROM scenarios WHERE id = %s", (scenario_id,)) == row_before

    def test_session_without_forecasts_returns_a_valid_empty_comparison(
        self, client, auth_headers
    ):
        data = _run(client, auth_headers, "sesion-sin-forecast",
                    [{"type": "demand_multiplier", "multiplier": 2.0}])
        assert data["base"]["skus_evaluated"] == 0
        assert data["scenario"]["skus_evaluated"] == 0
        assert data["delta"]["total_units_to_order"] == 0.0
        assert data["changes"] == []


# ── Bridge: the demand adjustment goes through ForecastingCore ───────────────

class TestForecastBridge:
    @pytest.mark.offline
    def test_bridge_scales_values_and_preserves_missing_bands(self):
        from backend.scenarios import bridge

        forecasts = {
            "A": {"m1": {"historical": [{"date": "2029-12-31", "value": 5.0}],
                         "forecast": [{"date": "2030-01-01", "value": 10.0,
                                       "lower": 8.0, "upper": 12.0}]}},
            "B": {"m1": {"forecast": [{"date": "2030-01-01", "value": 4.0}]}},
        }
        out = bridge.apply_rules_to_forecasts(
            forecasts, [bridge.build_core_rule(series_key=None, multiplier=2.0)]
        )

        assert out["A"]["m1"]["forecast"][0]["value"] == 20.0
        assert out["A"]["m1"]["forecast"][0]["upper"] == 24.0
        assert out["A"]["m1"]["forecast"][0]["lower"] == 16.0
        assert out["A"]["m1"]["historical"] == [{"date": "2029-12-31", "value": 5.0}]
        # A point stored without bands must not gain fabricated ones.
        assert out["B"]["m1"]["forecast"][0] == {"date": "2030-01-01", "value": 8.0}
        # Input untouched.
        assert forecasts["A"]["m1"]["forecast"][0]["value"] == 10.0

    @pytest.mark.offline
    def test_bridge_is_identity_without_rules(self):
        from backend.scenarios import bridge

        forecasts = {"A": {"m1": {"forecast": [{"date": "2030-01-01", "value": 10.0}]}}}
        assert bridge.apply_rules_to_forecasts(forecasts, []) is forecasts

    @pytest.mark.offline
    def test_store_keyed_series_are_targeted_by_bare_sku(self):
        from backend.scenarios import service as scn_svc

        forecasts = {"A│Tienda 1": {}, "A│Tienda 2": {}, "B│Tienda 1": {}}
        rules = scn_svc._build_demand_rules(
            [{"type": "demand_multiplier", "multiplier": 2.0, "sku": "A"}],
            forecasts, [],
        )
        assert sorted(r["sku"] for r in rules) == ["A│Tienda 1", "A│Tienda 2"]
