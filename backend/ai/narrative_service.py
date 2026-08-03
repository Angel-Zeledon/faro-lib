"""
Narrative Intelligence Service.

Converts structured business data into executive-level narratives using Claude.
Adapts language, focus, and recommendations by business profile
(retail / distributor / manufacturer).

Philosophy: AI explains, recommends and prioritizes. The user validates and decides.
All narratives are grounded in the data provided — never invented.
"""

import json
import logging

from backend.formatting import money, format_coverage_en

log = logging.getLogger(__name__)

# ── Profile-specific focus areas ──────────────────────────────────────────────

_PROFILE_CONTEXT = {
    'retail': (
        "The company is a retailer. "
        "Focus on: in-store stockouts, category turnover, direct impact on consumer "
        "sales, and opportunities to clear overstock."
    ),
    'distributor': (
        "The company is a distributor or wholesaler. "
        "Focus on: fill rate to customers, working capital tied up in stock, "
        "replenishment efficiency per SKU, and supplier reliability."
    ),
    'manufacturer': (
        "The company is a manufacturer. "
        "Focus on: raw-material or component shortages, feasibility of the production "
        "plan, line-stoppage risk, and urgency of input purchases."
    ),
}

# The prompt is English (CLAUDE.md: "LLM prompts are written in English — they may
# instruct the model to *answer* in Spanish"). Which language the ANSWER comes
# back in is a runtime decision, not a property of the prompt: the reader picks a
# language in the UI, and an English-mode user reading a Spanish briefing is the
# same defect as a Spanish string hardcoded in a handler. `{answer_language}` is
# filled from the request.
_SYSTEM_PROMPT = """\
You are the senior business analyst built into Faro, a platform for inventory,
demand, purchasing and production planning.

You receive structured business data and produce concise executive analysis for
operations, purchasing and supply-chain managers.

Write your answer in {answer_language}. This applies to every word of the
response, including the section headings you are asked to produce.

ABSOLUTE PRINCIPLES:
- ALWAYS quote specific numbers from the context (quantities, percentages, money).
- Use business language, NEVER technical (do not mention: algorithms, ML, models, WAPE, MAE).
- Be direct and concise. Managers do not want long paragraphs.
- Structure: situation -> risks -> opportunities -> concrete actions.
- The tone is a senior analyst giving a quick briefing to a director.
- NEVER invent data that is not in the context.
- If the information is limited, say so explicitly and work with what there is.
"""

# The languages the UI can be set to, mapped to how the model should be told to
# answer. An unknown value falls back to Spanish, the anchor market's language.
_ANSWER_LANGUAGE = {"es": "Spanish", "en": "English"}


def _answer_language(language: str | None) -> str:
    return _ANSWER_LANGUAGE.get((language or "es").lower(), _ANSWER_LANGUAGE["es"])


# The purchasing panel calls this on every load and blocks on it. 60s was long
# enough that nothing downstream survived the wait: measured end to end, the
# backend answered its honest fallback after 63.7s, by which point the Next
# proxy had already given up and handed the browser a bare 500 — so the
# graceful "we could not reach the AI, here is the rules-based summary" never
# arrived, and a red "Algo falló de nuestro lado" appeared instead, sometimes on
# a page the user had already navigated to.
#
# 12s is chosen against the client, not the model: the frontend stops waiting at
# 8s and renders its own fallback, so anything slower than that is already too
# late to be shown. A real Anthropic call returns well inside it; the local
# Ollama fallback either does too or was never going to.
_NARRATIVE_TIMEOUT_SECONDS = 12.0


def _get_client():
    """Returns the local LLM client, or None if it can't be constructed."""
    try:
        from backend.ai.local_llm import get_local_llm_client
        return get_local_llm_client(timeout=_NARRATIVE_TIMEOUT_SECONDS)
    except Exception as e:
        log.warning("Narrative service: local LLM client unavailable: %s", e)
        return None


