"""
A session-config POST must update only the fields it names.

Every one of these endpoints used to build its blob with `body.model_dump()`,
which fills every omitted field with the schema default. A user who set a
21-day lead time on the mapping screen and later changed only the service level
from another screen had the lead time silently reset to 7 — and the endpoint
answered 200 with the reverted config, so nothing anywhere said so. Every
purchase quantity for that session was then computed on a lead time the user
never chose and was never told about.

These tests read the OTHER fields back out of Postgres, not out of the
response: the response echo is exactly what made the bug invisible, so a test
that trusts it cannot see the bug either.
"""

import pytest

from backend.db.connection import query_one
from backend.db import session_store

pytestmark = pytest.mark.usefixtures("client")


def _stored(session_id: str, column: str) -> dict:
    """Read a config blob straight from the table, bypassing the API."""
    row = query_one(
        f"SELECT {column} FROM session_configs WHERE session_id = %s", (session_id,),
    )
    assert row is not None, f"no session_configs row was written for {session_id}"
    assert row[column] is not None, f"{column} is NULL after a 200 save"
    return row[column]


# ── business: the one that moves money ────────────────────────────────────────

class TestBusinessConfigPartialSave:
    def test_partial_save_keeps_the_fields_it_did_not_name(
        self, client, auth_headers, test_session,
    ):
        """Set three, post one, and the other two must survive in the DB."""
        sid = test_session["id"]

        first = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"holding_cost_pct": 0.5, "lead_time_days": 21, "service_level": 0.99},
            headers=auth_headers,
        )
        assert first.status_code == 200, first.text

        before = _stored(sid, "business_cfg")
        assert before["lead_time_days"]   == 21
        assert before["holding_cost_pct"] == 0.5

        second = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"service_level": 0.90},
            headers=auth_headers,
        )
        assert second.status_code == 200, second.text

        after = _stored(sid, "business_cfg")
        assert after["service_level"] == 0.90, "the field that WAS sent must change"
        assert after["lead_time_days"] == 21, (
            "lead_time_days was not in the request body and came back as the "
            "default 7 — this is the reorder-point input the user configured"
        )
        assert after["holding_cost_pct"] == 0.5, (
            "holding_cost_pct was not in the request body and was reset to the default"
        )

    def test_a_value_equal_to_the_default_is_still_an_explicit_choice(
        self, client, auth_headers, test_session,
    ):
        """`exclude_unset` must distinguish "not sent" from "sent as the default".

        This is the assumption the whole fix rests on: if pydantic could not
        tell the two apart, a user deliberately setting the lead time back to
        the default 7 would be treated as not having sent it, and their 21 would
        stick forever.
        """
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"lead_time_days": 21},
            headers=auth_headers,
        )
        assert _stored(sid, "business_cfg")["lead_time_days"] == 21

        r = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"lead_time_days": 7},   # 7 IS the schema default
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert _stored(sid, "business_cfg")["lead_time_days"] == 7, (
            "an explicitly sent value was dropped because it equalled the default"
        )

    def test_first_save_of_a_session_stores_a_complete_config(
        self, client, auth_headers, test_session,
    ):
        """Nothing stored yet: the merge must still yield every field.

        The training runner reads this blob by key. A first save that persisted
        only the one field the client sent would leave the rest missing
        entirely — which reads back as None, not as the default.
        """
        sid = test_session["id"]
        r = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"service_level": 0.97},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        cfg = _stored(sid, "business_cfg")
        assert cfg["service_level"] == 0.97
        for field, default in (
            ("lead_time_days", 7), ("holding_cost_pct", 0.20),
            ("stockout_cost_multiplier", 3.0),
        ):
            assert cfg[field] == default, (
                f"{field} is missing or wrong on a first save; the runner would "
                f"read a hole here"
            )

    def test_response_echoes_what_is_stored_not_what_was_sent(
        self, client, auth_headers, test_session,
    ):
        """The echo is how this stayed invisible — make it a measurement.

        A client that renders the response is entitled to see the whole config
        as it now exists, including the fields it did not send.
        """
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"lead_time_days": 30, "holding_cost_pct": 0.4},
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"service_level": 0.85},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        echoed = r.json()["data"]
        assert echoed == _stored(sid, "business_cfg"), (
            "the response and the database disagree about what was saved"
        )
        assert echoed["lead_time_days"] == 30

    # ── permission pair ──────────────────────────────────────────────────────

    def test_viewer_cannot_save_business_config_and_state_is_unchanged(
        self, client, auth_headers, viewer_headers, test_session,
    ):
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"lead_time_days": 21},
            headers=auth_headers,
        )
        before = _stored(sid, "business_cfg")

        r = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"lead_time_days": 3},
            headers=viewer_headers,
        )
        assert r.status_code == 403, r.text
        assert _stored(sid, "business_cfg") == before, (
            "the 403 was returned and the write happened anyway"
        )

    def test_analyst_can_save_business_config(
        self, client, analyst_headers, test_session,
    ):
        sid = test_session["id"]
        r = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"lead_time_days": 12},
            headers=analyst_headers,
        )
        assert r.status_code == 200, r.text
        assert _stored(sid, "business_cfg")["lead_time_days"] == 12


