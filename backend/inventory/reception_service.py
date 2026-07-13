"""
PO reception (feature 1.4, docs/features_propuestas_faro_2026-07-05.md).

Closes the purchase loop: when the order physically arrives, the buyer records
what came in. Two effects that compound Faro's value over time:
  1. Stock self-corrects (received units are added to inventory_stock), so the
     semáforo keeps matching reality without manual stock edits.
  2. Faro learns each supplier's REAL lead time (order date → reception date),
     turning the user's guess into observed data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Line statuses that were actually ordered (mirrors roi_service._ORDERED)
_ORDERED = ("approved", "modified")

RECEIVABLE_STATES = ("pending", "partial")


def get_po(tenant_id: str, po_log_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
        (po_log_id, tenant_id),
    )


def get_po_items(tenant_id: str, po_log_id: str) -> list[dict]:
    return query(
        """SELECT id, sku, display_name, proveedor, signal, status,
                  cantidad_recomendada, cantidad_final, cantidad_recibida, costo_unitario,
                  bodega
           FROM inventory_po_items
           WHERE po_log_id = %s AND tenant_id = %s
           ORDER BY proveedor NULLS LAST, sku""",
        (po_log_id, tenant_id),
    )


def receive_po(
    tenant_id: str,
    po_log_id: str,
    user_id: str,
    lines: Optional[list[dict]] = None,
    received_at: Optional[datetime] = None,
) -> dict:
    """
    Record a reception for a PO.

    lines: [{sku, cantidad_recibida}] — omit entirely (or None) to mean
           "everything arrived complete" (each ordered line receives its
           cantidad_final). Lines not mentioned receive 0 for this event.

    Raises ValueError with a user-facing message on invalid state/input.
    """
    po = get_po(tenant_id, po_log_id)
    if not po:
        raise ValueError("Orden de compra no encontrada")
    if po.get("reception_status") not in RECEIVABLE_STATES:
        raise ValueError(
            f"Esta orden ya fue recibida (estado: {po.get('reception_status')})"
        )

    items = get_po_items(tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in _ORDERED]
    if not ordered:
        raise ValueError("Esta orden no tiene líneas pedidas que recibir")

    received_at = received_at or datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    generated_at = po["generated_at"]
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    # Compare by calendar day: a date-only reception (midnight) on the same day
    # the PO was generated is valid even if the PO has a later timestamp.
    if received_at.date() < generated_at.date():
        raise ValueError("La fecha de recepción no puede ser anterior a la fecha de la orden")

    # Resolve received qty per ordered SKU
    if lines is None:
        received_by_sku = {i["sku"]: float(i["cantidad_final"] or 0) for i in ordered}
    else:
        received_by_sku = {}
        ordered_skus = {i["sku"] for i in ordered}
        for ln in lines:
            sku = str(ln.get("sku") or "")
            if sku not in ordered_skus:
                raise ValueError(f"El SKU '{sku}' no está en esta orden")
            qty = float(ln.get("cantidad_recibida") or 0)
            if qty < 0:
                raise ValueError(f"Cantidad recibida negativa para '{sku}'")
            received_by_sku[sku] = qty
        for i in ordered:
            received_by_sku.setdefault(i["sku"], 0.0)

    # 1. Per-line: accumulate cantidad_recibida (partial receptions add up)
    for i in ordered:
        qty = received_by_sku[i["sku"]]
        execute(
            """UPDATE inventory_po_items
               SET cantidad_recibida = COALESCE(cantidad_recibida, 0) + %s
               WHERE id = %s AND tenant_id = %s""",
            (qty, i["id"], tenant_id),
        )

    # 2. Stock: add received units. Creates the stock row if the SKU is new.
    from backend.inventory import service as inv_svc
    for i in ordered:
        qty = received_by_sku[i["sku"]]
        if qty <= 0:
            continue
        existing = inv_svc.get_stock(tenant_id, i["sku"])
        if existing:
            execute(
                """UPDATE inventory_stock
                   SET stock_actual = stock_actual + %s, updated_at = NOW()
                   WHERE tenant_id = %s AND sku = %s AND bodega = %s""",
                (qty, tenant_id, i["sku"], i["bodega"]),
            )
        else:
            inv_svc.upsert_stock(tenant_id, i["sku"], {
                "stock_actual": qty,
                "display_name": i.get("display_name"),
                "proveedor": i.get("proveedor"),
                "bodega": i.get("bodega") or "principal",
            })
        # Point-in-time snapshot so /stock/{sku}/history reflects the arrival
        try:
            new_row = inv_svc.get_stock(tenant_id, i["sku"])
            execute(
                """INSERT INTO inventory_snapshots (tenant_id, sku, stock_actual)
                   VALUES (%s, %s, %s)""",
                (tenant_id, i["sku"], new_row["stock_actual"]),
            )
        except Exception as e:
            log.warning("reception snapshot failed sku=%s: %s", i["sku"], e)

    # 3. Header status
    fresh = get_po_items(tenant_id, po_log_id)
    fresh_ordered = [i for i in fresh if i["status"] in _ORDERED]
    fully = all(float(i["cantidad_recibida"] or 0) >= float(i["cantidad_final"] or 0)
                for i in fresh_ordered)
    any_received = any(float(i["cantidad_recibida"] or 0) > 0 for i in fresh_ordered)
    status = "received" if fully else ("partial" if any_received else "not_received")

    execute(
        """UPDATE inventory_po_log
           SET reception_status = %s, received_at = %s, received_by = %s
           WHERE id = %s AND tenant_id = %s""",
        (status, received_at, user_id, po_log_id, tenant_id),
    )

    # 4. Learn real lead times — one observation per supplier that delivered
    # something in this event (first reception only, so partials don't skew).
    lead_days = max(0.0, (received_at - generated_at).total_seconds() / 86400.0)
    observed_suppliers = sorted({
        (i.get("proveedor") or "").strip()
        for i in ordered
        if (i.get("proveedor") or "").strip() and received_by_sku[i["sku"]] > 0
    })
    if po.get("reception_status") == "pending":  # was pending before this event
        for prov in observed_suppliers:
            execute(
                """INSERT INTO supplier_lead_time_obs
                       (tenant_id, proveedor, po_log_id, lead_time_days)
                   VALUES (%s, %s, %s, %s)""",
                (tenant_id, prov, po_log_id, round(lead_days, 2)),
            )

    log.info("[reception] tenant=%s po=%s status=%s lead_days=%.1f suppliers=%s",
             tenant_id, po_log_id, status, lead_days, observed_suppliers)

    return {
        "po_log_id": po_log_id,
        "reception_status": status,
        "received_at": received_at.isoformat(),
        "lead_time_days": round(lead_days, 2),
        "suppliers_observed": observed_suppliers,
        "items": get_po_items(tenant_id, po_log_id),
    }


def get_supplier_scorecard(tenant_id: str) -> list[dict]:
    """
    Per-supplier performance: real lead time range (min-max observed, not a
    single misleading average), on-time rate (real <= declared), fill rate
    and value purchased. Anchored to suppliers with at least one recorded
    reception — nothing to score before that.
    """
    lead_rows = query(
        """SELECT o.proveedor,
                  COUNT(*)::int                      AS n_recepciones,
                  MIN(o.lead_time_days)              AS lead_time_real_min,
                  MAX(o.lead_time_days)              AS lead_time_real_max,
                  AVG(o.lead_time_days)              AS lead_time_real_avg,
                  MAX(o.observed_at)                 AS ultima_recepcion,
                  s.lead_time_dias                   AS lead_time_declarado,
                  AVG(CASE WHEN o.lead_time_days <= s.lead_time_dias THEN 1.0 ELSE 0.0 END)
                      FILTER (WHERE s.lead_time_dias IS NOT NULL) AS on_time_rate
           FROM supplier_lead_time_obs o
           LEFT JOIN suppliers s
             ON s.tenant_id = o.tenant_id AND LOWER(s.name) = LOWER(o.proveedor)
           WHERE o.tenant_id = %s
           GROUP BY o.proveedor, s.lead_time_dias
           ORDER BY n_recepciones DESC, o.proveedor""",
        (tenant_id,),
    )

    fill_rows = query(
        """SELECT poi.proveedor,
                  COALESCE(SUM(poi.cantidad_recibida), 0) AS total_recibido,
                  COALESCE(SUM(poi.cantidad_final), 0)    AS total_pedido,
                  COALESCE(SUM(poi.cantidad_final * poi.costo_unitario), 0) AS valor_comprado
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
           WHERE poi.tenant_id = %s
             AND poi.status IN ('approved', 'modified')
             AND poi.proveedor IS NOT NULL AND poi.proveedor <> ''
             AND pol.reception_status <> 'pending'
           GROUP BY poi.proveedor""",
        (tenant_id,),
    )
    fill_by_proveedor = {r["proveedor"]: r for r in fill_rows}

    out = []
    for r in lead_rows:
        d = dict(r)
        if isinstance(d.get("ultima_recepcion"), datetime):
            d["ultima_recepcion"] = d["ultima_recepcion"].isoformat()
        for k in ("lead_time_real_avg", "lead_time_real_min", "lead_time_real_max"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 1)
        if d.get("on_time_rate") is not None:
            d["on_time_rate"] = round(float(d["on_time_rate"]), 3)

        declared = d.get("lead_time_declarado")
        avg = d.get("lead_time_real_avg")
        d["desviacion_dias"] = round(avg - declared, 1) if (declared is not None and avg is not None) else None

        fill = fill_by_proveedor.get(d["proveedor"])
        total_pedido = float(fill["total_pedido"]) if fill else 0.0
        d["fill_rate"] = round(float(fill["total_recibido"]) / total_pedido, 3) if fill and total_pedido > 0 else None
        d["valor_comprado"] = round(float(fill["valor_comprado"]), 2) if fill else 0.0

        out.append(d)

    # Suppliers with fill/value data (a reception event happened — the PO left
    # 'pending') but zero lead-time observations (e.g. everything received was
    # 0 units, so receive_po never wrote a supplier_lead_time_obs row). They
    # still belong on the scorecard with a real fill_rate/valor_comprado;
    # lead-time fields are simply unknown. Appended after the lead-time group,
    # ordered by proveedor.
    lead_proveedores = {r["proveedor"] for r in lead_rows}
    fill_only_proveedores = sorted(p for p in fill_by_proveedor if p not in lead_proveedores)
    for prov in fill_only_proveedores:
        fill = fill_by_proveedor[prov]
        total_pedido = float(fill["total_pedido"])
        out.append({
            "proveedor": prov,
            "n_recepciones": 0,
            "lead_time_real_min": None,
            "lead_time_real_max": None,
            "lead_time_real_avg": None,
            "ultima_recepcion": None,
            "lead_time_declarado": None,
            "on_time_rate": None,
            "desviacion_dias": None,
            "fill_rate": round(float(fill["total_recibido"]) / total_pedido, 3) if total_pedido > 0 else None,
            "valor_comprado": round(float(fill["valor_comprado"]), 2),
        })
    return out
