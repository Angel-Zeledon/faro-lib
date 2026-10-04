"""`GET /sessions/{id}/forecast-vs-actual`: the stored forecast graded against
sales uploaded AFTER it was made."""

import csv
import io

import pytest

from backend.db import session_store
from backend.forecast_check.service import _champion_forecasts

URL = "/api/v1/sessions/{sid}/forecast-vs-actual"


def _upload(client, headers, rows, name="later.csv"):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "sales"])
    w.writerows(rows)
    r = client.post("/api/v1/datasets",
                    files={"file": (name, buf.getvalue().encode(), "text/csv")},
                    headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _rows_from_forecast(tid, sid, factor, only_first_n=None):
    fc = _champion_forecasts(tid, sid)
    rows = []
    for sku, series in fc.items():
        dates = sorted(series)[:only_first_n] if only_first_n else sorted(series)
        rows += [(d, sku, round(series[d] * factor, 4)) for d in dates]
    return rows


class TestForecastVsActual:

    def test_no_later_upload_says_so(self, client, auth_headers, completed_session):
        r = client.get(URL.format(sid=completed_session["id"]), headers=auth_headers)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["status"] == "no_later_upload" and d["result"] is None

    def test_later_upload_is_graded_with_exact_metrics(
        self, client, auth_headers, completed_session, registered_user,
    ):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        rows = _rows_from_forecast(tid, sid, factor=2.0)   # reality ran 2x the forecast
        ds = _upload(client, auth_headers, rows)

        r = client.get(URL.format(sid=sid), headers=auth_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["status"] == "ok"
        assert d["source"]["dataset_id"] == ds["id"]

        agg = d["result"]["aggregate"]
        fc_total = sum(v for s in _champion_forecasts(tid, sid).values() for v in s.values())
        act_total = sum(row[2] for row in rows)
        assert agg["n_points"] == len(rows)
        assert agg["total_forecast"] == pytest.approx(fc_total)
        assert agg["total_actual"] == pytest.approx(act_total, rel=1e-6)
        assert agg["wape"] == pytest.approx(0.5, abs=1e-3)      # |f-2f| / 2f
        assert agg["bias"] == pytest.approx(-0.5, abs=1e-3)     # ran LOW
        assert agg["verdict"]["level"] == "poor"
        assert agg["verdict"]["direction"] == "under"
        assert {s["sku"] for s in d["result"]["skus"]} == set(_champion_forecasts(tid, sid))
        assert len(agg["series"]) == len({r[0] for r in rows})

    def test_explicit_dataset_overrides_the_automatic_pick(
        self, client, auth_headers, completed_session, registered_user,
    ):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        exact = _upload(client, auth_headers, _rows_from_forecast(tid, sid, 1.0), "exact.csv")
        partial = _upload(client, auth_headers, _rows_from_forecast(tid, sid, 3.0, only_first_n=2), "partial.csv")
        # Automatic: the upload overlapping the most points wins.
        auto = client.get(URL.format(sid=sid), headers=auth_headers).json()["data"]
        assert auto["source"]["dataset_id"] == exact["id"]
        assert auto["result"]["aggregate"]["wape"] == pytest.approx(0.0, abs=1e-6)
        assert auto["result"]["aggregate"]["verdict"]["level"] == "good"
        # Naming the dataset picks it, whatever overlaps more.
        named = client.get(URL.format(sid=sid) + f"?dataset_id={partial['id']}",
                           headers=auth_headers).json()["data"]
        assert named["source"]["dataset_id"] == partial["id"]
        assert named["result"]["aggregate"]["bias"] == pytest.approx(-2 / 3, abs=1e-3)

    def test_upload_that_does_not_cover_the_forecast_is_no_overlap(
        self, client, auth_headers, completed_session,
    ):
        _upload(client, auth_headers, [("2031-01-0%d" % d, "SKU_001", 5) for d in range(1, 8)])
        d = client.get(URL.format(sid=completed_session["id"]), headers=auth_headers).json()["data"]
        assert d["status"] == "no_overlap" and d["result"] is None

    def test_a_foreign_tenants_dataset_id_is_not_readable(
        self, client, auth_headers, completed_session, make_tenant_user_headers,
    ):
        other = make_tenant_user_headers(role="analyst")
        foreign = _upload(client, other, [("2023-04-01", "SKU_001", 5)])
        d = client.get(URL.format(sid=completed_session["id"]) + f"?dataset_id={foreign['id']}",
                       headers=auth_headers).json()["data"]
        assert d["status"] == "no_later_upload" and d["result"] is None

    def test_viewer_can_read(self, client, viewer_headers, completed_session):
        r = client.get(URL.format(sid=completed_session["id"]), headers=viewer_headers)
        assert r.status_code == 200

    def test_other_tenants_session_is_404(
        self, client, completed_session, make_tenant_user_headers,
    ):
        other = make_tenant_user_headers(role="admin")
        r = client.get(URL.format(sid=completed_session["id"]), headers=other)
        assert r.status_code == 404

    def test_session_still_training_is_refused(self, client, auth_headers, test_session):
        r = client.get(URL.format(sid=test_session["id"]), headers=auth_headers)
        assert r.status_code == 409

    def test_requires_authentication(self, client, completed_session):
        assert client.get(URL.format(sid=completed_session["id"])).status_code in (401, 403)

    def test_nothing_is_written(self, client, auth_headers, completed_session, registered_user):
        tid, sid = registered_user["tenant"]["id"], completed_session["id"]
        before = session_store.get_forecasts(tid, sid)
        client.get(URL.format(sid=sid), headers=auth_headers)
        assert session_store.get_forecasts(tid, sid) == before
