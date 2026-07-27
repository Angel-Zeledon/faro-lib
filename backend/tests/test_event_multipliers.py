"""
Per-product multipliers and the multiplier explanation (feature 3.4).

A single multiplier per event is false in practice: on Black Friday
electronics spike and milk does not move. These tests pin:
  * the sku > category > event resolution order,
  * that every simulated row reports WHICH multiplier applied and WHY,
  * the multiplier explanation (catalog estimate vs. user-set),
  * y el par de permisos en los endpoints que mutan.
"""
from datetime import date, timedelta
from unittest import mock

import pytest

from backend.db.connection import query, query_one
from backend.inventory import service as inv_svc


def _item(sku, daily, stock, category=None, lead_time=10, moq=1, cost=2.0, family=None):
    return {
        "sku": sku, "display_name": f"Prod {sku}", "supplier": "Prov A",
        "category": category, "family": family,
        "daily_demand": daily, "current_stock": stock,
        "lead_time_days": lead_time, "moq": moq, "unit_cost": cost,
    }


# ── Pure resolution (no DB) ─────────────────────────────────────────────────

class TestResolution:
    @pytest.mark.offline
    def test_sku_override_wins_over_category_and_event(self):
        idx = inv_svc._index_overrides([
            {"scope": "sku", "scope_value": "TV-1", "multiplier": 4.0},
            {"scope": "category", "scope_value": "electronica", "multiplier": 2.5},
        ])
        item = _item("TV-1", 10, 100, category="Electronica")
        assert inv_svc._resolve_multiplier(item, 1.5, idx) == (4.0, "sku")

    @pytest.mark.offline
    def test_category_override_used_when_no_sku_override(self):
        idx = inv_svc._index_overrides([
            {"scope": "category", "scope_value": "electronica", "multiplier": 2.5},
        ])
        item = _item("TV-9", 10, 100, category="Electronica")
        assert inv_svc._resolve_multiplier(item, 1.5, idx) == (2.5, "category")

    @pytest.mark.offline
    def test_category_match_is_case_insensitive(self):
        idx = inv_svc._index_overrides([
            {"scope": "category", "scope_value": "  ELECTRONICA ", "multiplier": 3.0},
        ])
        item = _item("TV-9", 10, 100, category="electronica")
        assert inv_svc._resolve_multiplier(item, 1.0, idx) == (3.0, "category")

    @pytest.mark.offline
    def test_falls_back_to_event_multiplier(self):
        idx = inv_svc._index_overrides([])
        item = _item("LECHE-1", 10, 100, category="Lacteos")
        assert inv_svc._resolve_multiplier(item, 1.5, idx) == (1.5, "event")

    @pytest.mark.offline
    def test_item_without_category_falls_back(self):
        idx = inv_svc._index_overrides([
            {"scope": "category", "scope_value": "electronica", "multiplier": 3.0},
        ])
        item = _item("X-1", 10, 100, category=None)
        assert inv_svc._resolve_multiplier(item, 1.2, idx) == (1.2, "event")


# ── El caso que motiva la feature ────────────────────────────────────────────

