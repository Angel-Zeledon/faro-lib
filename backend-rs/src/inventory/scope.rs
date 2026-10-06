//! Warehouse-scope helpers the inventory routes need on top of
//! `auth::warehouse_scope` (`require_count_in_scope`, `filter_rows`,
//! `require_in_scope`). Kept here so the shared scope module is not edited.

use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::auth::warehouse_scope::{denied, in_scope, Scope};
use crate::error::ApiError;

/// `require_in_scope(user, warehouse)`.
pub fn require_in_scope(scope: &Scope, warehouse: Option<&str>) -> Result<(), ApiError> {
    if in_scope(scope, warehouse) { Ok(()) } else { Err(denied(warehouse)) }
}

/// `filter_rows(user, rows, key="warehouse")`.
pub fn filter_rows(scope: &Scope, rows: Vec<Map<String, Value>>, key: &str) -> Vec<Map<String, Value>> {
    if scope.is_none() {
        return rows;
    }
    rows.into_iter()
        .filter(|r| in_scope(scope, r.get(key).and_then(Value::as_str)))
        .collect()
}

/// `require_count_in_scope`: 404 `count_not_found` when the count walks a
/// warehouse outside the scope. A count that does not exist is left for the
/// service's own 404.
pub async fn require_count_in_scope(
    pool: &PgPool,
    scope: &Scope,
    tenant_id: &str,
    count_id: &str,
) -> Result<(), ApiError> {
    if scope.is_none() {
        return Ok(());
    }
    let row: Option<(String,)> =
        sqlx::query_as("SELECT warehouse FROM stock_counts WHERE id = $1 AND tenant_id = $2")
            .bind(count_id)
            .bind(tenant_id)
            .fetch_optional(pool)
            .await?;
    if let Some((warehouse,)) = row {
        if !in_scope(scope, Some(&warehouse)) {
            return Err(count_not_found(count_id));
        }
    }
    Ok(())
}

pub fn count_not_found(count_id: &str) -> ApiError {
    ApiError::app("count_not_found", "Stock count not found", 404, json!({"count_id": count_id}))
}

/// Starlette routes on the DECODED path, so `%2F` inside a `{param}` segment
/// can never match a route: the app answers its 404. axum matches the raw path
/// and decodes afterwards, so every handler with a path parameter calls this
/// first (before auth, like the router would have).
pub fn reject_slash(params: &[&str]) -> Result<(), ApiError> {
    if params.iter().any(|p| p.contains('/')) {
        return Err(ApiError::http(404, "Not Found"));
    }
    Ok(())
}
