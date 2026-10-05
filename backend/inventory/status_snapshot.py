"""Persisted snapshot of the inventory status list (docs/status-performance.md).

`GET /inventory/status` used to recompute every SKU on every request. This
module keeps the computed rows in `inventory_status_snapshot` and serves paging,
filtering and the summary from SQL, recomputing only when an input changed.

The rule that matters is the one about staleness: a snapshot is served only when
ALL of these hold, and any doubt means "recompute" — never "serve it anyway":

  1. no row of any table the computation reads has been written for this tenant
     since the snapshot was taken (`status_input_bumps`, appended to by
     statement-level triggers — see backend/db/migrations.py);
  2. it was computed today (the computation reads `date.today()`: declared
     events, the 14-day sparkline);
  3. it is younger than `MAX_AGE` (the sparkline window slides with the clock);
  4. the code that computes it has not changed (`code_hash`, a digest of the
     source files that define the numbers — a deploy invalidates everything);
  5. it was computed for the same (session, period, service_level).

If building or reading the snapshot raises for any reason, `read_status`
returns None and the caller runs the full live computation: the snapshot is an
accelerator, never a second source of truth.

Granularity: one generation per (tenant, session, period, service_level), and a
refresh recomputes the whole generation. Per-row incremental refresh was
considered and rejected: ABC classes are a ranking over every row, and most
inputs (suppliers, rules, events, the forecast itself) are tenant-wide, so a
partial refresh would have to prove which rows an edit cannot reach — the
failure mode is a stale row served as fresh, which is exactly what this module
exists to prevent.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import psycopg2.extras

from backend.db.connection import _sanitize, get_conn, query, query_one

log = logging.getLogger(__name__)

# The sparkline looks back 14 days from "now"; an hour of drift is the bound on
# how far a served snapshot can differ from a live computation made at the
# moment of the request, when no input row changed.
MAX_AGE = timedelta(hours=1)

# Source files whose logic defines the numbers in a status row.
_CODE_FILES = (
    "inventory/service.py", "inventory/stock_defaults_service.py",
    "inventory/signal_thresholds.py", "inventory/defaults.py",
    "inventory/supplier_service.py", "inventory/series.py",
    "inventory/warehouse_service.py", "inventory/status_snapshot.py",
)
_code_hash_cache: Optional[str] = None


def code_hash() -> str:
    global _code_hash_cache
    if _code_hash_cache is None:
        base = Path(__file__).resolve().parent.parent
        h = hashlib.sha256()
        for rel in _CODE_FILES:
            f = base / rel
            h.update(rel.encode())
            h.update(f.read_bytes() if f.exists() else b"missing")
        _code_hash_cache = h.hexdigest()[:16]
    return _code_hash_cache


def current_version(tenant_id: str) -> int:
    """Newest input change recorded for the tenant (0 when none ever)."""
    row = query_one(
        "SELECT COALESCE(MAX(id), 0) AS v FROM status_input_bumps WHERE tenant_id = %s",
        (tenant_id,),
    )
    return int(row["v"])


_KEY = "tenant_id = %s AND session_id = %s AND period = %s AND service_level = %s"


def _meta(tenant_id: str, session_id: str, period: str, service_level: float,
          conn: Any = None) -> Optional[dict]:
    return query_one(
        f"SELECT * FROM inventory_status_snapshot_meta WHERE {_KEY}",
        (tenant_id, session_id, period, float(service_level)), conn=conn,
    )


def is_fresh(meta: Optional[dict], version: int, *, now: Optional[datetime] = None) -> bool:
    if not meta:
        return False
    now = now or datetime.now(timezone.utc)
    return (
        int(meta["inputs_version"]) == int(version)
        and meta["code_hash"] == code_hash()
        and meta["computed_on"] == date.today()
        and now - meta["computed_at"] < MAX_AGE
    )


def _json_default(o: Any):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def _dumps(o: Any) -> str:
    try:
        # Fast path: the walk in _sanitize costs more than the dump itself and
        # is only needed in the rare row that carries a NaN/Inf.
        return json.dumps(o, default=_json_default, allow_nan=False)
    except ValueError:
        return json.dumps(_sanitize(o), default=_json_default)


def _search_text(item: dict) -> str:
    # The exact haystack the live endpoint filter builds.
    return " ".join(
        str(item.get(k) or "") for k in ("sku", "display_name", "category", "supplier")
    ).lower()


def refresh(tenant_id: str, session_id: str, service_level: float, period: str) -> dict:
    """Recompute and store a new generation; return its meta row.

    Concurrent callers for the same key queue on an advisory lock and the
    second one finds the first one's fresh generation instead of computing
    again. The key is NOT the tenant lock `take_tenant_lock` uses, so a
    refresh never makes the tenant's own writes wait.
    """
    from backend.inventory import service as svc

    sl = float(service_level)
    lock_key = f"status_snapshot:{tenant_id}:{session_id}:{period}:{sl}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '120s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
        meta = _meta(tenant_id, session_id, period, sl, conn=conn)
        # Read BEFORE computing: a write that commits while we compute leaves
        # the stored version older than the live one, so the next read
        # recomputes. The reverse (a version that is newer than the data) is
        # impossible because a change and its bump commit together.
        version = current_version(tenant_id)
        if is_fresh(meta, version):
            return meta

        items = svc._compute_inventory_status(tenant_id, session_id, sl, period=period)
        generation = (int(meta["generation"]) + 1) if meta else 1
        computed_at = datetime.now(timezone.utc)
        rows = [
            (
                tenant_id, session_id, period, sl, generation, it["sku"], pos,
                it["signal"], (it.get("supplier") or "").lower(), _search_text(it),
                bool(it.get("has_stock")), bool(it.get("has_forecast")),
                it.get("inventory_value"),
                psycopg2.extras.Json({f: it.get(f) for f in _SORT_FIELDS}, dumps=_dumps),
                psycopg2.extras.Json(it, dumps=_dumps), computed_at,
            )
            for pos, it in enumerate(items)
        ]
        with conn.cursor() as cur:
            if rows:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO inventory_status_snapshot
                         (tenant_id, session_id, period, service_level, generation, sku,
                          urgency_pos, signal, supplier_lc, search_text, has_stock,
                          has_forecast, inventory_value, sort_keys, item, computed_at)
                       VALUES %s""",
                    rows, page_size=500,
                )
            cur.execute(
                """INSERT INTO inventory_status_snapshot_meta
                     (tenant_id, session_id, period, service_level, generation,
                      inputs_version, code_hash, computed_on, computed_at, n_rows)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, session_id, period, service_level)
                   DO UPDATE SET generation = EXCLUDED.generation,
                                 inputs_version = EXCLUDED.inputs_version,
                                 code_hash = EXCLUDED.code_hash,
                                 computed_on = EXCLUDED.computed_on,
                                 computed_at = EXCLUDED.computed_at,
                                 n_rows = EXCLUDED.n_rows""",
                (tenant_id, session_id, period, sl, generation, version, code_hash(),
                 date.today(), computed_at, len(rows)),
            )
            # Keep the previous generation for readers that picked it a moment
            # ago; drop everything older.
            cur.execute(
                f"DELETE FROM inventory_status_snapshot WHERE {_KEY} AND generation < %s",
                (tenant_id, session_id, period, sl, generation - 1),
            )
            # Other (session, period, level) keys of this tenant that are no
            # longer the active session are dead weight: keep the 3 newest.
            cur.execute(
                """DELETE FROM inventory_status_snapshot_meta m
                    WHERE m.tenant_id = %s AND m.computed_at < (
                      SELECT COALESCE(MIN(computed_at), 'epoch') FROM (
                        SELECT computed_at FROM inventory_status_snapshot_meta
                         WHERE tenant_id = %s ORDER BY computed_at DESC LIMIT 3) k)""",
                (tenant_id, tenant_id),
            )
            cur.execute(
                """DELETE FROM inventory_status_snapshot s
                    WHERE s.tenant_id = %s AND NOT EXISTS (
                      SELECT 1 FROM inventory_status_snapshot_meta m
                       WHERE m.tenant_id = s.tenant_id AND m.session_id = s.session_id
                         AND m.period = s.period AND m.service_level = s.service_level)""",
                (tenant_id,),
            )
            # The ledger only ever needs its newest row per tenant.
            cur.execute(
                """DELETE FROM status_input_bumps
                    WHERE tenant_id = %s AND at < NOW() - INTERVAL '1 day'
                      AND id < (SELECT MAX(id) FROM status_input_bumps WHERE tenant_id = %s)""",
                (tenant_id, tenant_id),
            )
        return _meta(tenant_id, session_id, period, sl, conn=conn) or {}


