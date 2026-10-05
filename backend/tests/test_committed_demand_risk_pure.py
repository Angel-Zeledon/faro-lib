"""Pure logic of committed demand: cover with no forecast, the optimizer's
buckets and the at-risk allocation. No database, no conftest."""
from datetime import date, timedelta

from backend.inventory import committed_demand_service as svc

TODAY = date(2026, 10, 5)


def _c(i, days, qty, customer="ACME", prob=1.0, warehouse=None):
    return {"id": i, "delivery_date": TODAY + timedelta(days=days), "quantity": qty,
            "probability": prob, "customer": customer, "warehouse_id": warehouse}


# ── allocate_risk ────────────────────────────────────────────────────────────

def test_earliest_commitment_gets_the_stock_first():
    out = svc.allocate_risk([_c("late", 60, 100), _c("early", 30, 100)],
                            stock=100, arrivals=[], lead_time_days=20, today=TODAY)
    assert out["early"]["at_risk"] is False and out["early"]["shortfall"] == 0.0
    assert out["late"]["at_risk"] is True and out["late"]["shortfall"] == 100.0


def test_partial_cover_reports_exact_shortfall_and_safe_date():
    out = svc.allocate_risk([_c("a", 40, 100)], stock=30, arrivals=[],
                            lead_time_days=15, today=TODAY)
    a = out["a"]
    assert a["shortfall"] == 70.0 and a["covered_units"] == 30.0
    assert a["latest_safe_order_date"] == (TODAY + timedelta(days=25)).isoformat()
    assert a["order_date_passed"] is False


def test_safe_date_already_past_is_flagged():
    out = svc.allocate_risk([_c("a", 10, 50)], stock=0, arrivals=[],
                            lead_time_days=15, today=TODAY)
    assert out["a"]["at_risk"] and out["a"]["order_date_passed"] is True


def test_an_arrival_before_delivery_covers_and_one_after_does_not():
    before = [(TODAY + timedelta(days=20), 100.0)]
    after = [(TODAY + timedelta(days=50), 100.0)]
    c = [_c("a", 30, 100)]
    assert svc.allocate_risk(c, 0, before, 10, TODAY)["a"]["at_risk"] is False
    assert svc.allocate_risk(c, 0, after, 10, TODAY)["a"]["shortfall"] == 100.0


def test_arrival_with_no_date_counts_as_available_now():
    out = svc.allocate_risk([_c("a", 5, 80)], 0, [(None, 80.0)], 10, TODAY)
    assert out["a"]["at_risk"] is False


def test_probability_scales_the_units():
    out = svc.allocate_risk([_c("a", 30, 100, prob=0.5)], 50, [], 10, TODAY)
    assert out["a"]["at_risk"] is False


def test_overdue_commitment_is_judged_as_of_today():
    out = svc.allocate_risk([_c("a", -10, 40)], 10, [(TODAY, 5.0)], 10, TODAY)
    assert out["a"]["shortfall"] == 25.0


def test_no_stock_row_means_no_verdict_not_a_guess():
    out = svc.allocate_risk([_c("a", 30, 100)], None, [], 10, TODAY)
    assert out["a"]["at_risk"] is None and out["a"]["shortfall"] is None


def test_cumulative_demand_not_per_commitment():
    # Each one alone fits in 100 of stock; together they do not.
    out = svc.allocate_risk([_c("a", 10, 80), _c("b", 20, 80)], 100, [], 5, TODAY)
    assert out["a"]["at_risk"] is False
    assert out["b"]["shortfall"] == 60.0


# ── summarize_by_customer ────────────────────────────────────────────────────

def test_summary_groups_and_orders_by_risk():
    items = [
        {"status": "open", "customer": "A", "at_risk": False},
        {"status": "open", "customer": "B", "at_risk": True, "shortfall": 10.0,
         "latest_safe_order_date": "2026-11-01"},
        {"status": "open", "customer": "B", "at_risk": True, "shortfall": 5.0,
         "latest_safe_order_date": "2026-10-20"},
        {"status": "open", "customer": None, "at_risk": None},
        {"status": "fulfilled", "customer": "A", "at_risk": None},
    ]
    out = svc.summarize_by_customer(items)
    assert [g["customer"] for g in out] == ["B", "A", None]
    b = out[0]
    assert b["open"] == 2 and b["at_risk"] == 2 and b["shortfall"] == 15.0
    assert b["first_safe_order_date"] == "2026-10-20"
    assert out[1]["open"] == 1 and out[2]["unknown"] == 1


# ── cover_without_forecast ───────────────────────────────────────────────────

