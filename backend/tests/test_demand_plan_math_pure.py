"""Demand plan versions, the pure half (backend/inventory/demand_plan_math.py).
No database, no conftest: every number below is computed by hand."""
from datetime import date, timedelta

import pytest

from backend.errors import AppError
from backend.inventory import demand_plan_math as pm
from backend.inventory.series import SERIES_SEPARATOR as SEP

TODAY = date(2026, 10, 5)          # a Monday


def _days(start: date, n: int, value: float = 10.0) -> dict:
    return {(start + timedelta(days=i)).isoformat(): value for i in range(n)}


def _adj(pct, start, end, aid="adj1"):
    return {"id": aid, "start_date": start, "end_date": end, "pct": pct, "mode": "percent",
            "reason_code": "promotion", "reason_note": None, "created_by": "u1",
            "created_by_name": "Ana"}


def _com(qty, delivery, prob=1.0, cid="c1", wh=None):
    return {"id": cid, "delivery_date": delivery, "quantity": qty, "probability": prob,
            "customer": "ACME", "warehouse_id": wh}


def _code(fn, *a, **kw):
    with pytest.raises(AppError) as e:
        fn(*a, **kw)
    return e.value


# ── period_spans ─────────────────────────────────────────────────────────────

class TestPeriodSpans:

    def test_daily_periods_are_their_own_day(self):
        spans = pm.period_spans(["2026-10-05", "2026-10-06"])
        assert spans == [(date(2026, 10, 5), date(2026, 10, 6)),
                         (date(2026, 10, 6), date(2026, 10, 7))]

    def test_month_start_labels_cover_the_whole_month(self):
        spans = pm.period_spans(["2026-01-01", "2026-02-01", "2026-03-01"])
        assert spans[1] == (date(2026, 2, 1), date(2026, 3, 1))
        assert spans[2] == (date(2026, 3, 1), date(2026, 4, 1))

    def test_december_rolls_into_next_year(self):
        assert pm.period_spans(["2026-12-01"], "monthly") == [(date(2026, 12, 1), date(2027, 1, 1))]

    def test_month_end_labels_are_the_month_they_close(self):
        spans = pm.period_spans(["2026-01-31", "2026-02-28"])
        assert spans == [(date(2026, 1, 1), date(2026, 2, 1)),
                         (date(2026, 2, 1), date(2026, 3, 1))]

    def test_weekly_sunday_labels_end_the_week(self):
        # 2026-10-11 is a Sunday: the week Monday 5th .. Sunday 11th.
        spans = pm.period_spans(["2026-10-11", "2026-10-18"])
        assert spans[0] == (date(2026, 10, 5), date(2026, 10, 12))
        assert spans[1] == (date(2026, 10, 12), date(2026, 10, 19))

    def test_weekly_labels_on_another_weekday_start_the_week(self):
        spans = pm.period_spans(["2026-10-05", "2026-10-12"])
        assert spans[0] == (date(2026, 10, 5), date(2026, 10, 12))

    def test_a_single_label_takes_its_length_from_the_granularity(self):
        assert pm.period_spans(["2026-10-11"], "weekly") == [(date(2026, 10, 5), date(2026, 10, 12))]
        assert pm.period_spans(["2026-10-05"]) == [(date(2026, 10, 5), date(2026, 10, 6))]

    def test_empty(self):
        assert pm.period_spans([]) == []


# ── build_snapshot ───────────────────────────────────────────────────────────

