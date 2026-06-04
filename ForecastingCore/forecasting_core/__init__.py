"""
forecasting_core — Enterprise-grade multi-SKU forecasting library.

Usage:
    from forecasting_core import ForecastEngine

    engine = ForecastEngine()
    engine.load_data("sales.csv")
    engine.choose_columns(target="sales", date="date", sku="sku")
    engine.configure_features(lags=[1, 7, 14], rolling=[7, 14])
    engine.train(models=["lightgbm", "prophet"])
    forecast = engine.predict(horizon=14)
    report   = engine.generate_report()
"""

from forecasting_core.engine import ForecastEngine
from forecasting_core.pipelines.pipeline import Pipeline, PipelineStatus
from forecasting_core.analysis.analyzer import TimeSeriesAnalyzer
from forecasting_core.data.transform import DataTransformer
from forecasting_core.scenarios.scenario import ScenarioEngine, ScenarioRule
from forecasting_core.config.config import TransformConfig

__version__ = "1.0.0"
__all__ = [
    "ForecastEngine",
    "Pipeline",
    "PipelineStatus",
    "TimeSeriesAnalyzer",
    "DataTransformer",
    "ScenarioEngine",
    "ScenarioRule",
    "TransformConfig",
]
