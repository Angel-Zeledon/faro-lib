"""
The semáforo's lead-time multipliers are configurable (owner's request,
2026-10-01) — and the defaults change nothing.

What is pinned here:

Only two of the three boundaries are lead-time multiples (0.5 and 3); the
middle one is the reorder point and stays so — see signal_thresholds.py.

1. Golden: with nothing configured, `_calc_signal` returns exactly what the
   shipped rule returned (a frozen copy of it lives below), on a dense grid AND
   on every real call made while computing a status over a fixture catalogue.
2. A configured multiplier moves the signal of a known SKU, through the real
   status computation and through the HTTP API.
3. Cascade: supplier override > category override > tenant rule > defaults,
   resolved as one unit.
4. Validation: bounds, ordering, every factor required — stable codes.
5. Permission pair on PUT and DELETE: viewer 403 + DB unchanged; analyst 200 +
   DB changed.
6. The 08:00 alert loop judges by the configured values.
7. The preview reports what would change and writes nothing.
8. No consumer keeps a private copy of the multipliers (grep).
"""
from __future__ import annotations

import io
import re
import tokenize
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.inventory import service as inv_svc
from backend.inventory import signal_thresholds as sig
from backend.sessions.service import create_session


# ── Frozen copy of the rule as it shipped (stability.md 17c) ─────────────────

def _shipped_calc_signal(coverage_days, lead_time, reorder_point_days):
    if coverage_days >= 9990:
        return "SOBRESTOCK"
    if coverage_days < lead_time * 0.5:
        return "PEDIR_YA"
    if coverage_days <= reorder_point_days:
        return "PEDIR_PRONTO"
    sobrestock_at = max(lead_time * 3, reorder_point_days * 2)
    if coverage_days < sobrestock_at:
        return "OK"
    return "SOBRESTOCK"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sku() -> str:
    return f"SKU-{uuid4().hex[:8].upper()}"


def _flat_forecast(per_day: float, n_points: int = 40) -> dict:
    """No upper/q90 band, so the classical safety stock is exactly 0 and the
    reorder point in days equals the lead time — hand-computable bands."""
    start = date.today()
    return {"lightgbm": {"forecast": [
        {"date": (start + timedelta(days=i)).isoformat(), "value": per_day}
        for i in range(n_points)
    ]}}


def _session(tenant_id: str) -> str:
    return create_session(tenant_id, "usr_test", f"thresholds-{uuid4().hex[:6]}")["id"]


def _status(tenant_id: str, session_id: str) -> dict[str, dict]:
    return {i["sku"]: i for i in inv_svc.get_inventory_status(tenant_id, session_id)}


def _arrange(tenant_id: str, stocks: dict[str, float], *, supplier=None, category=None,
             lead_time=10, per_day=10.0) -> tuple[str, dict[str, str]]:
    """One session, one SKU per label, 10/day demand and a 10-day lead time.
    Coverage in days is stock / 10, every band boundary is a whole number."""
    sid = _session(tenant_id)
    skus = {label: _sku() for label in stocks}
    fc = {}
    for label, stock in stocks.items():
        data = {"current_stock": stock, "lead_time_days": lead_time, "moq": 1.0}
        if supplier:
            data["supplier"] = supplier
        if category:
            data["category"] = category
        inv_svc.upsert_stock(tenant_id, skus[label], data)
        fc[skus[label]] = _flat_forecast(per_day)
    session_store.set_forecasts(tenant_id, sid, fc)
    return sid, skus


@pytest.fixture
def clean_rules(test_tenant):
    yield
    # stock_defaults has no FK to tenants; the tenant teardown leaves it.
    for table in ("stock_defaults", "inventory_recommendation_log", "inventory_stock"):
        execute(f"DELETE FROM {table} WHERE tenant_id = %s", (test_tenant["id"],))


