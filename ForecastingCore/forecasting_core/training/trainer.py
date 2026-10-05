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

Two MODELS come out of it as well. The one that is graded stops at the training
cutoff, because it is scored on what comes after. The one that is SERVED is
refitted on every observation, so the forecast a user receives is not produced
by a model blind to the newest fifth of their history. See `_serving_model`.

Example:
    trainer = Trainer(train_ratio=0.8, walk_forward=True, wfv_splits=3,
                      horizon=14, features_cfg=cfg.features)
    results = trainer.train(df_ml, models, group_cols=["sku"], target="sales", dt="date")
"""

import copy
import logging
import os
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

from forecasting_core.data.canonical import DEFAULT_STORE, series_key
from forecasting_core.data.quality import SERIES_INTERMITTENT, classify_series
from forecasting_core.evaluation.compound import (
    MIN_POSITIVE_OBSERVATIONS, simulate_cumulative_demand,
)
from forecasting_core.evaluation.metrics import evaluate_all
from forecasting_core.models.croston import estimate_intermittent_components

log = logging.getLogger(__name__)


# Floor so a near-empty or all-zero window never divides by (near) zero.
_MIN_SCALE = 1e-3


def _series_scale(y: pd.Series) -> float:
    """
    Denominator for the cumulative-residual bank (stability.md 17b, second
    half): what a residual is divided by before it joins the pooled bank, and
    multiplied back by to turn a pooled quantile into this series' own units.

    Used to be the series' own MEAN. That is the wrong choice for a
    zero-inflated series: the mean of a 70%-zero series is set mostly by HOW
    OFTEN it is zero, not by how big an order is when one happens, so two
    slow movers with the same typical order size but different order
    frequency (e.g. one that sells 2 units 40% of days and one that sells 2
    units 12% of days) get scaled by numbers more than 3x apart and the
    pooled bank ends up describing neither. Measured on the demo catalogue,
    dividing by a mean of ~1.2 units/day amplified noise instead of
    normalising it.

    The mean of the NONZERO observations — the typical size of an order,
    independent of how often one occurs — does not have that problem: it is
    set by size alone. Measured offline on the synthetic intermittent
    catalogue (`exp_scale_denominator2.py` in the 17b work), the standard
    deviation of the scaled cumulative L-sum error came out 4-6x lower with
    this denominator than with the mean, and did not blow up on the early,
    thin folds the way a high quantile of the raw series (also considered)
    did — a high quantile of a mostly-zero window can itself collapse to
    (near) zero and divide a residual into a spurious outlier.

    Falls back to the series' own mean when it has no positive observations
    at all (a flat-zero window) — the same floor the old code used, and the
    only sane answer when there is no "order size" to speak of.
    """
    arr = y.to_numpy(dtype=float) if hasattr(y, "to_numpy") else np.asarray(y, dtype=float)
    positive = arr[arr > 0]
    if positive.size:
        return max(float(positive.mean()), _MIN_SCALE)
    return max(abs(float(arr.mean())) if arr.size else 0.0, _MIN_SCALE)


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
        intermittent_objectives: Optional[Dict[str, dict]] = None,
    ):
        self.train_ratio    = train_ratio
        # {model_name: estimator kwargs} — the OPTIONAL count objective
        # (tweedie / poisson) a model switches to on an intermittent or lumpy
        # series. Built by `ModelFactory.intermittent_objectives()`; empty by
        # default, and empty means every model trains exactly as before. See
        # `_intermittent_overrides`.
        self.intermittent_objectives = dict(intermittent_objectives or {})
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
        # Pooled cumulative-residual bank — {stratum: {horizon: array of SCALED
        # cumulative errors}} for one model — built from the walk-forward folds
        # `_wfv` already fits, one entry per (SKU, fold). Reset at the start of
        # every `train()` call and mutated under `_bank_lock` because groups
        # run in worker threads. See `_bank_fold_cumulative_residuals` and the
        # end of `train()`.
        self.pooled_cumulative_residuals: Dict[str, Dict[str, Dict[int, np.ndarray]]] = {}
        # {model_name: {series_stratum: {horizon: [scaled cumulative
        # residuals]}}}. Keyed by MODEL, not just by horizon: one bank shared
        # across families pools lightgbm's errors with xgboost's and publishes
        # the mixture as the band for whichever of them wins the SKU — a
        # cushion that describes neither. Measured end to end on the demo
        # catalogue, the mixed bank delivered 83.3% against a nominal 95%, no
        # better than the classical formula it replaces.
        #
        # Keyed by series STRATUM as well (stability.md 17b, second half):
        # `classify_series` — the same shape/CV/zero-ratio classification the
        # quality checker and the router already use — labels each group
        # short/intermittent/volatile/seasonal/stable, and a residual from a
        # 70%-zero SKU is banked separately from one off a smooth daily
        # seller. Measured cause: a single pooled bank scales every residual
        # by the series' own MEAN, and dividing a persistently-zero series'
        # residual by a mean of ~1 unit/day amplifies noise instead of
        # normalising it — see `_series_scale`. Stratifying alone does not
        # rescue that; the scale had to change too. A stratum that cannot fund
        # `MIN_RESIDUALS_PER_HORIZON` at a horizon is never merged into a
        # busier stratum to make it look funded — `Pipeline._demand_risk`
        # drops it instead, exactly as it already drops a thin unstratified
        # bank. Publishing nothing beats a confident number built from a
        # handful of points from a different kind of series.
        self._cumulative_bank: Dict[str, Dict[str, Dict[int, List[float]]]] = {}
        self._bank_lock = threading.Lock()
        # `intermittent`'s bank additionally receives PARAMETRIC pseudo-
        # residuals from `_bank_fold_compound_residuals` (stability.md 17b:
        # stratifying by `classify_series` closed the "wrong shape"
        # hypothesis without rescuing intermittent coverage — the empirical
        # quantile of a zero-inflated L-sum is not estimable from as few
        # rolling origins as a small catalogue funds, whatever it is keyed
        # by). Those come from a compound count-times-size model fitted on
        # each SKU's OWN history, which needs far fewer observations than the
        # sum's own 95th percentile does — see `evaluation/compound.py`.

    def train(
        self,
        df: pd.DataFrame,
        models: dict,
        group_cols: Optional[List[str]] = None,
        target: str = "",
        dt: str = "",
        group_col: Optional[str] = None,   # deprecated alias — ignored if group_cols given
        on_unit=None,
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
            on_unit:    Optional ``callable(done, total)`` invoked from the calling
                        thread after each group finishes (success or failure), so
                        a caller can report real progress.

        Returns:
            {f"{model}_{series_key(sku, store)}": {mae, rmse, wape, bias, sku, store,
                                                   model, n, validation, horizon_metrics}}

            `mae`/`rmse`/`wape`/`cost` are the 1-step fold scores.
            `horizon_metrics` is the h-step-ahead score on the protocol the
            statistical and global models are graded on — the only one of the
            two that can be ranked against them. See _horizon_metrics.

            Every entry also carries `series_scale` (that group's own scale),
            `series_stratum` (its `classify_series` label) and
            `cumulative_residuals_by_horizon` (this run's pooled bank for that
            entry's OWN model and stratum, same reference shared by every
            other entry of that (model, stratum) pair) — see
            `_bank_fold_cumulative_residuals`.
        """
        if group_cols is None:
            group_cols = [group_col] if group_col else []

        has_group = bool(group_cols) and all(c in df.columns for c in group_cols)
        exclude = {dt, target} | set(group_cols)
        trainable = {n: m for n, m in models.items() if hasattr(m, "fit")}
        results = {}
        # Fresh for this run — a Trainer instance must not carry a bank over
        # from a previous train() call into this one.
        self._cumulative_bank = {}

        if has_group:
            if len(group_cols) == 1:
                groups = df.groupby(group_cols[0])
            else:
                groups = df.groupby(group_cols)
        else:
            groups = [("__all__", df)]

        group_list = list(groups)
        n_workers = self._resolve_max_workers(len(group_list))
        total_groups = len(group_list)

        def _unit_done(done: int) -> None:
            if on_unit is not None:
                try:
                    on_unit(done, total_groups)
                except Exception:
                    pass  # reporting must never fail training

        if n_workers <= 1:
            # Same isolation as the parallel branch below: one group's
            # unexpected failure must not abort every other group's
            # training, regardless of how many workers this machine has.
            for done, (group_val, g) in enumerate(group_list, start=1):
                try:
                    results.update(self._train_one_group(group_val, g, dt, target, exclude, trainable))
                except Exception as e:
                    log.exception(f"Group {group_val!r} training task failed: {e}")
                _unit_done(done)
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                future_to_group = {
                    executor.submit(self._train_one_group, group_val, g, dt, target, exclude, trainable): group_val
                    for group_val, g in group_list
                }
                for done, future in enumerate(as_completed(future_to_group), start=1):
                    try:
                        results.update(future.result())
                    except Exception as e:
                        log.exception(f"Group {future_to_group[future]!r} training task failed: {e}")
                    _unit_done(done)

        # Freeze the pooled bank now that every group has reported in, and
        # hand every entry the SAME reference — the shape `_demand_risk`
        # already knows from `GlobalDirectForecaster.cumulative_residuals_by_
        # horizon`, so it needs no new vocabulary to read a per-SKU champion's
        # bank instead of the global model's. A group with too few folds of
        # its own to contribute still benefits: it borrows ITS OWN STRATUM's
        # pooled residuals, exactly as a brand-new SKU borrows the global
        # model's bank — but never another stratum's, which is the mixing
        # this two-level key exists to prevent (see `_cumulative_bank`).
        banks = {
            name: {
                stratum: {h: np.asarray(v, dtype=float) for h, v in per_h.items() if v}
                for stratum, per_h in per_stratum.items()
            }
            for name, per_stratum in self._cumulative_bank.items()
        }
        self.pooled_cumulative_residuals = banks
        for entry in results.values():
            model = str(entry.get("model"))
            stratum = str(entry.get("series_stratum"))
            entry["cumulative_residuals_by_horizon"] = banks.get(model, {}).get(stratum, {})

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

        overrides, decisions = self._intermittent_overrides(y, group_models, sku_val)
        for name, kw in overrides.items():
            group_models[name].set_params(**kw)

        if self.walk_forward:
            results = self._wfv(X, y, group_models, sku_val, store_val, dates, overrides)
        else:
            results = self._simple(X, y, group_models, sku_val, store_val, dates, overrides)
        # Say which objective each opted-in model actually trained with on this
        # series — including when it was asked for and NOT applied — so a run
        # configured for tweedie can never be mistaken for one that used it.
        for entry in results.values():
            d = decisions.get(str(entry.get("model")))
            if d is not None:
                entry["intermittent_objective"] = dict(d)
        return results

    # Syntetos-Boylan average-demand-interval cut-off: at or above it a series
    # is intermittent or lumpy (benchmarks/metrics.py uses the same 1.32).
    INTERMITTENT_ADI = 1.32

    def _intermittent_overrides(self, y: pd.Series, models: dict, sku_val):
        """
        Which opted-in models switch to their count objective on this group.

        Decided ONCE per group, on the TRAINING portion only (the part before
        the evaluation cutoff), so the graded model, the folds, the tuner and
        the served model are all the same kind of model, and the decision does
        not peek at the window it is scored on.

        Applied only when the series is intermittent or lumpy (ADI >= 1.32)
        and its target is non-negative: tweedie and poisson are defined for
        y >= 0, and both libraries refuse (or silently misfit) a negative
        label. Returns (overrides {name: kwargs}, decisions {name: record}).
        """
        if not self.intermittent_objectives:
            return {}, {}
        cut = int(len(y) * self.train_ratio)
        head = y.iloc[:cut] if 0 < cut < len(y) else y
        arr = head.to_numpy(dtype=float)
        arr = arr[np.isfinite(arr)]
        n_pos = int(np.count_nonzero(arr > 0))
        adi = (len(arr) / n_pos) if n_pos else float("inf")
        if (y.to_numpy(dtype=float) < 0).any():
            reason = "negative_target"
        elif n_pos == 0:
            reason = "no_demand"
        elif adi < self.INTERMITTENT_ADI:
            reason = "not_intermittent"
        else:
            reason = None
        overrides: Dict[str, dict] = {}
        decisions: Dict[str, dict] = {}
        for name, kw in self.intermittent_objectives.items():
            if name not in models:
                continue
            applied = reason is None
            if applied:
                overrides[name] = dict(kw)
            elif reason == "negative_target":
                log.warning(f"SKU {sku_val} | {name} | {kw.get('objective')} objective "
                            "not applied: the target has negative values")
            decisions[name] = {
                "requested": kw.get("objective"),
                "applied": applied,
                "objective": kw.get("objective") if applied else "default",
                "reason": reason,
                "adi": round(adi, 3) if np.isfinite(adi) else None,
            }
        return overrides, decisions

    def _wfv(self, X, y, models, sku_val, store_val=DEFAULT_STORE, dates=None,
             overrides=None):
        overrides = overrides or {}
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
            return self._simple(X, y, models, sku_val, store_val, dates, overrides)

        fold_metrics  = {n: [] for n in models}
        oof_residuals = {n: [] for n in models}   # validation-set residuals per fold
        feature_names = list(X.columns)
        # The series' own scale, in units for the pooled cumulative-residual
        # bank (see _bank_fold_cumulative_residuals and _series_scale) — a SKU
        # selling 5/day and one selling 5000/day become comparable once
        # divided by it, exactly the reason GlobalTrainer scales its own
        # residual bank.
        level = _series_scale(y)
        # Which bank this group's residuals join, and whose bank its entries
        # read back — see _cumulative_bank.
        stratum = classify_series(y)

        for tr_idx, te_idx in splits:
            for name, model in models.items():
                try:
                    model.fit(X.iloc[tr_idx], y.iloc[tr_idx])
                    preds = model.predict(X.iloc[te_idx])
                    fold_metrics[name].append(evaluate_all(y.iloc[te_idx].values, preds))
                    oof_residuals[name].extend(
                        (y.iloc[te_idx].values - preds).tolist()
                    )
                    # Bank this fold's cumulative error — no extra fit() call,
                    # `model` is already fitted for this fold. See the method
                    # docstring for why this runs the recursive inference path
                    # rather than reusing the one-step `preds` above.
                    self._bank_fold_cumulative_residuals(
                        model, feature_names, y, int(tr_idx[-1]) + 1, dates,
                        level, sku_val, name, stratum,
                    )
                except Exception as e:
                    log.warning(f"SKU {sku_val} | {name} | fold error: {e}")

        results = {}
        cut = int(len(X) * self.train_ratio)
        train_X = X.iloc[:cut] if cut < len(X) else X
        train_y = y.iloc[:cut] if cut < len(y) else y
        sk = series_key(sku_val, store_val)

        for name, folds in fold_metrics.items():
            if not folds:
                continue
            avg = {k: float(np.mean([f[k] for f in folds])) for k in folds[0]}

            # Optional hyperparameter tuning before final fit
            fixed = overrides.get(name)
            best_params = self._maybe_tune(name, train_X, train_y, fixed)

            # The GRADED model: fitted on the training portion only, because the
            # h-step evaluation below scores it on what comes after.
            graded_model = None
            residuals = np.array([])
            try:
                graded = self._make_final(name, models[name], best_params, fixed)
                graded.fit(train_X, train_y)
                # Use OOF (out-of-fold) residuals for honest prediction intervals;
                # fall back to in-sample if no OOF residuals were collected.
                if oof_residuals[name]:
                    residuals = np.array(oof_residuals[name])
                else:
                    residuals = train_y.values - graded.predict(train_X)
                graded_model = graded
            except Exception as e:
                log.warning(f"SKU {sku_val} | {name} | final fit failed: {e}")

            horizon_metrics = self._horizon_metrics(
                graded_model, feature_names, y, cut, dates, sku_val, name,
            )
            fitted_model, shap_importance = self._serving_model(
                name, models[name], best_params, X, y, sku_val, graded_model, fixed,
            )

            results[f"{name}_{sk}"] = {
                **avg, "sku": sku_val, "store": store_val, "model": name, "n": len(X),
                "n_folds": len(folds), "validation": "wfv",
                "fitted_model": fitted_model,
                "feature_names": feature_names,
                "residuals": residuals,
                "tuned_params": best_params if best_params else None,
                "shap_importance": shap_importance,
                "horizon_metrics": horizon_metrics,
                # This group's own scale, in the same units as the pooled
                # bank `train()` attaches to this entry, and the stratum that
                # selects WHICH bank — see `_bank_fold_cumulative_residuals`.
                "series_scale": level,
                "series_stratum": stratum,
            }
        return results

    def _simple(self, X, y, models, sku_val, store_val=DEFAULT_STORE, dates=None,
                overrides=None):
        overrides = overrides or {}
        cut = int(len(X) * self.train_ratio)
        if cut < 2 or cut >= len(X):
            return {}
        results = {}
        feature_names = list(X.columns)
        train_X, train_y = X.iloc[:cut], y.iloc[:cut]
        sk = series_key(sku_val, store_val)
        # No walk-forward folds ran here, so this group contributes nothing
        # of its own to the pooled cumulative-residual bank — but it still
        # gets a scale and a stratum, so an entry from this path can borrow
        # its OWN stratum's bank exactly like one that did contribute. See
        # _wfv.
        level = _series_scale(y)
        stratum = classify_series(y)

        for name, model in models.items():
            try:
                fixed = overrides.get(name)
                best_params = self._maybe_tune(name, train_X, train_y, fixed)
                graded = self._make_final(name, model, best_params, fixed)
                graded.fit(train_X, train_y)
                preds     = graded.predict(X.iloc[cut:])
                metrics   = evaluate_all(y.iloc[cut:].values, preds)
                residuals = y.iloc[cut:].values - preds   # validation-set (OOF) residuals
                horizon_metrics = self._horizon_metrics(
                    graded, feature_names, y, cut, dates, sku_val, name,
                )
                fitted_model, shap_importance = self._serving_model(
                    name, model, best_params, X, y, sku_val, graded, fixed,
                )
                results[f"{name}_{sk}"] = {
                    **metrics, "sku": sku_val, "store": store_val, "model": name, "n": len(X),
                    "validation": "simple",
                    "fitted_model": fitted_model,
                    "feature_names": feature_names,
                    "residuals": residuals,
                    "tuned_params": best_params if best_params else None,
                    "shap_importance": shap_importance,
                    "horizon_metrics": horizon_metrics,
                    "series_scale": level,
                    "series_stratum": stratum,
                }
            except Exception as e:
                log.warning(f"SKU {sku_val} | {name} | error: {e}")
        return results

    # ------------------------------------------------------------------
    # The model that is graded, and the model that is served
    # ------------------------------------------------------------------

    def _serving_model(self, name, base_model, best_params, X, y, sku_val,
                       graded_model, fixed=None):
        """
        Refit on EVERY observation, and return the model inference will use.

        The model that gets graded has to stop where the grading starts: it is
        scored on the buckets it was not shown. The model that is SERVED has no
        such constraint, and leaving it fitted on the training portion means the
        forecast a user receives was produced by a model blind to the newest
        fifth of their history — the most informative fifth there is for a
        series with a level, a trend or a new product in it.

        Every statistical model in this pipeline already refits: `ets.py`,
        `arima.py`, `prophet.py` and `croston.py` each build a second model on
        the whole series before forecasting. The ML path did not, and the two
        sat in the same metrics table.

        Measured on the demo catalogue (10 SKUs x 450 buckets, last 30 held
        out): for the per-SKU recursive models the asymmetric cost over the
        horizon went 46.0 -> 43.5. For the direct global model — which cannot
        recover the recent level through lag features the way a recursive model
        partly does — the same change took the forecast from 14% high to
        unbiased and halved its WAPE (0.150 -> 0.077) on 10 series out of 10.

        The metrics keep describing `graded_model`; only `fitted_model` changes.
        If the refit fails the graded model is served instead, because a
        forecast from a model trained on less data beats no forecast at all.

        Returns (model_to_serve, shap_importance).
        """
        from forecasting_core.explainability import compute_shap

        try:
            serving = self._make_final(name, base_model, best_params, fixed)
            serving.fit(X, y)
        except Exception as e:
            log.warning(
                f"SKU {sku_val} | {name} | refit on full history failed: {e} "
                "— serving the validated model instead"
            )
            serving = copy.deepcopy(graded_model) if graded_model is not None else None

        if serving is None:
            return None, []
        # compute_shap swallows its own failures and returns [].
        return serving, compute_shap(serving, X)

    # ------------------------------------------------------------------
    # Pooled cumulative-residual bank (stability.md 17(b))
    # ------------------------------------------------------------------

    def _record_cumulative_residual(
        self, model_name: str, stratum: str, horizon: int, value: float,
    ) -> None:
        """Thread-safe append into this MODEL's, this STRATUM's pooled bank —
        groups can run in worker threads (see `train`). Keyed by model so a
        champion's cushion is built from that champion's own errors, and by
        `classify_series` stratum so it is built from series that fail the
        same way — see `_cumulative_bank`."""
        with self._bank_lock:
            per_model = self._cumulative_bank.setdefault(str(model_name), {})
            per_stratum = per_model.setdefault(str(stratum), {})
            per_stratum.setdefault(int(horizon), []).append(float(value))

    def _record_cumulative_residuals_bulk(
        self, model_name: str, stratum: str, horizon: int, values: np.ndarray,
    ) -> None:
        """Same bucket as `_record_cumulative_residual`, for many values at
        once — `extend` instead of `append` per value, which matters once a
        single fold contributes thousands of simulated residuals (see
        `_bank_fold_compound_residuals`)."""
        if values.size == 0:
            return
        with self._bank_lock:
            per_model = self._cumulative_bank.setdefault(str(model_name), {})
            per_stratum = per_model.setdefault(str(stratum), {})
            per_stratum.setdefault(int(horizon), []).extend(
                float(v) for v in values
            )

    def _bank_fold_cumulative_residuals(
        self, model, feature_names, y, cut, dates, level, sku_val, model_name,
        stratum,
    ) -> None:
        """
        Extend the pooled cumulative-residual bank with one more rolling origin.

        The product promises a service level on the demand that accumulates
        while an order is in transit — the SUM over the lead time, not any
        single bucket — and `z * sigma_1 * sqrt(L)` understates that sum's
        variance because per-bucket forecast errors are autocorrelated (a
        forecast running high today runs high tomorrow). Measuring the sum's
        error directly, the way `GlobalTrainer._backtest` already does for the
        global model, closes that gap for the per-SKU champion instead.

        This mirrors `_horizon_metrics`: it runs the PRODUCTION inference path
        (`recursive_ml_predict`) forward from a cutoff and scores it against
        the values actually held out. The difference is the origin — this
        runs from EVERY fold cutoff `_wfv` already produces, one rolling
        origin per fold, not only the final one — and what it records: the
        CUMULATIVE error at each step, divided by the series' own scale (see
        `_series_scale`) so it can be pooled with every other SKU's OF THE
        SAME STRATUM into one bank (see the end of `train`). Per
        `evaluation/conformal.py`, cumulative residuals of different horizons
        must never be pooled with EACH OTHER — only each horizon's own list
        is pooled across SKUs — and that separation is kept here: one bucket
        per horizon, filled from every series that shares this stratum.

        Cost: one recursive forecast per (fold, model) that was already
        fitted — no extra `fit()` call. `model` arrives already fitted for
        this fold; the caller (`_wfv`) is the one paying for `fit()`, and it
        pays for it exactly once per fold either way.

        Failures (a short fold, a broken forecast) are logged and swallowed:
        one bad origin must not lose the whole catalogue's bank, and a
        horizon nothing could contribute to simply stays absent — never
        fabricated at zero. See `Pipeline._demand_risk`, which is the reader.
        """
        if self.horizon < 1 or self.features_cfg is None:
            return
        if dates is None or cut is None or cut < 1 or cut >= len(y):
            return

        actual = y.iloc[cut:].astype(float).to_numpy()
        steps = int(min(self.horizon, len(actual), len(dates) - cut))
        if steps < 1:
            return
        actual = actual[:steps]
        future_dates = [pd.Timestamp(d) for d in dates.iloc[cut:cut + steps]]

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
                # Intervals are irrelevant here — only the point forecast
                # feeds the residual — so no residual bank is passed.
                residuals=np.array([]),
                history=history,
                features_cfg=cfg,
                horizon=steps,
                future_dates=future_dates,
                quantiles=[0.5],
            )
        except Exception as e:
            log.warning(
                f"SKU {sku_val} | {model_name} | fold cumulative-residual "
                f"forecast failed: {e}"
            )
            return

        preds = np.array([p["value"] for p in points], dtype=float)
        if preds.size != actual.size:
            return

        cum_actual = np.cumsum(actual)
        cum_pred = np.cumsum(preds)
        for h in range(1, steps + 1):
            self._record_cumulative_residual(
                model_name, stratum, h, (cum_actual[h - 1] - cum_pred[h - 1]) / level,
            )

        if stratum == SERIES_INTERMITTENT:
            self._bank_fold_compound_residuals(
                y, cut, cum_pred, level, sku_val, model_name, stratum,
            )

    def _bank_fold_compound_residuals(
        self, y, cut, cum_pred: np.ndarray, level: float, sku_val, model_name,
        stratum: str,
    ) -> None:
        """
        Widen the intermittent-stratum bank with a PARAMETRIC distribution
        instead of another real rolling origin (stability.md 17b: three
        refutations of re-keying the same empirical-quantile instrument — "the
        next idea should not be another key"). This changes the instrument.

        A lead-time sum of intermittent demand is a compound distribution — a
        count of demand occasions times a size per occasion. Both pieces are
        estimable from the whole history up to this fold's cutoff (every day
        is a data point about the rate; every positive day is a data point
        about the size), which is far more data than the handful of real
        rolling origins a small catalogue can fund for the SUM's own tail —
        see `evaluation/compound.py`'s module docstring.

        `_bank_fold_cumulative_residuals` already produced this fold's own
        point forecast (`cum_pred`, from a model fitted only on data before
        `cut`) — reused here rather than forecasting twice. What is added is
        a distribution of what actual cumulative demand COULD plausibly have
        been, drawn from the compound model estimated on `y[:cut]`, each
        draw turned into a residual against that SAME `cum_pred` exactly as
        the one real origin already is. The residual absorbs whatever bias
        `cum_pred` carries — a useless point forecast on this stratum (see
        stability.md 17b: WAPE 1.36) does not need correcting separately,
        because the offset it feeds already corrects for it.

        Silently a no-op when the history has too few positive observations
        to bootstrap a size distribution from (`MIN_POSITIVE_OBSERVATIONS`)
        — the fold's one real residual, banked above, is what the bank gets
        instead, exactly as before this existed.
        """
        history = y.iloc[:cut].astype(float).to_numpy()
        rate, sizes = estimate_intermittent_components(history)
        if sizes.size < MIN_POSITIVE_OBSERVATIONS or rate <= 0:
            return
        steps = int(cum_pred.size)
        if steps < 1:
            return
        # A per-call but DETERMINISTIC seed — Python's built-in `hash()` on a
        # str is randomized per process (PYTHONHASHSEED), which would make
        # this bank's contents change on every run with no code change at
        # all. zlib.crc32 has no such randomization.
        seed = zlib.crc32(f"{sku_val}|{model_name}|{cut}".encode()) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        sim_cum = simulate_cumulative_demand(rate, sizes, steps, rng=rng)
        residuals = (sim_cum - cum_pred[np.newaxis, :]) / level
        for h in range(1, steps + 1):
            self._record_cumulative_residuals_bulk(
                model_name, stratum, h, residuals[:, h - 1],
            )

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

    def _maybe_tune(self, model_name: str, X_train, y_train, fixed: Optional[dict] = None) -> dict:
        """Run Optuna tuning if enabled and model is supported. Returns best params or {}.

        `fixed` (the intermittent count objective, when this group uses one) is
        held constant in every trial: the search explores the trees, the
        tuner's own objective stays the asymmetric cost, and the winner is a
        tweedie model if a tweedie model is what will be served."""
        if not self.tuning:
            return {}
        from forecasting_core.training.tuner import HyperparamTuner, SEARCH_SPACES
        if model_name not in SEARCH_SPACES:
            return {}
        log.info(f"Tuning {model_name} ({self.tuning_trials} trials)...")
        tuner = HyperparamTuner(model_name, n_trials=self.tuning_trials, fixed_params=fixed)
        return tuner.tune(X_train, y_train)

    def _make_final(self, model_name: str, base_model, best_params: dict,
                    fixed: Optional[dict] = None):
        """Create the final model: deepcopy base if no tuning, new instance if tuned.

        A tuned instance is built from the tuned params alone (unchanged
        behaviour), plus `fixed` — without it, tuning would silently swap a
        tweedie model back to L2 for the model that is graded and served."""
        if best_params:
            from forecasting_core.training.tuner import _make_model
            return _make_model(model_name, {**best_params, **(fixed or {})})
        return copy.deepcopy(base_model)
