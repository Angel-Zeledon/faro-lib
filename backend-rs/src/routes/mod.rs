pub mod api_keys;
pub mod audit;
pub mod commitment_outlook;
pub mod audit_stream;
pub mod committed_demand;
pub mod customer_portal;
pub mod cost_centers;
pub mod contract_renewals;
pub mod consensus;
pub mod entitlements;
pub mod fx_rates;
pub mod health;
pub mod lineage;
pub mod ip_allowlist;
pub mod mfa;
pub mod r1;
pub mod schedule;
pub mod scheduled_reports;
pub mod sessions;
pub mod spike_edits;
pub mod webhooks;
pub mod po_cancellation;
pub mod po_approvals;
pub mod po_delegations;
pub mod po_payments;
pub mod saml;
pub mod recurring_deliveries;
pub mod signal_thresholds;
pub mod w2b;
pub mod w3;
pub mod roles;
pub mod session_policy;
pub mod org;
pub mod org_consolidated;
pub mod stock_allocation;

use axum::routing::{delete, get, patch, post, put};
use axum::{Json, Router};
use serde_json::{json, Value};

use crate::error::ApiError;
use crate::pycompat::now_isoformat;
use crate::state::AppState;

/// `backend/schemas/common.py::ok`: `{success, data, meta: {timestamp}}`.
pub fn ok(data: Value) -> Json<Value> {
    Json(json!({
        "success": true,
        "data": data,
        "meta": {"timestamp": now_isoformat()},
    }))
}

