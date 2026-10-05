"""`GET /sessions/{id}/forecast-total`: the whole catalogue as one series, which
the session comparison needs to put two updates on the same axis."""

import pytest

from backend.db import session_store


def _seed(tenant_id, session_id):
    hist_a = [{"date": f"2025-01-{d:02d}", "value": 10.0} for d in range(1, 29)]
    hist_b = [{"date": f"2025-01-{d:02d}", "value": 5.0} for d in range(1, 29)]
    fc_a = [{"date": f"2025-02-{d:02d}", "value": 11.0} for d in range(1, 8)]
    fc_b = [{"date": f"2025-02-{d:02d}", "value": 4.0} for d in range(1, 8)]
    session_store.set_forecasts(tenant_id, session_id, {
        "SKU-A": {"xgboost": {"historical": hist_a, "forecast": fc_a}},
        "SKU-B": {"xgboost": {"historical": hist_b, "forecast": fc_b}},
    })


class TestForecastTotal:
    def test_sums_every_sku_per_date(self, client, auth_headers, test_tenant, completed_session):
        _seed(test_tenant["id"], completed_session["id"])
        r = client.get(f"/api/v1/sessions/{completed_session['id']}/forecast-total",
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["sku"] == "__total__"
        assert data["n_skus"] == 2
        assert data["applied_granularity"] == "daily"
        assert len(data["forecast"]) == 7
        assert all(p["value"] == pytest.approx(15.0) for p in data["forecast"])
        assert len(data["historical"]) == 28
        assert all(p["value"] == pytest.approx(15.0) for p in data["historical"])

    def test_coarser_granularity_aggregates_the_total(
        self, client, auth_headers, test_tenant, completed_session
    ):
        _seed(test_tenant["id"], completed_session["id"])
        r = client.get(f"/api/v1/sessions/{completed_session['id']}/forecast-total",
                       params={"granularity": "monthly"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["applied_granularity"] == "monthly"
        assert len(data["historical"]) == 1
        assert data["historical"][0]["value"] == pytest.approx(15.0 * 28)
        assert data["forecast"][0]["value"] == pytest.approx(15.0 * 7)

    def test_session_without_forecasts_returns_empty_series_not_an_error(
        self, client, auth_headers, test_tenant, completed_session
    ):
        session_store.set_forecasts(test_tenant["id"], completed_session["id"], {})
        r = client.get(f"/api/v1/sessions/{completed_session['id']}/forecast-total",
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["n_skus"] == 0
        assert data["forecast"] == []
        assert data["accuracy_wape"] is None

    def test_unknown_session_is_404(self, client, auth_headers):
        r = client.get("/api/v1/sessions/sess_does_not_exist/forecast-total", headers=auth_headers)
        assert r.status_code == 404

    def test_viewer_can_read(self, client, viewer_headers, test_tenant, completed_session):
        _seed(test_tenant["id"], completed_session["id"])
        r = client.get(f"/api/v1/sessions/{completed_session['id']}/forecast-total",
                       headers=viewer_headers)
        assert r.status_code == 200, r.text
