//! API key management: `backend/api/v1/api_keys.py`.
//!
//! Migrated: `POST /api-keys` (mint, one-time reveal), `GET /api-keys`,
//! `GET /api-keys/usage`, `DELETE /api-keys/{key_id}`.
//!
//! Security properties kept from Python, each one load-bearing:
//! * the raw key is `sk_live_` + `secrets.token_urlsafe(32)` (43 url-safe
//!   base64 characters of 32 OS-random bytes), returned ONCE and never stored
//!   or logged; only its unsalted SHA-256 hex (`auth::api_key::hash_key`, the
//!   same function the authenticator uses) and its last four characters are;
//! * a key can never manage keys: the whole `api-keys` tag is internal, so a
//!   key presented here is refused before any work (`api_key_route_not_exposed`);
//! * API access is paid-only: `ensure_feature(api)` refuses before anything is
//!   minted or counted (skipped in testing mode, like Python);
//! * the `max_api_keys` ceiling is counted and the row inserted under the
//!   tenant's advisory lock in one transaction (`limits.rs`), so two clicks
//!   cannot both read "0 keys";
//! * a key never reaches past its creator's warehouse scope.

use axum::body::Bytes;
use axum::extract::{Path, RawQuery, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use base64::Engine;
use chrono::{DateTime, Datelike, NaiveDate, Utc};
use regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

use crate::activity::log_action;
use crate::auth::api_key::{hash_key, KEY_PREFIX, RATE_MAX_PER_MINUTE};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::entitlements::{self, tenant_limits, TenantRow};
use crate::error::ApiError;
use crate::pycompat::{isoformat_date, isoformat_utc, py_strip};
use crate::query::Query;
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, Body, Errors};

/// `INTERNAL_TAGS["api-keys"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'api-keys': key management: a key must never mint, list or revoke keys"),
    is_mcp: false,
};

// ── Randomness (Python's `secrets`) ──────────────────────────────────────────

fn os_random<const N: usize>() -> Result<[u8; N], ApiError> {
    let mut buf = [0u8; N];
    getrandom::getrandom(&mut buf).map_err(|e| {
        tracing::error!(error = %e, "OS randomness unavailable");
        ApiError::internal()
    })?;
    Ok(buf)
}

/// `KEY_PREFIX + secrets.token_urlsafe(32)`.
pub fn new_raw_key() -> Result<String, ApiError> {
    let bytes = os_random::<32>()?;
    Ok(format!("{KEY_PREFIX}{}", base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)))
}

/// `raw[-4:]`.
fn last4(raw: &str) -> String {
    let chars: Vec<char> = raw.chars().collect();
    chars[chars.len().saturating_sub(4)..].iter().collect()
}

// ── CreateKeyRequest ─────────────────────────────────────────────────────────

/// `SCOPE_ROLE` / `ROLE_SCOPE`.
fn scope_role(scope: &str) -> &'static str {
    if scope == "write" { "analyst" } else { "viewer" }
}
fn role_scope(role: &str) -> &'static str {
    if role == "analyst" { "write" } else { "read" }
}

/// An `int` as pydantic's lax mode reads it. `Big` is an integer too large
/// for i64, which pydantic accepts and the database then refuses.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Days {
    Small(i64),
    Big(bool),
}

#[derive(Debug)]
pub struct CreateKey {
    pub name: String,
    pub scope: String,
    pub role: String,
    pub expires_in_days: Option<Days>,
    pub warehouse_ids: Option<Vec<String>>,
}

fn value_error(errs: &mut Errors, loc: &[Value], message: &str, input: &Value) {
    errs.push("value_error", loc, format!("Value error, {message}"), input, Some(json!({"error": message})));
}

