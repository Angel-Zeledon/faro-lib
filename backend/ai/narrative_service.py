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

from backend.formatting import money, format_days

log = logging.getLogger(__name__)

# ── Profile-specific focus areas ──────────────────────────────────────────────

_PROFILE_CONTEXT = {
    'retail': (
        "La empresa es un retailer. "
        "Enfócate en: quiebres de inventario en tienda, rotación de categorías, "
        "impacto directo en ventas al consumidor, y oportunidades de liquidación de sobrestock."
    ),
    'distributor': (
        "La empresa es un distribuidor o mayorista. "
        "Enfócate en: fill rate hacia clientes, capital de trabajo inmovilizado, "
        "eficiencia de reposición por SKU, y confiabilidad de proveedores."
    ),
    'manufacturer': (
        "La empresa es un fabricante o productor. "
        "Enfócate en: escasez de materias primas o componentes, viabilidad del plan de producción, "
        "riesgo de parada de línea, y urgencia de compras de insumos."
    ),
}

_SYSTEM_PROMPT = """\
Eres el analista senior de negocio integrado en Faro, una plataforma de planificación
de inventario, demanda, compras y producción.

Recibes datos estructurados del negocio y generas análisis ejecutivos concisos en español
para gerentes de operaciones, compras y supply chain.

PRINCIPIOS ABSOLUTOS:
- Cita SIEMPRE números específicos del contexto (cantidades, porcentajes, valores monetarios).
- Usa lenguaje de negocio, NUNCA técnico (no menciones: algoritmos, ML, modelos, WAPE, MAE).
- Sé directo y conciso. Los gerentes no quieren parrafadas.
- Estructura: situación → riesgos → oportunidades → acciones concretas.
- El tono es el de un analista senior dando un briefing rápido a un director.
- NUNCA inventes datos que no estén en el contexto.
- Si la información es limitada, dilo explícitamente y trabaja con lo que hay.
"""


def _get_client():
    """Returns the local LLM client, or None if it can't be constructed."""
    try:
        from backend.ai.local_llm import get_local_llm_client
        return get_local_llm_client(timeout=60.0)
    except Exception as e:
        log.warning("Narrative service: local LLM client unavailable: %s", e)
        return None


