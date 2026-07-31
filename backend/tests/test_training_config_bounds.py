"""The training wizard must not bless a configuration it cannot train.

Every case here was a 200 against the running API before these bounds existed,
and every one of them fails LATER and QUIETLY:

  * an unknown model name advanced the session to MODELS_CONFIGURED and the
    factory then skipped it, so the run trained fewer models than the user
    picked — or, with `selected_models: []`, none at all — and reported success;
  * an unknown `holiday_country` is caught by `HolidayCalendar._load_year`,
    which degrades to Easter + Christmas with one server-log line, producing a
    measurably worse model that still looks like a good one;
  * a negative lag is `shift(-1)`: tomorrow's demand handed to the model as a
    feature, past a leakage guard that only drops features identical to the
    target;
  * `min_history: 1e9` skips every series and completes with zero forecasts.

So each bound is asserted as a PAIR: the nonsense value is refused with 422 AND
the value the product actually uses still passes. A bound that rejects the
wizard's own defaults would be a worse bug than the one it closes.
"""

from __future__ import annotations

import pytest

from backend.db.connection import query_one

# What the Quick Start wizard (Frontend/src/app/ventas/page.tsx) and
# backend/sessions/defaults.py actually post. These must all survive.
WIZARD_FEATURES = {"lags": [1, 7, 14, 28], "rolling": [7, 14, 28], "diffs": [1],
                   "calendar": True, "ewm_spans": [7, 14]}
WIZARD_MODELS = ["global_lgbm", "lightgbm", "prophet", "croston", "xgboost"]
WIZARD_VALIDATION = {"train_ratio": 0.8, "walk_forward": True, "wfv_splits": 3,
                     "min_history": 20, "seasonal_period": 7}


@pytest.fixture
def session_id(client, registered_user):
    from backend.sessions import service as session_svc

    s = session_svc.create_session(
        registered_user["tenant"]["id"], registered_user["user"]["id"], "bounds",
    )
    return s["id"]


def _features(client, headers, sid, body):
    return client.post(f"/api/v1/sessions/{sid}/configure/features",
                       json=body, headers=headers)


def _models(client, headers, sid, body):
    return client.post(f"/api/v1/sessions/{sid}/configure/models",
                       json=body, headers=headers)


def _validation(client, headers, sid, body):
    return client.post(f"/api/v1/sessions/{sid}/configure/validation",
                       json=body, headers=headers)


def _cfg(sid, column):
    row = query_one(
        f"SELECT {column} FROM session_configs WHERE session_id = %s", (sid,)
    )
    return (row or {}).get(column)


# ── Models ─────────────────────────────────────────────────────────────────


