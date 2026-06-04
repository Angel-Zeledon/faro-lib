"""
forecastlib — A professional ML and forecasting data library.

Quick start
-----------
    from forecastlib.data import Loader

    ds = Loader.from_csv("sales.csv")
    ds = ds.select(target="sales", datetime="date", group="store")

    ds = (
        ds
        .clean.fix_datetime()
        .clean.drop_duplicates()
        .fill.smart()
        .categorical().encode.auto()
        .numeric().scale.standard()
        .target().lags([1, 7, 14])
        .target().rolling.mean([7, 30])
        .datetime().features.calendar()
    )

    pipeline = ds.to_pipeline()
    pipeline.save("pipeline.pkl")
"""

from forecastlib.data.loader import Loader
from forecastlib.data.dataset import Dataset
from forecastlib.data.schema import SchemaInfo
from forecastlib.pipeline.pipeline import Pipeline
from forecastlib.time_series.splitting import TimeSeriesSplitter

__version__ = "0.1.0"
__all__ = [
    "Loader",
    "Dataset",
    "SchemaInfo",
    "Pipeline",
    "TimeSeriesSplitter",
]
