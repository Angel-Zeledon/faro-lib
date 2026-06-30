"""
Offline unit tests for the Decision-Centre additions:
  - Fase 1/2: ROI / adoption line-item logging (roi_service.log_po_generation)
  - Fase 3:   proactive future-peak alerts (service.get_demand_spikes)

All DB access is monkeypatched, so these run without Supabase
(marked `offline` — see conftest.pytest_collection_modifyitems).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.offline


# ── Fase 3: proactive demand-peak detection ──────────────────────────────────

def _curve(start: date, values: list[float]) -> list[dict]:
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "value": v}
        for i, v in enumerate(values, start=1)
    ]


def test_demand_spike_detected_with_future_order_by():
    from backend.inventory import service

    today = date.today()
    # Flat baseline of 10/day with a clear spike of 30 on day 20.
    vals = [10.0] * 25
    vals[19] = 30.0  # index 19 → day 20 ahead
    forecasts = {"SKU-1": {"prophet": {"forecast": _curve(today, vals)}}}
    items = [{
        "sku": "SKU-1", "display_name": "Producto 1", "has_forecast": True,
        "demanda_diaria": 10.0, "lead_time_dias": 15, "proveedor": "Prov", "signal": "OK",
    }]

    alerts = service.get_demand_spikes("t", "s", items=items, forecasts=forecasts)

    assert len(alerts) == 1
    a = alerts[0]
    assert a["sku"] == "SKU-1"
    assert a["uplift_pct"] == 200          # (30-10)/10 = +200%
    assert a["days_until_peak"] == 20
    assert a["already_late"] is False      # order_by = peak - 15d = day 5 ahead
    expected_order_by = (today + timedelta(days=20) - timedelta(days=15)).isoformat()
    assert a["order_by_date"] == expected_order_by


def test_demand_spike_marks_already_late_when_lead_time_exceeds_horizon():
    from backend.inventory import service

    today = date.today()
    vals = [10.0] * 8
    vals[4] = 25.0  # spike on day 5 ahead
    forecasts = {"SKU-2": {"prophet": {"forecast": _curve(today, vals)}}}
    items = [{
        "sku": "SKU-2", "display_name": "Producto 2", "has_forecast": True,
        "demanda_diaria": 10.0, "lead_time_dias": 15, "signal": "OK",
    }]

    alerts = service.get_demand_spikes("t", "s", items=items, forecasts=forecasts)

    assert len(alerts) == 1
    # peak in 5 days but lead time 15 → order-by is in the past → already late
    assert alerts[0]["already_late"] is True


def test_flat_forecast_yields_no_spikes():
    from backend.inventory import service

    today = date.today()
    forecasts = {"SKU-3": {"prophet": {"forecast": _curve(today, [10.0] * 20)}}}
    items = [{
        "sku": "SKU-3", "has_forecast": True, "demanda_diaria": 10.0,
        "lead_time_dias": 15, "signal": "OK",
    }]
    assert service.get_demand_spikes("t", "s", items=items, forecasts=forecasts) == []


def test_tiny_volume_spike_ignored_below_absolute_floor():
    from backend.inventory import service

    today = date.today()
    # +40% relative but only +0.4 units absolute → must be ignored.
    vals = [1.0] * 20
    vals[10] = 1.4
    forecasts = {"SKU-4": {"prophet": {"forecast": _curve(today, vals)}}}
    items = [{
        "sku": "SKU-4", "has_forecast": True, "demanda_diaria": 1.0,
        "lead_time_dias": 15, "signal": "OK",
    }]
    assert service.get_demand_spikes("t", "s", items=items, forecasts=forecasts) == []


def test_avg_forecast_curve_averages_across_models():
    from backend.inventory.service import _avg_forecast_curve

    mf = {
        "m1": {"forecast": [{"date": "2026-01-01", "value": 10.0},
                            {"date": "2026-01-02", "value": 20.0}]},
        "m2": {"forecast": [{"date": "2026-01-01", "value": 30.0},
                            {"date": "2026-01-02", "value": 40.0}]},
    }
    curve = _avg_forecast_curve(mf)
    assert [c["value"] for c in curve] == [20.0, 30.0]   # per-step mean
    assert curve[0]["date"] == "2026-01-01"


# ── Fase 1/2: PO line-item logging + adoption aggregates ─────────────────────

def test_log_po_generation_persists_decisions_and_aggregates(monkeypatch):
    from backend.inventory import roi_service

    captured_header: dict = {}
    line_inserts: list = []

    def fake_query_one(sql, params):
        # The header INSERT ... RETURNING *
        captured_header["sql"] = sql
        captured_header["params"] = params
        return {"id": "po1"}

    def fake_execute(sql, params):
        line_inserts.append(params)

    monkeypatch.setattr(roi_service, "query_one", fake_query_one)
    monkeypatch.setattr(roi_service, "execute", fake_execute)

    items = [
        {"sku": "A", "signal": "PEDIR_YA",     "cantidad_recomendada": 10, "cantidad_final": 10, "costo_unitario": 2, "status": "approved"},
        {"sku": "B", "signal": "PEDIR_PRONTO", "cantidad_recomendada": 5,  "cantidad_final": 8,  "costo_unitario": 3, "status": "modified"},
        {"sku": "C", "signal": "PEDIR_YA",     "cantidad_recomendada": 4,  "cantidad_final": 4,  "costo_unitario": None, "status": "approved"},
        {"sku": "D", "signal": "PEDIR_YA",     "cantidad_recomendada": 6,  "cantidad_final": 0,  "costo_unitario": 1, "status": "rejected"},
    ]

    result = roi_service.log_po_generation("t", "s", items)

    # Header params order:
    # (tenant, session, sku_count, total_units, total_value,
    #  skus_pedir_ya, skus_pedir_pronto,
    #  suggested_count, approved_count, modified_count, rejected_count)
    p = captured_header["params"]
    assert p[2] == 3            # sku_count = approved + modified
    assert p[3] == 22           # total_units = 10 + 8 + 4
    assert p[4] == 44           # total_value = 20 + 24 (C has no cost)
    assert p[5] == 2            # skus_pedir_ya among ordered = A, C
    assert p[6] == 1            # skus_pedir_pronto among ordered = B
    assert p[7] == 4            # suggested_count = all lines
    assert p[8] == 3            # approved_count (approved + modified)
    assert p[9] == 1            # modified_count
    assert p[10] == 1           # rejected_count

    # Every line is persisted, including the rejected one (for adoption audit).
    assert len(line_inserts) == 4
    assert result["id"] == "po1"


def test_log_po_generation_legacy_items_default_to_approved(monkeypatch):
    """Server-side CSV export sends status-less items → treated as ordered."""
    from backend.inventory import roi_service

    header: dict = {}
    monkeypatch.setattr(roi_service, "query_one",
                        lambda sql, params: header.update(params=params) or {"id": "po2"})
    monkeypatch.setattr(roi_service, "execute", lambda sql, params: None)

    items = [
        {"sku": "A", "signal": "PEDIR_YA", "cantidad_recomendada": 5, "costo_unitario": 2},
    ]
    roi_service.log_po_generation("t", "s", items)

    p = header["params"]
    assert p[2] == 1     # counted as ordered
    assert p[3] == 5.0   # falls back to cantidad_recomendada when no cantidad_final
    assert p[8] == 1     # approved_count
    assert p[10] == 0    # rejected_count
