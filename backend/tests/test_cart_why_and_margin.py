"""
Tests for the /hoy cart features 2.4 ("el por qué de cada recomendación") and
2.10 (margen visible en el carrito).

2.4 — every number behind a cart line (stock, demanda diaria, cobertura, lead
time REALMENTE usado y punto de reorden) plus a plain-Spanish sentence must come
from the backend, and the lead time must prefer the one LEARNED from the
supplier's real receptions over the one configured on the SKU card.

2.10 — margen_unitario per SKU, None (never 0) when precio_venta or
costo_unitario is missing, so the cart can report those SKUs as excluded.
"""

import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute, query_one
from backend.inventory.service import (
    build_explicacion,
    calc_margen_unitario,
    get_learned_lead_times,
    resolve_lead_time,
)


# ── Fixture override: @pytest.local fails email-validator — use @example.com ──

@pytest.fixture
def registered_user(test_tenant):
    from backend.users import service as user_svc
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"], email=email, password=password,
        role="admin", full_name="Test Admin",
    )
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def auth_headers(client, registered_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": registered_user["email"], "password": registered_user["password"],
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _ok(resp, code=200):
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"
    return resp.json()["data"]


def _sku():
    return f"SKU-{uuid.uuid4().hex[:8].upper()}"


def _flat_forecast(daily: float, spread: float, days: int = 30) -> dict:
    """One model, constant daily demand — makes every derived number hand-checkable."""
    return {"lightgbm": {"forecast": [
        {
            "date": (datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat(),
            "value": daily,
            "lower": max(0.0, daily - spread),
            "upper": daily + spread,
        }
        for i in range(days)
    ]}}


def _learn_lead_time(client, headers, *, sku: str, proveedor: str, days_ago: int) -> str:
    """
    Makes Faro LEARN a real lead time for `proveedor` by walking the actual
    product flow: log a PO, backdate it, then record its reception today.
    Returns the po_log_id.
    """
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": [{
            "sku": sku, "display_name": f"Prod {sku}", "proveedor": proveedor,
            "signal": "PEDIR_YA", "cantidad_recomendada": 10,
            "cantidad_final": 10, "costo_unitario": 1.0, "status": "approved",
        }]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    po_id = resp.json()["data"]["id"]

    execute(
        "UPDATE inventory_po_log SET generated_at = %s WHERE id = %s",
        (datetime.now(timezone.utc) - timedelta(days=days_ago), po_id),
    )
    recv = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=headers)
    assert recv.status_code == 200, recv.text
    return po_id


# ── 2.4 — unit tests, hand-verifiable numbers ────────────────────────────────

class TestResolveLeadTime:
    def test_prefers_learned_over_configured(self):
        lt, origen, learned = resolve_lead_time(7, "Acme", {"acme": 11.6})
        assert (lt, origen, learned) == (12, "aprendido", 11.6)  # 11.6 rounds to 12

    def test_falls_back_to_configured_when_supplier_never_delivered(self):
        assert resolve_lead_time(7, "Acme", {}) == (7, "configurado", None)

    def test_supplier_name_matched_case_insensitively(self):
        lt, origen, _ = resolve_lead_time(7, "  ACME  ", {"acme": 9.0})
        assert (lt, origen) == (9, "aprendido")

    def test_no_supplier_on_sku_uses_configured(self):
        assert resolve_lead_time(15, None, {"acme": 9.0}) == (15, "configurado", None)

    def test_learned_never_rounds_down_to_zero(self):
        """A 0.2-day observation must not produce a 0-day lead time (division traps)."""
        lt, origen, _ = resolve_lead_time(7, "Acme", {"acme": 0.2})
        assert lt == 1 and origen == "aprendido"


class TestMargenUnitario:
    def test_price_minus_cost(self):
        assert calc_margen_unitario(5.90, 3.50) == 2.40

    def test_none_when_price_missing(self):
        assert calc_margen_unitario(None, 3.50) is None

    def test_none_when_cost_missing(self):
        assert calc_margen_unitario(5.90, None) is None

    def test_zero_margin_is_zero_not_none(self):
        """0 ('vendo al costo') must stay distinguishable from 'no sé el margen'."""
        assert calc_margen_unitario(3.50, 3.50) == 0.0

    def test_negative_margin_is_reported_not_clamped(self):
        assert calc_margen_unitario(3.00, 3.50) == -0.50


