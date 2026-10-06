"""
The single vocabulary of things StockAI tells the user it did, and why.

Before this module the activity log recorded almost nothing: one row for a
deleted session, one per API-key call, and one per scheduled send. Everything
else the product does — a training that failed at 3 a.m., a sync the data gate
refused, a ceiling that stopped a write, a purchase order that reached nobody,
an import that dropped 37 rows — happened in silence, and the only trace was a
line in a log file the tenant cannot read. The owner's instruction (2026-09-16)
is that the user should always know what happened AND why.

Three rules this module exists to enforce:

1. **One action name, declared once.** A typo'd action name would write a row
   nothing can ever read back, which is the silent failure this whole feature
   is meant to end. `record_event` refuses an action that is not declared here.

2. **Every event carries its WHY as a code, not as prose.** `reason` is an
   English identifier the frontend renders through `events.reason.<code>` with
   `reason_params` interpolated — the same contract as `AppError`. No Spanish
   in backend logic, and a translator can reorder the sentence.

3. **Severity decides where it surfaces, not whether it is recorded.**
   Everything is recorded. `critical` and `warning` reach the bell, because
   they need a decision; `info` is the history the activity screen shows. A
   bell that lists every successful import is a bell people stop reading, and
   an event that is only in a log file may as well not exist. Both failure
   modes are avoided by recording everything and routing by severity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# Severities, in the order the UI ranks them.
CRITICAL = "critical"   # something the user relies on is broken or wrong now
WARNING  = "warning"    # it still works, but it will stop or mislead soon
INFO     = "info"       # it worked; here is what happened

SEVERITIES = (CRITICAL, WARNING, INFO)

# Severities that reach the bell. The rest are history, not an interruption.
BELL_SEVERITIES = (CRITICAL, WARNING)


@dataclass(frozen=True)
class EventSpec:
    """One thing that can happen, and how it is presented.

    `kind` groups actions the user thinks of as one thing (a training that
    finished and one that failed are both "training"), so the UI can filter by
    kind without knowing every action name.

    `detail_keys` is a WHITELIST, for the same reason `alert_history` has one:
    the context blob is written by many call sites and must not start leaking
    whatever a future one puts in it. It is also the exact set of params the
    frontend's i18n string may interpolate, so a copy change and a payload
    change cannot drift apart unnoticed.
    """
    kind:        str
    severity:    str
    detail_keys: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")


# ── The vocabulary ────────────────────────────────────────────────────────────
#
# Grouped by the question the user is asking when they look. Adding an entry
# here is half the work: `test_events_vocabulary.py` fails until the frontend
# catalogue has copy for the action and for every reason code it can carry.

EVENTS: dict[str, EventSpec] = {
    # ── Forecasting ──────────────────────────────────────────────────────────
    "training.completed": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("session_id", "session_name", "skus", "best_model", "period"),
    ),
    # Critical, not warning: every screen in the product reads the active
    # session. A training that failed overnight means the buyer opens the app
    # to yesterday's numbers, or to nothing at all, with no other signal.
    "training.failed": EventSpec(
        kind="training", severity=CRITICAL,
        detail_keys=("session_id", "session_name", "started_by"),
    ),
    "training.blocked": EventSpec(
        kind="training", severity=WARNING,
        detail_keys=("session_id", "session_name", "issues"),
    ),
    # Raised ONCE per degradation episode by the realised-accuracy tracker
    # (forecast_check/tracking.py): the forecast is doing materially worse
    # against sales that arrived after training than it did at training time.
    # A notification only; nothing retrains by itself.
    "forecast.accuracy_degraded": EventSpec(
        kind="training", severity=WARNING,
        detail_keys=("session_id", "session_name", "degradation_pct"),
    ),
    # A person changed what a product's forecast says for a period. Recorded
    # next to the immutable adjustment row so the activity feed answers "who
    # moved this number, and why" without opening the precision screen.
    "forecast.adjusted": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("sku", "adjustment", "adjustment_reason"),
    ),
    # A person marked a past period of a product as a one-off (excluded from the
    # baseline at the next training), or undid that mark.
    "forecast.spike_excluded": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("sku", "period", "spike_reason"),
    ),
    "forecast.spike_restored": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("sku", "period", "spike_reason"),
    ),
    # A person said a new product will sell like others (forecast by analogy),
    # or undid that statement.
    "forecast.analogy_defined": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("sku", "references"),
    ),
    "forecast.analogy_reverted": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("sku", "references"),
    ),
    # Demand plan versions (inventory/demand_plan_service.py): a frozen plan and
    # its sign-off. A record and a measurement; none of these moves a purchase.
    "demand_plan.created": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("plan_name", "skus", "periods"),
    ),
    "demand_plan.submitted": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("plan_name",),
    ),
    "demand_plan.approved": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("plan_name", "decision_comment", "superseded"),
    ),
    "demand_plan.rejected": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("plan_name", "decision_comment"),
    ),
    "demand_plan.commented": EventSpec(
        kind="training", severity=INFO,
        detail_keys=("plan_name",),
    ),

    # ── Purchasing ───────────────────────────────────────────────────────────
    # Customer orders placed ahead of time, entered by a person. They move the
    # purchase recommendation, so who entered or closed one is recorded.
    "committed_demand.created": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("sku", "quantity", "delivery_date", "customer"),
    ),
    "committed_demand.imported": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("rows",),
    ),
    "committed_demand.changed": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("sku", "status"),
    ),
    # Blanket contracts: every save is a new revision, so the feed names who
    # created, revised, activated, closed or cancelled one.
    "supply_contract.created": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("customer", "lines", "revision", "status"),
    ),
    "supply_contract.revised": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("customer", "lines", "revision", "status"),
    ),
    "supply_contract.status_changed": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("customer", "lines", "revision", "status"),
    ),
    # Purchase budgets (inventory/purchase_budget_service.py): who set or changed
    # a cap, and every order that went past what a cap had left (with the reason
    # a person gave, when an administrator overrode a hard cap).
    "purchase_budget.created": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("budget_scope", "amount", "period"),
    ),
    "purchase_budget.revised": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("budget_scope", "amount", "period"),
    ),
    "purchase_budget.exceeded": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("budget_scope", "over_by", "override_reason", "reference"),
    ),
    "purchase_budget.override": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("budget_scope", "over_by", "override_reason", "reference"),
    ),
    "purchase.order_generated": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "lines", "value", "suppliers"),
    ),
    "purchase.order_sent": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "sent", "skipped"),
    ),
    # The buyer believes the order is on its way. It is not.
    "purchase.order_not_sent": EventSpec(
        kind="purchase", severity=CRITICAL,
        detail_keys=("reference", "skipped"),
    ),
    # The supplier answered the confirmation link (inventory/po_confirmation_
    # service.py). Everything confirmed is history; a proposed change or a
    # declined line needs the buyer's decision, so it reaches the bell.
    "purchase.supplier_confirmed": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "supplier", "confirmed"),
    ),
    "purchase.supplier_changes_proposed": EventSpec(
        kind="purchase", severity=WARNING,
        detail_keys=("reference", "supplier", "confirmed", "changed", "declined"),
    ),
    # A person accepted a supplier's promised date: it now drives that order's
    # expected arrival. Recorded under their name, because it moves a decision.
    "purchase.supplier_change_accepted": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "supplier", "sku", "promised_date"),
    ),
    "purchase.supplier_link_reopened": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "supplier"),
    ),
    "purchase.supplier_link_revoked": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "supplier"),
    ),
    "purchase.reception_recorded": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "sku_count", "units", "warehouse"),
    ),
    # Reverses a reception: stock moved back out, a lead time un-learned.
    # Warning, not info — unlike a routine reception this is a correction of
    # something already acted on (the semáforo and the scorecard both moved on
    # the strength of the original reception), and the tenant should see it
    # without having to go looking in the plain history feed.
    "purchase.reception_undone": EventSpec(
        kind="purchase", severity=WARNING,
        detail_keys=("reference", "sku_count", "units", "warehouse"),
    ),
    # Reverses `sent_at`. Same reasoning as reception_undone: it un-anchors the
    # cash calendar, so it belongs on the bell, not only in the quiet feed.
    # (It no longer changes incoming stock: every open PO counts as on its way,
    # sent or not — see service.get_incoming_detail.)
    "purchase.order_unsent": EventSpec(
        kind="purchase", severity=WARNING,
        detail_keys=("reference",),
    ),
    # The buyer said the order's invoice is settled: it leaves the cash
    # calendar. Info — a routine bookkeeping step, like a reception.
    "purchase.order_paid": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference",),
    ),
    # Puts money back on the cash calendar, so it reaches the bell like every
    # other reversal.
    "purchase.order_unpaid": EventSpec(
        kind="purchase", severity=WARNING,
        detail_keys=("reference",),
    ),
    # The order is abandoned: its units stop counting as on the way, so the
    # semáforo may ask for them again. Warning, because every recommendation
    # for its SKUs moves on the strength of it.
    "purchase.order_cancelled": EventSpec(
        kind="purchase", severity=WARNING,
        detail_keys=("reference", "cancel_reason"),
    ),
    "purchase.order_uncancelled": EventSpec(
        kind="purchase", severity=WARNING,
        detail_keys=("reference",),
    ),
    # The approval workflow (opt-in: only tenants with an approval rule ever
    # see these). Info on purpose: the approver is reached through the bell's
    # attention rows and an email, which are addressed to THEM; a tenant-wide
    # bell entry for every request would be noise for everyone else.
    "purchase.approval_requested": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "value"),
    ),
    "purchase.approval_approved": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "value", "decision_comment"),
    ),
    "purchase.approval_rejected": EventSpec(
        kind="purchase", severity=INFO,
        detail_keys=("reference", "value", "decision_comment"),
    ),

    # ── Sales received by e-mail (backend/inbound_email/) ────────────────────
    "inbound_email.ingested": EventSpec(
        kind="data", severity=INFO,
        detail_keys=("filename", "email"),
    ),
    # The file is stored but nobody has said which column is the date, the
    # product and the quantity of THIS file. Never guessed: the user confirms
    # the columns in the app.
    "inbound_email.needs_review": EventSpec(
        kind="data", severity=WARNING,
        detail_keys=("filename", "email"),
    ),
    # A teammate mailed a file and it was not taken (too big, unreadable,
    # over the plan's ceiling). Strangers never reach this: they are logged
    # and never answered.
    "inbound_email.rejected": EventSpec(
        kind="data", severity=WARNING,
        detail_keys=("filename", "email"),
    ),

    # ── Connected databases (SQL data sources) ──────────────────────────────
    # A scheduled retraining could not read its connected database, so it did
    # not train (rather than train on stale data). Critical: the forecast the
    # buyer reads tomorrow is the old one, and the only other trace was a row
    # in the schedule's history nobody opens.
    "data.sql_refresh_failed": EventSpec(
        kind="data", severity=CRITICAL,
        detail_keys=("source_name",),
    ),

    # ── Data the tenant put in ───────────────────────────────────────────────
    "data.stock_imported": EventSpec(
        kind="data", severity=INFO,
        detail_keys=("rows_read", "rows_written", "duplicate_rows", "rejected_rows"),
    ),
    # Rows the file had and the database did not get. Not an error the import
    # can refuse — the rest of the file is good — but the user has to be told,
    # because "83 imported" after a 120-row preview is the only signal today.
    "data.stock_import_partial": EventSpec(
        kind="data", severity=WARNING,
        detail_keys=("rows_read", "rows_written", "duplicate_rows", "rejected_rows"),
    ),
    # Bulk imports of suppliers and of purchase orders. Same two-event shape as
    # the stock import: a clean one is history, one that dropped rows is a
    # warning the user is told about.
    "data.suppliers_imported": EventSpec(
        kind="data", severity=INFO,
        detail_keys=("rows_read", "rows_written", "duplicate_rows", "rejected_rows"),
    ),
    "data.suppliers_import_partial": EventSpec(
        kind="data", severity=WARNING,
        detail_keys=("rows_read", "rows_written", "duplicate_rows", "rejected_rows"),
    ),
    "data.orders_imported": EventSpec(
        kind="data", severity=INFO,
        detail_keys=("rows_read", "rows_written", "duplicate_rows", "rejected_rows"),
    ),
    "data.orders_import_partial": EventSpec(
        kind="data", severity=WARNING,
        detail_keys=("rows_read", "rows_written", "duplicate_rows", "rejected_rows"),
    ),
    "data.shrinkage_recorded": EventSpec(
        kind="data", severity=INFO,
        detail_keys=("sku", "quantity", "warehouse", "shrinkage_reason"),
    ),
    # A physical count applied: one document, so counted rather than named.
    "data.stock_count_applied": EventSpec(
        kind="data", severity=INFO,
        detail_keys=("warehouse", "lines", "units"),
    ),
    # A transfer is one document with many lines, so it is counted, not named:
    # a per-SKU event would put ten rows in the feed for one decision.
    "data.transfer_created": EventSpec(
        kind="data", severity=INFO,
        detail_keys=("sku_count", "units", "from_warehouse", "to_warehouse"),
    ),

    # ── Ceilings ─────────────────────────────────────────────────────────────
    # A refused write the user may be watching (the screen says so) or may not
    # (the nightly sync). Recorded either way, so the second case stops being
    # invisible.
    "limit.reached": EventSpec(
        kind="limit", severity=WARNING,
        detail_keys=("limit", "ceiling", "attempted_by"),
    ),

    # ── Account and access ───────────────────────────────────────────────────
    "account.user_invited": EventSpec(
        kind="account", severity=INFO, detail_keys=("email", "role"),
    ),
    "account.user_role_changed": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email", "role", "previous_role"),
    ),
    "account.user_deactivated": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email",),
    ),
    "account.api_key_created": EventSpec(
        kind="account", severity=WARNING, detail_keys=("key_name", "role"),
    ),
    "account.api_key_revoked": EventSpec(
        kind="account", severity=WARNING, detail_keys=("key_name",),
    ),
    # Social sign-in (backend/auth/social/). A new way into an account is a
    # warning, not history: "I did not link Google" is something only the owner
    # can notice, and only if it is put where they look.
    "account.signed_up_with_provider": EventSpec(
        kind="account", severity=INFO, detail_keys=("provider", "email"),
    ),
    "account.provider_linked": EventSpec(
        kind="account", severity=WARNING, detail_keys=("provider", "email"),
    ),
    "account.provider_unlinked": EventSpec(
        kind="account", severity=WARNING, detail_keys=("provider", "email"),
    ),
    # Enterprise sign-on (backend/auth/sso/) and warehouse scopes. Who got in
    # through the company provider, who was created by it, whose role a group
    # changed, and every change of the configuration or of what a person may
    # see: the things an administrator is asked about in an audit.
    "account.sso_sign_in": EventSpec(
        kind="account", severity=INFO, detail_keys=("email",),
    ),
    "account.sso_user_created": EventSpec(
        kind="account", severity=INFO, detail_keys=("email", "role"),
    ),
    "account.sso_role_mapped": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email", "role", "previous_role"),
    ),
    "account.sso_sign_in_refused": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email",),
    ),
    "account.sso_config_changed": EventSpec(
        kind="account", severity=WARNING,
        detail_keys=("issuer", "enabled", "enforce_sso", "domains"),
    ),
    "account.sso_config_removed": EventSpec(
        kind="account", severity=WARNING, detail_keys=("issuer",),
    ),
    "account.warehouse_scope_changed": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email", "warehouses"),
    ),
    # An outbound webhook that failed on several different days was switched
    # off (backend/webhooks/service.py). Warning, with the host so the admin
    # knows which receiver to fix before re-enabling it.
    "webhook.auto_disabled": EventSpec(
        kind="account", severity=WARNING, detail_keys=("host",),
    ),
    # SCIM provisioning (backend/scim/). The actor is "scim": the company's
    # identity provider did these, not a person in the app. Losing or regaining
    # access and a changed role are warnings - the things an admin is asked
    # about - while a created person or a renamed one is history.
    "account.scim_user_created": EventSpec(
        kind="account", severity=INFO, detail_keys=("email", "role"),
    ),
    "account.scim_user_updated": EventSpec(
        kind="account", severity=INFO, detail_keys=("email", "changes"),
    ),
    "account.scim_user_deactivated": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email",),
    ),
    "account.scim_user_reactivated": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email",),
    ),
    "account.scim_role_changed": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email", "role", "previous_role"),
    ),
    # A write the provider asked for and this product refused (last admin,
    # ceiling, another tenant's address...). The code is a reason param.
    "account.scim_request_refused": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email", "operation"),
    ),
    "account.scim_token_created": EventSpec(
        kind="account", severity=WARNING, detail_keys=("manage_admins", "rotated"),
    ),
    "account.scim_token_revoked": EventSpec(
        kind="account", severity=WARNING, detail_keys=(),
    ),
    "account.scim_settings_changed": EventSpec(
        kind="account", severity=WARNING, detail_keys=("manage_admins",),
    ),
    # Session and password policy (backend/auth/session_policy.py; the admin
    # routes live in the Rust API). A change to what every person of the tenant
    # must satisfy is a warning, with the names of the settings that moved (never
    # a value that could weaken a guess); a lockout is the thing an admin is
    # asked about the next morning, and an unlock says who lifted it.
    "account.session_policy_changed": EventSpec(
        kind="account", severity=WARNING, detail_keys=("changed",),
    ),
    "account.user_locked_out": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email", "attempts"),
    ),
    "account.user_unlocked": EventSpec(
        kind="account", severity=WARNING, detail_keys=("email",),
    ),

    # ── Paying for the plan (backend/billing/) ───────────────────────────────
    # Written by a VERIFIED provider webhook (or the hourly sweep applying what
    # one already stored), with "system" as the actor: no person did these.
    "billing.plan_activated": EventSpec(
        kind="billing", severity=INFO,
        detail_keys=("provider", "tier", "previous_tier"),
    ),
    # Critical: everything above the free ceilings stops accepting new rows
    # (nothing is deleted), and the admin has to hear it from us first.
    "billing.plan_downgraded": EventSpec(
        kind="billing", severity=CRITICAL,
        detail_keys=("provider", "tier", "previous_tier"),
    ),
    # A payment bounced. The plan stays until `grace_until`; this is the
    # moment to fix the card, not after the downgrade.
    "billing.payment_failed": EventSpec(
        kind="billing", severity=WARNING,
        detail_keys=("provider", "grace_until"),
    ),
    # Renewal set to stop, resumed, or another state change that did not move
    # the tier (yet).
    "billing.subscription_changed": EventSpec(
        kind="billing", severity=INFO,
        detail_keys=("provider", "status", "renews_at"),
    ),
}


# ── Reason codes ──────────────────────────────────────────────────────────────
#
# The WHY. Declared, not free text, so the frontend can render it in the user's
# language and so a reason cannot quietly become a sentence written in backend
# logic. Every code here needs `events.reason.<code>` in the frontend
# catalogue; the vocabulary test enforces it.

REASONS: tuple[str, ...] = (
    # training refusals
    "engine_error",
    "dataset_missing",
    "data_gate_blocked",
    # a scheduled retrain skipped because the day's training ceiling is spent
    "training_cap_reached",
    # the live forecast's error against real sales vs its training-time error
    "realised_accuracy_below_training",
    # delivery
    "supplier_has_no_contact",
    "no_transport_configured",
    "transport_error",
    # a supplier answered the confirmation link with a different date/quantity
    # or declined a line; nothing applies until the buyer accepts it
    "supplier_proposed_changes",
    # ceilings
    "plan_limit_reached",
    # account and access — the WHY of a role change or a new machine credential
    # is that a person with admin rights did it. Said out loud, because the
    # only useful reaction to "I did not do that" is to look at who has access.
    "changed_by_an_account_admin",
    # the tenant's lockout policy locked the account after repeated wrong passwords
    "too_many_failed_logins",
    # social sign-in: the provider vouched for the address, so the account
    # gained that way in; and the variant where the password nobody had
    # verified was dropped because the provider proved the mailbox.
    "linked_at_provider_sign_in",
    "linked_unverified_password_removed",
    "unlinked_by_the_account_owner",
    # enterprise sign-on: a group at the company provider changed a role, and a
    # sign-in the tenant's own provider flow refused (the code is a param)
    "mapped_from_identity_provider_groups",
    "sso_sign_in_refused",
    # SCIM: the company's identity provider made the change, and a change it
    # asked for that was refused (the code is a param)
    "provisioned_by_identity_provider",
    "scim_request_refused",
    # imports
    "rows_rejected_by_validation",
    "duplicate_rows_collapsed",
    # reversals — the WHY of an un-receive or un-send is that a person decided
    # the original action was a mistake and corrected it themselves.
    "reversed_by_user",
    # a person cancelled the order themselves
    "cancelled_by_user",
    # sales received by e-mail
    "inbound_columns_unconfirmed",
    "inbound_unreadable_file",
    "inbound_file_too_large",
    "inbound_duplicate_file",
    "inbound_no_usable_attachment",
    # paying for the plan: the provider confirmed a payment, a subscription
    # ran out (cancelled period over, grace over, expired), a charge bounced
    "payment_confirmed_by_provider",
    "subscription_lapsed",
    "payment_failed_at_provider",
    # connected databases: the scheduled refresh could not read the source;
    # `reason_params.error_code` names the failure for the screen to explain
    "sql_source_refresh_failed",
    # an outbound webhook gave up on `days` different days in a row
    "webhook_failing_for_days",
    # generic tail — an event whose cause the call site genuinely does not know
    "unknown",
)


def spec_for(action: str) -> EventSpec:
    try:
        return EVENTS[action]
    except KeyError:
        raise ValueError(
            f"unknown event action {action!r} — declare it in "
            f"backend/activity/events.py before recording it"
        ) from None


def record_event(
    tenant_id: str,
    user_id: str,
    action: str,
    *,
    resource: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
    reason_params: Optional[dict[str, Any]] = None,
    status: str = "success",
) -> None:
    """Record one thing that happened, with its reason.

    `reason` is required for anything that is not `INFO`: a warning the user
    cannot act on is worse than silence, because it costs attention and returns
    nothing. The call site knows why; the user should not have to guess.

    Never raises on a write failure. These calls sit inside the paths they
    describe — a reception, a sync, a training — and an audit row is not worth
    failing a reception over. A write that fails is logged at ERROR, which is
    the one place a silent failure here is acceptable, because the alternative
    is losing the user's actual work.
    """
    spec = spec_for(action)
    if spec.severity != INFO and not reason:
        raise ValueError(
            f"{action!r} is {spec.severity} and must carry a reason: a warning "
            f"the user cannot act on costs attention and returns nothing"
        )
    if reason is not None and reason not in REASONS:
        raise ValueError(
            f"unknown reason {reason!r} — declare it in REASONS "
            f"(backend/activity/events.py)"
        )

    supplied = details or {}
    unknown_keys = set(supplied) - set(spec.detail_keys)
    if unknown_keys:
        # Loud in tests, harmless in production: the row is still written with
        # the keys the spec allows. A detail the feed cannot show is a copy bug,
        # not a reason to lose the event.
        log.warning(
            "record_event: %s carried undeclared detail keys %s — they will not "
            "reach the feed", action, sorted(unknown_keys),
        )

    context: dict[str, Any] = {
        k: supplied[k] for k in spec.detail_keys if supplied.get(k) is not None
    }
    context["severity"] = spec.severity
    context["kind"] = spec.kind
    if reason:
        context["reason"] = reason
    if reason_params:
        context["reason_params"] = reason_params

    try:
        from backend.activity.service import log_action
        log_action(tenant_id, user_id, action, resource=resource,
                   context=context, status=status)
    except Exception:  # noqa: BLE001 — see the docstring
        log.exception("record_event: could not record %s for tenant=%s", action, tenant_id)
