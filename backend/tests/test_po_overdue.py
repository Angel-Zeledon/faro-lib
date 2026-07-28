"""
Tests for the overdue-reception detector (feature: /hoy 'did it arrive?'
nudge). Flags POs still pending/partial whose expected arrival — order date
plus the supplier's already-learned lead time — has passed. No forecasting
logic here: pure date arithmetic over data already recorded by feature 1.4
(reception) and the supplier scorecard.
"""

import uuid
from datetime import datetime, timedelta, timezone

from backend.db.connection import execute


def _make_supplier(tenant_id: str, name: str, lead_time_days: int = 10) -> None:
    """A supplier whose lead time the user actually filled in.

    `lead_time_set_by` has to be written here. The column is NOT NULL DEFAULT
    15, so without provenance "the card says 15" and "nobody ever filled the
    card" are the same row — and `_effective_lead_time` now (correctly) refuses
    to report our own assumption as the supplier's declared time. Inserting raw
    SQL bypasses `supplier_service`, which sets this on the real path.
    """
    execute(
        "INSERT INTO suppliers (tenant_id, name, lead_time_days, lead_time_set_by) "
        "VALUES (%s, %s, %s, 'user')",
        (tenant_id, name, lead_time_days),
    )


def _make_po(client, auth_headers, *, sku: str, qty: float, supplier: str, unit_cost: float = 2.0) -> str:
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": [{
            "sku": sku, "display_name": f"Prod {sku}", "supplier": supplier,
            "signal": "PEDIR_YA", "recommended_qty": qty,
            "final_qty": qty, "unit_cost": unit_cost, "status": "approved",
        }]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _set_generated_at(po_log_id: str, dt: datetime) -> None:
    execute(
        "UPDATE inventory_po_log SET generated_at = %s WHERE id = %s",
        (dt, po_log_id),
    )