/// `int | None` (lax) for `expires_in_days`.
fn lax_int(errs: &mut Errors, loc: &[Value], v: &Value) -> Option<Days> {
    match v {
        Value::Bool(b) => Some(Days::Small(i64::from(*b))),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(Days::Small(i))
            } else if n.as_u64().is_some() {
                Some(Days::Big(true))
            } else {
                let f = n.as_f64().unwrap_or(f64::NAN);
                if f.fract() != 0.0 || !f.is_finite() {
                    errs.push("int_from_float", loc,
                        "Input should be a valid integer, got a number with a fractional part".into(), v, None);
                    None
                } else if f.abs() < 9.2e18 {
                    Some(Days::Small(f as i64))
                } else {
                    Some(Days::Big(f > 0.0))
                }
            }
        }
        Value::String(s) => match crate::query::parse_py_int(s) {
            Some(crate::query::PyInt::Small(i)) => Some(Days::Small(i)),
            Some(crate::query::PyInt::Big(pos)) => Some(Days::Big(pos)),
            None => {
                errs.push("int_parsing", loc,
                    "Input should be a valid integer, unable to parse string as an integer".into(), v, None);
                None
            }
        },
        other => {
            errs.push("int_type", loc, "Input should be a valid integer".into(), other, None);
            None
        }
    }
}

/// `CreateKeyRequest`: field validators in field order, then the model
/// validator (only when every field passed).
pub fn validate_create(obj: &Map<String, Value>) -> Result<CreateKey, ApiError> {
    let mut errs = Errors::default();
    let at = |f: &str| vec![json!("body"), json!(f)];
    let mut ok = true;

    let name = match obj.get("name") {
        None => {
            errs.push("missing", &at("name"), "Field required".into(), &Value::Object(obj.clone()), None);
            None
        }
        Some(Value::String(s)) => {
            let stripped = py_strip(s);
            if stripped.is_empty() {
                value_error(&mut errs, &at("name"), "name cannot be empty", &json!(s));
                None
            } else {
                Some(stripped.to_string())
            }
        }
        Some(other) => {
            errs.push("string_type", &at("name"), "Input should be a valid string".into(), other, None);
            None
        }
    };
    if name.is_none() {
        ok = false;
    }

    let mut opt_choice = |field: &str, allowed: [&str; 2], message: &str| -> Option<String> {
        match obj.get(field) {
            None | Some(Value::Null) => None,
            Some(Value::String(s)) => {
                if allowed.contains(&s.as_str()) {
                    Some(s.clone())
                } else {
                    value_error(&mut errs, &at(field), message, &json!(s));
                    ok = false;
                    None
                }
            }
            Some(other) => {
                errs.push("string_type", &at(field), "Input should be a valid string".into(), other, None);
                ok = false;
                None
            }
        }
    };
    let scope = opt_choice("scope", ["read", "write"], "scope must be 'read' or 'write'");
    let role = opt_choice("role", ["viewer", "analyst"], "role must be 'viewer' or 'analyst'");

    let expires_in_days = match obj.get("expires_in_days") {
        None | Some(Value::Null) => None,
        Some(v) => match lax_int(&mut errs, &at("expires_in_days"), v) {
            Some(d) => {
                let below_one = match d {
                    Days::Small(i) => i < 1,
                    Days::Big(pos) => !pos,
                };
                if below_one {
                    value_error(&mut errs, &at("expires_in_days"), "expires_in_days must be at least 1", v);
                    ok = false;
                    None
                } else {
                    Some(d)
                }
            }
            None => {
                ok = false;
                None
            }
        },
    };

    let warehouse_ids = match obj.get("warehouse_ids") {
        None | Some(Value::Null) => None,
        Some(Value::Array(items)) => {
            let mut out = Vec::new();
            for (i, item) in items.iter().enumerate() {
                match item {
                    Value::String(s) => out.push(s.clone()),
                    other => {
                        errs.push("string_type", &[json!("body"), json!("warehouse_ids"), json!(i)],
                            "Input should be a valid string".into(), other, None);
                        ok = false;
                    }
                }
            }
            Some(out)
        }
        Some(other) => {
            errs.push("list_type", &at("warehouse_ids"), "Input should be a valid list".into(), other, None);
            ok = false;
            None
        }
    };
    errs.into_result()?;
    let name = name.filter(|_| ok).ok_or_else(ApiError::internal)?;

    // _scope_and_role_agree
    if let (Some(s), Some(r)) = (&scope, &role) {
        if scope_role(s) != r {
            let mut e = Errors::default();
            value_error(&mut e, &[json!("body")], "scope and role disagree; send only scope", &Value::Object(obj.clone()));
            e.into_result()?;
        }
    }
    let role = role.unwrap_or_else(|| scope_role(scope.as_deref().unwrap_or("read")).to_string());
    let scope = scope.unwrap_or_else(|| role_scope(&role).to_string());
    Ok(CreateKey { name, scope, role, expires_in_days, warehouse_ids })
}

