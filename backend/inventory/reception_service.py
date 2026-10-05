"""
PO reception (feature 1.4 of the 2026-07-05 proposals; that doc was retired in
the 2026-08-11 docs cleanup and lives in git history).

Closes the purchase loop: when the order physically arrives, the buyer records
what came in. Two effects that compound StockAI's value over time:
  1. Stock self-corrects (received units are added to inventory_stock), so the
     semáforo keeps matching reality without manual stock edits.
  2. StockAI learns each supplier's REAL lead time (order date → reception date),
     turning the user's guess into observed data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError
from backend.inventory.defaults import (
    DEFAULT_LEAD_TIME_DAYS,
    SOURCE_DEFAULT,
    SOURCE_LEARNED,
    SOURCE_SUPPLIER_RULE,
)

log = logging.getLogger(__name__)

# The one default the whole product uses when a supplier has no data at all —
# imported, not re-declared. This module used to carry its own 15.0 literal
# while canonical.py and the training runner carried 7; see
# backend/inventory/defaults.py for why one number, and why that number is 15.
_DEFAULT_LEAD_TIME_DAYS = float(DEFAULT_LEAD_TIME_DAYS)

# Line statuses that were actually ordered (mirrors roi_service._ORDERED)
_ORDERED = ("approved", "modified")

# PO header states that can still take goods in. 'not_received' belongs here:
# "nothing arrived today" is a statement about a delivery that did not happen,
# not about an order that never will. Leaving it out turned that entry into a
# one-way door — the 409 guard in receive_po rejected the PO forever, so when the
# goods finally turned up there was no way to record them: no stock increment, no
# lead-time observation, and the buyer's only escape was to re-create the order.
# It also governs get_overdue_receptions, which is exactly the list an order
# nobody has delivered belongs on.
RECEIVABLE_STATES = ("pending", "partial", "not_received")

# Minimum receptions before a PERCENTAGE on the scorecard is allowed to speak.
# Same reasoning as `trend_measurable` two columns over: one event is not a rate.
# A supplier with a single reception printed "100%" in the on-time column, in
# bold green — a claim about a habit, made from one delivery. The stricter
# MIN_LEAD_TIME_OBSERVATIONS (3) answers a different question, "may this REPLACE
# the declared lead time?", which is about what the planner acts on rather than
# what the table prints; a displayed average only needs to be an average.
MIN_RATE_OBSERVATIONS = 2


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

    Refuses an order that still needs approval: the last step every send path
    shares, so a caller that forgot the check higher up still cannot mark an
    unapproved order as having left.
    """
    from backend.inventory import po_approval_service as approval_svc
    approval_svc.assert_sendable(tenant_id, po_log_id)
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
    if po.get("cancelled_at") is not None:
        raise AppError(
            "po_cancelled",
            "This order was cancelled; reopen it before recording a reception",
            status_code=409,
        )
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
    from backend.entitlements.service import enforce_limit, take_tenant_lock
    from backend.inventory import warehouse_service as wh_svc

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

    # Steps 1-4 below all run inside ONE transaction so the whole reception is
    # all-or-nothing: a failure anywhere in the sequence must leave
    # received_qty, stock, the snapshot, the PO header and the lead-time
    # observations exactly as they were before this call — not partially
    # applied. Every write (and every read that needs to see this
    # transaction's own not-yet-committed writes) passes `conn` through;
    # reads that only need already-committed data (e.g. supplier_service
    # lookups elsewhere) don't need it.
    with transaction() as conn:
        # 0. The ceilings, moved inside this block (they used to be checked
        # before it). Both the counts and the writes they authorise now sit
        # under one per-tenant lock, released by the same commit that makes the
        # new rows visible — so a second reception landing at the same moment
        # cannot pass a count taken before this one wrote.
        take_tenant_lock(tenant_id, conn)

        existing_wh_names = wh_svc.list_warehouse_names(tenant_id)
        new_wh_names = {
            _line_warehouse(i, po)
            for i in ordered
            if received_by_item[i["id"]] > 0
        } - existing_wh_names
        if new_wh_names:
            enforce_limit(tenant_id, "max_locations",
                          wh_svc.count_warehouses(tenant_id),
                          adding=len(new_wh_names), conn=conn)

        existing_stock_keys = inv_svc.list_stock_keys(tenant_id, conn=conn)
        new_sku_warehouse_pairs = {
            (i["sku"], _line_warehouse(i, po))
            for i in ordered
            if received_by_item[i["id"]] > 0
        } - existing_stock_keys
        if new_sku_warehouse_pairs:
            enforce_limit(
                tenant_id, "max_skus", inv_svc.count_stock(tenant_id, conn=conn),
                adding=len(new_sku_warehouse_pairs), conn=conn,
            )

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
                """INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse)
                   VALUES (%s, %s, %s, %s)""",
                (tenant_id, i["sku"], new_row["current_stock"], warehouse),
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

        # 4. Learn real lead times — one observation per supplier, written only
        # when the PO reaches 'received', dated by the event that completed it.
        #
        # It used to be taken on that supplier's FIRST delivery against the PO,
        # which let the opening trickle fix the lead time forever: on an order of
        # 5000 units where 20 samples arrive in 2 days and the rest in 40, the
        # first event taught "this supplier takes 2 days" and the per-PO
        # already-observed gate then skipped every later delivery. The lead time
        # that matters for planning is when the buyer can actually COUNT on the
        # order being there — the moment the last unit lands — so that is the
        # moment measured. A PO that never completes yields no observation at
        # all, which is the honest answer: we do not know yet how long it took.
        #
        # Two consequences worth naming. A PO that arrives complete in one event
        # still writes exactly one observation per supplier, as before. And on a
        # multi-supplier PO every supplier is dated by the completion of the
        # whole order, even one who delivered early — deliberate, because a
        # partial order is not an order the buyer can sell from, and per-supplier
        # completion would reintroduce the same ambiguity for any supplier whose
        # own lines are still open.
        lead_days = max(0.0, (received_at - generated_at).total_seconds() / 86400.0)
        observed_suppliers: list[str] = []
        if status == "received":
            # Suppliers are matched case-insensitively, the way every reader of
            # this table already groups it (service.get_learned_lead_times and
            # _effective_lead_time both use LOWER). A PO spelling the same
            # supplier "Acme" on one line and "ACME" on another used to write TWO
            # observations for one delivery and double-weight it in the average.
            # The stored spelling is the alphabetically first one seen, purely so
            # the row is deterministic — no reader depends on its case.
            supplier_by_key: dict[str, str] = {}
            for i in fresh_ordered:
                name = (i.get("supplier") or "").strip()
                if not name or float(i["received_qty"] or 0) <= 0:
                    continue
                key = name.lower()
                if key not in supplier_by_key or name < supplier_by_key[key]:
                    supplier_by_key[key] = name
            already_observed = {
                (r["supplier"] or "").strip().lower()
                for r in query(
                    """SELECT DISTINCT supplier FROM supplier_lead_time_obs
                       WHERE tenant_id = %s AND po_log_id = %s""",
                    (tenant_id, po_log_id),
                    conn=conn,
                )
            }
            for key in sorted(supplier_by_key):
                if key in already_observed:
                    continue  # this PO already taught us this supplier's lead time
                prov = supplier_by_key[key]
                execute(
                    """INSERT INTO supplier_lead_time_obs
                           (tenant_id, supplier, po_log_id, lead_time_days)
                       VALUES (%s, %s, %s, %s)""",
                    (tenant_id, prov, po_log_id, round(lead_days, 2)),
                    conn=conn,
                )
                observed_suppliers.append(prov)

    log.info("[reception] tenant=%s po=%s status=%s lead_days=%.1f suppliers=%s",
             tenant_id, po_log_id, status, lead_days, observed_suppliers)

    # `lead_time_days` is the elapsed time of THIS event (order date -> today).
    # `suppliers_observed` lists only the suppliers an observation was actually
    # stored for, which is empty until the PO is complete — see step 4.
    return {
        "po_log_id": po_log_id,
        "reception_status": status,
        "received_at": received_at.isoformat(),
        "lead_time_days": round(lead_days, 2),
        "suppliers_observed": observed_suppliers,
        "items": get_po_items(tenant_id, po_log_id),
    }


