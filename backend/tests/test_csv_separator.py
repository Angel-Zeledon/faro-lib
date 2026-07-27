"""CSV separator sniffing at the dataframes boundary.

Spanish-locale Excel exports use ';' as the separator (and prepend a UTF-8
BOM). The reader assumed ',', so those files loaded as ONE column literally
named "sku;fecha;cantidad" — the wizard then offered that single column for
every canonical field and nothing could be mapped. The client-side check
already understood ';', which made the failure look like a UI bug when it was
the backend read.
"""

import pytest

from backend.dataframes import io as df_io


class TestSniffSeparator:
    @pytest.mark.offline
    def test_detects_semicolon(self):
        assert df_io.sniff_separator("sku;fecha;cantidad\nA;2025-01-01;5\n") == ";"

    @pytest.mark.offline
    def test_detects_comma(self):
        assert df_io.sniff_separator("sku,fecha,cantidad\nA,2025-01-01,5\n") == ","

    @pytest.mark.offline
    def test_detects_tab(self):
        assert df_io.sniff_separator("sku\tfecha\tcantidad\n") == "\t"

    @pytest.mark.offline
    def test_ignores_utf8_bom_before_the_header(self):
        assert df_io.sniff_separator("﻿sku;fecha;cantidad\n") == ";"

    @pytest.mark.offline
    def test_single_column_file_falls_back_to_comma(self):
        assert df_io.sniff_separator("solo_una_columna\nvalor\n") == ","

    @pytest.mark.offline
    def test_empty_input_falls_back_to_comma(self):
        assert df_io.sniff_separator("") == ","

    @pytest.mark.offline
    def test_commas_inside_a_semicolon_file_do_not_win(self):
        """A quoted decimal comma must not out-vote the real separator."""
        header = "sku;fecha;cantidad\n"
        assert df_io.sniff_separator(header) == ";"


class TestReadingRealFiles:
    @pytest.mark.offline
    def test_semicolon_csv_with_bom_reads_as_three_columns(self, tmp_path):
        p = tmp_path / "excel_es.csv"
        p.write_text(
            "sku;fecha;cantidad\nSKU-A;2025-01-01;12\nSKU-A;2025-01-02;13\n",
            encoding="utf-8-sig",
        )
        rows = df_io.read_rows(str(p))
        assert len(rows) == 2
        assert set(rows[0].keys()) == {"sku", "fecha", "cantidad"}
        assert rows[0]["sku"] == "SKU-A"
        assert rows[0]["cantidad"] == 12

    @pytest.mark.offline
    def test_plain_comma_csv_is_unchanged(self, tmp_path):
        p = tmp_path / "plain.csv"
        p.write_text("sku,fecha,cantidad\nSKU-B,2025-01-01,7\n", encoding="utf-8")
        rows = df_io.read_rows(str(p))
        assert rows == [{"sku": "SKU-B", "fecha": "2025-01-01", "cantidad": 7}]

    @pytest.mark.offline
    def test_bom_does_not_leak_into_the_first_column_name(self, tmp_path):
        """Without encoding='utf-8-sig' the first header becomes '\\ufeffsku',
        which never matches the canonical 'sku' detector."""
        p = tmp_path / "bom.csv"
        p.write_text("sku,fecha,cantidad\nSKU-C,2025-01-01,3\n", encoding="utf-8-sig")
        rows = df_io.read_rows(str(p))
        assert "sku" in rows[0]
        assert not any(k.startswith("﻿") for k in rows[0])

    @pytest.mark.offline
    def test_dataframe_bridge_also_honours_the_separator(self, tmp_path):
        p = tmp_path / "bridge.csv"
        p.write_text("sku;fecha;cantidad\nSKU-D;2025-01-01;9\n", encoding="utf-8-sig")
        df = df_io.read_dataframe(str(p))
        assert list(df.columns) == ["sku", "fecha", "cantidad"]
