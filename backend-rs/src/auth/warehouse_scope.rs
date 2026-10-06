//! Warehouse scopes: `backend/auth/warehouse_scope.py`, the slice the
//! migrated routes need (the purchase-order guard).
//!
//! A role is tenant-wide; a scope narrows WHERE it applies. The stored value
//! is `users.warehouse_scope` (or `api_keys.warehouse_scope` for a key):
//! SQL NULL = every warehouse, a JSON array of warehouse ids = only those,
//! `[]` = none. **Fail closed**, exactly like Python: an unreadable value, a
//! missing caller row or an id that is not a warehouse of the caller's tenant
//! all narrow the scope, never widen it.
//!
//! Python resolves the scope once per request and caches it on the
//! `CurrentUser`; here the handler resolves it once into a [`Scope`] and
//! passes that around.

use serde_json::{json, Value};
use sqlx::PgPool;

use crate::auth::CurrentUser;
use crate::error::ApiError;
use crate::pycompat::py_strip;

/// `DEFAULT_WAREHOUSE` (warehouse_scope.py and warehouse_service.py).
pub const DEFAULT_WAREHOUSE: &str = "principal";

/// The caller's resolved scope: `None` = unrestricted, `Some(names)` = only
/// those warehouse NAMES (possibly none).
pub type Scope = Option<Vec<String>>;

