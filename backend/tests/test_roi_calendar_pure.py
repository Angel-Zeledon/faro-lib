"""Pure checks (no database) for the recap's calendar and its cancelled-order rule.

A month is the TENANT's calendar month. For Costa Rica (UTC-6) March begins at
06:00 UTC on March 1st, so an order placed at 7 pm local on March 31st belongs
to March even though it is already April in UTC.
"""

import inspect
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.inventory import roi_service as roi

CR = ZoneInfo("America/Costa_Rica")        # UTC-6, no DST
MADRID = ZoneInfo("Europe/Madrid")         # UTC+1 / +2 (DST)
UTC = timezone.utc


class TestMonthBounds:
    def test_costa_rica_march_starts_at_six_utc(self):
        start, end = roi.month_bounds_utc(2026, 3, CR)
        assert start == datetime(2026, 3, 1, 6, 0, tzinfo=UTC)
        assert end == datetime(2026, 4, 1, 6, 0, tzinfo=UTC)

    def test_an_order_at_7pm_local_on_the_31st_is_in_march(self):
        start, end = roi.month_bounds_utc(2026, 3, CR)
        order = datetime(2026, 4, 1, 1, 0, tzinfo=UTC)     # 19:00 on Mar 31 local
        assert start <= order < end
        # ...and a cut at 00:00 UTC (the old behaviour) would have lost it.
        assert not (datetime(2026, 3, 1, tzinfo=UTC) <= order < datetime(2026, 4, 1, tzinfo=UTC))

    def test_an_order_just_after_local_midnight_is_in_the_next_month(self):
        _, end = roi.month_bounds_utc(2026, 3, CR)
        assert datetime(2026, 4, 1, 6, 0, tzinfo=UTC) >= end

    def test_east_of_utc_the_month_starts_before_utc_midnight(self):
        start, _ = roi.month_bounds_utc(2026, 1, MADRID)
        assert start == datetime(2025, 12, 31, 23, 0, tzinfo=UTC)

    def test_december_rolls_into_january_of_the_next_year(self):
        start, end = roi.month_bounds_utc(2025, 12, CR)
        assert start == datetime(2025, 12, 1, 6, 0, tzinfo=UTC)
        assert end == datetime(2026, 1, 1, 6, 0, tzinfo=UTC)

    def test_dst_months_use_the_offset_in_force_on_the_first(self):
        # Madrid: summer time begins 2026-03-29, so April 1st is UTC+2.
        start, _ = roi.month_bounds_utc(2026, 4, MADRID)
        assert start == datetime(2026, 3, 31, 22, 0, tzinfo=UTC)

    def test_consecutive_months_tile_without_gap_or_overlap(self):
        prev_end = None
        for month in range(1, 13):
            start, end = roi.month_bounds_utc(2026, month, MADRID)
            if prev_end is not None:
                assert start == prev_end
            prev_end = end


class TestMonthKeys:
    def test_local_month_key_follows_the_tenant_clock(self):
        moment = datetime(2026, 4, 1, 1, 0, tzinfo=UTC)
        assert roi.local_month_key(moment, CR) == "2026-03"
        assert roi.local_month_key(moment, UTC) == "2026-04"

    def test_recent_month_keys_end_with_the_local_month(self):
        now = datetime(2026, 4, 1, 1, 0, tzinfo=UTC)       # still March in Costa Rica
        assert roi.recent_month_keys(now, CR, 3) == ["2026-01", "2026-02", "2026-03"]
        assert roi.recent_month_keys(now, UTC, 2) == ["2026-03", "2026-04"]

    def test_recent_month_keys_cross_a_year_boundary(self):
        now = datetime(2026, 2, 10, tzinfo=UTC)
        assert roi.recent_month_keys(now, CR, 4) == [
            "2025-11", "2025-12", "2026-01", "2026-02"]

    def test_current_month_start_is_the_local_first(self):
        now = datetime(2026, 4, 1, 1, 0, tzinfo=UTC)
        assert roi.current_month_start_utc(now, CR) == datetime(2026, 3, 1, 6, 0, tzinfo=UTC)


class TestLastClosedMonth:
    def test_west_of_utc_the_month_is_still_open_at_00_05_utc_on_the_first(self):
        now = datetime(2026, 4, 1, 0, 5, tzinfo=UTC)       # 18:05 Mar 31 in Costa Rica
        assert roi.last_closed_month_for(now, CR) == (2026, 2)

    def test_once_the_zone_has_rolled_over_the_month_is_closed(self):
        now = datetime(2026, 4, 1, 7, 5, tzinfo=UTC)       # 01:05 Apr 1 in Costa Rica
        assert roi.last_closed_month_for(now, CR) == (2026, 3)

    def test_utc_tenants_close_at_utc_midnight(self):
        now = datetime(2026, 4, 1, 0, 5, tzinfo=UTC)
        assert roi.last_closed_month_for(now, UTC) == (2026, 3)

    def test_january_closes_december_of_the_previous_year(self):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert roi.last_closed_month_for(now, CR) == (2025, 12)

    def test_the_worker_settle_hour_is_after_every_supported_zone_rolled_over(self):
        """The second recap pass runs at ROI_RECAP_SETTLE_HOUR_UTC; every zone the
        product supports must have started its new month by then, or the pass
        would still skip tenants it exists to serve."""
        from backend.api.v1.timezone import SUPPORTED
        from backend.workers.worker import ROI_RECAP_SETTLE_HOUR_UTC

        # Worst case for "has the month rolled over": the 1st of a month in
        # winter (no DST help for the western zones).
        for january in (datetime(2026, 1, 1, ROI_RECAP_SETTLE_HOUR_UTC, 5, tzinfo=UTC),
                        datetime(2026, 7, 1, ROI_RECAP_SETTLE_HOUR_UTC, 5, tzinfo=UTC)):
            for name in SUPPORTED:
                local = january.astimezone(ZoneInfo(name))
                assert local.day == 1, f"{name} has not rolled over at settle hour"


class TestCancelledOrdersAreExcluded:
    """Every money/count aggregate over inventory_po_log must carry the standing
    filter. Reading the SQL is the pure half; the DB tests prove the behaviour."""

    def test_the_standing_filter_is_the_cancelled_marker(self):
        assert roi._STANDING == "cancelled_at IS NULL"
        assert roi._STANDING_POL == "pol.cancelled_at IS NULL"

    @pytest.mark.parametrize("fn,minimum", [
        (roi.get_roi_summary, 3),     # totals, this month, last month
        (roi.get_monthly_summary, 1),
        (roi.get_month_report, 2),    # header aggregate + line coverage
    ])
    def test_each_aggregating_function_filters_cancelled_orders(self, fn, minimum):
        src = inspect.getsource(fn)
        assert src.count("_STANDING") >= minimum

    def test_the_history_list_still_shows_cancelled_orders(self):
        """The list is the one place a cancelled order must stay visible, with
        its marker, so the buyer can reopen it."""
        src = inspect.getsource(roi.get_po_history)
        assert "cancelled_at" in src and "_STANDING" not in src


class TestWordingIsWhatIsComputed:
    def test_no_field_claims_stockouts_avoided_or_value_protected(self):
        src = inspect.getsource(roi.get_roi_summary) + inspect.getsource(roi.get_month_report)
        for claim in ("stockout_risks_handled", "total_skus_protected",
                      "estimated_value_protected"):
            assert claim not in src
        assert "urgent_lines_ordered" in src and "ordered_value" in src