class TestOnlyModelsTheEngineCanTrainAreAccepted:

    def test_the_legal_set_is_the_engines_own(self, client, auth_headers, session_id):
        """The allowed names must come from ModelFactory, not a copy of it.

        A hardcoded list in the schema would drift the first time a model is
        added to the engine, and the symptom would be a model the UI offers and
        the API refuses. So: assert that EVERY name the engine advertises is
        accepted, one request each — the only assertion that cannot pass while
        the two lists disagree.
        """
        from forecasting_core.models.factory import ModelFactory

        engine_models = ModelFactory.available_models()
        assert engine_models, "the engine advertises no models at all"

        for name in engine_models:
            resp = _models(client, auth_headers, session_id,
                           {"mode": "selected", "selected_models": [name]})
            assert resp.status_code == 200, (
                f"the engine can train {name!r} but the API refused it: {resp.text[:200]}"
            )

    def test_the_wizards_own_selection_is_accepted(self, client, auth_headers, session_id):
        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": WIZARD_MODELS,
                        "hyperparameters": {}, "auto_select_best": True,
                        "selection_metric": "wape"})
        assert resp.status_code == 200, resp.text[:300]
        assert _cfg(session_id, "models_cfg")["selected_models"] == WIZARD_MODELS

    def test_an_invented_model_name_is_refused(self, client, auth_headers, session_id):
        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": ["definitely_not_a_model"]})
        assert resp.status_code == 422, resp.text[:300]

    def test_one_bad_name_among_good_ones_is_refused(self, client, auth_headers, session_id):
        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected",
                        "selected_models": ["lightgbm", "definitely_not_a_model"]})
        assert resp.status_code == 422

    def test_the_refusal_names_the_offending_model_and_the_legal_set(
        self, client, auth_headers, session_id,
    ):
        """The frontend renders `errors.validation.unknown_model` from ctx.

        The values must arrive as parameters, not baked into a sentence, or the
        Spanish rendering has nothing to interpolate.
        """
        from forecasting_core.models.factory import ModelFactory

        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": ["banana"]})
        detail = resp.json()["detail"]
        ctx = next(d["ctx"] for d in detail if d["type"] == "unknown_model")
        assert ctx["model"] == "banana"
        for name in ModelFactory.available_models():
            assert name in ctx["allowed"]

    def test_zero_models_is_refused(self, client, auth_headers, session_id):
        """A session configured to train nothing used to reach MODELS_CONFIGURED."""
        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": []})
        assert resp.status_code == 422, resp.text[:300]

    def test_mode_all_may_leave_the_list_empty(self, client, auth_headers, session_id):
        """The runner replaces the list wholesale for mode 'all' — an empty list
        there is not a session that trains nothing, so it must still pass."""
        resp = _models(client, auth_headers, session_id,
                       {"mode": "all", "selected_models": []})
        assert resp.status_code == 200, resp.text[:300]

    def test_ten_thousand_names_are_refused(self, client, auth_headers, session_id):
        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": ["lightgbm"] * 10_000})
        assert resp.status_code == 422

    def test_an_unimplemented_mode_is_refused(self, client, auth_headers, session_id):
        resp = _models(client, auth_headers, session_id,
                       {"mode": "garbage", "selected_models": ["lightgbm"]})
        assert resp.status_code == 422

    def test_selection_metric_is_not_narrowed_to_an_invented_enum(
        self, client, auth_headers, session_id,
    ):
        """Nothing reads `selection_metric` — not the runner, not any service.

        Rejecting values here would enforce a rule with no consumer, so only the
        length is capped. This test exists so a future enum has to argue with it.
        """
        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": ["lightgbm"],
                        "selection_metric": "banana"})
        assert resp.status_code == 200
        assert _cfg(session_id, "models_cfg")["selection_metric"] == "banana"

    def test_a_megabyte_selection_metric_is_refused(self, client, auth_headers, session_id):
        resp = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": ["lightgbm"],
                        "selection_metric": "x" * 100_000})
        assert resp.status_code == 422

    def test_a_refused_selection_is_not_persisted(self, client, auth_headers, session_id):
        good = _models(client, auth_headers, session_id,
                       {"mode": "selected", "selected_models": ["lightgbm"]})
        assert good.status_code == 200

        _models(client, auth_headers, session_id,
                {"mode": "selected", "selected_models": ["definitely_not_a_model"]})

        assert _cfg(session_id, "models_cfg")["selected_models"] == ["lightgbm"], (
            "a refused request overwrote the stored model selection"
        )

    def test_viewer_is_denied_and_nothing_is_written(
        self, client, viewer_headers, session_id,
    ):
        resp = _models(client, viewer_headers, session_id,
                       {"mode": "selected", "selected_models": ["lightgbm"]})
        assert resp.status_code == 403
        assert not (_cfg(session_id, "models_cfg") or {}).get("selected_models")

    def test_analyst_succeeds_and_the_row_changes(
        self, client, analyst_headers, session_id,
    ):
        resp = _models(client, analyst_headers, session_id,
                       {"mode": "selected", "selected_models": ["croston"]})
        assert resp.status_code == 200
        assert _cfg(session_id, "models_cfg")["selected_models"] == ["croston"]


# ── Holiday country ────────────────────────────────────────────────────────


