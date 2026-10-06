//! Admin routes of the per-tenant IP allowlist. They exist ONLY in Rust (there
//! is no Python twin, so no Python failover): `GET /ip-allowlist`,
//! `POST /ip-allowlist/entries`, `DELETE /ip-allowlist/entries/{id}`,
//! `PUT /ip-allowlist/policy`. Enforcement lives in `crate::ip_allowlist` and
//! runs in both services.
//!
//! Admin only, on every tier, never callable with an API key (an integration
//! must not edit who may reach the account).
//!
//! The lockout guard, in the spirit of SSO "require": a change that would leave
//! the caller's own current address outside an ENABLED allowlist is refused
//! (`ip_allowlist_lockout`), so an admin cannot cut themselves off. It covers
//! enabling, and deleting the entry that covers the caller. A caller whose
//! address cannot be read cannot enable (`ip_allowlist_address_unknown`).

use std::net::IpAddr;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};

use crate::activity::{record_event_with_reason, Event};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::ip_allowlist::{self as ipa, Cidr};
use crate::pycompat::{isoformat_utc, py_strip};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, bool_field, body_object, str_field, Body, Errors, Field, StrRules};

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'ip-allowlist': who may reach the account is an administrator's decision on the screen; a key must never edit it",
    ),
    is_mcp: false,
};

const LABEL_MAX: usize = 100;

async fn admin(state: &AppState, headers: &HeaderMap, actors: &RequestActors) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    Ok(user)
}

struct EntryRow {
    id: String,
    cidr: String,
    label: String,
    created_by: Option<String>,
    created_at: DateTime<Utc>,
}

impl EntryRow {
    fn json(&self) -> Value {
        json!({
            "id": self.id,
            "cidr": self.cidr,
            "label": self.label,
            "created_by": self.created_by,
            "created_at": isoformat_utc(&self.created_at),
        })
    }
}

async fn load_entries(pool: &sqlx::PgPool, tenant_id: &str) -> Result<Vec<EntryRow>, sqlx::Error> {
    let rows: Vec<(String, String, String, Option<String>, DateTime<Utc>)> = sqlx::query_as(
        "SELECT id, cidr, label, created_by, created_at FROM ip_allowlist_entries
         WHERE tenant_id = $1 ORDER BY created_at, id",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(id, cidr, label, created_by, created_at)| EntryRow { id, cidr, label, created_by, created_at })
        .collect())
}

async fn policy_enabled(pool: &sqlx::PgPool, tenant_id: &str) -> Result<bool, sqlx::Error> {
    let row: Option<(bool,)> = sqlx::query_as("SELECT enabled FROM ip_allowlist_policies WHERE tenant_id = $1")
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    Ok(row.is_some_and(|r| r.0))
}

/// The screen's whole state: policy, entries, the caller's address and whether
/// the entries cover it (what the lockout guard will decide).
async fn view(state: &AppState, tenant_id: &str, caller: Option<IpAddr>) -> Result<Value, ApiError> {
    let entries = load_entries(&state.pool, tenant_id).await?;
    let cidrs: Vec<String> = entries.iter().map(|e| e.cidr.clone()).collect();
    Ok(json!({
        "enabled": policy_enabled(&state.pool, tenant_id).await?,
        "entries": entries.iter().map(EntryRow::json).collect::<Vec<_>>(),
        "your_ip": caller.map(|i| i.to_string()),
        "your_ip_covered": caller.is_some_and(|ip| ipa::any_covers(&cidrs, ip)),
        "max_entries": ipa::MAX_ENTRIES,
    }))
}

async fn record_change(state: &AppState, user: &CurrentUser, reason: &str, cidr: Option<&str>, label: Option<&str>) {
    let mut details = Map::new();
    if let Some(c) = cidr {
        details.insert("cidr".into(), json!(c));
    }
    if let Some(l) = label.filter(|l| !l.is_empty()) {
        details.insert("label".into(), json!(l));
    }
    record_event_with_reason(
        &state.pool, &user.tenant_id, &user.user_id, Event::IpAllowlistChanged, None, details, Some(reason),
    )
    .await;
}

fn lockout(ip: IpAddr) -> ApiError {
    ApiError::app(
        "ip_allowlist_lockout",
        "This change would lock you out: your current IP address would not be allowed.",
        409,
        json!({"ip": ip.to_string()}),
    )
}

fn address_unknown() -> ApiError {
    ApiError::app(
        "ip_allowlist_address_unknown",
        "Your IP address could not be determined, so the allowlist cannot be enabled safely.",
        409,
        json!({}),
    )
}

pub async fn get(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &headers, &actors).await?;
    let caller = ipa::request_ip(&state, &actors, &headers);
    Ok(ok(view(&state, &user.tenant_id, caller).await?))
}

struct NewEntry {
    cidr: Cidr,
    label: String,
}

fn validate_entry(obj: &Map<String, Value>) -> Result<NewEntry, ApiError> {
    let mut errs = Errors::default();
    let at = [json!("body")];
    let cidr = str_field(&mut errs, obj, &at, "cidr", true, false, &validation::NO_STR_RULES);
    let label = str_field(&mut errs, obj, &at, "label", false, false,
        &StrRules { min_length: None, max_length: Some(LABEL_MAX), pattern: None });
    errs.into_result()?;
    let Field::Value(cidr_text) = cidr else { return Err(ApiError::internal()) };
    let parsed = Cidr::parse(&cidr_text).ok_or_else(|| {
        ApiError::app(
            "ip_allowlist_invalid_cidr",
            "Enter an IPv4 or IPv6 address, or a range such as 203.0.113.0/24.",
            422,
            json!({}),
        )
    })?;
    let label = match label {
        Field::Value(l) => py_strip(&l).to_string(),
        _ => String::new(),
    };
    Ok(NewEntry { cidr: parsed, label })
}

