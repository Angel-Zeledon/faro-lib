"""
The closed set of tools the WhatsApp agent may call. Query tools are
read-only; write tools NEVER mutate — they return a pending_action that the
agent stores, and the real mutation happens later in execute_pending_action
on a confirming turn. Every tool is bound to the sender's tenant and, for
writes, re-checks analyst-or-above.

All returned strings are end-user copy (Spanish), like the existing
notifications/whatsapp.py message builders.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.db.connection import query

_ANALYST_ROLES = ("admin", "analyst")


class ToolError(Exception):
    """Carries an end-user (Spanish) message explaining why a tool could not run."""


@dataclass
class ToolContext:
    tenant_id: str
    user_id: str
    role: str

    @property
    def is_analyst_or_above(self) -> bool:
        return self.role in _ANALYST_ROLES


def _tenant_currency(tenant_id: str) -> dict:
    """The currency this company's money is shown in. One read per message, never
    one per line: these builders loop over orders."""
    from backend.api.v1.currency import currency_of
    return currency_of(tenant_id)


def _money(v, currency: dict | None = None) -> str:
    """An amount in the tenant's currency. Both of this module's money strings
    hardcoded a "$" — not even the anchor market's symbol — and one of them is the
    total the buyer confirms by replying SÍ."""
    from backend.formatting import money
    try:
        return money(float(v), currency=currency)
    except (TypeError, ValueError):
        return "—"


# ── Query tools (read-only) ──────────────────────────────────────────────────

def semaphore_status(ctx: ToolContext, args: dict) -> str:
    from backend.inventory import service as inv_svc
    sess = inv_svc.get_latest_completed_session(ctx.tenant_id)
    if not sess:
        return "Aún no hay un análisis de inventario listo. Sube tus ventas y entrena un modelo primero."
    briefing = inv_svc.get_morning_briefing(ctx.tenant_id, sess["session_id"])
    risks = briefing.get("risks", []) or []
    warnings = briefing.get("warnings", []) or []
    overstock = briefing.get("overstocked", []) or []
    lines = [
        f"🔴 {len(risks)} para pedir ya · 🟡 {len(warnings)} por reabastecer · 🟢 {len(overstock)} con sobrestock",
    ]
    for i in risks[:5]:
        cov = i.get("coverage_days")
        cov_s = f"{cov:.0f}d" if cov is not None else "—"
        qty = i.get("recommended_qty")
        qty_s = f" · pedir {qty:,.0f}" if qty else ""
        lines.append(f"  • {i.get('display_name') or i.get('sku')} ({cov_s}{qty_s})")
    return "\n".join(lines)


def list_pending_pos(ctx: ToolContext, args: dict) -> str:
    from backend.inventory.roi_service import get_po_history, format_po_number
    history = get_po_history(ctx.tenant_id, limit=50)
    pending = [p for p in history if p.get("reception_status") in ("pending", "partial")]
    if not pending:
        return "No tienes órdenes de compra pendientes de recibir."
    lines = ["Órdenes pendientes:"]
    currency = _tenant_currency(ctx.tenant_id)
    for p in pending[:10]:
        ref = format_po_number(p.get("po_number"), p["id"])
        total = p.get("total_value")
        total_s = f" · {_money(total, currency)}" if total else ""
        lines.append(f"  • {ref} — {p.get('sku_count', 0)} SKU{total_s} ({p.get('reception_status')})")
    return "\n".join(lines)


def forecast_summary(ctx: ToolContext, args: dict) -> str:
    from backend.inventory import service as inv_svc
    from backend.db import session_store
    sku = (args or {}).get("sku")
    if not sku:
        return "¿De qué SKU quieres el pronóstico? Indícame el código."
    sess = inv_svc.get_latest_completed_session(ctx.tenant_id)
    if not sess:
        return "Aún no hay pronósticos listos para esta cuenta."
    forecasts = session_store.get_forecasts(ctx.tenant_id, sess["session_id"]) or {}
    models = forecasts.get(str(sku))
    if not isinstance(models, dict) or not models:
        return f"No encontré pronóstico para el SKU {sku}."
    # Take the first model's near-term curve.
    series = next(iter(models.values()))
    pts = (series.get("forecast") or []) if isinstance(series, dict) else []
    values = [p["value"] for p in pts if isinstance(p, dict) and p.get("value") is not None]
    if len(values) < 2:
        return f"El pronóstico del SKU {sku} aún no tiene suficientes puntos."
    trend = "sube" if values[-1] > values[0] * 1.05 else ("baja" if values[-1] < values[0] * 0.95 else "estable")
    avg = sum(values) / len(values)
    return (f"Pronóstico SKU {sku}: {len(values)} periodos, promedio {avg:.1f} uds/periodo, "
            f"tendencia {trend} (de {values[0]:.1f} a {values[-1]:.1f}).")


# ── Write proposals (NO mutation) ────────────────────────────────────────────

def propose_approve_po(ctx: ToolContext, args: dict) -> dict:
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number
    po_log_id = (args or {}).get("po_log_id")
    if not po_log_id:
        raise ToolError("Indícame el número de la orden de compra a aprobar.")
    po = rec_svc.get_po(ctx.tenant_id, po_log_id)
    if not po:
        raise ToolError("No encontré esa orden de compra.")
    if po.get("sent_at") is not None:
        raise ToolError("Esa orden ya fue enviada.")
    items = rec_svc.get_po_items(ctx.tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in ("approved", "modified")]
    suppliers = sorted({(i.get("supplier") or "").strip() for i in ordered if (i.get("supplier") or "").strip()})
    ref = format_po_number(po.get("po_number"), po_log_id)
    summary = (f"Aprobar y enviar la orden {ref} — {len(suppliers)} proveedor(es), "
               f"total {_money(po.get('total_value'), _tenant_currency(ctx.tenant_id))}. "
               f"¿Confirmas? (responde SÍ)")
    return {"type": "approve_po", "po_log_id": po_log_id, "summary": summary}


def propose_reception(ctx: ToolContext, args: dict) -> dict:
    from backend.inventory.roi_service import format_po_number
    args = args or {}
    sku = str(args.get("sku") or "").strip()
    warehouse = (args.get("warehouse") or "").strip() or None
    try:
        quantity = float(args.get("quantity"))
    except (TypeError, ValueError):
        raise ToolError("¿Cuántas unidades llegaron? Indícame la cantidad.")
    if not sku:
        raise ToolError("¿De qué SKU es la recepción?")
    if quantity <= 0:
        raise ToolError("La cantidad recibida debe ser mayor a cero.")

    # Find the most recent pending/partial PO whose ordered line carries this
    # SKU (and warehouse, when given).
    rows = query(
        """SELECT pol.id AS po_log_id, pol.po_number, pol.generated_at,
                  poi.warehouse
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
           WHERE poi.tenant_id = %s AND poi.sku = %s
             AND poi.status IN ('approved', 'modified')
             AND pol.reception_status IN ('pending', 'partial')
           ORDER BY pol.generated_at DESC""",
        (ctx.tenant_id, sku),
    )
    if warehouse:
        rows = [r for r in rows if (r.get("warehouse") or "principal") == warehouse]
    if not rows:
        raise ToolError(f"No encontré una orden pendiente con el SKU {sku}"
                        + (f" en {warehouse}." if warehouse else "."))
    chosen = rows[0]
    wh = warehouse or (chosen.get("warehouse") or "principal")
    ref = format_po_number(chosen.get("po_number"), chosen["po_log_id"])
    summary = (f"Registrar recepción de {quantity:g} uds de {sku} en {wh} "
               f"(orden {ref}). ¿Confirmas? (responde SÍ)")
    return {"type": "register_reception", "po_log_id": chosen["po_log_id"],
            "sku": sku, "warehouse": wh, "quantity": quantity, "summary": summary}


# ── Confirmed execution (mutates) ────────────────────────────────────────────

def execute_pending_action(ctx: ToolContext, action: dict) -> str:
    if not ctx.is_analyst_or_above:
        raise ToolError("Tu perfil es de solo lectura; no puedes ejecutar esta acción.")
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number

    atype = (action or {}).get("type")
    if atype == "approve_po":
        po_log_id = action["po_log_id"]
        po = rec_svc.get_po(ctx.tenant_id, po_log_id)
        if not po:
            raise ToolError("No encontré esa orden de compra.")
        rec_svc.mark_po_sent(ctx.tenant_id, po_log_id)
        ref = format_po_number(po.get("po_number"), po_log_id)
        return f"Listo ✅ Orden {ref} aprobada y marcada como enviada."

    if atype == "register_reception":
        po_log_id = action["po_log_id"]
        sku = action["sku"]
        qty = float(action["quantity"])
        try:
            rec_svc.receive_po(
                ctx.tenant_id, po_log_id, ctx.user_id,
                lines=[{"sku": sku, "received_qty": qty}],
            )
        except ValueError as e:
            raise ToolError(str(e))
        return f"Listo ✅ Registré {qty:g} uds de {sku} en {action.get('warehouse')}."

    raise ToolError("Acción no reconocida.")


# ── Registries + specs for the agent's routing prompt ────────────────────────

QUERY_TOOLS = {
    "semaphore_status": semaphore_status,
    "list_pending_pos": list_pending_pos,
    "forecast_summary": forecast_summary,
}

WRITE_TOOLS = {
    "approve_po": propose_approve_po,
    "register_reception": propose_reception,
}

TOOL_SPECS = [
    {"name": "semaphore_status", "kind": "query",
     "description": "Estado del semáforo de inventario: qué pedir ya, qué reabastecer, sobrestock.",
     "args": {}},
    {"name": "list_pending_pos", "kind": "query",
     "description": "Lista las órdenes de compra pendientes de recibir.",
     "args": {}},
    {"name": "forecast_summary", "kind": "query",
     "description": "Resumen del pronóstico de demanda de un SKU.",
     "args": {"sku": "código del SKU"}},
    {"name": "approve_po", "kind": "write",
     "description": "Aprobar y enviar una orden de compra existente.",
     "args": {"po_log_id": "id o número de la orden"}},
    {"name": "register_reception", "kind": "write",
     "description": "Registrar la recepción de mercancía de una orden.",
     "args": {"sku": "código del SKU", "warehouse": "bodega (opcional)", "quantity": "unidades recibidas"}},
]