# ── the same defect, the same shape, on every other config endpoint ───────────

class TestEveryConfigEndpointMergesPartialBodies:
    def test_features_partial_save_keeps_the_lags(
        self, client, auth_headers, test_session,
    ):
        """The reported case: set lags, post only holiday_country, lags revert."""
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/configure/features",
            json={"lags": [1, 2, 3], "rolling": [3], "fourier_K": 4},
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/configure/features",
            json={"holiday_country": "MX"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        cfg = _stored(sid, "features_cfg")
        assert cfg["holiday_country"] == "MX"
        assert cfg["lags"] == [1, 2, 3], "lags reverted to the schema default"
        assert cfg["rolling"] == [3]
        assert cfg["fourier_K"] == 4

    def test_validation_partial_save_keeps_the_horizon(
        self, client, auth_headers, test_session,
    ):
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/configure/validation",
            json={"horizon": 30, "wfv_splits": 8, "train_ratio": 0.6},
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/configure/validation",
            json={"train_ratio": 0.7},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        cfg = _stored(sid, "validation_cfg")
        assert cfg["train_ratio"] == 0.7
        assert cfg["horizon"]     == 30, "horizon reverted to the schema default"
        assert cfg["wfv_splits"]  == 8

    def test_forecast_partial_save_keeps_the_quantiles(
        self, client, auth_headers, test_session,
    ):
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/config/forecast",
            json={"horizon": 60, "quantiles": [0.05, 0.5, 0.95]},
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/config/forecast",
            json={"horizon": 45},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        cfg = _stored(sid, "forecast_cfg")
        assert cfg["horizon"]   == 45
        assert cfg["quantiles"] == [0.05, 0.5, 0.95], (
            "quantiles reverted to the default [0.1, 0.9]"
        )

    def test_models_partial_save_keeps_the_hyperparameters(
        self, client, auth_headers, test_session,
    ):
        """Routing must never train a model the user did not select — and it
        cannot honour hyperparameters a later save silently emptied.

        `selected_models` is re-sent here on purpose; see the test below for
        why a body that omits it never reaches the handler at all.
        """
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/configure/models",
            json={
                "mode": "selected",
                "selected_models": ["lightgbm", "prophet"],
                "hyperparameters": {"lightgbm": {"n_estimators": 500}},
                "auto_select_best": False,
            },
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/configure/models",
            json={"selected_models": ["lightgbm", "prophet"], "selection_metric": "mape"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        cfg = _stored(sid, "models_cfg")
        assert cfg["selection_metric"] == "mape"
        assert cfg["selected_models"] == ["lightgbm", "prophet"]
        assert cfg["hyperparameters"] == {"lightgbm": {"n_estimators": 500}}, (
            "the tuned hyperparameters were wiped by a save that never mentioned them"
        )
        assert cfg["auto_select_best"] is False, (
            "auto_select_best flipped back to the default True"
        )

    def test_models_still_rejects_a_body_that_omits_the_selection(
        self, client, auth_headers, test_session,
    ):
        """KNOWN LIMITATION, asserted so it cannot rot unnoticed.

        `ModelsConfigRequest._selected_mode_needs_at_least_one_model` guards a
        real bug (a session configured to train zero models still advances to
        MODELS_CONFIGURED and forecasts nothing). But it runs at pydantic
        validation time, on `mode`/`selected_models` as they come out of the
        SCHEMA DEFAULTS — so it cannot tell "the client did not send
        selected_models" from "the client sent an empty list", which is the
        same confusion this whole module exists to remove. The consequence:
        /configure/models is the one config endpoint that cannot take a
        genuinely partial body, and it fails LOUDLY with a 422 rather than
        silently reverting, which is why it is recorded rather than worked
        around here.

        Fixing it means validating the MERGED config in the handler instead of
        the raw request body; that belongs in the same change as the validator.
        """
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/configure/models",
            json={"mode": "selected", "selected_models": ["lightgbm"]},
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/configure/models",
            json={"selection_metric": "mape"},
            headers=auth_headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"][0]["type"] == "no_models_selected"
        # The rejection must not have damaged what was already configured.
        assert _stored(sid, "models_cfg")["selected_models"] == ["lightgbm"]

    def test_columns_partial_save_keeps_the_outlier_config(
        self, client, auth_headers, test_session, uploaded_dataset,
    ):
        """Nested request models must merge too, or the bug just moves one
        level down: a body touching one outlier knob would blank the rest."""
        sid = test_session["id"]
        client.post(f"/api/v1/sessions/{sid}/dataset",
                    json={"dataset_id": uploaded_dataset["id"]}, headers=auth_headers)
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)

        client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={
                "date_column": "date", "target_column": "sales", "sku_column": "sku",
                "gap_fill": "interpolate",
                "outlier_config": {"strategy": "winsorize_sigma", "n_sigma": 2.5},
            },
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={
                "date_column": "date", "target_column": "sales",
                "outlier_config": {"iqr_k": 2.0},
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        cfg = _stored(sid, "columns_cfg")
        assert cfg["gap_fill"] == "interpolate", "gap_fill reverted to 'leave'"
        assert cfg["sku_column"] == "sku", "sku_column was dropped to None"
        oc = cfg["outlier_config"]
        assert oc["iqr_k"] == 2.0
        assert oc["strategy"] == "winsorize_sigma", (
            "the nested outlier strategy was reset by a partial nested body"
        )
        assert oc["n_sigma"] == 2.5


class TestMergeDoesNotBlurTheTwoColumnSchemas:
    def test_canonical_body_does_not_merge_onto_an_old_style_config(
        self, client, auth_headers, test_session, uploaded_dataset,
    ):
        """configure/columns takes two different schemas. Merging across them
        would leave both vocabularies in one row and the runner reads whichever
        it finds first."""
        sid = test_session["id"]
        client.post(f"/api/v1/sessions/{sid}/dataset",
                    json={"dataset_id": uploaded_dataset["id"]}, headers=auth_headers)
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)

        client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
            headers=auth_headers,
        )
        r = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"canonical_mapping": {"sku": "sku", "date": "date", "demand": "sales"}},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        cfg = _stored(sid, "columns_cfg")
        assert cfg["schema_version"] == "canonical_v1"
        assert cfg["canonical_mapping"]["demand"] == "sales"
        assert "date_column" not in cfg, (
            "the old-style keys survived into a canonical config — the two "
            "schemas were merged into one ambiguous row"
        )


