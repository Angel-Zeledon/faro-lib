"""Guided upload: read a messy file the way a careful person would.

Customers do not hand over a clean three-column CSV. They hand over the report
their ERP printed: a title above the header, a "Total" row at the bottom, a
workbook with five sheets, one column per month, dates written 01/02/2025,
quantities written 1.234,5, product codes that lost their leading zeros. The
rest of the product is strict - ``sku``, ``date`` and ``demand`` are required
and that contract does not loosen - so the work here is GUIDANCE: find out what
the file is, say it back in plain words, fix what has exactly one safe fix, and
ask ONE precise question about whatever is genuinely ambiguous.

Three rules shape every function in this module:

* **Never silently guess.** A reading is decisive (the data rules out the
  alternatives) or it is a question. ``01/02/2025`` with nothing else in the
  column to settle it is a question, not "probably January".
* **Every transformation is a recorded fix.** ``analyze()`` returns the
  effective ``fixes`` list; ``materialize()`` replays exactly that list over the
  untouched original and produces the clean table. Same input + same fixes =
  same output, so a manifest can say what was done to a file and a test can
  prove the result equals the clean equivalent.
* **Clean files get no questions.** A file that already says what it is
  produces no findings that ask for anything, and no fixes.

Pure Python over ``guidance_io`` grids; pandas appears only in ``materialize``,
which is the one function that hands a DataFrame to the rest of the engine.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from statistics import median
from typing import Any, Optional

from forecasting_core.data.guidance_io import Workbook
from forecasting_core.data.guidance_parse import (
    ORDER_DAY_FIRST, ORDER_MONTH_FIRST, _is_blank, analyze_date_column,
    analyze_number_column, code_text, is_float_code, month_number,
    read_date_cell, read_month_only, read_number_cell, variant_key,
)

GUIDANCE_VERSION = 1

#: A product with fewer periods than this gets a simpler forecast (plain words
#: for the "before you train" summary). Not the gate's hard minimum.
SHORT_HISTORY_PERIODS = 8
PROFILE_SAMPLE = 400
HEADER_SEARCH_ROWS = 40

SEV_INFO, SEV_AUTO, SEV_ASK, SEV_BLOCK = "info", "auto", "ask", "block"

CHECKS = ("table", "product", "date", "quantity", "numbers", "history")


# ── Column-name vocabulary ───────────────────────────────────────────────────

def _norm(name: Any) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _tokens(name: Any) -> set:
    return set(_norm(name).split("_")) - {""}


_SKU_STRONG = {"sku", "codigo", "cod", "code", "referencia", "upc", "ean", "barcode", "plu",
               "codbarras"}
_SKU_STRONG_FULL = {"id_producto", "product_id", "item_id", "item_code", "product_code",
                    "cod_producto", "codigo_producto", "id_articulo", "codigo_articulo"}
_SKU_MEDIUM = {"producto", "product", "articulo", "item", "material", "prod", "mercaderia", "ref"}
_NAME = {"nombre", "name", "descripcion", "description", "desc", "detalle"}
_ROWID = {"id", "nro", "num", "numero", "linea", "line", "row", "folio", "factura", "invoice",
          "ticket", "orden", "order", "pedido", "transaccion", "transaction", "recibo", "n"}
_DATE = {"fecha", "date", "dia", "day", "periodo", "period", "timestamp", "datetime", "fch",
         "semana", "week", "mes", "month"}
_UNITS_STRONG = {"cantidad", "qty", "quantity", "unidades", "units", "uds", "und", "demanda",
                 "demand", "vendido", "vendidas", "sold", "volumen", "volume", "pzas", "piezas",
                 "cajas", "unidad"}
_UNITS_WEAK = {"ventas", "venta", "sales", "pedidos", "despachos", "salidas", "consumo"}
_MONEY = {"total", "totales", "importe", "monto", "valor", "revenue", "ingreso", "ingresos",
          "amount", "subtotal", "facturado", "facturacion", "neto", "bruto", "dinero", "value",
          "dolares", "usd"}
_PRICE = {"precio", "price", "pvp", "costo", "cost", "tarifa"}
_STOCK = {"stock", "existencia", "existencias", "inventario", "inventory", "onhand", "disponible",
          "saldo", "saldos", "available"}
_STORE = {"tienda", "store", "sucursal", "local", "pdv", "bodega", "almacen", "warehouse",
          "canal", "punto"}
_NOT_DEMAND = {"lead", "leadtime", "descuento", "discount", "promo", "promocion", "margen",
               "peso", "weight", "iva", "impuesto", "tax"}

TOTAL_RE = re.compile(
    r"^\s*(?:gran\s+|grand\s+)?(?:sub\s*)?total(?:es)?(?:\s+general)?(?:\s+del?\s+(?:mes|periodo))?"
    r"\s*[:.]?\s*$|^\s*(?:suma|sum|resumen)\s*[:.]?\s*$", re.I)

_SHEET_GOOD = {"ventas", "venta", "sales", "historico", "data", "datos", "detalle", "movimientos",
               "demanda", "demand", "transacciones", "hoja1", "sheet1"}
_SHEET_BAD = {"resumen", "summary", "instrucciones", "readme", "portada", "config", "catalogo",
              "maestro", "inventario", "stock", "notas", "notes", "graficos", "dashboard",
              "pivot", "tabla", "dinamica", "cover"}


def classify_name(name: Any) -> set:
    """Roles a column NAME suggests. A hint, never a verdict."""
    n, toks = _norm(name), _tokens(name)
    out: set = set()
    if n in _SKU_STRONG_FULL or toks & _SKU_STRONG:
        out.add("sku_strong")
    if toks & _SKU_MEDIUM:
        out.add("sku_medium")
    if toks & _NAME:
        out.add("name")
    if toks & _ROWID and "sku_strong" not in out:
        out.add("rowid")
    if toks & _DATE:
        out.add("date")
    if toks & _UNITS_STRONG:
        out.add("units_strong")
    if toks & _UNITS_WEAK:
        out.add("units_weak")
    if toks & _MONEY and "units_strong" not in out:
        out.add("money")
    if toks & _PRICE:
        out.add("price")
    if toks & _STOCK:
        out.add("stock")
    if toks & _STORE:
        out.add("store")
    if toks & _NOT_DEMAND:
        out.add("not_demand")
    return out


# ── Tables ───────────────────────────────────────────────────────────────────

class Table:
    """A header plus rows of raw cells."""

    def __init__(self, header: list[str], rows: list[list]):
        self.header = header
        self.rows = rows

    def column(self, name: str) -> list:
        i = self.header.index(name)
        return [r[i] for r in self.rows]

    def __len__(self) -> int:
        return len(self.rows)


def _unique_header(cells: list) -> list[str]:
    out: list[str] = []
    seen: Counter = Counter()
    for i, c in enumerate(cells):
        if hasattr(c, "isoformat") and not isinstance(c, str):
            name = c.isoformat()[:10]
        elif _is_blank(c):
            name = f"Column {i + 1}"
        else:
            name = str(c).strip()
        seen[name] += 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


def _nonblank(row: list) -> list:
    return [c for c in row if not _is_blank(c)]


def _is_numberish(c: Any) -> bool:
    if isinstance(c, bool):
        return False
    if isinstance(c, (int, float)):
        return True
    if isinstance(c, str):
        v, fl = read_number_cell(c)
        return v is not None or "ambiguous" in fl
    return False


def _is_datish(c: Any) -> bool:
    d, fl = read_date_cell(c)
    return d is not None or "ambiguous" in fl


def detect_header_row(grid: list[list]) -> Optional[int]:
    """Index of the header row, or None when no row looks like one.

    The header is the first row that is mostly distinct text/date labels, nearly
    as wide as the data below it, and followed by rows that hold other kinds of
    values (numbers, dates). A title row ("Ventas 2025", one filled cell) is too
    narrow; a data row is mostly numbers.
    """
    limit = min(len(grid), HEADER_SEARCH_ROWS)
    for i in range(limit):
        row = grid[i]
        cells = _nonblank(row)
        below = grid[i + 1:i + 9]
        if len(cells) < 2 or not below:
            continue
        width = max((len(_nonblank(r)) for r in below), default=0)
        if len(cells) < max(2, 0.6 * width):
            continue
        labels = [c for c in cells if (isinstance(c, str) and not _is_numberish(c))
                  or hasattr(c, "isoformat")]
        if len(labels) < 0.8 * len(cells):
            continue
        keys = [str(c).strip().lower() for c in cells]
        if len(set(keys)) < 0.9 * len(keys):
            continue
        return i
    return None


def _drop_blank_rows(rows: list[list]) -> list[list]:
    return [r for r in rows if any(not _is_blank(c) for c in r)]


def _is_total_row(row: list) -> bool:
    for c in row:
        if _is_blank(c):
            continue
        return isinstance(c, str) and bool(TOTAL_RE.match(c))
    return False


def _split_footer(rows: list[list]) -> tuple[list[list], list[list], list[list]]:
    """(data rows, total rows, note rows). Totals anywhere; notes only trailing."""
    width = max((len(r) for r in rows), default=0)
    totals = [r for r in rows if _is_total_row(r)]
    kept = [r for r in rows if not _is_total_row(r)]
    notes: list[list] = []
    if width >= 3:
        while kept and len(_nonblank(kept[-1])) <= 1:
            notes.append(kept.pop())
    return kept, totals, notes


def _build_table(grid: list[list], plan: dict) -> Table:
    """Apply the structural part of a plan to a raw grid. Deterministic."""
    h = plan.get("header_row", 0)
    header = _unique_header(grid[h])
    rows = _drop_blank_rows(grid[h + 1:])
    if plan.get("ignore_totals"):
        rows, _, _ = _split_footer(rows)
    table = Table(header, [list(r) for r in rows])
    drop = set(plan.get("drop_columns") or [])
    if drop:
        keep = [i for i, n in enumerate(table.header) if n not in drop]
        table = Table([table.header[i] for i in keep],
                      [[r[i] for i in keep] for r in table.rows])
    un = plan.get("unpivot")
    if un:
        table = _unpivot(table, un)
    return table


def _unpivot(table: Table, spec: dict) -> Table:
    id_cols = spec["id_columns"]
    value_cols = spec["value_columns"]
    header_dates = spec["header_dates"]                 # {column: iso date}
    date_name, demand_name = spec["date_name"], spec["demand_name"]
    id_idx = [table.header.index(c) for c in id_cols]
    val_idx = [(table.header.index(c), date.fromisoformat(header_dates[c])) for c in value_cols]
    out = []
    for r in table.rows:
        ids = [r[i] for i in id_idx]
        for i, d in val_idx:
            v = r[i]
            if _is_blank(v):
                continue
            out.append(ids + [d, v])
    return Table(id_cols + [date_name, demand_name], out)


# ── Finding construction ─────────────────────────────────────────────────────

def _finding(code: str, severity: str, *, check: str, column: Optional[str] = None,
             confidence: float = 1.0, params: Optional[dict] = None,
             options: Optional[list] = None, examples: Optional[list] = None,
             fixes: Optional[list] = None, handled_by: Optional[str] = None) -> dict:
    f = {"code": code, "severity": severity, "check": check, "column": column,
         "confidence": round(float(confidence), 2), "params": params or {}}
    if options:
        f["options"] = options
    if examples:
        f["examples"] = examples
    if fixes:
        f["fixes"] = fixes
    if handled_by:
        f["handled_by"] = handled_by
    return f


def _opt(oid: str, decision: dict, **params: Any) -> dict:
    return {"id": oid, "decision": decision, "params": params}


# ── Sheets ───────────────────────────────────────────────────────────────────

def _quick_table(grid: list[list]) -> Optional[Table]:
    h = detect_header_row(grid)
    if h is None:
        return None
    rows = _drop_blank_rows(grid[h + 1:h + 1 + 300])
    rows, _, _ = _split_footer(rows)
    return Table(_unique_header(grid[h]), rows)


def _wide_headers(header: list[str], raw_header: Optional[list] = None) -> list[int]:
    cells = raw_header if raw_header is not None else header
    return [i for i, c in enumerate(cells) if _is_datish(c)
            or read_month_only(c if isinstance(c, str) else None)]


def score_sheet(name: str, grid: list[list]) -> dict:
    """How much a sheet looks like a sales history. Higher is better; >=5 usable."""
    toks = _tokens(name)
    score = 0.0
    if toks & _SHEET_GOOD:
        score += 1.0
    if toks & _SHEET_BAD:
        score -= 2.0
    rows = len(grid)
    if rows < 3:
        return {"name": name, "score": -5.0, "rows": rows, "columns": 0}
    h = detect_header_row(grid)
    cols = len(grid[0]) if grid else 0
    if h is None:
        return {"name": name, "score": round(score - 1, 2), "rows": rows, "columns": cols}
    table = _quick_table(grid)
    n = len(table.rows)
    score += min(n / 50, 1.5)
    wide = [i for i in _wide_headers(table.header, grid[h]) if i < len(table.header)]
    date_found = len(wide) >= 3
    numeric = text_rep = 0
    for i, col in enumerate(table.header):
        cells = [r[i] for r in table.rows[:PROFILE_SAMPLE]]
        nb = [c for c in cells if not _is_blank(c)]
        if not nb:
            continue
        dates = sum(1 for c in nb if _is_datish(c)) / len(nb)
        nums = sum(1 for c in nb if _is_numberish(c)) / len(nb)
        if dates >= 0.8 and i not in wide:
            date_found = True
        elif nums >= 0.9 and dates < 0.5:
            numeric += 1
        elif nums < 0.5 and len(set(map(str, nb))) < 0.9 * len(nb):
            text_rep += 1
    if date_found:
        score += 3
    if numeric:
        score += 2
    if text_rep or (len(wide) >= 3):
        score += 1.5
    return {"name": name, "score": round(score, 2), "rows": rows, "columns": cols}


# ── Column profiling & role choice ───────────────────────────────────────────

def _sample(cells: list, n: int = PROFILE_SAMPLE) -> list:
    if len(cells) <= n:
        return cells
    step = len(cells) / n
    return [cells[int(i * step)] for i in range(n)]


def _profile(name: str, cells: list) -> dict:
    s = _sample(cells)
    nb = [c for c in s if not _is_blank(c)]
    d = analyze_date_column(s)
    nm = analyze_number_column(s)
    texts = [c for c in nb if isinstance(c, str)]
    n = len(nb)
    uniq = len(set(map(str, nb)))
    return {
        "name": name, "hints": classify_name(name), "n": n,
        "fill": n / len(s) if s else 0.0,
        "date_rate": d["rate"] if n else 0.0, "serial_candidate": d["serial_candidate"],
        "num_rate": nm["rate"] if n else 0.0, "all_integers": nm["all_integers"],
        "currency_share": nm["currency_share"], "percent_share": nm["percent_share"],
        "unique_ratio": uniq / n if n else 0.0, "nunique": uniq,
        "text_share": len(texts) / n if n else 0.0,
        "avg_len": (sum(len(str(c)) for c in nb) / n) if n else 0.0,
        "space_share": (sum(1 for c in texts if " " in c.strip()) / n) if n else 0.0,
        "digit_share": (sum(1 for c in texts if any(ch.isdigit() for ch in c)) / n) if n else 0.0,
    }


def _is_date_like(p: dict) -> bool:
    return p["date_rate"] >= 0.8 or (p["serial_candidate"] and "date" in p["hints"])


def _is_numeric(p: dict) -> bool:
    """Numeric enough to be a quantity. A column NAMED like units may carry a few
    stray words ("n/a", "-"): those are the data gate's to resolve, not a reason
    to say the file has no quantity."""
    if _is_date_like(p):
        return False
    return p["num_rate"] >= 0.9 or (
        p["num_rate"] >= 0.5 and bool(p["hints"] & {"units_strong", "units_weak"}))


def _find_product_relation(cols: dict[str, list[float]]) -> Optional[tuple]:
    """(factor_a, factor_b, product) when product ~= a * b on nearly every row."""
    names = list(cols)
    if len(names) < 3:
        return None
    n = min(len(v) for v in cols.values())
    n = min(n, 3000)
    for c in names:
        for a in names:
            for b in names:
                if len({a, b, c}) < 3 or names.index(a) > names.index(b):
                    continue
                ok = tot = 0
                for i in range(n):
                    x, y, z = cols[a][i], cols[b][i], cols[c][i]
                    if x is None or y is None or z is None:
                        continue
                    tot += 1
                    if abs(x * y - z) <= 0.02 * max(abs(z), 1e-9) + 0.01:
                        ok += 1
                if tot >= 8 and ok >= 0.9 * tot and max(cols[c][:n] or [0]) > 0:
                    return (a, b, c)
    return None


def _samples_of(table: Table, col: str, k: int = 3) -> list:
    out = []
    for c in table.column(col):
        if not _is_blank(c):
            out.append(c.isoformat()[:10] if hasattr(c, "isoformat") else
                       (int(c) if isinstance(c, float) and c.is_integer() else c))
        if len(out) >= k:
            break
    return out


def _distinct_samples(table: Table, col: str, k: int = 3) -> list:
    seen, out = set(), []
    for c in table.column(col):
        if _is_blank(c):
            continue
        t = code_text(c)
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= k:
            break
    return out


# ── The analysis ─────────────────────────────────────────────────────────────

def analyze(book: Workbook, decisions: Optional[dict] = None, *,
            today: Optional[date] = None, mapping: Optional[dict] = None) -> dict:
    """Read a workbook and report what it is, what was fixed and what to ask.

    ``decisions`` are the user's answers so far (see the keys below); calling
    again with more answers walks the conversation forward. ``mapping`` is a
    user-chosen {sku,date,demand,store} which, when given, replaces the detected
    roles (and is then merely CHECKED, never overridden).

    Decision keys: ``sheet``, ``declined`` (fix codes to leave undone),
    ``date_order`` {col: day_first|month_first}, ``number_decimal`` {col: ','|'.'},
    ``sku_column`` / ``date_column`` / ``demand_column`` / ``store_column``,
    ``units_confirmed``, ``leading_zeros`` {col: bool}, ``merge_variants``
    {col: bool}, ``cumulative`` {col: bool}, ``ignore_unreadable`` (bool),
    ``wide_year`` (int).
    """
    decisions = dict(decisions or {})
    today = today or date.today()
    declined = set(decisions.get("declined") or [])
    findings: list[dict] = []
    fixes: list[dict] = []
    report: dict[str, Any] = {
        "version": GUIDANCE_VERSION, "findings": findings, "fixes": fixes,
        "shape": "long", "mapping": {}, "columns": [], "summary": None,
        "sheet": None, "preview": None,
    }

    # 1. Sheet ─────────────────────────────────────────────────────────────
    names = [n for n in book.sheet_names if book.sheets[n]]
    if not names:
        findings.append(_finding("file_empty", SEV_BLOCK, check="table"))
        return _finish(report)
    ranked = sorted((score_sheet(n, book.sheets[n]) for n in names),
                    key=lambda s: (-s["score"], names.index(s["name"])))
    chosen = decisions.get("sheet") if decisions.get("sheet") in book.sheets else None
    if chosen is None:
        chosen = ranked[0]["name"]
        usable = [s for s in ranked if s["score"] >= 5]
        if len(usable) >= 2 and usable[0]["score"] - usable[1]["score"] < 1.0:
            findings.append(_finding(
                "sheet_choice", SEV_ASK, check="table", confidence=0.5,
                params={"count": len(usable)},
                options=[_opt(s["name"], {"sheet": s["name"]}, sheet=s["name"],
                              rows=s["rows"], columns=s["columns"]) for s in usable[:4]]))
            report["sheet"] = {"selected": chosen, "ranked": ranked}
            return _finish(report)
    report["sheet"] = {"selected": chosen, "ranked": ranked, "first": names[0]}
    if len(names) > 1:
        findings.append(_finding(
            "sheet_auto_selected", SEV_INFO, check="table", confidence=0.9,
            params={"sheet": chosen, "others": [n for n in names if n != chosen]}))
    if chosen != names[0]:
        fixes.append({"code": "use_sheet", "params": {"sheet": chosen}})
    grid = book.sheets[chosen]

    # 2. Header, totals, notes ────────────────────────────────────────────
    h = detect_header_row(grid)
    if h is None:
        findings.append(_finding("header_not_found", SEV_BLOCK, check="table",
                                 params={"first_row": [str(c) for c in _nonblank(grid[0])][:6]}))
        return _finish(report)
    plan: dict[str, Any] = {"header_row": h}
    if h > 0 and "header_row" not in declined:
        skipped = [" ".join(str(c) for c in _nonblank(r)) for r in grid[:h]
                   if _nonblank(r)][:3]
        fixes.append({"code": "header_row", "params": {"row": h}})
        findings.append(_finding("header_row_offset", SEV_AUTO, check="table", confidence=0.95,
                                 params={"rows_above": h, "skipped": skipped},
                                 fixes=[{"code": "header_row", "params": {"row": h}}]))
    elif h > 0:
        plan["header_row"] = 0
    if "header_row" in declined:
        plan["header_row"] = 0
    raw_header = grid[plan["header_row"]]
    body = _drop_blank_rows(grid[plan["header_row"] + 1:])
    _, totals, notes = _split_footer(body)
    if (totals or notes) and "ignore_totals_row" not in declined:
        plan["ignore_totals"] = True
        fixes.append({"code": "ignore_totals_row", "params": {}})
        findings.append(_finding(
            "totals_row", SEV_AUTO, check="table", confidence=0.95,
            params={"totals": len(totals), "notes": len(notes)},
            examples=[" ".join(str(c) for c in _nonblank(r))[:60] for r in (totals + notes)[:2]],
            fixes=[{"code": "ignore_totals_row", "params": {}}]))

    table = _build_table(grid, plan)
    if len(table) == 0:
        findings.append(_finding("file_empty", SEV_BLOCK, check="table"))
        return _finish(report)

    # 3. Wide format ──────────────────────────────────────────────────────
    # Header cells can be real dates while the unique-ified name is a string.
    hdr_cells = _header_cells_for(table, raw_header)
    wide_idx = [i for i, c in enumerate(hdr_cells)
                if _is_datish(c) or read_month_only(c if isinstance(c, str) else None)]
    if len(wide_idx) >= 3 and "unpivot" not in declined:
        table, ok = _handle_wide(table, hdr_cells, wide_idx, decisions, plan, fixes,
                                 findings, today)
        report["shape"] = "wide"
        if not ok:
            return _finish(report)

    # 4. Roles ────────────────────────────────────────────────────────────
    profiles = {n: _profile(n, table.column(n)) for n in table.header}
    roles = _choose_roles(table, profiles, decisions, findings, mapping)
    report["mapping"] = {k: v for k, v in roles.items() if v}
    if report["shape"] == "long" and not roles.get("date"):
        _stock_or_missing(table, profiles, findings)
    if any(f["severity"] == SEV_BLOCK for f in findings):
        report["columns"] = _column_verdicts(table, profiles, roles, findings)
        return _finish(report, table, roles)

    # 5. Slot analyses ────────────────────────────────────────────────────
    parsed = _analyze_slots(table, roles, decisions, declined, findings, fixes, today)
    report["columns"] = _column_verdicts(table, profiles, roles, findings)

    # 6. Summary ──────────────────────────────────────────────────────────
    if parsed and parsed.get("dates") is not None and not any(
            f["severity"] in (SEV_ASK, SEV_BLOCK) and f["check"] in ("date", "product")
            for f in findings):
        report["summary"] = _summary(parsed, findings)
    report["preview"] = _preview(table, roles, parsed)
    return _finish(report, table, roles)


def _header_cells_for(table: Table, raw_header: list) -> list:
    """Raw header cells aligned with ``table.header`` (before de-duplication).

    ``_build_table`` can drop columns; this recovers the original cell objects
    for the surviving ones so a real Excel date header is still a date.
    """
    raw_names = _unique_header(raw_header)
    lookup = dict(zip(raw_names, raw_header))
    return [lookup.get(n, n) for n in table.header]


def _handle_wide(table: Table, hdr_cells: list, wide_idx: list[int], decisions: dict,
                 plan: dict, fixes: list, findings: list, today: date):
    """Turn one-column-per-period files into long format, or ask what is missing."""
    labels = [hdr_cells[i] for i in wide_idx]
    month_only = [read_month_only(c if isinstance(c, str) else None) for c in labels]
    header_dates: dict[str, str] = {}
    if all(month_only):
        year = decisions.get("wide_year")
        if not year:
            findings.append(_finding(
                "wide_year_missing", SEV_ASK, check="table", confidence=0.6,
                params={"months": [str(c) for c in labels[:3]]},
                options=[_opt(str(y), {"wide_year": y}, year=y)
                         for y in (today.year, today.year - 1, today.year - 2)]))
            return table, False
        for i, m in zip(wide_idx, month_only):
            header_dates[table.header[i]] = date(int(year), m, 1).isoformat()
        d_info = {"order": None, "order_source": None}
    else:
        da = analyze_date_column(labels, order=(decisions.get("date_order") or {}).get("__headers__"))
        if da["order_conflict"] or da["unresolved"]:
            findings.append(_finding(
                "date_order_ambiguous", SEV_ASK, check="date", column="__headers__",
                confidence=0.5, params={"where": "headers", "reason":
                                        "mixed" if da["order_conflict"] else "ambiguous"},
                examples=da["ambiguous_examples"],
                options=[_opt(ORDER_DAY_FIRST, {"date_order": {"__headers__": ORDER_DAY_FIRST}}),
                         _opt(ORDER_MONTH_FIRST, {"date_order": {"__headers__": ORDER_MONTH_FIRST}})]))
            return table, False
        if da["n_unparsed"] > 0:
            wide_idx = [i for i, d in zip(wide_idx, da["dates"]) if d is not None]
            labels = [hdr_cells[i] for i in wide_idx]
            da = analyze_date_column(labels, order=da["order"])
        for i, d in zip(wide_idx, da["dates"]):
            header_dates[table.header[i]] = d.isoformat()
        d_info = da
    value_cols = [table.header[i] for i in wide_idx]
    # Total / average columns are not periods: drop them, say so.
    summary_cols = [n for i, n in enumerate(table.header)
                    if i not in wide_idx and TOTAL_RE.match(n or "")]
    id_cols = [n for i, n in enumerate(table.header)
               if i not in wide_idx and n not in summary_cols]
    if not id_cols:
        findings.append(_finding("sku_missing", SEV_BLOCK, check="product"))
        return table, False
    date_name = "date" if "date" not in id_cols else "date_value"
    demand_name = "demand" if "demand" not in id_cols else "demand_value"
    if summary_cols:
        plan["drop_columns"] = summary_cols
        fixes.append({"code": "drop_summary_columns", "params": {"columns": summary_cols}})
    spec = {"id_columns": id_cols, "value_columns": value_cols, "header_dates": header_dates,
            "date_name": date_name, "demand_name": demand_name}
    fixes.append({"code": "unpivot", "params": spec})
    findings.append(_finding(
        "wide_format", SEV_AUTO, check="table", confidence=0.9,
        params={"periods": len(value_cols), "first": min(header_dates.values()),
                "last": max(header_dates.values()), "id_columns": id_cols,
                "dropped": summary_cols},
        fixes=[{"code": "unpivot", "params": {"periods": len(value_cols)}}]))
    if d_info.get("order_source") in ("data", "sequence"):
        findings.append(_finding("date_order_inferred", SEV_INFO, check="date",
                                 column="__headers__", confidence=0.95,
                                 params={"order": d_info["order"]}))
    plan2 = dict(plan)
    plan2["drop_columns"] = summary_cols
    t2 = _unpivot(Table(table.header, table.rows), spec)
    return t2, True


def _stock_or_missing(table: Table, profiles: dict, findings: list) -> None:
    stockish = [n for n, p in profiles.items() if "stock" in p["hints"] and _is_numeric(p)]
    if stockish:
        findings.append(_finding("stock_snapshot", SEV_BLOCK, check="table", confidence=0.9,
                                 params={"columns": stockish[:3]},
                                 fixes=[{"code": "use_stock_import", "params": {}}]))
    elif not any(f["code"] in ("date_missing", "date_column_choice") for f in findings):
        findings.append(_finding("date_missing", SEV_BLOCK, check="date",
                                 params={"columns": table.header[:8]}))


# ── Roles ────────────────────────────────────────────────────────────────────

def _choice(slot: str, reason: str, cands: list[str], table: Table, recommended: Optional[str],
            decision_key: str, check: str, extra: Optional[dict] = None) -> dict:
    options = []
    for c in cands[:5]:
        options.append(_opt(c, {decision_key: c}, column=c, slot=slot,
                            samples=_distinct_samples(table, c) if slot == "sku"
                            else _samples_of(table, c), recommended=(c == recommended)))
    return _finding(f"{slot}_column_choice", SEV_ASK, check=check, confidence=0.5,
                    params={"slot": slot, "reason": reason, **(extra or {})}, options=options)


def _choose_roles(table: Table, profiles: dict, decisions: dict, findings: list,
                  user_mapping: Optional[dict]) -> dict:
    roles: dict[str, Optional[str]] = {"sku": None, "date": None, "demand": None, "store": None}
    cols = table.header
    if user_mapping:
        for k in roles:
            v = user_mapping.get(k)
            roles[k] = v if v in cols else None
        return roles

    def valid(key: str) -> Optional[str]:
        v = decisions.get(key)
        return v if v in cols else None

    # DATE ────────────────────────────────────────────────────────────────
    if valid("date_column"):
        roles["date"] = valid("date_column")
    else:
        cands = [n for n, p in profiles.items() if _is_date_like(p)]
        def dscore(n):
            hints = profiles[n]["hints"]
            nm = _norm(n)
            return (3 if nm in ("fecha", "date") else 2 if "date" in hints else 0,
                    1 if re.search(r"venta|sale|pedido|order", nm) else 0,
                    profiles[n]["date_rate"])
        cands.sort(key=dscore, reverse=True)
        if cands:
            roles["date"] = cands[0]
            if len(cands) > 1 and dscore(cands[0])[:2] == dscore(cands[1])[:2]:
                findings.append(_choice("date", "tie", cands, table, cands[0],
                                        "date_column", "date"))
        # name-based guess that disagrees with the content (swapped columns)
    # DEMAND (after date, before sku) ─────────────────────────────────────
    taken = {roles["date"]}
    num = [n for n, p in profiles.items() if n not in taken and _is_numeric(p)]
    num_ok = [n for n in num if profiles[n]["percent_share"] < 0.5]
    for n in num:
        if profiles[n]["percent_share"] >= 0.5:
            findings.append(_finding("demand_percent", SEV_INFO, check="quantity", column=n,
                                     params={"column": n}))

    date_hints = profiles[roles["date"]]["hints"] if roles["date"] else set()
    swapped = bool(date_hints & {"units_strong", "units_weak"}) and "date" not in date_hints

    def klass(n: str) -> str:
        h, p = profiles[n]["hints"], profiles[n]
        if swapped and "date" in h:
            return "units_strong"                  # the headers are swapped; trust the content
        if "units_strong" in h:
            return "units_strong"
        if "money" in h or p["currency_share"] >= 0.5:
            return "money"
        if "units_weak" in h:
            return "units_weak"
        if h & {"price", "stock", "not_demand", "rowid", "store", "sku_strong", "sku_medium"}:
            return "other"
        return "generic"

    sku_pre = valid("sku_column")
    if valid("demand_column") or valid("units_confirmed"):
        roles["demand"] = valid("demand_column") or valid("units_confirmed")
    elif decisions.get("demand_column") == "__none__":
        findings.append(_finding("demand_missing_units", SEV_BLOCK, check="quantity"))
    else:
        pool = [n for n in num_ok if n != sku_pre]
        by = defaultdict(list)
        for n in pool:
            by[klass(n)].append(n)
        money = by["money"]
        for tier, reason_tier in (("units_strong", "tie"), ("units_weak", "tie")):
            if by[tier]:
                if len(by[tier]) == 1:
                    roles["demand"] = by[tier][0]
                else:
                    roles["demand"] = by[tier][0]
                    findings.append(_choice("demand", reason_tier, by[tier], table,
                                            by[tier][0], "demand_column", "quantity"))
                break
        else:
            gen = by["generic"]
            rel_cols = {n: profiles_values(table, n) for n in (gen + money)[:6]}
            if len(gen) == 1:
                roles["demand"] = gen[0]
                reason = "units_or_money" if money else "no_hint"
                findings.append(_choice("demand", reason, gen + money, table, gen[0],
                                        "demand_column", "quantity"))
            elif len(gen) > 1:
                relation = _find_product_relation(rel_cols) if len(rel_cols) >= 3 else None
                roles["demand"] = gen[0]
                reason = "units_or_money" if relation else "tie"
                findings.append(_choice("demand", reason, gen + money, table, gen[0],
                                        "demand_column", "quantity"))
            elif money:
                roles["demand"] = money[0]
                findings.append(_choice("demand", "units_or_money", money + by["other"], table,
                                        None, "units_confirmed", "quantity",
                                        extra={"only_money": True}))
            elif by["other"]:
                roles["demand"] = None
                findings.append(_choice("demand", "no_hint", by["other"], table, None,
                                        "demand_column", "quantity"))
            else:
                findings.append(_finding("demand_missing", SEV_BLOCK, check="quantity",
                                         params={"columns": cols[:8]}))
        # Money-named chosen demand next to another candidate is a units/money question.
        if (roles["demand"] and roles["demand"] in money and not by["units_strong"]
                and not any(f["code"] == "demand_column_choice" for f in findings)):
            findings.append(_choice("demand", "units_or_money", money + by["generic"], table,
                                    None, "units_confirmed", "quantity"))
        # Relation test even when names look fine: demand == a * b.
        if roles["demand"] and not any(f["code"] == "demand_column_choice" for f in findings):
            rel_cols = {n: profiles_values(table, n) for n in num_ok[:6]}
            relation = _find_product_relation(rel_cols) if len(rel_cols) >= 3 else None
            if relation and relation[2] == roles["demand"]:
                factors = [c for c in relation[:2]]
                factors.sort(key=lambda c: 0 if profiles[c]["all_integers"] else 1)
                findings.append(_choice("demand", "units_or_money", [roles["demand"]] + factors,
                                        table, factors[0], "demand_column", "quantity",
                                        extra={"relation": list(relation)}))

    if swapped and roles["demand"] and "date" in profiles[roles["demand"]]["hints"]:
        findings.append(_finding(
            "columns_swapped", SEV_INFO, check="quantity", confidence=0.95,
            params={"date_column": roles["date"], "demand_column": roles["demand"]}))

    # SKU ─────────────────────────────────────────────────────────────────
    if valid("sku_column"):
        roles["sku"] = valid("sku_column")
    else:
        used = {roles["date"], roles["demand"]}
        cands = []
        for n, p in profiles.items():
            if n in used or _is_date_like(p) or p["n"] == 0:
                continue
            if p["num_rate"] >= 0.9 and not p["all_integers"] and "sku_strong" not in p["hints"]:
                continue                                   # a measurement, not a code
            if p["num_rate"] >= 0.9 and p["all_integers"] and not (
                    p["hints"] & {"sku_strong", "sku_medium"}) and p["unique_ratio"] > 0.6:
                continue
            if p["hints"] & {"price", "stock", "not_demand", "money", "units_strong",
                             "units_weak", "store"}:
                continue
            cands.append(n)

        def sscore(n: str) -> float:
            p = profiles[n]
            h = p["hints"]
            s = 3.0 if "sku_strong" in h else 2.0 if "sku_medium" in h else 1.0 if "name" in h else 0.0
            repeated = p["unique_ratio"] <= 0.9
            s += 1.0 if repeated else 0.0
            if "rowid" in h:
                s -= 3.0
            if p["unique_ratio"] > 0.97 and p["n"] >= 20 and "sku_strong" not in h:
                s -= 2.0
            if p["text_share"] > 0.5:
                s += 0.5
            return s

        cands.sort(key=sscore, reverse=True)
        if not cands:
            findings.append(_finding("sku_missing", SEV_BLOCK, check="product",
                                     params={"columns": cols[:8]}))
        else:
            best = cands[0]
            roles["sku"] = best
            second = cands[1] if len(cands) > 1 else None
            reason = None
            pb = profiles[best]
            if second and sscore(best) - sscore(second) < 0.5:
                ps = profiles[second]
                code_like = lambda p: p["space_share"] < 0.3 and p["avg_len"] < 16
                name_like = lambda p: p["space_share"] >= 0.3 or p["avg_len"] >= 16
                reason = "name_vs_code" if (
                    (code_like(pb) and name_like(ps)) or (name_like(pb) and code_like(ps))
                ) and abs(pb["nunique"] - ps["nunique"]) <= 0.15 * max(pb["nunique"], 1) \
                    else "tie"
            elif sscore(best) < 2.0 and not pb["hints"] & {"sku_strong", "sku_medium"}:
                reason = "no_hint"
            elif pb["unique_ratio"] > 0.97 and pb["n"] >= 20 and "sku_strong" not in pb["hints"]:
                reason = "row_id"
            if reason:
                # Code-like first: it is the usual right answer.
                ordered = sorted(cands[:4], key=lambda c: (
                    profiles[c]["space_share"] >= 0.3, -sscore(c)))
                findings.append(_choice("sku", reason, ordered, table, ordered[0],
                                        "sku_column", "product"))
    # STORE (optional, for duplicate checking) ────────────────────────────
    if valid("store_column"):
        roles["store"] = valid("store_column")
    else:
        for n, p in profiles.items():
            if "store" in p["hints"] and p["num_rate"] < 0.9 and n not in roles.values() \
                    and p["unique_ratio"] < 0.5:
                roles["store"] = n
                break
    return roles


def profiles_values(table: Table, col: str) -> list:
    return analyze_number_column(table.column(col))["values"]


# ── Slot analyses ────────────────────────────────────────────────────────────

def _needs_text_read(cells: list) -> bool:
    """True when some text cell is a real number that plain ``float()`` cannot read
    ("1,5", "$12", "(3)"). Stray words are not numbers and are the gate's business."""
    for c in cells:
        if isinstance(c, str) and c.strip():
            try:
                float(c.strip())
            except ValueError:
                v, fl = read_number_cell(c)
                if v is not None or "ambiguous" in fl:
                    return True
    return False