def _db_factors(tenant_id: str, scope_type="global", scope_value=""):
    return query_one(
        """SELECT order_now_factor, overstock_factor
           FROM stock_defaults
           WHERE tenant_id = %s AND scope_type = %s AND scope_value = %s""",
        (tenant_id, scope_type, scope_value),
    )


# ── 1. Golden: the defaults are the shipped rule ─────────────────────────────

class TestDefaultsReproduceTheShippedRule:
    def test_default_constants_are_the_shipped_numbers(self):
        assert sig.DEFAULT_THRESHOLDS == {
            "order_now_factor": 0.5, "overstock_factor": 3.0,
        }
        assert sig.OVERSTOCK_REORDER_POINT_MULTIPLE == 2.0

    def test_dense_grid_is_identical_with_and_without_explicit_defaults(self):
        lead_times = [0.14, 0.5, 1, 2.14, 7, 10, 15, 21, 45, 90]
        # Reorder points at, below (rounding sliver), and above one lead time.
        rop_offsets = [-1e-3, 0, 0.3, 1, 5, 40]
        checked = 0
        for lt in lead_times:
            for off in rop_offsets:
                rop_days = lt + off
                covs = sorted({0, lt * 0.5 - 1e-9, lt * 0.5, lt * 0.5 + 1e-9,
                               rop_days - 1e-9, rop_days, rop_days + 1e-9, lt,
                               lt * 3 - 1e-9, lt * 3, rop_days * 2, rop_days * 2 + 1,
                               lt * 1.2, 9989.0, 9990.0, 9999.0})
                for cov in covs:
                    if cov < 0:
                        continue
                    expected = _shipped_calc_signal(cov, lt, rop_days)
                    assert inv_svc._calc_signal(cov, lt, rop_days) == expected
                    assert inv_svc._calc_signal(
                        cov, lt, rop_days, dict(sig.DEFAULT_THRESHOLDS)) == expected
                    assert inv_svc._calc_signal(
                        cov, lt, rop_days,
                        sig.resolve_signal_thresholds({})) == expected
                    checked += 1
        assert checked > 800

    def test_every_real_call_over_a_catalogue_matches_the_shipped_rule(
        self, test_tenant, monkeypatch, clean_rules,
    ):
        """Spy on the production call sites while the real status is computed
        over a catalogue spanning all four bands, and replay every call through
        the frozen rule."""
        tid = test_tenant["id"]
        stocks = {f"s{i}": float(s) for i, s in enumerate(
            [0, 20, 49, 50, 51, 80, 100, 101, 150, 299, 300, 301, 900, 5000])}
        sid, _ = _arrange(tid, stocks)

        calls = []
        real = inv_svc._calc_signal

        def spy(cov, lt, rop, thresholds=None):
            out = real(cov, lt, rop, thresholds)
            calls.append((cov, lt, rop, thresholds, out))
            return out

        monkeypatch.setattr(inv_svc, "_calc_signal", spy)
        items = inv_svc.get_inventory_status(tid, sid)
        assert len(calls) == len(stocks)
        for cov, lt, rop, th, out in calls:
            assert th["source"] == "default"
            assert out == _shipped_calc_signal(cov, lt, rop), (cov, lt, rop)
        seen = {i["signal"] for i in items}
        assert {"PEDIR_YA", "PEDIR_PRONTO", "OK", "SOBRESTOCK"} <= seen

    def test_saving_the_default_numbers_changes_no_signal_and_no_quantity(
        self, test_tenant, clean_rules,
    ):
        tid = test_tenant["id"]
        stocks = {"a": 20.0, "b": 70.0, "c": 150.0, "d": 400.0}
        sid, skus = _arrange(tid, stocks)
        before = _status(tid, sid)
        sig.set_signal_thresholds(tid, "global", None, dict(sig.DEFAULT_THRESHOLDS))
        after = _status(tid, sid)
        for sku in skus.values():
            assert after[sku]["signal"] == before[sku]["signal"]
            assert after[sku]["recommended_qty"] == before[sku]["recommended_qty"]
            assert before[sku]["signal_thresholds"]["source"] == "default"
            assert after[sku]["signal_thresholds"]["source"] == "global"


