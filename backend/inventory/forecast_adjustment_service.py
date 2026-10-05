"""Manual forecast adjustments: what a person changed, why, and whether it paid off.

An analyst who knows something the model cannot ("a promotion starts on the
20th") can move a product's forecast for a period, by a percentage or by units.
Three rules keep that honest:

1. **Append-only.** An adjustment is never edited. A correction is a new row
   that supersedes the overlapping old one (`superseded_by`), so "who changed
   this number, when, and why" can always be answered. Entering 0% clears the
   period.
2. **Transparent.** The adjusted number reaches the purchase recommendation
   through the one place demand is decided, next to the declared events, and the
   row says so (`adjustments_applied`: who, how much, why). Nothing moves
   silently.
3. **Graded.** Once real sales arrive the adjusted forecast is compared with the
   model's own on the same points (`forecast_check/adjustment_value.py`), so the
   product can say whether a person's judgement beat the model, in aggregate, per
   person and per reason.

`forecast_overrides` (the older per-day `PATCH /sessions/{id}/overrides`) is a
mutable store nothing reads; this ledger replaces it for this purpose rather than
growing a second reading of it.

An absolute adjustment is entered as units over the range. It is converted ONCE,
at entry, into the percentage of the unadjusted forecast it represents
(`pct = units / baseline_units`), and from then on every consumer — the planner
and the grader — applies the same percentage.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import math

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError

log = logging.getLogger(__name__)

# The list the analyst chooses from. Stable codes: the frontend renders the label.
REASONS = (
    "promotion", "price_change", "new_customer", "lost_customer", "seasonality",
    "supply_issue", "market_news", "data_error", "other",
)
MAX_NOTE_LENGTH = 300
# An adjustment cannot make demand negative, nor claim a tenfold jump.
MIN_PCT = -100.0
MAX_PCT = 1000.0
# Longest span one adjustment may cover.
MAX_SPAN_DAYS = 400


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _as_date(value, field: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError:
        raise AppError("date_invalid_iso", f"{field} must be an ISO date (YYYY-MM-DD)",
                       params={"field": field})


def _baseline_units(tenant_id: str, session_id: str, sku: str,
                    start: date, end: date) -> float:
    """The unadjusted forecast of `sku` inside [start, end], summed over the
    model each series is bought from (and over stores, when the session has
    them)."""
    from backend.forecast_check.service import _champion_forecasts
    from backend.inventory.series import split_key
    total = 0.0
    lo, hi = start.isoformat(), end.isoformat()
    for key, series in _champion_forecasts(tenant_id, session_id).items():
        if split_key(key)[0] != sku:
            continue
        total += sum(v for d, v in series.items() if lo <= d <= hi)
    return total


def _name_sql() -> str:
    return "COALESCE(NULLIF(u.full_name, ''), split_part(u.email, '@', 1))"


_COLS = f"""a.id, a.session_id, a.sku, a.start_date, a.end_date, a.mode, a.value, a.pct,
            a.baseline_units, a.reason_code, a.reason_note, a.created_by, a.created_at,
            a.superseded_by, a.superseded_at, {_name_sql()} AS created_by_name"""


def _fmt(row: dict) -> dict:
    d = dict(row)
    for k in ("start_date", "end_date", "created_at", "superseded_at"):
        d[k] = _iso(d.get(k))
    return d


def create(tenant_id: str, session_id: str, user_id: str, *, sku: str, start_date, end_date,
           mode: str, value: float, reason_code: str, reason_note: Optional[str] = None) -> dict:
    sku = (sku or "").strip()
    if not sku:
        raise AppError("forecast_adjustment_sku_required", "Choose a product to adjust")
    if reason_code not in REASONS:
        raise AppError("forecast_adjustment_reason_invalid", "Choose a reason from the list",
                       params={"reason": reason_code})
    note = (reason_note or "").strip()[:MAX_NOTE_LENGTH] or None
    if reason_code == "other" and not note:
        raise AppError("forecast_adjustment_note_required",
                       "Say what the reason is when you choose 'other'")
    start, end = _as_date(start_date, "start_date"), _as_date(end_date, "end_date")
    if end < start:
        raise AppError("forecast_adjustment_dates_invalid", "The end date is before the start",
                       params={"start_date": start.isoformat(), "end_date": end.isoformat()})
    if (end - start).days > MAX_SPAN_DAYS:
        raise AppError("forecast_adjustment_dates_invalid", "That period is too long to adjust",
                       params={"start_date": start.isoformat(), "end_date": end.isoformat()})
    if mode not in ("percent", "absolute"):
        raise AppError("forecast_adjustment_mode_invalid", "Adjust by a percentage or by units",
                       params={"mode": mode})

    baseline = _baseline_units(tenant_id, session_id, sku, start, end)
    if baseline <= 0 and mode == "absolute":
        raise AppError(
            "forecast_adjustment_no_baseline",
            "The forecast has no demand for this product in that period, so units cannot be "
            "turned into a change; use a percentage", status_code=409,
            params={"sku": sku})
    if baseline <= 0 and not _has_series(tenant_id, session_id, sku):
        raise AppError("forecast_adjustment_sku_unknown",
                       "This forecast has no product with that code", status_code=404,
                       params={"sku": sku})
    pct = float(value) if mode == "percent" else float(value) / baseline * 100.0
    if not MIN_PCT <= pct <= MAX_PCT:
        raise AppError("forecast_adjustment_out_of_range",
                       "That change would take demand below zero or past ten times the forecast",
                       params={"pct": round(pct, 1)})

    with transaction() as conn:
        row = query_one(
            """INSERT INTO forecast_adjustments
                   (tenant_id, session_id, sku, start_date, end_date, mode, value, pct,
                    baseline_units, reason_code, reason_note, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (tenant_id, session_id, sku, start, end, mode, float(value), pct,
             baseline, reason_code, note, user_id), conn=conn)
        # The new row replaces whatever it overlaps for the same product: the
        # planner's latest word on a period is the one that applies. The old
        # rows stay, marked, so the history is whole.
        execute(
            """UPDATE forecast_adjustments
                  SET superseded_by = %s, superseded_at = NOW()
                WHERE tenant_id = %s AND session_id = %s AND sku = %s
                  AND id <> %s AND superseded_by IS NULL
                  AND start_date <= %s AND end_date >= %s""",
            (row["id"], tenant_id, session_id, sku, row["id"], end, start), conn=conn)
    return get(tenant_id, row["id"])


