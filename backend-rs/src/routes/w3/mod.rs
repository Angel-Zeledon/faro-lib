//! Wave 3, the inventory hub. One router for every route moved in this wave,
//! so `routes/mod.rs` needs a single `.merge(w3::router())` and a merge with
//! other waves stays a one-line conflict at worst.
//!
//! What moved: stock rows (`stock`), physical counts (`stock_counts`),
//! reception reversals (`reversals`). What did not, and why, is in
//! docs/rust-migration.md section "Wave 3".

pub mod receive;
pub mod reversals;
pub mod shrinkage;
pub mod stock;
pub mod stock_counts;
pub mod transfers;

#[allow(unused_imports)]
use axum::routing::{delete, get, post, put};
use axum::Router;

use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/inventory/stock", get(stock::list))
        .route("/api/v1/inventory/stock/page", get(stock::page))
        .route("/api/v1/inventory/stock/lookup", get(stock::lookup_route))
        .route(
            "/api/v1/inventory/stock/{sku}",
            get(stock::get_one).put(stock::put).patch(stock::patch).delete(stock::delete),
        )
        .route("/api/v1/inventory/stock-counts", post(stock_counts::create).get(stock_counts::list))
        .route("/api/v1/inventory/stock-counts/{count_id}", get(stock_counts::get_one))
        .route("/api/v1/inventory/stock-counts/{count_id}/lines", put(stock_counts::upsert_line))
        .route("/api/v1/inventory/stock-counts/{count_id}/lines/{sku}", delete(stock_counts::delete_line))
        .route("/api/v1/inventory/stock-counts/{count_id}/close", post(stock_counts::close))
        .route("/api/v1/inventory/stock-counts/{count_id}/preview", get(stock_counts::preview))
        .route("/api/v1/inventory/stock-counts/{count_id}/apply", post(stock_counts::apply))
        .route("/api/v1/inventory/stock-counts/{count_id}/cancel", post(stock_counts::cancel))
        .route("/api/v1/inventory/po/{po_log_id}/unreceive", post(reversals::unreceive))
        .route("/api/v1/inventory/po/{po_log_id}/unsend", post(reversals::unsend))
        // Wave 3b (receive, transfers, shrinkage) is written but NOT registered:
        // its contract run did not finish. See docs/rust-migration.md.
}
