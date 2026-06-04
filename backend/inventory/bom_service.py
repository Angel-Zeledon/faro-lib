"""
BOM (Bill of Materials) service and MRP Level 1 explosion.

Supports manufacturing planning:
- Define what components/raw materials make up each finished good
- Explode forecast demand into material requirements
- Detect shortages before they happen
"""

import math
import logging
from typing import Optional
from backend.db.connection import query, query_one, execute

log = logging.getLogger(__name__)

# Product type labels
PRODUCT_TYPES = {
    'finished_good':  'Producto terminado',
    'semi_finished':  'Semiterminado',
    'component':      'Componente',
    'raw_material':   'Materia prima',
    'packaging':      'Empaque',
    'service':        'Servicio',
}


# ── BOM CRUD ──────────────────────────────────────────────────────────────────

def list_bom(tenant_id: str, parent_sku: str) -> list[dict]:
    """Returns all BOM items for a given parent SKU, joined with child stock info."""
    rows = query(
        """SELECT b.id, b.parent_sku, b.child_sku, b.quantity, b.unit, b.notes,
                  b.created_at,
                  s.display_name AS child_name,
                  s.stock_actual AS child_stock,
                  s.product_type AS child_type,
                  s.costo_unitario AS child_cost
           FROM bom_items b
           LEFT JOIN inventory_stock s
             ON s.tenant_id = b.tenant_id AND s.sku = b.child_sku
           WHERE b.tenant_id = %s AND b.parent_sku = %s
           ORDER BY b.child_sku""",
        (tenant_id, parent_sku),
    )
    return [dict(r) for r in rows]


def list_all_bom(tenant_id: str) -> list[dict]:
    """Returns all BOM items for the tenant."""
    rows = query(
        """SELECT b.parent_sku, b.child_sku, b.quantity, b.unit
           FROM bom_items b WHERE b.tenant_id = %s ORDER BY b.parent_sku, b.child_sku""",
        (tenant_id,),
    )
    return [dict(r) for r in rows]


def upsert_bom_item(tenant_id: str, parent_sku: str, child_sku: str, data: dict) -> dict:
    """Create or update a BOM relationship."""
    if parent_sku == child_sku:
        raise ValueError("A SKU cannot be its own component")

    execute(
        """INSERT INTO bom_items (tenant_id, parent_sku, child_sku, quantity, unit, notes)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, parent_sku, child_sku) DO UPDATE
           SET quantity = EXCLUDED.quantity,
               unit     = EXCLUDED.unit,
               notes    = EXCLUDED.notes""",
        (tenant_id, parent_sku, child_sku,
         data.get('quantity', 1.0),
         data.get('unit'),
         data.get('notes')),
    )
    rows = list_bom(tenant_id, parent_sku)
    return next((r for r in rows if r['child_sku'] == child_sku), {})


def delete_bom_item(tenant_id: str, parent_sku: str, child_sku: str) -> None:
    execute(
        "DELETE FROM bom_items WHERE tenant_id=%s AND parent_sku=%s AND child_sku=%s",
        (tenant_id, parent_sku, child_sku),
    )


def get_parents_using(tenant_id: str, child_sku: str) -> list[dict]:
    """Returns all finished goods that use this SKU as a component."""
    rows = query(
        """SELECT b.parent_sku, b.quantity, b.unit,
                  s.display_name AS parent_name, s.product_type AS parent_type
           FROM bom_items b
           LEFT JOIN inventory_stock s ON s.tenant_id = b.tenant_id AND s.sku = b.parent_sku
           WHERE b.tenant_id = %s AND b.child_sku = %s""",
        (tenant_id, child_sku),
    )
    return [dict(r) for r in rows]


# ── MRP Level 1 Explosion ─────────────────────────────────────────────────────

