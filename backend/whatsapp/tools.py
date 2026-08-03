"""
The closed set of tools the WhatsApp agent may call. Query tools are
read-only; write tools NEVER mutate — they return a pending_action that the
agent stores, and the real mutation happens later in execute_pending_action
on a confirming turn. Every tool is bound to the sender's tenant and, for
writes, re-checks analyst-or-above.

Every string a tool returns is end-user copy on a channel the frontend never
renders, so its Spanish comes from `backend/notifications/locale.py` keyed in
English — the same catalog the WhatsApp digest and the PO message use. Nothing
in this module is a Spanish literal; `TOOL_SPECS` at the bottom is prompt text
and is therefore English.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.db.connection import query
from backend.notifications.locale import render_es

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
        return render_es("wa_no_analysis_yet")
    briefing = inv_svc.get_morning_briefing(ctx.tenant_id, sess["session_id"])
    risks = briefing.get("risks", []) or []
    warnings = briefing.get("warnings", []) or []
    overstock = briefing.get("overstocked", []) or []
    lines = [render_es("wa_status_line", risks=len(risks),
                       warnings=len(warnings), overstock=len(overstock))]
    for i in risks[:5]:
        cov = i.get("coverage_days")
        cov_s = f"{cov:.0f}d" if cov is not None else "—"
        qty = i.get("recommended_qty")
        qty_s = render_es("wa_status_order_qty", qty=f"{qty:,.0f}") if qty else ""
        lines.append(render_es("wa_status_item_line",
                               name=i.get("display_name") or i.get("sku"),
                               coverage=cov_s, qty=qty_s))
    return "\n".join(lines)


def list_pending_pos(ctx: ToolContext, args: dict) -> str:
    from backend.inventory.roi_service import get_po_history, format_po_number
    history = get_po_history(ctx.tenant_id, limit=50)
    pending = [p for p in history if p.get("reception_status") in ("pending", "partial")]
    if not pending:
        return render_es("wa_no_pending_pos")
    lines = [render_es("wa_pending_pos_header")]
    currency = _tenant_currency(ctx.tenant_id)
    for p in pending[:10]:
        ref = format_po_number(p.get("po_number"), p["id"])
        total = p.get("total_value")
        total_s = f" · {_money(total, currency)}" if total else ""
        lines.append(render_es("wa_pending_po_line", reference=ref,
                               skus=p.get("sku_count", 0), total=total_s,
                               status=p.get("reception_status")))
    return "\n".join(lines)


def forecast_summary(ctx: ToolContext, args: dict) -> str:
    from backend.inventory import service as inv_svc
    from backend.db import session_store
    sku = (args or {}).get("sku")
    if not sku:
        return render_es("wa_ask_sku_for_forecast")
    sess = inv_svc.get_latest_completed_session(ctx.tenant_id)
    if not sess:
        return render_es("wa_no_forecasts_yet")
    forecasts = session_store.get_forecasts(ctx.tenant_id, sess["session_id"]) or {}
    models = forecasts.get(str(sku))
    if not isinstance(models, dict) or not models:
        return render_es("wa_forecast_not_found", sku=sku)
    # Take the first model's near-term curve.
    series = next(iter(models.values()))
    pts = (series.get("forecast") or []) if isinstance(series, dict) else []
    values = [p["value"] for p in pts if isinstance(p, dict) and p.get("value") is not None]
    if len(values) < 2:
        return render_es("wa_forecast_too_short", sku=sku)
    trend_key = ("wa_trend_up" if values[-1] > values[0] * 1.05
                 else "wa_trend_down" if values[-1] < values[0] * 0.95
                 else "wa_trend_flat")
    avg = sum(values) / len(values)
    return render_es("wa_forecast_summary", sku=sku, periods=len(values),
                     avg=f"{avg:.1f}", trend=render_es(trend_key),
                     first=f"{values[0]:.1f}", last=f"{values[-1]:.1f}")


# ── Write proposals (NO mutation) ────────────────────────────────────────────

def propose_approve_po(ctx: ToolContext, args: dict) -> dict:
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number
    po_log_id = (args or {}).get("po_log_id")
    if not po_log_id:
        raise ToolError(render_es("wa_ask_po_to_approve"))
    po = rec_svc.get_po(ctx.tenant_id, po_log_id)
    if not po:
        raise ToolError(render_es("wa_po_not_found"))
    if po.get("sent_at") is not None:
        raise ToolError(render_es("wa_po_already_sent"))
    items = rec_svc.get_po_items(ctx.tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in ("approved", "modified")]
    suppliers = sorted({(i.get("supplier") or "").strip() for i in ordered if (i.get("supplier") or "").strip()})
    ref = format_po_number(po.get("po_number"), po_log_id)
    summary = render_es("wa_confirm_send_po", reference=ref, suppliers=len(suppliers),
                        amount=_money(po.get("total_value"),
                                      _tenant_currency(ctx.tenant_id))) \
        + render_es("wa_confirm_suffix")
    return {"type": "approve_po", "po_log_id": po_log_id, "summary": summary}


def propose_reception(ctx: ToolContext, args: dict) -> dict:
    from backend.inventory.roi_service import format_po_number
    args = args or {}
    sku = str(args.get("sku") or "").strip()
    warehouse = (args.get("warehouse") or "").strip() or None
    try:
        quantity = float(args.get("quantity"))
    except (TypeError, ValueError):
        raise ToolError(render_es("wa_ask_quantity"))
    if not sku:
        raise ToolError(render_es("wa_ask_reception_sku"))
    if quantity <= 0:
        raise ToolError(render_es("wa_quantity_positive"))

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
        raise ToolError(render_es("wa_no_pending_po_sku_wh", sku=sku, warehouse=warehouse)
                        if warehouse else
                        render_es("wa_no_pending_po_sku", sku=sku))
    chosen = rows[0]
    wh = warehouse or (chosen.get("warehouse") or "principal")
    ref = format_po_number(chosen.get("po_number"), chosen["po_log_id"])
    summary = render_es("wa_confirm_reception", qty=f"{quantity:g}", sku=sku,
                        warehouse=wh, reference=ref) + render_es("wa_confirm_suffix")
    return {"type": "register_reception", "po_log_id": chosen["po_log_id"],
            "sku": sku, "warehouse": wh, "quantity": quantity, "summary": summary}


# ── Confirmed execution (mutates) ────────────────────────────────────────────

def execute_pending_action(ctx: ToolContext, action: dict) -> str:
    if not ctx.is_analyst_or_above:
        raise ToolError(render_es("wa_read_only_tool"))
    from backend.inventory import reception_service as rec_svc
    from backend.inventory.roi_service import format_po_number

    atype = (action or {}).get("type")
    if atype == "approve_po":
        po_log_id = action["po_log_id"]
        po = rec_svc.get_po(ctx.tenant_id, po_log_id)
        if not po:
            raise ToolError(render_es("wa_po_not_found"))
        rec_svc.mark_po_sent(ctx.tenant_id, po_log_id)
        ref = format_po_number(po.get("po_number"), po_log_id)
        return render_es("wa_po_sent_ok", reference=ref)

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
        return render_es("wa_reception_ok", qty=f"{qty:g}", sku=sku,
                         warehouse=action.get("warehouse"))

    raise ToolError(render_es("wa_unknown_action"))


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

# These descriptions are prompt text — the agent pastes them into the routing
# prompt for the model to choose from — so they are English like every other
# prompt in the codebase. Nothing here is ever shown to the user; what the bot
# SAYS comes from the copy catalog above.
TOOL_SPECS = [
    {"name": "semaphore_status", "kind": "query",
     "description": "Inventory status: what to order now, what to restock, what is overstocked.",
     "args": {}},
    {"name": "list_pending_pos", "kind": "query",
     "description": "List the purchase orders still waiting to be received.",
     "args": {}},
    {"name": "forecast_summary", "kind": "query",
     "description": "Summary of the demand forecast for one SKU.",
     "args": {"sku": "the SKU code"}},
    {"name": "approve_po", "kind": "write",
     "description": "Approve and send an existing purchase order.",
     "args": {"po_log_id": "id or number of the order"}},
    {"name": "register_reception", "kind": "write",
     "description": "Register the reception of goods for an order.",
     "args": {"sku": "the SKU code", "warehouse": "warehouse (optional)", "quantity": "units received"}},
]
