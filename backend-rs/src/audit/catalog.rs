//! `backend/audit/catalog.py`, as DATA: which routes write an `audit.*` row
//! (ROUTES) and which older event rows the trail reads in the same shape
//! (LEGACY). Copied entry for entry; `GET /audit/filters` serves the derived
//! vocabularies, so the contract test compares both lists against Python on
//! every run and a one-sided edit turns it red.

pub const PREFIX: &str = "audit.";

#[derive(Debug, Clone, Copy)]
pub struct AuditRoute {
    pub method: &'static str,
    /// Route template without `/api/v1`, exactly as FastAPI declares it.
    pub template: &'static str,
    pub action: &'static str,
    pub target_type: &'static str,
    pub target_param: Option<&'static str>,
}

const fn r(method: &'static str, template: &'static str, action: &'static str, target_type: &'static str,
           target_param: Option<&'static str>) -> AuditRoute {
    AuditRoute { method, template, action, target_type, target_param }
}

pub const ROUTES: &[AuditRoute] = &[
    r("POST", "/sessions", "session.created", "session", None),
    r("PATCH", "/sessions/{session_id}", "session.updated", "session", Some("session_id")),
    r("POST", "/sessions/{session_id}/train", "session.training_started", "session", Some("session_id")),
    r("GET", "/sessions/{session_id}/export-config", "export.session_config", "session", Some("session_id")),
    r("GET", "/sessions/{session_id}/artifacts/download/{artifact_path:path}", "export.artifact", "session", Some("session_id")),
    r("POST", "/sessions/{session_id}/reports/generate", "export.report", "session", Some("session_id")),
    r("POST", "/sessions/{session_id}/schedule", "schedule.saved", "schedule", Some("session_id")),
    r("DELETE", "/sessions/{session_id}/schedule", "schedule.deleted", "schedule", Some("session_id")),
    r("POST", "/datasets", "dataset.created", "dataset", None),
    r("POST", "/data-sources/file", "dataset.created", "dataset", None),
    r("POST", "/data-sources/sql", "dataset.created", "dataset", None),
    r("POST", "/data-sources/{source_id}/file", "dataset.replaced", "dataset", Some("source_id")),
    r("PATCH", "/data-sources/{source_id}", "dataset.updated", "dataset", Some("source_id")),
    r("PATCH", "/data-sources/{source_id}/query", "dataset.updated", "dataset", Some("source_id")),
    r("PATCH", "/data-sources/{source_id}/sql-config", "dataset.updated", "dataset", Some("source_id")),
    r("POST", "/data-sources/{source_id}/materialize", "dataset.materialized", "dataset", Some("source_id")),
    r("POST", "/data-sources/{source_id}/save-as-new", "dataset.created", "dataset", Some("source_id")),
    r("POST", "/data-sources/{source_id}/export-query", "export.query", "dataset", Some("source_id")),
    r("POST", "/data-sources/{source_id}/execute-query", "dataset.query_run", "dataset", Some("source_id")),
    r("POST", "/data-sources/{source_id}/test-connection", "dataset.connection_tested", "dataset", Some("source_id")),
    r("DELETE", "/data-sources/{source_id}", "dataset.deleted", "dataset", Some("source_id")),
    r("PATCH", "/users/{user_id}/permissions", "user.permissions_changed", "user", Some("user_id")),
    r("POST", "/inventory/warehouses", "warehouse.created", "warehouse", None),
    r("PATCH", "/inventory/warehouses/{name}", "warehouse.updated", "warehouse", Some("name")),
    r("PUT", "/inventory/warehouses/lanes", "warehouse.lanes_changed", "warehouse", None),
    r("DELETE", "/inventory/warehouses/lanes", "warehouse.lanes_changed", "warehouse", None),
    r("PUT", "/planning", "config.changed", "setting", None),
    r("PATCH", "/tenant/timezone", "config.changed", "setting", None),
    r("PATCH", "/tenant/currency", "config.changed", "setting", None),
    r("PUT", "/inventory/signal-thresholds", "config.changed", "setting", None),
    r("POST", "/inventory/po-approval/rules", "config.changed", "setting", None),
    r("PATCH", "/inventory/po-approval/rules/{rule_id}", "config.changed", "setting", Some("rule_id")),
    r("DELETE", "/inventory/po-approval/rules/{rule_id}", "config.changed", "setting", Some("rule_id")),
    r("PUT", "/inventory/po-approval/approvers/{user_id}", "user.permissions_changed", "user", Some("user_id")),
    r("DELETE", "/inventory/signal-thresholds", "config.changed", "setting", None),
    r("POST", "/inventory/service-level-classes/apply", "config.changed", "setting", None),
    r("PUT", "/service-config/tenant/services/{service_key}", "config.changed", "setting", Some("service_key")),
    r("DELETE", "/service-config/tenant/services/{service_key}", "config.changed", "setting", Some("service_key")),
    r("POST", "/inbound/email", "inbound_email.received", "dataset", None),
    r("POST", "/inbound-email/regenerate", "inbound_email.address_regenerated", "inbound_email", None),
    r("PUT", "/inbound-email/senders", "inbound_email.senders_changed", "inbound_email", None),
    r("POST", "/webhooks", "webhook.created", "webhook", None),
    r("DELETE", "/webhooks/{webhook_id}", "webhook.deleted", "webhook", Some("webhook_id")),
    r("POST", "/webhooks/{webhook_id}/rotate-secret", "webhook.secret_rotated", "webhook", Some("webhook_id")),
    r("POST", "/webhooks/{webhook_id}/test", "webhook.tested", "webhook", Some("webhook_id")),
    r("POST", "/webhooks/{webhook_id}/enable", "webhook.enabled", "webhook", Some("webhook_id")),
    r("POST", "/documents", "document.created", "document", None),
    r("DELETE", "/documents/{doc_id}", "document.deleted", "document", Some("doc_id")),
    r("POST", "/feedback", "feedback.sent", "feedback", None),
    r("GET", "/tenant/export", "export.tenant_data", "tenant", None),
    r("GET", "/inventory/status/export-po", "export.purchase_orders", "purchase_order", None),
    r("GET", "/inventory/report/pdf", "export.inventory_pdf", "session", None),
    r("GET", "/sessions/{session_id}/reports/{format}", "export.session_report", "session", Some("session_id")),
    r("GET", "/audit/export", "export.audit_log", "audit_log", None),
    r("POST", "/billing/checkout", "billing.checkout_started", "billing", None),
    r("POST", "/billing/portal", "billing.portal_opened", "billing", None),
];