class TestTheHolidayCalendarCountryMustExist:

    @pytest.mark.parametrize("country", ["CO", "MX", "PE", "CL", "AR", "US"])
    def test_the_countries_the_product_sells_into_are_accepted(
        self, client, auth_headers, session_id, country,
    ):
        resp = _features(client, auth_headers, session_id,
                         {**WIZARD_FEATURES, "holiday_country": country})
        assert resp.status_code == 200, resp.text[:300]
        assert _cfg(session_id, "features_cfg")["holiday_country"] == country

    def test_lowercase_is_normalized_rather_than_refused(
        self, client, auth_headers, session_id,
    ):
        """`HolidayCalendar` upper-cases anyway; refusing "mx" would be a bound
        that rejects a value the product handles perfectly well."""
        resp = _features(client, auth_headers, session_id,
                         {**WIZARD_FEATURES, "holiday_country": "mx"})
        assert resp.status_code == 200
        assert _cfg(session_id, "features_cfg")["holiday_country"] == "MX"

    def test_omitting_the_country_still_defaults_to_colombia(
        self, client, auth_headers, session_id,
    ):
        resp = _features(client, auth_headers, session_id, WIZARD_FEATURES)
        assert resp.status_code == 200
        assert _cfg(session_id, "features_cfg")["holiday_country"] == "CO"

    # The value is built inside the test, not parametrized: a 5 MB parameter
    # ends up in the test id, and pytest exports that id as an env var.
    @pytest.mark.parametrize("label", ["empty", "not_a_country", "200_letters",
                                       "emoji", "five_megabytes"])
    def test_a_country_without_a_calendar_is_refused(
        self, client, auth_headers, session_id, label,
    ):
        country = {
            "empty": "",
            "not_a_country": "ZZZZ",
            "200_letters": "A" * 200,
            "emoji": "\U0001F600",
            "five_megabytes": "x" * 5_000_000,
        }[label]
        resp = _features(client, auth_headers, session_id,
                         {**WIZARD_FEATURES, "holiday_country": country})
        assert resp.status_code == 422, f"{label} was accepted"

    def test_an_empty_country_does_not_silently_become_colombia(
        self, client, auth_headers, session_id,
    ):
        """`HolidayCalendar` reads "" as `country or DEFAULT_COUNTRY` → "CO".

        A Mexican distributor who cleared the field would silently get a
        Colombian calendar. Omitting the field is how you ask for the default;
        erasing it is a question, and the API asks it back.
        """
        first = _features(client, auth_headers, session_id,
                          {**WIZARD_FEATURES, "holiday_country": "MX"})
        assert first.status_code == 200

        blank = _features(client, auth_headers, session_id,
                          {**WIZARD_FEATURES, "holiday_country": ""})
        assert blank.status_code == 422
        assert _cfg(session_id, "features_cfg")["holiday_country"] == "MX", (
            "a refused country overwrote the stored one"
        )

    def test_the_refusal_carries_the_country_as_a_parameter(
        self, client, auth_headers, session_id,
    ):
        resp = _features(client, auth_headers, session_id,
                         {**WIZARD_FEATURES, "holiday_country": "ZZZZ"})
        detail = resp.json()["detail"]
        ctx = next(d["ctx"] for d in detail if d["type"] == "unknown_country")
        assert ctx["country"] == "ZZZZ"

    def test_the_allowed_set_is_the_holidays_packages_own(self):
        """Not a hardcoded list of LatAm codes — whatever the engine can resolve.

        `HolidayCalendar` calls `holidays.country_holidays(code)`; anything that
        call accepts must pass, or the API refuses a calendar the engine would
        have built.
        """
        import holidays

        from backend.schemas.configuration import _supported_holiday_countries

        supported = _supported_holiday_countries()
        assert supported == frozenset(holidays.list_supported_countries().keys())

    def test_viewer_is_denied_and_nothing_is_written(
        self, client, viewer_headers, session_id,
    ):
        resp = _features(client, viewer_headers, session_id,
                         {**WIZARD_FEATURES, "holiday_country": "MX"})
        assert resp.status_code == 403
        assert not (_cfg(session_id, "features_cfg") or {}).get("holiday_country")

    def test_analyst_succeeds_and_the_row_changes(
        self, client, analyst_headers, session_id,
    ):
        resp = _features(client, analyst_headers, session_id,
                         {**WIZARD_FEATURES, "holiday_country": "MX"})
        assert resp.status_code == 200
        assert _cfg(session_id, "features_cfg")["holiday_country"] == "MX"


