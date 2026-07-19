"""
Multiplicadores por producto y explicación del multiplicador (feature 3.4).

Un multiplicador único por evento es falso en la práctica: en Black Friday la
electrónica se dispara y la leche no se mueve. Estas pruebas fijan:
  * la resolución sku > categoria > evento,
  * que cada fila simulada reporte QUÉ multiplicador se aplicó y POR QUÉ,
  * la explicación del multiplicador (estimación del catálogo vs. del usuario),
  * y el par de permisos en los endpoints que mutan.
"""
from datetime import date, timedelta
from unittest import mock

import pytest

from backend.db.connection import query, query_one
from backend.inventory import service as inv_svc


def _item(sku, daily, stock, categoria=None, lead_time=10, moq=1, cost=2.0):
    return {
        "sku": sku, "display_name": f"Prod {sku}", "proveedor": "Prov A",
        "categoria": categoria,
        "demanda_diaria": daily, "stock_actual": stock,
        "lead_time_dias": lead_time, "moq": moq, "costo_unitario": cost,
    }


# ── Resolución pura (sin DB) ─────────────────────────────────────────────────

class TestResolution:
    @pytest.mark.offline
    def test_sku_override_wins_over_category_and_event(self):
        idx = inv_svc._index_overrides([
            {"scope": "sku", "scope_value": "TV-1", "multiplier": 4.0},
            {"scope": "categoria", "scope_value": "electronica", "multiplier": 2.5},
        ])
        item = _item("TV-1", 10, 100, categoria="Electronica")
        assert inv_svc._resolve_multiplier(item, 1.5, idx) == (4.0, "sku")

    @pytest.mark.offline
    def test_category_override_used_when_no_sku_override(self):
        idx = inv_svc._index_overrides([
            {"scope": "categoria", "scope_value": "electronica", "multiplier": 2.5},
        ])
        item = _item("TV-9", 10, 100, categoria="Electronica")
        assert inv_svc._resolve_multiplier(item, 1.5, idx) == (2.5, "categoria")

    @pytest.mark.offline
    def test_category_match_is_case_insensitive(self):
        idx = inv_svc._index_overrides([
            {"scope": "categoria", "scope_value": "  ELECTRONICA ", "multiplier": 3.0},
        ])
        item = _item("TV-9", 10, 100, categoria="electronica")
        assert inv_svc._resolve_multiplier(item, 1.0, idx) == (3.0, "categoria")

    @pytest.mark.offline
    def test_falls_back_to_event_multiplier(self):
        idx = inv_svc._index_overrides([])
        item = _item("LECHE-1", 10, 100, categoria="Lacteos")
        assert inv_svc._resolve_multiplier(item, 1.5, idx) == (1.5, "evento")

    @pytest.mark.offline
    def test_item_without_category_falls_back(self):
        idx = inv_svc._index_overrides([
            {"scope": "categoria", "scope_value": "electronica", "multiplier": 3.0},
        ])
        item = _item("X-1", 10, 100, categoria=None)
        assert inv_svc._resolve_multiplier(item, 1.2, idx) == (1.2, "evento")


# ── El caso que motiva la feature ────────────────────────────────────────────

