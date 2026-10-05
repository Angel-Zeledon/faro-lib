"""The assistant's tools. Every one of them reads; none of them writes.

`docs/assistant-actions.md`: a model cannot hold a confirmation or an undo
token, so it is never handed a write. When the user asks for something that
changes the account, the model answers with the screen that does it (see
`DEEP_LINKS` in `core.py`), and the person does it there.

Two walls keep that true, both in `backend/tests/test_assistant.py`:

  * every handler here touches the account only through `AccountData`, and
  * every function `AccountData` calls resolves to a FastAPI route served as
    exactly `{GET}` (or to one of its two named SELECT helpers).

A descriptor is plain data (name, description, JSON Schema) so the same entry
serialises to DeepSeek's `tools` array today and to MCP's `tools/list` the day
the external MCP server wants it (`backend/mcp/catalog.py` keeps its own,
narrower catalogue for API-key callers; it is not wired to this one).

Results are JSON-able dicts, capped in size: a tool result goes back into the
prompt, and a 3,000-SKU list would blow the window and come back truncated
mid-row with nobody told.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

from backend.assistant.account import AccountData

MAX_RESULT_CHARS = 5000

_SIGNALS = ("PEDIR_YA", "PEDIR_PRONTO", "OK", "SOBRESTOCK", "SIN_DATOS")
_URGENCY = {s: i for i, s in enumerate(("PEDIR_YA", "PEDIR_PRONTO", "SIN_DATOS", "SOBRESTOCK", "OK"))}


class ToolInputError(ValueError):
    """The model asked for something malformed; the message goes back to it."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[AccountData, dict[str, Any]], dict[str, Any]]

    def openai_spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters,
        }}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()


def _clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def _product_row(i: dict) -> dict:
    """The fields of a semáforo row worth a model's tokens."""
    keep = ("sku", "display_name", "signal", "current_stock", "coverage_days",
            "daily_demand", "recommended_qty", "incoming_qty", "reorder_point",
            "supplier", "lead_time_days", "lead_time_source", "lead_time_learned",
            "unit_cost", "unit_cost_source", "moq", "inventory_value",
            "sale_price", "unit_margin", "abc", "demand_trend_pct", "category")
    return {k: i.get(k) for k in keep if i.get(k) is not None}


def _resolve_sku(data: AccountData, ref: str) -> str | None:
    """A SKU code or a product name (as a person types it) to one SKU."""
    ref_n = _norm(ref)
    if not ref_n:
        return None
    rows = data.stock_rows or data.status_items
    for r in rows:
        if _norm(r.get("sku")) == ref_n:
            return str(r["sku"])
    for r in rows:
        if _norm(r.get("display_name")) == ref_n:
            return str(r["sku"])
    hits = [r for r in rows if ref_n in _norm(r.get("display_name")) or ref_n in _norm(r.get("sku"))]
    if len({str(h["sku"]) for h in hits}) == 1:
        return str(hits[0]["sku"])
    return None


# ── Handlers ─────────────────────────────────────────────────────────────────

def find_products(data: AccountData, args: dict) -> dict:
    query = _norm(args.get("query"))
    if not query:
        raise ToolInputError("query is required")
    limit = _clamp(args.get("limit"), 8, 1, 25)
    words = [w for w in re.findall(r"[a-z0-9]+", query) if len(w) >= 2]
    seen, hits = set(), []
    for r in data.stock_rows:
        sku = str(r.get("sku") or "")
        if sku in seen:
            continue
        hay = _norm(f"{r.get('sku')} {r.get('display_name')} {r.get('category') or ''} {r.get('supplier') or ''}")
        if query in hay or (words and all(w in hay for w in words)):
            seen.add(sku)
            hits.append({"sku": sku, "display_name": r.get("display_name"),
                         "supplier": r.get("supplier"), "warehouse": r.get("warehouse")})
    return {"query": args.get("query"), "matches": hits[:limit], "total_matches": len(hits),
            "note": None if hits else "No product in this account matches. Do not guess a SKU."}