# ── Feature windows ────────────────────────────────────────────────────────


class TestFeatureWindowsAreCountsOfPeriodsBackward:

    def test_the_wizards_own_features_still_pass(self, client, auth_headers, session_id):
        resp = _features(client, auth_headers, session_id, WIZARD_FEATURES)
        assert resp.status_code == 200, resp.text[:300]
        stored = _cfg(session_id, "features_cfg")
        assert stored["lags"] == [1, 7, 14, 28]
        assert stored["rolling"] == [7, 14, 28]
        assert stored["ewm_spans"] == [7, 14]

    def test_the_engines_own_schema_defaults_still_pass(self, client, auth_headers, session_id):
        """`SessionConfig.schema()` publishes lags [1,7,14] and diffs [1,7]."""
        resp = _features(client, auth_headers, session_id,
                         {"lags": [1, 7, 14], "diffs": [1, 7], "rolling": [7, 14, 28],
                          "calendar": True})
        assert resp.status_code == 200, resp.text[:300]

    def test_a_years_worth_of_history_is_still_configurable(
        self, client, auth_headers, session_id,
    ):
        """The ceiling must clear annual seasonality on daily data — a 365-day
        lag and a 365-day rolling window are a real configuration."""
        resp = _features(client, auth_headers, session_id,
                         {"lags": [1, 7, 365], "rolling": [28, 365], "diffs": [1, 7],
                          "calendar": True, "ewm_spans": [7, 90],
                          "fourier_periods": [7, 30, 365], "fourier_K": 3})
        assert resp.status_code == 200, resp.text[:300]
        assert _cfg(session_id, "features_cfg")["fourier_periods"] == [7, 30, 365]

    @pytest.mark.parametrize("body,label", [
        ({"lags": [-1, -7]},              "negative lags are shift() into the future"),
        ({"lags": [0]},                   "lag 0 is the target itself"),
        ({"lags": [1_000_000_000]},       "a lag of 1e9"),
        ({"lags": list(range(1, 10_001))}, "10 000 lags"),
        ({"rolling": [0]},                "a rolling window of 0"),
        ({"rolling": [-5]},               "a negative rolling window"),
        ({"diffs": [-3]},                 "a negative diff differences a future row"),
        ({"ewm_spans": [-1]},             "pandas requires span >= 1"),
        ({"ewm_spans": [0]},              "span 0"),
        ({"fourier_K": -5},               "negative harmonics produce no columns"),
        ({"fourier_K": 0},                "zero harmonics produce no columns"),
        ({"fourier_K": 1_000_000},        "a million harmonics"),
        ({"fourier_periods": [0]},        "period 0 divides by zero in fourier_frame"),
        ({"fourier_periods": [1]},        "period 1 makes every term constant"),
        ({"fourier_periods": [-7]},       "a negative period"),
    ])
    def test_nonsense_windows_are_refused(
        self, client, auth_headers, session_id, body, label,
    ):
        resp = _features(client, auth_headers, session_id, {**WIZARD_FEATURES, **body})
        assert resp.status_code == 422, f"{label}: accepted with {resp.status_code}"

    def test_a_refused_feature_config_is_not_persisted(
        self, client, auth_headers, session_id,
    ):
        good = _features(client, auth_headers, session_id, WIZARD_FEATURES)
        assert good.status_code == 200

        _features(client, auth_headers, session_id, {**WIZARD_FEATURES, "lags": [-1]})

        assert _cfg(session_id, "features_cfg")["lags"] == [1, 7, 14, 28], (
            "a refused request overwrote the stored lags"
        )

    def test_viewer_is_denied_and_nothing_is_written(
        self, client, viewer_headers, session_id,
    ):
        resp = _features(client, viewer_headers, session_id, WIZARD_FEATURES)
        assert resp.status_code == 403
        assert _cfg(session_id, "features_cfg") is None

    def test_analyst_succeeds_and_the_row_changes(
        self, client, analyst_headers, session_id,
    ):
        resp = _features(client, analyst_headers, session_id,
                         {**WIZARD_FEATURES, "lags": [1, 7]})
        assert resp.status_code == 200
        assert _cfg(session_id, "features_cfg")["lags"] == [1, 7]