def _call_llm(client, user_message: str, max_tokens: int = 600,
              language: str | None = None) -> str:
    """Single LLM call, returns text or raises."""
    resp = client.messages.create(
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT.format(answer_language=_answer_language(language)),
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text


# ── Morning Briefing Narrative ────────────────────────────────────────────────

def generate_morning_narrative(briefing: dict, profile: str = 'distributor',
                               currency: dict | None = None,
                               language: str | None = None) -> dict:
    """
    Generates a 150-200 word executive morning briefing narrative.
    Returns: {narrative, key_points, urgency, fallback}

    `currency` is the tenant's currency (`currency_of(tenant_id)`), resolved once
    by the caller: the key points and the fallback narrative quote money, and the
    reader costs a DB query. Omitting it renders the anchor market's colón.

    `language` is the reader's UI language. It reaches the model as an answer
    instruction; the rule-based fallback ignores it and returns English text plus
    `key_points` as code + params, which the frontend renders from its catalogue.
    """
    client = _get_client()
    profile_ctx = _PROFILE_CONTEXT.get(profile, _PROFILE_CONTEXT['distributor'])

    # Build a concise data summary
    kpis = briefing.get('kpis', {})
    risks = briefing.get('risks', [])
    warnings = briefing.get('warnings', [])
    demand_changes = briefing.get('demand_changes', [])

    # Coverage figures are in the briefing's active period unit (weekly session ->
    # weeks); label them with the matching noun, not a hardcoded "days". English,
    # because this is prompt CONTEXT: a Spanish noun in the data pulls the answer
    # into Spanish regardless of what the system prompt asked for.
    _period = briefing.get('period', 'daily')
    risk_names = [
        f"{r.get('display_name') or r.get('sku')} "
        f"({format_coverage_en(r['coverage_days'], _period) if r.get('coverage_days') is not None else '?'})"
        for r in risks[:5]
    ] if risks else []
    demand_up   = [f"{i.get('display_name') or i.get('sku')} (+{i.get('demand_trend_pct', 0):.0f}%)"
                   for i in demand_changes if (i.get('demand_trend_pct') or 0) > 0][:3]
    demand_down = [f"{i.get('display_name') or i.get('sku')} ({i.get('demand_trend_pct', 0):.0f}%)"
                   for i in demand_changes if (i.get('demand_trend_pct') or 0) < 0][:3]

    data_summary = {
        "date": briefing.get('date'),
        "session": briefing.get('session_name'),
        "total_skus_monitored": kpis.get('total_skus', 0),
        "products_at_immediate_risk": kpis.get('order_now', 0),
        "products_to_order_this_week": kpis.get('order_soon', 0),
        "products_ok": kpis.get('ok', 0),
        "products_overstock": kpis.get('overstock', 0),
        "average_forecast_accuracy": f"{(kpis.get('avg_accuracy') or 0) * 100:.1f}%" if kpis.get('avg_accuracy') else "not available",
        "total_inventory_value": kpis.get('total_inventory_value', 0),
        "capital_trapped_in_overstock": kpis.get('capital_in_overstock', 0),
        "product_names_at_immediate_risk": risk_names,
        "products_urgent_order": [w.get('display_name') or w.get('sku') for w in warnings[:3]],
        "demand_rising": demand_up,
        "demand_falling": demand_down,
        "demand_change_alerts": kpis.get('demand_alerts', 0),
    }

    if not client:
        # Rule-based fallback when Claude is not available
        urgency = 'critical' if kpis.get('order_now', 0) > 0 else ('warning' if kpis.get('order_soon', 0) > 0 else 'ok')
        narrative = _build_fallback_narrative(data_summary, currency)
        return {"narrative": narrative, "key_points": _extract_key_points(data_summary, currency), "urgency": urgency, "fallback": True}

    prompt = f"""Company profile: {profile_ctx}

Morning briefing data:
```json
{json.dumps(data_summary, ensure_ascii=False, indent=2)}
```

Write an executive summary of at most 220 words with exactly this structure,
with the four headings translated into the answer language:

**Current situation**
[2-3 sentences describing the state of the inventory today]

**Priority risks**
[2-4 bullets with the most urgent risks, quoting SKU names and numbers]

**Opportunities**
[1-2 bullets with concrete opportunities detected]

**Recommended actions for today**
[2-3 concrete actions, ordered by urgency]

Be specific, use the numbers from the context, no technical jargon."""

    try:
        text = _call_llm(client, prompt, max_tokens=700, language=language)
        urgency = 'critical' if kpis.get('order_now', 0) > 0 else ('warning' if kpis.get('order_soon', 0) > 0 else 'ok')
        return {"narrative": text, "key_points": _extract_key_points(data_summary, currency), "urgency": urgency, "fallback": False}
    except Exception as e:
        log.error("Morning narrative failed: %s", e)
        urgency = 'critical' if kpis.get('order_now', 0) > 0 else 'ok'
        return {"narrative": _build_fallback_narrative(data_summary, currency), "key_points": [], "urgency": urgency, "fallback": True, "error": str(e)}


# ── Inventory Insight ─────────────────────────────────────────────────────────

def generate_inventory_insight(items: list[dict], profile: str = 'distributor',
                               currency: dict | None = None,
                               language: str | None = None) -> dict:
    """
    Generates a concise insight about the current inventory state.
    Returns: {insight, recommendations, urgency, fallback}

    `currency`: as in `generate_morning_narrative` — the fallback sentence quotes
    the overstock value, so it must carry the tenant's symbol.
    """
    client = _get_client()
    profile_ctx = _PROFILE_CONTEXT.get(profile, _PROFILE_CONTEXT['distributor'])

    # Build compact summary
    signals = {}
    for item in items:
        s = item.get('signal', 'SIN_DATOS')
        signals[s] = signals.get(s, 0) + 1

    abc_dist = {}
    for item in items:
        abc = item.get('abc', '?')
        abc_dist[abc] = abc_dist.get(abc, 0) + 1

    critical_a_items = [
        f"{i.get('display_name') or i.get('sku')} ({i.get('coverage_days', 0):.0f}d coverage)"
        for i in items
        if i.get('signal') == 'PEDIR_YA' and i.get('abc') == 'A'
    ][:5]

    overstock_value = sum(i.get('inventory_value', 0) or 0 for i in items if i.get('signal') == 'SOBRESTOCK')

    data_summary = {
        # The signal values themselves (PEDIR_YA, SOBRESTOCK, …) stay as they
        # are: they are persisted enum values, not copy — see CLAUDE.md.
        "signal_distribution": signals,
        "total_skus": len(items),
        "abc_distribution": abc_dist,
        "products_a_at_critical_risk": critical_a_items,
        "overstock_value": overstock_value,
        "skus_without_stock_data": signals.get('SIN_DATOS', 0),
    }

    if not client:
        return {"insight": _build_inventory_fallback(data_summary, currency), "recommendations": [], "urgency": "warning" if signals.get('PEDIR_YA', 0) > 0 else "ok", "fallback": True}

    prompt = f"""Profile: {profile_ctx}

Inventory state:
```json
{json.dumps(data_summary, ensure_ascii=False, indent=2)}
```

In at most 150 words, explain:
1. What is happening with the inventory
2. Which segments or products deserve immediate attention
3. What optimization opportunity exists

Use concrete numbers. No technical jargon."""

    try:
        text = _call_llm(client, prompt, max_tokens=400, language=language)
        urgency = 'critical' if signals.get('PEDIR_YA', 0) > 2 else ('warning' if signals.get('PEDIR_YA', 0) > 0 else 'ok')
        return {"insight": text, "urgency": urgency, "fallback": False}
    except Exception as e:
        log.error("Inventory insight failed: %s", e)
        return {"insight": _build_inventory_fallback(data_summary, currency), "urgency": "warning", "fallback": True}


# ── Forecast Explanation ──────────────────────────────────────────────────────

def generate_forecast_explanation(sku: str, sku_data: dict, profile: str = 'distributor',
                                  language: str | None = None) -> dict:
    """
    Explains why a specific SKU has the forecast it has, in plain business language.
    sku_data: {'avg_daily', 'signal', 'coverage_days', 'abc', 'xyz', 'demand_trend_pct',
               'display_name', 'current_stock', 'recommended_qty', 'calc_explanation'}
    """
    client = _get_client()
    profile_ctx = _PROFILE_CONTEXT.get(profile, _PROFILE_CONTEXT['distributor'])

    name = sku_data.get('display_name') or sku
    calc = sku_data.get('calc_explanation') or {}

    data = {
        "sku": sku, "name": name,
        "avg_daily_demand": sku_data.get('daily_demand'),
        "current_coverage_days": sku_data.get('coverage_days'),
        "inventory_signal": sku_data.get('signal'),
        "abc_class": sku_data.get('abc'),
        "demand_variability_xyz": sku_data.get('xyz'),
        "current_stock": sku_data.get('current_stock'),
        "recommended_order_qty": sku_data.get('recommended_qty'),
        "calculation_detail": calc,
        "recent_demand_change": f"{sku_data.get('demand_trend_pct', 0):.0f}%" if sku_data.get('demand_trend_pct') else "not enough data",
    }

    # Both fallbacks below ship English text plus a code and its params: this
    # endpoint has no frontend consumer today, and the next one to arrive should
    # render the reader's language from the catalogue rather than inherit a
    # sentence whose language was decided here.
    if not client:
        return {
            "explanation": (
                f"The system recommends ordering {calc.get('final_qty', '?')} units of {name} "
                f"based on a daily demand of {sku_data.get('daily_demand', '?')} units and a "
                f"lead time of {calc.get('lead_time_days', '?')} days."
            ),
            "explanation_code": "forecast_explanation_unavailable",
            "explanation_params": {
                "name": name, "qty": calc.get('final_qty', '?'),
                "daily_demand": sku_data.get('daily_demand', '?'),
                "lead_days": calc.get('lead_time_days', '?'),
            },
            "fallback": True,
        }

    prompt = f"""Profile: {profile_ctx}

SKU data:
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```

Explain in at most 120 words:
1. Why the system gives this inventory signal for this product
2. What the purchase recommendation means for the business
3. Whether there is anything unusual the manager should know

No technical jargon. Like an analyst explaining to their boss."""

    try:
        text = _call_llm(client, prompt, max_tokens=300, language=language)
        return {"explanation": text, "fallback": False}
    except Exception as e:
        log.error("Forecast explanation failed for %s: %s", sku, e)
        return {
            "explanation": (
                f"The automatic explanation could not be generated. The data shows "
                f"{sku_data.get('coverage_days', '?')} days of coverage against a lead time of "
                f"{sku_data.get('lead_time_days', '?')} days."
            ),
            "explanation_code": "forecast_explanation_failed",
            "explanation_params": {
                "days": sku_data.get('coverage_days', '?'),
                "lead_days": sku_data.get('lead_time_days', '?'),
            },
            "fallback": True,
        }


# ── Quick Insights for Analyst ────────────────────────────────────────────────

def get_suggested_questions(profile: str, has_inventory: bool, has_production: bool) -> list[dict]:
    """Returns suggested questions for the AI Analyst, adapted by profile.

    Each entry is `code` + English `text` + `icon`. The user clicks one and it
    becomes the message they send, so it has to be in THEIR language: the
    frontend renders `analyst.q.<code>` from the catalogue and only falls back to
    `text` for a code it does not know.

    `needs_inventory` marks the questions that only make sense once there is
    inventory data. It used to be inferred by searching the sentence for the
    Spanish words "inventario"/"sobrestock" — which silently stopped filtering
    the moment any of that copy was reworded, and could not survive the wording
    moving to the frontend at all.
    """
    base = [
        {"code": "review_this_week",   "text": "What should I review this week?",              "icon": "calendar"},
        {"code": "highest_risk",       "text": "Which products are most at risk?",             "icon": "alert"},
        {"code": "demand_trends",      "text": "Which demand trends are changing?",            "icon": "trend"},
        {"code": "optimization",       "text": "What optimization opportunities do you see?",  "icon": "sparkle"},
    ]
    by_profile = {
        'retail': [
            {"code": "categories_stockouts", "text": "Which categories have the most stockouts?", "icon": "store", "needs_inventory": True},
            {"code": "slow_movers",          "text": "Which products are slow movers?",           "icon": "slow"},
        ],
        'distributor': [
            {"code": "capital_in_overstock", "text": "How much capital is tied up in overstock?", "icon": "money", "needs_inventory": True},
            {"code": "class_a_at_risk",      "text": "Which class A products are at risk?",       "icon": "star"},
            {"code": "supplier_to_contact",  "text": "Which supplier should I contact first?",    "icon": "truck"},
        ],
        'manufacturer': [
            {"code": "raw_materials_needed", "text": "Which raw materials do I need to buy for the production plan?", "icon": "factory"},
            {"code": "production_stop_risk", "text": "Is there a risk of production stopping because of a shortage?", "icon": "warning"},
            {"code": "components_at_risk",   "text": "Which components are most at risk of running short?",           "icon": "cog"},
        ],
    }
    questions = base + by_profile.get(profile, by_profile['distributor'])
    if not has_inventory:
        questions = [q for q in questions if not q.get('needs_inventory')]
    return questions[:8]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_key_points(data: dict, currency: dict | None = None) -> list[dict]:
    """The one-line summary shown collapsed under the narrative card.

    Returned as code + params, because these survive a successful AI call: the
    narrative itself comes back in the reader's language, and these three
    sentences used to arrive in Spanish underneath it whatever the UI was set to.

    `amount` is pre-formatted — only the backend knows the tenant's currency
    setting — while counts travel as raw numbers so the catalogue can order the
    sentence however the language needs.
    """
    points: list[dict] = []
    if data.get('products_at_immediate_risk', 0) > 0:
        n = data['products_at_immediate_risk']
        points.append({
            "code": "immediate_stockout_risk", "params": {"n": n},
            "text": f"{n} product(s) at immediate risk of running out",
        })
    if data.get('capital_trapped_in_overstock', 0) > 0:
        amount = money(data['capital_trapped_in_overstock'], currency=currency)
        points.append({
            "code": "capital_in_overstock", "params": {"amount": amount},
            "text": f"{amount} tied up in overstock",
        })
    if data.get('demand_change_alerts', 0) > 0:
        n = data['demand_change_alerts']
        points.append({
            "code": "demand_change_alerts", "params": {"n": n},
            "text": f"{n} SKU(s) with a significant change in demand",
        })
    return points


def _build_fallback_narrative(data: dict, currency: dict | None = None) -> str:
    """English. The frontend composes its own Spanish/English version from the
    briefing whenever `fallback` is true (`buildFallbackNarrative` in
    `/compras`), so this text is what reaches a client that has no such builder
    — the API, an integration, a future channel."""
    ya = data.get('products_at_immediate_risk', 0)
    soon = data.get('products_to_order_this_week', 0)
    total = data.get('total_skus_monitored', 0)
    capital = data.get('capital_trapped_in_overstock', 0)

    parts = [f"Of {total} products monitored, "]
    if ya > 0:
        names = ', '.join(data.get('product_names_at_immediate_risk', [])[:3])
        parts.append(f"{ya} are at immediate risk of running out ({names}). ")
    if soon > 0:
        parts.append(f"{soon} need an order this week. ")
    if capital > 0:
        parts.append(f"There is {money(capital, currency=currency)} tied up in overstock that can be freed. ")

    demand_up = data.get('demand_rising', [])
    if demand_up:
        parts.append(f"Demand is rising in: {', '.join(demand_up[:2])}. ")

    parts.append("Check the inventory dashboard for the recommended actions.")
    return ''.join(parts)


def _build_inventory_fallback(data: dict, currency: dict | None = None) -> str:
    signals = data.get('signal_distribution', {})
    total = data.get('total_skus', 0)
    ya = signals.get('PEDIR_YA', 0)
    soon = signals.get('PEDIR_PRONTO', 0)
    ok = signals.get('OK', 0)
    over = signals.get('SOBRESTOCK', 0)
    return (f"Of {total} SKUs: {ya} at immediate risk, {soon} need an order this week, "
            f"{ok} are well covered and {over} are overstocked. "
            f"Overstock value: {money(data.get('overstock_value', 0), currency=currency)}.")
