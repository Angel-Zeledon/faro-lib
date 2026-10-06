//! `backend/activity/service.py::log_action` and the slice of
//! `backend/activity/events.py::record_event` the migrated routes use.
//!
//! Same table (`activity_logs`), same id format (`act_` + 12 hex of a uuid4),
//! same context shape (whitelisted detail keys, then `severity` and `kind`).
//! The event vocabulary is NOT duplicated wholesale: only the events a Rust
//! route records are declared here, each with its spec copied from Python,
//! and an undeclared action is a compile-time impossibility because callers
//! pass an [`Event`] variant, not a string.

use serde_json::{Map, Value};
use sqlx::PgPool;

/// `generate_id(prefix)`: `<prefix>_` + the first 12 hex chars of a uuid4.
pub fn generate_id(prefix: &str) -> String {
    let hex = uuid::Uuid::new_v4().simple().to_string();
    format!("{prefix}_{}", &hex[..12])
}

/// `log_action`. Writes one row; the error is returned so callers can decide
/// (record_event swallows it, like Python).
pub async fn log_action(
    pool: &PgPool,
    tenant_id: &str,
    user_id: &str,
    action: &str,
    resource: Option<&str>,
    context: &Value,
    status: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO activity_logs (id, tenant_id, user_id, action, resource, context, status, created_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())",
    )
    .bind(generate_id("act"))
    .bind(tenant_id)
    .bind(user_id)
    .bind(action)
    .bind(resource)
    .bind(context)
    .bind(status)
    .execute(pool)
    .await?;
    Ok(())
}

#[derive(Debug, Clone, Copy)]
pub enum Event {
    CommittedDemandCreated,
    CommittedDemandImported,
    CommittedDemandChanged,
    PurchaseOrderPaid,
    PurchaseOrderUnpaid,
    PurchaseOrderCancelled,
    PurchaseOrderUncancelled,
    ApprovalApproved,
    ApprovalRejected,
    ApiKeyCreated,
    ApiKeyRevoked,
    SpikeExcluded,
    SpikeRestored,
    ApprovalDelegationCreated,
    ApprovalDelegationRevoked,
    CustomerPortalLinkCreated,
    CustomerPortalLinkRevoked,
    CustomerPortalLinkReopened,
    CustomerPortalLinkUpdated,
    CustomerPortalPromiseSet,
    CustomerPortalReceived,
    CustomerPortalDateObjected,
    IpAllowlistChanged,
    IpAccessRefused,
    MfaEnrolled,
    MfaDisabled,
    MfaRecoveryCodesRegenerated,
    MfaReset,
    MfaPolicyChanged,
}

