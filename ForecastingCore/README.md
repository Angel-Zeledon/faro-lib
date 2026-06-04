# faro-core

Enterprise-grade multi-SKU time-series forecasting engine. Train and compare multiple models (LightGBM, XGBoost, Prophet, ARIMA, ETS, SARIMAX) per SKU/group with automatic feature engineering and walk-forward validation.

## Installation

```bash
pip install faro-core
```

## Quick Start

```python
from forecasting_core import ForecastEngine

engine = (
    ForecastEngine()
    .load_data("sales.csv")
    .choose_columns(target="sales", date="date", sku="item_id")
    .configure_features(lags=[1, 7, 14], rolling=[7, 14, 28], calendar=True)
    .configure_training(walk_forward=True, wfv_splits=3)
    .configure_forecast(horizon=14)
    .select_models(["lightgbm", "prophet", "ets"])
    .train()
)

print(engine.get_metrics())
forecast = engine.predict(horizon=14)
```

## From Config File

```python
engine = ForecastEngine.from_config("session_config.json")
engine.train()
report = engine.generate_report()
```

## Features

- Multi-model training per SKU: LightGBM, XGBoost, Prophet, ARIMA, ETS, SARIMAX
- Walk-forward validation with configurable splits
- Automatic feature engineering: lags, rolling stats, EWM, calendar features
- Colombia-specific holiday distances (Easter, Christmas)
- Model registry and ensemble support
- Inventory optimization: service level, safety stock
- Data drift monitoring
- Hyperparameter tuning

## License

MIT