def _analyze_slots(table: Table, roles: dict, decisions: dict, declined: set,
                   findings: list, fixes: list, today: date) -> Optional[dict]:
    out: dict[str, Any] = {}
    n_rows = len(table)

    # ── DATE ───────────────────────────────────────────────────────────────
    dcol = roles.get("date")
    dates = None
    if dcol:
        cells = table.column(dcol)
        order = (decisions.get("date_order") or {}).get(dcol)
        da = analyze_date_column(cells, order=order, today=today)
        if da["order_conflict"]:
            findings.append(_finding(
                "date_order_ambiguous", SEV_ASK, check="date", column=dcol, confidence=0.5,
                params={"reason": "mixed", "day_first_rows": da["proof_day_first"],
                        "month_first_rows": da["proof_month_first"]},
                options=[_opt(ORDER_DAY_FIRST, {"date_order": {dcol: ORDER_DAY_FIRST}}),
                         _opt(ORDER_MONTH_FIRST, {"date_order": {dcol: ORDER_MONTH_FIRST}})]))
        elif da["unresolved"]:
            findings.append(_finding(
                "date_order_ambiguous", SEV_ASK, check="date", column=dcol, confidence=0.5,
                params={"reason": "ambiguous", "rows": da["ambiguous"]},
                examples=da["ambiguous_examples"],
                options=[_opt(ORDER_DAY_FIRST, {"date_order": {dcol: ORDER_DAY_FIRST}}),
                         _opt(ORDER_MONTH_FIRST, {"date_order": {dcol: ORDER_MONTH_FIRST}})]))
        else:
            dates = da["dates"]
            unreadable = da["n_unparsed"]
            if da["rate"] < 0.5:
                findings.append(_finding("date_unreadable", SEV_BLOCK, check="date",
                                         column=dcol, params={"examples": da["unparsed_examples"]}))
                dates = None
            elif unreadable:
                if decisions.get("ignore_unreadable") is True:
                    pass
                elif decisions.get("ignore_unreadable") is False:
                    findings.append(_finding("date_unreadable", SEV_BLOCK, check="date",
                                             column=dcol,
                                             params={"examples": da["unparsed_examples"]}))
                    dates = None
                else:
                    findings.append(_finding(
                        "date_rows_unreadable", SEV_ASK, check="date", column=dcol,
                        confidence=0.7,
                        params={"rows": unreadable, "total": da["n_nonblank"]},
                        examples=da["unparsed_examples"],
                        options=[_opt("ignore", {"ignore_unreadable": True}),
                                 _opt("stop", {"ignore_unreadable": False})]))
                    dates = None
            if dates is not None:
                shapes = set(da["shapes"])
                extras = da["two_digit_year"] or da["has_tz"] or da["has_time"]
                plain = not extras and (shapes <= {"iso", "obj"} or (
                    shapes == {"dmy"} and da["order_source"] == "data"))
                need_fix = (not plain) or da["order_source"] in ("user", "sequence")
                if shapes <= {"obj"}:
                    need_fix = False
                if decisions.get("ignore_unreadable") is True and unreadable:
                    need_fix = True
                if need_fix:
                    fixes.append({"code": "read_dates", "params": {
                        "column": dcol, "order": da["order"], "serial": da["serial"]}})
                    if decisions.get("ignore_unreadable") is True and unreadable:
                        fixes.append({"code": "ignore_unreadable_rows",
                                      "params": {"columns": [dcol]}})
                if da["order_source"] == "sequence":
                    findings.append(_finding("date_order_inferred", SEV_INFO, check="date",
                                             column=dcol, confidence=0.9,
                                             params={"order": da["order"]}))
                if da["serial"]:
                    findings.append(_finding("date_excel_serial", SEV_AUTO, check="date",
                                             column=dcol, params={"column": dcol}))
                if da["mixed_formats"]:
                    findings.append(_finding("date_mixed_formats", SEV_INFO, check="date",
                                             column=dcol, params={"shapes": sorted(da["shapes"])}))
                if da["text_months"]:
                    findings.append(_finding("date_text_months", SEV_INFO, check="date",
                                             column=dcol))
                if da["two_digit_year"]:
                    findings.append(_finding("date_two_digit_year", SEV_INFO, check="date",
                                             column=dcol))
                if da["has_tz"]:
                    findings.append(_finding("date_timezone", SEV_INFO, check="date",
                                             column=dcol))
                if da["future"]:
                    findings.append(_finding("dates_in_future", SEV_INFO, check="date",
                                             column=dcol, params={"rows": da["future"]},
                                             handled_by="data_gate"))
    out["dates"] = dates

    # ── DEMAND ─────────────────────────────────────────────────────────────
    mcol = roles.get("demand")
    values = None
    if mcol:
        cells = table.column(mcol)
        dec = (decisions.get("number_decimal") or {}).get(mcol)
        na = analyze_number_column(cells, decimal=dec)
        if na["format_conflict"] or na["unresolved"]:
            findings.append(_finding(
                "number_format_ambiguous", SEV_ASK, check="numbers", column=mcol,
                confidence=0.5,
                params={"reason": "mixed" if na["format_conflict"] else "ambiguous",
                        "rows": na["ambiguous"]},
                examples=na["ambiguous_examples"],
                options=[_opt(",", {"number_decimal": {mcol: ","}}),
                         _opt(".", {"number_decimal": {mcol: "."}})]))
        else:
            values = na["values"]
            if na["rate"] < 0.5:
                findings.append(_finding("demand_not_numeric", SEV_BLOCK, check="quantity",
                                         column=mcol,
                                         params={"examples": na["unparsed_examples"]}))
                values = None
            else:
                needs = _needs_text_read(cells) or na["decimal"] == ","
                if any(isinstance(c, str) for c in cells) and (
                        needs or na["currency_share"] or na["percent_share"]):
                    fixes.append({"code": "read_numbers",
                                  "params": {"column": mcol, "decimal": na["decimal"]}})
                    if na["decimal_source"] in ("data", "user") and na["decimal"] == ",":
                        findings.append(_finding("number_decimal_comma", SEV_AUTO, check="numbers",
                                                 column=mcol, params={"source": na["decimal_source"]}))
                if na["n_unparsed"]:
                    findings.append(_finding(
                        "demand_text_values", SEV_INFO, check="numbers", column=mcol,
                        params={"rows": na["n_unparsed"], "examples": na["unparsed_examples"]},
                        handled_by="data_gate"))
                if na["currency_share"] >= 0.3:
                    findings.append(_finding("demand_currency", SEV_INFO, check="numbers",
                                             column=mcol, params={"share": round(na["currency_share"], 2)}))
                if na["negative_share"] > 0:
                    findings.append(_finding("demand_negatives", SEV_INFO, check="numbers",
                                             column=mcol, handled_by="data_gate",
                                             params={"share": round(na["negative_share"], 3),
                                                     "min": na["min"]}))
                if na["constant"] or (na["max"] is not None and na["max"] == 0):
                    findings.append(_finding("demand_constant", SEV_BLOCK, check="quantity",
                                             column=mcol,
                                             params={"value": na["max"], "zero": na["max"] == 0}))
                    values = None
    out["values"] = values

    # ── SKU ────────────────────────────────────────────────────────────────
    scol = roles.get("sku")
    skus: Optional[list] = None
    if scol:
        cells = table.column(scol)
        skus = _analyze_sku(scol, cells, decisions, declined, findings, fixes)
    out["skus"] = skus

    # Store (as a series key).
    out["stores"] = table.column(roles["store"]) if roles.get("store") else None

    # ── Cumulative, duplicates ─────────────────────────────────────────────
    if dates is not None and values is not None and skus is not None:
        if decisions.get("cumulative", {}).get(mcol) is True:
            fixes.append({"code": "undo_cumulative",
                          "params": {"column": mcol, "sku": scol, "date": dcol,
                                     "store": roles.get("store")}})
            values = _undo_cumulative(skus, dates, values, out["stores"])
            out["values"] = values
        elif mcol not in (decisions.get("cumulative") or {}) and \
                _looks_cumulative(skus, dates, values, out["stores"]):
            findings.append(_finding(
                "demand_cumulative", SEV_ASK, check="quantity", column=mcol, confidence=0.8,
                options=[_opt("undo", {"cumulative": {mcol: True}}),
                         _opt("keep", {"cumulative": {mcol: False}})]))
        conflicts = _conflicting_duplicates(skus, dates, values, out["stores"])
        if conflicts:
            findings.append(_finding("conflicting_duplicates", SEV_INFO, check="numbers",
                                     params={"groups": conflicts}, handled_by="data_gate"))
    return out