/// Every route this service answers. The SAME list is what the proxy sends
/// here (deploy/rust-api/Caddyfile.rust-api.example); a path in one and not
/// the other is either dead code or a 404 waiting for a user.
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health", get(health::health))
        .route("/api/v1/entitlements", get(entitlements::get_entitlements))
        .route("/api/v1/committed-demand", post(committed_demand::create))
        .route(
            "/api/v1/committed-demand/bulk",
            post(committed_demand::create_bulk).patch(committed_demand::update_bulk_literal),
        )
        .route("/api/v1/committed-demand/{commitment_id}", patch(committed_demand::update))
        .route("/api/v1/committed-demand/{commitment_id}/status", post(committed_demand::set_status))
        // Contract renewals: new routes, Rust only (no Python implementation, no
        // failover). Literal `/renewals` first: Python would read it as a root id.
        .route("/api/v1/supply-contracts/renewals", get(contract_renewals::renewals))
        .route("/api/v1/supply-contracts/{root_id}/comparison", get(contract_renewals::comparison))
        .route("/api/v1/supply-contracts/{root_id}/renew", post(contract_renewals::renew))
        .merge(r1::router())
        .merge(w3::router())
        .merge(commitment_outlook::router())
        // Two-step sign-in management (new routes, no Python twin).
        .merge(mfa::router())
        // Multi-currency (new routes, no Python twin): exchange rates + conversion preview.
        .route("/api/v1/tenant/currency/rates", get(fx_rates::list).post(fx_rates::create))
        .route("/api/v1/tenant/currency/rates/resolve", get(fx_rates::resolve))
        .route("/api/v1/tenant/currency/rates/{rate_id}", patch(fx_rates::update).delete(fx_rates::delete))
        .route("/api/v1/tenant/currency/convert", post(fx_rates::convert))
        // S&OP forecast consensus: new routes, Rust only (no Python twin, no failover).
        .merge(consensus::router())
        .merge(sessions::router()).merge(lineage::router()).merge(reception_reversals::router()).merge(po_approvals::router()).merge(schedule::router()).merge(scheduled_reports::router()).merge(spike_edits::router())
        // R3: API keys, webhook subscriptions, audit trail reads.
        .route("/api/v1/api-keys", post(api_keys::create).get(api_keys::list))
        .route("/api/v1/api-keys/usage", get(api_keys::usage).delete(api_keys::revoke_literal_usage))
        .route("/api/v1/api-keys/{key_id}", delete(api_keys::revoke))
        // POST /webhooks (SSRF host check) and POST /webhooks/{id}/test stay Python.
        .route("/api/v1/webhooks", get(webhooks::list))
        .route("/api/v1/webhooks/events", get(webhooks::list_event_types).delete(webhooks::delete_literal_events))
        .route("/api/v1/webhooks/{webhook_id}", delete(webhooks::delete))
        .route("/api/v1/webhooks/{webhook_id}/rotate-secret", post(webhooks::rotate_secret))
        .route("/api/v1/webhooks/{webhook_id}/enable", post(webhooks::enable))
        .route("/api/v1/webhooks/{webhook_id}/deliveries", get(webhooks::deliveries))
        .route("/api/v1/audit", get(audit::list))
        .route("/api/v1/audit/filters", get(audit::filters_vocabulary))
        .route("/api/v1/audit/export", get(audit::export))
        // Continuous audit export (new routes, no Python twin).
        .route("/api/v1/audit-stream", get(audit_stream::get_config).put(audit_stream::put_config).delete(audit_stream::delete_config))
        .route("/api/v1/audit-stream/enable", post(audit_stream::enable))
        .route("/api/v1/audit-stream/disable", post(audit_stream::disable))
        .route("/api/v1/audit-stream/rotate-secret", post(audit_stream::rotate_secret))
        .route("/api/v1/audit-stream/replay", post(audit_stream::replay))
        .route("/api/v1/audit-stream/test", post(audit_stream::test_delivery))
        .route("/api/v1/audit-stream/deliveries", get(audit_stream::deliveries))
        // R4: purchase-order payments / cancellation, signal thresholds.
        .route("/api/v1/inventory/po/{po_log_id}/mark-paid", post(po_payments::mark_paid))
        .route("/api/v1/inventory/po/{po_log_id}/mark-unpaid", post(po_payments::mark_unpaid))
        .route("/api/v1/inventory/po/{po_log_id}/cancel", post(po_cancellation::cancel))
        .route("/api/v1/inventory/po/{po_log_id}/uncancel", post(po_cancellation::uncancel))
        .route(
            "/api/v1/inventory/signal-thresholds",
            get(signal_thresholds::get_thresholds)
                .put(signal_thresholds::put_thresholds)
                .delete(signal_thresholds::reset_thresholds),
        )
        // Wave 2b: freshness, tenant data.
        .merge(w2b::router())
        // NEW (Rust only, no Python route): purchase-order approval delegation.
        .route("/api/v1/inventory/po-approval/delegations", get(po_delegations::list).post(po_delegations::create))
        .route("/api/v1/inventory/po-approval/delegations/{delegation_id}/revoke", post(po_delegations::revoke))
        // Customer portal (new in Rust, no Python twin): tenant link management and the public token routes.
        .merge(customer_portal::router())
        // IP allowlist admin routes: Rust only, no Python twin.
        .route("/api/v1/ip-allowlist", get(ip_allowlist::get))
        .route("/api/v1/ip-allowlist/entries", post(ip_allowlist::add_entry))
        .route("/api/v1/ip-allowlist/entries/{entry_id}", delete(ip_allowlist::delete_entry))
        .route("/api/v1/ip-allowlist/policy", put(ip_allowlist::set_policy))
        // Custom roles: Rust-only routes (no Python failover), see roles.rs.
        .merge(roles::router())
        // Session and password policy (Rust-only: no Python route, no failover).
        .route(
            "/api/v1/session-policy",
            get(session_policy::get_policy).put(session_policy::put_policy).delete(session_policy::reset_policy),
        )
        .route("/api/v1/session-policy/unlock/{user_id}", post(session_policy::unlock))
        // SAML 2.0 SSO configuration (NEW in Rust, no Python twin, no failover).
        .route(
            "/api/v1/auth/saml/config",
            get(saml::get_config).put(saml::put_config).delete(saml::delete_config),
        )
        .route("/api/v1/auth/saml/sp-metadata", get(saml::sp_metadata))
        // Organization hierarchy (Rust only, no Python route): links, grants and
        // the consolidated read-only views.
        .route("/api/v1/org/overview", get(org::overview))
        .route("/api/v1/org/links", post(org::create_link).get(org::list_links))
        .route("/api/v1/org/links/accept", post(org::accept))
        .route("/api/v1/org/links/{link_id}", delete(org::revoke))
        .route("/api/v1/org/links/{link_id}/members", get(org::list_members))
        .route(
            "/api/v1/org/links/{link_id}/members/{user_id}",
            axum::routing::put(org::grant).delete(org::ungrant),
        )
        .route("/api/v1/org/consolidated/committed-demand", get(org_consolidated::committed_demand))
        .route("/api/v1/org/consolidated/stock-signals", get(org_consolidated::stock_signals))
        .route("/api/v1/org/consolidated/purchase-orders", get(org_consolidated::purchase_orders))
        .route("/api/v1/org/consolidated/budgets", get(org_consolidated::budgets))
        // NEW (Rust only, no Python route): cost centers, approval chains, attribution.
        .route("/api/v1/cost-centers", get(cost_centers::list_centers).post(cost_centers::create_center))
        .route("/api/v1/cost-centers/spend", get(cost_centers::spend))
        .route("/api/v1/cost-centers/{center_id}", patch(cost_centers::update_center))
        .route("/api/v1/approval-chains", get(cost_centers::list_chains).post(cost_centers::create_chain))
        .route("/api/v1/approval-chains/evaluate", post(cost_centers::evaluate))
        .route("/api/v1/approval-chains/{chain_id}", patch(cost_centers::update_chain))
        .route("/api/v1/inventory/po/{po_log_id}/cost-center", put(cost_centers::set_po_cost_center))
        // Stock allocation among committed customers: new routes, Rust only.
        .route("/api/v1/allocation/priorities",
            get(stock_allocation::get_priorities).put(stock_allocation::put_priorities))
        .route("/api/v1/allocation/preview", post(stock_allocation::preview))
        .route("/api/v1/allocation/apply", post(stock_allocation::apply))
        .route("/api/v1/allocation/release", post(stock_allocation::release))
        .route("/api/v1/allocation/reservations", get(stock_allocation::reservations))
        .route("/api/v1/allocation/overview", get(stock_allocation::overview))
        // Recurring delivery schedules (Rust-only: no Python route, no failover).
        .route("/api/v1/recurring-deliveries",
            get(recurring_deliveries::list).post(recurring_deliveries::create))
        .route("/api/v1/recurring-deliveries/preview", post(recurring_deliveries::preview))
        .route("/api/v1/recurring-deliveries/{schedule_id}",
            get(recurring_deliveries::get).patch(recurring_deliveries::update))
        .route("/api/v1/recurring-deliveries/{schedule_id}/status", post(recurring_deliveries::set_status))
        // After every route above: records (method, matched template) for the
        // custom-role permission check in auth::current_user.
        .route_layer(axum::middleware::from_fn(crate::auth::permissions::record_route))
        .fallback(not_found)
        .method_not_allowed_fallback(method_not_allowed)
}

/// Starlette's 404, through the error-code bridge.
async fn not_found() -> ApiError {
    ApiError::http(404, "Not Found")
}

/// Starlette's 405, through the error-code bridge.
async fn method_not_allowed() -> ApiError {
    ApiError::http(405, "Method Not Allowed")
}
