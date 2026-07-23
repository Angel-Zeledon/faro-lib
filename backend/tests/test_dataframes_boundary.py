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