def _analyze_sku(col: str, cells: list, decisions: dict, declined: set,
                 findings: list, fixes: list) -> list:
    nb = [c for c in cells if not _is_blank(c)]
    texts = [c for c in nb if isinstance(c, str)]
    # Float codes: 123.0 -> 123.
    if nb and all(is_float_code(c) for c in nb) and "codes_as_text" not in declined:
        fixes.append({"code": "codes_as_text", "params": {"column": col}})
        findings.append(_finding("sku_float_codes", SEV_AUTO, check="product", column=col,
                                 examples=[str(c) for c in nb[:2]]))
    # Leading zeros present in text, in a column pandas would read as numbers.
    digits = [c for c in texts if c.strip().isdigit()]
    zeros = [c for c in digits if c.strip().startswith("0") and len(c.strip()) > 1]
    if zeros and len(digits) == len(nb) and "keep_leading_zeros" not in declined \
            and (decisions.get("leading_zeros") or {}).get(col) is not False:
        width = max(len(c.strip()) for c in digits)
        fixes.append({"code": "keep_leading_zeros", "params": {"column": col, "width": 0}})
        findings.append(_finding("sku_leading_zeros", SEV_AUTO, check="product", column=col,
                                 examples=[c.strip() for c in zeros[:2]],
                                 params={"width": width}))
    # Zeros already lost: whole numbers of mixed width where most share one width.
    nums = [c for c in nb if isinstance(c, (int, float)) and not isinstance(c, bool)
            and float(c).is_integer()]
    if nums and len(nums) == len(nb):
        lens = Counter(len(str(int(c))) for c in nums)
        w = max(lens)
        short = sum(k for l, k in lens.items() if l < w)
        if (w >= 4 and lens[w] >= 0.6 * len(nums) and 0 < short <= 0.4 * len(nums)
                and all(w - l <= 2 for l in lens)):
            lz = (decisions.get("leading_zeros") or {}).get(col)
            if lz is True:
                fixes.append({"code": "keep_leading_zeros", "params": {"column": col, "width": w}})
            elif lz is None:
                examples = [str(int(c)) for c in nums if len(str(int(c))) < w][:2]
                findings.append(_finding(
                    "sku_leading_zeros_lost", SEV_ASK, check="product", column=col,
                    confidence=0.6, params={"width": w},
                    examples=[{"raw": e, "padded": e.zfill(w)} for e in examples],
                    options=[_opt("pad", {"leading_zeros": {col: True}}, width=w),
                             _opt("keep", {"leading_zeros": {col: False}})]))
    # Spelling variants of one code.
    texts_all = [code_text(c) for c in nb]
    groups: dict[str, Counter] = defaultdict(Counter)
    for t, c in zip(texts_all, nb):
        groups[variant_key(t)][t if not isinstance(c, str) else re.sub(r"\s+", " ", c.strip())] += 1
    raw_forms = Counter()
    for c in nb:
        if isinstance(c, str):
            raw_forms[variant_key(c)] += 0
    ws_variants = case_variants = 0
    ex_ws, ex_case = [], []
    for key, forms in groups.items():
        if len(forms) > 1:
            case_variants += 1
            if len(ex_case) < 2:
                ex_case.append(sorted(forms))
    for c in nb:
        if isinstance(c, str) and (c != c.strip() or "  " in c):
            ws_variants += 1
            if len(ex_ws) < 2:
                ex_ws.append(c)
    if ws_variants and "normalize_codes" not in declined:
        merge_case = bool((decisions.get("merge_variants") or {}).get(col)) and case_variants > 0
        fixes.append({"code": "normalize_codes",
                      "params": {"column": col, "merge_case": merge_case}})
        findings.append(_finding("sku_whitespace", SEV_AUTO, check="product", column=col,
                                 params={"rows": ws_variants}, examples=ex_ws))
    elif case_variants and (decisions.get("merge_variants") or {}).get(col) is True:
        fixes.append({"code": "normalize_codes", "params": {"column": col, "merge_case": True}})
    if case_variants and col not in (decisions.get("merge_variants") or {}):
        findings.append(_finding(
            "sku_variants", SEV_ASK, check="product", column=col, confidence=0.7,
            params={"groups": case_variants}, examples=ex_case,
            options=[_opt("merge", {"merge_variants": {col: True}}),
                     _opt("keep", {"merge_variants": {col: False}})]))
    return _apply_sku_fixes(cells, [f for f in fixes if f["params"].get("column") == col])


