//! Route group R1: the small settings and read routers.
//!
//! | Python router              | Module          | Migrated                                  |
//! |----------------------------|-----------------|-------------------------------------------|
//! | `api/v1/preferences.py`    | `preferences`   | GET, PATCH `/me/preferences`              |
//! | `api/v1/activity.py`       | `me_activity`   | GET `/me/activity`, `/me/activity/action-types` |
//! | `api/v1/models.py`         | `models`        | GET `/models`                             |
//! | `api/v1/alerts.py`         | `alerts`        | GET `/alerts`, `/alerts/activity`, `/alerts/kinds`; POST `/alerts/read` |
//! | `api/v1/currency.py`       | `currency`      | GET, PATCH `/tenant/currency`             |
//! | `api/v1/timezone.py`       | `timezone`      | GET `/tenant/timezone` (PATCH stays Python, see the module) |
//!
//! Kept in one sub-router so the group is ONE line in `routes/mod.rs` and in
//! the proxy (`deploy/rust-api/routes.d/33-settings-reads.caddy.example`).

pub mod alerts;
pub mod currency;
pub mod me_activity;
pub mod models;
pub mod preferences;
pub mod query_params;
pub mod timezone;

use axum::routing::{get, post};
use axum::Router;

use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/me/preferences", get(preferences::get_preferences).patch(preferences::update_preferences))
        .route("/api/v1/me/activity", get(me_activity::get_activity))
        .route("/api/v1/me/activity/action-types", get(me_activity::get_action_types))
        .route("/api/v1/models", get(models::list_models))
        .route("/api/v1/alerts", get(alerts::list_alerts))
        .route("/api/v1/alerts/activity", get(alerts::list_activity))
        .route("/api/v1/alerts/kinds", get(alerts::list_kinds))
        .route("/api/v1/alerts/read", post(alerts::mark_alerts_read))
        .route("/api/v1/tenant/currency", get(currency::get_currency).patch(currency::set_currency))
        .route("/api/v1/tenant/timezone", get(timezone::get_timezone))
}
