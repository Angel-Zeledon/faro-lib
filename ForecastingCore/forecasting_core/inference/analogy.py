"""Forecast by analogy: a stand-in forecast for a product with no usable history.

A new product has no sales to train on, so the engine cannot forecast it. A
person who knows the business can say "it will sell like products A and B, a
bit more". This module turns that statement into a forecast series from the
reference products' own champion forecasts. It is NOT a trained model and it is
built so nothing downstream can mistake it for one:

* it is a pure function of the references' stored forecast points and a scale
  factor, with no learning anywhere;
* its uncertainty band is the references' own band, scaled, WIDENED by
  ``BAND_WIDEN_FACTOR`` (an analogy is strictly less certain than a model that
  was fitted and back-tested on the product itself), plus the disagreement
  between the references, with a floor of ``MIN_RELATIVE_SIGMA`` of the value so
  a single reference with a very tight band can never yield a falsely tight one;
* the result carries ``method = "analogy"`` and every parameter used, so the
  caller can name them on the row.

Alignment of the references onto the new product's periods:

``calendar`` (no ``start_date``)
    By calendar date: the new product's value on a date is the mean of the
    references' values on that same date.

``since_start`` (``start_date`` given)
    By periods since launch: every reference's series is shifted so that its
    first forecast point lands on ``start_date``, keeping the spacing between
    its points. The new product's k-th period then reads the k-th period of
    each reference. This reads a reference's forecast as if it were the start of
    a life curve, which is only as good as that assumption; the caller says so.

A date some references do not have is averaged over the ones that do (never
treated as zero); a date none has is simply absent from the output.

Pure Python: no pandas, no numpy, no I/O.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Iterable, Optional

METHOD = "analogy"

# How much wider than the references' own band an analogy's band is. 1.5 means
# the half-width (the distance from the value to its upper/q90 edge) is one and
# a half times the references'. A judgement, not a measurement: nobody has
# back-tested "it will sell like A" for this product, so the band must say the
# number is softer than a trained one. Raise it, never lower it, when in doubt.
BAND_WIDEN_FACTOR = 1.5

# Smallest relative sigma the band may have, as a fraction of the value. Applies
# when the references carry no band, or an implausibly tight one.
MIN_RELATIVE_SIGMA = 0.3

# z of the q90 / upper edge (the engine writes q90 = value + 1.2816 * sigma).
_Z90 = 1.2816

MIN_SCALE = 0.1
MAX_SCALE = 10.0
MIN_REFERENCES = 1
MAX_REFERENCES = 5

_BAND_KEYS = ("lower", "upper", "q10", "q90")
# +1 for the upper edges, -1 for the lower ones.
_BAND_SIGN = {"lower": -1.0, "q10": -1.0, "upper": 1.0, "q90": 1.0}


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _finite(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clean_series(points: Iterable[dict]) -> list[dict]:
    """Points with a usable date and value, sorted by date, one per date."""
    by_date: dict[date, dict] = {}
    for p in points or []:
        d = _as_date(p.get("date"))
        v = _finite(p.get("value"))
        if d is None or v is None:
            continue
        by_date[d] = {**p, "_date": d, "_value": max(0.0, v)}
    return [by_date[d] for d in sorted(by_date)]


def validate_scale_factor(scale_factor) -> float:
    s = _finite(scale_factor)
    if s is None or not (MIN_SCALE <= s <= MAX_SCALE):
        raise ValueError(f"scale_factor must be between {MIN_SCALE} and {MAX_SCALE}")
    return s


def build_analogy_forecast(
    references: list[dict],
    scale_factor: float = 1.0,
    start_date=None,
    widen_factor: float = BAND_WIDEN_FACTOR,
    min_relative_sigma: float = MIN_RELATIVE_SIGMA,
) -> dict:
    """The analog forecast for a new product.

    ``references`` is ``[{"sku": str, "points": [{"date", "value", and
    optionally "lower"/"upper"/"q10"/"q90"}]}]``: each reference's champion
    forecast points, as stored. Returns::

        {"method": "analogy", "alignment": "calendar" | "since_start",
         "scale_factor", "widen_factor", "min_relative_sigma",
         "references_used": [sku, ...], "references_empty": [sku, ...],
         "points": [{"date", "value", "lower", "upper", "q10", "q90",
                     "n_references"}]}

    ``points`` is empty (and ``references_used`` too) when no reference has a
    usable point: the caller must then NOT plan from an analogy.
    """
    scale = validate_scale_factor(scale_factor)
    if not (MIN_REFERENCES <= len(references or []) <= MAX_REFERENCES):
        raise ValueError(f"an analogy takes {MIN_REFERENCES} to {MAX_REFERENCES} references")
    if widen_factor < 1.0:
        raise ValueError("an analogy band may never be narrower than its references'")
    start = _as_date(start_date) if start_date not in (None, "") else None
    if start_date not in (None, "") and start is None:
        raise ValueError("start_date must be an ISO date")

    used: list[str] = []
    empty: list[str] = []
    # date -> list of that date's reference points
    columns: dict[date, list[dict]] = {}
    for ref in references:
        series = _clean_series(ref.get("points"))
        if not series:
            empty.append(str(ref.get("sku")))
            continue
        used.append(str(ref.get("sku")))
        first = series[0]["_date"]
        for p in series:
            d = p["_date"] if start is None else start + (p["_date"] - first)
            columns.setdefault(d, []).append(p)

    out: list[dict] = []
    for d in sorted(columns):
        pts = columns[d]
        values = [p["_value"] * scale for p in pts]
        mean = sum(values) / len(values)
        # Disagreement between references (sample sd; 0 with a single one).
        between = (math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
                   if len(values) > 1 else 0.0)
        floor = _Z90 * min_relative_sigma * mean
        point = {"date": d.isoformat(), "value": round(mean, 4), "n_references": len(pts)}
        for key in _BAND_KEYS:
            offsets = [abs(_finite(p.get(key)) - p["_value"]) * scale
                       for p in pts if _finite(p.get(key)) is not None]
            own = (sum(offsets) / len(offsets)) * widen_factor if offsets else 0.0
            # Independent sources of doubt add in quadrature.
            half = max(math.sqrt(own ** 2 + (_Z90 * between) ** 2), floor)
            edge = mean + _BAND_SIGN[key] * half
            point[key] = round(max(0.0, edge), 4)
        out.append(point)

    return {
        "method": METHOD,
        "alignment": "calendar" if start is None else "since_start",
        "scale_factor": scale,
        "widen_factor": widen_factor,
        "min_relative_sigma": min_relative_sigma,
        "references_used": used,
        "references_empty": empty,
        "points": out,
    }
