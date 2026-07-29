"""GET /sessions/{id}/decomposition/{sku} — STL trend/seasonal/residual split.

The point of the endpoint is a chart that tells growth apart from "it is
December again", so these tests care about the *content* of the three series:
they must be aligned with the input dates, reconstruct the observed series
(STL is additive), and come back at the seasonal period the session's grain
implies. A plausible-looking wrong split is the failure mode worth guarding —
hence the short-history assertions, which pin the error code rather than
accepting whatever a two-point decomposition would have drawn.
"""

from __future__ import annotations

import csv
import io
import math
import random
from datetime import date, timedelta
from uuid import uuid4

import pytest

from tests.fixtures.synthetic_data import generate_csv_bytes

BASE = "/api/v1/sessions"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _periodic_csv_bytes(n_points: int, step_days: int, sku: str = "SKU_001",
                        seed: int = 5) -> bytes:
    """One SKU sampled every `step_days`, with trend + a 1-year cycle + noise."""
    rng = random.Random(seed)
    start = date(2019, 1, 7)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "sku", "sales"])
    for i in range(n_points):
        dt = start + timedelta(days=i * step_days)
        seasonal = 30 * math.sin(2 * math.pi * (i * step_days) / 365.0)
        value = max(0.0, 200 + 0.5 * i + seasonal + rng.gauss(0, 6))
        writer.writerow([dt.isoformat(), sku, round(value, 2)])
    return buf.getvalue().encode("utf-8")


def _completed_session_from_csv(client, headers, tenant_id, csv_bytes) -> str:
    """Upload a CSV, wire the wizard up to columns, force the session COMPLETED.

    Training itself is irrelevant here: the decomposition reads the history off
    the dataset file through the same helper the forecast chart uses.
    """
    from backend.sessions.service import force_status

    up = client.post(
        "/api/v1/datasets",
        files={"file": (f"decomp-{uuid4().hex[:6]}.csv", csv_bytes, "text/csv")},
        headers=headers,
    )
    assert up.status_code == 201, up.text
    dataset_id = up.json()["data"]["id"]

    created = client.post(
        BASE, json={"name": f"decomp-{uuid4().hex[:6]}"}, headers=headers,
    )
    assert created.status_code == 201, created.text
    sid = created.json()["data"]["id"]

    att = client.post(
        f"{BASE}/{sid}/dataset", json={"dataset_id": dataset_id}, headers=headers,
    )
    assert att.status_code == 200, att.text
    assert client.get(f"{BASE}/{sid}/inspect", headers=headers).status_code == 200

    cols = client.post(
        f"{BASE}/{sid}/configure/columns",
        json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
        headers=headers,
    )
    assert cols.status_code == 200, cols.text

    force_status(tenant_id, sid, "COMPLETED", "results")
    return sid


def _get(client, sid, sku, headers, **params):
    return client.get(f"{BASE}/{sid}/decomposition/{sku}", headers=headers, params=params)


# ── Pure policy (no DB) ───────────────────────────────────────────────────────

@pytest.mark.offline
class TestSeasonalPeriodPolicy:
    """The period is derived from the grain, never hardcoded to 7."""

    def test_period_per_granularity(self):
        from backend.utils.seasonality import seasonal_period
        assert seasonal_period("daily")     == 7    # day-of-week cycle
        assert seasonal_period("weekly")    == 52   # annual cycle in weeks
        assert seasonal_period("monthly")   == 12   # annual cycle in months
        assert seasonal_period("quarterly") == 4    # annual cycle in quarters
        assert seasonal_period("yearly")    is None

    def test_min_points_is_two_full_cycles(self):
        from backend.utils.seasonality import MIN_CYCLES, min_points
        assert MIN_CYCLES == 2
        assert min_points("daily")   == 14
        assert min_points("weekly")  == 104
        assert min_points("monthly") == 24
        assert min_points("yearly")  is None

    def test_target_freq_maps_back_to_grain(self):
        from backend.utils.seasonality import granularity_from_target_freq
        assert granularity_from_target_freq("W-MON") == "weekly"
        assert granularity_from_target_freq("MS")    == "monthly"
        assert granularity_from_target_freq(None)    is None
        assert granularity_from_target_freq("bogus") is None