class TestFamilyScope:
    """PENDIENTES #6: a family sits between category and SKU — a Christmas
    multiplier is rarely uniform across a whole category."""

    @pytest.mark.offline
    def test_family_beats_category(self):
        idx = inv_svc._index_overrides([
            {"scope": "family", "scope_value": "gaseosas", "multiplier": 3.0},
            {"scope": "category", "scope_value": "bebidas", "multiplier": 1.2},
        ])
        item = _item("COCA-1L", 10, 100, category="Bebidas", family="Gaseosas")
        assert inv_svc._resolve_multiplier(item, 1.0, idx) == (3.0, "family")

    @pytest.mark.offline
    def test_sku_still_beats_family(self):
        idx = inv_svc._index_overrides([
            {"scope": "sku", "scope_value": "COCA-1L", "multiplier": 5.0},
            {"scope": "family", "scope_value": "gaseosas", "multiplier": 3.0},
        ])
        item = _item("COCA-1L", 10, 100, category="Bebidas", family="Gaseosas")
        assert inv_svc._resolve_multiplier(item, 1.0, idx) == (5.0, "sku")

    @pytest.mark.offline
    def test_family_match_is_case_insensitive(self):
        idx = inv_svc._index_overrides([
            {"scope": "family", "scope_value": "  GASEOSAS ", "multiplier": 2.0},
        ])
        item = _item("COCA-1L", 10, 100, family="Gaseosas")
        assert inv_svc._resolve_multiplier(item, 1.0, idx) == (2.0, "family")

    @pytest.mark.offline
    def test_item_without_family_falls_through_to_category(self):
        idx = inv_svc._index_overrides([
            {"scope": "family", "scope_value": "gaseosas", "multiplier": 3.0},
            {"scope": "category", "scope_value": "bebidas", "multiplier": 1.2},
        ])
        item = _item("AGUA-1L", 10, 100, category="Bebidas")
        assert inv_svc._resolve_multiplier(item, 1.0, idx) == (1.2, "category")

    @pytest.mark.offline
    def test_unknown_legacy_scope_is_ignored_not_fatal(self):
        idx = inv_svc._index_overrides([
            {"scope": "retired_scope", "scope_value": "x", "multiplier": 9.0},
            {"scope": "family", "scope_value": "gaseosas", "multiplier": 3.0},
        ])
        item = _item("COCA-1L", 10, 100, family="Gaseosas")
        assert inv_svc._resolve_multiplier(item, 1.0, idx) == (3.0, "family")

    @pytest.mark.offline
    def test_christmas_hits_families_differently_inside_one_category(self):
        """Rompope x4 and rice x1.4 both live under 'abarrotes'."""
        idx = inv_svc._index_overrides([
            {"scope": "family", "scope_value": "licores navidenos", "multiplier": 4.0},
            {"scope": "family", "scope_value": "granos", "multiplier": 1.4},
            {"scope": "category", "scope_value": "abarrotes", "multiplier": 1.1},
        ])
        rompope = _item("ROM-1", 5, 50, category="Abarrotes", family="Licores Navidenos")
        arroz   = _item("ARR-5", 5, 50, category="Abarrotes", family="Granos")
        otro    = _item("OTR-1", 5, 50, category="Abarrotes")
        assert inv_svc._resolve_multiplier(rompope, 1.0, idx) == (4.0, "family")
        assert inv_svc._resolve_multiplier(arroz, 1.0, idx) == (1.4, "family")
        assert inv_svc._resolve_multiplier(otro, 1.0, idx) == (1.1, "category")


class TestBlackFridayIsNotUniform:
    @pytest.mark.offline
    def test_electronics_spike_while_milk_does_not(self):
        """
        Black Friday with a x2.2 catalog multiplier: electronics go to x4,
        milk stays at x1. Without overrides, Faro would over-order milk.
        """
        start = date.today() + timedelta(days=20)
        end = start + timedelta(days=3)   # 4 días
        items = [
            _item("TV-1", 10, 0, category="Electronica"),
            _item("LECHE-1", 10, 0, category="Lacteos"),
        ]
        overrides = [
            {"scope": "category", "scope_value": "electronica", "multiplier": 4.0},
            {"scope": "category", "scope_value": "lacteos", "multiplier": 1.0},
        ]
        with mock.patch.object(inv_svc, "get_inventory_status", return_value=items), \
             mock.patch.object(inv_svc, "get_event_multipliers", return_value=overrides), \
             mock.patch.object(inv_svc, "get_event", return_value={"notes": "BF", "catalog_key": "cr_black_friday:2026"}):
            res = inv_svc.simulate_event_impact(
                "t", "s", start, end, 2.2, event_name="Black Friday", event_id="ev_1",
            )

        by_sku = {r["sku"]: r for r in res["items"]}
        # 10 u/día × 4 días = 40 baseline.
        assert by_sku["TV-1"]["event_units"] == 160.0      # ×4
        assert by_sku["TV-1"]["multiplier"] == 4.0
        assert by_sku["TV-1"]["multiplier_source"] == "category"

        assert by_sku["LECHE-1"]["event_units"] == 40.0    # ×1 — no sube
        assert by_sku["LECHE-1"]["multiplier"] == 1.0
        assert by_sku["LECHE-1"]["extra_units"] == 0.0

        # The event's x2.2 applied to nobody: both had an override.
        assert all(d["source"] == "category" for d in res["multipliers_applied"])

    @pytest.mark.offline
    def test_without_overrides_every_sku_uses_the_event_multiplier(self):
        start = date.today() + timedelta(days=10)
        items = [_item("A", 10, 0), _item("B", 5, 0)]
        with mock.patch.object(inv_svc, "get_inventory_status", return_value=items):
            res = inv_svc.simulate_event_impact("t", "s", start, start, 2.0)
        assert {r["multiplier"] for r in res["items"]} == {2.0}
        assert {r["multiplier_source"] for r in res["items"]} == {"event"}
        assert res["multipliers_applied"] == [
            {"multiplier": 2.0, "source": "event", "skus": 2},
        ]


