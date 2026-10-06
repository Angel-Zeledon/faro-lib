//! The slice of `backend/auth/warehouse_scope.py` the R3 routes need: reading
//! a caller's scope (fail closed), the company-wide refusal, the 403 for a
//! warehouse outside the scope, and validating a scope being SET.
//!
//! Same storage: `users.warehouse_scope` / `api_keys.warehouse_scope`, JSONB,
//! SQL NULL (or JSON null) = unrestricted, an array of warehouse ids = only
//! those, anything unreadable = nothing at all.

use serde_json::{json, Value};
use sqlx::PgPool;

use crate::auth::CurrentUser;
use crate::error::ApiError;
use crate::pyjson;
use crate::pycompat::py_strip;

pub const DEFAULT_WAREHOUSE: &str = "principal";

/// `_raw_scope`: the stored value, `None` for unrestricted. A caller whose
/// row cannot be read is `Some([])`, never unrestricted.
async fn raw_scope(pool: &PgPool, user: &CurrentUser) -> Result<Option<Value>, sqlx::Error> {
    let row: Option<(Option<Value>,)> = match &user.api_key_id {
        Some(key_id) => {
            sqlx::query_as("SELECT warehouse_scope FROM api_keys WHERE id = $1 AND tenant_id = $2")
                .bind(key_id)
                .bind(&user.tenant_id)
                .fetch_optional(pool)
                .await?
        }
        None => {
            sqlx::query_as("SELECT warehouse_scope FROM users WHERE id = $1 AND tenant_id = $2")
                .bind(&user.user_id)
                .bind(&user.tenant_id)
                .fetch_optional(pool)
                .await?
        }
    };
    Ok(match row {
        None => Some(json!([])),
        // psycopg2 turns both SQL NULL and a JSON null into None.
        Some((None,)) | Some((Some(Value::Null),)) => None,
        Some((Some(v),)) => Some(v),
    })
}

/// The ids a stored value lists: a JSON string is decoded first, a list is
/// stringified item by item, anything else is the empty scope.
fn ids_of(raw: Value) -> Vec<String> {
    let raw = match raw {
        Value::String(s) => match serde_json::from_str::<Value>(&s) {
            Ok(v) => v,
            Err(_) => return Vec::new(),
        },
        other => other,
    };
    match raw {
        Value::Array(items) => items.iter().map(pyjson::str_of).collect(),
        _ => Vec::new(),
    }
}

/// `scope_ids`: the stored ids (stale ones included), `None` = unrestricted.
pub async fn scope_ids(pool: &PgPool, user: &CurrentUser) -> Result<Option<Vec<String>>, sqlx::Error> {
    Ok(raw_scope(pool, user).await?.map(ids_of))
}

/// `is_scoped`: `scope_names(user) is not None`, which is exactly "the stored
/// value is not None" (an empty or stale scope still counts as scoped).
pub async fn is_scoped(pool: &PgPool, user: &CurrentUser) -> Result<bool, sqlx::Error> {
    Ok(raw_scope(pool, user).await?.is_some())
}

/// `require_company_wide`.
pub async fn require_company_wide(pool: &PgPool, user: &CurrentUser) -> Result<(), ApiError> {
    if is_scoped(pool, user).await? {
        return Err(ApiError::app(
            "warehouse_scope_company_totals",
            "This shows company-wide totals, which are not available to a user limited to some warehouses.",
            403,
            json!({}),
        ));
    }
    Ok(())
}

/// `effective_name`.
pub fn effective_name(name: Option<&str>) -> String {
    let s = py_strip(name.unwrap_or(""));
    if s.is_empty() { DEFAULT_WAREHOUSE.to_string() } else { s.to_string() }
}

/// `denied(warehouse)`.
pub fn denied(warehouse: Option<&str>) -> ApiError {
    ApiError::app(
        "warehouse_out_of_scope",
        "This user is not allowed to work with that warehouse.",
        403,
        json!({"warehouse": effective_name(warehouse)}),
    )
}

/// The first 60 code points (`unknown[0][:60]`).
fn cut60(s: &str) -> String {
    s.chars().take(60).collect()
}

/// `validate_scope_ids`: strip, drop blanks, de-duplicate keeping order, at
/// most 500, and every id a warehouse of THIS tenant (the first unknown one
/// is named).
pub async fn validate_scope_ids(
    pool: &PgPool,
    tenant_id: &str,
    ids: Option<&[String]>,
) -> Result<Option<Vec<String>>, ApiError> {
    let Some(ids) = ids else { return Ok(None) };
    let mut clean: Vec<String> = Vec::new();
    for i in ids {
        let s = py_strip(i).to_string();
        if !s.is_empty() && !clean.contains(&s) {
            clean.push(s);
        }
    }
    if clean.len() > 500 {
        return Err(ApiError::app("warehouse_scope_invalid", "Too many warehouses.", 422, json!({})));
    }
    if !clean.is_empty() {
        let known: Vec<(String,)> =
            sqlx::query_as("SELECT id FROM warehouses WHERE tenant_id = $1 AND id = ANY($2)")
                .bind(tenant_id)
                .bind(&clean)
                .fetch_all(pool)
                .await?;
        let known: Vec<String> = known.into_iter().map(|r| r.0).collect();
        if let Some(unknown) = clean.iter().find(|i| !known.contains(i)) {
            return Err(ApiError::app(
                "warehouse_scope_invalid",
                "One of the warehouses does not exist in this account.",
                422,
                json!({"warehouse_id": cut60(unknown)}),
            ));
        }
    }
    Ok(Some(clean))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stored_values_fail_closed() {
        assert_eq!(ids_of(json!(["a", 5])), vec!["a".to_string(), "5".to_string()]);
        assert_eq!(ids_of(json!("[\"x\"]")), vec!["x".to_string()]);
        assert!(ids_of(json!("not json")).is_empty());
        assert!(ids_of(json!({"a": 1})).is_empty());
    }

    #[test]
    fn denial_names_the_default_warehouse_for_a_blank_name() {
        let e = denied(None);
        assert_eq!(e.body["error_params"], json!({"warehouse": "principal"}));
        let e = denied(Some("  Norte "));
        assert_eq!(e.body["error_params"], json!({"warehouse": "Norte"}));
    }
}