def _call_llm(client, user_message: str, max_tokens: int = 600) -> str:
    """Single LLM call, returns text or raises."""
    resp = client.messages.create(
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text


# ── Morning Briefing Narrative ────────────────────────────────────────────────

def generate_morning_narrative(briefing: dict, profile: str = 'distributor') -> dict:
    """
    Generates a 150-200 word executive morning briefing narrative.
    Returns: {narrative, key_points, urgency, fallback}
    """
    client = _get_client()
    profile_ctx = _PROFILE_CONTEXT.get(profile, _PROFILE_CONTEXT['distributor'])

    # Build a concise data summary
    kpis = briefing.get('kpis', {})
    risks = briefing.get('risks', [])
    warnings = briefing.get('warnings', [])
    demand_changes = briefing.get('demand_changes', [])
    overstocked = briefing.get('overstocked', [])

    risk_names = [
        f"{r.get('display_name') or r.get('sku')} "
        f"({format_days(r['coverage_days']) if r.get('coverage_days') is not None else '? días'})"
        for r in risks[:5]
    ] if risks else []
    demand_up   = [f"{i.get('display_name') or i.get('sku')} (+{i.get('demand_trend_pct', 0):.0f}%)"
                   for i in demand_changes if (i.get('demand_trend_pct') or 0) > 0][:3]
    demand_down = [f"{i.get('display_name') or i.get('sku')} ({i.get('demand_trend_pct', 0):.0f}%)"
                   for i in demand_changes if (i.get('demand_trend_pct') or 0) < 0][:3]

    data_summary = {
        "date": briefing.get('date'),
        "sesion": briefing.get('session_name'),
        "total_skus_monitored": kpis.get('total_skus', 0),
        "products_at_immediate_risk": kpis.get('order_now', 0),
        "products_to_order_this_week": kpis.get('order_soon', 0),
        "products_ok": kpis.get('ok', 0),
        "products_overstock": kpis.get('overstock', 0),
        "precision_promedio_del_forecast": f"{(kpis.get('avg_accuracy') or 0) * 100:.1f}%" if kpis.get('avg_accuracy') else "no disponible",
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
        narrative = _build_fallback_narrative(data_summary, profile)
        return {"narrative": narrative, "key_points": _extract_key_points(data_summary), "urgency": urgency, "fallback": True}

    prompt = f"""Perfil de empresa: {profile_ctx}

Datos del briefing matutino:
```json
{json.dumps(data_summary, ensure_ascii=False, indent=2)}
```

Genera un resumen ejecutivo de máximo 220 palabras con esta estructura exacta:

**Situación actual**
[2-3 oraciones describiendo el estado del inventario hoy]

**Riesgos prioritarios**
[2-4 bullets con los riesgos más urgentes, citando nombres de SKUs y números]

**Oportunidades**
[1-2 bullets con oportunidades concretas detectadas]

**Acciones recomendadas para hoy**
[2-3 acciones concretas, ordenadas por urgencia]

Sé específico, usa los números del contexto, no uses jerga técnica."""

    try:
        text = _call_llm(client, prompt, max_tokens=700)
        urgency = 'critical' if kpis.get('order_now', 0) > 0 else ('warning' if kpis.get('order_soon', 0) > 0 else 'ok')
        return {"narrative": text, "key_points": _extract_key_points(data_summary), "urgency": urgency, "fallback": False}
    except Exception as e:
        log.error("Morning narrative failed: %s", e)
        urgency = 'critical' if kpis.get('order_now', 0) > 0 else 'ok'
        return {"narrative": _build_fallback_narrative(data_summary, profile), "key_points": [], "urgency": urgency, "fallback": True, "error": str(e)}


# ── Inventory Insight ─────────────────────────────────────────────────────────

def generate_inventory_insight(items: list[dict], profile: str = 'distributor') -> dict:
    """
    Generates a concise insight about the current inventory state.
    Returns: {insight, recommendations, urgency, fallback}
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
        "total_skus": len(items),
        "distribucion_señales": signals,
        "distribucion_abc": abc_dist,
        "products_a_at_critical_risk": critical_a_items,
        "overstock_value": overstock_value,
        "skus_sin_datos_de_stock": signals.get('SIN_DATOS', 0),
    }

    if not client:
        return {"insight": _build_inventory_fallback(data_summary), "recommendations": [], "urgency": "warning" if signals.get('PEDIR_YA', 0) > 0 else "ok", "fallback": True}

    prompt = f"""Perfil: {profile_ctx}

Estado del inventario:
```json
{json.dumps(data_summary, ensure_ascii=False, indent=2)}
```

En máximo 150 palabras, explica:
1. Qué está ocurriendo con el inventario
2. Qué segmentos o productos merecen atención inmediata
3. Qué oportunidad de optimización existe

Usa números concretos. Sin jerga técnica."""

    try:
        text = _call_llm(client, prompt, max_tokens=400)
        urgency = 'critical' if signals.get('PEDIR_YA', 0) > 2 else ('warning' if signals.get('PEDIR_YA', 0) > 0 else 'ok')
        return {"insight": text, "urgency": urgency, "fallback": False}
    except Exception as e:
        log.error("Inventory insight failed: %s", e)
        return {"insight": _build_inventory_fallback(data_summary), "urgency": "warning", "fallback": True}


# ── Forecast Explanation ──────────────────────────────────────────────────────

def generate_forecast_explanation(sku: str, sku_data: dict, profile: str = 'distributor') -> dict:
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
        "señal_inventario": sku_data.get('signal'),
        "clasificacion_abc": sku_data.get('abc'),
        "demand_variability_xyz": sku_data.get('xyz'),
        "current_stock": sku_data.get('current_stock'),
        "recommended_order_qty": sku_data.get('recommended_qty'),
        "calculo_detalle": calc,
        "recent_demand_change": f"{sku_data.get('demand_trend_pct', 0):.0f}%" if sku_data.get('demand_trend_pct') else "sin datos suficientes",
    }

    if not client:
        return {"explanation": f"El sistema recomienda pedir {calc.get('final_qty', '?')} unidades de {name} basándose en una demanda diaria de {sku_data.get('daily_demand', '?')} unidades y un lead time de {calc.get('lead_time_days', '?')} días.", "fallback": True}

    prompt = f"""Perfil: {profile_ctx}

Datos del SKU:
```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```

Explica en máximo 120 palabras:
1. Por qué el sistema da esta señal de inventario para este producto
2. Qué significa la recomendación de compra para el negocio
3. Si hay algo inusual que el gerente deba saber

Sin jerga técnica. Como un analista explicándole a su jefe."""

    try:
        text = _call_llm(client, prompt, max_tokens=300)
        return {"explanation": text, "fallback": False}
    except Exception as e:
        log.error("Forecast explanation failed for %s: %s", sku, e)
        return {"explanation": f"No fue posible generar la explicación automática. Los datos muestran {sku_data.get('coverage_days', '?')} días de cobertura vs {sku_data.get('lead_time_days', '?')} días de lead time.", "fallback": True}


# ── Quick Insights for Analyst ────────────────────────────────────────────────

def get_suggested_questions(profile: str, has_inventory: bool, has_production: bool) -> list[dict]:
    """Returns suggested questions for the AI Analyst, adapted by profile."""
    base = [
        {"text": "¿Qué debería revisar esta semana?", "icon": "calendar"},
        {"text": "¿Cuáles son los productos con mayor riesgo?", "icon": "alert"},
        {"text": "¿Qué tendencias de demanda están cambiando?", "icon": "trend"},
        {"text": "¿Qué oportunidades de optimización detectas?", "icon": "sparkle"},
    ]
    by_profile = {
        'retail': [
            {"text": "¿Qué categorías tienen más quiebres de inventario?", "icon": "store"},
            {"text": "¿Qué productos tienen baja rotación?", "icon": "slow"},
        ],
        'distributor': [
            {"text": "¿Cuánto capital tengo inmovilizado en sobrestock?", "icon": "money"},
            {"text": "¿Cuáles son mis productos de clase A en riesgo?", "icon": "star"},
            {"text": "¿Qué proveedor debería contactar primero?", "icon": "truck"},
        ],
        'manufacturer': [
            {"text": "¿Qué materias primas necesito comprar para el plan de producción?", "icon": "factory"},
            {"text": "¿Hay riesgo de parada de producción por escasez?", "icon": "warning"},
            {"text": "¿Qué componentes tienen mayor riesgo de escasez?", "icon": "cog"},
        ],
    }
    questions = base + by_profile.get(profile, by_profile['distributor'])
    if not has_inventory:
        # The suggested questions are Spanish end-user copy, so the filter has
        # to match Spanish words — these are data, not identifiers.
        questions = [q for q in questions if 'inventario' not in q['text'].lower() and 'sobrestock' not in q['text'].lower()]
    return questions[:8]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_key_points(data: dict) -> list[str]:
    points = []
    if data.get('products_at_immediate_risk', 0) > 0:
        points.append(f"{data['products_at_immediate_risk']} producto(s) en riesgo inmediato de quiebre")
    if data.get('capital_trapped_in_overstock', 0) > 0:
        val = data['capital_trapped_in_overstock']
        points.append(f"{money(val)} inmovilizado en sobrestock")
    if data.get('demand_change_alerts', 0) > 0:
        points.append(f"{data['demand_change_alerts']} SKU(s) con cambio significativo en demanda")
    return points


def _build_fallback_narrative(data: dict, profile: str) -> str:
    ya = data.get('products_at_immediate_risk', 0)
    pronto = data.get('products_to_order_this_week', 0)
    total = data.get('total_skus_monitored', 0)
    capital = data.get('capital_trapped_in_overstock', 0)

    parts = [f"De {total} productos monitoreados, "]
    if ya > 0:
        names = ', '.join(data.get('product_names_at_immediate_risk', [])[:3])
        parts.append(f"{ya} tienen riesgo inmediato de quiebre ({names}). ")
    if pronto > 0:
        parts.append(f"{pronto} necesitan pedido esta semana. ")
    if capital > 0:
        parts.append(f"Hay {money(capital)} inmovilizado en sobrestock que puede liberarse. ")

    demand_up = data.get('demand_rising', [])
    if demand_up:
        parts.append(f"Demanda creciente en: {', '.join(demand_up[:2])}. ")

    parts.append("Revisa el semáforo de inventario para ver las acciones recomendadas.")
    return ''.join(parts)


def _build_inventory_fallback(data: dict) -> str:
    signals = data.get('distribucion_señales', {})
    total = data.get('total_skus', 0)
    ya = signals.get('PEDIR_YA', 0)
    pronto = signals.get('PEDIR_PRONTO', 0)
    ok = signals.get('OK', 0)
    over = signals.get('SOBRESTOCK', 0)
    return (f"De {total} SKUs: {ya} en riesgo inmediato, {pronto} requieren pedido esta semana, "
            f"{ok} están bien cubiertos y {over} tienen sobrestock. "
            f"Valor en sobrestock: {money(data.get('overstock_value', 0))}.")