# ── 2. A configured multiplier moves a known SKU ─────────────────────────────

class TestConfiguredMultipliersMoveTheSignal:
    def test_each_factor_moves_exactly_the_sku_it_should(self, test_tenant, clean_rules):
        tid = test_tenant["id"]
        # coverage: 6d, 15d, 25d against a 10-day lead time (reorder point 10d)
        sid, skus = _arrange(tid, {"six": 60.0, "fifteen": 150.0, "twentyfive": 250.0})
        before = _status(tid, sid)
        assert before[skus["six"]]["signal"] == "PEDIR_PRONTO"
        assert before[skus["fifteen"]]["signal"] == "OK"
        assert before[skus["twentyfive"]]["signal"] == "OK"

        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": 0.7, "overstock_factor": 2.4,
        })
        after = _status(tid, sid)
        assert after[skus["six"]]["signal"] == "PEDIR_YA"            # 6 < 7
        assert after[skus["fifteen"]]["signal"] == "OK"              # untouched
        assert after[skus["twentyfive"]]["signal"] == "SOBRESTOCK"   # 25 >= 24
        # Urgency changed, the quantity did not: both ordering signals size the
        # same top-up to the reorder point.
        assert after[skus["six"]]["recommended_qty"] == before[skus["six"]]["recommended_qty"] > 0
        assert after[skus["six"]]["signal_thresholds"] == {
            "order_now_factor": 0.7, "overstock_factor": 2.4,
            "source": "global", "scope_value": None,
        }

    def test_the_highest_order_now_factor_still_leaves_a_quantity_on_an_urgent_row(
        self, test_tenant, clean_rules,
    ):
        """The ceiling (0.95) exists so PEDIR_YA can never fire above the
        reorder point, where the quantity is 0. Zero-cushion SKU, 9.4 days of
        cover against 10: urgent, and with something to order."""
        tid = test_tenant["id"]
        sid, skus = _arrange(tid, {"x": 94.0})
        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": sig.FACTOR_BOUNDS["order_now_factor"][1],
            "overstock_factor": 3.0,
        })
        row = _status(tid, sid)[skus["x"]]
        assert row["signal"] == "PEDIR_YA", row
        assert row["recommended_qty"] > 0

    def test_the_middle_boundary_stays_the_reorder_point_whatever_is_configured(
        self, test_tenant, clean_rules,
    ):
        """stability.md 17c must survive any configuration: a SKU at or below
        its reorder point is never OK. Reorder point stretched to 20 days by a
        10-day review period; 15 days of cover; the most aggressive overstock
        setting allowed."""
        from backend.inventory import supplier_service as sup_svc
        tid = test_tenant["id"]
        supplier = f"Prov-{uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {
            "name": supplier, "lead_time_days": 10, "lead_time_std": 0,
            "review_period_days": 10,
        })
        sid, skus = _arrange(tid, {"x": 150.0}, supplier=supplier)
        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": 0.1, "overstock_factor": 1.5,
        })
        row = _status(tid, sid)[skus["x"]]
        assert row["signal"] == "PEDIR_PRONTO", row


# ── 3. Cascade ───────────────────────────────────────────────────────────────

