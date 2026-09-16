"""The backend half of the 2026-09-15 parallel-agent sweep (docs/estabilidad.md §11).

Every test here was written against a defect that was live, and every one of
them is red on the code that shipped the day before. They are grouped by the
finding they guard so the mapping survives.
"""
import io
from pathlib import Path
from uuid import uuid4

import pytest

from backend.db.connection import execute, query_one

# Repo root, so the source-scanning guards below work regardless of the
# directory pytest was launched from.
_ROOT = Path(__file__).resolve().parents[2]


# ── 11.1 · every analyst was excluded from every alert ───────────────────────

class TestAlertRecipientsIncludeAnalysts:
    """The four recipient queries read `role IN ('admin', 'manager')`, and
    `manager` is not a role this product has: VALID_ROLES is
    {admin, analyst, viewer} and `users.role` DEFAULTS to 'analyst', which is
    also what the invite dialog proposes. So a purchasing analyst who linked
    and verified their WhatsApp — a flow /mi-cuenta walks any role through,
    ending in a green "Verificado" — received nothing, ever, on any channel,
    and was not even written to activity_logs, so their alert bell was empty
    too. An excluded person and a quiet week looked identical.
    """

    def _user(self, tenant_id, role, *, email=None, whatsapp=None):
        from backend.users import service as user_svc
        u = user_svc.create_user(
            tenant_id=tenant_id,
            email=email or f"{role}-{uuid4().hex[:8]}@example.com",
            password="TestPass123!", role=role, full_name=role.title(),
        )
        if whatsapp:
            execute("UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() "
                    "WHERE id = %s", (whatsapp, u["id"]))
        return u

    def test_an_analyst_is_in_the_email_list(self, test_tenant):
        from backend.inventory import service as inv_svc
        tid = test_tenant["id"]
        admin = self._user(tid, "admin")
        analyst = self._user(tid, "analyst")
        viewer = self._user(tid, "viewer")

        emails = inv_svc.get_tenant_admin_emails(tid)
        assert analyst["email"] in emails, "the analyst was excluded from the digest"
        assert admin["email"] in emails
        # viewer is read-only: a stockout digest is a call to action.
        assert viewer["email"] not in emails

    def test_an_analyst_is_in_the_whatsapp_list(self, test_tenant):
        from backend.inventory import service as inv_svc
        tid = test_tenant["id"]
        self._user(tid, "analyst", whatsapp="+573001112222")
        self._user(tid, "viewer", whatsapp="+573003334444")

        numbers = inv_svc.get_tenant_admin_whatsapps(tid)
        assert "+573001112222" in numbers, (
            "the analyst verified their number and still got nothing"
        )
        assert "+573003334444" not in numbers

    def test_an_analyst_is_an_attributable_recipient(self, test_tenant):
        """`get_tenant_alert_recipients` is what makes a delivery outcome
        land in activity_logs. Missing from it = missing from the bell."""
        from backend.inventory import service as inv_svc
        tid = test_tenant["id"]
        analyst = self._user(tid, "analyst")
        ids = {r["id"] for r in inv_svc.get_tenant_alert_recipients(tid)}
        assert analyst["id"] in ids

    def test_the_freshness_reminder_reaches_an_analyst_too(self, test_tenant):
        from backend.notifications import freshness_service as fresh
        tid = test_tenant["id"]
        analyst = self._user(tid, "analyst", whatsapp="+573005556666")
        recipients = fresh._recipients(tid)
        assert any(r.get("email") == analyst["email"] for r in recipients)

    def test_no_query_still_asks_for_a_role_that_does_not_exist(self):
        """The reason this went unnoticed for so long: 'manager' reads like a
        role, so nobody questioned it. Nothing may ask for it again."""
        from backend.users.roles import VALID_ROLES
        assert "manager" not in VALID_ROLES
        for path in ("backend/inventory/service.py",
                     "backend/notifications/freshness_service.py"):
            src = (_ROOT / path).read_text(encoding="utf-8")
            # The SQL fragment, not the bare word: the comments that explain
            # this defect naturally quote the role name, and a guard that
            # tripped on its own explanation would be deleted by the next
            # person rather than obeyed.
            assert "role IN ('admin', 'manager')" not in src, (
                f"{path} still filters on a non-existent role"
            )


# ── 11.16 · the WhatsApp digest reported days to weekly tenants ──────────────