def explode_requirements(tenant_id: str, session_id: str, horizon_days: int = 30) -> dict:
    """
    MRP Level 1 explosion:
    Given forecasted demand for finished goods + BOM,
    calculates required quantities of each component and raw material.
    Flags shortages and calculates purchase requirements.
    """
    from backend.inventory.service import get_inventory_status

    items = get_inventory_status(tenant_id, session_id)
    if not items:
        return _empty_explosion(session_id, horizon_days)

    # Build maps
    stock_map  = {i['sku']: i for i in items}
    bom_all    = list_all_bom(tenant_id)
    bom_map: dict[str, list[dict]] = {}
    for b in bom_all:
        bom_map.setdefault(b['parent_sku'], []).append(b)

    # Process each item that has a BOM
    finished_goods: list[dict] = []
    material_totals: dict[str, dict] = {}

    for item in items:
        sku          = item['sku']
        product_type = item.get('product_type', 'finished_good')

        if product_type not in ('finished_good', 'semi_finished'):
            continue
        if sku not in bom_map:
            continue

        forecast_demand = round((item.get('demanda_diaria') or 0) * horizon_days, 1)
        current_stock   = item.get('stock_actual') or 0
        to_produce      = max(0.0, forecast_demand - current_stock)

        requirements: list[dict] = []
        for bom_item in bom_map[sku]:
            child_sku = bom_item['child_sku']
            qty_per_unit = float(bom_item['quantity'])
            required_qty = round(qty_per_unit * to_produce, 2)

            child = stock_map.get(child_sku, {})
            child_stock  = child.get('stock_actual') or 0
            child_name   = child.get('display_name') or child_sku
            child_type   = child.get('product_type', 'component')
            child_cost   = child.get('costo_unitario')
            shortage     = round(max(0.0, required_qty - child_stock), 2)

            # Accumulate totals across all finished goods
            if child_sku not in material_totals:
                material_totals[child_sku] = {
                    'sku': child_sku, 'display_name': child_name,
                    'product_type': child_type, 'current_stock': child_stock,
                    'costo_unitario': child_cost, 'total_required': 0.0,
                    'unit': bom_item.get('unit'),
                }
            material_totals[child_sku]['total_required'] += required_qty

            requirements.append({
                'child_sku':         child_sku,
                'display_name':      child_name,
                'product_type':      child_type,
                'quantity_per_unit': qty_per_unit,
                'unit':              bom_item.get('unit'),
                'required_quantity': required_qty,
                'current_stock':     child_stock,
                'shortage':          shortage,
                'status':            'SHORTAGE' if shortage > 0 else 'OK',
            })

        finished_goods.append({
            'sku':          sku,
            'display_name': item.get('display_name') or sku,
            'product_type': product_type,
            'forecast_demand': forecast_demand,
            'current_stock':   current_stock,
            'to_produce':      round(to_produce, 1),
            'signal':          item.get('signal'),
            'requirements':    sorted(requirements, key=lambda r: r['shortage'], reverse=True),
        })

    # Build raw material summary
    raw_summary: list[dict] = []
    for child_sku, data in material_totals.items():
        total_req = data['total_required']
        stock     = data['current_stock']
        shortage  = round(max(0.0, total_req - stock), 2)
        cost      = data.get('costo_unitario')
        raw_summary.append({
            'sku':           child_sku,
            'display_name':  data['display_name'],
            'product_type':  data['product_type'],
            'unit':          data.get('unit'),
            'total_required': round(total_req, 2),
            'current_stock':  stock,
            'shortage':       shortage,
            'status':         'SHORTAGE' if shortage > 0 else 'OK',
            'must_order':     shortage,
            'estimated_cost': round(shortage * cost, 2) if cost and shortage > 0 else None,
        })
    raw_summary.sort(key=lambda x: x['shortage'], reverse=True)

    total_shortage_value = sum(r.get('estimated_cost') or 0 for r in raw_summary)
    has_shortages = any(r['shortage'] > 0 for r in raw_summary)

    return {
        'session_id':            session_id,
        'horizon_days':          horizon_days,
        'finished_goods_count':  len(finished_goods),
        'has_shortages':         has_shortages,
        'total_shortage_value':  round(total_shortage_value, 2),
        'finished_goods':        finished_goods,
        'raw_material_summary':  raw_summary,
    }


def _empty_explosion(session_id: str, horizon_days: int) -> dict:
    return {
        'session_id': session_id, 'horizon_days': horizon_days,
        'finished_goods_count': 0, 'has_shortages': False,
        'total_shortage_value': 0, 'finished_goods': [], 'raw_material_summary': [],
    }
