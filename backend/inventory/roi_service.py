"""
ROI tracking service for inventory PO generation.

Logs every PO export and provides cumulative ROI metrics so clients
can see the value Faro has generated for their operation over time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Statuses that mean "the buyer decided to order this line".
_ORDERED = ("approved", "modified")


def _ordered_qty(item: dict) -> float:
    """
    Units actually ordered for a line. When the buyer kept/modified the line we
    use cantidad_final; we fall back to cantidad_recomendada for legacy callers
    that don't send a final quantity.
    """
    if item.get("cantidad_final") is not None:
        return float(item.get("cantidad_final") or 0)
    return float(item.get("cantidad_recomendada") or 0)


def log_po_generation(tenant_id: str, session_id: str, items: list[dict]) -> dict:
    """
    Called every time a user exports a PO.
    Records the buyer's actual decisions per line for ROI / adoption tracking.

    items: list of decision dicts. Each may carry:
        sku, display_name, proveedor, signal,
        cantidad_recomendada (what Faro suggested),
        cantidad_final        (what the buyer kept),
        costo_unitario,
        status ∈ approved | modified | rejected.

    Legacy callers (server-side CSV export) pass status-less items already
    filtered to the order; those are treated as 'approved'.
    """
    # Normalize: default missing status to 'approved' (legacy behaviour).
    norm: list[dict] = []
    for i in items:
        status = (i.get("status") or "approved").lower()
        if status not in ("approved", "modified", "rejected"):
            status = "approved"
        norm.append({**i, "status": status})

    ordered = [i for i in norm if i["status"] in _ORDERED]

    suggested_count = len(norm)
    approved_count  = len(ordered)
    modified_count  = sum(1 for i in norm if i["status"] == "modified")
    rejected_count  = sum(1 for i in norm if i["status"] == "rejected")

    # Header aggregates describe the *order* (approved/modified lines only).
    sku_count         = approved_count
    total_units       = sum(_ordered_qty(i) for i in ordered)
    skus_pedir_ya     = sum(1 for i in ordered if i.get("signal") == "PEDIR_YA")
    skus_pedir_pronto = sum(1 for i in ordered if i.get("signal") == "PEDIR_PRONTO")

    # total_value: sum of (units ordered × costo_unitario) where cost is available.
    value_parts: list[float] = []
    for i in ordered:
        cost = i.get("costo_unitario")
        if cost is not None:
            value_parts.append(_ordered_qty(i) * float(cost))
    total_value: float | None = sum(value_parts) if value_parts else None

    inserted = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value,
                skus_pedir_ya, skus_pedir_pronto,
                suggested_count, approved_count, modified_count, rejected_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (tenant_id, session_id, sku_count, total_units, total_value,
         skus_pedir_ya, skus_pedir_pronto,
         suggested_count, approved_count, modified_count, rejected_count),
    )

    # Persist every line (including rejected) so adoption is auditable per SKU.
    if inserted and norm:
        po_log_id = inserted["id"]
        for i in norm:
            try:
                execute(
                    """INSERT INTO inventory_po_items
                           (po_log_id, tenant_id, sku, display_name, proveedor,
                            signal, cantidad_recomendada, cantidad_final,
                            costo_unitario, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (po_log_id, tenant_id, str(i.get("sku") or ""),
                     i.get("display_name"), i.get("proveedor"), i.get("signal"),
                     float(i.get("cantidad_recomendada") or 0),
                     _ordered_qty(i) if i["status"] in _ORDERED else 0.0,
                     (float(i["costo_unitario"]) if i.get("costo_unitario") is not None else None),
                     i["status"]),
                )
            except Exception as e:
                log.warning("log_po_generation: skipped line sku=%s err=%s", i.get("sku"), e)

    if inserted:
        return dict(inserted)
    return {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "sku_count": sku_count,
        "total_units": total_units,
        "total_value": total_value,
        "skus_pedir_ya": skus_pedir_ya,
        "skus_pedir_pronto": skus_pedir_pronto,
        "suggested_count": suggested_count,
        "approved_count": approved_count,
        "modified_count": modified_count,
        "rejected_count": rejected_count,
    }


