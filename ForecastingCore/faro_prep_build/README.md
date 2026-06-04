# faro-prep

Data preprocessing and feature engineering library for time-series forecasting. Fluent chainable API for cleaning, encoding, scaling, and generating time-series features from pandas DataFrames.

## Installation

```bash
pip install faro-prep
```

## Quick Start

```python
from forecastlib.data import Loader

ds = (
    Loader.from_csv("sales.csv")
    .select(target="sales", datetime="date", group="store")
    .clean.fix_datetime()
    .fill.smart()
    .categorical().encode.auto()
    .numeric().exclude(["sales"]).scale.standard()
    .target().lags([1, 7, 14])
    .target().rolling.mean([7, 30])
    .datetime().features.calendar()
)

df = ds.to_dataframe()

pipeline = ds.to_pipeline()
pipeline.save("pipeline.pkl")

from forecastlib.pipeline import Pipeline
loaded = Pipeline.load("pipeline.pkl")
```

## Features

- Chainable fluent API on `Dataset` objects
- Smart missing value imputation (median, forward-fill, interpolation)
- Automatic categorical encoding: label, one-hot, ordinal
- Flexible scaling: standard, minmax, robust, log
- Time-series features: lags, rolling mean/std/min/max, EWM, diffs
- Calendar features with cyclical sin/cos encoding, Colombia holidays
- Train/test splitting with expanding window cross-validation
- Serializable preprocessing pipelines (save/load as `.pkl`)

## License

MIT
