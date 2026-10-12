"""The mapping assistant: which table holds the stock, and which column is what.

Deterministic on purpose — no LLM, no network: the same schema always gets the
same proposal, it works on an installation with no AI key, and every proposal
carries the reasons it was made so the person confirming it can judge it. The
person always confirms; nothing here is applied on its own.

Vocabulary covers English, Spanish and the names the common ERPs actually use:
SAP (MARD.LABST, MATNR, WERKS, LGORT), Oracle EBS (MTL_ONHAND_QUANTITIES,
INVENTORY_ITEM_ID, SUBINVENTORY_CODE, TRANSACTION_QUANTITY), Odoo
(stock_quant, product_id, location_id, quantity), NetSuite (inventorybalance,
quantityonhand), Dynamics (InventSum, ItemId, InventLocationId, PhysicalInvent,
AvailPhysical) and the plain LatAm names (existencia, saldo, almacen, codigo,
articulo, bodega).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from backend.inventory_sync import fields as F

# ── Name normalisation ────────────────────────────────────────────────────────

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokens(name: str) -> list[str]:
    """``InventLocationId`` -> [invent, location, id]; ``cod_articulo`` ->
    [cod, articulo]; accents stripped, lower-cased."""
    text = str(name or "")
    text = text.rsplit(".", 1)[-1] if "." in text else text   # last segment of a dotted path
    text = _CAMEL.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def compact(name: str) -> str:
    return "".join(tokens(name))


# ── Column vocabulary ─────────────────────────────────────────────────────────
# Per field: EXACT compact names (strong evidence) and TOKENS (weaker evidence).
# Exact names are compared after `compact()`, so "Cod_Articulo", "COD ARTICULO"
# and "codArticulo" are the same name.

_EXACT: dict[str, tuple[str, ...]] = {
    "sku": (
        "sku", "codigo", "codigoproducto", "codigoarticulo", "codarticulo", "codart",
        "codproducto", "articulo", "idarticulo", "idproducto", "productcode", "itemcode",
        "itemno", "itemnumber", "partnumber", "noparte", "clave", "claveproducto",
        "referencia", "ref", "matnr", "material", "inventoryitemid", "itemid",
        "productid", "defaultcode", "item", "code", "productno",
    ),
    "warehouse": (
        "warehouse", "bodega", "almacen", "codalmacen", "codigoalmacen", "idalmacen",
        "deposito", "sucursal", "tienda", "sede", "werks", "lgort", "plant",
        "storagelocation", "subinventorycode", "subinventory", "organizationid",
        "locationid", "location", "inventlocationid", "warehouseid", "warehousecode",
        "whse", "wh", "site", "store", "ubicacion", "centro",
    ),
    "current_stock": (
        "currentstock", "stock", "stockactual", "existencia", "existencias", "exist",
        "saldo", "saldoactual", "cantidad", "cantidadactual", "disponible", "onhand",
        "qtyonhand", "quantityonhand", "onhandqty", "quantity", "qty", "labst",
        "transactionquantity", "primarytransactionquantity", "physicalinvent",
        "availphysical", "inventario", "unidades", "stockqty", "available",
        "quantityavailable", "inventoryqty",
    ),
    "min_stock": (
        "minstock", "stockminimo", "minimo", "existenciaminima", "cantidadminima",
        "minqty", "minimumstock", "safetystock", "eisbe", "minbe", "reorderpoint",
        "puntoreorden", "puntopedido", "stockseguridad",
    ),
    "unit_cost": (
        "unitcost", "costo", "costounitario", "costounit", "ultimocosto",
        "costopromedio", "preciocompra", "preciocosto", "cost", "standardcost",
        "averagecost", "avgcost", "verpr", "stprs", "itemcost", "costprice",
    ),
    "sale_price": (
        "saleprice", "precio", "precioventa", "preciopublico", "pvp", "price",
        "sellingprice", "listprice", "unitprice", "lstprice",
    ),
    "supplier": (
        "supplier", "proveedor", "suplidor", "vendor", "vendorid", "vendorname",
        "lifnr", "partnerid", "nombreproveedor", "codproveedor", "supplierid",
    ),
    "lead_time_days": (
        "leadtimedays", "leadtime", "diasentrega", "tiempoentrega", "plazoentrega",
        "plifz", "deliverydays", "diasreposicion", "leadtimeindays",
    ),
    "category": (
        "category", "categoria", "rubro", "linea", "familia", "departamento", "grupo",
        "matkl", "categid", "itemgroup", "itemgroupid", "productcategory",
    ),
    "brand": ("brand", "marca", "fabricante", "manufacturer"),
    "unit_of_measure": (
        "unitofmeasure", "unidad", "unidadmedida", "um", "uom", "meins",
        "primaryuomcode", "uomcode", "presentacion", "medida",
    ),
    "barcode": ("barcode", "codigobarras", "codigodebarras", "ean", "ean13", "ean11", "upc", "gtin"),
    "display_name": (
        "displayname", "nombre", "nombreproducto", "descripcion", "description",
        "productname", "itemname", "maktx", "name", "detalle",
    ),
}

_TOKENS: dict[str, tuple[str, ...]] = {
    "sku": ("sku", "codigo", "cod", "articulo", "item", "product", "material", "matnr", "code"),
    "warehouse": ("warehouse", "bodega", "almacen", "deposito", "sucursal", "location",
                  "werks", "lgort", "subinventory", "whse", "store", "site"),
    "current_stock": ("stock", "existencia", "saldo", "onhand", "qty", "quantity",
                      "cantidad", "disponible", "labst", "physical", "available"),
    "min_stock": ("min", "minimo", "safety", "reorder", "seguridad"),
    "unit_cost": ("cost", "costo"),
    "sale_price": ("price", "precio", "pvp"),
    "supplier": ("supplier", "proveedor", "vendor"),
    "lead_time_days": ("lead", "entrega", "plazo"),
    "category": ("category", "categoria", "familia", "group", "grupo"),
    "brand": ("brand", "marca"),
    "unit_of_measure": ("uom", "unidad", "measure"),
    "barcode": ("barcode", "ean", "upc", "barras"),
    "display_name": ("name", "nombre", "descripcion", "description"),
}

_NUMERIC_TYPES = re.compile(r"(?i)int|num|dec|float|double|real|money|number")
_TEXTY_SKU_FIELDS = ("sku", "warehouse", "barcode")


def _numeric_type(type_name: str) -> Optional[bool]:
    t = str(type_name or "")
    if not t:
        return None
    return bool(_NUMERIC_TYPES.search(t))


def _samples_numeric(samples: list[Any]) -> Optional[bool]:
    vals = [s for s in samples if s not in (None, "")]
    if not vals:
        return None
    ok = 0
    for v in vals:
        try:
            float(str(v).replace(",", "."))
            ok += 1
        except ValueError:
            pass
    return ok == len(vals)


def score_column(field: str, column: dict) -> tuple[float, list[str]]:
    """How likely `column` is `field`, 0..1, with the reasons."""
    name = column.get("name") or ""
    c = compact(name)
    toks = tokens(name)
    reasons: list[str] = []
    score = 0.0
    if c in _EXACT.get(field, ()):
        score = 0.9
        reasons.append("name_exact")
    elif any(t in _TOKENS.get(field, ()) for t in toks):
        score = 0.55
        reasons.append("name_token")
    else:
        return 0.0, []

    numeric_type = _numeric_type(column.get("type", ""))
    numeric_samples = _samples_numeric(column.get("samples") or [])
    wants_number = field in F.NUMBER_FIELDS or field in F.INTEGER_FIELDS
    if wants_number:
        if numeric_type is True or numeric_samples is True:
            score += 0.08
            reasons.append("values_numeric")
        elif numeric_type is False and numeric_samples is False:
            score -= 0.45
            reasons.append("values_not_numeric")
    # "id" columns that are surrogate keys are weak SKU evidence.
    if field == "sku" and c in ("id", "productid", "itemid", "inventoryitemid") :
        score -= 0.1
        reasons.append("surrogate_key")
    return max(0.0, min(1.0, score)), reasons


def confidence_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def suggest_mapping(columns: list[dict]) -> dict:
    """Propose ``{field: {column, confidence, label, reasons}}``. Each column
    is used at most once (greedy, strongest evidence first); fields with no
    candidate are listed in `unmapped`."""
    candidates: list[tuple[float, str, str, list[str]]] = []
    for field in F.CANONICAL_FIELDS:
        for col in columns:
            score, reasons = score_column(field, col)
            if score > 0.2:
                candidates.append((score, field, col["name"], reasons))
    # Field priority breaks ties: a quantity column is a stock before a minimum.
    order = {f: i for i, f in enumerate(F.CANONICAL_FIELDS)}
    candidates.sort(key=lambda c: (-c[0], order[c[1]]))
    taken_cols: set[str] = set()
    mapping: dict[str, dict] = {}
    for score, field, col, reasons in candidates:
        if field in mapping or col in taken_cols:
            continue
        mapping[field] = {"column": col, "confidence": round(score, 2),
                          "label": confidence_label(score), "reasons": reasons}
        taken_cols.add(col)
    missing_required = [f for f in F.REQUIRED_FIELDS if f not in mapping]
    return {
        "mapping": mapping,
        "unmapped": [f for f in F.CANONICAL_FIELDS if f not in mapping],
        "missing_required": missing_required,
    }


# ── Table ranking ─────────────────────────────────────────────────────────────

_TABLE_STRONG = (
    "mard", "mtlonhandquantities", "stockquant", "inventsum", "inventorybalance",
    "existencias", "existencia", "inventario", "inventory", "stock", "stocklevels",
    "onhand", "saldos", "saldosinventario", "itemlocation", "itemwarehouse",
    "articuloalmacen", "productowarehouse", "stockbalance",
)
_TABLE_TOKENS = ("stock", "inventory", "inventario", "existencia", "existencias", "saldo",
                 "saldos", "onhand", "quant", "balance", "almacen", "bodega", "warehouse")
# History / movement / log tables carry quantities too, but they are not the
# current level: summing a ledger is a query a person writes on purpose.
_TABLE_PENALTY = ("log", "hist", "history", "historial", "audit", "mov", "movimiento",
                  "movimientos", "kardex", "trans", "transaction", "transactions", "move",
                  "moves", "backup", "bak", "tmp", "temp", "old")


def score_table(name: str, columns: Optional[list[dict]] = None) -> tuple[float, list[str]]:
    c = compact(name)
    toks = tokens(name)
    score, reasons = 0.0, []
    if c in _TABLE_STRONG:
        score += 0.6
        reasons.append("name_exact")
    elif any(t in _TABLE_TOKENS for t in toks) or any(s in c for s in ("stock", "invent", "existenc")):
        score += 0.35
        reasons.append("name_token")
    if any(t in _TABLE_PENALTY for t in toks):
        score -= 0.3
        reasons.append("looks_like_history")
    if columns:
        proposal = suggest_mapping(columns)
        found = [f for f in F.REQUIRED_FIELDS if f in proposal["mapping"]]
        if len(found) == len(F.REQUIRED_FIELDS):
            score += 0.35
            reasons.append("has_sku_and_quantity")
        elif found:
            score += 0.1
        if "warehouse" in proposal["mapping"]:
            score += 0.05
            reasons.append("has_warehouse")
    return max(0.0, min(1.0, score)), reasons


def rank_tables(tables: list[dict], limit: int = 10) -> list[dict]:
    """The listing from the schema browser, best stock candidates first. Each
    entry keeps its fields and gains `score`, `label` and `reasons`."""
    ranked = []
    for t in tables:
        score, reasons = score_table(t.get("name") or "", t.get("columns"))
        if (t.get("row_estimate") or 0) == 0 and t.get("row_estimate") is not None:
            reasons.append("empty")
            score *= 0.5
        ranked.append({**{k: v for k, v in t.items() if k != "columns"},
                       "score": round(score, 2), "label": confidence_label(score),
                       "reasons": reasons})
    ranked.sort(key=lambda r: (-r["score"], str(r.get("name"))))
    return [r for r in ranked if r["score"] > 0][:limit]
