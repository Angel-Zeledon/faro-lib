"""
ROI tracking service for inventory PO generation.

Logs every PO export and provides cumulative ROI metrics so clients
can see the value StockAI has generated for their operation over time.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError
from backend.inventory.warehouse_service import DEFAULT_WAREHOUSE as _DEFAULT_WAREHOUSE

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


def _insert_lines(
    tenant_id: str, header: dict, items: list[dict],
    destination_warehouse: str | None, conn,
) -> None:
    """Every line of a PO, on the caller's transaction.

    Lines without their own warehouse inherit the PO's destination (feature
    5.4); the default warehouse keeps the pre-5.4 behaviour when neither is
    given. `signal` and the decision counters are written for every line,
    including rejected ones, so adoption stays auditable per SKU.
    """
    po_log_id = header["id"]
    for i in items:
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, display_name, supplier,
                    supplier_id, signal, recommended_qty, final_qty,
                    unit_cost, status, warehouse)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (po_log_id, tenant_id, str(i.get("sku") or ""),
             i.get("display_name"), i.get("supplier"),
             i.get("supplier_id"), i.get("signal"),
             float(i.get("recommended_qty") or 0),
             _ordered_qty(i) if i.get("status", "approved") in _ORDERED else 0.0,
             (float(i["unit_cost"]) if i.get("unit_cost") is not None else None),
             i.get("status", "approved"),
             i.get("warehouse") or destination_warehouse or _DEFAULT_WAREHOUSE),
            conn=conn,
        )


# Longest idempotency key accepted. A UUID is 36 characters; the bound only
# stops a client from storing an arbitrary blob in an indexed column.
MAX_IDEMPOTENCY_KEY_LEN = 128


def validate_idempotency_key(key: str | None) -> str | None:
    """Normalize a client-supplied idempotency key (None/blank = no key).

    Refused rather than truncated when too long or not printable ASCII: a key
    silently shortened could collide with a different submission's key and
    answer it with the wrong order.
    """
    if key is None:
        return None
    key = key.strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_LEN or not all(33 <= ord(c) < 127 for c in key):
        raise AppError(
            "po_idempotency_key_invalid",
            f"Idempotency-Key must be 1-{MAX_IDEMPOTENCY_KEY_LEN} printable ASCII characters",
            status_code=422,
            params={"max": MAX_IDEMPOTENCY_KEY_LEN},
        )
    return key


def po_fingerprint(payload: dict) -> str:
    """Stable hash of WHAT was ordered, stored next to the idempotency key.

    Same key + same fingerprint = a replay of the same submission. Same key +
    a different fingerprint = a client bug (a key reused for another order),
    which is refused: answering it with the first order would tell the buyer
    their second order exists when it does not.
    """
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def find_by_idempotency_key(tenant_id: str, key: str) -> dict | None:
    row = query_one(
        "SELECT * FROM inventory_po_log WHERE tenant_id = %s AND idempotency_key = %s",
        (tenant_id, key),
    )
    return dict(row) if row else None


def _replay_or_conflict(existing: dict, fingerprint: str | None) -> dict:
    """The first order written under this key, marked as a replay — or a 409
    when the key arrives with a different order behind it."""
    if fingerprint is not None and existing.get("idempotency_fingerprint") not in (None, fingerprint):
        raise AppError(
            "po_idempotency_key_reused",
            "This idempotency key was already used for a different purchase order",
            status_code=409,
            params={"po_number": format_po_number(existing.get("po_number"),
                                                  str(existing.get("id") or ""))},
        )
    return {**existing, "replayed": True}


def _write_po_atomically(
    insert_header, tenant_id: str, items: list[dict],
    destination_warehouse: str | None,
    idempotency_key: str | None = None,
    fingerprint: str | None = None,
) -> dict:
    """Write a purchase order's header and its lines as ONE unit.

    Before this, the header was an auto-committing INSERT followed by N
    auto-committing line INSERTs, each wrapped in `except: log.warning(...)`.
    A line that failed to write vanished from the supplier's fill rate, from
    `purchased_value` and from the PDF the supplier receives, while the header
    kept its full `sku_count` and `total_value`: the order said twelve lines and
    the database held eleven, with nothing on screen and nothing in the response
    (stability 11.33).

    Failing the whole generation instead would lose the buyer's cart, which is
    why the swallow was there. Inside a transaction neither happens — the header
    rolls back with its lines and the API returns the error, while the cart is
    still in the browser to retry.

    `po_number` is MAX+1 inside the INSERT, so two orders in the same instant
    can collide on the unique index. That is not a failure of this order: the
    losing side retries on a clean transaction (the poisoned one is already
    rolled back and nothing it wrote survived).

    `idempotency_key` (optional) makes a replay return the FIRST order instead
    of writing a second one. The read before the write is only the fast path;
    the guarantee is the partial unique index on (tenant_id, idempotency_key):
    two identical submissions racing both pass the read, and the loser's INSERT
    blocks on the index until the winner commits, then fails with a unique
    violation — which is resolved here by returning the winner's order. The
    loser's transaction rolled back whole, so none of its lines survive.
    """
    if idempotency_key:
        existing = find_by_idempotency_key(tenant_id, idempotency_key)
        if existing:
            return _replay_or_conflict(existing, fingerprint)

    for attempt in (1, 2):
        try:
            with transaction() as conn:
                header = insert_header(conn)
                if header is None:                      # pragma: no cover
                    raise RuntimeError("purchase order header was not returned")
                _insert_lines(tenant_id, header, items, destination_warehouse, conn)
                return dict(header)
        except Exception as exc:
            if getattr(exc, "pgcode", "") != _UNIQUE_VIOLATION:
                raise
            if idempotency_key:
                existing = find_by_idempotency_key(tenant_id, idempotency_key)
                if existing:
                    return _replay_or_conflict(existing, fingerprint)
            if attempt == 2:
                raise
            log.info("po_number race on tenant=%s — retrying once", tenant_id)
    raise RuntimeError("unreachable")                    # pragma: no cover


def log_po_generation(
    tenant_id: str, session_id: str, items: list[dict],
    destination_warehouse: str | None = None,
    decisions_recorded: bool = True,
    idempotency_key: str | None = None,
) -> dict:
    """
    Called every time a user exports a PO.
    Records the buyer's actual decisions per line for ROI / adoption tracking.

    items: list of decision dicts. Each may carry:
        sku, display_name, supplier, signal,
        recommended_qty (what StockAI suggested),
        final_qty        (what the buyer kept),
        unit_cost,
        status ∈ approved | modified | rejected.

    `decisions_recorded=False` is the legacy server-side CSV export: the caller
    sent no per-line decisions, so the endpoint re-derived every actionable line
    and this function's normalizer defaults them all to 'approved'.

    The ORDER is still real and is still recorded — the buyer downloaded a file
    and will act on it. What is NOT real is the adoption reading, so the four
    decision counters stay at 0 for these rows, exactly as `create_manual_po`
    already does for orders written from scratch.

    Without this, a tenant working from /inventario saw 100% adoption, in green,
    permanently: every actionable line was logged as approved because nobody had
    said otherwise, and 'rejected' was unreachable by construction. A download is
    evidence the buyer took the list away, not evidence they agreed with every
    line on it — and the difference is the whole meaning of the metric.

    `idempotency_key`: see `_write_po_atomically`. A replay comes back with
    `replayed: True` and writes nothing.
    """
    # Normalize status + forbid 0-unit orders (see _normalize_decisions).
    norm = _normalize_decisions(items)
    fingerprint = po_fingerprint({
        "kind": "forecast", "session_id": session_id,
        "destination_warehouse": destination_warehouse,
        "decisions_recorded": decisions_recorded,
        "lines": [{k: i.get(k) for k in ("sku", "supplier", "supplier_id", "status",
                                          "final_qty", "recommended_qty", "unit_cost",
                                          "warehouse")}
                  for i in norm],
    }) if idempotency_key else None

    ordered = [i for i in norm if i["status"] in _ORDERED]

    if decisions_recorded:
        suggested_count = len(norm)
        approved_count  = len(ordered)
        modified_count  = sum(1 for i in norm if i["status"] == "modified")
        rejected_count  = sum(1 for i in norm if i["status"] == "rejected")
    else:
        suggested_count = approved_count = modified_count = rejected_count = 0

    # Header aggregates describe the *order* (approved/modified lines only).
    # NOT `approved_count`, which is a DECISION counter and is deliberately 0
    # when no decisions were recorded — the order still has as many lines as it
    # has, and /pedidos reads this to show the buyer their own order.
    sku_count         = len(ordered)
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

    def _insert(conn=None) -> dict | None:
        # po_number is computed inside the INSERT so number and row commit
        # atomically. Volume is human-driven, so MAX+1 contention is rare;
        # the unique index catches the race and we retry once.
        return query_one(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, source, sku_count, total_units, total_value,
                    skus_order_now, skus_order_soon,
                    suggested_count, approved_count, modified_count, rejected_count,
                    destination_warehouse, idempotency_key, idempotency_fingerprint,
                    po_number)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       (SELECT COALESCE(MAX(po_number), 0) + 1
                          FROM inventory_po_log WHERE tenant_id = %s))
               RETURNING *""",
            (tenant_id, session_id,
             # 'forecast' is the column default and means "the buyer decided
             # line by line". 'export' says the rows came from a download with
             # no decisions attached, so a later reader can tell why this order
             # contributes nothing to adoption.
             "forecast" if decisions_recorded else "export",
             sku_count, total_units, total_value,
             skus_order_now, skus_order_soon,
             suggested_count, approved_count, modified_count, rejected_count,
             destination_warehouse, idempotency_key, fingerprint, tenant_id),
            conn=conn,
        )

    # Header and lines commit together or not at all — see _write_po_atomically.
    # The old fallback return (a dict built from the locals, for when the INSERT
    # came back empty) is gone with it: a header that was not written is not an
    # order, and handing one back is what let the caller log a PO that does not
    # exist.
    return _write_po_atomically(_insert, tenant_id, norm, destination_warehouse,
                                idempotency_key=idempotency_key, fingerprint=fingerprint)


