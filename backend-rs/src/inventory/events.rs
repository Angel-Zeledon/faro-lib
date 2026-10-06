//! The activity events the wave-3 routes record. Same table, same context
//! shape as `backend/activity/events.py::record_event` (declared detail keys
//! that are not None, in spec order, then `severity`, `kind`, `reason`).
//!
//! Kept in this module (not in `activity::Event`) so the wave-3 branch does not
//! edit a file other migration work also extends; `context` is unit-tested
//! against `activity::event_context`'s documented shape.

use serde_json::{Map, Value};
use sqlx::PgPool;

use crate::activity::log_action;

pub struct Spec {
    pub action: &'static str,
    pub kind: &'static str,
    pub severity: &'static str,
    pub keys: &'static [&'static str],
}

/// `data.stock_count_applied`.
pub const STOCK_COUNT_APPLIED: Spec = Spec {
    action: "data.stock_count_applied", kind: "data", severity: "info",
    keys: &["warehouse", "lines", "units"],
};
/// `purchase.reception_undone`.
pub const RECEPTION_UNDONE: Spec = Spec {
    action: "purchase.reception_undone", kind: "purchase", severity: "warning",
    keys: &["reference", "sku_count", "units", "warehouse"],
};
/// `purchase.order_unsent`.
pub const ORDER_UNSENT: Spec = Spec {
    action: "purchase.order_unsent", kind: "purchase", severity: "warning",
    keys: &["reference"],
};

/// `purchase.reception_recorded`.
pub const RECEPTION_RECORDED: Spec = Spec {
    action: "purchase.reception_recorded", kind: "purchase", severity: "info",
    keys: &["reference", "sku_count", "units", "warehouse"],
};
/// `data.shrinkage_recorded`.
pub const SHRINKAGE_RECORDED: Spec = Spec {
    action: "data.shrinkage_recorded", kind: "data", severity: "info",
    keys: &["sku", "quantity", "warehouse", "shrinkage_reason"],
};
/// `data.transfer_created`.
pub const TRANSFER_CREATED: Spec = Spec {
    action: "data.transfer_created", kind: "data", severity: "info",
    keys: &["sku_count", "units", "from_warehouse", "to_warehouse"],
};

/// The stored context.
pub fn context(spec: &Spec, details: &Map<String, Value>, reason: Option<&str>) -> Value {
    let mut ctx = Map::new();
    for k in spec.keys {
        if let Some(v) = details.get(*k) {
            if !v.is_null() {
                ctx.insert((*k).to_string(), v.clone());
            }
        }
    }
    ctx.insert("severity".into(), Value::String(spec.severity.into()));
    ctx.insert("kind".into(), Value::String(spec.kind.into()));
    if let Some(r) = reason.filter(|r| !r.is_empty()) {
        ctx.insert("reason".into(), Value::String(r.into()));
    }
    Value::Object(ctx)
}

/// `record_event`: never fails the caller, a lost row is logged at ERROR.
pub async fn record(
    pool: &PgPool,
    tenant_id: &str,
    user_id: &str,
    spec: &Spec,
    resource: Option<&str>,
    details: Map<String, Value>,
    reason: Option<&str>,
) {
    debug_assert!(spec.severity == "info" || reason.is_some(), "{} needs a reason", spec.action);
    let ctx = context(spec, &details, reason);
    if let Err(e) = log_action(pool, tenant_id, user_id, spec.action, resource, &ctx, "success").await {
        tracing::error!(error = %e, action = spec.action, tenant = tenant_id, "record_event: could not record");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn contexts_match_python() {
        let d = json!({"warehouse": "Norte", "lines": 3, "units": 12.5, "extra": 1});
        let ctx = context(&STOCK_COUNT_APPLIED, d.as_object().unwrap(), None);
        assert_eq!(
            serde_json::to_string(&ctx).unwrap(),
            r#"{"warehouse":"Norte","lines":3,"units":12.5,"severity":"info","kind":"data"}"#
        );
        let d = json!({"reference": "OC-000001", "sku_count": 2, "units": 5.0, "warehouse": null});
        let ctx = context(&RECEPTION_UNDONE, d.as_object().unwrap(), Some("reversed_by_user"));
        assert_eq!(
            serde_json::to_string(&ctx).unwrap(),
            r#"{"reference":"OC-000001","sku_count":2,"units":5.0,"severity":"warning","kind":"purchase","reason":"reversed_by_user"}"#
        );
    }
}
