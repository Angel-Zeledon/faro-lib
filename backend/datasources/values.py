"""How a value read from a customer database is written down.

Two destinations, two rule sets:

* `csv_cell` — the snapshot CSV a materialized dataset IS. Whatever is written
  here is what the forecast trains on, so nothing may be rounded, reformatted
  into something a CSV reader parses differently, or silently become text:
  Decimal keeps every digit (never ``1E+3``), an aware datetime keeps its
  offset (ISO 8601), a NaN or infinity becomes empty (a CSV reader would
  otherwise take the literal ``nan``), bytes become ``0x``-prefixed hex (not
  ``<memory at 0x...>``), JSON stays JSON, and text goes through the
  formula-injection guard.
* `json_cell` — a result shown in the query editor. The browser parses JSON
  numbers as doubles, so integers beyond 2^53 and every Decimal travel as
  strings (a grid showing 9007199254740993 as ...992 is wrong data on screen);
  bytes are shown as truncated hex.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import uuid
from decimal import Decimal
from typing import Any

_JS_SAFE_INT = 2 ** 53 - 1
MAX_JSON_BYTES_SHOWN = 64
MAX_JSON_TEXT_CHARS = 10_000


def _bytes_of(value: Any) -> bytes | None:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    return None


def _decimal_text(value: Decimal) -> str | None:
    if not value.is_finite():
        return None
    text = format(value, "f")
    # format(Decimal('1.50'), 'f') keeps the scale; that is the stored value.
    return "0" if text in ("-0", "") else text


def _temporal(value: Any) -> str | None:
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return str(value.total_seconds())
    return None


def csv_cell(value: Any) -> Any:
    """One value as the snapshot CSV must hold it (None -> empty cell)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    temporal = _temporal(value)
    if temporal is not None:
        return temporal
    raw = _bytes_of(value)
    if raw is not None:
        return "0x" + raw.hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        from backend.utils.csv_safe import csv_safe
        return csv_safe(json.dumps(value, ensure_ascii=False, default=str))
    text = value if isinstance(value, str) else str(value)
    if "\x00" in text:
        text = text.replace("\x00", "")
    try:
        float(text)
        return text                      # a numeric string is never a formula
    except ValueError:
        from backend.utils.csv_safe import csv_safe
        return csv_safe(text)


def json_cell(value: Any) -> Any:
    """One value as the query editor's JSON response carries it."""
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > MAX_JSON_TEXT_CHARS:
            return value[:MAX_JSON_TEXT_CHARS] + "…"
        return value
    if isinstance(value, int):
        return value if abs(value) <= _JS_SAFE_INT else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return _decimal_text(value)
    temporal = _temporal(value)
    if temporal is not None:
        return temporal
    raw = _bytes_of(value)
    if raw is not None:
        shown = raw[:MAX_JSON_BYTES_SHOWN].hex()
        return "0x" + shown + ("…" if len(raw) > MAX_JSON_BYTES_SHOWN else "")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)
