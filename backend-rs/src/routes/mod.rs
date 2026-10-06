pub mod api_keys;
pub mod audit;
pub mod committed_demand;
pub mod entitlements;
pub mod health;
pub mod r1;
pub mod schedule;
pub mod sessions;
pub mod spike_edits;
pub mod webhooks;
pub mod po_cancellation;
pub mod po_payments;
pub mod saml;
pub mod signal_thresholds;

use axum::routing::{delete, get, patch, post};
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
        .merge(r1::router())
        .merge(sessions::router()).merge(schedule::router()).merge(spike_edits::router())
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
        // SAML 2.0 SSO configuration (NEW in Rust, no Python twin, no failover).
        .route(
            "/api/v1/auth/saml/config",
            get(saml::get_config).put(saml::put_config).delete(saml::delete_config),
        )
        .route("/api/v1/auth/saml/sp-metadata", get(saml::sp_metadata))
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
