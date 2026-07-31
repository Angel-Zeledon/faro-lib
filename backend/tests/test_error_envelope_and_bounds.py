"""
An invalid request must be refused, in the API's own shape, without writing.

Three defects found by throwing hostile payloads at the running server:

1. `NaN` in a JSON body — which Python's `json.dumps` emits by default, so any
   integration sends it without trying — produced a 500 with an empty
   `text/plain` body. On fields with no bounds the value was WRITTEN FIRST:
   `service_level: NaN` reached JSONB, Postgres stored it as `null`, and
   `business_cfg.get("service_level", 0.95)` then did NOT fall back, because
   the key exists with value None. The user saw "the server broke", assumed
   nothing was saved, and their service level had been erased.
2. `/config/forecast` accepted `horizon` of 0, -1 and 1e18 while
   `SessionConfig.validate()` raises on anything below 1 — the exact defect the
   `ValidationConfigRequest` docstring says was fixed for `train_ratio`,
   surviving on the sibling endpoint.
3. Every unhandled exception answered bare `text/plain` "Internal Server Error",
   which is indistinguishable from the proxy-cannot-reach-the-backend symptom
   the team is trained to look for first.
"""

import json

import pytest

from backend.db.connection import query_one


@pytest.fixture
def session_id(client, registered_user):
    from backend.sessions import service as session_svc

    s = session_svc.create_session(
        registered_user["tenant"]["id"], registered_user["user"]["id"], "bounds-test",
    )
    return s["id"]


def _raw_post(client, headers, path, body: str):
    """Send a body verbatim — `json.dumps` emits bare NaN, and so do clients."""
    return client.post(path, content=body, headers={**headers,
                                                    "Content-Type": "application/json"})


class TestNonFiniteNumbersAreRefusedAndNothingIsWritten:

    def test_nan_service_level_is_rejected(self, client, auth_headers, session_id):
        resp = _raw_post(client, auth_headers, f"/api/v1/sessions/{session_id}/config/business",
                         json.dumps({"service_level": float("nan"), "lead_time_days": 11}))
        assert resp.status_code == 422, f"got {resp.status_code}: {resp.text[:200]}"

    def test_the_rejected_value_was_not_persisted(self, client, auth_headers, session_id):
        """The half that made it dangerous: the 500 came AFTER the write."""
        good = client.post(f"/api/v1/sessions/{session_id}/config/business",
                           json={"service_level": 0.97, "lead_time_days": 11},
                           headers=auth_headers)
        assert good.status_code == 200

        _raw_post(client, auth_headers, f"/api/v1/sessions/{session_id}/config/business",
                  json.dumps({"service_level": float("nan"), "lead_time_days": 11}))

        row = query_one("SELECT business_cfg FROM session_configs WHERE session_id = %s",
                        (session_id,))
        assert row["business_cfg"]["service_level"] == 0.97, (
            "a refused request overwrote the stored service level"
        )

    def test_a_refusal_is_json_with_an_error_code(self, client, auth_headers, session_id):
        resp = _raw_post(client, auth_headers, f"/api/v1/sessions/{session_id}/config/business",
                         json.dumps({"service_level": float("nan")}))
        assert "application/json" in resp.headers.get("content-type", "")
        assert resp.json().get("error_code") == "validation_error"

    def test_infinity_is_refused_too(self, client, auth_headers, session_id):
        resp = _raw_post(client, auth_headers,
                         f"/api/v1/sessions/{session_id}/configure/validation",
                         json.dumps({"train_ratio": float("inf")}))
        assert resp.status_code == 422

    def test_nan_inside_a_list_is_refused(self, client, auth_headers, session_id):
        resp = _raw_post(client, auth_headers,
                         f"/api/v1/sessions/{session_id}/config/forecast",
                         json.dumps({"horizon": 21, "quantiles": [float("nan"), 0.9]}))
        assert resp.status_code == 422


