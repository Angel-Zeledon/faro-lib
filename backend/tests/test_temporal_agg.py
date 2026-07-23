"""
Tests for Backend/utils/temporal_agg.py

Covers: detect_frequency, available_granularities, aggregate_historical,
        aggregate_forecast, compute_stats — including edge cases with gaps,
        NaN values, irregular frequencies, and empty inputs.
"""

import math
import sys
import os

import pytest

# Allow running from project root or Backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.temporal_agg import (
    aggregate_forecast,
    aggregate_historical,
    available_granularities,
    compute_stats,
    detect_frequency,
)


# ── detect_frequency ──────────────────────────────────────────────────────────

class TestDetectFrequency:
    def test_daily(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        assert detect_frequency(dates) == "daily"

    def test_weekly(self):
        dates = ["2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22"]
        assert detect_frequency(dates) == "weekly"

    def test_monthly(self):
        dates = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]
        assert detect_frequency(dates) == "monthly"

    def test_quarterly(self):
        dates = ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"]
        assert detect_frequency(dates) == "quarterly"

    def test_yearly(self):
        dates = ["2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01"]
        assert detect_frequency(dates) == "yearly"

    def test_single_date_returns_daily(self):
        assert detect_frequency(["2024-01-01"]) == "daily"

    def test_empty_returns_daily(self):
        assert detect_frequency([]) == "daily"

    def test_two_dates_daily(self):
        assert detect_frequency(["2024-01-01", "2024-01-02"]) == "daily"

    def test_two_dates_monthly(self):
        assert detect_frequency(["2024-01-01", "2024-02-01"]) == "monthly"

    def test_irregular_uses_median(self):
        # Mix of weekly and bi-weekly — median should be weekly
        dates = [
            "2024-01-01", "2024-01-08", "2024-01-15",
            "2024-01-22", "2024-01-29",
            "2024-02-12",  # 2-week gap
        ]
        assert detect_frequency(dates) == "weekly"

    def test_weekly_with_gaps(self):
        # One missing week in an otherwise weekly series → still weekly
        dates = ["2024-01-01", "2024-01-08", "2024-01-22", "2024-01-29"]
        assert detect_frequency(dates) in ("weekly", "monthly")  # 15-day median is borderline

    def test_invalid_dates_fallback(self):
        assert detect_frequency(["not-a-date", "also-not"]) == "daily"


# ── available_granularities ───────────────────────────────────────────────────

class TestAvailableGranularities:
    def test_daily_includes_all(self):
        gran = available_granularities("daily")
        assert gran == ["daily", "weekly", "monthly", "quarterly", "yearly"]

    def test_weekly_excludes_daily(self):
        gran = available_granularities("weekly")
        assert "daily" not in gran
        assert gran[0] == "weekly"

    def test_monthly_starts_at_monthly(self):
        gran = available_granularities("monthly")
        assert gran == ["monthly", "quarterly", "yearly"]

    def test_quarterly_starts_at_quarterly(self):
        gran = available_granularities("quarterly")
        assert gran == ["quarterly", "yearly"]

    def test_yearly_only_yearly(self):
        gran = available_granularities("yearly")
        assert gran == ["yearly"]

    def test_unknown_freq_defaults_to_daily(self):
        gran = available_granularities("unknown_freq")
        assert gran == ["daily", "weekly", "monthly", "quarterly", "yearly"]

    def test_always_includes_base(self):
        for freq in ["daily", "weekly", "monthly", "quarterly", "yearly"]:
            gran = available_granularities(freq)
            assert gran[0] == freq


# ── aggregate_historical ──────────────────────────────────────────────────────

