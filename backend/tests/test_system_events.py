"""The system-event vocabulary and the feed that reads it back.

Built on the owner's instruction (2026-09-16): the user should always know what
happened AND why. Before this, the activity log recorded a deleted session, API
key calls and four scheduled sends — everything else the product did happened in
silence, and the only trace was a log file the tenant cannot read.

The tests fall into two groups, and the first one is the important one: a
vocabulary whose copy does not exist is a feed that renders raw keys at buyers,
which this product has done before.
"""
from pathlib import Path
from uuid import uuid4

import pytest

from backend.activity.events import (
    BELL_SEVERITIES, EVENTS, INFO, REASONS, SEVERITIES, record_event, spec_for,
)
from backend.db.connection import query, query_one
from backend.notifications import alert_history

_ROOT = Path(__file__).resolve().parents[2]
_TRANSLATIONS = _ROOT / "Frontend" / "src" / "i18n" / "translations.ts"


def _catalogue() -> str:
    return _TRANSLATIONS.read_text(encoding="utf-8")


class TestVocabularyCannotDriftFromItsCopy:
    """`t()` echoes an unmapped key straight back, so a declared event with no
    copy puts `events.action.training.failed` on screen — this product printed
    `inventory.source_file` at buyers once already."""

    def test_every_event_action_has_a_title_in_both_languages(self):
        cat = _catalogue()
        missing = [a for a in EVENTS if f"'events.action.{a}'" not in cat]
        assert not missing, f"no copy for: {missing}"
        # Both blocks: the catalogue is one file with an `es` object and an `en`
        # object, so a key present once is present in one language only.
        for action in EVENTS:
            assert cat.count(f"'events.action.{action}'") == 2, (
                f"events.action.{action} is not in both language blocks"
            )

    def test_every_reason_code_has_copy_in_both_languages(self):
        cat = _catalogue()
        missing = [r for r in REASONS if f"'events.reason.{r}'" not in cat]
        assert not missing, f"no copy for reason codes: {missing}"
        for reason in REASONS:
            assert cat.count(f"'events.reason.{reason}'") == 2, (
                f"events.reason.{reason} is not in both language blocks"
            )

    def test_every_kind_has_a_filter_label(self):
        cat = _catalogue()
        for kind in alert_history.activity_kinds():
            assert cat.count(f"'events.kind.{kind}'") == 2, (
                f"events.kind.{kind} is missing from one of the language blocks"
            )

    def test_every_detail_key_has_a_label_in_both_languages(self):
        """The feed renders each number as `Label: value`. Without copy the
        label IS the identifier, and `rows_written: 83` becomes
        `events.detail.rows_written: 83` on a buyer's screen."""
        cat = _catalogue()
        keys = sorted({k for spec in EVENTS.values() for k in spec.detail_keys})
        missing = [k for k in keys if cat.count(f"'events.detail.{k}'") != 2]
        assert not missing, f"no label in both languages for: {missing}"

    def test_every_severity_is_declared_and_labelled(self):
        cat = _catalogue()
        for sev in SEVERITIES:
            assert sev in ("critical", "warning", "info")
            assert cat.count(f"'events.severity.{sev}'") == 2

    def test_only_critical_and_warning_reach_the_bell(self):
        """The routing rule, asserted rather than assumed. An `info` event in
        the bell is how a bell becomes something people stop reading."""
        assert set(BELL_SEVERITIES) == {"critical", "warning"}
        bell = set(alert_history._bell_event_actions())
        for action, spec in EVENTS.items():
            assert (action in bell) == (spec.severity != INFO), action
        # And there is genuinely something in each bucket, or the rule is vacuous.
        assert bell and len(bell) < len(EVENTS)