class TestTheStateMachineStillFiresWhenItDidBefore:
    def test_features_save_still_advances_the_wizard(
        self, client, auth_headers, test_session, uploaded_dataset,
    ):
        """The merge changed how the blob is built, not when a session moves on."""
        sid = test_session["id"]
        client.post(f"/api/v1/sessions/{sid}/dataset",
                    json={"dataset_id": uploaded_dataset["id"]}, headers=auth_headers)
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
            headers=auth_headers,
        )
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] \
            == "COLUMNS_CONFIGURED"

        client.post(f"/api/v1/sessions/{sid}/configure/features",
                    json={"lags": [1, 7]}, headers=auth_headers)
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] \
            == "FEATURES_CONFIGURED"

        client.post(f"/api/v1/sessions/{sid}/configure/models",
                    json={"selected_models": ["lightgbm"]}, headers=auth_headers)
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] \
            == "MODELS_CONFIGURED"


class TestSetFieldReportsWhatTheDatabaseHolds:
    def test_set_field_returns_the_stored_blob(self, test_tenant, test_session):
        """The handler's echo is only trustworthy because this is a read."""
        tid, sid = test_tenant["id"], test_session["id"]
        returned = session_store.set_field(tid, sid, "business_cfg", {"lead_time_days": 9})
        assert returned == {"lead_time_days": 9}
        assert returned == session_store.get_field(tid, sid, "business_cfg")

    def test_set_field_reports_the_null_postgres_actually_stored(
        self, test_tenant, test_session,
    ):
        """NaN does not survive the trip: `_json` turns it into None.

        A handler echoing its own argument would tell the user it saved a
        number, while every later read gets None.
        """
        tid, sid = test_tenant["id"], test_session["id"]
        returned = session_store.set_field(
            tid, sid, "business_cfg", {"service_level": float("nan")},
        )
        assert returned["service_level"] is None, (
            "set_field echoed the NaN it was handed instead of the null stored"
        )