# ── Validation config ──────────────────────────────────────────────────────


class TestValidationConfigIsBoundedOnBothSides:

    def test_the_wizards_own_validation_config_still_passes(
        self, client, auth_headers, session_id,
    ):
        resp = _validation(client, auth_headers, session_id, WIZARD_VALIDATION)
        assert resp.status_code == 200, resp.text[:300]
        stored = _cfg(session_id, "validation_cfg")
        assert stored["min_history"] == 20
        assert stored["seasonal_period"] == 7

    def test_a_generous_but_real_config_still_passes(
        self, client, auth_headers, session_id,
    ):
        """Annual seasonality on daily data, two years of required history and a
        full year of forecast are all inside the ceilings."""
        resp = _validation(client, auth_headers, session_id,
                           {"train_ratio": 0.9, "walk_forward": True, "wfv_splits": 10,
                            "min_history": 730, "seasonal_period": 365, "horizon": 365})
        assert resp.status_code == 200, resp.text[:300]

    @pytest.mark.parametrize("body,label", [
        ({"horizon": 10 ** 18},         "horizon 1e18"),
        ({"horizon": 366},              "a horizon past the year ForecastConfigRequest caps at"),
        ({"min_history": 10 ** 9},      "min_history 1e9 skips every series"),
        ({"seasonal_period": 10 ** 9},  "seasonal_period 1e9 is an OOM, not a fit"),
        ({"seasonal_period": 367},      "a seasonal period longer than a year"),
        ({"wfv_splits": 11},            "wfv_splits was already bounded — the guard must stay"),
    ])
    def test_absurd_magnitudes_are_refused(
        self, client, auth_headers, session_id, body, label,
    ):
        resp = _validation(client, auth_headers, session_id, {**WIZARD_VALIDATION, **body})
        assert resp.status_code == 422, f"{label}: accepted with {resp.status_code}"

    def test_the_two_horizon_endpoints_agree(self, client, auth_headers, session_id):
        """The runner falls back to `validation_cfg.horizon` when `forecast_cfg`
        has none, so a value one endpoint refuses cannot be reachable via the
        other."""
        too_big = 10 ** 18
        via_validation = _validation(client, auth_headers, session_id,
                                     {**WIZARD_VALIDATION, "horizon": too_big})
        via_forecast = client.post(f"/api/v1/sessions/{session_id}/config/forecast",
                                   json={"horizon": too_big}, headers=auth_headers)
        assert via_validation.status_code == via_forecast.status_code == 422

    def test_a_refused_validation_config_is_not_persisted(
        self, client, auth_headers, session_id,
    ):
        good = _validation(client, auth_headers, session_id, WIZARD_VALIDATION)
        assert good.status_code == 200

        _validation(client, auth_headers, session_id,
                    {**WIZARD_VALIDATION, "min_history": 10 ** 9})

        assert _cfg(session_id, "validation_cfg")["min_history"] == 20, (
            "a refused request overwrote the stored min_history"
        )

    def test_viewer_is_denied_and_nothing_is_written(
        self, client, viewer_headers, session_id,
    ):
        resp = _validation(client, viewer_headers, session_id, WIZARD_VALIDATION)
        assert resp.status_code == 403
        assert _cfg(session_id, "validation_cfg") is None

    def test_analyst_succeeds_and_the_row_changes(
        self, client, analyst_headers, session_id,
    ):
        resp = _validation(client, analyst_headers, session_id,
                           {**WIZARD_VALIDATION, "min_history": 30})
        assert resp.status_code == 200
        assert _cfg(session_id, "validation_cfg")["min_history"] == 30