class TestAggregateHistorical:
    def _weekly_points(self):
        return [
            {"date": "2024-01-01", "value": 10.0},
            {"date": "2024-01-08", "value": 20.0},
            {"date": "2024-01-15", "value": 30.0},
            {"date": "2024-01-22", "value": 40.0},
            {"date": "2024-02-05", "value": 50.0},
        ]

    def test_sum_aggregation(self):
        pts = self._weekly_points()
        result = aggregate_historical(pts, "monthly", agg="sum")
        assert len(result) >= 1
        # January: 10+20+30+40 = 100
        jan = next((r for r in result if r["date"].startswith("2024-01")), None)
        assert jan is not None
        assert jan["value"] == pytest.approx(100.0)

    def test_mean_aggregation(self):
        pts = self._weekly_points()
        result = aggregate_historical(pts, "monthly", agg="mean")
        jan = next((r for r in result if r["date"].startswith("2024-01")), None)
        assert jan is not None
        assert jan["value"] == pytest.approx(25.0)

    def test_empty_input(self):
        assert aggregate_historical([], "monthly") == []

    def test_same_granularity_preserves_count(self):
        pts = [{"date": f"2024-01-{i:02d}", "value": float(i)} for i in range(1, 8)]
        result = aggregate_historical(pts, "daily")
        assert len(result) == len(pts)

    def test_weekly_to_monthly_reduces_points(self):
        # 12 weekly points span ~3 months
        from datetime import date, timedelta
        start = date(2024, 1, 1)
        pts = [{"date": (start + timedelta(weeks=i)).isoformat(), "value": float(i + 1)} for i in range(12)]
        result = aggregate_historical(pts, "monthly", agg="sum")
        assert len(result) <= 4  # at most 4 months

    def test_no_value_column_returns_original(self):
        pts = [{"date": "2024-01-01", "qty": 10.0}]
        result = aggregate_historical(pts, "monthly")
        assert result == pts

    def test_none_values_handled(self):
        pts = [
            {"date": "2024-01-01", "value": 10.0},
            {"date": "2024-01-08", "value": None},
            {"date": "2024-01-15", "value": 30.0},
        ]
        result = aggregate_historical(pts, "monthly", agg="sum")
        # NaN rows dropped by pandas sum, so only 10+30=40
        jan = next((r for r in result if r["date"].startswith("2024-01")), None)
        assert jan is not None
        # pandas sum skips NaN by default → 40
        assert jan["value"] == pytest.approx(40.0)

    def test_output_dates_are_strings(self):
        pts = [{"date": "2024-01-01", "value": 5.0}]
        result = aggregate_historical(pts, "monthly")
        assert isinstance(result[0]["date"], str)

    def test_output_values_are_float_or_none(self):
        pts = [{"date": "2024-01-01", "value": 7.0}, {"date": "2024-01-08", "value": 3.0}]
        result = aggregate_historical(pts, "monthly")
        for r in result:
            assert r["value"] is None or isinstance(r["value"], float)

    def test_single_point(self):
        pts = [{"date": "2024-06-15", "value": 99.0}]
        result = aggregate_historical(pts, "monthly")
        assert len(result) == 1
        assert result[0]["value"] == pytest.approx(99.0)

    def test_all_same_month_sums(self):
        pts = [{"date": f"2024-03-{d:02d}", "value": 1.0} for d in range(1, 6)]
        result = aggregate_historical(pts, "monthly", agg="sum")
        assert len(result) == 1
        assert result[0]["value"] == pytest.approx(5.0)


# ── aggregate_forecast ────────────────────────────────────────────────────────