/// legacy stored action -> (target_type, audit action name).
pub const LEGACY: &[(&str, &str, &str)] = &[
    ("session.delete", "session", "session.deleted"),
    ("session.archive", "session", "session.archived"),
    ("session.restore", "session", "session.restored"),
    ("session.backtest", "session", "session.backtest_started"),
    ("training.completed", "session", "session.training_completed"),
    ("training.failed", "session", "session.training_failed"),
    ("training.blocked", "session", "session.training_blocked"),
    ("account.user_invited", "user", "user.invited"),
    ("account.user_role_changed", "user", "user.role_changed"),
    ("account.user_deactivated", "user", "user.deactivated"),
    ("account.scim_user_created", "user", "user.provisioned"),
    ("account.scim_user_updated", "user", "user.provisioning_updated"),
    ("account.scim_user_deactivated", "user", "user.deprovisioned"),
    ("account.scim_user_reactivated", "user", "user.reprovisioned"),
    ("account.scim_role_changed", "user", "user.role_changed"),
    ("account.scim_request_refused", "user", "user.provisioning_refused"),
    ("account.scim_token_created", "scim_token", "scim_token.created"),
    ("account.scim_token_revoked", "scim_token", "scim_token.revoked"),
    ("account.scim_settings_changed", "scim_token", "scim_token.changed"),
    ("account.session_policy_changed", "session_policy", "session_policy.changed"),
    ("account.user_locked_out", "user", "user.locked_out"),
    ("account.user_unlocked", "user", "user.unlocked"),
    ("account.api_key_created", "api_key", "api_key.created"),
    ("account.api_key_revoked", "api_key", "api_key.revoked"),
    ("purchase.order_generated", "purchase_order", "purchase_order.created"),
    ("purchase.order_sent", "purchase_order", "purchase_order.sent"),
    ("purchase.order_not_sent", "purchase_order", "purchase_order.not_sent"),
    ("purchase.supplier_confirmed", "purchase_order", "purchase_order.supplier_confirmed"),
    ("purchase.supplier_changes_proposed", "purchase_order", "purchase_order.supplier_changes_proposed"),
    ("purchase.supplier_change_accepted", "purchase_order", "purchase_order.supplier_change_accepted"),
    ("purchase.supplier_link_reopened", "purchase_order", "purchase_order.supplier_link_reopened"),
    ("purchase.supplier_link_revoked", "purchase_order", "purchase_order.supplier_link_revoked"),
    ("purchase.reception_recorded", "purchase_order", "purchase_order.received"),
    ("purchase.reception_undone", "purchase_order", "purchase_order.reception_undone"),
    ("purchase.order_unsent", "purchase_order", "purchase_order.unsent"),
    ("purchase.order_paid", "purchase_order", "purchase_order.paid"),
    ("purchase.order_unpaid", "purchase_order", "purchase_order.unpaid"),
    ("purchase.order_cancelled", "purchase_order", "purchase_order.cancelled"),
    ("purchase.order_uncancelled", "purchase_order", "purchase_order.uncancelled"),
    ("purchase.approval_requested", "purchase_order", "purchase_order.approval_requested"),
    ("purchase.approval_approved", "purchase_order", "purchase_order.approval_approved"),
    ("purchase.approval_rejected", "purchase_order", "purchase_order.approval_rejected"),
    ("forecast.adjusted", "forecast_adjustment", "forecast_adjustment.created"),
    ("forecast.spike_excluded", "spike_edit", "spike_edit.created"),
    ("forecast.spike_restored", "spike_edit", "spike_edit.reverted"),
    ("forecast.analogy_defined", "sku_analogy", "sku_analogy.created"),
    ("forecast.analogy_reverted", "sku_analogy", "sku_analogy.reverted"),
    ("committed_demand.created", "committed_demand", "committed_demand.created"),
    ("committed_demand.imported", "committed_demand", "committed_demand.imported"),
    ("committed_demand.changed", "committed_demand", "committed_demand.changed"),
    ("supply_contract.created", "supply_contract", "supply_contract.created"),
    ("supply_contract.revised", "supply_contract", "supply_contract.revised"),
    ("supply_contract.status_changed", "supply_contract", "supply_contract.status_changed"),
    ("purchase_budget.created", "purchase_budget", "purchase_budget.created"),
    ("purchase_budget.revised", "purchase_budget", "purchase_budget.revised"),
    ("purchase_budget.exceeded", "purchase_budget", "purchase_budget.exceeded"),
    ("purchase_budget.override", "purchase_budget", "purchase_budget.override"),
    ("demand_plan.created", "demand_plan", "demand_plan.created"),
    ("demand_plan.submitted", "demand_plan", "demand_plan.submitted"),
    ("demand_plan.approved", "demand_plan", "demand_plan.approved"),
    ("demand_plan.rejected", "demand_plan", "demand_plan.rejected"),
    ("demand_plan.commented", "demand_plan", "demand_plan.commented"),
    ("data.stock_imported", "bulk_import", "bulk_import.stock"),
    ("data.stock_import_partial", "bulk_import", "bulk_import.stock"),
    ("data.suppliers_imported", "bulk_import", "bulk_import.suppliers"),
    ("data.suppliers_import_partial", "bulk_import", "bulk_import.suppliers"),
    ("data.orders_imported", "bulk_import", "bulk_import.purchase_orders"),
    ("data.orders_import_partial", "bulk_import", "bulk_import.purchase_orders"),
    ("data.transfer_created", "transfer", "transfer.created"),
    ("data.shrinkage_recorded", "shrinkage", "shrinkage.recorded"),
    ("data.stock_count_applied", "stock_count", "stock_count.applied"),
    ("api_write", "api_call", "api_call.write"),
    ("billing.plan_activated", "billing", "billing.plan_activated"),
    ("billing.plan_downgraded", "billing", "billing.plan_downgraded"),
    ("billing.payment_failed", "billing", "billing.payment_failed"),
    ("billing.subscription_changed", "billing", "billing.subscription_changed"),
];

