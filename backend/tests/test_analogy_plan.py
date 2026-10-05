"""Forecast by analogy: the pure serving decision (no database).

`plan_analogies` decides, per live analogy, whether it serves, was superseded by
a trained model, or cannot apply. The DB-backed ledger and status tests are in
`test_sku_analogies.py`.
"""

import pytest

from backend.inventory.analogy_service import champion_points, plan_analogies


def _fc(values, start=1, **extra):
    return {"forecast": [{"date": f"2026-02-{start + i:02d}", "value": v, **extra}
                         for i, v in enumerate(values)]}


def _analogy(refs, scale=1.0, start=None, aid="an1"):
    return {"id": aid, "new_sku": "NEW", "reference_skus": refs, "scale_factor": scale,
            "start_date": start, "note": "n", "created_by_name": "Ana", "created_at": "t"}


class TestChampionPoints:

    def test_preferred_model_wins(self):
        mf = {"a": _fc([1, 1]), "b": _fc([9, 9])}
        assert [p["value"] for p in champion_points(mf, "b")] == [9, 9]

    def test_unknown_champion_averages_models_per_date(self):
        mf = {"a": _fc([2, 4]), "b": _fc([4, 8])}
        assert [p["value"] for p in champion_points(mf, None)] == [3.0, 6.0]

    def test_preferred_without_points_falls_back_to_the_others(self):
        mf = {"a": {"forecast": []}, "b": _fc([5])}
        assert [p["value"] for p in champion_points(mf, "a")] == [5]

    def test_nothing(self):
        assert champion_points({}, None) == []


class TestPlanAnalogies:

    def test_serves_a_product_with_no_trained_forecast(self):
        forecasts = {"A": {"m": _fc([10, 10], q90=14)}, "B": {"m": _fc([20, 20], q90=26)}}
        serving, retired, unavailable = plan_analogies(
            {"NEW": _analogy(["A", "B"], scale=2.0)}, forecasts, {"A": "m", "B": "m"})
        assert retired == {} and unavailable == {}
        pts = serving["NEW"]["model_forecasts"]["analogy"]["forecast"]
        assert [p["value"] for p in pts] == [30.0, 30.0]          # mean 15 * 2
        # band is wider than the references' own (mean offset 5 * 2 = 10)
        assert pts[0]["q90"] - pts[0]["value"] > 10
        applied = serving["NEW"]["applied"]
        assert applied["references"] == ["A", "B"] and applied["scale_factor"] == 2.0
        assert applied["band_widen_factor"] > 1.0 and applied["created_by_name"] == "Ana"

    def test_the_stand_in_is_never_named_like_a_trained_model(self):
        serving, _, _ = plan_analogies({"NEW": _analogy(["A"])},
                                       {"A": {"lightgbm": _fc([5])}}, {"A": "lightgbm"})
        assert list(serving["NEW"]["model_forecasts"]) == ["analogy"]

    def test_trained_forecast_takes_over_and_the_analogy_is_retired(self):
        a = _analogy(["A"])
        forecasts = {"A": {"m": _fc([10])}, "NEW": {"m": _fc([3])}}
        serving, retired, unavailable = plan_analogies({"NEW": a}, forecasts, {})
        assert serving == {} and unavailable == {} and retired == {"NEW": a}

    def test_a_trained_entry_without_points_does_not_count_as_trained(self):
        forecasts = {"A": {"m": _fc([10])}, "NEW": {"m": {"forecast": []}}}
        serving, retired, _ = plan_analogies({"NEW": _analogy(["A"])}, forecasts, {})
        assert "NEW" in serving and retired == {}

    def test_references_without_forecast_are_reported_not_invented(self):
        serving, retired, unavailable = plan_analogies(
            {"NEW": _analogy(["GHOST", "OTHER"], aid="x")}, {}, {})
        assert serving == {} and retired == {}
        assert unavailable["NEW"] == {"analogy_id": "x",
                                      "references_missing": ["GHOST", "OTHER"]}

    def test_partial_references_name_the_missing_ones(self):
        serving, _, _ = plan_analogies({"NEW": _analogy(["A", "GHOST"])},
                                       {"A": {"m": _fc([4])}}, {})
        assert serving["NEW"]["applied"]["references"] == ["A"]
        assert serving["NEW"]["applied"]["references_missing"] == ["GHOST"]

    def test_start_date_aligns_by_periods_since_launch(self):
        serving, _, _ = plan_analogies(
            {"NEW": _analogy(["A"], start="2026-06-01")},
            {"A": {"m": _fc([1, 2])}}, {})
        pts = serving["NEW"]["model_forecasts"]["analogy"]["forecast"]
        assert [p["date"] for p in pts] == ["2026-06-01", "2026-06-02"]
        assert serving["NEW"]["applied"]["alignment"] == "since_start"

    def test_no_analogies_changes_nothing(self):
        assert plan_analogies({}, {"A": {"m": _fc([1])}}, {}) == ({}, {}, {})

    def test_an_analog_reference_is_not_chained(self):
        # NEW2 references NEW, which has only an analogy (no trained forecast):
        # nothing to read, so it is unavailable rather than a copy of a copy.
        analogies = {"NEW": _analogy(["A"], aid="1"),
                     "NEW2": {**_analogy(["NEW"], aid="2"), "new_sku": "NEW2"}}
        serving, _, unavailable = plan_analogies(
            analogies, {"A": {"m": _fc([5])}}, {})
        assert "NEW" in serving and "NEW2" in unavailable
