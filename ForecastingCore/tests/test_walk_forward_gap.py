"""
The walk-forward gap: training data must stop `horizon` buckets before the
window it is scored on.

Without the gap the model is fitted on the day immediately before the day it is
graded on, which is information no production forecast can have. The score that
comes back is real, it just answers an easier question than the product does.
"""

import numpy as np
import pytest

from forecasting_core.training.trainer import WalkForwardSplitter


class TestGapGeometry:

    def test_no_gap_keeps_train_adjacent_to_test(self):
        splits = WalkForwardSplitter(n_splits=3, min_train_ratio=0.4, gap=0).split(100)
        assert splits
        for tr, te in splits:
            assert tr[-1] + 1 == te[0], "gap=0 must leave train touching test"

    def test_gap_withholds_exactly_gap_observations(self):
        gap = 7
        splits = WalkForwardSplitter(n_splits=3, min_train_ratio=0.4, gap=gap).split(200)
        assert splits
        for tr, te in splits:
            assert te[0] - tr[-1] - 1 == gap, (
                f"expected {gap} withheld buckets, got {te[0] - tr[-1] - 1}"
            )

    def test_gap_never_leaks_into_training(self):
        splits = WalkForwardSplitter(n_splits=3, min_train_ratio=0.4, gap=14).split(200)
        for tr, te in splits:
            assert set(tr).isdisjoint(set(te))
            assert tr.max() < te.min() - 14

    def test_test_windows_are_unchanged_by_the_gap(self):
        """Only the training side shrinks — the evaluation set must not move,
        or scores before and after this change would not be comparable."""
        without = WalkForwardSplitter(3, 0.4, 0).split(200)
        with_gap = WalkForwardSplitter(3, 0.4, 10).split(200)
        assert len(without) == len(with_gap)
        for (_, te_a), (_, te_b) in zip(without, with_gap):
            assert np.array_equal(te_a, te_b)


class TestGapDegradation:

    def test_effective_gap_backs_off_on_short_series(self):
        """A 30-row series with a 14-bucket horizon cannot fund the full gap."""
        splitter = WalkForwardSplitter(n_splits=3, min_train_ratio=0.4, gap=14)
        n = 30
        effective = splitter.effective_gap(n)
        assert effective < 14
        assert WalkForwardSplitter(3, 0.4, effective).split(n), (
            "the backed-off gap must actually yield folds"
        )

    def test_effective_gap_is_the_full_gap_when_affordable(self):
        splitter = WalkForwardSplitter(n_splits=3, min_train_ratio=0.4, gap=14)
        assert splitter.effective_gap(500) == 14

    def test_effective_gap_is_zero_when_gap_is_zero(self):
        assert WalkForwardSplitter(3, 0.4, 0).effective_gap(500) == 0


class TestGapChangesTheScore:

    def test_gap_makes_a_drifting_series_measurably_harder(self):
        """The whole justification, asserted rather than argued.

        The gap's mechanism is recency: it takes away the freshest observations,
        which are the ones an autocorrelated series depends on most. The naive
        last-value forecaster isolates that mechanism exactly — it is nothing
        BUT recency — so on a series with drift a staler training window has to
        score worse. If it did not, the gap would be decoration.
        """
        from forecasting_core.evaluation.metrics import mae

        n = 400
        drift = 0.5
        series = 100.0 + drift * np.arange(n)

        def score(gap):
            errs = []
            for tr, te in WalkForwardSplitter(3, 0.4, gap).split(n):
                last_seen = series[tr[-1]]                     # naive forecast
                errs.append(mae(series[te], np.full(len(te), last_seen)))
            return float(np.mean(errs))

        gapped, adjacent = score(gap=30), score(gap=0)
        assert gapped > adjacent, (
            "withholding the most recent buckets must not make the problem easier"
        )
        # And by roughly the drift it was denied — not a rounding difference.
        assert gapped - adjacent == pytest.approx(drift * 30, rel=0.05)
