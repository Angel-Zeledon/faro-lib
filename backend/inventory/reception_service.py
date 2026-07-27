"""
PO reception (feature 1.4, docs/features_propuestas_faro_2026-07-05.md).

Closes the purchase loop: when the order physically arrives, the buyer records
what came in. Two effects that compound Faro's value over time:
  1. Stock self-corrects (received units are added to inventory_stock), so the
     semáforo keeps matching reality without manual stock edits.
  2. Faro learns each supplier's REAL lead time (order date → reception date),
     turning the user's guess into observed data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError

log = logging.getLogger(__name__)

# Same fallback used in service.py's stock signal calc when a SKU has no
# lead_time_days on file — keeps the "expected arrival" guess consistent
# with the rest of the app when a supplier has no data at all.
_DEFAULT_LEAD_TIME_DAYS = 15.0

# Line statuses that were actually ordered (mirrors roi_service._ORDERED)
_ORDERED = ("approved", "modified")

RECEIVABLE_STATES = ("pending", "partial")


def _line_warehouse(item: dict, po: dict) -> str:
    """A PO line lands in its own warehouse if set, else the PO's destination
    warehouse (feature 5.4), else the historical default."""
    from backend.inventory.warehouse_service import DEFAULT_WAREHOUSE
    return item.get("warehouse") or po.get("destination_warehouse") or DEFAULT_WAREHOUSE


def get_po(tenant_id: str, po_log_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
        (po_log_id, tenant_id),
    )


def get_po_items(tenant_id: str, po_log_id: str, conn: Optional[Any] = None) -> list[dict]:
    """
    `conn`: optional shared connection from db.connection.transaction() — see
    receive_po, which reads its own just-written (still uncommitted)
    received_qty updates back through this function inside its transaction.
    """
    return query(
        """SELECT id, sku, display_name, supplier, supplier_id, signal, status,
                  recommended_qty, final_qty, received_qty, unit_cost,
                  warehouse
           FROM inventory_po_items
           WHERE po_log_id = %s AND tenant_id = %s
           ORDER BY supplier NULLS LAST, sku""",
        (po_log_id, tenant_id),
        conn=conn,
    )


def mark_po_sent(tenant_id: str, po_log_id: str) -> None:
    """
    Stamps when a PO actually reached a supplier — the moment the payment clock
    starts, and therefore the anchor the cash calendar (feature 3.6) dates every
    invoice from.

    `sent_at IS NULL` in the WHERE clause makes this first-write-wins: resending
    a PO (a supplier lost the email, a second supplier on the same order) must
    not move the due date of an invoice already issued against the first send.
    """
    execute(
        """UPDATE inventory_po_log
              SET sent_at = NOW()
            WHERE id = %s AND tenant_id = %s AND sent_at IS NULL""",
        (po_log_id, tenant_id),
    )


def receive_po(
    tenant_id: str,
    po_log_id: str,
    user_id: str,
    lines: Optional[list[dict]] = None,
    received_at: Optional[datetime] = None,
) -> dict:
    """
    Record a reception for a PO.

    lines: [{sku, received_qty}] — omit entirely (or None) to mean
           "everything arrived complete" (each ordered line receives its
           final_qty). Lines not mentioned receive 0 for this event.

    Raises AppError (code + params + English fallback) on invalid state/input.
    """
    po = get_po(tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    if po.get("reception_status") not in RECEIVABLE_STATES:
        status = po.get("reception_status")
        raise AppError(
            "reception_already_received",
            f"This order was already received (status: {status})",
            status_code=409,
            params={"status": status},
        )

    items = get_po_items(tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in _ORDERED]
    if not ordered:
        raise AppError(
            "reception_no_ordered_lines",
            "This order has no ordered lines to receive",
        )

    received_at = received_at or datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    generated_at = po["generated_at"]
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    # Compare by calendar day: a date-only reception (midnight) on the same day
    # the PO was generated is valid even if the PO has a later timestamp.
    if received_at.date() < generated_at.date():
        raise AppError(
            "reception_date_before_order",
            "The reception date cannot be earlier than the order date",
        )

    # Resolve received qty per ordered PO LINE (keyed by item id, not sku —
    # a PO can carry the same SKU on separate lines for different warehouses,
    # and each line has its own final_qty/warehouse).
    if lines is None:
        # "Everything arrived complete" books only what is still OUTSTANDING
        # per line (ordered minus already received), not the full final_qty.
        # After a partial reception, re-booking final_qty would double-count
        # the units already received and push received_qty > final_qty (the
        # over-receipt QA reproduced via "Llegó todo completo"). Mirrors the
        # cap the explicit-lines branch already applies.
        received_by_item = {
            i["id"]: max(0.0, float(i["final_qty"] or 0) - float(i.get("received_qty") or 0))
            for i in ordered
        }
    else:
        received_by_item = {}
        sku_to_items: dict[str, list[dict]] = {}
        for i in ordered:
            sku_to_items.setdefault(i["sku"], []).append(i)
        for ln in lines:
            sku = str(ln.get("sku") or "")
            matches = sku_to_items.get(sku)
            if not matches:
                raise AppError(
                    "reception_sku_not_in_order",
                    f"SKU '{sku}' is not in this order",
                    params={"sku": sku},
                )
            if len(matches) > 1:
                # The {sku, received_qty} line shape can't disambiguate
                # which warehouse's line the quantity belongs to.
                raise AppError(
                    "reception_sku_multiple_warehouses",
                    f"SKU '{sku}' appears in more than one warehouse on this order; "
                    "it cannot be received by SKU, record the full reception instead",
                    params={"sku": sku},
                )
            qty = float(ln.get("received_qty") or 0)
            if qty < 0:
                raise AppError(
                    "reception_negative_qty",
                    f"Negative received quantity for '{sku}'",
                    params={"sku": sku},
                )
            # Cap at what is still outstanding on this line (ordered minus
            # already received). Without this, a reception could book more
            # units than were ordered and inflate stock arbitrarily — QA
            # received 5000 against a line of 312. Partial receptions
            # accumulate, so the bound is per remaining, not per final_qty.
            item = matches[0]
            outstanding = float(item["final_qty"] or 0) - float(item.get("received_qty") or 0)
            if qty > outstanding:
                raise AppError(
                    "reception_over_pending",
                    f"Received quantity of '{sku}' ({qty:g}) exceeds the amount "
                    f"pending on this order ({outstanding:g})",
                    params={"sku": sku, "qty": qty, "pending": outstanding},
                )
            received_by_item[item["id"]] = qty
        for i in ordered:
            received_by_item.setdefault(i["id"], 0.0)

    # Pre-check max_locations BEFORE any write. A PO line whose warehouse has
    # no existing inventory_stock row yet will, further down, create one via
    # inv_svc.upsert_stock -> _ensure_warehouse — the same auto-create bypass
    # that exists on the direct stock-write endpoints. Computing the distinct
    # NEW warehouse names up front and enforcing here (before step 1 touches
    # inventory_po_items) keeps this reception all-or-nothing: a blocked
    # reception must not leave received_qty partially accumulated.
    from backend.entitlements.service import enforce_limit
    from backend.inventory import warehouse_service as wh_svc
    existing_wh_names = wh_svc.list_warehouse_names(tenant_id)
    new_wh_names = {
        _line_warehouse(i, po)
        for i in ordered
        if received_by_item[i["id"]] > 0
    } - existing_wh_names
    if new_wh_names:
        enforce_limit(tenant_id, "max_locations", wh_svc.count_warehouses(tenant_id), adding=len(new_wh_names))

    # Pre-check max_skus BEFORE any write, mirroring the max_locations pre-check
    # above. A reception line whose (sku, warehouse) pair has no existing
    # inventory_stock row is a NEW row that step 2 below will create via
    # inv_svc.upsert_stock -> the same auto-create chokepoint that PUT/PATCH
    # /stock and bulk import go through. Computing the distinct NEW (sku,
    # warehouse) pairs up front and enforcing here (before step 1 touches
    # inventory_po_items) keeps this reception all-or-nothing: a blocked
    # reception must not leave received_qty partially accumulated. Uses the
    # same list_stock_keys/count_stock helpers the dataset-sync/bulk pre-loop
    # checks use, and the same warehouse-default handling ('principal') as
    # the rest of this function and upsert_stock, so "new pair" detection here
    # matches exactly what upsert_stock would actually insert.
    from backend.inventory import service as inv_svc
    existing_stock_keys = inv_svc.list_stock_keys(tenant_id)
    new_sku_warehouse_pairs = {
        (i["sku"], _line_warehouse(i, po))
        for i in ordered
        if received_by_item[i["id"]] > 0
    } - existing_stock_keys
    if new_sku_warehouse_pairs:
        enforce_limit(
            tenant_id, "max_skus", inv_svc.count_stock(tenant_id),
            adding=len(new_sku_warehouse_pairs),
        )

    # Steps 1-4 below all run inside ONE transaction so the whole reception is
    # all-or-nothing: a failure anywhere in the sequence must leave
    # received_qty, stock, the snapshot, the PO header and the lead-time
    # observations exactly as they were before this call — not partially
    # applied. Every write (and every read that needs to see this
    # transaction's own not-yet-committed writes) passes `conn` through;
    # reads that only need already-committed data (e.g. supplier_service
    # lookups elsewhere) don't need it.
    with transaction() as conn:
        # 1. Per-line: accumulate received_qty (partial receptions add up)
        for i in ordered:
            qty = received_by_item[i["id"]]
            execute(
                """UPDATE inventory_po_items
                   SET received_qty = COALESCE(received_qty, 0) + %s
                   WHERE id = %s AND tenant_id = %s""",
                (qty, i["id"], tenant_id),
                conn=conn,
            )

        # 2. Stock: add received units. Creates the stock row if the SKU/warehouse
        # combination is new. The existence check and the update/insert below
        # all target the SAME warehouse — checking one warehouse's presence but
        # writing to another would silently drop the received units.
        for i in ordered:
            qty = received_by_item[i["id"]]
            if qty <= 0:
                continue
            warehouse = _line_warehouse(i, po)
            existing = inv_svc.get_stock(tenant_id, i["sku"], warehouse=warehouse, conn=conn)
            if existing:
                execute(
                    """UPDATE inventory_stock
                       SET current_stock = current_stock + %s, updated_at = NOW()
                       WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                    (qty, tenant_id, i["sku"], warehouse),
                    conn=conn,
                )
            else:
                inv_svc.upsert_stock(tenant_id, i["sku"], {
                    "current_stock": qty,
                    "display_name": i.get("display_name"),
                    "supplier": i.get("supplier"),
                    "warehouse": warehouse,
                }, conn=conn)
            # Point-in-time snapshot so /stock/{sku}/history reflects the arrival.
            # Runs on the SAME transaction connection as everything else in this
            # reception: unlike before this fix, a failure here is no longer
            # swallowed — it must roll back the whole reception like any other
            # write in this sequence, not leave received_qty/stock committed
            # without a matching snapshot.
            new_row = inv_svc.get_stock(tenant_id, i["sku"], warehouse=warehouse, conn=conn)
            execute(
                """INSERT INTO inventory_snapshots (tenant_id, sku, current_stock)
                   VALUES (%s, %s, %s)""",
                (tenant_id, i["sku"], new_row["current_stock"]),
                conn=conn,
            )

        # 3. Header status
        fresh = get_po_items(tenant_id, po_log_id, conn=conn)
        fresh_ordered = [i for i in fresh if i["status"] in _ORDERED]
        fully = all(float(i["received_qty"] or 0) >= float(i["final_qty"] or 0)
                    for i in fresh_ordered)
        any_received = any(float(i["received_qty"] or 0) > 0 for i in fresh_ordered)
        status = "received" if fully else ("partial" if any_received else "not_received")

        execute(
            """UPDATE inventory_po_log
               SET reception_status = %s, received_at = %s, received_by = %s
               WHERE id = %s AND tenant_id = %s""",
            (status, received_at, user_id, po_log_id, tenant_id),
            conn=conn,
        )

        # 4. Learn real lead times — one observation per supplier, taken on THAT
        # supplier's own first delivery against this PO. Gating this on whether
        # the PO HEADER was still 'pending' (its state before this event) is
        # wrong for a multi-supplier PO: if supplier A delivers in event 1 (PO
        # goes pending -> partial) and supplier B only delivers in event 2, a
        # pending-only gate would silently never observe B — the PO is no longer
        # 'pending' by the time B's first delivery happens. Instead, gate per
        # supplier on whether THIS po_log_id already has an observation for them.
        lead_days = max(0.0, (received_at - generated_at).total_seconds() / 86400.0)
        observed_suppliers = sorted({
            (i.get("supplier") or "").strip()
            for i in ordered
            if (i.get("supplier") or "").strip() and received_by_item[i["id"]] > 0
        })
        already_observed = {
            r["supplier"] for r in query(
                """SELECT DISTINCT supplier FROM supplier_lead_time_obs
                   WHERE tenant_id = %s AND po_log_id = %s""",
                (tenant_id, po_log_id),
                conn=conn,
            )
        }
        for prov in observed_suppliers:
            if prov in already_observed:
                continue  # this supplier already has its first-delivery observation for this PO
            execute(
                """INSERT INTO supplier_lead_time_obs
                       (tenant_id, supplier, po_log_id, lead_time_days)
                   VALUES (%s, %s, %s, %s)""",
                (tenant_id, prov, po_log_id, round(lead_days, 2)),
                conn=conn,
            )

    log.info("[reception] tenant=%s po=%s status=%s lead_days=%.1f suppliers=%s",
             tenant_id, po_log_id, status, lead_days, observed_suppliers)

    return {
        "po_log_id": po_log_id,
        "reception_status": status,
        "received_at": received_at.isoformat(),
        "lead_time_days": round(lead_days, 2),
        "suppliers_observed": observed_suppliers,
        "items": get_po_items(tenant_id, po_log_id),
    }


