"""
Inventory intelligence service.

Combines inventory_stock (current levels) with session forecast data
to produce per-SKU signals, ABC-XYZ classification, and order recommendations.
"""

import math
import logging
from datetime import datetime, timedelta
from typing import Optional

from backend.db.connection import query, query_one, execute

log = logging.getLogger(__name__)

# Z-scores for common service levels
_Z = {0.90: 1.282, 0.95: 1.645, 0.97: 1.881, 0.99: 2.326}
_SIGNAL_PRIORITY = {"PEDIR_YA": 0, "PEDIR_PRONTO": 1, "OK": 2, "SOBRESTOCK": 3, "SIN_DATOS": 4}


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_stock(tenant_id: str, sku: str, data: dict) -> dict:
    allowed = {
        "display_name", "stock_actual", "stock_minimo",
        "lead_time_dias", "costo_unitario", "moq", "proveedor", "notas",
        "service_level",
        "precio_venta", "categoria", "marca", "unidad_medida", "codigo_barras",
        "bodega",
    }

    # bodega is NOT NULL with a DB default of 'principal', but the ON CONFLICT
    # target now includes it, so the INSERT must always supply a value.
    if "bodega" not in data:
        data = {**data, "bodega": "principal"}

    safe = {k: v for k, v in data.items() if k in allowed}
    if not safe:
        raise ValueError("No valid fields to update")

    cols   = ", ".join(safe.keys())
    values = list(safe.values())
    phs    = ", ".join(["%s"] * len(safe))
    upd    = ", ".join(f"{k} = EXCLUDED.{k}" for k in safe if k != "bodega")

    execute(
        f"""INSERT INTO inventory_stock (tenant_id, sku, {cols}, updated_at)
            VALUES (%s, %s, {phs}, NOW())
            ON CONFLICT (tenant_id, sku, bodega) DO UPDATE
            SET {upd}, updated_at = NOW()""",
        (tenant_id, sku, *values),
    )
    _ensure_warehouse(tenant_id, safe["bodega"])

    # NOTE (widened per final-review finding, tracked for MW-2/MW-3): several
    # call sites are NOT yet warehouse-aware and will misbehave the moment a
    # tenant has a real second warehouse — this is safe today only because no
    # tenant does yet:
    #   - get_stock(tenant, sku) below returns whichever bodega row Postgres
    #     returns first (no ORDER BY / no bodega filter) — this upsert's own
    #     return value can therefore echo a DIFFERENT warehouse's row after a
    #     multi-bodega write.
    #   - reception_service.py's PO-reception stock UPDATE has no bodega
    #     filter and will add the received quantity to EVERY bodega row for
    #     that SKU (stock inflation across warehouses).
    #   - get_inventory_status()'s stock_map keeps one arbitrary bodega row
    #     per SKU instead of summing across bodegas (semáforo/valuation
    #     under-reports for a multi-warehouse SKU).
    # All three must be fixed before multi-warehouse reaches a real tenant.
    row = get_stock(tenant_id, sku)

    # Auto-snapshot when stock_actual is updated
    if "stock_actual" in safe and row:
        _record_snapshot(tenant_id, sku, float(safe["stock_actual"]))

    return row


def _ensure_warehouse(tenant_id: str, name: str) -> None:
    """Auto-create a `warehouses` row the first time a bodega name is seen for
    this tenant. Best-effort: a warehouse-insert hiccup must never fail the
    stock write it's attached to."""
    try:
        execute(
            "INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id, name) DO NOTHING",
            (tenant_id, name),
        )
    except Exception as e:
        log.warning("_ensure_warehouse: failed to upsert bodega=%s tenant=%s err=%s", name, tenant_id, e)


def get_stock(tenant_id: str, sku: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )


def list_stock(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s ORDER BY sku",
        (tenant_id,),
    )


