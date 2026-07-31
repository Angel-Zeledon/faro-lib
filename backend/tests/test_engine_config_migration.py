"""
The bridge from stored session config to the engine's SessionConfig.

`build_engine_config` is the only place the backend translates a tenant's saved
wizard answers into the dict the ML engine runs on. Two of the new fields are
load-bearing in ways that fail silently if they are wrong:

  * `columns.inventory` gates censored-demand recovery. Passing the canonical
    default here would flag every row of every unmapped dataset as a stockout
    and inflate the whole tenant's forecast.
  * `models` must be able to carry `global_lgbm` end to end, or the model is
    selectable in the UI and never trained.
"""

import pytest

from backend.db import session_store
from backend.db.connection import query_one
from backend.workers.runner import build_engine_config


LEGACY_COLUMNS = {
    "sku_column": "sku", "date_column": "fecha", "target_column": "ventas",
}


@pytest.fixture
def make_session(client, registered_user):
    """Create a DRAFT session and seed its config blobs.

    Depends on `client` because that fixture is what initialises the DB pool,
    and on `registered_user` because a session needs an owner.
    """
    from backend.sessions import service as session_svc

    tenant_id = registered_user["tenant"]["id"]
    user_id = registered_user["user"]["id"]

    def _make(name: str, **blobs):
        s = session_svc.create_session(tenant_id, user_id, name)
        for field, value in blobs.items():
            session_store.set_field(tenant_id, s["id"], field, value)
        return tenant_id, s["id"]

    return _make


class TestInventoryMapping:
    """The guard that keeps censored-demand recovery honest."""

    def test_mapped_inventory_column_reaches_the_engine(self, make_session):
        tenant_id, sid = make_session("with-inventory", columns_cfg={
            "schema_version": "canonical_v1",
            "canonical_mapping": {
                "sku": "producto", "date": "fecha", "demand": "ventas",
                "inventory": "existencias",
            },
        })
        cfg = build_engine_config(tenant_id, sid)
        assert cfg["columns"]["inventory"] == "existencias"

    def test_unmapped_inventory_is_empty_not_the_canonical_default(self, make_session):
        """
        apply_canonical_defaults broadcasts inventory=0 into sessions that did
        not map one. If that column name reached the engine, every bucket would
        read as a stockout. The contract is an EMPTY string, which switches the
        recovery off entirely.
        """
        tenant_id, sid = make_session("no-inventory", columns_cfg={
            "schema_version": "canonical_v1",
            "canonical_mapping": {
                "sku": "producto", "date": "fecha", "demand": "ventas",
            },
        })
        cfg = build_engine_config(tenant_id, sid)
        assert cfg["columns"]["inventory"] == "", (
            "an unmapped inventory column must not be passed to the engine"
        )

    def test_legacy_sessions_get_no_inventory_column(self, make_session):
        tenant_id, sid = make_session("legacy", columns_cfg=LEGACY_COLUMNS)
        cfg = build_engine_config(tenant_id, sid)
        assert cfg["columns"]["inventory"] == ""


class TestGlobalModelReachesTheEngine:

    def test_selected_global_model_is_in_the_engine_config(self, make_session):
        tenant_id, sid = make_session(
            "global-model",
            columns_cfg=LEGACY_COLUMNS,
            models_cfg={
                "mode": "selected",
                "selected_models": ["global_lgbm", "lightgbm"],
                "hyperparameters": {"global_lgbm": {"n_estimators": 250}},
            },
        )
        cfg = build_engine_config(tenant_id, sid)
        assert "global_lgbm" in cfg["models"]
        assert cfg["models"]["global_lgbm"] == {"n_estimators": 250}

    def test_mode_all_includes_it(self, make_session):
        tenant_id, sid = make_session(
            "all-models", columns_cfg=LEGACY_COLUMNS, models_cfg={"mode": "all"},
        )
        cfg = build_engine_config(tenant_id, sid)
        assert "global_lgbm" in cfg["models"]

    def test_it_is_not_trained_when_not_selected(self, make_session):
        """Routing must never train a model the user did not choose."""
        tenant_id, sid = make_session(
            "narrow", columns_cfg=LEGACY_COLUMNS,
            models_cfg={"mode": "selected", "selected_models": ["lightgbm"]},
        )
        cfg = build_engine_config(tenant_id, sid)
        assert "global_lgbm" not in cfg["models"]


