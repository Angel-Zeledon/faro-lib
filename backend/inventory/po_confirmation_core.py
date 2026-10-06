"""
Supplier confirmation link — the rules that need no database.

A buyer sends a purchase order; the supplier opens a link (no login) and, for
every order line, confirms the quantity and a promised delivery date, proposes
another date or quantity, or declines the line. Everything here is pure so the
parts that decide what is trusted can be tested without a server:

* the token: 256 random bits, only a hash is ever stored, compared in constant
  time, and a malformed string is rejected before it reaches the database;
* when a link stops working (21 days, or 7 days after the expected arrival,
  whichever is later);
* when a submission is locked, and when the buyer reopening it unlocks it;
* what status a line ends up with — derived by the SERVER from the numbers, never
  taken from the client, so a "confirmed" cannot carry a changed date;
* validation of the submitted payload (every line exactly once, bounded
  numbers, bounded text);
* which date drives the overdue logic once the buyer accepts a proposal.

Nothing here touches prices, costs or stock. The supplier's view is built from
the whitelist in `po_confirmation_service.portal_view`.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

# ── Token ────────────────────────────────────────────────────────────────────

TOKEN_BYTES = 32
# `token_urlsafe(32)` is 43 characters. The upper bound exists only to refuse a
# megabyte of garbage in the URL before hashing it.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{43,128}")


def new_token() -> str:
    """A fresh link credential: 32 random bytes, URL-safe."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 of the token. Salting buys nothing: the input is 256 random
    bits, so there is no dictionary to precompute against."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_is_wellformed(token: object) -> bool:
    return isinstance(token, str) and _TOKEN_SHAPE.fullmatch(token) is not None


def verify_token(presented: object, stored_hash: Optional[str]) -> bool:
    """Constant-time check of a presented token against a stored hash. A
    malformed token, or no stored hash, is simply False."""
    if not token_is_wellformed(presented) or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(presented), stored_hash)


def hash_client_value(value: str, secret: str) -> str:
    """Keyed hash of an address (or any identifier) that is worth comparing but
    not worth keeping: the ledger stores this, never the address itself."""
    mac = hmac.new(secret.encode("utf-8"), (value or "").encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:32]


# ── Expiry and locking ───────────────────────────────────────────────────────

MIN_TTL_DAYS = 21
DAYS_AFTER_ARRIVAL = 7


def compute_expiry(created_at: datetime, expected_arrival: Optional[date]) -> datetime:
    """21 days after the link was made, or 7 days after the order is expected to
    arrive, whichever is later: a supplier answering a long-lead order must not
    find the link dead before the goods are even due."""
    base = created_at + timedelta(days=MIN_TTL_DAYS)
    if expected_arrival is None:
        return base
    after = datetime.combine(
        expected_arrival, datetime.min.time(), tzinfo=created_at.tzinfo,
    ) + timedelta(days=DAYS_AFTER_ARRIVAL)
    return max(base, after)


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return now >= expires_at


def link_is_usable(*, expires_at: datetime, revoked_at: Optional[datetime],
                   now: datetime) -> bool:
    return revoked_at is None and not is_expired(expires_at, now)


def is_locked(submitted_at: Optional[datetime], reopened_at: Optional[datetime]) -> bool:
    """Locked once a submission exists, until the buyer reopens it. A reopening
    older than the last submission does not unlock it (a supplier who submits
    again after a reopening locks the page again)."""
    if submitted_at is None:
        return False
    if reopened_at is None:
        return True
    return reopened_at < submitted_at


# ── Statuses ─────────────────────────────────────────────────────────────────

CONFIRMED = "confirmed"
CHANGED = "changed"
DECLINED = "declined"
LINE_STATUSES = (CONFIRMED, CHANGED, DECLINED)

DECISION_CONFIRM = "confirm"
DECISION_DECLINE = "decline"
DECISIONS = (DECISION_CONFIRM, DECISION_DECLINE)

# Ceilings. A quantity is a number of units somebody could plausibly ship; the
# date window keeps a typo (year 2206) from becoming an expected arrival.
MAX_QTY = 1_000_000_000.0
MAX_NOTE_CHARS = 500
MAX_LINES = 500
MAX_BODY_BYTES = 128 * 1024
PAST_GRACE_DAYS = 1
MAX_DAYS_AHEAD = 730

_QTY_EPSILON = 1e-9


def derive_line_status(
    *, decision: str, ordered_qty: float, confirmed_qty: Optional[float],
    requested_date: Optional[date], promised_date: Optional[date],
) -> str:
    """The status a line gets. Decided here, from the numbers:

    * declined            — the supplier will not supply it;
    * confirmed           — the quantity AND the date are what was asked for;
    * changed             — anything else (another quantity, another date).

    With no requested date on file there is nothing to differ from, so only the
    quantity decides.
    """
    if decision == DECISION_DECLINE:
        return DECLINED
    qty_same = (
        confirmed_qty is not None
        and abs(float(confirmed_qty) - float(ordered_qty)) <= _QTY_EPSILON
    )
    date_same = requested_date is None or promised_date == requested_date
    return CONFIRMED if (qty_same and date_same) else CHANGED


def derive_overall(statuses: Iterable[str]) -> str:
    """One word for a whole order: 'pending' (nobody answered), 'declined'
    (every line), 'changed' (anything changed or declined), 'confirmed'."""
    s = list(statuses)
    if not s:
        return "pending"
    if all(x == DECLINED for x in s):
        return DECLINED
    if any(x in (CHANGED, DECLINED) for x in s):
        return CHANGED
    return CONFIRMED


