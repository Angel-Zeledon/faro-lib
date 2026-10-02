"""The assistant screen's opening: who the user is and what to ask first.

Suggestions are built from the account's own top risks — "¿Por qué Aceite de
Oliva 1L está en rojo?" — not from a fixed list. The backend returns a CODE plus
params; the frontend renders the sentence in the UI language from
`analyst.suggest.<code>` (CLAUDE.md, Language). Clicking one sends it, so it
must read in the user's language.
"""
from __future__ import annotations

from backend.assistant.account import AccountData
from backend.auth.guards import CurrentUser

MAX_SUGGESTIONS = 4


def build_welcome(user: CurrentUser) -> dict:
    from backend.inventory.roi_service import format_po_number

    data = AccountData(user)
    has_forecast = bool(data.planning.get("active_session_id"))
    out: dict = {
        "first_name": data.first_name,
        "company": data.company_name,
        "has_forecast": has_forecast,
        "summary": None,
        "suggestions": [],
    }
    suggestions: list[dict] = []

    if not has_forecast:
        suggestions.append({"code": "how_to_start", "params": {}})
        out["suggestions"] = suggestions
        return out

    k = data.briefing.get("kpis") or {}
    risks = sorted(data.briefing.get("risks") or [], key=lambda i: i.get("coverage_days") or 0)
    warnings = data.briefing.get("warnings") or []
    overdue = data.overdue_pos
    out["summary"] = {
        "order_now": int(k.get("order_now") or 0),
        "order_soon": int(k.get("order_soon") or 0),
        "overstock": int(k.get("overstock") or 0),
        "no_stock_data": int(k.get("sin_datos") or 0),
        "overdue_orders": len({o.get("po_log_id") for o in overdue}),
    }

    if risks:
        top = risks[0]
        suggestions.append({"code": "why_red", "params": {"name": top.get("display_name") or top.get("sku")}})
        if top.get("supplier"):
            suggestions.append({"code": "how_much_order",
                                "params": {"name": top.get("display_name") or top.get("sku")}})
    elif warnings:
        top = warnings[0]
        suggestions.append({"code": "why_amber", "params": {"name": top.get("display_name") or top.get("sku")}})
    else:
        suggestions.append({"code": "what_to_buy_today", "params": {}})

    if overdue:
        by_id = {p.get("id"): p for p in data.po_history}
        o = overdue[0]
        po = by_id.get(o.get("po_log_id")) or {}
        suggestions.append({"code": "overdue_order", "params": {
            "reference": format_po_number(po.get("po_number"), o.get("po_log_id")),
            "supplier": o.get("supplier") or ""}})

    supplier = next((i.get("supplier") for i in risks + warnings if i.get("supplier")), None)
    if supplier:
        suggestions.append({"code": "supplier_reliability", "params": {"supplier": supplier}})

    if k.get("overstock"):
        suggestions.append({"code": "overstock_money", "params": {}})
    if k.get("sin_datos"):
        suggestions.append({"code": "no_stock_data", "params": {"n": int(k["sin_datos"])}})
    if risks and len(suggestions) < MAX_SUGGESTIONS:
        suggestions.append({"code": "what_to_buy_today", "params": {}})

    # One per code, in priority order.
    seen, unique = set(), []
    for s in suggestions:
        if s["code"] not in seen:
            seen.add(s["code"])
            unique.append(s)
    out["suggestions"] = unique[:MAX_SUGGESTIONS]
    return out
