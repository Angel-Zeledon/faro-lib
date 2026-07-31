"""
Trainer — trains ML models per SKU using walk-forward validation.

Walk-forward validation (expanding window):
  - Prevents data leakage in time series
  - Produces more reliable out-of-sample estimates than a single split
  - Averages metrics across N folds

Two scores come out of this, and they answer different questions. The fold
metrics are 1-step scores: the validation rows carry their own true lag
features, so the model is asked "what happens next?" once per row, always with
yesterday's real demand in hand. `horizon_metrics` is the h-step score — the
production inference path run forward from the final cutoff, where step 2 is
built on step 1's guess. Only the second is comparable with the statistical
models and the global model, which were never scored any other way.

Example:
    trainer = Trainer(train_ratio=0.8, walk_forward=True, wfv_splits=3,
                      horizon=14, features_cfg=cfg.features)
    results = trainer.train(df_ml, models, group_cols=["sku"], target="sales", dt="date")
"""

import copy
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

from forecasting_core.data.canonical import DEFAULT_STORE, series_key
from forecasting_core.evaluation.metrics import evaluate_all

log = logging.getLogger(__name__)


class WalkForwardSplitter:
    """
    Expanding window cross-validation for time series.

    `gap` withholds the last `gap` observations before each test window from
    training. Without it the model is fitted on data ending the day before the
    period it is scored on, while in production the freshest observation it can
    possibly have is `horizon` buckets old. Adjacent train data is the most
    informative data there is for an autocorrelated series, so a gap-less score
    measures an easier problem than the one the product actually solves — it
    reads as accuracy the user will never see.

    The gap does NOT, on its own, make the score a true h-step-ahead score: the
    test rows still carry their own true lag features, so each row is still
    graded as a 1-step problem. The fold metrics that come out of here are
    therefore 1-step metrics and are reported as such; the h-step-ahead number
    that is comparable with the statistical and global models is produced
    separately by `Trainer._horizon_metrics`.
    """

    def __init__(self, n_splits: int = 3, min_train_ratio: float = 0.5, gap: int = 0):
        self.n_splits = n_splits
        self.min_train_ratio = min_train_ratio
        self.gap = max(0, int(gap))

    def split(self, n: int) -> List[Tuple[np.ndarray, np.ndarray]]:
        min_train = max(2, int(n * self.min_train_ratio))
        remaining = n - min_train
        if remaining < self.n_splits:
            return []
        fold_size = remaining // (self.n_splits + 1)
        splits = []
        for i in range(1, self.n_splits + 1):
            te = min_train + (i - 1) * fold_size
            te_end = te + fold_size
            if te_end > n:
                break
            # Everything from (te - gap) onwards is withheld: it postdates the
            # information a real forecast for this window could have used.
            train_end = te - self.gap
            tr_idx, te_idx = np.arange(max(0, train_end)), np.arange(te, te_end)
            if len(tr_idx) < 2 or len(te_idx) == 0:
                continue
            splits.append((tr_idx, te_idx))
        return splits

    def effective_gap(self, n: int) -> int:
        """
        The gap this many observations can actually afford.

        A short series plus a long horizon starves the early folds: the first
        window is only `min_train` buckets wide to begin with, so a 14-bucket
        gap on a 30-row series leaves it with nothing to learn from. Two things
        are protected here, and `split()` alone protects neither:

          * the FOLD COUNT — losing folds silently turns a 3-fold estimate into
            a 1-fold one, and the metric keeps the same name either way;
          * the WINDOW SIZE — `split()` accepts any window of 2 rows, which is
            not a training set, just a shape that does not raise.

        So the gap is capped at half the smallest training window and then
        walked down until every fold that existed without a gap still exists.
        """
        if self.gap <= 0:
            return 0
        baseline = len(WalkForwardSplitter(self.n_splits, self.min_train_ratio, 0).split(n))
        if baseline == 0:
            return 0
        min_train = max(2, int(n * self.min_train_ratio))
        ceiling = min(self.gap, min_train // 2)
        for gap in range(ceiling, -1, -1):
            probe = WalkForwardSplitter(self.n_splits, self.min_train_ratio, gap)
            if len(probe.split(n)) == baseline:
                return gap
        return 0


class Trainer:
    """Trains sklearn-compatible models per SKU with optional walk-forward validation."""

    def __init__(
        self,
        train_ratio: float = 0.8,
        walk_forward: bool = True,
        wfv_splits: int = 3,
        tuning: bool = False,
        tuning_trials: int = 30,
        max_workers: Optional[int] = None,
        gap: int = 0,
        horizon: int = 0,
        features_cfg=None,
    ):
        self.train_ratio    = train_ratio
        self.walk_forward   = walk_forward
        self.wfv_splits     = wfv_splits
        self.tuning         = tuning
        self.tuning_trials  = tuning_trials
        # Buckets withheld between each training window and the window it is
        # scored on — normally the forecast horizon. See WalkForwardSplitter.
        self.gap            = max(0, int(gap))
        # How many steps the h-step-ahead evaluation forecasts (see
        # _horizon_metrics). Deliberately NOT `gap`: the two happen to be the
        # same number today, but one is a leakage guard on the fold geometry and
        # the other is the length of a forecast. Conflating them would make one
        # impossible to change without silently changing the other. 0 = off.
        self.horizon        = max(0, int(horizon))
        # FeaturesConfig — required to rebuild features step by step the way the
        # production predictor does. Without it the h-step evaluation cannot run
        # and is skipped rather than approximated.
        self.features_cfg   = features_cfg
        # None = auto (min(cpu_count, n_groups)). Each SKU/store group trains
        # independently, so groups can run concurrently in worker threads —
        # LightGBM/XGBoost's fit()/predict() are native code and release the
        # GIL, so this achieves real parallelism despite the GIL. Models are
        # built with n_jobs=1 (see ModelFactory) so this is the only layer
        # that parallelizes; letting both layers parallelize independently
        # would oversubscribe the CPU.
        self.max_workers    = max_workers

    def train(
        self,
        df: pd.DataFrame,
        models: dict,
        group_cols: Optional[List[str]] = None,
        target: str = "",
        dt: str = "",
        group_col: Optional[str] = None,   # deprecated alias — ignored if group_cols given
    ) -> Dict[str, dict]:
        """
        Train models per SKU / (SKU, store) group.

        Args:
            df:         Feature-engineered DataFrame.
            models:     {model_name: sklearn_model}
            group_cols: Column names of the group identifier(s).
                        When len == 1 → sku only, store defaults to "Tienda única".
                        When len == 2 → (sku, store).
            target:     Column name of the target variable.
            dt:         Column name of the date.
            group_col:  Deprecated single-column alias; normalised to group_cols=[group_col].

        Returns:
            {f"{model}_{series_key(sku, store)}": {mae, rmse, wape, bias, sku, store,
                                                   model, n, validation, horizon_metrics}}

            `mae`/`rmse`/`wape`/`cost` are the 1-step fold scores.
            `horizon_metrics` is the h-step-ahead score on the protocol the
            statistical and global models are graded on — the only one of the
            two that can be ranked against them. See _horizon_metrics.
        """
        if group_cols is None:
            group_cols = [group_col] if group_col else []

        has_group = bool(group_cols) and all(c in df.columns for c in group_cols)
        exclude = {dt, target} | set(group_cols)
        trainable = {n: m for n, m in models.items() if hasattr(m, "fit")}
        results = {}

        if has_group:
            if len(group_cols) == 1:
                groups = df.groupby(group_cols[0])
            else:
                groups = df.groupby(group_cols)
        else:
            groups = [("__all__", df)]

        group_list = list(groups)
        n_workers = self._resolve_max_workers(len(group_list))

        if n_workers <= 1:
            # Same isolation as the parallel branch below: one group's
            # unexpected failure must not abort every other group's
            # training, regardless of how many workers this machine has.
            for group_val, g in group_list:
                try:
                    results.update(self._train_one_group(group_val, g, dt, target, exclude, trainable))
                except Exception as e:
                    log.exception(f"Group {group_val!r} training task failed: {e}")
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                future_to_group = {
                    executor.submit(self._train_one_group, group_val, g, dt, target, exclude, trainable): group_val
                    for group_val, g in group_list
                }
                for future in as_completed(future_to_group):
                    try:
                        results.update(future.result())
                    except Exception as e:
                        log.exception(f"Group {future_to_group[future]!r} training task failed: {e}")

        return results

    def _resolve_max_workers(self, n_groups: int) -> int:
        """Worker count for the per-group parallel loop. None (auto) uses
        every available core, capped to the number of groups (no point
        spinning up more workers than there is work)."""
        if n_groups <= 1:
            return 1
        cap = self.max_workers if self.max_workers is not None else (os.cpu_count() or 1)
        return max(1, min(cap, n_groups))

    def _train_one_group(self, group_val, g, dt, target, exclude, trainable) -> Dict[str, dict]:
        """Train every model on one SKU/store group. Runs in a worker thread
        when the group loop is parallelized — must not mutate shared state."""
        if isinstance(group_val, tuple):
            sku_val   = str(group_val[0]) if len(group_val) > 0 else "__all__"
            store_val = str(group_val[1]) if len(group_val) > 1 else DEFAULT_STORE
        elif isinstance(group_val, str):
            sku_val   = group_val
            store_val = DEFAULT_STORE
        else:
            sku_val   = str(group_val)
            store_val = DEFAULT_STORE

        g = g.sort_values(dt).reset_index(drop=True)
        # Kept alongside X (which drops the date column) because the h-step
        # evaluation forecasts real future dates, and the calendar features it
        # rebuilds are only correct on the real ones.
        dates = pd.to_datetime(g[dt]) if dt and dt in g.columns else None
        X = g.drop(columns=[c for c in exclude if c in g.columns])
        non_numeric = X.select_dtypes(exclude=["number", "bool"]).columns.tolist()
        if non_numeric:
            log.warning(
                f"SKU {sku_val} | dropping non-numeric feature column(s) {non_numeric} "
                "— not selected as group_col/target/dt but unsuitable as a raw ML feature"
            )
            X = X.drop(columns=non_numeric)
        y = g[target].astype(float)

        # Leakage guard: drop any feature that is an exact copy of the
        # target (e.g. the canonical 'demand' alias added on top of the
        # user's mapped column) — a model given the answer as a feature
        # reports near-zero validation error and is useless in production.
        leak_cols = [
            col for col in X.columns
            if pd.api.types.is_numeric_dtype(X[col]) and X[col].astype(float).equals(y)
        ]
        if leak_cols:
            log.warning(
                f"SKU {sku_val} | dropping feature column(s) {leak_cols} — "
                f"identical to target '{target}' (data leakage)"
            )
            X = X.drop(columns=leak_cols)

        # _wfv/_simple call .fit() directly on these model instances across
        # fold iterations (not just the final deepcopy). trainable is shared
        # across every group's call, so when groups run in parallel worker
        # threads, concurrent .fit() calls on the same object would corrupt
        # each other's state — give this group its own private copies.
        group_models = {name: copy.deepcopy(m) for name, m in trainable.items()}

        if self.walk_forward:
            return self._wfv(X, y, group_models, sku_val, store_val, dates)
        return self._simple(X, y, group_models, sku_val, store_val, dates)

    def _wfv(self, X, y, models, sku_val, store_val=DEFAULT_STORE, dates=None):
        splitter = WalkForwardSplitter(self.wfv_splits, self.train_ratio / 2, self.gap)
        # A long horizon on a short series can starve every fold; back the gap
        # off rather than collapsing to a single split (see effective_gap).
        gap = splitter.effective_gap(len(X))
        if gap != splitter.gap:
            log.info(
                f"SKU {sku_val} | walk-forward gap reduced {splitter.gap}→{gap} "
                f"— {len(X)} rows cannot fund the full horizon"
            )
            splitter = WalkForwardSplitter(self.wfv_splits, self.train_ratio / 2, gap)
        splits = splitter.split(len(X))
        if not splits:
            return self._simple(X, y, models, sku_val, store_val, dates)

        fold_metrics  = {n: [] for n in models}
        oof_residuals = {n: [] for n in models}   # validation-set residuals per fold

        for tr_idx, te_idx in splits:
            for name, model in models.items():
                try:
                    model.fit(X.iloc[tr_idx], y.iloc[tr_idx])
                    preds = model.predict(X.iloc[te_idx])
                    fold_metrics[name].append(evaluate_all(y.iloc[te_idx].values, preds))
                    oof_residuals[name].extend(
                        (y.iloc[te_idx].values - preds).tolist()
                    )
                except Exception as e:
                    log.warning(f"SKU {sku_val} | {name} | fold error: {e}")

        results = {}
        cut = int(len(X) * self.train_ratio)
        feature_names = list(X.columns)
        train_X = X.iloc[:cut] if cut < len(X) else X
        train_y = y.iloc[:cut] if cut < len(y) else y
        sk = series_key(sku_val, store_val)

        for name, folds in fold_metrics.items():
            if not folds:
                continue
            avg = {k: float(np.mean([f[k] for f in folds])) for k in folds[0]}

            # Optional hyperparameter tuning before final fit
            best_params = self._maybe_tune(name, train_X, train_y)

            # Final model fitted on full training portion for future inference
            fitted_model = None
            residuals = np.array([])
            shap_importance = []
            try:
                final = self._make_final(name, models[name], best_params)
                final.fit(train_X, train_y)
                # Use OOF (out-of-fold) residuals for honest prediction intervals;
                # fall back to in-sample if no OOF residuals were collected.
                if oof_residuals[name]:
                    residuals = np.array(oof_residuals[name])
                else:
                    residuals = train_y.values - final.predict(train_X)
                fitted_model = final
                from forecasting_core.explainability import compute_shap
                shap_importance = compute_shap(final, train_X)
            except Exception as e:
                log.warning(f"SKU {sku_val} | {name} | final fit failed: {e}")

            results[f"{name}_{sk}"] = {
                **avg, "sku": sku_val, "store": store_val, "model": name, "n": len(X),
                "n_folds": len(folds), "validation": "wfv",
                "fitted_model": fitted_model,
                "feature_names": feature_names,
                "residuals": residuals,
                "tuned_params": best_params if best_params else None,
                "shap_importance": shap_importance,
                "horizon_metrics": self._horizon_metrics(
                    fitted_model, feature_names, y, cut, dates, sku_val, name,
                ),
            }
        return results

    def _simple(self, X, y, models, sku_val, store_val=DEFAULT_STORE, dates=None):
        cut = int(len(X) * self.train_ratio)
        if cut < 2 or cut >= len(X):
            return {}
        results = {}
        feature_names = list(X.columns)
        train_X, train_y = X.iloc[:cut], y.iloc[:cut]
        sk = series_key(sku_val, store_val)

        for name, model in models.items():
            try:
                best_params = self._maybe_tune(name, train_X, train_y)
                final = self._make_final(name, model, best_params)
                final.fit(train_X, train_y)
                preds     = final.predict(X.iloc[cut:])
                metrics   = evaluate_all(y.iloc[cut:].values, preds)
                residuals = y.iloc[cut:].values - preds   # validation-set (OOF) residuals
                from forecasting_core.explainability import compute_shap
                shap_importance = compute_shap(copy.deepcopy(final), train_X)
                results[f"{name}_{sk}"] = {
                    **metrics, "sku": sku_val, "store": store_val, "model": name, "n": len(X),
                    "validation": "simple",
                    "fitted_model": copy.deepcopy(final),
                    "feature_names": feature_names,
                    "residuals": residuals,
                    "tuned_params": best_params if best_params else None,
                    "shap_importance": shap_importance,
                    "horizon_metrics": self._horizon_metrics(
                        final, feature_names, y, cut, dates, sku_val, name,
                    ),
                }
            except Exception as e:
                log.warning(f"SKU {sku_val} | {name} | error: {e}")
        return results

    # ------------------------------------------------------------------
    # Honest h-step-ahead evaluation
    # ------------------------------------------------------------------

    def _horizon_metrics(self, model, feature_names, y, cut, dates,
                         sku_val, model_name) -> Dict[str, dict]:
        """
        Score the final model on the job the product actually asks of it.

        `_wfv`/`_simple` grade a model by predicting the held-out ROWS, and those
        rows carry their own true lag features — every one of them is a fresh
        1-step problem with yesterday's real demand handed over. In production
        nothing hands it over: step 2 is built on step 1's guess, step 14 on
        thirteen of them. The statistical models and the global model are already
        graded that way, so a table that mixes the two protocols is comparing an
        easy question with a hard one, and the easy one wins every time.

        This closes that gap by running the PRODUCTION inference path
        (`recursive_ml_predict`) forward from the final train cutoff and scoring
        it against the values that were actually held out.

        Cost: exactly ONE forecast per (model, series) — from the final cutoff
        only, never per fold. It must stay that way; the Trainer runs over every
        SKU in a catalogue, and a per-fold version would multiply the whole
        evaluation by the fold count for no extra information.

        Returns the same shape `GlobalTrainer._series_metrics` emits:
        `{"all_horizons": {...}, "by_horizon": {"1": {...}, ...}}`, or `{}` when
        the evaluation cannot run at all. A series with less held-out data than
        the horizon is scored over the steps it can fund and reports exactly that
        many keys under `by_horizon` — the shortfall is visible in the data
        rather than hidden behind a full-length metric computed on short input.
        """
        if model is None or self.horizon < 1 or self.features_cfg is None:
            return {}
        if dates is None or cut is None or cut < 1 or cut >= len(y):
            return {}

        actual = y.iloc[cut:].astype(float).to_numpy()
        steps = int(min(self.horizon, len(actual), len(dates) - cut))
        if steps < 1:
            return {}
        actual = actual[:steps]
        future_dates = [pd.Timestamp(d) for d in dates.iloc[cut:cut + steps]]

        # Same buffer the production path builds (see predict_all_skus): the
        # point is to reproduce inference, not to give the evaluation a longer
        # history than serving would have.
        cfg = self.features_cfg
        max_lookback = max(
            max(cfg.lags or [1]), max(cfg.rolling or [1]), max(cfg.diffs or [1]),
        ) + 2
        history = y.iloc[:cut].astype(float).to_numpy()[-max_lookback:].tolist()

        from forecasting_core.inference.predictor import recursive_ml_predict
        try:
            points = recursive_ml_predict(
                fitted_model=model,
                feature_names=list(feature_names),
                # Intervals are irrelevant here — only the point forecast is
                # scored — so no residual bank is passed.
                residuals=np.array([]),
                history=history,
                features_cfg=cfg,
                horizon=steps,
                future_dates=future_dates,
                quantiles=[0.5],
            )
        except Exception as e:
            log.warning(
                f"SKU {sku_val} | {model_name} | h-step evaluation failed: {e}"
            )
            return {}

        preds = np.array([p["value"] for p in points], dtype=float)
        if preds.size != actual.size:
            log.warning(
                f"SKU {sku_val} | {model_name} | h-step evaluation returned "
                f"{preds.size} steps for {actual.size} actuals — discarded"
            )
            return {}

        return {
            "all_horizons": evaluate_all(actual, preds),
            "by_horizon": {
                str(i + 1): evaluate_all(actual[i:i + 1], preds[i:i + 1])
                for i in range(steps)
            },
        }

    # ------------------------------------------------------------------
    # Tuning helpers
    # ------------------------------------------------------------------

    def _maybe_tune(self, model_name: str, X_train, y_train) -> dict:
        """Run Optuna tuning if enabled and model is supported. Returns best params or {}."""
        if not self.tuning:
            return {}
        from forecasting_core.training.tuner import HyperparamTuner, SEARCH_SPACES
        if model_name not in SEARCH_SPACES:
            return {}
        log.info(f"Tuning {model_name} ({self.tuning_trials} trials)...")
        tuner = HyperparamTuner(model_name, n_trials=self.tuning_trials)
        return tuner.tune(X_train, y_train)

    def _make_final(self, model_name: str, base_model, best_params: dict):
        """Create the final model: deepcopy base if no tuning, new instance if tuned."""
        if best_params:
            from forecasting_core.training.tuner import _make_model
            return _make_model(model_name, best_params)
        return copy.deepcopy(base_model)