def test_no_commitments_is_neutral():
    out = svc.cover_without_forecast(None, TODAY, 15, 0, 0, 0)
    assert out["units"] == 0 and out["signal"] is None and out["applied"] == []


def test_uncovered_commitment_inside_lead_time_says_order_now():
    out = svc.cover_without_forecast([_c("a", 10, 100)], TODAY, 15, 0, 20, 30, moq=1)
    assert out["signal"] == "PEDIR_YA"
    assert out["shortfall"] == 50.0 and out["recommended"] == 50.0
    assert out["applied"][0]["units"] == 100.0


def test_uncovered_commitment_beyond_lead_time_says_order_soon():
    out = svc.cover_without_forecast([_c("a", 25, 100)], TODAY, 15, 14, 0, 0, moq=60)
    assert out["signal"] == "PEDIR_PRONTO"
    assert out["recommended"] == 100.0  # shortfall above the MOQ


def test_moq_floor_applies_to_the_recommendation():
    out = svc.cover_without_forecast([_c("a", 25, 10)], TODAY, 15, 14, 0, 0, moq=60)
    assert out["recommended"] == 60.0


def test_covered_commitment_changes_no_signal():
    out = svc.cover_without_forecast([_c("a", 10, 100)], TODAY, 15, 0, 100, 0)
    assert out["signal"] is None and out["units"] == 100.0 and out["applied"]


def test_outside_the_protection_interval_is_not_counted():
    out = svc.cover_without_forecast([_c("a", 200, 100)], TODAY, 15, 0, 0, 0)
    assert out["units"] == 0 and out["signal"] is None


def test_unknown_stock_signals_but_recommends_no_quantity():
    out = svc.cover_without_forecast([_c("a", 10, 100)], TODAY, 15, 0, None, 0)
    assert out["stock_unknown"] is True
    assert out["signal"] == "PEDIR_YA" and out["recommended"] is None


def test_first_uncovered_commitment_decides_urgency():
    # The early one is covered by stock; only the later one (beyond lead time) is short.
    out = svc.cover_without_forecast([_c("e", 5, 100), _c("l", 30, 50)], TODAY, 15, 30, 100, 0)
    assert out["signal"] == "PEDIR_PRONTO" and out["shortfall"] == 50.0


# ── demand_buckets_by_warehouse (the optimizer's view) ───────────────────────

def test_buckets_use_the_panel_window_and_probability():
    c = [_c("in", 3, 100, prob=0.5), _c("out", 40, 999), _c("over", -2, 10)]
    out = svc.demand_buckets_by_warehouse(c, TODAY, 14, 1, 14, ["W1"], {"W1": 1.0}, "W1")
    assert out["W1"][3] == 50.0 and out["W1"][0] == 10.0
    assert sum(out["W1"]) == 60.0  # the one at day 40 is past the horizon


def test_buckets_split_unassigned_by_share_and_keep_named_whole():
    c = [_c("free", 2, 100), _c("named", 2, 40, warehouse="W2")]
    out = svc.demand_buckets_by_warehouse(c, TODAY, 14, 1, 14, ["W1", "W2"],
                                          {"W1": 0.75, "W2": 0.25}, "W1")
    assert out["W1"][2] == 75.0
    assert out["W2"][2] == 25.0 + 40.0


def test_buckets_weekly_period_lands_in_the_right_bucket():
    out = svc.demand_buckets_by_warehouse([_c("a", 15, 70)], TODAY, 28, 7, 4,
                                          ["W1"], {"W1": 1.0}, "W1")
    assert out["W1"] == [0.0, 0.0, 70.0, 0.0]


def test_store_mode_unassigned_goes_to_default_once():
    c = [_c("free", 1, 100)]
    out = svc.demand_buckets_by_warehouse(c, TODAY, 7, 1, 7, ["S1", "S2"], None, "S2")
    assert sum(out.get("S1", [0.0])) == 0.0
    assert sum(out["S2"]) == 100.0


def test_no_commitments_no_change():
    assert svc.demand_buckets_by_warehouse(None, TODAY, 14, 1, 14, ["W1"], {"W1": 1.0}, "W1") == {}


def test_optimizer_matches_panel_units_for_the_same_window():
    """Owner rule: the optimizer's committed units for a window equal what the
    Panel's `committed_units` counts for that window (one implementation)."""
    c = [_c("a", 3, 100, prob=0.8), _c("b", 9, 40), _c("late", 30, 500)]
    panel_units, _ = svc.committed_units(c, TODAY, 14, warehouse_id=None)
    out = svc.demand_buckets_by_warehouse(c, TODAY, 14, 1, 14, ["W1"], {"W1": 1.0}, "W1")
    assert abs(sum(out["W1"]) - panel_units) < 1e-9
