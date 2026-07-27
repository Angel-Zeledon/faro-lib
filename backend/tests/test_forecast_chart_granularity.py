"""The chart must never put a coarser forecast next to finer history.

A weekly session predicts weekly totals. Served alongside daily history they
share one axis at ~7x the scale, which reads as an unexplained jump right where
the forecast starts (PENDIENTES #3). The granularity floor is therefore the
FORECAST's own frequency, not the history's."""

import pytest

from backend.db import session_store


def _seed_mixed_freq_session(tenant_id, session_id):
    """Daily history + weekly forecast — the exact shape a weekly session on
    daily sales data produces."""
    historical = [
        {"date": f"2025-01-{d:02d}", "value": 30.0} for d in range(1, 29)
    ]
    forecast = [
        {"date": "2025-02-03", "value": 210.0},
        {"date": "2025-02-10", "value": 210.0},
        {"date": "2025-02-17", "value": 210.0},
        {"date": "2025-02-24", "value": 210.0},
    ]
    session_store.set_forecasts(tenant_id, session_id, {
        "SKU-W": {"xgboost": {"historical": historical, "forecast": forecast}},
    })


@pytest.fixture
def mixed_freq_session(test_tenant, completed_session):
    _seed_mixed_freq_session(test_tenant["id"], completed_session["id"])
    return completed_session


class TestGranularityFloorFollowsTheForecast:
    def test_daily_is_not_offered_for_a_weekly_forecast(
        self, client, auth_headers, mixed_freq_session
    ):
        r = client.get(
            f"/api/v1/sessions/{mixed_freq_session['id']}/sku-intelligence/SKU-W",
            headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert "daily" not in data["available_granularities"]
        assert data["available_granularities"][0] == "weekly"
        assert data["applied_granularity"] == "weekly"
        assert data["original_freq"] == "weekly"

    def test_asking_for_daily_falls_back_to_weekly_instead_of_mixing_scales(
        self, client, auth_headers, mixed_freq_session
    ):
        r = client.get(
            f"/api/v1/sessions/{mixed_freq_session['id']}/sku-intelligence/SKU-W",
            params={"granularity": "daily"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["applied_granularity"] == "weekly"

        # The regression this guards: history must be on the forecast's scale,
        # not 7x below it. Weekly history buckets ~210 (7 x 30/day).
        hist = [p["value"] for p in data["historical"]]
        fc = [p["value"] for p in data["forecast"]]
        assert hist, "history should be returned"
        assert max(hist) > 100, f"history still daily-scaled: {hist[:3]}"
        # Same order of magnitude on both sides of the boundary.
        assert 0.5 < (sum(fc) / len(fc)) / (sum(hist) / len(hist)) < 2.0

    def test_coarser_grains_still_aggregate_both_series(
        self, client, auth_headers, mixed_freq_session
    ):
        r = client.get(
            f"/api/v1/sessions/{mixed_freq_session['id']}/sku-intelligence/SKU-W",
            params={"granularity": "monthly"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["applied_granularity"] == "monthly"
        # 28 daily points of 30 collapse into one ~840 month.
        assert len(data["historical"]) == 1
        assert data["historical"][0]["value"] == pytest.approx(840.0, rel=0.01)


class TestPlainDailySessionIsUnchanged:
    def test_daily_forecast_keeps_daily_as_the_floor(
        self, client, auth_headers, test_tenant, completed_session
    ):
        historical = [{"date": f"2025-01-{d:02d}", "value": 30.0} for d in range(1, 29)]
        forecast = [{"date": f"2025-02-{d:02d}", "value": 31.0} for d in range(1, 8)]
        session_store.set_forecasts(test_tenant["id"], completed_session["id"], {
            "SKU-D": {"xgboost": {"historical": historical, "forecast": forecast}},
        })

        r = client.get(
            f"/api/v1/sessions/{completed_session['id']}/sku-intelligence/SKU-D",
            headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["available_granularities"][0] == "daily"
        assert data["applied_granularity"] == "daily"
        assert len(data["historical"]) == 28
