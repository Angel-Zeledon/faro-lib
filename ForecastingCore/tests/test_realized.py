import pytest

from forecasting_core.evaluation.realized import (
    BIAS_NOTABLE, MIN_POINTS_FOR_VERDICT, compare_forecast_to_actuals, verdict_for,
)


def _days(n, f, a):
    return ({f"2026-01-{d:02d}": f for d in range(1, n + 1)},
            {f"2026-01-{d:02d}": a for d in range(1, n + 1)})


def test_metrics_wape_mape_bias_by_hand():
    out = compare_forecast_to_actuals(
        {"A": {"d1": 12.0, "d2": 8.0}}, {"A": {"d1": 10.0, "d2": 10.0}})
    a = out["aggregate"]
    assert a["wape"] == pytest.approx(4 / 20)          # |2|+|-2| over 20
    assert a["mape"] == pytest.approx((0.2 + 0.2) / 2)
    assert a["bias"] == pytest.approx(0.0)              # over and under cancel
    assert a["total_forecast"] == 20 and a["total_actual"] == 20


def test_bias_sign_positive_when_forecast_runs_high():
    f, a = _days(10, 15.0, 10.0)
    out = compare_forecast_to_actuals({"A": f}, {"A": a})
    assert out["aggregate"]["bias"] == pytest.approx(0.5)
    v = out["aggregate"]["verdict"]
    assert v["direction"] == "over" and v["level"] == "poor"


def test_bias_negative_means_under_forecasting():
    f, a = _days(10, 8.0, 10.0)
    v = compare_forecast_to_actuals({"A": f}, {"A": a})["aggregate"]["verdict"]
    assert v["direction"] == "under" and v["level"] == "good"   # wape 0.2


def test_only_dates_in_both_are_compared_and_missing_is_not_zero():
    out = compare_forecast_to_actuals(
        {"A": {"d1": 5.0, "d2": 5.0, "d3": 5.0}}, {"A": {"d1": 5.0}})
    assert out["aggregate"]["n_points"] == 1
    assert out["skipped_points"] == 2
    assert out["aggregate"]["wape"] == 0.0


def test_zero_actual_volume_gives_undefined_wape_not_a_crash():
    out = compare_forecast_to_actuals({"A": {"d1": 3.0}}, {"A": {"d1": 0.0}})
    assert out["aggregate"]["wape"] is None and out["aggregate"]["mape"] is None
    assert out["aggregate"]["verdict"]["level"] == "no_data"


def test_no_overlap_is_no_data():
    out = compare_forecast_to_actuals({"A": {"d1": 3.0}}, {"B": {"d1": 3.0}})
    assert out["aggregate"]["verdict"]["level"] == "no_data"
    assert out["skus"] == [] and out["skipped_points"] == 1


def test_few_points_are_too_little_to_judge():
    f, a = _days(MIN_POINTS_FOR_VERDICT - 1, 10.0, 10.0)
    v = compare_forecast_to_actuals({"A": f}, {"A": a})["aggregate"]["verdict"]
    assert v["level"] == "too_little"


def test_skus_sorted_worst_first_and_series_summed_by_date():
    out = compare_forecast_to_actuals(
        {"good": {"d1": 10.0, "d2": 10.0}, "bad": {"d1": 30.0, "d2": 30.0}},
        {"good": {"d1": 10.0, "d2": 10.0}, "bad": {"d1": 10.0, "d2": 10.0}})
    assert [s["sku"] for s in out["skus"]] == ["bad", "good"]
    assert out["aggregate"]["series"][0] == {"date": "d1", "forecast": 40.0, "actual": 20.0}
    assert out["aggregate"]["n_skus"] == 2


def test_verdict_levels_cover_thresholds():
    assert verdict_for(0.20, 0.0, 10)["level"] == "good"
    assert verdict_for(0.21, 0.0, 10)["level"] == "fair"
    assert verdict_for(0.40, 0.0, 10)["level"] == "fair"
    assert verdict_for(0.41, 0.0, 10)["level"] == "poor"
    assert verdict_for(0.1, BIAS_NOTABLE + 0.01, 10)["direction"] == "over"
    assert verdict_for(0.1, BIAS_NOTABLE, 10)["direction"] == "balanced"


# ── describe_overlap: where a dataset sits against the forecast window ───────

from forecasting_core.evaluation.realized import describe_overlap


@pytest.mark.parametrize("data,expected", [
    (("2026-01-01", "2026-03-31"), "covers"),
    (("2026-02-01", "2026-03-31"), "partial"),
    (("2026-01-01", "2026-02-10"), "partial"),
    (("2025-01-01", "2025-12-20"), "ends_before_forecast"),
    (("2026-04-01", "2026-05-01"), "starts_after_forecast"),
])
def test_describe_overlap_relations(data, expected):
    out = describe_overlap("2026-01-15", "2026-03-15", *data)
    assert out["relation"] == expected


def test_describe_overlap_numbers():
    out = describe_overlap("2026-01-15", "2026-03-15", "2025-01-01", "2025-12-20")
    assert out["gap_days"] == 26 and out["overlap_from"] is None
    out = describe_overlap("2026-01-15", "2026-03-15", "2026-02-01", "2026-03-31")
    assert (out["overlap_from"], out["overlap_to"]) == ("2026-02-01", "2026-03-15")
    assert describe_overlap(None, "2026-03-15", "2026-02-01", "2026-03-31")["relation"] == "unknown"
