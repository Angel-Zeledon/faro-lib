"""Warehouse and supplier masters: the places and vendors behind the stock rows.

A stock row usually carries a CODE ("01", "BOD-NORTE", SAP plant + storage
location), not the name a buyer reads. An optional master source (a table,
view, saved query or collection on the same data source) says what each code
is: its name, whether it is still active, its kind and city — and for
suppliers their lead time, terms and contacts, plus an optional item-vendor
relation (several suppliers per SKU, one primary).

Rules, all visible in the dry run and the run's warnings:

* **Codes are matched normalised**: trimmed, accents dropped, case folded,
  inner spaces collapsed, and a purely numeric code loses its leading zeros
  ("007" = "7"). Two master rows that normalise alike with different names are
  a collision: the first wins and both are reported.
* **A source code is remembered** (`inventory_sync_warehouses` /
  `inventory_sync_suppliers`) with the StockAI name it became. When the source
  later renames it, StockAI keeps its name (stock, orders and history are keyed
  by it) and records the new source name — reported, never duplicated.
* **Nothing is deleted.** A code the master marks inactive, or stops listing,
  is recorded inactive; its stock rows still sync (stock is stock).
* **Unknown warehouse policy** (`unknown_warehouse`): `create` the warehouse
  (within the plan's ceiling, which the apply step enforces and reports),
  `reject` the row by name, or `default` (book it to the default warehouse).
  Unset: `create` when a master is configured or `allow_new_warehouses` is on,
  `reject` otherwise — the v1 behaviour.
* **Supplier lead time from the source is a DECLARED value**: written only
  where nobody set one by hand (`lead_time_set_by` empty or `sync`), never over
  a user's value unless the master says `source_wins`. A lead time StockAI
  LEARNED from receptions is computed at read time and keeps precedence over
  any declared value, so a sync never hides what the receptions measured.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from backend.db.connection import execute, query, query_one
from backend.errors import AppError

MAX_MASTER_ROWS = 20_000
UNKNOWN_POLICIES = ("create", "reject", "default")
_KINDS = ("table", "query", "collection", "pipeline")
_FALSEY = {"0", "false", "f", "n", "no", "inactive", "inactivo", "i", "x", "closed", "cerrado", "baja", "b"}


def _bad(reason: str, **params: Any) -> AppError:
    return AppError("inventory_sync_master_invalid", f"The master source is not valid: {reason}.",
                    status_code=422, params={"reason": reason, **params})


def norm_code(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value if value is not None else ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip().casefold()
    if text.isdigit():
        text = text.lstrip("0") or "0"
    return text


def _col(cfg: dict, key: str, required: bool = False) -> Optional[str]:
    v = cfg.get(key)
    v = None if v in (None, "") else str(v).strip()[:256]
    if required and not v:
        raise _bad("column_required", field=key)
    return v


def _source(cfg: dict) -> dict:
    kind = cfg.get("source_kind") or "table"
    if kind not in _KINDS:
        raise _bad("source_kind", value=str(kind)[:20])
    out = {"source_kind": kind, "source_schema": _col(cfg, "source_schema"),
           "source_object": _col(cfg, "source_object"), "source_query": None}
    if kind in ("query", "pipeline"):
        q = str(cfg.get("source_query") or "").strip()
        if not q:
            raise _bad("query_required")
        out["source_query"] = q[:100_000]
    if kind in ("table", "collection", "pipeline") and not out["source_object"]:
        raise _bad("object_required")
    return out


def normalize(masters: Any, *, has_source: bool) -> dict:
    """Validate the `masters` blob of a profile."""
    if masters in (None, ""):
        return {}
    if not isinstance(masters, dict):
        raise _bad("not_object")
    out: dict = {}
    if masters.get("warehouses"):
        if not has_source:
            raise _bad("needs_data_source")
        w = masters["warehouses"]
        if not isinstance(w, dict):
            raise _bad("not_object", part="warehouses")
        out["warehouses"] = {**_source(w), "code_column": _col(w, "code_column", True),
                             "name_column": _col(w, "name_column", True),
                             "active_column": _col(w, "active_column"), "kind_column": _col(w, "kind_column"),
                             "city_column": _col(w, "city_column")}
    if masters.get("suppliers"):
        if not has_source:
            raise _bad("needs_data_source")
        s = masters["suppliers"]
        if not isinstance(s, dict):
            raise _bad("not_object", part="suppliers")
        out["suppliers"] = {**_source(s), "code_column": _col(s, "code_column", True),
                            "name_column": _col(s, "name_column", True),
                            "active_column": _col(s, "active_column"),
                            "lead_time_column": _col(s, "lead_time_column"),
                            "payment_terms_column": _col(s, "payment_terms_column"),
                            "email_column": _col(s, "email_column"), "phone_column": _col(s, "phone_column"),
                            "source_wins": bool(s.get("source_wins", False)),
                            "unknown": s.get("unknown") if s.get("unknown") in ("create", "reject") else "create"}
    return out


def effective_unknown_policy(profile: dict) -> str:
    p = profile.get("unknown_warehouse")
    if p in UNKNOWN_POLICIES:
        return p
    has_master = bool((profile.get("masters") or {}).get("warehouses"))
    return "create" if (profile.get("allow_new_warehouses") or has_master) else "reject"


def _active(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() not in _FALSEY


def _read(tenant_id: str, profile: dict, cfg: dict) -> list[dict]:
    from backend.inventory_sync import reader
    pseudo = {"data_source_id": profile["data_source_id"], "source_kind": cfg["source_kind"],
              "source_schema": cfg.get("source_schema"), "source_object": cfg.get("source_object"),
              "source_query": cfg.get("source_query"), "mapping": {}, "mode": "full"}
    rows: list[dict] = []
    for batch in reader.read_batches(tenant_id, pseudo, max_rows=MAX_MASTER_ROWS):
        rows.extend(batch)
    return rows


def _lookup(row: dict, column: Optional[str]) -> Any:
    from backend.inventory_sync import mapper
    return mapper._lookup(row, column) if column else None


_COLUMN_KEYS = ("code_column", "name_column", "active_column", "kind_column", "city_column",
                "lead_time_column", "payment_terms_column", "email_column", "phone_column")


def _require_columns(rows: list[dict], cfg: dict, which: str) -> None:
    """A configured column that no master row carries is a mistake to name, not
    an empty master: a typo in the code column would otherwise skip every row,
    report zero master rows and, on a real run, mark every code the master used
    to list inactive. Same rule as the stock mapping: absent from every row
    (documents are polymorphic), compared case-blind like the lookup."""
    if not rows:
        return
    seen: set[str] = set()
    for r in rows:
        seen.update(str(k).lower() for k in r)
    missing = [cfg[k] for k in _COLUMN_KEYS
               if cfg.get(k) and str(cfg[k]).lower() not in seen and not _dotted_present(rows, cfg[k])]
    if missing:
        raise AppError("inventory_sync_column_missing",
                       f"The {which} master has no column {missing[0]}.", status_code=422,
                       params={"column": missing[0], "columns": ", ".join(missing), "master": which})


def _dotted_present(rows: list[dict], column: str) -> bool:
    # A MongoDB master may name a nested field ("detalle.nombre").
    return "." in column and any(_lookup(r, column) is not None for r in rows)


def load(tenant_id: str, profile: dict, which: str) -> tuple[dict, list[dict]]:
    """{normalised code: entry} for a configured master, and the collisions."""
    cfg = (profile.get("masters") or {}).get(which)
    if not cfg:
        return {}, []
    out: dict[str, dict] = {}
    collisions: list[dict] = []
    rows = _read(tenant_id, profile, cfg)
    _require_columns(rows, cfg, which)
    for r in rows:
        code = _lookup(r, cfg["code_column"])
        name = _lookup(r, cfg["name_column"])
        if code in (None, "") or name in (None, ""):
            continue
        key = norm_code(code)
        entry = {"code": str(code).strip(), "name": str(name).strip()[:200],
                 "active": _active(_lookup(r, cfg.get("active_column")))}
        for extra in ("kind_column", "city_column", "lead_time_column", "payment_terms_column",
                      "email_column", "phone_column"):
            if cfg.get(extra):
                entry[extra[:-7]] = _lookup(r, cfg[extra])
        if key in out:
            if norm_code(out[key]["name"]) != norm_code(entry["name"]) and len(collisions) < 50:
                collisions.append({"code": entry["code"], "kept": out[key]["name"], "dropped": entry["name"]})
            continue
        out[key] = entry
    return out, collisions


def aliases(profile_id: str, which: str) -> dict[str, dict]:
    table, col = (("inventory_sync_warehouses", "warehouse_name") if which == "warehouses"
                  else ("inventory_sync_suppliers", "supplier_name"))
    return {r["source_code"]: r for r in query(
        f"SELECT source_code, {col} AS name, source_name, active FROM {table} WHERE profile_id = %s",
        (profile_id,))}


def record_warehouses(tenant_id: str, profile_id: str, seen: dict[str, dict]) -> None:
    """Remember code -> StockAI warehouse for every code this run saw. An
    existing link keeps its StockAI name; the source name and flags update."""
    for code, e in list(seen.items())[:5000]:
        execute(
            "INSERT INTO inventory_sync_warehouses (tenant_id, profile_id, source_code, warehouse_name, "
            "source_name, kind, city, active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (profile_id, source_code) DO UPDATE SET source_name = EXCLUDED.source_name, "
            "kind = EXCLUDED.kind, city = EXCLUDED.city, active = EXCLUDED.active, last_seen_at = NOW()",
            (tenant_id, profile_id, code, e["name"], e.get("source_name"),
             None if e.get("kind") is None else str(e["kind"])[:60],
             None if e.get("city") is None else str(e["city"])[:120], bool(e.get("active", True))))


def mark_unlisted_inactive(profile_id: str, which: str, listed: set[str]) -> int:
    """Codes the master no longer lists: recorded inactive, never deleted."""
    table = "inventory_sync_warehouses" if which == "warehouses" else "inventory_sync_suppliers"
    rows = query(f"SELECT source_code FROM {table} WHERE profile_id = %s AND active", (profile_id,))
    gone = [r["source_code"] for r in rows if r["source_code"] not in listed]
    if gone:
        execute(f"UPDATE {table} SET active = FALSE WHERE profile_id = %s AND source_code = ANY(%s)",
                (profile_id, gone))
    return len(gone)


# ── Suppliers ────────────────────────────────────────────────────────────────

def _int_or_none(v: Any) -> Optional[int]:
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return int(round(f)) if 0 <= f <= 3650 else None


def apply_suppliers(tenant_id: str, profile: dict, master: dict[str, dict]) -> dict:
    """Create / update StockAI suppliers from the supplier master. Returns
    counters {created, updated, unchanged, lead_time_kept, renamed_in_source,
    inactive}. Never deletes; never overwrites a user-set lead time unless the
    master says source_wins; a learned lead time keeps precedence at read time."""
    from backend.inventory import supplier_service as sup
    cfg = (profile.get("masters") or {}).get("suppliers") or {}
    links = aliases(profile["id"], "suppliers")
    out = {"created": 0, "updated": 0, "unchanged": 0, "lead_time_kept": 0, "renamed_in_source": 0,
           "inactive": 0}
    for key, e in master.items():
        link = links.get(key)
        stockai_name = link["name"] if link else e["name"]
        if link and norm_code(link.get("source_name") or link["name"]) != norm_code(e["name"]):
            out["renamed_in_source"] += 1
        existing = sup.find_supplier_by_name_any_state(tenant_id, stockai_name)
        data: dict[str, Any] = {}
        lt = _int_or_none(e.get("lead_time"))
        if e.get("email") not in (None, ""):
            data["email"] = str(e["email"]).strip()[:200]
        if e.get("phone") not in (None, ""):
            data["phone"] = str(e["phone"]).strip()[:60]
        if e.get("payment_terms") not in (None, ""):
            data["payment_terms"] = str(e["payment_terms"]).strip()[:120]
        if not existing:
            row = sup.create_supplier(tenant_id, {"name": stockai_name, **data})
            if lt is not None:
                execute("UPDATE suppliers SET lead_time_days = %s, lead_time_set_by = 'sync' "
                        "WHERE tenant_id = %s AND id = %s", (lt, tenant_id, row["id"]))
            out["created"] += 1
        else:
            changed = {k: v for k, v in data.items() if existing.get(k) != v}
            if changed and existing.get("active", True):
                sup.update_supplier(tenant_id, existing["id"], changed)
            if lt is not None and lt != existing.get("lead_time_days"):
                if (existing.get("lead_time_set_by") in (None, "sync")) or cfg.get("source_wins"):
                    execute("UPDATE suppliers SET lead_time_days = %s, lead_time_set_by = 'sync' "
                            "WHERE tenant_id = %s AND id = %s", (lt, tenant_id, existing["id"]))
                    changed["lead_time_days"] = lt
                else:
                    out["lead_time_kept"] += 1
            out["updated" if changed else "unchanged"] += 1
        if not e.get("active", True):
            out["inactive"] += 1
        execute(
            "INSERT INTO inventory_sync_suppliers (tenant_id, profile_id, source_code, supplier_name, "
            "source_name, active) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (profile_id, source_code) DO UPDATE "
            "SET source_name = EXCLUDED.source_name, active = EXCLUDED.active, last_seen_at = NOW()",
            (tenant_id, profile["id"], key, stockai_name, e["name"], bool(e.get("active", True))))
    mark_unlisted_inactive(profile["id"], "suppliers", set(master))
    return out


def supplier_name_for(master: dict[str, dict], links: dict[str, dict], value: Any) -> Optional[str]:
    """The StockAI supplier name for a supplier code/name on a stock row."""
    if value in (None, ""):
        return None
    key = norm_code(value)
    if key in links:
        return links[key]["name"]
    if key in master:
        return master[key]["name"]
    return None


def preview_suppliers(tenant_id: str, profile: dict, master: dict[str, dict]) -> dict:
    """What `apply_suppliers` would do, writing nothing."""
    from backend.inventory import supplier_service as sup
    cfg = (profile.get("masters") or {}).get("suppliers") or {}
    links = aliases(profile["id"], "suppliers") if profile.get("id") else {}
    out = {"create": [], "update": 0, "renamed_in_source": [], "lead_time_kept": 0, "inactive": 0}
    for key, e in master.items():
        link = links.get(key)
        name = link["name"] if link else e["name"]
        if link and norm_code(link.get("source_name") or link["name"]) != norm_code(e["name"]):
            out["renamed_in_source"].append({"code": e["code"], "stockai": name, "source": e["name"]})
        existing = sup.find_supplier_by_name_any_state(tenant_id, name)
        if not existing:
            if len(out["create"]) < 50:
                out["create"].append(name)
        else:
            out["update"] += 1
            lt = _int_or_none(e.get("lead_time"))
            if lt is not None and lt != existing.get("lead_time_days") and \
                    existing.get("lead_time_set_by") not in (None, "sync") and not cfg.get("source_wins"):
                out["lead_time_kept"] += 1
        if not e.get("active", True):
            out["inactive"] += 1
    return out