def _apply_sku_fixes(cells: list, fixes: list) -> list:
    out = [code_text(c) if not _is_blank(c) else None for c in cells]
    for f in fixes:
        p = f["params"]
        if f["code"] == "codes_as_text":
            out = [str(int(float(t))) if t and is_float_code(t) else t for t in out]
        elif f["code"] == "keep_leading_zeros" and p.get("width"):
            out = [t.zfill(p["width"]) if t and t.isdigit() else t for t in out]
        elif f["code"] == "normalize_codes":
            out = [re.sub(r"\s+", " ", t.strip()) if t else t for t in out]
            if p.get("merge_case"):
                rep: dict[str, Counter] = defaultdict(Counter)
                for t in out:
                    if t:
                        rep[variant_key(t)][t] += 1
                out = [rep[variant_key(t)].most_common(1)[0][0] if t else t for t in out]
    return out


def _series_key(i: int, skus: list, stores: Optional[list]) -> tuple:
    return (skus[i], stores[i] if stores else None)


def _looks_cumulative(skus: list, dates: list, values: list, stores: Optional[list]) -> bool:
    """A running total (month-to-date, year-to-date) rather than per-period demand.

    Within a series the values only go up, apart from resets back to a small
    number, and over a stretch of at least four points they end at least three
    times where they began. Per-period demand with a trend does not do that.
    """
    groups: dict[tuple, list] = defaultdict(list)
    for i, (s, d, v) in enumerate(zip(skus, dates, values)):
        if s and d and v is not None:
            groups[_series_key(i, skus, stores)].append((d, v))
    eligible = flagged = 0
    for pts in groups.values():
        if len(pts) < 6:
            continue
        eligible += 1
        pts.sort(key=lambda x: x[0])
        vs = [v for _, v in pts]
        segments, cur = [], [vs[0]]
        valid = True
        for prev, v in zip(vs, vs[1:]):
            if v < prev:
                if v > 0.5 * prev:
                    valid = False
                    break
                segments.append(cur)
                cur = [v]
            else:
                cur.append(v)
        segments.append(cur)
        if not valid:
            continue
        growing = [sg for sg in segments if len(sg) >= 4
                   and sg[-1] >= 3 * max(sg[0], 1e-9) and sg[-1] > sg[0]]
        if growing and sum(len(sg) for sg in growing) >= 0.6 * len(vs):
            flagged += 1
    return eligible >= 1 and flagged >= 0.6 * eligible