# ── Payload validation ───────────────────────────────────────────────────────

class SubmissionInvalid(ValueError):
    """The submitted answer is unusable. `code` is a stable machine word and
    `params` carries the offending field, never prose."""

    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_note(note: object) -> Optional[str]:
    """Plain text, bounded. HTML is NOT stripped here — it is stored verbatim
    and every surface that prints it escapes it (React text nodes, `html.escape`
    in the e-mail); stripping would silently rewrite what the supplier typed."""
    if note is None:
        return None
    if not isinstance(note, str):
        raise SubmissionInvalid("note_invalid")
    text = _CONTROL_CHARS.sub("", note).strip()
    if len(text) > MAX_NOTE_CHARS:
        raise SubmissionInvalid("note_too_long", max=MAX_NOTE_CHARS)
    return text or None


def _parse_date(value: object, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise SubmissionInvalid("date_invalid", field=field)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SubmissionInvalid("date_invalid", field=field) from None


def _parse_qty(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SubmissionInvalid("quantity_invalid", field=field)
    qty = float(value)
    if math.isnan(qty) or math.isinf(qty) or qty <= 0 or qty > MAX_QTY:
        raise SubmissionInvalid("quantity_invalid", field=field)
    return qty


def validate_submission(
    raw_lines: object,
    lines_by_id: dict[str, dict],
    *,
    today: date,
    requested_date: Optional[date],
) -> list[dict]:
    """Check a submitted answer against the order's own lines.

    `lines_by_id` is {line id: {"ordered_qty": float}} for the lines of THIS
    request only — so a line id from another order is simply unknown. Every line
    must be answered exactly once; a partial answer would leave the buyer
    unable to tell "not answered" from "answered, unchanged".

    Returns the clean rows [{po_item_id, decision, status, confirmed_qty,
    promised_date, note}] in the order's own order. Raises `SubmissionInvalid`.
    """
    if not isinstance(raw_lines, list) or not raw_lines:
        raise SubmissionInvalid("lines_missing")
    if len(raw_lines) > MAX_LINES:
        raise SubmissionInvalid("too_many_lines", max=MAX_LINES)

    earliest = today - timedelta(days=PAST_GRACE_DAYS)
    latest = today + timedelta(days=MAX_DAYS_AHEAD)

    seen: dict[str, dict] = {}
    for item in raw_lines:
        if not isinstance(item, dict):
            raise SubmissionInvalid("line_invalid")
        line_id = item.get("line_id")
        if not isinstance(line_id, str) or line_id not in lines_by_id:
            raise SubmissionInvalid("line_unknown")
        if line_id in seen:
            raise SubmissionInvalid("line_duplicated")

        decision = item.get("decision")
        if decision not in DECISIONS:
            raise SubmissionInvalid("decision_invalid", field="decision")
        note = clean_note(item.get("note"))
        ordered = float(lines_by_id[line_id]["ordered_qty"])

        if decision == DECISION_DECLINE:
            seen[line_id] = {
                "po_item_id": line_id, "decision": decision, "status": DECLINED,
                "confirmed_qty": None, "promised_date": None, "note": note,
            }
            continue

        qty = _parse_qty(item.get("confirmed_qty"), "confirmed_qty")
        promised = _parse_date(item.get("promised_date"), "promised_date")
        if promised < earliest or promised > latest:
            raise SubmissionInvalid("date_out_of_range", field="promised_date")
        status = derive_line_status(
            decision=decision, ordered_qty=ordered, confirmed_qty=qty,
            requested_date=requested_date, promised_date=promised,
        )
        seen[line_id] = {
            "po_item_id": line_id, "decision": decision, "status": status,
            "confirmed_qty": qty, "promised_date": promised, "note": note,
        }

    if set(seen) != set(lines_by_id):
        raise SubmissionInvalid("lines_incomplete")
    return [seen[line_id] for line_id in lines_by_id]


# ── The promise that drives the overdue logic ────────────────────────────────

SOURCE_MODEL = "model"
SOURCE_SUPPLIER_PROMISE = "supplier_promise"


def expected_arrival_with_promises(
    model_expected: date, accepted_promises: Iterable[Optional[date]],
) -> tuple[date, str]:
    """The date an order is expected complete, for one (order, supplier).

    `accepted_promises` has ONE entry per ordered line: the promised date when
    the buyer accepted the supplier's proposal for that line, else None. A line
    nobody accepted keeps the model's date, and the order is expected when its
    EARLIEST line is: the "did it arrive?" nudge exists to catch something that
    should already be here, so accepting a late promise for one line must never
    hide an unaccepted line that is already overdue. With no accepted promise at
    all this returns the model date untouched, which is exactly what the product
    did before this feature.

    The source says where the returned date came from: the model, or a promise a
    person accepted.
    """
    promises = list(accepted_promises)
    if not any(p is not None for p in promises):
        return model_expected, SOURCE_MODEL
    dates = [p if p is not None else model_expected for p in promises]
    result = min(dates)
    unaccepted_present = any(p is None for p in promises)
    if result == model_expected and unaccepted_present:
        return result, SOURCE_MODEL
    return result, SOURCE_SUPPLIER_PROMISE


def promise_slip_days(promised: date, received: date) -> int:
    """Days the goods arrived after the accepted promise (negative: early)."""
    return (received - promised).days
