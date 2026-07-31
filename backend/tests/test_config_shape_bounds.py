"""
Config the runner cannot honour must not be accepted with a 200.

Two fields reached the training path unchecked, and each failed in its own
direction — one silently, one loudly.

**Outlier strategy.** `_apply_outlier_treatment` is a plain if/elif chain that
ENDS. A name outside it matches no branch, so the run does nothing, raises
nothing and reports nothing — while the config screen keeps saying the treatment
is active. Measured: `strategy: "no_existe"` was accepted with a 200 and quietly
became "leave". The user's explicit choice was not honoured and they were not
told, which is the exact shape the repo's silent-failures notes warn about.

The numbers around it fail differently: `n_sigma <= 0` winsorizes every point
onto the mean, which is a flat line the models then learn perfectly and forecast
uselessly. A percentile at or past 50 clips from both ends beyond the median and
does the same. Those destroy the series rather than treating it, so they are
bounded; anything inside the bound is left alone.

**Hyperparameters.** `runner.py` builds `{model: hyperparams.get(model, {})}`
and hands it to the engine, so whatever is stored here is passed to the
library's constructor. A block keyed by a model that does not exist is dead
config that looks live; a nested value where a scalar is expected fails deep
inside the fit and takes the whole run down with a library traceback — the file
was fine, the config killed it.

What is deliberately NOT checked: whether a given library parameter is in range.
Whether `n_estimators` may be negative is LightGBM's rule to state, not ours to
guess, and a table of invented limits would reject values the product may later
want. Only the shape the runner and the factory actually require is enforced.
"""

import pytest

from pydantic import ValidationError

from backend.schemas.configuration import ModelsConfigRequest, OutlierConfig


def _err(exc_info) -> str:
    return exc_info.value.errors()[0]["type"]


class TestOutlierStrategyMustExist:

    def test_an_invented_strategy_is_refused(self):
        with pytest.raises(ValidationError) as e:
            OutlierConfig(strategy="no_existe")
        assert _err(e) == "outlier_strategy_unknown"

    def test_a_per_sku_override_cannot_smuggle_one_in(self):
        """The override is the same decision through a different door."""
        with pytest.raises(ValidationError) as e:
            OutlierConfig(per_sku_overrides={"SKU-1": "no_existe"})
        assert _err(e) == "outlier_strategy_unknown"

    @pytest.mark.parametrize("strategy", [
        "leave", "winsorize_sigma", "winsorize_pct", "iqr_fence", "remove", "log1p",
    ])
    def test_every_strategy_the_runner_implements_is_accepted(self, strategy):
        """If this fails, the schema and the runner have drifted apart and the
        product just lost a treatment the engine still supports."""
        assert OutlierConfig(strategy=strategy).strategy == strategy

    def test_the_message_lists_what_is_allowed(self):
        """A refusal the user cannot act on is only half a refusal."""
        with pytest.raises(ValidationError) as e:
            OutlierConfig(strategy="nope")
        ctx = e.value.errors()[0].get("ctx") or {}
        assert "winsorize_sigma" in str(ctx.get("allowed", ""))


class TestOutlierNumbersThatDestroyTheSeries:

    @pytest.mark.parametrize("kw", [
        {"n_sigma": 0}, {"n_sigma": -3}, {"n_sigma": 1000},
        {"percentile": 0}, {"percentile": 50}, {"percentile": 99999},
        {"iqr_k": 0}, {"iqr_k": -1},
    ])
    def test_they_are_refused(self, kw):
        with pytest.raises(ValidationError):
            OutlierConfig(**kw)

    @pytest.mark.parametrize("kw", [
        {"n_sigma": 2.5}, {"n_sigma": 3.0}, {"percentile": 1.0},
        {"percentile": 5.0}, {"iqr_k": 1.5}, {"iqr_k": 3.0},
    ])
    def test_ordinary_values_are_untouched(self, kw):
        cfg = OutlierConfig(**kw)
        for key, value in kw.items():
            assert getattr(cfg, key) == value

    def test_per_sku_numbers_are_bounded_too(self):
        with pytest.raises(ValidationError):
            OutlierConfig(per_sku_n_sigma={"SKU-1": -2})

    def test_the_override_maps_are_capped(self):
        """Straight into JSONB otherwise; 5000 is far past any plan's max_skus."""
        with pytest.raises(ValidationError) as e:
            OutlierConfig(per_sku_overrides={f"s{i}": "leave" for i in range(6000)})
        assert _err(e) == "too_long"


class TestHyperparametersReachARealModel:

    def test_a_block_for_a_model_that_does_not_exist(self):
        with pytest.raises(ValidationError) as e:
            ModelsConfigRequest(selected_models=["lightgbm"],
                                hyperparameters={"definitely_not_a_model": {"a": 1}})
        assert _err(e) == "unknown_model"

    @pytest.mark.parametrize("value", [{"b": 1}, [1, 2], {"nested": {"deep": 1}}])
    def test_a_non_scalar_value_is_refused(self, value):
        """It would fail inside the library's fit and take the run down."""
        with pytest.raises(ValidationError) as e:
            ModelsConfigRequest(selected_models=["lightgbm"],
                                hyperparameters={"lightgbm": {"a": value}})
        assert _err(e) == "hyperparameter_not_scalar"

    def test_a_huge_string_is_refused(self):
        with pytest.raises(ValidationError) as e:
            ModelsConfigRequest(selected_models=["lightgbm"],
                                hyperparameters={"lightgbm": {"a": "A" * 5000}})
        assert _err(e) == "hyperparameter_too_long"

    def test_the_count_is_capped(self):
        with pytest.raises(ValidationError) as e:
            ModelsConfigRequest(
                selected_models=["lightgbm"],
                hyperparameters={"lightgbm": {f"p{i}": 1 for i in range(200)}})
        assert _err(e) == "too_many_hyperparameters"

    def test_a_normal_block_still_works(self):
        """The whole point is to keep tuning possible, not to forbid it."""
        cfg = ModelsConfigRequest(
            selected_models=["lightgbm"],
            hyperparameters={"lightgbm": {"n_estimators": 300, "learning_rate": 0.05,
                                          "verbose": -1, "objective": "regression"}})
        assert cfg.hyperparameters["lightgbm"]["n_estimators"] == 300

    def test_none_is_a_legal_value(self):
        """Several libraries take None to mean "use the default"."""
        cfg = ModelsConfigRequest(selected_models=["lightgbm"],
                                  hyperparameters={"lightgbm": {"max_depth": None}})
        assert cfg.hyperparameters["lightgbm"]["max_depth"] is None

    def test_library_ranges_are_left_to_the_library(self):
        """Deliberate: a value LightGBM rejects is LightGBM's to reject. Inventing
        the bound here would reject values the product may later want."""
        cfg = ModelsConfigRequest(selected_models=["lightgbm"],
                                  hyperparameters={"lightgbm": {"n_estimators": -5}})
        assert cfg.hyperparameters["lightgbm"]["n_estimators"] == -5