def _undo_cumulative(skus: list, dates: list, values: list, stores: Optional[list]) -> list:
    order = defaultdict(list)
    for i, (s, d, v) in enumerate(zip(skus, dates, values)):
        if s and d and v is not None:
            order[_series_key(i, skus, stores)].append(i)
    out = list(values)
    for idxs in order.values():
        idxs.sort(key=lambda i: (dates[i], i))
        prev = None
        for i in idxs:
            v = values[i]
            out[i] = v if (prev is None or v < prev) else v - prev
            prev = v
    return out


def _conflicting_duplicates(skus: list, dates: list, values: list,
                            stores: Optional[list]) -> int:
    seen: dict[tuple, set] = defaultdict(set)
    count: Counter = Counter()
    for i, (s, d, v) in enumerate(zip(skus, dates, values)):
        if s and d and v is not None:
            k = (s, d, stores[i] if stores else None)
            seen[k].add(v)
            count[k] += 1
    return sum(1 for k, c in count.items() if c > 1 and len(seen[k]) > 1)


# ── Summary, preview, verdicts ───────────────────────────────────────────────

def _granularity(dates: list) -> Optional[dict]:
    uniq = sorted({d for d in dates if d})
    if len(uniq) < 3:
        return None
    steps = [(b - a).days for a, b in zip(uniq, uniq[1:])]
    m = median(steps)
    if m <= 1.5:
        label, step = "daily", 1
    elif 5 <= m <= 9:
        label, step = "weekly", 7
    elif 25 <= m <= 35:
        label, step = "monthly", 30
    else:
        label, step = "irregular", int(m)
    return {"label": label, "step_days": step, "median_step": m}