// ── Events (backend/activity/events.py) ──────────────────────────────────────

/// `record_event(..., "account.api_key_*", reason="changed_by_an_account_admin")`:
/// WARNING, kind `account`, whitelisted detail keys in spec order.
async fn record_account_event(
    state: &AppState,
    user: &CurrentUser,
    action: &str,
    resource: &str,
    details: &[(&str, Option<String>)],
) {
    let mut ctx = Map::new();
    for (k, v) in details {
        if let Some(v) = v {
            ctx.insert((*k).to_string(), json!(v));
        }
    }
    ctx.insert("severity".into(), json!("warning"));
    ctx.insert("kind".into(), json!("account"));
    ctx.insert("reason".into(), json!("changed_by_an_account_admin"));
    if let Err(e) = log_action(&state.pool, &user.tenant_id, &user.user_id, action, Some(resource),
        &Value::Object(ctx), "success").await
    {
        tracing::error!(error = %e, action, tenant = %user.tenant_id, "record_event: could not record");
    }
}

// ── Handlers ─────────────────────────────────────────────────────────────────

/// `_key_warehouse_scope`.
async fn key_warehouse_scope(state: &AppState, user: &CurrentUser, requested: Option<&[String]>)
    -> Result<Option<Vec<String>>, ApiError>
{
    let ids = wscope::validate_scope_ids(&state.pool, &user.tenant_id, requested).await?;
    let Some(creator) = wscope::scope_ids(&state.pool, user).await? else { return Ok(ids) };
    let Some(ids) = ids else { return Ok(Some(creator)) };
    if let Some(outside) = ids.iter().find(|i| !creator.contains(i)) {
        let row: Option<(String,)> = sqlx::query_as("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2")
            .bind(outside)
            .bind(&user.tenant_id)
            .fetch_optional(&state.pool)
            .await?;
        return Err(wscope::denied(row.map(|r| r.0).as_deref()));
    }
    Ok(Some(ids))
}

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: Body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let req = validate_create(&body_object(&body)?)?;

    entitlements::ensure_feature(&state.pool, state.settings.testing_mode, &user.tenant_id, "api", "").await?;

    let raw = new_raw_key()?;
    let scope_ids = key_warehouse_scope(&state, &user, req.warehouse_ids.as_deref()).await?;

    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let (existing,): (i64,) = sqlx::query_as("SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_one(&mut *tx)
        .await?;
    crate::limits::enforce_limit(&state.pool, &mut tx, state.settings.testing_mode, &user.tenant_id,
        "max_api_keys", existing, 1).await?;
    let days: Option<i64> = match req.expires_in_days {
        None => None,
        Some(Days::Small(d)) => Some(d),
        // Python hands the huge int to Postgres, which refuses the interval.
        Some(Days::Big(_)) => return Err(ApiError::internal()),
    };
    sqlx::query(
        "INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4, expires_at,
                               warehouse_scope)
         VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6,
                 CASE WHEN $7::bigint IS NULL THEN NULL
                      ELSE NOW() + (($7::bigint)::text || ' days')::INTERVAL END, $8::jsonb)",
    )
    .bind(&user.tenant_id)
    .bind(&req.name)
    .bind(hash_key(&raw))
    .bind(&req.role)
    .bind(&user.user_id)
    .bind(last4(&raw))
    .bind(days)
    .bind(scope_ids.as_ref().map(|ids| json!(ids)))
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;

    // The name and scope are safe to log; the key never is.
    tracing::info!("[api-keys] created name={} scope={} tenant={}", req.name, req.scope, user.tenant_id);
    record_account_event(&state, &user, "account.api_key_created", &req.name,
        &[("key_name", Some(req.name.clone())), ("role", Some(req.role.clone()))]).await;
    Ok(ok(json!({
        "key": raw,
        "name": req.name,
        "role": req.role,
        "scope": req.scope,
        "warehouse_ids": scope_ids,
    })))
}