# ── Happy path: daily session ─────────────────────────────────────────────────

class TestDailyDecomposition:
    """`completed_session` carries a 5-SKU x 60-day daily CSV."""

    def test_returns_aligned_components_at_the_daily_period(
        self, client, auth_headers, completed_session,
    ):
        r = _get(client, completed_session["id"], "SKU_001", auth_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]

        assert d["sku"] == "SKU_001"
        assert d["granularity"] == "daily"
        assert d["original_granularity"] == "daily"
        assert d["period"] == 7
        assert d["seasonal_cycle"] == "weekly"

        # 60 daily rows in, 60 buckets out — one component value per input date.
        assert d["n_points"] == 60
        assert len(d["series"]) == 60
        assert d["cycles_covered"] == round(60 / 7, 2)

        expected_dates = [
            (date(2023, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)
        ]
        assert [p["date"] for p in d["series"]] == expected_dates

        for p in d["series"]:
            for key in ("observed", "trend", "seasonal", "residual"):
                assert isinstance(p[key], (int, float)), f"{key} missing on {p['date']}"

        assert 0.0 <= d["trend_strength"] <= 1.0
        assert 0.0 <= d["seasonal_strength"] <= 1.0

    def test_components_reconstruct_the_observed_series(
        self, client, auth_headers, completed_session,
    ):
        """STL is additive: observed == trend + seasonal + residual, per bucket.

        This is the assertion that a garbage split cannot survive.
        """
        r = _get(client, completed_session["id"], "SKU_001", auth_headers)
        assert r.status_code == 200, r.text
        for p in r.json()["data"]["series"]:
            recomposed = p["trend"] + p["seasonal"] + p["residual"]
            assert abs(recomposed - p["observed"]) < 1e-3, p

    def test_observed_matches_the_forecast_series_history(
        self, client, auth_headers, completed_session,
    ):
        """Both endpoints must read the same history — one derivation, one truth."""
        sid = completed_session["id"]
        hist = client.get(
            f"{BASE}/{sid}/forecast-series/SKU_001", headers=auth_headers,
        ).json()["data"]["historical"]
        decomp = _get(client, sid, "SKU_001", auth_headers).json()["data"]["series"]

        assert len(hist) == len(decomp) > 0
        assert [h["date"] for h in hist] == [p["date"] for p in decomp]
        for h, p in zip(hist, decomp):
            assert abs(float(h["value"]) - p["observed"]) < 1e-6, (h, p)

    def test_only_viable_granularities_are_offered(
        self, client, auth_headers, completed_session,
    ):
        """60 daily rows span ~8 weeks — far short of the 104 weekly buckets a
        weekly decomposition needs, so the chip must not offer weekly."""
        d = _get(client, completed_session["id"], "SKU_001", auth_headers).json()["data"]
        assert d["available_granularities"] == ["daily"]

    def test_unknown_granularity_falls_back_to_the_data_grain(
        self, client, auth_headers, completed_session,
    ):
        d = _get(
            client, completed_session["id"], "SKU_001", auth_headers,
            granularity="fortnightly",
        )
        assert d.status_code == 200, d.text
        assert d.json()["data"]["granularity"] == "daily"


# ── Grain derivation ──────────────────────────────────────────────────────────

class TestGranularityDrivesThePeriod:

    def test_weekly_history_uses_the_annual_period_of_52(
        self, client, auth_headers, registered_user,
    ):
        sid = _completed_session_from_csv(
            client, auth_headers, registered_user["tenant"]["id"],
            _periodic_csv_bytes(n_points=120, step_days=7),
        )
        r = _get(client, sid, "SKU_001", auth_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["original_granularity"] == "weekly"
        assert d["granularity"] == "weekly"
        assert d["period"] == 52
        assert d["seasonal_cycle"] == "annual"
        assert d["n_points"] == 120
        assert len(d["series"]) == 120

    def test_requested_grain_finer_than_the_data_is_floored(
        self, client, auth_headers, registered_user,
    ):
        """Asking for daily panels over weekly rows would invent observations."""
        sid = _completed_session_from_csv(
            client, auth_headers, registered_user["tenant"]["id"],
            _periodic_csv_bytes(n_points=120, step_days=7, seed=6),
        )
        r = _get(client, sid, "SKU_001", auth_headers, granularity="daily")
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["granularity"] == "weekly"
        assert d["period"] == 52

    def test_monthly_history_uses_the_annual_period_of_12(
        self, client, auth_headers, registered_user,
    ):
        sid = _completed_session_from_csv(
            client, auth_headers, registered_user["tenant"]["id"],
            _periodic_csv_bytes(n_points=36, step_days=30, seed=8),
        )
        r = _get(client, sid, "SKU_001", auth_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["original_granularity"] == "monthly"
        assert d["period"] == 12
        assert d["seasonal_cycle"] == "annual"
        assert len(d["series"]) == d["n_points"] >= 24


# ── Short series: refuse, never fake ──────────────────────────────────────────

class TestShortHistory:

    def test_ten_daily_points_are_refused_with_a_code(
        self, client, auth_headers, registered_user,
    ):
        """10 days < 2 x 7. The chart must get an error, not a fabricated split."""
        sid = _completed_session_from_csv(
            client, auth_headers, registered_user["tenant"]["id"],
            generate_csv_bytes(n_skus=1, n_days=10, seed=21),
        )
        r = _get(client, sid, "SKU_001", auth_headers)
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["error_code"] == "decomposition_history_too_short"
        assert body["error_params"] == {
            "sku": "SKU_001", "granularity": "daily", "period": 7,
            "cycles": 2, "required": 14, "available": 10,
        }
        assert "series" not in body

    def test_weekly_grain_over_two_months_of_data_is_refused(
        self, client, auth_headers, completed_session,
    ):
        """60 daily rows aggregate to ~9 weeks; a weekly split needs 104."""
        r = _get(
            client, completed_session["id"], "SKU_001", auth_headers,
            granularity="weekly",
        )
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["error_code"] == "decomposition_history_too_short"
        assert body["error_params"]["granularity"] == "weekly"
        assert body["error_params"]["period"] == 52
        assert body["error_params"]["required"] == 104
        assert body["error_params"]["available"] < 104

    def test_unknown_sku_reports_missing_history(
        self, client, auth_headers, completed_session,
    ):
        r = _get(client, completed_session["id"], "SKU_DOES_NOT_EXIST", auth_headers)
        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "decomposition_no_history"
        assert r.json()["error_params"]["sku"] == "SKU_DOES_NOT_EXIST"


# ── Access control ────────────────────────────────────────────────────────────

class TestDecompositionAccess:

    def test_viewer_can_read(self, client, viewer_headers, completed_session):
        r = _get(client, completed_session["id"], "SKU_001", viewer_headers)
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]["series"]) == 60

    def test_analyst_can_read(self, client, analyst_headers, completed_session):
        r = _get(client, completed_session["id"], "SKU_001", analyst_headers)
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]["series"]) == 60

    def test_unauthenticated_is_rejected(self, client, completed_session):
        r = client.get(f"{BASE}/{completed_session['id']}/decomposition/SKU_001")
        assert r.status_code == 401
        assert "series" not in r.json()

    def test_other_tenant_cannot_read(
        self, client, completed_session, make_tenant_user_headers,
    ):
        outsider = make_tenant_user_headers(role="admin")
        r = _get(client, completed_session["id"], "SKU_001", outsider)
        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "session_not_found"

    def test_session_still_training_is_rejected(
        self, client, auth_headers, configured_session,
    ):
        r = _get(client, configured_session["id"], "SKU_001", auth_headers)
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "session_still_training"