def _summary(parsed: dict, findings: list) -> Optional[dict]:
    dates, skus, values = parsed.get("dates"), parsed.get("skus"), parsed.get("values")
    if dates is None or skus is None:
        return None
    gran = _granularity(dates)
    rows = [(s, d) for s, d in zip(skus, dates) if s and d]
    if not rows:
        return None
    per: dict[str, set] = defaultdict(set)
    for s, d in rows:
        per[s].add(d)
    all_dates = sorted({d for _, d in rows})
    span = (all_dates[-1] - all_dates[0]).days + 1
    step = gran["step_days"] if gran and gran["label"] != "irregular" else 1
    periods = len(all_dates) if gran and gran["label"] != "irregular" else span
    short = sum(1 for ds in per.values() if len(ds) < SHORT_HISTORY_PERIODS)
    if gran:
        findings.append(_finding("granularity", SEV_INFO, check="history",
                                 params={"label": gran["label"], "step_days": gran["step_days"]}))
    return {
        "rows": len(rows), "products": len(per), "periods": periods,
        "date_min": all_dates[0].isoformat(), "date_max": all_dates[-1].isoformat(),
        "history_days": span, "granularity": gran["label"] if gran else None,
        "step_days": step, "short_products": short,
        "short_threshold": SHORT_HISTORY_PERIODS,
        "history_ok": periods >= SHORT_HISTORY_PERIODS,
    }


