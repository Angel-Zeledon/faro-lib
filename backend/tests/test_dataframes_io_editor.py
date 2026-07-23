"""Unit tests for the editor read/write boundary helpers."""
import pytest

from backend.dataframes.io import read_table, write_rows


@pytest.mark.offline
def test_write_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "data.csv")
    write_rows(path, ["sku", "qty"], [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}])
    out = read_table(path)
    assert out["columns"] == ["sku", "qty"]
    assert out["rows"] == [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]


@pytest.mark.offline
def test_write_preserves_column_order_and_zero_rows(tmp_path):
    path = str(tmp_path / "empty.csv")
    write_rows(path, ["b", "a"], [])
    text = open(path, encoding="utf-8").read().splitlines()
    assert text[0] == "b,a"          # header preserved, order follows columns
    assert len(text) == 1            # header only, no data rows
    out = read_table(path)
    assert out["columns"] == ["b", "a"]
    assert out["rows"] == []


@pytest.mark.offline
def test_write_only_declared_columns(tmp_path):
    path = str(tmp_path / "sel.csv")
    # rows may carry extra keys; only declared columns are written, in order
    write_rows(path, ["a"], [{"a": 1, "extra": 9}])
    out = read_table(path)
    assert out["columns"] == ["a"]
    assert out["rows"] == [{"a": 1}]
