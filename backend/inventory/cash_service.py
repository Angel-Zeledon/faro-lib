"""
Cash calendar / accounts payable (feature 3.6).

The binding constraint for a LatAm SMB distributor is cash, not information: an
order can be perfectly justified by the semáforo and still be impossible to pay
this week. This module turns two things StockAI already has — sent POs and supplier
payment terms — into "this week $X falls due; the recommended purchase fits /
does not fit".

Free text in, structure out
---------------------------
`suppliers.payment_terms` is free text ("30 días", "contado", "a convenir") and
stays that way: it is what the user typed and the only record when parsing
fails. `parse_payment_terms_days` derives `payment_terms_days` from it, and
anything it cannot read stays None — reported to the user as "missing terms"
rather than defaulted to some invented number, because a wrong due date is worse
than an acknowledged gap in a cash forecast.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from backend.db.connection import query

log = logging.getLogger(__name__)

MAX_CREDIT_DAYS = 365

# Terms meaning "pay on the spot" — zero credit days, not unknown.
#
# `anticip` rather than `anticipad`: the stem covers "anticipado" AND the far
# more common noun form "anticipo". Without it "50% anticipo" fell through to
# the number extractor and was reported as 50 DAYS of credit — an invoice dated
# seven weeks after money that is actually due on the spot, and reported with
# `terms_known: True` on top.
_IMMEDIATE_RE = re.compile(
    r"contado|cash|anticip|adelant|prepag|inmediat|contra\s?entrega|\bcod\b",
    re.IGNORECASE,
)
_MONTHS_RE = re.compile(r"(\d+)\s*mes", re.IGNORECASE)
_FORTNIGHT_RE = re.compile(r"quincen", re.IGNORECASE)
# Two numbers joined by x, / or -: "2x30" (two instalments of 30 days),
# "30/60/90" (three dated instalments), "30-45 días" (a range). A schedule has
# no single credit-day answer, and the first number in it is never that answer:
# the old parser read "2x30" as 2 days and "30/60/90" as 30. These are exactly
# the strings the module docstring promises to leave as None.
_INSTALMENTS_RE = re.compile(r"\d+\s*[x/×-]\s*\d+", re.IGNORECASE)
# A number carrying a % sign is a share of the invoice, not a day count.
_PERCENT_RE = re.compile(r"\d+\s*%")
_NUMBER_RE = re.compile(r"\d+")


def parse_payment_terms_days(text: Optional[str]) -> Optional[int]:
    """
    Credit days from free-text payment terms, or None when unreadable.

    Rule order matters and mirrors the SQL backfill in db/migrations.py
    ('backfill_suppliers_payment_terms_days'):

      1. "contado" / "contra entrega" / "anticipo" / "prepago" / "COD"  -> 0
         Checked first because "pago de contado a 8 dias" must not be read as
         8 days of credit, and "50% anticipo" must not be read as 50.
      2. an instalment schedule ("2x30", "30/60/90", "30-45")           -> None
         Before every number rule: the first number in a schedule is not the
         credit period, so answering with it is worse than admitting the gap.
      3. "N mes(es)"                                       -> N * 30
         Before the generic number rule, otherwise "2 meses" reads as 2 days.
      4. "quincenal"                                       -> 15
      5. first number found ("30 días", "net 30", "30d")   -> N
         Percentages are stripped before this rule runs: in "50% a 30 dias" the
         day count is 30, and in a bare "50%" there is no day count at all.
      6. anything else ("a convenir")                      -> None

    Results are clamped to 0..365: a typo of "3000 días" must not push an
    invoice a decade into the future.
    """
    if text is None:
        return None
    raw = text.strip()
    if not raw:
        return None

    if _IMMEDIATE_RE.search(raw):
        return 0

    if _INSTALMENTS_RE.search(raw):
        return None

    months = _MONTHS_RE.search(raw)
    if months:
        return min(int(months.group(1)) * 30, MAX_CREDIT_DAYS)

    if _FORTNIGHT_RE.search(raw):
        return 15

    number = _NUMBER_RE.search(_PERCENT_RE.sub(" ", raw))
    if number:
        return min(int(number.group(0)), MAX_CREDIT_DAYS)

    return None


def resolve_credit_days(supplier: Optional[dict]) -> Optional[int]:
    """
    Credit days for a supplier: the structured column when set, otherwise parsed
    on the fly from the free text. The second path matters for rows written
    before the column existed and for anything the backfill could not read at
    the time but a later parser improvement can.
    """
    if not supplier:
        return None
    days = supplier.get("payment_terms_days")
    if days is not None:
        return int(days)
    return parse_payment_terms_days(supplier.get("payment_terms"))


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _suppliers_by_name(tenant_id: str) -> dict[str, dict]:
    """
    Every supplier of the tenant keyed by lower-cased name — INCLUDING the
    deactivated ones.

    Deactivating a supplier is a statement about future purchases ("stop
    ordering from them"); it says nothing about the invoices they already
    issued. Filtering them out here moved a sent-and-unpaid PO into
    `unknown_terms` and out of `committed_total`, so archiving a supplier card
    silently shrank the money the buyer still owes — the one number this module
    exists to keep honest. `supplier_service` filters on `active` on purpose
    (a PO must not auto-send to a supplier the business dropped); accounts
    payable is the opposite case and must not inherit that filter.

    `COALESCE(active, TRUE) DESC` decides a key collision only case can produce
    ("Andina" and "andina" are two rows under UNIQUE (tenant_id, name)): the
    active card is the one still being maintained. The column is nullable, and
    a NULL there has always meant active.
    """
    by_name: dict[str, dict] = {}
    for supplier in query(
        "SELECT * FROM suppliers WHERE tenant_id = %s "
        "ORDER BY COALESCE(active, TRUE) DESC, name",
        (tenant_id,),
    ):
        by_name.setdefault((supplier["name"] or "").strip().lower(), supplier)
    return by_name


def get_payables(tenant_id: str, horizon_days: int = 30) -> dict:
    """
    Invoices coming due from POs already SENT to a supplier.

    Only sent POs count: an unsent draft is not a commitment to anybody, and
    including it would inflate the very number the buyer is using to decide
    whether they can afford one more order. The invoice clock runs from
    `sent_at`, so due_date = sent_at + the supplier's credit days.

    A PO can span several suppliers, so payables are grouped by (PO, supplier):
    each supplier invoices its own lines under its own terms.
    """
    rows = query(
        """SELECT l.id            AS po_log_id,
                  l.sent_at,
                  i.supplier,
                  SUM(i.final_qty * COALESCE(i.unit_cost, 0)) AS amount
             FROM inventory_po_log l
             JOIN inventory_po_items i ON i.po_log_id = l.id
            WHERE l.tenant_id = %s
              AND l.sent_at IS NOT NULL
              AND i.status IN ('approved', 'modified')
              AND i.final_qty > 0
            GROUP BY l.id, l.sent_at, i.supplier
            ORDER BY l.sent_at""",
        (tenant_id,),
    )

    suppliers_by_name = _suppliers_by_name(tenant_id)

    today = _today()
    horizon_end = today + timedelta(days=horizon_days)

    due_items: list[dict] = []
    unknown_terms: list[dict] = []

    for r in rows:
        amount = round(float(r["amount"] or 0), 2)
        if amount <= 0:
            continue
        name = (r.get("supplier") or "").strip()
        supplier = suppliers_by_name.get(name.lower())
        credit_days = resolve_credit_days(supplier)

        if credit_days is None:
            # No usable terms: we know money is owed but not when. Surfacing it
            # separately keeps the dated total honest and tells the user exactly
            # which supplier card to complete to fix the gap.
            unknown_terms.append({
                "po_log_id": r["po_log_id"],
                "supplier_name": name or None,
                "amount": amount,
                "payment_terms": (supplier or {}).get("payment_terms"),
            })
            continue

        sent_at = r["sent_at"]
        sent_date = sent_at.date() if isinstance(sent_at, datetime) else sent_at
        due_date = sent_date + timedelta(days=credit_days)
        due_items.append({
            "po_log_id": r["po_log_id"],
            "supplier_name": name or None,
            "amount": amount,
            "sent_date": sent_date.isoformat(),
            "credit_days": credit_days,
            "due_date": due_date.isoformat(),
            "days_until_due": (due_date - today).days,
            "overdue": due_date < today,
            "within_horizon": today <= due_date <= horizon_end,
        })

    due_items.sort(key=lambda d: d["due_date"])

    overdue_total = round(sum(d["amount"] for d in due_items if d["overdue"]), 2)
    horizon_total = round(sum(d["amount"] for d in due_items if d["within_horizon"]), 2)
    this_week_total = round(
        sum(
            d["amount"] for d in due_items
            if not d["overdue"] and 0 <= d["days_until_due"] <= 7
        ),
        2,
    )

    return {
        "today": today.isoformat(),
        "horizon_days": horizon_days,
        "due_items": due_items,
        "weeks": _bucket_by_week(due_items, today, horizon_days),
        "overdue_total": overdue_total,
        "this_week_total": this_week_total,
        "horizon_total": horizon_total,
        "unknown_terms": unknown_terms,
        "unknown_terms_total": round(sum(u["amount"] for u in unknown_terms), 2),
    }


def _bucket_by_week(due_items: list[dict], today: date, horizon_days: int) -> list[dict]:
    """Rolling 7-day buckets from today, so 'this week' means the next seven
    days rather than whatever is left of the calendar week."""
    weeks: list[dict] = []
    week_count = max(1, (horizon_days + 6) // 7)
    for w in range(week_count):
        start = today + timedelta(days=7 * w)
        end = start + timedelta(days=6)
        amount = sum(
            d["amount"] for d in due_items
            if not d["overdue"] and start.isoformat() <= d["due_date"] <= end.isoformat()
        )
        weeks.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "amount": round(amount, 2),
        })
    return weeks


def evaluate_purchase_fit(
    tenant_id: str,
    purchase_lines: list[dict],
    budget: Optional[float] = None,
    horizon_days: int = 30,
) -> dict:
    """
    "Does the recommended purchase fit?" — the payables already committed plus
    what this purchase would add, against the cash the user says they have.

    The subtlety worth the code: a purchase does NOT necessarily hit the same
    window it is placed in. Ordering today from a supplier with 30-day terms
    costs nothing this month; the same order from a cash-on-delivery supplier
    hits immediately. So each line is dated by ITS supplier's credit days
    (assuming the PO is sent today) and only the part landing inside the horizon
    is counted against the budget. Lines whose supplier has no usable terms are
    counted as due immediately — the conservative side of an unknown, since
    telling a buyer an order fits when it might be cash-on-delivery is the
    expensive mistake.

    `budget` is user-supplied; StockAI stores no cash balance. Without it the
    committed and purchase totals are still returned, and `fits` is None
    (unknown), never a guess.
    """
    payables = get_payables(tenant_id, horizon_days)

    suppliers_by_name = _suppliers_by_name(tenant_id)

    today = _today()
    horizon_end = today + timedelta(days=horizon_days)

    purchase_total = 0.0
    purchase_in_horizon = 0.0
    assumed_immediate: list[str] = []
    lines_out: list[dict] = []

    for line in purchase_lines:
        quantity = float(line.get("quantity") or 0)
        unit_cost = float(line.get("unit_cost") or 0)
        amount = quantity * unit_cost
        if amount <= 0:
            continue
        name = (line.get("supplier_name") or "").strip()
        supplier = suppliers_by_name.get(name.lower())
        credit_days = resolve_credit_days(supplier)
        terms_known = credit_days is not None
        if not terms_known:
            credit_days = 0
            if name and name not in assumed_immediate:
                assumed_immediate.append(name)

        due_date = today + timedelta(days=credit_days)
        in_horizon = due_date <= horizon_end
        purchase_total += amount
        if in_horizon:
            purchase_in_horizon += amount

        lines_out.append({
            "sku": line.get("sku"),
            "supplier_name": name or None,
            "amount": round(amount, 2),
            "credit_days": credit_days,
            "terms_known": terms_known,
            "due_date": due_date.isoformat(),
            "within_horizon": in_horizon,
        })

    committed = round(payables["horizon_total"] + payables["overdue_total"], 2)
    purchase_in_horizon = round(purchase_in_horizon, 2)
    required = round(committed + purchase_in_horizon, 2)

    fits: Optional[bool] = None
    shortfall: Optional[float] = None
    if budget is not None:
        fits = required <= float(budget)
        shortfall = round(max(0.0, required - float(budget)), 2)

    return {
        "today": today.isoformat(),
        "horizon_days": horizon_days,
        "budget": round(float(budget), 2) if budget is not None else None,
        "committed_total": committed,
        "overdue_total": payables["overdue_total"],
        "this_week_total": payables["this_week_total"],
        "purchase_total": round(purchase_total, 2),
        "purchase_in_horizon": purchase_in_horizon,
        "required_total": required,
        "fits": fits,
        "shortfall": shortfall,
        "lines": lines_out,
        "suppliers_assumed_immediate": assumed_immediate,
        "unknown_terms_total": payables["unknown_terms_total"],
        "weeks": payables["weeks"],
    }