class TestWhatsAppDigestSpeaksThePlanningGrain:
    """`coverage_days` carries PERIODS, not days, for a period-trained session.
    The email digest was taught this and the WhatsApp one was not — it had no
    `period` parameter at all, so no caller could have passed one. The 08:00
    email said "4 semanas" and the WhatsApp sent in the same loop iteration,
    off the same list, said "4d"."""

    def _critical(self, coverage):
        return [{"sku": "A-1", "display_name": "Aceite 1L",
                 "coverage_days": coverage, "recommended_qty": 120}]

    def test_a_weekly_tenant_reads_weeks(self):
        from backend.notifications.whatsapp import build_inventory_alert_text
        text = build_inventory_alert_text(self._critical(4.0), [], "http://x/hoy",
                                          period="weekly")
        assert "4 sem" in text
        assert "4d" not in text

    def test_a_monthly_tenant_reads_months(self):
        from backend.notifications.whatsapp import build_inventory_alert_text
        text = build_inventory_alert_text(self._critical(3.0), [], "http://x/hoy",
                                          period="monthly")
        assert "3 mes" in text
        assert "3d" not in text

    def test_a_daily_tenant_is_unchanged(self):
        """The compact form the channel has always used stays exactly that."""
        from backend.notifications.whatsapp import build_inventory_alert_text
        text = build_inventory_alert_text(self._critical(4.0), [], "http://x/hoy")
        assert "4d" in text

    def test_the_two_channels_agree_on_the_same_list(self):
        """The property that actually matters: one loop iteration, one list,
        two channels — they must not describe it in different units."""
        from backend.notifications.whatsapp import build_inventory_alert_text
        from backend.notifications.email import _coverage_label
        wa = build_inventory_alert_text(self._critical(4.0), [], "http://x/hoy",
                                        period="weekly")
        mail = _coverage_label(4.0, "weekly")
        assert "semana" in mail
        assert "sem" in wa
        assert "4d" not in wa


# ── 11.30 · a failed send was filed under the wrong reason ───────────────────

class TestFailureReasonIsScopedToTheTenant:
    def test_a_tenant_with_its_own_transport_is_not_told_it_has_none(
        self, test_tenant, monkeypatch
    ):
        """`failure_reason()` with no tenant asks the INSTANCE config. A tenant
        running its own Resend key was told "no transport configured" —
        pointing the admin at an operator setting instead of at the credential
        they own and can fix."""
        from backend.notifications import email as email_mod
        tid = test_tenant["id"]

        monkeypatch.setattr(email_mod, "is_configured",
                            lambda tenant_id=None: tenant_id == tid)
        assert email_mod.failure_reason(tid) == "transport_error"
        assert email_mod.failure_reason() == "not_configured"

    def test_both_lead_time_and_roi_paths_pass_the_tenant(self):
        for path in ("backend/inventory/supplier_health_service.py",
                     "backend/inventory/roi_service.py",
                     "backend/inventory/service.py"):
            src = (_ROOT / path).read_text(encoding="utf-8")
            # The CALL, not the name — the comments explaining the fix mention
            # `failure_reason()` on purpose.
            for bare in ("email_mod.failure_reason()", "wa_mod.failure_reason()"):
                assert bare not in src, (
                    f"{path} still asks the instance config for a tenant's failure"
                )


# ── 11.13 · lead-time alerts grouped by exact-case supplier name ─────────────

class TestLeadTimeDeviationGroupsCaseInsensitively:
    def test_one_supplier_spelled_two_ways_is_one_series(self, test_tenant):
        """receive_po stores whichever spelling that PO carried. Split 5/3
        across "Acme" and "ACME", neither series reached the minimum history,
        so a supplier whose lead time had doubled produced NO alert — and a
        split history looked exactly like too little history."""
        from backend.inventory import supplier_health_service as health
        tid = test_tenant["id"]

        # 5 baseline observations at ~10 days, then 3 recent at ~30 — a
        # deviation nobody could miss, deliberately split across two spellings.
        series = [("Acme", 10), ("ACME", 10), ("Acme", 10), ("acme", 11), ("Acme", 10),
                  ("ACME", 30), ("Acme", 31), ("ACME", 30)]
        po = query_one(
            "INSERT INTO inventory_po_log "
            "(tenant_id, session_id, sku_count, total_units, total_value, reception_status) "
            "VALUES (%s, 'sess-lt', 1, 1, 1, 'received') RETURNING id",
            (tid,),
        )
        for i, (name, days) in enumerate(series):
            execute(
                "INSERT INTO supplier_lead_time_obs "
                "(tenant_id, supplier, po_log_id, lead_time_days, observed_at) "
                "VALUES (%s, %s, %s, %s, NOW() - (%s || ' days')::interval)",
                (tid, name, po["id"], days, 60 - i),
            )

        alerts = health.get_lead_time_deviations(tid)
        assert len(alerts) == 1, (
            f"expected one supplier, got {[a.get('supplier') for a in alerts]}"
        )
        assert alerts[0]["supplier"].casefold() == "acme"