impl Event {
    /// (action, kind, severity, detail_keys) exactly as declared in EVENTS.
    fn spec(self) -> (&'static str, &'static str, &'static str, &'static [&'static str]) {
        match self {
            Event::PurchaseOrderPaid => ("purchase.order_paid", "purchase", "info", &["reference"]),
            Event::PurchaseOrderUnpaid => ("purchase.order_unpaid", "purchase", "warning", &["reference"]),
            Event::PurchaseOrderCancelled => (
                "purchase.order_cancelled", "purchase", "warning", &["reference", "cancel_reason"],
            ),
            Event::PurchaseOrderUncancelled => ("purchase.order_uncancelled", "purchase", "warning", &["reference"]),
            Event::ApprovalApproved => (
                "purchase.approval_approved", "purchase", "info", &["reference", "value", "decision_comment", "on_behalf_of"],
            ),
            Event::ApprovalRejected => (
                "purchase.approval_rejected", "purchase", "info", &["reference", "value", "decision_comment", "on_behalf_of"],
            ),
            Event::CommittedDemandCreated => (
                "committed_demand.created", "purchase", "info",
                &["sku", "quantity", "delivery_date", "customer"],
            ),
            Event::CommittedDemandImported => ("committed_demand.imported", "purchase", "info", &["rows"]),
            Event::CommittedDemandChanged => ("committed_demand.changed", "purchase", "info", &["sku", "status"]),
            Event::ApiKeyCreated => ("account.api_key_created", "account", "warning", &["key_name", "role"]),
            Event::ApiKeyRevoked => ("account.api_key_revoked", "account", "warning", &["key_name"]),
            Event::SpikeExcluded => ("forecast.spike_excluded", "training", "info", &["sku", "period", "spike_reason"]),
            Event::SpikeRestored => ("forecast.spike_restored", "training", "info", &["sku", "period", "spike_reason"]),
            Event::ApprovalDelegationCreated => (
                "approval_delegation.created", "purchase", "info", &["delegate", "starts_on", "ends_on"],
            ),
            Event::ApprovalDelegationRevoked => ("approval_delegation.revoked", "purchase", "info", &["delegate"]),
            Event::CustomerPortalLinkCreated => ("customer_portal.link_created", "purchase", "info", &["customer"]),
            Event::CustomerPortalLinkRevoked => ("customer_portal.link_revoked", "purchase", "info", &["customer"]),
            Event::CustomerPortalLinkReopened => ("customer_portal.link_reopened", "purchase", "info", &["customer"]),
            Event::CustomerPortalLinkUpdated => ("customer_portal.link_updated", "purchase", "info", &["customer"]),
            Event::CustomerPortalPromiseSet => (
                "customer_portal.promise_set", "purchase", "info", &["customer", "sku", "promised_date"],
            ),
            Event::CustomerPortalReceived => ("customer_portal.received", "purchase", "info", &["customer", "sku"]),
            Event::CustomerPortalDateObjected => (
                "customer_portal.date_objected", "purchase", "warning", &["customer", "sku", "delivery_date"],
            ),
            Event::IpAllowlistChanged => ("account.ip_allowlist_changed", "account", "warning", &["cidr", "label"]),
            Event::IpAccessRefused => ("account.ip_access_refused", "account", "warning", &["ip"]),
            Event::MfaEnrolled => ("account.mfa_enrolled", "account", "info", &[]),
            Event::MfaDisabled => ("account.mfa_disabled", "account", "warning", &[]),
            Event::MfaRecoveryCodesRegenerated => ("account.mfa_recovery_codes_regenerated", "account", "info", &[]),
            Event::MfaReset => ("account.mfa_reset", "account", "warning", &["email"]),
            Event::MfaPolicyChanged => ("account.mfa_policy_changed", "account", "warning", &["mfa_required"]),
        }
    }
}

/// The context `record_event` stores: allowed detail keys that are not None,
/// in spec order, then severity and kind.
pub fn event_context(event: Event, details: &Map<String, Value>) -> Value {
    let (_, kind, severity, keys) = event.spec();
    let mut ctx = Map::new();
    for k in keys {
        if let Some(v) = details.get(*k) {
            if !v.is_null() {
                ctx.insert((*k).to_string(), v.clone());
            }
        }
    }
    let undeclared: Vec<&String> = details.keys().filter(|k| !keys.contains(&k.as_str())).collect();
    if !undeclared.is_empty() {
        tracing::warn!(?undeclared, "record_event: undeclared detail keys will not reach the feed");
    }
    ctx.insert("severity".into(), Value::String(severity.into()));
    ctx.insert("kind".into(), Value::String(kind.into()));
    Value::Object(ctx)
}

/// `record_event` for an INFO event without a reason. Never fails the caller: a lost audit row is logged at ERROR.
pub async fn record_event(
    pool: &PgPool,
    tenant_id: &str,
    user_id: &str,
    event: Event,
    resource: Option<&str>,
    details: Map<String, Value>,
) {
    record_event_with_reason(pool, tenant_id, user_id, event, resource, details, None).await
}

/// `record_event(..., reason=...)`: the context gains `reason` after
/// `severity` and `kind`. Python refuses a non-INFO event without a reason
/// (a ValueError at the call site); the Rust callers of warning events always
/// pass one, and a debug assertion keeps it that way.
pub async fn record_event_with_reason(
    pool: &PgPool,
    tenant_id: &str,
    user_id: &str,
    event: Event,
    resource: Option<&str>,
    details: Map<String, Value>,
    reason: Option<&str>,
) {
    let (action, _, severity, _) = event.spec();
    debug_assert!(severity == "info" || reason.is_some(), "{action} is {severity} and must carry a reason");
    let mut ctx = event_context(event, &details);
    if let (Some(r), Value::Object(m)) = (reason.filter(|r| !r.is_empty()), &mut ctx) {
        m.insert("reason".into(), Value::String(r.into()));
    }
    if let Err(e) = log_action(pool, tenant_id, user_id, action, resource, &ctx, "success").await {
        tracing::error!(error = %e, action, tenant = tenant_id, "record_event: could not record");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn ids_look_like_python_generate_id() {
        let id = generate_id("act");
        assert!(id.starts_with("act_"));
        assert_eq!(id.len(), 16);
        assert!(id[4..].chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn purchase_order_contexts_match_python() {
        let d = json!({"reference": "OC-000007", "cancel_reason": null});
        let ctx = event_context(Event::PurchaseOrderCancelled, d.as_object().unwrap());
        assert_eq!(
            serde_json::to_string(&ctx).unwrap(),
            r#"{"reference":"OC-000007","severity":"warning","kind":"purchase"}"#
        );
        let ctx = event_context(Event::PurchaseOrderPaid, d.as_object().unwrap());
        assert_eq!(ctx["severity"], "info");
    }

    #[test]
    fn context_keeps_declared_non_null_keys_in_order() {
        let d = json!({"customer": "", "quantity": 5.0, "sku": "A", "delivery_date": null, "extra": 1});
        let ctx = event_context(Event::CommittedDemandCreated, d.as_object().unwrap());
        assert_eq!(
            serde_json::to_string(&ctx).unwrap(),
            r#"{"sku":"A","quantity":5.0,"customer":"","severity":"info","kind":"purchase"}"#
        );
    }
}