/// `str.casefold()` for the comparisons the scope makes. Full case folding
/// differs from lowercasing for a handful of characters; the ones a warehouse
/// name can plausibly carry are mapped explicitly (`ß`, the final sigma and
/// the Latin ligatures), the rest is Unicode lowercase per character (which,
/// unlike `str::to_lowercase`, never produces a context-dependent final sigma).
pub fn py_casefold(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            'ß' | 'ẞ' => out.push_str("ss"),
            'ς' => out.push('σ'),
            'ﬀ' => out.push_str("ff"),
            'ﬁ' => out.push_str("fi"),
            'ﬂ' => out.push_str("fl"),
            'ﬃ' => out.push_str("ffi"),
            'ﬄ' => out.push_str("ffl"),
            'ﬅ' | 'ﬆ' => out.push_str("st"),
            _ => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// `str(i)` for the elements of a stored scope array.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// The stored ids as Python's `scope_names` reads them: `None` when
/// unrestricted, otherwise the list (a string is JSON-decoded once; anything
/// that is not then a list is an empty scope).
fn stored_ids(raw: Option<Value>) -> Option<Vec<String>> {
    let raw = match raw {
        // SQL NULL, and a JSONB `null` that psycopg2 also decodes to None.
        None | Some(Value::Null) => return None,
        Some(v) => v,
    };
    let raw = match raw {
        Value::String(s) => match serde_json::from_str::<Value>(&s) {
            Ok(v) => v,
            Err(_) => return Some(Vec::new()),
        },
        other => other,
    };
    match raw {
        Value::Array(items) => Some(items.iter().map(py_str).collect()),
        _ => Some(Vec::new()),
    }
}

/// `scope_names(user)`.
pub async fn scope_names(pool: &PgPool, user: &CurrentUser) -> Result<Scope, ApiError> {
    let row: Option<(Option<Value>,)> = if let Some(key_id) = user.api_key_id.as_deref() {
        sqlx::query_as("SELECT warehouse_scope FROM api_keys WHERE id = $1 AND tenant_id = $2")
            .bind(key_id)
            .bind(&user.tenant_id)
            .fetch_optional(pool)
            .await?
    } else {
        sqlx::query_as("SELECT warehouse_scope FROM users WHERE id = $1 AND tenant_id = $2")
            .bind(&user.user_id)
            .bind(&user.tenant_id)
            .fetch_optional(pool)
            .await?
    };
    // A caller whose row cannot be read has no business being unrestricted.
    let ids = match row {
        None => Some(Vec::new()),
        Some((raw,)) => stored_ids(raw),
    };
    let Some(ids) = ids else { return Ok(None) };
    if ids.is_empty() {
        return Ok(Some(Vec::new()));
    }
    let names: Vec<(String,)> =
        sqlx::query_as("SELECT name FROM warehouses WHERE tenant_id = $1 AND id = ANY($2)")
            .bind(&user.tenant_id)
            .bind(&ids)
            .fetch_all(pool)
            .await?;
    Ok(Some(names.into_iter().map(|(n,)| n).collect()))
}

fn fold(name: &str) -> String {
    py_casefold(py_strip(name))
}

/// `effective_name`: the warehouse a row with this (possibly missing) name is on.
pub fn effective_name(name: Option<&str>) -> String {
    let s = py_strip(name.unwrap_or(""));
    if s.is_empty() { DEFAULT_WAREHOUSE.to_string() } else { s.to_string() }
}

/// `in_scope`.
pub fn in_scope(scope: &Scope, warehouse: Option<&str>) -> bool {
    let Some(names) = scope else { return true };
    let target = fold(&effective_name(warehouse));
    names.iter().any(|n| fold(n) == target)
}

/// `denied(warehouse)`: 403 `warehouse_out_of_scope`.
pub fn denied(warehouse: Option<&str>) -> ApiError {
    ApiError::app(
        "warehouse_out_of_scope",
        "This user is not allowed to work with that warehouse.",
        403,
        json!({"warehouse": effective_name(warehouse)}),
    )
}

/// `warehouse_service.name_precedence_key`.
fn name_precedence_key(name: &str) -> (bool, String) {
    (name != DEFAULT_WAREHOUSE, py_casefold(name))
}

/// `_tenant_default`: `get_default_warehouse_name(tenant) or "principal"`.
/// The flagged default first; else by name precedence over the rows in the
/// database's `ORDER BY name` order (Python's sort is stable, so ties keep it).
pub async fn tenant_default(pool: &PgPool, tenant_id: &str) -> Result<String, ApiError> {
    let rows: Vec<(String, bool)> =
        sqlx::query_as("SELECT name, is_default FROM warehouses WHERE tenant_id = $1 ORDER BY name")
            .bind(tenant_id)
            .fetch_all(pool)
            .await?;
    Ok(pick_default(&rows).unwrap_or_else(|| DEFAULT_WAREHOUSE.to_string()))
}

fn pick_default(rows: &[(String, bool)]) -> Option<String> {
    if rows.is_empty() {
        return None;
    }
    if let Some((n, _)) = rows.iter().find(|(_, d)| *d) {
        return Some(n.clone());
    }
    let mut sorted: Vec<&(String, bool)> = rows.iter().collect();
    sorted.sort_by_key(|(n, _)| name_precedence_key(n));
    Some(sorted[0].0.clone())
}

/// `require_po_in_scope` (what the `po_guard` dependency runs): 403 when the
/// order's destination warehouse is outside the scope. An order that does not
/// exist in the caller's tenant is left for the endpoint's own 404.
pub async fn require_po_in_scope(pool: &PgPool, scope: &Scope, tenant_id: &str, po_log_id: &str)
    -> Result<(), ApiError>
{
    if scope.is_none() {
        return Ok(());
    }
    let row: Option<(Option<String>,)> =
        sqlx::query_as("SELECT destination_warehouse FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
            .bind(po_log_id)
            .bind(tenant_id)
            .fetch_optional(pool)
            .await?;
    let Some((destination,)) = row else { return Ok(()) };
    // require_destination_in_scope -> _po_target
    let named = py_strip(destination.as_deref().unwrap_or("")).to_string();
    let target = if named.is_empty() { tenant_default(pool, tenant_id).await? } else { named };
    if !in_scope(scope, Some(&target)) {
        return Err(denied(Some(&target)));
    }
    Ok(())
}

/// `po_guard`: resolve the caller's scope and apply the order check.
pub async fn po_guard(pool: &PgPool, user: &CurrentUser, po_log_id: &str) -> Result<(), ApiError> {
    let scope = scope_names(pool, user).await?;
    require_po_in_scope(pool, &scope, &user.tenant_id, po_log_id).await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(names: &[&str]) -> Scope {
        Some(names.iter().map(|n| n.to_string()).collect())
    }

    #[test]
    fn stored_values_fail_closed() {
        assert_eq!(stored_ids(None), None);
        assert_eq!(stored_ids(Some(Value::Null)), None);
        assert_eq!(stored_ids(Some(json!([]))), Some(vec![]));
        assert_eq!(stored_ids(Some(json!(["a", 1]))), Some(vec!["a".into(), "1".into()]));
        assert_eq!(stored_ids(Some(json!("[\"a\"]"))), Some(vec!["a".into()]));
        assert_eq!(stored_ids(Some(json!("not json"))), Some(vec![]));
        assert_eq!(stored_ids(Some(json!({"a": 1}))), Some(vec![]));
    }

    #[test]
    fn in_scope_folds_and_defaults() {
        assert!(in_scope(&None, Some("anything")));
        assert!(!in_scope(&s(&[]), Some("Norte")));
        assert!(in_scope(&s(&["Norte"]), Some("  norte ")));
        assert!(in_scope(&s(&["Straße"]), Some("STRASSE")));
        assert!(in_scope(&s(&["principal"]), None));
        assert!(in_scope(&s(&["Principal"]), Some("   ")));
        assert!(!in_scope(&s(&["Norte"]), None));
    }

    #[test]
    fn denied_names_the_effective_warehouse() {
        let e = denied(Some("  "));
        assert_eq!(e.body["error_params"], json!({"warehouse": "principal"}));
        assert_eq!(e.status.as_u16(), 403);
    }

    #[test]
    fn default_warehouse_precedence() {
        let rows = vec![("Bodega".to_string(), false), ("principal".to_string(), false)];
        assert_eq!(pick_default(&rows).as_deref(), Some("principal"));
        let rows = vec![("Bodega".to_string(), false), ("principal".to_string(), false), ("Zeta".to_string(), true)];
        assert_eq!(pick_default(&rows).as_deref(), Some("Zeta"));
        let rows = vec![("beta".to_string(), false), ("Alfa".to_string(), false)];
        assert_eq!(pick_default(&rows).as_deref(), Some("Alfa"));
        assert_eq!(pick_default(&[]), None);
    }
}
