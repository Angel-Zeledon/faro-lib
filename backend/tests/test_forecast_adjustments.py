"""Manual forecast adjustments and their measured value (owner-authorised 2026-10-05).

Pins: an adjustment is stored append-only with who/when/why; it reaches the
purchase recommendation through the same window-blended arithmetic as a declared
event and is NAMED on the row; a later overlapping adjustment supersedes the old
one without deleting it; and once real sales arrive the adjusted forecast is
graded against the model's own on a synthetic case with a known answer.
"""

import csv
import io
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import query, query_one
from backend.errors import AppError
from backend.forecast_check.service import _champion_forecasts
from backend.inventory import forecast_adjustment_service as adj_svc
from backend.inventory import service as inv_svc
from backend.sessions.service import create_session


# ── Application to the recommendation (hand-computed numbers) ────────────────

def _flat_session(test_tenant, sku, per_day=10.0, lead_time=20, stock=50.0):
    tid = test_tenant["id"]
    sid = create_session(tid, "usr_test", f"adj-{uuid4().hex[:6]}")["id"]
    inv_svc.upsert_stock(tid, sku, {"current_stock": stock, "lead_time_days": lead_time,
                                    "moq": 1.0})
    start = date.today()
    session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
        {"date": (start + timedelta(days=i)).isoformat(), "value": per_day}
        for i in range(40)]}}})
    return sid


def _row(test_tenant, sid, sku):
    return {i["sku"]: i for i in inv_svc.get_inventory_status(test_tenant["id"], sid)}[sku]


def _adjust(test_tenant, sid, sku, start, end, pct, user="u1", reason="promotion", note=None):
    return adj_svc.create(test_tenant["id"], sid, user, sku=sku,
                          start_date=start, end_date=end, mode="percent", value=pct,
                          reason_code=reason, reason_note=note)


