"""Wave 3 contract cases: the inventory hub (stock rows, physical counts,
reception reversals).

Called from `contract_test.run()` as `w3_cases.run_w3(args, fx, db, h)` with
`h` the contract_test module (HTTP client, fixture helpers, diff), so this file
does not import it back.

How the two implementations are compared here, and why it differs from the
other groups: these routes change stock quantities, counts and PO state, so a
request must never run against the same objects on both sides. Instead the
harness builds TWO throwaway tenants through the Python API, seeds both
IDENTICALLY with SQL (same SKUs, warehouses, quantities, costs, POs), sends the
Python API's tenant to Python and the other tenant to Rust, and runs the same
sequence of cases against each. Then, per case:

* status, `error_code`, `error_params` and the whole body must be equal once
  ids, tenant/user ids and timestamps are replaced by placeholders;
* the rows each side ADDED or REMOVED in the tenant's tables (stock rows,
  snapshots, warehouses, counts, lines, adjustments, PO rows, lead-time
  observations and the activity feed) must be equal after the same masking.

A third tenant owns objects of its own; cases aimed at its ids must answer the
same 404 on both sides and its rows must come out untouched.
"""

from __future__ import annotations

import json
import re
import secrets
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import w3_cases_3b as w3b  # noqa: E402

API = "/api/v1"

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TS_RE = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:\+00:00|Z)?")
ACT_RE = re.compile(r"act_[0-9a-f]{12}")
KEY_ACTOR_RE = re.compile(r"api_key:[0-9a-zA-Z_-]+")


# ── The two sides ────────────────────────────────────────────────────────────

@dataclass
class Side:
    name: str                      # "py" | "rs"
    base: str
    fx: Any
    vars: dict = field(default_factory=dict)
    wh: dict = field(default_factory=dict)       # warehouse name -> id

    def subs(self) -> list[tuple[str, str]]:
        pairs = [(self.fx.tenant_id, "<tenant>"), (self.fx.admin_id, "<admin>"),
                 (self.fx.analyst_id, "<analyst>"), (self.fx.viewer_id, "<viewer>")]
        pairs += [(v, f"<{k}>") for k, v in self.vars.items() if isinstance(v, str) and v]
        pairs += [(v, f"<wh:{k}>") for k, v in self.wh.items()]
        return sorted(pairs, key=lambda p: -len(p[0]))


def mask(obj: Any, side: Side) -> Any:
    text = json.dumps(obj, default=str)
    for real, name in side.subs():
        text = text.replace(real, name)
    text = ACT_RE.sub("<act>", text)
    text = KEY_ACTOR_RE.sub("<key-actor>", text)
    text = UUID_RE.sub("<uuid>", text)
    text = TS_RE.sub("<ts>", text)
    return json.loads(text)


def fill(value: Any, side: Side) -> Any:
    """Replace `{var}` tokens in a path or a body with this side's values."""
    if isinstance(value, str):
        for k, v in side.vars.items():
            value = value.replace("{" + k + "}", str(v))
        return value
    if isinstance(value, list):
        return [fill(v, side) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, side) for k, v in value.items()}
    return value


def dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


# ── Seeding (identical on both tenants) ──────────────────────────────────────

STOCK_COLS = ("sku", "warehouse", "display_name", "current_stock", "min_stock", "lead_time_days",
              "unit_cost", "moq", "supplier", "category", "barcode", "unit_of_measure")

# (sku, warehouse, display_name, stock, min, lead, cost, moq, supplier, category, barcode, uom)
BASE_STOCK = [
    ("A1", "principal", "Agua 1L", 100, 10, 15, 2.5, 6, "Acme", "Bebidas", "BC-A1", "unit"),
    ("A1", "Norte", "Agua 1L", 40, 10, 15, 2.5, 6, "Acme", "Bebidas", "BC-A1", "unit"),
    ("A2", "principal", "Galletas", 10, 5, 10, None, 1, "Beta", "Snacks", "BC-A2", None),
    ("B1", "Norte", "Jugo", 0, 3, 20, 10.0, 12, "Acme", "Bebidas", None, None),
    ("M1", "Norte", "Multi", 7, 1, 15, 1.1, 1, None, None, None, None),
    ("M1", "Sur", "Multi", 9, 1, 15, 1.1, 1, None, None, None, None),
    ("DUP1", "principal", "Dup uno", 3, 0, 15, 1.0, 1, None, None, "BC-DUP", None),
    ("DUP2", "principal", "Dup dos", 4, 0, 15, 1.0, 1, None, None, "BC-DUP", None),
    ("case-x", "principal", "Lower", 5, 0, 15, 1.0, 1, None, None, None, None),
    ("CASE-X", "principal", "Upper", 6, 0, 15, 1.0, 1, None, None, None, None),
    ("X 1", "principal", "Con espacio", 2, 0, 15, 3.3, 1, "Beta", "Snacks", None, None),
    ("SUR1", "Sur", "Solo sur", 12, 0, 15, 0.7, 1, "Beta", None, "BC-SUR1", None),
]


def seed_tenant(db, side: Side) -> None:
    cur = db.cursor()
    tid = side.fx.tenant_id
    for i, name in enumerate(("principal", "Norte", "Sur")):
        cur.execute("INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, %s, %s) RETURNING id",
                    (tid, name, name == "principal"))
        side.wh[name] = cur.fetchone()[0]
    rows = list(BASE_STOCK) + [
        (f"P-{i:03d}", "principal" if i % 3 else "Sur", f"Producto {i}", i * 3.0, 1, 7 + i % 5,
         round(1.5 + i * 0.37, 2), 1, "Gamma" if i % 2 else "Delta", "Lote" if i % 4 else "Otros",
         f"BC-P{i:03d}", None)
        for i in range(1, 31)
    ]
    for r in rows:
        cur.execute(f"INSERT INTO inventory_stock (tenant_id, {', '.join(STOCK_COLS)}) "
                    f"VALUES (%s, {', '.join(['%s'] * len(STOCK_COLS))})", (tid, *r))


