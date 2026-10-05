"""Forecast by analogy: a person says a new product will sell like others.

A product with too little history never reaches the engine (`min_history`), so
the status screen showed it as SIN_DATOS: no signal, no quantity. A planner who
knows the business can say "the new item sells like A and B, a bit more". This
ledger records that statement; `plan_analogies` (pure) turns it into a stand-in
forecast from the references' own champion forecasts, using
`forecasting_core.inference.analogy` for the arithmetic.

Rules that keep it honest:

1. **Append-only.** A row is never edited or deleted. Undo stamps
   `reverted_by`/`reverted_at`; defining it again is a new row. One live
   analogy per product (a partial unique index backs the check).
2. **Never a trained forecast.** The status row says `forecast_source:
   'analogy'` and `low_confidence: true`, names the references, the factor and
   the band widening in `analogy_applied`, and the band is wider than the
   references' own. Nothing here trains, stores or caches a forecast.
3. **Only when there is nothing better.** It serves a product ONLY while the
   session has no trained forecast for it (the engine drops series shorter than
   `min_history`, so "absent from the session's forecasts" IS "too little
   history"). The moment the product has one, the trained model takes over; the
   first time that is seen the ledger row is stamped `superseded_at` and the
   status row says `analogy_retired`.
4. **Loud when it cannot apply.** References with no forecast in the session are
   named (`analogy_unavailable`); the row then stays SIN_DATOS exactly as before.
5. **No analogy defined = nothing changes.** Every number is what it was.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import psycopg2.extras

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError

log = logging.getLogger(__name__)

MAX_NOTE_LENGTH = 300
MAX_SKU_LENGTH = 200


def _limits() -> tuple[int, int, float, float]:
    from forecasting_core.inference import analogy as engine
    return engine.MIN_REFERENCES, engine.MAX_REFERENCES, engine.MIN_SCALE, engine.MAX_SCALE


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


_COLS = """a.id, a.new_sku, a.reference_skus, a.scale_factor, a.start_date, a.note,
           a.created_by, a.created_at, a.reverted_by, a.reverted_at,
           a.superseded_at, a.superseded_session_id,
           COALESCE(NULLIF(u.full_name, ''), split_part(u.email, '@', 1)) AS created_by_name"""


def _fmt(row: dict) -> dict:
    d = dict(row)
    for k in ("start_date", "created_at", "reverted_at", "superseded_at"):
        d[k] = _iso(d.get(k))
    d["reference_skus"] = list(d.get("reference_skus") or [])
    return d


# ── The ledger ───────────────────────────────────────────────────────────────

def create(tenant_id: str, user_id: str, *, new_sku: str, reference_skus: list,
           scale_factor: float = 1.0, start_date=None, note: Optional[str] = None) -> dict:
    new_sku = (new_sku or "").strip()
    if not new_sku or len(new_sku) > MAX_SKU_LENGTH:
        raise AppError("analogy_sku_required", "Choose the new product")
    refs: list[str] = []
    for r in reference_skus or []:
        r = str(r or "").strip()
        if r and r not in refs:
            refs.append(r)
    lo, hi, smin, smax = _limits()
    if not (lo <= len(refs) <= hi):
        raise AppError("analogy_references_invalid",
                       f"Choose between {lo} and {hi} reference products",
                       params={"min": lo, "max": hi})
    if new_sku in refs:
        raise AppError("analogy_reference_is_self",
                       "A product cannot be its own reference", params={"sku": new_sku})
    try:
        scale = float(scale_factor)
    except (TypeError, ValueError):
        scale = float("nan")
    if not (smin <= scale <= smax):          # NaN fails this too
        raise AppError("analogy_scale_invalid",
                       f"The scale factor must be between {smin} and {smax}",
                       params={"min": smin, "max": smax})
    start = None
    if start_date not in (None, ""):
        try:
            start = start_date if isinstance(start_date, date) else date.fromisoformat(str(start_date)[:10])
        except ValueError:
            raise AppError("date_invalid_iso", "start_date must be an ISO date (YYYY-MM-DD)",
                           params={"field": "start_date"})
    clean_note = (note or "").strip()[:MAX_NOTE_LENGTH] or None

    with transaction() as conn:
        clash = query_one(
            "SELECT id FROM sku_analogies WHERE tenant_id = %s AND new_sku = %s "
            "AND reverted_at IS NULL LIMIT 1", (tenant_id, new_sku), conn=conn)
        if clash:
            raise AppError("analogy_already_active",
                           "This product already has an analogy; undo it first to change it",
                           status_code=409, params={"sku": new_sku, "analogy_id": clash["id"]})
        row = query_one(
            """INSERT INTO sku_analogies
                   (tenant_id, new_sku, reference_skus, scale_factor, start_date, note, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (tenant_id, new_sku, psycopg2.extras.Json(refs), scale, start, clean_note, user_id),
            conn=conn)
    return get(tenant_id, row["id"])


def get(tenant_id: str, analogy_id: str) -> dict:
    row = query_one(
        f"""SELECT {_COLS} FROM sku_analogies a
              LEFT JOIN users u ON u.id = a.created_by
             WHERE a.id = %s AND a.tenant_id = %s""", (analogy_id, tenant_id))
    if not row:
        raise AppError("analogy_not_found", "Analogy not found", status_code=404)
    return _fmt(row)


