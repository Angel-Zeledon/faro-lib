"""Chaos: the tabular boundary under real volume and real malice.

Every byte a customer owns enters Faro through `backend/dataframes/io.py`. The
existing suite exercises that path with files of five and ten rows, which proves
the parser is wired up and nothing else. It does not answer the two questions
production actually asks:

  1. **Does it hold under weight?** A distributor's three years of daily sales
     over 5.000 SKUs is millions of rows. The preview endpoint runs on every
     upload, in the request thread, before the user has agreed to anything — if
     it materializes the file, one upload takes the API down for everybody.
  2. **Does it fail gracefully when handed garbage?** The input is an export
     from somebody else's ERP, on a Windows box, in a Spanish locale, possibly
     hand-edited in Excel. It WILL contain NUL bytes, ragged rows, duplicated
     headers, mixed encodings and cells someone typed a formula into.

The bar these tests hold the code to is not "it works". It is: **bounded memory
regardless of file size, no silent truncation, and every malformed input either
parses or raises something the API layer can turn into a message.** A hang, a
`MemoryError`, or an exception nobody can classify are all failures here.

Marked `stress` because the fixtures write real multi-hundred-thousand-row
files. Run the fast half with `-m "not stress"`.
"""

import gc
import io
import time
import tracemalloc
from pathlib import Path

import pandas as pd
import pytest

from backend.dataframes.io import (
    dataset_preview, read_csv_any_encoding, read_dataframe, read_rows,
    sniff_separator,
)

# How big "big" is. 400k rows of three columns is ~11 MB on disk and roughly a
# year of daily sales for 1.100 SKUs — squarely inside what a real customer
# uploads, which is the point. A number nobody would ever hit proves nothing.
BIG_ROWS = 400_000

# Exceptions the API layer knows how to turn into a message. Anything outside
# this set reaches the user as "an unexpected error occurred" — which for a
# malformed upload is a lie about whose fault it is.
GRACEFUL = (
    ValueError,          # covers pandas' ParserError / EmptyDataError subclasses
    UnicodeDecodeError,
    TypeError,
    KeyError,
    ArithmeticError,
)


# ── Generators. Nothing is held in memory that the code under test would not ──

def _write_big_csv(path: Path, rows: int = BIG_ROWS) -> float:
    """A large but entirely well-formed sales file. Returns the demand checksum
    so a reader can be caught truncating instead of merely being slow."""
    checksum = 0.0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("sku,date,demand\n")
        for i in range(rows):
            demand = i % 97
            checksum += demand
            fh.write(f"SKU-{i % 5000:05d},2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d},{demand}\n")
    return checksum


@pytest.fixture(scope="module")
def big_csv(tmp_path_factory):
    path = tmp_path_factory.mktemp("chaos") / "big.csv"
    checksum = _write_big_csv(path)
    return {"path": str(path), "rows": BIG_ROWS, "checksum": checksum,
            "size_mb": path.stat().st_size / (1024 * 1024)}


def _peak_mb(fn):
    """Peak allocation of `fn`, in MB, isolated from whatever the suite left
    behind. Returns (result, peak_mb, seconds)."""
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = fn()
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return result, peak / (1024 * 1024), time.perf_counter() - started


# ── 1. Volume ────────────────────────────────────────────────────────────────

@pytest.mark.stress
def test_csv_preview_never_loads_the_file_it_is_previewing(big_csv):
    """THE test that matters for uptime.

    `dataset_preview` runs synchronously on upload. Asking for 20 rows of a
    400.000-row file must cost 20 rows of memory — the row count comes from
    streaming the file, not from parsing it. If this ever starts allocating in
    proportion to file size, a customer with a real history takes the API down
    by dropping their export on the page.
    """
    preview, peak_mb, seconds = _peak_mb(
        lambda: dataset_preview(big_csv["path"], rows=20)
    )

    assert len(preview["rows"]) == 20
    assert preview["columns"] == ["sku", "date", "demand"]
    # The count is the whole file's, even though the file was never held.
    assert preview["total_rows"] == big_csv["rows"], (
        "the row count is wrong — a preview that miscounts teaches the user to "
        "distrust every number after it"
    )
    assert peak_mb < 32, (
        f"preview allocated {peak_mb:.1f} MB for 20 rows of an "
        f"{big_csv['size_mb']:.0f} MB file — it is materializing the upload"
    )
    assert seconds < 20, f"preview took {seconds:.1f}s — this runs in the request thread"


