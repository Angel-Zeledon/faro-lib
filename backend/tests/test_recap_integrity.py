"""The purchasing recap is safe to put in front of an executive.

Five defects found by the export/reporting research, each pinned against the
database (state asserted from rows, not from echoes):

1. cancelled purchase orders were counted in the month's orders, units and money;
2. exporting the same list twice wrote two orders;
3. months were cut at 00:00 UTC instead of in the tenant's timezone;
4. fields and labels claimed "stockouts avoided" / "value protected" while the
   code counts urgent lines ordered and sums order values;
5. the inventory PDF, the session report and the audit CSV left no audit row.
"""

from datetime import datetime, timezone

import pytest

from backend.db.connection import execute, query, query_one
from backend.inventory import roi_service
from backend.inventory.po_cancel_service import cancel, uncancel

UTC = timezone.utc
_MARCH = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _po(tid, when, *, value=100, urgent=1, units=10, suggested=2, approved=2) -> str:
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, generated_at, sku_count, total_units,
                total_value, skus_order_now, skus_order_soon,
                suggested_count, approved_count)
           VALUES (%s, 's1', %s, 1, %s, %s, %s, 0, %s, %s)
        RETURNING id""",
        (tid, when, units, value, urgent, suggested, approved),
    )
    return row["id"]


def _audit(tid, action):
    return query(
        "SELECT user_id, resource, context FROM activity_logs "
        "WHERE tenant_id = %s AND action = %s ORDER BY created_at",
        (tid, action))


# ── 1. cancelled orders ──────────────────────────────────────────────────────

@pytest.mark.integration
class TestCancelledOrdersDoNotCount:
    def test_month_report_excludes_a_cancelled_order_and_counts_it_again_on_reopen(
        self, test_tenant,
    ):
        tid = test_tenant["id"]
        _po(tid, _MARCH, value=1000, urgent=2)
        gone = _po(tid, _MARCH, value=500, urgent=3)

        both = roi_service.get_month_report(tid, 2026, 3)
        assert both["orders_generated"] == 2
        assert both["managed_purchase_value"] == 1500.0
        assert both["urgent_lines_ordered"] == 5

        cancel(tid, gone, "u1", "supplier fell through")
        assert query_one("SELECT cancelled_at FROM inventory_po_log WHERE id = %s",
                         (gone,))["cancelled_at"] is not None
        after = roi_service.get_month_report(tid, 2026, 3)
        assert after["orders_generated"] == 1
        assert after["managed_purchase_value"] == 1000.0
        assert after["urgent_lines_ordered"] == 2

        uncancel(tid, gone, "u1")
        again = roi_service.get_month_report(tid, 2026, 3)
        assert again["orders_generated"] == 2
        assert again["managed_purchase_value"] == 1500.0

    def test_a_month_whose_only_order_was_cancelled_has_no_history(self, test_tenant):
        tid = test_tenant["id"]
        only = _po(tid, _MARCH, value=900)
        cancel(tid, only, "u1")
        r = roi_service.get_month_report(tid, 2026, 3)
        assert r["has_sufficient_history"] is False
        assert r["managed_purchase_value"] is None      # unknown, not zero
        assert r["urgent_lines_ordered"] is None

    def test_line_coverage_ignores_the_lines_of_a_cancelled_order(self, test_tenant):
        """A cancelled order's uncosted lines must not turn a complete month
        into a 'floor' figure."""
        tid = test_tenant["id"]
        ok_po = _po(tid, _MARCH, value=100)
        bad_po = _po(tid, _MARCH, value=None)
        for po, cost in ((ok_po, 5.0), (bad_po, None)):
            execute(
                """INSERT INTO inventory_po_items
                       (po_log_id, tenant_id, sku, signal, recommended_qty,
                        final_qty, unit_cost, status)
                   VALUES (%s, %s, 'A', 'PEDIR_YA', 1, 1, %s, 'approved')""",
                (po, tid, cost))
        assert roi_service.get_month_report(tid, 2026, 3)[
            "managed_purchase_value_complete"] is False
        cancel(tid, bad_po, "u1")
        assert roi_service.get_month_report(tid, 2026, 3)[
            "managed_purchase_value_complete"] is True

    def test_roi_summary_and_monthly_table_exclude_cancelled_orders(self, test_tenant):
        tid = test_tenant["id"]
        now = datetime.now(tz=UTC)
        _po(tid, now, value=200, urgent=1, units=4)
        gone = _po(tid, now, value=800, urgent=5, units=40)
        cancel(tid, gone, "u1")

        s = roi_service.get_roi_summary(tid)
        assert s["total_pos_generated"] == 1
        assert s["urgent_lines_ordered"] == 1
        assert s["ordered_value"] == 200.0
        assert s["total_units_ordered"] == 4.0
        assert s["pos_this_month"] == 1

        rows = roi_service.get_monthly_summary(tid, months=2)
        assert sum(r["pos_count"] for r in rows) == 1
        assert sum(r["urgent_lines_ordered"] for r in rows) == 1

    def test_the_history_list_still_shows_the_cancelled_order(self, test_tenant):
        tid = test_tenant["id"]
        gone = _po(tid, _MARCH)
        cancel(tid, gone, "u1")
        listed = {r["id"]: r for r in roi_service.get_po_history(tid)}
        assert listed[gone]["cancelled_at"] is not None


# ── 2. exporting twice ───────────────────────────────────────────────────────

def _cart(qty=10.0):
    return [{"sku": "A", "supplier": "ACME", "signal": "PEDIR_YA",
             "recommended_qty": 10, "final_qty": qty, "unit_cost": 2.0,
             "status": "approved"}]


def _count(tid):
    return query_one("SELECT COUNT(*)::int AS n FROM inventory_po_log WHERE tenant_id = %s",
                     (tid,))["n"]


@pytest.mark.integration
class TestExportingTheSameListTwice:
    def test_the_second_export_reuses_the_order(self, test_tenant):
        tid = test_tenant["id"]
        first = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        second = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        assert not first.get("replayed")
        assert second["replayed"] is True and second["id"] == first["id"]
        assert _count(tid) == 1
        assert query_one(
            "SELECT COUNT(*)::int AS n FROM inventory_po_items WHERE tenant_id = %s",
            (tid,))["n"] == 1

    def test_a_different_list_is_a_different_order(self, test_tenant):
        tid = test_tenant["id"]
        roi_service.log_po_generation(tid, "s1", _cart(10), decisions_recorded=False)
        roi_service.log_po_generation(tid, "s1", _cart(25), decisions_recorded=False)
        assert _count(tid) == 2

    def test_once_the_order_was_sent_the_same_list_is_a_new_order(self, test_tenant):
        tid = test_tenant["id"]
        first = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        execute("UPDATE inventory_po_log SET sent_at = NOW() WHERE id = %s", (first["id"],))
        again = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        assert not again.get("replayed") and again["id"] != first["id"]
        assert _count(tid) == 2

    def test_a_cancelled_order_is_not_reused(self, test_tenant):
        tid = test_tenant["id"]
        first = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        cancel(tid, first["id"], "u1")
        again = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        assert again["id"] != first["id"]
        assert _count(tid) == 2

    def test_an_old_identical_order_is_not_reused(self, test_tenant):
        tid = test_tenant["id"]
        first = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        execute("UPDATE inventory_po_log SET generated_at = NOW() - INTERVAL '2 hours' "
                "WHERE id = %s", (first["id"],))
        again = roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        assert again["id"] != first["id"]

    def test_another_tenant_never_gets_this_tenants_order(self, test_tenant, make_tenant_user_headers):
        tid = test_tenant["id"]
        roi_service.log_po_generation(tid, "s1", _cart(), decisions_recorded=False)
        _headers, other = make_tenant_user_headers(role="analyst", return_tenant_id=True)
        mine = roi_service.log_po_generation(other, "s1", _cart(), decisions_recorded=False)
        assert not mine.get("replayed")
        assert _count(other) == 1

    def test_endpoint_second_export_returns_the_first_order_and_one_activity_row(
        self, client, analyst_headers, analyst_user, monkeypatch,
    ):
        tid = analyst_user["tenant"]["id"]
        body = {"items": _cart()}
        r1 = client.post("/api/v1/inventory/log-po?session_id=s1", json=body,
                         headers=analyst_headers)
        r2 = client.post("/api/v1/inventory/log-po?session_id=s1", json=body,
                         headers=analyst_headers)
        assert r1.status_code == 201
        assert r2.status_code == 200 and r2.json()["data"]["replayed"] is True
        assert r2.json()["data"]["id"] == r1.json()["data"]["id"]
        assert _count(tid) == 1
        assert len(_audit(tid, "purchase.order_generated")) == 1

    def test_a_viewer_cannot_log_an_order(self, client, viewer_headers, viewer_user):
        tid = viewer_user["tenant"]["id"]
        r = client.post("/api/v1/inventory/log-po?session_id=s1", json={"items": _cart()},
                        headers=viewer_headers)
        assert r.status_code == 403
        assert _count(tid) == 0


# ── 3. the tenant's calendar ─────────────────────────────────────────────────

@pytest.mark.integration
class TestMonthsAreCutInTheTenantTimezone:
    # 19:00 on March 31st in Costa Rica (UTC-6) = 01:00 UTC on April 1st.
    _LATE_MARCH_LOCAL = datetime(2026, 4, 1, 1, 0, tzinfo=UTC)

    def test_a_costa_rica_evening_order_belongs_to_march(self, test_tenant):
        from backend.tenants.service import update_settings
        tid = test_tenant["id"]
        update_settings(tid, {"timezone": "America/Costa_Rica"})
        _po(tid, self._LATE_MARCH_LOCAL, value=700)
        assert roi_service.get_month_report(tid, 2026, 3)["orders_generated"] == 1
        assert roi_service.get_month_report(tid, 2026, 4)["has_sufficient_history"] is False

    def test_the_same_order_belongs_to_april_for_a_utc_tenant(self, test_tenant):
        from backend.tenants.service import update_settings
        tid = test_tenant["id"]
        update_settings(tid, {"timezone": "UTC"})
        _po(tid, self._LATE_MARCH_LOCAL, value=700)
        assert roi_service.get_month_report(tid, 2026, 4)["orders_generated"] == 1
        assert roi_service.get_month_report(tid, 2026, 3)["has_sufficient_history"] is False

    def test_the_monthly_recap_email_waits_for_the_tenants_month_to_close(
        self, test_tenant, monkeypatch,
    ):
        """At 00:05 UTC on April 1st a Costa Rican tenant is still in March:
        nothing is mailed for March yet; once its April begins, it is."""
        from backend.inventory import service
        from backend.tenants.service import update_settings

        tid = test_tenant["id"]
        update_settings(tid, {"timezone": "America/Costa_Rica"})
        _po(tid, _MARCH, value=400)
        monkeypatch.setattr(service, "get_tenants_with_active_sessions",
                            lambda: [{"tenant_id": tid}])
        monkeypatch.setattr(service, "get_tenant_admin_emails", lambda t: ["boss@acme.cr"])
        sent_reports = []
        monkeypatch.setattr(
            "backend.notifications.email.send_monthly_roi_email",
            lambda to, report, roi_url, currency=None, **_kw:
                (sent_reports.append(report), True)[1])

        early = datetime(2026, 4, 1, 0, 5, tzinfo=UTC)        # 18:05 Mar 31 local
        roi_service.run_monthly_roi_emails(now=early)
        assert [r["month"] for r in sent_reports] == []        # February: no history
        assert query_one("SELECT id FROM inventory_roi_email_log WHERE tenant_id = %s",
                         (tid,)) is None

        later = datetime(2026, 4, 1, 7, 5, tzinfo=UTC)        # 01:05 Apr 1 local
        assert roi_service.run_monthly_roi_emails(now=later) == 1
        assert [r["month"] for r in sent_reports] == ["2026-03"]
        assert query_one("SELECT month FROM inventory_roi_email_log WHERE tenant_id = %s",
                         (tid,))["month"] == "2026-03"


# ── 4. what the numbers are called ───────────────────────────────────────────

@pytest.mark.integration
class TestNumbersAreNamedForWhatIsComputed:
    def test_the_report_has_no_stockouts_avoided_or_value_protected_field(self, test_tenant):
        tid = test_tenant["id"]
        _po(tid, _MARCH)
        report = roi_service.get_month_report(tid, 2026, 3)
        summary = roi_service.get_roi_summary(tid)
        for payload in (report, summary):
            assert not {k for k in payload if "protected" in k or "avoided" in k
                        or "stockout_risks" in k}
        assert "urgent_lines_ordered" in report and "urgent_lines_ordered" in summary
        assert "ordered_value" in summary

    def test_unknown_money_is_none_never_zero(self, test_tenant):
        tid = test_tenant["id"]
        _po(tid, _MARCH, value=None)
        assert roi_service.get_month_report(tid, 2026, 3)["managed_purchase_value"] is None
        summary = roi_service.get_roi_summary(tid)
        assert summary["ordered_value"] is None
        months = {r["month"]: r for r in roi_service.get_monthly_summary(tid, months=24)}
        assert months["2026-03"]["pos_count"] == 1
        assert months["2026-03"]["total_value"] is None

    def test_the_wire_names_are_gone_from_the_source(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        banned = ("stockout_risks_handled", "total_skus_protected",
                  "estimated_value_protected", "roi_email_metric_risks_")
        offenders = []
        for path in root.rglob("*.py"):
            if "tests" in path.parts or ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders += [f"{path.name}:{b}" for b in banned if b in text]
        assert offenders == []


# ── 5. exports leave an audit row ────────────────────────────────────────────

@pytest.mark.integration
class TestDownloadsAreAudited:
    def test_the_audit_csv_download_is_recorded_with_who_and_how_many_rows(
        self, client, auth_headers, registered_user,
    ):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        client.post("/api/v1/sessions", json={"name": "one"}, headers=auth_headers)
        r = client.get("/api/v1/audit/export", headers=auth_headers)
        assert r.status_code == 200
        rows = _audit(tid, "audit.export.audit_log")
        assert len(rows) == 1
        assert rows[0]["user_id"] == uid
        assert rows[0]["context"]["target_type"] == "audit_log"
        assert rows[0]["context"]["after"]["rows"] >= 1
        assert rows[0]["context"]["after"]["format"] == "csv"

    def test_a_viewer_cannot_download_the_audit_log_and_nothing_is_recorded(
        self, client, viewer_headers, viewer_user,
    ):
        tid = viewer_user["tenant"]["id"]
        r = client.get("/api/v1/audit/export", headers=viewer_headers)
        assert r.status_code == 403
        assert _audit(tid, "audit.export.audit_log") == []

    def test_the_inventory_pdf_download_is_recorded(
        self, client, auth_headers, registered_user, monkeypatch,
    ):
        from backend.inventory import service as inv
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        monkeypatch.setattr(inv, "generate_inventory_pdf", lambda *a, **k: b"%PDF-1.4 x")
        r = client.get("/api/v1/inventory/report/pdf?session_id=sess-1", headers=auth_headers)
        assert r.status_code == 200
        (row,) = _audit(tid, "audit.export.inventory_pdf")
        assert row["user_id"] == uid and row["resource"] == "sess-1"
        assert row["context"]["after"]["format"] == "pdf"
        assert row["context"]["after"]["bytes"] == len(b"%PDF-1.4 x")

    def test_the_po_csv_download_records_how_many_lines_left(
        self, client, auth_headers, registered_user, monkeypatch,
    ):
        from backend.inventory import service as inv
        tid = registered_user["tenant"]["id"]
        rows = [
            {"sku": "A", "signal": "PEDIR_YA", "recommended_qty": 5, "unit_cost": 1.0},
            {"sku": "B", "signal": "PEDIR_PRONTO", "recommended_qty": 3, "unit_cost": 2.0},
            {"sku": "C", "signal": "OK", "recommended_qty": 0},
        ]
        monkeypatch.setattr(inv, "get_inventory_status", lambda *a, **k: rows)
        r = client.get("/api/v1/inventory/status/export-po?session_id=sess-1",
                       headers=auth_headers)
        assert r.status_code == 200
        (row,) = _audit(tid, "audit.export.purchase_orders")
        assert row["context"]["after"]["rows"] == 2

    def test_a_session_report_download_is_recorded_and_a_missing_one_is_not(
        self, client, auth_headers, registered_user, test_session,
    ):
        from backend.storage import paths
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        sid = test_session["id"]

        missing = client.get(f"/api/v1/sessions/{sid}/reports/excel", headers=auth_headers)
        assert missing.status_code >= 400
        assert _audit(tid, "audit.export.session_report") == []

        report_dir = paths.reports_artifact_dir(tid, sid)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "report.xlsx").write_bytes(b"PK-fake")
        r = client.get(f"/api/v1/sessions/{sid}/reports/excel", headers=auth_headers)
        assert r.status_code == 200
        (row,) = _audit(tid, "audit.export.session_report")
        assert row["user_id"] == uid and row["resource"] == sid
        assert row["context"]["after"] == {"format": "excel", "bytes": len(b"PK-fake")}
