"""Purchase orders out to the ERP.

The feed is derived from `inventory_po_log` at read time, with a cursor the
database stamps itself (see `migrations.py`), so no code path that creates,
approves, cancels or receives an order has to remember to announce it.

Rules that exist because each one has cost real money elsewhere:

* **Nothing leaves before approval.** An order is served only while
  `po_approval_service.assert_sendable` lets it leave, the same gate every send
  path uses. When an order is approved the trigger moves its cursor, so it
  appears then and not before.
* **A cancellation is a fact the ERP must hear.** Cancelled orders stay in the feed
  with `state = "cancelled"`; the ERP matches them by the stable id.
* **An order exported twice becomes a duplicate purchase**, so every document and
  line carries an id that is the same on every export, and the ERP's own number is
  written back once and never overwritten.
* **An unmapped supplier is flagged, not dropped.** The document is served with
  `supplier.erp_code = null` and `supplier.mapped = false`.
* The feed is a pure read; it records nothing, so it is callable with a read key.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser
from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError
from backend.inventory.roi_service import format_po_number
from backend.po_erp import core

log = logging.getLogger(__name__)

# Lines that are part of the order: the ones the buyer kept.
_ORDERED = ("approved", "modified")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _approval_allows(tenant_id: str, po: dict) -> bool:
    """True when the order is allowed to leave. Never raises."""
    from backend.inventory import po_approval_service as approvals
    try:
        approvals.assert_sendable(tenant_id, po=po)
    except AppError:
        return False
    return True


def _expected_dates(tenant_id: str, orders: list[dict],
                    lines_by_po: dict[str, list[dict]]) -> dict[tuple[str, str], tuple[str, str]]:
    """{(po_log_id, supplier name): (expected ISO day, source)}: the order date plus
    the supplier's learned lead time, or the date a person accepted from the
    supplier (the same rule the overdue list uses)."""
    from backend.inventory import po_confirmation_core as confirm_core
    from backend.inventory import po_confirmation_service as confirm_svc
    from backend.inventory import reception_service as reception

    accepted = confirm_svc.accepted_promises(tenant_id, [o["id"] for o in orders])
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for po in orders:
        generated = _utc(po["generated_at"])
        lines = lines_by_po.get(po["id"], [])
        for name in sorted({(ln.get("supplier") or "").strip() for ln in lines}):
            if not name:
                continue
            lead, _src = reception._effective_lead_time(tenant_id, name)
            day = (generated + timedelta(days=lead)).date()
            source = confirm_core.SOURCE_MODEL
            promises = accepted.get((po["id"], name))
            if promises:
                ids = [ln["id"] for ln in lines if (ln.get("supplier") or "").strip() == name]
                promised, source = confirm_core.expected_arrival_with_promises(
                    day, [promises.get(i) for i in ids])
                if source == confirm_core.SOURCE_SUPPLIER_PROMISE:
                    day = promised
            out[(po["id"], name)] = (day.isoformat(), source)
    return out


def build_documents(tenant_id: str, orders: list[dict]) -> list[dict]:
    """The orders as feed objects: one per order, with one document per supplier."""
    if not orders:
        return []
    ids = [o["id"] for o in orders]
    items = query(
        """SELECT i.id, i.po_log_id, i.sku, i.display_name, i.supplier, i.supplier_id,
                  i.final_qty, i.unit_cost, i.currency, i.warehouse, i.received_qty,
                  s.erp_code AS supplier_erp_code
             FROM inventory_po_items i
             LEFT JOIN suppliers s ON s.id = i.supplier_id AND s.tenant_id = i.tenant_id
            WHERE i.tenant_id = %s AND i.po_log_id = ANY(%s) AND i.status IN %s
            ORDER BY i.po_log_id, i.supplier NULLS LAST, i.sku, i.id""",
        (tenant_id, ids, _ORDERED),
    )
    lines_by_po: dict[str, list[dict]] = {}
    for it in items:
        lines_by_po.setdefault(it["po_log_id"], []).append(it)

    refs = query(
        "SELECT po_log_id, supplier_key, reference, created_at FROM po_erp_references "
        "WHERE tenant_id = %s AND po_log_id = ANY(%s)",
        (tenant_id, ids),
    )
    ref_by = {(r["po_log_id"], r["supplier_key"]): r for r in refs}
    expected = _expected_dates(tenant_id, orders, lines_by_po)

    out: list[dict] = []
    for po in orders:
        cancelled = po.get("cancelled_at") is not None
        number = format_po_number(po.get("po_number"), po["id"])
        documents = []
        for grp in core.split_documents(lines_by_po.get(po["id"], [])):
            key = grp["supplier_key"]
            rows = grp["lines"]
            first = rows[0]
            currency, mixed = core.common_currency(r.get("currency") for r in rows)
            ref = ref_by.get((po["id"], key))
            exp = expected.get((po["id"], (first.get("supplier") or "").strip()))
            documents.append({
                "document_id": core.document_id(po["id"], key),
                "number": number,
                "supplier": {
                    "id": key or None,
                    "name": first.get("supplier"),
                    "erp_code": first.get("supplier_erp_code"),
                    "mapped": bool(first.get("supplier_erp_code")),
                },
                "currency": currency,
                "mixed_currency": mixed,
                "order_date": _utc(po["generated_at"]).date().isoformat(),
                "expected_date": exp[0] if exp else None,
                "expected_date_source": exp[1] if exp else None,
                "warehouse": first.get("warehouse"),
                "erp_reference": ref["reference"] if ref else None,
                "acknowledged": ref is not None,
                "lines": [{
                    "line_id": r["id"],
                    "sku": r["sku"],
                    "name": r.get("display_name"),
                    "quantity": float(r["final_qty"] or 0),
                    "received_quantity": float(r["received_qty"] or 0),
                    "unit_cost": float(r["unit_cost"]) if r["unit_cost"] is not None else None,
                    "currency": r.get("currency"),
                    "total": core.line_total(r["final_qty"] or 0, r["unit_cost"]),
                } for r in rows],
            })
        out.append({
            "id": po["id"],
            "number": number,
            "state": core.order_state(po.get("reception_status"), cancelled),
            "cancelled_at": po["cancelled_at"].isoformat() if cancelled else None,
            "cancel_reason": po.get("cancel_reason") if cancelled else None,
            "created_at": _utc(po["generated_at"]).isoformat(),
            "documents": documents,
        })
    return out


def read_feed(user: CurrentUser, cursor: Optional[str], limit: Optional[int]) -> dict:
    """One page of orders changed after `cursor`, oldest change first.

    `next_cursor` is the position to resume from. It advances past orders that
    were held back (still awaiting approval) as well as served ones, so a retry
    never re-reads them; when they are approved they come back by themselves.
    """
    try:
        after = core.decode_cursor(cursor)
    except ValueError:
        raise AppError("feed_cursor_invalid",
                       "The cursor was not issued by this feed; start without one",
                       status_code=400)
    size = core.clamp_page_size(limit)
    rows = query(
        """SELECT * FROM inventory_po_log
            WHERE tenant_id = %s AND erp_feed_seq > %s
              AND erp_feed_at <= clock_timestamp() - make_interval(secs => %s)
            ORDER BY erp_feed_seq
            LIMIT %s""",
        (user.tenant_id, after, core.FEED_HOLDBACK_SECONDS, size + 1),
    )
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = core.encode_cursor(rows[-1]["erp_feed_seq"]) if rows else (cursor or None)

    visible = wscope.filter_po_rows(user, rows)
    served = [po for po in visible
              if po.get("cancelled_at") is not None or _approval_allows(user.tenant_id, po)]
    return {
        "orders": build_documents(user.tenant_id, served),
        "next_cursor": next_cursor,
        "has_more": has_more,
        "held_back": len(visible) - len(served),
    }


def unacknowledged(user: CurrentUser) -> list[dict]:
    """Open, approved orders with a document the ERP has not confirmed yet."""
    rows = query(
        """SELECT * FROM inventory_po_log
            WHERE tenant_id = %s AND cancelled_at IS NULL
              AND reception_status IN ('pending', 'partial', 'not_received')
            ORDER BY generated_at DESC LIMIT 500""",
        (user.tenant_id,),
    )
    rows = [po for po in wscope.filter_po_rows(user, rows)
            if _approval_allows(user.tenant_id, po)]
    pending = []
    for order in build_documents(user.tenant_id, rows):
        missing = [d for d in order["documents"] if not d["acknowledged"]]
        if missing:
            pending.append({**order, "documents": missing})
    return pending


def documents_for_export(user: CurrentUser, po_log_ids: list[str]) -> list[dict]:
    """The documents of the requested approved, non-cancelled orders, for a file.
    Refuses to export an order that may not leave yet, naming it."""
    if not po_log_ids:
        raise AppError("export_no_orders", "Choose at least one order to export",
                       status_code=400)
    rows = query("SELECT * FROM inventory_po_log WHERE tenant_id = %s AND id = ANY(%s)",
                 (user.tenant_id, po_log_ids))
    found = {r["id"] for r in rows}
    missing = [i for i in po_log_ids if i not in found]
    if missing:
        raise AppError("po_not_found", "Purchase order not found", status_code=404,
                       params={"id": missing[0]})
    rows = wscope.filter_po_rows(user, rows)
    if len(rows) != len(found):
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    for po in rows:
        ref = format_po_number(po.get("po_number"), po["id"])
        if po.get("cancelled_at") is not None:
            raise AppError("export_order_cancelled", "A cancelled order cannot be exported",
                           status_code=409, params={"reference": ref})
        if not _approval_allows(user.tenant_id, po):
            raise AppError("po_approval_required",
                           "This order needs approval before it can be sent",
                           status_code=409, params={"reference": ref})
    docs: list[dict] = []
    for order in build_documents(user.tenant_id, rows):
        docs.extend(order["documents"])
    return docs


def acknowledge(tenant_id: str, user_id: str, po_log_id: str, reference: str,
                supplier_id: Optional[str]) -> dict:
    """Store the ERP's own document number for one supplier's document of an order.

    Idempotent on (order, reference). A different reference for a document that
    already has one, or a reference already used by another order, is refused:
    silently re-pointing it would break the link to a purchase that already exists.
    """
    try:
        ref = core.clean_reference(reference)
    except ValueError as exc:
        raise AppError("erp_reference_invalid",
                       "The reference is empty or longer than "
                       f"{core.MAX_REFERENCE_LENGTH} characters",
                       status_code=400) from exc
    key = core.supplier_key(supplier_id)

    po = query_one("SELECT id, cancelled_at, po_number FROM inventory_po_log "
                   "WHERE id = %s AND tenant_id = %s", (po_log_id, tenant_id))
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    keys = {core.supplier_key(r["supplier_id"]) for r in query(
        "SELECT supplier_id FROM inventory_po_items WHERE po_log_id = %s AND tenant_id = %s "
        "AND status IN %s", (po_log_id, tenant_id, _ORDERED))}
    if key not in keys:
        raise AppError("erp_reference_unknown_supplier",
                       "The order has no document for that supplier",
                       status_code=404, params={"supplier_id": key or None})

    with transaction() as conn:
        # Serialise two acknowledgements of the same order.
        query("SELECT id FROM inventory_po_log WHERE id = %s FOR UPDATE", (po_log_id,), conn=conn)
        existing = query_one(
            "SELECT reference FROM po_erp_references WHERE po_log_id = %s AND supplier_key = %s",
            (po_log_id, key), conn=conn)
        if existing:
            if existing["reference"] == ref:
                return {"changed": False, "po_log_id": po_log_id, "reference": ref,
                        "document_id": core.document_id(po_log_id, key)}
            raise AppError("erp_reference_conflict",
                           "This document already has a different ERP reference",
                           status_code=409,
                           params={"existing": existing["reference"], "given": ref})
        used = query_one(
            "SELECT po_log_id FROM po_erp_references WHERE tenant_id = %s AND reference = %s "
            "AND po_log_id <> %s", (tenant_id, ref, po_log_id), conn=conn)
        if used:
            raise AppError("erp_reference_in_use",
                           "That ERP reference already belongs to another order",
                           status_code=409, params={"reference": ref})
        execute("INSERT INTO po_erp_references (tenant_id, po_log_id, supplier_key, reference, "
                "created_by) VALUES (%s, %s, %s, %s, %s)",
                (tenant_id, po_log_id, key, ref, user_id), conn=conn)
        execute("UPDATE inventory_po_log SET erp_reference_at = NOW() WHERE id = %s",
                (po_log_id,), conn=conn)
    return {"changed": True, "po_log_id": po_log_id, "reference": ref,
            "document_id": core.document_id(po_log_id, key),
            "number": format_po_number(po.get("po_number"), po_log_id)}
