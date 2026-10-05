"""Turning loader rows into the bytes a BI tool reads: JSON values and CSV.

Pure functions, no I/O. Everything a report depends on being STABLE lives here:

  * columns come out in the dataset's declared order, always;
  * numbers use '.' as the decimal separator, never a thousands separator, never
    an exponent (`1e-05` would be text to Excel in a comma locale), and a whole
    number has no trailing `.0`;
  * dates are `YYYY-MM-DD`; instants are UTC, `YYYY-MM-DDTHH:MM:SSZ`;
  * booleans are `true` / `false`; null is an EMPTY CSV cell and JSON `null`;
  * the CSV is UTF-8 with a byte-order mark (Excel reads accents wrongly
    without one; Power BI and pandas ignore it), CRLF line ends.

A value that cannot be read as its column's type becomes null and is LOGGED, not
raised: one bad cell must not fail a nightly refresh, and must not vanish
without a trace either.
"""

from __future__ import annotations

import csv
import io
import logging
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from backend.bi_datasets.spec import Dataset

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 1000
MAX_LIMIT = 10_000

CSV_BOM = "﻿"
# Decimals kept in a number. Enough for a unit cost of a fraction of a cent, and
# it makes 0.1 + 0.2 print as 0.3 on every platform.
NUMBER_DECIMALS = 10


def _bad(value: Any, type_: str, column: str) -> None:
    log.warning("[bi-datasets] column %s: %r is not a valid %s; sent as null",
                column, value, type_)
    return None


def normalize(value: Any, type_: str, column: str = "?") -> Any:
    """The JSON-safe value of one cell: str, int, float, bool or None."""
    if value is None:
        return None
    if type_ == "string":
        text = str(value)
        return text
    if type_ == "boolean":
        if isinstance(value, bool):
            return value
        return _bad(value, type_, column)
    if type_ in ("integer", "number"):
        if isinstance(value, bool):
            return _bad(value, type_, column)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return _bad(value, type_, column)
        if not math.isfinite(number):
            return None  # NaN / inf are 'not computable', the same as null
        if type_ == "integer":
            if number != int(number):
                return _bad(value, type_, column)
            return int(number)
        return round(number, NUMBER_DECIMALS)
    if type_ == "date":
        if isinstance(value, datetime):
            return _as_utc(value).date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            return _bad(value, type_, column)
    if type_ == "datetime":
        if isinstance(value, datetime):
            return _utc_text(value)
        if isinstance(value, date):
            return _utc_text(datetime(value.year, value.month, value.day))
        try:
            return _utc_text(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except ValueError:
            return _bad(value, type_, column)
    raise ValueError(f"unknown column type {type_!r}")


def _as_utc(value: datetime) -> datetime:
    # A naive timestamp is read as UTC: the database stores TIMESTAMPTZ and the
    # app runs in UTC, so a naive value here is already UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def shape_row(dataset: Dataset, raw: dict) -> dict:
    """One output row: every declared column, in order, normalised."""
    return {c.name: normalize(raw.get(c.name), c.type, c.name) for c in dataset.columns}


def shape_rows(dataset: Dataset, rows: Iterable[dict]) -> list[dict]:
    return [shape_row(dataset, r) for r in rows]


# ── CSV ──────────────────────────────────────────────────────────────────────

def number_text(value: float | int) -> str:
    """A number as invariant text: '.' decimal, no exponent, no trailing '.0'."""
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"  # also folds -0.0
    text = format(Decimal(repr(float(value))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


_FORMULA_LEADERS = ("=", "@", "\t", "\r", "+", "-")


def _is_number_text(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def text_cell(text: str) -> str:
    """A string cell made safe to open in a spreadsheet.

    Excel and Sheets evaluate a cell that starts with = + - @ as a formula, and
    product names and customers are text somebody typed. Such a cell gets a
    leading apostrophe, the usual neutraliser. A value that is simply a number
    (-12.5) is left alone: it is a number, not a formula.
    """
    if text and text[0] in _FORMULA_LEADERS and not _is_number_text(text.strip()):
        return "'" + text
    return text


def cell_text(value: Any, type_: str) -> str:
    """One normalised value as CSV text."""
    if value is None:
        return ""
    if type_ == "boolean":
        return "true" if value else "false"
    if type_ in ("integer", "number"):
        return number_text(value)
    if type_ == "string":
        return text_cell(str(value))
    return str(value)  # date / datetime are already ISO text


def to_csv(dataset: Dataset, shaped_rows: list[dict]) -> str:
    """The CSV document for already-shaped rows: header line, then the rows."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(dataset.column_names)
    types = [c.type for c in dataset.columns]
    for row in shaped_rows:
        writer.writerow(
            [cell_text(row.get(name), type_)
             for name, type_ in zip(dataset.column_names, types)]
        )
    return CSV_BOM + out.getvalue()


# ── Paging ───────────────────────────────────────────────────────────────────

def page_bounds(total: int, page: int, limit: int) -> dict:
    """The paging facts for `page` (1-based) of `limit` rows over `total` rows.

    `next_page` is null on the last page, so a client loops until it is null
    instead of counting. A page past the end is valid and empty.
    """
    if page < 1 or limit < 1:
        raise ValueError("page and limit start at 1")
    pages = max(1, math.ceil(total / limit)) if total else 1
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "pages": pages,
        "offset": (page - 1) * limit,
        "next_page": page + 1 if page < pages else None,
    }


def slice_page(rows: list, page: int, limit: int) -> tuple[list, dict]:
    """(the rows of this page, paging facts) for an in-memory list."""
    info = page_bounds(len(rows), page, limit)
    return rows[info["offset"]: info["offset"] + limit], info


def page_headers(info: dict) -> dict[str, str]:
    """Paging facts as headers, for CSV (which has no envelope to carry them)."""
    headers = {
        "X-Total-Count": str(info["total"]),
        "X-Page": str(info["page"]),
        "X-Page-Size": str(info["limit"]),
        "X-Page-Count": str(info["pages"]),
    }
    if info["next_page"] is not None:
        headers["X-Next-Page"] = str(info["next_page"])
    return headers


def sort_key(*names: str):
    """A sort key over dict rows by column names, nulls first, mixed types safe."""
    def key(row: dict):
        return tuple(("" if row.get(n) is None else str(row.get(n))) for n in names)
    return key


def clamp_limit(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))
