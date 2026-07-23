"""Boundary package: plain-Python I/O over pandas (pandas-boundary refactor)."""

import math


def _csv_bytes():
    return b"sku,fecha,cantidad\nA,2026-01-01,5\nB,2026-01-02,\n"


class TestReadRows:
    def test_read_rows_from_csv_bytes(self):
        from backend.dataframes.io import read_rows
        rows = read_rows(_csv_bytes(), fmt="csv")
        assert rows[0] == {"sku": "A", "fecha": "2026-01-01", "cantidad": 5}
        # Empty numeric cell -> None (not NaN), and no numpy types leak.
        assert rows[1]["cantidad"] is None
        assert all(not (isinstance(v, float) and math.isnan(v))
                   for r in rows for v in r.values())

    def test_read_rows_from_path_infers_csv(self, tmp_path):
        from backend.dataframes.io import read_rows
        p = tmp_path / "d.csv"
        p.write_bytes(_csv_bytes())
        rows = read_rows(str(p))
        assert len(rows) == 2 and rows[0]["sku"] == "A"

    def test_read_rows_nrows_limit(self):
        from backend.dataframes.io import read_rows
        rows = read_rows(_csv_bytes(), fmt="csv", nrows=1)
        assert len(rows) == 1

    def test_read_columns_subset(self, tmp_path):
        from backend.dataframes.io import read_columns
        p = tmp_path / "d.csv"
        p.write_bytes(_csv_bytes())
        rows = read_columns(str(p), ["fecha"])
        assert rows[0] == {"fecha": "2026-01-01"}
        assert "sku" not in rows[0]


class TestDatasetPreview:
    def test_preview_csv(self, tmp_path):
        from backend.dataframes.io import dataset_preview
        p = tmp_path / "d.csv"
        p.write_bytes(b"sku,cantidad\nA,5\nB,7\nC,9\n")
        out = dataset_preview(str(p), rows=2)
        assert out["columns"] == ["sku", "cantidad"]
        assert len(out["rows"]) == 2               # limited to `rows`
        assert out["rows"][0] == {"sku": "A", "cantidad": 5}
        assert out["sheets"] is None
        assert out["total_rows"] == 3              # full count, not the preview slice


class TestSeries:
    def _write(self, tmp_path):
        p = tmp_path / "s.csv"
        p.write_bytes(
            b"sku,fecha,ventas\n"
            b"A,2026-01-02,5\nA,2026-01-01,3\nB,2026-01-01,9\n")
        return str(p)

    def test_historical_series_sorted_and_filtered(self, tmp_path):
        from backend.dataframes.series import historical_series
        out = historical_series(self._write(tmp_path), "fecha", "ventas", "sku", sku="A")
        assert out == [{"date": "2026-01-01", "value": 3.0},
                       {"date": "2026-01-02", "value": 5.0}]

    def test_historical_series_all_skus_when_none(self, tmp_path):
        from backend.dataframes.series import historical_series
        out = historical_series(self._write(tmp_path), "fecha", "ventas", "sku", sku=None)
        assert len(out) == 3

    def test_filter_rows_by_date_inclusive(self):
        from backend.dataframes.series import filter_rows_by_date
        rows = [{"d": "2026-01-01"}, {"d": "2026-01-05"}, {"d": "bad"}]
        out = filter_rows_by_date(rows, "d", "2026-01-02", None)
        assert out == [{"d": "2026-01-05"}]   # in-range kept, unparseable dropped


class TestSeriesBridge:
    """DataFrame-bridge helpers for the ForecastingCore analysis path."""

    def test_normalize_date_column(self):
        from backend.dataframes.series import normalize_date_column
        rows = [{"fecha": "2026-01-01", "v": 1},
                {"fecha": "bad", "v": 2},
                {"fecha": "2026-01-02T10:00:00", "v": 3}]
        out = normalize_date_column(rows, "fecha")
        assert out == [{"fecha": "2026-01-01", "v": 1},
                       {"fecha": "2026-01-02", "v": 3}]  # unparseable dropped, normalized

    def test_filter_dataframe_by_date_returns_dataframe(self):
        import pandas as pd
        from backend.dataframes.series import filter_dataframe_by_date
        df = pd.DataFrame({"fecha": ["2026-01-01", "2026-01-05", "bad"],
                           "v": [1, 2, 3]})
        out = filter_dataframe_by_date(df, "fecha", "2026-01-02", None)
        assert isinstance(out, pd.DataFrame)
        assert list(out["v"]) == [2]                       # in-range kept, NaT dropped
        assert str(out["fecha"].dtype).startswith("datetime")

    def test_dataframe_series_filtered_and_sorted(self):
        import pandas as pd
        from backend.dataframes.series import dataframe_series
        df = pd.DataFrame({"sku": ["A", "A", "B"],
                           "fecha": ["2026-01-02", "2026-01-01", "2026-01-01"],
                           "ventas": [5.0, 3.0, None]})
        out = dataframe_series(df, "fecha", "ventas", "sku", "A")
        assert out == [{"date": "2026-01-01", "value": 3.0},
                       {"date": "2026-01-02", "value": 5.0}]

    def test_detect_outliers_tukey(self):
        from backend.dataframes.series import detect_outliers
        series = [{"date": f"2026-01-{i:02d}", "value": float(v)}
                  for i, v in enumerate([10, 11, 9, 10, 12, 11, 500], start=1)]
        out = detect_outliers(series)
        assert len(out) == 1
        assert out[0]["value"] == 500.0
        assert out[0]["date"] == "2026-01-07"


