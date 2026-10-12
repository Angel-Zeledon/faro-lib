"""Things a customer's developer copies: grants, example queries, a view to
create, a forwarder for push, and a transform tester. Pure text, no secrets.

The recommended shortcut: ask the DBA to create a VIEW named
``stockai_inventory`` whose columns are named like StockAI's fields (sku,
warehouse, current_stock, ...). The mapping assistant maps a canonical name at
confidence 1.0, so the whole mapping step becomes "confirm".
"""

from __future__ import annotations

from typing import Any, Optional

from backend.errors import AppError
from backend.inventory_sync import fields as F
from backend.inventory_sync import mapper

ENGINES = ("postgresql", "mysql", "mssql", "oracle", "mongodb")

# Example read-only queries for common ERP schemas: a starting point the
# developer adapts (company / plant filters), never run by us as-is.
ERP_EXAMPLES: dict[str, dict[str, str]] = {
    "sap_mard": {
        "title": "SAP ECC / S/4HANA - MARD (stock per plant / storage location)",
        "sql": "SELECT MATNR AS sku, WERKS || '-' || LGORT AS warehouse, LABST AS current_stock\n"
               "FROM MARD\nWHERE LABST <> 0",
    },
    "oracle_ebs": {
        "title": "Oracle E-Business Suite - MTL_ONHAND_QUANTITIES",
        "sql": "SELECT msi.SEGMENT1 AS sku, moq.SUBINVENTORY_CODE AS warehouse,\n"
               "       SUM(moq.TRANSACTION_QUANTITY) AS current_stock\n"
               "FROM MTL_ONHAND_QUANTITIES moq\n"
               "JOIN MTL_SYSTEM_ITEMS_B msi ON msi.INVENTORY_ITEM_ID = moq.INVENTORY_ITEM_ID\n"
               "  AND msi.ORGANIZATION_ID = moq.ORGANIZATION_ID\n"
               "GROUP BY msi.SEGMENT1, moq.SUBINVENTORY_CODE",
    },
    "odoo": {
        "title": "Odoo - stock_quant (internal locations)",
        "sql": "SELECT pp.default_code AS sku, sl.complete_name AS warehouse, SUM(sq.quantity) AS current_stock\n"
               "FROM stock_quant sq\nJOIN product_product pp ON pp.id = sq.product_id\n"
               "JOIN stock_location sl ON sl.id = sq.location_id AND sl.usage = 'internal'\n"
               "GROUP BY pp.default_code, sl.complete_name",
    },
    "netsuite": {
        "title": "NetSuite (SuiteAnalytics Connect) - InventoryBalance",
        "sql": "SELECT i.itemid AS sku, l.name AS warehouse, b.quantityonhand AS current_stock\n"
               "FROM inventorybalance b\nJOIN item i ON i.id = b.item\nJOIN location l ON l.id = b.location",
    },
    "dynamics": {
        "title": "Dynamics 365 F&O / AX - InventSum",
        "sql": "SELECT s.ITEMID AS sku, d.INVENTLOCATIONID AS warehouse, SUM(s.PHYSICALINVENT) AS current_stock\n"
               "FROM INVENTSUM s\nJOIN INVENTDIM d ON d.INVENTDIMID = s.INVENTDIMID AND d.DATAAREAID = s.DATAAREAID\n"
               "WHERE s.CLOSED = 0\nGROUP BY s.ITEMID, d.INVENTLOCATIONID",
    },
    "generic_ledger": {
        "title": "Any ERP - movement ledger (use the 'ledger' stock model on it)",
        "sql": "SELECT movement_id, item_code AS sku, warehouse_code AS warehouse, quantity,\n"
               "       movement_type, status, posted_at\nFROM inventory_movements",
    },
}


def _q(engine: str, name: str) -> str:
    from backend.datasources import catalog
    return catalog.quote_ident(engine, name)


def grants(engine: str, database: str, schema: Optional[str] = None) -> list[str]:
    from backend.datasources import catalog
    if engine == "mongodb":
        return ['use admin',
                'db.createUser({user: "stockai_reader", pwd: "<choose-a-password>", '
                f'roles: [{{role: "read", db: "{database or "<database>"}"}}]}})']
    return catalog.least_privilege_sql(engine, database or "<database>", schema)


def view_sql(engine: str, source: str, mapping: dict, *, name: str = "stockai_inventory") -> str:
    """CREATE VIEW stockai_inventory AS SELECT <col> AS sku, ... FROM <source>."""
    cols = []
    for f in F.CANONICAL_FIELDS:
        spec = mapping.get(f) or {}
        col = spec.get("column") if isinstance(spec, dict) else spec
        if col:
            cols.append(f"  {_q(engine, col)} AS {f}")
    if not cols:
        raise AppError("inventory_sync_mapping_invalid", "Map at least the SKU and the quantity first.",
                       status_code=422, params={"reason": "required_missing", "fields": "sku, current_stock"})
    create = "CREATE OR REPLACE VIEW" if engine in ("postgresql", "mysql", "oracle") else "CREATE OR ALTER VIEW"
    return f"{create} {name} AS\nSELECT\n" + ",\n".join(cols) + f"\nFROM {source};"


