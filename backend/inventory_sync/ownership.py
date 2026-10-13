"""Which stock fields a live sync will overwrite, per (sku, warehouse).

The conflict rule is "the source wins": a value a person types into a field a
sync owns is replaced at that sync's next run (counted and audit-logged as
`manual_overwritten`). The editors warn BEFORE saving; this is what they ask.

The owner of a key is the rule the apply step enforces (apply.py,
"precedence"): among the profiles whose key ledger holds it, the one with the
better (priority, age). Only profiles that will run again count: enabled ones
and on-demand ones (a refresh runs those). A switched-off sync overwrites
nothing until someone turns it on, so it owns nothing here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.db.connection import query
from backend.inventory_sync import fields as F

MAX_KEYS = 5000


def _next_sync_at(p: dict) -> Optional[str]:
    """When this profile is expected to write again; None when nobody can
    tell (push: whenever the customer's system sends; on demand: whenever
    someone presses Refresh)."""
    if p.get("source_kind") == "push" or p.get("freshness") == "on_demand":
        return None
    if p.get("freshness") == "continuous":
        return (datetime.now(timezone.utc) + timedelta(seconds=int(p.get("poll_seconds") or 60))).isoformat()
    nxt = p.get("next_run_at")
    return nxt.isoformat() if isinstance(nxt, datetime) else nxt


def owned_keys(tenant_id: str, skus: Optional[list[str]] = None,
               warehouse_visible=None) -> dict:
    """{"items": [...], "profiles": [...], "truncated": bool}. Each item is one
    (sku, warehouse) a live sync owns, with the fields it writes and the id of
    the owning profile; `profiles` carries each owner's name, schedule and
    next expected run once."""
    params: list = [tenant_id]
    sku_filter = ""
    if skus:
        sku_filter = "AND k.sku = ANY(%s)"
        params.append(list(skus))
    # The precedence winner over EVERY profile holding the key (as apply.py
    # decides it), then kept only when that winner will run again.
    rows = query(
        "SELECT * FROM (SELECT DISTINCT ON (k.sku, k.warehouse) k.sku, k.warehouse, p.id AS profile_id, "
        "  (p.enabled OR p.freshness = 'on_demand') AS live "
        "  FROM inventory_sync_keys k JOIN inventory_sync_profiles p ON p.id = k.profile_id "
        "  WHERE k.tenant_id = %s AND NOT k.inactive AND p.tenant_id = k.tenant_id " + sku_filter + " "
        "  ORDER BY k.sku, k.warehouse, p.priority, p.created_at) w "
        f"WHERE w.live ORDER BY w.sku, w.warehouse LIMIT {MAX_KEYS + 1}", tuple(params))
    truncated = len(rows) > MAX_KEYS
    rows = rows[:MAX_KEYS]
    if warehouse_visible is not None:
        rows = [r for r in rows if warehouse_visible(r["warehouse"])]
    ids = sorted({r["profile_id"] for r in rows})
    profiles = {p["id"]: p for p in (query(
        "SELECT id, name, mapping, freshness, source_kind, enabled, next_run_at, poll_seconds, "
        "interval_minutes FROM inventory_sync_profiles WHERE tenant_id = %s AND id = ANY(%s)",
        (tenant_id, ids)) if ids else [])}
    fields_of = {}
    for pid, p in profiles.items():
        owned = F.owned_fields(p.get("mapping") or {})
        # The apply step always writes the quantity (apply.py), mapped or not.
        fields_of[pid] = ["current_stock"] + [f for f in owned if f != "current_stock"]
    return {
        "items": [{"sku": r["sku"], "warehouse": r["warehouse"], "profile_id": r["profile_id"],
                   "fields": fields_of.get(r["profile_id"], ["current_stock"])} for r in rows],
        "profiles": [{"id": pid, "name": p["name"], "freshness": p.get("freshness"),
                      "push": p.get("source_kind") == "push",
                      "interval_minutes": p.get("interval_minutes"),
                      "next_sync_at": _next_sync_at(p), "fields": fields_of[pid]}
                     for pid, p in sorted(profiles.items())],
        "truncated": truncated,
    }


def overwrites_for(tenant_id: str, rows: list[dict], warehouse_visible=None) -> list[dict]:
    """For rows a person is about to write (an import preview): per owning
    profile, how many of them set a field that sync will replace at its next
    run, and which fields. A row that names no warehouse matches the SKU in
    any warehouse (the import resolves the default one later)."""
    skus = sorted({str(r["sku"]) for r in rows if r.get("sku")})
    if not skus:
        return []
    owned = owned_keys(tenant_id, skus, warehouse_visible=warehouse_visible)
    by_key = {(i["sku"], i["warehouse"]): i for i in owned["items"]}
    by_sku: dict[str, list[dict]] = {}
    for i in owned["items"]:
        by_sku.setdefault(i["sku"], []).append(i)
    from backend.inventory import warehouse_service as wh_svc
    canonical: dict[str, str] = {}
    groups: dict[str, dict] = {}
    for r in rows:
        sku, wh = str(r.get("sku") or ""), r.get("warehouse")
        if wh:
            # "norte " and "Norte" are one warehouse, as the import will store it.
            if wh not in canonical:
                canonical[wh] = wh_svc.resolve_canonical_name(tenant_id, wh)
            wh = canonical[wh]
        hits = [by_key[(sku, wh)]] if wh and (sku, wh) in by_key else ([] if wh else by_sku.get(sku, []))
        for hit in hits[:1]:
            fields = [f for f in hit["fields"] if r.get(f) is not None]
            if not fields:
                continue
            g = groups.setdefault(hit["profile_id"], {"rows": 0, "fields": set()})
            g["rows"] += 1
            g["fields"].update(fields)
    meta = {p["id"]: p for p in owned["profiles"]}
    return [{**{k: meta[pid][k] for k in ("name", "freshness", "push", "next_sync_at")},
             "profile_id": pid, "rows": g["rows"], "fields": sorted(g["fields"])}
            for pid, g in sorted(groups.items())]
