"""
Suggested service level per ABC class — shown, then applied only by a person.

The classification itself is `abc_xyz.py` (and the semáforo already computes it
per SKU in `service._compute_inventory_status`). This module answers two
questions for the "service level by class" panel:

  * `get_suggestions`  read-only: per class, how many SKUs, how much of the
                       value, what service level they plan on today and what the
                       suggestion is, and how many SKUs a click would change.
  * `apply_suggestion` the click: writes the suggested level onto the SKUs of ONE
                       class that nobody has configured.

There is no second store. The service level a SKU plans on is resolved by
`stock_defaults_service.resolve_field` (SKU row > supplier rule > category rule >
global rule > default). Applying writes the SKU row — the top of that same
cascade — through `service.upsert_stock`, stamped 'user' like any other manual
edit. Which SKUs it touches is deliberately narrow:

  * a SKU whose service level already comes from the buyer, a file import or a
    supplier / category / global rule is left alone — somebody already decided,
    and a one-click suggestion must never overwrite a decision;
  * only the SKUs that currently plan on the DEFAULT are written.

Nothing runs by itself: no training, no import and no reclassification moves a
number. A SKU that later changes class keeps the level it was given, because it
is now the buyer's own value (they can clear it from the SKU card).
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.connection import query, transaction
from backend.errors import AppError
from backend.inventory import abc_xyz
from backend.inventory.defaults import SOURCE_DEFAULT

log = logging.getLogger(__name__)


def _status_items(tenant_id: str, session_id: str, service_level: float, period: str) -> list[dict]:
    """One row per SKU, from the persisted snapshot when it is fresh and from a
    live computation otherwise — the same two sources the status endpoint uses."""
    from backend.inventory import service as svc
    from backend.inventory import status_snapshot

    snap = status_snapshot.read_status(tenant_id, session_id, service_level, period)
    if snap is not None:
        return list(snap["items"])
    return svc.get_inventory_status(tenant_id, session_id, service_level, period)


def _resolve_session(tenant_id: str, session_id: Optional[str]) -> tuple[Optional[str], str]:
    from backend.sessions import planning_service

    period = planning_service.get_planning(tenant_id).get("period", "daily")
    return session_id or planning_service.resolve_active_session(tenant_id), period


def _rows(items: list[dict]) -> list[dict]:
    """The per-SKU facts the summary needs. Value is the same proxy the ABC
    ranking used (demand x cost, cost 1.0 when unknown), so the shares add up to
    the classification the SKU column shows."""
    out = []
    for it in items:
        if it.get("abc") not in abc_xyz.ABC_CLASSES:
            continue
        out.append({
            "sku": it["sku"],
            "abc": it["abc"],
            "value": (it.get("daily_demand") or 0.0) * (it.get("unit_cost") or 1.0),
            "service_level": it.get("service_level"),
            "owned": it.get("service_level_source") != SOURCE_DEFAULT,
        })
    return out


def get_suggestions(
    tenant_id: str, session_id: Optional[str] = None, service_level: float = 0.95,
) -> dict:
    """Read-only. `available: False` (with a reason) when there is no completed
    session to classify against — the panel says so instead of showing zeros."""
    sid, period = _resolve_session(tenant_id, session_id)
    if not sid:
        return {"available": False, "reason": "no_session"}
    rows = _rows(_status_items(tenant_id, sid, service_level, period))
    return {
        "available": True,
        "session_id": sid,
        "classes": abc_xyz.class_summary(rows),
        "cutoffs": {
            "a": abc_xyz.ABC_A_CUTOFF, "b": abc_xyz.ABC_B_CUTOFF,
            "x": abc_xyz.XYZ_X_CUTOFF, "y": abc_xyz.XYZ_Y_CUTOFF,
        },
    }


def apply_suggestion(
    tenant_id: str, abc_class: str, session_id: Optional[str] = None,
    service_level: float = 0.95,
) -> dict:
    """Write the class's suggested level onto its unconfigured SKUs, atomically.

    Returns what was changed and what was left alone, so the screen can say "12
    updated, 5 keep the level someone set" instead of a bare success.
    """
    from backend.inventory import service as svc

    cls = (abc_class or "").upper()
    if cls not in abc_xyz.ABC_CLASSES:
        raise AppError(
            "service_level_class_invalid", "Class must be A, B or C",
            status_code=422, params={"abc": abc_class},
        )
    sid, period = _resolve_session(tenant_id, session_id)
    if not sid:
        raise AppError(
            "no_completed_session", "No completed session for this tenant yet",
            status_code=400,
        )

    suggested = abc_xyz.SUGGESTED_SERVICE_LEVEL[cls]
    items = [i for i in _status_items(tenant_id, sid, service_level, period) if i.get("abc") == cls]
    candidates = {i["sku"] for i in items if i.get("service_level_source") == SOURCE_DEFAULT}
    kept = len(items) - len(candidates)

    updated_skus: set[str] = set()
    with transaction() as conn:
        # Re-read the rows inside the transaction: the status may be an hour old
        # and a person may have set a level since. `service_level_set_by` is the
        # authority on who owns the number.
        stock_rows = query(
            "SELECT sku, warehouse, service_level_set_by FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = ANY(%s)",
            (tenant_id, sorted(candidates)), conn=conn,
        ) if candidates else []
        for row in stock_rows:
            if row.get("service_level_set_by"):
                continue
            svc.upsert_stock(
                tenant_id, row["sku"],
                {"service_level": suggested, "warehouse": row["warehouse"]},
                conn=conn,
            )
            updated_skus.add(row["sku"])

    return {
        "abc": cls,
        "service_level": suggested,
        "updated": len(updated_skus),
        "kept_own_level": kept,
        # Classified SKUs that have no stock row to write onto.
        "without_stock_row": len(candidates - {r["sku"] for r in stock_rows}) if candidates else 0,
        "skus": sorted(updated_skus),
    }
