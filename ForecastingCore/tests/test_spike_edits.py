"""Manual spike exclusions applied as a data-cleaning step (pure logic, hand-computed)."""

import numpy as np
import pandas as pd

from forecasting_core.data.spike_edits import (
    STATUS_APPLIED, STATUS_NO_BASELINE, STATUS_NO_MATCH,
    apply_spike_exclusions, summarize_by_sku,
)


def _frame(values, sku="A", start="2026-01-01", store=None):
    n = len(values)
    d = {"date": pd.date_range(start, periods=n, freq="D"), "sku": sku, "qty": values}
    if store:
        d["store"] = store
    return pd.DataFrame(d)


def _ex(sku, start, end, id="e1"):
    return {"id": id, "sku": sku, "start_date": start, "end_date": end}


def test_no_exclusions_returns_the_same_frame():
    df = _frame([1.0, 2.0, 3.0])
    out, report = apply_spike_exclusions(df, "date", "qty", ["sku"], [])
    assert out is df and report == []


def test_spike_is_replaced_by_the_median_of_its_neighbours():
    # 4 clean days of 10, a 500 spike, 4 clean days of 20 -> median of the
    # eight neighbours (10,10,10,10,20,20,20,20) is 15.
    df = _frame([10.0] * 4 + [500.0] + [20.0] * 4)
    out, report = apply_spike_exclusions(
        df, "date", "qty", ["sku"], [_ex("A", "2026-01-05", "2026-01-05")])
    assert out["qty"].tolist() == [10.0] * 4 + [15.0] + [20.0] * 4
    assert report[0]["status"] == STATUS_APPLIED
    assert report[0]["points_treated"] == 1
    assert report[0]["original_total"] == 500.0
    assert report[0]["replacement_total"] == 15.0


def test_the_input_frame_is_never_mutated():
    df = _frame([10.0, 10.0, 900.0, 10.0, 10.0])
    before = df.copy(deep=True)
    apply_spike_exclusions(df, "date", "qty", ["sku"], [_ex("A", "2026-01-03", "2026-01-03")])
    pd.testing.assert_frame_equal(df, before)


def test_a_two_day_period_ignores_the_other_excluded_day_in_the_estimate():
    # Both 800s are excluded; the estimate must come from the 5s only (not 800).
    df = _frame([5.0, 5.0, 800.0, 800.0, 5.0, 5.0])
    out, _ = apply_spike_exclusions(
        df, "date", "qty", ["sku"], [_ex("A", "2026-01-03", "2026-01-04")])
    assert out["qty"].tolist() == [5.0] * 6


def test_other_products_and_dates_are_untouched():
    a = _frame([10.0, 10.0, 300.0, 10.0, 10.0], sku="A")
    b = _frame([10.0, 10.0, 300.0, 10.0, 10.0], sku="B")
    df = pd.concat([a, b], ignore_index=True)
    out, report = apply_spike_exclusions(
        df, "date", "qty", ["sku"], [_ex("A", "2026-01-03", "2026-01-03")])
    assert out.loc[out.sku == "B", "qty"].tolist() == [10.0, 10.0, 300.0, 10.0, 10.0]
    assert out.loc[out.sku == "A", "qty"].tolist() == [10.0] * 5
    assert report[0]["points_treated"] == 1


def test_every_warehouse_of_the_product_is_treated_against_its_own_neighbours():
    north = _frame([10.0, 10.0, 400.0, 10.0, 10.0], store="N")
    south = _frame([100.0, 100.0, 900.0, 100.0, 100.0], store="S")
    df = pd.concat([north, south], ignore_index=True)
    out, report = apply_spike_exclusions(
        df, "date", "qty", ["sku", "store"], [_ex("A", "2026-01-03", "2026-01-03")])
    assert out.loc[out.store == "N", "qty"].tolist() == [10.0] * 5
    assert out.loc[out.store == "S", "qty"].tolist() == [100.0] * 5
    assert report[0]["points_treated"] == 2
    assert report[0]["original_total"] == 1300.0


def test_a_period_with_no_data_is_reported_not_silent():
    df = _frame([1.0, 2.0, 3.0])
    out, report = apply_spike_exclusions(
        df, "date", "qty", ["sku"], [_ex("A", "2030-01-01", "2030-01-31"),
                                     _ex("ZZZ", "2026-01-01", "2026-01-03", id="e2")])
    assert out is df
    assert [r["status"] for r in report] == [STATUS_NO_MATCH, STATUS_NO_MATCH]


def test_a_series_that_is_entirely_excluded_is_left_alone():
    df = _frame([50.0, 60.0, 70.0])
    out, report = apply_spike_exclusions(
        df, "date", "qty", ["sku"], [_ex("A", "2026-01-01", "2026-01-03")])
    assert out["qty"].tolist() == [50.0, 60.0, 70.0]
    assert report[0]["status"] == STATUS_NO_BASELINE
    assert report[0]["points_treated"] == 0


def test_empty_cells_are_not_invented():
    df = _frame([10.0, 10.0, np.nan, 10.0, 10.0])
    out, report = apply_spike_exclusions(
        df, "date", "qty", ["sku"], [_ex("A", "2026-01-03", "2026-01-03")])
    assert np.isnan(out["qty"].iloc[2])
    assert report[0]["points_treated"] == 0


def test_integer_quantities_survive_and_other_rows_keep_their_values():
    df = _frame([3, 3, 99, 3, 3])
    out, _ = apply_spike_exclusions(df, "date", "qty", ["sku"], [_ex("A", "2026-01-03", "2026-01-03")])
    assert out["qty"].tolist() == [3.0] * 5


def test_overlapping_exclusions_treat_a_row_once():
    df = _frame([10.0, 10.0, 500.0, 10.0, 10.0])
    out, report = apply_spike_exclusions(
        df, "date", "qty", ["sku"],
        [_ex("A", "2026-01-02", "2026-01-03", id="e1"), _ex("A", "2026-01-03", "2026-01-04", id="e2")])
    assert out["qty"].tolist() == [10.0] * 5
    assert report[0]["points_treated"] + report[1]["points_treated"] == 3  # rows 2,3,4 once each


def test_no_group_column_changes_nothing():
    df = _frame([1.0, 900.0, 1.0])
    out, report = apply_spike_exclusions(df, "date", "qty", [], [_ex("A", "2026-01-02", "2026-01-02")])
    assert out is df and report[0]["status"] == STATUS_NO_MATCH


def test_summary_lists_unmatched_marks_too():
    report = [
        {"id": "1", "sku": "A", "status": STATUS_APPLIED, "points_treated": 2,
         "original_total": 100.0, "replacement_total": 20.0},
        {"id": "2", "sku": "A", "status": STATUS_NO_MATCH, "points_treated": 0,
         "original_total": 0.0, "replacement_total": 0.0},
        {"id": "3", "sku": "B", "status": STATUS_NO_MATCH, "points_treated": 0,
         "original_total": 0.0, "replacement_total": 0.0},
    ]
    s = {x["sku"]: x for x in summarize_by_sku(report)}
    assert s["A"]["points_treated"] == 2 and s["A"]["exclusions"] == 2 and s["A"]["unmatched"] == 1
    assert s["B"]["points_treated"] == 0 and s["B"]["unmatched"] == 1
