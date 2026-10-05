"""Signed forecast bias: positive = over-forecast, negative = under-forecast."""
import math

import pytest

from forecasting_core.evaluation.realized import (
    compare_forecast_to_actuals,
    forecast_bias,
    forecast_value_added,
)


def test_over_forecast_is_positive():
    assert forecast_bias([(120.0, 100.0)]) == pytest.approx(0.2)


def test_under_forecast_is_negative():
    assert forecast_bias([(80.0, 100.0), (90.0, 100.0)]) == pytest.approx(-0.15)


def test_errors_of_opposite_sign_cancel():
    assert forecast_bias([(110.0, 100.0), (90.0, 100.0)]) == pytest.approx(0.0)


def test_zero_demand_is_undefined_not_infinite():
    assert forecast_bias([(5.0, 0.0), (3.0, 0.0)]) is None


def test_no_points_is_undefined():
    assert forecast_bias([]) is None


def test_nan_points_are_ignored_not_poisoning():
    assert forecast_bias([(float("nan"), 100.0), (120.0, 100.0)]) == pytest.approx(0.2)
    assert forecast_bias([(100.0, float("nan"))]) is None
    assert forecast_bias([(math.inf, 100.0)]) is None


def test_per_sku_bias_matches_the_pooled_definition():
    out = compare_forecast_to_actuals(
        {"A": {"2026-01-01": 120.0, "2026-02-01": 80.0}, "B": {"2026-01-01": 50.0}},
        {"A": {"2026-01-01": 100.0, "2026-02-01": 100.0}, "B": {"2026-01-01": 100.0}},
    )
    by_sku = {r["sku"]: r for r in out["skus"]}
    assert by_sku["A"]["bias"] == pytest.approx(0.0)
    assert by_sku["B"]["bias"] == pytest.approx(-0.5)
    assert out["aggregate"]["bias"] == pytest.approx((250 - 300) / 300)


def test_single_point_sku():
    out = compare_forecast_to_actuals({"A": {"2026-01-01": 130.0}}, {"A": {"2026-01-01": 100.0}})
    assert out["skus"][0]["bias"] == pytest.approx(0.3)
    assert out["skus"][0]["n_points"] == 1


def test_nan_actual_is_skipped_and_counted():
    out = compare_forecast_to_actuals(
        {"A": {"2026-01-01": 110.0, "2026-02-01": 90.0}},
        {"A": {"2026-01-01": 100.0, "2026-02-01": float("nan")}},
    )
    assert out["skus"][0]["n_points"] == 1
    assert out["skus"][0]["bias"] == pytest.approx(0.1)
    assert out["skipped_points"] == 1


def test_zero_demand_sku_has_no_bias_but_is_listed():
    out = compare_forecast_to_actuals({"A": {"2026-01-01": 4.0}}, {"A": {"2026-01-01": 0.0}})
    assert out["skus"][0]["bias"] is None


def test_value_added_reports_direction_of_base_and_adjusted():
    pts = [{"base": 100.0, "adjusted": 120.0, "actual": 110.0}]
    r = forecast_value_added(pts)
    assert r["base_bias"] == pytest.approx(-10 / 110)
    assert r["adjusted_bias"] == pytest.approx(10 / 110)
    assert forecast_value_added([])["base_bias"] is None
