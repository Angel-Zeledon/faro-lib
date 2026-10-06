//! Per-tenant session and password policy: the admin routes.
//!
//! New in Rust, no Python twin (and so no Python failover for these paths):
//!
//! * `GET    /session-policy`                    the tenant's policy, its bounds
//!                                               and who is locked out right now
//! * `PUT    /session-policy`                    replace the policy
//! * `DELETE /session-policy`                    back to "no policy"
//! * `POST   /session-policy/unlock/{user_id}`   lift one person's lockout
//!
//! Python owns the schema (`tenant_session_policies`, the `users` columns) and
//! ENFORCES the policy at login, refresh, password entry and in its JWT guard
//! (`backend/auth/session_policy.py`); this service enforces the token half in
//! its own guard (`auth/session_policy.rs`) and stores what the admin decides.
//!
//! Admin only, and no API key may call them (a key must not loosen the rules
//! that protect the people). `PUT` is a REPLACEMENT: a field that is absent or
//! null is "not set", so what the screen shows is exactly what is enforced.
//! Unknown fields are refused: a mistyped `max_session_hour` that silently
//! stored nothing would leave an admin believing sessions are capped.
//!
//! Every change that moves a setting records `account.session_policy_changed`
//! (names of what moved, never values), every unlock `account.user_unlocked`.
//! Both are `warning` events carrying the `changed_by_an_account_admin` reason,
//! and both reach the audit trail through the LEGACY catalogue.

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::activity::{record_event_with_reason, Event};
use crate::auth::session_policy::{BOUNDS, DEFAULT_LOCKOUT_MINUTES};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object};

/// `INTERNAL_TAGS` reason, in `exposure()`'s words.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'session-policy': it sets the sign-in rules of every person of the tenant; a key must never loosen them",
    ),
    is_mcp: false,
};

const REASON: &str = "changed_by_an_account_admin";

/// The integer settings, in the order the screen and the event list them.
const INT_FIELDS: [&str; 7] = [
    "max_session_hours",
    "idle_timeout_minutes",
    "min_password_length",
    "password_max_age_days",
    "max_concurrent_sessions",
    "lockout_threshold",
    "lockout_minutes",
];
const BOOL_FIELDS: [&str; 2] = ["require_mixed_case", "require_symbol"];

/// The policy as the API speaks it. `None` / `false` = not set.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Policy {
    pub max_session_hours: Option<i32>,
    pub idle_timeout_minutes: Option<i32>,
    pub min_password_length: Option<i32>,
    pub require_mixed_case: bool,
    pub require_symbol: bool,
    pub password_max_age_days: Option<i32>,
    pub max_concurrent_sessions: Option<i32>,
    pub lockout_threshold: Option<i32>,
    pub lockout_minutes: Option<i32>,
}

impl Policy {
    fn int(&self, field: &str) -> Option<i32> {
        match field {
            "max_session_hours" => self.max_session_hours,
            "idle_timeout_minutes" => self.idle_timeout_minutes,
            "min_password_length" => self.min_password_length,
            "password_max_age_days" => self.password_max_age_days,
            "max_concurrent_sessions" => self.max_concurrent_sessions,
            "lockout_threshold" => self.lockout_threshold,
            "lockout_minutes" => self.lockout_minutes,
            _ => None,
        }
    }

    fn set_int(&mut self, field: &str, v: Option<i32>) {
        match field {
            "max_session_hours" => self.max_session_hours = v,
            "idle_timeout_minutes" => self.idle_timeout_minutes = v,
            "min_password_length" => self.min_password_length = v,
            "password_max_age_days" => self.password_max_age_days = v,
            "max_concurrent_sessions" => self.max_concurrent_sessions = v,
            "lockout_threshold" => self.lockout_threshold = v,
            "lockout_minutes" => self.lockout_minutes = v,
            _ => {}
        }
    }

    fn flag(&self, field: &str) -> bool {
        match field {
            "require_mixed_case" => self.require_mixed_case,
            "require_symbol" => self.require_symbol,
            _ => false,
        }
    }

    /// Whether anything is set (a row of nothing is the same as no row).
    pub fn restricts(&self) -> bool {
        INT_FIELDS.iter().any(|f| *f != "lockout_minutes" && self.int(f).is_some())
            || BOOL_FIELDS.iter().any(|f| self.flag(f))
    }

