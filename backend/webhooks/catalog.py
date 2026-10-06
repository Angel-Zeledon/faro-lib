"""The outbound webhook events and the stable shape of what they carry.

Pure: no database, no HTTP. `test_webhook_catalog.py` pins every `data_keys`
tuple, because a receiver's code is written against these names - renaming or
dropping one is a breaking change that must be a deliberate `API_VERSION` bump,
never a side effect of refactoring an emitter.

Envelope, identical for every event (JSON, UTF-8):

    {
      "id":          "evt_<32 hex>",         # same across retries and hooks
      "type":        "purchase_order.sent",
      "api_version": "2026-10-05",
      "occurred_at": "2026-10-05T14:03:11Z", # ISO 8601, UTC
      "tenant_id":   "<tenant id>",
      "data":        { ...exactly the keys listed for the type... }
    }

`data` carries ids, numbers and the supplier / customer / warehouse names the
product already shows - never an e-mail address, phone number or free text a
person typed (comments, notes). A key with no value is present with null, so a
receiver never has to test for a missing key.

Delivery headers (see `signing.py` for the signature):

    X-StockAI-Event:      the `type`
    X-StockAI-Delivery:   unique per delivery row (retries of one delivery
                          share it; use it to log, use `id` to deduplicate)
    X-StockAI-Signature:  t=<unix seconds>,v1=<hex HMAC-SHA256>
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

API_VERSION = "2026-10-05"


@dataclass(frozen=True)
class EventType:
    name: str
    data_keys: tuple[str, ...]
    # Whether the event is ABOUT a warehouse. Such an event reaches only the
    # webhooks whose scope includes that warehouse (or that are company-wide);
    # an event with no warehouse (a company-wide commitment) reaches only the
    # company-wide hooks, so a scoped hook never learns a company total.
    warehouse_aware: bool
    # Offered in the "events to subscribe" list. The test event is not.
    subscribable: bool = True


_PO_KEYS = ("po_log_id", "po_number", "warehouse", "warehouse_id", "sku_count",
            "total_units", "total_value")

EVENT_TYPES: dict[str, EventType] = {e.name: e for e in (
    EventType("job.completed", ("job_id", "session_id"), warehouse_aware=False),
    EventType("job.failed", ("job_id", "session_id", "error"), warehouse_aware=False),
    EventType("purchase_order.approved", _PO_KEYS + ("approved_amount", "decided_by"),
              warehouse_aware=True),
    EventType("purchase_order.rejected", _PO_KEYS + ("decided_by",), warehouse_aware=True),
    EventType("purchase_order.sent", _PO_KEYS + ("sent_at",), warehouse_aware=True),
    EventType("purchase_order.cancelled", _PO_KEYS + ("cancelled_at", "cancelled_by"),
              warehouse_aware=True),
    EventType("stockout.imminent",
              ("sku", "warehouse", "warehouse_id", "signal", "current_stock",
               "coverage_days", "reorder_point", "detected_on"),
              warehouse_aware=True),
    EventType("commitment.at_risk",
              ("commitment_id", "sku", "warehouse", "warehouse_id", "customer",
               "delivery_date", "quantity", "shortfall", "latest_safe_order_date",
               "order_date_passed"),
              warehouse_aware=True),
    EventType("commitment.fulfilled",
              ("commitment_id", "sku", "warehouse", "warehouse_id", "customer",
               "delivery_date", "quantity", "fulfilled_at"),
              warehouse_aware=True),
    EventType("webhook.test", ("webhook_id",), warehouse_aware=False, subscribable=False),
)}

# What a webhook may subscribe to: only events some code path really emits.
SUPPORTED_EVENTS: frozenset[str] = frozenset(
    name for name, e in EVENT_TYPES.items() if e.subscribable)


def shape_data(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """`data` with exactly the declared keys: missing ones as None, and an
    undeclared key refused (a field nobody documented must not leak out)."""
    spec = EVENT_TYPES[event_type]
    extra = set(data) - set(spec.data_keys)
    if extra:
        raise ValueError(f"{event_type}: undeclared data keys {sorted(extra)}")
    return {k: data.get(k) for k in spec.data_keys}


def _iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_envelope(event_type: str, tenant_id: str, data: dict[str, Any], *,
                   event_id: Optional[str] = None,
                   occurred_at: Optional[datetime] = None) -> dict[str, Any]:
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex}",
        "type": event_type,
        "api_version": API_VERSION,
        "occurred_at": _iso_utc(occurred_at or datetime.now(timezone.utc)),
        "tenant_id": tenant_id,
        "data": shape_data(event_type, data),
    }
