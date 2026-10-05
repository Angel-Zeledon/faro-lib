"""Money at risk: pure math and ordering (no DB needed)."""
import math

from backend.inventory.money_at_risk import (
    money_at_risk, sort_by_money_at_risk, unit_value,
)


def test_basic_amount_uses_price():
    # 10/day, lead 14, cover 4 -> 10 days exposed x 10 x 5.0
    assert money_at_risk(10, 14, 4, 5.0, 2.0) == (500.0, "price")


def test_falls_back_to_cost_then_unknown():
    assert money_at_risk(10, 14, 4, None, 2.0) == (200.0, "cost")
    assert money_at_risk(10, 14, 4, 0, 2.0) == (200.0, "cost")  # 0 price = not entered
    assert money_at_risk(10, 14, 4, None, None) == (None, "unknown")
    assert money_at_risk(10, 14, 4, 0, 0) == (None, "unknown")
    assert unit_value("x", float("nan")) == (None, "unknown")


def test_coverage_at_or_above_lead_time_is_zero_not_none():
    assert money_at_risk(10, 14, 14, 5.0) == (0.0, "price")
    assert money_at_risk(10, 14, 30, 5.0) == (0.0, "price")


def test_zero_or_negative_demand_is_zero():
    assert money_at_risk(0, 14, 2, 5.0) == (0.0, "price")
    assert money_at_risk(-3, 14, 2, 5.0) == (0.0, "price")


def test_missing_demand_or_coverage_is_unknown_amount_not_zero():
    assert money_at_risk(None, 14, 2, 5.0) == (None, "price")
    assert money_at_risk(10, 14, None, 5.0) == (None, "price")


def test_stockout_counts_lead_time_without_history():
    assert money_at_risk(10, 14, 0, 5.0, in_stockout=True) == (700.0, "price")


def test_stockout_uses_days_since_only_when_present():
    assert money_at_risk(10, 14, 0, 5.0, in_stockout=True,
                         periods_since_stockout=3) == (150.0, "price")
    assert money_at_risk(10, 14, 0, 5.0, in_stockout=True,
                         periods_since_stockout=None) == (700.0, "price")


def test_negative_stock_coverage_does_not_inflate_exposure():
    # Negative coverage floors at 0: exposure is the lead time, never more.
    assert money_at_risk(10, 14, -50, 5.0) == (700.0, "price")


def test_huge_values_stay_finite_or_unknown():
    amount, basis = money_at_risk(1e12, 365, 0, 1e9)
    assert basis == "price" and amount is not None and math.isfinite(amount)
    assert money_at_risk(1e308, 365, 0, 1e308) == (None, "price")  # overflow
    assert money_at_risk(10, 14, 4, float("inf")) == (None, "unknown")


def _row(sku, amount):
    return {"sku": sku, "money_at_risk": amount}


def test_sort_valued_first_descending_unknown_last():
    rows = [_row("a", None), _row("b", 10.0), _row("c", 500.0), _row("d", 0.0)]
    assert [r["sku"] for r in sort_by_money_at_risk(rows)] == ["c", "b", "d", "a"]


def test_sort_is_stable_for_ties_and_for_tenants_with_no_values():
    rows = [_row("x", None), _row("y", None), _row("z", None)]
    assert [r["sku"] for r in sort_by_money_at_risk(rows)] == ["x", "y", "z"]
    ties = [_row("p", 5.0), _row("q", 5.0), _row("r", 5.0)]
    assert [r["sku"] for r in sort_by_money_at_risk(ties)] == ["p", "q", "r"]
    assert [r["sku"] for r in sort_by_money_at_risk(iter(rows))] == ["x", "y", "z"]


# ── Briefing wiring (needs the DB fixtures; written, run centrally) ──────────

def _flat_forecast(per_day):
    return {"lightgbm": {"forecast": [
        {"date": f"2026-01-{i + 1:02d}", "value": per_day, "lower": per_day, "upper": per_day}
        for i in range(30)]}}


def test_briefing_ranks_pedir_ya_by_money_and_keeps_unvalued_last(client, auth_headers, test_tenant):
    from backend.db import session_store
    from backend.inventory import service as inv_svc
    from backend.sessions.service import create_session
    tid = test_tenant["id"]
    sid = create_session(tid, "usr_test", "mar-briefing")["id"]
    # Same demand, same lead time, same stock-out: only the unit value differs.
    stocks = {"MAR-NOVAL": None, "MAR-CHEAP": 1.0, "MAR-DEAR": 50.0}
    forecasts = {}
    for sku, price in stocks.items():
        body = {"current_stock": 0, "lead_time_days": 10, "moq": 1}
        if price is not None:
            body["sale_price"] = price
        r = client.put(f"/api/v1/inventory/stock/{sku}", json=body, headers=auth_headers)
        assert r.status_code == 200, r.text
        forecasts[sku] = _flat_forecast(10.0)
    session_store.set_forecasts(tid, sid, forecasts)

    b = inv_svc.get_morning_briefing(tid, sid)
    rows = {r["sku"]: r for r in b["risks"]}
    assert set(rows) >= set(stocks)
    assert rows["MAR-DEAR"]["money_at_risk"] == 10 * 10 * 50.0
    assert rows["MAR-DEAR"]["money_at_risk_basis"] == "price"
    assert rows["MAR-NOVAL"]["money_at_risk"] is None
    assert rows["MAR-NOVAL"]["money_at_risk_basis"] == "unknown"
    order = [r["sku"] for r in b["risks"] if r["sku"] in stocks]
    assert order == ["MAR-DEAR", "MAR-CHEAP", "MAR-NOVAL"]
    # The signal itself is untouched by the ranking.
    assert all(r["signal"] == "PEDIR_YA" for r in b["risks"])