def seed_po(db, side: Side, number: int, *, lines: list, destination: Optional[str] = "principal",
            received: bool = False, sent: bool = False, paid: bool = False,
            reception_status: Optional[str] = None, observations: list = ()) -> str:
    """One purchase order. `lines`: (sku, warehouse, received_qty, status, supplier)."""
    cur = db.cursor()
    tid = side.fx.tenant_id
    status = reception_status or ("received" if received else "pending")
    cur.execute("""INSERT INTO inventory_po_log
                     (tenant_id, sku_count, total_units, reception_status, received_at, received_by,
                      sent_at, paid_at, paid_by, po_number, destination_warehouse, generated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, date_trunc('day', NOW()) - INTERVAL '10 days' + INTERVAL '7 hours')
                   RETURNING id""",
                (tid, len(lines), sum(float(l[2] or 0) for l in lines), status,
                 "2026-10-01T00:00:00+00" if received else None, side.fx.admin_id if received else None,
                 "2026-09-20T00:00:00+00" if sent else None, "2026-10-02T00:00:00+00" if paid else None,
                 side.fx.admin_id if paid else None, number, destination))
    po_id = cur.fetchone()[0]
    for ln in lines:
        sku, wh, rq, st, supplier = ln[:5]
        final = ln[5] if len(ln) > 5 else (rq or 0)
        cur.execute("""INSERT INTO inventory_po_items
                         (po_log_id, tenant_id, sku, display_name, supplier, signal, recommended_qty,
                          final_qty, unit_cost, status, warehouse, received_qty)
                       VALUES (%s, %s, %s, %s, %s, 'PEDIR_YA', %s, %s, 2.0, %s, %s, %s)""",
                    (po_id, tid, sku, sku, supplier, final, final, st, wh, rq))
    for supplier, days in observations:
        cur.execute("""INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days)
                       VALUES (%s, %s, %s, %s)""", (tid, supplier, po_id, days))
    return po_id


# ── Canonical state, per side ────────────────────────────────────────────────

STATE_QUERIES = {
    "stock": """SELECT sku, warehouse, display_name, current_stock, min_stock, lead_time_days, unit_cost,
                       moq, supplier, notes, product_type, service_level, sale_price, category, brand,
                       unit_of_measure, barcode, family, lead_time_set_by, service_level_set_by,
                       unit_cost_set_by, moq_set_by
                  FROM inventory_stock WHERE tenant_id = %s ORDER BY sku, warehouse""",
    "snapshots": """SELECT sku, warehouse, current_stock FROM inventory_snapshots WHERE tenant_id = %s
                     ORDER BY sku, warehouse, recorded_at, current_stock""",
    "warehouses": "SELECT name, is_default, demand_share FROM warehouses WHERE tenant_id = %s ORDER BY name",
    "counts": """SELECT notes, warehouse, status, scope_category, scope_supplier, created_by,
                        closed_at IS NOT NULL, closed_by, applied_at IS NOT NULL, applied_by,
                        cancelled_at IS NOT NULL, cancelled_by
                   FROM stock_counts WHERE tenant_id = %s ORDER BY created_at, notes""",
    "lines": """SELECT c.notes, l.sku, l.counted_qty, l.system_qty_at_count, l.source, l.scanned_by,
                       l.applied_at IS NOT NULL, l.applied_by, l.applied_from, l.applied_to
                  FROM stock_count_lines l JOIN stock_counts c ON c.id = l.count_id
                 WHERE l.tenant_id = %s ORDER BY c.created_at, c.notes, l.sku""",
    "ops": """SELECT c.notes, o.client_ref FROM stock_count_ops o JOIN stock_counts c ON c.id = o.count_id
               WHERE o.tenant_id = %s ORDER BY c.notes, o.client_ref""",
    "adjustments": """SELECT sku, warehouse, qty_before, qty_after, delta, unit_cost, reason, ref_id, created_by
                        FROM stock_adjustments WHERE tenant_id = %s ORDER BY created_at, sku""",
    "po_log": """SELECT po_number, reception_status, received_at IS NOT NULL, received_by,
                        sent_at IS NOT NULL, paid_at IS NOT NULL, cancelled_at IS NOT NULL
                   FROM inventory_po_log WHERE tenant_id = %s ORDER BY po_number""",
    "po_items": """SELECT l.po_number, i.sku, i.warehouse, i.status, i.received_qty
                     FROM inventory_po_items i JOIN inventory_po_log l ON l.id = i.po_log_id
                    WHERE i.tenant_id = %s ORDER BY l.po_number, i.sku, i.warehouse""",
    "lead_obs": """SELECT l.po_number, o.supplier, o.lead_time_days
                     FROM supplier_lead_time_obs o JOIN inventory_po_log l ON l.id = o.po_log_id
                    WHERE o.tenant_id = %s ORDER BY l.po_number, o.supplier""",
    "activity": """SELECT action, resource, user_id, context, status FROM activity_logs
                    WHERE tenant_id = %s ORDER BY created_at, action""",
}


def capture(db, side: Side) -> dict:
    cur = db.cursor()
    out = {}
    for name, sql in STATE_QUERIES.items():
        cur.execute(sql, (side.fx.tenant_id,))
        out[name] = mask([list(r) for r in cur.fetchall()], side)
    return out


def delta(before: dict, after: dict) -> dict:
    out = {}
    for table in after:
        b = Counter(json.dumps(r, sort_keys=True) for r in before[table])
        a = Counter(json.dumps(r, sort_keys=True) for r in after[table])
        added, removed = sorted((a - b).elements()), sorted((b - a).elements())
        if added or removed:
            out[table] = {"added": added, "removed": removed}
    return out


# ── Cases ────────────────────────────────────────────────────────────────────

@dataclass
class C:
    name: str
    method: str
    path: str
    who: str = "admin"
    body: Any = None
    raw: Optional[bytes] = None
    ctype: Optional[str] = "application/json"
    route: str = ""
    save: dict = field(default_factory=dict)       # var -> dotted path into the response
    prepare: Optional[Callable] = None             # (side) before this side's call
    scope: Optional[list] = None                   # analyst's warehouse names for this case
    vol: set = field(default_factory=set)


