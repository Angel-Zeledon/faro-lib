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

def erp_examples() -> dict[str, dict[str, str]]:
    """Example read-only queries: the ERP presets rendered with sample
    parameters (client 100, company USMF...). One source of truth with the
    presets, so the client / company filters are never forgotten here."""
    from backend.inventory_sync import presets
    sample = {"sap_client": "100", "code": "USMF", "code_list": "1000", "int_list": "204", "int": "1",
              "decimals": "2", "schema": None, "bc_company": "CRONUS", "guid": None, "prefix": None}
    out: dict[str, dict[str, str]] = {}
    for pid, pr in presets.PRESETS.items():
        params = {sp["name"]: sample[sp["kind"]] for sp in pr["params"]
                  if sp["required"] or sp["kind"] in ("int", "decimals")}
        params = {k: v for k, v in params.items() if v}
        engine = pr["engines"][0]
        body = presets.render(pid, engine, params)["profile"]
        verified = "" if pr.get("verified") else " (unverified against a live system)"
        out[pid] = {"title": f"{pr['product']} - {engine}{verified}", "sql": body["source_query"]}
    return out


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
            proj[f] = "$" + str(col)          # a MongoDB field path, not money
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
        "  -H \"Authorization: Bearer $STOCKAI_API_KEY\" -H 'Content-Type: application/x-ndjson' \\\n"
        "  --data-binary @changes.ndjson"),
}


def recipes(engine: Optional[str], database: Optional[str] = None, schema: Optional[str] = None) -> dict:
    from backend.config import settings
    egress = [ip.strip() for ip in (getattr(settings, "inventory_sync_egress_ips", "") or "").split(",") if ip.strip()]
    engines = [engine] if engine in ENGINES else list(ENGINES)
    return {
        "grants": {e: grants(e, database or "<database>", schema) for e in engines},
        "egress_ips": egress,
        "examples": erp_examples(),
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
