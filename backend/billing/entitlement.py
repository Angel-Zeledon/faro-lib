"""Subscription state -> tier. The ONE place that decides; pure, no I/O.

Everything that can change a tenant's tier because of a payment goes through
`decide_tier`: the webhooks after they store what the provider said, and the
hourly sweep that notices a grace period or a paid-up period running out (no
provider sends an event for "time passed").

The rules, in the order they are applied:

1. **`corporate` and `demo` are never touched.** Corporate is quoted and set by
   hand; demo is a throwaway trial that cannot buy.
2. **A `paid` tier somebody set by hand stays.** Only a tenant whose tier was
   set by billing (`tier_source = 'billing'`) is managed by billing — the
   grandfather rule. An operator's handshake deal is not undone by a webhook.
3. **Any one subscription that grants access is enough.** A tenant that moved
   from PayPal to Stripe has a cancelled one and an active one; the active one
   decides.
4. What grants access:

   ===========================  ==========================================
   normalized status            access
   ===========================  ==========================================
   active, trialing             yes (until current_period_end when it is
                                set to cancel at period end)
   past_due                     yes for GRACE_DAYS from past_due_since
   canceled                     yes until ended_at, else current_period_end
   unpaid, expired, refunded,   no
   incomplete_expired, paused
   incomplete, approval_pending undecided: never changes the tier
   anything else                no (fail closed: never hand out paid)
   ===========================  ==========================================

5. **Losing access is a downgrade to `free`, never a deletion.** The ceilings
   only refuse NEW creation; every row the tenant already has stays readable.
   That is the permanent-data rule, and it is why this function returns a tier
   and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from backend.entitlements.plans import CORPORATE, DEMO, FREE, PAID

GRACE_DAYS = 7

SOURCE_MANUAL = "manual"
SOURCE_BILLING = "billing"

ENTITLED_STATUSES = frozenset({"active", "trialing"})
GRACE_STATUSES = frozenset({"past_due"})
ENDING_STATUSES = frozenset({"canceled"})
LAPSED_STATUSES = frozenset({
    "unpaid", "expired", "refunded", "incomplete_expired", "paused",
})
# A checkout that has not been paid yet. It must neither grant nor revoke:
# a tenant on `paid` that opens a second checkout and abandons it keeps paid.
PENDING_STATUSES = frozenset({"incomplete", "approval_pending"})

KNOWN_STATUSES = (
    ENTITLED_STATUSES | GRACE_STATUSES | ENDING_STATUSES | LAPSED_STATUSES
    | PENDING_STATUSES
)


@dataclass(frozen=True)
class SubscriptionState:
    """What we know about one provider subscription, already normalized."""

    status: str
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    past_due_since: Optional[datetime] = None
    ended_at: Optional[datetime] = None


@dataclass(frozen=True)
class TierDecision:
    tier: str
    tier_source: str
    changed: bool
    # English identifier, never prose: why the tier is what it is.
    reason: str


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def grace_until(sub: SubscriptionState, now: datetime) -> Optional[datetime]:
    """End of the past-due grace window, or None when not past due.

    A past-due subscription whose start we never saw is graced from `now`:
    the generous reading, bounded, rather than an instant downgrade for a
    timestamp we failed to store.
    """
    if sub.status not in GRACE_STATUSES:
        return None
    since = _utc(sub.past_due_since) or _utc(now)
    return since + timedelta(days=GRACE_DAYS)


def access_until(sub: SubscriptionState, now: datetime) -> Optional[datetime]:
    """When this subscription stops granting access, if that is known.

    None means either "open-ended" (an active subscription that renews) or
    "never granted" — `grants_access` is what tells the two apart.
    """
    if sub.status in ENTITLED_STATUSES:
        return _utc(sub.current_period_end) if sub.cancel_at_period_end else None
    if sub.status in GRACE_STATUSES:
        return grace_until(sub, now)
    if sub.status in ENDING_STATUSES:
        return _utc(sub.ended_at) or _utc(sub.current_period_end)
    return None


def grants_access(sub: SubscriptionState, now: datetime) -> bool:
    now = _utc(now)
    if sub.status in ENTITLED_STATUSES:
        if sub.cancel_at_period_end and sub.current_period_end is not None:
            return now < _utc(sub.current_period_end)
        return True
    if sub.status in GRACE_STATUSES:
        return now < grace_until(sub, now)
    if sub.status in ENDING_STATUSES:
        until = access_until(sub, now)
        return until is not None and now < until
    # Lapsed, pending, or a status nobody mapped: never grants.
    return False


def purchase_block(
    current_tier: str,
    tier_source: Optional[str],
    has_access: bool,
    providers_enabled: bool,
) -> Optional[str]:
    """Why this tenant may NOT start a checkout right now, or None if it may.

    The codes are the wire contract (`errors.<code>` on the frontend):

    - billing_not_configured       no provider is configured on this installation
    - billing_trial_account        a throwaway trial: register a real account first
    - billing_corporate_plan       corporate is quoted, never bought online
    - billing_plan_managed_manually  `paid` set by hand: nothing to buy
    - billing_subscription_active  already paying: manage it instead
    """
    if not providers_enabled:
        return "billing_not_configured"
    if current_tier == DEMO:
        return "billing_trial_account"
    if current_tier == CORPORATE:
        return "billing_corporate_plan"
    if current_tier == PAID and (tier_source or SOURCE_MANUAL) != SOURCE_BILLING:
        return "billing_plan_managed_manually"
    if has_access:
        return "billing_subscription_active"
    return None


def decide_tier(
    current_tier: str,
    tier_source: Optional[str],
    subscriptions: Iterable[SubscriptionState],
    now: datetime,
) -> TierDecision:
    """The tier this tenant should be on, given what billing knows."""
    source = tier_source or SOURCE_MANUAL
    unchanged = lambda reason: TierDecision(current_tier, source, False, reason)  # noqa: E731

    if current_tier in (CORPORATE, DEMO):
        return unchanged("tier_not_managed")
    if current_tier not in (FREE, PAID):
        # A drifted column. Not ours to repair, and never ours to upgrade.
        return unchanged("tier_not_managed")
    if current_tier == PAID and source != SOURCE_BILLING:
        return unchanged("manual_grant")

    decisive = [s for s in subscriptions if s.status not in PENDING_STATUSES]
    if not decisive:
        return unchanged("no_decisive_subscription")

    granting = [s for s in decisive if grants_access(s, now)]
    if granting:
        if any(s.status in ENTITLED_STATUSES and not s.cancel_at_period_end for s in granting):
            reason = "subscription_active"
        elif any(s.status in GRACE_STATUSES for s in granting):
            reason = "subscription_in_grace"
        else:
            reason = "paid_until_period_end"
        if current_tier == PAID:
            return TierDecision(PAID, SOURCE_BILLING, False, reason)
        return TierDecision(PAID, SOURCE_BILLING, True, reason)

    if current_tier == FREE:
        return unchanged("subscription_lapsed")
    # Billing-managed paid, and nothing grants any more: back to free. The data
    # stays; only new creation above the free ceilings is refused.
    return TierDecision(FREE, SOURCE_BILLING, True, "subscription_lapsed")