def build_cases() -> list[C]:
    cs: list[C] = []
    add = cs.append
    S = f"{API}/inventory/stock"
    K = f"{API}/inventory/stock-counts"

    # ── GET /stock ──────────────────────────────────────────────────────────
    r = "GET /inventory/stock"
    add(C("stock list admin", "GET", S, route=r))
    add(C("stock list viewer", "GET", S, who="viewer", route=r))
    add(C("stock list read key", "GET", S, who="key_read", route=r))
    add(C("stock list no auth", "GET", S, who="none", route=r))
    add(C("stock list scoped Norte", "GET", S, who="analyst", scope=["Norte"], route=r))
    add(C("stock list scoped nothing", "GET", S, who="analyst", scope=[], route=r))

    # ── GET /stock/page ─────────────────────────────────────────────────────
    r = "GET /inventory/stock/page"
    add(C("page default", "GET", f"{S}/page", route=r))
    add(C("page limit offset", "GET", f"{S}/page?limit=5&offset=3", route=r))
    add(C("page offset past the end", "GET", f"{S}/page?offset=500", route=r))
    add(C("page search q", "GET", f"{S}/page?q=agua", route=r))
    add(C("page search q spaces", "GET", f"{S}/page?q=%20%20producto%2012%20%20", route=r))
    add(C("page search q wildcard", "GET", f"{S}/page?q=%25", route=r))
    add(C("page search blank q", "GET", f"{S}/page?q=%20%20", route=r))
    add(C("page search supplier", "GET", f"{S}/page?q=gamma&limit=100", route=r))
    add(C("page warehouse Norte", "GET", f"{S}/page?warehouse=Norte", route=r))
    add(C("page warehouse is exact", "GET", f"{S}/page?warehouse=norte", route=r))
    add(C("page warehouse and q", "GET", f"{S}/page?warehouse=Sur&q=p-0", route=r))
    add(C("page limit 0", "GET", f"{S}/page?limit=0", route=r))
    add(C("page limit 501", "GET", f"{S}/page?limit=501", route=r))
    add(C("page limit text", "GET", f"{S}/page?limit=abc&offset=-1", route=r))
    add(C("page q too long", "GET", f"{S}/page?q={'x' * 101}&warehouse={'y' * 101}", route=r))
    add(C("page viewer", "GET", f"{S}/page", who="viewer", route=r))
    add(C("page no auth", "GET", f"{S}/page", who="none", route=r))
    add(C("page scoped Norte", "GET", f"{S}/page?limit=3", who="analyst", scope=["Norte"], route=r))
    add(C("page scoped offset", "GET", f"{S}/page?limit=2&offset=1", who="analyst", scope=["Norte", "Sur"], route=r))
    add(C("page scoped out of scope warehouse", "GET", f"{S}/page?warehouse=Sur", who="analyst",
          scope=["Norte"], route=r))
    add(C("page scoped own warehouse", "GET", f"{S}/page?warehouse=norte", who="analyst", scope=["Norte"], route=r))

    # ── GET /stock/lookup ───────────────────────────────────────────────────
    r = "GET /inventory/stock/lookup"
    add(C("lookup barcode sums warehouses", "GET", f"{S}/lookup?code=BC-A1", route=r))
    add(C("lookup barcode in warehouse", "GET", f"{S}/lookup?code=BC-A1&warehouse=Norte", route=r))
    add(C("lookup warehouse canonical case", "GET", f"{S}/lookup?code=BC-A1&warehouse=%20norte%20", route=r))
    add(C("lookup unknown warehouse", "GET", f"{S}/lookup?code=BC-A1&warehouse=Nowhere", route=r))
    add(C("lookup blank warehouse is default", "GET", f"{S}/lookup?code=BC-A1&warehouse=%20", route=r))
    add(C("lookup sku", "GET", f"{S}/lookup?code=A2", route=r))
    add(C("lookup sku case-insensitive", "GET", f"{S}/lookup?code=a2", route=r))
    add(C("lookup exact beats case-insensitive", "GET", f"{S}/lookup?code=case-x", route=r))
    add(C("lookup cost from another row", "GET", f"{S}/lookup?code=B1&warehouse=Sur", route=r))
    add(C("lookup no cost anywhere", "GET", f"{S}/lookup?code=BC-A2", route=r))
    add(C("lookup ambiguous barcode", "GET", f"{S}/lookup?code=BC-DUP", route=r))
    add(C("lookup not found", "GET", f"{S}/lookup?code=NOPE", route=r))
    add(C("lookup code with spaces", "GET", f"{S}/lookup?code=%20A2%20", route=r))
    add(C("lookup blank code", "GET", f"{S}/lookup?code=%20", route=r))
    add(C("lookup empty code", "GET", f"{S}/lookup?code=", route=r))
    add(C("lookup missing code", "GET", f"{S}/lookup", route=r))
    add(C("lookup code too long", "GET", f"{S}/lookup?code={'z' * 201}&warehouse={'w' * 101}", route=r))
    add(C("lookup viewer", "GET", f"{S}/lookup?code=A2", who="viewer", route=r))
    add(C("lookup no auth", "GET", f"{S}/lookup?code=A2", who="none", route=r))
    add(C("lookup scoped sums own warehouses", "GET", f"{S}/lookup?code=BC-A1", who="analyst",
          scope=["Norte"], route=r))
    add(C("lookup scoped code held elsewhere", "GET", f"{S}/lookup?code=BC-A2", who="analyst",
          scope=["Norte"], route=r))
    add(C("lookup scoped out of scope warehouse", "GET", f"{S}/lookup?code=BC-A1&warehouse=principal",
          who="analyst", scope=["Norte"], route=r))
    add(C("lookup scoped own warehouse", "GET", f"{S}/lookup?code=BC-A1&warehouse=Norte", who="analyst",
          scope=["Norte"], route=r))

    # ── GET /stock/{sku} ────────────────────────────────────────────────────
    r = "GET /inventory/stock/{sku}"
    add(C("stock get one warehouse", "GET", f"{S}/A2", route=r))
    add(C("stock get single warehouse row", "GET", f"{S}/B1", route=r))
    add(C("stock get with space", "GET", f"{S}/X%201", route=r))
    add(C("stock get not found", "GET", f"{S}/NOPE", route=r))
    add(C("stock get viewer", "GET", f"{S}/A2", who="viewer", route=r))
    add(C("stock get read key", "GET", f"{S}/A2", who="key_read", route=r))
    add(C("stock get no auth", "GET", f"{S}/A2", who="none", route=r))
    add(C("stock get scoped picks own row", "GET", f"{S}/A1", who="analyst", scope=["Norte"], route=r))
    add(C("stock get scoped other warehouse", "GET", f"{S}/A2", who="analyst", scope=["Norte"], route=r))

    # ── PUT /stock/{sku} ────────────────────────────────────────────────────
    r = "PUT /inventory/stock/{sku}"
    add(C("put create minimal", "PUT", f"{S}/W3-NEW1", who="analyst", body={"current_stock": 12}, route=r))
    add(C("put create full", "PUT", f"{S}/W3-NEW2", body={
        "display_name": "Nuevo", "current_stock": 5.5, "min_stock": 2, "lead_time_days": 9,
        "unit_cost": 1.25, "moq": 4, "supplier": "Zeta", "notes": "n", "sale_price": 3.5,
        "category": "Cat", "family": "Fam", "brand": "Br", "unit_of_measure": "kg",
        "barcode": "BC-NEW2", "warehouse": "Norte"}, route=r))
    add(C("put update keeps unsent fields", "PUT", f"{S}/A2", body={"current_stock": 77}, route=r))
    add(C("put update with provenance", "PUT", f"{S}/A2", body={"current_stock": 70, "lead_time_days": 21,
                                                                   "moq": 3, "unit_cost": 2.0}, route=r))
    add(C("put nulls are dropped", "PUT", f"{S}/A2", body={"current_stock": 71, "unit_cost": None,
                                                              "supplier": None, "notes": None}, route=r))
    add(C("put warehouse case canonical", "PUT", f"{S}/W3-NEW3", body={"current_stock": 1, "warehouse": " norte "}, route=r))
    add(C("put new warehouse is created", "PUT", f"{S}/W3-NEW4", body={"current_stock": 3, "warehouse": "Este"}, route=r))
    add(C("put blank warehouse is default", "PUT", f"{S}/W3-NEW5", body={"current_stock": 3, "warehouse": "   "}, route=r))
    add(C("put invisible chars in warehouse", "PUT", f"{S}/W3-NEW6",
          body={"current_stock": 3, "warehouse": "Oe\u200bste"}, route=r))
    add(C("put blank sku", "PUT", f"{S}/%20%20%20", body={"current_stock": 1}, route=r))
    add(C("put sku with slash is not a route", "PUT", f"{S}/a%2Fb", body={"current_stock": 1}, route=r))
    add(C("put viewer denied", "PUT", f"{S}/W3-NEW7", who="viewer", body={"current_stock": 1}, route=r))
    add(C("put no auth", "PUT", f"{S}/W3-NEW7", who="none", body={"current_stock": 1}, route=r))
    add(C("put read key refused", "PUT", f"{S}/W3-NEW7", who="key_read", body={"current_stock": 1}, route=r))
    add(C("put write key", "PUT", f"{S}/W3-KEY1", who="key_write", body={"current_stock": 2}, route=r))
    add(C("put missing stock", "PUT", f"{S}/W3-NEW8", body={}, route=r))
    add(C("put no body", "PUT", f"{S}/W3-NEW8", route=r))
    add(C("put body is a list", "PUT", f"{S}/W3-NEW8", body=[1], route=r))
    add(C("put invalid json", "PUT", f"{S}/W3-NEW8", raw=b"{nope", route=r, vol={"ctx", "loc"}))
    add(C("put stock null", "PUT", f"{S}/W3-NEW8", body={"current_stock": None}, route=r))
    add(C("put negative stock", "PUT", f"{S}/W3-NEW8", body={"current_stock": -1}, route=r))
    add(C("put huge stock", "PUT", f"{S}/W3-NEW8", body={"current_stock": 2e9}, route=r))
    add(C("put stock as text", "PUT", f"{S}/W3-NEW8", body={"current_stock": "abc"}, route=r))
    add(C("put stock as numeric text", "PUT", f"{S}/W3-NUM1", body={"current_stock": "7.5"}, route=r))
    add(C("put lead time bounds", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "lead_time_days": 0}, route=r))
    add(C("put lead time too big", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "lead_time_days": 366}, route=r))
    add(C("put lead time fraction", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "lead_time_days": 15.5}, route=r))
    add(C("put lead time whole float", "PUT", f"{S}/W3-LT1", body={"current_stock": 1, "lead_time_days": 15.0}, route=r))
    add(C("put lead time text", "PUT", f"{S}/W3-LT2", body={"current_stock": 1, "lead_time_days": "12"}, route=r))
    add(C("put lead time bad text", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "lead_time_days": "x"}, route=r))
    add(C("put lead time null", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "lead_time_days": None}, route=r))
    add(C("put moq below 1", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "moq": 0.5}, route=r))
    add(C("put moq huge", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "moq": 2e6}, route=r))
    add(C("put cost negative", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "unit_cost": -0.01,
                                                              "sale_price": 2e9, "min_stock": -1}, route=r))
    add(C("put string field with number", "PUT", f"{S}/W3-NEW8", body={"current_stock": 1, "supplier": 12,
                                                                         "notes": [], "warehouse": 5}, route=r))
    add(C("put scoped out of scope", "PUT", f"{S}/W3-SC1", who="analyst", scope=["Norte"],
          body={"current_stock": 1, "warehouse": "Sur"}, route=r))
    add(C("put scoped default warehouse out of scope", "PUT", f"{S}/W3-SC2", who="analyst", scope=["Norte"],
          body={"current_stock": 1}, route=r))
    add(C("put scoped own warehouse", "PUT", f"{S}/W3-SC3", who="analyst", scope=["Norte"],
          body={"current_stock": 1, "warehouse": "norte"}, route=r))

    # ── PATCH /stock/{sku} ──────────────────────────────────────────────────
    r = "PATCH /inventory/stock/{sku}"
    add(C("patch single warehouse", "PATCH", f"{S}/B1", who="analyst", body={"current_stock": 8, "notes": "x"}, route=r))
    add(C("patch default of several", "PATCH", f"{S}/A1", body={"min_stock": 11}, route=r))
    add(C("patch named warehouse", "PATCH", f"{S}/A1", body={"min_stock": 12, "warehouse": "norte"}, route=r))
    add(C("patch needs a warehouse", "PATCH", f"{S}/M1", body={"min_stock": 1}, route=r))
    add(C("patch warehouse resolves", "PATCH", f"{S}/M1", body={"min_stock": 2, "warehouse": "SUR"}, route=r))
    add(C("patch sku not in that warehouse", "PATCH", f"{S}/B1", body={"min_stock": 1, "warehouse": "Sur"}, route=r))
    add(C("patch not found", "PATCH", f"{S}/NOPE", body={"min_stock": 1}, route=r))
    add(C("patch empty body returns the row", "PATCH", f"{S}/B1", body={}, route=r))
    add(C("patch only warehouse returns the row", "PATCH", f"{S}/A1", body={"warehouse": "Norte"}, route=r))
    add(C("patch nulls are ignored", "PATCH", f"{S}/B1", body={"current_stock": None, "unit_cost": None}, route=r))
    add(C("patch provenance fields", "PATCH", f"{S}/B1", body={"lead_time_days": 33, "moq": 12, "unit_cost": 9.5}, route=r))
    add(C("patch validation", "PATCH", f"{S}/B1", body={"current_stock": -3, "lead_time_days": 0, "moq": 0}, route=r))
    add(C("patch viewer denied", "PATCH", f"{S}/B1", who="viewer", body={"min_stock": 1}, route=r))
    add(C("patch no auth", "PATCH", f"{S}/B1", who="none", body={"min_stock": 1}, route=r))
    add(C("patch no body", "PATCH", f"{S}/B1", route=r))
    add(C("patch scoped hides other warehouses", "PATCH", f"{S}/A2", who="analyst", scope=["Norte"],
          body={"min_stock": 1}, route=r))
    add(C("patch scoped out of scope warehouse", "PATCH", f"{S}/A1", who="analyst", scope=["Norte"],
          body={"min_stock": 1, "warehouse": "principal"}, route=r))
    add(C("patch scoped lands on own row", "PATCH", f"{S}/A1", who="analyst", scope=["Norte"],
          body={"min_stock": 5}, route=r))
    add(C("patch scoped sees several", "PATCH", f"{S}/M1", who="analyst", scope=["Norte", "Sur"],
          body={"min_stock": 5}, route=r))

    # ── DELETE /stock/{sku} ─────────────────────────────────────────────────
    r = "DELETE /inventory/stock/{sku}"
    add(C("delete viewer denied", "DELETE", f"{S}/SUR1", who="viewer", route=r))
    add(C("delete scoped refused", "DELETE", f"{S}/SUR1", who="analyst", scope=["Sur"], route=r))
    add(C("delete all warehouses of a sku", "DELETE", f"{S}/M1", who="analyst", route=r))
    add(C("delete again not found", "DELETE", f"{S}/M1", route=r))
    add(C("delete not found", "DELETE", f"{S}/NOPE", route=r))
    add(C("delete no auth", "DELETE", f"{S}/SUR1", who="none", route=r))
    add(C("delete read key refused", "DELETE", f"{S}/SUR1", who="key_read", route=r))
    add(C("delete write key", "DELETE", f"{S}/SUR1", who="key_write", route=r))

    # ── Stock counts ────────────────────────────────────────────────────────
    r = "POST /inventory/stock-counts"
    add(C("count create Norte", "POST", K, who="analyst", route=r, save={"count1": "data.id"},
          body={"warehouse": "Norte", "notes": "w3-count-1"}))
    add(C("count create canonical name", "POST", K, route=r, save={"count2": "data.id"},
          body={"warehouse": " norte ", "notes": "w3-count-2", "scope_category": "Bebidas",
                "scope_supplier": "Acme"}))
    add(C("count create default warehouse", "POST", K, route=r, save={"count3": "data.id"},
          body={"notes": "w3-count-3", "scope_category": "", "scope_supplier": ""}))
    add(C("count create empty body", "POST", K, route=r, save={"count_default": "data.id"}, body={}))
    add(C("count create unknown warehouse", "POST", K, route=r, body={"warehouse": "Nowhere"}))
    add(C("count create viewer denied", "POST", K, who="viewer", route=r, body={"notes": "w3-denied"}))
    add(C("count create no auth", "POST", K, who="none", route=r, body={}))
    add(C("count create read key refused", "POST", K, who="key_read", route=r, body={}))
    add(C("count create write key", "POST", K, who="key_write", route=r, body={"notes": "w3-key"}))
    add(C("count create no body", "POST", K, route=r))
    add(C("count create list body", "POST", K, route=r, body=[1]))
    add(C("count create invalid json", "POST", K, route=r, raw=b"{nope", vol={"ctx", "loc"}))
    add(C("count create validation", "POST", K, route=r,
          body={"warehouse": "w" * 101, "scope_category": "c" * 101, "scope_supplier": "s" * 201,
                "notes": "n" * 501}))
    add(C("count create wrong types", "POST", K, route=r, body={"warehouse": 5, "notes": []}))
    add(C("count create scoped out of scope", "POST", K, who="analyst", scope=["Sur"], route=r,
          body={"warehouse": "Norte", "notes": "w3-scoped-no"}))
    add(C("count create scoped default out of scope", "POST", K, who="analyst", scope=["Sur"], route=r,
          body={"notes": "w3-scoped-no2"}))
    add(C("count create scoped own", "POST", K, who="analyst", scope=["Sur"], route=r,
          save={"count_sur": "data.id"}, body={"warehouse": "sur", "notes": "w3-scoped-sur"}))

    r = "GET /inventory/stock-counts"
    add(C("count list", "GET", K, route=r))
    add(C("count list limit", "GET", f"{K}?limit=2", route=r))
    add(C("count list status open", "GET", f"{K}?status=open", route=r))
    add(C("count list status invalid", "GET", f"{K}?status=nope&limit=0", route=r))
    add(C("count list limit too big", "GET", f"{K}?limit=201", route=r))
    add(C("count list viewer", "GET", K, who="viewer", route=r))
    add(C("count list no auth", "GET", K, who="none", route=r))
    add(C("count list scoped", "GET", K, who="analyst", scope=["Sur"], route=r))
    add(C("count list scoped limit", "GET", f"{K}?limit=1", who="analyst", scope=["Sur", "Norte"], route=r))

    r = "GET /inventory/stock-counts/{id}"
    add(C("count get empty", "GET", f"{K}/{{count1}}", route=r))
    add(C("count get not found", "GET", f"{K}/nope", route=r))
    add(C("count get foreign tenant", "GET", f"{K}/{{foreign_count}}", route=r))
    add(C("count get viewer", "GET", f"{K}/{{count1}}", who="viewer", route=r))
    add(C("count get no auth", "GET", f"{K}/{{count1}}", who="none", route=r))
    add(C("count get scoped other warehouse", "GET", f"{K}/{{count1}}", who="analyst", scope=["Sur"], route=r))
    add(C("count get scoped own", "GET", f"{K}/{{count_sur}}", who="analyst", scope=["Sur"], route=r))

    r = "PUT /inventory/stock-counts/{id}/lines"
    L = f"{K}/{{count1}}/lines"
    add(C("line add scan", "PUT", L, who="analyst", route=r,
          body={"sku": "A1", "quantity": 5, "mode": "add", "source": "scan", "client_ref": "r1"}))
    add(C("line same client_ref is a no-op", "PUT", L, route=r,
          body={"sku": "A1", "quantity": 5, "mode": "add", "source": "scan", "client_ref": "r1"}))
    add(C("line duplicate client_ref other sku", "PUT", L, route=r,
          body={"sku": "A2", "quantity": 9, "client_ref": "r1"}))
    add(C("line add accumulates", "PUT", L, route=r, body={"sku": "A1", "quantity": 3}))
    add(C("line set replaces", "PUT", L, route=r, body={"sku": "A1", "quantity": 50, "mode": "set"}))
    add(C("line sku with no stock row here", "PUT", L, route=r, body={"sku": "A2", "quantity": 12, "source": "scan"}))
    add(C("line priced sku zero system", "PUT", L, route=r, body={"sku": "B1", "quantity": 2}))
    add(C("line sku in another warehouse only", "PUT", L, route=r, body={"sku": "SUR1", "quantity": 4}))
    add(C("line empty client_ref", "PUT", L, route=r, body={"sku": "M1", "quantity": 1, "client_ref": ""}))
    add(C("line unknown sku", "PUT", L, route=r, body={"sku": "NOPE", "quantity": 1, "client_ref": "r-nope"}))
    add(C("line negative quantity", "PUT", L, route=r, body={"sku": "A1", "quantity": -1}))
    add(C("line quantity too big", "PUT", L, route=r, body={"sku": "A1", "quantity": 1e9 + 1}))
    add(C("line add overflows", "PUT", L, route=r, body={"sku": "A1", "quantity": 1e9, "mode": "add"}))
    add(C("line quantity at the maximum", "PUT", L, route=r, body={"sku": "A1", "quantity": 1e9, "mode": "set"}))
    add(C("line quantity text", "PUT", L, route=r, body={"sku": "A1", "quantity": "2.5", "mode": "set"}))
    add(C("line fractional quantity", "PUT", L, route=r, body={"sku": "M1", "quantity": 2.25}))
    add(C("line bad mode and source", "PUT", L, route=r, body={"sku": "A1", "quantity": 1, "mode": "SET", "source": 3}))
    add(C("line validation", "PUT", L, route=r, body={"sku": "", "client_ref": "c" * 101}))
    add(C("line missing fields", "PUT", L, route=r, body={}))
    add(C("line sku too long", "PUT", L, route=r, body={"sku": "s" * 201, "quantity": 1}))
    add(C("line viewer denied", "PUT", L, who="viewer", route=r, body={"sku": "A1", "quantity": 1}))
    add(C("line no auth", "PUT", L, who="none", route=r, body={"sku": "A1", "quantity": 1}))
    add(C("line read key refused", "PUT", L, who="key_read", route=r, body={"sku": "A1", "quantity": 1}))
    add(C("line not found", "PUT", f"{K}/nope/lines", route=r, body={"sku": "A1", "quantity": 1}))
    add(C("line foreign count", "PUT", f"{K}/{{foreign_count}}/lines", route=r, body={"sku": "A1", "quantity": 1}))
    add(C("line scoped other warehouse", "PUT", L, who="analyst", scope=["Sur"], route=r,
          body={"sku": "A1", "quantity": 1}))
    add(C("line no body", "PUT", L, route=r))

    r = "DELETE /inventory/stock-counts/{id}/lines/{sku}"
    add(C("line delete", "DELETE", f"{K}/{{count1}}/lines/SUR1", route=r))
    add(C("line delete again", "DELETE", f"{K}/{{count1}}/lines/SUR1", route=r))
    add(C("line delete viewer denied", "DELETE", f"{K}/{{count1}}/lines/M1", who="viewer", route=r))
    add(C("line delete no auth", "DELETE", f"{K}/{{count1}}/lines/M1", who="none", route=r))
    add(C("line delete count not found", "DELETE", f"{K}/nope/lines/M1", route=r))
    add(C("line delete scoped other warehouse", "DELETE", f"{K}/{{count1}}/lines/M1", who="analyst",
          scope=["Sur"], route=r))

    r = "POST /inventory/stock-counts/{id}/close"
    add(C("close empty count", "POST", f"{K}/{{count3}}/close", route=r))
    add(C("close viewer denied", "POST", f"{K}/{{count1}}/close", who="viewer", route=r))
    add(C("close scoped other warehouse", "POST", f"{K}/{{count1}}/close", who="analyst", scope=["Sur"], route=r))
    add(C("close", "POST", f"{K}/{{count1}}/close", who="analyst", route=r))
    add(C("close again", "POST", f"{K}/{{count1}}/close", route=r))
    add(C("close not found", "POST", f"{K}/nope/close", route=r))
    add(C("close no auth", "POST", f"{K}/{{count1}}/close", who="none", route=r))
    add(C("line on a closed count", "PUT", L, route="PUT /inventory/stock-counts/{id}/lines",
          body={"sku": "A1", "quantity": 1}))
    add(C("line delete on a closed count", "DELETE", f"{K}/{{count1}}/lines/A1", route=r))

    r = "GET /inventory/stock-counts/{id}/preview"
    add(C("preview", "GET", f"{K}/{{count1}}/preview", route=r))
    add(C("preview viewer", "GET", f"{K}/{{count1}}/preview", who="viewer", route=r))
    add(C("preview not found", "GET", f"{K}/nope/preview", route=r))
    add(C("preview foreign", "GET", f"{K}/{{foreign_count}}/preview", route=r))
    add(C("preview no auth", "GET", f"{K}/{{count1}}/preview", who="none", route=r))
    add(C("preview scoped other warehouse", "GET", f"{K}/{{count1}}/preview", who="analyst", scope=["Sur"], route=r))
    add(C("preview of a count with no lines", "GET", f"{K}/{{count3}}/preview", route=r))
    add(C("preview with scope filters", "GET", f"{K}/{{count2}}/preview", route=r))

    r = "POST /inventory/stock-counts/{id}/apply"
    A = f"{K}/{{count1}}/apply"
    add(C("apply viewer denied", "POST", A, who="viewer", route=r))
    add(C("apply scoped other warehouse", "POST", A, who="analyst", scope=["Sur"], route=r))
    add(C("apply not closed", "POST", f"{K}/{{count2}}/apply", route=r))
    add(C("apply no lines selected", "POST", A, route=r, body={"skus": []}))
    add(C("apply unknown sku in the list", "POST", A, route=r, body={"skus": ["NOPE", "AAA"]}))
    add(C("apply skus wrong type", "POST", A, route=r, body={"skus": [1, "A1"]}))
    add(C("apply skus not a list", "POST", A, route=r, body={"skus": "A1"}))
    add(C("apply body not an object", "POST", A, route=r, body=[1]))
    add(C("apply body not json", "POST", A, route=r, raw=b"skus=A1", ctype="text/plain"))
    add(C("apply invalid json", "POST", A, route=r, raw=b"{nope", vol={"ctx", "loc"}))
    add(C("apply no auth", "POST", A, who="none", route=r))
    add(C("apply read key refused", "POST", A, who="key_read", route=r))
    add(C("apply: stock moved since the scan", "POST", A, who="analyst", route=r,
          prepare=lambda s, db: moved(db, s, "A1", "Norte", +7)))
    add(C("apply again", "POST", A, route=r))
    add(C("apply a cancelled count", "POST", f"{K}/{{count1}}/cancel", route="POST /inventory/stock-counts/{id}/cancel"))

    # A second count: partial apply, clamping at zero, a row created in the count's warehouse.
    r = "POST /inventory/stock-counts/{id}/apply"
    add(C("count4 create", "POST", K, route="POST /inventory/stock-counts", save={"count4": "data.id"},
          body={"warehouse": "Norte", "notes": "w3-count-4"}))
    for sku, qty, mode in (("A1", 0, "set"), ("B1", 6, "set"), ("A2", 3, "add"), ("M1", 2, "set")):
        add(C(f"count4 line {sku}", "PUT", f"{K}/{{count4}}/lines", route="PUT /inventory/stock-counts/{id}/lines",
              body={"sku": sku, "quantity": qty, "mode": mode, "source": "scan"}))
    add(C("count4 close", "POST", f"{K}/{{count4}}/close", route="POST /inventory/stock-counts/{id}/close"))
    add(C("count4 preview", "GET", f"{K}/{{count4}}/preview", route="GET /inventory/stock-counts/{id}/preview",
          prepare=lambda s, db: moved(db, s, "A1", "Norte", -35)))
    add(C("count4 apply some", "POST", f"{K}/{{count4}}/apply", route=r, body={"skus": ["A1", "A2", "B1"]}))
    add(C("count4 cancel after apply", "POST", f"{K}/{{count4}}/cancel", route="POST /inventory/stock-counts/{id}/cancel"))
    add(C("count4 get after apply", "GET", f"{K}/{{count4}}", route="GET /inventory/stock-counts/{id}"))

    # Applying an all-equal count writes nothing but still closes the count.
    add(C("count5 create", "POST", K, route="POST /inventory/stock-counts", save={"count5": "data.id"},
          body={"warehouse": "Sur", "notes": "w3-count-5"}))
    add(C("count5 line equal", "PUT", f"{K}/{{count5}}/lines", route="PUT /inventory/stock-counts/{id}/lines",
          body={"sku": "SUR1", "quantity": 12, "mode": "set"}))
    add(C("count5 close", "POST", f"{K}/{{count5}}/close", route="POST /inventory/stock-counts/{id}/close"))
    add(C("count5 apply unchanged", "POST", f"{K}/{{count5}}/apply", who="analyst", route=r))

    r = "POST /inventory/stock-counts/{id}/cancel"
    add(C("count6 create", "POST", K, route="POST /inventory/stock-counts", save={"count6": "data.id"},
          body={"warehouse": "Sur", "notes": "w3-count-6"}))
    add(C("cancel open", "POST", f"{K}/{{count6}}/cancel", who="analyst", route=r))
    add(C("cancel again", "POST", f"{K}/{{count6}}/cancel", route=r))
    add(C("cancel viewer denied", "POST", f"{K}/{{count2}}/cancel", who="viewer", route=r))
    add(C("cancel scoped other warehouse", "POST", f"{K}/{{count2}}/cancel", who="analyst", scope=["Sur"], route=r))
    add(C("cancel closed", "POST", f"{K}/{{count5}}/cancel", route=r))
    add(C("cancel not found", "POST", f"{K}/nope/cancel", route=r))
    add(C("cancel no auth", "POST", f"{K}/{{count2}}/cancel", who="none", route=r))
    add(C("cancel foreign", "POST", f"{K}/{{foreign_count}}/cancel", route=r))
    add(C("stock after the counts", "GET", S, route="GET /inventory/stock"))

    # ── Reception reversals ─────────────────────────────────────────────────
    P = f"{API}/inventory/po"
    r = "POST /inventory/po/{id}/unreceive"
    add(C("unreceive viewer denied", "POST", f"{P}/{{po_rcv}}/unreceive", who="viewer", route=r))
    add(C("unreceive no auth", "POST", f"{P}/{{po_rcv}}/unreceive", who="none", route=r))
    add(C("unreceive read key refused", "POST", f"{P}/{{po_rcv}}/unreceive", who="key_read", route=r))
    add(C("unreceive not found", "POST", f"{P}/nope/unreceive", route=r))
    add(C("unreceive foreign order", "POST", f"{P}/{{foreign_po}}/unreceive", route=r))
    add(C("unreceive never received", "POST", f"{P}/{{po_pending}}/unreceive", route=r))
    add(C("unreceive scoped out of scope", "POST", f"{P}/{{po_rcv}}/unreceive", who="analyst", scope=["Norte"], route=r))
    add(C("unreceive insufficient stock", "POST", f"{P}/{{po_short}}/unreceive", route=r))
    add(C("unreceive", "POST", f"{P}/{{po_rcv}}/unreceive", who="analyst", route=r))
    add(C("unreceive again", "POST", f"{P}/{{po_rcv}}/unreceive", route=r))
    add(C("unreceive nothing to remove", "POST", f"{P}/{{po_zero}}/unreceive", route=r))
    add(C("unreceive two warehouses and a rejected line", "POST", f"{P}/{{po_multi}}/unreceive", route=r))
    add(C("unreceive with write key", "POST", f"{P}/{{po_key}}/unreceive", who="key_write", route=r))

    r = "POST /inventory/po/{id}/unsend"
    add(C("unsend viewer denied", "POST", f"{P}/{{po_sent}}/unsend", who="viewer", route=r))
    add(C("unsend no auth", "POST", f"{P}/{{po_sent}}/unsend", who="none", route=r))
    add(C("unsend not found", "POST", f"{P}/nope/unsend", route=r))
    add(C("unsend foreign order", "POST", f"{P}/{{foreign_po}}/unsend", route=r))
    add(C("unsend never sent", "POST", f"{P}/{{po_pending}}/unsend", route=r))
    add(C("unsend after reception", "POST", f"{P}/{{po_sent_rcv}}/unsend", route=r))
    add(C("unsend after payment", "POST", f"{P}/{{po_sent_paid}}/unsend", route=r))
    add(C("unsend scoped out of scope", "POST", f"{P}/{{po_sent}}/unsend", who="analyst", scope=["Norte"], route=r))
    add(C("unsend", "POST", f"{P}/{{po_sent}}/unsend", who="analyst", route=r))
    add(C("unsend again", "POST", f"{P}/{{po_sent}}/unsend", route=r))
    add(C("unsend with write key", "POST", f"{P}/{{po_sent_key}}/unsend", who="key_write", route=r))
    cs += w3b.build_cases_3b(C)
    return cs


