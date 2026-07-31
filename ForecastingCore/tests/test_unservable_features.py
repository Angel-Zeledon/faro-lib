"""
A model may only use inputs that exist on both sides of the forecast boundary.

Engineered features qualify: the predictor computes calendar terms, Fourier
terms, lags and rolling statistics for future dates too. Columns that merely
rode along with the upload do not. `recursive_ml_predict` builds each future row
from the feature NAMES and fills whatever it cannot compute with
`row.get(f, 0.0)`, so `inventory`, `price`, `cost` and the censoring flag are
real numbers while fitting and hard zeros at serve time.

Measured before this was removed: on a series whose demand is genuinely
truncated by stock, `inventory` came back with the HIGHEST feature importance of
all — above the lag — while moving the served forecast by only about 2%, since a
tree cannot extrapolate below its lowest split. The forecast harm is mild. The
harm that is not mild is that the same column tops the SHAP list the product
shows the user as the explanation for a number it never influenced.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import FeaturesConfig, SessionConfig
from forecasting_core.data.censoring import CENSORED_FLAG
from forecasting_core.features.engineer import FeatureEngineer
from forecasting_core.pipelines.pipeline import Pipeline


def _raw(n=140):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="D"),
        "sku": "A",
        "demand": np.full(n, 50.0),
        "inventory": np.linspace(400, 200, n),
        "price": np.full(n, 9.99),
        CENSORED_FLAG: np.zeros(n, dtype=int),
    })


def _engineered(raw):
    cfg = FeaturesConfig(lags=[1, 7], rolling=[7], diffs=[1], calendar=True)
    eng = FeatureEngineer(cfg, dt_col="date", target="demand", group_cols=["sku"])
    return eng.transform(raw)


def _columns():
    cfg = SessionConfig.from_dict({
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"],
                    "inventory": "inventory"},
        "models": {"lightgbm": {}},
    })
    return cfg.columns


class TestWhatGetsDropped:

    def test_upload_columns_the_forecast_cannot_supply_are_removed(self):
        raw = _raw()
        engineered = _engineered(raw)
        out, reported = Pipeline._drop_unservable_features(engineered, raw, _columns())
        # `inventory` varies, so it is both removed and worth naming. `price`
        # and the censoring flag are constant here: removed, not named.
        for col in ("inventory", "price", CENSORED_FLAG):
            assert col not in out.columns
        assert "inventory" in reported

    def test_engineered_features_all_survive(self):
        raw = _raw()
        engineered = _engineered(raw)
        out, _ = Pipeline._drop_unservable_features(engineered, raw, _columns())
        for col in ("lag_1", "lag_7", "roll_mean_7", "dow", "is_holiday", "diff_1"):
            assert col in out.columns, f"{col} is reproducible at serve time and must stay"

    def test_target_date_and_group_are_untouched(self):
        raw = _raw()
        out, dropped = Pipeline._drop_unservable_features(_engineered(raw), raw, _columns())
        for col in ("demand", "date", "sku"):
            assert col in out.columns
            assert col not in dropped

    def test_a_frame_with_nothing_to_drop_is_returned_unchanged(self):
        raw = _raw()[["date", "sku", "demand"]]
        engineered = _engineered(raw)
        out, dropped = Pipeline._drop_unservable_features(engineered, raw, _columns())
        assert dropped == []
        assert out is engineered

    def test_non_numeric_passthrough_is_left_alone(self):
        """Those are dropped later by the Trainer with its own message."""
        raw = _raw()
        raw["categoria"] = "bebidas"
        out, dropped = Pipeline._drop_unservable_features(_engineered(raw), raw, _columns())
        assert "categoria" not in dropped


class TestWhatTheUserIsTold:
    """
    Removing a column and telling someone about it are different decisions.

    The first version of this warning fired on every session and named
    `inventory`, `lead_time`, `promo` and `discount` — columns the canonical
    schema broadcasts as constants into files that never contained them.
    Reading "we dropped your inventory column" when you never uploaded one is
    worse than saying nothing.
    """

    def test_broadcast_constants_are_dropped_but_not_named(self):
        raw = _raw()
        raw["lead_time"] = 15          # canonical default, same value everywhere
        raw["discount"] = 0.0
        out, reported = Pipeline._drop_unservable_features(
            _engineered(raw), raw, _columns())
        for col in ("lead_time", "discount"):
            assert col not in out.columns, f"{col} must still be removed"
            assert col not in reported, f"{col} was never in the user's file"

    def test_a_column_that_actually_varies_is_named(self):
        raw = _raw()
        out, reported = Pipeline._drop_unservable_features(
            _engineered(raw), raw, _columns())
        assert "inventory" in reported, (
            "a column with real values in the file is worth mentioning"
        )

    def test_the_target_alias_is_dropped_silently(self):
        """
        `apply_canonical_defaults` adds `demand` as a copy of the mapped target.
        It is the leakage alias, not a feature the user lost.
        """
        raw = _raw()
        raw["demand_alias"] = raw["demand"]
        out, reported = Pipeline._drop_unservable_features(
            _engineered(raw), raw, _columns())
        assert "demand_alias" not in out.columns
        assert "demand_alias" not in reported

    def test_the_target_alias_is_silent_even_after_it_stops_matching(self):
        """
        Measured on a real session: the warning reached the user reading
        "Dropped column(s) that exist in the history but cannot be known for a
        future date: demand." Nobody uploaded a column called `demand` — the
        user mapped `cantidad`, and `apply_canonical_defaults` copied it.

        The copy is taken BEFORE the backend collapses same-day rows, and the
        collapse aggregates the target only. Two rows for one day therefore left
        `demand` holding one of them and `cantidad` holding their sum: the alias
        now varies AND differs from the target, so both of the earlier guards
        pass it through. The pipeline was reporting its own bookkeeping to the
        user as if they had lost a column.
        """
        n = 140
        raw = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=n, freq="D"),
            "sku": "A",
            "cantidad": np.linspace(40.0, 60.0, n),   # the mapped target, collapsed
            "demand": np.linspace(20.0, 30.0, n),     # the pre-collapse copy
        })
        cfg = FeaturesConfig(lags=[1, 7], rolling=[7], diffs=[1], calendar=True)
        engineered = FeatureEngineer(
            cfg, dt_col="date", target="cantidad", group_cols=["sku"]).transform(raw)
        columns = SessionConfig.from_dict({
            "columns": {"target": "cantidad", "date": "date", "group_keys": ["sku"]},
            "models": {"lightgbm": {}},
        }).columns

        out, reported = Pipeline._drop_unservable_features(engineered, raw, columns)

        assert not engineered["demand"].equals(engineered["cantidad"]), (
            "fixture must reproduce the divergence, or it proves nothing"
        )
        assert "demand" not in out.columns, "the alias must still be dropped"
        assert "demand" not in reported, (
            f"the target's canonical alias was named to the user: {reported}"
        )

    def test_a_column_genuinely_called_demand_by_the_user_is_still_named(self):
        """The name-based skip must not silence a real, mapped `demand` column."""
        raw = _raw()
        raw["inventory"] = np.linspace(400, 200, len(raw))
        out, reported = Pipeline._drop_unservable_features(
            _engineered(raw), raw, _columns())
        assert "demand" not in reported, "it is the target here, never a feature"
        assert "inventory" in reported

    def test_a_clean_file_produces_no_warning_at_all(self):
        raw = _raw()[["date", "sku", "demand"]]
        _out, reported = Pipeline._drop_unservable_features(
            _engineered(raw), raw, _columns())
        assert reported == []


class TestTheReasonItMatters:

    def test_the_dropped_columns_are_exactly_the_zero_filled_ones(self):
        """
        The inference path is the authority on what is servable: it builds a
        future row from the feature names and defaults anything it cannot
        compute. Whatever it defaults is what must not be a feature.
        """
        from forecasting_core.inference.predictor import recursive_ml_predict

        raw = _raw()
        engineered = _engineered(raw)
        kept, dropped = Pipeline._drop_unservable_features(engineered, raw, _columns())
        feature_names = [c for c in kept.columns if c not in ("date", "sku", "demand")]

        class _Echo:
            """Returns the row it was given, so we can inspect what arrived."""
            def __init__(self): self.seen = []
            def predict(self, X):
                self.seen.append(X.iloc[0].to_dict())
                return np.array([50.0])

        echo = _Echo()
        cfg = FeaturesConfig(lags=[1, 7], rolling=[7], diffs=[1], calendar=True)
        recursive_ml_predict(
            fitted_model=echo, feature_names=feature_names,
            residuals=np.array([1.0, 2.0]), history=list(np.full(30, 50.0)),
            features_cfg=cfg, horizon=3,
            future_dates=list(pd.date_range("2026-06-01", periods=3, freq="D")),
        )
        served = echo.seen[0]
        # Nothing that survived the drop is a constant zero placeholder.
        for col in dropped:
            assert col not in served, f"{col} should not have been asked for at all"
        assert served["lag_1"] != 0.0, "a real lag must reach the model"

    def test_shap_can_no_longer_credit_an_unused_column(self):
        """The user-facing consequence: the explanation names real inputs."""
        raw = _raw()
        kept, dropped = Pipeline._drop_unservable_features(_engineered(raw), raw, _columns())
        explainable = set(kept.columns)
        assert not (set(dropped) & explainable)