class TestStockExtract:
    def test_last_row_per_group(self):
        import pandas as pd
        from backend.dataframes.stock import last_row_per_group
        df = pd.DataFrame({
            "sku":   ["A", "A", "B"],
            "fecha": ["2026-01-01", "2026-01-03", "2026-01-02"],
            "current_stock": [5, 9, 7],
            "moq": [1, 1, None],
        })
        out = dict(last_row_per_group(df, "sku", "fecha", ["current_stock", "moq"]))
        assert out["A"] == {"current_stock": 9, "moq": 1}   # latest A row
        assert out["B"] == {"current_stock": 7}             # NaN moq dropped

    def test_last_row_per_group_empty(self):
        import pandas as pd
        from backend.dataframes.stock import last_row_per_group
        assert last_row_per_group(pd.DataFrame(), "sku", "fecha", ["current_stock"]) == []


class TestAnalysis:
    def test_analyze_dataset_basic(self, tmp_path):
        from backend.dataframes.analysis import analyze_dataset
        p = tmp_path / "a.csv"
        # 40 daily-ish rows -> columns classified; temporal key present.
        rows = ["sku,fecha,ventas"]
        for i in range(40):
            rows.append(f"A,2026-01-{(i % 28) + 1:02d},{i}")
        p.write_text("\n".join(rows))
        out = analyze_dataset(str(p))
        assert "columns" in out and "temporal" in out
        names = {c["name"] for c in out["columns"]}
        assert {"sku", "fecha", "ventas"} <= names
        # ventas is numeric, sku categorical
        role = {c["name"]: c["role"] for c in out["columns"]}
        assert role["ventas"] == "numeric" and role["sku"] == "categorical"

    def test_analyze_dataset_temporal_with_date_col(self, tmp_path):
        from backend.dataframes.analysis import analyze_dataset
        p = tmp_path / "a.csv"
        rows = ["sku,fecha,ventas"]
        for i in range(40):
            rows.append(f"A,2026-01-{(i % 28) + 1:02d},{i}")
        p.write_text("\n".join(rows))
        out = analyze_dataset(str(p), dt_col="fecha", target_col="ventas",
                              group_col="sku", freq_label="daily")
        assert out["temporal"]["freq_label"] == "daily"
        assert out["temporal"]["date_min"] <= out["temporal"]["date_max"]

    def test_accuracy_actuals(self, tmp_path):
        from backend.dataframes.analysis import accuracy_actuals
        p = tmp_path / "b.csv"
        p.write_text("fecha,ventas\n2026-01-01,5\n2026-01-02,\n")
        out = accuracy_actuals(str(p), "fecha", "ventas")
        assert out[0] == {"date": "2026-01-01", "value": 5.0}
        assert out[1]["value"] is None

    def test_read_error_message_classifies(self):
        import pandas as pd
        from backend.dataframes.analysis import read_error_message
        assert read_error_message(pd.errors.EmptyDataError("x")) == "empty"
        assert read_error_message(pd.errors.ParserError("x")) == "parser"
        assert read_error_message(ValueError("x")) is None


class TestDataframeIO:
    def test_read_dataframe_returns_dataframe(self, tmp_path):
        import pandas as pd
        from backend.dataframes.io import read_dataframe
        p = tmp_path / "d.csv"
        p.write_bytes(b"sku,ventas\nA,5\nB,7\n")
        df = read_dataframe(str(p))
        assert isinstance(df, pd.DataFrame)
        assert list(df["sku"]) == ["A", "B"]

    def test_dataframe_from_records(self):
        import pandas as pd
        from backend.dataframes.io import dataframe_from_records
        df = dataframe_from_records([("A", 5), ("B", 7)], ["sku", "ventas"])
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["sku", "ventas"]
        assert list(df["ventas"]) == [5, 7]
