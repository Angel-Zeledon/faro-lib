"""
Tests for the monthly recap (feature 3.2): single-month report, the honest
"not enough history" state, and the automatic monthly email with its dedup
ledger.

Numbers in these tests are chosen so every expected value is verifiable by
hand from the rows inserted.
"""

from datetime import datetime, timezone

import pytest

from backend.db.connection import execute, query_one
from backend.notifications.locale import render_es


def _insert_po(tid, when, *, suggested, approved, order_now, total_value):
    execute(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, generated_at, sku_count, total_units,
                total_value, skus_order_now, skus_order_soon,
                suggested_count, approved_count)
           VALUES (%s, 's1', %s, %s, 0, %s, %s, 0, %s, %s)""",
        (tid, when, approved, total_value, order_now, suggested, approved),
    )


def _insert_snapshot(tid, when, value):
    execute(
        """INSERT INTO inventory_overstock_snapshots
               (tenant_id, session_id, overstock_value, recorded_at)
           VALUES (%s, 's1', %s, %s)""",
        (tid, value, when),
    )


# A fixed month in the past keeps these tests independent of the current date.
_YEAR, _MONTH = 2026, 3
_IN_MONTH = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
_MONTH_OPEN = datetime(2026, 3, 1, 0, 5, tzinfo=timezone.utc)
_NEXT_MONTH_OPEN = datetime(2026, 4, 1, 0, 5, tzinfo=timezone.utc)


class TestPreviousMonth:
    def test_mid_year(self):
        from backend.inventory.roi_service import previous_month
        assert previous_month(datetime(2026, 7, 1, tzinfo=timezone.utc)) == (2026, 6)

    def test_january_rolls_back_a_year(self):
        from backend.inventory.roi_service import previous_month
        assert previous_month(datetime(2026, 1, 1, tzinfo=timezone.utc)) == (2025, 12)


class TestCapitalFreedAttribution:
    """Capital freed during month M is snapshot(M) - snapshot(M+1): snapshots
    are opening measurements, so the drop observed between the start of M and
    the start of M+1 is what happened during M."""

    def test_freed_amount_is_attributed_to_the_month_it_happened_in(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=4, approved=3, order_now=2, total_value=1000)
        _insert_snapshot(tid, _MONTH_OPEN, 10_000)
        _insert_snapshot(tid, _NEXT_MONTH_OPEN, 6_000)

        report = get_month_report(tid, _YEAR, _MONTH)
        # 10000 at the start of March, 6000 at the start of April -> 4000 freed
        # during March.
        assert report["capital_freed"] == 4000.0

    def test_no_capital_freed_without_the_closing_snapshot(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=4, approved=3, order_now=2, total_value=1000)
        _insert_snapshot(tid, _MONTH_OPEN, 10_000)  # opening only

        report = get_month_report(tid, _YEAR, _MONTH)
        assert report["capital_freed"] is None
        assert report["capital_freed_status"] == "not_measured"

    def test_growing_overstock_is_not_reported_as_zero_saving(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=4, approved=3, order_now=2, total_value=1000)
        _insert_snapshot(tid, _MONTH_OPEN, 5_000)
        _insert_snapshot(tid, _NEXT_MONTH_OPEN, 8_000)

        report = get_month_report(tid, _YEAR, _MONTH)
        # Overstock grew by 3000. That is not a saving, and 0.0 would read as
        # "we saved nothing" rather than "this did not happen".
        assert report["capital_freed"] is None
        # And it is NOT the same None as the missing-snapshot case above: both
        # measurements exist, so telling this tenant we lack data would be
        # false. The screen says their overstock went up.
        assert report["capital_freed_status"] == "grew"


class TestMonthReportMath:
    def test_aggregates_only_the_requested_month(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=10, approved=6, order_now=3, total_value=1200)
        _insert_po(tid, datetime(2026, 3, 20, tzinfo=timezone.utc),
                   suggested=10, approved=9, order_now=2, total_value=800)
        # Neighbouring months must not leak in.
        _insert_po(tid, datetime(2026, 2, 27, tzinfo=timezone.utc),
                   suggested=99, approved=99, order_now=99, total_value=99_999)
        _insert_po(tid, datetime(2026, 4, 2, tzinfo=timezone.utc),
                   suggested=77, approved=77, order_now=77, total_value=77_777)

        r = get_month_report(tid, _YEAR, _MONTH)

        assert r["month"] == "2026-03"
        assert r["has_sufficient_history"] is True
        assert r["orders_generated"] == 2
        assert r["recommendations_shown"] == 20        # 10 + 10
        assert r["recommendations_followed"] == 15     # 6 + 9
        assert r["adoption_rate"] == pytest.approx(0.75)  # 15 / 20
        assert r["stockout_risks_handled"] == 5        # 3 + 2
        assert r["managed_purchase_value"] == 2000.0   # 1200 + 800

    def test_managed_value_is_none_not_zero_without_cost_data(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=5, approved=5, order_now=1, total_value=None)

        r = get_month_report(tid, _YEAR, _MONTH)
        # A tenant that never entered unit costs must be told the figure is
        # unavailable, not shown ₡0 of "purchases managed".
        assert r["managed_purchase_value"] is None
        assert r["orders_generated"] == 1

    def test_partial_cost_coverage_is_flagged_instead_of_passing_for_the_total(
        self, client, test_tenant,
    ):
        """
        `total_value` sums only the lines that carried a unit cost — a NULL cost
        annuls the product and SQL drops it. The all-missing case was already
        honest (None, not 0); the PARTIAL case was not, and partial is the
        common state while a tenant is still filling costs in. 40 ordered lines
        with 6 costed reported those 6 as the month's managed purchasing, and it
        looked exact: ₡30M could read as ₡2,1M.
        """
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=3, approved=3, order_now=1, total_value=250)
        po = query_one(
            "SELECT id FROM inventory_po_log WHERE tenant_id = %s ORDER BY generated_at DESC LIMIT 1",
            (tid,),
        )
        # Two ordered lines: one costed (10 x 25 = 250), one with no cost at all.
        for sku, cost in (("COSTED", 25.0), ("BARE", None)):
            execute(
                """INSERT INTO inventory_po_items
                       (po_log_id, tenant_id, sku, recommended_qty, final_qty,
                        unit_cost, status, warehouse)
                   VALUES (%s, %s, %s, 10, 10, %s, 'approved', 'principal')""",
                (po["id"], tid, sku, cost),
            )

        r = get_month_report(tid, _YEAR, _MONTH)

        assert r["managed_purchase_value"] == 250.0
        assert r["managed_purchase_value_complete"] is False

    def test_full_cost_coverage_reports_the_figure_as_complete(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=2, approved=2, order_now=1, total_value=500)
        po = query_one(
            "SELECT id FROM inventory_po_log WHERE tenant_id = %s ORDER BY generated_at DESC LIMIT 1",
            (tid,),
        )
        for sku in ("A", "B"):
            execute(
                """INSERT INTO inventory_po_items
                       (po_log_id, tenant_id, sku, recommended_qty, final_qty,
                        unit_cost, status, warehouse)
                   VALUES (%s, %s, %s, 10, 10, 25.0, 'approved', 'principal')""",
                (po["id"], tid, sku),
            )

        r = get_month_report(tid, _YEAR, _MONTH)

        assert r["managed_purchase_value"] == 500.0
        assert r["managed_purchase_value_complete"] is True

    def test_adoption_rate_none_when_nothing_was_suggested(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        tid = test_tenant["id"]
        _insert_po(tid, _IN_MONTH, suggested=0, approved=0, order_now=0, total_value=500)

        r = get_month_report(tid, _YEAR, _MONTH)
        assert r["adoption_rate"] is None


class TestInsufficientHistory:
    def test_tenant_without_orders_gets_honest_empty_state(self, client, test_tenant):
        from backend.inventory.roi_service import get_month_report

        r = get_month_report(test_tenant["id"], _YEAR, _MONTH)

        assert r["has_sufficient_history"] is False
        # Metrics must be None, never zeros that could be read as achievements.
        assert r["adoption_rate"] is None
        assert r["stockout_risks_handled"] is None
        assert r["managed_purchase_value"] is None
        assert r["orders_generated"] == 0


class TestMonthReportEndpoint:
    def test_viewer_can_read(self, client, viewer_headers, test_tenant):
        _insert_po(test_tenant["id"], _IN_MONTH,
                   suggested=4, approved=2, order_now=1, total_value=600)

        resp = client.get(
            "/api/v1/inventory/roi/month-report?year=2026&month=3", headers=viewer_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["month"] == "2026-03"
        assert data["adoption_rate"] == pytest.approx(0.5)

    def test_defaults_to_the_month_that_just_closed(self, client, auth_headers):
        from backend.inventory.roi_service import previous_month

        y, m = previous_month(datetime.now(tz=timezone.utc))
        resp = client.get("/api/v1/inventory/roi/month-report", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["month"] == f"{y}-{m:02d}"

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/roi/month-report")
        assert resp.status_code == 401

    def test_invalid_month_rejected(self, client, auth_headers):
        resp = client.get(
            "/api/v1/inventory/roi/month-report?year=2026&month=13", headers=auth_headers
        )
        assert resp.status_code == 422


class TestRunMonthlyRoiEmails:
    """The runner writes inventory_roi_email_log; assertions go against that
    table, not the return value alone."""

    def _arrange_tenant(self, monkeypatch, tid):
        from backend.inventory import service

        monkeypatch.setattr(
            service, "get_tenants_with_active_sessions", lambda: [{"tenant_id": tid}]
        )
        monkeypatch.setattr(
            service, "get_tenant_admin_emails", lambda t: ["boss@acme.cr"]
        )

    def test_sends_and_records_the_send(self, client, monkeypatch, test_tenant):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        self._arrange_tenant(monkeypatch, tid)
        _insert_po(tid, _IN_MONTH, suggested=8, approved=6, order_now=2, total_value=4000)
        _insert_snapshot(tid, _MONTH_OPEN, 9_000)
        _insert_snapshot(tid, _NEXT_MONTH_OPEN, 7_500)
        # This company keeps its books in dollars, so the recap must be composed
        # in dollars — the amounts are relabelled, never converted.
        from backend.tenants.service import update_settings
        update_settings(tid, {"currency": "USD"})

        captured = []
        monkeypatch.setattr(
            "backend.notifications.email.send_monthly_roi_email",
            lambda to, report, roi_url, currency=None, **_kw:
                (captured.append((to, report, currency)), True)[1],
        )

        sent = roi_service.run_monthly_roi_emails(
            now=datetime(2026, 4, 1, 0, 5, tzinfo=timezone.utc)
        )

        assert sent == 1
        assert captured[0][0] == "boss@acme.cr"
        assert captured[0][1]["capital_freed"] == 1500.0   # 9000 - 7500
        assert captured[0][1]["adoption_rate"] == pytest.approx(0.75)  # 6 / 8
        # Without this the subject line says ₡ to a company that trades in dollars.
        assert captured[0][2] is not None, "the recap was mailed with no currency"
        assert captured[0][2]["code"] == "USD" and captured[0][2]["symbol"] == "$"

        row = query_one(
            "SELECT month, recipients FROM inventory_roi_email_log WHERE tenant_id = %s",
            (tid,),
        )
        assert row is not None
        assert row["month"] == "2026-03"
        assert row["recipients"] == 1

    def test_second_run_does_not_resend(self, client, monkeypatch, test_tenant):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        self._arrange_tenant(monkeypatch, tid)
        _insert_po(tid, _IN_MONTH, suggested=8, approved=6, order_now=2, total_value=4000)

        calls = []
        monkeypatch.setattr(
            "backend.notifications.email.send_monthly_roi_email",
            lambda to, report, roi_url, currency=None, **_kw: (calls.append(to), True)[1],
        )

        now = datetime(2026, 4, 1, 0, 5, tzinfo=timezone.utc)
        roi_service.run_monthly_roi_emails(now=now)
        roi_service.run_monthly_roi_emails(now=now)

        assert len(calls) == 1  # worker restarts must not mail twice

        count = query_one(
            "SELECT COUNT(*)::int AS c FROM inventory_roi_email_log WHERE tenant_id = %s",
            (tid,),
        )
        assert count["c"] == 1

    def test_tenant_without_history_is_not_mailed_at_all(self, client, monkeypatch, test_tenant):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        self._arrange_tenant(monkeypatch, tid)
        # No purchase orders in the month at all.

        calls = []
        monkeypatch.setattr(
            "backend.notifications.email.send_monthly_roi_email",
            lambda to, report, roi_url, currency=None, **_kw: (calls.append(to), True)[1],
        )

        sent = roi_service.run_monthly_roi_emails(
            now=datetime(2026, 4, 1, 0, 5, tzinfo=timezone.utc)
        )

        assert sent == 0
        assert calls == []
        row = query_one(
            "SELECT id FROM inventory_roi_email_log WHERE tenant_id = %s", (tid,)
        )
        assert row is None

    def test_failed_delivery_is_not_recorded_as_sent(self, client, monkeypatch, test_tenant):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        self._arrange_tenant(monkeypatch, tid)
        _insert_po(tid, _IN_MONTH, suggested=8, approved=6, order_now=2, total_value=4000)

        monkeypatch.setattr(
            "backend.notifications.email.send_monthly_roi_email",
            lambda to, report, roi_url, currency=None, **_kw: False,
        )

        sent = roi_service.run_monthly_roi_emails(
            now=datetime(2026, 4, 1, 0, 5, tzinfo=timezone.utc)
        )

        assert sent == 0
        # Nothing logged, so a later run can retry.
        row = query_one(
            "SELECT id FROM inventory_roi_email_log WHERE tenant_id = %s", (tid,)
        )
        assert row is None


class TestMonthlyRecapEmailTemplate:
    def test_omits_metrics_that_could_not_be_derived(self, monkeypatch):
        from backend.notifications import email as email_mod

        # conftest replaces _send session-wide, so _transport_send is never
        # reached. This test is about the template the customer reads, not the
        # transport, so it captures at _send.
        captured = {}
        monkeypatch.setattr(
            email_mod, "_send",
            lambda to, subject, html, attachment=None, **_kw: captured.update(
                to=to, subject=subject, html=html
            ),
        )

        report = {
            "month": "2026-03",
            "has_sufficient_history": True,
            "orders_generated": 2,
            "recommendations_shown": 8,
            "recommendations_followed": 6,
            "adoption_rate": 0.75,
            "stockout_risks_handled": 3,
            "managed_purchase_value": None,   # tenant has no unit costs
            "capital_freed": None,            # only one snapshot so far
        }
        assert email_mod.send_monthly_roi_email(
            "boss@acme.cr", report, "http://app/inventory/roi"
        ) is True

        html = captured["html"]
        assert "75%" in html
        # Asserted through the catalog rather than a literal: the wording of
        # these tiles is exactly what this test is not about, and pinning the
        # sentence made a copy correction look like a regression.
        assert render_es("roi_email_metric_risks_label") in html
        # Underivable metrics must be absent, not rendered as ₡0.
        assert render_es("roi_email_metric_purchases_label") not in html
        assert render_es("roi_email_metric_capital_label") not in html
        # And the subject must not carry an amount we cannot back.
        assert captured["subject"] == render_es(
            "roi_email_subject_default", month="marzo de 2026")

    def test_capital_freed_headline_uses_colones(self, monkeypatch):
        from backend.notifications import email as email_mod

        captured = {}
        monkeypatch.setattr(
            email_mod, "_send",
            lambda to, subject, html, attachment=None, **_kw: captured.update(
                subject=subject, html=html
            ),
        )

        report = {
            "month": "2026-03",
            "has_sufficient_history": True,
            "orders_generated": 2,
            "recommendations_shown": 8,
            "recommendations_followed": 6,
            "adoption_rate": 0.75,
            "stockout_risks_handled": 3,
            "managed_purchase_value": 2_000_000.0,
            "capital_freed": 1_500_000.0,
        }
        email_mod.send_monthly_roi_email("boss@acme.cr", report, "http://app/inventory/roi")

        assert "₡1.500.000" in captured["subject"]
        assert "marzo de 2026" in captured["subject"]
        assert "₡2.000.000" in captured["html"]
