"""Billing persistence: customers, subscriptions, events — and the tier.

The only function here that changes `tenants.tier` is `_apply_tier`, and it is
only reached from `apply_event` (a webhook whose signature was verified) and
from `reconcile_tenant` (the hourly sweep, which only ever moves a tenant the
way the stored state already says: a grace window or a paid-up period that
ran out). Both run `entitlement.decide_tier`, so there is one rule.

Idempotency is the database's job, not a read-then-write: the event row is
INSERTed first with ON CONFLICT DO NOTHING inside the same transaction as the
state change. A replay — or the same event delivered twice at once — finds the
row and changes nothing. If processing fails the transaction rolls back, the
event row goes with it, and the provider's retry is processed for real.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.billing.entitlement import (
    SOURCE_BILLING, SubscriptionState, TierDecision, access_until, decide_tier,
    grace_until,
)
from backend.db.connection import execute, query, query_one, transaction
from backend.entitlements.plans import FREE, PAID
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Reads ────────────────────────────────────────────────────────────────────

def customer_id_for(tenant_id: str, provider: str) -> Optional[str]:
    row = query_one(
        "SELECT provider_customer_id FROM billing_customers "
        "WHERE tenant_id = %s AND provider = %s",
        (tenant_id, provider),
    )
    return row["provider_customer_id"] if row else None


def tenant_for_customer(provider: str, customer_id: str) -> Optional[str]:
    if not customer_id:
        return None
    row = query_one(
        "SELECT tenant_id FROM billing_customers "
        "WHERE provider = %s AND provider_customer_id = %s",
        (provider, customer_id),
    )
    return row["tenant_id"] if row else None


def tenant_for_subscription(provider: str, subscription_id: str) -> Optional[str]:
    if not subscription_id:
        return None
    row = query_one(
        "SELECT tenant_id FROM billing_subscriptions "
        "WHERE provider = %s AND provider_subscription_id = %s",
        (provider, subscription_id),
    )
    return row["tenant_id"] if row else None


def subscriptions_for(tenant_id: str, conn=None) -> list[dict]:
    return query(
        "SELECT * FROM billing_subscriptions WHERE tenant_id = %s "
        "ORDER BY updated_at DESC",
        (tenant_id,), conn=conn,
    )


def event_seen(provider: str, event_id: str) -> bool:
    return query_one(
        "SELECT 1 AS hit FROM billing_events WHERE provider = %s AND event_id = %s",
        (provider, event_id),
    ) is not None


def state_of(row: dict) -> SubscriptionState:
    return SubscriptionState(
        status=row.get("status") or "",
        current_period_end=row.get("current_period_end"),
        cancel_at_period_end=bool(row.get("cancel_at_period_end")),
        past_due_since=row.get("past_due_since"),
        ended_at=row.get("ended_at"),
    )


def governing_subscription(rows: list[dict], now: datetime) -> Optional[dict]:
    """The one subscription to show a person: one that grants access if any
    does (renewing before ending), else the most recently updated."""
    from backend.billing.entitlement import grants_access
    granting = [r for r in rows if grants_access(state_of(r), now)]
    if granting:
        granting.sort(key=lambda r: (not r.get("cancel_at_period_end"),
                                     r.get("status") == "active"), reverse=True)
        return granting[0]
    return rows[0] if rows else None


def describe(row: Optional[dict], now: datetime) -> Optional[dict]:
    """A subscription row as the status endpoint shows it. No provider ids
    beyond the provider's name: nothing a person needs, nothing to leak."""
    if not row:
        return None
    state = state_of(row)
    until = access_until(state, now)
    grace = grace_until(state, now)

    def iso(v):
        return v.isoformat() if v else None
    return {
        "provider": row["provider"],
        "status": state.status,
        "current_period_end": iso(row.get("current_period_end")),
        "cancel_at_period_end": state.cancel_at_period_end,
        "past_due_since": iso(row.get("past_due_since")),
        "grace_until": iso(grace),
        "access_until": iso(until),
    }


