"""
Estrategia B must not silently disable censored-demand recovery.

The resampler used to return exactly [date, group, target]. Every other column
was dropped, so a session that aggregated to a common frequency arrived at the
censoring step with no inventory column, `recover_censored_demand` skipped with
reason "no_inventory_column_mapped", and the forecast carried the full stockout
bias while the run reported clean.

Nothing failed. The user mapped their inventory column, the app accepted it, and
the correction it enables never happened.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import SessionConfig
from forecasting_core.data.censoring import recover_censored_demand
from forecasting_core.data.resampler import resample_to_frequency
from forecasting_core.pipelines.pipeline import Pipeline


def _daily(days=84, sku="SKU_A"):
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    return pd.DataFrame({
        "date": dates, "sku": sku,
        "demand": np.full(days, 10.0),
        "inventory": np.full(days, 500.0),
    })


class TestColumnsSurvive:

    def test_target_is_still_summed(self):
        out = resample_to_frequency(_daily(), "date", "sku", "demand", "W")
        assert out["demand"].iloc[0] > 10.0, "a weekly bucket must sum its days"

    def test_requested_column_survives(self):
        out = resample_to_frequency(_daily(), "date", "sku", "demand", "W",
                                    extra_cols={"inventory": "min"})
        assert "inventory" in out.columns

    def test_unrequested_columns_are_still_dropped(self):
        """The default stays narrow; carrying a column is opt-in."""
        df = _daily()
        df["noise"] = 1.0
        out = resample_to_frequency(df, "date", "sku", "demand", "W",
                                    extra_cols={"inventory": "min"})
        assert "noise" not in out.columns

    def test_a_missing_requested_column_does_not_raise(self):
        out = resample_to_frequency(_daily(), "date", "sku", "demand", "W",
                                    extra_cols={"ghost": "min"})
        assert "ghost" not in out.columns
        assert not out.empty

    def test_works_without_a_group_column(self):
        df = _daily().drop(columns=["sku"])
        out = resample_to_frequency(df, "date", None, "demand", "W",
                                    extra_cols={"inventory": "min"})
        assert "inventory" in out.columns


class TestMinIsTheRightAggregation:

    def test_a_stockout_on_any_day_survives_to_the_bucket(self):
        """
        `last` would lose it. A shelf that emptied on Tuesday and was restocked
        on Friday closes the week at 500 units, and the truncated demand of that
        week becomes invisible.
        """
        df = _daily(days=14)
        df.loc[2, "inventory"] = 0.0        # emptied mid-week
        df.loc[2, "demand"] = 0.0
        out = resample_to_frequency(df, "date", "sku", "demand", "W",
                                    extra_cols={"inventory": "min"})
        assert (out["inventory"] <= 0).any(), (
            "the stockout week closed with stock on hand and the bucket lost it"
        )

    def test_last_would_have_missed_it(self):
        """Stated explicitly so the choice of `min` is not mistaken for taste."""
        df = _daily(days=14)
        df.loc[2, ["inventory", "demand"]] = [0.0, 0.0]
        with_last = resample_to_frequency(df, "date", "sku", "demand", "W",
                                          extra_cols={"inventory": "last"})
        assert not (with_last["inventory"] <= 0).any()


class TestEndToEndThroughThePipeline:

    def _config(self, strategy, freq=None):
        return SessionConfig.from_dict({
            "columns": {"target": "demand", "date": "date", "group_keys": ["sku"],
                        "inventory": "inventory"},
            "models": {"lightgbm": {}},
            "granularity": {"strategy": strategy, "target_freq": freq},
        })

    def test_inventory_reaches_the_censoring_step_after_aggregation(self):
        df = _daily(days=120)
        df.loc[40:43, ["inventory", "demand"]] = [0.0, 0.0]

        resampled = Pipeline(self._config("aggregate", "W"))._maybe_resample(df)
        assert "inventory" in resampled.columns, (
            "Estrategia B dropped the inventory column before censoring ran"
        )

        _out, report = recover_censored_demand(
            resampled, "date", "demand", "sku", "inventory",
        )
        assert report.skipped_reason != "no_inventory_column_mapped", (
            "censoring switched itself off on an aggregated session"
        )
        assert report.n_flagged > 0

    def test_native_strategy_is_untouched(self):
        df = _daily()
        out = Pipeline(self._config("native"))._maybe_resample(df)
        pd.testing.assert_frame_equal(out, df)
