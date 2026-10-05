"""Forecast by analogy: the pure engine function (no pandas, no I/O)."""

import pytest

from forecasting_core.inference.analogy import (
    BAND_WIDEN_FACTOR, MIN_RELATIVE_SIGMA, build_analogy_forecast,
)

Z = 1.2816


def pts(start_day, values, band=None):
    """Daily points from 2026-01-<start_day>; band = half-width of upper/q90."""
    out = []
    for i, v in enumerate(values):
        p = {"date": f"2026-01-{start_day + i:02d}", "value": v}
        if band is not None:
            p.update({"upper": v + band, "lower": max(0, v - band),
                      "q90": v + band, "q10": max(0, v - band)})
        out.append(p)
    return out


def by_date(result):
    return {p["date"]: p for p in result["points"]}


def test_one_reference_scaled_with_widened_band():
    r = build_analogy_forecast(
        [{"sku": "A", "points": pts(1, [10, 20], band=4)}], scale_factor=2.0)
    p = by_date(r)
    assert p["2026-01-01"]["value"] == 20.0 and p["2026-01-02"]["value"] == 40.0
    # half-width = ref half-width 4 * scale 2 * widen factor
    assert p["2026-01-01"]["upper"] == pytest.approx(20 + 4 * 2 * BAND_WIDEN_FACTOR)
    assert p["2026-01-01"]["q90"] == pytest.approx(20 + 4 * 2 * BAND_WIDEN_FACTOR)
    assert p["2026-01-01"]["lower"] == pytest.approx(20 - 4 * 2 * BAND_WIDEN_FACTOR)
    assert r["method"] == "analogy" and r["alignment"] == "calendar"
    assert r["references_used"] == ["A"] and r["references_empty"] == []


def test_band_is_always_wider_than_the_reference_band():
    ref = pts(1, [50], band=5)
    r = build_analogy_forecast([{"sku": "A", "points": ref}])
    assert r["points"][0]["upper"] - 50 > 5
    assert 50 - r["points"][0]["lower"] > 5


def test_cannot_ask_for_a_narrower_band():
    with pytest.raises(ValueError):
        build_analogy_forecast([{"sku": "A", "points": pts(1, [1])}], widen_factor=0.9)


def test_mean_of_references_aligned_by_calendar_date():
    r = build_analogy_forecast([
        {"sku": "A", "points": pts(1, [10, 10])},
        {"sku": "B", "points": pts(1, [20, 40])},
    ])
    p = by_date(r)
    assert p["2026-01-01"]["value"] == 15.0
    assert p["2026-01-02"]["value"] == 25.0
    assert p["2026-01-01"]["n_references"] == 2


def test_missing_date_is_averaged_over_the_references_that_have_it_never_zero():
    r = build_analogy_forecast([
        {"sku": "A", "points": pts(1, [10, 10, 10])},   # 1,2,3
        {"sku": "B", "points": pts(2, [30, 30])},       # 2,3
    ])
    p = by_date(r)
    assert p["2026-01-01"]["value"] == 10.0 and p["2026-01-01"]["n_references"] == 1
    assert p["2026-01-02"]["value"] == 20.0 and p["2026-01-02"]["n_references"] == 2


def test_date_no_reference_has_is_absent():
    r = build_analogy_forecast([
        {"sku": "A", "points": [{"date": "2026-01-01", "value": 5},
                                {"date": "2026-01-05", "value": 5}]},
    ])
    assert sorted(by_date(r)) == ["2026-01-01", "2026-01-05"]


def test_reference_without_points_is_reported_not_used():
    r = build_analogy_forecast([
        {"sku": "A", "points": pts(1, [10])},
        {"sku": "GHOST", "points": []},
    ])
    assert r["references_used"] == ["A"] and r["references_empty"] == ["GHOST"]
    assert by_date(r)["2026-01-01"]["value"] == 10.0


def test_no_usable_reference_gives_no_points():
    r = build_analogy_forecast([{"sku": "A", "points": [{"date": "bad", "value": 1}]}])
    assert r["points"] == [] and r["references_used"] == []


def test_references_that_disagree_widen_the_band():
    agree = build_analogy_forecast([
        {"sku": "A", "points": pts(1, [20], band=2)},
        {"sku": "B", "points": pts(1, [20], band=2)},
    ])
    disagree = build_analogy_forecast([
        {"sku": "A", "points": pts(1, [5], band=2)},
        {"sku": "B", "points": pts(1, [35], band=2)},
    ])
    assert disagree["points"][0]["value"] == 20.0 == agree["points"][0]["value"]
    assert (disagree["points"][0]["upper"] - 20) > (agree["points"][0]["upper"] - 20)
    # sample sd of 5 and 35 is ~21.2; the disagreement alone is Z * sd wide
    assert disagree["points"][0]["upper"] - 20 >= Z * 21.2 - 0.1


def test_floor_when_references_carry_no_band():
    r = build_analogy_forecast([{"sku": "A", "points": pts(1, [100])}])
    p = r["points"][0]
    assert p["upper"] == pytest.approx(100 + Z * MIN_RELATIVE_SIGMA * 100, abs=1e-3)
    assert p["q90"] == p["upper"] and p["lower"] < 100


def test_lower_edge_never_negative():
    r = build_analogy_forecast([{"sku": "A", "points": pts(1, [1], band=50)}])
    assert r["points"][0]["lower"] == 0.0 and r["points"][0]["q10"] == 0.0


def test_start_date_aligns_by_periods_since_launch():
    # Two references whose forecasts begin on different calendar days land on
    # the same new-product periods.
    r = build_analogy_forecast([
        {"sku": "A", "points": pts(1, [10, 20])},
        {"sku": "B", "points": pts(10, [30, 40])},
    ], start_date="2026-03-01")
    p = by_date(r)
    assert r["alignment"] == "since_start"
    assert p["2026-03-01"]["value"] == 20.0      # (10 + 30) / 2
    assert p["2026-03-02"]["value"] == 30.0      # (20 + 40) / 2
    assert "2026-01-01" not in p


def test_start_date_keeps_spacing_of_the_reference():
    r = build_analogy_forecast(
        [{"sku": "A", "points": [{"date": "2026-01-01", "value": 1},
                                 {"date": "2026-01-08", "value": 2}]}],
        start_date="2026-06-01")
    assert sorted(by_date(r)) == ["2026-06-01", "2026-06-08"]


@pytest.mark.parametrize("scale", [0.05, 10.5, 0, -1, "x", None, float("nan")])
def test_scale_factor_out_of_range_refused(scale):
    with pytest.raises(ValueError):
        build_analogy_forecast([{"sku": "A", "points": pts(1, [1])}], scale_factor=scale)


@pytest.mark.parametrize("n", [0, 6])
def test_reference_count_bounds(n):
    refs = [{"sku": f"R{i}", "points": pts(1, [1])} for i in range(n)]
    with pytest.raises(ValueError):
        build_analogy_forecast(refs)


def test_bad_start_date_refused():
    with pytest.raises(ValueError):
        build_analogy_forecast([{"sku": "A", "points": pts(1, [1])}], start_date="soon")


def test_inputs_not_mutated_and_negative_values_clipped():
    ref = pts(1, [-5, 4], band=1)
    snapshot = [dict(p) for p in ref]
    r = build_analogy_forecast([{"sku": "A", "points": ref}])
    assert ref == snapshot
    assert by_date(r)["2026-01-01"]["value"] == 0.0
