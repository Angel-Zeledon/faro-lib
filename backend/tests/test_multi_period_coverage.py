"""Per-period coverage/semáforo + horizon windowing (multi-period Phase C)."""

import math

from backend.inventory.service import (
    _DAYS_PER_PERIOD,
    _days_per_period,
    _lead_time_in_periods,
    _steps_for_lead_time,
)


class TestPeriodMathHelpers:
    def test_days_per_period_map(self):
        assert _DAYS_PER_PERIOD == {"daily": 1, "weekly": 7, "monthly": 30}
        assert _days_per_period("daily") == 1
        assert _days_per_period("weekly") == 7
        assert _days_per_period("monthly") == 30
        # Unknown/legacy period degrades to daily (1) — never raises.
        assert _days_per_period("fortnightly") == 1
        assert _days_per_period(None) == 1

    def test_lead_time_in_periods(self):
        assert _lead_time_in_periods(14, "weekly") == 2.0
        assert _lead_time_in_periods(15, "weekly") == 15 / 7
        assert _lead_time_in_periods(30, "monthly") == 1.0
        assert _lead_time_in_periods(15, "daily") == 15.0

    def test_steps_for_lead_time_rounds_up_min_one(self):
        assert _steps_for_lead_time(15, "weekly") == 3     # ceil(15/7)
        assert _steps_for_lead_time(14, "weekly") == 2     # ceil(14/7)
        assert _steps_for_lead_time(5, "weekly") == 1      # ceil(5/7) -> 1 floor
        assert _steps_for_lead_time(45, "monthly") == 2    # ceil(45/30)
        assert _steps_for_lead_time(15, "daily") == 15