def _display(cell: Any) -> Any:
    if hasattr(cell, "isoformat") and not isinstance(cell, str):
        return cell.isoformat()[:10]
    if isinstance(cell, float) and cell.is_integer():
        return int(cell)
    return cell


def _preview(table: Table, roles: dict, parsed: Optional[dict], rows: int = 6) -> dict:
    parsed = parsed or {}
    sel = [(slot, roles[slot]) for slot in ("sku", "date", "demand") if roles.get(slot)]
    out_rows = []
    for i, r in enumerate(table.rows[:rows]):
        read: dict[str, Any] = {}
        if parsed.get("skus") is not None and roles.get("sku"):
            read["sku"] = parsed["skus"][i]
        if parsed.get("dates") is not None and roles.get("date"):
            d = parsed["dates"][i]
            read["date"] = d.isoformat() if d else None
        if parsed.get("values") is not None and roles.get("demand"):
            read["demand"] = parsed["values"][i]
        out_rows.append({"cells": [_display(c) for c in r], "read": read})
    return {"columns": list(table.header), "rows": out_rows,
            "slots": {slot: col for slot, col in sel}, "total_rows": len(table)}


_COLUMN_FINDING_CHECK = {"sku": "product", "date": "date", "demand": "quantity"}


def _column_verdicts(table: Table, profiles: dict, roles: dict, findings: list) -> list:
    out = []
    for slot in ("sku", "date", "demand"):
        col = roles.get(slot)
        if not col:
            out.append({"slot": slot, "column": None, "status": "bad",
                        "reason": f"{slot}_not_found"})
            continue
        chk = _COLUMN_FINDING_CHECK[slot]
        bad = [f for f in findings if f["check"] == chk and f["severity"] == SEV_BLOCK]
        ask = [f for f in findings if f["check"] in (chk, "numbers" if slot == "demand" else chk)
               and f["severity"] == SEV_ASK]
        status = "bad" if bad else "check" if ask else "ok"
        out.append({"slot": slot, "column": col, "status": status,
                    "reason": (bad or ask or [{"code": None}])[0]["code"],
                    "samples": _samples_of(table, col)})
    return out


