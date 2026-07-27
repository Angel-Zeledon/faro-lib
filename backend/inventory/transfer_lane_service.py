"""
Transfer lanes: how long a move between two warehouses takes and what it costs
(PENDIENTES #2).

A lane is one row per ordered (from_warehouse, to_warehouse) pair carrying
`lead_time_days`, `cost_per_unit` and `fixed_cost`. Everything that has to
reason about time or money for an inter-warehouse move — the transfer-vs-buy
recommendation in `service._network_transfer_pass`, the MILP input builder,
and `transfer_service.create_transfer` (which stamps the promised ETA) — reads
its numbers from here so there is exactly one owner of "what does this lane
cost / how slow is it".

A tenant that never configures a lane must keep working exactly as before, so
an unconfigured pair resolves to DEFAULT_LANE: 1 day, zero variable cost, zero
fixed cost. That default is deliberately optimistic (fast and free): it makes
the transfer beat any purchase with a lead time above one day, which is the
pre-feature behavior the recommendation had when it only reasoned about
coverage.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Documented fallback for an unconfigured (from, to) pair. See module docstring.
DEFAULT_LANE_LEAD_TIME_DAYS = 1
DEFAULT_LANE_COST_PER_UNIT = 0.0
DEFAULT_LANE_FIXED_COST = 0.0


def default_lane(from_warehouse: str, to_warehouse: str) -> dict:
    """The resolved lane for a pair with no configured row."""
    return {
        "from_warehouse": from_warehouse,
        "to_warehouse": to_warehouse,
        "lead_time_days": DEFAULT_LANE_LEAD_TIME_DAYS,
        "cost_per_unit": DEFAULT_LANE_COST_PER_UNIT,
        "fixed_cost": DEFAULT_LANE_FIXED_COST,
        "is_default": True,
    }


def _as_lane(row: dict) -> dict:
    return {
        "from_warehouse": row["from_warehouse"],
        "to_warehouse": row["to_warehouse"],
        "lead_time_days": int(row["lead_time_days"]),
        "cost_per_unit": float(row["cost_per_unit"]),
        "fixed_cost": float(row["fixed_cost"]),
        "is_default": False,
    }


def list_lanes(tenant_id: str) -> list[dict]:
    """Configured lanes only (never synthesizes defaults) — this is what the
    lane-configuration UI edits."""
    return query(
        """SELECT * FROM transfer_lanes WHERE tenant_id = %s
           ORDER BY from_warehouse, to_warehouse""",
        (tenant_id,),
    )


def get_lane(tenant_id: str, from_warehouse: str, to_warehouse: str) -> Optional[dict]:
    return query_one(
        """SELECT * FROM transfer_lanes
           WHERE tenant_id = %s AND from_warehouse = %s AND to_warehouse = %s""",
        (tenant_id, from_warehouse, to_warehouse),
    )


def resolve_lane(tenant_id: str, from_warehouse: str, to_warehouse: str) -> dict:
    """Lane for a pair, falling back to DEFAULT_LANE when none is configured.
    Callers that need many pairs should use `lane_map` instead (one query)."""
    row = get_lane(tenant_id, from_warehouse, to_warehouse)
    return _as_lane(row) if row else default_lane(from_warehouse, to_warehouse)


def lane_map(tenant_id: str) -> dict[tuple[str, str], dict]:
    """All configured lanes keyed by (from, to). Pairs absent from the map are
    unconfigured — resolve them through `lane_for` so the fallback stays in one
    place."""
    return {(r["from_warehouse"], r["to_warehouse"]): _as_lane(r)
            for r in list_lanes(tenant_id)}


def lane_for(lanes: dict[tuple[str, str], dict],
             from_warehouse: str, to_warehouse: str) -> dict:
    """Look a pair up in a preloaded `lane_map`, falling back to DEFAULT_LANE."""
    return lanes.get((from_warehouse, to_warehouse)) or default_lane(
        from_warehouse, to_warehouse)


def upsert_lane(
    tenant_id: str,
    from_warehouse: str,
    to_warehouse: str,
    lead_time_days: int,
    cost_per_unit: float = DEFAULT_LANE_COST_PER_UNIT,
    fixed_cost: float = DEFAULT_LANE_FIXED_COST,
    conn: Optional[Any] = None,
) -> dict:
    """Create or update the lane for one ordered pair.

    Warehouse names go through the same normalize-at-write resolution as every
    other warehouse write, so a lane typed as 'norte' lands on the existing
    'Norte' location instead of creating a lane nothing will ever match.
    """
    from backend.inventory import warehouse_service as wh_svc

    from_warehouse = wh_svc.resolve_canonical_name(tenant_id, from_warehouse)
    to_warehouse = wh_svc.resolve_canonical_name(tenant_id, to_warehouse)
    if from_warehouse == to_warehouse:
        raise ValueError("Origin and destination warehouses must differ")
    if not wh_svc.get_warehouse_by_name(tenant_id, from_warehouse):
        raise ValueError(f"Warehouse '{from_warehouse}' not found")
    if not wh_svc.get_warehouse_by_name(tenant_id, to_warehouse):
        raise ValueError(f"Warehouse '{to_warehouse}' not found")

    lead_time_days = int(lead_time_days)
    cost_per_unit = float(cost_per_unit)
    fixed_cost = float(fixed_cost)
    if lead_time_days < 0:
        raise ValueError("lead_time_days cannot be negative")
    if cost_per_unit < 0 or fixed_cost < 0:
        raise ValueError("Lane costs cannot be negative")

    row = query_one(
        """INSERT INTO transfer_lanes
               (tenant_id, from_warehouse, to_warehouse,
                lead_time_days, cost_per_unit, fixed_cost)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, from_warehouse, to_warehouse) DO UPDATE
               SET lead_time_days = EXCLUDED.lead_time_days,
                   cost_per_unit  = EXCLUDED.cost_per_unit,
                   fixed_cost     = EXCLUDED.fixed_cost
           RETURNING *""",
        (tenant_id, from_warehouse, to_warehouse,
         lead_time_days, cost_per_unit, fixed_cost),
        conn=conn,
    )
    log.info("[lane] upsert tenant=%s %s->%s days=%s unit=%s fixed=%s",
             tenant_id, from_warehouse, to_warehouse,
             lead_time_days, cost_per_unit, fixed_cost)
    return row  # type: ignore[return-value]


def delete_lane(tenant_id: str, from_warehouse: str, to_warehouse: str) -> bool:
    """Remove a lane; the pair reverts to DEFAULT_LANE. Returns False when
    nothing was there to delete (the endpoint turns that into a 404)."""
    if not get_lane(tenant_id, from_warehouse, to_warehouse):
        return False
    execute(
        """DELETE FROM transfer_lanes
           WHERE tenant_id = %s AND from_warehouse = %s AND to_warehouse = %s""",
        (tenant_id, from_warehouse, to_warehouse),
    )
    return True
