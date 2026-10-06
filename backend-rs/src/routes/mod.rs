pub mod committed_demand;
pub mod entitlements;
pub mod health;
pub mod po_cancellation;
pub mod po_payments;
pub mod signal_thresholds;

use axum::routing::{get, patch, post};
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