def ensure_fresh(tenant_id: str, session_id: str, service_level: float, period: str) -> dict:
    meta = _meta(tenant_id, session_id, period, service_level)
    if is_fresh(meta, current_version(tenant_id)):
        return meta
    return refresh(tenant_id, session_id, service_level, period)


_SORT_FIELDS = (
    "sku", "signal", "display_name", "supplier", "current_stock", "coverage_days",
    "lead_time_demand", "recommended_qty", "lead_time_days", "moq", "abc_xyz",
    "inventory_value",
)


def read_status(
    tenant_id: str, session_id: str, service_level: float, period: str, *,
    signal: Optional[str] = None, supplier: Optional[str] = None,
    skus: Optional[str] = None, q: Optional[str] = None,
    sort: str = "urgency", order: Optional[str] = None,
    limit: Optional[int] = None, offset: int = 0,
    abc: Optional[str] = None,
) -> Optional[dict]:
    """The rows and summary the status endpoint answers with, from the snapshot.

    Filter semantics are the live endpoint's, exactly (see inventory_status in
    backend/api/v1/inventory.py): signal upper-cased, supplier compared
    case-insensitively, `skus` an exact set, `q` a lower-cased substring of
    "sku display_name category supplier". Returns None when the snapshot cannot
    be trusted or built — the caller then computes live.
    """
    from backend.inventory import service as svc

    try:
        sl = float(service_level)
        meta = ensure_fresh(tenant_id, session_id, sl, period)
        if not meta:
            return None
        gen = int(meta["generation"])

        where = [f"{_KEY}", "generation = %s"]
        params: list = [tenant_id, session_id, period, sl, gen]
        if signal:
            where.append("signal = %s")
            params.append(signal.upper())
        if supplier:
            where.append("supplier_lc = %s")
            params.append(supplier.lower())
        if abc:
            # The class is part of the stored row, not a column of its own.
            where.append("item->>'abc' = %s")
            params.append(abc.upper())
        if skus and skus.strip():
            wanted = sorted({x.strip() for x in skus.split(",") if x.strip()})
            where.append("sku = ANY(%s)")
            params.append(wanted)
        if q and q.strip():
            where.append("position(%s in search_text) > 0")
            params.append(q.strip().lower())
        clause = " AND ".join(where)

        agg = query_one(
            f"""SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE signal = 'PEDIR_YA') AS order_now,
                       COUNT(*) FILTER (WHERE signal = 'PEDIR_PRONTO') AS order_soon,
                       COUNT(*) FILTER (WHERE signal = 'OK') AS ok,
                       COUNT(*) FILTER (WHERE NOT has_stock) AS without_stock,
                       COUNT(*) FILTER (WHERE has_forecast) AS with_forecast,
                       COUNT(*) FILTER (WHERE signal = 'SOBRESTOCK') AS overstock,
                       COUNT(*) FILTER (WHERE signal = 'SIN_DATOS') AS sin_datos,
                       COALESCE(SUM(inventory_value), 0) AS total_value
                  FROM inventory_status_snapshot WHERE {clause}""",
            tuple(params),
        )

        if limit is None:
            rows = query(
                f"SELECT item FROM inventory_status_snapshot WHERE {clause} ORDER BY urgency_pos",
                tuple(params))
            shown = [r["item"] for r in rows]
        elif sort == "urgency":
            rows = query(
                f"SELECT item FROM inventory_status_snapshot WHERE {clause} "
                f"ORDER BY urgency_pos LIMIT %s OFFSET %s",
                tuple(params) + (limit, offset))
            shown = [r["item"] for r in rows]
        else:
            # Same ordering function as the live path, fed from the sort keys
            # only; the full rows are fetched for the page alone.
            light = [r["light"] for r in query(
                f"SELECT sort_keys AS light FROM inventory_status_snapshot "
                f"WHERE {clause} ORDER BY urgency_pos", tuple(params))]
            page_skus = [i["sku"] for i in svc.sort_status_items(light, sort, order)[offset:offset + limit]]
            by_sku = {r["sku"]: r["item"] for r in query(
                f"SELECT sku, item FROM inventory_status_snapshot WHERE {_KEY} "
                f"AND generation = %s AND sku = ANY(%s)",
                (tenant_id, session_id, period, sl, gen, page_skus))}
            shown = [by_sku[s] for s in page_skus if s in by_sku]

        return {
            "items": shown,
            "total": int(agg["total"]),
            "counts": {
                "order_now": int(agg["order_now"]), "order_soon": int(agg["order_soon"]),
                "ok": int(agg["ok"]), "without_stock": int(agg["without_stock"]),
                "with_forecast": int(agg["with_forecast"]),
                "overstock": int(agg["overstock"]), "sin_datos": int(agg["sin_datos"]),
            },
            "total_value": float(agg["total_value"] or 0),
            "computed_at": meta["computed_at"].isoformat(),
        }
    except Exception:
        log.exception("status snapshot unavailable for tenant=%s; computing live", tenant_id)
        return None