class TestGetOverdueReceptions:
    def test_flags_po_past_declared_lead_time(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_overdue_receptions

        tid = test_tenant["id"]
        prov = f"Overdue-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=5)

        sku = f"OD1-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=10, supplier=prov)
        # Generated 10 days ago; declared lead time is 5 days -> 5 days overdue.
        _set_generated_at(po, datetime.now(timezone.utc) - timedelta(days=10))

        rows = get_overdue_receptions(tid)
        row = next(r for r in rows if r["po_log_id"] == po)

        assert row["supplier"] == prov
        # 'supplier_rule', not 'declared': this module and the SKU card used to
        # answer the same question with two different vocabularies.
        assert row["lead_time_source"] == "supplier_rule"
        assert row["lead_time_used"] == 5.0
        assert row["days_overdue"] >= 4  # ~5 days, allow for clock-boundary rounding

    def test_not_overdue_within_lead_time(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_overdue_receptions

        tid = test_tenant["id"]
        prov = f"OnTime-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=15)

        sku = f"OD2-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=10, supplier=prov)
        # Generated just now; lead time is 15 days -> nowhere near overdue.

        rows = get_overdue_receptions(tid)
        assert not any(r["po_log_id"] == po for r in rows)

    def test_received_po_excluded_even_if_old(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_overdue_receptions

        tid = test_tenant["id"]
        prov = f"Received-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=3)

        sku = f"OD3-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=10, supplier=prov)
        _set_generated_at(po, datetime.now(timezone.utc) - timedelta(days=30))

        resp = client.post(f"/api/v1/inventory/po/{po}/receive", json={}, headers=auth_headers)
        assert resp.status_code == 200

        rows = get_overdue_receptions(tid)
        assert not any(r["po_log_id"] == po for r in rows)

    def test_prefers_observed_lead_time_over_declared(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_overdue_receptions

        tid = test_tenant["id"]
        prov = f"Learned-{uuid.uuid4().hex[:6]}"
        # Declared lead time says 20 days (would NOT be overdue at day 8),
        # but this supplier has real observations averaging 3 days.
        _make_supplier(tid, prov, lead_time_days=20)
        # supplier_lead_time_obs.po_log_id FKs into inventory_po_log, so the
        # seed observations need real (already-received) POs behind them.
        # At least MIN_LEAD_TIME_OBSERVATIONS of them: below that threshold the
        # learned average is intentionally ignored (see the thin-evidence test).
        seed_po_a = _make_po(client, auth_headers, sku=f"SEED-A-{uuid.uuid4().hex[:6]}", qty=1, supplier=prov)
        seed_po_b = _make_po(client, auth_headers, sku=f"SEED-B-{uuid.uuid4().hex[:6]}", qty=1, supplier=prov)
        seed_po_c = _make_po(client, auth_headers, sku=f"SEED-C-{uuid.uuid4().hex[:6]}", qty=1, supplier=prov)
        execute(
            """INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days)
               VALUES (%s, %s, %s, %s), (%s, %s, %s, %s), (%s, %s, %s, %s)""",
            (tid, prov, seed_po_a, 2.0, tid, prov, seed_po_b, 4.0, tid, prov, seed_po_c, 3.0),
        )

        sku = f"OD4-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=10, supplier=prov)
        _set_generated_at(po, datetime.now(timezone.utc) - timedelta(days=8))

        rows = get_overdue_receptions(tid)
        row = next(r for r in rows if r["po_log_id"] == po)

        # 'learned' is the unified name for "evidence from real deliveries".
        assert row["lead_time_source"] == "learned"
        assert row["lead_time_used"] == 3.0  # avg(2.0, 4.0)
        assert row["days_overdue"] >= 4

    def test_ignores_observed_lead_time_below_minimum(self, client, auth_headers, test_tenant):
        """With fewer than MIN_LEAD_TIME_OBSERVATIONS receptions, one atypical
        delivery must NOT rewrite the supplier's lead time. The overdue detector
        falls back to the declared value until the evidence is thick enough —
        consistent with the semaphore calc (get_learned_lead_times)."""
        from backend.inventory.reception_service import get_overdue_receptions
        from backend.inventory.service import MIN_LEAD_TIME_OBSERVATIONS

        tid = test_tenant["id"]
        prov = f"Thin-{uuid.uuid4().hex[:6]}"
        # Declared 20 days (NOT overdue at day 8). Two freak-fast observations
        # (below the minimum) must be ignored, so the PO is judged on 20 days.
        _make_supplier(tid, prov, lead_time_days=20)
        seed_pos = [
            _make_po(client, auth_headers, sku=f"THIN-{i}-{uuid.uuid4().hex[:6]}", qty=1, supplier=prov)
            for i in range(MIN_LEAD_TIME_OBSERVATIONS - 1)
        ]
        for sp in seed_pos:
            execute(
                """INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days)
                   VALUES (%s, %s, %s, %s)""",
                (tid, prov, sp, 2.0),
            )

        sku = f"OD-THIN-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=10, supplier=prov)
        _set_generated_at(po, datetime.now(timezone.utc) - timedelta(days=8))

        rows = get_overdue_receptions(tid)
        # Judged on the declared 20 days -> at day 8 it is NOT overdue.
        assert not any(r["po_log_id"] == po for r in rows), (
            "thin evidence (n < MIN_LEAD_TIME_OBSERVATIONS) wrongly rewrote the lead time"
        )

    def test_po_with_no_supplier_name_excluded(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_overdue_receptions

        tid = test_tenant["id"]
        sku = f"OD5-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=10, supplier="")
        _set_generated_at(po, datetime.now(timezone.utc) - timedelta(days=60))

        rows = get_overdue_receptions(tid)
        assert not any(r["po_log_id"] == po for r in rows)


class TestOverdueEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/po/overdue", headers=viewer_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/po/overdue")
        assert resp.status_code == 401

    def test_analyst_sees_overdue_po_via_endpoint(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        prov = f"EndOverdue-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=4)

        sku = f"OD6-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=10, supplier=prov)
        _set_generated_at(po, datetime.now(timezone.utc) - timedelta(days=20))

        resp = client.get("/api/v1/inventory/po/overdue", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert any(r["po_log_id"] == po and r["supplier"] == prov for r in rows)