class TestHolidayCountry:

    def test_default_is_colombia(self, make_session):
        tenant_id, sid = make_session("holidays-default", columns_cfg=LEGACY_COLUMNS)
        cfg = build_engine_config(tenant_id, sid)
        assert cfg["features"]["holiday_country"] == "CO"

    def test_configured_country_reaches_the_engine(self, make_session):
        tenant_id, sid = make_session(
            "holidays-mx", columns_cfg=LEGACY_COLUMNS,
            features_cfg={"holiday_country": "MX"},
        )
        cfg = build_engine_config(tenant_id, sid)
        assert cfg["features"]["holiday_country"] == "MX"


class TestModelsEndpoint:

    def test_catalogue_lists_the_global_model(self, client, auth_headers):
        resp = client.get("/api/v1/models", headers=auth_headers)
        assert resp.status_code == 200
        names = {m["name"] for m in resp.json()["data"]}
        assert "global_lgbm" in names

    def test_engine_declares_it_available(self):
        from forecasting_core.models.factory import ModelFactory
        assert "global_lgbm" in ModelFactory.available_models()

    def test_it_is_not_built_as_a_per_sku_model(self):
        """build_ml() feeds the per-SKU Trainer. A global model landing there
        would be fitted once per SKU — the exact opposite of the point."""
        from forecasting_core.models.factory import ModelFactory
        factory = ModelFactory({"global_lgbm": {}, "lightgbm": {}})
        assert list(factory.build_ml()) == ["lightgbm"]
        assert factory.global_names() == ["global_lgbm"]


class TestRoutingReachesEverySeries:

    def test_the_global_model_is_assigned_to_every_series_type(self):
        """
        Routing exists to keep a model away from series it handles badly. The
        global model is the opposite case: the short and intermittent series the
        table sends to `naive` are exactly where borrowing from the rest of the
        catalogue is worth most.
        """
        from forecasting_core.data.quality import (
            SERIES_INTERMITTENT, SERIES_SHORT, SERIES_STABLE,
        )
        from forecasting_core.training.router import ModelRouter

        class _Report:
            def __init__(self, series_type):
                self.series_type = series_type
                self.series_flags = {series_type}

        router = ModelRouter({"global_lgbm": {}, "lightgbm": {}}, enabled=True)
        routing = router.route({
            "SHORT": _Report(SERIES_SHORT),
            "INTERMITTENT": _Report(SERIES_INTERMITTENT),
            "STABLE": _Report(SERIES_STABLE),
        })
        for sku, models in routing.items():
            assert "global_lgbm" in models, f"{sku} did not get the global model"

    def test_it_is_never_added_when_the_user_did_not_select_it(self):
        from forecasting_core.data.quality import SERIES_SHORT
        from forecasting_core.training.router import ModelRouter

        class _Report:
            series_type = SERIES_SHORT
            series_flags = {SERIES_SHORT}

        router = ModelRouter({"lightgbm": {}}, enabled=True)
        routing = router.route({"A": _Report()})
        assert "global_lgbm" not in routing["A"]


class TestModelsConfigPersistence:
    """Testing mandate: assert the DB row, and pair the permissions."""

    def _post(self, client, headers, sid, models):
        return client.post(
            f"/api/v1/sessions/{sid}/configure/models",
            json={"mode": "selected", "selected_models": models},
            headers=headers,
        )

    def test_analyst_can_select_the_global_model(
        self, client, analyst_headers, make_session,
    ):
        _tenant_id, sid = make_session("persist-ok")
        resp = self._post(client, analyst_headers, sid, ["global_lgbm", "lightgbm"])
        assert resp.status_code == 200

        row = query_one(
            "SELECT models_cfg FROM session_configs WHERE session_id = %s", (sid,)
        )
        assert row["models_cfg"]["selected_models"] == ["global_lgbm", "lightgbm"]

    def test_viewer_is_denied_and_nothing_changes(
        self, client, viewer_headers, make_session,
    ):
        _tenant_id, sid = make_session("persist-denied")
        resp = self._post(client, viewer_headers, sid, ["global_lgbm"])
        assert resp.status_code == 403

        row = query_one(
            "SELECT models_cfg FROM session_configs WHERE session_id = %s", (sid,)
        )
        assert row is None or not (row["models_cfg"] or {}).get("selected_models"), (
            "a denied request must not have written the config"
        )