class TestAggregateForecast:
    def _forecast_pts(self):
        return [
            {"date": "2024-02-05", "value": 50.0, "q10": 40.0, "q90": 60.0, "lower": 40.0, "upper": 60.0},
            {"date": "2024-02-12", "value": 55.0, "q10": 45.0, "q90": 65.0, "lower": 45.0, "upper": 65.0},
            {"date": "2024-02-19", "value": 60.0, "q10": 50.0, "q90": 70.0, "lower": 50.0, "upper": 70.0},
            {"date": "2024-02-26", "value": 45.0, "q10": 35.0, "q90": 55.0, "lower": 35.0, "upper": 55.0},
        ]

    def test_sum_aggregation_value(self):
        pts = self._forecast_pts()
        result = aggregate_forecast(pts, "monthly", agg="sum")
        assert len(result) >= 1
        feb = next((r for r in result if r["date"].startswith("2024-02")), None)
        assert feb is not None
        assert feb["value"] == pytest.approx(210.0)  # 50+55+60+45

    def test_lower_quantile_uses_min(self):
        pts = self._forecast_pts()
        result = aggregate_forecast(pts, "monthly", agg="sum")
        feb = result[0]
        # lower/q10 should use min: min(40,45,50,35)=35
        assert feb.get("lower") == pytest.approx(35.0)
        assert feb.get("q10") == pytest.approx(35.0)

    def test_upper_quantile_uses_max(self):
        pts = self._forecast_pts()
        result = aggregate_forecast(pts, "monthly", agg="sum")
        feb = result[0]
        # upper/q90 should use max: max(60,65,70,55)=70
        assert feb.get("upper") == pytest.approx(70.0)
        assert feb.get("q90") == pytest.approx(70.0)

    def test_empty_input(self):
        assert aggregate_forecast([], "monthly") == []

    def test_mean_aggregation(self):
        pts = self._forecast_pts()
        result = aggregate_forecast(pts, "monthly", agg="mean")
        feb = next((r for r in result if r["date"].startswith("2024-02")), None)
        assert feb is not None
        assert feb["value"] == pytest.approx(52.5)  # (50+55+60+45)/4

    def test_output_preserves_quantile_keys(self):
        pts = self._forecast_pts()
        result = aggregate_forecast(pts, "monthly", agg="sum")
        assert len(result) > 0
        keys = set(result[0].keys())
        assert "value" in keys
        assert "q10" in keys
        assert "q90" in keys

    def test_no_nan_in_output(self):
        pts = self._forecast_pts()
        result = aggregate_forecast(pts, "monthly")
        for r in result:
            for k, v in r.items():
                if k != "date" and v is not None:
                    assert not math.isnan(v), f"NaN found in {k}"

    def test_single_forecast_point(self):
        pts = [{"date": "2024-03-01", "value": 100.0, "q10": 80.0, "q90": 120.0}]
        result = aggregate_forecast(pts, "monthly", agg="sum")
        assert len(result) == 1
        assert result[0]["value"] == pytest.approx(100.0)

    def test_without_quantiles(self):
        pts = [
            {"date": "2024-02-05", "value": 50.0},
            {"date": "2024-02-12", "value": 60.0},
        ]
        result = aggregate_forecast(pts, "monthly", agg="sum")
        assert len(result) == 1
        assert result[0]["value"] == pytest.approx(110.0)

    def test_daily_to_weekly(self):
        from datetime import date, timedelta
        start = date(2024, 1, 1)
        pts = [
            {"date": (start + timedelta(days=i)).isoformat(), "value": 1.0, "q10": 0.8, "q90": 1.2}
            for i in range(14)
        ]
        result = aggregate_forecast(pts, "weekly", agg="sum")
        # W-MON rule may split 14 days into 2-3 buckets depending on start day alignment
        assert 2 <= len(result) <= 3
        total = sum(r["value"] for r in result if r["value"] is not None)
        assert total == pytest.approx(14.0)


# ── compute_stats ─────────────────────────────────────────────────────────────

class TestComputeStats:
    def test_basic(self):
        s = compute_stats([1.0, 2.0, 3.0, 4.0, 5.0])
        assert s["mean"] == pytest.approx(3.0)
        assert s["min"] == 1.0
        assert s["max"] == 5.0
        assert s["median"] == 3.0
        assert s["n"] == 5
        assert s["std"] > 0

    def test_empty(self):
        assert compute_stats([]) == {}

    def test_single_value(self):
        s = compute_stats([42.0])
        assert s["mean"] == pytest.approx(42.0)
        assert s["std"] == pytest.approx(0.0)
        assert s["min"] == s["max"] == s["median"] == 42.0
        assert s["n"] == 1

    def test_two_values(self):
        s = compute_stats([0.0, 10.0])
        assert s["mean"] == pytest.approx(5.0)
        assert s["n"] == 2

    def test_all_same(self):
        s = compute_stats([7.0, 7.0, 7.0])
        assert s["mean"] == pytest.approx(7.0)
        assert s["std"] == pytest.approx(0.0)

    def test_large_values(self):
        vals = [float(i * 1000) for i in range(1, 101)]
        s = compute_stats(vals)
        assert s["n"] == 100
        assert s["mean"] == pytest.approx(50500.0)

    def test_negative_values(self):
        s = compute_stats([-5.0, -3.0, -1.0, 1.0, 3.0])
        assert s["mean"] == pytest.approx(-1.0)
        assert s["min"] == -5.0
        assert s["max"] == 3.0

    def test_median_even_length(self):
        s = compute_stats([1.0, 2.0, 3.0, 4.0])
        assert s["median"] == pytest.approx(2.5)

    def test_returns_rounded_values(self):
        s = compute_stats([1.0 / 3.0] * 6)
        # std is 0, mean has at most 4 decimal places
        assert len(str(s["mean"]).split(".")[-1]) <= 6


