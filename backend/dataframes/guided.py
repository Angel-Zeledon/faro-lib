"""Guided upload boundary: read a messy file, report on it, write the cleaned copy.

The intelligence lives in ``forecasting_core.data.guidance``; this module is the
backend's only door to it, and it speaks plain Python (dicts, lists) in both
directions. The one thing that leaves as a file is the cleaned copy, written by
``write_cleaned`` - the original file is opened read-only and never touched.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional


class GuidedReadError(Exception):
    """The file cannot be read as a table at all (corrupt, wrong format)."""


def _book(path: str):
    from forecasting_core.data.guidance_io import GuidanceReadError, read_workbook
    try:
        return read_workbook(path)
    except GuidanceReadError as exc:
        raise GuidedReadError(str(exc)) from exc


def analyze_file(path: str, decisions: Optional[dict] = None,
                 mapping: Optional[dict] = None, today: Optional[date] = None) -> dict:
    """The guide's report for one file, given the answers collected so far."""
    from forecasting_core.data.guidance import analyze
    return analyze(_book(path), decisions or {}, mapping=mapping, today=today)


def write_cleaned(src_path: str, fixes: list[dict], dst_path: str) -> dict:
    """Replay ``fixes`` over ``src_path`` and write the result to ``dst_path``.

    Written as .xlsx on purpose: a CSV would hand product codes such as
    ``00123`` back to a reader that turns them into the number 123 again, while
    an Excel text cell stays text. Returns row/column counts.
    """
    from forecasting_core.data.guidance import materialize
    df, stats = materialize(_book(src_path), fixes)
    tmp = dst_path[:-5] + ".partial.xlsx" if dst_path.endswith(".xlsx") else dst_path + ".partial.xlsx"
    try:
        df.to_excel(tmp, index=False, engine="openpyxl")
        if os.path.getsize(tmp) == 0:
            raise GuidedReadError("The cleaned file came out empty.")
        os.replace(tmp, dst_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return {"rows": int(stats["rows_out"]), "columns": int(len(df.columns)),
            "rows_in": int(stats["rows_in"])}