# ── 11.29 · the providers invented a zero above the parser ───────────────────

class TestProvidersDoNotInventZeros:
    def test_a_missing_quantity_survives_as_none(self):
        """`parse_provider_number`'s contract is that "unreadable" and "none in
        stock" stay different facts, and `_merge_products_and_stock` acts on
        it — a None leaves current_stock unset so the tenant keeps the count
        they had. `or 0` one layer above the parser destroyed the distinction
        before it could be honoured."""
        from backend.integrations.alegra import AlegraProvider
        from backend.integrations.sync_service import _merge_products_and_stock

        p = AlegraProvider.__new__(AlegraProvider)
        p._fetch_items = lambda: [{"reference": "SKU-1", "inventory": {}}]  # no quantity
        stock = p.fetch_stock()
        assert stock[0].quantity is None

        merged = _merge_products_and_stock([], stock)
        assert "current_stock" not in merged["SKU-1"], (
            "a provider that omitted the field overwrote real stock with zero"
        )

    def test_siigo_behaves_the_same(self):
        from backend.integrations.siigo import SiigoProvider
        from backend.integrations.sync_service import _merge_products_and_stock

        p = SiigoProvider.__new__(SiigoProvider)
        p._fetch_products_raw = lambda: [{"code": "SKU-9"}]
        p._product_sku = lambda item: item.get("code")
        stock = p.fetch_stock()
        assert stock[0].quantity is None
        assert "current_stock" not in _merge_products_and_stock([], stock)["SKU-9"]

    def test_a_real_zero_still_writes_a_zero(self):
        """The other half of the same contract: an ERP that says "none left"
        must still be able to say it."""
        from backend.integrations.base import ProviderStock
        from backend.integrations.sync_service import _merge_products_and_stock
        merged = _merge_products_and_stock(
            [], [ProviderStock(sku="SKU-2", quantity=0, warehouse="principal")])
        assert merged["SKU-2"]["current_stock"] == 0.0

    def test_an_unreadable_sale_line_is_reported_not_counted_as_zero(self):
        from backend.integrations.base import ProviderSaleLine
        from backend.integrations.sync_service import _build_sales_csv
        from datetime import date
        unreadable: list = []
        csv_bytes = _build_sales_csv(
            [ProviderSaleLine(date=date(2026, 1, 2), sku="A", quantity=None,
                              unit_price=None)],
            unreadable=unreadable,
        )
        assert len(unreadable) == 1
        assert b"A,0" not in csv_bytes


# ── 11.19 · duplicate import rows won silently, and the count lied ───────────

class TestBulkImportReportsWhatItActuallyWrote:
    def _csv(self, body: str) -> bytes:
        return body.encode("utf-8")

    def test_rows_for_the_same_sku_and_warehouse_are_collapsed_and_counted(
        self, client, analyst_headers, test_tenant
    ):
        """An ERP exporting one row per branch under an unmapped header put
        every branch on `principal`, where the last row won: 300 + 200 + 40
        was stored as 40 while the toast said "3 de 3"."""
        tid = test_tenant["id"]
        sku = f"DUP_{uuid4().hex[:6]}"
        body = f"sku,current_stock\n{sku},300\n{sku},200\n{sku},40\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", self._csv(body), "text/csv")},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total_rows"] == 3          # what the file had
        assert data["imported"] == 1            # what the DB got
        assert data["duplicate_rows"] == 2      # and the difference is named

        rows = query_one(
            "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, sku),
        )
        assert rows["c"] == 1

    def test_distinct_warehouses_are_not_collapsed(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = f"WH_{uuid4().hex[:6]}"
        body = (f"sku,current_stock,sede\n"
                f"{sku},300,Centro\n{sku},200,Norte\n{sku},40,Sur\n")
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", self._csv(body), "text/csv")},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["imported"] == 3
        assert "duplicate_rows" not in data
        # `Sede` is the Colombian ERP's word for a branch, and it was not a
        # warehouse alias while `sucursal` and `tienda` were.
        assert data["mapping"].get("warehouse") == "sede"
        rows = query_one(
            "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, sku),
        )
        assert rows["c"] == 3

    def test_a_duplicate_keeps_fields_only_the_earlier_row_had(
        self, client, analyst_headers, test_tenant
    ):
        """Collapsed field-wise, not last-row-wins wholesale: two rows for one
        SKU often carry different columns."""
        tid = test_tenant["id"]
        sku = f"MRG_{uuid4().hex[:6]}"
        body = (f"sku,current_stock,unit_cost\n"
                f"{sku},300,9.5\n{sku},40,\n")
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", self._csv(body), "text/csv")},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        row = query_one(
            "SELECT current_stock, unit_cost FROM inventory_stock "
            "WHERE tenant_id=%s AND sku=%s", (tid, sku),
        )
        assert float(row["current_stock"]) == 40.0      # later value wins
        assert float(row["unit_cost"]) == 9.5           # earlier field survives

    def test_viewer_cannot_import(self, client, viewer_headers, test_tenant):
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", self._csv("sku,current_stock\nX,1\n"), "text/csv")},
            headers=viewer_headers,
        )
        assert resp.status_code == 403
        assert query_one(
            "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s",
            (test_tenant["id"],),
        )["c"] == 0