class TestCascade:
    def test_supplier_beats_category_beats_tenant(self, test_tenant, clean_rules):
        tid = test_tenant["id"]
        # 6 days of cover against a 10-day lead time: PEDIR_YA iff the
        # resolved order-now factor is above 0.6.
        _, a = _arrange(tid, {"x": 60.0}, supplier="Acme Imports", category="Bebidas")
        _, b = _arrange(tid, {"x": 60.0}, category="Bebidas")
        _, c = _arrange(tid, {"x": 60.0})
        # One session holding all three SKUs.
        sid = _session(tid)
        session_store.set_forecasts(tid, sid, {
            a["x"]: _flat_forecast(10.0), b["x"]: _flat_forecast(10.0),
            c["x"]: _flat_forecast(10.0)})

        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": 0.5, "overstock_factor": 3.0})
        sig.set_signal_thresholds(tid, "category", "Bebidas", {
            "order_now_factor": 0.65, "overstock_factor": 3.0})
        sig.set_signal_thresholds(tid, "supplier", "  ACME imports ", {
            "order_now_factor": 0.55, "overstock_factor": 3.0})

        st = _status(tid, sid)
        assert (st[a["x"]]["signal_thresholds"]["source"], st[a["x"]]["signal"]) == (
            "supplier", "PEDIR_PRONTO")
        assert (st[b["x"]]["signal_thresholds"]["source"], st[b["x"]]["signal"]) == (
            "category", "PEDIR_YA")
        assert (st[c["x"]]["signal_thresholds"]["source"], st[c["x"]]["signal"]) == (
            "global", "PEDIR_PRONTO")

        # Clearing the supplier override falls back to the category's factors.
        assert sig.clear_signal_thresholds(tid, "supplier", "Acme Imports") is True
        st = _status(tid, sid)
        assert (st[a["x"]]["signal_thresholds"]["source"], st[a["x"]]["signal"]) == (
            "category", "PEDIR_YA")

    def test_a_lead_time_only_rule_does_not_shadow_the_tenant_factors(self, test_tenant, clean_rules):
        from backend.inventory import stock_defaults_service as sd
        tid = test_tenant["id"]
        sd.set_stock_default(tid, "supplier", "Acme", {"lead_time_days": 10})
        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": 0.6, "overstock_factor": 4.0})
        th = sig.resolve_signal_thresholds(sd.build_rule_index(tid), supplier="acme")
        assert th["source"] == "global"
        assert th["overstock_factor"] == 4.0

    def test_clearing_only_nulls_the_factors_not_the_rest_of_the_rule(self, test_tenant, clean_rules):
        from backend.inventory import stock_defaults_service as sd
        tid = test_tenant["id"]
        sd.set_stock_default(tid, "global", None, {"lead_time_days": 21})
        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": 0.6, "overstock_factor": 4.0})
        assert sig.clear_signal_thresholds(tid, "global", None) is True
        row = query_one(
            "SELECT lead_time_days, order_now_factor FROM stock_defaults "
            "WHERE tenant_id = %s AND scope_type = 'global'", (tid,))
        assert row["lead_time_days"] == 21
        assert row["order_now_factor"] is None
        assert sig.clear_signal_thresholds(tid, "global", None) is False


# ── 4. Validation ────────────────────────────────────────────────────────────

class TestValidation:
    @pytest.mark.parametrize("values,code", [
        ({"order_now_factor": 0.5}, "signal_thresholds_missing_field"),
        ({"order_now_factor": "x", "overstock_factor": 3},
         "signal_thresholds_not_a_number"),
        ({"order_now_factor": float("nan"), "overstock_factor": 3},
         "signal_thresholds_not_a_number"),
        ({"order_now_factor": 0.05, "overstock_factor": 3},
         "signal_thresholds_out_of_range"),
        ({"order_now_factor": 1.0, "overstock_factor": 3},
         "signal_thresholds_out_of_range"),
        ({"order_now_factor": 0.5, "overstock_factor": 1.2},
         "signal_thresholds_out_of_range"),
        ({"order_now_factor": 0.5, "overstock_factor": 13},
         "signal_thresholds_out_of_range"),
    ])
    def test_rejected_with_a_stable_code(self, values, code):
        with pytest.raises(AppError) as exc:
            sig.validate_thresholds(values)
        assert exc.value.code == code

    def test_ordering_is_checked_even_if_the_bounds_ever_overlap(self, monkeypatch):
        monkeypatch.setitem(sig.FACTOR_BOUNDS, "order_now_factor", (0.1, 5.0))
        with pytest.raises(AppError) as exc:
            sig.validate_thresholds({"order_now_factor": 3.0, "overstock_factor": 2.0})
        assert exc.value.code == "signal_thresholds_not_increasing"

    def test_an_override_needs_a_name(self, test_tenant, clean_rules):
        with pytest.raises(AppError) as exc:
            sig.set_signal_thresholds(test_tenant["id"], "supplier", "  ",
                                      dict(sig.DEFAULT_THRESHOLDS))
        assert exc.value.code == "signal_thresholds_missing_scope_value"
        assert query("SELECT 1 FROM stock_defaults WHERE tenant_id = %s",
                     (test_tenant["id"],)) == []

    def test_api_rejection_is_422_with_code_and_writes_nothing(
        self, client, auth_headers, test_tenant, clean_rules,
    ):
        r = client.put("/api/v1/inventory/signal-thresholds", headers=auth_headers, json={
            "order_now_factor": 0.97, "overstock_factor": 3.0})
        assert r.status_code == 422
        assert r.json()["error_code"] == "signal_thresholds_out_of_range"
        assert r.json()["error_params"]["field"] == "order_now_factor"
        assert _db_factors(test_tenant["id"]) is None


