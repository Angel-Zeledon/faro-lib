"""
MinT reconciliation.

The property that distinguishes it from what was already here: bottom-up and
top-down cannot improve the level the purchase order is written at. Bottom-up
leaves every leaf exactly as it found it; top-down overwrites the leaves with a
share of the total and discards what their own models learned. MinT mixes the
levels, so a category forecast estimated on ten times the data can correct the
SKUs beneath it.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.hierarchy import HierarchicalReconciler


def _frame(rows):
    return pd.DataFrame(rows)


def _two_leaves(cat_value, a_value, b_value, date="2026-07-01"):
    """One category and its two SKUs, forecast independently and incoherently."""
    return _frame([
        {"category": "C1", "sku": None, "date": pd.Timestamp(date), "forecast": cat_value},
        {"category": "C1", "sku": "A", "date": pd.Timestamp(date), "forecast": a_value},
        {"category": "C1", "sku": "B", "date": pd.Timestamp(date), "forecast": b_value},
    ])


class TestCoherence:

    def test_reconciled_leaves_sum_to_the_reconciled_aggregate(self):
        """The defining property: after reconciliation the hierarchy adds up."""
        df = _two_leaves(cat_value=100.0, a_value=30.0, b_value=40.0)
        out = HierarchicalReconciler(["category", "sku"]).mint(df)

        agg = out[out["sku"].isna()]["forecast"].iloc[0]
        leaves = out[out["sku"].notna()]["forecast"].sum()
        assert leaves == pytest.approx(agg, rel=1e-6), (
            f"incoherent after reconciliation: leaves={leaves:.3f} agg={agg:.3f}"
        )

    def test_an_already_coherent_hierarchy_is_left_alone(self):
        df = _two_leaves(cat_value=70.0, a_value=30.0, b_value=40.0)
        out = HierarchicalReconciler(["category", "sku"]).mint(df)
        assert out["forecast"].tolist() == pytest.approx([70.0, 30.0, 40.0], rel=1e-6)

    def test_each_date_is_reconciled_independently(self):
        df = pd.concat([
            _two_leaves(100.0, 30.0, 40.0, date="2026-07-01"),
            _two_leaves(200.0, 60.0, 80.0, date="2026-07-02"),
        ], ignore_index=True)
        out = HierarchicalReconciler(["category", "sku"]).mint(df)
        for _date, block in out.groupby("date"):
            agg = block[block["sku"].isna()]["forecast"].iloc[0]
            assert block[block["sku"].notna()]["forecast"].sum() == pytest.approx(agg, rel=1e-6)


class TestItImprovesTheLeaves:

    def test_leaves_move(self):
        """Bottom-up would leave these three numbers untouched at 30 and 40."""
        df = _two_leaves(cat_value=100.0, a_value=30.0, b_value=40.0)
        out = HierarchicalReconciler(["category", "sku"]).mint(df)
        leaves = out[out["sku"].notna()]["forecast"].tolist()
        assert leaves != pytest.approx([30.0, 40.0]), (
            "MinT left the leaves unchanged — that is bottom-up, not MinT"
        )
        assert all(v > 0 for v in leaves)

    def test_bottom_up_by_contrast_does_not_touch_the_leaves(self):
        """Stated as a test so the difference is not a matter of opinion."""
        df = _two_leaves(cat_value=100.0, a_value=30.0, b_value=40.0)
        out = HierarchicalReconciler(["category", "sku"]).bottom_up(df)
        leaves = out[out["sku"].notna()]["forecast"].tolist()
        assert leaves == pytest.approx([30.0, 40.0])

    def test_a_trusted_leaf_moves_less_than_a_noisy_one(self):
        """
        The point of the W matrix. Leaf A has small residuals, B has large ones,
        so the correction needed to reach coherence should land mostly on B.
        """
        df = _two_leaves(cat_value=100.0, a_value=30.0, b_value=40.0)
        residuals = {
            "A": np.random.default_rng(1).normal(0, 0.1, 200),
            "B": np.random.default_rng(2).normal(0, 10.0, 200),
            "C1": np.random.default_rng(3).normal(0, 1.0, 200),
        }
        out = HierarchicalReconciler(["category", "sku"]).mint(df, residuals=residuals)
        moved_a = abs(out[out["sku"] == "A"]["forecast"].iloc[0] - 30.0)
        moved_b = abs(out[out["sku"] == "B"]["forecast"].iloc[0] - 40.0)
        assert moved_b > moved_a, (
            f"the noisy leaf should absorb the correction: A moved {moved_a:.3f}, "
            f"B moved {moved_b:.3f}"
        )


class TestDegradation:

    def test_no_leaf_column_returns_input(self):
        df = _frame([{"category": "C1", "date": pd.Timestamp("2026-07-01"),
                      "forecast": 10.0}])
        out = HierarchicalReconciler(["category", "sku"]).mint(df)
        pd.testing.assert_frame_equal(out, df)

    def test_single_level_is_a_no_op(self):
        df = _two_leaves(100.0, 30.0, 40.0)
        out = HierarchicalReconciler(["sku"]).mint(df)
        pd.testing.assert_frame_equal(out, df)

    def test_single_leaf_block_is_left_alone(self):
        df = _frame([
            {"category": "C1", "sku": None, "date": pd.Timestamp("2026-07-01"),
             "forecast": 100.0},
            {"category": "C1", "sku": "A", "date": pd.Timestamp("2026-07-01"),
             "forecast": 30.0},
        ])
        out = HierarchicalReconciler(["category", "sku"]).mint(df)
        assert out["forecast"].tolist() == [100.0, 30.0]

    def test_reconciled_values_are_never_negative(self):
        """A projection can go below zero; demand cannot."""
        df = _two_leaves(cat_value=1.0, a_value=90.0, b_value=95.0)
        out = HierarchicalReconciler(["category", "sku"]).mint(df)
        assert (out["forecast"] >= 0).all()

    def test_missing_residuals_fall_back_to_the_pooled_variance(self):
        df = _two_leaves(cat_value=100.0, a_value=30.0, b_value=40.0)
        out = HierarchicalReconciler(["category", "sku"]).mint(
            df, residuals={"A": np.random.default_rng(0).normal(0, 1, 50)},
        )
        agg = out[out["sku"].isna()]["forecast"].iloc[0]
        assert out[out["sku"].notna()]["forecast"].sum() == pytest.approx(agg, rel=1e-6)