def get_roi_summary(tenant_id: str) -> dict:
    """
    Returns accumulated ROI metrics across all time for a given tenant.
    """
    agg = query_one(
        """SELECT
               COUNT(*)::int                    AS total_pos_generated,
               COALESCE(SUM(skus_pedir_ya), 0)::int  AS total_skus_protected,
               COALESCE(SUM(total_units), 0)    AS total_units_ordered,
               COALESCE(SUM(total_value), 0)    AS estimated_value_protected,
               COALESCE(SUM(suggested_count), 0)::int AS total_suggested,
               COALESCE(SUM(approved_count), 0)::int  AS total_approved,
               COALESCE(SUM(rejected_count), 0)::int  AS total_rejected,
               MIN(generated_at)                AS first_po_at,
               MAX(generated_at)                AS last_po_at
           FROM inventory_po_log
           WHERE tenant_id = %s""",
        (tenant_id,),
    )

    now = datetime.now(tz=timezone.utc)

    # Month boundaries (UTC)
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Last month start/end
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)

    this_month_count_row = query_one(
        "SELECT COUNT(*)::int AS cnt FROM inventory_po_log WHERE tenant_id = %s AND generated_at >= %s",
        (tenant_id, this_month_start),
    )
    last_month_count_row = query_one(
        """SELECT COUNT(*)::int AS cnt FROM inventory_po_log
           WHERE tenant_id = %s AND generated_at >= %s AND generated_at < %s""",
        (tenant_id, last_month_start, this_month_start),
    )

    first_po_at = agg.get("first_po_at") if agg else None
    last_po_at  = agg.get("last_po_at")  if agg else None

    active_days = 0
    if first_po_at and last_po_at and first_po_at != last_po_at:
        # Both may be timezone-aware or naive depending on DB config; normalise to UTC
        def _to_dt(v):
            if isinstance(v, datetime):
                return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
            return datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc)
        active_days = max(0, (_to_dt(last_po_at) - _to_dt(first_po_at)).days)

    total_suggested = int(agg.get("total_suggested") or 0) if agg else 0
    total_approved  = int(agg.get("total_approved")  or 0) if agg else 0
    total_rejected  = int(agg.get("total_rejected")  or 0) if agg else 0
    # Adoption rate: of the recommendations the buyer actually acted on, what
    # share did they keep/order? Only defined once we have decision data — older
    # rows (pre-decision-tracking) contribute 0 to both sides and don't distort it.
    adoption_rate = (total_approved / total_suggested) if total_suggested > 0 else None

    return {
        "total_pos_generated":      int(agg.get("total_pos_generated") or 0) if agg else 0,
        "total_skus_protected":     int(agg.get("total_skus_protected") or 0) if agg else 0,
        "total_units_ordered":      float(agg.get("total_units_ordered") or 0) if agg else 0.0,
        "estimated_value_protected": float(agg.get("estimated_value_protected") or 0) if agg else 0.0,
        "total_suggested":          total_suggested,
        "total_approved":           total_approved,
        "total_rejected":           total_rejected,
        "adoption_rate":            adoption_rate,
        "first_po_at":              first_po_at.isoformat() if isinstance(first_po_at, datetime) else (str(first_po_at) if first_po_at else None),
        "last_po_at":               last_po_at.isoformat()  if isinstance(last_po_at,  datetime) else (str(last_po_at)  if last_po_at  else None),
        "active_days":              active_days,
        "pos_this_month":           int(this_month_count_row.get("cnt") or 0) if this_month_count_row else 0,
        "pos_last_month":           int(last_month_count_row.get("cnt") or 0) if last_month_count_row else 0,
    }


def get_po_history(tenant_id: str, limit: int = 20) -> list[dict]:
    """Returns recent PO generation events for the history panel."""
    rows = query(
        """SELECT id, session_id, generated_at, sku_count, total_units,
                  total_value, skus_pedir_ya, skus_pedir_pronto,
                  reception_status, received_at
           FROM inventory_po_log
           WHERE tenant_id = %s
           ORDER BY generated_at DESC
           LIMIT %s""",
        (tenant_id, limit),
    )
    result = []
    for row in rows:
        r = dict(row)
        # Serialize datetimes to ISO strings for JSON
        for k in ("generated_at", "received_at"):
            if isinstance(r.get(k), datetime):
                r[k] = r[k].isoformat()
        result.append(r)
    return result