def list_for_tenant(tenant_id: str, sku: Optional[str] = None,
                    include_reverted: bool = False) -> list[dict]:
    where, params = ["a.tenant_id = %s"], [tenant_id]
    if sku:
        where.append("a.new_sku = %s")
        params.append(sku)
    if not include_reverted:
        where.append("a.reverted_at IS NULL")
    rows = query(
        f"""SELECT {_COLS} FROM sku_analogies a
              LEFT JOIN users u ON u.id = a.created_by
             WHERE {' AND '.join(where)}
             ORDER BY a.created_at DESC, a.id""", tuple(params))
    return [_fmt(r) for r in rows]


def revert(tenant_id: str, analogy_id: str, user_id: str) -> dict:
    """Undo an analogy. The row stays, stamped; the next status read ignores it."""
    changed = query_one(
        """UPDATE sku_analogies SET reverted_by = %s, reverted_at = NOW()
            WHERE id = %s AND tenant_id = %s AND reverted_at IS NULL
        RETURNING id""", (user_id, analogy_id, tenant_id))
    if not changed:
        existing = get(tenant_id, analogy_id)       # 404 when it is not this tenant's
        raise AppError("analogy_already_reverted", "That analogy was already undone",
                       status_code=409, params={"analogy_id": existing["id"]})
    return get(tenant_id, analogy_id)


def active_by_sku(tenant_id: str) -> dict[str, dict]:
    """The live (not undone) analogy of every product that has one."""
    return {r["new_sku"]: r for r in list_for_tenant(tenant_id)}


def mark_superseded(tenant_id: str, analogy_ids: list[str], session_id: str) -> None:
    """Stamp, once, that a trained forecast took over. Idempotent."""
    if not analogy_ids:
        return
    execute(
        """UPDATE sku_analogies SET superseded_at = NOW(), superseded_session_id = %s
            WHERE tenant_id = %s AND id = ANY(%s) AND superseded_at IS NULL""",
        (session_id, tenant_id, list(analogy_ids)))


# ── Serving (pure) ───────────────────────────────────────────────────────────

def champion_points(model_forecasts: dict, preferred: Optional[str]) -> list[dict]:
    """The forecast points a product is bought from: its champion model's, or,
    when the champion is unknown, the per-date mean of the models that carry
    points. Same preference as the status screen's own model choice."""
    if not model_forecasts:
        return []
    if preferred:
        chosen = model_forecasts.get(preferred)
        if chosen and (chosen.get("forecast") or []):
            return list(chosen["forecast"])
    series = [m.get("forecast") or [] for m in model_forecasts.values()]
    series = [s for s in series if s]
    if len(series) <= 1:
        return list(series[0]) if series else []
    by_date: dict[str, list[dict]] = {}
    for s in series:
        for p in s:
            if p.get("date") is not None:
                by_date.setdefault(str(p["date"])[:10], []).append(p)
    out = []
    for d in sorted(by_date):
        group = by_date[d]
        point = {"date": d}
        for key in ("value", "lower", "upper", "q10", "q90"):
            vals = [float(p[key]) for p in group if p.get(key) is not None]
            if vals:
                point[key] = sum(vals) / len(vals)
        out.append(point)
    return out


def _has_trained_forecast(model_forecasts: Optional[dict]) -> bool:
    return any((m or {}).get("forecast") for m in (model_forecasts or {}).values())


def plan_analogies(analogies: dict[str, dict], forecasts: dict, best_model: dict
                   ) -> tuple[dict, dict, dict]:
    """Decide, for every live analogy, what the status screen does with it.

    `forecasts` is the session's per-SKU forecast dict (already rolled up per
    SKU), `best_model` the champion per SKU. Returns
    ``(serving, retired, unavailable)`` keyed by the new SKU:

    * ``serving``    - no trained forecast and at least one reference has one:
      ``{"model_forecasts": {"analogy": {"forecast": [...]}}, "applied": {...}}``,
      ready to stand in for the SKU's own model forecasts.
    * ``retired``    - the product now has a trained forecast: the analogy row.
    * ``unavailable``- no trained forecast and NO reference has points either:
      ``{"analogy_id", "references_missing"}``. The SKU stays as it was.
    """
    from forecasting_core.inference import analogy as engine

    serving: dict[str, dict] = {}
    retired: dict[str, dict] = {}
    unavailable: dict[str, dict] = {}
    for sku, a in analogies.items():
        if _has_trained_forecast(forecasts.get(sku)):
            retired[sku] = a
            continue
        refs = [{"sku": r, "points": champion_points(forecasts.get(r) or {}, best_model.get(r))}
                for r in a["reference_skus"]]
        result = engine.build_analogy_forecast(
            refs, scale_factor=a["scale_factor"], start_date=a.get("start_date"))
        if not result["points"]:
            unavailable[sku] = {"analogy_id": a["id"],
                                "references_missing": list(a["reference_skus"])}
            continue
        serving[sku] = {
            "model_forecasts": {engine.METHOD: {"forecast": result["points"]}},
            "applied": {
                "analogy_id": a["id"],
                "references": result["references_used"],
                "references_missing": result["references_empty"],
                "scale_factor": result["scale_factor"],
                "start_date": a.get("start_date"),
                "alignment": result["alignment"],
                "band_widen_factor": result["widen_factor"],
                "min_relative_sigma": result["min_relative_sigma"],
                "note": a.get("note"),
                "created_by_name": a.get("created_by_name"),
                "created_at": a.get("created_at"),
            },
        }
    return serving, retired, unavailable