fn ts(v: Option<DateTime<Utc>>) -> Value {
    v.map(|d| json!(isoformat_utc(&d))).unwrap_or(Value::Null)
}

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    #[allow(clippy::type_complexity)]
    let rows: Vec<(String, String, String, Option<String>, Option<String>, Option<DateTime<Utc>>,
                   Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<Value>)> = sqlx::query_as(
        "SELECT id, name, role, scope, last4, last_used, expires_at, created_at, warehouse_scope
           FROM api_keys WHERE tenant_id = $1 ORDER BY created_at DESC",
    )
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    let out: Vec<Value> = rows
        .into_iter()
        .map(|(id, name, role, scope, last4, last_used, expires_at, created_at, wh)| {
            json!({
                "id": id, "name": name, "role": role, "scope": scope, "last4": last4,
                "last_used": ts(last_used), "expires_at": ts(expires_at), "created_at": ts(created_at),
                "warehouse_scope": wh.unwrap_or(Value::Null),
            })
        })
        .collect();
    Ok(ok(Value::Array(out)))
}

// ── Usage ────────────────────────────────────────────────────────────────────

/// `_MONTH_RE.match(month)`: `\d` is any Unicode decimal digit and `$` also
/// matches before one trailing newline, as in Python's `re`.
fn month_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d{4}-(0[1-9]|1[0-2])\n?\z").expect("valid"))
}

fn is_digit(c: char) -> bool {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d$").expect("valid")).is_match(c.encode_utf8(&mut [0u8; 4]))
}

/// `int(c)` for one Unicode decimal digit: its offset in its run of digits.
fn digit_value(c: char) -> u32 {
    if let Some(d) = c.to_digit(10) {
        return d;
    }
    let mut start = c as u32;
    while let Some(prev) = char::from_u32(start.wrapping_sub(1)) {
        if is_digit(prev) { start -= 1 } else { break }
    }
    (c as u32 - start) % 10
}

/// The (year, month) a valid `month` names.
pub fn parse_month(month: &str) -> Option<(i32, u32)> {
    if !month_re().is_match(month) {
        return None;
    }
    let chars: Vec<char> = month.chars().collect();
    let year = chars[..4].iter().fold(0u32, |acc, c| acc * 10 + digit_value(*c));
    let mon = chars[5..7].iter().fold(0u32, |acc, c| acc * 10 + digit_value(*c));
    Some((year as i32, mon))
}

fn last_day(year: i32, month: u32) -> Option<NaiveDate> {
    let (ny, nm) = if month == 12 { (year + 1, 1) } else { (year, month + 1) };
    NaiveDate::from_ymd_opt(ny, nm, 1)?.pred_opt()
}

