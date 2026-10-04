"""A sales CSV that is not UTF-8 must still load.

An ERP export from a Windows machine in a Spanish locale is cp1252, not UTF-8.
Every read on the sales path was pinned to ``utf-8-sig``, which raises
``UnicodeDecodeError`` on the first accented byte — so a whole year of history
was refused over one "Camión", and the error surfaced as a broken/unreadable
file rather than a wrong encoding. The stock importer had fallen back to
latin-1 since forever (``backend/api/v1/inventory.py::_decode_csv``); the sales
path, where most files actually enter the product, had not.

These tests read through the real boundary functions, not the helper alone,
because the defect was that the helper existed nowhere the sales files went.
"""

import pytest

from backend.dataframes import analysis as df_analysis
from backend.dataframes import io as df_io
from backend.dataframes import series as df_series

# 'ó' is 0xF3 in cp1252 AND latin-1 — enough to break utf-8, not enough to tell
# the two fallbacks apart. The euro sign is 0x80, which ONLY cp1252 maps to '€'
# (latin-1 decodes it to a control character), so it is what pins the order.
_ROWS = "sku,fecha,cantidad\nCamión,2025-01-01,5\nAzúcar,2025-01-02,7\n"


def _write(path, text: str, encoding: str) -> str:
    path.write_bytes(text.encode(encoding))
    return str(path)


@pytest.fixture
def cp1252_csv(tmp_path):
    return _write(tmp_path / "ventas.csv", _ROWS, "cp1252")


@pytest.fixture
def utf8_csv(tmp_path):
    return _write(tmp_path / "ventas_utf8.csv", _ROWS, "utf-8")


class TestANonUtf8ExportIsNoLongerRefused:
    @pytest.mark.offline
    def test_read_rows_reads_a_cp1252_file(self, cp1252_csv):
        rows = df_io.read_rows(cp1252_csv)
        assert [r["sku"] for r in rows] == ["Camión", "Azúcar"]

    @pytest.mark.offline
    def test_read_dataframe_reads_a_cp1252_file(self, cp1252_csv):
        df = df_io.read_dataframe(cp1252_csv)
        assert list(df["sku"]) == ["Camión", "Azúcar"]

    @pytest.mark.offline
    def test_read_table_reads_a_cp1252_file(self, cp1252_csv):
        table = df_io.read_table(cp1252_csv)
        assert [r["sku"] for r in table["rows"]] == ["Camión", "Azúcar"]

    @pytest.mark.offline
    def test_dataset_preview_reads_a_cp1252_file(self, cp1252_csv):
        preview = df_io.dataset_preview(cp1252_csv, rows=10)
        assert preview["columns"] == ["sku", "fecha", "cantidad"]
        assert preview["rows"][0]["sku"] == "Camión"

    @pytest.mark.offline
    def test_read_columns_reads_a_cp1252_file(self, cp1252_csv):
        rows = df_io.read_columns(cp1252_csv, ["sku"])
        assert [r["sku"] for r in rows] == ["Camión", "Azúcar"]

    @pytest.mark.offline
    def test_the_series_reader_reads_a_cp1252_file(self, cp1252_csv):
        """The chart path: `historical_series` had its own pinned read."""
        out = df_series.historical_series(
            cp1252_csv, date_col="fecha", target_col="cantidad", sku_col="sku",
            sku="Camión")
        assert out == [{"date": "2025-01-01", "value": 5.0}]

    @pytest.mark.offline
    def test_the_analysis_reader_reads_a_cp1252_file(self, cp1252_csv):
        """The analysis path had a third pinned read of its own."""
        assert df_analysis._read(cp1252_csv).shape == (2, 3)

    @pytest.mark.offline
    def test_a_semicolon_cp1252_export_works_too(self, tmp_path):
        """The realistic file: Spanish Excel picks ';' AND cp1252 at once.
        Separator sniffing has to survive the bytes it cannot decode."""
        path = _write(
            tmp_path / "excel_es.csv",
            "sku;fecha;cantidad\nCamión;2025-01-01;5\n", "cp1252")
        rows = df_io.read_rows(path)
        assert [r["sku"] for r in rows] == ["Camión"]
        assert rows[0]["cantidad"] == 5


class TestTheFallbackDoesNotCorruptWhatAlreadyWorked:
    @pytest.mark.offline
    def test_a_utf8_file_is_still_read_as_utf8(self, utf8_csv):
        """The risk of a fallback chain is reinterpreting a CORRECT file and
        returning mojibake instead of an error. UTF-8 is tried first for this."""
        rows = df_io.read_rows(utf8_csv)
        assert [r["sku"] for r in rows] == ["Camión", "Azúcar"]

    @pytest.mark.offline
    def test_a_bom_prefixed_utf8_file_is_still_read_without_the_bom(self, tmp_path):
        path = _write(tmp_path / "bom.csv", _ROWS, "utf-8-sig")
        rows = df_io.read_rows(path)
        assert list(rows[0].keys())[0] == "sku", "the BOM leaked into the header"

    @pytest.mark.offline
    def test_cp1252_is_tried_before_latin1(self, tmp_path):
        """Both fallbacks decode 'ó' identically, so only a byte in 0x80-0x9F
        proves which one ran. latin-1 would turn this euro sign into U+0080."""
        path = _write(
            tmp_path / "precios.csv",
            "sku,precio\nCamión,€100\n", "cp1252")
        rows = df_io.read_rows(path)
        assert rows[0]["precio"] == "€100"

    @pytest.mark.offline
    def test_an_empty_file_still_reports_as_empty_not_as_an_encoding_problem(
        self, tmp_path,
    ):
        """Only UnicodeDecodeError is retried. A file that is broken for another
        reason must keep surfacing as itself — the 422 copy depends on it."""
        import pandas as pd
        path = _write(tmp_path / "vacio.csv", "", "utf-8")
        with pytest.raises(pd.errors.EmptyDataError):
            df_io.read_rows(path)

    @pytest.mark.offline
    def test_a_malformed_file_still_reports_as_a_parser_error(self, tmp_path):
        import pandas as pd
        path = _write(
            tmp_path / "roto.csv",
            'sku,fecha\n"sin cerrar,2025-01-01,9,9,9\nB,2025-01-02\n', "utf-8")
        with pytest.raises(pd.errors.ParserError):
            df_io.read_rows(path, fmt="csv")
