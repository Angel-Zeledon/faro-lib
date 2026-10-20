"""The decisions behind the purchase-order feed, as pure functions.

Free of the database and of HTTP so each rule is pinned by a test that a mock
cannot fool: the cursor, the identity of a document and of a line, and how an
order's lines split into the one document per supplier an ERP raises.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

# A feed row younger than this is not served yet. A transaction that took a lower
# sequence number but commits a moment later would otherwise be skipped by a reader
# that already moved its cursor past it.
FEED_HOLDBACK_SECONDS = 10

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

MAX_REFERENCE_LENGTH = 100

_CURSOR_PREFIX = "po_"

# What the feed says about an order. `open` covers pending and not_received (nothing
# has arrived yet); the ERP only needs to tell open, partly received, closed and
# cancelled apart.
STATE_OPEN = "open"
STATE_PARTIAL = "partially_received"
STATE_RECEIVED = "received"
STATE_CANCELLED = "cancelled"


def encode_cursor(seq: int) -> str:
    return f"{_CURSOR_PREFIX}{int(seq)}"


def decode_cursor(raw: Optional[str]) -> int:
    """The sequence a cursor stands for; no cursor means "from the beginning".
    Anything that is not a cursor this module issued is refused, never guessed."""
    if raw is None or raw == "":
        return 0
    text = str(raw).strip()
    if not text.startswith(_CURSOR_PREFIX) or not text[len(_CURSOR_PREFIX):].isdigit():
        raise ValueError("not a cursor issued by this feed")
    return int(text[len(_CURSOR_PREFIX):])


def clamp_page_size(raw: Optional[int]) -> int:
    if raw is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(raw), MAX_PAGE_SIZE))


def order_state(reception_status: Optional[str], cancelled: bool) -> str:
    if cancelled:
        return STATE_CANCELLED
    if reception_status == "received":
        return STATE_RECEIVED
    if reception_status == "partial":
        return STATE_PARTIAL
    return STATE_OPEN


def supplier_key(supplier_id: Optional[str]) -> str:
    """The key an ERP reference is stored under; '' for lines with no supplier."""
    return (supplier_id or "").strip()


def document_id(po_log_id: str, key: str) -> str:
    """Stable id of one supplier's document inside an order. The same on every
    export of the same order, so an importer can refuse a duplicate."""
    return f"{po_log_id}:{key or '-'}"


def clean_reference(raw: Any) -> str:
    text = str(raw or "").replace("\x00", "").strip()
    if not text:
        raise ValueError("empty reference")
    if len(text) > MAX_REFERENCE_LENGTH:
        raise ValueError("reference too long")
    return text


def common_currency(currencies: Iterable[Optional[str]]) -> tuple[Optional[str], bool]:
    """(currency of the document, mixed). One currency across every priced line is
    the document's; two different ones leave it None and flag it, because an ERP
    purchase order has a single currency and picking one would misprice a line."""
    seen = {c for c in currencies if c}
    if len(seen) == 1:
        return next(iter(seen)), False
    return None, len(seen) > 1


def split_documents(lines: list[dict]) -> list[dict]:
    """Group an order's ordered lines into one document per supplier, suppliers in
    name order and unassigned lines last. Each line must carry `supplier_id`."""
    groups: dict[str, list[dict]] = {}
    for line in lines:
        groups.setdefault(supplier_key(line.get("supplier_id")), []).append(line)

    def sort_key(k: str) -> tuple[int, str]:
        name = (groups[k][0].get("supplier") or "").lower()
        return (1 if not k else 0, name)

    return [{"supplier_key": k, "lines": groups[k]} for k in sorted(groups, key=sort_key)]


def line_total(quantity: float, unit_cost: Optional[float]) -> Optional[float]:
    """None when nobody recorded a cost: unknown is not zero (po_pdf.py's rule)."""
    if unit_cost is None:
        return None
    return round(float(quantity) * float(unit_cost), 2)


def iso_day(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None