def mongo_view_pipeline(mapping: dict) -> list[dict]:
    """A $project stage renaming mapped fields to StockAI's names (for a
    MongoDB view: db.createView("stockai_inventory", "<collection>", pipeline))."""
    proj: dict[str, Any] = {"_id": 0}
    for f in F.CANONICAL_FIELDS:
        spec = mapping.get(f) or {}
        col = spec.get("column") if isinstance(spec, dict) else spec
        if col:
            proj[f] = f"${col}"
    return [{"$project": proj}]


FORWARDERS: dict[str, str] = {
    # The smallest thing that pushes changes as they happen: a trigger writes
    # an outbox table (read-only for StockAI to pull, or drained by a script).
    "postgresql_outbox": (
        "CREATE TABLE stockai_outbox (id BIGSERIAL PRIMARY KEY, sku TEXT, warehouse TEXT,\n"
        "  current_stock NUMERIC, changed_at TIMESTAMPTZ DEFAULT now());\n"
        "CREATE OR REPLACE FUNCTION stockai_outbox_fn() RETURNS trigger AS $$\n"
        "BEGIN INSERT INTO stockai_outbox (sku, warehouse, current_stock)\n"
        "  VALUES (NEW.<sku_col>, NEW.<warehouse_col>, NEW.<qty_col>); RETURN NEW; END $$ LANGUAGE plpgsql;\n"
        "CREATE TRIGGER stockai_outbox_trg AFTER INSERT OR UPDATE ON <stock_table>\n"
        "  FOR EACH ROW EXECUTE FUNCTION stockai_outbox_fn();\n"
        "-- Then sync the outbox table incrementally (watermark: id), or drain it with the script below."),
    "python_push": (
        "import json, os, urllib.request\n"
        "URL = 'https://<your-stockai>/api/v1/inventory-sync/profiles/<profile_id>/ingest'\n"
        "def push(batch_id, rows):\n"
        "    body = json.dumps({'batch_id': batch_id, 'rows': rows}).encode()\n"
        "    req = urllib.request.Request(URL, body, {'Content-Type': 'application/json',\n"
        "          'Authorization': 'Bearer ' + os.environ['STOCKAI_API_KEY']})\n"
        "    with urllib.request.urlopen(req, timeout=60) as r:\n"
        "        return json.load(r)  # retry with the SAME batch_id: it is idempotent\n"),
    "curl_ndjson": (
        "curl -X POST 'https://<your-stockai>/api/v1/inventory-sync/profiles/<profile_id>/ingest?batch_id=2026-10-12T02' \\\n"
        "  -H 'Authorization: Bearer $STOCKAI_API_KEY' -H 'Content-Type: application/x-ndjson' \\\n"
        "  --data-binary @changes.ndjson"),
}


def recipes(engine: Optional[str], database: Optional[str] = None, schema: Optional[str] = None) -> dict:
    from backend.config import settings
    egress = [ip.strip() for ip in (getattr(settings, "inventory_sync_egress_ips", "") or "").split(",") if ip.strip()]
    engines = [engine] if engine in ENGINES else list(ENGINES)
    return {
        "grants": {e: grants(e, database or "<database>", schema) for e in engines},
        "egress_ips": egress,
        "examples": ERP_EXAMPLES,
        "forwarders": FORWARDERS,
        "view_template": {e: (view_sql(e, "<your_stock_table>", {f: {"column": f"<{f}_column>"}
                                                                  for f in ("sku", "warehouse", "current_stock")})
                              if e != "mongodb" else mongo_view_pipeline(
                                  {f: {"column": f"<{f}_field>"} for f in ("sku", "warehouse", "current_stock")}))
                          for e in engines},
    }


def test_transform(field: str, spec: Any, value: Any) -> dict:
    """What one field mapping does to one sample value: the output, or the
    rejection code the run would report."""
    if field not in F.CANONICAL_FIELDS:
        raise AppError("inventory_sync_mapping_invalid", "Unknown field.", status_code=422,
                       params={"reason": "unknown_field", "field": str(field)[:60]})
    spec = {"column": "value", **(spec if isinstance(spec, dict) else {})}
    probe = {"sku": {"column": "__sku"}, "current_stock": {"column": "__qty"}, field: spec}
    norm = F.normalize_mapping(probe)
    try:
        out = mapper._field_value({"value": value, "__sku": "x", "__qty": 0}, field, norm[field])
    except mapper.Rejected as exc:
        return {"ok": False, "code": exc.code, "params": exc.params}
    if out is not None and field in F.BOUNDS:
        lo, hi = F.BOUNDS[field]
        if not (lo <= out <= hi):
            if field == "current_stock" and out < 0:
                return {"ok": False, "code": "negative_quantity", "params": {"field": field, "value": out}}
            return {"ok": False, "code": "out_of_range", "params": {"field": field, "value": out, "min": lo, "max": hi}}
    return {"ok": True, "output": out}