pub async fn add_entry(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: Body = validation::read_body(content_type, &bytes)?;
    let user = admin(&state, &headers, &actors).await?;
    let entry = validate_entry(&body_object(&body)?)?;
    let cidr = entry.cidr.to_string();

    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let (existing,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM ip_allowlist_entries WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_one(&mut *tx)
        .await?;
    if existing >= ipa::MAX_ENTRIES {
        return Err(ApiError::app(
            "ip_allowlist_limit_reached",
            "The allowlist is full. Remove an entry or use a wider range.",
            409,
            json!({"limit": ipa::MAX_ENTRIES}),
        ));
    }
    let id = crate::activity::generate_id("ipa");
    let inserted = sqlx::query(
        "INSERT INTO ip_allowlist_entries (id, tenant_id, cidr, label, created_by)
         VALUES ($1, $2, $3, $4, $5) ON CONFLICT (tenant_id, cidr) DO NOTHING",
    )
    .bind(&id)
    .bind(&user.tenant_id)
    .bind(&cidr)
    .bind(&entry.label)
    .bind(&user.user_id)
    .execute(&mut *tx)
    .await?;
    if inserted.rows_affected() == 0 {
        return Err(ApiError::app(
            "ip_allowlist_duplicate_entry",
            "That address or range is already in the allowlist.",
            409,
            json!({"cidr": cidr}),
        ));
    }
    tx.commit().await?;
    record_change(&state, &user, "ip_allowlist_entry_added", Some(&cidr), Some(&entry.label)).await;

    let caller = ipa::request_ip(&state, &actors, &headers);
    Ok((StatusCode::CREATED, ok(view(&state, &user.tenant_id, caller).await?)))
}

pub async fn delete_entry(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(entry_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &headers, &actors).await?;
    let caller = ipa::request_ip(&state, &actors, &headers);

    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let found: Option<(String, String)> = sqlx::query_as(
        "SELECT cidr, label FROM ip_allowlist_entries WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&entry_id)
    .bind(&user.tenant_id)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((cidr, label)) = found else {
        return Err(ApiError::app("ip_allowlist_entry_not_found", "Allowlist entry not found.", 404, json!({})));
    };

    let enabled: Option<(bool,)> = sqlx::query_as("SELECT enabled FROM ip_allowlist_policies WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_optional(&mut *tx)
        .await?;
    if enabled.is_some_and(|r| r.0) {
        let remaining: Vec<(String,)> = sqlx::query_as(
            "SELECT cidr FROM ip_allowlist_entries WHERE tenant_id = $1 AND id <> $2",
        )
        .bind(&user.tenant_id)
        .bind(&entry_id)
        .fetch_all(&mut *tx)
        .await?;
        let remaining: Vec<String> = remaining.into_iter().map(|r| r.0).collect();
        match caller {
            Some(ip) if ipa::any_covers(&remaining, ip) => {}
            Some(ip) => return Err(lockout(ip)),
            None => return Err(address_unknown()),
        }
    }
    sqlx::query("DELETE FROM ip_allowlist_entries WHERE id = $1 AND tenant_id = $2")
        .bind(&entry_id)
        .bind(&user.tenant_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    record_change(&state, &user, "ip_allowlist_entry_removed", Some(&cidr), Some(&label)).await;
    Ok(ok(json!({"deleted": entry_id})))
}

pub async fn set_policy(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: Body = validation::read_body(content_type, &bytes)?;
    let user = admin(&state, &headers, &actors).await?;
    let obj = body_object(&body)?;
    let mut errs = Errors::default();
    let flag = bool_field(&mut errs, &obj, &[json!("body")], "enabled", false);
    if matches!(flag, Field::Absent) && !obj.contains_key("enabled") {
        errs.push("missing", &[json!("body"), json!("enabled")], "Field required".into(), &Value::Object(obj.clone()), None);
    }
    errs.into_result()?;
    let Field::Value(enable) = flag else { return Err(ApiError::internal()) };
    let caller = ipa::request_ip(&state, &actors, &headers);

    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let current: Option<(bool,)> = sqlx::query_as("SELECT enabled FROM ip_allowlist_policies WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_optional(&mut *tx)
        .await?;
    let was_enabled = current.is_some_and(|r| r.0);

    if enable {
        let entries: Vec<(String,)> = sqlx::query_as("SELECT cidr FROM ip_allowlist_entries WHERE tenant_id = $1")
            .bind(&user.tenant_id)
            .fetch_all(&mut *tx)
            .await?;
        let entries: Vec<String> = entries.into_iter().map(|r| r.0).collect();
        match caller {
            Some(ip) if ipa::any_covers(&entries, ip) => {}
            Some(ip) => return Err(lockout(ip)),
            None => return Err(address_unknown()),
        }
    }
    sqlx::query(
        "INSERT INTO ip_allowlist_policies (tenant_id, enabled, updated_at, updated_by)
         VALUES ($1, $2, NOW(), $3)
         ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled,
             updated_at = NOW(), updated_by = EXCLUDED.updated_by",
    )
    .bind(&user.tenant_id)
    .bind(enable)
    .bind(&user.user_id)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;

    if enable != was_enabled {
        let reason = if enable { "ip_allowlist_enabled" } else { "ip_allowlist_disabled" };
        record_change(&state, &user, reason, None, None).await;
    }
    Ok(ok(view(&state, &user.tenant_id, caller).await?))
}