def get_product(data: AccountData, args: dict) -> dict:
    ref = args.get("sku") or args.get("product") or ""
    sku = _resolve_sku(data, ref)
    if not sku:
        return {"found": False, "query": ref,
                "note": "No single product matches; call find_products to list candidates."}
    row = next((i for i in data.status_items if str(i.get("sku")) == sku), None)
    out: dict[str, Any] = {"found": True, "sku": sku,
                           "coverage_unit": data.coverage_unit}
    if row:
        out["status"] = _product_row(row)
        if row.get("explanation"):
            out["why"] = row["explanation"]
    else:
        out["status"] = None
        out["note"] = ("Product is in the catalogue but not in the current stock signals "
                       "(no forecast for it yet, or excluded from training).")
    suppliers = data.sku_suppliers(sku)
    if suppliers:
        out["suppliers"] = [{k: s.get(k) for k in ("supplier_name", "name", "is_primary",
                                                   "unit_cost", "lead_time_days", "moq")
                             if s.get(k) is not None} for s in suppliers[:5]]
    return out


def get_forecast(data: AccountData, args: dict) -> dict:
    sku = _resolve_sku(data, args.get("sku") or args.get("product") or "")
    if not sku:
        return {"found": False, "note": "No single product matches; call find_products first."}
    fc = data.forecast(sku)
    if not fc:
        return {"found": False, "sku": sku,
                "note": "No forecast available for this product (no completed training, "
                        "or the product was not part of it)."}
    pts = [p for p in (fc.get("forecast") or []) if isinstance(p, dict) and p.get("value") is not None]
    hist = [p for p in (fc.get("historical") or []) if isinstance(p, dict) and p.get("value") is not None]
    metrics = fc.get("metrics") or []
    chosen = next((m for m in metrics if m.get("model") == fc.get("model")), None)
    return {
        "found": True, "sku": sku, "model": fc.get("model"),
        "granularity": fc.get("applied_granularity"),
        "forecast_next": [{"date": p.get("date"), "value": round(float(p["value"]), 1)} for p in pts[:14]],
        "forecast_total": round(sum(float(p["value"]) for p in pts), 1) if pts else None,
        "recent_actuals": [{"date": p.get("date"), "value": round(float(p["value"]), 1)} for p in hist[-8:]],
        "model_error_wape": chosen.get("wape") if chosen else None,
    }


def list_products(data: AccountData, args: dict) -> dict:
    signal = (args.get("signal") or "").strip().upper() or None
    if signal and signal not in _SIGNALS:
        raise ToolInputError(f"Unknown signal {signal!r}; valid: {', '.join(_SIGNALS)}")
    supplier = _norm(args.get("supplier"))
    limit = _clamp(args.get("limit"), 15, 1, 40)
    items = data.status_items
    if signal:
        items = [i for i in items if i.get("signal") == signal]
    if supplier:
        items = [i for i in items if supplier in _norm(i.get("supplier"))]
    items.sort(key=lambda i: (_URGENCY.get(i.get("signal"), 9),
                              float("inf") if i.get("coverage_days") is None else i["coverage_days"]))
    out = {"total_matching": len(items), "coverage_unit": data.coverage_unit,
           "items": [_product_row(i) for i in items[:limit]]}
    if not items and (signal or supplier):
        out["note"] = ("The filter matched nothing. That is NOT the same as 'nothing to "
                       "order' — check the supplier spelling or call without the filter.")
    if not data.status_items:
        out["note"] = "No stock signals available (no completed forecast or no stock loaded)."
    return out


def get_supplier(data: AccountData, args: dict) -> dict:
    name = _norm(args.get("name"))
    if not name:
        raise ToolInputError("name is required")
    match = lambda s: name in _norm(s)  # noqa: E731
    card = next((s for s in data.suppliers if match(s.get("name"))), None)
    score = next((s for s in data.scorecard if match(s.get("supplier"))), None)
    alert = next((a for a in (data.lead_time_alerts or []) if match(a.get("supplier"))), None)
    if not (card or score):
        return {"found": False, "name": args.get("name"),
                "known_suppliers": [s.get("name") for s in data.suppliers[:20]]}
    products = [_product_row(i) for i in data.status_items if match(i.get("supplier"))]
    overdue = [o for o in data.overdue_pos if match(o.get("supplier"))]
    out: dict[str, Any] = {"found": True}
    if card:
        out["card"] = {k: card.get(k) for k in ("name", "lead_time_days", "lead_time_set_by",
                                                "lead_time_std", "payment_terms", "active")
                       if k in card}
    if score:
        out["performance"] = score
    else:
        out["performance"] = None
        out["note"] = "No receptions recorded for this supplier yet: real lead time and reliability are unknown."
    if alert:
        out["lead_time_alert"] = alert
    out["products"] = products[:15]
    out["products_total"] = len(products)
    out["overdue_orders"] = overdue[:10]
    return out