def get_supplier_scorecard(tenant_id: str) -> list[dict]:
    """
    Per-supplier performance: real lead time range (min-max observed, not a
    single misleading average), on-time rate (real <= declared), fill rate
    and value purchased. Anchored to suppliers with at least one recorded
    reception — nothing to score before that.
    """
    lead_rows = query(
        """SELECT o.supplier,
                  COUNT(*)::int                      AS n_recepciones,
                  MIN(o.lead_time_days)              AS lead_time_real_min,
                  MAX(o.lead_time_days)              AS lead_time_real_max,
                  AVG(o.lead_time_days)              AS lead_time_real_avg,
                  MAX(o.observed_at)                 AS ultima_recepcion,
                  s.lead_time_days                   AS lead_time_declarado,
                  AVG(CASE WHEN o.lead_time_days <= s.lead_time_days THEN 1.0 ELSE 0.0 END)
                      FILTER (WHERE s.lead_time_days IS NOT NULL) AS on_time_rate
           FROM supplier_lead_time_obs o
           LEFT JOIN suppliers s
             ON s.tenant_id = o.tenant_id AND LOWER(s.name) = LOWER(o.supplier)
           WHERE o.tenant_id = %s
           GROUP BY o.supplier, s.lead_time_days
           ORDER BY n_recepciones DESC, o.supplier""",
        (tenant_id,),
    )

    fill_rows = query(
        """SELECT poi.supplier,
                  COALESCE(SUM(poi.received_qty), 0) AS total_received,
                  COALESCE(SUM(poi.final_qty), 0)    AS order_total,
                  COALESCE(SUM(poi.final_qty * poi.unit_cost), 0) AS purchased_value
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
           WHERE poi.tenant_id = %s
             AND poi.status IN ('approved', 'modified')
             AND poi.supplier IS NOT NULL AND poi.supplier <> ''
             AND pol.reception_status <> 'pending'
           GROUP BY poi.supplier""",
        (tenant_id,),
    )
    fill_by_supplier = {r["supplier"]: r for r in fill_rows}

    out = []
    for r in lead_rows:
        d = dict(r)
        if isinstance(d.get("ultima_recepcion"), datetime):
            d["ultima_recepcion"] = d["ultima_recepcion"].isoformat()
        for k in ("lead_time_real_avg", "lead_time_real_min", "lead_time_real_max"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 1)
        if d.get("on_time_rate") is not None:
            d["on_time_rate"] = round(float(d["on_time_rate"]), 3)

        declared = d.get("lead_time_declarado")
        avg = d.get("lead_time_real_avg")
        d["deviation_days"] = round(avg - declared, 1) if (declared is not None and avg is not None) else None

        fill = fill_by_supplier.get(d["supplier"])
        order_total = float(fill["order_total"]) if fill else 0.0
        d["fill_rate"] = round(float(fill["total_received"]) / order_total, 3) if fill and order_total > 0 else None
        d["purchased_value"] = round(float(fill["purchased_value"]), 2) if fill else 0.0

        out.append(d)

    # Suppliers with fill/value data (a reception event happened — the PO left
    # 'pending') but zero lead-time observations (e.g. everything received was
    # 0 units, so receive_po never wrote a supplier_lead_time_obs row). They
    # still belong on the scorecard with a real fill_rate/purchased_value;
    # lead-time fields are simply unknown. Appended after the lead-time group,
    # ordered by supplier.
    lead_suppliers = {r["supplier"] for r in lead_rows}
    fill_only_suppliers = sorted(p for p in fill_by_supplier if p not in lead_suppliers)
    for prov in fill_only_suppliers:
        fill = fill_by_supplier[prov]
        order_total = float(fill["order_total"])
        out.append({
            "supplier": prov,
            "n_recepciones": 0,
            "lead_time_real_min": None,
            "lead_time_real_max": None,
            "lead_time_real_avg": None,
            "ultima_recepcion": None,
            "lead_time_declarado": None,
            "on_time_rate": None,
            "deviation_days": None,
            "fill_rate": round(float(fill["total_received"]) / order_total, 3) if order_total > 0 else None,
            "purchased_value": round(float(fill["purchased_value"]), 2),
        })
    return out