pub async fn usage(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw_query): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw_query.as_deref());
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_role(&user, &["admin"])?;
    let today = Utc::now().date_naive();
    let (year, mon) = match q.get("month") {
        None => (today.year(), today.month()),
        Some(m) => parse_month(m).ok_or_else(|| {
            ApiError::app("invalid_month", "month must be YYYY-MM", 422, json!({"month": m}))
        })?,
    };
    // `date(year, mon, 1)`: year 0000 is a ValueError in Python, i.e. a 500.
    if year < 1 {
        return Err(ApiError::internal());
    }
    let first = NaiveDate::from_ymd_opt(year, mon, 1).ok_or_else(ApiError::internal)?;
    let last = last_day(year, mon).ok_or_else(ApiError::internal)?;

    let rows: Vec<(NaiveDate, String, String, i64, Option<String>, bool)> = sqlx::query_as(
        "SELECT u.day, u.api_key_id, u.key_name, u.calls::bigint,
                k.scope, (k.id IS NOT NULL) AS active
           FROM api_usage_daily u
           LEFT JOIN api_keys k ON k.id = u.api_key_id AND k.tenant_id = u.tenant_id
          WHERE u.tenant_id = $1 AND u.day BETWEEN $2 AND $3
          ORDER BY u.day",
    )
    .bind(&user.tenant_id)
    .bind(first)
    .bind(last)
    .fetch_all(&state.pool)
    .await?;

    let mut per_day: std::collections::HashMap<NaiveDate, i64> = std::collections::HashMap::new();
    let mut per_key: Vec<(String, Map<String, Value>)> = Vec::new();
    for (day, key_id, key_name, calls, scope, active) in rows {
        *per_day.entry(day).or_insert(0) += calls;
        let idx = match per_key.iter().position(|(k, _)| *k == key_id) {
            Some(i) => i,
            None => {
                let mut e = Map::new();
                e.insert("api_key_id".into(), json!(key_id));
                e.insert("name".into(), json!(key_name));
                e.insert("scope".into(), scope.map(Value::String).unwrap_or(Value::Null));
                e.insert("active".into(), json!(active));
                e.insert("calls".into(), json!(0));
                per_key.push((key_id.clone(), e));
                per_key.len() - 1
            }
        };
        let entry = &mut per_key[idx].1;
        let prev = entry["calls"].as_i64().unwrap_or(0);
        entry.insert("calls".into(), json!(prev + calls));
        entry.insert("name".into(), json!(key_name));
    }

    let mut by_day = Vec::new();
    let end = last.min(today);
    let mut d = first;
    while d <= end {
        by_day.push(json!({"day": isoformat_date(&d), "calls": per_day.get(&d).copied().unwrap_or(0)}));
        d = d.succ_opt().ok_or_else(ApiError::internal)?;
    }
    let mut by_key: Vec<Map<String, Value>> = per_key.into_iter().map(|(_, e)| e).collect();
    // sorted(key=lambda e: (-e["calls"], e["name"])), stable.
    by_key.sort_by(|a, b| {
        let ca = a["calls"].as_i64().unwrap_or(0);
        let cb = b["calls"].as_i64().unwrap_or(0);
        cb.cmp(&ca).then_with(|| a["name"].as_str().unwrap_or("").cmp(b["name"].as_str().unwrap_or("")))
    });

    // `_daily_ceiling`: the raw limit (None without a tenant row).
    let tenant = TenantRow::load(&state.pool, &user.tenant_id).await?;
    let per_day_per_key = match tenant {
        None => Value::Null,
        Some(t) => tenant_limits(&TenantRow { trial_ends_at: None, ..t })
            .get("max_api_calls_per_day")
            .cloned()
            .unwrap_or(Value::Null),
    };
    let current_month = (year, mon) == (today.year(), today.month());
    Ok(ok(json!({
        "month": format!("{year:04}-{mon:02}"),
        "timezone": "UTC",
        "total": per_day.values().sum::<i64>(),
        "today": if current_month { json!(per_day.get(&today).copied().unwrap_or(0)) } else { Value::Null },
        "by_day": by_day,
        "by_key": by_key,
        "limits": {
            "per_minute_per_key": RATE_MAX_PER_MINUTE,
            "per_day_per_key": per_day_per_key,
        },
    })))
}

// ── Revoke ───────────────────────────────────────────────────────────────────