# ── 5. API + permission pairs ────────────────────────────────────────────────

_NEW = {"order_now_factor": 0.75, "overstock_factor": 4.0}


class TestApi:
    def test_get_reports_defaults_as_defaults_not_as_a_choice(
        self, client, viewer_headers, test_tenant, clean_rules,
    ):
        r = client.get("/api/v1/inventory/signal-thresholds", headers=viewer_headers)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["tenant"] is None
        assert d["source"] == "default"
        assert d["effective"] == sig.DEFAULT_THRESHOLDS
        assert d["bounds"]["order_now_factor"] == {"min": 0.1, "max": 0.95}

    def test_viewer_cannot_put_and_db_is_unchanged(
        self, client, auth_headers, viewer_headers, test_tenant, clean_rules,
    ):
        tid = test_tenant["id"]
        sig.set_signal_thresholds(tid, "global", None, dict(sig.DEFAULT_THRESHOLDS))
        r = client.put("/api/v1/inventory/signal-thresholds", headers=viewer_headers, json=_NEW)
        assert r.status_code == 403
        assert dict(_db_factors(tid)) == sig.DEFAULT_THRESHOLDS

    def test_analyst_put_persists_and_get_reads_it_back(
        self, client, analyst_headers, test_tenant, clean_rules,
    ):
        tid = test_tenant["id"]
        r = client.put("/api/v1/inventory/signal-thresholds", headers=analyst_headers, json=_NEW)
        assert r.status_code == 200, r.text
        assert dict(_db_factors(tid)) == _NEW
        g = client.get("/api/v1/inventory/signal-thresholds", headers=analyst_headers).json()["data"]
        assert g["source"] == "global"
        assert g["effective"] == _NEW

    def test_analyst_put_supplier_override(self, client, analyst_headers, test_tenant, clean_rules):
        tid = test_tenant["id"]
        r = client.put("/api/v1/inventory/signal-thresholds", headers=analyst_headers,
                       json={**_NEW, "scope_type": "supplier", "scope_value": "Acme"})
        assert r.status_code == 200, r.text
        assert dict(_db_factors(tid, "supplier", "acme")) == _NEW
        assert _db_factors(tid) is None
        overrides = r.json()["data"]["overrides"]
        assert [(o["scope_type"], o["scope_value"]) for o in overrides] == [("supplier", "acme")]

    def test_viewer_cannot_reset_and_db_is_unchanged(
        self, client, viewer_headers, test_tenant, clean_rules,
    ):
        tid = test_tenant["id"]
        sig.set_signal_thresholds(tid, "global", None, _NEW)
        r = client.delete("/api/v1/inventory/signal-thresholds", headers=viewer_headers)
        assert r.status_code == 403
        assert dict(_db_factors(tid)) == _NEW

    def test_analyst_reset_returns_to_defaults(self, client, analyst_headers, test_tenant, clean_rules):
        tid = test_tenant["id"]
        sig.set_signal_thresholds(tid, "global", None, _NEW)
        r = client.delete("/api/v1/inventory/signal-thresholds", headers=analyst_headers)
        assert r.status_code == 200
        assert r.json()["data"]["cleared"] is True
        assert r.json()["data"]["source"] == "default"
        # The row carried nothing but the factors, so it is gone, not emptied.
        assert _db_factors(tid) is None

    def test_status_endpoint_carries_the_configured_signal(
        self, client, analyst_headers, test_tenant, clean_rules,
    ):
        tid = test_tenant["id"]
        sid, skus = _arrange(tid, {"x": 250.0})
        url = f"/api/v1/inventory/status?session_id={sid}"
        assert {i["sku"]: i for i in client.get(url, headers=analyst_headers)
                .json()["data"]["items"]}[skus["x"]]["signal"] == "OK"
        client.put("/api/v1/inventory/signal-thresholds", headers=analyst_headers, json={
            "order_now_factor": 0.5, "overstock_factor": 2.4})
        assert {i["sku"]: i for i in client.get(url, headers=analyst_headers)
                .json()["data"]["items"]}[skus["x"]]["signal"] == "SOBRESTOCK"