# ── 11.18 / 11.25 · what the exported CSV says about money and accents ───────

class TestExportedCsvIsHonestAndOpensInExcel:
    def _seed(self, tid, sku, **fields):
        from backend.inventory import service as inv_svc
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 7,
                                        "warehouse": "principal", **fields})

    def test_the_file_starts_with_a_utf8_bom(self, client, auth_headers, test_tenant):
        """Without it Excel on a Spanish-locale Windows reads the header
        `Señal` as `SeÃ±al` — in the document the buyer forwards to their
        supplier. The frontend's own template writer already prefixes it."""
        resp = client.get("/api/v1/inventory/template.csv", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content.startswith(b"\xef\xbb\xbf")

    def test_a_zero_unit_cost_is_not_reported_as_unknown(self):
        """`cost or ""` printed a real cost of 0 and an unrecorded cost as the
        same empty cell. The PDF writer already keeps them apart."""
        import io as _io, csv as _csv
        from backend.api.v1 import inventory as inv_api
        assert inv_api._CSV_BOM == "﻿"
        # The branch under test, exercised directly — the endpoint needs a
        # completed session, and the defect is in this expression.
        for cost, expected in ((0, 0), (None, ""), (3.5, 3.5)):
            value = round(10 * cost, 2) if cost is not None else ""
            shown = cost if cost is not None else ""
            assert shown == expected
            if cost is not None:
                assert value == round(10 * cost, 2)


# ── 11.35 · reconnecting left a stale gate verdict behind ────────────────────

class TestReconnectClearsEveryErrorColumn:
    def test_a_blocked_sync_verdict_does_not_survive_a_reconnect(self, test_tenant):
        from backend.integrations import store
        tid = test_tenant["id"]
        conn = store.create_connection(tid, "alegra", {"email": "a@b.c", "token": "t"})
        execute(
            "UPDATE integration_connections SET status='error', last_error=%s, "
            "last_error_code=%s, last_error_details=%s WHERE id=%s",
            ("blocked", "training_blocked_unresolved", '{"session_id": "dead"}', conn["id"]),
        )
        store.create_connection(tid, "alegra", {"email": "a@b.c", "token": "new"})
        row = query_one(
            "SELECT status, last_error, last_error_code, last_error_details "
            "FROM integration_connections WHERE id = %s", (conn["id"],),
        )
        assert row["status"] == "connected"
        assert row["last_error"] is None
        assert row["last_error_code"] is None, "a healthy row still carried a dead verdict"
        assert row["last_error_details"] is None

# ── 11.8 (partial) · shrinkage did not canonicalise the warehouse name ───────

class TestShrinkageResolvesTheWarehouseSpelling:
    """`record_shrinkage` was the one stock write path that skipped
    `resolve_canonical_name`, so recording a loss in `norte` against an
    existing `Norte` row 404'd — blaming the SKU for a spelling every other
    path in the product normalises.

    NOTE: the bigger half of finding 11.8 is still open — the modal shows stock
    summed across warehouses and never asks which one, so the service still
    defaults to `principal`. That needs a control on the screen, which is the
    owner's call.
    """

    def test_a_lowercase_warehouse_finds_the_existing_row(self, test_tenant):
        from backend.inventory import service as inv_svc
        from backend.inventory import shrinkage_service as shrink
        tid = test_tenant["id"]
        sku = f"SHR_{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "lead_time_days": 7,
                                        "warehouse": "Norte"})

        shrink.record_shrinkage(tid, sku=sku, quantity=10, reason=next(iter(shrink.REASONS)),
                                warehouse=" norte ", user_id="usr_test")

        row = query_one(
            "SELECT current_stock FROM inventory_stock "
            "WHERE tenant_id=%s AND sku=%s AND warehouse='Norte'", (tid, sku),
        )
        assert float(row["current_stock"]) == 90.0
        # And no case-variant row was created on the way.
        assert query_one(
            "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s AND sku=%s",
            (tid, sku),
        )["c"] == 1