def delete_stock(tenant_id: str, sku: str) -> None:
    execute(
        "DELETE FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )


# Dataset columns we recognize as inventory data when present in an uploaded file.
_DATASET_STOCK_FLOAT_COLS = {"stock_actual", "stock_minimo", "costo_unitario", "moq", "service_level", "precio_venta"}
_DATASET_STOCK_INT_COLS   = {"lead_time_dias"}
_DATASET_STOCK_STR_COLS   = {"proveedor", "notas", "display_name", "categoria", "marca", "unidad_medida", "codigo_barras"}
_DATASET_STOCK_COLS = _DATASET_STOCK_FLOAT_COLS | _DATASET_STOCK_INT_COLS | _DATASET_STOCK_STR_COLS


def sync_stock_from_dataset(tenant_id: str, df, group_col: Optional[str], date_col: str) -> int:
    """
    If the uploaded dataset contains recognized inventory columns (stock_actual,
    lead_time_dias, costo_unitario, moq, proveedor, notas, display_name,
    stock_minimo, service_level), seed/update inventory_stock with the most
    recent value per SKU. This is what lets a Quick Start upload actually
    control what /inventory shows, instead of /inventory silently falling back
    to whatever was entered manually in a previous session.
    """
    import pandas as pd

    if df is None or df.empty:
        return 0

    present = [c for c in df.columns if c in _DATASET_STOCK_COLS]
    if not present:
        return 0

    work = df.copy()
    if date_col in work.columns:
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work = work.sort_values(date_col)

    groups = work.groupby(group_col) if group_col and group_col in work.columns else [("__all__", work)]

    count = 0
    for sku, g in groups:
        last = g.iloc[-1]
        data: dict = {}
        for col in present:
            val = last[col]
            if pd.isna(val):
                continue
            if col in _DATASET_STOCK_FLOAT_COLS:
                data[col] = float(val)
            elif col in _DATASET_STOCK_INT_COLS:
                data[col] = int(val)
            else:
                data[col] = str(val)
        if not data:
            continue
        try:
            upsert_stock(tenant_id, str(sku), data)
            count += 1
        except Exception as e:
            log.warning("sync_stock_from_dataset: skipped sku=%s err=%s", sku, e)
    return count


def bulk_upsert(tenant_id: str, rows: list[dict]) -> int:
    """Upsert multiple SKUs from a CSV/bulk import. Returns count saved."""
    count = 0
    for row in rows:
        sku = row.get("sku", "").strip()
        if not sku:
            continue
        try:
            upsert_stock(tenant_id, sku, {k: v for k, v in row.items() if k != "sku"})
            count += 1
        except Exception as e:
            log.warning("bulk_upsert: skipped sku=%s err=%s", sku, e)
    return count


# ── Stock snapshots ───────────────────────────────────────────────────────────

def _record_snapshot(tenant_id: str, sku: str, stock_actual: float) -> None:
    """Record a point-in-time stock level. Called automatically on upsert."""
    try:
        execute(
            "INSERT INTO inventory_snapshots (tenant_id, sku, stock_actual) VALUES (%s, %s, %s)",
            (tenant_id, sku, stock_actual),
        )
    except Exception as e:
        log.warning("snapshot record failed sku=%s: %s", sku, e)


def get_stock_history(tenant_id: str, sku: str, days: int = 30) -> list[dict]:
    """Returns daily stock snapshots for the last N days, most recent last."""
    from datetime import timezone
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = query(
        """SELECT stock_actual, recorded_at
           FROM inventory_snapshots
           WHERE tenant_id = %s AND sku = %s AND recorded_at >= %s
           ORDER BY recorded_at ASC""",
        (tenant_id, sku, since),
    )
    return [{"stock": r["stock_actual"], "date": r["recorded_at"].isoformat()} for r in rows]


# ── ABC-XYZ classification ────────────────────────────────────────────────────

def _classify_xyz(cv: Optional[float]) -> str:
    """
    X = low variability (predictable), Y = moderate, Z = high (erratic).
    Uses coefficient of variation from the series analysis.
    """
    if cv is None:
        return "?"
    if cv < 0.5:
        return "X"
    if cv < 1.0:
        return "Y"
    return "Z"


def _classify_abc(items: list[dict]) -> dict[str, str]:
    """
    A = top 80% cumulative revenue proxy, B = next 15%, C = rest.
    Revenue proxy = demanda_diaria * costo_unitario (or just demanda_diaria if no cost).
    """
    scored = []
    for item in items:
        demand = item.get("demanda_diaria") or 0.0
        cost   = item.get("costo_unitario") or 1.0
        scored.append((item["sku"], demand * cost))

    scored.sort(key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in scored)

    if total == 0:
        return {sku: "C" for sku, _ in scored}

    result: dict[str, str] = {}
    cumulative = 0.0
    for sku, val in scored:
        # Assign tier based on cumulative BEFORE adding this item,
        # so a single dominant SKU (e.g. 99% revenue) gets classified as A not C.
        if cumulative < 0.80:
            result[sku] = "A"
        elif cumulative < 0.95:
            result[sku] = "B"
        else:
            result[sku] = "C"
        cumulative += val / total
    return result


# ── Signal calculation ────────────────────────────────────────────────────────

def _avg_daily_forecast(model_forecasts: dict, lead_time: int) -> tuple[float, float]:
    """
    Returns (avg_daily_demand, avg_daily_std) across all models,
    using the first `lead_time` forecast steps.
    """
    all_values: list[float] = []
    all_stds:   list[float] = []

    for model_data in model_forecasts.values():
        pts = model_data.get("forecast", [])[:lead_time]
        if not pts:
            continue
        vals = [p.get("value") or 0.0 for p in pts]
        stds = [
            ((p.get("upper") or p.get("value") or 0) - (p.get("value") or 0))
            for p in pts
        ]
        all_values.extend(vals)
        all_stds.extend(stds)

    if not all_values:
        return 0.0, 0.0

    avg_daily = sum(all_values) / len(all_values)
    avg_std   = sum(all_stds) / len(all_stds) if all_stds else 0.0
    return max(0.0, avg_daily), max(0.0, avg_std)


def _avg_forecast_curve(model_forecasts: dict, max_steps: int = 90) -> list[dict]:
    """
    Averages the per-step forecast value across all models, aligned by step
    index, returning a chronological [{step, date, value}] curve. Dates come
    from whichever model provides them (all models share the same horizon).
    """
    step_values: dict[int, list[float]] = {}
    step_dates:  dict[int, str] = {}
    for model_data in model_forecasts.values():
        pts = model_data.get("forecast", []) or []
        for idx, p in enumerate(pts[:max_steps]):
            v = p.get("value")
            if v is None:
                continue
            step_values.setdefault(idx, []).append(float(v))
            if idx not in step_dates and p.get("date"):
                step_dates[idx] = str(p["date"])[:10]

    curve: list[dict] = []
    for idx in sorted(step_values):
        vals = step_values[idx]
        if vals:
            curve.append({"step": idx, "date": step_dates.get(idx), "value": sum(vals) / len(vals)})
    return curve


def _calc_signal(dias_cobertura: float, lead_time: int) -> str:
    if dias_cobertura < lead_time * 0.5:
        return "PEDIR_YA"
    if dias_cobertura < lead_time * 1.2:
        return "PEDIR_PRONTO"
    if dias_cobertura < lead_time * 3:
        return "OK"
    return "SOBRESTOCK"


def _calc_recommended(
    stock_actual: float,
    avg_daily: float,
    avg_std: float,
    lead_time: int,
    moq: float,
    service_level: float = 0.95,
) -> float:
    z = _Z.get(service_level, 1.645)
    demanda_lead_time = avg_daily * lead_time
    safety_stock = z * avg_std * math.sqrt(lead_time)
    raw = max(0.0, demanda_lead_time + safety_stock - stock_actual)
    if moq and moq > 0:
        raw = math.ceil(raw / moq) * moq
    return float(round(raw, 2))


# Signals for which recommending an order is meaningful. On any other signal
# (OK / SOBRESTOCK / SIN_DATOS) the semáforo says stock is sufficient, so the
# suggested quantity MUST be 0 — otherwise a healthy SKU shows "pedir N".
_ORDERING_SIGNALS = ("PEDIR_YA", "PEDIR_PRONTO")


def _gate_recommended_by_signal(signal: str, recomendado: float) -> float:
    """Zero the recommendation unless the signal actually calls for ordering."""
    if signal in _ORDERING_SIGNALS:
        return float(recomendado)
    return 0.0


def _aggregate_stock_rows_by_sku(stock_rows: list[dict]) -> dict[str, dict]:
    """
    Collapse per-bodega inventory_stock rows into one summary row per SKU:
    stock_actual is SUMMED across bodegas (true total stock the tenant
    holds); every other field (lead_time_dias, costo_unitario, proveedor,
    etc.) is taken from a single deterministic representative row (the
    'principal' bodega if present, else the alphabetically-first bodega) —
    those are per-SKU catalog attributes, not per-warehouse quantities, so
    picking one is correct as long as it's deterministic.
    """
    by_sku: dict[str, list[dict]] = {}
    for r in stock_rows:
        by_sku.setdefault(r["sku"], []).append(r)

    result: dict[str, dict] = {}
    for sku, rows in by_sku.items():
        rows_sorted = sorted(rows, key=lambda r: (r.get("bodega") != "principal", r.get("bodega") or ""))
        representative = dict(rows_sorted[0])
        representative["stock_actual"] = sum(float(r["stock_actual"] or 0) for r in rows)
        result[sku] = representative
    return result


# ── Main status calculation ───────────────────────────────────────────────────

def get_inventory_status(tenant_id: str, session_id: str, service_level: float = 0.95) -> list[dict]:
    """
    Merges inventory_stock with session forecast.
    Includes ABC-XYZ classification, stock trend, and order recommendation.
    """
    from backend.db import session_store

    forecasts: dict = session_store.get_forecasts(tenant_id, session_id) or {}

    # Try to pull CV per SKU from the quality report stored in training_result
    cv_by_sku: dict[str, Optional[float]] = {}
    try:
        result = session_store.get_training_result(tenant_id, session_id) or {}
        quality: dict = result.get("data_quality") or {}
        for sku_key, q in quality.items():
            if isinstance(q, dict):
                cv_by_sku[str(sku_key)] = q.get("cv")
    except Exception:
        pass

    stock_rows = list_stock(tenant_id)
    stock_map  = _aggregate_stock_rows_by_sku(stock_rows)

    # Scope strictly to the SKUs forecast in THIS session. inventory_stock is a
    # tenant-wide table (no session_id column) that accumulates rows from every
    # session ever run for this tenant, so it must never be the source of which
    # SKUs to display — only of the stock fields to enrich a SKU already present
    # in the active session's forecasts. Otherwise, stale/unrelated SKUs from
    # past sessions leak into sessions that never uploaded them.
    all_skus = sorted(forecasts.keys())

    items: list[dict] = []

    for sku in all_skus:
        stock = stock_map.get(sku)
        model_forecasts = forecasts.get(sku, {})

        lead_time    = int(stock["lead_time_dias"]) if stock else 15
        stock_actual = float(stock["stock_actual"]) if stock else None
        moq          = float(stock["moq"]) if stock else 1.0

        has_forecast = bool(model_forecasts)
        has_stock    = stock is not None and stock_actual is not None

        if has_forecast and has_stock:
            sku_service_level = float(stock.get("service_level") or service_level) if stock else service_level
            z = _Z.get(sku_service_level, 1.645)
            avg_daily, avg_std = _avg_daily_forecast(model_forecasts, lead_time)
            dias_cobertura = stock_actual / avg_daily if avg_daily > 0 else 9999.0
            signal = _calc_signal(dias_cobertura, lead_time)
            recomendado = _calc_recommended(
                stock_actual, avg_daily, avg_std, lead_time, moq, sku_service_level
            )
            recomendado = _gate_recommended_by_signal(signal, recomendado)
            valor_inventario = (
                round(stock_actual * float(stock["costo_unitario"]), 2)
                if stock.get("costo_unitario") is not None else None
            )
            _demanda_lt  = round(avg_daily * lead_time, 2)
            _safety      = round(z * avg_std * math.sqrt(lead_time), 2)
            _antes_moq   = round(max(0.0, _demanda_lt + _safety - stock_actual), 2)
            calc_explanation = {
                "demanda_diaria":    round(avg_daily, 2),
                "lead_time_dias":    lead_time,
                "demanda_lead_time": _demanda_lt,
                "safety_stock":      _safety,
                "stock_actual":      stock_actual,
                "antes_moq":         _antes_moq,
                "moq":               moq,
                "cantidad_final":    recomendado,
            }
            if recomendado <= 0:
                # Enough stock: keep the numbers (the what-if simulator needs
                # them) but flag it so the tooltip shows "no ordering needed".
                calc_explanation["suficiente"] = True
        else:
            avg_daily = avg_std = None
            dias_cobertura = None
            signal = "SIN_DATOS"
            recomendado = None
            valor_inventario = None
            calc_explanation = None

        # Recent stock history (last 14 days, at most 10 points for sparkline)
        history: list[dict] = []
        if has_stock:
            try:
                history = get_stock_history(tenant_id, sku, days=14)[-10:]
            except Exception:
                pass

        # "__all__" is the internal sentinel used when the dataset has no SKU/group
        # column (single-series session) — it must never surface unexplained as a SKU
        # name in the UI, so give it a friendly label traceable to its real cause.
        display_name = stock.get("display_name") if stock else None
        if sku == "__all__" and not display_name:
            display_name = "Serie única (sin columna SKU)"

        items.append({
            "sku":                sku,
            "display_name":       display_name,
            "stock_actual":       stock_actual,
            "stock_minimo":       float(stock["stock_minimo"]) if stock else 0.0,
            "lead_time_dias":     lead_time,
            "costo_unitario":     float(stock["costo_unitario"]) if stock and stock.get("costo_unitario") is not None else None,
            "moq":                moq,
            "proveedor":          stock.get("proveedor") if stock else None,
            "notas":              stock.get("notas") if stock else None,
            "precio_venta":       float(stock["precio_venta"]) if stock and stock.get("precio_venta") is not None else None,
            "categoria":          stock.get("categoria") if stock else None,
            "marca":              stock.get("marca") if stock else None,
            "unidad_medida":      stock.get("unidad_medida") if stock else None,
            "codigo_barras":      stock.get("codigo_barras") if stock else None,
            "has_forecast":       has_forecast,
            "has_stock":          has_stock,
            "demanda_diaria":     round(avg_daily, 4) if avg_daily is not None else None,
            "demanda_lead_time":  round(avg_daily * lead_time, 2) if avg_daily is not None else None,
            "dias_cobertura":     round(dias_cobertura, 1) if dias_cobertura is not None and dias_cobertura < 9990 else None,
            "signal":             signal,
            "cantidad_recomendada": recomendado,
            "valor_inventario":   valor_inventario,
            "n_models":           len(model_forecasts),
            "xyz":               _classify_xyz(cv_by_sku.get(sku)),
            "stock_history":     history,
            "calc_explanation":  calc_explanation,
            "demand_trend_pct":  None,  # populated by morning_briefing; None by default in status
        })

    # ABC classification across all items (needs demand info so done after building list)
    abc_map = _classify_abc(items)
    for item in items:
        item["abc"] = abc_map.get(item["sku"], "?")
        item["abc_xyz"] = f"{item['abc']}{item['xyz']}" if item["xyz"] != "?" else item["abc"]

    items.sort(key=lambda x: (_SIGNAL_PRIORITY.get(x["signal"], 5), x["dias_cobertura"] or 9999))
    return items


# ── Promotion / event impact simulator (feature 2.3) ─────────────────────────

def simulate_event_impact(
    tenant_id: str,
    session_id: str,
    start_date,
    end_date,
    multiplier: float,
    event_name: Optional[str] = None,
) -> dict:
    """
    Project what a demand event (promo, season) does to each SKU:
    extra units to sell, whether current stock survives it, how much to order
    and the latest date to place that order (event start − lead time).

    Pure read — nothing is persisted. Uses the same per-SKU daily demand the
    semáforo uses, so the simulation is consistent with the rest of the app.
    """
    from datetime import date as _date, timedelta

    if isinstance(start_date, str):
        start_date = _date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = _date.fromisoformat(end_date)
    if end_date < start_date:
        raise ValueError("end_date no puede ser anterior a start_date")
    if multiplier <= 0:
        raise ValueError("multiplier debe ser mayor que 0")

    today = _date.today()
    event_days = (end_date - start_date).days + 1
    days_until_start = max(0, (start_date - today).days)

    items = get_inventory_status(tenant_id, session_id)
    rows: list[dict] = []

    for it in items:
        daily = it.get("demanda_diaria")
        if not daily or daily <= 0:
            continue  # sin forecast no hay nada que simular

        lead_time = int(it.get("lead_time_dias") or 15)
        moq       = float(it.get("moq") or 1)
        stock     = it.get("stock_actual")
        cost      = it.get("costo_unitario")

        baseline_units = daily * event_days
        event_units    = baseline_units * multiplier
        extra_units    = event_units - baseline_units

        # Stock projected to the event start: today's stock minus normal
        # consumption until then (floored at 0).
        stock_at_start = None
        deficit = None
        pedir = None
        if stock is not None:
            stock_at_start = max(0.0, float(stock) - daily * days_until_start)
            deficit = max(0.0, event_units - stock_at_start)
            if deficit > 0:
                pedir = math.ceil(deficit / moq) * moq

        order_by = start_date - timedelta(days=lead_time)
        late = order_by < today  # ordering today would still arrive mid/after event

        rows.append({
            "sku":               it["sku"],
            "display_name":      it.get("display_name"),
            "proveedor":         it.get("proveedor"),
            "demanda_diaria":    round(daily, 2),
            "baseline_units":    round(baseline_units, 1),
            "event_units":       round(event_units, 1),
            "extra_units":       round(extra_units, 1),
            "stock_actual":      stock,
            "stock_al_inicio":   round(stock_at_start, 1) if stock_at_start is not None else None,
            "deficit":           round(deficit, 1) if deficit is not None else None,
            "cantidad_pedir":    pedir,
            "valor_pedido":      round(pedir * float(cost), 2) if (pedir and cost is not None) else None,
            "lead_time_dias":    lead_time,
            "order_by":          order_by.isoformat(),
            "llega_tarde":       late,
            "en_riesgo":         bool(deficit and deficit > 0),
        })

    # Riskiest first: SKUs that need an order, largest deficit on top
    rows.sort(key=lambda r: (not r["en_riesgo"], -(r["deficit"] or 0)))

    at_risk = [r for r in rows if r["en_riesgo"]]
    total_pedir = sum(r["cantidad_pedir"] or 0 for r in at_risk)
    total_valor = sum(r["valor_pedido"] or 0 for r in at_risk)
    earliest_order_by = min((r["order_by"] for r in at_risk), default=None)

    return {
        "event_name":  event_name,
        "start_date":  start_date.isoformat(),
        "end_date":    end_date.isoformat(),
        "event_days":  event_days,
        "multiplier":  multiplier,
        "items":       rows,
        "summary": {
            "skus_simulados":     len(rows),
            "skus_en_riesgo":     len(at_risk),
            "unidades_extra":     round(sum(r["extra_units"] for r in rows), 1),
            "total_pedir":        round(total_pedir, 1),
            "valor_total_pedido": round(total_valor, 2),
            "pedir_antes_de":     earliest_order_by,
            "algun_pedido_tarde": any(r["llega_tarde"] for r in at_risk),
        },
    }


# ── Events (temporadas / promociones) ────────────────────────────────────────

def list_events(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM inventory_events WHERE tenant_id = %s ORDER BY start_date",
        (tenant_id,),
    )


def get_event(tenant_id: str, event_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM inventory_events WHERE tenant_id = %s AND id = %s",
        (tenant_id, event_id),
    )


def get_upcoming_events(tenant_id: str, days_ahead: int = 60) -> list[dict]:
    """Events starting within the next N days (for dashboard banner)."""
    return query(
        """SELECT * FROM inventory_events
           WHERE tenant_id = %s AND start_date <= CURRENT_DATE + %s
             AND end_date >= CURRENT_DATE
           ORDER BY start_date""",
        (tenant_id, days_ahead),
    )


def create_event(tenant_id: str, data: dict) -> dict:
    eid = f"ev_{__import__('uuid').uuid4().hex[:12]}"
    execute(
        """INSERT INTO inventory_events (id, tenant_id, name, start_date, end_date, multiplier, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (eid, tenant_id, data["name"], data["start_date"], data["end_date"],
         data.get("multiplier", 1.0), data.get("notes")),
    )
    return query_one("SELECT * FROM inventory_events WHERE id = %s", (eid,))


def update_event(tenant_id: str, event_id: str, data: dict) -> Optional[dict]:
    allowed = {"name", "start_date", "end_date", "multiplier", "notes"}
    safe = {k: v for k, v in data.items() if k in allowed}
    if not safe:
        return query_one("SELECT * FROM inventory_events WHERE id = %s AND tenant_id = %s", (event_id, tenant_id))
    cols = ", ".join(f"{k} = %s" for k in safe)
    execute(
        f"UPDATE inventory_events SET {cols} WHERE id = %s AND tenant_id = %s",
        (*safe.values(), event_id, tenant_id),
    )
    return query_one("SELECT * FROM inventory_events WHERE id = %s AND tenant_id = %s", (event_id, tenant_id))


def delete_event(tenant_id: str, event_id: str) -> None:
    execute("DELETE FROM inventory_events WHERE id = %s AND tenant_id = %s", (event_id, tenant_id))


# ── PDF report ────────────────────────────────────────────────────────────────

def generate_inventory_pdf(tenant_id: str, session_id: str, service_level: float = 0.95) -> bytes:
    """
    Generates a one-page executive summary PDF in Spanish.
    Returns raw bytes ready for StreamingResponse.
    """
    from io import BytesIO
    from datetime import date
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    items = get_inventory_status(tenant_id, session_id, service_level)

    # ── Color palette ──────────────────────────────────────────────────────
    RED    = colors.HexColor("#ef4444")
    AMBER  = colors.HexColor("#f59e0b")
    GREEN  = colors.HexColor("#22c55e")
    BLUE   = colors.HexColor("#3b82f6")
    INDIGO = colors.HexColor("#6366f1")
    DARK   = colors.HexColor("#0f172a")
    LIGHT  = colors.HexColor("#f8fafc")
    BORDER = colors.HexColor("#e2e8f0")

    SIGNAL_COLORS = {
        "PEDIR_YA": RED, "PEDIR_PRONTO": AMBER,
        "OK": GREEN, "SOBRESTOCK": BLUE, "SIN_DATOS": colors.grey,
    }
    SIGNAL_LABELS = {
        "PEDIR_YA": "🔴 PEDIR YA", "PEDIR_PRONTO": "🟡 Pedir pronto",
        "OK": "🟢 OK", "SOBRESTOCK": "🔵 Sobrestock", "SIN_DATOS": "Sin datos",
    }

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=1.8*cm, bottomMargin=1.5*cm,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
    )

    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", fontSize=18, fontName="Helvetica-Bold",
                        textColor=DARK, spaceAfter=2)
    H2 = ParagraphStyle("H2", fontSize=10, fontName="Helvetica-Bold",
                        textColor=INDIGO, spaceAfter=6, spaceBefore=12,
                        textTransform="uppercase")
    BODY = ParagraphStyle("BODY", fontSize=9, fontName="Helvetica", textColor=DARK,
                          leading=14)
    SMALL = ParagraphStyle("SMALL", fontSize=8, fontName="Helvetica",
                           textColor=colors.HexColor("#64748b"))
    CELL  = ParagraphStyle("CELL", fontSize=8, fontName="Helvetica", textColor=DARK)
    CELL_BOLD = ParagraphStyle("CELL_BOLD", fontSize=8, fontName="Helvetica-Bold", textColor=DARK)

    story = []

    # ── Header ─────────────────────────────────────────────────────────────
    today_str = date.today().strftime("%d de %B de %Y")
    header_data = [[
        Paragraph("<b>RESUMEN DE INVENTARIO</b>", H1),
        Paragraph(f"<font color='#64748b'>Generado el {today_str}</font>", SMALL),
    ]]
    header_table = Table(header_data, colWidths=["70%", "30%"])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("ALIGN",  (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=INDIGO, spaceAfter=12))

    # ── KPI row ─────────────────────────────────────────────────────────────
    total   = len(items)
    urgent  = sum(1 for i in items if i["signal"] == "PEDIR_YA")
    warning = sum(1 for i in items if i["signal"] == "PEDIR_PRONTO")
    ok      = sum(1 for i in items if i["signal"] == "OK")
    over    = sum(1 for i in items if i["signal"] == "SOBRESTOCK")
    valor   = sum(i["valor_inventario"] for i in items if i.get("valor_inventario") or 0)

    def _kpi_cell(number, label, color):
        return [
            Paragraph(f"<font color='#{color}'><b>{number}</b></font>",
                      ParagraphStyle("kpi", fontSize=22, fontName="Helvetica-Bold",
                                     alignment=TA_CENTER, textColor=colors.HexColor(f"#{color}"))),
            Paragraph(label, ParagraphStyle("kpi_lbl", fontSize=8, fontName="Helvetica",
                                            alignment=TA_CENTER, textColor=colors.HexColor("#64748b"))),
        ]

    kpi_data = [[
        _kpi_cell(total,   "Total SKUs",     "6366f1"),
        _kpi_cell(urgent,  "Pedir YA 🔴",   "ef4444"),
        _kpi_cell(warning, "Pedir pronto 🟡","f59e0b"),
        _kpi_cell(ok,      "OK 🟢",          "22c55e"),
        _kpi_cell(over,    "Sobrestock 🔵",  "3b82f6"),
        _kpi_cell(
            f"${valor:,.0f}" if valor else "—",
            "Valor inventario", "6366f1",
        ),
    ]]
    # Flatten for table row
    flat_kpi = [[cell for sublist in kpi_data[0] for cell in (sublist if isinstance(sublist, list) else [sublist])]]
    kpi_table_data = [[
        [Paragraph(f"<b><font color='#{c}'>{n}</font></b>",
                   ParagraphStyle("kn", fontSize=20, fontName="Helvetica-Bold", alignment=TA_CENTER)),
         Paragraph(l, ParagraphStyle("kl", fontSize=7.5, alignment=TA_CENTER,
                                     textColor=colors.HexColor("#64748b")))]
        for n, l, c in [
            (total,   "Total SKUs",      "6366f1"),
            (urgent,  "Pedir YA",        "ef4444"),
            (warning, "Pedir pronto",    "f59e0b"),
            (ok,      "OK",              "22c55e"),
            (over,    "Sobrestock",      "3b82f6"),
            (f"${valor:,.0f}" if valor else "—", "Valor bodega", "6366f1"),
        ]
    ]]
    kpi_row = [[Table([[v] for v in cell], colWidths=["100%"]) for cell in kpi_table_data[0]]]
    kpi_t = Table(kpi_row, colWidths=[doc.width / 6] * 6)
    kpi_t.setStyle(TableStyle([
        ("BOX",    (0, 0), (-1, -1), 0.5, BORDER),
        ("GRID",   (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kpi_t)
    story.append(Spacer(1, 10))

    # ── Urgent SKUs table ──────────────────────────────────────────────────
    critical_items = [i for i in items if i["signal"] in ("PEDIR_YA", "PEDIR_PRONTO")][:20]
    if critical_items:
        story.append(Paragraph("Productos que requieren acción", H2))
        tdata = [[
            Paragraph("<b>SKU</b>", CELL_BOLD),
            Paragraph("<b>Nombre</b>", CELL_BOLD),
            Paragraph("<b>Señal</b>", CELL_BOLD),
            Paragraph("<b>Stock actual</b>", CELL_BOLD),
            Paragraph("<b>Días cobertura</b>", CELL_BOLD),
            Paragraph("<b>Pedir</b>", CELL_BOLD),
            Paragraph("<b>Proveedor</b>", CELL_BOLD),
        ]]
        row_styles = []
        for idx, item in enumerate(critical_items):
            sig_color = SIGNAL_COLORS.get(item["signal"], colors.grey)
            sig_label = SIGNAL_LABELS.get(item["signal"], item["signal"])
            tdata.append([
                Paragraph(item["sku"], CELL),
                Paragraph(item.get("display_name") or "—", CELL),
                Paragraph(sig_label, ParagraphStyle("sig", fontSize=8,
                          fontName="Helvetica-Bold", textColor=sig_color)),
                Paragraph(f"{item['stock_actual']:,.0f}" if item.get("stock_actual") is not None else "—", CELL),
                Paragraph(f"{item['dias_cobertura']:.0f} días" if item.get("dias_cobertura") is not None else "—", CELL),
                Paragraph(f"<b>{item['cantidad_recomendada']:,.0f}</b>" if item.get("cantidad_recomendada") else "—",
                          ParagraphStyle("qty", fontSize=8, fontName="Helvetica-Bold", textColor=GREEN)),
                Paragraph(item.get("proveedor") or "—", CELL),
            ])
            if idx % 2 == 0:
                row_styles.append(("BACKGROUND", (0, idx+1), (-1, idx+1), colors.HexColor("#f8fafc")))

        cws = [2.2*cm, 3.5*cm, 2.5*cm, 2.2*cm, 2.4*cm, 2.0*cm, 3.2*cm]
        t = Table(tdata, colWidths=cws)
        ts = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
        for s in row_styles:
            ts.add(*s)
        t.setStyle(ts)
        story.append(t)
        story.append(Spacer(1, 8))

    # ── All SKUs compact table ─────────────────────────────────────────────
    remaining = [i for i in items if i["signal"] not in ("PEDIR_YA", "PEDIR_PRONTO")]
    if remaining:
        story.append(Paragraph("Resto del inventario", H2))
        small_data = [[
            Paragraph("<b>SKU</b>", CELL_BOLD),
            Paragraph("<b>Nombre</b>", CELL_BOLD),
            Paragraph("<b>Señal</b>", CELL_BOLD),
            Paragraph("<b>Días cobertura</b>", CELL_BOLD),
            Paragraph("<b>ABC-XYZ</b>", CELL_BOLD),
        ]]
        for item in remaining[:30]:
            small_data.append([
                Paragraph(item["sku"], CELL),
                Paragraph(item.get("display_name") or "—", CELL),
                Paragraph(SIGNAL_LABELS.get(item["signal"], "—"),
                          ParagraphStyle("s2", fontSize=8, textColor=SIGNAL_COLORS.get(item["signal"], colors.grey))),
                Paragraph(f"{item['dias_cobertura']:.0f}d" if item.get("dias_cobertura") is not None else "—", CELL),
                Paragraph(item.get("abc_xyz") or "—", CELL),
            ])
        st = Table(small_data, colWidths=[3*cm, 5*cm, 3.2*cm, 3*cm, 2*cm])
        st.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(st)

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Paragraph(
        f"Generado automáticamente · Sesión {session_id[:8]} · Nivel de servicio {service_level*100:.0f}%",
        ParagraphStyle("footer", fontSize=7, textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER),
    ))

    doc.build(story)
    return buf.getvalue()


# ── Decision Centre helpers ───────────────────────────────────────────────────

def _calc_demand_trend(tenant_id: str, sku: str, avg_daily: float, days: int = 14) -> Optional[float]:
    """
    Returns % change in actual demand vs forecast.
    Positive = demand is running above forecast (risk of stockout).
    Negative = demand is below forecast (risk of overstock).
    Uses stock snapshot history to estimate actual consumption.
    Returns None if insufficient data or change is not significant (< 15%).
    """
    if avg_daily <= 0:
        return None

    history = get_stock_history(tenant_id, sku, days=days)
    if len(history) < 4:
        return None

    # Actual depletion (stock went down)
    first_stock = history[0]['stock']
    last_stock  = history[-1]['stock']
    actual_depletion = first_stock - last_stock

    # Expected depletion based on forecast
    period_days      = min(len(history), days)
    expected_depletion = avg_daily * period_days

    if expected_depletion <= 0 or actual_depletion < 0:
        return None

    trend_pct = ((actual_depletion - expected_depletion) / expected_depletion) * 100

    # Only flag if significant (>= 15%)
    return round(trend_pct, 1) if abs(trend_pct) >= 15 else None


def get_excluded_skus(tenant_id: str, session_id: str) -> list[dict]:
    """SKUs uploaded but left out of the forecast (recorded at training time)."""
    from backend.db import session_store
    result = session_store.get_training_result(tenant_id, session_id) or {}
    return result.get("excluded_skus") or []


def get_demand_spikes(
    tenant_id: str,
    session_id: str,
    service_level: float = 0.95,
    uplift_threshold: float = 0.25,
    items: Optional[list[dict]] = None,
    forecasts: Optional[dict] = None,
) -> list[dict]:
    """
    Proactive demand alerts — the value Excel can't give.

    Detects a demand peak the model projects within the forecast horizon and,
    given each SKU's lead time, computes the *latest date to order* so the spike
    is covered. Lets the buyer act on a peak the forecast sees BEFORE the stock
    semaphore turns red.

    Honest about its limits: only fires for peaks still in the future (relative
    to today) and within whatever horizon the session was trained for. If the
    forecast horizon is shorter than the lead time, the alert says "the peak
    arrives inside your lead time — order now if you haven't".
    """
    from datetime import date as _date

    if items is None:
        items = get_inventory_status(tenant_id, session_id, service_level)
    if forecasts is None:
        from backend.db import session_store
        forecasts = session_store.get_forecasts(tenant_id, session_id) or {}

    items_by_sku = {i["sku"]: i for i in items}
    today = _date.today()
    alerts: list[dict] = []

    for sku, model_forecasts in forecasts.items():
        item = items_by_sku.get(sku)
        if not item or not item.get("has_forecast"):
            continue

        curve = _avg_forecast_curve(model_forecasts)
        if len(curve) < 2:
            continue

        baseline = item.get("demanda_diaria")
        if not baseline or baseline <= 0:
            baseline = sum(c["value"] for c in curve) / len(curve)
        if not baseline or baseline <= 0:
            continue

        peak = max(curve, key=lambda c: c["value"])
        uplift = (peak["value"] - baseline) / baseline
        # Require both a relative and a small absolute jump (avoids noise on
        # tiny-volume SKUs where +30% is still < 1 unit).
        if uplift < uplift_threshold or (peak["value"] - baseline) < 1:
            continue

        lead = int(item.get("lead_time_dias") or 15)

        peak_date: Optional[object] = None
        days_until = peak["step"] + 1
        if peak.get("date"):
            try:
                peak_date = _date.fromisoformat(peak["date"])
                days_until = (peak_date - today).days
            except Exception:
                peak_date = None

        # Skip peaks already in the past (stale session run long after training).
        if days_until <= 0:
            continue

        order_by = (peak_date - timedelta(days=lead)) if peak_date else None
        already_late = bool(order_by and order_by <= today)

        alerts.append({
            "sku":             sku,
            "display_name":    item.get("display_name") or sku,
            "proveedor":       item.get("proveedor"),
            "baseline_diaria": round(baseline, 1),
            "peak_value":      round(peak["value"], 1),
            "uplift_pct":      round(uplift * 100),
            "peak_date":       peak_date.isoformat() if peak_date else None,
            "days_until_peak": days_until,
            "lead_time_dias":  lead,
            "order_by_date":   order_by.isoformat() if order_by else None,
            "already_late":    already_late,
            "signal":          item.get("signal"),
        })

    # Most actionable first: ones you're already late for, then soonest deadline.
    alerts.sort(key=lambda a: (not a["already_late"], a["days_until_peak"]))
    return alerts[:8]


def generate_recommendations(items: list[dict]) -> list[dict]:
    """
    Generates plain-language, actionable recommendations from inventory status items.
    Each recommendation has: priority (1=critical), sku, name, rec_type, text, action.
    """
    recs: list[dict] = []

    for item in items:
        sku      = item['sku']
        name     = item.get('display_name') or sku
        signal   = item['signal']
        dias     = item.get('dias_cobertura')
        lead     = item.get('lead_time_dias', 15)
        qty      = item.get('cantidad_recomendada') or 0
        prov     = item.get('proveedor') or 'el proveedor'
        abc      = item.get('abc', '?')
        trend    = item.get('demand_trend_pct')
        valor    = item.get('valor_inventario')

        if signal == 'PEDIR_YA' and dias is not None:
            recs.append({
                'priority': 1, 'sku': sku, 'name': name,
                'rec_type': 'STOCKOUT_RISK',
                'text': (
                    f"Emite la orden de {name} HOY — tienes {dias:.0f} días de stock "
                    f"y {prov} tarda {lead} días en entregar. "
                    f"Si no actúas hoy, habrá quiebre antes de recibir el pedido."
                ),
                'action': f"Pedir {qty:.0f} unidades a {prov}" if qty > 0 else "Emitir orden urgente",
                'signal': signal,
            })

        elif signal == 'PEDIR_PRONTO' and dias is not None:
            recs.append({
                'priority': 2, 'sku': sku, 'name': name,
                'rec_type': 'REORDER_SOON',
                'text': (
                    f"{name} tiene {dias:.0f} días de cobertura frente a un lead time de {lead} días. "
                    f"Emite el pedido esta semana para mantener el colchón de seguridad."
                ),
                'action': f"Pedir {qty:.0f} unidades antes del viernes" if qty > 0 else "Planificar pedido",
                'signal': signal,
            })

        if trend is not None:
            if trend >= 15:
                recs.append({
                    'priority': 3, 'sku': sku, 'name': name,
                    'rec_type': 'DEMAND_UP',
                    'text': (
                        f"La demanda real de {name} está corriendo {trend:.0f}% por encima del pronóstico. "
                        f"Considera aumentar el stock de seguridad o anticipar el próximo pedido."
                    ),
                    'action': "Revisar stock de seguridad",
                    'signal': signal,
                })
            elif trend <= -15:
                recs.append({
                    'priority': 4, 'sku': sku, 'name': name,
                    'rec_type': 'DEMAND_DOWN',
                    'text': (
                        f"La demanda de {name} está {abs(trend):.0f}% por debajo del pronóstico. "
                        f"Verifica si perdiste un cliente clave o hay un cambio de tendencia real."
                    ),
                    'action': "Revisar con el equipo de ventas",
                    'signal': signal,
                })

        if signal == 'SOBRESTOCK' and abc in ('A', 'B') and dias is not None and valor:
            excess_days = dias - lead * 3
            recs.append({
                'priority': 5, 'sku': sku, 'name': name,
                'rec_type': 'OVERSTOCK',
                'text': (
                    f"{name} tiene {dias:.0f} días de cobertura ({excess_days:.0f} días más de lo óptimo). "
                    f"Pausar el próximo pedido liberaría ${valor:,.0f} en capital de trabajo."
                ),
                'action': "Pausar próximo pedido",
                'signal': signal,
            })

    # Deduplicate by sku+rec_type, keep highest priority
    seen: dict = {}
    for r in sorted(recs, key=lambda x: x['priority']):
        key = f"{r['sku']}_{r['rec_type']}"
        if key not in seen:
            seen[key] = r

    return list(seen.values())[:20]  # top 20 recommendations


def get_morning_briefing(tenant_id: str, session_id: str, service_level: float = 0.95) -> dict:
    """
    Returns everything needed for the daily operations briefing:
    - risks: SKUs with PEDIR_YA signal
    - warnings: SKUs with PEDIR_PRONTO signal
    - overstocked: SKUs with SOBRESTOCK, ordered by value
    - demand_changes: SKUs with significant demand trend
    - recommendations: plain-language action items
    - kpis: summary metrics
    """
    items = get_inventory_status(tenant_id, session_id, service_level)

    # Compute demand trend for each item that has forecast + stock data
    for item in items:
        avg = item.get('demanda_diaria')
        if avg and avg > 0 and item.get('has_stock') and item.get('has_forecast'):
            item['demand_trend_pct'] = _calc_demand_trend(
                tenant_id, item['sku'], avg, days=14
            )
        else:
            item['demand_trend_pct'] = None

    risks      = [i for i in items if i['signal'] == 'PEDIR_YA']
    warnings   = [i for i in items if i['signal'] == 'PEDIR_PRONTO']
    overstocked = sorted(
        [i for i in items if i['signal'] == 'SOBRESTOCK' and i.get('valor_inventario')],
        key=lambda x: x.get('valor_inventario') or 0,
        reverse=True,
    )
    demand_changes = [i for i in items if i.get('demand_trend_pct') is not None]

    # Proactive: future demand peaks the forecast sees, with order-by dates.
    demand_spikes: list[dict] = []
    try:
        from backend.db import session_store
        spike_forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
        demand_spikes = get_demand_spikes(
            tenant_id, session_id, service_level,
            items=items, forecasts=spike_forecasts,
        )
    except Exception as e:
        log.warning("get_demand_spikes failed for session=%s: %s", session_id, e)

    recs = generate_recommendations(items)

    # Pull session-level forecast accuracy if available
    avg_accuracy: Optional[float] = None
    try:
        from backend.db import session_store
        result = session_store.get_training_result(tenant_id, session_id) or {}
        metrics = result.get('metrics', {})
        rows = metrics.get('rows', [])
        if rows:
            wapes = [r['wape'] for r in rows if r.get('wape') is not None]
            if wapes:
                avg_accuracy = round(1 - (sum(wapes) / len(wapes)), 4)
    except Exception:
        pass

    total_valor    = sum(i['valor_inventario'] for i in items if i.get('valor_inventario') or 0)
    overstock_val  = sum(i['valor_inventario'] for i in overstocked if i.get('valor_inventario') or 0)

    # Session name
    try:
        from backend.sessions.service import get_session
        sess = get_session(tenant_id, session_id) or {}
        session_name = sess.get('name', session_id[:8])
    except Exception:
        session_name = session_id[:8]

    return {
        'date':         datetime.utcnow().strftime('%Y-%m-%d'),
        'session_id':   session_id,
        'session_name': session_name,
        'has_data':     bool(items),
        'risks':        risks[:10],
        'warnings':     warnings[:10],
        'overstocked':  overstocked[:10],
        'demand_changes': demand_changes[:8],
        'demand_spikes': demand_spikes,
        'excluded_skus': get_excluded_skus(tenant_id, session_id),
        'recommendations': recs,
        'kpis': {
            'total_skus':           len(items),
            'pedir_ya':             len(risks),
            'pedir_pronto':         len(warnings),
            'ok':                   sum(1 for i in items if i['signal'] == 'OK'),
            'sobrestock':           len(overstocked),
            'sin_datos':            sum(1 for i in items if i['signal'] == 'SIN_DATOS'),
            'avg_accuracy':         avg_accuracy,
            'total_inventory_value': round(total_valor, 2),
            'capital_in_overstock':  round(overstock_val, 2),
            'demand_alerts':        len(demand_changes),
            'demand_spikes':        len(demand_spikes),
        },
    }


# ── Alert logic ───────────────────────────────────────────────────────────────

def get_tenants_with_active_sessions() -> list[dict]:
    """Returns all tenants that have at least one COMPLETED session."""
    return query(
        """SELECT DISTINCT s.tenant_id, t.name AS tenant_name,
                  MAX(s.updated_at) AS last_session_at
           FROM sessions s
           JOIN tenants t ON t.id = s.tenant_id
           WHERE s.status = 'COMPLETED'
           GROUP BY s.tenant_id, t.name""",
    )


def get_latest_completed_session(tenant_id: str) -> Optional[dict]:
    return query_one(
        """SELECT id AS session_id FROM sessions
           WHERE tenant_id = %s AND status = 'COMPLETED'
           ORDER BY updated_at DESC LIMIT 1""",
        (tenant_id,),
    )


def get_tenant_admin_emails(tenant_id: str) -> list[str]:
    rows = query(
        """SELECT email FROM users
           WHERE tenant_id = %s AND role IN ('admin', 'manager')
           AND email IS NOT NULL""",
        (tenant_id,),
    )
    return [r["email"] for r in rows]


def get_tenant_admin_whatsapps(tenant_id: str) -> list[str]:
    """E.164 numbers of admins/managers who opted into WhatsApp alerts."""
    rows = query(
        """SELECT whatsapp_number FROM users
           WHERE tenant_id = %s AND role IN ('admin', 'manager')
           AND whatsapp_number IS NOT NULL AND whatsapp_number <> ''""",
        (tenant_id,),
    )
    return [r["whatsapp_number"] for r in rows]


def run_daily_inventory_alerts() -> None:
    """
    Called once per day by the scheduler.
    For each tenant with a completed session, checks for PEDIR_YA SKUs
    and sends an alert email to admin/manager users.
    """
    from backend.notifications.email import send_inventory_alert_email
    from backend.config import settings

    tenants = get_tenants_with_active_sessions()
    log.info("inventory_alert: checking %d tenants", len(tenants))

    for tenant in tenants:
        tid = tenant["tenant_id"]
        try:
            session = get_latest_completed_session(tid)
            if not session:
                continue

            items = get_inventory_status(tid, session["session_id"])
            critical = [i for i in items if i["signal"] == "PEDIR_YA"]
            warning  = [i for i in items if i["signal"] == "PEDIR_PRONTO"]

            if not critical and not warning:
                continue

            app_url = getattr(settings, "frontend_url", "http://localhost:3000")
            inventory_url = f"{app_url}/inventory"

            emails = get_tenant_admin_emails(tid)
            for email in emails:
                try:
                    send_inventory_alert_email(
                        to=email,
                        critical_items=critical[:10],
                        warning_items=warning[:5],
                        inventory_url=inventory_url,
                    )
                except Exception as e:
                    log.warning("alert email failed to=%s: %s", email, e)

            # WhatsApp channel — highest open-rate in LatAm; opt-in per user
            # via users.whatsapp_number. No-op when Twilio isn't configured.
            from backend.notifications.whatsapp import build_inventory_alert_text, send_whatsapp
            numbers = get_tenant_admin_whatsapps(tid)
            if numbers:
                text = build_inventory_alert_text(critical[:10], warning[:5], inventory_url)
                for number in numbers:
                    send_whatsapp(number, text)

        except Exception as e:
            log.error("inventory_alert: tenant=%s error=%s", tid, e)


def _sum_overstock_value(items: list[dict]) -> float:
    """Total valor_inventario of SKUs currently flagged SOBRESTOCK."""
    return sum(
        (i.get("valor_inventario") or 0)
        for i in items
        if i.get("signal") == "SOBRESTOCK"
    )


def run_monthly_overstock_snapshot() -> None:
    """
    Called once a month by the scheduler (day 1). For each tenant with a
    completed session, records the current total SOBRESTOCK value so the
    ROI monthly view can compute capital freed month over month.
    """
    tenants = get_tenants_with_active_sessions()
    log.info("overstock_snapshot: checking %d tenants", len(tenants))

    for tenant in tenants:
        tid = tenant["tenant_id"]
        try:
            session = get_latest_completed_session(tid)
            if not session:
                continue

            items = get_inventory_status(tid, session["session_id"])
            overstock_value = _sum_overstock_value(items)

            execute(
                """INSERT INTO inventory_overstock_snapshots
                       (tenant_id, session_id, overstock_value)
                   VALUES (%s, %s, %s)""",
                (tid, session["session_id"], overstock_value),
            )
        except Exception as e:
            log.error("overstock_snapshot: tenant=%s error=%s", tid, e)