class TestBuildSnapshot:

    def test_no_adjustment_no_commitment_plan_equals_forecast(self):
        snap = pm.build_snapshot({"A": _days(TODAY, 5, 10.0)}, {}, {}, today=TODAY)
        line = snap["skus"]["A"]
        assert line["f"] == [10.0] * 5 and line["p"] == [10.0] * 5
        assert line["a"] == [0.0] * 5 and line["c"] == [0.0] * 5
        assert line["adj"] == [] and line["com"] == []
        assert snap["totals"]["plan"] == 50.0 == snap["totals"]["forecast"]
        assert snap["totals"]["adjustment"] == 0.0 and snap["totals"]["committed"] == 0.0

    def test_stores_are_summed_into_their_sku(self):
        fc = {f"A{SEP}S1": _days(TODAY, 2, 3.0), f"A{SEP}S2": _days(TODAY, 2, 4.0)}
        snap = pm.build_snapshot(fc, {}, {}, today=TODAY)
        assert list(snap["skus"]) == ["A"]
        assert snap["skus"]["A"]["f"] == [7.0, 7.0]

    def test_periods_already_over_are_not_planned(self):
        snap = pm.build_snapshot({"A": _days(TODAY - timedelta(days=3), 6)}, {}, {}, today=TODAY)
        assert snap["periods"][0] == TODAY.isoformat()
        assert len(snap["periods"]) == 3
        assert snap["anchor"] == TODAY.isoformat()

    def test_the_current_week_is_planned_even_though_it_started(self):
        # Week labelled Sunday 11th spans Mon 5th..Sun 11th; "today" is Wed 7th.
        fc = {"A": {"2026-10-04": 70.0, "2026-10-11": 70.0, "2026-10-18": 70.0}}
        snap = pm.build_snapshot(fc, {}, {}, today=date(2026, 10, 7))
        assert snap["periods"] == ["2026-10-11", "2026-10-18"]
        assert snap["starts"] == ["2026-10-05", "2026-10-12"]

    def test_horizon_cuts_the_periods(self):
        snap = pm.build_snapshot({"A": _days(TODAY, 10)}, {}, {}, today=TODAY, horizon_periods=4)
        assert len(snap["periods"]) == 4 and snap["totals"]["plan"] == 40.0

    def test_a_percentage_adjustment_moves_the_days_it_covers(self):
        adj = _adj(50.0, TODAY + timedelta(days=1), TODAY + timedelta(days=2))
        snap = pm.build_snapshot({"A": _days(TODAY, 4, 10.0)}, {"A": [adj]}, {}, today=TODAY)
        line = snap["skus"]["A"]
        assert line["a"] == [0.0, 5.0, 5.0, 0.0]
        assert line["p"] == [10.0, 15.0, 15.0, 10.0]
        assert line["adj"] == ["adj1"]
        assert snap["totals"]["adjustment"] == 10.0

    def test_an_adjustment_covering_part_of_a_week_is_blended_like_the_planner(self):
        # +70% on 3 of the week's 7 days -> x(1 + 3/7*0.7) = x1.3
        adj = _adj(70.0, date(2026, 10, 5), date(2026, 10, 7))
        snap = pm.build_snapshot({"A": {"2026-10-11": 100.0}}, {"A": [adj]}, {},
                                 today=TODAY, granularity="weekly")
        assert snap["skus"]["A"]["a"] == [pytest.approx(30.0)]
        assert snap["skus"]["A"]["p"] == [pytest.approx(130.0)]

    def test_an_adjustment_on_another_sku_changes_nothing(self):
        adj = _adj(50.0, TODAY, TODAY + timedelta(days=9))
        snap = pm.build_snapshot({"A": _days(TODAY, 3)}, {"B": [adj]}, {}, today=TODAY)
        assert snap["skus"]["A"]["p"] == [10.0] * 3

    def test_commitments_land_in_the_period_of_their_delivery(self):
        coms = [_com(100, TODAY + timedelta(days=2), prob=0.5, cid="half"),
                _com(30, TODAY - timedelta(days=4), cid="late"),      # overdue -> first
                _com(999, TODAY + timedelta(days=10), cid="far")]     # past the horizon
        snap = pm.build_snapshot({"A": _days(TODAY, 4, 10.0)}, {}, {"A": coms}, today=TODAY)
        line = snap["skus"]["A"]
        assert line["c"] == [30.0, 0.0, 50.0, 0.0]
        assert line["p"] == [40.0, 10.0, 60.0, 10.0]
        assert sorted(line["com"]) == ["half", "late"]
        assert snap["totals"]["committed"] == 80.0

    def test_a_commitment_inside_a_month_lands_in_that_month(self):
        fc = {"A": {"2026-10-01": 100.0, "2026-11-01": 100.0, "2026-12-01": 100.0}}
        snap = pm.build_snapshot(fc, {}, {"A": [_com(40, date(2026, 11, 30))]}, today=TODAY)
        assert snap["skus"]["A"]["c"] == [0.0, 40.0, 0.0]

    def test_a_sku_with_only_commitments_is_planned_without_a_forecast(self):
        snap = pm.build_snapshot({"A": _days(TODAY, 3)}, {},
                                 {"NEW": [_com(25, TODAY + timedelta(days=1))]}, today=TODAY)
        line = snap["skus"]["NEW"]
        assert line["f"] == [None, None, None]
        assert line["p"] == [0.0, 25.0, 0.0]
        assert snap["totals"]["skus_without_forecast"] == 1
        assert snap["totals"]["sku_count"] == 2

    def test_by_period_totals_add_up(self):
        snap = pm.build_snapshot({"A": _days(TODAY, 2, 1.0), "B": _days(TODAY, 2, 2.0)},
                                 {}, {"B": [_com(5, TODAY)]}, today=TODAY)
        by = snap["totals"]["by_period"]
        assert [r["plan"] for r in by] == [8.0, 3.0]
        assert sum(r["plan"] for r in by) == snap["totals"]["plan"]

    def test_refusals_carry_codes(self):
        assert _code(pm.build_snapshot, {}, {}, {}, today=TODAY).code == "demand_plan_no_forecast"
        past = _code(pm.build_snapshot, {"A": _days(TODAY - timedelta(days=10), 3)}, {}, {},
                     today=TODAY)
        assert past.code == "demand_plan_no_future_periods" and past.status_code == 409
        bad = _code(pm.build_snapshot, {"A": _days(TODAY, 3)}, {}, {}, today=TODAY,
                    horizon_periods=0)
        assert bad.code == "demand_plan_horizon_invalid"

    def test_too_many_cells_is_refused_not_cut(self, monkeypatch):
        monkeypatch.setattr(pm, "MAX_CELLS", 5)
        err = _code(pm.build_snapshot, {"A": _days(TODAY, 3), "B": _days(TODAY, 3)}, {}, {},
                    today=TODAY)
        assert err.code == "demand_plan_too_large"
        assert err.params == {"skus": 2, "periods": 3, "cells": 6, "max_cells": 5}
        # A horizon that fits passes.
        assert pm.build_snapshot({"A": _days(TODAY, 3), "B": _days(TODAY, 3)}, {}, {},
                                 today=TODAY, horizon_periods=2)["totals"]["sku_count"] == 2

    def test_non_finite_forecast_values_are_ignored(self):
        snap = pm.build_snapshot({"A": {TODAY.isoformat(): float("nan"),
                                        (TODAY + timedelta(days=1)).isoformat(): 4.0}},
                                 {}, {}, today=TODAY)
        assert snap["periods"] == [(TODAY + timedelta(days=1)).isoformat()]