pub async fn revoke(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(key_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    revoke_inner(state, actors, key_id, headers).await
}

/// `DELETE /api-keys/usage`: Starlette tries `GET /usage` (path matches,
/// method does not), then falls through to `DELETE /{key_id}` with the id
/// "usage". The Rust router matches the static segment first, so this keeps
/// Python's 404 instead of a 405.
pub async fn revoke_literal_usage(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    revoke_inner(state, actors, "usage".into(), headers).await
}

async fn revoke_inner(state: AppState, actors: RequestActors, key_id: String, headers: HeaderMap)
    -> Result<Json<Value>, ApiError>
{
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let row: Option<(String, Option<String>)> =
        sqlx::query_as("SELECT id, name FROM api_keys WHERE id = $1 AND tenant_id = $2")
            .bind(&key_id)
            .bind(&user.tenant_id)
            .fetch_optional(&state.pool)
            .await?;
    let Some((_, name)) = row else {
        return Err(ApiError::http(404, "API key not found"));
    };
    // Python deletes by id alone after the tenant-scoped lookup; the tenant is
    // repeated here as defence in depth (same rows: ids are unique).
    sqlx::query("DELETE FROM api_keys WHERE id = $1 AND tenant_id = $2")
        .bind(&key_id)
        .bind(&user.tenant_id)
        .execute(&state.pool)
        .await?;
    record_account_event(&state, &user, "account.api_key_revoked", &key_id, &[("key_name", name)]).await;
    Ok(ok(json!({"revoked": key_id})))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::api_key::looks_like_api_key;

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn minted_keys_look_like_python_ones() {
        let a = new_raw_key().unwrap();
        let b = new_raw_key().unwrap();
        assert!(looks_like_api_key(&a));
        assert_eq!(a.len(), "sk_live_".len() + 43);
        assert!(a["sk_live_".len()..].chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
        assert_ne!(a, b);
        assert_eq!(last4("sk_live_abcdWXYZ"), "WXYZ");
        // The stored hash is exactly what the authenticator looks up.
        assert_eq!(hash_key(&a).len(), 64);
    }

    #[test]
    fn scope_and_role_resolution() {
        let k = validate_create(&obj(json!({"name": "  nightly  "}))).unwrap();
        assert_eq!((k.name.as_str(), k.scope.as_str(), k.role.as_str()), ("nightly", "read", "viewer"));
        let k = validate_create(&obj(json!({"name": "n", "scope": "write"}))).unwrap();
        assert_eq!(k.role, "analyst");
        let k = validate_create(&obj(json!({"name": "n", "role": "analyst"}))).unwrap();
        assert_eq!(k.scope, "write");
        let k = validate_create(&obj(json!({"name": "n", "scope": "write", "role": "analyst"}))).unwrap();
        assert_eq!(k.role, "analyst");
        let e = validate_create(&obj(json!({"name": "n", "scope": "read", "role": "analyst"}))).unwrap_err();
        assert_eq!(e.body["detail"][0]["loc"], json!(["body"]));
        assert_eq!(e.body["detail"][0]["msg"], "Value error, scope and role disagree; send only scope");
    }

    #[test]
    fn field_errors_match_the_live_api() {
        // Observed on the Python API (2026-10-05).
        let e = validate_create(&obj(json!({"name": "  ", "scope": "x", "role": "admin",
            "expires_in_days": 0, "warehouse_ids": "a"}))).unwrap_err();
        let types: Vec<&str> = e.body["detail"].as_array().unwrap().iter()
            .map(|x| x["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["value_error", "value_error", "value_error", "value_error", "list_type"]);
        assert_eq!(e.body["detail"][0]["ctx"], json!({"error": "name cannot be empty"}));
        let e = validate_create(&obj(json!({"name": "x", "expires_in_days": 2.5}))).unwrap_err();
        assert_eq!(e.body["detail"][0]["type"], "int_from_float");
        // Lax ints: True, "5.0" and 5.0 are accepted.
        assert_eq!(validate_create(&obj(json!({"name": "x", "expires_in_days": true}))).unwrap().expires_in_days,
            Some(Days::Small(1)));
        assert_eq!(validate_create(&obj(json!({"name": "x", "expires_in_days": "5.0"}))).unwrap().expires_in_days,
            Some(Days::Small(5)));
        let e = validate_create(&obj(json!({"name": 5, "warehouse_ids": [1]}))).unwrap_err();
        assert_eq!(e.body["detail"][1]["loc"], json!(["body", "warehouse_ids", 0]));
        let e = validate_create(&obj(json!({}))).unwrap_err();
        assert_eq!(e.body["detail"][0]["type"], "missing");
    }

    #[test]
    fn month_parsing_is_pythons() {
        assert_eq!(parse_month("2026-10"), Some((2026, 10)));
        assert_eq!(parse_month("2026-10\n"), Some((2026, 10)));
        assert_eq!(parse_month("２０２６-10"), Some((2026, 10)));
        assert_eq!(parse_month("2026-13"), None);
        assert_eq!(parse_month("2026-1"), None);
        assert_eq!(parse_month(""), None);
        assert_eq!(parse_month("0000-01"), Some((0, 1)));
        assert_eq!(last_day(2024, 2), NaiveDate::from_ymd_opt(2024, 2, 29));
        assert_eq!(last_day(2026, 12), NaiveDate::from_ymd_opt(2026, 12, 31));
    }
}