class TestExplanation:
    @pytest.mark.offline
    def test_catalog_event_is_flagged_as_an_estimate(self):
        ev = {"catalog_key": "cr_black_friday:2026",
              "notes": "Viernes siguiente al cuarto jueves de noviembre."}
        exp = inv_svc.build_multiplier_explanation(ev, 2.2, [])
        assert exp["source"] == "catalog"
        assert exp["es_estimacion"] is True
        assert exp["editable"] is True
        assert exp["base_multiplier"] == 2.2
        assert "cuarto jueves" in exp["reason"]

    @pytest.mark.offline
    def test_user_event_is_not_flagged_as_an_estimate(self):
        ev = {"catalog_key": None, "notes": "Promo interna"}
        exp = inv_svc.build_multiplier_explanation(ev, 1.5, [])
        assert exp["source"] == "user"
        assert exp["es_estimacion"] is False

    @pytest.mark.offline
    def test_explanation_counts_overrides_by_scope(self):
        overrides = [
            {"scope": "sku", "scope_value": "A", "multiplier": 3.0},
            {"scope": "sku", "scope_value": "B", "multiplier": 2.0},
            {"scope": "category", "scope_value": "lacteos", "multiplier": 1.0},
        ]
        exp = inv_svc.build_multiplier_explanation(None, 2.0, overrides)
        assert exp["overrides_activos"] == 3
        assert exp["overrides_por_sku"] == 2
        assert exp["overrides_by_category"] == 1


# ── Endpoints: estado en DB + par de permisos ────────────────────────────────

