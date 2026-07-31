"""
The calendar features a model TRAINS on must be the ones it is SERVED.

These tests exist because the two used to be different code: the feature
engineer computed real holidays, the predictor hardcoded is_holiday=0 for every
future date. Nothing failed — the model just quietly lost the holiday signal at
the forecast boundary, which is exactly the kind of defect a status-code test
cannot see.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import FeaturesConfig
from forecasting_core.features.calendar import (
    CALENDAR_COLUMNS, HolidayCalendar, calendar_frame, fourier_frame,
)
from forecasting_core.features.engineer import FeatureEngineer
from forecasting_core.inference.predictor import _calendar_rows, _fourier_rows


CHRISTMAS = pd.Timestamp("2025-12-25")
NEW_YEAR = pd.Timestamp("2026-01-01")


class TestHolidayCalendar:

    def test_future_years_resolve(self):
        """A year beyond the training data is not a special case."""
        cal = HolidayCalendar("CO")
        assert cal.is_holiday(CHRISTMAS)
        assert cal.is_holiday(NEW_YEAR)

    def test_country_changes_the_holiday_set(self):
        """A LatAm product cannot serve one country's calendar to all of them."""
        co = HolidayCalendar("CO").holidays_for([2025])
        mx = HolidayCalendar("MX").holidays_for([2025])
        assert co != mx, "CO and MX must not resolve to the same holiday set"
        # Mexican Independence Day is a holiday there and not in Colombia.
        assert pd.Timestamp("2025-09-16") in mx
        assert pd.Timestamp("2025-09-16") not in co

    def test_unknown_country_degrades_but_keeps_easter_and_christmas(self):
        cal = HolidayCalendar("ZZ")
        holidays = cal.holidays_for([2025])
        assert CHRISTMAS in holidays
        assert pd.Timestamp("2025-04-20") in holidays   # Easter 2025

    def test_days_to_holiday_is_zero_on_the_holiday(self):
        frame = calendar_frame([CHRISTMAS], HolidayCalendar("CO"))
        assert frame["days_to_xmas"].iloc[0] == 0
        assert frame["days_to_holiday"].iloc[0] == 0

    def test_days_to_holiday_counts_whole_days(self):
        frame = calendar_frame([pd.Timestamp("2025-12-22")], HolidayCalendar("CO"))
        assert frame["days_to_xmas"].iloc[0] == 3


class TestTrainServeAgreement:
    """The regression guard: same date, same features, both sides."""

    def _train_frame(self, dates, country="CO"):
        df = pd.DataFrame({
            "date": dates,
            "sku": "SKU_A",
            "sales": np.arange(len(dates), dtype=float) + 1.0,
        })
        cfg = FeaturesConfig(lags=[1], diffs=[], rolling=[], calendar=True,
                             holiday_country=country)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        return eng.transform(df), cfg

    def test_holiday_features_match_between_training_and_inference(self):
        dates = pd.date_range("2025-12-15", periods=20, freq="D")
        trained, cfg = self._train_frame(dates)
        served = _calendar_rows(list(dates), cfg)

        # Align on the dates that survived the lag warm-up drop.
        trained = trained.set_index(pd.to_datetime(trained["date"]))
        for i, dt in enumerate(dates):
            if dt not in trained.index:
                continue
            for col in CALENDAR_COLUMNS:
                assert served[i][col] == pytest.approx(float(trained.loc[dt, col])), (
                    f"{col} disagrees on {dt.date()}: "
                    f"train={trained.loc[dt, col]} serve={served[i][col]}"
                )

    def test_future_christmas_is_not_flattened_to_zero(self):
        """The exact defect: training saw a holiday, inference saw nothing."""
        cfg = FeaturesConfig(calendar=True, holiday_country="CO")
        served = _calendar_rows([CHRISTMAS, NEW_YEAR], cfg)
        assert served[0]["is_holiday"] == 1
        assert served[0]["is_christmas"] == 1
        assert served[1]["is_holiday"] == 1
        assert any(r["holiday_intensity"] > 0 for r in served)

    def test_fourier_terms_match_between_training_and_inference(self):
        dates = pd.date_range("2025-01-01", periods=60, freq="D")
        df = pd.DataFrame({
            "date": dates, "sku": "SKU_A",
            "sales": np.arange(len(dates), dtype=float) + 1.0,
        })
        cfg = FeaturesConfig(lags=[1], diffs=[], rolling=[], calendar=False,
                             fourier_periods=[7, 365], fourier_K=2)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        trained = eng.transform(df).set_index(pd.to_datetime(df["date"].iloc[1:]).values)
        served = _fourier_rows(list(dates), cfg)

        cols = list(fourier_frame(dates, [7, 365], 2).columns)
        assert cols, "fourier columns must exist for this config"
        for i, dt in enumerate(dates):
            if dt not in trained.index:
                continue
            for col in cols:
                assert served[i][col] == pytest.approx(float(trained.loc[dt, col])), (
                    f"{col} disagrees on {dt.date()}"
                )

    def test_fourier_phase_is_independent_of_where_the_dataset_starts(self):
        """Two uploads covering the same day must produce the same feature.

        With a dataset-relative origin they did not, so the value a model
        learned depended on the customer's export window.
        """
        overlap = pd.Timestamp("2025-06-15")
        early = fourier_frame(pd.date_range("2025-01-01", "2025-07-01", freq="D"), [365], 1)
        late = fourier_frame(pd.date_range("2025-05-01", "2025-07-01", freq="D"), [365], 1)
        early.index = pd.date_range("2025-01-01", "2025-07-01", freq="D")
        late.index = pd.date_range("2025-05-01", "2025-07-01", freq="D")
        for col in early.columns:
            assert early.loc[overlap, col] == pytest.approx(late.loc[overlap, col])