@pytest.mark.stress
def test_nrows_is_honoured_and_not_a_slice_after_the_fact(big_csv):
    """`read_rows(nrows=100)` must read 100 rows, not read 400.000 and keep 100.
    The difference is invisible in the return value and total in the memory
    profile — which is exactly how this class of bug survives review."""
    rows, peak_mb, _ = _peak_mb(lambda: read_rows(big_csv["path"], nrows=100))
    assert len(rows) == 100
    assert peak_mb < 32, (
        f"reading 100 rows peaked at {peak_mb:.1f} MB — nrows is being applied "
        f"after the full parse"
    )


@pytest.mark.stress
def test_a_full_read_of_400k_rows_is_complete_and_exact(big_csv):
    """Volume must not silently truncate. The checksum is the assertion: a
    reader that drops the tail, or that re-types the column and rounds it, fails
    here while `len(df)` alone would still look right."""
    df, peak_mb, seconds = _peak_mb(lambda: read_dataframe(big_csv["path"]))

    assert len(df) == big_csv["rows"], "rows went missing on a full read"
    assert float(df["demand"].sum()) == pytest.approx(big_csv["checksum"]), (
        "the data changed on the way in"
    )
    # A generous ceiling — the point is that it is proportional to the file and
    # not to some multiple of it. 11 MB of CSV becoming 400 MB of DataFrame is a
    # different bug from being slow.
    assert peak_mb < 400, f"400k rows cost {peak_mb:.0f} MB"
    assert seconds < 60, f"full read took {seconds:.1f}s"


@pytest.mark.stress
def test_repeated_previews_do_not_leak(big_csv):
    """Thirty previews in a row — the shape of a busy afternoon on one file.

    Compares the peak of the last ten against the first ten instead of asserting
    an absolute number: what a leak looks like is not "high", it is "higher each
    time".
    """
    peaks = []
    for _ in range(30):
        _, peak_mb, _ = _peak_mb(lambda: dataset_preview(big_csv["path"], rows=10))
        peaks.append(peak_mb)

    early = sum(peaks[:10]) / 10
    late = sum(peaks[-10:]) / 10
    assert late < early * 1.5 + 2, (
        f"memory per preview grew from {early:.1f} MB to {late:.1f} MB over 30 "
        f"runs on the same file — something is retained between calls"
    )


@pytest.mark.stress
def test_a_single_line_of_ten_megabytes_does_not_hang_the_parser(tmp_path):
    """One cell holding a 10 MB string, with no newline anywhere. Real: an ERP
    that exported a base64 blob into a notes column. The parser must finish —
    bounded or refusing, either is fine — and must not spin."""
    path = tmp_path / "onelong.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("sku,notes\n")
        fh.write("SKU-1," + ("x" * 10_000_000) + "\n")

    started = time.perf_counter()
    try:
        df = read_dataframe(str(path))
        assert len(df) == 1
        assert len(df.iloc[0]["notes"]) == 10_000_000, "the cell was truncated silently"
    except GRACEFUL:
        pass  # refusing a 10 MB cell is a legitimate answer
    assert time.perf_counter() - started < 30, "the parser spun on one long line"


@pytest.mark.stress
def test_five_thousand_columns(tmp_path):
    """Width, not length. A pivoted export — one column per day of the year per
    warehouse — is how a file ends up 5.000 columns wide, and column handling is
    usually written assuming a dozen."""
    path = tmp_path / "wide.csv"
    cols = [f"c{i}" for i in range(5000)]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(",".join(cols) + "\n")
        for _ in range(50):
            fh.write(",".join("1" for _ in cols) + "\n")

    df, peak_mb, seconds = _peak_mb(lambda: read_dataframe(str(path)))
    assert df.shape == (50, 5000)
    assert seconds < 30, f"5.000 columns took {seconds:.1f}s"
    assert peak_mb < 200, f"5.000 columns cost {peak_mb:.0f} MB"


# ── 2. The evil path: files a real ERP actually produces ─────────────────────

