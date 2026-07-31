"""
GlobalTrainer — ONE model fitted across every series at once (cross-learning).

Why this exists
---------------
The per-SKU Trainer fits an independent model on each series. For a distributor
with three thousand SKUs and fourteen months of history that is three thousand
models, most of them fitted on a few dozen points, each blind to the fact that
the SKU next to it is the same product in a different size and peaks in the same
week. Short series get routed to `naive` and a brand-new SKU gets nothing at all.

A global model turns the catalogue into ONE training set. A SKU with two months
of history borrows the weekly shape, the holiday response and the seasonality of
every SKU that has years of it. Two things make that work rather than merely
average everything together:

  * per-series scaling — the target and every feature denominated in units are
    divided by the series' own level, so a SKU selling 5/day and one selling
    5000/day become the same shape and the model learns shape, not size;
  * series identity as a categorical feature, plus the series' level and
    variability, so the model can still specialise where it has the evidence.

Direct multi-horizon
--------------------
This trainer does NOT forecast recursively. Recursion feeds a prediction back in
as if it were an observation, so step 14 is built on thirteen guesses and the
error compounds — and the intervals, derived from one-step residuals, do not
widen to match. Here the horizon `h` is a FEATURE: each training row pairs the
features known at an origin with the actual value h buckets later, so a 14-step
forecast is a direct estimate of what happens in 14 buckets, and every horizon
carries its own honestly-measured error distribution.

Validation is a rolling-origin backtest: at each fold cutoff the model is fitted
only on targets that had already occurred, then asked to forecast forward from
the last observation — exactly the shape of the job it does in production. The
per-horizon residuals that fall out are what the conformal intervals are built
from (see evaluation/conformal.py).

Example:
    trainer = GlobalTrainer(horizon=14, train_ratio=0.8, wfv_splits=3)
    results = trainer.train(df_ml, group_cols=["sku"], target="demand", dt="date")
    # results["global_lgbm_SKU_1│Tienda única"]["direct_forecaster"].point(14)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from forecasting_core.data.canonical import (
    DEFAULT_STORE, SERIES_SEPARATOR, series_key,
)
from forecasting_core.evaluation.metrics import evaluate_all
from forecasting_core.training.trainer import WalkForwardSplitter

log = logging.getLogger(__name__)

MODEL_NAME = "global_lgbm"

# Features denominated in the target's units. They are divided by the series'
# scale alongside the target so the model sees one comparable problem instead of
# one problem per order of magnitude. Ratios (cv_, pct_change_) and calendar
# terms are already scale-free and must NOT be touched.
_SCALED_PREFIXES = ("lag_", "roll_mean_", "roll_min_", "roll_max_", "roll_std_",
                    "ewm_", "diff_")

# Columns the trainer adds. Named with a reserved prefix so a user column can
# never collide with one.
SERIES_CODE_PREFIX = "series_code_"
SERIES_LEVEL = "series_log_level"
SERIES_CV = "series_cv"
HORIZON_COL = "horizon"

# Ceiling on the stacked (origin x horizon) training matrix. Beyond this the
# origins are thinned by a stride rather than the horizons: dropping horizons
# would leave whole steps of the forecast with no training signal, while
# dropping origins only costs sample size.
MAX_STACKED_ROWS = 2_000_000

DEFAULT_PARAMS: Dict[str, Any] = {
    "n_estimators": 400,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 20,
    "subsample": 0.9,
    "subsample_freq": 1,
    "colsample_bytree": 0.9,
}


def scaled_feature_columns(columns: Sequence[str]) -> List[str]:
    """Feature columns that live in the target's units."""
    return [c for c in columns if str(c).startswith(_SCALED_PREFIXES)]


@dataclass
class SeriesProfile:
    """Everything the model needs to know about one series, fitted leak-free."""
    key: str
    sku: str
    store: str
    codes: Tuple[int, ...]
    scale: float
    cv: float
    n_rows: int

    @property
    def log_level(self) -> float:
        return float(np.log1p(max(self.scale, 0.0)))


