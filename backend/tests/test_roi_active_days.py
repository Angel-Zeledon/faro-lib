"""«Días activo» must count days the buyer showed up, not days on the calendar.

Named after the failure. /impacto printed "5 días activo" next to "desde 4 de
agosto de 2026", and the number was `(last_po - first_po).days` — the SPAN
between the first and last order. It grows while the buyer is away, so a tenant
who generated one order and came back a year later would have read "365 días
activo" after using Faro on exactly two days. Nothing errored; the figure simply
described something other than its own label.
"""

from datetime import datetime, timedelta, timezone

from backend.db.connection import execute


def _po(tenant_id: str, when: datetime) -> None:
    execute(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                skus_order_now, skus_order_soon, suggested_count, approved_count)
           VALUES (%s, 's1', %s, 1, 10, 100, 1, 0, 1, 1)""",
        (tenant_id, when),
    )


class TestActiveDaysCountsDaysWithActivity:
    def test_a_long_gap_between_two_orders_is_still_two_days(self, test_tenant):
        """The exact shape that made the old number absurd."""
        from backend.inventory.roi_service import get_roi_summary

        tid = test_tenant["id"]
        first = datetime.now(tz=timezone.utc) - timedelta(days=365)
        _po(tid, first)
        _po(tid, first + timedelta(days=365))

        summary = get_roi_summary(tid)
        assert summary["active_days"] == 2, (
            f"two orders a year apart reported {summary['active_days']} active days"
        )

    def test_several_orders_on_one_day_count_once(self, test_tenant):
        from backend.inventory.roi_service import get_roi_summary

        tid = test_tenant["id"]
        day = datetime.now(tz=timezone.utc) - timedelta(days=3)
        _po(tid, day.replace(hour=8))
        _po(tid, day.replace(hour=13))
        _po(tid, day.replace(hour=19))

        assert get_roi_summary(tid)["active_days"] == 1

    def test_a_tenant_that_never_ordered_reports_zero(self, test_tenant):
        from backend.inventory.roi_service import get_roi_summary

        assert get_roi_summary(test_tenant["id"])["active_days"] == 0