# ── Writes the checkout makes (never the tier) ───────────────────────────────

def link_customer(tenant_id: str, provider: str, customer_id: str, conn=None) -> bool:
    """Remember `customer_id` as this tenant's customer at `provider`.

    Refuses (returns False) when that customer already belongs to ANOTHER
    tenant: an event naming somebody else's customer must not graft it here.
    """
    if not customer_id:
        return False
    owner = query_one(
        "SELECT tenant_id FROM billing_customers "
        "WHERE provider = %s AND provider_customer_id = %s",
        (provider, customer_id), conn=conn,
    )
    if owner and owner["tenant_id"] != tenant_id:
        log.warning("[billing] %s customer already linked to another tenant; "
                    "not relinking to tenant=%s", provider, tenant_id)
        return False
    query_one(
        """INSERT INTO billing_customers (id, tenant_id, provider, provider_customer_id)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (tenant_id, provider) DO UPDATE
              SET provider_customer_id = EXCLUDED.provider_customer_id,
                  updated_at = NOW()
           RETURNING id""",
        (generate_id("bcus"), tenant_id, provider, customer_id), conn=conn,
    )
    return True


def record_pending_subscription(tenant_id: str, provider: str, subscription_id: str) -> None:
    """A PayPal subscription created and awaiting approval. Pending grants
    nothing and revokes nothing; it only lets the webhook find the tenant."""
    execute(
        """INSERT INTO billing_subscriptions
               (id, tenant_id, provider, provider_subscription_id, status, plan)
           VALUES (%s, %s, %s, %s, 'approval_pending', 'full')
           ON CONFLICT (provider, provider_subscription_id) DO NOTHING""",
        (generate_id("bsub"), tenant_id, provider, subscription_id),
    )


# ── Webhook application ──────────────────────────────────────────────────────

@dataclass
class Outcome:
    outcome: str
    tenant_id: Optional[str] = None
    previous_tier: Optional[str] = None
    decision: Optional[TierDecision] = None
    notes: dict = field(default_factory=dict)


def _insert_event(conn, provider: str, event_id: str, event_type: str,
                  event_created_at: Optional[datetime]) -> Optional[str]:
    row = query_one(
        """INSERT INTO billing_events
               (id, provider, event_id, event_type, event_created_at)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (provider, event_id) DO NOTHING
           RETURNING id""",
        (generate_id("bevt"), provider, event_id, event_type[:120], event_created_at),
        conn=conn,
    )
    return row["id"] if row else None


def _close_event(conn, row_id: str, outcome: str, tenant_id: Optional[str]) -> None:
    execute(
        "UPDATE billing_events SET processed_at = NOW(), outcome = %s, tenant_id = %s "
        "WHERE id = %s",
        (outcome, tenant_id, row_id), conn=conn,
    )


def record_unattributed(provider: str, event_id: str, event_type: str,
                        event_created_at: Optional[datetime], outcome: str) -> str:
    """Record a verified event that changes nothing (a type we do not act on,
    or one naming no tenant we know). Idempotent like everything else."""
    with transaction() as conn:
        row_id = _insert_event(conn, provider, event_id, event_type, event_created_at)
        if row_id is None:
            return "duplicate"
        _close_event(conn, row_id, outcome, None)
    return outcome


def _apply_tier(conn, tenant_id: str, tenant: dict, now: datetime) -> tuple[TierDecision, Optional[str]]:
    rows = subscriptions_for(tenant_id, conn=conn)
    decision = decide_tier(
        tenant.get("tier") or FREE, tenant.get("tier_source"),
        [state_of(r) for r in rows], now,
    )
    previous = tenant.get("tier")
    if decision.changed:
        execute(
            "UPDATE tenants SET tier = %s, tier_source = %s, tier_changed_at = %s "
            "WHERE id = %s",
            (decision.tier, decision.tier_source, now, tenant_id), conn=conn,
        )
    return decision, previous