def list_purchase_orders(data: AccountData, args: dict) -> dict:
    from backend.inventory.roi_service import format_po_number
    status = (args.get("status") or "open").lower()
    if status not in ("open", "overdue", "all"):
        raise ToolInputError("status must be open, overdue or all")
    limit = _clamp(args.get("limit"), 10, 1, 30)
    overdue_ids = {o.get("po_log_id") for o in data.overdue_pos}
    rows = data.po_history
    if status == "open":
        rows = [p for p in rows if p.get("reception_status") in ("pending", "partial")]
    elif status == "overdue":
        rows = [p for p in rows if p.get("id") in overdue_ids]
    out = []
    for p in rows[:limit]:
        out.append({
            "reference": format_po_number(p.get("po_number"), p.get("id")),
            "reception_status": p.get("reception_status"),
            "sku_count": p.get("sku_count"), "total_units": p.get("total_units"),
            "total_value": p.get("total_value"),
            "created": str(p.get("generated_at") or "")[:10],
            "sent": str(p.get("sent_at") or "")[:10] or None,
            "overdue": p.get("id") in overdue_ids,
        })
    return {"status": status, "orders": out, "total": len(rows),
            "overdue_lines": [o for o in data.overdue_pos if o.get("po_log_id") in {p.get("id") for p in rows[:limit]}]}


def get_purchase_order(data: AccountData, args: dict) -> dict:
    from backend.inventory.roi_service import format_po_number
    ref = str(args.get("reference") or "").strip()
    digits = re.sub(r"\D", "", ref)
    po = None
    for p in data.po_history:
        if ref and (ref == p.get("id") or ref.upper() == format_po_number(p.get("po_number"), p.get("id"))):
            po = p
            break
        if digits and p.get("po_number") is not None and int(digits) == int(p["po_number"]):
            po = p
            break
    if not po:
        return {"found": False, "reference": ref,
                "note": "No order with that reference among the latest 50. Call list_purchase_orders."}
    detail = data.po_items(po["id"])
    lines = detail.get("items") or detail.get("lines") or []
    return {
        "found": True,
        "reference": format_po_number(po.get("po_number"), po.get("id")),
        "reception_status": po.get("reception_status"),
        "created": str(po.get("generated_at") or "")[:10],
        "sent": str(po.get("sent_at") or "")[:10] or None,
        "total_value": po.get("total_value"),
        "lines": [{k: l.get(k) for k in ("sku", "display_name", "supplier", "final_qty",
                                          "received_qty", "unit_cost", "warehouse", "status")
                   if l.get(k) is not None} for l in lines[:25]],
        "overdue": [o for o in data.overdue_pos if o.get("po_log_id") == po["id"]],
        "link": "/pedidos",
    }


def list_committed_demand(data: AccountData, args: dict) -> dict:
    ledger = data.committed_demand  # the read happens here, so `missing` is filled below
    if "committed_demand" in data.missing:
        return {"error": "Customer commitments could not be read right now. "
                         "Tell the user they are unavailable; do not say there are none."}
    limit = _clamp(args.get("limit"), 15, 1, 40)
    customer = _norm(args.get("customer"))
    sku = _norm(args.get("sku"))
    items = list(ledger.get("items") or [])
    if customer:
        items = [i for i in items if customer in _norm(i.get("customer"))]
    if sku:
        items = [i for i in items if sku == _norm(i.get("sku"))]
    if args.get("at_risk_only"):
        items = [i for i in items if i.get("at_risk")]
    keep = ("sku", "customer", "delivery_date", "quantity", "probability", "warehouse_id",
            "overdue", "at_risk", "shortfall", "latest_safe_order_date", "order_date_passed",
            "on_top_of_base")
    out: dict[str, Any] = {
        "total_matching": len(items),
        "items": [{k: i.get(k) for k in keep if k in i} for i in items[:limit]],
        "by_customer": list(ledger.get("by_customer") or [])[:15],
        "note": ("at_risk=null means the product has no stock recorded, so nobody can say "
                 "whether it is covered. shortfall is units short after stock and incoming, "
                 "earliest delivery date first."),
    }
    if not items:
        out["note"] = ("No open commitment matches." if (customer or sku or args.get("at_risk_only"))
                       else "No open customer commitments are recorded.")
    return out


def get_recent_activity(data: AccountData, args: dict) -> dict:
    limit = _clamp(args.get("limit"), 10, 1, 30)
    return {"items": [{"when": str(a.get("created_at") or "")[:16], "action": a.get("action"),
                       "status": a.get("status"), "resource": a.get("resource")}
                      for a in data.activity(limit)]}