class TestBlackFridayIsNotUniform:
    @pytest.mark.offline
    def test_electronics_spike_while_milk_does_not(self):
        """
        Black Friday con ×2.2 de catálogo: la electrónica sube a ×4, la leche
        se queda en ×1. Sin overrides, Faro pediría leche de más.
        """
        start = date.today() + timedelta(days=20)
        end = start + timedelta(days=3)   # 4 días
        items = [
            _item("TV-1", 10, 0, categoria="Electronica"),
            _item("LECHE-1", 10, 0, categoria="Lacteos"),
        ]
        overrides = [
            {"scope": "categoria", "scope_value": "electronica", "multiplier": 4.0},
            {"scope": "categoria", "scope_value": "lacteos", "multiplier": 1.0},
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
        assert by_sku["TV-1"]["multiplicador"] == 4.0
        assert by_sku["TV-1"]["multiplicador_origen"] == "categoria"

        assert by_sku["LECHE-1"]["event_units"] == 40.0    # ×1 — no sube
        assert by_sku["LECHE-1"]["multiplicador"] == 1.0
        assert by_sku["LECHE-1"]["extra_units"] == 0.0

        # El ×2.2 del evento no se aplicó a nadie: ambos tenían override.
        assert all(d["origen"] == "categoria" for d in res["multiplicadores_aplicados"])

    @pytest.mark.offline
    def test_without_overrides_every_sku_uses_the_event_multiplier(self):
        start = date.today() + timedelta(days=10)
        items = [_item("A", 10, 0), _item("B", 5, 0)]
        with mock.patch.object(inv_svc, "get_inventory_status", return_value=items):
            res = inv_svc.simulate_event_impact("t", "s", start, start, 2.0)
        assert {r["multiplicador"] for r in res["items"]} == {2.0}
        assert {r["multiplicador_origen"] for r in res["items"]} == {"evento"}
        assert res["multiplicadores_aplicados"] == [
            {"multiplicador": 2.0, "origen": "evento", "skus": 2},
        ]


class TestExplanation:
    @pytest.mark.offline
    def test_catalog_event_is_flagged_as_an_estimate(self):
        ev = {"catalog_key": "cr_black_friday:2026",
              "notes": "Viernes siguiente al cuarto jueves de noviembre."}
        exp = inv_svc.build_multiplier_explanation(ev, 2.2, [])
        assert exp["origen"] == "catalogo"
        assert exp["es_estimacion"] is True
        assert exp["editable"] is True
        assert exp["multiplicador_base"] == 2.2
        assert "cuarto jueves" in exp["motivo"]

    @pytest.mark.offline
    def test_user_event_is_not_flagged_as_an_estimate(self):
        ev = {"catalog_key": None, "notes": "Promo interna"}
        exp = inv_svc.build_multiplier_explanation(ev, 1.5, [])
        assert exp["origen"] == "usuario"
        assert exp["es_estimacion"] is False

    @pytest.mark.offline
    def test_explanation_counts_overrides_by_scope(self):
        overrides = [
            {"scope": "sku", "scope_value": "A", "multiplier": 3.0},
            {"scope": "sku", "scope_value": "B", "multiplier": 2.0},
            {"scope": "categoria", "scope_value": "lacteos", "multiplier": 1.0},
        ]
        exp = inv_svc.build_multiplier_explanation(None, 2.0, overrides)
        assert exp["overrides_activos"] == 3
        assert exp["overrides_por_sku"] == 2
        assert exp["overrides_por_categoria"] == 1


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
            json={"scope": "categoria", "scope_value": "electronica", "multiplier": 4.0},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert len(_overrides(test_tenant["id"], saved_event)) == before, \
            "un viewer rechazado no debe haber escrito un override"

    def test_analyst_sets_multiplier_persisted_in_db(
        self, client, analyst_headers, saved_event, test_tenant
    ):
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "categoria", "scope_value": "Electronica", "multiplier": 4.0},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text

        row = query_one(
            "SELECT scope, scope_value, multiplier FROM inventory_event_multipliers "
            "WHERE tenant_id = %s AND event_id = %s",
            (test_tenant["id"], saved_event),
        )
        assert row["scope"] == "categoria"
        assert row["scope_value"] == "electronica"  # categorías normalizadas al guardar
        assert row["multiplier"] == 4.0

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
        ) is not None, "el viewer no debió poder borrar el override"

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
            "borrar el evento debe arrastrar sus overrides (ON DELETE CASCADE)"

    def test_invalid_scope_rejected(self, client, analyst_headers, saved_event):
        resp = client.put(
            f"/api/v1/inventory/events/{saved_event}/multipliers",
            json={"scope": "marca", "scope_value": "X", "multiplier": 2.0},
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
            json={"scope": "categoria", "scope_value": "Lacteos", "multiplier": 1.0},
            headers=analyst_headers,
        )
        resp = client.get(
            f"/api/v1/inventory/events/{saved_event}/multipliers", headers=analyst_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        # Las categorías se normalizan a minúscula al guardar (ver
        # test_category_override_is_case_insensitive_on_write).
        assert data[0]["scope_value"] == "lacteos"
        assert data[0]["multiplier"] == 1.0

    def test_category_override_is_case_insensitive_on_write(
        self, client, analyst_headers, saved_event
    ):
        """
        Regresión: el índice único es case-sensitive pero la resolución compara
        en minúscula. Sin normalizar al escribir, "Lacteos" y "lacteos" crean
        dos filas y una se pierde en silencio al simular. Guardar la misma
        categoría con distinta caja debe ser UN upsert, no dos filas.
        """
        for value, mult in (("Lacteos", 2.0), ("LACTEOS", 1.5), ("lacteos", 1.0)):
            resp = client.put(
                f"/api/v1/inventory/events/{saved_event}/multipliers",
                json={"scope": "categoria", "scope_value": value, "multiplier": mult},
                headers=analyst_headers,
            )
            assert resp.status_code == 200, resp.text

        rows = query(
            """SELECT scope_value, multiplier FROM inventory_event_multipliers
               WHERE event_id = %s AND scope = 'categoria'""",
            (saved_event,),
        )
        assert len(rows) == 1, f"esperaba 1 fila tras 3 upserts, hay {len(rows)}: {rows}"
        assert rows[0]["scope_value"] == "lacteos"
        assert float(rows[0]["multiplier"]) == 1.0  # gana el último upsert