def _csv(content: str, tmp_path: Path, name: str = "evil.csv", encoding="utf-8") -> str:
    path = tmp_path / name
    path.write_bytes(content.encode(encoding, errors="replace")
                     if isinstance(content, str) else content)
    return str(path)


EVIL_FILES = {
    # name: (bytes, what it is in the wild)
    "empty": b"",
    "header_only": b"sku,date,demand\n",
    "only_separators": b",,,,\n,,,,\n,,,,\n",
    "only_newlines": b"\n\n\n\n\n",
    "nul_in_cell": b"sku,demand\nSKU-\x00-1,5\n",
    "ragged_long": b"sku,date,demand\nA,2024-01-01,5,EXTRA,MORE\nB,2024-01-02,6\n",
    "ragged_short": b"sku,date,demand\nA\nB,2024-01-02\n",
    "duplicate_headers": b"sku,sku,sku\nA,B,C\n",
    "blank_header_names": b",,\n1,2,3\n",
    "quote_never_closed": b'sku,notes\nA,"never ends\nB,ok\n',
    "embedded_newlines": b'sku,notes\nA,"line one\nline two"\nB,fine\n',
    "crlf_and_lf_mixed": b"sku,demand\r\nA,1\nB,2\r\n",
    "bom_utf8": b"\xef\xbb\xbfsku,demand\nA,1\n",
    "utf16_pretending_to_be_csv": "sku,demand\nA,1\n".encode("utf-16"),
    "cp1252_accents": "sku,nombre\nA,Camión\nB,Ñandú\n".encode("cp1252"),
    "mixed_encoding_midfile": (b"sku,nombre\nA,normal\n" + "B,Cami\xf3n\n".encode("latin-1")),
    "nan_and_inf_literals": b"sku,demand\nA,NaN\nB,inf\nC,-inf\nD,1e400\n",
    "numbers_as_text_with_thousands": b"sku,demand\nA,\"1,234,567\"\nB,1.234.567\n",
    "formula_injection": b'sku,notes\nA,=1+1\nB,@SUM(1:99)\nC,+cmd|calc\nD,-2+3\n',
    "emoji_and_rtl": "sku,nombre\n🧃,Jugo\n‮SKU‬,Reversed\n​ZW,Zero width\n".encode("utf-8"),
    "sql_shaped_values": b"sku,nombre\n'; DROP TABLE tenants;--,pwn\n\" OR 1=1 --,pwn2\n",
    "path_shaped_values": b"sku,nombre\n../../../../etc/passwd,traversal\nC:\\Windows\\system32,win\n",
    "control_characters": b"sku,nombre\n\x01\x02\x03,ctrl\n\x1b[31mred\x1b[0m,ansi\n",
    "single_column_no_separator": b"justonecolumn\nvalue1\nvalue2\n",
    "header_is_numbers": b"1,2,3\n4,5,6\n",
    "trailing_separator_every_row": b"sku,demand,\nA,1,\nB,2,\n",
    "whitespace_only_cells": b"sku,demand\n   ,   \n\t,\t\n",
}


@pytest.mark.parametrize("name", sorted(EVIL_FILES))
def test_a_malformed_file_parses_or_refuses_but_never_escapes(name, tmp_path):
    """Twenty-six files that a real export has actually produced.

    The assertion is deliberately not "it parses". Several of these SHOULD be
    refused. What must never happen is the third outcome: an exception the API
    layer cannot classify, which reaches the user as "an unexpected error
    occurred" and reaches the on-call as a 500 with no cause.
    """
    path = tmp_path / f"{name}.csv"
    path.write_bytes(EVIL_FILES[name])

    started = time.perf_counter()
    try:
        df = read_dataframe(str(path))
        assert isinstance(df, pd.DataFrame)
        # Whatever came back must be describable — the profiler will ask.
        assert df.shape[1] >= 0
    except GRACEFUL as exc:
        assert str(exc) != "", f"{name} raised {type(exc).__name__} with no message"
    except Exception as exc:                       # noqa: BLE001 — that IS the test
        pytest.fail(
            f"{name} raised {type(exc).__name__}, which nothing upstream catches: {exc}"
        )
    assert time.perf_counter() - started < 15, f"{name} took too long to fail"