# ── 6. The 08:00 alert loop ──────────────────────────────────────────────────

class TestAlertLoopUsesConfiguredValues:
    def test_a_sku_only_the_configured_rule_flags_reaches_the_alert(
        self, monkeypatch, registered_user, test_tenant, clean_rules,
    ):
        from backend.sessions import planning_service

        tid = test_tenant["id"]
        sid, skus = _arrange(tid, {"x": 60.0})   # 6d: PEDIR_PRONTO by default
        monkeypatch.setattr(inv_svc, "get_tenants_with_active_sessions",
                            lambda: [{"tenant_id": tid}])
        monkeypatch.setattr(planning_service, "resolve_active_session", lambda t: sid)
        calls = []
        monkeypatch.setattr(
            "backend.notifications.email.send_inventory_alert_email",
            lambda **kw: calls.append(kw) or True)

        inv_svc.run_daily_inventory_alerts()
        assert len(calls) == 1
        assert [i["sku"] for i in calls[0]["critical_items"]] == []
        assert [i["sku"] for i in calls[0]["warning_items"]] == [skus["x"]]

        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": 0.8, "overstock_factor": 3.0})
        calls.clear()
        inv_svc.run_daily_inventory_alerts()
        assert len(calls) == 1
        assert [i["sku"] for i in calls[0]["critical_items"]] == [skus["x"]]


# ── 7. Preview ───────────────────────────────────────────────────────────────