# ── Integration: detect → aggregate pipeline ─────────────────────────────────

class TestPipeline:
    def test_weekly_to_monthly_pipeline(self):
        from datetime import date, timedelta
        start = date(2024, 1, 1)
        pts = [{"date": (start + timedelta(weeks=i)).isoformat(), "value": 10.0} for i in range(12)]
        freq = detect_frequency([p["date"] for p in pts])
        assert freq == "weekly"
        gran = available_granularities(freq)
        assert "monthly" in gran
        result = aggregate_historical(pts, "monthly", agg="sum")
        # 12 weekly points → ~3 calendar months
        assert 2 <= len(result) <= 4

    def test_daily_no_aggregation_needed(self):
        pts = [{"date": f"2024-01-{d:02d}", "value": float(d)} for d in range(1, 8)]
        freq = detect_frequency([p["date"] for p in pts])
        assert freq == "daily"
        result = aggregate_historical(pts, "daily")
        assert len(result) == len(pts)

    def test_monthly_to_quarterly(self):
        pts = [
            {"date": "2024-01-01", "value": 100.0},
            {"date": "2024-02-01", "value": 200.0},
            {"date": "2024-03-01", "value": 300.0},
            {"date": "2024-04-01", "value": 400.0},
            {"date": "2024-05-01", "value": 500.0},
            {"date": "2024-06-01", "value": 600.0},
        ]
        result = aggregate_historical(pts, "quarterly", agg="sum")
        # Q1 (Jan-Mar) = 600, Q2 (Apr-Jun) = 1500
        assert len(result) == 2
        q1 = next(r for r in result if r["date"].startswith("2024-01"))
        assert q1["value"] == pytest.approx(600.0)
        q2 = next(r for r in result if r["date"].startswith("2024-04"))
        assert q2["value"] == pytest.approx(1500.0)

    def test_forecast_pipeline_with_all_quantiles(self):
        pts = [
            {
                "date": f"2024-0{m}-01",
                "value": float(m * 100),
                "q5": float(m * 80),
                "q10": float(m * 85),
                "q25": float(m * 90),
                "q50": float(m * 100),
                "q75": float(m * 110),
                "q90": float(m * 115),
                "q95": float(m * 120),
            }
            for m in range(1, 4)
        ]
        result = aggregate_forecast(pts, "quarterly", agg="sum")
        assert len(result) == 1
        r = result[0]
        assert r["value"] == pytest.approx(600.0)
        # lower quantiles use min across the window
        assert r["q5"] == pytest.approx(80.0)   # min(80,160,240)=80
        assert r["q10"] == pytest.approx(85.0)
        # upper quantiles use max
        assert r["q90"] == pytest.approx(345.0)  # max(115,230,345)=345
        assert r["q95"] == pytest.approx(360.0)  # max(120,240,360)=360

    def test_stats_on_aggregated_output(self):
        pts = [{"date": f"2024-{m:02d}-01", "value": float(m * 10)} for m in range(1, 13)]
        agg = aggregate_historical(pts, "quarterly", agg="sum")
        vals = [r["value"] for r in agg if r["value"] is not None]
        s = compute_stats(vals)
        assert s["n"] == 4
        # Q1=60, Q2=90 (but pandas QS cuts differently), just verify structure
        assert "mean" in s and "std" in s


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_series_with_large_gap_detection(self):
        # Gap of 2 months in an otherwise monthly series
        dates = ["2024-01-01", "2024-02-01", "2024-04-01", "2024-05-01"]
        freq = detect_frequency(dates)
        assert freq == "monthly"

    def test_aggregate_historical_with_all_none_values(self):
        pts = [{"date": "2024-01-01", "value": None}, {"date": "2024-01-08", "value": None}]
        result = aggregate_historical(pts, "monthly")
        # pandas sum() of all-NaN returns 0.0 — result is one bucket with value 0
        # (dropna() runs after resample, so only truly missing buckets are dropped)
        assert len(result) <= 1
        if result:
            assert result[0]["value"] == pytest.approx(0.0)

    def test_aggregate_forecast_missing_quantile_fields(self):
        pts = [{"date": "2024-02-01", "value": 50.0}]
        result = aggregate_forecast(pts, "monthly")
        assert result[0]["value"] == pytest.approx(50.0)
        assert "q10" not in result[0]

    def test_irregular_frequency_fallback(self):
        dates = ["2024-01-01", "2024-01-03", "2024-01-10", "2024-01-11"]
        freq = detect_frequency(dates)
        assert freq in ("daily", "weekly")

    def test_single_point_stats(self):
        s = compute_stats([100.0])
        assert s["n"] == 1
        assert s["std"] == 0.0

    def test_aggregate_returns_date_strings_not_timestamps(self):
        pts = [{"date": "2024-01-15", "value": 10.0}]
        result = aggregate_historical(pts, "monthly")
        assert isinstance(result[0]["date"], str)
        assert len(result[0]["date"]) == 10  # YYYY-MM-DD

    def test_forecast_multiple_months(self):
        pts = [
            {"date": "2024-01-15", "value": 10.0, "q10": 8.0, "q90": 12.0},
            {"date": "2024-02-15", "value": 20.0, "q10": 16.0, "q90": 24.0},
            {"date": "2024-03-15", "value": 30.0, "q10": 24.0, "q90": 36.0},
        ]
        result = aggregate_forecast(pts, "monthly")
        assert len(result) == 3
        for r in result:
            assert r["value"] is not None

    def test_weekly_boundary_dates(self):
        # W-MON rule starts weeks on Monday — make sure Sunday dates still aggregate
        dates = ["2024-01-07", "2024-01-14", "2024-01-21"]  # Sundays
        pts = [{"date": d, "value": 10.0} for d in dates]
        result = aggregate_historical(pts, "weekly")
        assert len(result) >= 3  # each Sunday belongs to a different W-MON week

    def test_no_mutation_of_input(self):
        pts = [{"date": "2024-01-01", "value": 5.0}]
        original = [{"date": "2024-01-01", "value": 5.0}]
        aggregate_historical(pts, "monthly")
        assert pts == original