    /// The names of the settings whose value differs between two policies, in
    /// declaration order. What the event records: never a value.
    pub fn changed_fields(&self, other: &Policy) -> Vec<&'static str> {
        let mut out = Vec::new();
        for f in INT_FIELDS {
            if self.int(f) != other.int(f) {
                out.push(f);
            }
        }
        for f in BOOL_FIELDS {
            if self.flag(f) != other.flag(f) {
                out.push(f);
            }
        }
        out
    }

    fn to_json(&self) -> Value {
        let mut m = Map::new();
        for f in INT_FIELDS {
            m.insert(f.into(), self.int(f).map(Value::from).unwrap_or(Value::Null));
        }
        for f in BOOL_FIELDS {
            m.insert(f.into(), Value::Bool(self.flag(f)));
        }
        Value::Object(m)
    }
}

// ── Validation (pure) ────────────────────────────────────────────────────────

fn bounds_of(field: &str) -> (i64, i64) {
    BOUNDS.iter().find(|b| b.0 == field).map(|b| (b.1, b.2)).unwrap_or((i64::MIN, i64::MAX))
}

/// A JSON integer (not a float, not a bool, not a string) or null.
fn read_int(field: &str, raw: &Value) -> Result<Option<i32>, ApiError> {
    match raw {
        Value::Null => Ok(None),
        Value::Number(n) if n.is_i64() || n.is_u64() => {
            let (min, max) = bounds_of(field);
            let v = n.as_i64().unwrap_or(i64::MAX);
            if v < min || v > max {
                return Err(ApiError::app(
                    "session_policy_out_of_range",
                    format!("{field} must be between {min} and {max}"),
                    422,
                    json!({"field": field, "min": min, "max": max, "value": raw}),
                ));
            }
            Ok(Some(v as i32))
        }
        other => Err(ApiError::app(
            "session_policy_not_an_integer",
            format!("{field} must be a whole number or null"),
            422,
            json!({"field": field, "value": other}),
        )),
    }
}

fn read_flag(field: &str, raw: &Value) -> Result<bool, ApiError> {
    match raw {
        Value::Null => Ok(false),
        Value::Bool(b) => Ok(*b),
        other => Err(ApiError::app(
            "session_policy_not_a_boolean",
            format!("{field} must be true or false"),
            422,
            json!({"field": field, "value": other}),
        )),
    }
}

/// The policy a PUT body describes. Unknown keys are refused, a lockout period
/// without a threshold is refused, and a threshold without a period gets the
/// default one written out (the stored row says what is enforced).
pub fn parse_policy(obj: &Map<String, Value>) -> Result<Policy, ApiError> {
    for key in obj.keys() {
        if !INT_FIELDS.contains(&key.as_str()) && !BOOL_FIELDS.contains(&key.as_str()) {
            return Err(ApiError::app(
                "session_policy_unknown_field",
                format!("Unknown setting '{key}'"),
                422,
                json!({"field": key}),
            ));
        }
    }
    let mut policy = Policy::default();
    for f in INT_FIELDS {
        if let Some(raw) = obj.get(f) {
            policy.set_int(f, read_int(f, raw)?);
        }
    }
    for f in BOOL_FIELDS {
        if let Some(raw) = obj.get(f) {
            let v = read_flag(f, raw)?;
            match f {
                "require_mixed_case" => policy.require_mixed_case = v,
                _ => policy.require_symbol = v,
            }
        }
    }
    match (policy.lockout_threshold, policy.lockout_minutes) {
        (None, Some(_)) => {
            return Err(ApiError::app(
                "session_policy_lockout_needs_threshold",
                "lockout_minutes needs lockout_threshold",
                422,
                json!({"field": "lockout_minutes"}),
            ));
        }
        (Some(_), None) => policy.lockout_minutes = Some(DEFAULT_LOCKOUT_MINUTES),
        _ => {}
    }
    Ok(policy)
}

// ── Storage ──────────────────────────────────────────────────────────────────

type Row = (
    Option<i32>, Option<i32>, Option<i32>, bool, bool, Option<i32>,
    Option<i32>, Option<i32>, Option<i32>, DateTime<Utc>, Option<String>,
);

const COLUMNS: &str = "max_session_hours, idle_timeout_minutes, min_password_length,
    require_mixed_case, require_symbol, password_max_age_days, max_concurrent_sessions,
    lockout_threshold, lockout_minutes, updated_at, updated_by";

fn from_row(r: &Row) -> Policy {
    Policy {
        max_session_hours: r.0,
        idle_timeout_minutes: r.1,
        min_password_length: r.2,
        require_mixed_case: r.3,
        require_symbol: r.4,
        password_max_age_days: r.5,
        max_concurrent_sessions: r.6,
        lockout_threshold: r.7,
        lockout_minutes: r.8,
    }
}