def apply_event(
    provider: str,
    event_id: str,
    event_type: str,
    event_created_at: Optional[datetime],
    sub: dict,
    tenant_id: str,
    *,
    now: Optional[datetime] = None,
) -> Outcome:
    """Store one provider subscription state, carried by one verified event,
    and move the tier if the state says so. Exactly once per event id.

    `sub` is the provider subscription already normalized (stripe_api /
    paypal_api `normalize_subscription`), fetched fresh from the provider.
    """
    now = now or utcnow()
    result = Outcome("duplicate", tenant_id)
    with transaction() as conn:
        row_id = _insert_event(conn, provider, event_id, event_type, event_created_at)
        if row_id is None:
            return result

        from backend.entitlements.service import take_tenant_lock
        take_tenant_lock(tenant_id, conn)
        tenant = query_one(
            "SELECT id, tier, tier_source FROM tenants WHERE id = %s FOR UPDATE",
            (tenant_id,), conn=conn,
        )
        if not tenant:
            _close_event(conn, row_id, "ignored_unknown_tenant", None)
            return Outcome("ignored_unknown_tenant")

        sub_id = sub["provider_subscription_id"]
        existing = query_one(
            "SELECT * FROM billing_subscriptions "
            "WHERE provider = %s AND provider_subscription_id = %s FOR UPDATE",
            (provider, sub_id), conn=conn,
        )
        if existing and existing["tenant_id"] != tenant_id:
            log.warning("[billing] %s event %s names a subscription of another "
                        "tenant; ignored", provider, event_type)
            _close_event(conn, row_id, "ignored_tenant_mismatch", tenant_id)
            return Outcome("ignored_tenant_mismatch", tenant_id)

        last = existing.get("last_event_at") if existing else None
        if last is not None and event_created_at is not None and event_created_at < last:
            # Out of order: a newer event already set this subscription. The
            # older one is recorded so a replay of it is still a no-op, and
            # applied never — it cannot downgrade anybody.
            _close_event(conn, row_id, "stale_ignored", tenant_id)
            return Outcome("stale_ignored", tenant_id)

        status = sub["status"]
        stamp = event_created_at or now
        if status == "past_due":
            past_due_since = (existing or {}).get("past_due_since") or stamp
        else:
            past_due_since = None
        previous_status = (existing or {}).get("status")
        previous_cancel = bool((existing or {}).get("cancel_at_period_end"))
        newest = max(filter(None, (last, event_created_at))) if (last or event_created_at) else None

        if existing:
            execute(
                """UPDATE billing_subscriptions
                      SET status = %s,
                          plan = COALESCE(NULLIF(%s, ''), plan),
                          current_period_end = COALESCE(%s, current_period_end),
                          cancel_at_period_end = %s,
                          past_due_since = %s,
                          ended_at = COALESCE(%s, ended_at),
                          last_event_at = %s,
                          updated_at = NOW()
                    WHERE id = %s""",
                (status, sub.get("plan") or "", sub.get("current_period_end"),
                 bool(sub.get("cancel_at_period_end")), past_due_since,
                 sub.get("ended_at"), newest, existing["id"]),
                conn=conn,
            )
        else:
            execute(
                """INSERT INTO billing_subscriptions
                       (id, tenant_id, provider, provider_subscription_id, status,
                        plan, current_period_end, cancel_at_period_end,
                        past_due_since, ended_at, last_event_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (generate_id("bsub"), tenant_id, provider, sub_id, status,
                 sub.get("plan") or "full", sub.get("current_period_end"),
                 bool(sub.get("cancel_at_period_end")), past_due_since,
                 sub.get("ended_at"), newest),
                conn=conn,
            )

        if sub.get("provider_customer_id"):
            link_customer(tenant_id, provider, sub["provider_customer_id"], conn=conn)

        decision, previous_tier = _apply_tier(conn, tenant_id, dict(tenant), now)
        outcome = "applied_tier_changed" if decision.changed else "applied"
        _close_event(conn, row_id, outcome, tenant_id)
        result = Outcome(
            outcome, tenant_id, previous_tier, decision,
            notes={
                "status": status,
                "status_changed": status != previous_status,
                "cancel_changed": bool(sub.get("cancel_at_period_end")) != previous_cancel,
                "current_period_end": sub.get("current_period_end"),
                "grace_until": grace_until(
                    SubscriptionState(status=status, past_due_since=past_due_since), now,
                ) if status == "past_due" else None,
            },
        )

    # After the commit: an activity row is not worth rolling a payment back for,
    # and record_event writes on its own connection anyway.
    _announce(provider, result, event_type)
    return result


def _announce(provider: Optional[str], result: Outcome, event_type: str) -> None:
    """The activity rows for what just changed (audit + the bell).

    Webhook-driven, so the actor is "system": no person did this, a payment
    provider told us. `provider` is None for the sweep.
    """
    from backend.activity.events import record_event
    tenant_id = result.tenant_id
    if not tenant_id or result.decision is None:
        return
    d = result.decision
    resource = provider or "billing"
    if d.changed and d.tier == PAID:
        record_event(tenant_id, "system", "billing.plan_activated", resource=resource,
                     details={"provider": provider, "tier": d.tier,
                              "previous_tier": result.previous_tier},
                     reason="payment_confirmed_by_provider")
    elif d.changed and d.tier == FREE:
        record_event(tenant_id, "system", "billing.plan_downgraded", resource=resource,
                     details={"provider": provider, "tier": d.tier,
                              "previous_tier": result.previous_tier},
                     reason="subscription_lapsed")
    notes = result.notes
    if notes.get("status") == "past_due" and notes.get("status_changed"):
        grace = notes.get("grace_until")
        record_event(tenant_id, "system", "billing.payment_failed", resource=resource,
                     details={"provider": provider,
                              "grace_until": grace.isoformat() if grace else None},
                     reason="payment_failed_at_provider")
    elif not d.changed and (notes.get("status_changed") or notes.get("cancel_changed")):
        end = notes.get("current_period_end")
        record_event(tenant_id, "system", "billing.subscription_changed", resource=resource,
                     details={"provider": provider, "status": notes.get("status"),
                              "renews_at": end.isoformat() if end else None})


# ── The sweep: time passing is not an event ──────────────────────────────────

def reconcile_tenant(tenant_id: str, now: Optional[datetime] = None) -> Optional[TierDecision]:
    """Re-run the rule for one tenant against what is already stored.

    Needed because no provider sends "your grace period ended" or "the period
    you cancelled has run out" at the moment it happens. Only ever applies what
    the stored, verified state already implies.
    """
    now = now or utcnow()
    with transaction() as conn:
        from backend.entitlements.service import take_tenant_lock
        take_tenant_lock(tenant_id, conn)
        tenant = query_one(
            "SELECT id, tier, tier_source FROM tenants WHERE id = %s FOR UPDATE",
            (tenant_id,), conn=conn,
        )
        if not tenant:
            return None
        decision, previous = _apply_tier(conn, tenant_id, dict(tenant), now)
    if decision.changed:
        _announce(None, Outcome("reconciled", tenant_id, previous, decision), "sweep")
    return decision


def reconcile_all(now: Optional[datetime] = None) -> int:
    """The hourly sweep over every tenant billing manages. Returns how many
    tenants changed tier."""
    rows = query(
        """SELECT DISTINCT t.id FROM tenants t
             JOIN billing_subscriptions s ON s.tenant_id = t.id
            WHERE t.tier_source = %s AND t.tier = %s""",
        (SOURCE_BILLING, PAID),
    )
    changed = 0
    for row in rows:
        try:
            decision = reconcile_tenant(row["id"], now)
            if decision and decision.changed:
                changed += 1
        except Exception:  # noqa: BLE001 - one tenant must not stop the sweep
            log.exception("[billing] reconcile failed for tenant=%s", row["id"])
    return changed