class TestApplicationToTheRecommendation:

    def test_an_adjustment_over_the_whole_window_scales_the_order_and_is_named(
            self, test_tenant, registered_user):
        sku = f"ADJ-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        base = _row(test_tenant, sid, sku)
        assert base["recommended_qty"] == 150.0           # 10/day * 20 days - 50 stock
        assert base["adjustments_applied"] == []

        today = date.today()
        _adjust(test_tenant, sid, sku, today, today + timedelta(days=30), 50,
                user=registered_user["user"]["id"], note="summer promo")
        after = _row(test_tenant, sid, sku)
        assert after["recommended_qty"] == pytest.approx(250.0)   # 10*1.5*20 - 50
        applied = after["adjustments_applied"]
        assert len(applied) == 1
        assert applied[0]["pct"] == 50 and applied[0]["reason_code"] == "promotion"
        assert applied[0]["reason_note"] == "summer promo"
        assert applied[0]["created_by_name"] == "Test Admin"
        assert after["calc_explanation"]["adjustments_applied"] == applied
        # the plain forecast stays visible as the model's own number
        assert after["daily_demand"] == base["daily_demand"]

    def test_partial_overlap_blends_by_the_share_of_the_window(self, test_tenant):
        sku = f"ADJ-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        today = date.today()
        # 8 of the 20 lead-time days: blended = 1 + 8/20 * 0.5 = 1.2
        _adjust(test_tenant, sid, sku, today + timedelta(days=5), today + timedelta(days=12), 50)
        row = _row(test_tenant, sid, sku)
        assert row["recommended_qty"] == pytest.approx(190.0)      # 10*1.2*20 - 50
        assert row["adjustments_applied"][0]["blended_multiplier"] == pytest.approx(1.2)
        assert row["adjustments_applied"][0]["overlap_days"] == 8

    def test_a_past_or_far_future_adjustment_changes_and_names_nothing(self, test_tenant):
        sku = f"ADJ-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        today = date.today()
        _adjust(test_tenant, sid, sku, today - timedelta(days=30), today - timedelta(days=5), 80)
        _adjust(test_tenant, sid, sku, today + timedelta(days=25), today + timedelta(days=30), 80)
        row = _row(test_tenant, sid, sku)
        assert row["recommended_qty"] == 150.0 and row["adjustments_applied"] == []

    def test_a_later_overlapping_adjustment_supersedes_but_keeps_the_old_row(
            self, test_tenant):
        sku = f"ADJ-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        today = date.today()
        first = _adjust(test_tenant, sid, sku, today, today + timedelta(days=30), 50)
        second = _adjust(test_tenant, sid, sku, today, today + timedelta(days=30), 20,
                         reason="price_change")
        assert _row(test_tenant, sid, sku)["recommended_qty"] == pytest.approx(10 * 1.2 * 20 - 50)
        old = query_one("SELECT superseded_by, pct, reason_code FROM forecast_adjustments "
                        "WHERE id = %s", (first["id"],))
        assert old["superseded_by"] == second["id"] and old["pct"] == 50
        assert old["reason_code"] == "promotion"       # history is untouched
        assert query_one("SELECT COUNT(*) AS n FROM forecast_adjustments WHERE session_id = %s",
                         (sid,))["n"] == 2
        # zero clears the period
        _adjust(test_tenant, sid, sku, today, today + timedelta(days=30), 0)
        assert _row(test_tenant, sid, sku)["recommended_qty"] == 150.0

    def test_the_warehouse_view_applies_the_same_adjustment(self, test_tenant):
        sku = f"ADJ-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        today = date.today()
        _adjust(test_tenant, sid, sku, today, today + timedelta(days=30), 50)
        rows = inv_svc.get_inventory_status_by_warehouse(test_tenant["id"], sid)
        mine = [r for r in rows if r["sku"] == sku]
        judged = [r for r in mine if r["signal"] != "SIN_DATOS"]
        assert judged and all(r["adjustments_applied"] for r in judged)

    def test_another_tenants_adjustments_never_apply(self, test_tenant, make_tenant_user_headers):
        sku = f"ADJ-{uuid4().hex[:6]}"
        sid = _flat_session(test_tenant, sku)
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        today = date.today()
        query_one(
            """INSERT INTO forecast_adjustments (tenant_id, session_id, sku, start_date, end_date,
                   mode, value, pct, reason_code, created_by)
               VALUES (%s, %s, %s, %s, %s, 'percent', 90, 90, 'promotion', 'x') RETURNING id""",
            (other_tid, sid, sku, today, today + timedelta(days=30)))
        assert _row(test_tenant, sid, sku)["recommended_qty"] == 150.0


# ── The API ──────────────────────────────────────────────────────────────────

def _first_series(tid, sid, n_dates=6):
    fc = _champion_forecasts(tid, sid)
    skus = sorted(fc)
    return skus, fc, {s: sorted(fc[s])[:n_dates] for s in skus}


