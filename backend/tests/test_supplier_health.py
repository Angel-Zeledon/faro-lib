"""
Tests for supplier data health (feature 2.5) and supplier lead-time
deviation detection (feature 3.3).

Both features exist to stop a silent failure:
  2.5  POST /po/{id}/send skips suppliers with no contact channel without
       telling anyone. The health endpoint must flag EXACTLY that set — the
       tests below pin the two together so they cannot drift apart.
  3.3  The deviation rule must fire on a real shift and stay quiet on noise.
       Both directions are tested; a detector that only ever fires is as
       useless as one that never does.
"""

import uuid
from unittest import mock

import pytest

from backend.db.connection import execute, query_one
from backend.inventory import supplier_health_service as health_svc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_supplier(tenant_id: str, name: str, *, email=None, whatsapp=None,
                   lead_time_dias: int = 10, active: bool = True) -> str:
    row = query_one(
        """INSERT INTO suppliers (tenant_id, name, email, whatsapp, lead_time_dias, active)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (tenant_id, name, email, whatsapp, lead_time_dias, active),
    )
    return row["id"]


def _make_po(client, headers, *, sku: str, proveedor: str, qty: float = 10.0) -> str:
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": [{
            "sku": sku, "display_name": f"Prod {sku}", "proveedor": proveedor,
            "signal": "PEDIR_YA", "cantidad_recomendada": qty,
            "cantidad_final": qty, "costo_unitario": 1.0, "status": "approved",
        }]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _row_for(rows: list[dict], proveedor: str) -> dict | None:
    return next((r for r in rows if r["proveedor"] == proveedor), None)


# ══════════════════════════════════════════════════════════════════════════════
# 2.5 — Supplier contact health
# ══════════════════════════════════════════════════════════════════════════════

class TestContactHealthDetection:
    def test_supplier_without_email_or_whatsapp_is_flagged(self, client, test_tenant):
        tid = test_tenant["id"]
        prov = f"SinContacto-{uuid.uuid4().hex[:6]}"
        sup_id = _make_supplier(tid, prov)

        row = _row_for(health_svc.get_contact_health(tid), prov)

        assert row is not None, "supplier with no contact channel must be flagged"
        assert row["motivo"] == "sin_contacto"
        assert row["supplier_id"] == sup_id
        assert row["tiene_email"] is False
        assert row["tiene_whatsapp"] is False

    def test_supplier_with_email_is_not_flagged(self, client, test_tenant):
        """Negative case: having ONE channel is enough for the send path."""
        tid = test_tenant["id"]
        prov = f"ConEmail-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, email="ventas@acme.test")

        assert _row_for(health_svc.get_contact_health(tid), prov) is None

    def test_supplier_with_only_whatsapp_is_not_flagged(self, client, test_tenant):
        tid = test_tenant["id"]
        prov = f"ConWA-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, whatsapp="+50588887777")

        assert _row_for(health_svc.get_contact_health(tid), prov) is None

    def test_phone_alone_does_not_count_as_contact(self, client, test_tenant):
        """phone is not used by the send path, so it must not clear the flag."""
        tid = test_tenant["id"]
        prov = f"SoloTel-{uuid.uuid4().hex[:6]}"
        execute(
            "INSERT INTO suppliers (tenant_id, name, phone) VALUES (%s, %s, %s)",
            (tid, prov, "+50522223333"),
        )

        row = _row_for(health_svc.get_contact_health(tid), prov)
        assert row is not None
        assert row["motivo"] == "sin_contacto"

    def test_proveedor_on_po_with_no_supplier_record_is_flagged(self, client, auth_headers, test_tenant):
        """The easiest gap to miss: a name typed on a PO that has no ficha."""
        tid = test_tenant["id"]
        prov = f"Fantasma-{uuid.uuid4().hex[:6]}"
        _make_po(client, auth_headers, sku=f"GH-{uuid.uuid4().hex[:6]}", proveedor=prov)

        row = _row_for(health_svc.get_contact_health(tid), prov)

        assert row is not None, "proveedor with no ficha must be flagged"
        assert row["motivo"] == "sin_ficha"
        assert row["supplier_id"] is None

    def test_inactive_supplier_is_not_flagged(self, client, test_tenant):
        tid = test_tenant["id"]
        prov = f"Inactivo-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, active=False)

        assert _row_for(health_svc.get_contact_health(tid), prov) is None

    def test_tenant_isolation(self, client, test_tenant):
        """A flagged supplier must not leak into another tenant's health list."""
        from backend.tenants.service import create_tenant
        tid = test_tenant["id"]
        prov = f"Aislado-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov)

        other = create_tenant(f"pytest-other-{uuid.uuid4().hex[:8]}")
        try:
            assert _row_for(health_svc.get_contact_health(other["id"]), prov) is None
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


class TestContactHealthRelevance:
    def test_open_po_marks_supplier_as_relevant(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        prov = f"Pendiente-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov)
        _make_po(client, auth_headers, sku=f"REL-{uuid.uuid4().hex[:6]}", proveedor=prov)

        row = _row_for(health_svc.get_contact_health(tid), prov)

        assert row["en_ordenes_pendientes"] is True
        assert row["ordenes_pendientes"] == 1

    def test_supplier_with_no_orders_is_flagged_but_not_relevant(self, client, test_tenant):
        """Still returned — the /hoy cart filter needs the full flagged set —
        but marked dormant so surfaces can leave it out."""
        tid = test_tenant["id"]
        prov = f"Dormido-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov)

        row = _row_for(health_svc.get_contact_health(tid), prov)

        assert row is not None
        assert row["en_ordenes_pendientes"] is False
        assert row["ordenes_pendientes"] == 0

    def test_received_po_no_longer_counts_as_relevant(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        prov = f"Recibido-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov)
        sku = f"RCV-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, proveedor=prov)

        before = _row_for(health_svc.get_contact_health(tid), prov)
        assert before["ordenes_pendientes"] == 1

        resp = client.post(f"/api/v1/inventory/po/{po}/receive", json={}, headers=auth_headers)
        assert resp.status_code == 200, resp.text

        after = _row_for(health_svc.get_contact_health(tid), prov)
        assert after["ordenes_pendientes"] == 0
        assert after["en_ordenes_pendientes"] is False


class TestContactHealthMatchesSendBehaviour:
    """The warning's whole value is that it predicts the send outcome. If
    these ever disagree, the banner is lying to the buyer."""

    def test_flagged_suppliers_are_exactly_the_ones_send_skips(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        ok_prov      = f"Contactable-{uuid.uuid4().hex[:6]}"
        no_contact   = f"SinCanal-{uuid.uuid4().hex[:6]}"
        no_ficha     = f"SinFicha-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, ok_prov, email="ok@acme.test")
        _make_supplier(tid, no_contact)

        # One PO spanning all three suppliers.
        resp = client.post(
            "/api/v1/inventory/log-po",
            params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
            json={"items": [
                {"sku": f"A-{uuid.uuid4().hex[:5]}", "proveedor": p, "signal": "PEDIR_YA",
                 "cantidad_recomendada": 5, "cantidad_final": 5,
                 "costo_unitario": 1.0, "status": "approved"}
                for p in (ok_prov, no_contact, no_ficha)
            ]},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        po_id = resp.json()["data"]["id"]

        flagged = {
            r["proveedor"] for r in health_svc.get_contact_health(tid)
            if r["en_ordenes_pendientes"]
        }

        send = client.post(f"/api/v1/inventory/po/{po_id}/send", headers=auth_headers)
        assert send.status_code == 200, send.text
        skipped = {s["supplier"] for s in send.json()["data"]["skipped"]}

        assert skipped == {no_contact, no_ficha}
        assert flagged == skipped, (
            f"health endpoint promised {flagged} would be skipped, send skipped {skipped}"
        )


class TestContactHealthEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/suppliers/contact-health", headers=viewer_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/suppliers/contact-health")
        assert resp.status_code == 401

    def test_endpoint_returns_the_flagged_supplier(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        prov = f"Endpoint-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov)

        resp = client.get("/api/v1/inventory/suppliers/contact-health", headers=auth_headers)
        assert resp.status_code == 200
        assert _row_for(resp.json()["data"], prov) is not None


class TestFixingContactDataPermissionPair:
    """The banner's one-click CTA leads to PATCH /suppliers/{id}. That is the
    mutating step of feature 2.5, so it carries the permission pair: viewer
    denied with the row untouched, analyst succeeds with the row changed."""

    def test_viewer_cannot_fix_contact_and_row_is_unchanged(
        self, client, viewer_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        prov = f"PermV-{uuid.uuid4().hex[:6]}"
        sup_id = _make_supplier(tid, prov)

        resp = client.patch(
            f"/api/v1/inventory/suppliers/{sup_id}",
            json={"email": "nuevo@acme.test"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

        # State unchanged in the DB…
        row = query_one("SELECT email, whatsapp FROM suppliers WHERE id = %s", (sup_id,))
        assert row["email"] is None
        assert row["whatsapp"] is None
        # …and therefore still flagged.
        assert _row_for(health_svc.get_contact_health(tid), prov) is not None

    def test_analyst_can_fix_contact_and_supplier_stops_being_flagged(
        self, client, analyst_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        prov = f"PermA-{uuid.uuid4().hex[:6]}"
        sup_id = _make_supplier(tid, prov)
        assert _row_for(health_svc.get_contact_health(tid), prov) is not None

        resp = client.patch(
            f"/api/v1/inventory/suppliers/{sup_id}",
            json={"email": "compras@acme.test"},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text

        row = query_one("SELECT email FROM suppliers WHERE id = %s", (sup_id,))
        assert row["email"] == "compras@acme.test"
        assert _row_for(health_svc.get_contact_health(tid), prov) is None


# ══════════════════════════════════════════════════════════════════════════════
# 3.3 — Lead-time deviation
# ══════════════════════════════════════════════════════════════════════════════

class TestDeviationRuleFires:
    """Series that MUST trigger an alert."""

    def test_clear_sustained_shift_is_detected(self):
        # Baseline hovers around 7 days, the last three land around 12.
        series = [7, 8, 7, 6, 7, 12, 12, 13]
        alert = health_svc._evaluate_supplier("Acme", series)

        assert alert is not None, "a 5-day sustained shift must fire"
        assert alert["lead_time_historico"] == 7.0
        assert alert["lead_time_reciente"] == 12.0
        assert alert["desviacion_dias"] == 5.0
        assert alert["z_score"] >= health_svc.Z_THRESHOLD
        assert alert["n_baseline"] == 5
        assert alert["n_reciente"] == 3
        assert alert["mensaje"] == "Acme está tardando 12 días, no 7"

    def test_shift_from_a_perfectly_consistent_supplier_is_detected(self):
        """MAD == 0 here; the sigma floor must still let a real shift through."""
        series = [7, 7, 7, 7, 7, 11, 11, 11]
        alert = health_svc._evaluate_supplier("Consistente", series)

        assert alert is not None
        assert alert["desviacion_dias"] == 4.0
        # Floor is max(1.0, 0.10 * 7) = 1.0 -> z = 4.0
        assert alert["sigma"] == 1.0
        assert alert["z_score"] == 4.0
        assert alert["severidad"] == "media"

    def test_extreme_shift_is_marked_high_severity(self):
        series = [5, 5, 5, 5, 5, 30, 30, 30]
        alert = health_svc._evaluate_supplier("Desastre", series)

        assert alert is not None
        assert alert["z_score"] >= 2 * health_svc.Z_THRESHOLD
        assert alert["severidad"] == "alta"

    def test_minimum_viable_history_fires(self):
        """Exactly MIN_BASELINE + MIN_RECENT observations."""
        series = [6, 6, 6, 6, 14, 14]
        alert = health_svc._evaluate_supplier("Justo", series)

        assert alert is not None
        assert alert["n_baseline"] == health_svc.MIN_BASELINE
        assert alert["n_reciente"] == health_svc.MIN_RECENT


class TestDeviationRuleStaysQuiet:
    """Series that MUST NOT trigger an alert. A detector that fires on
    everything gets ignored, which is the same as not existing."""

    def test_stable_supplier_does_not_fire(self):
        series = [7, 8, 7, 6, 7, 7, 8, 7]
        assert health_svc._evaluate_supplier("Estable", series) is None

    def test_naturally_erratic_supplier_does_not_fire(self):
        """Wide historical spread -> a large sigma -> the same absolute delay
        is not evidence of a shift. This is the MAD doing its job."""
        series = [4, 14, 6, 18, 5, 16, 15, 17]
        assert health_svc._evaluate_supplier("Errático", series) is None

    def test_supplier_getting_faster_does_not_fire(self):
        """One-sided by design: good news is not an alert."""
        series = [14, 15, 14, 13, 14, 5, 5, 6]
        assert health_svc._evaluate_supplier("Mejorando", series) is None

    def test_single_freak_late_delivery_does_not_fire(self):
        """The recent window is summarised by its MEDIAN, so one outlier in a
        window of three cannot move it."""
        series = [7, 7, 7, 7, 7, 7, 40, 7]
        assert health_svc._evaluate_supplier("UnRetraso", series) is None

    def test_tiny_wobble_from_a_consistent_supplier_does_not_fire(self):
        """MAD == 0 would make z infinite; the practical-significance floor
        keeps a half-day drift off the buyer's screen."""
        series = [7, 7, 7, 7, 7, 7.5, 7.5, 7.5]
        assert health_svc._evaluate_supplier("Casi", series) is None

    @pytest.mark.parametrize("series", [
        [],
        [10],
        [10, 20],
        [7, 7, 7, 15, 15],          # baseline of 3 < MIN_BASELINE
    ])
    def test_insufficient_history_does_not_fire(self, series):
        assert health_svc._evaluate_supplier("Nuevo", series) is None


class TestDeviationFromRealReceptions:
    """End-to-end through the real reception path, not synthetic series."""

    def _observe(self, tid: str, proveedor: str, days: float, seq: int) -> None:
        """Writes one real supplier_lead_time_obs row via a real PO row, so
        the reading query and its ordering are exercised too."""
        po = query_one(
            """INSERT INTO inventory_po_log (tenant_id, session_id, sku_count)
               VALUES (%s, %s, %s) RETURNING id""",
            (tid, f"sess_{uuid.uuid4().hex[:6]}", 1),
        )
        execute(
            """INSERT INTO supplier_lead_time_obs
                   (tenant_id, proveedor, po_log_id, lead_time_days, observed_at)
               VALUES (%s, %s, %s, %s, NOW() + (%s || ' seconds')::interval)""",
            (tid, proveedor, po["id"], days, seq),
        )

    def test_shifted_supplier_appears_stable_supplier_does_not(self, client, test_tenant):
        tid = test_tenant["id"]
        late   = f"Tarde-{uuid.uuid4().hex[:6]}"
        steady = f"Firme-{uuid.uuid4().hex[:6]}"

        for i, d in enumerate([7, 7, 8, 7, 7, 12, 12, 13]):
            self._observe(tid, late, d, i)
        for i, d in enumerate([9, 9, 10, 9, 9, 9, 10, 9]):
            self._observe(tid, steady, d, i)

        alerts = health_svc.get_lead_time_deviations(tid)
        names = {a["proveedor"] for a in alerts}

        assert late in names, "supplier that drifted late must be reported"
        assert steady not in names, "stable supplier must not be reported"

        row = next(a for a in alerts if a["proveedor"] == late)
        assert row["lead_time_historico"] == 7.0
        assert row["lead_time_reciente"] == 12.0

    def test_receiving_pos_late_produces_an_alert(self, client, auth_headers, test_tenant):
        """Drives supplier_lead_time_obs purely through POST /po/{id}/receive."""
        tid = test_tenant["id"]
        prov = f"RealPO-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_dias=5)

        # 5 baseline receptions at ~5 days, then 3 at ~15 days.
        plan = [
            ("2026-01-01", "2026-01-06"), ("2026-01-10", "2026-01-15"),
            ("2026-01-20", "2026-01-25"), ("2026-02-01", "2026-02-06"),
            ("2026-02-10", "2026-02-15"),
            ("2026-03-01", "2026-03-16"), ("2026-03-20", "2026-04-04"),
            ("2026-04-10", "2026-04-25"),
        ]
        for ordered_on, arrived_on in plan:
            sku = f"LT-{uuid.uuid4().hex[:6]}"
            po = _make_po(client, auth_headers, sku=sku, proveedor=prov)
            execute(
                "UPDATE inventory_po_log SET generated_at = %s WHERE id = %s",
                (f"{ordered_on}T00:00:00Z", po),
            )
            resp = client.post(
                f"/api/v1/inventory/po/{po}/receive",
                json={"received_at": f"{arrived_on}T00:00:00Z"},
                headers=auth_headers,
            )
            assert resp.status_code == 200, resp.text

        alerts = health_svc.get_lead_time_deviations(tid)
        row = next((a for a in alerts if a["proveedor"] == prov), None)

        assert row is not None, "8 real receptions shifting 5d -> 15d must alert"
        assert row["lead_time_historico"] == 5.0
        assert row["lead_time_reciente"] == 15.0
        assert row["mensaje"] == f"{prov} está tardando 15 días, no 5"

    def test_tenant_isolation(self, client, test_tenant):
        from backend.tenants.service import create_tenant
        tid = test_tenant["id"]
        prov = f"Priv-{uuid.uuid4().hex[:6]}"
        for i, d in enumerate([7, 7, 8, 7, 7, 12, 12, 13]):
            self._observe(tid, prov, d, i)

        other = create_tenant(f"pytest-other-{uuid.uuid4().hex[:8]}")
        try:
            names = {a["proveedor"] for a in health_svc.get_lead_time_deviations(other["id"])}
            assert prov not in names
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


class TestLeadTimeAlertsEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/suppliers/lead-time-alerts", headers=viewer_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/suppliers/lead-time-alerts")
        assert resp.status_code == 401

    def test_endpoint_reports_the_shifted_supplier(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        prov = f"EP-{uuid.uuid4().hex[:6]}"
        helper = TestDeviationFromRealReceptions()
        for i, d in enumerate([6, 6, 7, 6, 6, 14, 14, 15]):
            helper._observe(tid, prov, d, i)

        resp = client.get("/api/v1/inventory/suppliers/lead-time-alerts", headers=auth_headers)
        assert resp.status_code == 200
        assert prov in {a["proveedor"] for a in resp.json()["data"]}


class TestDailyLeadTimeAlertJob:
    """The 8:00 UTC worker pass (backend/workers/worker.py)."""

    def test_emails_admins_about_a_drifting_supplier(self, client, test_tenant, registered_user):
        tid = test_tenant["id"]
        prov = f"Daily-{uuid.uuid4().hex[:6]}"
        helper = TestDeviationFromRealReceptions()
        for i, d in enumerate([7, 7, 8, 7, 7, 13, 13, 14]):
            helper._observe(tid, prov, d, i)

        with mock.patch(
            "backend.notifications.email.send_supplier_lead_time_alert_email"
        ) as send:
            health_svc.run_daily_supplier_lead_time_alerts()

        recipients = [c.kwargs["to"] for c in send.call_args_list]
        assert registered_user["email"] in recipients, "tenant admin must be notified"

        sent = next(
            c for c in send.call_args_list if c.kwargs["to"] == registered_user["email"]
        )
        proveedores = {d["proveedor"] for d in sent.kwargs["deviations"]}
        assert prov in proveedores

    def test_no_email_when_every_supplier_is_stable(self, client, test_tenant, registered_user):
        tid = test_tenant["id"]
        prov = f"Quiet-{uuid.uuid4().hex[:6]}"
        helper = TestDeviationFromRealReceptions()
        for i, d in enumerate([9, 9, 10, 9, 9, 9, 10, 9]):
            helper._observe(tid, prov, d, i)

        with mock.patch(
            "backend.notifications.email.send_supplier_lead_time_alert_email"
        ) as send:
            health_svc.run_daily_supplier_lead_time_alerts()

        recipients = [c.kwargs["to"] for c in send.call_args_list]
        assert registered_user["email"] not in recipients, (
            "a stable supplier must not generate a daily email"
        )