class TestExplicacion:
    def test_mentions_every_business_number_and_learned_origin(self):
        text = build_explicacion(
            stock_actual=120, demanda_diaria=8.0, dias_cobertura=15.0,
            lead_time=12, lead_time_origen="aprendido", punto_reorden=110.0,
            signal="PEDIR_PRONTO",
        )
        assert "120" in text and "8.0" in text and "15 días" in text
        assert "12 días" in text and "110" in text
        assert "aprendido de sus entregas reales" in text
        assert "esta semana" in text

    def test_configured_lead_time_is_labelled_as_such(self):
        text = build_explicacion(
            stock_actual=10, demanda_diaria=5.0, dias_cobertura=2.0,
            lead_time=7, lead_time_origen="configurado", punto_reorden=40.0,
            signal="PEDIR_YA",
        )
        assert "lead time configurado" in text
        assert "urgente" in text

    def test_no_ml_vocabulary_leaks_into_the_sentence(self):
        text = build_explicacion(
            stock_actual=10, demanda_diaria=5.0, dias_cobertura=2.0,
            lead_time=7, lead_time_origen="configurado", punto_reorden=40.0,
            signal="PEDIR_YA",
        ).lower()
        for jargon in ("forecast", "modelo", "lightgbm", "safety stock", "z-score", "wape"):
            assert jargon not in text, f"jerga técnica filtrada: {jargon}"

    def test_zero_demand_does_not_claim_a_coverage_window(self):
        text = build_explicacion(
            stock_actual=50, demanda_diaria=0.0, dias_cobertura=None,
            lead_time=7, lead_time_origen="configurado", punto_reorden=0.0,
            signal="SOBRESTOCK",
        )
        assert "no proyecta ventas" in text
        assert "alcanza para" not in text


# ── 2.4 — integration: the status endpoint really carries these fields ───────

class TestStatusExposesWhyFields:
    def test_learned_lead_time_replaces_configured_one_in_the_recommendation(
        self, client, auth_headers, test_tenant,
    ):
        """
        The supplier's card says 5 days but their real deliveries took ~12.
        The recommendation must be built on 12 — and say so.
        """
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        prov = f"Lento-{uuid.uuid4().hex[:6]}"
        sku = _sku()

        # The reception below credits its own units back into stock, so it runs
        # against a throwaway SKU — the SKU under test keeps the stock we set.
        _learn_lead_time(client, auth_headers, sku=_sku(), proveedor=prov, days_ago=12)
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"stock_actual": 20, "lead_time_dias": 5, "moq": 1,
                  "proveedor": prov, "costo_unitario": 3.5, "precio_venta": 5.9},
            headers=auth_headers,
        )

        # The observation was really persisted (state assertion, not an echo).
        obs = query_one(
            """SELECT COUNT(*)::int AS n, AVG(lead_time_days) AS avg_days
               FROM supplier_lead_time_obs
               WHERE tenant_id = %s AND LOWER(proveedor) = LOWER(%s)""",
            (tid, prov),
        )
        assert obs["n"] == 1
        assert 11.5 <= float(obs["avg_days"]) <= 12.5
        assert round(get_learned_lead_times(tid)[prov.lower()]) == 12

        session_id = create_session(tid, "usr_test", "why-test")["id"]
        session_store.set_forecasts(tid, session_id, {sku: _flat_forecast(10.0, 4.0)})

        item = next(
            i for i in _ok(client.get(
                f"/api/v1/inventory/status?session_id={session_id}", headers=auth_headers,
            ))["items"] if i["sku"] == sku
        )

        assert item["lead_time_origen"] == "aprendido"
        assert item["lead_time_configurado"] == 5
        assert item["lead_time_dias"] == 12          # NOT the configured 5
        assert 11.5 <= item["lead_time_aprendido"] <= 12.5

        # Hand-checkable: demanda 10/day, lead 12 -> 120 units of lead-time demand;
        # safety = z(0.95)=1.645 * std(4.0) * sqrt(12); reorder point is their sum.
        assert item["demanda_diaria"] == 10.0
        assert item["demanda_lead_time"] == 120.0
        expected_safety = 1.645 * 4.0 * math.sqrt(12)
        assert item["punto_reorden"] == pytest.approx(120.0 + expected_safety, abs=0.05)

        # Coverage: 20 units / 10 per day = 2 days -> below 12*0.5 -> PEDIR_YA.
        assert item["dias_cobertura"] == 2.0
        assert item["signal"] == "PEDIR_YA"

        assert "12 días" in item["explicacion"]
        assert "aprendido de sus entregas reales" in item["explicacion"]
        assert "20" in item["explicacion"]

    def test_without_receptions_the_configured_lead_time_is_used_and_labelled(
        self, client, auth_headers, test_tenant,
    ):
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"stock_actual": 20, "lead_time_dias": 8, "moq": 1,
                  "proveedor": f"Nuevo-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        session_id = create_session(tid, "usr_test", "why-test-2")["id"]
        session_store.set_forecasts(tid, session_id, {sku: _flat_forecast(10.0, 0.0)})

        item = next(
            i for i in _ok(client.get(
                f"/api/v1/inventory/status?session_id={session_id}", headers=auth_headers,
            ))["items"] if i["sku"] == sku
        )
        assert item["lead_time_origen"] == "configurado"
        assert item["lead_time_dias"] == 8
        assert item["lead_time_aprendido"] is None
        # spread 0 -> no safety stock -> reorder point is pure lead-time demand.
        assert item["punto_reorden"] == 80.0
        assert "lead time configurado" in item["explicacion"]

    def test_briefing_cart_lines_carry_the_same_why_fields(
        self, client, auth_headers, test_tenant,
    ):
        """The /hoy cart is built from morning-briefing risks — not /status."""
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"stock_actual": 5, "lead_time_dias": 10, "moq": 1,
                  "costo_unitario": 3.5, "precio_venta": 5.9},
            headers=auth_headers,
        )
        session_id = create_session(tid, "usr_test", "briefing-why")["id"]
        session_store.set_forecasts(tid, session_id, {sku: _flat_forecast(10.0, 1.0)})

        data = _ok(client.get(
            f"/api/v1/inventory/morning-briefing?session_id={session_id}", headers=auth_headers,
        ))
        line = next(r for r in data["risks"] if r["sku"] == sku)
        for field in ("explicacion", "punto_reorden", "lead_time_origen",
                      "demanda_diaria", "stock_actual", "dias_cobertura", "margen_unitario"):
            assert field in line, f"falta {field} en la línea del carrito"
        assert line["explicacion"]
        assert line["margen_unitario"] == 2.40