@dataclass
class GlobalDirectForecaster:
    """
    The global model, presented as if it were one series' own forecaster.

    Holds the origin row — the features at the last observation of this series —
    so producing the forecast needs no further access to the dataset. Point
    forecasts come from the model; the intervals come from the conformal bands
    fitted on the rolling-origin backtest (residuals_by_horizon), which is why
    they widen with the horizon instead of being one number reused H times.
    """
    model: Any
    profile: SeriesProfile
    model_features: List[str]
    scaled_features: List[str]
    origin_row: Dict[str, float]
    # How many buckets old the origin is. A feature row's newest input is the
    # PREVIOUS observation, so the freshest row available conditions on the
    # second-to-last bucket, not the last one — the first future bucket is
    # therefore `origin_lag + 1` steps from the origin, not 1. Getting this
    # wrong shifts the entire forecast one bucket into the past, which is
    # invisible in aggregate error and wrong on every single date.
    origin_lag: int = 1
    # {horizon: array of SCALED residuals} pooled across the whole catalogue.
    residuals_by_horizon: Dict[int, np.ndarray] = field(default_factory=dict)
    # {horizon: array of SCALED residuals of the CUMULATIVE demand to that
    # horizon} — what a safety stock actually needs, see business/inventory.
    cumulative_residuals_by_horizon: Dict[int, np.ndarray] = field(default_factory=dict)

    def _design(self, horizons: Sequence[int]) -> pd.DataFrame:
        scale = self.profile.scale
        row = {k: float(v) for k, v in self.origin_row.items()}
        for col in self.scaled_features:
            if col in row:
                row[col] = row[col] / scale
        base = {col: row.get(col, 0.0) for col in self.model_features}
        for i, code in enumerate(self.profile.codes):
            base[f"{SERIES_CODE_PREFIX}{i}"] = code
        base[SERIES_LEVEL] = self.profile.log_level
        base[SERIES_CV] = self.profile.cv
        frame = pd.DataFrame([base for _ in horizons])
        frame[HORIZON_COL] = list(horizons)
        return frame[self.model_features]

    def point(self, horizon: int) -> np.ndarray:
        """
        Point forecast for future steps 1..horizon, in the series' own units.

        Future step k is `origin_lag + k` buckets from the origin, so the model
        is asked for those horizons and the answer is re-keyed to step space.
        The trainer fits one horizon beyond the requested one precisely so the
        last step does not fall outside the range the model was trained on.
        """
        steps = int(horizon)
        if steps < 1:
            return np.array([])
        horizons = [self.origin_lag + k for k in range(1, steps + 1)]
        preds = np.asarray(self.model.predict(self._design(horizons)), dtype=float)
        return np.clip(preds * self.profile.scale, 0.0, None)