# ── line_rows ────────────────────────────────────────────────────────────────

class TestLineRows:

    def _snap(self):
        return pm.build_snapshot({"small": _days(TODAY, 2, 1.0), "BIG": _days(TODAY, 2, 9.0)},
                                 {}, {}, today=TODAY)

    def test_biggest_plan_first_and_filter_by_sku(self):
        rows = pm.line_rows(self._snap())
        assert [r["sku"] for r in rows["items"]] == ["BIG", "small"]
        assert rows["items"][0]["plan"] == 18.0 and rows["total"] == 2
        only = pm.line_rows(self._snap(), q="sma")
        assert [r["sku"] for r in only["items"]] == ["small"]

    def test_paging(self):
        page = pm.line_rows(self._snap(), offset=1, limit=1)
        assert [r["sku"] for r in page["items"]] == ["small"] and page["total"] == 2


# ── diff_versions ────────────────────────────────────────────────────────────

class TestDiff:

    def test_biggest_change_first_over_common_periods(self):
        a = pm.build_snapshot({"A": _days(TODAY, 3, 10.0), "B": _days(TODAY, 3, 10.0)},
                              {}, {}, today=TODAY)
        b = pm.build_snapshot({"A": _days(TODAY, 4, 12.0), "B": _days(TODAY, 4, 5.0),
                               "C": _days(TODAY, 4, 1.0)}, {}, {}, today=TODAY)
        d = pm.diff_versions(a, b)
        assert d["status"] == "ok" and d["n_common_periods"] == 3
        assert d["periods_only_in_b"] == 1
        assert [(r["sku"], r["change"]) for r in d["items"]] == [("B", -15.0), ("A", 6.0), ("C", 3.0)]
        assert d["items"][1]["change_pct"] == 20.0
        assert d["items"][2]["only_in"] == "b" and d["items"][2]["change_pct"] is None
        assert d["total_a"] == 60.0 and d["total_b"] == 54.0
        assert d["skus_only_in_b"] == 1 and d["n_skus_changed"] == 3

    def test_identical_versions_have_no_changes(self):
        a = pm.build_snapshot({"A": _days(TODAY, 3)}, {}, {}, today=TODAY)
        d = pm.diff_versions(a, a)
        assert d["items"] == [] and d["n_skus_changed"] == 0 and d["total_a"] == d["total_b"]

    def test_no_common_periods(self):
        a = pm.build_snapshot({"A": _days(TODAY, 2)}, {}, {}, today=TODAY)
        b = pm.build_snapshot({"A": _days(TODAY + timedelta(days=5), 2)}, {}, {}, today=TODAY)
        d = pm.diff_versions(a, b)
        assert d["status"] == "no_common_periods" and d["items"] == []