async fn load(pool: &PgPool, tenant_id: &str) -> Result<Option<(Policy, DateTime<Utc>, Option<String>)>, ApiError> {
    let row: Option<Row> = sqlx::query_as(&format!(
        "SELECT {COLUMNS} FROM tenant_session_policies WHERE tenant_id = $1"
    ))
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|r| (from_row(&r), r.9, r.10)))
}

async fn locked_users(pool: &PgPool, tenant_id: &str, lockout_on: bool) -> Result<Vec<Value>, ApiError> {
    if !lockout_on {
        return Ok(Vec::new());
    }
    let rows: Vec<(String, String, Option<String>, DateTime<Utc>, i32)> = sqlx::query_as(
        "SELECT id, email, full_name, locked_until, failed_login_count
           FROM users
          WHERE tenant_id = $1 AND locked_until IS NOT NULL AND locked_until > NOW()
          ORDER BY locked_until DESC, id
          LIMIT 200",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(id, email, name, until, count)| {
            json!({
                "user_id": id, "email": email, "full_name": name,
                "locked_until": isoformat_utc(&until), "failed_attempts": count,
            })
        })
        .collect())
}

async fn view(pool: &PgPool, tenant_id: &str) -> Result<Value, ApiError> {
    let stored = load(pool, tenant_id).await?;
    let (policy, updated_at, updated_by) = match &stored {
        Some((p, at, by)) => (p.clone(), Some(isoformat_utc(at)), by.clone()),
        None => (Policy::default(), None, None),
    };
    let mut bounds = Map::new();
    for (field, min, max) in BOUNDS {
        bounds.insert(field.into(), json!({"min": min, "max": max}));
    }
    Ok(json!({
        "policy": policy.to_json(),
        // True when nothing is enforced: sessions and passwords behave as they
        // did before this feature existed.
        "is_default": !policy.restricts(),
        "updated_at": updated_at,
        "updated_by": updated_by,
        "bounds": Value::Object(bounds),
        "defaults": {
            "access_token_minutes": 15,
            "refresh_token_days": 7,
            "max_sessions_per_person": 5,
            "min_password_length": 8,
            "lockout_minutes": DEFAULT_LOCKOUT_MINUTES,
        },
        "locked_users": locked_users(pool, tenant_id, policy.lockout_threshold.is_some()).await?,
    }))
}

/// Replace the policy. Returns what it was before (for the event).
async fn store(pool: &PgPool, tenant_id: &str, user_id: &str, p: &Policy) -> Result<(), ApiError> {
    let mut tx = pool.begin().await?;
    // `password_max_age_since` restarts whenever the age limit changes (and is
    // empty when there is none): passwords that are already old get the whole
    // limit from the day it is set, instead of an instant lockout.
    sqlx::query(
        "INSERT INTO tenant_session_policies
             (tenant_id, max_session_hours, idle_timeout_minutes, min_password_length,
              require_mixed_case, require_symbol, password_max_age_days, password_max_age_since,
              max_concurrent_sessions, lockout_threshold, lockout_minutes, updated_at, updated_by)
         VALUES ($1, $2, $3, $4, $5, $6, $7, CASE WHEN $7::int IS NULL THEN NULL ELSE NOW() END,
                 $8, $9, $10, NOW(), $11)
         ON CONFLICT (tenant_id) DO UPDATE SET
             max_session_hours = EXCLUDED.max_session_hours,
             idle_timeout_minutes = EXCLUDED.idle_timeout_minutes,
             min_password_length = EXCLUDED.min_password_length,
             require_mixed_case = EXCLUDED.require_mixed_case,
             require_symbol = EXCLUDED.require_symbol,
             password_max_age_days = EXCLUDED.password_max_age_days,
             password_max_age_since = CASE
                 WHEN EXCLUDED.password_max_age_days IS NULL THEN NULL
                 WHEN tenant_session_policies.password_max_age_days
                      IS NOT DISTINCT FROM EXCLUDED.password_max_age_days
                     THEN tenant_session_policies.password_max_age_since
                 ELSE NOW() END,
             max_concurrent_sessions = EXCLUDED.max_concurrent_sessions,
             lockout_threshold = EXCLUDED.lockout_threshold,
             lockout_minutes = EXCLUDED.lockout_minutes,
             updated_at = NOW(),
             updated_by = EXCLUDED.updated_by",
    )
    .bind(tenant_id)
    .bind(p.max_session_hours)
    .bind(p.idle_timeout_minutes)
    .bind(p.min_password_length)
    .bind(p.require_mixed_case)
    .bind(p.require_symbol)
    .bind(p.password_max_age_days)
    .bind(p.max_concurrent_sessions)
    .bind(p.lockout_threshold)
    .bind(p.lockout_minutes)
    .bind(user_id)
    .execute(&mut *tx)
    .await?;
    if p.lockout_threshold.is_none() {
        clear_all_locks(&mut tx, tenant_id).await?;
    }
    tx.commit().await?;
    Ok(())
}

