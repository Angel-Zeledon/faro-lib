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
