//! Outbound webhook management: `backend/api/v1/webhooks.py` (the version with
//! business events, scoped hooks and the delivery log, commit 2592d73).
//!
//! Migrated:
//! * `GET /webhooks/events`              the subscribable catalogue
//! * `GET /webhooks`                     hooks the caller may manage
//! * `DELETE /webhooks/{id}`             with its delivery log
//! * `POST /webhooks/{id}/enable`        re-enable after auto-disable
//! * `POST /webhooks/{id}/rotate-secret` new `token_hex(32)`, returned once
//! * `GET /webhooks/{id}/deliveries`     the delivery log (no payload)
//!
//! NOT migrated, stays Python:
//! * `POST /webhooks`: `validate_target` resolves the host name and refuses
//!   private / loopback addresses (SSRF guard, `backend/net`); that network
//!   check has one implementation and it is Python's.
//! * `POST /webhooks/{id}/test`: enqueues a delivery and wakes the Python
//!   delivery worker in-process (`hook_service.kick`).
//! * Delivery itself (the worker loop, signing, retries).
//!
//! A hook carries the warehouse scope of its creator; a scoped caller sees and
//! manages only hooks confined to their own warehouses, and any other hook is
//! a 404 to them, exactly like one that does not exist.

use axum::extract::{Path, RawQuery, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Value};
use sqlx::postgres::PgRow;
use sqlx::Row;

use crate::audit::{self, Note};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::entitlements;
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::query::{self, PyInt, Query};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::Errors;

/// Writes need an analyst (a write key); reads admit a viewer.
pub const ROUTE_WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };
pub const ROUTE_READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };

pub const MAX_DELIVERIES_PAGE: i64 = 200;

// ── backend/webhooks/catalog.py (the subscribable part) ─────────────────────

pub const API_VERSION: &str = "2026-10-05";

const PO_KEYS: [&str; 7] = ["po_log_id", "po_number", "warehouse", "warehouse_id", "sku_count",
    "total_units", "total_value"];