def _effective_lead_time(tenant_id: str, supplier: str) -> tuple[float, str]:
    """
    The "already-learned" lead time for a supplier, preferring real observed
    receptions over the declared value on the supplier's card, and falling
    back to a sane default when neither exists yet.
    Returns (lead_time_days, source) where source ∈ observed | declared | default.
    """
    obs = query_one(
        """SELECT AVG(lead_time_days) AS avg_days, COUNT(*)::int AS n
           FROM supplier_lead_time_obs
           WHERE tenant_id = %s AND LOWER(supplier) = LOWER(%s)""",
        (tenant_id, supplier),
    )
    # Require enough receptions before trusting the observed average: a single
    # freak delivery must not rewrite the supplier's lead time and move the
    # "overdue" verdict because of an accident. Same threshold the semaphore
    # calc uses (service.get_learned_lead_times) — trusting at n=1 here while
    # that path waits for MIN_LEAD_TIME_OBSERVATIONS was an inconsistency.
    from backend.inventory.service import MIN_LEAD_TIME_OBSERVATIONS
    if (
        obs
        and obs.get("n")
        and obs["n"] >= MIN_LEAD_TIME_OBSERVATIONS
        and obs.get("avg_days") is not None
    ):
        return float(obs["avg_days"]), "observed"

    from backend.inventory import supplier_service as sup_svc
    supplier = sup_svc.get_supplier_by_name(tenant_id, supplier)
    if supplier and supplier.get("lead_time_days") is not None:
        return float(supplier["lead_time_days"]), "declared"

    return _DEFAULT_LEAD_TIME_DAYS, "default"


