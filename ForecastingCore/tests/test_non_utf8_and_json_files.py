"""Two file shapes the loader refused that the rest of the product accepts.

1. A cp1252 sales export — the Windows/Spanish-locale default. `utf-8-sig` was
   pinned, so training died with `UnicodeDecodeError` on the first accented
   byte. The failure named the file, not the encoding.
2. A `.json` dataset. It is in both upload allow-lists
   (`backend/datasets/service.py`, `backend/datasources/service.py`), is offered
   by the Archivos page's file picker, and is read/previewed/edited/written by
   `backend/dataframes/io.py`. This loader was the single place that refused it,
   so a JSON dataset uploaded fine, previewed fine, and then failed at training.
"""

import pandas as pd
import pytest

from forecasting_core.data.loader import DataLoader, LoadError

_ROWS = "sku,date,demand\nCamión,2025-01-01,5\nAzúcar,2025-01-02,7\n"


@pytest.fixture
def loader():
    return DataLoader()


class TestANonUtf8CsvNoLongerKillsTraining:
    def test_a_cp1252_export_loads_with_its_accents_intact(self, loader, tmp_path):
        path = tmp_path / "ventas.csv"
        path.write_bytes(_ROWS.encode("cp1252"))
        df = loader.load(str(path))
        assert list(df["sku"]) == ["Camión", "Azúcar"]

    def test_a_semicolon_cp1252_export_loads(self, loader, tmp_path):
        """The realistic file: Spanish Excel picks ';' AND cp1252 together, so
        the separator sniff has to survive bytes it cannot decode as UTF-8."""
        path = tmp_path / "excel_es.csv"
        path.write_bytes(
            "sku;date;demand\nCamión;2025-01-01;5\n".encode("cp1252"))
        df = loader.load(str(path))
        assert list(df.columns) == ["sku", "date", "demand"]
        assert list(df["sku"]) == ["Camión"]

    def test_a_utf8_file_is_still_read_as_utf8(self, loader, tmp_path):
        """A fallback chain's own risk: silently reinterpreting a CORRECT file
        into mojibake instead of failing. UTF-8 is attempted first for this."""
        path = tmp_path / "ventas_utf8.csv"
        path.write_bytes(_ROWS.encode("utf-8"))
        assert list(loader.load(str(path))["sku"]) == ["Camión", "Azúcar"]

    def test_cp1252_is_tried_before_latin1(self, loader, tmp_path):
        """Both fallbacks decode 'ó' the same way, so only a byte in 0x80-0x9F
        shows which ran: latin-1 turns this euro sign into U+0080."""
        path = tmp_path / "precios.csv"
        path.write_bytes("sku,date,price\nA,2025-01-01,€100\n".encode("cp1252"))
        assert loader.load(str(path))["price"].iloc[0] == "€100"


class TestAJsonDatasetReachesTraining:
    def test_a_json_dataset_loads(self, loader, tmp_path):
        path = tmp_path / "ventas.json"
        pd.DataFrame({
            "sku": ["A", "B"],
            "date": ["2025-01-01", "2025-01-02"],
            "demand": [5, 7],
        }).to_json(path, orient="records")

        df = loader.load(str(path))
        assert sorted(df.columns) == ["date", "demand", "sku"]
        assert len(df) == 2
        assert sorted(df["sku"]) == ["A", "B"]

    def test_json_is_written_and_read_back_by_the_same_orientation(
        self, loader, tmp_path,
    ):
        """`backend/dataframes/io.py::write_rows` persists edits with
        `orient="records"`. An edited JSON dataset has to survive a round trip
        back into training, which is the only reason the orientation matters."""
        path = tmp_path / "editado.json"
        pd.DataFrame({"sku": ["A"], "date": ["2025-01-01"], "demand": [5]}).to_json(
            path, orient="records")
        assert loader.load(str(path))["demand"].iloc[0] == 5

    def test_an_unsupported_format_is_still_rejected(self, loader, tmp_path):
        """Widening the allow-list must not turn it into 'anything goes'."""
        path = tmp_path / "notas.txt"
        path.write_text("hola", encoding="utf-8")
        with pytest.raises(LoadError, match="Unsupported format"):
            loader.load(str(path))
