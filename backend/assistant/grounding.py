"""Grounding guard: every figure in a reply must exist in what the model read.

The persona prompt already says "never invent a number". A prompt is a
request, not a guarantee, and the failure it guards against is the expensive
one — a confident "pide 780" when the data says 78. So after the model answers,
every number in the reply is checked against the numbers that were actually in
front of it: the account context, the tool results and the user's own message.

What counts as "the same number" is deliberately forgiving about PRESENTATION
and strict about VALUE:

  * either decimal convention (`25,430` / `25.430` / `25 430` / `25430`);
  * rounding to the precision the reply wrote (`164` for 164.41, `0.2` for 0.18);
  * a fraction shown as a percentage (`86%` for 0.8574);
  * thousands / millions shorthand (`25 mil` for 25,430, `1.2 M` for 1,180,000).

What is NOT checked, because it is not a quantity: dates and times, SKU and
order codes (`SKU-001`, `OC-000012`), digits glued to letters (`1L`, `500g`),
years, and integers up to 10 (list numbering, "two products").

The guard reports; `core.py` decides what to do (one corrective retry, then an
explicit note to the user listing what could not be verified).
"""
from __future__ import annotations

import math
import re

# Skipped before numbers are read: dates, times, codes.
_SKIP_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:?\d{2}|Z)?)?"),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b\d{1,2}:\d{2}\b"),
    re.compile(r"[A-Za-z_#][A-Za-z_]*[-_]?\d[\w-]*"),     # SKU-001, OC-000012, A1, sess_ab12
    re.compile(r"\b\d+[A-Za-z]+\w*"),                      # 1L, 500g, 3x
)

_NUMBER = re.compile(
    r"(?<![\w.,])"
    r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)*)"
    r"(?:\s*(%|mil\b|k\b|K\b|M\b|millones\b|mill(?:o|\u00f3)n\b|million\b|thousand\b))?"
)

_TRIVIAL_MAX = 10


def _strip_skipped(text: str) -> str:
    for pat in _SKIP_PATTERNS:
        text = pat.sub(" ", text)
    return text


def _interpretations(token: str) -> set[float]:
    """Every value a written number could mean, across both conventions."""
    t = token.replace(" ", "").replace(" ", "").replace(" ", "")
    out: set[float] = set()
    if t.isdigit():
        return {float(t)}
    has_dot, has_comma = "." in t, "," in t
    if has_dot and has_comma:
        dec = "." if t.rfind(".") > t.rfind(",") else ","
        thou = "," if dec == "." else "."
        try:
            out.add(float(t.replace(thou, "").replace(dec, ".")))
        except ValueError:
            pass
        return out
    sep = "." if has_dot else ","
    parts = t.split(sep)
    # As a thousands separator: every group after the first has 3 digits.
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
        out.add(float("".join(parts)))
    # As a decimal mark: exactly one separator.
    if len(parts) == 2:
        try:
            out.add(float(f"{parts[0]}.{parts[1]}"))
        except ValueError:
            pass
    return out


def _decimals_written(token: str) -> int:
    """How many decimals the writer showed, under the decimal reading."""
    t = token.replace(" ", "")
    m = re.search(r"[.,](\d+)$", t)
    if not m:
        return 0
    # "25.430" is more likely thousands than three decimals; treat 3-digit
    # tails as integers for the precision check (the decimal reading is still
    # tried through _interpretations, at 3 decimals of tolerance).
    return 0 if len(m.group(1)) == 3 else len(m.group(1))


def extract_numbers(text: str) -> list[tuple[str, set[float], int, str]]:
    """(token as written, possible values, decimals shown, suffix)."""
    clean = _strip_skipped(text or "")
    found = []
    for m in _NUMBER.finditer(clean):
        token, suffix = m.group(1), (m.group(2) or "").lower()
        values = _interpretations(token)
        if values:
            found.append((m.group(0).strip(), values, _decimals_written(token), suffix))
    return found


def evidence_values(*texts: str) -> list[float]:
    """Every number in the evidence, plus the presentations a reply may use."""
    base: set[float] = set()
    for text in texts:
        if not text:
            continue
        # Evidence is read WITHOUT skipping codes: a "7" in "lead time 7 days"
        # and in "SKU-007" are different, but missing a genuine figure costs a
        # false alarm while admitting one costs nothing here.
        for m in _NUMBER.finditer(_strip_skipped(text)):
            base |= _interpretations(m.group(1))
        # JSON numbers with exponents or negatives, as tools emit them.
        for m in re.finditer(r"-?\d+\.\d+(?:[eE][-+]?\d+)?", text):
            try:
                base.add(abs(float(m.group(0))))
            except ValueError:
                pass
    out: set[float] = set()
    for v in base:
        out.add(v)
        if 0 < v <= 1.5:
            out.add(v * 100)          # 0.8574 -> 85.74 (%)
        if v >= 1000:
            out.add(v / 1000)         # 25,430 -> 25.43 (mil / k)
        if v >= 1_000_000:
            out.add(v / 1_000_000)    # 1,180,000 -> 1.18 (M)
    return sorted(out)


def _matches(values: set[float], decimals: int, evidence: list[float]) -> bool:
    for v in values:
        # Tolerance: half a unit at the precision the reply wrote, plus a small
        # relative slack for rounding chains (164.41 -> "164", 0.85 -> "85%").
        tol = 0.5 * (10 ** -decimals) + 1e-9
        for e in evidence:
            if abs(v - e) <= max(tol, 0.006 * abs(e)):
                return True
    return False


def unverified_numbers(reply: str, *evidence_texts: str) -> list[str]:
    """The figures in `reply` that appear nowhere in the evidence."""
    evidence = evidence_values(*evidence_texts)
    bad: list[str] = []
    for written, values, decimals, suffix in extract_numbers(reply):
        # Small counts and years are not claims worth a false alarm. A
        # percentage is always a claim ("5%" must come from somewhere).
        if suffix != "%" and all(
            v.is_integer() and (0 <= v <= _TRIVIAL_MAX or 1990 <= v <= 2100) for v in values
        ):
            continue
        # "25 mil" / "1.2 M" are compared against the /1000 and /1e6 variants
        # evidence_values() already added, so the value is used as written.
        if any(math.isnan(v) for v in values):
            continue
        if not _matches(values, decimals, evidence) and written not in bad:
            bad.append(written)
    return bad
