"""
ROI tracking service for inventory PO generation.

Logs every PO export and provides cumulative ROI metrics so clients
can see the value Faro has generated for their operation over time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Statuses that mean "the buyer decided to order this line".
_ORDERED = ("approved", "modified")

# SQLSTATE for unique_violation — the losing side of a po_number race.
_UNIQUE_VIOLATION = "23505"


def format_po_number(po_number: int | None, fallback: str) -> str:
    """Human-readable order reference (OC-000123); raw id when unnumbered."""
    return f"OC-{int(po_number):06d}" if po_number else fallback


def _ordered_qty(item: dict) -> float:
    """
    Units actually ordered for a line. When the buyer kept/modified the line we
    use final_qty; we fall back to recommended_qty for legacy callers
    that don't send a final quantity.
    """
    if item.get("final_qty") is not None:
        return float(item.get("final_qty") or 0)
    return float(item.get("recommended_qty") or 0)


def _normalize_decisions(items: list[dict]) -> list[dict]:
    """
    Normalize each buyer decision's status and forbid 0-unit orders.

    A line marked approved/modified but with an ordered quantity <= 0 is not a
    real order (it would create a purchase-order line for 0 units), so it is
    downgraded to 'rejected'. This is the single guard every PO path passes
    through, including the direct API.
    """
    norm: list[dict] = []
    for i in items:
        status = (i.get("status") or "approved").lower()
        if status not in ("approved", "modified", "rejected"):
            status = "approved"
        if status in _ORDERED and _ordered_qty(i) <= 0:
            status = "rejected"
        norm.append({**i, "status": status})
    return norm


def log_po_generation(
    tenant_id: str, session_id: str, items: list[dict],
    destination_warehouse: str | None = None,
) -> dict:
    """
    Called every time a user exports a PO.
    Records the buyer's actual decisions per line for ROI / adoption tracking.

    items: list of decision dicts. Each may carry:
        sku, display_name, supplier, signal,
        recommended_qty (what Faro suggested),
        final_qty        (what the buyer kept),
        unit_cost,
        status ∈ approved | modified | rejected.

    Legacy callers (server-side CSV export) pass status-less items already
    filtered to the order; those are treated as 'approved'.
    """
    # Normalize status + forbid 0-unit orders (see _normalize_decisions).
    norm = _normalize_decisions(items)

    ordered = [i for i in norm if i["status"] in _ORDERED]

    suggested_count = len(norm)
    approved_count  = len(ordered)
    modified_count  = sum(1 for i in norm if i["status"] == "modified")
    rejected_count  = sum(1 for i in norm if i["status"] == "rejected")

    # Header aggregates describe the *order* (approved/modified lines only).
    sku_count         = approved_count
    total_units       = sum(_ordered_qty(i) for i in ordered)
    skus_order_now     = sum(1 for i in ordered if i.get("signal") == "PEDIR_YA")
    skus_order_soon = sum(1 for i in ordered if i.get("signal") == "PEDIR_PRONTO")

    # total_value: sum of (units ordered × unit_cost) where cost is available.
    value_parts: list[float] = []
    for i in ordered:
        cost = i.get("unit_cost")
        if cost is not None:
            value_parts.append(_ordered_qty(i) * float(cost))
    total_value: float | None = sum(value_parts) if value_parts else None

    def _insert() -> dict | None:
        # po_number is computed inside the INSERT so number and row commit
        # atomically. Volume is human-driven, so MAX+1 contention is rare;
        # the unique index catches the race and we retry once.
        return query_one(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, sku_count, total_units, total_value,
                    skus_order_now, skus_order_soon,
                    suggested_count, approved_count, modified_count, rejected_count,
                    destination_warehouse, po_number)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       (SELECT COALESCE(MAX(po_number), 0) + 1
                          FROM inventory_po_log WHERE tenant_id = %s))
               RETURNING *""",
            (tenant_id, session_id, sku_count, total_units, total_value,
             skus_order_now, skus_order_soon,
             suggested_count, approved_count, modified_count, rejected_count,
             destination_warehouse, tenant_id),
        )

    try:
        inserted = _insert()
    except Exception as exc:
        if getattr(exc, "pgcode", "") != _UNIQUE_VIOLATION:
            raise
        inserted = _insert()

    # Persist every line (including rejected) so adoption is auditable per SKU.
    if inserted and norm:
        po_log_id = inserted["id"]
        for i in norm:
            try:
                execute(
                    """INSERT INTO inventory_po_items
                           (po_log_id, tenant_id, sku, display_name, supplier,
                            signal, recommended_qty, final_qty,
                            unit_cost, status, warehouse)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (po_log_id, tenant_id, str(i.get("sku") or ""),
                     i.get("display_name"), i.get("supplier"), i.get("signal"),
                     float(i.get("recommended_qty") or 0),
                     _ordered_qty(i) if i["status"] in _ORDERED else 0.0,
                     (float(i["unit_cost"]) if i.get("unit_cost") is not None else None),
                     i["status"],
                     # Lines without their own warehouse inherit the PO's
                     # destination (feature 5.4); 'principal' keeps the
                     # pre-5.4 behavior when neither is given.
                     i.get("warehouse") or destination_warehouse or "principal"),
                )
            except Exception as e:
                log.warning("log_po_generation: skipped line sku=%s err=%s", i.get("sku"), e)

    if inserted:
        return dict(inserted)
    return {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "sku_count": sku_count,
        "total_units": total_units,
        "total_value": total_value,
        "skus_order_now": skus_order_now,
        "skus_order_soon": skus_order_soon,
        "suggested_count": suggested_count,
        "approved_count": approved_count,
        "modified_count": modified_count,
        "rejected_count": rejected_count,
    }


def get_roi_summary(tenant_id: str) -> dict:
    """
    Returns accumulated ROI metrics across all time for a given tenant.
    """
    agg = query_one(
        """SELECT
               COUNT(*)::int                    AS total_pos_generated,
               COALESCE(SUM(skus_order_now), 0)::int  AS total_skus_protected,
               COALESCE(SUM(total_units), 0)    AS total_units_ordered,
               COALESCE(SUM(total_value), 0)    AS estimated_value_protected,
               COALESCE(SUM(suggested_count), 0)::int AS total_suggested,
               COALESCE(SUM(approved_count), 0)::int  AS total_approved,
               COALESCE(SUM(rejected_count), 0)::int  AS total_rejected,
               MIN(generated_at)                AS first_po_at,
               MAX(generated_at)                AS last_po_at
           FROM inventory_po_log
           WHERE tenant_id = %s""",
        (tenant_id,),
    )

    now = datetime.now(tz=timezone.utc)

    # Month boundaries (UTC)
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Last month start/end
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)

    this_month_count_row = query_one(
        "SELECT COUNT(*)::int AS cnt FROM inventory_po_log WHERE tenant_id = %s AND generated_at >= %s",
        (tenant_id, this_month_start),
    )
    last_month_count_row = query_one(
        """SELECT COUNT(*)::int AS cnt FROM inventory_po_log
           WHERE tenant_id = %s AND generated_at >= %s AND generated_at < %s""",
        (tenant_id, last_month_start, this_month_start),
    )

    first_po_at = agg.get("first_po_at") if agg else None
    last_po_at  = agg.get("last_po_at")  if agg else None

    active_days = 0
    if first_po_at and last_po_at and first_po_at != last_po_at:
        # Both may be timezone-aware or naive depending on DB config; normalise to UTC
        def _to_dt(v):
            if isinstance(v, datetime):
                return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
            return datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc)
        active_days = max(0, (_to_dt(last_po_at) - _to_dt(first_po_at)).days)

    total_suggested = int(agg.get("total_suggested") or 0) if agg else 0
    total_approved  = int(agg.get("total_approved")  or 0) if agg else 0
    total_rejected  = int(agg.get("total_rejected")  or 0) if agg else 0
    # Adoption rate: of the recommendations the buyer actually acted on, what
    # share did they keep/order? Only defined once we have decision data — older
    # rows (pre-decision-tracking) contribute 0 to both sides and don't distort it.
    adoption_rate = (total_approved / total_suggested) if total_suggested > 0 else None

    return {
        "total_pos_generated":      int(agg.get("total_pos_generated") or 0) if agg else 0,
        "total_skus_protected":     int(agg.get("total_skus_protected") or 0) if agg else 0,
        "total_units_ordered":      float(agg.get("total_units_ordered") or 0) if agg else 0.0,
        "estimated_value_protected": float(agg.get("estimated_value_protected") or 0) if agg else 0.0,
        "total_suggested":          total_suggested,
        "total_approved":           total_approved,
        "total_rejected":           total_rejected,
        "adoption_rate":            adoption_rate,
        "first_po_at":              first_po_at.isoformat() if isinstance(first_po_at, datetime) else (str(first_po_at) if first_po_at else None),
        "last_po_at":               last_po_at.isoformat()  if isinstance(last_po_at,  datetime) else (str(last_po_at)  if last_po_at  else None),
        "active_days":              active_days,
        "pos_this_month":           int(this_month_count_row.get("cnt") or 0) if this_month_count_row else 0,
        "pos_last_month":           int(last_month_count_row.get("cnt") or 0) if last_month_count_row else 0,
    }


def get_po_history(tenant_id: str, limit: int = 20) -> list[dict]:
    """Returns recent PO generation events for the history panel."""
    rows = query(
        """SELECT id, session_id, generated_at, sku_count, total_units,
                  total_value, skus_order_now, skus_order_soon,
                  reception_status, received_at, po_number
           FROM inventory_po_log
           WHERE tenant_id = %s
           ORDER BY generated_at DESC
           LIMIT %s""",
        (tenant_id, limit),
    )
    result = []
    for row in rows:
        r = dict(row)
        # Serialize datetimes to ISO strings for JSON
        for k in ("generated_at", "received_at"):
            if isinstance(r.get(k), datetime):
                r[k] = r[k].isoformat()
        result.append(r)
    return result


def _next_month_key(key: str) -> str:
    """'2026-06' -> '2026-07'."""
    y, m = (int(p) for p in key.split("-"))
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _capital_freed_during(key: str, snap_by_month: dict[str, float]) -> float | None:
    """
    Overstock capital freed during calendar month `key`, in currency units.

    Derived strictly from two measured snapshots: the one opening the month and
    the one opening the next month. Returns None when either snapshot is
    missing (we cannot measure a change we never observed) or when overstock
    grew instead of shrinking — an increase is not a saving, and reporting it as
    0 would read as "we saved nothing this month" rather than "this did not
    happen". No modelling, no assumptions: it is a difference of two
    measurements.
    """
    nxt = _next_month_key(key)
    if key not in snap_by_month or nxt not in snap_by_month:
        return None
    delta = snap_by_month[key] - snap_by_month[nxt]
    return round(delta, 2) if delta > 0 else None


def get_monthly_summary(tenant_id: str, months: int = 6) -> list[dict]:
    """
    Last `months` calendar months (most recent first): orders generated,
    stockout risks acted on, managed value, adoption rate, and overstock
    capital freed.

    Capital-freed attribution: snapshots are taken on day 1 of each month, so
    the snapshot stamped month M measures the overstock *at the start* of M.
    The reduction that happened *during* M is therefore
    snapshot(M) - snapshot(M+1) — not snapshot(M-1) - snapshot(M). Every other
    figure in a row describes activity during M, so the capital figure has to
    describe the same window or the row mixes two different months.
    Stays None until two consecutive snapshots exist, and when overstock grew.
    """
    now = datetime.now(tz=timezone.utc)
    month_starts: list[datetime] = []
    y, m = now.year, now.month
    for _ in range(months):
        month_starts.append(datetime(y, m, 1, tzinfo=timezone.utc))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    month_starts.sort()  # oldest first

    po_rows = query(
        """SELECT date_trunc('month', generated_at AT TIME ZONE 'UTC') AS month,
                  COUNT(*)::int                          AS pos_count,
                  COALESCE(SUM(skus_order_now), 0)::int    AS skus_order_now,
                  COALESCE(SUM(total_value), 0)           AS total_value,
                  COALESCE(SUM(suggested_count), 0)::int  AS total_suggested,
                  COALESCE(SUM(approved_count), 0)::int   AS total_approved
           FROM inventory_po_log
           WHERE tenant_id = %s AND generated_at >= %s
           GROUP BY month""",
        (tenant_id, month_starts[0]),
    )
    po_by_month = {r["month"].strftime("%Y-%m"): r for r in po_rows}

    snap_rows = query(
        """SELECT date_trunc('month', recorded_at AT TIME ZONE 'UTC') AS month,
                  AVG(overstock_value) AS overstock_value
           FROM inventory_overstock_snapshots
           WHERE tenant_id = %s
           GROUP BY month""",
        (tenant_id,),
    )
    snap_by_month = {r["month"].strftime("%Y-%m"): float(r["overstock_value"]) for r in snap_rows}

    result: list[dict] = []
    for start in month_starts:
        key = start.strftime("%Y-%m")
        po = po_by_month.get(key)
        pos_count       = int(po["pos_count"]) if po else 0
        skus_order_now   = int(po["skus_order_now"]) if po else 0
        total_value     = float(po["total_value"]) if po else 0.0
        total_suggested = int(po["total_suggested"]) if po else 0
        total_approved  = int(po["total_approved"]) if po else 0
        adoption_rate = (total_approved / total_suggested) if total_suggested > 0 else None

        capital_liberado = _capital_freed_during(key, snap_by_month)

        result.append({
            "month":            key,
            "pos_count":        pos_count,
            "skus_order_now":    skus_order_now,
            "total_value":      round(total_value, 2),
            "adoption_rate":    adoption_rate,
            "capital_liberado": capital_liberado,
        })

    result.reverse()  # most recent first
    return result


# ── Monthly recap ("what Faro did for you last month") ────────────────────────
#
# Provenance of every figure below. Each one is a straight aggregation of rows
# the product already writes; none is modelled, extrapolated or assumed.
#
#   orders_generated        COUNT(inventory_po_log) in the month.
#   recommendations_shown   SUM(suggested_count)  — lines Faro put in the cart.
#   recommendations_followed SUM(approved_count)  — lines kept or modified.
#   adoption_rate           followed / shown, None when nothing was shown.
#   stockout_risks_handled  SUM(skus_order_now) over ordered lines: PEDIR_YA SKUs
#                           the buyer actually ordered. This is NOT "stockouts
#                           avoided" — we never observe the counterfactual, so
#                           we count the action taken, not an averted outcome.
#   managed_purchase_value  SUM(total_value), i.e. units x unit cost. None (not
#                           0) when no line carried a unit cost, so a tenant
#                           without cost data is told the figure is unavailable
#                           instead of being shown a fake zero.
#   capital_freed           See _capital_freed_during: difference of two
#                           measured overstock snapshots.
#
# Deliberately NOT computed: any single "Faro saved you $X" headline, and any
# count of "stockouts avoided". Both require assumptions we cannot ground in
# tenant data (lost margin per stockout, holding-cost rate, the counterfactual
# of not ordering). Inventing them would put an unfalsifiable number in front of
# the customer's boss.

_MIN_ORDERS_FOR_REPORT = 1


def get_month_report(tenant_id: str, year: int, month: int) -> dict:
    """
    Recap of one calendar month for a tenant.

    `has_sufficient_history` is False when the tenant generated no purchase
    order in the month. In that case every metric is None and callers must show
    an honest "not enough history yet" state — never zeros dressed up as
    achievements. The monthly email is skipped entirely for such tenants.
    """
    key = f"{year}-{month:02d}"
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
    end = datetime(nxt_y, nxt_m, 1, tzinfo=timezone.utc)

    agg = query_one(
        """SELECT COUNT(*)::int                          AS orders_generated,
                  COALESCE(SUM(suggested_count), 0)::int  AS recommendations_shown,
                  COALESCE(SUM(approved_count), 0)::int   AS recommendations_followed,
                  COALESCE(SUM(skus_order_now), 0)::int    AS stockout_risks_handled,
                  SUM(total_value)                        AS managed_purchase_value
           FROM inventory_po_log
           WHERE tenant_id = %s AND generated_at >= %s AND generated_at < %s""",
        (tenant_id, start, end),
    ) or {}

    orders_generated = int(agg.get("orders_generated") or 0)

    # Overstock snapshots opening this month and the next one.
    snap_rows = query(
        """SELECT date_trunc('month', recorded_at AT TIME ZONE 'UTC') AS month,
                  AVG(overstock_value) AS overstock_value
           FROM inventory_overstock_snapshots
           WHERE tenant_id = %s AND recorded_at >= %s AND recorded_at < %s
           GROUP BY month""",
        (tenant_id, start, datetime(
            nxt_y + 1 if nxt_m == 12 else nxt_y,
            1 if nxt_m == 12 else nxt_m + 1, 1, tzinfo=timezone.utc,
        )),
    )
    snap_by_month = {
        r["month"].strftime("%Y-%m"): float(r["overstock_value"]) for r in snap_rows
    }
    capital_freed = _capital_freed_during(key, snap_by_month)

    if orders_generated < _MIN_ORDERS_FOR_REPORT:
        return {
            "month": key,
            "has_sufficient_history": False,
            "orders_generated": 0,
            "recommendations_shown": 0,
            "recommendations_followed": 0,
            "adoption_rate": None,
            "stockout_risks_handled": None,
            "managed_purchase_value": None,
            "capital_freed": capital_freed,
        }

    shown    = int(agg.get("recommendations_shown") or 0)
    followed = int(agg.get("recommendations_followed") or 0)
    raw_value = agg.get("managed_purchase_value")

    return {
        "month": key,
        "has_sufficient_history": True,
        "orders_generated": orders_generated,
        "recommendations_shown": shown,
        "recommendations_followed": followed,
        "adoption_rate": (followed / shown) if shown > 0 else None,
        "stockout_risks_handled": int(agg.get("stockout_risks_handled") or 0),
        "managed_purchase_value": (
            round(float(raw_value), 2) if raw_value is not None else None
        ),
        "capital_freed": capital_freed,
    }


def previous_month(now: datetime) -> tuple[int, int]:
    """(year, month) of the calendar month that closed before `now`."""
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def run_monthly_roi_emails(now: datetime | None = None) -> int:
    """
    Send the previous month's recap to every tenant that has enough history.

    Called on the 1st of each month by the worker, right after the overstock
    snapshot: that snapshot is what closes the previous month's capital-freed
    figure, so the order matters.

    Tenants without a single purchase order in the month are skipped — they get
    no email at all rather than a recap full of zeros. Sends are deduped through
    inventory_roi_email_log so a worker restart cannot mail anyone twice.
    Returns the number of tenants actually mailed.
    """
    from backend.config import settings
    from backend.inventory.service import (
        get_tenant_admin_emails,
        get_tenants_with_active_sessions,
    )
    from backend.notifications.email import send_monthly_roi_email

    now = now or datetime.now(tz=timezone.utc)
    year, month = previous_month(now)
    month_key = f"{year}-{month:02d}"

    app_url = getattr(settings, "frontend_url", "http://localhost:3000")
    roi_url = f"{app_url}/inventory/roi"

    tenants = get_tenants_with_active_sessions()
    log.info("roi_email: checking %d tenants for %s", len(tenants), month_key)

    sent_count = 0
    for tenant in tenants:
        tid = tenant["tenant_id"]
        try:
            already = query_one(
                "SELECT id FROM inventory_roi_email_log WHERE tenant_id = %s AND month = %s",
                (tid, month_key),
            )
            if already:
                continue

            report = get_month_report(tid, year, month)
            if not report["has_sufficient_history"]:
                continue

            emails = get_tenant_admin_emails(tid)
            if not emails:
                continue

            delivered = 0
            for email in emails:
                try:
                    if send_monthly_roi_email(to=email, report=report, roi_url=roi_url):
                        delivered += 1
                except Exception as e:
                    log.warning("roi_email failed to=%s: %s", email, e)

            if delivered:
                execute(
                    """INSERT INTO inventory_roi_email_log (tenant_id, month, recipients)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (tenant_id, month) DO NOTHING""",
                    (tid, month_key, delivered),
                )
                sent_count += 1
        except Exception as e:
            log.error("roi_email: tenant=%s error=%s", tid, e)

    return sent_count