@pytest.fixture
def saved_event(client, analyst_headers):
    start = (date.today() + timedelta(days=15)).isoformat()
    end = (date.today() + timedelta(days=18)).isoformat()
    r = client.post(
        "/api/v1/inventory/events",
        json={"name": "Black Friday test", "start_date": start,
              "end_date": end, "multiplier": 2.2},
        headers=analyst_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _overrides(tenant_id, event_id):
    return query(
        "SELECT * FROM inventory_event_multipliers "
        "WHERE tenant_id = %s AND event_id = %s",
        (tenant_id, event_id),
    )


class TestMultiplierEndpoints:
    def test_viewer_cannot_set_multiplier_and_db_unchanged(
        self, client, viewer_headers, saved_event, test_tenant
    ):
        before = len(_overrides(test_tenant["id"], saved_event))
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "category", "scope_value": "electronica", "multiplier": 4.0},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert len(_overrides(test_tenant["id"], saved_event)) == before, \
            "a rejected viewer must not have written an override"

    def test_analyst_sets_multiplier_persisted_in_db(
        self, client, analyst_headers, saved_event, test_tenant
    ):
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "category", "scope_value": "Electronica", "multiplier": 4.0},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text

        row = query_one(
            "SELECT scope, scope_value, multiplier FROM inventory_event_multipliers "
            "WHERE tenant_id = %s AND event_id = %s",
            (test_tenant["id"], saved_event),
        )
        assert row["scope"] == "category"
        assert row["scope_value"] == "electronica"  # categorías normalizadas al save
        assert row["multiplier"] == 4.0

    def test_analyst_sets_family_multiplier_persisted_normalized(
        self, client, analyst_headers, saved_event, test_tenant
    ):
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "family", "scope_value": "  Licores Navidenos ", "multiplier": 4.0},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        row = query_one(
            "SELECT scope, scope_value, multiplier FROM inventory_event_multipliers "
            "WHERE tenant_id = %s AND event_id = %s AND scope = 'family'",
            (test_tenant["id"], saved_event),
        )
        assert row["scope_value"] == "licores navidenos"
        assert row["multiplier"] == 4.0

    def test_viewer_cannot_set_family_multiplier_and_db_unchanged(
        self, client, viewer_headers, saved_event, test_tenant
    ):
        before = len(_overrides(test_tenant["id"], saved_event))
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "family", "scope_value": "gaseosas", "multiplier": 3.0},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert len(_overrides(test_tenant["id"], saved_event)) == before

    def test_unknown_scope_is_rejected(
        self, client, analyst_headers, saved_event, test_tenant
    ):
        before = len(_overrides(test_tenant["id"], saved_event))
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "brand", "scope_value": "sony", "multiplier": 2.0},
            headers=analyst_headers,
        )
        assert resp.status_code == 422, resp.text
        assert len(_overrides(test_tenant["id"], saved_event)) == before

    def test_upsert_updates_instead_of_duplicating(
        self, client, analyst_headers, saved_event, test_tenant
    ):
        for mult in (3.0, 4.5):
            resp = client.put(
                f"/api/v1/inventory/events/{saved_event}/multipliers",
                json={"scope": "sku", "scope_value": "TV-1", "multiplier": mult},
                headers=analyst_headers,
            )
            assert resp.status_code == 200, resp.text

        rows = _overrides(test_tenant["id"], saved_event)
        assert len(rows) == 1, "el upsert no debe duplicar filas"
        assert rows[0]["multiplier"] == 4.5

    def test_viewer_cannot_delete_and_row_survives(
        self, client, analyst_headers, viewer_headers, saved_event, test_tenant
    ):
        created = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "sku", "scope_value": "TV-1", "multiplier": 3.0},
            headers=analyst_headers,
        ).json()["data"]

        resp = client.delete(
            f"/api/v1/inventory/events/{saved_event}/multipliers/{created['id']}",
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert query_one(
            "SELECT id FROM inventory_event_multipliers WHERE id = %s", (created["id"],)
        ) is not None, "the viewer must not have been able to delete the override"

    def test_analyst_deletes_and_row_is_gone(
        self, client, analyst_headers, saved_event, test_tenant
    ):
        created = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "sku", "scope_value": "TV-1", "multiplier": 3.0},
            headers=analyst_headers,
        ).json()["data"]

        resp = client.delete(
            f"/api/v1/inventory/events/{saved_event}/multipliers/{created['id']}",
            headers=analyst_headers,
        )
        assert resp.status_code == 204, resp.text
        assert query_one(
            "SELECT id FROM inventory_event_multipliers WHERE id = %s", (created["id"],)
        ) is None

    def test_deleting_event_cascades_to_its_multipliers(
        self, client, analyst_headers, saved_event, test_tenant
    ):
        client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "sku", "scope_value": "TV-1", "multiplier": 3.0},
            headers=analyst_headers,
        )
        assert _overrides(test_tenant["id"], saved_event)

        resp = client.delete(
            f"/api/v1/inventory/events/{saved_event}", headers=analyst_headers,
        )
        assert resp.status_code == 204
        assert _overrides(test_tenant["id"], saved_event) == [], \
            "deleting the event must take its overrides with it (ON DELETE CASCADE)"

    def test_invalid_scope_rejected(self, client, analyst_headers, saved_event):
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "brand", "scope_value": "X", "multiplier": 2.0},
            headers=analyst_headers,
        )
        assert resp.status_code == 422

    def test_multiplier_out_of_range_rejected(self, client, analyst_headers, saved_event):
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "sku", "scope_value": "A", "multiplier": 99.0},
            headers=analyst_headers,
        )
        assert resp.status_code == 422

    def test_unknown_event_404(self, client, analyst_headers):
        resp = client.put(
            "/api/v1/inventory/events/no-existe/multipliers",
            json={"scope": "sku", "scope_value": "A", "multiplier": 2.0},
            headers=analyst_headers,
        )
        assert resp.status_code == 404

    def test_list_returns_what_was_saved(self, client, analyst_headers, saved_event):
        client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "category", "scope_value": "Lacteos", "multiplier": 1.0},
            headers=analyst_headers,
        )
        resp = client.get(
            f"/api/v1/inventory/events/{saved_event}/multipliers", headers=analyst_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        # Categories are lower-cased on save (see
        # test_category_override_is_case_insensitive_on_write).
        assert data[0]["scope_value"] == "lacteos"
        assert data[0]["multiplier"] == 1.0

    def test_category_override_is_case_insensitive_on_write(
        self, client, analyst_headers, saved_event
    ):
        """
        Regression: the unique index is case-sensitive but resolution compares
        lower-cased. Without normalising on write, "Lacteos" and "lacteos"
        create two rows and one is silently lost when simulating. Saving the
        same category in different case must be ONE upsert, not two rows.
        """
        for value, mult in (("Lacteos", 2.0), ("LACTEOS", 1.5), ("lacteos", 1.0)):
            resp = client.put(
                f"/api/v1/inventory/events/{saved_event}/multipliers",
                json={"scope": "category", "scope_value": value, "multiplier": mult},
                headers=analyst_headers,
            )
            assert resp.status_code == 200, resp.text

        rows = query(
            """SELECT scope_value, multiplier FROM inventory_event_multipliers
               WHERE event_id = %s AND scope = 'category'""",
            (saved_event,),
        )
        assert len(rows) == 1, f"esperaba 1 fila tras 3 upserts, hay {len(rows)}: {rows}"
        assert rows[0]["scope_value"] == "lacteos"
        assert float(rows[0]["multiplier"]) == 1.0  # gana el último upsert
