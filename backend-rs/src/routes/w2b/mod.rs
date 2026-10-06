//! Wave 2b: the non-inventory, non-engine routers that need no outbox.
//!
//! | Python router                 | Module        | Migrated                               |
//! |-------------------------------|---------------|----------------------------------------|
//! | `api/v1/freshness.py`         | `freshness`   | GET `/data-freshness`                  |
//! | `api/v1/tenant_data.py`       | `tenant_data` | GET `/tenant/export`, DELETE `/tenant` |
//!
//! One sub-router so the group is ONE line in `routes/mod.rs`.

pub mod billing_access;
pub mod freshness;
pub mod tenant_data;
pub mod tenant_tables;

use axum::routing::{delete, get};
use axum::Router;

use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/data-freshness", get(freshness::get_data_freshness))
        .route("/api/v1/tenant/export", get(tenant_data::export))
        .route("/api/v1/tenant", delete(tenant_data::erase))
}