fn sorted_unique(mut v: Vec<String>) -> Vec<String> {
    v.sort();
    v.dedup();
    v
}

pub fn route(method: &str, template: &str) -> Option<&'static AuditRoute> {
    ROUTES.iter().find(|r| r.method == method && r.template == template)
}

pub fn legacy(stored: &str) -> Option<(&'static str, &'static str)> {
    LEGACY.iter().find(|(s, ..)| *s == stored).map(|(_, t, a)| (*t, *a))
}

pub fn audit_action(action: &str) -> String {
    format!("{PREFIX}{action}")
}

/// `TARGET_TYPES`.
pub fn target_types() -> Vec<String> {
    sorted_unique(
        ROUTES.iter().map(|r| r.target_type.to_string())
            .chain(LEGACY.iter().map(|(_, t, _)| t.to_string()))
            .collect(),
    )
}

/// `service.audit_actions()`.
pub fn audit_actions() -> Vec<String> {
    sorted_unique(
        ROUTES.iter().map(|r| r.action.to_string())
            .chain(LEGACY.iter().map(|(_, _, a)| a.to_string()))
            .collect(),
    )
}

/// `actions_for_target_type`.
pub fn actions_for_target_type(target_type: &str) -> Vec<String> {
    sorted_unique(
        ROUTES.iter().filter(|r| r.target_type == target_type).map(|r| audit_action(r.action))
            .chain(LEGACY.iter().filter(|(_, t, _)| *t == target_type).map(|(s, ..)| s.to_string()))
            .collect(),
    )
}