def create_manual_po(
    tenant_id: str, supplier: dict, lines: list[dict],
    destination_warehouse: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """
    A purchase order the buyer wrote from scratch — no forecast session behind
    it. Rows carry session_id NULL and source='manual'; adoption counters stay
    at 0 so manual orders never inflate the recommendation-adoption metrics.

    lines: [{sku, qty, unit_cost?, display_name?}], every qty > 0 (validated
    at the API layer).
    """
    total_units = sum(float(l["qty"]) for l in lines)
    value_parts = [
        float(l["qty"]) * float(l["unit_cost"])
        for l in lines if l.get("unit_cost") is not None
    ]
    total_value: float | None = sum(value_parts) if value_parts else None
    fingerprint = po_fingerprint({
        "kind": "manual", "supplier_id": supplier["id"],
        "destination_warehouse": destination_warehouse,
        "lines": [{k: l.get(k) for k in ("sku", "qty", "unit_cost")} for l in lines],
    }) if idempotency_key else None

    def _insert(conn=None) -> dict | None:
        return query_one(
            """INSERT INTO inventory_po_log
                   (tenant_id, session_id, source, sku_count, total_units,
                    total_value, destination_warehouse, idempotency_key,
                    idempotency_fingerprint, po_number)
               VALUES (%s, NULL, 'manual', %s, %s, %s, %s, %s, %s,
                       (SELECT COALESCE(MAX(po_number), 0) + 1
                          FROM inventory_po_log WHERE tenant_id = %s))
               RETURNING *""",
            (tenant_id, len(lines), total_units, total_value,
             destination_warehouse, idempotency_key, fingerprint, tenant_id),
            conn=conn,
        )

    # A manual order is typed line by line, so a line lost on the way to the
    # database is a line the buyer wrote and nobody will ever see again. Same
    # transaction as the forecast path.
    items = [{
        "sku":             str(l["sku"]),
        "display_name":    l.get("display_name"),
        "supplier":        supplier["name"],
        "supplier_id":     supplier["id"],
        "signal":          None,
        "recommended_qty": 0,
        "final_qty":       float(l["qty"]),
        "unit_cost":       l.get("unit_cost"),
        "status":          "approved",
    } for l in lines]
    return _write_po_atomically(_insert, tenant_id, items, destination_warehouse,
                                idempotency_key=idempotency_key, fingerprint=fingerprint)


def get_roi_summary(tenant_id: str) -> dict:
    """
    Returns accumulated ROI metrics across all time for a given tenant.
    """
    # `active_days` counts the days the buyer actually DID something, which is
    # what the screen's "days active" figure claims. It used to be the span
    # between the first and last order, so a tenant who ordered once and came
    # back a year later read "365 days active" after using StockAI on two days. A
    # distinct-day count cannot overstate: it is bounded by the days they showed
    # up.
    #
    # This rationale lives OUT here rather than inside the SQL: the guard in
    # test_no_spanish_in_backend_logic scans string literals, and quoting the
    # screen's own Spanish label inside the query tripped it — correctly, since
    # it cannot tell a comment from copy once both are inside the same string.
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
               MAX(generated_at)                AS last_po_at,
               COUNT(DISTINCT generated_at::date)::int AS active_days
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

    # Counted in SQL as distinct calendar days with a generated order — see the
    # comment on the aggregate. Deliberately NOT the first-to-last span: that
    # number grows while the buyer is away, which is the opposite of what a
    # figure called "days active" is read to mean.
    # Counted in SQL as distinct calendar days with a generated order — see the
    # comment on the aggregate. Deliberately NOT the first-to-last span: that
    # number grows while the buyer is away, which is the opposite of what a
    # figure called "days active" is read to mean.
    active_days = int(agg.get("active_days") or 0) if agg else 0

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
        # `sent_at` rides along so the history can show whether an order was
        # ever sent — and so the screen can offer to undo that send, which it
        # cannot decide without knowing.
        """SELECT id, session_id, source, generated_at, sku_count, total_units,
                  total_value, skus_order_now, skus_order_soon,
                  reception_status, received_at, po_number, sent_at,
                  paid_at, cancelled_at, cancel_reason,
                  destination_warehouse, approval_status, approved_amount
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
        for k in ("generated_at", "received_at", "paid_at", "cancelled_at"):
            if isinstance(r.get(k), datetime):
                r[k] = r[k].isoformat()
        result.append(r)
    return result


PO_FILTERS = ("all", "unpaid", "paid", "cancelled")

# Mirrors `reception_service.RECEIVABLE_STATES` and the frontend's
# `OPEN_RECEPTION_STATUSES`: an order that has not (fully) arrived.
_OPEN_RECEPTION = ("pending", "partial", "not_received")


def get_po_history_page(
    tenant_id: str, limit: int = 50, offset: int = 0,
    status: str = "all", q: Optional[str] = None,
) -> dict:
    """One page of the PO history, filtered and counted on the server.

    `get_po_history` returns the newest N and the screen filtered those N in
    the browser, so a tenant with more orders than N could not reach the rest
    and a filter ("unpaid") silently searched only what happened to be loaded.
    `total` counts the filtered set; `awaiting_reception` counts the whole
    tenant, because the header badge answers a different question than the
    table's filter and must not change with it.
    """
    clauses = ["tenant_id = %s"]
    params: list = [tenant_id]
    if status == "unpaid":
        clauses.append("sent_at IS NOT NULL AND paid_at IS NULL AND cancelled_at IS NULL")
    elif status == "paid":
        clauses.append("paid_at IS NOT NULL")
    elif status == "cancelled":
        clauses.append("cancelled_at IS NOT NULL")
    if q and q.strip():
        clauses.append("(CAST(po_number AS TEXT) ILIKE %s OR id ILIKE %s)")
        like = f"%{q.strip()}%"
        params += [like, like]
    where = " AND ".join(clauses)

    rows = query(
        f"""SELECT id, session_id, source, generated_at, sku_count, total_units,
                   total_value, skus_order_now, skus_order_soon,
                   reception_status, received_at, po_number, sent_at,
                   paid_at, cancelled_at, cancel_reason,
                   destination_warehouse, approval_status, approved_amount
              FROM inventory_po_log
             WHERE {where}
             ORDER BY generated_at DESC, id DESC
             LIMIT %s OFFSET %s""",
        (*params, limit, offset),
    )
    total = query_one(f"SELECT COUNT(*) AS n FROM inventory_po_log WHERE {where}", tuple(params))
    awaiting = query_one(
        """SELECT COUNT(*) AS n FROM inventory_po_log
            WHERE tenant_id = %s AND cancelled_at IS NULL
              AND COALESCE(reception_status, 'pending') IN %s""",
        (tenant_id, _OPEN_RECEPTION),
    )
    items = []
    for row in rows:
        r = dict(row)
        for k in ("generated_at", "received_at", "paid_at", "cancelled_at"):
            if isinstance(r.get(k), datetime):
                r[k] = r[k].isoformat()
        items.append(r)
    return {
        "items": items,
        "total": int(total["n"]) if total else 0,
        "limit": limit, "offset": offset,
        "awaiting_reception": int(awaiting["n"]) if awaiting else 0,
    }


def _next_month_key(key: str) -> str:
    """'2026-06' -> '2026-07'."""
    y, m = (int(p) for p in key.split("-"))
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


# Why `capital_freed` is None, for the UI to say the right sentence.
#   measured     a drop was measured; the amount is in `capital_freed`
#   not_measured one of the two monthly snapshots does not exist
#   grew         both snapshots exist and overstock went UP
CAPITAL_MEASURED = "measured"
CAPITAL_NOT_MEASURED = "not_measured"
CAPITAL_GREW = "grew"


def _capital_freed_during(
    key: str, snap_by_month: dict[str, float],
) -> tuple[float | None, str]:
    """
    Overstock reduction during calendar month `key`, and WHY it is what it is.

    Derived strictly from two measured snapshots: the one opening the month and
    the one opening the next month. No modelling, no assumptions — a difference
    of two measurements.

    The status is the whole point of the second return value. This used to
    answer `None` to two completely different questions — "we never took one of
    the measurements" and "we took both and your overstock GREW" — and the UI,
    having only the None, printed *"Necesitamos dos mediciones mensuales
    seguidas"* for both. A tenant whose dead stock had just grown was told we
    lacked data. The column was structurally incapable of reporting anything but
    good news, which is exactly the shape of a vanity metric.

    An increase still does not go in `capital_freed`: that field is a reduction,
    and a negative reduction is a different quantity wearing its name.
    """
    nxt = _next_month_key(key)
    if key not in snap_by_month or nxt not in snap_by_month:
        return None, CAPITAL_NOT_MEASURED
    delta = snap_by_month[key] - snap_by_month[nxt]
    if delta > 0:
        return round(delta, 2), CAPITAL_MEASURED
    return None, CAPITAL_GREW


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

        capital_freed, capital_status = _capital_freed_during(key, snap_by_month)

        result.append({
            "month":            key,
            "pos_count":        pos_count,
            "skus_order_now":    skus_order_now,
            "total_value":      round(total_value, 2),
            "adoption_rate":    adoption_rate,
            "capital_freed":    capital_freed,
            "capital_freed_status": capital_status,
        })

    result.reverse()  # most recent first
    return result


# ── Monthly recap ("what StockAI did for you last month") ────────────────────────
#
# Provenance of every figure below. Each one is a straight aggregation of rows
# the product already writes; none is modelled, extrapolated or assumed.
#
#   orders_generated        COUNT(inventory_po_log) in the month.
#   recommendations_shown   SUM(suggested_count) — lines that reached this log,
#                           i.e. lines the buyer DECIDED on. Recommendations
#                           they never acted on are not recorded anywhere, so
#                           this is not "everything StockAI put in front of them"
#                           and the copy must not claim it is. Counting those
#                           would mean persisting what was displayed, which the
#                           product does not do.
#   recommendations_followed SUM(approved_count)  — lines kept or modified.
#   adoption_rate           followed / shown, None when nothing was shown.
#   stockout_risks_handled  SUM(skus_order_now) over ordered lines: PEDIR_YA
#                           LINES the buyer actually ordered — lines, not
#                           distinct SKUs, so the same 30 urgent products
#                           ordered monthly for a year sum to 360. This is NOT
#                           "stockouts avoided" (we never observe the
#                           counterfactual) and NOT "handled on time" (nothing
#                           here checks the order arrived).
#   managed_purchase_value  SUM(total_value), i.e. units x unit cost. None (not
#                           0) when no line carried a unit cost, so a tenant
#                           without cost data is told the figure is unavailable
#                           instead of being shown a fake zero.
#   managed_purchase_value_complete
#                           False when only SOME ordered lines carried a cost,
#                           which makes the figure above a floor. Without it a
#                           partial sum was indistinguishable from a total.
#   capital_freed           See _capital_freed_during: difference of two
#                           measured overstock snapshots. NOT attributable to
#                           StockAI — overstock also falls on sales, shrinkage,
#                           SKU deletion and retraining — so the copy says what
#                           moved, not who moved it.
#   capital_freed_status    Why capital_freed is what it is: measured /
#                           not_measured / grew. A single None conflated "no
#                           measurement" with "your overstock went up".
#
# Deliberately NOT computed: any single "StockAI saved you $X" headline, and any
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

    # How much of the month's ordered volume the money figure above actually
    # covers. `total_value` per PO sums only the lines that carried a unit cost
    # — a NULL cost annuls the product — so 40 ordered lines with 6 costed
    # reported those 6 as the month's managed purchasing, and it looked exact:
    # ₡30M could read as ₡2,1M. The all-missing case was already handled (None,
    # not 0); the PARTIAL case was not, and partial is the common one while a
    # tenant is still filling in costs.
    #
    # Derived from the line table at read time rather than stored on the header:
    # `inventory_po_items` already carries every line's unit_cost, so this needs
    # no column and no backfill, and it cannot drift from the value it qualifies.
    coverage = query_one(
        """SELECT COUNT(*)::int              AS n_lines,
                  COUNT(poi.unit_cost)::int  AS n_lines_costed
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
           WHERE poi.tenant_id = %s
             AND poi.status IN ('approved', 'modified')
             AND pol.generated_at >= %s AND pol.generated_at < %s""",
        (tenant_id, start, end),
    ) or {}
    n_lines = int(coverage.get("n_lines") or 0)
    n_costed = int(coverage.get("n_lines_costed") or 0)
    managed_value_complete = bool(n_lines > 0 and n_costed == n_lines)

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
    capital_freed, capital_status = _capital_freed_during(key, snap_by_month)

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
            "managed_purchase_value_complete": False,
            "capital_freed": capital_freed,
            "capital_freed_status": capital_status,
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
        "managed_purchase_value_complete": managed_value_complete,
        "capital_freed": capital_freed,
        "capital_freed_status": capital_status,
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
        get_tenant_alert_recipients,
        get_tenants_with_active_sessions,
        record_digest_withheld,
        record_notification_delivery,
    )
    from backend.notifications import email as email_mod
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

            # Company-wide money: active, unscoped admins/analysts only (the
            # /impacto screen refuses a warehouse-scoped user the same figures).
            emails = get_tenant_admin_emails(tid)
            record_digest_withheld(tid, "monthly_roi")
            if not emails:
                continue

            # Recipient → user id, so a failed recap lands in that user's own
            # activity feed. An address with no user row (only reachable when
            # the recipient list is stubbed) is still mailed, just unattributed.
            user_id_by_email = {
                r["email"]: r["id"] for r in get_tenant_alert_recipients(tid) if r.get("email")
            }

            # One read per tenant, not per recipient: every admin of the same
            # company reads the same money in the same currency.
            from backend.api.v1.currency import currency_of
            tenant_currency = currency_of(tid)

            delivered = 0
            for email in emails:
                ok_sent = send_monthly_roi_email(to=email, report=report, roi_url=roi_url,
                                                 currency=tenant_currency,
                                                 tenant_id=tid)
                if ok_sent:
                    delivered += 1
                else:
                    log.warning("roi_email not delivered to=%s", email)
                uid = user_id_by_email.get(email)
                if uid:
                    record_notification_delivery(
                        tid, uid, "monthly_roi_email", ok_sent,
                        context={
                            "channel": "email",
                            "recipient": email,
                            "month": month_key,
                            # Tenant-scoped: see the note in service.py — the
                            # bare call reads the instance config and can name
                            # the wrong cause.
                            **({} if ok_sent else {"reason": email_mod.failure_reason(tid)}),
                        },
                    )

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
