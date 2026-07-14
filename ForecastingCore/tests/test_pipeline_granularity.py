import pandas as pd
from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline


def test_session_config_defaults_to_native_granularity():
    cfg = SessionConfig.from_dict({
        "name": "t",
        "columns": {"date": "date", "target": "sales", "group_keys": ["sku"]},
    })
    assert cfg.granularity.strategy == "native"
    assert cfg.granularity.target_freq is None


def test_session_config_reads_aggregate_granularity():
    cfg = SessionConfig.from_dict({
        "name": "t",
        "columns": {"date": "date", "target": "sales", "group_keys": ["sku"]},
        "granularity": {"strategy": "aggregate", "target_freq": "W"},
    })
    assert cfg.granularity.strategy == "aggregate"
    assert cfg.granularity.target_freq == "W"


def test_pipeline_resamples_when_strategy_is_aggregate():
    rows = []
    for d in pd.date_range("2024-01-01", periods=14, freq="D"):
        rows.append((d, "SKU1", 10.0))
    df = pd.DataFrame(rows, columns=["date", "sku", "sales"])

    cfg = SessionConfig.from_dict({
        "name": "t",
        "columns": {"date": "date", "target": "sales", "group_keys": ["sku"]},
        "granularity": {"strategy": "aggregate", "target_freq": "W"},
        "training": {"min_history": 1, "wfv_splits": 1, "walk_forward": False},
        "models": {"lightgbm": {}},
    })
    pipeline = Pipeline(cfg, df=df)
    resampled = pipeline._maybe_resample(df.copy())
    assert len(resampled) == 2  # 14 daily rows collapsed to 2 weekly buckets
    assert set(resampled["sales"]) == {70.0}


def test_pipeline_skips_resample_when_strategy_is_native():
    rows = [(d, "SKU1", 10.0) for d in pd.date_range("2024-01-01", periods=5, freq="D")]
    df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
    cfg = SessionConfig.from_dict({
        "name": "t",
        "columns": {"date": "date", "target": "sales", "group_keys": ["sku"]},
    })
    pipeline = Pipeline(cfg, df=df)
    result = pipeline._maybe_resample(df.copy())
    assert len(result) == 5  # untouched — native strategy is the default