# ── 2.10 — integration: margin data reaches the cart, gaps are visible ──────

class TestMargenEnCarrito:
    def test_margin_present_and_null_when_price_or_cost_missing(
        self, client, auth_headers, test_tenant,
    ):
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        sku_full, sku_no_price, sku_no_cost = _sku(), _sku(), _sku()

        client.put(f"/api/v1/inventory/stock/{sku_full}",
                   json={"stock_actual": 5, "lead_time_dias": 10,
                         "costo_unitario": 3.5, "precio_venta": 5.9},
                   headers=auth_headers)
        client.put(f"/api/v1/inventory/stock/{sku_no_price}",
                   json={"stock_actual": 5, "lead_time_dias": 10, "costo_unitario": 3.5},
                   headers=auth_headers)
        client.put(f"/api/v1/inventory/stock/{sku_no_cost}",
                   json={"stock_actual": 5, "lead_time_dias": 10, "precio_venta": 5.9},
                   headers=auth_headers)

        session_id = create_session(tid, "usr_test", "margin-test")["id"]
        session_store.set_forecasts(tid, session_id, {
            s: _flat_forecast(10.0, 1.0) for s in (sku_full, sku_no_price, sku_no_cost)
        })

        items = {i["sku"]: i for i in _ok(client.get(
            f"/api/v1/inventory/status?session_id={session_id}", headers=auth_headers,
        ))["items"]}

        assert items[sku_full]["margen_unitario"] == 2.40
        assert items[sku_full]["precio_venta"] == 5.9
        # Missing data must be None so the cart can COUNT these as excluded
        # instead of quietly adding a 0-margin line to the total.
        assert items[sku_no_price]["margen_unitario"] is None
        assert items[sku_no_cost]["margen_unitario"] is None


# ── Permission pair on the mutating endpoint the cart's margin depends on ────

class TestPricingPermissionPair:
    def test_viewer_cannot_set_prices_and_db_is_unchanged(
        self, client, auth_headers, viewer_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"stock_actual": 5, "lead_time_dias": 10,
                         "costo_unitario": 3.5, "precio_venta": 5.9},
                   headers=auth_headers)

        resp = client.patch(f"/api/v1/inventory/stock/{sku}",
                            json={"precio_venta": 99.0}, headers=viewer_headers)
        assert resp.status_code == 403

        row = query_one(
            "SELECT precio_venta FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert float(row["precio_venta"]) == 5.9  # untouched
        assert calc_margen_unitario(float(row["precio_venta"]), 3.5) == 2.40

    def test_analyst_can_set_prices_and_margin_follows_in_the_db(
        self, client, analyst_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"stock_actual": 5, "lead_time_dias": 10,
                         "costo_unitario": 3.5, "precio_venta": 5.9},
                   headers=analyst_headers)

        resp = client.patch(f"/api/v1/inventory/stock/{sku}",
                            json={"precio_venta": 7.0}, headers=analyst_headers)
        assert resp.status_code == 200, resp.text

        row = query_one(
            "SELECT precio_venta, costo_unitario FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert float(row["precio_venta"]) == 7.0
        assert calc_margen_unitario(
            float(row["precio_venta"]), float(row["costo_unitario"]),
        ) == 3.50