def _has_series(tenant_id: str, session_id: str, sku: str) -> bool:
    from backend.forecast_check.service import _champion_forecasts
    from backend.inventory.series import split_key
    return any(split_key(k)[0] == sku
               for k in _champion_forecasts(tenant_id, session_id))


def get(tenant_id: str, adjustment_id: str) -> dict:
    row = query_one(
        f"""SELECT {_COLS} FROM forecast_adjustments a
              LEFT JOIN users u ON u.id = a.created_by
             WHERE a.id = %s AND a.tenant_id = %s""", (adjustment_id, tenant_id))
    if not row:
        raise AppError("forecast_adjustment_not_found", "Adjustment not found", status_code=404)
    return _fmt(row)


def list_for_session(tenant_id: str, session_id: str, sku: Optional[str] = None,
                     include_superseded: bool = False) -> list[dict]:
    clauses, params = ["a.tenant_id = %s", "a.session_id = %s"], [tenant_id, session_id]
    if sku:
        clauses.append("a.sku = %s")
        params.append(sku)
    if not include_superseded:
        clauses.append("a.superseded_by IS NULL")
    rows = query(
        f"""SELECT {_COLS} FROM forecast_adjustments a
              LEFT JOIN users u ON u.id = a.created_by
             WHERE {' AND '.join(clauses)}
             ORDER BY a.start_date, a.created_at""", tuple(params))
    return [_fmt(r) for r in rows]


# ── Reaching the recommendation ──────────────────────────────────────────────

def active_by_sku(tenant_id: str, session_id: str, today: Optional[date] = None) -> dict[str, list[dict]]:
    """Current (not superseded) adjustments whose period has not fully passed,
    keyed by SKU. One query for the whole tenant, never per SKU."""
    today = today or date.today()
    rows = query(
        f"""SELECT {_COLS} FROM forecast_adjustments a
              LEFT JOIN users u ON u.id = a.created_by
             WHERE a.tenant_id = %s AND a.session_id = %s
               AND a.superseded_by IS NULL AND a.end_date >= %s AND a.pct <> 0""",
        (tenant_id, session_id, today))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["sku"], []).append(dict(r))
    return out


def demand_multiplier(adjustments: Optional[list[dict]], today: date,
                      lead_time_days: float) -> tuple[float, list[dict]]:
    """The factor an SKU's demand carries for the lead-time window starting
    today, and one entry per adjustment that touched it.

    Same blending as a declared event: an adjustment covering 4 of a 15-day
    window moves that window's demand by 4/15 of its percentage, never all of it
    (the other 11 days are ordinary days). Several adjustments compound.
    """
    if not adjustments or lead_time_days <= 0:
        return 1.0, []
    window_end = today + timedelta(days=lead_time_days)             # exclusive
    combined, applied = 1.0, []
    for adj in adjustments:
        lo = max(today, adj["start_date"])
        hi = min(window_end, adj["end_date"] + timedelta(days=1))
        overlap = max(0, (hi - lo).days)
        if overlap <= 0:
            continue
        fraction = min(1.0, overlap / float(lead_time_days))
        blended = 1.0 + fraction * (float(adj["pct"]) / 100.0)
        combined *= blended
        applied.append({
            "adjustment_id": adj["id"],
            "pct": round(float(adj["pct"]), 2),
            "mode": adj["mode"],
            "reason_code": adj["reason_code"],
            "reason_note": adj.get("reason_note"),
            "created_by": adj["created_by"],
            "created_by_name": adj.get("created_by_name"),
            "overlap_days": overlap,
            "window_days": int(math.ceil(lead_time_days)),
            "blended_multiplier": round(blended, 4),
        })
    return combined, applied
