"""Cell-level reading rules for the guided upload layer.

Pure functions, no pandas, no I/O. Everything here answers one question about a
single cell or a single column: "what did the person MEAN by this text?".

The rule that shapes the whole module: a reading is either DECISIVE (the data
itself rules out every other reading) or AMBIGUOUS (two readings are both
plausible). Ambiguous readings are never resolved here; the caller turns them
into a question for the user. `01/02/2025` is not "probably January 2nd" -
it is a question, unless something else in the same column settles it.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

# ── Months ───────────────────────────────────────────────────────────────────

MONTHS: dict[str, int] = {}
for _num, _names in enumerate([
    ("ene", "enero", "jan", "january"),
    ("feb", "febrero", "february"),
    ("mar", "marzo", "march"),
    ("abr", "abril", "apr", "april"),
    ("may", "mayo"),
    ("jun", "junio", "june"),
    ("jul", "julio", "july"),
    ("ago", "agosto", "aug", "august"),
    ("sep", "sept", "set", "setiembre", "septiembre", "september"),
    ("oct", "octubre", "october"),
    ("nov", "noviembre", "november"),
    ("dic", "diciembre", "dec", "december"),
], start=1):
    for _n in _names:
        MONTHS[_n] = _num

EXCEL_EPOCH = date(1899, 12, 30)
EXCEL_SERIAL_MIN = 20_000      # 1954-10-03
EXCEL_SERIAL_MAX = 60_000      # 2064-01-28

ORDER_DAY_FIRST = "day_first"
ORDER_MONTH_FIRST = "month_first"

_TZ = r"(?:\s*(?:Z|UTC|GMT|[+-]\d{2}:?\d{2}))"
_TIME = r"(?:[T\s]+\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:\s*[AaPp][Mm])?)"

_ISO = re.compile(rf"^(\d{{4}})[-/.](\d{{1,2}})[-/.](\d{{1,2}})({_TIME})?({_TZ})?$", re.I)
_DMY = re.compile(rf"^(\d{{1,2}})[/\-.](\d{{1,2}})[/\-.](\d{{4}}|\d{{2}})({_TIME})?({_TZ})?$", re.I)
_TEXT_DMY = re.compile(
    r"^(?:[A-Za-zÁÉÍÓÚáéíóú]{3,10}\.?,?\s+)?(\d{1,2})[\s\-/.]*(?:de\s+)?"
    r"([A-Za-zÁÉÍÓÚáéíóúñ]{3,12})\.?[\s\-/.,]*(?:de\s+|del\s+)?(\d{4}|\d{2})$", re.I)
_TEXT_MDY = re.compile(
    r"^([A-Za-z]{3,10})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", re.I)
_MONTH_YEAR = re.compile(
    r"^([A-Za-zÁÉÍÓÚáéíóúñ]{3,12})\.?[\s\-/.,]*(?:de\s+|del\s+)?(\d{4}|\d{2})$", re.I)
_YEAR_MONTH = re.compile(r"^(\d{4})[-/.](\d{1,2})$")
_MONTH_YEAR_NUM = re.compile(r"^(\d{1,2})[-/.](\d{4})$")
_MONTH_ONLY = re.compile(r"^([A-Za-zÁÉÍÓÚáéíóúñ]{3,12})\.?$")
_NUMERIC_TEXT = re.compile(r"^\d{4,6}(?:[.,]0+)?$")


def _is_blank(cell: Any) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip()) or (
        isinstance(cell, float) and cell != cell)


def _year(y: str) -> tuple[int, bool]:
    """(four-digit year, was it two digits). 00-69 -> 20xx, 70-99 -> 19xx."""
    if len(y) == 2:
        n = int(y)
        return (2000 + n if n <= 69 else 1900 + n), True
    return int(y), False


def _mk(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except (ValueError, OverflowError):
        return None


def month_number(token: str) -> Optional[int]:
    return MONTHS.get(token.strip().strip(".").lower())


def serial_to_date(value: float) -> Optional[date]:
    try:
        return EXCEL_EPOCH + timedelta(days=int(value))
    except (OverflowError, ValueError):
        return None


def read_date_cell(cell: Any, order: Optional[str] = None,
                   serial_ok: bool = False) -> tuple[Optional[date], set]:
    """Read one cell as a date. Returns (date | None, flags).

    Flags describe HOW it was read, so a column-level caller can tell a clean
    ISO column from one that needed interpretation: ``obj`` (already a date),
    ``iso``, ``dmy`` (numeric day/month/year), ``text_month``, ``month_year``,
    ``serial``, ``tz`` (a timezone suffix was dropped), ``yy`` (two-digit year),
    ``time`` (a time of day was dropped), ``ambiguous`` (day/month order cannot
    be told), ``proof_day_first`` / ``proof_month_first`` (this very cell rules
    the other order out), ``blank``, ``invalid``, ``number``, ``text``.
    """
    flags: set = set()
    if _is_blank(cell):
        return None, {"blank"}
    if isinstance(cell, bool):
        return None, {"text"}
    if isinstance(cell, datetime):
        return cell.date(), {"obj"}
    if isinstance(cell, date):
        return cell, {"obj"}
    if hasattr(cell, "to_pydatetime"):          # pandas Timestamp without importing pandas
        try:
            return cell.to_pydatetime().date(), {"obj"}
        except Exception:
            return None, {"invalid"}
    if isinstance(cell, (int, float)):
        if serial_ok and EXCEL_SERIAL_MIN <= cell <= EXCEL_SERIAL_MAX:
            d = serial_to_date(cell)
            return d, ({"serial"} if d else {"invalid"})
        return None, {"number"}
    text = str(cell).strip().replace(" ", " ")
    if serial_ok and _NUMERIC_TEXT.match(text):
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            v = -1
        if EXCEL_SERIAL_MIN <= v <= EXCEL_SERIAL_MAX:
            d = serial_to_date(v)
            return d, ({"serial"} if d else {"invalid"})

    m = _ISO.match(text)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        flags.add("iso")
        if m[4]:
            flags.add("time")
        if m[5]:
            flags.add("tz")
        res = _mk(y, mo, d)
        return res, flags if res else flags | {"invalid"}

    m = _DMY.match(text)
    if m:
        a, b = int(m[1]), int(m[2])
        y, yy = _year(m[3])
        flags.add("dmy")
        if yy:
            flags.add("yy")
        if m[4]:
            flags.add("time")
        if m[5]:
            flags.add("tz")
        if order == ORDER_DAY_FIRST:
            res = _mk(y, b, a)
        elif order == ORDER_MONTH_FIRST:
            res = _mk(y, a, b)
        elif a > 12 and b <= 12:
            flags.add("proof_day_first")
            res = _mk(y, b, a)
        elif b > 12 and a <= 12:
            flags.add("proof_month_first")
            res = _mk(y, a, b)
        elif a == b:
            res = _mk(y, a, b)                  # same either way
        elif a <= 12 and b <= 12:
            flags.add("ambiguous")
            return None, flags
        else:
            res = None
        return res, flags if res else flags | {"invalid"}

    m = _TEXT_DMY.match(text)
    if m and month_number(m[2]):
        y, yy = _year(m[3])
        res = _mk(y, month_number(m[2]), int(m[1]))
        flags |= {"text_month"} | ({"yy"} if yy else set())
        return res, flags if res else flags | {"invalid"}

    m = _TEXT_MDY.match(text)
    if m and month_number(m[1]):
        res = _mk(int(m[3]), month_number(m[1]), int(m[2]))
        flags.add("text_month")
        return res, flags if res else flags | {"invalid"}

    m = _YEAR_MONTH.match(text)
    if m and 1 <= int(m[2]) <= 12:
        return _mk(int(m[1]), int(m[2]), 1), {"month_year"}

    m = _MONTH_YEAR_NUM.match(text)
    if m and 1 <= int(m[1]) <= 12:
        return _mk(int(m[2]), int(m[1]), 1), {"month_year"}

    m = _MONTH_YEAR.match(text)
    if m and month_number(m[1]):
        y, yy = _year(m[2])
        res = _mk(y, month_number(m[1]), 1)
        return res, ({"month_year"} | ({"yy"} if yy else set())) if res else {"invalid"}

    return None, {"text"}


def read_month_only(cell: Any) -> Optional[int]:
    """'Enero' / 'feb' -> 1 / 2. A header that names a month but no year."""
    if not isinstance(cell, str):
        return None
    m = _MONTH_ONLY.match(cell.strip())
    return month_number(m[1]) if m else None


_MAJOR_SHAPES = ("obj", "iso", "dmy", "text_month", "month_year", "serial")


def _shape(flags: set) -> str:
    for s in _MAJOR_SHAPES:
        if s in flags:
            return s
    return "other"


def analyze_date_column(cells: Iterable[Any], *, order: Optional[str] = None,
                        serial: Optional[bool] = None,
                        today: Optional[date] = None) -> dict:
    """Everything the guide needs to know about one column of dates.

    ``order`` / ``serial`` are the user's (or an earlier layer's) decisions; with
    ``None`` the column is judged on its own evidence. The result never hides
    an ambiguity: ``unresolved`` is True when day/month order is genuinely open.
    """
    cells = list(cells)
    today = today or date.today()
    nonblank = [c for c in cells if not _is_blank(c)]
    n = len(nonblank)

    # Excel serial numbers: a column of numbers in the date range.
    numeric = []
    for c in nonblank:
        if isinstance(c, bool):
            continue
        if isinstance(c, (int, float)):
            numeric.append(c)
        elif isinstance(c, str) and _NUMERIC_TEXT.match(c.strip()):
            numeric.append(float(c.strip().replace(",", ".")))
    in_range = [c for c in numeric if EXCEL_SERIAL_MIN <= c <= EXCEL_SERIAL_MAX]
    serial_candidate = bool(n) and len(in_range) >= 0.9 * n
    use_serial = serial_candidate if serial is None else serial

    proof_df = proof_mf = ambiguous = 0
    shapes: Counter = Counter()
    flags_seen: set = set()
    ambiguous_examples: list = []
    for c in nonblank:
        d, fl = read_date_cell(c, None, use_serial)
        flags_seen |= fl
        if "proof_day_first" in fl:
            proof_df += 1
        if "proof_month_first" in fl:
            proof_mf += 1
        if "ambiguous" in fl:
            ambiguous += 1
            if len(ambiguous_examples) < 3 and c not in [e[0] for e in ambiguous_examples]:
                ambiguous_examples.append((c, None, None))
        if d is not None or "ambiguous" in fl:
            shapes[_shape(fl)] += 1

    resolved, source, conflict = order, ("user" if order else None), False
    if order is None:
        if proof_df and proof_mf:
            conflict = True
        elif proof_df:
            resolved, source = ORDER_DAY_FIRST, "data"
        elif proof_mf:
            resolved, source = ORDER_MONTH_FIRST, "data"
        elif ambiguous:
            guess = _order_by_sequence(nonblank)
            if guess:
                resolved, source = guess, "sequence"

    dates: list[Optional[date]] = []
    still_ambiguous = 0
    parsed = 0
    unparsed_examples: list = []
    future = 0
    for c in cells:
        if _is_blank(c):
            dates.append(None)
            continue
        d, fl = read_date_cell(c, resolved, use_serial)
        if "ambiguous" in fl:
            still_ambiguous += 1
        if d is not None:
            parsed += 1
            if (d - today).days > 365:
                future += 1
        elif "ambiguous" not in fl and len(unparsed_examples) < 3:
            unparsed_examples.append(str(c))
        dates.append(d)

    # Both readings of the first ambiguous examples, for the question.
    examples = []
    for raw, _, _ in ambiguous_examples:
        df_d, _ = read_date_cell(raw, ORDER_DAY_FIRST, use_serial)
        mf_d, _ = read_date_cell(raw, ORDER_MONTH_FIRST, use_serial)
        examples.append({"raw": str(raw),
                         "day_first": df_d.isoformat() if df_d else None,
                         "month_first": mf_d.isoformat() if mf_d else None})

    major = [s for s, k in shapes.items() if k >= max(1, 0.05 * n)]
    return {
        "dates": dates,
        "n_nonblank": n,
        "n_parsed": parsed,
        "rate": (parsed + still_ambiguous) / n if n else 0.0,
        "parsed_rate": parsed / n if n else 0.0,
        "shapes": dict(shapes),
        "mixed_formats": len(major) > 1,
        "order": resolved,
        "order_source": source,
        "order_conflict": conflict,
        "ambiguous": ambiguous,
        "unresolved": bool(still_ambiguous) and resolved is None,
        "proof_day_first": proof_df,
        "proof_month_first": proof_mf,
        "ambiguous_examples": examples,
        "serial": bool(use_serial and "serial" in flags_seen),
        "serial_candidate": serial_candidate,
        "has_tz": "tz" in flags_seen,
        "has_time": "time" in flags_seen,
        "two_digit_year": "yy" in flags_seen,
        "text_months": "text_month" in flags_seen or "month_year" in flags_seen,
        "future": future,
        "unparsed_examples": unparsed_examples,
        "n_unparsed": n - parsed - still_ambiguous,
    }


def _order_by_sequence(cells: list) -> Optional[str]:
    """Settle day/month order by whether rows run forward in time.

    A sales export is almost always sorted by date. When one reading puts the
    rows in order and the other jumps back and forth, the file has told us the
    answer. Deliberately strict: it only speaks when the evidence is lopsided.
    """
    if len(cells) < 20:
        return None
    seqs = {}
    for o in (ORDER_DAY_FIRST, ORDER_MONTH_FIRST):
        seq = []
        for c in cells:
            d, fl = read_date_cell(c, o)
            if d is None:
                return None
            seq.append(d)
        seqs[o] = seq
    back = {}
    for o, seq in seqs.items():
        steps = len(seq) - 1
        back[o] = sum(1 for i in range(1, len(seq)) if seq[i] < seq[i - 1]) / steps
    if back[ORDER_DAY_FIRST] <= 0.02 and back[ORDER_MONTH_FIRST] >= 0.2:
        return ORDER_DAY_FIRST
    if back[ORDER_MONTH_FIRST] <= 0.02 and back[ORDER_DAY_FIRST] >= 0.2:
        return ORDER_MONTH_FIRST
    return None


# ── Numbers ──────────────────────────────────────────────────────────────────

_CURRENCY = re.compile(
    r"(?i)(?:US\$|R\$|S/\.?|\b(?:COP|USD|CRC|MXN|PEN|CLP|ARS|EUR|UYU|BOB|GTQ|DOP|PAB|HNL|NIO|PYG|VES)\b|"
    r"[$€£¥₡₲]|\bBs\.?(?=\s?\d)|\bQ(?=\s?\d)|\bL(?=\s?\d)|\bRD\$?)")
_NUM_BODY = re.compile(r"^\d[\d.,' ]*$|^[.,]\d+$")


def _clean_number_text(text: str) -> tuple[str, set, bool]:
    flags: set = set()
    s = text.strip().replace(" ", " ").replace("−", "-").replace("–", "-")
    if _CURRENCY.search(s):
        flags.add("currency")
        s = _CURRENCY.sub("", s)
    s = s.strip()
    if s.endswith("%"):
        flags.add("percent")
        s = s[:-1].strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
        flags.add("paren_neg")
    if s.startswith("-"):
        neg, s = True, s[1:].strip()
    elif s.endswith("-"):
        neg, s = True, s[:-1].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = s.replace("'", "").replace(" ", "")
    return s, flags, neg


def read_number_cell(cell: Any, decimal: Optional[str] = None) -> tuple[Optional[float], set]:
    """Read one cell as a number. Returns (float | None, flags).

    Flags: ``obj`` (already numeric), ``currency``, ``percent``, ``paren_neg``,
    ``dec_comma`` / ``dec_dot`` (this very cell proves the decimal mark),
    ``ambiguous`` (``1,234`` or ``1.234`` - thousands or decimals?), ``blank``,
    ``text`` (not a number at all).
    """
    if _is_blank(cell):
        return None, {"blank"}
    if isinstance(cell, bool):
        return None, {"text"}
    if isinstance(cell, (int, float)):
        return float(cell), {"obj"}
    if hasattr(cell, "year"):
        return None, {"text"}
    s, flags, neg = _clean_number_text(str(cell))
    if not s or not _NUM_BODY.match(s):
        return None, {"text"}
    sign = -1.0 if neg else 1.0
    has_dot, has_comma = "." in s, "," in s

    def done(txt: str, extra: str = "") -> tuple[Optional[float], set]:
        try:
            v = sign * float(txt)
        except ValueError:
            return None, {"text"}
        return v, flags | ({extra} if extra else set())

    if decimal == ",":
        return done(s.replace(".", "").replace(",", "."))
    if decimal == ".":
        return done(s.replace(",", ""))

    if has_dot and has_comma:
        if s.rfind(",") > s.rfind("."):
            return done(s.replace(".", "").replace(",", "."), "dec_comma")
        return done(s.replace(",", ""), "dec_dot")
    if has_comma:
        parts = s.split(",")
        if len(parts) > 2:
            return done(s.replace(",", ""), "dec_dot")           # 1,234,567
        if len(parts[1]) == 3 and parts[0] not in ("", "0"):
            return None, flags | {"ambiguous"}                     # 1,234
        return done(s.replace(",", "."), "dec_comma")
    if has_dot:
        parts = s.split(".")
        if len(parts) > 2:
            return done(s.replace(".", ""), "dec_comma")          # 1.234.567
        if len(parts[1]) == 3 and parts[0] not in ("", "0"):
            return None, flags | {"ambiguous"}                     # 1.234
        return done(s, "dec_dot")
    return done(s)


def analyze_number_column(cells: Iterable[Any], *, decimal: Optional[str] = None) -> dict:
    """Judge one column of quantities. See ``analyze_date_column`` for the idiom."""
    cells = list(cells)
    nonblank = [c for c in cells if not _is_blank(c)]
    n = len(nonblank)
    proof_comma = proof_dot = ambiguous = 0
    currency = percent = 0
    amb_examples: list = []
    for c in nonblank:
        _, fl = read_number_cell(c, None)
        if "dec_comma" in fl:
            proof_comma += 1
        if "dec_dot" in fl:
            proof_dot += 1
        if "ambiguous" in fl:
            ambiguous += 1
            if len(amb_examples) < 3 and str(c) not in amb_examples:
                amb_examples.append(str(c))
        if "currency" in fl:
            currency += 1
        if "percent" in fl:
            percent += 1

    resolved, source, conflict = decimal, ("user" if decimal else None), False
    if decimal is None:
        if proof_comma and proof_dot:
            conflict = True
        elif proof_comma:
            resolved, source = ",", "data"
        elif proof_dot:
            resolved, source = ".", "data"

    values: list[Optional[float]] = []
    still_amb = parsed = 0
    bad_examples: list = []
    for c in cells:
        if _is_blank(c):
            values.append(None)
            continue
        v, fl = read_number_cell(c, resolved)
        if "ambiguous" in fl:
            still_amb += 1
        if v is not None:
            parsed += 1
        elif "ambiguous" not in fl and len(bad_examples) < 3:
            bad_examples.append(str(c))
        values.append(v)

    ambiguous_examples = []
    for raw in amb_examples:
        comma, _ = read_number_cell(raw, ",")
        dot, _ = read_number_cell(raw, ".")
        ambiguous_examples.append({"raw": raw, "decimal_comma": comma, "decimal_dot": dot})

    nums = [v for v in values if v is not None]
    return {
        "values": values,
        "n_nonblank": n,
        "n_parsed": parsed,
        "rate": (parsed + still_amb) / n if n else 0.0,
        "decimal": resolved,
        "decimal_source": source,
        "format_conflict": conflict,
        "ambiguous": ambiguous,
        "unresolved": bool(still_amb) and resolved is None,
        "ambiguous_examples": ambiguous_examples,
        "currency_share": currency / n if n else 0.0,
        "percent_share": percent / n if n else 0.0,
        "negative_share": (sum(1 for v in nums if v < 0) / len(nums)) if nums else 0.0,
        "zero_share": (sum(1 for v in nums if v == 0) / len(nums)) if nums else 0.0,
        "constant": len(set(nums)) <= 1 and len(nums) > 1,
        "all_integers": bool(nums) and all(float(v).is_integer() for v in nums),
        "min": min(nums) if nums else None,
        "max": max(nums) if nums else None,
        "unparsed_examples": bad_examples,
        "n_unparsed": n - parsed - still_amb,
    }


# ── Product codes ────────────────────────────────────────────────────────────

def code_text(cell: Any) -> str:
    """A product code as text, without the ``.0`` a spreadsheet float adds."""
    if _is_blank(cell):
        return ""
    if isinstance(cell, bool):
        return str(cell)
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    if isinstance(cell, str):
        return cell.strip()
    return str(cell).strip()


_FLOAT_CODE = re.compile(r"^-?\d+\.0+$")


def is_float_code(cell: Any) -> bool:
    if isinstance(cell, float):
        return cell.is_integer()
    return isinstance(cell, str) and bool(_FLOAT_CODE.match(cell.strip()))


def variant_key(text: str) -> str:
    """Two spellings of one code share this key (spacing and case are ignored)."""
    return re.sub(r"\s+", " ", text.strip()).upper()
