//! `GET /api/v1/entitlements` (backend/api/v1/entitlements.py): the tenant's
//! tier, ceilings (with `tenants.quota` overrides), the three paid-only
//! features, current usage, the configured contact channels and the trial
//! state. Callable with a read key (`entitlements` is an exposed tag).
//!
//! `POST /entitlements/upgrade-request` is NOT migrated: it sends an email.

use axum::extract::State;
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::Utc;
use serde_json::{json, Map, Value};

use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::entitlements::{is_read_only, tenant_features, tenant_limits, tenant_tier, trial_state, TenantRow};
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::routes::ok;
use crate::service_config;
use crate::state::AppState;

pub const ROUTE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };

async fn count(pool: &sqlx::PgPool, sql: &str, tenant_id: &str) -> Result<i64, sqlx::Error> {
    let (n,): (i64,) = sqlx::query_as(sql).bind(tenant_id).fetch_one(pool).await?;
    Ok(n)
}

/// `daily_cap.count_trainings_today`: training launches (family heads only;
/// no back-tests, no re-forecasts, no job that failed or was cancelled before
/// a worker started it) created during the tenant's current calendar day.
///
/// `day_bounds_utc` builds [local midnight, next local midnight) with
/// zoneinfo; here Postgres does the same arithmetic with its own tz database.
/// Both resolve a midnight inside a DST gap with the offset in effect before
/// the transition (Python's fold=0, Postgres' rule for nonexistent local
/// times), so the bounds agree for every supported zone.
async fn count_trainings_today(pool: &sqlx::PgPool, tenant_id: &str, tz: &str) -> Result<i64, sqlx::Error> {
    let (n,): (i64,) = sqlx::query_as(
        "WITH day AS (SELECT date_trunc('day', NOW() AT TIME ZONE $2) AS d)
         SELECT COUNT(*)
           FROM jobs j
           JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id, day
          WHERE j.tenant_id = $1
            AND j.created_at >= (day.d AT TIME ZONE $2)
            AND j.created_at < ((day.d + INTERVAL '1 day') AT TIME ZONE $2)
            AND NOT s.is_backtest
            AND NOT s.is_reforecast
            AND (s.family_id IS NULL OR s.family_id = s.id)
            AND NOT (j.started_at IS NULL AND j.status IN ('FAILED', 'CANCELLED'))",
    )
    .bind(tenant_id)
    .bind(tz)
    .fetch_one(pool)
    .await?;
    Ok(n)
}

/// `_usage`: same keys as `limits`, so the UI pairs them without a table.
/// `trainings_today` is the readable alias of `max_trainings_per_day`.
async fn usage(pool: &sqlx::PgPool, tenant_id: &str) -> Result<Map<String, Value>, ApiError> {
    let tz = crate::routes::r1::timezone::timezone_of(pool, tenant_id).await?;
    let trainings_today = count_trainings_today(pool, tenant_id, &tz).await?;
    let (skus, users, locations, sessions, keys) = tokio::try_join!(
        count(pool, "SELECT COUNT(*) FROM inventory_stock WHERE tenant_id = $1", tenant_id),
        count(pool, "SELECT COUNT(*) FROM users WHERE tenant_id = $1", tenant_id),
        count(pool, "SELECT COUNT(*) FROM warehouses WHERE tenant_id = $1", tenant_id),
        // count_sessions(): ACTIVE sessions only (archived ones free a slot).
        count(pool, "SELECT COUNT(*) FROM sessions WHERE tenant_id = $1 AND archived_at IS NULL", tenant_id),
        count(pool, "SELECT COUNT(*) FROM api_keys WHERE tenant_id = $1", tenant_id),
    )?;
    let mut m = Map::new();
    m.insert("max_trainings_per_day".into(), json!(trainings_today));
    m.insert("trainings_today".into(), json!(trainings_today));
    m.insert("max_skus".into(), json!(skus));
    m.insert("max_users".into(), json!(users));
    m.insert("max_locations".into(), json!(locations));
    m.insert("max_sessions".into(), json!(sessions));
    m.insert("max_api_keys".into(), json!(keys));
    Ok(m)
}

pub async fn get_entitlements(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let pool = &state.pool;
    // `get_tenant(...) or {"quota": {}}`: a missing row is the free tier.
    let tenant = TenantRow::load(pool, &user.tenant_id).await?.unwrap_or_default();
    let usage = usage(pool, &user.tenant_id).await?;
    let contact = service_config::contact(pool, &state.settings).await;
    let now = Utc::now();
    Ok(ok(json!({
        "tier": tenant_tier(&tenant),
        "limits": tenant_limits(&tenant),
        "features": tenant_features(&tenant),
        "usage": usage,
        "contact": contact,
        "trial": {
            "state": trial_state(&tenant, now),
            "ends_at": tenant.trial_ends_at.as_ref().map(isoformat_utc),
        },
        "read_only": is_read_only(&tenant, now),
    })))
}