/// `all_stored_actions`.
pub fn all_stored_actions() -> Vec<String> {
    sorted_unique(
        ROUTES.iter().map(|r| audit_action(r.action))
            .chain(LEGACY.iter().map(|(s, ..)| s.to_string()))
            .collect(),
    )
}

/// `service._stored_for_action`: the catalogued name first, then the legacy
/// names in LEGACY order.
pub fn stored_for_action(action: &str) -> Vec<String> {
    let mut stored = Vec::new();
    if ROUTES.iter().any(|r| r.action == action) {
        stored.push(audit_action(action));
    }
    stored.extend(LEGACY.iter().filter(|(_, _, a)| *a == action).map(|(s, ..)| s.to_string()));
    stored
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vocabularies_have_the_python_sizes() {
        // python -c "from backend.audit.catalog import *; from backend.audit.service import audit_actions;
        //   print(len(ROUTES), len(LEGACY), len(TARGET_TYPES), len(audit_actions()), len(all_stored_actions()))"
        assert_eq!(ROUTES.len(), 56);
        assert_eq!(LEGACY.len(), 76);
        assert_eq!(target_types().len(), 29);
        assert_eq!(audit_actions().len(), 111);
        assert_eq!(all_stored_actions().len(), 115);
        assert!(target_types().contains(&"audit_log".to_string()));
        assert!(all_stored_actions().contains(&"api_write".to_string()));
        assert_eq!(stored_for_action("bulk_import.stock"),
            vec!["data.stock_imported".to_string(), "data.stock_import_partial".to_string()]);
        assert_eq!(route("DELETE", "/webhooks/{webhook_id}").unwrap().target_param, Some("webhook_id"));
    }
}
