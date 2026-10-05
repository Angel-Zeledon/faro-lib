"""compute_session_accuracy: headline accuracy = 1 - WAPE of each SKU's best
real model, demand-weighted. Guards the fix for the old formula that averaged
every model x SKU row INCLUDING naive baselines, which made the KPI disagree
with the per-SKU cards."""

from backend.inventory.service import compute_session_accuracy


def _row(sku, model, wape, type_="ml"):
    return {"sku": sku, "model": model, "wape": wape, "type": type_}


def _item(sku, daily_demand):
    return {"sku": sku, "daily_demand": daily_demand}


class TestBaselineExclusion:
    def test_baseline_rows_do_not_drag_the_number(self):
        rows = [
            _row("A", "lightgbm", 0.10),
            _row("A", "naive", 0.90, type_="baseline"),
        ]
        assert compute_session_accuracy(rows, [_item("A", 5.0)]) == 0.9

    def test_only_baseline_rows_yields_none(self):
        rows = [_row("A", "naive", 0.30, type_="baseline")]
        assert compute_session_accuracy(rows, [_item("A", 5.0)]) is None

    def test_empty_rows_yields_none(self):
        assert compute_session_accuracy([], [_item("A", 5.0)]) is None


class TestBestModelPerSku:
    def test_picks_lowest_wape_model_per_sku(self):
        rows = [
            _row("A", "lightgbm", 0.40),
            _row("A", "prophet", 0.20),
            _row("A", "xgboost", 0.35),
        ]
        # best is 0.20 -> accuracy 0.80 (not the mean 1-0.3167)
        assert compute_session_accuracy(rows, [_item("A", 1.0)]) == 0.8

    def test_rows_without_wape_are_ignored(self):
        rows = [
            _row("A", "lstm", None),
            _row("A", "prophet", 0.25),
        ]
        assert compute_session_accuracy(rows, [_item("A", 1.0)]) == 0.75


class TestDemandWeighting:
    def test_weighted_by_daily_demand(self):
        rows = [
            _row("HIGH", "lightgbm", 0.10),
            _row("LOW", "lightgbm", 0.50),
        ]
        items = [_item("HIGH", 90.0), _item("LOW", 10.0)]
        # weighted wape = (0.1*90 + 0.5*10) / 100 = 0.14
        assert compute_session_accuracy(rows, items) == 0.86

    def test_unweighted_mean_when_no_demand_available(self):
        rows = [
            _row("A", "lightgbm", 0.10),
            _row("B", "lightgbm", 0.50),
        ]
        # No items at all -> plain mean wape 0.30
        assert compute_session_accuracy(rows, []) == 0.7
        # Mixed: A sold nothing, B's demand is UNKNOWN. Real sales cannot be
        # ruled out, so this still reports the plain mean rather than nothing.
        items_partial = [_item("A", 0.0), _item("B", None)]
        assert compute_session_accuracy(rows, items_partial) == 0.7

    def test_sku_missing_from_items_gets_zero_weight_not_crash(self):
        rows = [
            _row("A", "lightgbm", 0.10),
            _row("B", "lightgbm", 0.50),
        ]
        # only A has demand -> B contributes nothing to the weighted average
        assert compute_session_accuracy(rows, [_item("A", 10.0)]) == 0.9


class TestZeroDemandIsNotPerfectAccuracy:
    """WAPE divides by total real demand: a catalog that never sold scores a
    meaningless 0 error, which used to surface as a proud 100%."""

    def test_all_skus_with_zero_demand_report_no_accuracy(self):
        rows = [_row("Z1", "croston", 0.0), _row("Z2", "croston", 0.0)]
        items = [_item("Z1", 0.0), _item("Z2", 0.0)]
        assert compute_session_accuracy(rows, items) is None

    def test_zero_demand_skus_do_not_drag_a_real_catalog(self):
        rows = [_row("REAL", "lightgbm", 0.20), _row("Z1", "croston", 0.0)]
        items = [_item("REAL", 50.0), _item("Z1", 0.0)]
        # Only the SKU with demand carries weight → 1 - 0.20.
        assert compute_session_accuracy(rows, items) == 0.8

    def test_metrics_only_callers_without_items_still_get_a_number(self):
        rows = [_row("A", "lightgbm", 0.10)]
        assert compute_session_accuracy(rows, []) == 0.9


class TestClamping:
    def test_wape_above_one_clamps_accuracy_to_zero(self):
        rows = [_row("A", "lightgbm", 1.5)]
        assert compute_session_accuracy(rows, [_item("A", 1.0)]) == 0.0


def _dead(sku, model, type_="ml"):
    """A row from a validation window that held no demand: 0 error, 0 scale."""
    return {"sku": sku, "model": model, "wape": 0.0, "mae": 0.0, "type": type_}


class TestAnEmptyValidationWindowIsNotPerfectAccuracy:
    """
    WAPE divides by the total real demand in the VALIDATION window. A window
    with no demand gives 0/0 — an error of zero over a scale of zero. That is
    not a perfect forecast; it is the absence of anything to be accurate about.

    Measured on a real session (break_fechas.csv, 1 SKU): all seven models,
    naive baselines included, scored exactly wape=0 mae=0. `daily_demand` was
    unknown, so the demand-weighted branch fell through to the plain mean and
    the purchasing panel announced "Precisión promedio 100.0%" — twice, once as
    a KPI and once in the footer — directly above a suggested order of 130
    units. The SKU screen already refused to print that same number, so the two
    screens were reporting different things about the same session.
    """

    def test_a_zero_over_zero_window_reports_no_accuracy(self):
        rows = [_dead("A", "lightgbm"), _dead("A", "xgboost"),
                _dead("A", "naive", "baseline")]
        assert compute_session_accuracy(rows, [_item("A", None)]) is None

    def test_it_holds_when_the_demand_weight_is_unknown(self):
        """The exact shape measured: demand None, so no weight rules it out."""
        assert compute_session_accuracy([_dead("A", "global_lgbm")], [{"sku": "A"}]) is None

    def test_a_dead_sku_does_not_inflate_a_real_catalog(self):
        rows = [
            {"sku": "REAL", "model": "lightgbm", "wape": 0.20, "mae": 4.0, "type": "ml"},
            _dead("DEAD", "lightgbm"),
        ]
        # Without the guard the dead SKU's 0 pulls the plain mean to 0.10 and
        # reports 90% for a catalog whose only real model scores 80%.
        assert compute_session_accuracy(rows, [{"sku": "REAL"}, {"sku": "DEAD"}]) == 0.8

    def test_a_real_model_with_a_zero_wape_but_real_error_is_kept(self):
        """Only 0/0 is excluded. A tiny error against a huge scale is real."""
        rows = [{"sku": "A", "model": "lightgbm", "wape": 0.0, "mae": 0.4, "type": "ml"}]
        assert compute_session_accuracy(rows, [_item("A", 8.0)]) == 1.0