class TestTheApiCannotBlessAConfigTheEngineRejects:
    """`SessionConfig.validate()` is the authority; the API must not out-accept it."""

    @pytest.mark.parametrize("horizon", [0, -1, 10**18])
    def test_impossible_horizons_are_refused(self, client, auth_headers, session_id, horizon):
        resp = client.post(f"/api/v1/sessions/{session_id}/config/forecast",
                           json={"horizon": horizon}, headers=auth_headers)
        assert resp.status_code == 422, f"horizon={horizon} was accepted"

    def test_the_engine_agrees_that_zero_is_invalid(self):
        from forecasting_core.config.config import ConfigError, SessionConfig

        with pytest.raises(ConfigError):
            SessionConfig.from_dict({
                "columns": {"target": "d", "date": "dt", "group_keys": ["sku"]},
                "models": {"lightgbm": {}}, "forecast": {"horizon": 0},
            })

    def test_a_normal_horizon_still_works(self, client, auth_headers, session_id):
        resp = client.post(f"/api/v1/sessions/{session_id}/config/forecast",
                           json={"horizon": 30}, headers=auth_headers)
        assert resp.status_code == 200


class TestBusinessValuesThatWouldPoisonTheMaths:

    @pytest.mark.parametrize("field,value", [
        ("service_level", 0.0),
        ("service_level", 1.0),
        ("service_level", -1.0),
        ("service_level", 1e9),
        ("lead_time_days", 0),
        ("lead_time_days", -30),
        ("lead_time_days", 10**9),
        # Read verbatim into the MILP objective: negative makes holding stock
        # profitable, so the solver accumulates inventory without bound.
        ("holding_cost_pct", -5.0),
    ])
    def test_refused(self, client, auth_headers, session_id, field, value):
        resp = client.post(f"/api/v1/sessions/{session_id}/config/business",
                           json={field: value}, headers=auth_headers)
        assert resp.status_code == 422, f"{field}={value} was accepted"

    def test_ordinary_values_still_pass(self, client, auth_headers, session_id):
        resp = client.post(f"/api/v1/sessions/{session_id}/config/business",
                           json={"service_level": 0.95, "lead_time_days": 15,
                                 "holding_cost_pct": 0.2}, headers=auth_headers)
        assert resp.status_code == 200


class TestQuantilesAreProbabilities:

    @pytest.mark.parametrize("quantiles", [[0.0, 0.9], [0.1, 1.0], [-0.5], [1.5]])
    def test_out_of_range_refused(self, client, auth_headers, session_id, quantiles):
        resp = client.post(f"/api/v1/sessions/{session_id}/config/forecast",
                           json={"horizon": 14, "quantiles": quantiles},
                           headers=auth_headers)
        assert resp.status_code == 422, f"{quantiles} was accepted"

    def test_normal_quantiles_pass(self, client, auth_headers, session_id):
        resp = client.post(f"/api/v1/sessions/{session_id}/config/forecast",
                           json={"horizon": 14, "quantiles": [0.1, 0.5, 0.9]},
                           headers=auth_headers)
        assert resp.status_code == 200


@pytest.fixture
def serving_client(app):
    """A client that returns the 500 instead of re-raising it.

    The shared `client` fixture uses Starlette's default
    `raise_server_exceptions=True`, which re-raises an unhandled exception into
    the test rather than letting the app's own handler answer. That is useful
    for most tests and useless for this one: the whole point here is what the
    caller RECEIVES, which is exactly what that flag hides.
    """
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestUnhandledFailuresStillAnswerInTheApiShape:

    def test_a_nul_byte_in_the_path_does_not_return_bare_text(
        self, serving_client, auth_headers,
    ):
        """
        psycopg2 rejects NUL in a bind parameter, which reached the client as
        `text/plain` "Internal Server Error" — the same thing an unreachable
        backend produces, sending whoever debugs it to the wrong layer.
        """
        resp = serving_client.get("/api/v1/sessions/%00x/results", headers=auth_headers)
        assert resp.status_code in (404, 422, 500)
        assert "application/json" in resp.headers.get("content-type", ""), (
            f"answered {resp.headers.get('content-type')}: {resp.text[:120]}"
        )
        if resp.status_code == 500:
            assert resp.json().get("error_code") == "internal_error"

    def test_the_envelope_never_leaks_internals(self, serving_client, auth_headers):
        resp = serving_client.get("/api/v1/sessions/%00x/results", headers=auth_headers)
        body = resp.text.lower()
        for leak in ("traceback", "psycopg2", "nul (0x00)", 'file "'):
            assert leak not in body, f"the response leaked {leak!r}"
