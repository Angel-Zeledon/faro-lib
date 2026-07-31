"""
Runner — bridges the backend job system to the forecasting_core ML library.

This is the ONLY place the backend imports from forecasting_core.
Everything else in the backend is pure orchestration.
"""

import logging
import os
import random
import time as _time
from datetime import datetime, timezone

from fastapi import HTTPException

from backend.config import settings
from backend.db import session_store
from backend.datasets.service import get_dataset
from backend.sessions.service import get_session
from backend.training.job_service import mark_completed, mark_failed, update_progress
from backend.training.progress_broadcaster import broadcaster
from backend.sessions.service import force_status
from backend.api.v1.webhooks import fire_webhooks

log = logging.getLogger(__name__)


class TrainingDataError(RuntimeError):
    """Training failed because of the DATA, not a defect.

    `str(exc)` is a stable machine code (never a sentence): the job row stores
    it verbatim and the frontend renders the localized copy from
    `errors.training.<code>`, falling back to the raw text for anything it does
    not recognize. Keeps CLAUDE.md's "no Spanish in backend logic" intact while
    still giving the user an actionable message.
    """


def _training_max_workers() -> int:
    """
    Cap forecasting_core's per-SKU training parallelism to this machine's
    core count divided across the job worker's own concurrent-session pool
    (settings.max_concurrent_jobs), so N sessions training at once can never
    oversubscribe the CPU between them.
    """
    return max(1, (os.cpu_count() or 1) // max(1, settings.max_concurrent_jobs))


# ── Config assembly ────────────────────────────────────────────────────────

def build_engine_config(tenant_id: str, session_id: str) -> dict:
    """
    Assembles a SessionConfig-compatible dict from the session's DB config blobs.
    Maps backend schema → forecasting_core schema.

    Supports two column-mapping schemas stored in columns_cfg:
      - "legacy"       (default): expects sku_column / date_column / target_column keys.
      - "canonical_v1": expects a nested canonical_mapping dict
                        {canonical_field: actual_column_name}.
    """
    s = get_session(tenant_id, session_id)
    columns_cfg = session_store.get_field(tenant_id, session_id, "columns_cfg") or {}
    features_cfg = session_store.get_field(tenant_id, session_id, "features_cfg") or {}
    models_cfg = session_store.get_field(tenant_id, session_id, "models_cfg") or {}
    validation_cfg = session_store.get_field(tenant_id, session_id, "validation_cfg") or {}
    business_cfg = session_store.get_field(tenant_id, session_id, "business_cfg") or {}
    forecast_cfg = session_store.get_field(tenant_id, session_id, "forecast_cfg") or {}
    granularity_cfg = session_store.get_field(tenant_id, session_id, "granularity_cfg") or {}

    # Resolve dataset file path from DB
    dataset_path = ""
    dataset_id = s.get("dataset_id") if s else None
    if dataset_id:
        ds_meta = get_dataset(tenant_id, dataset_id)
        if ds_meta:
            dataset_path = ds_meta.get("file_path", "")

    # Build models dict: {model_name: {hyperparams}}
    selected = models_cfg.get("selected_models", [])
    hyperparams = models_cfg.get("hyperparameters", {})
    if models_cfg.get("mode") == "all":
        from forecasting_core.models.factory import ModelFactory
        selected = ModelFactory.available_models()
    models_dict = {m: hyperparams.get(m, {}) for m in selected}

    # ── Column mapping: branch on schema_version ──────────────────────────
    schema_version = columns_cfg.get("schema_version", "legacy")

    # One resolver, shared with the pre-training gate: the gate must judge the
    # same date/demand/product columns this function is about to train on, or
    # the standard the API enforces is not the standard the run obeys.
    from backend.sessions.data_gate import resolve_columns
    _resolved = resolve_columns(columns_cfg)

    if schema_version == "canonical_v1":
        # New canonical path: columns_cfg has a nested canonical_mapping dict
        # {canonical_field ("sku","date","demand","store",...): actual_column_name}
        cmap = columns_cfg.get("canonical_mapping", {})
        target_col = _resolved["target"]   # canonical "demand" → user's actual column
        date_col   = _resolved["date"]
        sku_col    = _resolved["group"]
        group_keys = list(_resolved["group_keys"])
        if not group_keys:
            group_keys = ["sku"]   # safe fallback: canonical column name added by apply_canonical_defaults
        cols_dict = {
            "target":     target_col or "demand",
            "date":       date_col   or "date",
            "group_keys": group_keys,
            "exogenous":  [],
            # Only a column the user ACTUALLY mapped. apply_canonical_defaults
            # broadcasts inventory=0 into every session that did not map one, and
            # the censoring pass reads a non-positive stock as a stockout — so
            # passing the canonical default here would flag every row in the
            # dataset as censored and inflate the whole tenant's forecast.
            "inventory":  cmap.get("inventory") or "",
        }
    else:
        # Legacy path: columns_cfg has flat sku_column / date_column / target_column
        cmap = {}
        sku_col = columns_cfg.get("sku_column")
        cols_dict = {
            "target":     columns_cfg.get("target_column", ""),
            "date":       columns_cfg.get("date_column", ""),
            "group_keys": [sku_col] if sku_col else ["sku"],
            "exogenous":  columns_cfg.get("exogenous", []) or [],
            # Legacy sessions have no canonical mapping and therefore no
            # inventory column to point at; censoring stays off for them.
            "inventory":  columns_cfg.get("inventory_column", "") or "",
        }

    return {
        "name": f"session_{session_id[:8]}",
        "data": {
            "path": dataset_path,
            "date_freq": None,
        },
        "columns": cols_dict,
        "_gap_fill": columns_cfg.get("gap_fill", "leave"),
        "_outlier_config": columns_cfg.get("outlier_config", {}),
        # The answers the user gave at the pre-training gate,
        # {issue_type: option_code} — see forecasting_core.data.gate.
        "_remediations": columns_cfg.get("remediations") or {},
        # Stash schema_version + canonical_mapping so run_training_job can apply defaults
        "_schema_version": schema_version,
        "_canonical_mapping": cmap,
        "features": {
            "lags": features_cfg.get("lags", [1, 7, 14]),
            "rolling": features_cfg.get("rolling", [7, 14, 28]),
            "diffs": features_cfg.get("diffs", [1]),
            "calendar": features_cfg.get("calendar", True),
            "ewm_spans": features_cfg.get("ewm_spans", []),
            "fourier_periods": features_cfg.get("fourier_periods", []),
            "fourier_K": features_cfg.get("fourier_K", 2),
            "holiday_country": features_cfg.get("holiday_country", "CO"),
        },
        "models": models_dict,
        "training": {
            "train_ratio": validation_cfg.get("train_ratio", 0.8),
            "walk_forward": validation_cfg.get("walk_forward", True),
            "wfv_splits": validation_cfg.get("wfv_splits", 3),
            "min_history": validation_cfg.get("min_history", 20),
            "seasonal_period": validation_cfg.get("seasonal_period", 7),
            "max_workers": _training_max_workers(),
        },
        "forecast": {
            "horizon": forecast_cfg.get("horizon", validation_cfg.get("horizon", 14)),
        },
        "granularity": {
            "strategy": granularity_cfg.get("strategy", "native"),
            "target_freq": granularity_cfg.get("target_freq"),
        },
        # Reconciliation across aggregation levels. Off unless the session names
        # levels: with `levels` empty the engine skips the reconciler entirely,
        # which is the behaviour every existing session already has.
        "hierarchy": {
            "levels": columns_cfg.get("hierarchy_levels") or [],
            "reconciliation": columns_cfg.get("reconciliation", "bottom_up"),
        },
        "business": {
            "service_level": business_cfg.get("service_level", 0.95),
            "lead_time_days": business_cfg.get("lead_time_days", 7),
            "holding_cost_pct": business_cfg.get("holding_cost_pct", 0.20),
            "stockout_cost_multiplier": business_cfg.get("stockout_cost_multiplier", 3.0),
        },
    }


# ── Column config helper ──────────────────────────────────────────────────

def _primary_group_col(col_cfg: dict):
    """
    Return the primary (first) group column from the columns config dict.

    After the canonical_v1 / legacy fix, the dict always has 'group_keys' (a list).
    Falls back to the old 'group' key for any lingering legacy callers.
    """
    gk = col_cfg.get("group_keys")
    if gk and isinstance(gk, list):
        return gk[0] if gk else None
    return col_cfg.get("group")   # legacy fallback (should no longer be needed)


def _group_label(key) -> str:
    """The name of one series, written for the person who reads the warning.

    `df.groupby(["producto"])` hands back 1-TUPLES in pandas 2, so the moment
    grouping moved from a single column to the full key list (the multi-warehouse
    fix), every prep note that names a product started saying `('A',)` instead of
    `A` — in a sentence the user reads on the results screen. A two-key series is
    two facts about one row, so it reads as "A · Norte", not as a Python repr.
    """
    if key is None:
        return ""
    if isinstance(key, tuple):
        return " · ".join(str(k) for k in key)
    return str(key)


def _group_cols(col_cfg: dict) -> list:
    """
    ALL the columns that identify a series, not just the first one.

    A series is a (SKU, warehouse) pair whenever the session maps a store
    column — which the integrations path does automatically for any tenant whose
    ERP reports a warehouse. Prep steps that group by the SKU alone silently
    merge every warehouse into one:

        in                                   out
        2026-01-05, SKU-A, 10.0, Norte       2026-01-05, SKU-A, 15.0, Norte
        2026-01-05, SKU-A,  5.0, Sur

    Sur is gone. It never gets a forecast, never gets a reorder point, and its
    stock sits without a signal forever; Norte is left carrying the whole
    network's demand and is over-ordered by exactly what the deleted branches
    sold. Neither number is flagged, because from the pipeline's point of view
    nothing failed.
    """
    gk = col_cfg.get("group_keys")
    if gk and isinstance(gk, list):
        return [c for c in gk if c]
    legacy = col_cfg.get("group")
    return [legacy] if legacy else []


# ── Corrupted-value guard ─────────────────────────────────────────────────

# ── Data-prep notes ────────────────────────────────────────────────────────
#
# The prep helpers below silently rewrite the user's data: transactions get
# summed into daily totals, infinities become NaN, gap fill is abandoned for a
# SKU with an implausible date range, a configured outlier strategy fails.
# Every one of those is something only the USER can fix upstream, and every one
# of them used to exist solely as a line in the server log.
#
# They append here instead. `code` is a stable English identifier; the Spanish
# comes from the frontend's i18n, same contract as the engine's error_id.
# Each helper takes `notes` optionally so it stays a pure function under test.

def _note(notes: "list | None", code: str, severity: str = "warning", **context) -> None:
    if notes is None:
        return
    notes.append({"error_id": code, "severity": severity, "layer": "data_prep",
                  "message": "", "context": context, "suggestions": []})


def _note_granularity_fallback(
    requested: "str | None", trained: "str | None", notes: "list | None" = None,
) -> None:
    """The Quick Start granularity pick is advisory, and its rejection was mute.

    `family_service.plan_family` drops a pick the data cannot support — fewer
    than MIN_BUCKETS_FOR_GRANULARITY buckets of that grain, or a grain finer
    than the data's native one — and fans out automatically instead so the run
    never fails. Nothing told the user: they chose "Mensual", got a daily plan,
    and the only trace was `forecast_cfg.user_granularity` disagreeing with
    `sessions.granularity`. Monthly needs ~20 months of history, so this is the
    common case for a young catalogue, not a corner.
    """
    if not requested or requested == "auto" or not trained or requested == trained:
        return
    _note(notes, "GRANULARITY_PICK_NOT_SUPPORTED", "warning",
          requested=requested, trained=trained)


def _coerce_decimal_comma(df: "pd.DataFrame", target_col: str,
                          notes: "list | None" = None,
                          convention: "str | None" = None) -> "pd.DataFrame":
    """Read a quantity written the way most of Latin America writes it.

    `10,5` means ten and a half here. Left as text it survives every check the
    upload screen makes — the column is not empty, it is not the word "diez",
    the separator was detected correctly — and the run dies deep inside training
    with `could not convert string to float: '10,5'`, which the UI then shows
    verbatim in the middle of a Spanish sentence. Reproduced end to end: the
    file was accepted, the screen said "Esto se ve bien, continuar", and two
    minutes later the user got a Python exception.

    Only two shapes are converted, and only when the WHOLE column agrees:

        1.234,56  ->  1234.56     dot thousands, comma decimal
        10,5      ->  10.5        comma decimal

    A column mixing both conventions is left alone.

    `1,234` is the case this function used to refuse outright — 1234 in English,
    1.234 in Spanish — and refusing was right while there was nobody to ask. Now
    the gate asks (`ambiguous_number_format`) and the answer arrives here as
    `convention`: "thousands" or "decimal". Without an answer the old behaviour
    stands, because a silent 1000x is the worst outcome on this whole list.
    """
    import re
    import pandas as pd

    if target_col not in df.columns:
        return df
    col = df[target_col]
    if pd.api.types.is_numeric_dtype(col):
        return df

    text = col.dropna().astype(str).str.strip()
    if text.empty:
        return df

    european = re.compile(r"^-?\d{1,3}(?:\.\d{3})+,\d+$")   # 1.234,56
    simple = re.compile(r"^-?\d+,\d+$")                      # 10,5
    ambiguous = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")        # 1,234 — could be either

    if text.str.match(ambiguous).any():
        if convention == "thousands":
            numeric = pd.to_numeric(
                col.astype(str).str.strip().str.replace(",", "", regex=False),
                errors="coerce",
            )
            n = int(numeric.notna().sum())
            _note(notes, "PREP_NUMBER_FORMAT_THOUSANDS", "warning",
                  n_rows=n, column=target_col)
            out = df.copy()
            out[target_col] = numeric
            return out
        if convention == "decimal":
            numeric = pd.to_numeric(
                col.astype(str).str.strip().str.replace(",", ".", regex=False),
                errors="coerce",
            )
            n = int(numeric.notna().sum())
            _note(notes, "PREP_NUMBER_FORMAT_DECIMAL", "warning",
                  n_rows=n, column=target_col)
            out = df.copy()
            out[target_col] = numeric
            return out
        log.warning(
            "Column %r contains values like '1,234' where the comma could be a "
            "thousands separator or a decimal point. Leaving it untouched: "
            "guessing wrong changes the quantity by 1000x.", target_col,
        )
        return df

    matches = text.str.match(european) | text.str.match(simple)
    if not matches.all():
        return df

    converted = (col.astype(str).str.strip()
                    .str.replace(".", "", regex=False)
                    .str.replace(",", ".", regex=False))
    numeric = pd.to_numeric(converted, errors="coerce")
    if numeric.isna().all():
        return df

    n = int(numeric.notna().sum())
    log.info("Read %d value(s) in column %r as decimal-comma numbers.", n, target_col)
    _note(notes, "PREP_DECIMAL_COMMA_PARSED", "warning",
          n_rows=n, column=target_col)
    out = df.copy()
    out[target_col] = numeric
    return out


def _neutralize_infinities(df: "pd.DataFrame", target_col: str,
                           notes: "list | None" = None) -> "pd.DataFrame":
    """Replace ±inf in the target with NaN, so the missing-data path handles it.

    An overflowing quantity is not a large sale — it is a broken cell. Left
    alone it is neither NaN nor an outlier, so it reaches training intact and
    every error metric for that SKU comes back inf/NaN. Returns the frame
    unchanged (same object) when there is nothing to fix, which is the case for
    every healthy dataset.
    """
    import numpy as np
    import pandas as pd

    if target_col not in df.columns:
        return df
    try:
        col = pd.to_numeric(df[target_col], errors="coerce")
    except Exception:
        return df
    mask = np.isinf(col)
    n = int(mask.sum())
    if not n:
        return df
    log.warning(
        "Neutralized %d infinite value(s) in target column '%s' — treated as missing.",
        n, target_col,
    )
    _note(notes, "PREP_INFINITE_NEUTRALIZED", n_rows=n, column=target_col)
    out = df.copy()
    out.loc[mask.fillna(False), target_col] = np.nan
    return out


def _collapse_duplicate_periods(df: "pd.DataFrame", date_col: str, target_col: str,
                                group_cols: "list | str | None",
                                notes: "list | None" = None) -> "pd.DataFrame":
    """Sum rows that share the same (date, series) into a single observation.

    ERPs commonly export one row per SALE, not per day. Left as-is those rows
    become separate points of the series: 3 transactions of 4+3+3 on one day
    train a model that predicts ~3 per step instead of 10 per day, and the
    resulting purchase suggestion is a third of what the business needs — a
    silent stockout. The profiler already reports the duplicates ("180
    duplicate date+SKU rows"); nothing acted on them.

    Summing is the only defensible reading for demand: the day's sales ARE the
    sum of that day's transactions. Non-target columns (price, cost, stock)
    would be nonsense summed, so they keep their first value in the group.
    Returns the frame untouched when there is nothing to collapse.

    `group_cols` is EVERY column that identifies a series, not just the SKU.
    Grouping by the SKU alone on a multi-warehouse dataset does not collapse
    duplicates — it deletes warehouses: two branches selling the same SKU on the
    same day become one row carrying the sum, tagged with whichever branch came
    first. The others vanish from the forecast entirely. See `_group_cols`.
    """
    import pandas as pd

    if not date_col or date_col not in df.columns or target_col not in df.columns:
        return df
    if isinstance(group_cols, str):
        group_cols = [group_cols]
    keys = [date_col] + [c for c in (group_cols or []) if c and c in df.columns]
    try:
        n_dupes = int(df.duplicated(subset=keys, keep=False).sum())
    except Exception:
        return df
    if not n_dupes:
        return df

    others = [c for c in df.columns if c not in keys and c != target_col]
    agg = {target_col: "sum"}
    agg.update({c: "first" for c in others})
    out = df.groupby(keys, as_index=False, sort=False).agg(agg)
    log.warning(
        "Collapsed %d duplicate (date, SKU) row(s) into %d period totals — "
        "quantities summed so demand is per period, not per transaction.",
        n_dupes, len(out),
    )
    _note(notes, "PREP_DUPLICATES_COLLAPSED", n_rows=n_dupes, n_periods=len(out))
    return out[df.columns.tolist()]


# ── Gap fill helper ───────────────────────────────────────────────────────

# Most buckets one series may be reindexed to. ~55 years of daily history —
# far beyond any real forecasting need, and low enough that a bad date cannot
# turn one SKU into millions of rows.
MAX_GAP_FILL_BUCKETS = 20_000


def _apply_gap_fill(df: "pd.DataFrame", date_col: str, target_col: str,
                    group_cols: "list | str | None", strategy: str,
                    notes: "list | None" = None) -> "pd.DataFrame":
    """
    Reindex a time series to fill missing dates using the chosen strategy.
    strategy: "zero" | "mean" | "forward" | "interpolate" | "leave"

    Grouped by EVERY series key. Grouping by the SKU alone on a multi-warehouse
    dataset is worse here than merely wrong: the per-group
    `drop_duplicates(subset=[date_col])` then throws away one branch's row for
    every date two branches both sold on — silently, before any model sees it.
    See `_group_cols`.
    """
    import pandas as pd

    if strategy == "leave" or not strategy:
        return df

    if isinstance(group_cols, str):
        group_cols = [group_cols]
    group_cols = [c for c in (group_cols or []) if c and c in df.columns]

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    # Detect native frequency (median gap in days)
    try:
        sample_dates = df[date_col].dropna().sort_values().drop_duplicates()
        freq_days = int(sample_dates.diff().dropna().dt.days.median())
        if freq_days < 1:
            freq_days = 1
    except Exception:
        return df

    freq_str = f"{freq_days}D"
    if group_cols:
        groups = [(key, part.copy()) for key, part in df.groupby(group_cols, sort=False)]
    else:
        groups = [(None, df)]
    parts = []

    for g, sub in groups:
        sub = sub.drop_duplicates(subset=[date_col]).sort_values(date_col)
        if len(sub) < 2:
            parts.append(sub)
            continue

        # A single absurd date (a 1900 typo, a 2099 future-dated row) stretches
        # the span to tens of thousands of buckets PER SKU — filling them would
        # materialize millions of rows and take the worker down. The series is
        # left unfilled instead: forecasting on real-but-gappy history beats
        # not finishing at all, and the profiler already flags both the gaps
        # and the out-of-range dates.
        span = pd.date_range(sub[date_col].min(), sub[date_col].max(), freq=freq_str)
        if len(span) > MAX_GAP_FILL_BUCKETS:
            log.warning(
                "Skipped gap fill for group %r: %d buckets exceeds the %d cap — "
                "the date range is implausible (check for typo'd or future dates).",
                g, len(span), MAX_GAP_FILL_BUCKETS,
            )
            _note(notes, "PREP_GAP_FILL_SKIPPED", sku=_group_label(g),
                  date_min=str(sub[date_col].min().date()),
                  date_max=str(sub[date_col].max().date()))
            parts.append(sub)
            continue

        full_idx = span
        sub = sub.set_index(date_col).reindex(full_idx)
        sub.index.name = date_col

        if group_cols:
            # `g` is a scalar for one key and a tuple for several; either way
            # every key has to be written back onto the reindexed rows, or the
            # new dates come back with no series identity at all.
            values = g if isinstance(g, tuple) else (g,)
            for col, value in zip(group_cols, values):
                sub[col] = value

        if strategy == "zero":
            sub[target_col] = sub[target_col].fillna(0)
        elif strategy == "mean":
            sub[target_col] = sub[target_col].fillna(sub[target_col].mean())
        elif strategy == "forward":
            sub[target_col] = sub[target_col].ffill().bfill()
        elif strategy == "interpolate":
            sub[target_col] = sub[target_col].interpolate(method="linear").bfill().ffill()

        parts.append(sub.reset_index())

    if not parts:
        return df

    result = pd.concat(parts, ignore_index=True)
    # Restore other columns (non-numeric cols in original)
    other_cols = [c for c in df.columns
                  if c not in ([date_col, target_col] + group_cols)]
    if other_cols and group_cols:
        fill_map = df.groupby(group_cols)[other_cols].first()
        result = result.merge(fill_map, on=group_cols, how="left", suffixes=("", "_orig"))
    elif other_cols and not group_cols:
        for c in other_cols:
            if c not in result.columns:
                result[c] = df[c].iloc[0]

    return result[[c for c in df.columns if c in result.columns]]


# ── Outlier treatment ─────────────────────────────────────────────────────

def _apply_outlier_treatment(
    df: "pd.DataFrame",
    date_col: str,
    target_col: str,
    group_col: str | None,
    outlier_cfg: dict,
    notes: "list | None" = None,
) -> "pd.DataFrame":
    """
    Apply outlier treatment per SKU before training.

    Strategies:
      leave           — no change
      winsorize_sigma — clip to mean ± n_sigma × std
      winsorize_pct   — clip to [percentile%, (100-percentile)%]
      iqr_fence       — clip to Q1 - iqr_k×IQR .. Q3 + iqr_k×IQR
      remove          — replace outliers with NaN then ffill/bfill
      log1p           — log1p transform of the entire series (stabilises variance)

    Per-SKU overrides in outlier_cfg["per_sku_overrides"] take precedence.
    """
    import pandas as pd
    import numpy as np

    global_strategy = outlier_cfg.get("strategy", "leave") if isinstance(outlier_cfg, dict) else "leave"
    per_sku_overrides = outlier_cfg.get("per_sku_overrides", {}) if isinstance(outlier_cfg, dict) else {}
    if global_strategy == "leave" and not per_sku_overrides:
        return df

    df = df.copy()
    groups = [None] if not group_col else df[group_col].unique().tolist()

    def _get(key, default, sku_key=None):
        per_sku_map = outlier_cfg.get(f"per_sku_{key}", {}) if isinstance(outlier_cfg, dict) else {}
        if sku_key and str(sku_key) in per_sku_map:
            return per_sku_map[str(sku_key)]
        return outlier_cfg.get(key, default) if isinstance(outlier_cfg, dict) else default

    for g in groups:
        mask = pd.Series(True, index=df.index) if g is None else (df[group_col] == g)
        sku_key = g

        overrides = outlier_cfg.get("per_sku_overrides", {}) if isinstance(outlier_cfg, dict) else {}
        strategy = overrides.get(str(sku_key), global_strategy) if sku_key is not None else global_strategy

        if strategy == "leave":
            continue

        vals = pd.to_numeric(df.loc[mask, target_col], errors="coerce")
        clean = vals.dropna()
        if len(clean) < 4:
            continue

        try:
            if strategy == "winsorize_sigma":
                n_sigma = _get("n_sigma", 3.0, sku_key)
                mu, sigma = clean.mean(), clean.std()
                if sigma > 0:
                    df.loc[mask, target_col] = vals.clip(mu - n_sigma * sigma, mu + n_sigma * sigma)

            elif strategy == "winsorize_pct":
                pct = _get("percentile", 1.0, sku_key)
                lo = clean.quantile(pct / 100)
                hi = clean.quantile(1 - pct / 100)
                df.loc[mask, target_col] = vals.clip(lo, hi)

            elif strategy == "iqr_fence":
                iqr_k = _get("iqr_k", 1.5, sku_key)
                Q1, Q3 = clean.quantile(0.25), clean.quantile(0.75)
                IQR = Q3 - Q1
                if IQR > 0:
                    df.loc[mask, target_col] = vals.clip(Q1 - iqr_k * IQR, Q3 + iqr_k * IQR)

            elif strategy == "remove":
                # Use 3×IQR to identify, then interpolate
                Q1, Q3 = clean.quantile(0.25), clean.quantile(0.75)
                IQR = Q3 - Q1
                if IQR > 0:
                    lo, hi = Q1 - 3 * IQR, Q3 + 3 * IQR
                    outlier_mask = mask & ((pd.to_numeric(df[target_col], errors="coerce") < lo) |
                                          (pd.to_numeric(df[target_col], errors="coerce") > hi))
                    df.loc[outlier_mask, target_col] = np.nan
                    df.loc[mask, target_col] = (
                        df.loc[mask, target_col]
                          .interpolate(method="linear")
                          .ffill()
                          .bfill()
                    )

            elif strategy == "log1p":
                clipped = vals.clip(lower=0)
                df.loc[mask, target_col] = np.log1p(clipped)

        except Exception as e:
            # The user picked this strategy in the wizard and the config screen
            # still says it is active — silence here means raw outliers reach
            # the model while the UI claims they were handled.
            log.warning(f"Outlier treatment failed for SKU={g}: {e}")
            _note(notes, "PREP_OUTLIER_TREATMENT_FAILED", sku=_group_label(g), strategy=strategy)

    return df


# ── Remediations the user picked at the gate ───────────────────────────────
#
# Every option `forecasting_core.data.gate` offers is applied by exactly one of
# the helpers below, and `REMEDIATION_STEPS` is the map the gate's `applied_by`
# key points at. A test walks the catalogue and asserts each option lands here:
# an option that changes nothing is worse than no option, because the user
# believes they made a decision.

def _apply_date_format(df: "pd.DataFrame", date_col: str, dayfirst: bool,
                       notes: "list | None" = None) -> "pd.DataFrame":
    """Parse `03/04/2026` the way the user says their ERP writes it.

    Left to pandas this is a coin flip that never lands loudly: read day-first
    data month-first and every observation moves to another week, weekly
    seasonality — the strongest signal in distributor demand — is gone, and the
    run finishes with ordinary-looking metrics on a forecast built from a
    shuffled calendar.
    """
    import pandas as pd

    if not date_col or date_col not in df.columns:
        return df
    parsed = pd.to_datetime(df[date_col], dayfirst=dayfirst, errors="coerce")
    n_ok = int(parsed.notna().sum())
    if not n_ok:
        return df
    out = df.copy()
    out[date_col] = parsed
    _note(notes, "PREP_DATE_FORMAT_APPLIED", n_rows=n_ok,
          column=date_col, dayfirst=dayfirst)
    return out


def _apply_excel_serial_dates(df: "pd.DataFrame", col: str,
                              notes: "list | None" = None) -> "pd.DataFrame":
    """Turn Excel's day-count (45000) back into a date (2023-03-15)."""
    import pandas as pd

    from forecasting_core.data.gate import EXCEL_EPOCH

    if not col or col not in df.columns:
        return df
    try:
        parsed = pd.to_datetime(pd.to_numeric(df[col], errors="coerce"),
                                unit="D", origin=EXCEL_EPOCH, errors="coerce")
    except Exception:
        return df
    n_ok = int(parsed.notna().sum())
    if not n_ok:
        return df
    out = df.copy()
    out[col] = parsed
    _note(notes, "PREP_EXCEL_SERIAL_CONVERTED", n_rows=n_ok, column=col)
    return out


def _drop_out_of_range_dates(df: "pd.DataFrame", date_col: str,
                             notes: "list | None" = None) -> "pd.DataFrame":
    """Remove the 1900 and 2099 rows the user chose to drop.

    Their real cost is not the lost row: one of them stretches the series span
    past the gap-fill cap, so gap filling is abandoned for that whole product —
    which used to happen on its own, in a log line.
    """
    import pandas as pd

    from forecasting_core.data.gate import MAX_PLAUSIBLE_FUTURE_DAYS, MIN_PLAUSIBLE_YEAR

    if not date_col or date_col not in df.columns:
        return df
    dates = pd.to_datetime(df[date_col], errors="coerce")
    lo = pd.Timestamp(year=MIN_PLAUSIBLE_YEAR, month=1, day=1)
    hi = pd.Timestamp.now().normalize() + pd.Timedelta(days=MAX_PLAUSIBLE_FUTURE_DAYS)
    bad = dates.notna() & ((dates < lo) | (dates > hi))
    n = int(bad.sum())
    if not n:
        return df
    _note(notes, "PREP_OUT_OF_RANGE_DATES_DROPPED", n_rows=n, column=date_col)
    return df.loc[~bad].copy()


def _apply_non_numeric_policy(df: "pd.DataFrame", target_col: str, mode: str,
                              notes: "list | None" = None) -> "pd.DataFrame":
    """`$10`, `10 kg`, `N/D`, `-` in the quantity column.

    Coerced to NaN and dropped further downstream without a word — the user's
    file said something and the product silently decided it said nothing.
    """
    import pandas as pd

    if not target_col or target_col not in df.columns:
        return df
    if mode not in ("strip", "zero", "drop"):
        return df
    col = df[target_col]
    if pd.api.types.is_numeric_dtype(col):
        return df

    text = col.astype(str).str.strip()
    direct = pd.to_numeric(text.str.replace(",", ".", regex=False), errors="coerce")
    bad = direct.isna() & col.notna() & (text != "")
    n = int(bad.sum())
    if not n:
        return df

    out = df.copy()
    if mode == "strip":
        cleaned = pd.to_numeric(
            text.str.replace(r"[^0-9,.\-]", "", regex=True)
                .str.replace(",", ".", regex=False),
            errors="coerce",
        )
        recovered = int((bad & cleaned.notna()).sum())
        out[target_col] = direct.where(~bad, cleaned)
        _note(notes, "PREP_NON_NUMERIC_STRIPPED", n_rows=n, n_recovered=recovered,
              column=target_col)
    elif mode == "zero":
        out[target_col] = direct.where(~bad, 0.0)
        _note(notes, "PREP_NON_NUMERIC_AS_ZERO", n_rows=n, column=target_col)
    else:  # drop
        out = df.loc[~bad].copy()
        out[target_col] = pd.to_numeric(
            out[target_col].astype(str).str.strip().str.replace(",", ".", regex=False),
            errors="coerce",
        )
        _note(notes, "PREP_NON_NUMERIC_ROWS_DROPPED", n_rows=n, column=target_col)
    return out


def _apply_null_target_policy(df: "pd.DataFrame", target_col: str, mode: str,
                              notes: "list | None" = None) -> "pd.DataFrame":
    """Empty quantity cells — a different question from unreadable text."""
    import pandas as pd

    if not target_col or target_col not in df.columns or mode not in ("zero", "drop"):
        return df
    values = pd.to_numeric(df[target_col], errors="coerce")
    missing = values.isna()
    n = int(missing.sum())
    if not n:
        return df
    if mode == "zero":
        out = df.copy()
        out[target_col] = values.fillna(0.0)
        _note(notes, "PREP_NULL_TARGET_AS_ZERO", n_rows=n, column=target_col)
        return out
    out = df.loc[~missing].copy()
    _note(notes, "PREP_NULL_TARGET_ROWS_DROPPED", n_rows=n, column=target_col)
    return out


def _unify_group_values(df: "pd.DataFrame", group_cols: "list | str | None",
                        notes: "list | None" = None) -> "pd.DataFrame":
    """Fold `SKU-1`, `sku-1` and `SKU-1 ` back into one product.

    Split, each fragment carries a slice of the history, several fall under the
    trainable minimum and disappear, and the stock the buyer typed against the
    real code matches whichever fragment happened to survive.
    """
    if isinstance(group_cols, str):
        group_cols = [group_cols]
    group_cols = [c for c in (group_cols or []) if c and c in df.columns]
    if not group_cols:
        return df

    out = df.copy()
    total_merged = 0
    for col in group_cols:
        raw = out[col].astype(str)
        norm = raw.str.strip().str.casefold().str.replace(r"\s+", " ", regex=True)
        # Keep a human-readable label: the most frequent raw spelling wins, so
        # the SKU shown on the semáforo is one the user recognises.
        canonical = (
            raw.groupby(norm).agg(lambda s: s.value_counts().idxmax()).to_dict()
        )
        merged = int((raw != norm.map(canonical)).sum())
        if merged:
            out[col] = norm.map(canonical)
            total_merged += merged
    if total_merged:
        _note(notes, "PREP_SKU_IDENTITY_UNIFIED", n_rows=total_merged,
              columns=",".join(group_cols))
    return out


def _apply_negative_policy(df: "pd.DataFrame", date_col: str, target_col: str,
                           group_cols: "list | str | None", mode: str,
                           notes: "list | None" = None) -> "pd.DataFrame":
    """Returns booked as negative sales — three readings, three different totals.

    Runs BEFORE the duplicates policy on purpose: `net` means "subtract the
    return from the sales of the same day", which only exists while the day's
    rows are still separate. It touches only the (day, product) groups that
    actually contain a negative, so a `keep_last` choice still governs every
    other duplicated day.
    """
    import pandas as pd

    if not target_col or target_col not in df.columns:
        return df
    if mode not in ("net", "zero", "drop"):
        return df
    values = pd.to_numeric(df[target_col], errors="coerce")
    negative = values < 0
    n = int(negative.sum())
    if not n:
        return df

    if mode == "zero":
        out = df.copy()
        out[target_col] = values.where(~negative, 0.0)
        _note(notes, "PREP_NEGATIVES_AS_ZERO", n_rows=n, column=target_col)
        return out

    if mode == "drop":
        out = df.loc[~negative].copy()
        _note(notes, "PREP_NEGATIVES_ROWS_DROPPED", n_rows=n, column=target_col)
        return out

    # net — collapse only the affected (date, series) groups to their sum,
    # floored at zero, and leave every other group untouched.
    if isinstance(group_cols, str):
        group_cols = [group_cols]
    keys = [date_col] + [c for c in (group_cols or []) if c and c in df.columns]
    keys = [k for k in keys if k in df.columns]
    if not keys:
        out = df.copy()
        out[target_col] = values.clip(lower=0)
        _note(notes, "PREP_NEGATIVES_NETTED", n_rows=n, n_periods=0, column=target_col)
        return out

    work = df.copy()
    work[target_col] = values
    marker = work.groupby(keys, sort=False)[target_col].transform(lambda s: (s < 0).any())
    affected = work.loc[marker.fillna(False)]
    untouched = work.loc[~marker.fillna(False)]
    if affected.empty:
        return df

    others = [c for c in work.columns if c not in keys and c != target_col]
    agg = {target_col: "sum"}
    agg.update({c: "first" for c in others})
    collapsed = affected.groupby(keys, as_index=False, sort=False).agg(agg)
    collapsed[target_col] = collapsed[target_col].clip(lower=0)

    out = pd.concat([untouched, collapsed[work.columns.tolist()]], ignore_index=True)
    _note(notes, "PREP_NEGATIVES_NETTED", n_rows=n, n_periods=len(collapsed),
          column=target_col)
    return out[df.columns.tolist()]


def _dedupe_keep_last(df: "pd.DataFrame", date_col: str,
                      group_cols: "list | str | None",
                      notes: "list | None" = None) -> "pd.DataFrame":
    """Keep the last row of each (day, product) — the "these are corrections" reading.

    The opposite of `_collapse_duplicate_periods`, and the reason both exist as
    a CHOICE: summing corrections doubles demand, keeping the last of real
    transactions divides it. Only the user knows which their ERP exports.
    """
    if isinstance(group_cols, str):
        group_cols = [group_cols]
    keys = [date_col] + [c for c in (group_cols or []) if c and c in df.columns]
    keys = [k for k in keys if k in df.columns]
    if not keys:
        return df
    try:
        n_dupes = int(df.duplicated(subset=keys, keep=False).sum())
    except Exception:
        return df
    if not n_dupes:
        return df
    out = df.drop_duplicates(subset=keys, keep="last")
    _note(notes, "PREP_DUPLICATES_KEPT_LAST", n_rows=n_dupes, n_periods=len(out))
    return out.reset_index(drop=True)


def _decumulate(df: "pd.DataFrame", date_col: str, target_col: str,
                group_cols: "list | str | None",
                notes: "list | None" = None) -> "pd.DataFrame":
    """Turn a running month-to-date total back into per-period demand.

    Trained as-is, a cumulative column has a perfect upward trend, fits
    beautifully and predicts that next period beats every period in the
    company's history — forever, and with good-looking accuracy numbers.
    """
    import pandas as pd

    if not target_col or target_col not in df.columns or date_col not in df.columns:
        return df
    if isinstance(group_cols, str):
        group_cols = [group_cols]
    group_cols = [c for c in (group_cols or []) if c and c in df.columns]

    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out[target_col] = pd.to_numeric(out[target_col], errors="coerce")
    out = out.sort_values(([*group_cols] if group_cols else []) + [date_col])

    if group_cols:
        diffed = out.groupby(group_cols, sort=False)[target_col].diff()
    else:
        diffed = out[target_col].diff()

    # The first period of every series has nothing to subtract from. Dropping it
    # is the honest reading — inventing a value would put a fabricated
    # observation at the very start of every product's history.
    keep = diffed.notna()
    n_dropped = int((~keep).sum())
    out = out.loc[keep].copy()
    # A running total that goes DOWN is a return or a counter reset, not
    # negative demand.
    out[target_col] = diffed.loc[keep].clip(lower=0)
    _note(notes, "PREP_CUMULATIVE_DIFFED", n_rows=len(out), n_dropped=n_dropped,
          column=target_col)
    return out.reset_index(drop=True)


# Gate option code → the concrete argument each helper needs. Single source of
# truth for "what does picking this actually do".
_GAP_FILL_BY_OPTION = {
    "gaps_fill_zero": "zero",
    "gaps_forward_fill": "forward",
    "gaps_interpolate": "interpolate",
    "gaps_leave": "leave",
}
_OUTLIER_STRATEGY_BY_OPTION = {
    "outliers_leave": "leave",
    "outliers_clip_iqr": "iqr_fence",
    "outliers_winsorize": "winsorize_sigma",
}


def _remediation_choices(columns_cfg: dict) -> dict:
    """The user's answers at the gate: {issue_type: option_code}."""
    chosen = columns_cfg.get("remediations") or {}
    return chosen if isinstance(chosen, dict) else {}


# ── Forecast series generation ─────────────────────────────────────────────

def _generate_forecast_series(engine, config: dict) -> dict:
    """
    Build {sku: {model: {"historical": [...], "forecast": [...]}}} from engine results.
    Uses the ML/stat forecasts produced by train(); returns {} if no forecast data.
    """
    import pandas as pd
    from collections import defaultdict

    fc_raw = engine.get_forecast()   # {"rows": [...], "n_skus": N, "horizon": H}
    rows = fc_raw.get("rows", [])
    if not rows:
        return {}

    col_cfg    = config["columns"]
    dt_col     = col_cfg["date"]
    target_col = col_cfg["target"]
    group_keys = [c for c in (col_cfg.get("group_keys") or []) if c]

    df = engine._df.copy() if engine._df is not None else pd.DataFrame()

    # Historical series per forecast key. With two group keys the engine keys
    # its forecast rows via series_key(sku, store) = "sku│store" — build the
    # history keys with the ENGINE'S OWN helper (this module is the one place
    # allowed to import forecasting_core), so history and forecast can never
    # drift onto different key formats.
    from forecasting_core.data.canonical import series_key

    historical_by_sku: dict = {}
    if not df.empty and dt_col in df.columns and target_col in df.columns:
        df[dt_col] = pd.to_datetime(df[dt_col])
        present_keys = [c for c in group_keys if c in df.columns]
        if len(present_keys) >= 2:
            src = (
                (series_key(g[0], g[1]), frame)
                for g, frame in df.groupby(present_keys[:2])
            )
        elif len(present_keys) == 1:
            src = df.groupby(present_keys[0])
        else:
            src = [("__all__", df)]
        for sku, g in src:
            historical_by_sku[str(sku)] = [
                {"date": str(row[dt_col])[:10], "value": round(float(row[target_col]), 4)}
                for _, row in g.sort_values(dt_col).iterrows()
            ]

    # Group forecast rows by (sku, model)
    by_sku_model: dict = defaultdict(list)
    for r in rows:
        by_sku_model[(str(r["sku"]), str(r["model"]))].append(r)

    # Detect quantile column names from any point in the data
    q_keys: list = []
    for pts_list in by_sku_model.values():
        if pts_list:
            q_keys = sorted(
                [k for k in pts_list[0] if k.startswith("q") and k[1:].isdigit()],
                key=lambda k: int(k[1:]),
            )
            break

    result: dict = {}
    for (sku_key, model_name), pts in by_sku_model.items():
        pts_sorted = sorted(pts, key=lambda x: x.get("step", 0))
        forecast_pts = []
        for p in pts_sorted:
            # Resolve lower/upper: prefer explicit lower/upper, fall back to p90 bands
            lo = p.get("lower") if p.get("lower") is not None else p.get("p90_lo")
            hi = p.get("upper") if p.get("upper") is not None else p.get("p90_hi")
            raw_val = float(p["forecast"])
            raw_lo  = float(lo) if lo is not None else None
            raw_hi  = float(hi) if hi is not None else None
            pt: dict = {
                "date":  str(p["date"])[:10],
                "value": round(max(0.0, raw_val), 4),
                "lower": round(max(0.0, raw_lo), 4) if raw_lo is not None else None,
                "upper": round(max(0.0, raw_hi), 4) if raw_hi is not None else None,
            }
            for q_key in q_keys:
                v = p.get(q_key)
                pt[q_key] = round(float(v), 4) if v is not None else None
            forecast_pts.append(pt)
        if sku_key not in result:
            result[sku_key] = {}
        result[sku_key][model_name] = {
            "historical": historical_by_sku.get(sku_key, []),
            "forecast":   forecast_pts,
        }

    return result


# ── Excluded-SKU transparency ──────────────────────────────────────────────

def _compute_excluded_skus(df, group_col, forecasts: dict, min_history: int) -> list[dict]:
    """
    SKUs that were uploaded but did NOT make it into the forecast — so the UI can
    tell the user *which* products were left out and *why*, instead of silently
    dropping them (the #1 source of "I uploaded 5 SKUs and only 3 showed up").

    Reason is derived from row count: the engine drops series shorter than
    min_history (can't be forecast reliably); anything else that's missing is
    flagged generically as "no_forecast".
    """
    if df is None or not group_col or group_col not in getattr(df, "columns", []):
        return []
    forecast_keys = set(forecasts.keys())
    excluded: list[dict] = []
    for sku, g in df.groupby(group_col):
        sku = str(sku)
        if sku in forecast_keys:
            continue
        n = int(len(g))
        if n < min_history:
            excluded.append({
                "sku": sku, "n_rows": n, "reason": "insufficient_history",
                "detail": f"Solo {n} registros de historia (se necesitan al menos {min_history})",
            })
        else:
            excluded.append({
                "sku": sku, "n_rows": n, "reason": "no_forecast",
                "detail": "No se pudo generar un pronóstico confiable para este producto",
            })
    return excluded


# ── Run warnings ───────────────────────────────────────────────────────────

# One finding per SKU would put thousands of near-identical rows in the
# payload. They are grouped by code, so the cap is on distinct codes.
MAX_WARNING_CODES = 20
MAX_WARNING_SAMPLES = 5


def _collect_run_warnings(engine, prep_notes: "list | None" = None) -> dict:
    """Group the validation layers' findings so the UI can show them.

    The layers run in WARNING mode and never abort a run, so before this the
    only trace was the server log. TARGET_FEATURE_LEAKAGE is the one that
    matters most: it yields a near-perfect score on a forecast that is
    worthless, which the user has no way to diagnose alone.

    Codes stay English (`error_id`); the frontend renders the Spanish.
    """
    try:
        raw = engine.get_run_warnings()
    except Exception as exc:  # never let a reporting detail fail a good run
        log.warning(f"Could not collect run warnings: {exc}")
        raw = {"validation": [], "corrections": []}

    # The prep helpers in this module rewrite the user's data before the engine
    # ever sees it (transactions summed, infinities voided, gap fill abandoned),
    # so their notes belong in the same list as the engine's own findings.
    findings = list(raw.get("validation") or []) + list(prep_notes or [])

    grouped: dict[str, dict] = {}
    for f in findings:
        code = f.get("error_id") or "UNKNOWN"
        entry = grouped.get(code)
        if entry is None:
            if len(grouped) >= MAX_WARNING_CODES:
                continue
            entry = grouped[code] = {
                "code": code,
                "severity": f.get("severity") or "warning",
                "layer": f.get("layer") or "",
                "count": 0,
                "samples": [],
            }
        entry["count"] += 1
        # An error anywhere in the group outranks a warning.
        if f.get("severity") == "error":
            entry["severity"] = "error"
        if len(entry["samples"]) < MAX_WARNING_SAMPLES:
            entry["samples"].append({
                "message": f.get("message") or "",
                "context": f.get("context") or {},
                "suggestions": (f.get("suggestions") or [])[:3],
            })

    # `action` is the stable code the UI renders Spanish from; `description` is
    # the engine's English and survives only as the fallback for an action with
    # no copy yet. `n_skus` is what the sentence needs to name a number, and the
    # SKU list itself is dropped — it can run to thousands.
    #
    # `n_skus` prefers the producer's OWN count over the length of the sample
    # list. Reports that carry both (censored demand, for one) truncate
    # `skus_affected` to the first 20 for payload size while still stating the
    # real total, so measuring the truncated list would tell a tenant whose
    # whole catalogue was touched that 20 SKUs were.
    #
    # The counts a correction chose to publish travel too. A sentence that can
    # say "47 buckets, +312 units" is a different thing to a buyer than one that
    # can only say "we adjusted some numbers" — and for a correction that
    # REWRITES the user's own sales figures, the difference is whether they can
    # reconcile our output against their file at all.
    _COUNT_KEYS = ("n_recovered", "n_flagged", "units_recovered", "n_rows", "n_periods")
    mapped = [
        {
            "action": c.get("action") or "",
            "description": c.get("description") or "",
            "n_skus": (
                c.get("n_skus_affected")
                if c.get("n_skus_affected") is not None
                else len(c.get("skus_affected") or [])
            ),
            **{k: c[k] for k in _COUNT_KEYS if c.get(k) is not None},
        }
        for c in (raw.get("corrections") or [])
    ]
    # Corrections that CHANGED THE USER'S NUMBERS come first, so the cap can
    # only ever drop the cosmetic ones. A correction reporting how many values
    # it rewrote is by definition one that rewrote values; censored-demand
    # recovery is appended last by the pipeline, and on a run with twenty other
    # corrections the truncation would have silently discarded the single most
    # consequential thing the engine did to the data.
    mapped.sort(key=lambda c: 0 if any(k in c for k in _COUNT_KEYS) else 1)
    if len(mapped) > MAX_WARNING_CODES:
        log.warning(
            "Truncating %d run corrections to %d for the payload; dropped: %s",
            len(mapped), MAX_WARNING_CODES,
            [c["action"] for c in mapped[MAX_WARNING_CODES:]],
        )
    corrections = mapped[:MAX_WARNING_CODES]

    ordered = sorted(
        grouped.values(),
        key=lambda e: (0 if e["severity"] == "error" else 1, -e["count"]),
    )
    return {"validation": ordered, "corrections": corrections}


# ── Progress helpers ───────────────────────────────────────────────────────

def _emit(tenant_id: str, session_id: str, job_id: str, percent: int, step: str, message: str):
    progress = {"percent": percent, "step": step, "message": message}
    update_progress(tenant_id, job_id, progress)
    broadcaster.broadcast_sync(job_id, {"type": "progress", "job_id": job_id, **progress})
    session_store.append_log(
        tenant_id, session_id, job_id,
        f"[{datetime.now(timezone.utc).isoformat()}] [{step}] {message}",
    )


# ── Main training entry point ──────────────────────────────────────────────

def run_training_job(tenant_id: str, session_id: str, job_id: str) -> None:
    log.info(f"Runner starting job={job_id} session={session_id} tenant={tenant_id}")
    # Stress-test shim: when MOCK_TRAINING=1 (only honored under testing_mode), skip the
    # heavy ML and simulate a fast job so the queue/worker/concurrency machinery can be
    # saturated without waiting on real LightGBM/XGBoost/Prophet fits. Default off.
    from backend.config import settings as _settings
    if _settings.testing_mode and os.getenv("MOCK_TRAINING") == "1":
        _time.sleep(random.uniform(0.05, 0.25))
        mark_completed(tenant_id, job_id)
        try:
            force_status(tenant_id, session_id, "COMPLETED", "results")
        except Exception:
            pass
        log.info(f"[MOCK_TRAINING] job {job_id} completed (no ML)")
        return
    try:
        _emit(tenant_id, session_id, job_id, 5, "init", "Building engine config...")
        config = build_engine_config(tenant_id, session_id)

        _emit(tenant_id, session_id, job_id, 10, "load", "Loading dataset...")
        from forecasting_core.engine import ForecastEngine
        engine = ForecastEngine.from_dict(config)
        engine.load_data(config["data"]["path"])

        gap_fill = config.pop("_gap_fill", "leave")
        outlier_cfg = config.pop("_outlier_config", {})
        schema_version = config.pop("_schema_version", "legacy")
        canonical_mapping = config.pop("_canonical_mapping", {})

        remediations = config.pop("_remediations", {}) or {}

        col_cfg = config["columns"]

        # Neutralize infinities BEFORE gap fill and outlier treatment. A
        # corrupted quantity ("1e309", an overflowing formula) parses as a
        # valid float(inf) that is neither NaN nor an outlier, so it survives
        # every later step and turns that SKU's metrics into inf/NaN. Turning
        # it into NaN hands it to the missing-data machinery that already
        # exists instead of inventing a second policy for it. The validator
        # reports the count; the pipeline runs in WARNING mode and only logs,
        # so the data has to be made safe here.
        prep_notes: list = []
        # Checked here, not in family_service: the fan-out decides the grain
        # before any job exists, and this is the first point that both owns a
        # findings channel and knows what this session actually trained at.
        _note_granularity_fallback(
            (session_store.get_field(tenant_id, session_id, "forecast_cfg") or {})
            .get("user_granularity"),
            (get_session(tenant_id, session_id) or {}).get("granularity"),
            prep_notes,
        )
        if engine._df is not None:
            date_col, target_col = col_cfg["date"], col_cfg["target"]
            group_cols = _group_cols(col_cfg)   # EVERY series key — see _group_cols.

            # ── The gate's answers, applied in the only order that is coherent.
            # Dates are settled before anything reads them; the quantity column
            # is made numeric before anything measures it; identity is unified
            # before anything groups by it; and `net` runs before the duplicates
            # policy because netting a return against a day's sales only exists
            # while that day's rows are still separate.

            # 1 — how to read an ambiguous date
            if remediations.get("ambiguous_date_format") in (
                    "date_format_day_first", "date_format_month_first"):
                engine._df = _apply_date_format(
                    engine._df, date_col,
                    dayfirst=remediations["ambiguous_date_format"] == "date_format_day_first",
                    notes=prep_notes,
                )
            # 2 — an Excel serial column the user confirmed is a date
            if remediations.get("excel_serial_dates") == "excel_serial_as_date":
                engine._df = _apply_excel_serial_dates(engine._df, date_col, prep_notes)
            # 3 — impossible dates
            if remediations.get("out_of_range_dates") == "out_of_range_dates_drop":
                engine._df = _drop_out_of_range_dates(engine._df, date_col, prep_notes)

            # 4 — how to read `1,234`. BEFORE the infinity guard: that one calls
            # pd.to_numeric and would turn every "10,5" into NaN, so the
            # quantities would be silently emptied rather than read.
            engine._df = _coerce_decimal_comma(
                engine._df, target_col=target_col, notes=prep_notes,
                convention={
                    "separator_comma_is_thousands": "thousands",
                    "separator_comma_is_decimal": "decimal",
                }.get(remediations.get("ambiguous_number_format")),
            )
            # 5 — text where a quantity belongs
            engine._df = _apply_non_numeric_policy(
                engine._df, target_col,
                mode={
                    "non_numeric_strip_symbols": "strip",
                    "non_numeric_as_zero": "zero",
                    "non_numeric_drop_rows": "drop",
                }.get(remediations.get("non_numeric_target"), ""),
                notes=prep_notes,
            )
            engine._df = _neutralize_infinities(
                engine._df, target_col=target_col, notes=prep_notes,
            )
            # 6 — empty quantity cells
            engine._df = _apply_null_target_policy(
                engine._df, target_col,
                mode={
                    "null_target_as_zero": "zero",
                    "null_target_drop_rows": "drop",
                }.get(remediations.get("null_target"), ""),
                notes=prep_notes,
            )
            # 7 — `SKU-1` vs `sku-1 `
            if remediations.get("inconsistent_sku_identity") == "sku_identity_unify":
                engine._df = _unify_group_values(engine._df, group_cols, prep_notes)
            # 8 — returns booked as negative sales
            engine._df = _apply_negative_policy(
                engine._df, date_col, target_col, group_cols,
                mode={
                    "negatives_net_into_period": "net",
                    "negatives_as_zero": "zero",
                    "negatives_drop_rows": "drop",
                }.get(remediations.get("negative_target"), ""),
                notes=prep_notes,
            )
            # 9 — one row per sale, or one row per day. Summing stays the
            # default for sessions configured before the gate existed: it is
            # what they already ran with, and changing it silently would be the
            # exact defect this whole change is against.
            duplicates_choice = remediations.get("duplicates") or "duplicates_sum"
            if duplicates_choice == "duplicates_keep_last":
                engine._df = _dedupe_keep_last(
                    engine._df, date_col, group_cols, prep_notes)
            else:
                engine._df = _collapse_duplicate_periods(
                    engine._df, date_col=date_col, target_col=target_col,
                    group_cols=group_cols, notes=prep_notes,
                )
            # 10 — a running month-to-date total
            if remediations.get("cumulative_demand") == "cumulative_to_periodic":
                engine._df = _decumulate(
                    engine._df, date_col, target_col, group_cols, prep_notes)

        # For canonical_v1 sessions: enrich the DataFrame with canonical column
        # aliases + defaults (adds 'sku', 'date', 'demand', 'store', etc.).
        # AFTER the remediations, not before: an alias is a COPY of the mapped
        # column, so building it first would hand the inventory sync and every
        # canonical consumer the quantities the user just chose to correct.
        if schema_version == "canonical_v1" and canonical_mapping and engine._df is not None:
            try:
                from forecasting_core.data.canonical import apply_canonical_defaults
                engine._df = apply_canonical_defaults(engine._df, canonical_mapping)
                log.info("Applied canonical defaults to dataset (canonical_v1 session)")
            except Exception as e:
                log.warning(f"Canonical defaults application failed (non-fatal): {e}")

        # 11 — calendar gaps. The gate's answer wins over the wizard's dropdown
        # when the user was actually asked; otherwise the session's own setting
        # stands.
        gap_fill = _GAP_FILL_BY_OPTION.get(
            remediations.get("dataset_wide_gap"), gap_fill)
        if gap_fill and gap_fill != "leave" and engine._df is not None:
            _emit(tenant_id, session_id, job_id, 14, "gap_fill",
                  f"Filling missing dates (strategy: {gap_fill})...")
            engine._df = _apply_gap_fill(
                engine._df,
                date_col=col_cfg["date"],
                target_col=col_cfg["target"],
                group_cols=_group_cols(col_cfg),
                strategy=gap_fill,
                notes=prep_notes,
            )

        # 12 — extreme values. Advisory at the gate, so this only fires when the
        # user actively picked something.
        if remediations.get("outliers") in _OUTLIER_STRATEGY_BY_OPTION:
            outlier_cfg = {
                **(outlier_cfg if isinstance(outlier_cfg, dict) else {}),
                "strategy": _OUTLIER_STRATEGY_BY_OPTION[remediations["outliers"]],
            }
        strategy = outlier_cfg.get("strategy", "leave") if isinstance(outlier_cfg, dict) else "leave"
        if strategy and strategy != "leave" and engine._df is not None:
            _emit(tenant_id, session_id, job_id, 16, "outliers",
                  f"Applying outlier treatment (strategy: {strategy})...")
            engine._df = _apply_outlier_treatment(
                engine._df,
                date_col=col_cfg["date"],
                target_col=col_cfg["target"],
                group_col=_primary_group_col(col_cfg),
                outlier_cfg=outlier_cfg,
                notes=prep_notes,
            )

        if engine._df is not None:
            try:
                from backend.inventory.service import sync_stock_from_dataset
                n_synced = sync_stock_from_dataset(
                    tenant_id, engine._df,
                    group_col=_primary_group_col(col_cfg),
                    date_col=col_cfg["date"],
                    # Without the mapping the sync cannot tell an inventory
                    # column the user mapped from the 0 that
                    # apply_canonical_defaults broadcasts into every session.
                    canonical_mapping=canonical_mapping,
                )
                if n_synced:
                    log.info(f"Synced inventory stock for {n_synced} SKU(s) from uploaded dataset")
            except HTTPException:
                # A plan-limit breach (e.g. max_skus) must fail the job with a
                # clear reason — swallowing it here as "non-fatal" like a real
                # sync bug would report the job as a silent success while
                # quietly capping how many SKUs got synced. Falls through to
                # the outer except below, which calls mark_failed with this
                # exception's message and marks the session FAILED.
                raise
            except Exception as e:
                log.warning(f"Inventory stock sync failed (non-fatal): {e}")

        _emit(tenant_id, session_id, job_id, 20, "inspect", "Running data quality check...")
        dq_report = engine.get_data_quality_report()

        _emit(tenant_id, session_id, job_id, 30, "routing", "Computing model routing...")
        routing = engine.get_routing_plan()

        _emit(tenant_id, session_id, job_id, 40, "training", "Training models (this may take a while)...")
        engine.train()

        _emit(tenant_id, session_id, job_id, 85, "results", "Collecting metrics...")
        metrics = engine.get_metrics()

        # Not one SKU-model pair survived. That is a DATA problem (series too
        # short, all-zero demand, one row per SKU), not an engine crash, so it
        # must fail with something the user can act on — this used to escape as
        # a raw KeyError('model') rendered verbatim as "El entrenamiento falló:
        # 'model'".
        if not metrics.get("rows"):
            raise TrainingDataError("no_models_trained")

        inventory = engine.get_inventory_report()

        _emit(tenant_id, session_id, job_id, 90, "saving", "Saving results...")
        result_payload = {
            "job_id": job_id,
            "run_id": engine._run_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "inventory": inventory,
            # Cumulative demand-uncertainty bands per SKU. The reorder point
            # reads these instead of rebuilding a sigma and re-applying z; empty
            # for runs whose champion models cannot supply them, and the
            # consumer falls back on its own.
            "demand_risk": engine.get_demand_risk(),
            # Fill rate, stockouts and inventory the forecast would have
            # produced, simulated over real past demand against a naive-forecast
            # policy. The metric a distributor can check against experience,
            # unlike WAPE.
            "policy_backtest": engine.get_policy_backtest(),
            "routing": routing,
            "data_quality": dq_report,
            "warnings": _collect_run_warnings(engine, prep_notes),
            "config": config,
        }
        session_store.set_training_result(tenant_id, session_id, result_payload)

        _emit(tenant_id, session_id, job_id, 93, "forecast", "Generating forecast series...")
        forecasts: dict = {}
        try:
            forecasts = _generate_forecast_series(engine, config)
            if forecasts:
                session_store.set_forecasts(tenant_id, session_id, forecasts)
                log.info(f"Saved forecast series for {len(forecasts)} SKUs")
            else:
                log.info("No forecast data available to save")
        except Exception as e:
            log.warning(f"Forecast series generation failed (non-fatal): {e}")

        # Transparency: record uploaded SKUs that did NOT make it into the forecast
        # (e.g. dropped for insufficient history) so the UI can surface them instead
        # of letting them silently vanish from the inventory view.
        try:
            excluded = _compute_excluded_skus(
                engine._df, _primary_group_col(col_cfg), forecasts,
                min_history=int(config.get("training", {}).get("min_history", 20)),
            )
            if excluded:
                result_payload["excluded_skus"] = excluded
                session_store.set_training_result(tenant_id, session_id, result_payload)
                log.info(f"Recorded {len(excluded)} excluded SKU(s) for session {session_id}")
        except Exception as e:
            log.warning(f"Excluded-SKU computation failed (non-fatal): {e}")

        _emit(tenant_id, session_id, job_id, 96, "indexing", "Indexing session for AI analyst...")
        try:
            from backend.ai import rag as _rag
            from backend.training.job_service import get_job as _get_job
            _job       = _get_job(tenant_id, job_id) or {}
            _user_id   = _job.get("created_by") or "system"
            _sess      = get_session(tenant_id, session_id) or {}
            _sess_name = _sess.get("name") or session_id
            inspection = session_store.get_field(tenant_id, session_id, "inspection") or {}
            n = _rag.index_session(
                tenant_id=tenant_id,
                session_id=session_id,
                result=result_payload,
                inspection=inspection,
                forecasts=forecasts,
                user_id=_user_id,
                session_name=_sess_name,
            )
            log.info(f"RAG: indexed {n} vectors for session {session_id} user {_user_id}")
        except Exception as e:
            log.warning(f"RAG indexing failed (non-fatal): {e}")

        # Save binary artifacts to disk
        from backend.storage import paths
        artifact_path = paths.artifacts_dir(tenant_id, session_id)
        artifact_path.mkdir(parents=True, exist_ok=True)
        try:
            engine.export_config(str(artifact_path / "session_config.json"))
        except Exception as e:
            log.warning(f"Config export failed: {e}")

        mark_completed(tenant_id, job_id)
        force_status(tenant_id, session_id, "COMPLETED", "results")
        fire_webhooks(tenant_id, "job.completed", {"job_id": job_id, "session_id": session_id})
        broadcaster.broadcast_sync(job_id, {"type": "completed", "job_id": job_id})
        log.info(f"Job {job_id} completed successfully")

    except Exception as exc:
        error_msg = str(exc)
        log.error(f"Job {job_id} failed: {error_msg}", exc_info=True)
        mark_failed(tenant_id, job_id, error_msg)
        force_status(tenant_id, session_id, "FAILED")
        fire_webhooks(tenant_id, "job.failed", {"job_id": job_id, "session_id": session_id, "error": error_msg})
        broadcaster.broadcast_sync(job_id, {
            "type": "failed",
            "job_id": job_id,
            "error": error_msg,
        })