def check_mapping(book: Workbook, mapping: dict, decisions: Optional[dict] = None,
                  today: Optional[date] = None) -> dict:
    """Judge a mapping the USER chose (advanced view): same report, no overriding."""
    return analyze(book, decisions, today=today, mapping=mapping)


def _finish(report: dict, table: Optional[Table] = None, roles: Optional[dict] = None) -> dict:
    findings = report["findings"]
    blocks = [f for f in findings if f["severity"] == SEV_BLOCK]
    asks = [f for f in findings if f["severity"] == SEV_ASK]
    report["questions"] = asks
    report["next_question"] = asks[0] if asks and not blocks else None
    report["verdict"] = "unusable" if blocks else "ask" if asks else "ready"
    report["needs_apply"] = bool(report["fixes"])
    status = {c: "ok" for c in CHECKS}
    for f in findings:
        c = f["check"]
        if c not in status:
            continue
        if f["severity"] == SEV_BLOCK:
            status[c] = "block"
        elif f["severity"] == SEV_ASK and status[c] != "block":
            status[c] = "ask"
    summary = report.get("summary")
    if summary is None:
        status["history"] = "pending"
    elif not summary["history_ok"]:
        status["history"] = "check"
    report["checks"] = [{"id": c, "status": status[c]} for c in CHECKS]
    report["checks_done"] = sum(1 for c in CHECKS if status[c] in ("ok", "check"))
    report["checks_total"] = len(CHECKS)
    return report


# ── Materialization ──────────────────────────────────────────────────────────

def _plan_from_fixes(fixes: list[dict]) -> dict:
    plan: dict[str, Any] = {"header_row": 0}
    for f in fixes:
        c, p = f["code"], f["params"]
        if c == "header_row":
            plan["header_row"] = p["row"]
        elif c == "ignore_totals_row":
            plan["ignore_totals"] = True
        elif c == "drop_summary_columns":
            plan["drop_columns"] = list(p["columns"])
    return plan


def materialize(book: Workbook, fixes: list[dict], mapping: Optional[dict] = None):
    """Replay ``fixes`` over the original workbook; return (DataFrame, stats).

    Deterministic: the same workbook and the same fixes always give the same
    table. Columns that no fix names are carried over untouched, except that
    all-numeric text columns (price, stock...) are converted so the engine
    does not receive numbers as strings.
    """
    import pandas as pd

    sheet = next((f["params"]["sheet"] for f in fixes if f["code"] == "use_sheet"),
                 book.sheet_names[0])
    grid = book.sheets[sheet]
    plan = _plan_from_fixes(fixes)
    unpivot = next((f["params"] for f in fixes if f["code"] == "unpivot"), None)
    if unpivot:
        plan["unpivot"] = unpivot
        # unpivot must see the summary columns dropped first and the value
        # columns by their original header names.
    table = _build_table(grid, plan)
    rows_in = len(table)
    cols: dict[str, list] = {n: table.column(n) for n in table.header}
    keep = [True] * rows_in
    protected: set = set()

    for f in fixes:
        c, p = f["code"], f["params"]
        col = p.get("column")
        if c in ("codes_as_text", "keep_leading_zeros", "normalize_codes"):
            cols[col] = _apply_sku_fixes(cols[col], [f])
            protected.add(col)
        elif c == "read_dates":
            da = analyze_date_column(cols[col], order=p.get("order"), serial=p.get("serial"))
            cols[col] = da["dates"]
            protected.add(col)
        elif c == "read_numbers":
            na = analyze_number_column(cols[col], decimal=p.get("decimal"))
            cols[col] = na["values"]
            protected.add(col)
        elif c == "ignore_unreadable_rows":
            for dc in p["columns"]:
                keep = [k and (v is not None) for k, v in zip(keep, cols[dc])]
    # Unreadable-row filtering must precede cumulative undo.
    for f in fixes:
        if f["code"] == "undo_cumulative":
            p = f["params"]
            sk = [code_text(x) if not _is_blank(x) else None for x in cols[p["sku"]]]
            dts = analyze_date_column(cols[p["date"]])["dates"]
            vals = analyze_number_column(cols[p["column"]])["values"]
            cols[p["column"]] = _undo_cumulative(
                sk, dts, vals, cols[p["store"]] if p.get("store") else None)
            protected.add(p["column"])
    # Untouched columns: convert plain-numeric text (price, stock) to numbers.
    for name, values in cols.items():
        if name in protected:
            continue
        nb = [v for v in values if not _is_blank(v)]
        if nb and all(isinstance(v, str) for v in nb) and not (
                classify_name(name) & {"sku_strong", "sku_medium", "store", "name"}):
            if all(re.fullmatch(r"-?(?:[1-9]\d*|0)(?:[.,]\d+)?", v.strip()) for v in nb):
                na = analyze_number_column(values)
                if not na["unresolved"] and not na["format_conflict"]:
                    cols[name] = na["values"]
    data = {n: [v for v, k in zip(vals, keep) if k] for n, vals in cols.items()}
    df = pd.DataFrame(data)
    for f in fixes:
        if f["code"] == "read_dates":
            df[f["params"]["column"]] = pd.to_datetime(df[f["params"]["column"]])
        if f["code"] == "read_numbers" or f["code"] == "undo_cumulative":
            c = f["params"]["column"]
            df[c] = pd.to_numeric(df[c], errors="coerce")
    stats = {"rows_in": rows_in, "rows_out": len(df), "columns": list(df.columns)}
    return df, stats