class TestRecordEventRefusesToBeSilent:
    def test_an_undeclared_action_is_refused(self, test_tenant):
        with pytest.raises(ValueError, match="unknown event action"):
            record_event(test_tenant["id"], "system", "training.exploded")

    def test_an_undeclared_reason_is_refused(self, test_tenant):
        with pytest.raises(ValueError, match="unknown reason"):
            record_event(test_tenant["id"], "system", "training.failed",
                         reason="because_reasons")

    def test_a_warning_without_a_reason_is_refused(self, test_tenant):
        """A warning the user cannot act on costs attention and returns
        nothing, which is the opposite of what this feature is for."""
        with pytest.raises(ValueError, match="must carry a reason"):
            record_event(test_tenant["id"], "system", "limit.reached",
                         details={"limit": "max_skus", "ceiling": 100})

    def test_an_info_event_needs_no_reason(self, test_tenant):
        record_event(test_tenant["id"], "system", "training.completed",
                     details={"session_id": "s1", "session_name": "Enero"})
        row = query_one(
            "SELECT action, context FROM activity_logs "
            "WHERE tenant_id=%s AND action='training.completed'", (test_tenant["id"],))
        assert row is not None
        assert row["context"]["severity"] == "info"
        assert row["context"]["session_name"] == "Enero"

    def test_undeclared_detail_keys_are_dropped_not_stored(self, test_tenant):
        """The whitelist is what stops the payload leaking whatever a future
        call site decides to attach."""
        record_event(test_tenant["id"], "system", "training.completed",
                     details={"session_id": "s1", "customer_email": "a@b.c"})
        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='training.completed'", (test_tenant["id"],))
        assert "customer_email" not in row["context"]

    def test_a_write_failure_never_propagates(self, test_tenant, monkeypatch):
        """These calls sit inside a reception, a sync, a training. Losing the
        audit row is bad; failing the user's actual work over it is worse."""
        import backend.activity.service as svc
        monkeypatch.setattr(svc, "log_action",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
        record_event(test_tenant["id"], "system", "training.completed",
                     details={"session_id": "s1"})   # must not raise


class TestTheFeedShowsBothSources:
    def _events(self, tid, n=1, action="training.failed"):
        for i in range(n):
            record_event(tid, "system", action, resource=f"s{i}",
                         details={"session_id": f"s{i}", "session_name": f"Run {i}",
                                  "started_by": "system"},
                         reason="engine_error",
                         reason_params={"detail": "boom"}, status="error")

    def test_a_system_event_reaches_the_bell_with_its_reason(self, test_tenant):
        tid = test_tenant["id"]
        self._events(tid)
        feed = alert_history.list_alerts(tid, "usr_test", limit=20)
        entry = next(e for e in feed["items"] if e["action"] == "training.failed")
        assert entry["source"] == "system"
        assert entry["severity"] == "critical"
        assert entry["reason"] == "engine_error"
        assert entry["reason_params"]["detail"] == "boom"
        assert entry["details"]["session_name"] == "Run 0"
        assert entry["unread"] is True

    def test_a_system_event_is_never_reported_as_delivered(self, test_tenant):
        """Zero recipients and zero failures would otherwise fall through the
        delivery three-way and read as 'delivered' — a confident wrong answer
        from the feed whose whole job is not to produce them."""
        tid = test_tenant["id"]
        self._events(tid)
        entry = next(e for e in alert_history.list_alerts(tid, "usr_test")["items"]
                     if e["source"] == "system")
        assert entry["status"] == "failed"
        assert entry["channel"] == "system"
        assert entry["delivered_count"] == 0

    def test_info_events_stay_out_of_the_bell_and_in_the_activity_feed(self, test_tenant):
        tid = test_tenant["id"]
        record_event(tid, "system", "training.completed",
                     details={"session_id": "s9", "session_name": "Quiet win"})
        assert not [e for e in alert_history.list_alerts(tid, "usr_test")["items"]
                    if e["action"] == "training.completed"]
        actions = [e["action"] for e in alert_history.list_activity(tid)["items"]]
        assert "training.completed" in actions

    def test_system_events_are_not_fanout_grouped(self, test_tenant):
        """Three failures in the same minute are three failures. The fan-out
        rule exists for one send to many recipients, and applying it here would
        hide two of them."""
        tid = test_tenant["id"]
        self._events(tid, n=3)
        entries = [e for e in alert_history.list_alerts(tid, "usr_test")["items"]
                   if e["action"] == "training.failed"]
        assert len(entries) == 3

    def test_the_activity_feed_filters_by_kind_and_severity(self, test_tenant):
        tid = test_tenant["id"]
        self._events(tid)                                     # training, critical
        record_event(tid, "system", "data.stock_imported",
                     details={"rows_read": 10, "rows_written": 10})   # data, info

        by_kind = alert_history.list_activity(tid, kind="data")
        assert {e["kind"] for e in by_kind["items"]} == {"data"}

        by_sev = alert_history.list_activity(tid, severity="critical")
        assert by_sev["items"], "a critical event must be findable by severity"
        assert all(e["severity"] == "critical" for e in by_sev["items"])

    def test_an_unknown_kind_returns_nothing_rather_than_everything(self, test_tenant):
        """A filter that silently falls back to 'all' is how a user concludes
        there is nothing wrong."""
        out = alert_history.list_activity(test_tenant["id"], kind="not_a_kind")
        assert out["items"] == [] and out["total"] == 0

    def test_a_failed_delivery_is_still_findable_as_critical(self, test_tenant):
        """Delivery rows predate `severity` in the context, so filtering by
        critical must not hide exactly the failed sends being looked for."""
        from backend.inventory.service import record_notification_delivery
        tid = test_tenant["id"]
        record_notification_delivery(
            tid, "usr_test", "inventory_alert_email", False,
            context={"channel": "email", "reason": "not_configured"},
        )
        out = alert_history.list_activity(tid, severity="critical")
        assert any(e["kind"] == "stockout_digest" for e in out["items"])


class TestEndpoints:
    def test_activity_is_readable_by_every_role(self, client, viewer_headers):
        resp = client.get("/api/v1/alerts/activity", headers=viewer_headers)
        assert resp.status_code == 200, resp.text
        assert "items" in resp.json()["data"]

    def test_a_viewer_can_clear_their_own_badge(self, client, viewer_headers, test_tenant):
        """The bell carries tenant-wide system events now, so a viewer HAS a
        badge. Leaving the one role that cannot clear it staring at it forever
        is not a permission decision, it is a bug."""
        resp = client.post("/api/v1/alerts/read", headers=viewer_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["unread_count"] == 0

    def test_the_kinds_endpoint_matches_the_registry(self, client, auth_headers):
        resp = client.get("/api/v1/alerts/kinds", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["kinds"] == alert_history.activity_kinds()

    def test_severity_is_validated_not_ignored(self, client, auth_headers):
        resp = client.get("/api/v1/alerts/activity?severity=urgent", headers=auth_headers)
        assert resp.status_code == 422

    def test_activity_does_not_cross_tenants(self, client, auth_headers, test_tenant):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"other-{uuid4().hex[:8]}")
        record_event(other["id"], "system", "training.completed",
                     details={"session_id": "x", "session_name": "Not yours"})
        resp = client.get("/api/v1/alerts/activity", headers=auth_headers)
        names = [e["details"].get("session_name") for e in resp.json()["data"]["items"]]
        assert "Not yours" not in names


class TestTheRealCallSitesRecord:
    def test_a_ceiling_refusal_is_recorded_even_with_nobody_watching(
        self, monkeypatch, make_tenant_user_headers, client,
    ):
        """The browser explains a 403 with a dialog. The nightly sync and the
        bulk import have nobody in front of them, and that refusal used to
        leave no trace at all."""
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        from backend.db.connection import execute, _json
        headers, tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        execute("UPDATE tenants SET quota = %s WHERE id = %s",
                (_json({"max_skus": 1}), tid))

        assert client.put("/api/v1/inventory/stock/A", json={"current_stock": 1},
                          headers=headers).status_code == 200
        assert client.put("/api/v1/inventory/stock/B", json={"current_stock": 1},
                          headers=headers).status_code == 403

        rows = query(
            "SELECT context FROM activity_logs WHERE tenant_id=%s AND action='limit.reached'",
            (tid,),
        )
        assert rows, "the ceiling stopped a write and recorded nothing"
        ctx = rows[0]["context"]
        assert ctx["limit"] == "max_skus"
        assert ctx["reason"] == "plan_limit_reached"
        assert ctx["reason_params"]["max"] == 1

    # ── The rest of the vocabulary, at the endpoints that write it ────────────
    #
    # A declared event nothing ever records is a promise the screen cannot
    # keep, so each of these walks the real endpoint and then reads the row
    # back out of activity_logs.

    def test_generating_a_purchase_order_is_recorded_with_its_reference(
        self, client, auth_headers, test_tenant,
    ):
        resp = client.post(
            "/api/v1/inventory/log-po",
            params={"session_id": f"sess_{uuid4().hex[:6]}"},
            json={"items": [{
                "sku": "EV-1", "display_name": "Prod", "supplier": "Acme",
                "signal": "PEDIR_YA", "recommended_qty": 10, "final_qty": 10,
                "unit_cost": 3.0, "status": "approved",
            }]},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text

        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='purchase.order_generated'",
            (test_tenant["id"],),
        )
        assert row is not None, "the buyer took an order away and nothing recorded it"
        assert row["context"]["reference"].startswith("OC-")
        assert row["context"]["lines"] == 1
        assert row["context"]["value"] == 30.0
        assert row["context"]["suppliers"] == 1
        assert row["context"]["severity"] == "info"

    def test_a_reception_records_what_actually_arrived(
        self, client, auth_headers, test_tenant,
    ):
        po = client.post(
            "/api/v1/inventory/log-po",
            params={"session_id": f"sess_{uuid4().hex[:6]}"},
            json={"items": [{
                "sku": "EV-2", "supplier": "Acme", "signal": "PEDIR_YA",
                "recommended_qty": 8, "final_qty": 8, "unit_cost": 1.0,
                "status": "approved",
            }]},
            headers=auth_headers,
        ).json()["data"]["id"]

        resp = client.post(f"/api/v1/inventory/po/{po}/receive",
                           json={"lines": [{"sku": "EV-2", "received_qty": 5}]},
                           headers=auth_headers)
        assert resp.status_code == 200, resp.text

        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='purchase.reception_recorded'",
            (test_tenant["id"],),
        )
        assert row is not None
        # What ARRIVED, not what was ordered: the two differ on a partial
        # delivery, and the history must not report the optimistic number.
        assert row["context"]["units"] == 5
        assert row["context"]["sku_count"] == 1

    def test_a_clean_import_is_info_and_a_lossy_one_is_a_warning(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        clean = "sku,current_stock\nEV-A,10\nEV-B,20\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("clean.csv", clean, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='data.stock_imported'", (tid,))
        assert row is not None
        assert row["context"]["rows_written"] == 2
        assert row["context"]["severity"] == "info"

        # The same file with a repeated key: two rows read, one row written.
        lossy = "sku,current_stock\nEV-C,10\nEV-C,25\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("lossy.csv", lossy, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='data.stock_import_partial'", (tid,))
        assert row is not None, "the file lost a row and the history says nothing"
        assert row["context"]["duplicate_rows"] == 1
        assert row["context"]["severity"] == "warning"
        assert row["context"]["reason"] == "duplicate_rows_collapsed"

    def test_a_shrinkage_records_the_warehouse_it_came_off(
        self, client, auth_headers, test_tenant,
    ):
        """Not the warehouse the form sent — the one the service resolved.
        Those differ (stability 11.8) and the history has to say where the
        units actually left from."""
        from backend.inventory import service as inv_svc
        tid = test_tenant["id"]
        inv_svc.upsert_stock(tid, "EV-S", {"current_stock": 40, "warehouse": "Norte"})

        resp = client.post("/api/v1/inventory/shrinkage",
                           json={"sku": "EV-S", "quantity": 3, "reason": "breakage",
                                 "warehouse": "norte"},
                           headers=auth_headers)
        assert resp.status_code == 201, resp.text

        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='data.shrinkage_recorded'", (tid,))
        assert row is not None
        assert row["context"]["warehouse"] == "Norte"
        assert row["context"]["quantity"] == 3
        assert row["context"]["shrinkage_reason"] == "breakage"

    def test_a_transfer_is_counted_not_listed_per_sku(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Sur")
        inv_svc.upsert_stock(tid, "EV-T1", {"current_stock": 100, "warehouse": "principal"})
        inv_svc.upsert_stock(tid, "EV-T2", {"current_stock": 100, "warehouse": "principal"})

        resp = client.post(
            "/api/v1/inventory/transfers",
            json={"from_warehouse": "principal", "to_warehouse": "Sur",
                  "items": [{"sku": "EV-T1", "qty": 5}, {"sku": "EV-T2", "qty": 7}]},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text

        rows = query(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='data.transfer_created'", (tid,))
        assert len(rows) == 1, "one document, one row - not one per line"
        assert rows[0]["context"]["sku_count"] == 2
        assert rows[0]["context"]["units"] == 12
        assert rows[0]["context"]["to_warehouse"] == "Sur"

    def test_minting_an_api_key_is_recorded_without_the_key(
        self, client, auth_headers, test_tenant,
    ):
        """The one event a stranger who got in would create. The name and role
        are the point; the secret must not be anywhere near the row."""
        resp = client.post("/api/v1/api-keys",
                           json={"name": "nightly-erp", "role": "viewer"},
                           headers=auth_headers)
        assert resp.status_code == 200, resp.text
        raw = resp.json()["data"]["key"]

        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='account.api_key_created'",
            (test_tenant["id"],),
        )
        assert row is not None
        assert row["context"]["key_name"] == "nightly-erp"
        assert row["context"]["reason"] == "changed_by_an_account_admin"
        assert raw not in str(row["context"])
        assert raw[-4:] not in str(row["context"])

    def test_a_role_change_records_both_roles_and_a_rename_does_not(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        created = client.post(
            "/api/v1/users",
            json={"email": f"ev-{uuid4().hex[:8]}@example.com", "role": "viewer",
                  "full_name": "Ev"},
            headers=auth_headers,
        )
        assert created.status_code == 201, created.text
        uid = created.json()["data"]["user"]["id"]
        assert query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='account.user_invited'", (tid,)
        ) is not None

        # A rename is not an access change and must not ring anybody's bell.
        client.patch(f"/api/v1/users/{uid}", json={"full_name": "Ev Renamed"},
                     headers=auth_headers)
        assert query(
            "SELECT id FROM activity_logs "
            "WHERE tenant_id=%s AND action='account.user_role_changed'", (tid,)
        ) == []

        resp = client.patch(f"/api/v1/users/{uid}", json={"role": "analyst"},
                            headers=auth_headers)
        assert resp.status_code == 200, resp.text
        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='account.user_role_changed'", (tid,))
        assert row is not None
        assert row["context"]["role"] == "analyst"
        assert row["context"]["previous_role"] == "viewer"
        assert row["context"]["severity"] == "warning"

    def test_a_refused_launch_is_recorded_once_not_twice(self, test_tenant, monkeypatch):
        """The gate refusing is one event. The ERP sync reports it in its own
        vocabulary (`integration.sync_blocked`, which names the provider), so
        the launch path must not also write `training.blocked` for the same
        refusal — two bell rows for one cause is the noise this feature is
        against."""
        from backend.errors import AppError
        from backend.sessions import data_gate, family_service as fam

        tid = test_tenant["id"]
        monkeypatch.setattr(
            data_gate, "enforce",
            lambda *a, **k: (_ for _ in ()).throw(
                AppError("training_blocked_unresolved", "no", params={"issues": "gaps"})),
        )

        with pytest.raises(AppError):
            fam.launch_training_family(tid, "sess_x", "usr_test")
        rows = query("SELECT context FROM activity_logs "
                     "WHERE tenant_id=%s AND action='training.blocked'", (tid,))
        assert len(rows) == 1
        assert rows[0]["context"]["reason"] == "data_gate_blocked"
        assert rows[0]["context"]["issues"] == "gaps"

        with pytest.raises(AppError):
            fam.launch_training_family(tid, "sess_x", "usr_test", record_refusal=False)
        rows = query("SELECT context FROM activity_logs "
                     "WHERE tenant_id=%s AND action='training.blocked'", (tid,))
        assert len(rows) == 1, "the caller that reports it itself wrote a second row"
