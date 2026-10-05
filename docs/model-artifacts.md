# Persisted models and re-forecasting

A full training now stores its models. A **re-forecast** then produces a fresh
forecast from newer sales using those stored models, without fitting anything.

## What is stored

One file per model family per session (`lightgbm`, `xgboost`, `global_lgbm`,
`arima`, `ets`, `croston`, `prophet`...) plus a `context` file (config, input
schema, metrics the champions were chosen from, demand-risk bands, training
window). Path: `storage/artifacts/<tenant>/<session>/models/<family>.<hash16>.json.gz`
(never in git). One row per file in `model_artifacts`: family, kind, version,
SHA-256 of the bytes, size, and metadata (hyperparameters, training window,
feature spec, engine and library versions, input schema hash).

A re-forecast session registers its parent's files under its own id
(`inherited_from_session_id`) instead of copying them. Archived sessions keep
their rows: sessions are permanent.

## Trust boundary

The format is gzip-compressed JSON. There is no pickle or joblib anywhere on the
path, so loading a file cannot execute code. LightGBM models are stored in
LightGBM's text format, XGBoost models in XGBoost's UBJSON, fitted statistical
parameters as numbers, arrays as exact base64.

Every file is hashed when written. The digest is recorded twice: in
`model_artifacts` and in the immutable `session_manifests` row of the training.
Before a re-forecast parses a file, the registry re-hashes the bytes and refuses
(`artifact_integrity_failed`) unless they match **both** records, and the engine
verifies again. Editing a file, or a file and its registry row together, is
therefore detected. What remains trusted is the database itself: whoever can
rewrite the immutable manifest and the row can substitute a model.

## What a re-forecast does per family

| Family | Behaviour |
|---|---|
| `lightgbm`, `xgboost` | Model restored, lag/rolling/EWM features rebuilt from the new history. No refit. |
| `global_lgbm` | Model and per-series scale fixed, origin feature row recomputed. No refit. Limited to the trained horizon. |
| `arima` | Fitted parameters applied to the new history. No refit. |
| `ets` | Smoothing parameters and initial states applied to the new history. No refit while the series starts where it did; otherwise refitted and flagged. |
| `croston` | No fitted state: re-run on the new history. |
| `prophet` | Cannot carry state: refitted per series, flagged `refit`. |
| `lstm`, `sarimax` | Not updatable: skipped, reason recorded. |

## What it refuses (stable codes)

`schema_incompatible`, `cadence_changed`, `history_older_than_models`,
`window_shifted_too_far` (default limit: the trained horizon, in buckets),
`unsupported_transforms`, `unsupported_hierarchy`, `nothing_to_forecast`; and
from the backend `artifacts_not_found`, `artifact_integrity_failed`.

## Scheduled retrain

A schedule's `retrain_mode` is `refit` (default, unchanged behaviour) or
`reforecast`. In `reforecast` mode a run with new data re-forecasts from the
stored models while the last full refit is younger than
`REFORECAST_FULL_REFIT_DAYS` (default 7), and refits otherwise, or when the
schedule's last re-forecast failed.
