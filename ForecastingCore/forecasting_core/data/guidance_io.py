"""Raw reading for the guided upload layer.

``DataLoader`` assumes the first row is the header and the file is one clean
table, which is exactly the assumption the guide exists to check. This reads a
file as a GRID of cells - no header, no type inference - so the guide can find
the header itself, rank several sheets and see the cells as the person typed
them (``"01/02/2025"``, not a coerced timestamp).
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from typing import Any

Grid = list  # list[list[Any]], rectangular

MAX_ROWS = 400_000
CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


class GuidanceReadError(Exception):
    """The file could not be read as a table at all."""


@dataclass
class Workbook:
    """Sheets of raw cells. A CSV is a workbook with one sheet named ``""``."""
    sheets: dict[str, Grid] = field(default_factory=dict)
    kind: str = "csv"

    @property
    def sheet_names(self) -> list[str]:
        return list(self.sheets)


def _rectangular(rows: list[list]) -> Grid:
    width = max((len(r) for r in rows), default=0)
    out = []
    for r in rows:
        r = list(r) + [None] * (width - len(r))
        out.append([None if (isinstance(c, str) and not c.strip()) else c for c in r])
    # Drop trailing fully blank rows (Excel keeps formatted empty rows).
    while out and all(c is None for c in out[-1]):
        out.pop()
    return out


def decode_text(raw: bytes) -> str:
    for enc in CSV_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")      # pragma: no cover


def sniff_delimiter(text: str) -> str:
    """The delimiter that splits the most lines into the same number of fields.

    Judged over many lines, not the first one: an export with a title above the
    header would otherwise be read as a single column.
    """
    lines = [ln for ln in text.splitlines()[:80] if ln.strip()]
    best, best_score = ",", (0, 0)
    for cand in (",", ";", "\t", "|"):
        counts = []
        for row in csv.reader(lines, delimiter=cand):
            counts.append(len(row))
        if not counts:
            continue
        modal = max(set(counts), key=counts.count)
        score = (counts.count(modal) if modal > 1 else 0, modal)
        if score > best_score:
            best, best_score = cand, score
    return best


def read_csv_grid(raw: bytes) -> Grid:
    text = decode_text(raw)
    if "\x00" in text:
        raise GuidanceReadError("The file contains NUL bytes and cannot be read as text.")
    delim = sniff_delimiter(text)
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        rows.append(row)
        if len(rows) >= MAX_ROWS:
            break
    return _rectangular(rows)


def _frame_to_grid(df) -> Grid:
    rows = [[str(c) for c in df.columns]]
    for rec in df.itertuples(index=False, name=None):
        rows.append([None if (v != v if isinstance(v, float) else v is None) else v for v in rec])
    return _rectangular(rows)


def read_workbook(path: str) -> Workbook:
    """Read any supported file into raw grids. Raises ``GuidanceReadError``."""
    if not os.path.exists(path):
        raise GuidanceReadError(f"File not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".csv", ".txt", ".tsv"):
            with open(path, "rb") as fh:
                return Workbook({"": read_csv_grid(fh.read())}, "csv")
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
            sheets: dict[str, Grid] = {}
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(list(row))
                    if len(rows) >= MAX_ROWS:
                        break
                grid = _rectangular(rows)
                if grid:
                    sheets[ws.title] = grid
            wb.close()
            return Workbook(sheets, "excel")
        if ext == ".xls":
            import pandas as pd
            frames = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
            sheets = {}
            for name, df in frames.items():
                grid = _rectangular([[None if (isinstance(v, float) and v != v) else v for v in r]
                                     for r in df.itertuples(index=False, name=None)])
                if grid:
                    sheets[str(name)] = grid
            return Workbook(sheets, "excel")
        if ext in (".json", ".parquet"):
            import pandas as pd
            df = pd.read_json(path) if ext == ".json" else pd.read_parquet(path)
            return Workbook({"": _frame_to_grid(df)}, ext.lstrip("."))
    except GuidanceReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - every reader failure means "unreadable"
        raise GuidanceReadError(f"{type(exc).__name__}: {exc}") from exc
    raise GuidanceReadError(f"Unsupported format '{ext}'.")


def workbook_from_grid(grid: list[list[Any]], sheet: str = "") -> Workbook:
    """Test/helper constructor: a one-sheet workbook from in-memory cells."""
    return Workbook({sheet: _rectangular(grid)}, "memory")
