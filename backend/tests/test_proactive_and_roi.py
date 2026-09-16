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
        "daily_demand": 10.0, "lead_time_days": 15, "supplier": "Prov", "signal": "OK",
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
        "daily_demand": 10.0, "lead_time_days": 15, "signal": "OK",
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
        "sku": "SKU-3", "has_forecast": True, "daily_demand": 10.0,
        "lead_time_days": 15, "signal": "OK",
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
        "sku": "SKU-4", "has_forecast": True, "daily_demand": 1.0,
        "lead_time_days": 15, "signal": "OK",
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

def _header_values(sql: str, params: tuple) -> dict:
    """Map the header INSERT's column names to the values bound to them.

    These assertions used to index `params` positionally (`p[8] == 3`), which
    silently shifts the moment a column is added to the INSERT — every later
    assertion then checks a different field than its comment claims, and the
    test either fails for an unrelated reason or, worse, passes. Reading the
    column list out of the SQL keeps each assertion attached to its field.
    """
    cols = sql.split("(", 1)[1].split(")", 1)[0]
    names = [c.strip() for c in cols.split(",")]
    return dict(zip(names, params))


def _no_db_transaction(monkeypatch, roi_service):
    """The header and its lines now commit as one unit (estabilidad 11.33), so
    `log_po_generation` opens a transaction and threads its connection through
    every write. These tests are offline: there is no connection to open, and
    the sentinel is only there to be passed around."""
    from contextlib import contextmanager

    @contextmanager
    def _fake_transaction():
        yield "offline-conn"

    monkeypatch.setattr(roi_service, "transaction", _fake_transaction)


def test_log_po_generation_persists_decisions_and_aggregates(monkeypatch):
    from backend.inventory import roi_service

    captured_header: dict = {}
    line_inserts: list = []

    def fake_query_one(sql, params, **kwargs):
        # The header INSERT ... RETURNING *
        captured_header["sql"] = sql
        captured_header["params"] = params
        return {"id": "po1"}

    def fake_execute(sql, params, **kwargs):
        line_inserts.append(params)

    monkeypatch.setattr(roi_service, "query_one", fake_query_one)
    monkeypatch.setattr(roi_service, "execute", fake_execute)
    _no_db_transaction(monkeypatch, roi_service)

    items = [
        {"sku": "A", "signal": "PEDIR_YA",     "recommended_qty": 10, "final_qty": 10, "unit_cost": 2, "status": "approved"},
        {"sku": "B", "signal": "PEDIR_PRONTO", "recommended_qty": 5,  "final_qty": 8,  "unit_cost": 3, "status": "modified"},
        {"sku": "C", "signal": "PEDIR_YA",     "recommended_qty": 4,  "final_qty": 4,  "unit_cost": None, "status": "approved"},
        {"sku": "D", "signal": "PEDIR_YA",     "recommended_qty": 6,  "final_qty": 0,  "unit_cost": 1, "status": "rejected"},
    ]

    result = roi_service.log_po_generation("t", "s", items)

    v = _header_values(captured_header["sql"], captured_header["params"])
    assert v["sku_count"] == 3            # approved + modified
    assert v["total_units"] == 22         # 10 + 8 + 4
    assert v["total_value"] == 44         # 20 + 24 (C has no cost)
    assert v["skus_order_now"] == 2       # among ordered = A, C
    assert v["skus_order_soon"] == 1      # among ordered = B
    assert v["suggested_count"] == 4      # all lines
    assert v["approved_count"] == 3       # approved + modified
    assert v["modified_count"] == 1
    assert v["rejected_count"] == 1
    assert v["source"] == "forecast"      # the buyer decided line by line

    # Every line is persisted, including the rejected one (for adoption audit).
    assert len(line_inserts) == 4
    assert result["id"] == "po1"


def test_log_po_generation_legacy_items_default_to_approved(monkeypatch):
    """Server-side CSV export sends status-less items → treated as ordered."""
    from backend.inventory import roi_service

    header: dict = {}
    monkeypatch.setattr(
        roi_service, "query_one",
        lambda sql, params, **kw: header.update(sql=sql, params=params) or {"id": "po2"})
    monkeypatch.setattr(roi_service, "execute", lambda sql, params, **kw: None)
    _no_db_transaction(monkeypatch, roi_service)

    items = [
        {"sku": "A", "signal": "PEDIR_YA", "recommended_qty": 5, "unit_cost": 2},
    ]
    roi_service.log_po_generation("t", "s", items)

    v = _header_values(header["sql"], header["params"])
    assert v["sku_count"] == 1           # counted as ordered
    assert v["total_units"] == 5.0       # falls back to recommended_qty
    assert v["approved_count"] == 1
    assert v["rejected_count"] == 0


def test_an_export_with_no_decisions_records_the_order_but_not_an_adoption_reading(
    monkeypatch,
):
    """
    The download path. The caller sent no per-line decisions, so the endpoint
    re-derived every actionable line and the normalizer defaults them all to
    'approved'. Counting that as "the buyer followed every recommendation" gave
    a tenant working from /inventario a permanent green 100% — with 'rejected'
    unreachable by construction — and every urgent line also counted as a risk
    acted on. Pressing Export three times tripled the month.

    The ORDER is still recorded in full: they downloaded it and will act on it.
    Only the four DECISION counters are withheld, exactly as create_manual_po
    already withholds them for orders written from scratch.
    """
    from backend.inventory import roi_service

    header: dict = {}
    monkeypatch.setattr(
        roi_service, "query_one",
        lambda sql, params, **kw: header.update(sql=sql, params=params) or {"id": "po3"})
    lines: list = []
    monkeypatch.setattr(roi_service, "execute",
                        lambda sql, params, **kw: lines.append(params))
    _no_db_transaction(monkeypatch, roi_service)

    items = [
        {"sku": "A", "signal": "PEDIR_YA",     "recommended_qty": 5, "unit_cost": 2},
        {"sku": "B", "signal": "PEDIR_PRONTO", "recommended_qty": 3, "unit_cost": 4},
    ]
    roi_service.log_po_generation("t", "s", items, decisions_recorded=False)

    v = _header_values(header["sql"], header["params"])
    # The order is real and complete.
    assert v["sku_count"] == 2
    assert v["total_units"] == 8.0
    assert v["total_value"] == 22.0
    assert v["skus_order_now"] == 1
    assert v["source"] == "export"
    # The adoption reading is not.
    assert v["suggested_count"] == 0
    assert v["approved_count"] == 0
    assert v["modified_count"] == 0
    assert v["rejected_count"] == 0
    # Lines are still persisted so the order can be received and audited.
    assert len(lines) == 2


def test_normalize_decisions_downgrades_zero_qty_orders():
    from backend.inventory.roi_service import _normalize_decisions
    out = _normalize_decisions([
        {"sku": "A", "status": "approved", "final_qty": 0},
        {"sku": "B", "status": "modified", "final_qty": 12},
        {"sku": "C", "status": "approved", "final_qty": 5},
        {"sku": "D", "status": "rejected", "final_qty": 0},
    ])
    by_sku = {i["sku"]: i for i in out}
    # A ordered 0 units -> not a real order
    assert by_sku["A"]["status"] == "rejected"
    # B and C keep their ordering status
    assert by_sku["B"]["status"] == "modified"
    assert by_sku["C"]["status"] == "approved"
    # D was already rejected
    assert by_sku["D"]["status"] == "rejected"
