"""
Censored demand recovery.

Two failure modes matter here and they pull in opposite directions:

  * doing nothing — the stockout is learned as a demand collapse and the SKU
    spirals down through reorder after reorder;
  * doing too much — inventing demand from an inventory column the user never
    mapped, which the canonical schema fills with zeros, would flag the entire
    dataset as censored and inflate every forecast in the tenant.

The guard tests below are the more important half.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.data.canonical import apply_canonical_defaults
from forecasting_core.data.censoring import (
    CENSORED_FLAG, MAX_LIFT_FACTOR, recover_censored_demand,
)


def _series(n=90, level=100.0, sku="SKU_A", start="2026-01-01"):
    dates = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "sku": sku,
        "demand": np.full(n, level),
        "inventory": np.full(n, 500.0),
    })


class TestGuards:
    """It must refuse to act on evidence it does not have."""

    def test_no_inventory_column_is_a_no_op(self):
        df = _series().drop(columns=["inventory"])
        out, report = recover_censored_demand(df, "date", "demand", "sku", None)
        assert report.skipped_reason == "no_inventory_column_mapped"
        assert report.n_recovered == 0
        pd.testing.assert_frame_equal(out, df)

    def test_unmapped_canonical_inventory_is_not_treated_as_a_stockout(self):
        """
        The trap: apply_canonical_defaults broadcasts inventory=0 into every
        session where the user did not map one. Reading that as "out of stock
        every single day" would rewrite the whole dataset upward.
        """
        raw = pd.DataFrame({
            "fecha": pd.date_range("2026-01-01", periods=60, freq="D"),
            "producto": "SKU_A",
            "ventas": np.full(60, 40.0),
        })
        canonical = apply_canonical_defaults(
            raw, {"sku": "producto", "date": "fecha", "demand": "ventas"},
        )
        assert (canonical["inventory"] == 0).all(), "fixture assumption"

        out, report = recover_censored_demand(
            canonical, "date", "demand", "sku", "inventory",
        )
        assert report.skipped_reason == "inventory_never_positive"
        assert report.n_recovered == 0
        assert out["demand"].equals(canonical["demand"]), (
            "a dataset with no real inventory data must come back untouched"
        )

    def test_missing_column_name_is_reported_not_raised(self):
        df = _series()
        _out, report = recover_censored_demand(df, "date", "demand", "sku", "ghost")
        assert report.skipped_reason == "inventory_column_missing"

    def test_series_without_enough_clean_history_is_flagged_but_not_altered(self):
        df = _series(n=10)
        df.loc[8:9, "inventory"] = 0.0
        df.loc[8:9, "demand"] = 0.0
        out, report = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert report.n_flagged == 2
        assert report.n_recovered == 0, "too little clean history to estimate from"
        assert out[CENSORED_FLAG].sum() == 2, "still visible to the user"


class TestRecovery:

    def _with_stockout(self, stockout_days=(60, 61, 62)):
        df = _series(n=90, level=100.0)
        for day in stockout_days:
            df.loc[day, "inventory"] = 0.0
            df.loc[day, "demand"] = 0.0        # sold nothing: had nothing
        return df

    def test_a_zero_sales_stockout_is_lifted_to_the_series_level(self):
        df = self._with_stockout()
        out, report = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert report.n_recovered == 3
        recovered = out.loc[[60, 61, 62], "demand"]
        assert (recovered > 50).all(), f"still reads as a collapse: {list(recovered)}"
        assert (recovered <= 100 * MAX_LIFT_FACTOR).all()

    def test_partial_sales_are_never_lowered(self):
        """What sold is a fact; the estimate is only evidence about the rest."""
        df = self._with_stockout(stockout_days=())
        df.loc[70, "inventory"] = 0.0
        df.loc[70, "demand"] = 250.0           # sold out after a huge day
        out, _ = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert out.loc[70, "demand"] == 250.0

    def test_uncensored_rows_are_untouched(self):
        df = self._with_stockout()
        out, _ = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        untouched = [i for i in range(90) if i not in (60, 61, 62)]
        assert np.allclose(out.loc[untouched, "demand"], df.loc[untouched, "demand"])

    def test_lift_is_capped(self):
        """One mis-flagged bucket must not become the outlier that drives the
        whole forecast."""
        df = _series(n=90, level=100.0)
        df.loc[80, "inventory"] = 0.0
        df.loc[80, "demand"] = 0.0
        out, _ = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert out.loc[80, "demand"] <= 100.0 * MAX_LIFT_FACTOR

    def test_weekly_shape_is_respected(self):
        """A Saturday stockout should be recovered to a Saturday's demand."""
        n = 120
        dates = pd.date_range("2026-01-05", periods=n, freq="D")   # starts Monday
        by_dow = {0: 50.0, 1: 50.0, 2: 50.0, 3: 50.0, 4: 50.0, 5: 200.0, 6: 200.0}
        df = pd.DataFrame({
            "date": dates, "sku": "SKU_A",
            "demand": [by_dow[d.dayofweek] for d in dates],
            "inventory": np.full(n, 500.0),
        })
        saturday = next(i for i in range(100, n) if dates[i].dayofweek == 5)
        df.loc[saturday, ["inventory", "demand"]] = [0.0, 0.0]

        out, report = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert report.n_recovered == 1
        assert out.loc[saturday, "demand"] > 100.0, (
            f"recovered a weekend day to a weekday level: "
            f"{out.loc[saturday, 'demand']:.1f}"
        )

    def test_each_sku_is_recovered_from_its_own_history(self):
        small = _series(n=90, level=10.0, sku="SMALL")
        large = _series(n=90, level=1000.0, sku="LARGE")
        for frame in (small, large):
            frame.loc[70, ["inventory", "demand"]] = [0.0, 0.0]
        df = pd.concat([small, large], ignore_index=True)

        out, report = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert report.n_recovered == 2
        got_small = out[(out["sku"] == "SMALL")].iloc[70]["demand"]
        got_large = out[(out["sku"] == "LARGE")].iloc[70]["demand"]
        assert got_small < 40, f"SMALL recovered to {got_small:.1f} — borrowed LARGE's level"
        assert got_large > 400, f"LARGE recovered to {got_large:.1f}"


