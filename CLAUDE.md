# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A configuration-driven time-series forecasting engine that trains and compares multiple model types (ML, statistical, deep learning) per SKU/group. Built for demand/sales forecasting with Colombia-specific holiday features.

## Running the Application

```bash
# Install dependencies
pip install -r forecasting/requirements.txt

# Run the forecasting engine
cd forecasting
python main.py
```

Note: `main.py` line 104 hardcodes `"config.txt"` but the actual config file is `config.json`.

There are no tests, linters, or build steps configured in this project.

## Architecture

All source code lives under `forecasting/`. The entry point is `main.py` which orchestrates the `ForecastEngine` pipeline.

### Pipeline Flow

1. **Config** (`core/config.py`) - Loads `config.json`, validates required keys (`data`, `dt`, `target`, `group_id`, `models`, `features`)
2. **Data Loading** (`core/loader.py`) - Reads CSV/XLSX/Parquet/SQL based on file extension
3. **Feature Engineering** (`core/features/`) - Transforms raw data for ML models only (not used by ARIMA/Prophet)
4. **Model Training** - Three parallel tracks:
   - **ML models** (`core/models/models.py` + `core/trainer.py`) - LightGBM, XGBoost via factory pattern; trained per group with train/test split
   - **ARIMA** (`core/models/arima.py`) - Runs directly on raw data per group
   - **Prophet** (`core/models/prophet_model.py`) - Runs directly on raw data per group
5. **Results** - All model outputs are flattened into a DataFrame with `model`, `type`, `sku`, `mae` columns

### Key Design Decisions

- **Config is a session dict, not the Config object**: `ForecastEngine` passes session config dicts (from `config.json > sessions > session_1`) to components. Some components expect `config.get()` to work like a dict, while `Config` class wraps sessions differently. The `main.py` constructor passes the config path, but individual components like `DataLoader`, `Trainer`, and `ModelFactory` receive the Config object and call `.get()` on it directly - this only works if Config has a `get` method or is dict-like.
- **Feature engineering applies only to ML models**: ARIMA and Prophet receive the original DataFrame, not the feature-engineered one.
- **Per-group training**: The `Trainer` groups data by `group_id` (e.g., SKU) and trains separate models for each group.
- **ModelFactory handles ML models only**: LSTM config is stored but not instantiated as a model; LSTM has a separate module (`core/models/lstm.py`).

### Feature Engineering Modules (`core/features/`)

- `calendar.py` - Temporal features (year, month, DOW), cyclical sin/cos encodings, Colombia holiday distances (Easter, Christmas)
- `lags.py` - Lag features, differences, and percentage changes (configurable via `features.lags` in config)
- `rolling.py` - Rolling statistics (mean, std, min, max), trend via polynomial fit, volatility (configurable via `features.rolling` in config)
- `features.py` - Orchestrator that chains calendar, lag, and rolling transformations

### Evaluation Metrics (`core/evaluator.py`)

Provides `mae`, `rmse`, `wape`, `global_wape`, and `evaluate_all`. Note: `wape` is defined twice in the file (duplicate function).

## Configuration (`config.json`)

Config is structured as sessions. Required keys per session: `data`, `dt`, `target`, `group_id`, `models`, `features`. Key optional fields: `prediction_horizon`, `train_ratio` (default split ratio for train/test).

Models are configured as `{model_name: {hyperparameters}}`. Supported model names: `lightgbm`, `xgboost`, `lstm`.