/// `EVENT_TYPES` in declaration order: (name, data_keys, warehouse_aware,
/// subscribable).
pub fn event_types() -> Vec<(&'static str, Vec<&'static str>, bool, bool)> {
    let po = |extra: &[&'static str]| -> Vec<&'static str> { PO_KEYS.iter().chain(extra).copied().collect() };
    vec![
        ("job.completed", vec!["job_id", "session_id"], false, true),
        ("job.failed", vec!["job_id", "session_id", "error"], false, true),
        ("purchase_order.approved", po(&["approved_amount", "decided_by"]), true, true),
        ("purchase_order.rejected", po(&["decided_by"]), true, true),
        ("purchase_order.sent", po(&["sent_at"]), true, true),
        ("purchase_order.cancelled", po(&["cancelled_at", "cancelled_by"]), true, true),
        ("stockout.imminent", vec!["sku", "warehouse", "warehouse_id", "signal", "current_stock",
            "coverage_days", "reorder_point", "detected_on"], true, true),
        ("commitment.at_risk", vec!["commitment_id", "sku", "warehouse", "warehouse_id", "customer",
            "delivery_date", "quantity", "shortfall", "latest_safe_order_date", "order_date_passed"], true, true),
        ("commitment.fulfilled", vec!["commitment_id", "sku", "warehouse", "warehouse_id", "customer",
            "delivery_date", "quantity", "fulfilled_at"], true, true),
        ("webhook.test", vec!["webhook_id"], false, false),
    ]
}

// ── backend/webhooks/policy.py ───────────────────────────────────────────────

/// `parse_scope`: None = company-wide; unreadable = EMPTY (fails closed).
pub fn parse_scope(raw: Option<&Value>) -> Option<Vec<String>> {
    let raw = match raw {
        None | Some(Value::Null) => return None,
        Some(Value::String(s)) => match serde_json::from_str::<Value>(s) {
            Ok(v) => v,
            Err(_) => return Some(Vec::new()),
        },
        Some(v) => v.clone(),
    };
    match raw {
        Value::Array(items) => Some(items.iter().map(crate::pyjson::str_of).collect()),
        // json.loads of a str giving null is not a list: empty, like Python.
        _ => Some(Vec::new()),
    }
}

/// `manageable_by`.
pub fn manageable_by(user_scope: Option<&[String]>, hook_scope: Option<&[String]>) -> bool {
    match (user_scope, hook_scope) {
        (None, _) => true,
        (Some(_), None) => false,
        (Some(u), Some(h)) => h.iter().all(|w| u.contains(w)),
    }
}

// ── Rows ─────────────────────────────────────────────────────────────────────

const LIST_COLUMNS: &str = "id, url, events, created_at, warehouse_scope, disabled_at,
                   disabled_reason, failure_days, secret_rotated_at";

fn ts(row: &PgRow, col: &str) -> Result<Value, sqlx::Error> {
    let v: Option<DateTime<Utc>> = row.try_get(col)?;
    Ok(v.map(|d| json!(isoformat_utc(&d))).unwrap_or(Value::Null))
}

fn scope_of(row: &PgRow) -> Result<Option<Vec<String>>, sqlx::Error> {
    let raw: Option<Value> = row.try_get("warehouse_scope")?;
    Ok(parse_scope(raw.as_ref()))
}

/// `_present`: what the API shows of a webhook. Never the secret.
fn present(row: &PgRow) -> Result<Value, sqlx::Error> {
    let events: Vec<String> = row.try_get("events")?;
    let failure_days: Option<i32> = row.try_get("failure_days")?;
    let reason: Option<String> = row.try_get("disabled_reason")?;
    Ok(json!({
        "id": row.try_get::<String, _>("id")?,
        "url": row.try_get::<String, _>("url")?,
        "events": events,
        "created_at": ts(row, "created_at")?,
        "warehouse_ids": scope_of(row)?,
        "disabled_at": ts(row, "disabled_at")?,
        "disabled_reason": reason,
        "failure_days": failure_days.unwrap_or(0),
        "secret_rotated_at": ts(row, "secret_rotated_at")?,
    }))
}

fn not_found() -> ApiError {
    ApiError::app("webhook_not_found", "Webhook not found", 404, json!({}))
}

/// `_managed_hook`: the webhook, or 404 also when it is outside the caller's
/// warehouse scope (a scoped user must not learn it is there).
async fn managed_hook(state: &AppState, user: &CurrentUser, webhook_id: &str) -> Result<PgRow, ApiError> {
    let row = sqlx::query(&format!("SELECT {LIST_COLUMNS} FROM webhooks WHERE id = $1 AND tenant_id = $2"))
        .bind(webhook_id)
        .bind(&user.tenant_id)
        .fetch_optional(&state.pool)
        .await?;
    let Some(row) = row else { return Err(not_found()) };
    let mine = wscope::scope_ids(&state.pool, user).await?;
    if !manageable_by(mine.as_deref(), scope_of(&row)?.as_deref()) {
        return Err(not_found());
    }
    Ok(row)
}

/// `secrets.token_hex(32)`.
fn new_secret() -> Result<String, ApiError> {
    let mut buf = [0u8; 32];
    getrandom::getrandom(&mut buf).map_err(|e| {
        tracing::error!(error = %e, "OS randomness unavailable");
        ApiError::internal()
    })?;
    Ok(hex::encode(buf))
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn list_event_types(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let events: Vec<Value> = event_types()
        .into_iter()
        .filter(|(.., subscribable)| *subscribable)
        .map(|(name, keys, aware, _)| json!({"type": name, "data_keys": keys, "warehouse_aware": aware}))
        .collect();
    Ok(ok(json!({"api_version": API_VERSION, "events": events})))
}

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let rows = sqlx::query(&format!(
        "SELECT {LIST_COLUMNS} FROM webhooks WHERE tenant_id = $1 ORDER BY created_at DESC"
    ))
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    let mine = wscope::scope_ids(&state.pool, &user).await?;
    let mut out = Vec::new();
    for r in &rows {
        if manageable_by(mine.as_deref(), scope_of(r)?.as_deref()) {
            out.push(present(r)?);
        }
    }
    Ok(ok(Value::Array(out)))
}

pub async fn delete(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(webhook_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    delete_inner(state, actors, webhook_id, headers).await
}

/// `DELETE /webhooks/events`: Starlette passes `GET /events` (method does not
/// match) and lands on `DELETE /{webhook_id}` with the id "events".
pub async fn delete_literal_events(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    delete_inner(state, actors, "events".into(), headers).await
}

async fn delete_inner(state: AppState, actors: RequestActors, webhook_id: String, headers: HeaderMap)
    -> Result<Json<Value>, ApiError>
{
    let user = auth::current_user(&state, &headers, ROUTE_WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    managed_hook(&state, &user, &webhook_id).await?;
    sqlx::query("DELETE FROM webhook_deliveries WHERE webhook_id = $1 AND tenant_id = $2")
        .bind(&webhook_id)
        .bind(&user.tenant_id)
        .execute(&state.pool)
        .await?;
    sqlx::query("DELETE FROM webhooks WHERE id = $1 AND tenant_id = $2")
        .bind(&webhook_id)
        .bind(&user.tenant_id)
        .execute(&state.pool)
        .await?;
    audit::record(&state, &actors, "DELETE", "/webhooks/{webhook_id}", Some(&webhook_id), Note::default(), 200).await;
    Ok(ok(json!({"deleted": webhook_id})))
}

/// The common head of rotate / enable: role, trial, the paid API feature,
/// then the scoped lookup.
async fn writable_hook(state: &AppState, actors: &RequestActors, headers: &HeaderMap, webhook_id: &str)
    -> Result<CurrentUser, ApiError>
{
    let user = auth::current_user(state, headers, ROUTE_WRITE, actors).await?;
    auth::require_analyst_or_above(state, &user).await?;
    entitlements::ensure_feature(&state.pool, state.settings.testing_mode, &user.tenant_id, "api", "").await?;
    managed_hook(state, &user, webhook_id).await?;
    Ok(user)
}

pub async fn rotate_secret(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(webhook_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = writable_hook(&state, &actors, &headers, &webhook_id).await?;
    let secret = new_secret()?;
    sqlx::query("UPDATE webhooks SET secret = $1, secret_rotated_at = NOW() WHERE id = $2 AND tenant_id = $3")
        .bind(&secret)
        .bind(&webhook_id)
        .bind(&user.tenant_id)
        .execute(&state.pool)
        .await?;
    audit::record(&state, &actors, "POST", "/webhooks/{webhook_id}/rotate-secret", Some(&webhook_id),
        Note::default(), 200).await;
    Ok(ok(json!({"id": webhook_id, "secret": secret})))
}

pub async fn enable(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(webhook_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = writable_hook(&state, &actors, &headers, &webhook_id).await?;
    sqlx::query(
        "UPDATE webhooks SET disabled_at = NULL, disabled_reason = NULL,
                failure_days = 0, last_failure_on = NULL
          WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&webhook_id)
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    audit::record(&state, &actors, "POST", "/webhooks/{webhook_id}/enable", Some(&webhook_id),
        Note::default(), 200).await;
    Ok(ok(json!({"id": webhook_id, "enabled": true})))
}

fn delivery_status(s: &str) -> bool {
    matches!(s, "pending" | "delivered" | "failed" | "abandoned")
}

pub async fn deliveries(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(webhook_id): Path<String>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    // FastAPI: the auth dependency raises first; query errors are then a 422.
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let mut errs = Errors::default();
    let status = query::opt_pattern(&mut errs, &q, "status", "^(pending|delivered|failed|abandoned)$", delivery_status);
    let limit = query::int_param(&mut errs, &q, "limit", 50, Some(1), Some(MAX_DELIVERIES_PAGE));
    errs.into_result()?;
    let Some(PyInt::Small(limit)) = limit else { return Err(ApiError::internal()) };
    managed_hook(&state, &user, &webhook_id).await?;

    let mut sql = String::from(
        "SELECT id, event_id, event_type, is_test, status, attempts, last_status_code,
                last_error, next_attempt_at, created_at, last_attempt_at, delivered_at
           FROM webhook_deliveries WHERE webhook_id = $1 AND tenant_id = $2",
    );
    let status = status.filter(|s| !s.is_empty());
    if status.is_some() {
        sql.push_str(" AND status = $3 ORDER BY created_at DESC LIMIT $4");
    } else {
        sql.push_str(" ORDER BY created_at DESC LIMIT $3");
    }
    let mut qy = sqlx::query(&sql).bind(&webhook_id).bind(&user.tenant_id);
    if let Some(s) = &status {
        qy = qy.bind(s);
    }
    let rows = qy.bind(limit).fetch_all(&state.pool).await?;
    let mut out = Vec::new();
    for r in &rows {
        out.push(json!({
            "id": r.try_get::<String, _>("id")?,
            "event_id": r.try_get::<String, _>("event_id")?,
            "event_type": r.try_get::<String, _>("event_type")?,
            "is_test": r.try_get::<bool, _>("is_test")?,
            "status": r.try_get::<String, _>("status")?,
            "attempts": r.try_get::<i32, _>("attempts")?,
            "last_status_code": r.try_get::<Option<i32>, _>("last_status_code")?,
            "last_error": r.try_get::<Option<String>, _>("last_error")?,
            "next_attempt_at": ts(r, "next_attempt_at")?,
            "created_at": ts(r, "created_at")?,
            "last_attempt_at": ts(r, "last_attempt_at")?,
            "delivered_at": ts(r, "delivered_at")?,
        }));
    }
    Ok(ok(Value::Array(out)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalogue_matches_catalog_py() {
        let ev = event_types();
        let names: Vec<&str> = ev.iter().filter(|e| e.3).map(|e| e.0).collect();
        assert_eq!(names, ["job.completed", "job.failed", "purchase_order.approved", "purchase_order.rejected",
            "purchase_order.sent", "purchase_order.cancelled", "stockout.imminent", "commitment.at_risk",
            "commitment.fulfilled"]);
        let approved = &ev[2].1;
        assert_eq!(approved, &vec!["po_log_id", "po_number", "warehouse", "warehouse_id", "sku_count",
            "total_units", "total_value", "approved_amount", "decided_by"]);
        assert!(!ev.iter().find(|e| e.0 == "webhook.test").unwrap().3);
    }

    #[test]
    fn scope_policy_matches_policy_py() {
        assert_eq!(parse_scope(None), None);
        assert_eq!(parse_scope(Some(&Value::Null)), None);
        assert_eq!(parse_scope(Some(&json!(["a", 1]))), Some(vec!["a".to_string(), "1".to_string()]));
        assert_eq!(parse_scope(Some(&json!("[\"x\"]"))), Some(vec!["x".to_string()]));
        assert_eq!(parse_scope(Some(&json!("junk"))), Some(vec![]));
        assert_eq!(parse_scope(Some(&json!({"a": 1}))), Some(vec![]));
        let a = vec!["a".to_string()];
        let ab = vec!["a".to_string(), "b".to_string()];
        assert!(manageable_by(None, None));
        assert!(manageable_by(None, Some(&ab)));
        assert!(!manageable_by(Some(&a), None));
        assert!(manageable_by(Some(&ab), Some(&a)));
        assert!(!manageable_by(Some(&a), Some(&ab)));
        // An empty (fail-closed) hook scope is confined to nothing: manageable.
        assert!(manageable_by(Some(&a), Some(&[])));
    }
}