def get_overdue_receptions(tenant_id: str) -> list[dict]:
    """
    POs still pending/partial whose expected arrival — generation date plus
    the supplier's already-learned lead time (see _effective_lead_time) — has
    passed without any recorded reception. One row per (po_log_id, supplier)
    pair, since a single PO can span several suppliers with different lead
    times and only some of them may actually be late.
    """
    pos = query(
        """SELECT id, generated_at FROM inventory_po_log
           WHERE tenant_id = %s AND reception_status IN %s
           ORDER BY generated_at""",
        (tenant_id, RECEIVABLE_STATES),
    )
    if not pos:
        return []

    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for po in pos:
        po_log_id = po["id"]
        generated_at = po["generated_at"]
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)

        items = get_po_items(tenant_id, po_log_id)
        ordered = [i for i in items if i["status"] in _ORDERED]
        suppliers = sorted({
            (i.get("supplier") or "").strip()
            for i in ordered
            if (i.get("supplier") or "").strip()
        })
        if not suppliers:
            continue

        for prov in suppliers:
            lead_time, source = _effective_lead_time(tenant_id, prov)
            expected_arrival = generated_at + timedelta(days=lead_time)
            if now <= expected_arrival:
                continue
            out.append({
                "po_log_id":        po_log_id,
                "supplier":        prov,
                "generated_at":     generated_at.isoformat(),
                "expected_arrival": expected_arrival.date().isoformat(),
                "days_overdue":     (now - expected_arrival).days,
                "lead_time_used":   round(lead_time, 1),
                "lead_time_source": source,
            })

    out.sort(key=lambda r: -r["days_overdue"])
    return out
