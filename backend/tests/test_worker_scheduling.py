"""
Pure-logic tests for worker scheduling helpers (feature 1.5: monthly
overstock snapshot). No DB access — offline.
"""

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.offline


def test_next_month_start_mid_month_rolls_to_first_of_next_month():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc)


def test_next_month_start_before_trigger_time_on_the_1st_stays_same_day():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc)


def test_next_month_start_after_trigger_time_on_the_1st_rolls_forward():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc)


def test_next_month_start_rolls_over_year_boundary():
    from backend.workers.worker import _next_month_start

    now = datetime(2026, 12, 20, 9, 0, tzinfo=timezone.utc)
    result = _next_month_start(now)
    assert result == datetime(2027, 1, 1, 0, 5, tzinfo=timezone.utc)