# ── The catalogue ────────────────────────────────────────────────────────────

_PRODUCT_ARG = {"type": "string", "description": "SKU code or product name as the user wrote it."}

TOOLS: tuple[Tool, ...] = (
    Tool("find_products",
         "Search this account's products by name, SKU, category or supplier. Use it "
         "when the user names a product loosely and you need its SKU.",
         {"type": "object", "properties": {
             "query": {"type": "string", "description": "Words to search for."},
             "limit": {"type": "integer", "minimum": 1, "maximum": 25}},
          "required": ["query"], "additionalProperties": False},
         find_products),
    Tool("get_product",
         "One product's live stock-signal row: stock, cover, signal, recommended order, "
         "supplier and lead time (with where each assumption came from), plus why.",
         {"type": "object", "properties": {"sku": _PRODUCT_ARG},
          "required": ["sku"], "additionalProperties": False},
         get_product),
    Tool("get_forecast",
         "A product's demand forecast for the coming periods and its recent actual sales.",
         {"type": "object", "properties": {"sku": _PRODUCT_ARG},
          "required": ["sku"], "additionalProperties": False},
         get_forecast),
    Tool("list_products",
         "Products filtered by stock signal and/or supplier, most urgent first. "
         "Signals: PEDIR_YA (order now), PEDIR_PRONTO (order soon), OK, SOBRESTOCK, "
         "SIN_DATOS (no stock on record: unknown, not safe).",
         {"type": "object", "properties": {
             "signal": {"type": "string", "enum": list(_SIGNALS)},
             "supplier": {"type": "string"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 40}},
          "additionalProperties": False},
         list_products),
    Tool("get_supplier",
         "A supplier's card, real performance learned from receptions (lead time, on-time "
         "rate, fill rate), lead-time alerts, the products it supplies and overdue orders.",
         {"type": "object", "properties": {"name": {"type": "string"}},
          "required": ["name"], "additionalProperties": False},
         get_supplier),
    Tool("list_purchase_orders",
         "Purchase orders: open (pending/partial), overdue, or all recent ones.",
         {"type": "object", "properties": {
             "status": {"type": "string", "enum": ["open", "overdue", "all"]},
             "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
          "additionalProperties": False},
         list_purchase_orders),
    Tool("get_purchase_order",
         "One purchase order by reference (e.g. OC-000012 or 12): lines, quantities, "
         "what was received and whether it is overdue.",
         {"type": "object", "properties": {"reference": {"type": "string"}},
          "required": ["reference"], "additionalProperties": False},
         get_purchase_order),
    Tool("list_committed_demand",
         "Open customer commitments (orders placed months or years ahead): which ones the "
         "stock plus incoming will NOT cover, how many units short, and the latest safe date "
         "to order. Optionally filter by customer, SKU or only the ones at risk.",
         {"type": "object", "properties": {
             "customer": {"type": "string"},
             "sku": {"type": "string"},
             "at_risk_only": {"type": "boolean"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 40}},
          "additionalProperties": False},
         list_committed_demand),
    Tool("get_recent_activity",
         "What this user did recently in StockAI (uploads, orders, receptions, alerts).",
         {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 30}},
          "additionalProperties": False},
         get_recent_activity),
)

BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def openai_specs() -> list[dict]:
    return [t.openai_spec() for t in TOOLS]


def run_tool(data: AccountData, name: str, raw_args: str | dict | None) -> tuple[str, bool]:
    """Run one tool call. Returns (JSON result text, ok).

    Never raises: an unknown name, unparseable arguments or a failing read all
    come back to the model as an error object it can react to, and the caller
    logs them. A name outside the catalogue — `approve_po`, say — runs nothing.
    """
    tool = BY_NAME.get(name)
    if tool is None:
        return json.dumps({"error": f"Unknown tool {name!r}. You cannot change anything in "
                                    "the account; point the user to the screen that does it."}), False
    try:
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ToolInputError("arguments must be a JSON object")
        result = tool.handler(data, args)
        ok = True
    except (ToolInputError, json.JSONDecodeError) as exc:
        result, ok = {"error": str(exc)}, False
    except Exception as exc:  # noqa: BLE001 — a read failed; the model must not invent around it
        result, ok = {"error": f"The data could not be read ({type(exc).__name__}). "
                               "Tell the user it is unavailable right now."}, False
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + ' …"(truncated: narrow the request)"'
    return text, ok
