"""
Quickstart example — programmatic usage of forecasting_core.

Install:
    pip install -e .

Run:
    python docs/examples/quickstart.py
"""

from forecasting_core import ForecastEngine

# Option A: Fluent chaining
engine = (
    ForecastEngine()
    .load_data("sales.csv")
    .choose_columns(target="sales", date="date", sku="item_id")
    .configure_features(lags=[1, 7, 14], rolling=[7, 14, 28], calendar=True)
    .configure_training(walk_forward=True, wfv_splits=3)
    .configure_forecast(horizon=14)
    .configure_business(service_level=0.95, lead_time_days=7)
    .select_models(["lightgbm", "prophet", "ets"])
    .train()
)

print(engine.get_metrics())
print(engine.get_inventory_report())

# Option B: From config file
engine2 = ForecastEngine.from_config("session_config.json")
engine2.train()
report = engine2.generate_report()
print(report["run_id"])

# Inspect dataset before training
engine3 = ForecastEngine()
engine3.load_data("sales.csv")
profile = engine3.get_profile()             # column types, stats, recommendations
columns = engine3.get_column_options()      # dropdown data for each column role
schema  = engine3.get_config_schema()       # all configurable parameters
models  = engine3.get_available_models()    # all supported model names

print(profile["recommended"])   # auto-detected date/target/group
print(columns)
print(models)