class GlobalTrainer:
    """Fits one cross-learning model over every series in the dataset."""

    def __init__(
        self,
        horizon: int = 14,
        train_ratio: float = 0.8,
        walk_forward: bool = True,
        wfv_splits: int = 3,
        max_workers: Optional[int] = None,
        params: Optional[dict] = None,
    ):
        self.horizon = max(1, int(horizon))
        # One horizon beyond what is asked for. The production origin is a
        # bucket stale (see GlobalDirectForecaster.origin_lag), so the last
        # future step needs horizon+1 — and asking a model to extrapolate its
        # own horizon feature past the range it was trained on is exactly the
        # kind of quiet nonsense that produces a confident wrong number.
        self.train_horizon = self.horizon + 1
        self.train_ratio = train_ratio
        self.walk_forward = walk_forward
        self.wfv_splits = wfv_splits
        self.max_workers = max_workers
        self.params = {**DEFAULT_PARAMS, **(params or {})}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def train(
        self,
        df: pd.DataFrame,
        group_cols: Optional[List[str]] = None,
        target: str = "",
        dt: str = "",
    ) -> Dict[str, dict]:
        """
        Fit the global model and return ONE result entry per series.

        The entries have the same shape the per-SKU Trainer produces, so every
        consumer downstream — the metrics table, the ensemble, the champion
        selection, the inventory advisor — treats the global model as just
        another competitor without knowing anything about it.
        """
        prepared = self._prepare(df, group_cols or [], target, dt)
        if prepared is None:
            return {}

        stacked, profiles, feature_cols, model_features = prepared

        folds = self._fold_cutoffs(stacked)
        backtest = self._backtest(stacked, model_features, folds) if folds else None

        final_cut = self._quantile_date(stacked["_target_date"], self.train_ratio)
        train_rows = stacked[stacked["_target_date"] <= final_cut]
        if len(train_rows) < 10:
            train_rows = stacked
        model = self._fit(train_rows, model_features)
        if model is None:
            return {}

        return self._build_entries(
            model=model,
            stacked=stacked,
            profiles=profiles,
            feature_cols=feature_cols,
            model_features=model_features,
            backtest=backtest,
            n_folds=len(folds),
        )

    # ------------------------------------------------------------------
    # Preparation
    # ------------------------------------------------------------------

    def _prepare(self, df, group_cols, target, dt):
        if df is None or df.empty or target not in df.columns or dt not in df.columns:
            return None

        group_cols = [c for c in group_cols if c in df.columns]
        base = df.copy()
        base[dt] = pd.to_datetime(base[dt])

        if group_cols:
            base = base.sort_values(group_cols + [dt]).reset_index(drop=True)
            series_values = base[group_cols].astype(str)
            keys = series_values.agg(SERIES_SEPARATOR.join, axis=1)
        else:
            base = base.sort_values(dt).reset_index(drop=True)
            series_values = pd.DataFrame({"__all__": ["__all__"] * len(base)})
            keys = pd.Series(["__all__"] * len(base))

        # Feature columns: numeric, not the target/date/group identifiers.
        reserved = {dt, target, *group_cols}
        feature_cols = [
            c for c in base.columns
            if c not in reserved and pd.api.types.is_numeric_dtype(base[c])
        ]
        # A feature identical to the target is the target (the canonical
        # 'demand' alias); the per-SKU Trainer drops those too.
        y_full = base[target].astype(float)
        feature_cols = [
            c for c in feature_cols
            if not base[c].astype(float).equals(y_full)
        ]
        if not feature_cols:
            log.warning("GlobalTrainer: no usable feature columns — skipping")
            return None

        codes_frame = pd.DataFrame(index=base.index)
        for i, col in enumerate(group_cols or ["__all__"]):
            values = series_values[col] if col in series_values else series_values.iloc[:, 0]
            codes_frame[f"{SERIES_CODE_PREFIX}{i}"] = (
                values.astype("category").cat.codes.astype(int)
            )
        code_cols = list(codes_frame.columns)

        grouped = base.groupby(keys, sort=False)
        info_date = grouped[dt].shift(1)

        profiles = self._fit_profiles(
            base, keys, codes_frame, target, dt, group_cols,
        )
        if not profiles:
            return None

        scale_per_row = keys.map({k: p.scale for k, p in profiles.items()}).astype(float)
        level_per_row = keys.map({k: p.log_level for k, p in profiles.items()}).astype(float)
        cv_per_row = keys.map({k: p.cv for k, p in profiles.items()}).astype(float)

        scaled_cols = scaled_feature_columns(feature_cols)
        design = base[feature_cols].astype(float).copy()
        for col in scaled_cols:
            design[col] = design[col] / scale_per_row
        for col in code_cols:
            design[col] = codes_frame[col]
        design[SERIES_LEVEL] = level_per_row
        design[SERIES_CV] = cv_per_row

        stacked = self._stack_horizons(
            design=design, base=base, keys=keys, target=target, dt=dt,
            info_date=info_date, scale_per_row=scale_per_row,
        )
        if stacked is None or stacked.empty:
            log.warning("GlobalTrainer: no (origin, horizon) pairs could be built")
            return None

        model_features = list(design.columns) + [HORIZON_COL]
        return stacked, profiles, feature_cols, model_features

    def _fit_profiles(self, base, keys, codes_frame, target, dt, group_cols):
        """
        Per-series scale and variability, measured on data that predates every
        fold. Computing them over the whole series would leak the validation
        window's level into the features the validation window is scored on.
        """
        cutoff = self._first_fold_cutoff(base[dt])
        window = base[base[dt] <= cutoff]
        if window.empty:
            window = base

        profiles: Dict[str, SeriesProfile] = {}
        window_keys = keys.loc[window.index]
        stats = window.groupby(window_keys, sort=False)[target].agg(["mean", "std", "count"])
        overall = base.groupby(keys, sort=False)[target].agg(["mean", "count"])

        # One pass for every series' first row. Scanning `base.index[keys == k]`
        # per series is O(rows x series) and is the difference between seconds
        # and minutes on a real catalogue.
        first_index: Dict[str, Any] = {}
        for pos, k in enumerate(keys.to_numpy()):
            if k not in first_index:
                first_index[k] = base.index[pos]

        for key in overall.index:
            if key in stats.index and stats.loc[key, "count"] >= 2:
                mean = float(stats.loc[key, "mean"])
                std = float(stats.loc[key, "std"] or 0.0)
            else:
                # Series that starts after the scaling window (a new SKU) has no
                # leak-free history — fall back to its own mean. Better a scale
                # from later data than a scale of zero, which would send every
                # feature to infinity.
                mean = float(overall.loc[key, "mean"])
                std = 0.0
            scale = float(max(abs(mean), 1e-3))
            cv = float(std / scale) if scale > 0 else 0.0

            first = first_index.get(key)
            codes = tuple(
                int(codes_frame.loc[first, c]) for c in codes_frame.columns
            ) if first is not None else tuple()
            sku, store = self._split_key(base, first, group_cols)
            profiles[key] = SeriesProfile(
                key=key, sku=sku, store=store, codes=codes,
                scale=scale, cv=cv, n_rows=int(overall.loc[key, "count"]),
            )
        return profiles

    @staticmethod
    def _split_key(base, idx, group_cols) -> Tuple[str, str]:
        """(sku, store) exactly as the per-SKU Trainer derives them."""
        if idx is None or not group_cols:
            return "__all__", DEFAULT_STORE
        sku = str(base.loc[idx, group_cols[0]])
        store = str(base.loc[idx, group_cols[1]]) if len(group_cols) > 1 else DEFAULT_STORE
        return sku, store

    def _stack_horizons(self, design, base, keys, target, dt, info_date, scale_per_row):
        """
        Build the (origin, horizon) training matrix.

        Row i of `design` carries features whose newest input is the observation
        at `info_date[i]`. Pairing it with the target h-1 rows further on gives a
        row that says: "from what was known at info_date, the value h buckets
        later was y". That pairing IS the direct multi-horizon formulation.
        """
        grouped_y = base.groupby(keys, sort=False)[target]
        grouped_dt = base.groupby(keys, sort=False)[dt]

        n_origins = len(design)
        stride = 1
        if n_origins * self.train_horizon > MAX_STACKED_ROWS:
            stride = int(np.ceil(n_origins * self.train_horizon / MAX_STACKED_ROWS))
            log.info(
                "GlobalTrainer: %d origins x %d horizons exceeds the %d-row cap — "
                "thinning origins by a stride of %d (horizons kept intact)",
                n_origins, self.train_horizon, MAX_STACKED_ROWS, stride,
            )

        keep = np.zeros(n_origins, dtype=bool)
        keep[::stride] = True

        frames = []
        for h in range(1, self.train_horizon + 1):
            y_h = grouped_y.shift(-(h - 1))
            d_h = grouped_dt.shift(-(h - 1))
            valid = keep & y_h.notna().to_numpy() & info_date.notna().to_numpy()
            if not valid.any():
                continue
            part = design.loc[valid].copy()
            part[HORIZON_COL] = h
            part["_y"] = (y_h[valid].astype(float) / scale_per_row[valid]).to_numpy()
            part["_y_units"] = y_h[valid].astype(float).to_numpy()
            part["_scale"] = scale_per_row[valid].to_numpy()
            part["_series"] = keys[valid].to_numpy()
            part["_info_date"] = info_date[valid].to_numpy()
            part["_target_date"] = d_h[valid].to_numpy()
            frames.append(part)

        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)

    # ------------------------------------------------------------------
    # Folds and backtest
    # ------------------------------------------------------------------

    @staticmethod
    def _quantile_date(dates: pd.Series, ratio: float) -> pd.Timestamp:
        unique = np.sort(pd.to_datetime(pd.Series(dates)).dropna().unique())
        if len(unique) == 0:
            return pd.Timestamp.min
        idx = min(len(unique) - 1, max(0, int(len(unique) * ratio) - 1))
        return pd.Timestamp(unique[idx])

    def _first_fold_cutoff(self, dates: pd.Series) -> pd.Timestamp:
        """Where the earliest training window ends — the leak-free boundary."""
        unique = np.sort(pd.to_datetime(pd.Series(dates)).dropna().unique())
        if len(unique) == 0:
            return pd.Timestamp.min
        splits = WalkForwardSplitter(self.wfv_splits, self.train_ratio / 2).split(len(unique))
        if not splits:
            return self._quantile_date(pd.Series(unique), self.train_ratio)
        return pd.Timestamp(unique[splits[0][0][-1]])

    def _fold_cutoffs(self, stacked: pd.DataFrame) -> List[pd.Timestamp]:
        """
        Cutoff dates for the rolling-origin backtest.

        Folds run over the DATE axis, not over row positions: with thousands of
        series of different lengths concatenated together, a row index means
        nothing, and splitting on it would put one SKU's future in another SKU's
        past.
        """
        if not self.walk_forward:
            return [self._quantile_date(stacked["_target_date"], self.train_ratio)]
        unique = np.sort(pd.to_datetime(stacked["_info_date"]).dropna().unique())
        if len(unique) < 4:
            return []
        splits = WalkForwardSplitter(self.wfv_splits, self.train_ratio / 2).split(len(unique))
        return [pd.Timestamp(unique[tr[-1]]) for tr, _te in splits]

    def _backtest(self, stacked, model_features, cutoffs) -> Optional[dict]:
        """
        Rolling-origin backtest: fit on the past, forecast forward, score.

        Returns per-series errors and the pooled per-horizon residual bank the
        conformal intervals are calibrated from. Residuals are pooled in SCALED
        units on purpose — that is what lets a SKU with eight weeks of history
        inherit a calibrated interval from the whole catalogue instead of
        estimating its own from nothing.
        """
        per_series: Dict[str, List[dict]] = {}
        residuals_by_h: Dict[int, List[float]] = {}
        cumulative_by_h: Dict[int, List[float]] = {}
        origin_records: List[pd.DataFrame] = []

        for cutoff in cutoffs:
            train_rows = stacked[stacked["_target_date"] <= cutoff]
            if len(train_rows) < 10:
                continue
            model = self._fit(train_rows, model_features)
            if model is None:
                continue

            eligible = stacked[stacked["_info_date"] <= cutoff]
            if eligible.empty:
                continue
            latest = eligible.groupby("_series")["_info_date"].transform("max")
            evaluation = eligible[eligible["_info_date"] == latest]
            evaluation = evaluation[evaluation["_target_date"] > cutoff]
            # The backtest origin conditions on data through the cutoff itself,
            # so its horizon h already IS future step h — unlike the production
            # origin. Keep the residual bank in step space by dropping the extra
            # horizon that only exists to serve the stale production origin.
            evaluation = evaluation[evaluation[HORIZON_COL] <= self.horizon]
            if evaluation.empty:
                continue

            predicted = np.asarray(model.predict(evaluation[model_features]), dtype=float)
            frame = evaluation[["_series", HORIZON_COL, "_y", "_y_units", "_scale"]].copy()
            frame["_pred"] = predicted
            frame["_pred_units"] = np.clip(predicted * frame["_scale"].to_numpy(), 0.0, None)
            origin_records.append(frame)

            for h, chunk in frame.groupby(HORIZON_COL):
                residuals_by_h.setdefault(int(h), []).extend(
                    (chunk["_y"] - chunk["_pred"]).tolist()
                )
            for series, chunk in frame.groupby("_series"):
                chunk = chunk.sort_values(HORIZON_COL)
                per_series.setdefault(str(series), []).append({
                    "actual": chunk["_y_units"].to_numpy(),
                    "pred": chunk["_pred_units"].to_numpy(),
                    "horizons": chunk[HORIZON_COL].to_numpy(),
                })
                # Cumulative error: the quantity a reorder point is exposed to
                # is the SUM of demand over the lead time, not any single day.
                cum_actual = np.cumsum(chunk["_y"].to_numpy())
                cum_pred = np.cumsum(chunk["_pred"].to_numpy())
                for i, h in enumerate(chunk[HORIZON_COL].to_numpy()):
                    cumulative_by_h.setdefault(int(h), []).append(
                        float(cum_actual[i] - cum_pred[i])
                    )

        if not per_series:
            return None
        return {
            "per_series": per_series,
            "residuals_by_horizon": {h: np.array(v) for h, v in residuals_by_h.items()},
            "cumulative_by_horizon": {h: np.array(v) for h, v in cumulative_by_h.items()},
            "records": pd.concat(origin_records, ignore_index=True) if origin_records else None,
        }

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def _fit(self, rows: pd.DataFrame, model_features: List[str]):
        try:
            from lightgbm import LGBMRegressor
        except ImportError:
            log.warning("GlobalTrainer: lightgbm not installed — global model skipped")
            return None

        categorical = [c for c in model_features if c.startswith(SERIES_CODE_PREFIX)]
        n_jobs = self.max_workers if self.max_workers else -1
        model = LGBMRegressor(**{**self.params, "n_jobs": n_jobs, "verbosity": -1})
        try:
            model.fit(
                rows[model_features], rows["_y"].astype(float),
                categorical_feature=categorical or "auto",
            )
        except Exception as exc:
            log.warning(f"GlobalTrainer: fit failed ({exc})")
            return None
        return model

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    def _build_entries(self, model, stacked, profiles, feature_cols,
                       model_features, backtest, n_folds) -> Dict[str, dict]:
        from forecasting_core.explainability import compute_shap

        scaled_cols = scaled_feature_columns(feature_cols)
        origin_rows = self._origin_rows(stacked, feature_cols)
        origin_lags = self._origin_lags(stacked)

        # One model means one importance profile — the honest thing to report is
        # the same list for every series, not a fabricated per-SKU variation.
        sample = stacked[model_features].tail(2000)
        shap_importance = compute_shap(model, sample) if len(sample) else []

        residuals_by_h = (backtest or {}).get("residuals_by_horizon", {})
        cumulative_by_h = (backtest or {}).get("cumulative_by_horizon", {})
        per_series = (backtest or {}).get("per_series", {})

        entries: Dict[str, dict] = {}
        for key, profile in profiles.items():
            origin = origin_rows.get(key)
            if origin is None:
                continue

            forecaster = GlobalDirectForecaster(
                model=model,
                profile=profile,
                model_features=model_features,
                scaled_features=scaled_cols,
                origin_row=origin,
                origin_lag=origin_lags.get(key, 1),
                residuals_by_horizon=residuals_by_h,
                cumulative_residuals_by_horizon=cumulative_by_h,
            )

            metrics, horizon_metrics, residuals = self._series_metrics(
                per_series.get(key, []), profile,
            )

            entries[f"{MODEL_NAME}_{series_key(profile.sku, profile.store)}"] = {
                **metrics,
                "sku": profile.sku,
                "store": profile.store,
                "model": MODEL_NAME,
                "n": profile.n_rows,
                "n_folds": n_folds,
                "validation": "rolling_origin",
                # The adapter is not an sklearn model and must not be handed to
                # recursive_ml_predict — `forecast_strategy` is what tells the
                # inference layer to take the direct path instead.
                "forecast_strategy": "direct",
                "direct_forecaster": forecaster,
                "fitted_model": None,
                "feature_names": feature_cols,
                "residuals": residuals,
                "horizon_metrics": horizon_metrics,
                # Origin-by-origin predictions against what actually happened.
                # The policy backtest replays the purchasing decision over these,
                # which is the only way to score the DECISION rather than the
                # forecast (see business/policy_backtest.py).
                "backtest_records": per_series.get(key, []),
                "tuned_params": None,
                "shap_importance": shap_importance,
            }
        return entries

    @staticmethod
    def _origin_lags(stacked: pd.DataFrame) -> Dict[str, int]:
        """
        How many observations each series has AFTER its newest origin.

        Feature rows condition on the previous observation, so the freshest
        origin is one bucket behind the series' last data point. That distance
        is what future step 1 has to be offset by; assuming it is zero silently
        publishes yesterday's forecast as tomorrow's.
        """
        if stacked.empty:
            return {}
        step_one = stacked[stacked[HORIZON_COL] == 1]
        if step_one.empty:
            return {}
        newest = step_one.groupby("_series")["_info_date"].transform("max")
        origin_date = step_one.assign(_origin=newest).groupby("_series")["_origin"].first()
        lags: Dict[str, int] = {}
        for series, group in step_one.groupby("_series"):
            after = group["_target_date"] > origin_date.loc[series]
            lags[str(series)] = max(1, int(after.sum()))
        return lags

    @staticmethod
    def _origin_rows(stacked: pd.DataFrame, feature_cols: List[str]) -> Dict[str, dict]:
        """
        Each series' newest origin, in raw units, keyed by series.

        This is the row a production forecast starts from. It is captured here
        so the forecaster is self-contained and inference never has to reopen
        the feature frame.
        """
        if stacked.empty:
            return {}
        newest = stacked.groupby("_series")["_info_date"].transform("max")
        latest = stacked[stacked["_info_date"] == newest]
        latest = latest.drop_duplicates(subset=["_series"], keep="first")
        out: Dict[str, dict] = {}
        for _, row in latest.iterrows():
            scale = float(row["_scale"])
            # Back to raw units: the forecaster re-scales on its own so it can
            # be handed a raw origin from anywhere.
            values = {}
            for col in feature_cols:
                if col not in row:
                    continue
                value = float(row[col])
                values[col] = value * scale if str(col).startswith(_SCALED_PREFIXES) else value
            out[str(row["_series"])] = values
        return out

    @staticmethod
    def _series_metrics(records: List[dict], profile: SeriesProfile):
        """
        Per-series accuracy from the rolling-origin backtest.

        `mae`/`rmse`/… are reported at horizon 1 so they sit in the same table
        as the per-SKU models' one-step scores without comparing two different
        questions. The full 1..H picture is reported separately under
        `horizon_metrics` — it is the number that matches what the user
        actually receives, and it is always the worse of the two.
        """
        if not records:
            empty = {k: float("nan") for k in
                     ("mae", "rmse", "wape", "bias", "mape", "smape")}
            return empty, {}, np.array([])

        first_actual, first_pred = [], []
        all_actual, all_pred = [], []
        by_h: Dict[int, List[Tuple[float, float]]] = {}
        for rec in records:
            actual, pred, horizons = rec["actual"], rec["pred"], rec["horizons"]
            all_actual.extend(actual.tolist())
            all_pred.extend(pred.tolist())
            for a, p, h in zip(actual, pred, horizons):
                by_h.setdefault(int(h), []).append((float(a), float(p)))
                if int(h) == 1:
                    first_actual.append(float(a))
                    first_pred.append(float(p))

        step1 = (evaluate_all(first_actual, first_pred) if first_actual
                 else evaluate_all(all_actual, all_pred))
        horizon_metrics = {
            "all_horizons": evaluate_all(all_actual, all_pred),
            "by_horizon": {
                str(h): evaluate_all([a for a, _ in pairs], [p for _, p in pairs])
                for h, pairs in sorted(by_h.items())
            },
        }
        residuals = np.array(all_actual) - np.array(all_pred)
        return step1, horizon_metrics, residuals