@pytest.mark.parametrize("name", sorted(EVIL_FILES))
def test_a_malformed_file_can_also_be_previewed(name, tmp_path):
    """Same files through `dataset_preview`, because that is the function the
    user hits FIRST — before any of them has been told the file is bad. A
    preview that explodes where a read succeeds is a worse bug than either."""
    path = tmp_path / f"{name}.csv"
    path.write_bytes(EVIL_FILES[name])
    try:
        preview = dataset_preview(str(path), rows=5)
        assert set(preview) >= {"columns", "rows", "total_rows"}
        assert len(preview["rows"]) <= 5
    except GRACEFUL:
        pass
    except Exception as exc:                       # noqa: BLE001
        pytest.fail(f"preview of {name} raised {type(exc).__name__}: {exc}")


def test_a_formula_is_data_not_a_formula(tmp_path):
    """`=1+1` in a cell must survive as the four characters somebody typed.
    Faro re-exports these values into CSVs the customer opens in Excel; a value
    that arrives as `2` has been evaluated somewhere it should not have been."""
    path = tmp_path / "formula.csv"
    path.write_bytes(EVIL_FILES["formula_injection"])
    df = read_dataframe(str(path))
    assert list(df["notes"]) == ["=1+1", "@SUM(1:99)", "+cmd|calc", "-2+3"]


def test_a_nul_byte_is_refused_never_silently_truncated(tmp_path):
    """pandas' C parser reads a NUL as end-of-field, so `SKU-<NUL>-1` arrives as
    `SKU-`: no error, no warning, a different product code than the file
    contains. The buyer then orders against something their supplier has never
    heard of.

    The contract is refusal, decided 2026-08-22. A DataFrame has no channel to
    report "and 12 rows were dropped", so filtering would trade one invisible
    corruption for another. The stock CSV importer, which DOES have a per-row
    error report, rejects the individual rows instead."""
    path = tmp_path / "nul.csv"
    path.write_bytes(EVIL_FILES["nul_in_cell"])
    with pytest.raises(ValueError) as exc:
        read_dataframe(str(path))
    assert "NUL" in str(exc.value), f"refused, but not for that reason: {exc.value}"
    # The message has to point at the file, not at the parser's internals — the
    # person reading it has to know which line to go fix.
    assert "line" in str(exc.value).lower()


def test_a_binary_file_full_of_nul_is_not_mistaken_for_a_corrupt_csv(tmp_path):
    """An .xlsx is a ZIP and a .parquet is a binary column store: both are full
    of NUL bytes by construction. The refusal above must not fire on them —
    which it did, the first time it was written, refusing every Excel upload in
    the product."""
    pytest.importorskip("openpyxl")
    path = tmp_path / "fine.xlsx"
    pd.DataFrame({"sku": ["A", "B"], "demand": [1, 2]}).to_excel(path, index=False)
    assert len(read_dataframe(str(path))) == 2
    assert dataset_preview(str(path), rows=5)["total_rows"] == 2


def test_separator_sniffing_survives_files_designed_to_fool_it():
    """A header with more commas inside a quoted field than real semicolons
    outside it is how sniffing picks the wrong separator and collapses a file
    into one unusable column — the exact bug `sniff_separator` was written for,
    now pushed at from the other side."""
    assert sniff_separator("sku;fecha;cantidad\n") == ";"
    assert sniff_separator("sku\tfecha\tcantidad\n") == "\t"
    assert sniff_separator("sku|fecha|cantidad\n") == "|"
    assert sniff_separator("sku,fecha,cantidad\n") == ","
    # Degenerate inputs must answer something, never raise.
    for pathological in ("", "\n", "   ", "﻿", "onecolumn", "\x00\x00\x00"):
        assert sniff_separator(pathological) in (",", ";", "\t", "|")


def test_encoding_fallback_does_not_reinterpret_a_valid_utf8_file(tmp_path):
    """latin-1 maps all 256 byte values, so it always "succeeds" — which is why
    it goes last. If the order ever flips, every accented UTF-8 file starts
    arriving as mojibake and nothing errors."""
    path = tmp_path / "utf8.csv"
    path.write_text("sku,nombre\nA,Camión\nB,Ñandú\n", encoding="utf-8")
    df = read_csv_any_encoding(str(path))
    assert list(df["nombre"]) == ["Camión", "Ñandú"], "a valid UTF-8 file was re-decoded"


