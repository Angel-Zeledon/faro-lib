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


def log_po_generation(tenant_id: str, session_id: str, items: list[dict]) -> dict:
    """
    Called every time a user exports a PO.
    Records what was in the PO for ROI tracking.

    items: list of InventoryStatusItem dicts (those included in the PO,
           already filtered to PEDIR_YA / PEDIR_PRONTO with cantidad_recomendada > 0).
    """
    sku_count       = len(items)
    total_units     = sum(float(i.get("cantidad_recomendada") or 0) for i in items)
    skus_pedir_ya   = sum(1 for i in items if i.get("signal") == "PEDIR_YA")
    skus_pedir_pronto = sum(1 for i in items if i.get("signal") == "PEDIR_PRONTO")

    # total_value: sum of (cantidad_recomendada × costo_unitario) where cost is available
    total_value: float | None = None
    value_parts: list[float] = []
    for i in items:
        qty  = float(i.get("cantidad_recomendada") or 0)
        cost = i.get("costo_unitario")
        if cost is not None:
            value_parts.append(qty * float(cost))
    if value_parts:
        total_value = sum(value_parts)

    execute(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value,
                skus_pedir_ya, skus_pedir_pronto)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (tenant_id, session_id, sku_count, total_units,
         total_value, skus_pedir_ya, skus_pedir_pronto),
    )

    row = query_one(
        "SELECT * FROM inventory_po_log WHERE tenant_id = %s ORDER BY generated_at DESC LIMIT 1",
        (tenant_id,),
    )
    return dict(row) if row else {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "sku_count": sku_count,
        "total_units": total_units,
        "total_value": total_value,
        "skus_pedir_ya": skus_pedir_ya,
        "skus_pedir_pronto": skus_pedir_pronto,
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

    return {
        "total_pos_generated":      int(agg.get("total_pos_generated") or 0) if agg else 0,
        "total_skus_protected":     int(agg.get("total_skus_protected") or 0) if agg else 0,
        "total_units_ordered":      float(agg.get("total_units_ordered") or 0) if agg else 0.0,
        "estimated_value_protected": float(agg.get("estimated_value_protected") or 0) if agg else 0.0,
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
                  total_value, skus_pedir_ya, skus_pedir_pronto
           FROM inventory_po_log
           WHERE tenant_id = %s
           ORDER BY generated_at DESC
           LIMIT %s""",
        (tenant_id, limit),
    )
    result = []
    for row in rows:
        r = dict(row)
        # Serialize datetime to ISO string for JSON
        if isinstance(r.get("generated_at"), datetime):
            r["generated_at"] = r["generated_at"].isoformat()
        result.append(r)
    return result