/// With no lockout setting nothing is enforced, so no old lock may survive to
/// come back to life the day the setting is switched on again.
async fn clear_all_locks(tx: &mut sqlx::Transaction<'_, sqlx::Postgres>, tenant_id: &str) -> Result<(), ApiError> {
    sqlx::query(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL
          WHERE tenant_id = $1 AND (failed_login_count <> 0 OR locked_until IS NOT NULL)",
    )
    .bind(tenant_id)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

fn admin_only(user: &CurrentUser) -> Result<(), ApiError> {
    auth::require_role(user, &["admin"])
}

async fn record_changed(state: &AppState, user: &CurrentUser, changed: &[&str]) {
    if changed.is_empty() {
        return;
    }
    let mut details = Map::new();
    details.insert("settings".into(), json!(changed));
    record_event_with_reason(
        &state.pool, &user.tenant_id, &user.user_id, Event::SessionPolicyChanged,
        Some("session-policy"), details, Some(REASON),
    )
    .await;
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn get_policy(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    admin_only(&user)?;
    Ok(ok(view(&state.pool, &user.tenant_id).await?))
}

pub async fn put_policy(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    admin_only(&user)?;
    auth::require_analyst_or_above(&state, &user).await?;
    let wanted = parse_policy(&body_object(&body)?)?;

    let before = load(&state.pool, &user.tenant_id).await?.map(|s| s.0).unwrap_or_default();
    store(&state.pool, &user.tenant_id, &user.user_id, &wanted).await?;
    // Never report a save that did not land.
    let after = load(&state.pool, &user.tenant_id).await?.map(|s| s.0);
    if after.as_ref() != Some(&wanted) {
        return Err(ApiError::app("session_policy_not_saved", "The policy could not be saved", 500, json!({})));
    }
    record_changed(&state, &user, &before.changed_fields(&wanted)).await;
    Ok(ok(view(&state.pool, &user.tenant_id).await?))
}

pub async fn reset_policy(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    admin_only(&user)?;
    auth::require_analyst_or_above(&state, &user).await?;

    let before = load(&state.pool, &user.tenant_id).await?.map(|s| s.0).unwrap_or_default();
    let mut tx = state.pool.begin().await?;
    sqlx::query("DELETE FROM tenant_session_policies WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .execute(&mut *tx)
        .await?;
    clear_all_locks(&mut tx, &user.tenant_id).await?;
    tx.commit().await?;
    record_changed(&state, &user, &before.changed_fields(&Policy::default())).await;
    Ok(ok(view(&state.pool, &user.tenant_id).await?))
}

pub async fn unlock(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(target_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    admin_only(&user)?;
    auth::require_analyst_or_above(&state, &user).await?;

    // Scoped to the caller's tenant in the statement itself: another tenant's
    // user id is simply not found.
    let found: Option<(String, bool)> = sqlx::query_as(
        "SELECT email, (locked_until IS NOT NULL AND locked_until > NOW()) OR failed_login_count <> 0
           FROM users WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&target_id)
    .bind(&user.tenant_id)
    .fetch_optional(&state.pool)
    .await?;
    let Some((email, was_locked)) = found else {
        return Err(ApiError::app("user_not_found", "User not found", 404, json!({})));
    };
    sqlx::query(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL
          WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&target_id)
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    if was_locked {
        let mut details = Map::new();
        details.insert("email".into(), json!(email));
        record_event_with_reason(
            &state.pool, &user.tenant_id, &user.user_id, Event::UserUnlocked, Some(&target_id),
            details, Some(REASON),
        )
        .await;
    }
    Ok(ok(json!({"user_id": target_id, "unlocked": was_locked})))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(v: Value) -> Result<Policy, ApiError> {
        parse_policy(v.as_object().unwrap())
    }

    #[test]
    fn an_empty_body_is_the_unrestricted_policy() {
        let p = parse(json!({})).unwrap();
        assert_eq!(p, Policy::default());
        assert!(!p.restricts());
    }

    #[test]
    fn nulls_and_absent_are_the_same_as_unset() {
        let p = parse(json!({"max_session_hours": null, "require_symbol": null})).unwrap();
        assert!(!p.restricts());
    }

    #[test]
    fn a_full_policy_round_trips() {
        let p = parse(json!({
            "max_session_hours": 8, "idle_timeout_minutes": 30, "min_password_length": 12,
            "require_mixed_case": true, "require_symbol": true, "password_max_age_days": 90,
            "max_concurrent_sessions": 2, "lockout_threshold": 5, "lockout_minutes": 30,
        }))
        .unwrap();
        assert_eq!(p.max_session_hours, Some(8));
        assert_eq!(p.lockout_minutes, Some(30));
        assert!(p.require_mixed_case && p.require_symbol && p.restricts());
        assert_eq!(p.to_json()["idle_timeout_minutes"], 30);
    }

    #[test]
    fn every_bound_is_inclusive_and_enforced() {
        for (field, min, max) in BOUNDS {
            if field == "lockout_minutes" {
                continue; // needs a threshold; covered below
            }
            let ok_lo = parse(json!({ field: min })).unwrap();
            assert_eq!(ok_lo.int(field), Some(min as i32), "{field} at min");
            let ok_hi = parse(json!({ field: max })).unwrap();
            assert_eq!(ok_hi.int(field), Some(max as i32), "{field} at max");
            for bad in [min - 1, max + 1, 0, -5] {
                if bad >= min && bad <= max {
                    continue;
                }
                let e = parse(json!({ field: bad })).unwrap_err();
                assert_eq!(e.code(), Some("session_policy_out_of_range"), "{field}={bad}");
                assert_eq!(e.status.as_u16(), 422);
                assert_eq!(e.body["error_params"]["field"], field);
                assert_eq!(e.body["error_params"]["min"], min);
                assert_eq!(e.body["error_params"]["max"], max);
            }
        }
    }

    #[test]
    fn huge_and_non_integer_numbers_are_refused_not_wrapped() {
        // 2^32 + 8 would wrap to 8 in an `as i32` cast.
        let e = parse(json!({"max_session_hours": 4294967304u64})).unwrap_err();
        assert_eq!(e.code(), Some("session_policy_out_of_range"));
        for bad in [json!(8.5), json!(8.0), json!("8"), json!(true), json!([8])] {
            let e = parse(json!({"max_session_hours": bad})).unwrap_err();
            assert_eq!(e.code(), Some("session_policy_not_an_integer"));
        }
    }

    #[test]
    fn booleans_must_be_booleans() {
        let e = parse(json!({"require_symbol": "yes"})).unwrap_err();
        assert_eq!(e.code(), Some("session_policy_not_a_boolean"));
        let e = parse(json!({"require_mixed_case": 1})).unwrap_err();
        assert_eq!(e.code(), Some("session_policy_not_a_boolean"));
    }

    #[test]
    fn an_unknown_setting_is_refused_so_a_typo_cannot_silently_store_nothing() {
        let e = parse(json!({"max_session_hour": 8})).unwrap_err();
        assert_eq!(e.code(), Some("session_policy_unknown_field"));
        assert_eq!(e.body["error_params"], json!({"field": "max_session_hour"}));
    }

    #[test]
    fn lockout_period_needs_a_threshold_and_a_threshold_gets_the_default_period() {
        let e = parse(json!({"lockout_minutes": 10})).unwrap_err();
        assert_eq!(e.code(), Some("session_policy_lockout_needs_threshold"));
        let p = parse(json!({"lockout_threshold": 5})).unwrap();
        assert_eq!(p.lockout_minutes, Some(DEFAULT_LOCKOUT_MINUTES));
        // A period alone does not count as a restriction, a threshold does.
        assert!(p.restricts());
    }

    #[test]
    fn changed_fields_names_settings_in_declaration_order_and_never_values() {
        let a = Policy { max_session_hours: Some(8), require_symbol: true, ..Policy::default() };
        let b = Policy {
            max_session_hours: Some(12), idle_timeout_minutes: Some(30), ..Policy::default()
        };
        assert_eq!(a.changed_fields(&b), vec!["max_session_hours", "idle_timeout_minutes", "require_symbol"]);
        assert!(a.changed_fields(&a).is_empty());
        let reset = a.changed_fields(&Policy::default());
        assert_eq!(reset, vec!["max_session_hours", "require_symbol"]);
    }
}
