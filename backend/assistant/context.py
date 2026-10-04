"""The account context: what the assistant knows about this person's business
before it reads a single tool result.

It is prompt material, so it is English (the model answers in the user's
language). It is compact on purpose — a token budget, not a dump — and ranked:
every section has a base priority, the question raises the ones it is about,
and sections are packed in order until `MAX_CONTEXT_CHARS` is spent. A section
that does not fit is cut by whole lines and says how many it left out, so the
model never reads a truncated list as the whole list.

Missing data is written down as missing ("No completed forecast yet"), never
omitted: an empty risks section reads to a model as "nothing is at risk".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from backend.assistant.account import AccountData

# ~4 characters per token: 7,000 characters is about 1,800 tokens, which leaves
# the model room for history, tool results and the answer inside one window.
MAX_CONTEXT_CHARS = 7000

# How many rows a list section carries before it says "and N more".
_RISK_ROWS = 8
_OVERSTOCK_ROWS = 5
_PO_ROWS = 6
_SUPPLIER_ROWS = 6
_ACTIVITY_ROWS = 6

# Words a user types when the question is about one area (user-input
# vocabulary, kept in its own module — see vocabulary.py).
from backend.assistant.vocabulary import TOPIC_WORDS as _TOPIC_WORDS  # noqa: E402


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _num(v, nd: int = 1) -> str:
    """A number as the context writes it: no trailing .0, thousands commas."""
    if v is None:
        return "?"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return f"{int(round(f)):,}"
    return f"{f:,.{nd}f}"


@dataclass
class Section:
    key: str
    title: str
    lines: list[str]
    priority: float
    # Rows left out by the section's own row cap (not by the char budget).
    hidden: int = 0

    def render(self, max_chars: int | None = None) -> str:
        head = f"## {self.title}"
        body: list[str] = []
        used = len(head) + 1
        cut = 0
        for i, line in enumerate(self.lines):
            if max_chars is not None and used + len(line) + 1 > max_chars:
                cut = len(self.lines) - i
                break
            body.append(line)
            used += len(line) + 1
        more = self.hidden + cut
        if more:
            body.append(f"(+{more} more not shown — use a tool to see them)")
        return "\n".join([head, *body])


@dataclass
class AccountContext:
    text: str
    sections: list[str]
    first_name: str
    company: str
    language: str
    # SKUs the question named, resolved against the catalogue.
    focus_skus: list[str] = field(default_factory=list)


# ── Product mention matching ─────────────────────────────────────────────────

def find_mentioned_skus(data: AccountData, question: str, limit: int = 3) -> list[str]:
    """SKUs whose code or name the question contains.

    Cheap on purpose: it scans the stored catalogue (no forecast), so naming a
    product costs a SELECT, and only a hit pays for the full semáforo.
    """
    q = _norm(question)
    if not q:
        return []
    hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    for row in data.stock_rows:
        sku = str(row.get("sku") or "")
        if not sku or sku in seen:
            continue
        name = _norm(row.get("display_name") or "")
        score = 0
        if len(sku) >= 3 and _norm(sku) in q:
            score = 100 + len(sku)
        elif name and len(name) >= 4 and name in q:
            score = 50 + len(name)
        else:
            # A product named by its distinctive words ("el aceite de oliva").
            words = [w for w in re.findall(r"[a-z0-9]+", name) if len(w) >= 4]
            if words and all(w in q for w in words[:2]):
                score = 10 + sum(len(w) for w in words[:2])
        if score:
            hits.append((score, sku))
            seen.add(sku)
    hits.sort(reverse=True)
    return [sku for _, sku in hits[:limit]]


# ── Section builders ─────────────────────────────────────────────────────────

def _risk_line(data: AccountData, i: dict, unit: str) -> str:
    name = i.get("display_name") or i.get("sku")
    label = name if name == i.get("sku") else f"{name} [{i.get('sku')}]"
    parts = [f"- {label}: stock {_num(i.get('current_stock'))}",
             f"cover {_num(i.get('coverage_days'))} {unit}(s)"]
    if i.get("recommended_qty"):
        parts.append(f"order {_num(i.get('recommended_qty'), 0)} units")
    if i.get("incoming_qty"):
        parts.append(f"{_num(i.get('incoming_qty'))} already incoming")
    if i.get("supplier"):
        lt = i.get("lead_time_days")
        src = i.get("lead_time_source")
        parts.append(f"supplier {i['supplier']}"
                     + (f" (lead time {_num(lt)} days, source={src})" if lt is not None else ""))
    if i.get("unit_cost") and i.get("recommended_qty"):
        parts.append(f"order value ~{data.money(float(i['unit_cost']) * float(i['recommended_qty']))}")
    if i.get("demand_trend_pct") is not None:
        parts.append(f"demand {i['demand_trend_pct']:+.0f}% vs forecast")
    return ", ".join(parts)


def _identity(data: AccountData, language: str, channel: str) -> Section:
    lines = [
        f"- User: {data.me.get('full_name') or data.first_name or 'unknown'}"
        f" (first name: {data.first_name or 'unknown'}), role {data.role}",
        f"- Company: {data.company_name or 'unknown'}",
        f"- Today: {date.today().isoformat()}; channel: {channel}; answer language: {language}",
    ]
    if data.currency:
        lines.append(f"- Currency: {data.currency.get('code')} ({data.currency.get('symbol')})")
    return Section("identity", "Who you are talking to", lines, 1000)


def _freshness(data: AccountData) -> Section:
    lines: list[str] = []
    plan = data.planning
    if not plan.get("active_session_id"):
        lines.append("- No completed forecast yet: the stock signals, risks and order "
                     "quantities do not exist until sales are uploaded (/ventas) "
                     "and a forecast is trained.")
        return Section("freshness", "Forecast and data freshness", lines, 950)
    lines.append(f"- Planning period: {plan.get('period')} (coverage is measured in "
                 f"{data.coverage_unit}s), horizon {plan.get('horizon')} periods")
    fr = data.freshness
    sales, stock = fr.get("sales") or {}, fr.get("stock") or {}
    if sales:
        lines.append(f"- Forecast '{sales.get('session_name')}' trained "
                     f"{str(sales.get('trained_at') or '')[:10]}; sales data age "
                     f"{_num(sales.get('age_days'))} days (state {sales.get('state')})")
    if stock:
        lines.append(f"- Stock levels last updated {str(stock.get('updated_at') or '')[:10]}, "
                     f"{_num(stock.get('age_days'))} days ago (state {stock.get('state')}) "
                     f"for {_num(stock.get('tracked_skus'))} SKUs")
    if fr.get("semaphore") == "degraded":
        lines.append("- WARNING: data is stale (" + ", ".join(fr.get("degraded_by") or [])
                     + "); colours may be out of date — say so when it matters.")
    acc = (data.briefing.get("kpis") or {}).get("avg_accuracy")
    if acc is not None:
        lines.append(f"- Forecast accuracy: {acc * 100:.0f}%")
    return Section("freshness", "Forecast and data freshness", lines, 90)


def _kpis(data: AccountData) -> Section | None:
    k = data.briefing.get("kpis") or {}
    if not k:
        return None
    lines = [
        f"- SKUs tracked {_num(k.get('total_skus'))}: order now (PEDIR_YA) {_num(k.get('order_now'))}, "
        f"order soon (PEDIR_PRONTO) {_num(k.get('order_soon'))}, OK {_num(k.get('ok'))}, "
        f"overstock (SOBRESTOCK) {_num(k.get('overstock'))}, no stock data (SIN_DATOS) {_num(k.get('sin_datos'))}",
        f"- Inventory value {data.money(k.get('total_inventory_value') or 0)} "
        f"(from {_num(k.get('valued_skus'))} SKUs with a unit cost); "
        f"capital in overstock {data.money(k.get('capital_in_overstock') or 0)}",
    ]
    if k.get("sin_datos"):
        lines.append("- SIN_DATOS means no stock on record: UNKNOWN, not safe. "
                     "Stock is loaded at /inventario.")
    return Section("kpis", "Stock signal totals today", lines, 85)


def _risks(data: AccountData) -> list[Section]:
    b = data.briefing
    unit = data.coverage_unit
    out = []
    for key, title, rows, prio in (
        ("risks", "Order now (PEDIR_YA, red) — least cover first", b.get("risks") or [], 80),
        ("warnings", "Order soon (PEDIR_PRONTO, amber)", b.get("warnings") or [], 70),
    ):
        k = b.get("kpis") or {}
        total = k.get("order_now" if key == "risks" else "order_soon") or len(rows)
        if not rows:
            continue
        rows = sorted(rows, key=lambda i: (i.get("coverage_days") is None, i.get("coverage_days") or 0))
        lines = [_risk_line(data, i, unit) for i in rows[:_RISK_ROWS]]
        out.append(Section(key, title, lines, prio, hidden=max(0, int(total) - len(lines))))
    return out


def _overstock(data: AccountData) -> Section | None:
    rows = data.briefing.get("overstocked") or []
    if not rows:
        return None
    k = data.briefing.get("kpis") or {}
    lines = [
        f"- {i.get('display_name') or i.get('sku')} [{i.get('sku')}]: stock {_num(i.get('current_stock'))}, "
        f"cover {_num(i.get('coverage_days'))} {data.coverage_unit}(s), value {data.money(i.get('inventory_value') or 0)}"
        + (f", supplier {i['supplier']}" if i.get("supplier") else "")
        for i in rows[:_OVERSTOCK_ROWS]
    ]
    total = int(k.get("overstock") or len(rows))
    return Section("overstock", "Overstock (SOBRESTOCK) — most capital first", lines, 50,
                   hidden=max(0, total - len(lines)))


def _orders(data: AccountData) -> Section | None:
    from backend.inventory.roi_service import format_po_number
    open_pos = [p for p in data.po_history if p.get("reception_status") in ("pending", "partial")]
    overdue = data.overdue_pos
    if not open_pos and not overdue:
        if data.po_history:
            return Section("orders", "Purchase orders", ["- No open purchase orders."], 40)
        return Section("orders", "Purchase orders", ["- No purchase orders recorded yet (they are created at /compras)."], 30)
    late_by_po: dict[str, list[dict]] = {}
    for o in overdue:
        late_by_po.setdefault(o.get("po_log_id"), []).append(o)
    lines = []
    for p in open_pos[:_PO_ROWS]:
        ref = format_po_number(p.get("po_number"), p.get("id"))
        sent = str(p.get("sent_at") or "")[:10]
        line = (f"- {ref}: {p.get('reception_status')}, {_num(p.get('sku_count'))} SKUs, "
                f"{_num(p.get('total_units'))} units, {data.money(p.get('total_value') or 0)}, "
                f"created {str(p.get('generated_at') or '')[:10]}"
                + (f", sent {sent}" if sent else ", not sent yet"))
        for o in late_by_po.get(p.get("id"), []):
            line += (f"; OVERDUE {_num(o.get('days_overdue'))} days from {o.get('supplier')} "
                     f"(expected {o.get('expected_arrival')}, lead time {_num(o.get('lead_time_used'))} "
                     f"days, source={o.get('lead_time_source')})")
        lines.append(line)
    prio = 75 if overdue else 60
    return Section("orders", f"Open purchase orders ({len(open_pos)} open, {len(overdue)} overdue lines)",
                   lines, prio, hidden=max(0, len(open_pos) - len(lines)))


def _suppliers(data: AccountData) -> Section | None:
    score = data.scorecard
    alerts = data.lead_time_alerts if isinstance(data.lead_time_alerts, list) else []
    if not score and not alerts:
        if data.suppliers:
            return Section("suppliers", "Suppliers", [
                f"- {len(data.suppliers)} suppliers on file; no receptions recorded yet, so "
                "no real lead time or reliability is known (it is learned from receptions at /pedidos)."], 35)
        return None
    lines = []
    for s in sorted(score, key=lambda r: -(r.get("n_receptions") or 0))[:_SUPPLIER_ROWS]:
        parts = [f"- {s.get('supplier')}: {_num(s.get('n_receptions'))} receptions"]
        if s.get("lead_time_real_avg") is not None:
            parts.append(f"real lead time avg {_num(s.get('lead_time_real_avg'))} days "
                         f"(range {_num(s.get('lead_time_real_min'))}-{_num(s.get('lead_time_real_max'))})")
        if s.get("lead_time_declarado") is not None:
            parts.append(f"declared {_num(s.get('lead_time_declarado'))} days")
        else:
            parts.append("no declared lead time")
        if s.get("on_time_rate") is not None:
            parts.append(f"on time {float(s['on_time_rate']) * 100:.0f}%")
        if s.get("fill_rate") is not None:
            parts.append(f"fill rate {float(s['fill_rate']) * 100:.0f}%")
        lines.append(", ".join(parts))
    for a in alerts[:3]:
        lines.append(f"- ALERT {a.get('supplier')} is slower than usual: recent lead time "
                     f"{_num(a.get('lead_time_recent'))} days vs historical "
                     f"{_num(a.get('lead_time_historical'))}")
    return Section("suppliers", "Supplier performance (learned from receptions)", lines, 45,
                   hidden=max(0, len(score) - _SUPPLIER_ROWS))


def _demand(data: AccountData) -> Section | None:
    b = data.briefing
    lines = []
    for i in (b.get("demand_changes") or [])[:5]:
        lines.append(f"- {i.get('display_name') or i.get('sku')}: selling {i.get('demand_trend_pct'):+.0f}% "
                     f"vs forecast over the last 14 days")
    for s in (b.get("demand_spikes") or [])[:3]:
        name = s.get("display_name") or s.get("sku")
        lines.append(
            f"- Upcoming demand peak for {name}: {_num(s.get('peak_value'))} per period on "
            f"{s.get('peak_date')} (+{_num(s.get('uplift_pct'))}% over normal); order by "
            f"{s.get('order_by_date')}" + (" — ALREADY LATE" if s.get("already_late") else ""))
    if not lines:
        return None
    return Section("demand", "Recent sales trend and upcoming peaks", lines, 40)


def _activity(data: AccountData) -> Section | None:
    items = data.activity(_ACTIVITY_ROWS)
    if not items:
        return None
    lines = [f"- {str(a.get('created_at') or '')[:10]} {a.get('action')}"
             + (f" ({a.get('status')})" if a.get("status") and a.get("status") != "success" else "")
             for a in items]
    return Section("activity", "What this user did recently (newest first)", lines, 25)


def _focus_products(data: AccountData, skus: list[str]) -> Section | None:
    if not skus:
        return None
    by_sku = {str(i.get("sku")): i for i in data.status_items}
    lines = []
    for sku in skus:
        i = by_sku.get(sku)
        if not i:
            lines.append(f"- {sku}: in the catalogue but not in the current stock signals "
                         "(no forecast for it, or it was excluded from training).")
            continue
        lines.append(_risk_line(data, i, data.coverage_unit) + f", signal {i.get('signal')}")
        if i.get("explanation"):
            lines.append(f"  why: {i['explanation']}")
        if i.get("unit_cost_source") == "default" or i.get("lead_time_source") == "default":
            lines.append("  note: some inputs are StockAI defaults, not the user's figures.")
    return Section("focus", "Products named in the question", lines, 95)


def _boost(question: str) -> dict[str, float]:
    q = _norm(question)
    return {topic: 60.0 for topic, words in _TOPIC_WORDS.items() if any(w in q for w in words)}


def build_account_context(
    data: AccountData, question: str, *, language: str, channel: str,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> AccountContext:
    focus = find_mentioned_skus(data, question)
    candidates: list[Section | None] = [
        _identity(data, language, channel),
        _freshness(data),
    ]
    if data.planning.get("active_session_id"):
        candidates += [_kpis(data), *_risks(data), _overstock(data), _demand(data),
                       _focus_products(data, focus)]
    candidates += [_orders(data), _suppliers(data), _activity(data)]

    boost = _boost(question)
    sections = [s for s in candidates if s is not None]
    for s in sections:
        topic = "risks" if s.key in ("risks", "warnings", "kpis") else s.key
        s.priority += boost.get(topic, 0.0)
    sections.sort(key=lambda s: -s.priority)

    rendered: list[str] = []
    used_keys: list[str] = []
    budget = max_chars
    for s in sections:
        if budget < 120:
            break
        block = s.render(max_chars=budget)
        rendered.append(block)
        used_keys.append(s.key)
        budget -= len(block) + 2

    return AccountContext(
        text="\n\n".join(rendered),
        sections=used_keys,
        first_name=data.first_name,
        company=data.company_name,
        language=language,
        focus_skus=focus,
    )