def test_a_cp1252_export_is_read_as_spanish_not_as_mojibake(tmp_path):
    path = tmp_path / "cp1252.csv"
    path.write_bytes(EVIL_FILES["cp1252_accents"])
    df = read_csv_any_encoding(str(path))
    assert list(df["nombre"]) == ["Camión", "Ñandú"]


def test_bytes_source_requires_a_format_instead_of_guessing(tmp_path):
    """Reading from bytes with no format is ambiguous, and guessing would mean
    handing an Excel file to the CSV parser. It must refuse, in a way the caller
    can catch."""
    with pytest.raises(GRACEFUL):
        read_rows(b"sku,demand\nA,1\n")


def test_an_excel_bomb_does_not_take_the_process_with_it(tmp_path):
    """An .xlsx is a zip. A small file can hold an enormous sheet, and unlike
    the CSV path, `dataset_preview` on Excel reads the WHOLE sheet before taking
    its first 20 rows. This measures that asymmetry rather than assuming it is
    fine: 200.000 rows is a plausible export, not an attack."""
    pytest.importorskip("openpyxl")
    path = tmp_path / "bomb.xlsx"
    frame = pd.DataFrame({
        "sku": [f"SKU-{i % 500:04d}" for i in range(200_000)],
        "demand": [i % 50 for i in range(200_000)],
    })
    frame.to_excel(path, index=False)

    preview, peak_mb, seconds = _peak_mb(lambda: dataset_preview(str(path), rows=20))
    assert len(preview["rows"]) == 20
    assert preview["total_rows"] == 200_000

    # These bounds are regression guards, NOT a claim that the current numbers
    # are acceptable. Measured 2026-08-22: 140 s and ~700 MB to return twenty
    # rows, against under a second for the same data as CSV — the Excel branch
    # reads the entire sheet before slicing. That gap is a finding in
    # docs/stability.md, not something this test blesses; what it catches here
    # is the day it gets even worse.
    assert peak_mb < 1200, (
        f"an Excel preview of 200k rows peaked at {peak_mb:.0f} MB — the Excel "
        f"branch loads the entire sheet to show twenty rows"
    )
    assert seconds < 300, (
        f"Excel preview took {seconds:.1f}s in the request thread "
        f"(140s was already the measured baseline)"
    )


def test_json_that_is_not_a_table_is_refused_not_crashed(tmp_path):
    """`.json` is in ALLOWED_EXTENSIONS, and `pd.read_json` is happy to be
    handed something that is valid JSON and not remotely a table."""
    for name, payload in {
        "scalar.json": b"42",
        "string.json": b'"hello"',
        "nested.json": b'{"a": {"b": {"c": [1,2,3]}}}',
        "deep.json": b"[" * 400 + b"1" + b"]" * 400,
        "not_json.json": b"{definitely not json",
    }.items():
        path = tmp_path / name
        path.write_bytes(payload)
        try:
            read_dataframe(str(path))
        except GRACEFUL:
            pass
        except RecursionError:
            pytest.fail(f"{name} blew the stack — a nested JSON is a free 500")
        except Exception as exc:                   # noqa: BLE001
            pytest.fail(f"{name} raised {type(exc).__name__}: {exc}")


def test_a_stream_of_a_million_rows_can_be_consumed_in_bounded_memory(tmp_path):
    """The generator case: a million rows arriving as an iterator, never as a
    list. This is the shape any future streaming importer must have, and the
    test states the contract now — one row of memory at a time, and the sum is
    still exact at the end.
    """
    def million_rows():
        for i in range(1_000_000):
            yield {"sku": f"SKU-{i % 5000:05d}", "demand": i % 97}

    def consume():
        total = 0
        seen = 0
        for row in million_rows():
            total += row["demand"]
            seen += 1
        return total, seen

    (total, seen), peak_mb, seconds = _peak_mb(consume)
    assert seen == 1_000_000
    assert total == sum(i % 97 for i in range(1_000_000))
    assert peak_mb < 8, (
        f"consuming a million rows peaked at {peak_mb:.1f} MB — something is "
        f"accumulating the stream"
    )
    assert seconds < 60
