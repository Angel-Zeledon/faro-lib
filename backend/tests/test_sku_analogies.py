"""Forecast by analogy: the ledger, its audit trail and its effect on the status row.

Pins: a row is stored append-only (who/when/why) and audited; undo stamps instead
of deleting; viewers cannot write; another tenant cannot see or undo a row; one
live analogy per product; the status row of a product with no trained forecast
carries `forecast_source: 'analogy'` and says so everywhere, the trained model
takes over (once recorded) when it exists, and with no analogy every row is what
it was. (Written without a database to run against: run before relying on them.)
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import query, query_one
from backend.errors import AppError
from backend.inventory import analogy_service as svc
from backend.inventory import service as inv_svc
from backend.sessions.service import create_session

URL = "/api/v1/sku-analogies"


def _body(**kw):
    base = {"new_sku": "NEW-1", "reference_skus": ["REF-A", "REF-B"], "scale_factor": 1.5,
            "start_date": None, "note": "Same family, a bit bigger"}
    base.update(kw)
    return base


def _forecast(per_day, days=40):
    start = date.today()
    return {"lightgbm": {"forecast": [
        {"date": (start + timedelta(days=i)).isoformat(), "value": per_day, "q90": per_day * 1.2}
        for i in range(days)]}}


def _session(tid, forecasts):
    sid = create_session(tid, "usr_test", f"an-{uuid4().hex[:6]}")["id"]
    session_store.set_forecasts(tid, sid, forecasts)
    return sid


def _row(tid, sid, sku):
    return {i["sku"]: i for i in inv_svc.get_inventory_status(tid, sid)}[sku]


def _listed(tid, sid, sku):
    """The status lists only SKUs forecast in the session (or with a commitment or
    an analogy that can serve them): a stock-only product is not a row at all."""
    return sku in {i["sku"] for i in inv_svc.get_inventory_status(tid, sid)}


class TestAnalogyApi:

    def test_permission_pair_and_the_stored_row(self, client, viewer_headers, analyst_headers,
                                                analyst_user, registered_user):
        tid = registered_user["tenant"]["id"]
        assert client.post(URL, json=_body(), headers=viewer_headers).status_code == 403
        assert query("SELECT 1 FROM sku_analogies WHERE tenant_id = %s", (tid,)) == []

        ok = client.post(URL, json=_body(), headers=analyst_headers)
        assert ok.status_code == 201, ok.text
        row = query_one("SELECT * FROM sku_analogies WHERE tenant_id = %s", (tid,))
        assert row["new_sku"] == "NEW-1" and row["reference_skus"] == ["REF-A", "REF-B"]
        assert row["scale_factor"] == 1.5 and row["note"] == "Same family, a bit bigger"
        assert row["created_by"] == analyst_user["user"]["id"] and row["reverted_at"] is None
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'forecast.analogy_defined' AND resource = %s", (tid, row["id"]))

        listed = client.get(URL, headers=viewer_headers).json()["data"]
        assert [i["id"] for i in listed["items"]] == [row["id"]]
        assert listed["items"][0]["created_by_name"]
        assert listed["limits"]["max_references"] == 5

    def test_revert_permission_pair_keeps_the_row_and_audits(
            self, client, viewer_headers, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        created = client.post(URL, json=_body(), headers=analyst_headers).json()["data"]
        assert client.post(f"{URL}/{created['id']}/revert", headers=viewer_headers).status_code == 403
        assert query_one("SELECT reverted_at FROM sku_analogies WHERE id = %s",
                         (created["id"],))["reverted_at"] is None

        assert client.post(f"{URL}/{created['id']}/revert", headers=analyst_headers).status_code == 200
        row = query_one("SELECT * FROM sku_analogies WHERE id = %s", (created["id"],))
        assert row is not None and row["reverted_at"] is not None and row["reverted_by"]
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'forecast.analogy_reverted' AND resource = %s",
                     (tid, created["id"]))
        again = client.post(f"{URL}/{created['id']}/revert", headers=analyst_headers)
        assert again.status_code == 409
        # reverted rows leave the default list but stay listable
        assert client.get(URL, headers=analyst_headers).json()["data"]["items"] == []
        assert len(client.get(f"{URL}?include_reverted=true",
                              headers=analyst_headers).json()["data"]["items"]) == 1

    def test_one_live_analogy_per_product_and_redefining_after_undo(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        first = client.post(URL, json=_body(), headers=analyst_headers).json()["data"]
        dup = client.post(URL, json=_body(), headers=analyst_headers)
        assert dup.status_code == 409
        assert len(query("SELECT 1 FROM sku_analogies WHERE tenant_id = %s", (tid,))) == 1
        client.post(f"{URL}/{first['id']}/revert", headers=analyst_headers)
        assert client.post(URL, json=_body(), headers=analyst_headers).status_code == 201
        assert len(query("SELECT 1 FROM sku_analogies WHERE tenant_id = %s", (tid,))) == 2

    @pytest.mark.parametrize("patch", [
        {"reference_skus": []},
        {"reference_skus": ["A", "B", "C", "D", "E", "F"]},
        {"reference_skus": ["NEW-1"]},
        {"scale_factor": 0.05},
        {"scale_factor": 11},
        {"start_date": "soon"},
        {"new_sku": ""},
    ])
    def test_invalid_bodies_store_nothing(self, client, analyst_headers, registered_user, patch):
        tid = registered_user["tenant"]["id"]
        r = client.post(URL, json=_body(**patch), headers=analyst_headers)
        assert r.status_code in (400, 422), r.text
        assert query("SELECT 1 FROM sku_analogies WHERE tenant_id = %s", (tid,)) == []

    def test_duplicate_references_collapse(self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        r = client.post(URL, json=_body(reference_skus=["REF-A", "REF-A", " REF-B "]),
                        headers=analyst_headers)
        assert r.status_code == 201
        row = query_one("SELECT reference_skus FROM sku_analogies WHERE tenant_id = %s", (tid,))
        assert row["reference_skus"] == ["REF-A", "REF-B"]

    def test_another_tenant_cannot_see_or_undo(self, client, analyst_headers,
                                               make_tenant_user_headers):
        created = client.post(URL, json=_body(), headers=analyst_headers).json()["data"]
        other = make_tenant_user_headers(role="admin")
        assert client.get(URL, headers=other).json()["data"]["items"] == []
        assert client.post(f"{URL}/{created['id']}/revert", headers=other).status_code == 404
        assert query_one("SELECT reverted_at FROM sku_analogies WHERE id = %s",
                         (created["id"],))["reverted_at"] is None


class TestStatusRow:

    def test_no_analogy_leaves_rows_as_they_were(self, test_tenant):
        tid = test_tenant["id"]
        ref = f"REF-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, ref, {"current_stock": 50.0, "lead_time_days": 20, "moq": 1.0})
        sid = _session(tid, {ref: _forecast(10.0)})
        row = _row(tid, sid, ref)
        # 10/day * 20 days - 50 on hand = 150, plus the safety stock this fixture's
        # q90 band (1.2x) yields: ceil(150 + 1.645 * sqrt(20) * 2/1.2816) = 162.
        assert row["recommended_qty"] == 162.0
        assert row["forecast_source"] == "trained" and row["low_confidence"] is False
        assert row["analogy_applied"] == [] and row["analogy_retired"] is None

    def test_new_product_is_planned_from_the_references_and_labelled(self, test_tenant):
        tid = test_tenant["id"]
        ref_a, ref_b, new = (f"{p}-{uuid4().hex[:6]}" for p in ("RA", "RB", "NEW"))
        for sku in (ref_a, ref_b):
            inv_svc.upsert_stock(tid, sku, {"current_stock": 500.0, "lead_time_days": 20, "moq": 1.0})
        inv_svc.upsert_stock(tid, new, {"current_stock": 50.0, "lead_time_days": 20, "moq": 1.0})
        sid = _session(tid, {ref_a: _forecast(10.0), ref_b: _forecast(20.0)})
        before = _row(tid, sid, ref_a)["recommended_qty"]
        assert not _listed(tid, sid, new)       # stock-only, no forecast, no analogy: no row

        a = svc.create(tid, "u1", new_sku=new, reference_skus=[ref_a, ref_b], scale_factor=2.0)
        row = _row(tid, sid, new)
        assert row["forecast_source"] == "analogy" and row["low_confidence"] is True
        assert row["signal"] != "SIN_DATOS"
        assert row["daily_demand"] == pytest.approx(30.0)            # mean 15 * 2
        applied = row["analogy_applied"][0]
        assert applied["analogy_id"] == a["id"] and applied["references"] == [ref_a, ref_b]
        assert applied["scale_factor"] == 2.0 and applied["band_widen_factor"] > 1.0
        assert row["calc_explanation"]["analogy_applied"] == row["analogy_applied"]
        assert row["n_models"] == 1
        # the references' own rows did not move
        assert _row(tid, sid, ref_a)["recommended_qty"] == before
        # nothing was stored as a forecast for the new product
        assert new not in (session_store.get_forecasts(tid, sid) or {})

    def test_undo_returns_the_product_to_sin_datos(self, test_tenant):
        tid = test_tenant["id"]
        ref, new = f"RA-{uuid4().hex[:6]}", f"NEW-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, new, {"current_stock": 50.0, "lead_time_days": 20, "moq": 1.0})
        sid = _session(tid, {ref: _forecast(10.0)})
        a = svc.create(tid, "u1", new_sku=new, reference_skus=[ref])
        assert _row(tid, sid, new)["forecast_source"] == "analogy"
        svc.revert(tid, a["id"], "u1")
        assert not _listed(tid, sid, new)       # back to no row, never a guessed number

    def test_references_without_forecast_are_named_and_the_row_stays_sin_datos(self, test_tenant):
        tid = test_tenant["id"]
        new = f"NEW-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, new, {"current_stock": 50.0, "lead_time_days": 20, "moq": 1.0})
        sid = _session(tid, {f"OTHER-{uuid4().hex[:4]}": _forecast(1.0)})
        a = svc.create(tid, "u1", new_sku=new, reference_skus=["NO-FORECAST"])
        row = _row(tid, sid, new)
        assert row["signal"] == "SIN_DATOS" and row["forecast_source"] is None
        assert row["analogy_unavailable"] == {"analogy_id": a["id"],
                                              "references_missing": ["NO-FORECAST"]}

    def test_without_stock_the_analogy_is_reported_as_not_applied(self, test_tenant):
        tid = test_tenant["id"]
        ref, new = f"RA-{uuid4().hex[:6]}", f"NEW-{uuid4().hex[:6]}"
        sid = _session(tid, {ref: _forecast(10.0)})
        svc.create(tid, "u1", new_sku=new, reference_skus=[ref])
        row = _row(tid, sid, new)
        assert row["signal"] == "SIN_DATOS" and row["analogy_applied"] == []
        assert row["analogy_unavailable"]["reason"] == "no_stock"

    def test_trained_model_takes_over_and_the_ledger_records_it_once(self, test_tenant):
        tid = test_tenant["id"]
        ref, new = f"RA-{uuid4().hex[:6]}", f"NEW-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, new, {"current_stock": 50.0, "lead_time_days": 20, "moq": 1.0})
        sid = _session(tid, {ref: _forecast(10.0)})
        a = svc.create(tid, "u1", new_sku=new, reference_skus=[ref])
        assert _row(tid, sid, new)["forecast_source"] == "analogy"

        sid2 = _session(tid, {ref: _forecast(10.0), new: _forecast(4.0)})
        row = _row(tid, sid2, new)
        assert row["forecast_source"] == "trained" and row["low_confidence"] is False
        assert row["daily_demand"] == pytest.approx(4.0)
        assert row["analogy_applied"] == [] and row["analogy_retired"]["analogy_id"] == a["id"]
        stamped = query_one("SELECT superseded_at, superseded_session_id FROM sku_analogies "
                            "WHERE id = %s", (a["id"],))
        assert stamped["superseded_at"] is not None and stamped["superseded_session_id"] == sid2
        # a second read does not move the stamp
        _row(tid, sid2, new)
        again = query_one("SELECT superseded_at FROM sku_analogies WHERE id = %s", (a["id"],))
        assert again["superseded_at"] == stamped["superseded_at"]

    def test_another_tenants_analogy_never_applies(self, test_tenant, make_tenant_user_headers):
        tid = test_tenant["id"]
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        ref, new = f"RA-{uuid4().hex[:6]}", f"NEW-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, new, {"current_stock": 50.0, "lead_time_days": 20, "moq": 1.0})
        sid = _session(tid, {ref: _forecast(10.0)})
        svc.create(other_tid, "u1", new_sku=new, reference_skus=[ref])
        assert not _listed(tid, sid, new)       # the other tenant's analogy planned nothing


class TestTenantErasureAndExport:

    def test_the_ledger_is_in_both_lists(self):
        from backend.tenants import data_export
        assert "sku_analogies" in data_export._DELETE_ORDER
        assert "sku_analogies" in [spec[0] for spec in data_export._EXPORT_SPECS]