def moved(db, side: Side, sku: str, warehouse: str, by: float) -> None:
    """Stock moves between the scan and the apply: the same change on each side."""
    db.cursor().execute("""UPDATE inventory_stock SET current_stock = GREATEST(0, current_stock + %s)
                            WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                        (by, side.fx.tenant_id, sku, warehouse))


# ── The runner ───────────────────────────────────────────────────────────────

def seed_pos(db, side: Side) -> None:
    v = side.vars
    v["po_rcv"] = seed_po(db, side, 1001, received=True, sent=True, destination="principal",
                          lines=[("A1", "principal", 30, "approved", "Acme"), ("A2", "principal", 4, "modified", "Beta")],
                          observations=[("Acme", 12.0), ("Beta", 9.0)])
    v["po_pending"] = seed_po(db, side, 1002, destination="principal",
                              lines=[("A1", "principal", 0, "approved", "Acme")])
    v["po_short"] = seed_po(db, side, 1003, received=True, destination="Norte",
                            lines=[("B1", "Norte", 5, "approved", "Acme"), ("A1", "Norte", 10, "approved", "Acme")],
                            observations=[("Acme", 20.0)])
    v["po_zero"] = seed_po(db, side, 1004, received=True, destination="principal",
                           lines=[("A1", "principal", 0, "approved", "Acme")])
    v["po_multi"] = seed_po(db, side, 1005, received=True, destination="principal",
                            lines=[("P-001", "principal", 1, "approved", "Acme"),
                                   ("P-001", "principal", 1.5, "modified", "Acme"),
                                   ("P-003", "Sur", 0.5, "approved", "Acme"),
                                   ("A1", "Norte", 9, "rejected", "Acme"),
                                   ("P-002", "", 2, "approved", None)],
                            observations=[("Acme", 11.5), ("acme", 10.0)])
    v["po_key"] = seed_po(db, side, 1006, received=True, destination="Norte",
                          lines=[("A1", "Norte", 1, "approved", "Acme")])
    v["po_sent"] = seed_po(db, side, 1007, sent=True, destination="principal",
                           lines=[("A1", "principal", None, "approved", "Acme")])
    v["po_sent_rcv"] = seed_po(db, side, 1008, sent=True, received=True, destination="principal",
                               lines=[("A1", "principal", 1, "approved", "Acme")], reception_status="partial")
    v["po_sent_paid"] = seed_po(db, side, 1009, sent=True, paid=True, destination="principal",
                                lines=[("A1", "principal", None, "approved", "Acme")])
    v["po_sent_key"] = seed_po(db, side, 1010, sent=True, destination="Norte",
                               lines=[("A1", "Norte", None, "approved", "Acme")])


def run_w3(args, fx_a, db, h) -> list:
    if db is None:
        print("W3 cases skipped: they need --db (the fixtures are database rows)")
        return []
    secret = fx_a.secret
    fx_b = h.make_fixture(args.python, secret)
    fx_c = h.make_fixture(args.python, secret)
    print(f"W3 tenants: python {fx_a.tenant_id}, rust {fx_b.tenant_id}, foreign {fx_c.tenant_id}")
    sides = {"py": Side("py", args.python, fx_a), "rs": Side("rs", args.rust, fx_b)}
    foreign = Side("fx", args.python, fx_c)
    results = []
    try:
        for s in (*sides.values(), foreign):
            seed_tenant(db, s)
            seed_pos(db, s)
            w3b.seed_pos_3b(db, s, seed_po)
        # A foreign count (its own tenant) both sides are pointed at.
        cur = db.cursor()
        cur.execute("""INSERT INTO stock_counts (tenant_id, warehouse, notes, created_by)
                       VALUES (%s, 'Norte', 'w3-foreign', %s) RETURNING id""", (fx_c.tenant_id, fx_c.admin_id))
        foreign_count = cur.fetchone()[0]
        foreign_before = capture(db, foreign)
        for s in sides.values():
            s.vars["foreign_count"] = foreign_count
            s.vars["foreign_po"] = foreign.vars["po_rcv"]

        def scope_of(side: Side, names: Optional[list]) -> None:
            ids = None if names is None else [side.wh[n] for n in names if n in side.wh]
            db.cursor().execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                                (None if ids is None else json.dumps(ids), side.fx.analyst_id))

        for case in build_cases():
            if args.only and args.only not in case.name:
                continue
            out = {}
            for name, side in sides.items():
                token = h.auth_for(side.fx, case.who)
                if case.who.startswith("key_") and token is None:
                    out[name] = None
                    continue
                scope_of(side, case.scope)
                if case.prepare:
                    case.prepare(side, db)
                before = capture(db, side)
                r = h.http(side.base, case.method, fill(case.path, side), token=token,
                           body=fill(case.body, side), raw_body=case.raw, content_type=case.ctype)
                for var, path in case.save.items():
                    try:
                        side.vars[var] = dig(r.body, path)
                    except (KeyError, IndexError, TypeError):
                        pass
                after = capture(db, side)
                out[name] = (r, delta(before, after), mask(r.body, side))
                scope_of(side, None)
            if out["py"] is None or out["rs"] is None:
                results.append((h.Case(case.name, case.method, case.path, route=case.route), "SKIP",
                                ["no API key in this tenant"]))
                continue
            (rp, dp, bp), (rr, dr, br) = out["py"], out["rs"]
            problems = []
            if rp.status != rr.status:
                problems.append(f"status python={rp.status} rust={rr.status}")
            vol = case.vol
            problems += h.diff(h.normalize(bp, vol), h.normalize(br, vol))
            for hd in ("www-authenticate", "retry-after"):
                if rp.headers.get(hd) != rr.headers.get(hd):
                    problems.append(f"header {hd}: python={rp.headers.get(hd)!r} rust={rr.headers.get(hd)!r}")
            if dp != dr:
                for table in sorted(set(dp) | set(dr)):
                    if dp.get(table) != dr.get(table):
                        problems.append(f"state {table}: python={json.dumps(dp.get(table))[:600]} "
                                        f"rust={json.dumps(dr.get(table))[:600]}")
            if args.dump:
                print(f"\n--- {case.name}\nPY {rp.status} {json.dumps(rp.body)[:1200]}"
                      f"\nRS {rr.status} {json.dumps(rr.body)[:1200]}\nSTATE PY {json.dumps(dp)[:800]}"
                      f"\nSTATE RS {json.dumps(dr)[:800]}")
            results.append((h.Case(case.name, case.method, case.path, route=case.route),
                            "FAIL" if problems else "PASS", problems))
        untouched = capture(db, foreign) == foreign_before
        results.append((h.Case("foreign tenant untouched", "-", "-", route="(tenant scope)"),
                        "PASS" if untouched else "FAIL",
                        [] if untouched else ["another tenant's rows were written"]))
    finally:
        if not args.keep:
            for fx in (fx_b, fx_c):
                h.erase_fixture(args.python, fx)
    return results
