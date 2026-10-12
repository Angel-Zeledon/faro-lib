"""How a run reads its source: the stock model x the change-capture strategy.

One entry point, `read_for_run`, yields batches of raw rows in the shape the
profile's column mapping expects — whatever the model. That is what keeps a
single validation path: an aggregate or a ledger is pushed down to the source
as a GROUP BY whose result columns carry the SAME names as the mapped columns,
so the mapper, its transforms, the warehouse value map, the brake and the run
log apply unchanged.

Stock models (`profile.stock_model`):

    snapshot   one row per (sku, warehouse) with the quantity             (v1)
    aggregate  several rows per key (lots, bins, serials): SUM / MIN / MAX of the
               quantity per key, pushed down (SQL GROUP BY, MongoDB $group)
    ledger     stock = SUM of signed movements; see `ledger.py`

Strategies (`profile.strategy`, `auto` picks per engine and mode):

    full           read everything (every model)
    watermark      rows whose updated-at / id is >= the stored watermark - overlap
    ledger_cursor  movements after the stored cursor (ledger incremental)
    change_tracking  SQL Server CHANGETABLE(CHANGES ...) since the stored version
    oracle_rowscn  Oracle rows whose ORA_ROWSCN is above the stored SCN
    change_stream  MongoDB change stream from the stored resume token (replica sets)

Scoped reads (an on-demand refresh of a few SKUs) push `WHERE sku IN (...)`
down when the SKU column is mapped without transforms, and always re-check the
scope after mapping, so a transform can never widen what a scoped run touches.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator, Optional

from backend.errors import AppError
from backend.inventory_sync import reader

STRATEGIES = ("auto", "full", "watermark", "ledger_cursor", "change_tracking", "oracle_rowscn",
              "change_stream")
STOCK_MODELS = ("snapshot", "aggregate", "ledger")
AGGREGATES = ("sum", "min", "max")
MAX_SCOPE_KEYS = 1000


def _literal(value: Any) -> Optional[str]:
    """A string as a SQL literal, or None when it cannot be one safely (a
    backslash means something on MySQL; NUL means a corrupt value)."""
    text = str(value)
    if "\\" in text or "\x00" in text or len(text) > 200:
        return None
    return "'" + text.replace("'", "''") + "'"


def sku_pushdown_ok(profile: dict) -> bool:
    spec = (profile.get("mapping") or {}).get("sku") or {}
    return bool(spec.get("column")) and not spec.get("value_map") and (spec.get("case") or "none") == "none" \
        and (spec.get("cast") or "auto") in ("auto", "text")


def scope_predicate(profile: dict, engine: str, scope: Optional[dict]) -> Optional[str]:
    """`<sku column> IN (...)` for a scoped SQL read, or None (read all, filter after)."""
    from backend.datasources import catalog
    skus = list((scope or {}).get("skus") or [])[:MAX_SCOPE_KEYS]
    if not skus or not sku_pushdown_ok(profile):
        return None
    lits = [_literal(s) for s in skus]
    if any(x is None for x in lits):
        return None
    col = catalog.quote_ident(engine, profile["mapping"]["sku"]["column"])
    return f"{col} IN ({', '.join(lits)})"


def resolve_strategy(profile: dict, engine: str, incremental: bool) -> str:
    chosen = profile.get("strategy") or "auto"
    model = profile.get("stock_model") or "snapshot"
    if chosen != "auto":
        return chosen if incremental else "full"
    if not incremental:
        return "full"
    if model == "ledger":
        return "ledger_cursor"
    return "watermark"


def aggregate_sql(profile: dict, engine: str, base: str) -> str:
    """Wrap `base` (a SELECT) in a GROUP BY over the mapped key columns. The
    quantity becomes SUM/MIN/MAX(quantity) AS <its own column name>; other
    mapped columns MAX(...) AS themselves, so the mapping reads the result."""
    from backend.datasources import catalog
    cfg = profile.get("model_config") or {}
    fn = (cfg.get("aggregate") or "sum").lower()
    if fn not in AGGREGATES:
        raise AppError("inventory_sync_model_invalid", "Unknown aggregate.", status_code=422,
                       params={"reason": "aggregate", "value": fn[:20]})
    mapping = profile.get("mapping") or {}
    q = lambda c: catalog.quote_ident(engine, c)  # noqa: E731
    keys = [mapping[f]["column"] for f in ("sku", "warehouse") if mapping.get(f, {}).get("column")]
    keys += [c for c in (cfg.get("group_by") or []) if c not in keys]
    qty = mapping["current_stock"]["column"]
    others = {m["column"] for f, m in mapping.items()
              if m.get("column") and m["column"] not in keys and m["column"] != qty}
    if profile.get("watermark_column"):
        # Kept (as its MAX per key) so an incremental aggregate can advance its watermark.
        others.add(profile["watermark_column"])
    others = sorted(others)
    select = [f"src.{q(k)} AS {q(k)}" for k in keys]
    select.append(f"{fn.upper()}(src.{q(qty)}) AS {q(qty)}")
    select += [f"MAX(src.{q(c)}) AS {q(c)}" for c in others]
    group = ", ".join(f"src.{q(k)}" for k in keys)
    return f"SELECT {', '.join(select)} FROM ({base}) src GROUP BY {group}"


def read_for_run(tenant_id: str, profile: dict, *, run_id: Optional[str] = None,
                 scope: Optional[dict] = None, incremental: bool = False,
                 max_rows: Optional[int] = None, limit: Optional[int] = None,
                 batch_size: int = reader.BATCH, report: Optional[dict] = None) -> Iterator[list[dict]]:
    model = profile.get("stock_model") or "snapshot"
    if model == "ledger":
        from backend.inventory_sync import ledger
        yield from ledger.read_balances(tenant_id, profile, run_id=run_id, scope=scope,
                                        incremental=incremental, max_rows=max_rows, limit=limit,
                                        report=report)
        return
    chosen = profile.get("strategy") or "auto"
    if chosen in ("change_tracking", "oracle_rowscn", "change_stream") and not scope and not limit:
        # Full runs go through the capture strategy too: they record the
        # starting position (version / SCN / resume token) for the next read.
        from backend.inventory_sync import capture
        yield from capture.read(tenant_id, profile, chosen, run_id=run_id, max_rows=max_rows,
                                incremental=incremental, report=report)
        return
    strategy = resolve_strategy(profile, profile.get("engine") or "", incremental)
    if strategy in ("change_tracking", "oracle_rowscn", "change_stream"):
        strategy = "full"
    watermark = profile.get("watermark_value") if strategy == "watermark" else None
    yield from reader.read_batches(tenant_id, profile, watermark=watermark, limit=limit,
                                   batch_size=batch_size, max_rows=max_rows, scope=scope)


def aggregate_examples(tenant_id: str, profile: dict, skus: list[str]) -> list[dict]:
    """For a few SKUs: the source rows (lots, bins) and their sum — the
    arithmetic an aggregate profile does, shown before it is enabled."""
    from backend.datasources.service import _read_only_rows
    from backend.inventory_sync import mapper
    cfg = reader._cfg(tenant_id, profile["data_source_id"])
    engine = cfg.get("engine") or "postgresql"
    if engine == "mongodb" or not skus or not sku_pushdown_ok(profile):
        return []
    pred = scope_predicate(profile, engine, {"skus": skus[:3]})
    if not pred:
        return []
    sql = f"SELECT src.* FROM ({reader.raw_select(profile, engine)}) src WHERE src.{pred}"
    mapping = profile["mapping"]
    groups: dict[tuple[str, str], list] = {}
    with _read_only_rows(cfg, sql, stream=True, tenant_id=tenant_id, bulk=True) as result:
        cols = [str(c) for c in result.keys()]
        for r in result.fetchmany(300):
            row = dict(zip(cols, r))
            try:
                key = mapper.map_key(row, mapping, default_warehouse=profile.get("default_warehouse"))
                qty = mapper.number(mapper._lookup(row, mapping["current_stock"]["column"]),
                                    mapping["current_stock"].get("decimal") or "auto")
            except mapper.Rejected as exc:
                groups.setdefault(("?", "?"), []).append({"rejected": exc.code})
                continue
            groups.setdefault(key, []).append({"quantity": qty})
    fn = (profile.get("model_config") or {}).get("aggregate") or "sum"
    out = []
    for (sku, wh), rows in list(groups.items())[:3]:
        qs = [r["quantity"] for r in rows if "quantity" in r]
        total = (sum(qs) if fn == "sum" else min(qs) if fn == "min" else max(qs)) if qs else None
        out.append({"sku": sku, "warehouse": wh, "rows": rows[:20], "row_count": len(rows),
                    "aggregate": fn, "total": total})
    return out