# ── compare_with_actuals ─────────────────────────────────────────────────────

class TestCompare:

    def _snap(self):
        # Plan: A forecast 10/day, +50% on the first 5 days; B commitment-only.
        adj = _adj(50.0, TODAY, TODAY + timedelta(days=4))
        return pm.build_snapshot({"A": _days(TODAY, 8, 10.0)}, {"A": [adj]},
                                 {"B": [_com(7, TODAY + timedelta(days=1))]}, today=TODAY)

    def test_nothing_is_graded_before_a_period_ends(self):
        out = pm.compare_with_actuals(self._snap(), {"A": {TODAY.isoformat(): 15.0}}, TODAY)
        assert out["status"] == "periods_not_passed"
        assert out["aggregate"] is None and out["first_period_end"] == "2026-10-06"

    def test_passed_periods_without_sales_are_counted_not_zeroed(self):
        out = pm.compare_with_actuals(self._snap(), {}, TODAY + timedelta(days=2))
        assert out["status"] == "no_actuals"
        assert out["periods_passed"] == 2 and out["skipped_no_actual"] == 2
        assert out["skipped_no_forecast"] == 2          # B, both passed days

    def test_plan_and_model_are_graded_on_the_same_points(self):
        actual = {"A": {(TODAY + timedelta(days=i)).isoformat(): v
                        for i, v in enumerate([15, 15, 15, 15, 15, 10])}}
        # Six passed days: plan 15,15,15,15,15,10 (exact); model 10 every day.
        out = pm.compare_with_actuals(self._snap(), actual, TODAY + timedelta(days=6))
        assert out["status"] == "ok" and out["periods_compared"] == 6
        agg = out["aggregate"]
        assert agg["n_points"] == 6 and agg["actual_total"] == 85.0
        assert agg["plan_error"] == 0.0 and agg["model_error"] == 25.0
        assert agg["plan_wape"] == 0.0
        assert agg["model_wape"] == pytest.approx(25 / 85, abs=1e-4)
        assert agg["model_bias"] == pytest.approx(-25 / 85, abs=1e-4)
        assert agg["plan_bias"] == 0.0
        assert agg["improvement_pct"] == 100.0 and agg["verdict"] == "improved"
        assert agg["better_points"] == 5 and agg["worse_points"] == 0
        assert [r["sku"] for r in out["by_sku"]] == ["A"]

    def test_a_consensus_that_ran_high_is_called_worse(self):
        actual = {"A": {(TODAY + timedelta(days=i)).isoformat(): 10.0 for i in range(6)}}
        out = pm.compare_with_actuals(self._snap(), actual, TODAY + timedelta(days=6))
        agg = out["aggregate"]
        assert agg["model_error"] == 0.0 and agg["plan_error"] == 25.0
        assert agg["plan_bias"] == pytest.approx(25 / 60, abs=1e-4)
        assert agg["improvement_pct"] is None      # the model was exact: no ratio
        # ...and yet the plan missed: never "neutral" (ForecastingCore's answer).
        assert agg["verdict"] == "worsened"

    def test_both_exact_stays_neutral(self):
        actual = {"A": {(TODAY + timedelta(days=i)).isoformat(): 10.0 for i in range(5, 8)}}
        out = pm.compare_with_actuals(self._snap(), actual, TODAY + timedelta(days=8))
        assert out["aggregate"]["n_points"] == 3
        assert out["aggregate"]["plan_error"] == 0.0 == out["aggregate"]["model_error"]
        assert out["aggregate"]["verdict"] == "too_little"   # 3 points < 5

    def test_actuals_by_sku_sums_stores(self):
        out = pm.actuals_by_sku({f"A{SEP}S1": {"2026-10-05": 2.0},
                                 f"A{SEP}S2": {"2026-10-05": 3.0}, "B": {"2026-10-05": None}})
        assert out == {"A": {"2026-10-05": 5.0}, "B": {}}