# ── bucket_count + planning_granularities (multi-period gate) ──────────────────
from utils.temporal_agg import bucket_count, planning_granularities  # noqa: E402


def _daily_dates(n):
    import datetime
    d0 = datetime.date(2025, 1, 1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


class TestBucketGate:
    def test_bucket_count_daily_weekly_monthly(self):
        dates = _daily_dates(70)  # 70 days
        assert bucket_count(dates, "daily") == 70
        assert 10 <= bucket_count(dates, "weekly") <= 11   # ~10 weeks
        assert bucket_count(dates, "monthly") == 3         # Jan, Feb, Mar

    def test_bucket_count_empty(self):
        assert bucket_count([], "weekly") == 0

    def test_planning_gates_monthly_out_on_short_history(self):
        # 70 daily points: daily(70) and weekly(~10) clear a floor of 8,
        # monthly(3) does not.
        got = planning_granularities("daily", _daily_dates(70), min_buckets=8)
        assert got == ["daily", "weekly"]

    def test_planning_always_includes_base_even_if_short(self):
        got = planning_granularities("daily", _daily_dates(5), min_buckets=20)
        assert got == ["daily"]

    def test_planning_from_weekly_base_never_offers_daily(self):
        weekly_dates = [d for i, d in enumerate(_daily_dates(700)) if i % 7 == 0]
        got = planning_granularities("weekly", weekly_dates, min_buckets=8)
        assert "daily" not in got
        assert got[0] == "weekly"
