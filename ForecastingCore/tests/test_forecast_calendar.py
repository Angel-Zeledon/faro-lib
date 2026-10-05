"""
One mistyped year must not decide the forecast calendar.

Found by uploading a hostile file through the real app: 60 daily rows plus one
row dated 1900 and one dated 2099. The run COMPLETED, reported no error, and
produced forecast dates of

    2173-11-01,  2247-09-03,  2321-07-05

because the cadence was read off the gap between the last two observations —
which, once a 2099 row sorts to the end, is about seventy-three years. The
numbers were then presented as an ordinary forecast. Nothing failed; the answer
was simply about the twenty-fourth century.
"""

import pandas as pd
import pytest

from forecasting_core.inference.predictor import _bucket_delta, _future_dates


def _frame(dates):
    return pd.DataFrame({"date": pd.to_datetime(dates), "demand": 1.0})


class TestCadence:

    def test_daily_series(self):
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        assert _bucket_delta(pd.Series(dates)) == pd.Timedelta(days=1)

    def test_weekly_series(self):
        dates = pd.date_range("2026-01-01", periods=40, freq="W")
        assert _bucket_delta(pd.Series(dates)) == pd.Timedelta(days=7)

    def test_a_single_outlier_year_does_not_move_it(self):
        """The defect, isolated."""
        dates = list(pd.date_range("2026-01-01", periods=60, freq="D"))
        dates += [pd.Timestamp("2099-12-31")]
        assert _bucket_delta(pd.Series(dates)) == pd.Timedelta(days=1)

    def test_outliers_on_both_ends_do_not_move_it(self):
        dates = [pd.Timestamp("1900-01-01")]
        dates += list(pd.date_range("2026-01-01", periods=60, freq="D"))
        dates += [pd.Timestamp("2099-12-31")]
        assert _bucket_delta(pd.Series(dates)) == pd.Timedelta(days=1)

    def test_unsorted_input_is_handled(self):
        dates = list(pd.date_range("2026-01-01", periods=30, freq="D"))
        assert _bucket_delta(pd.Series(dates[::-1])) == pd.Timedelta(days=1)

    def test_a_single_date_falls_back_to_one_day(self):
        assert _bucket_delta(pd.Series([pd.Timestamp("2026-01-01")])) == pd.Timedelta(days=1)

    def test_all_identical_dates_fall_back_to_one_day(self):
        """Zero gaps would otherwise yield a zero-length step and stack every
        forecast point on the same date."""
        same = pd.Series([pd.Timestamp("2026-01-01")] * 5)
        assert _bucket_delta(same) == pd.Timedelta(days=1)


class TestFutureDates:

    def test_they_follow_the_last_observation(self):
        frame = _frame(pd.date_range("2026-01-01", periods=30, freq="D"))
        future = _future_dates(frame, "date", 3)
        assert future == [pd.Timestamp("2026-01-31"), pd.Timestamp("2026-02-01"),
                          pd.Timestamp("2026-02-02")]

    def test_a_mistyped_year_does_not_send_the_forecast_to_the_next_century(self):
        """
        The exact file that produced 2321-07-05. The 2099 row is still the
        maximum date, so the forecast legitimately starts after it — but it must
        advance ONE DAY at a time, not seventy-three years.
        """
        dates = list(pd.date_range("2026-01-01", periods=60, freq="D"))
        dates += [pd.Timestamp("2099-12-31"), pd.Timestamp("1900-01-01")]
        future = _future_dates(_frame(dates), "date", 3)

        assert future[0] == pd.Timestamp("2100-01-01")
        assert future[-1] == pd.Timestamp("2100-01-03")
        for a, b in zip(future, future[1:]):
            assert (b - a) == pd.Timedelta(days=1), (
                f"forecast steps are {b - a} apart, not one bucket"
            )

    def test_steps_are_evenly_spaced_on_a_weekly_series(self):
        frame = _frame(pd.date_range("2026-01-04", periods=20, freq="W"))
        future = _future_dates(frame, "date", 4)
        for a, b in zip(future, future[1:]):
            assert (b - a) == pd.Timedelta(days=7)

    def test_the_horizon_length_is_respected(self):
        frame = _frame(pd.date_range("2026-01-01", periods=30, freq="D"))
        assert len(_future_dates(frame, "date", 14)) == 14
