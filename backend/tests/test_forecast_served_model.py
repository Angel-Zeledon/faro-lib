"""The forecast chart must draw the model the SKU is actually bought from.

Named after the failure, not the function. Walking /pronosticos on a real
session, the chart drew prophet at 45.7% WAPE while the purchase quantity for
that same SKU came from xgboost at 24.6%. Nothing errored: the endpoint served
`next(iter(sku_forecasts.keys()))` — whichever model the forecasts dict happened
to store first — so which curve a buyer studied was an accident of insertion
order, and the tile beside it truthfully named a different "best model".

This is the same drift `backend/inventory/service.py` records having closed once
between the engine, the semáforo and the accuracy figure. It had survived in the
chart, which is the surface a buyer actually looks at before committing money.
"""

from backend.db import session_store


def _seed(tenant_id, session_id, *, first_key_model, champion_model):
    """A SKU whose forecasts dict lists the LOSER first.

    `cost_horizon` is what the champion is chosen by, so the champion here is
    the expensive-mistakes-avoiding model, deliberately NOT the one with the
    best MAE/WAPE — otherwise this test would still pass with a ranking that
    reads the wrong column.
    """
    series = {
        "historical": [{"date": f"2025-01-{d:02d}", "value": 30.0} for d in range(1, 29)],
        "forecast":   [{"date": f"2025-02-{d:02d}", "value": 30.0} for d in range(1, 8)],
    }
    # Insertion order is the point of this test: the loser goes in first.
    session_store.set_forecasts(tenant_id, session_id, {
        "SKU-C": {first_key_model: dict(series), champion_model: dict(series)},
    })

    result = session_store.get_training_result(tenant_id, session_id) or {}
    result["metrics"] = {"rows": [
        # Worst on cost, best on MAE and WAPE — the trap.
        {"sku": "SKU-C", "model": first_key_model, "type": "stat",
         "cost_horizon": 18.32, "mae": 4.10, "wape": 0.11},
        {"sku": "SKU-C", "model": champion_model, "type": "ml",
         "cost_horizon": 14.60, "mae": 10.04, "wape": 0.24},
        # A baseline that would win on cost if baselines were eligible. They are
        # not: they exist to be beaten, and buying from one would be a bug.
        {"sku": "SKU-C", "model": "naive", "type": "baseline",
         "cost_horizon": 1.00, "mae": 1.00, "wape": 0.01},
    ]}
    session_store.set_training_result(tenant_id, session_id, result)


class TestTheChartDrawsTheModelTheOrdersComeFrom:
    def test_the_first_key_of_the_dict_does_not_decide_what_is_served(
        self, client, auth_headers, test_tenant, completed_session
    ):
        _seed(test_tenant["id"], completed_session["id"],
              first_key_model="prophet", champion_model="xgboost")

        r = client.get(
            f"/api/v1/sessions/{completed_session['id']}/sku-intelligence/SKU-C",
            headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]

        assert data["available_models"][0] == "prophet", (
            "fixture no longer reproduces the bug: the loser must be stored first"
        )
        assert data["model"] == "xgboost", (
            f"served {data['model']!r}; the orders come from 'xgboost' (lowest "
            f"cost_horizon), so the chart must draw that one"
        )

    def test_a_baseline_is_never_served_even_when_it_is_the_cheapest(
        self, client, auth_headers, test_tenant, completed_session
    ):
        _seed(test_tenant["id"], completed_session["id"],
              first_key_model="prophet", champion_model="xgboost")

        r = client.get(
            f"/api/v1/sessions/{completed_session['id']}/sku-intelligence/SKU-C",
            headers=auth_headers)
        assert r.json()["data"]["model"] != "naive"

    def test_an_explicit_model_still_wins_over_the_champion(
        self, client, auth_headers, test_tenant, completed_session
    ):
        """Picking a model in the UI must keep working — the champion is the
        DEFAULT, not a lock."""
        _seed(test_tenant["id"], completed_session["id"],
              first_key_model="prophet", champion_model="xgboost")

        r = client.get(
            f"/api/v1/sessions/{completed_session['id']}/sku-intelligence/SKU-C",
            params={"model": "prophet"}, headers=auth_headers)
        assert r.json()["data"]["model"] == "prophet"

    def test_a_champion_with_no_stored_series_falls_back_instead_of_failing(
        self, client, auth_headers, test_tenant, completed_session
    ):
        """Metrics can name a model whose forecast was never persisted. Naming a
        curve we cannot draw would be worse than falling back, and 500ing worse
        still."""
        _seed(test_tenant["id"], completed_session["id"],
              first_key_model="prophet", champion_model="xgboost")
        # Drop the champion's series, keep its metrics row.
        forecasts = session_store.get_forecasts(test_tenant["id"], completed_session["id"])
        del forecasts["SKU-C"]["xgboost"]
        session_store.set_forecasts(test_tenant["id"], completed_session["id"], forecasts)

        r = client.get(
            f"/api/v1/sessions/{completed_session['id']}/sku-intelligence/SKU-C",
            headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["model"] == "prophet"
