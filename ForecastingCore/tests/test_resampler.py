import pandas as pd
from forecasting_core.data.resampler import resample_to_frequency


def _dates(start, periods, freq):
    return list(pd.date_range(start, periods=periods, freq=freq))


class TestResampleToFrequency:
    def test_daily_to_weekly_sums_within_bucket(self):
        rows = []
        for d in _dates("2024-01-01", 14, "D"):  # 2 full weeks
            rows.append((d, "SKU1", 10.0))
        df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
        out = resample_to_frequency(df, "date", "sku", "sales", "W")
        assert len(out) == 2  # 14 daily rows -> 2 weekly buckets
        assert set(out["sales"]) == {70.0}  # 7 days * 10 per bucket

    def test_preserves_per_group_separation(self):
        rows = []
        for d in _dates("2024-01-01", 7, "D"):
            rows.append((d, "A", 5.0))
        for d in _dates("2024-01-01", 7, "D"):
            rows.append((d, "B", 100.0))
        df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
        out = resample_to_frequency(df, "date", "sku", "sales", "W")
        by_sku = dict(zip(out["sku"], out["sales"]))
        assert by_sku["A"] == 35.0
        assert by_sku["B"] == 700.0

    def test_single_series_no_group_col(self):
        rows = [(d, 3.0) for d in _dates("2024-01-01", 14, "D")]
        df = pd.DataFrame(rows, columns=["date", "sales"])
        out = resample_to_frequency(df, "date", None, "sales", "W")
        assert len(out) == 2
        assert set(out["sales"]) == {21.0}

    def test_output_has_only_date_group_target_columns(self):
        df = pd.DataFrame(
            [("2024-01-01", "A", 5.0, "extra_col_value")],
            columns=["date", "sku", "sales", "notes"],
        )
        out = resample_to_frequency(df, "date", "sku", "sales", "D")
        assert set(out.columns) == {"date", "sku", "sales"}

    def test_monthly_aggregation(self):
        rows = []
        for d in _dates("2024-01-01", 6, "W"):  # 6 weeks
            rows.append((d, "SKU1", 20.0))
        df = pd.DataFrame(rows, columns=["date", "sku", "sales"])
        out = resample_to_frequency(df, "date", "sku", "sales", "MS")
        assert len(out) >= 1
        assert out["sales"].sum() == 120.0  # total demand conserved across the re-aggregation