class TestAdjustmentApi:

    def test_permission_pair_and_the_stored_row(self, client, viewer_headers, analyst_headers,
                                                analyst_user, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        skus, fc, dates = _first_series(tid, sid)
        sku = skus[0]
        body = {"sku": sku, "start_date": dates[sku][0], "end_date": dates[sku][-1],
                "mode": "percent", "value": 15, "reason_code": "promotion",
                "reason_note": "Black Friday"}
        url = f"/api/v1/sessions/{sid}/adjustments"
        denied = client.post(url, json=body, headers=viewer_headers)
        assert denied.status_code == 403
        assert query("SELECT 1 FROM forecast_adjustments WHERE session_id = %s", (sid,)) == []

        ok = client.post(url, json=body, headers=analyst_headers)
        assert ok.status_code == 201, ok.text
        row = query_one("SELECT * FROM forecast_adjustments WHERE session_id = %s", (sid,))
        assert (row["sku"], row["pct"], row["mode"], row["reason_code"]) == (
            sku, 15, "percent", "promotion")
        assert row["created_by"] == analyst_user["user"]["id"] and row["created_at"] is not None
        assert row["baseline_units"] == pytest.approx(sum(fc[sku][d] for d in dates[sku]))
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'forecast.adjusted' AND resource = %s", (tid, row["id"]))

        listed = client.get(url, headers=viewer_headers).json()["data"]
        assert [i["id"] for i in listed["items"]] == [row["id"]]
        assert listed["items"][0]["created_by_name"] and "promotion" in listed["reasons"]

    def test_absolute_units_become_the_equivalent_percentage_once(
            self, client, analyst_headers, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        skus, fc, dates = _first_series(tid, sid)
        sku = skus[0]
        baseline = sum(fc[sku][d] for d in dates[sku])
        r = client.post(f"/api/v1/sessions/{sid}/adjustments", headers=analyst_headers, json={
            "sku": sku, "start_date": dates[sku][0], "end_date": dates[sku][-1],
            "mode": "absolute", "value": baseline / 4, "reason_code": "new_customer"})
        assert r.status_code == 201, r.text
        assert r.json()["data"]["pct"] == pytest.approx(25.0)
        assert query_one("SELECT pct, value, mode FROM forecast_adjustments "
                         "WHERE session_id = %s", (sid,))["mode"] == "absolute"

    @pytest.mark.parametrize("change,code,status", [
        ({"reason_code": "nonsense"}, "forecast_adjustment_reason_invalid", 422),
        ({"reason_code": "other", "reason_note": " "}, "forecast_adjustment_note_required", 422),
        ({"value": -150}, "forecast_adjustment_out_of_range", 422),
        ({"value": 5000}, "forecast_adjustment_out_of_range", 422),
        ({"sku": "NO_SUCH_SKU"}, "forecast_adjustment_sku_unknown", 404),
        ({"end_date": "2000-01-01"}, "forecast_adjustment_dates_invalid", 422),
    ])
    def test_refusals_change_nothing(self, client, analyst_headers, completed_session,
                                     registered_user, change, code, status):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        skus, fc, dates = _first_series(tid, sid)
        body = {"sku": skus[0], "start_date": dates[skus[0]][0], "end_date": dates[skus[0]][-1],
                "mode": "percent", "value": 10, "reason_code": "promotion", **change}
        r = client.post(f"/api/v1/sessions/{sid}/adjustments", json=body, headers=analyst_headers)
        assert r.status_code == status, r.text
        assert r.json()["error_code"] == code
        assert query("SELECT 1 FROM forecast_adjustments WHERE session_id = %s", (sid,)) == []

    def test_units_with_no_baseline_demand_are_refused(self, client, analyst_headers,
                                                       completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        skus, _, _ = _first_series(tid, sid)
        r = client.post(f"/api/v1/sessions/{sid}/adjustments", headers=analyst_headers, json={
            "sku": skus[0], "start_date": "2001-01-01", "end_date": "2001-01-10",
            "mode": "absolute", "value": 50, "reason_code": "promotion"})
        assert r.status_code == 409
        assert r.json()["error_code"] == "forecast_adjustment_no_baseline"

    def test_other_tenants_session_is_404(self, client, completed_session, make_tenant_user_headers):
        other = make_tenant_user_headers(role="admin")
        url = f"/api/v1/sessions/{completed_session['id']}/adjustments"
        assert client.get(url, headers=other).status_code == 404
        assert client.post(url, headers=other, json={
            "sku": "X", "start_date": "2030-01-01", "end_date": "2030-01-02", "mode": "percent",
            "value": 1, "reason_code": "promotion"}).status_code == 404


# ── Forecast value added: a synthetic case with a known answer ───────────────

def _upload(client, headers, rows, name="actuals.csv"):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "sales"])
    w.writerows(rows)
    r = client.post("/api/v1/datasets",
                    files={"file": (name, buf.getvalue().encode(), "text/csv")}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _adj_api(client, headers, sid, sku, dates, pct, reason):
    r = client.post(f"/api/v1/sessions/{sid}/adjustments", headers=headers, json={
        "sku": sku, "start_date": dates[0], "end_date": dates[-1], "mode": "percent",
        "value": pct, "reason_code": reason})
    assert r.status_code == 201, r.text


class TestValueAdded:

    def test_no_adjustments_and_no_sales_yet_are_said_plainly(
            self, client, auth_headers, analyst_headers, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        url = f"/api/v1/sessions/{sid}/adjustments/value-added"
        assert client.get(url, headers=auth_headers).json()["data"]["status"] == "no_adjustments"
        skus, fc, dates = _first_series(tid, sid)
        _adj_api(client, analyst_headers, sid, skus[0], dates[skus[0]], 10, "promotion")
        assert client.get(url, headers=auth_headers).json()["data"]["status"] == "no_data_yet"

    def test_known_answer_aggregate_per_user_and_per_reason(
            self, client, auth_headers, analyst_headers, viewer_headers, completed_session,
            registered_user, analyst_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        skus, fc, dates = _first_series(tid, sid, n_dates=6)
        a, b = skus[0], skus[1]
        # Ana (analyst) lifts SKU `a` by 20% for 6 periods; sales turn out +15% over the model.
        _adj_api(client, analyst_headers, sid, a, dates[a], 20, "promotion")
        # The admin lifts SKU `b` by 50% for 6 periods; sales turn out only +10% over the model.
        _adj_api(client, auth_headers, sid, b, dates[b], 50, "market_news")
        rows = ([(d, a, round(fc[a][d] * 1.15, 6)) for d in dates[a]]
                + [(d, b, round(fc[b][d] * 1.10, 6)) for d in dates[b]])
        ds = _upload(client, auth_headers, rows)

        r = client.get(f"/api/v1/sessions/{sid}/adjustments/value-added", headers=viewer_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["status"] == "ok" and d["source"]["dataset_id"] == ds["id"]
        assert d["n_adjustments"] == 2 and d["n_adjustments_graded"] == 2

        fa = sum(fc[a][x] for x in dates[a])
        fb = sum(fc[b][x] for x in dates[b])
        # per SKU: |f - 1.15f| = .15 f ; |1.2f - 1.15f| = .05 f.  SKU b: .10 f vs .40 f.
        base_err = 0.15 * fa + 0.10 * fb
        adj_err = 0.05 * fa + 0.40 * fb
        agg = d["aggregate"]
        assert agg["n_points"] == 12
        assert agg["base_error"] == pytest.approx(base_err, rel=1e-4)
        assert agg["adjusted_error"] == pytest.approx(adj_err, rel=1e-4)
        assert agg["improvement_pct"] == pytest.approx((base_err - adj_err) / base_err * 100,
                                                       rel=1e-3)

        by_user = {u["user"]: u for u in d["by_user"]}
        ana = by_user[analyst_user["user"]["id"]]
        assert ana["improvement_pct"] == pytest.approx((0.15 - 0.05) / 0.15 * 100, rel=1e-3)
        assert ana["verdict"] == "improved" and ana["name"]
        admin = by_user[registered_user["user"]["id"]]
        assert admin["improvement_pct"] == pytest.approx((0.10 - 0.40) / 0.10 * 100, rel=1e-3)
        assert admin["verdict"] == "worsened"
        by_reason = {x["reason"]: x for x in d["by_reason"]}
        assert by_reason["promotion"]["verdict"] == "improved"
        assert by_reason["market_news"]["verdict"] == "worsened"

    def test_a_superseded_adjustment_is_not_graded(
            self, client, auth_headers, analyst_headers, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        skus, fc, dates = _first_series(tid, sid, n_dates=6)
        a = skus[0]
        _adj_api(client, analyst_headers, sid, a, dates[a], 90, "promotion")      # wrong, replaced
        _adj_api(client, analyst_headers, sid, a, dates[a], 20, "promotion")
        _upload(client, auth_headers, [(d, a, round(fc[a][d] * 1.2, 6)) for d in dates[a]])
        d = client.get(f"/api/v1/sessions/{sid}/adjustments/value-added",
                       headers=auth_headers).json()["data"]
        assert d["n_adjustments"] == 1
        assert d["aggregate"]["adjusted_error"] == pytest.approx(0.0, abs=1e-3)
        assert d["aggregate"]["verdict"] == "improved"      # model alone was 20% off; planner exact
        assert d["aggregate"]["improvement_pct"] == pytest.approx(100.0, abs=0.1)

    def test_other_tenants_session_is_404(self, client, completed_session, make_tenant_user_headers):
        other = make_tenant_user_headers(role="admin")
        r = client.get(f"/api/v1/sessions/{completed_session['id']}/adjustments/value-added",
                       headers=other)
        assert r.status_code == 404