class TestPreview:
    def test_preview_counts_the_changes_and_writes_nothing(
        self, client, viewer_headers, test_tenant, clean_rules,
    ):
        tid = test_tenant["id"]
        sid, skus = _arrange(tid, {"six": 60.0, "fifteen": 150.0, "twentyfive": 250.0})
        r = client.post("/api/v1/inventory/signal-thresholds/preview", headers=viewer_headers,
                        json={"session_id": sid, "order_now_factor": 0.7,
                              "overstock_factor": 2.4})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["available"] is True
        assert d["total"] == 3
        assert d["changed"] == 2
        assert d["transitions"] == {"PEDIR_PRONTO>PEDIR_YA": 1, "OK>SOBRESTOCK": 1}
        assert d["counts_after"]["PEDIR_YA"] == 1
        assert d["counts_after"]["SOBRESTOCK"] == 1
        assert {s["sku"] for s in d["sample"]} == {skus["six"], skus["twentyfive"]}
        # Nothing saved.
        assert _db_factors(tid) is None
        # The real status is untouched.
        assert _status(tid, sid)[skus["twentyfive"]]["signal"] == "OK"

    def test_a_patched_pass_is_never_written_to_the_recommendation_log(
        self, test_tenant, clean_rules,
    ):
        """The log records what the tenant was TOLD. A preview of unsaved
        numbers on a fresh tenant (nothing logged today yet) must not become
        today's record — the guard is only 'already recorded today', so
        without the early return this would write 2 rows."""
        tid = test_tenant["id"]
        sid, _ = _arrange(tid, {"a": 60.0, "b": 150.0})
        n = lambda: query_one(
            "SELECT COUNT(*)::int AS n FROM inventory_recommendation_log WHERE tenant_id = %s",
            (tid,))["n"]
        assert n() == 0
        inv_svc._compute_inventory_status(
            tid, sid, signal_threshold_patch=("global", None, {
                "order_now_factor": 0.7, "overstock_factor": 2.4}))
        assert n() == 0
        inv_svc._compute_inventory_status(tid, sid)
        assert n() == 2

    def test_preview_of_a_reset(self, test_tenant, clean_rules):
        tid = test_tenant["id"]
        sid, skus = _arrange(tid, {"twentyfive": 250.0})
        sig.set_signal_thresholds(tid, "global", None, {
            "order_now_factor": 0.5, "overstock_factor": 2.4})
        d = sig.preview_signal_changes(tid, sid, "daily", "global", None, None)
        assert d["transitions"] == {"SOBRESTOCK>OK": 1}

    def test_preview_rejects_invalid_values_with_the_same_code(self, client, viewer_headers, test_tenant):
        r = client.post("/api/v1/inventory/signal-thresholds/preview", headers=viewer_headers,
                        json={"session_id": "whatever", "order_now_factor": 0.5,
                              "overstock_factor": 20})
        assert r.status_code == 422
        assert r.json()["error_code"] == "signal_thresholds_out_of_range"


# ── 8. One source of truth ───────────────────────────────────────────────────

_LITERAL_TIMES_LEAD = re.compile(
    r"lead[a-z_]*\s*\)?\s*\*\s*\d+(\.\d+)?\b"        # lead_time * 3, float(lead) * 3.0
    r"|\b\d+(\.\d+)?\s*\*\s*(float\s*\(\s*)?lead"     # 3 * lead_time, 0.5 * float(lead
)


def _code_lines(source: str) -> dict[int, str]:
    """Each line's code with strings and comments removed — a docstring that
    explains the old rule is history, not a second authority."""
    lines: dict[int, list[str]] = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT, tokenize.NL,
                        tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            continue
        if tok.type == getattr(tokenize, "FSTRING_START", -1):
            continue
        lines.setdefault(tok.start[0], []).append(tok.string)
    return {n: " ".join(parts) for n, parts in lines.items()}


class TestNoPrivateCopies:
    def test_no_backend_module_multiplies_a_lead_time_by_a_literal(self):
        """The shapes the rule used to be restated in: `lead_time * 3`,
        `lead_periods * 3`, `lead_time * 0.5`, `3.0 * lead`. Every consumer
        reads `resolve_signal_thresholds` instead."""
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            # Seeds generate data (they place stock in bands on purpose).
            if rel.startswith(("tests/", ".venv/", "scripts/")) or "site-packages" in rel:
                continue
            for n, code in _code_lines(path.read_text(encoding="utf-8")).items():
                if _LITERAL_TIMES_LEAD.search(code):
                    offenders.append(f"{rel}:{n}: {code}")
        assert offenders == [], "\n".join(offenders)

    def test_the_grep_would_catch_the_old_shapes(self):
        for old in ("excess = days - lead_periods * 3\n",
                    "if coverage_days < lead_time * 0.5:\n    pass\n",
                    "coverage_limit = min(float(lead_time_days) * 3.0, MAX_COVERAGE_DAYS)\n",
                    "x = 3 * lead_time\n"):
            assert any(_LITERAL_TIMES_LEAD.search(c) for c in _code_lines(old).values()), old
        # ...and not the prose explaining it.
        assert not any(_LITERAL_TIMES_LEAD.search(c) for c in _code_lines(
            '"""`coverage_days < 0.5 * lead_time`"""\n# lead_time * 3\n').values())