class TestTransparency:

    def test_report_states_what_changed(self):
        df = _series(n=90)
        df.loc[[60, 61], ["inventory", "demand"]] = [0.0, 0.0]
        _out, report = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        payload = report.to_dict()
        assert payload["action"] == "censored_demand_recovered"
        assert payload["n_flagged"] == 2
        assert payload["n_recovered"] == 2
        assert payload["units_recovered"] > 0
        assert payload["skus_affected"] == ["SKU_A"]

    def test_the_report_always_describes_itself(self):
        """
        Consumers fall back to `description` when they have no localized copy
        for the action. An empty one renders as a blank bullet — a UI claiming
        something changed and then refusing to say what. That is how this notice
        first shipped, while the engine rewrote sales figures behind it.
        """
        df = _series(n=90)
        df.loc[[60, 61], ["inventory", "demand"]] = [0.0, 0.0]
        _out, recovered = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert recovered.to_dict()["description"].strip()
        assert str(recovered.n_recovered) in recovered.to_dict()["description"]

        # And when nothing was altered, it says that rather than going blank.
        quiet = _series(n=10)
        quiet.loc[8:9, ["inventory", "demand"]] = [0.0, 0.0]
        _out2, untouched = recover_censored_demand(
            quiet, "date", "demand", "sku", "inventory")
        assert untouched.n_recovered == 0
        assert untouched.to_dict()["description"].strip()

    def test_flag_column_marks_estimates(self):
        df = _series(n=90)
        df.loc[[60], ["inventory", "demand"]] = [0.0, 0.0]
        out, _ = recover_censored_demand(df, "date", "demand", "sku", "inventory")
        assert CENSORED_FLAG in out.columns
        assert out[CENSORED_FLAG].sum() == 1