# ── Canonical column mapping: English backend, localizable envelope ────────


class TestColumnMappingFailsInEnglishWithAStructuredCode:
    """CLAUDE.md: backend user-facing copy is English or a code + params.

    `validate_required` used to raise Spanish sentences — "'demand' → columna
    'nope' no existe en el archivo" — straight out of backend logic.
    """

    def test_a_missing_required_field_carries_a_code_and_params(self):
        from backend.errors import AppError
        from backend.schemas.configuration import CanonicalColumnsRequest

        req = CanonicalColumnsRequest(canonical_mapping={"date": "fecha"})
        with pytest.raises(AppError) as exc:
            req.validate_required(["fecha", "sku_id", "ventas"])

        assert exc.value.code == "canonical_columns_missing"
        assert exc.value.status_code == 422
        assert exc.value.params["fields"] == "demand, sku"

    def test_a_column_absent_from_the_file_carries_a_code_and_params(self):
        from backend.errors import AppError
        from backend.schemas.configuration import CanonicalColumnsRequest

        req = CanonicalColumnsRequest(
            canonical_mapping={"sku": "sku_id", "date": "fecha", "demand": "nope"},
        )
        with pytest.raises(AppError) as exc:
            req.validate_required(["fecha", "sku_id", "ventas"])

        assert exc.value.code == "canonical_columns_not_in_file"
        assert exc.value.params["fields"] == "demand"
        assert exc.value.params["chosen"] == "nope"
        assert exc.value.params["columns"] == "fecha, sku_id, ventas"

    def test_an_optional_field_pointing_at_a_ghost_column_is_still_caught(self):
        from backend.errors import AppError
        from backend.schemas.configuration import CanonicalColumnsRequest

        req = CanonicalColumnsRequest(
            canonical_mapping={"sku": "sku_id", "date": "fecha", "demand": "ventas",
                               "store": "MISSING"},
        )
        with pytest.raises(AppError) as exc:
            req.validate_required(["fecha", "sku_id", "ventas"])
        assert exc.value.params["chosen"] == "MISSING"

    def test_a_valid_mapping_raises_nothing(self):
        from backend.schemas.configuration import CanonicalColumnsRequest

        req = CanonicalColumnsRequest(
            canonical_mapping={"sku": "sku_id", "date": "fecha", "demand": "ventas"},
        )
        req.validate_required(["fecha", "sku_id", "ventas"])  # must not raise

    def test_no_message_the_backend_produces_is_in_spanish(self):
        """The specific regression: Spanish prose living in backend logic.

        Checked as words, not as a language guess — these are the exact ones the
        old sentences used, and they are not English.
        """
        from backend.errors import AppError
        from backend.schemas.configuration import CanonicalColumnsRequest

        spanish = ("requerido", "columna", "no existe", "archivo", "disponibles")
        for mapping in ({}, {"sku": "ghost", "date": "fecha", "demand": "ventas"}):
            req = CanonicalColumnsRequest(canonical_mapping=mapping)
            with pytest.raises(AppError) as exc:
                req.validate_required(["fecha", "ventas"])
            message = str(exc.value).lower()
            for word in spanish:
                assert word not in message, f"Spanish leaked into {message!r}"

    def test_the_endpoint_still_answers_422(
        self, client, auth_headers, configured_session,
    ):
        """AppError subclasses ValueError, so the API's existing catch still
        converts it into the 422 envelope the frontend localizes."""
        sid = configured_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"canonical_mapping": {"sku": "sku", "date": "date",
                                        "demand": "definitely_not_a_column"},
                  "defaults_override": {}},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text[:300]
        body = resp.json()
        assert body["error_code"], "no machine code for the frontend to localize"
        assert isinstance(body["detail"], str)