def unreceive_po(tenant_id: str, po_log_id: str, user_id: str) -> dict:
    """
    Reverse a reception: take the units it added back out of stock, restore
    the PO line's `received_qty` and the order's `reception_status`, and
    un-teach the supplier lead time it taught (see docs/assistant-actions.md
    section 0 — this is what receive_po was missing before the WhatsApp
    assistant's write tools could be trusted with it).

    **Scope, deliberately.** This reverses the PO's WHOLE recorded reception,
    not just the most recent `receive_po` call. StockAI keeps no per-call
    reception history — each call only accumulates `received_qty` on the PO
    line — so "the state before THIS reception" is only reconstructible as
    "the state before ANY reception", which is also the one state
    `receive_po`'s own gate (`RECEIVABLE_STATES`) ever returns a PO to (once a
    PO reaches `received` no further reception call is even accepted). A
    reception-event log that could undo one partial delivery while keeping a
    later one is new capability this product does not have yet — flagged, not
    built here.

    **Stock that already moved on.** If some of the received units were sold,
    transferred out, or written off as shrinkage since the reception, taking
    them back out would either go negative (a lie about physical stock) or
    have to be silently clamped to zero (which would still remove the
    lead-time observation and the received_qty as if the units came back,
    when they provably didn't). Neither is acceptable, so this refuses —
    entirely and atomically, before a single row is written — the moment ANY
    touched (sku, warehouse) does not currently hold enough stock to give the
    units back. The caller gets back exactly which SKU/warehouse is short and
    by how much (`AppError.params["shortfalls"]`), so a human decides: fix the
    stock count first, or leave the mistaken reception standing.

    **Idempotent by construction, not by a separate flag.** Reversing sets
    `received_at` back to NULL — the same field `receive_po` treats as "has
    this PO ever had a reception recorded" — so a second call finds nothing to
    undo and refuses with 409 instead of decrementing stock a second time for
    units that were already given back on the first call.
    """
    po = get_po(tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    if po.get("received_at") is None:
        raise AppError(
            "reception_nothing_to_undo",
            "This order has no recorded reception to undo",
            status_code=409,
        )

    items = get_po_items(tenant_id, po_log_id)
    ordered = [i for i in items if i["status"] in _ORDERED]

    # Aggregate by (sku, warehouse): a PO can carry the same SKU on two lines
    # for two different warehouses (mirrors receive_po's own step 2).
    to_remove: dict[tuple[str, str], float] = {}
    for i in ordered:
        qty = float(i.get("received_qty") or 0)
        if qty > 0:
            key = (i["sku"], _line_warehouse(i, po))
            to_remove[key] = to_remove.get(key, 0.0) + qty

    from backend.entitlements.service import take_tenant_lock
    from backend.inventory import service as inv_svc

    with transaction() as conn:
        # Same per-tenant lock receive_po takes, for the same reason: this
        # read-then-write on inventory_stock must not race a concurrent
        # reception or another undo touching the same rows.
        take_tenant_lock(tenant_id, conn)

        # Pre-check EVERY touched row's current stock before writing anything —
        # all-or-nothing, so a shortfall discovered on the third SKU cannot
        # leave the first two already decremented.
        shortfalls = []
        for (sku, warehouse), qty in to_remove.items():
            row = inv_svc.get_stock(tenant_id, sku, warehouse=warehouse, conn=conn)
            available = float(row["current_stock"]) if row else 0.0
            if available < qty:
                shortfalls.append({
                    "sku": sku, "warehouse": warehouse,
                    "available": available, "needed": qty,
                })
        if shortfalls:
            raise AppError(
                "reception_undo_insufficient_stock",
                "Cannot undo this reception: some of the received units are no "
                "longer in stock (sold, transferred, or written off since the "
                "reception)",
                status_code=409,
                params={"shortfalls": shortfalls},
            )

        for (sku, warehouse), qty in to_remove.items():
            execute(
                """UPDATE inventory_stock
                      SET current_stock = current_stock - %s, updated_at = NOW()
                    WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                (qty, tenant_id, sku, warehouse),
                conn=conn,
            )
            # Point-in-time snapshot of the reversal, same as receive_po does
            # for the original reception — /stock/{sku}/history must show the
            # undo as an event, not a gap.
            new_row = inv_svc.get_stock(tenant_id, sku, warehouse=warehouse, conn=conn)
            execute(
                """INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse)
                   VALUES (%s, %s, %s, %s)""",
                (tenant_id, sku, new_row["current_stock"], warehouse),
                conn=conn,
            )

        for i in ordered:
            execute(
                "UPDATE inventory_po_items SET received_qty = 0 WHERE id = %s AND tenant_id = %s",
                (i["id"], tenant_id),
                conn=conn,
            )

        execute(
            """UPDATE inventory_po_log
                  SET reception_status = 'pending', received_at = NULL, received_by = NULL
                WHERE id = %s AND tenant_id = %s""",
            (po_log_id, tenant_id),
            conn=conn,
        )

        # Un-teach the lead time this reception taught. At most one batch of
        # rows can exist for this po_log_id — supplier_lead_time_obs is only
        # written the moment a PO reaches 'received' (step 4 of receive_po),
        # and RECEIVABLE_STATES excludes 'received', so no PO can be received
        # twice without an undo in between.
        unlearned = query(
            """SELECT DISTINCT supplier FROM supplier_lead_time_obs
               WHERE tenant_id = %s AND po_log_id = %s""",
            (tenant_id, po_log_id),
            conn=conn,
        )
        execute(
            "DELETE FROM supplier_lead_time_obs WHERE tenant_id = %s AND po_log_id = %s",
            (tenant_id, po_log_id),
            conn=conn,
        )

    log.info(
        "[reception] UNDO tenant=%s po=%s undone_by=%s units=%.2f suppliers_unlearned=%s",
        tenant_id, po_log_id, user_id, sum(to_remove.values()),
        [r["supplier"] for r in unlearned],
    )

    return {
        "po_log_id": po_log_id,
        "reception_status": "pending",
        "units_removed": sum(to_remove.values()),
        "sku_count": len({sku for sku, _ in to_remove}),
        "suppliers_lead_time_unlearned": [r["supplier"] for r in unlearned],
        "items": get_po_items(tenant_id, po_log_id),
    }


def unsend_po(tenant_id: str, po_log_id: str, user_id: str) -> dict:
    """
    Reverse `mark_po_sent`: clear `sent_at` so the order returns to
    "not yet sent" — the state `POST /po/{id}/send` reads before it stamps it.

    **What this does NOT do, and cannot do.** It does not recall the email or
    WhatsApp message `POST /po/{id}/send` already put in a supplier's inbox —
    nothing in this product, or in email/WhatsApp, can (see
    docs/assistant-actions.md, class C.1: "it left the system"). This reverses
    only StockAI's OWN bookkeeping about the order.

    **Why clearing the column is the whole fix.** `cash_service`'s payables
    calendar filters live on `inventory_po_log.sent_at IS NOT NULL` and keeps
    no copy, so the very next read stops counting this order as a payable.

    It does NOT take the order out of "units on the way": since 2026-10-01
    `service.get_incoming_detail` counts every open PO whether or not it was
    sent through StockAI (a downloaded PO the buyer mailed themselves is just
    as much on its way), so un-sending leaves the purchase recommendation
    exactly where it was.

    **Refuses once a reception exists.** A reception recorded against this PO
    (`reception_status != 'pending'`) is physical evidence the order DID reach
    someone — units are on their way or already arrived — so un-sending at
    that point would misstate something that provably happened, not correct a
    mistake. If the reception itself was the mistake, `unreceive_po` undoes
    that first; only then can this run.

    **Idempotent.** `sent_at IS NOT NULL` in the WHERE clause, the same guard
    `mark_po_sent` uses in reverse, means a second call finds nothing left to
    clear — refused with 409, not a silent no-op a caller could mistake for
    "it worked".
    """
    po = get_po(tenant_id, po_log_id)
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    if po.get("sent_at") is None:
        raise AppError(
            "po_not_sent",
            "This order has not been marked as sent; there is nothing to undo",
            status_code=409,
        )
    if po.get("reception_status") != "pending":
        raise AppError(
            "po_unsend_after_reception",
            "This order already has a recorded reception, which is evidence it "
            "reached the supplier; undo the reception before un-sending",
            status_code=409,
            params={"reception_status": po.get("reception_status")},
        )
    # A paid invoice is evidence the supplier invoiced it, i.e. that the order
    # reached them. Un-sending would also strand `paid_at` on a draft the cash
    # calendar no longer shows. Undo the payment first (mark-unpaid).
    if po.get("paid_at") is not None:
        raise AppError(
            "po_unsend_after_payment",
            "This order is marked as paid; mark it as unpaid before un-sending",
            status_code=409,
        )

    execute(
        """UPDATE inventory_po_log
              SET sent_at = NULL
            WHERE id = %s AND tenant_id = %s AND sent_at IS NOT NULL""",
        (po_log_id, tenant_id),
    )
    log.info("[reception] UNSEND tenant=%s po=%s undone_by=%s", tenant_id, po_log_id, user_id)
    return {"po_log_id": po_log_id, "sent_at": None}


def _fill_rate(total_received: float, order_total: float) -> Optional[float]:
    """
    Share of what was ordered that actually arrived, capped at 1.0.

    Over-delivery is not better service: a supplier who ships 120 against an
    order of 100 filled the order — and sent 20 units nobody asked for, which is
    a stock problem, not a fulfilment merit. Printed as "120%" in a column headed
    "% fill rate" it reads as the supplier outperforming, and it breaks the
    column's own promise, since a share of the order cannot exceed the order.
    Capped rather than reported as a separate over-delivery figure because the
    reception path already refuses to book more than a line's outstanding
    quantity: the only way past 100% is data that predates that guard, and a
    new column for a residue of old rows is not worth the screen space.
    """
    if order_total <= 0:
        return None
    return round(min(1.0, float(total_received) / order_total), 3)


# Days past a supplier's own lead time before a half-delivered order counts
# against them. A delivery window is a promise about a date, not about a
# minute: a truck that arrives the morning after the promised day is late by
# any reasonable reading, and one that arrives the same afternoon is not.
FILL_RATE_GRACE_DAYS = 2


def _still_in_transit(row: dict, lead_time_days: float, now: datetime) -> bool:
    """Is this order still inside the window its supplier promised?

    Fill rate used to include every `partial` and `not_received` order, summing
    what had arrived against the FULL ordered quantity with no notion of a
    delivery still being on its way (stability 11.12). A supplier with two
    half-delivered orders, both on schedule, printed **50%** — presented as a
    performance verdict, with the one who had shorted nothing reading worst on
    the page.

    An order that is fully received is judged immediately; it has nothing left
    to arrive. Everything else waits for `generated_at + lead time + grace`,
    where the lead time is `_effective_lead_time` — the same learned-then-
    declared-then-default rule the overdue screen and the semáforo already use,
    so the two screens cannot disagree about whether a supplier is late.
    """
    if row.get("reception_status") == "received":
        return False
    generated_at = row.get("generated_at")
    if not isinstance(generated_at, datetime):
        return False
    due = generated_at + timedelta(days=float(lead_time_days) + FILL_RATE_GRACE_DAYS)
    return now < due


def _fill_counts_for(tenant_id: str, rows: list[dict]) -> dict[str, dict]:
    """Aggregate per-order fill rows into the per-supplier figures the scorecard
    reads, dropping the orders that are still inside their delivery window.

    `n_orders` is the sample size behind the percentage — how many orders it
    averages over — and it now counts only the orders that were actually judged.
    `orders_in_transit` says how many were set aside, so a supplier with one
    order and nothing to report can say WHY it is empty instead of looking like
    a supplier with no history.

    On `purchased_value`: deliberately NOT COALESCEd to 0. A NULL unit_cost
    annuls the product and SQL drops it from the SUM, so a supplier you bought
    40 million from with no costs on file summed to NULL and printed a confident
    zero in bold green. It is also NOT filtered by the window: money left the
    company when the order was placed, whatever is still on the road.
    """
    now = datetime.now(timezone.utc)
    lead_times: dict[str, float] = {}
    out: dict[str, dict] = {}

    for row in rows:
        key = row["supplier_key"]
        acc = out.setdefault(key, {
            "supplier_key":      key,
            "supplier":          row["supplier"],
            "n_orders":          0,
            "orders_in_transit": 0,
            "total_received":    0.0,
            "order_total":       0.0,
            "purchased_value":   None,
            "n_lines":           0,
            "n_lines_costed":    0,
        })

        if row["purchased_value"] is not None:
            acc["purchased_value"] = (acc["purchased_value"] or 0.0) + float(row["purchased_value"])
        acc["n_lines"] += int(row["n_lines"] or 0)
        acc["n_lines_costed"] += int(row["n_lines_costed"] or 0)

        if key not in lead_times:
            lead_times[key] = _effective_lead_time(tenant_id, row["supplier"])[0]
        if _still_in_transit(row, lead_times[key], now):
            acc["orders_in_transit"] += 1
            continue

        acc["n_orders"] += 1
        acc["total_received"] += float(row["total_received"] or 0)
        acc["order_total"] += float(row["order_total"] or 0)

    return out


def get_supplier_scorecard(tenant_id: str) -> list[dict]:
    """
    Per-supplier performance: real lead time range (min-max observed, not a
    single misleading average), on-time rate (real <= declared), fill rate
    and value purchased. Anchored to suppliers with at least one recorded
    reception — nothing to score before that.
    """
    # Grouped by LOWER(supplier), like every other reader of this data
    # (service.get_learned_lead_times, _effective_lead_time). A CSV import that
    # spells the same supplier "Acme" and "ACME" used to produce two scorecard
    # rows, each with half the deliveries, while the planner had long since
    # merged them into one lead time — two answers to "how does this supplier
    # perform?" on two screens.
    #
    # The suppliers side is collapsed by LOWER(name) in a subquery BEFORE the
    # join for the same reason it matters here: joining a case-insensitive name
    # against a table that may hold both spellings would multiply every
    # observation row and inflate n_receptions.
    #
    # `lead_time_set_by` gates the declared value exactly as _effective_lead_time
    # does further down: `suppliers.lead_time_days` is INT NOT NULL DEFAULT 15,
    # so "the card says 15 days" and "nobody ever filled the card" are the same
    # row. Without the gate, a supplier imported by CSV was shown "DECLARADO 15d"
    # and graded on punctuality against a promise they never made — StockAI's own
    # assumption, scored as if it were theirs. NULL here lets the UI say "no
    # declarado" instead of inventing one, and takes on_time_rate and
    # deviation_days down with it.
    lead_rows = query(
        """SELECT LOWER(o.supplier)                 AS supplier_key,
                  MIN(o.supplier)                   AS supplier,
                  COUNT(*)::int                     AS n_receptions,
                  MIN(o.lead_time_days)             AS lead_time_real_min,
                  MAX(o.lead_time_days)             AS lead_time_real_max,
                  AVG(o.lead_time_days)             AS lead_time_real_avg,
                  MAX(o.observed_at)                AS last_reception,
                  s.declared_lead_time              AS lead_time_declarado,
                  AVG(CASE WHEN o.lead_time_days <= s.declared_lead_time THEN 1.0 ELSE 0.0 END)
                      FILTER (WHERE s.declared_lead_time IS NOT NULL) AS on_time_rate
           FROM supplier_lead_time_obs o
           LEFT JOIN (
               SELECT tenant_id,
                      LOWER(name) AS name_key,
                      MIN(CASE WHEN COALESCE(lead_time_set_by, '') <> ''
                               THEN lead_time_days END) AS declared_lead_time
                 FROM suppliers
                WHERE tenant_id = %s
                GROUP BY tenant_id, LOWER(name)
           ) s ON s.tenant_id = o.tenant_id AND s.name_key = LOWER(o.supplier)
           WHERE o.tenant_id = %s
           GROUP BY LOWER(o.supplier), s.declared_lead_time
           ORDER BY n_receptions DESC, supplier""",
        (tenant_id, tenant_id),
    )

    # Same LOWER() grouping as above, so a supplier's fill data lands on their
    # one row. `n_orders` is not returned to the UI; it is the sample size behind
    # fill_rate — how many orders that percentage averages over.
    #
    # On `purchased_value`: deliberately NOT COALESCEd to 0. A NULL unit_cost
    # annuls the product and SQL drops it from the SUM, so a supplier you bought
    # 40 million from with no costs on file summed to NULL and printed a
    # confident zero in bold green. /impacto applies the opposite rule to the
    # same quantity (roi_service's managed_purchase_value is None, not 0, when
    # nothing carries a cost) — same data, two policies, one screen apart.
    #
    # `n_lines` / `n_lines_costed` are how much of the order that figure covers.
    # Without them, 40 lines with 6 costed report those 6 as the total and it
    # looks exact.
    #
    # This rationale lives OUT here rather than inside the SQL, for the same
    # reason roi_service.get_roi_summary keeps its own out: the guard in
    # test_currency_reaches_backend_strings scans string literals for a currency
    # symbol, and it cannot tell a comment from copy once both are inside the
    # same string.
    # Per (supplier, ORDER), not per supplier: the window below is a property of
    # each order, so the totals can only be added up once every order has been
    # judged. See _fill_counts_for.
    fill_rows = query(
        """SELECT LOWER(poi.supplier)                AS supplier_key,
                  MIN(poi.supplier)                  AS supplier,
                  poi.po_log_id                      AS po_log_id,
                  pol.generated_at                   AS generated_at,
                  pol.reception_status               AS reception_status,
                  COALESCE(SUM(poi.received_qty), 0) AS total_received,
                  COALESCE(SUM(poi.final_qty), 0)    AS order_total,
                  SUM(poi.final_qty * poi.unit_cost) AS purchased_value,
                  COUNT(*)::int                      AS n_lines,
                  COUNT(poi.unit_cost)::int          AS n_lines_costed
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
           WHERE poi.tenant_id = %s
             AND poi.status IN ('approved', 'modified')
             AND poi.supplier IS NOT NULL AND poi.supplier <> ''
             AND pol.reception_status <> 'pending'
           GROUP BY LOWER(poi.supplier), poi.po_log_id, pol.generated_at,
                    pol.reception_status""",
        (tenant_id,),
    )
    fill_by_supplier = _fill_counts_for(tenant_id, fill_rows)

    out = []
    for r in lead_rows:
        d = dict(r)
        # Internal join key: the UI keys its rows on the display name.
        supplier_key = d.pop("supplier_key")
        if isinstance(d.get("last_reception"), datetime):
            d["last_reception"] = d["last_reception"].isoformat()
        for k in ("lead_time_real_avg", "lead_time_real_min", "lead_time_real_max"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 1)
        if d.get("on_time_rate") is not None:
            d["on_time_rate"] = round(float(d["on_time_rate"]), 3)

        declared = d.get("lead_time_declarado")
        avg = d.get("lead_time_real_avg")
        d["deviation_days"] = round(avg - declared, 1) if (declared is not None and avg is not None) else None

        # Every delivery landed the same day it was ordered, so the observed
        # average is 0 and measures nothing. The scorecard printed "LEAD TIME
        # REAL 0d" flat beside "DECLARADO 10d", which invites the buyer to lower
        # their lead time to zero and order too late — the exact decision
        # /proveedores works to prevent by explaining why it will not learn.
        #
        # NOT gated on MIN_LEAD_TIME_OBSERVATIONS, and the distinction matters:
        # that constant answers "have I seen enough to REPLACE the declared lead
        # time?", which is about learning. This answers "does this measurement
        # say anything at all?", which is about display. A zero average says
        # nothing whether it came from one delivery or ten — a first cut did
        # require >= 3 and left a supplier with a single same-day reception still
        # showing `0d`.
        d["lead_time_unusable"] = bool(avg is not None and float(avg) <= 0)
        # A trend needs at least two points to be a trend. Reported over a single
        # reception it read "Estable", which is a claim about a shape nobody has
        # seen yet.
        d["trend_measurable"] = int(d.get("n_receptions") or 0) >= 2
        # The two percentages on this row had no sample floor at all while the
        # columns beside them refuse to speak below n=2 and n=3: one reception
        # printed "100%" in bold green. Reported the way `lead_time_unusable` and
        # `trend_measurable` are — the number stays, a boolean says whether it
        # means anything — so the UI keeps one convention for "we are not sure".
        d["on_time_measurable"] = bool(
            d.get("on_time_rate") is not None
            and int(d.get("n_receptions") or 0) >= MIN_RATE_OBSERVATIONS
        )

        fill = fill_by_supplier.get(supplier_key)
        d["fill_rate"] = _fill_rate(fill["total_received"], float(fill["order_total"])) if fill else None
        d["fill_rate_measurable"] = bool(
            fill
            and d["fill_rate"] is not None
            and int(fill["n_orders"] or 0) >= MIN_RATE_OBSERVATIONS
        )
        # Orders set aside because they are still inside their delivery window.
        # Reported so an empty fill rate can say WHY it is empty: "two orders on
        # the way" and "we have never bought from them" look identical
        # otherwise, and only one of them is a reason to worry.
        d["orders_in_transit"] = int(fill["orders_in_transit"]) if fill else 0
        # None — not 0 — when no line of this supplier's orders carries a unit
        # cost. "We bought nothing from them" and "we never recorded what it
        # cost" are different statements and the column must not merge them.
        raw_value = fill["purchased_value"] if fill else None
        d["purchased_value"] = round(float(raw_value), 2) if raw_value is not None else None
        # True only when EVERY ordered line carried a cost. Partial coverage
        # still reports the covered part — throwing away a real, if incomplete,
        # figure helps nobody — but the row no longer presents it as the total.
        d["purchased_value_complete"] = bool(
            fill and int(fill["n_lines"] or 0) > 0
            and int(fill["n_lines_costed"] or 0) == int(fill["n_lines"] or 0)
        )

        out.append(d)

    # Suppliers with fill/value data (a reception event happened — the PO left
    # 'pending') but zero lead-time observations: everything received was 0
    # units, or the PO is still part-delivered and receive_po only measures a
    # lead time once the order is complete. Either way no obs row exists. They
    # still belong on the scorecard with a real fill_rate/purchased_value;
    # lead-time fields are simply unknown. Appended after the lead-time group,
    # ordered by supplier.
    lead_supplier_keys = {r["supplier_key"] for r in lead_rows}
    fill_only_keys = sorted(k for k in fill_by_supplier if k not in lead_supplier_keys)
    for key in fill_only_keys:
        fill = fill_by_supplier[key]
        fill_rate = _fill_rate(fill["total_received"], float(fill["order_total"]))
        out.append({
            "supplier": fill["supplier"],
            "n_receptions": 0,
            "lead_time_real_min": None,
            "lead_time_real_max": None,
            "lead_time_real_avg": None,
            "last_reception": None,
            "lead_time_declarado": None,
            "on_time_rate": None,
            "deviation_days": None,
            "fill_rate": fill_rate,
            "purchased_value": (
                round(float(fill["purchased_value"]), 2)
                if fill["purchased_value"] is not None else None
            ),
            "purchased_value_complete": bool(
                int(fill["n_lines"] or 0) > 0
                and int(fill["n_lines_costed"] or 0) == int(fill["n_lines"] or 0)
            ),
            # No lead-time observations at all, so nothing to disbelieve, no
            # trend to report and no punctuality to grade. Keys present on every
            # row so the UI never has to tell "false" from "absent".
            "lead_time_unusable": False,
            "trend_measurable": False,
            "on_time_measurable": False,
            "orders_in_transit": int(fill["orders_in_transit"]),
            "fill_rate_measurable": bool(
                fill_rate is not None
                and int(fill["n_orders"] or 0) >= MIN_RATE_OBSERVATIONS
            ),
        })
    return out


def _effective_lead_time(tenant_id: str, supplier: str) -> tuple[float, str]:
    """
    The "already-learned" lead time for a supplier, preferring real observed
    receptions over the declared value on the supplier's card, and falling
    back to a sane default when neither exists yet.

    Returns (lead_time_days, source) where source is one of the FIVE values in
    `backend/inventory/defaults.py` — the same vocabulary the semáforo speaks.
    This module used to answer 'observed' | 'declared' | 'default' while
    service.py answered 'learned' | 'configured' for the identical question, so
    the overdue-receptions screen and the SKU card described the same lead time
    with two different words. 'observed' is now 'learned' (evidence from real
    deliveries) and 'declared' is 'supplier_rule' (a value carried by the
    supplier's card, not by the SKU).
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
    #
    # And the average has to be POSITIVE. Three same-day counter pickups
    # (ordered and collected the same morning — routine in this market) average
    # to 0.0, and a zero lead time makes expected_arrival == generated_at, so
    # get_overdue_receptions flagged every open PO from that supplier as overdue
    # the instant it was created. service.resolve_lead_time (`learned > 0`) and
    # supplier_service.list_suppliers (`float(average) > 0`) both already refuse
    # a non-positive average; the latter's comment even states that this
    # function does the same, which until now it did not.
    from backend.inventory.service import MIN_LEAD_TIME_OBSERVATIONS
    if (
        obs
        and obs.get("n")
        and obs["n"] >= MIN_LEAD_TIME_OBSERVATIONS
        and obs.get("avg_days") is not None
        and float(obs["avg_days"]) > 0
    ):
        return float(obs["avg_days"]), SOURCE_LEARNED

    from backend.inventory import supplier_service as sup_svc
    supplier = sup_svc.get_supplier_by_name(tenant_id, supplier)
    # `lead_time_set_by` must be set: the column is NOT NULL DEFAULT 15, so
    # "the supplier card says 15" and "nobody filled the supplier card" were the
    # same row, and this screen reported our assumption as a declared value.
    if (supplier
            and supplier.get("lead_time_days") is not None
            and supplier.get("lead_time_set_by")):
        return float(supplier["lead_time_days"]), SOURCE_SUPPLIER_RULE

    return _DEFAULT_LEAD_TIME_DAYS, SOURCE_DEFAULT


def get_overdue_receptions(tenant_id: str) -> list[dict]:
    """
    POs that still have something to receive (RECEIVABLE_STATES — pending,
    partial, and not_received: an order the buyer already reported as "nothing
    arrived" is late by definition, not closed) whose expected arrival —
    generation date plus the supplier's already-learned lead time (see
    _effective_lead_time) — has passed. One row per (po_log_id, supplier)
    pair, since a single PO can span several suppliers with different lead
    times and only some of them may actually be late.
    """
    pos = query(
        """SELECT id, generated_at FROM inventory_po_log
           WHERE tenant_id = %s AND reception_status IN %s
             AND cancelled_at IS NULL
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
