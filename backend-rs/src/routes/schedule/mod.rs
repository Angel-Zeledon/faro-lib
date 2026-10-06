//! Scheduled retrains: `backend/api/v1/schedule.py` (tag `schedule`, exposed
//! to API keys: GETs need a read key, writes a write key).
//!
//! Migrated, all five routes of the router:
//! * `GET /schedules`                     every schedule of the tenant
//! * `GET /schedules/history`             what the scheduler actually did
//! * `GET /sessions/{id}/schedule`
//! * `POST /sessions/{id}/schedule`       save (create or update), retrain mode
//! * `DELETE /sessions/{id}/schedule`
//!
//! The two writes are catalogued in the audit trail
//! (`backend/audit/catalog.py`: `schedule.saved` / `schedule.deleted`), so
//! they write the same `audit.*` row `AuditMiddleware` writes, with the
//! handler's before/after note.
//!
//! The cron expression is validated and evaluated by a port of `croniter`
//! (`cron.rs`) in the tenant's time zone (`tz.rs`). Nothing here trains:
//! the worker (Python) picks the schedule up from `scheduled_jobs`.

pub mod cron;
pub mod tz;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, Uri};
use axum::{Extension, Json};
use chrono::{DateTime, Duration, Utc};
use serde_json::{json, Map, Value};
use sqlx::Row;

use crate::audit::{self, Note};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::routes::ok;
use crate::routes::sessions::{get_session, int_query, query_map, query_pairs, row_json, session_not_found};
use crate::state::AppState;
use crate::validation::{self, bool_field, str_field, Errors, Field, NO_STR_RULES};

pub const ROUTE_READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
pub const ROUTE_WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

const SCHEDULE_PATH: &str = "/sessions/{session_id}/schedule";

// ── Request model ────────────────────────────────────────────────────────────

struct SaveScheduleRequest {
    cron_expr: String,
    enabled: bool,
    retrain_mode: Option<String>,
}

/// A pydantic `field_validator` failure: `ValueError(msg)`.
fn value_error(errs: &mut Errors, field: &str, msg: &str, input: &Value) {
    errs.push("value_error", &validation::loc(&[json!("body")], field), format!("Value error, {msg}"), input,
        Some(json!({"error": msg})));
}

/// `SaveScheduleRequest._valid_cron`: five fields, then croniter's verdict.
pub fn validate_cron(v: &str) -> Result<String, String> {
    let stripped = cron::py_strip(v);
    if cron::py_split(stripped).len() != 5 {
        return Err("cron_expr must have exactly 5 fields (e.g. '0 6 * * 1')".into());
    }
    cron::expand(stripped).map_err(|e| format!("cron_expr is not a valid cron expression: {}", e.0))?;
    Ok(stripped.to_string())
}

fn validate_body(obj: &Map<String, Value>) -> Result<SaveScheduleRequest, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let cron_expr = match str_field(&mut errs, obj, &p, "cron_expr", true, false, &NO_STR_RULES) {
        Field::Value(v) => match validate_cron(&v) {
            Ok(c) => Some(c),
            Err(msg) => {
                value_error(&mut errs, "cron_expr", &msg, &obj["cron_expr"]);
                None
            }
        },
        _ => None,
    };
    let enabled = bool_field(&mut errs, obj, &p, "enabled", false);
    let retrain_mode = match str_field(&mut errs, obj, &p, "retrain_mode", false, true, &NO_STR_RULES) {
        Field::Value(m) if m != "refit" && m != "reforecast" => {
            value_error(&mut errs, "retrain_mode", "retrain_mode must be 'refit' or 'reforecast'", &obj["retrain_mode"]);
            None
        }
        Field::Value(m) => Some(m),
        _ => None,
    };
    errs.into_result()?;
    Ok(SaveScheduleRequest {
        cron_expr: cron_expr.ok_or_else(ApiError::internal)?,
        enabled: match enabled { Field::Value(b) => b, _ => true },
        retrain_mode,
    })
}

// ── Time zone and next run ───────────────────────────────────────────────────

/// `timezone.timezone_of`: the IANA NAME of the tenant's supported zone
/// (scheduled reports store which zone their next run was computed in).
pub(crate) async fn tenant_zone_name(state: &AppState, tenant_id: &str) -> Result<String, ApiError> {
    let row: Option<(Option<Value>,)> = sqlx::query_as("SELECT settings FROM tenants WHERE id = $1")
        .bind(tenant_id)
        .fetch_optional(&state.pool)
        .await?;
    let name = row
        .and_then(|r| r.0)
        .and_then(|s| s.get("timezone").cloned())
        .filter(crate::pycompat::truthy);
    Ok(match name {
        Some(Value::String(s)) if tz::Zone::from_name(&s).is_some() => s,
        Some(other) => {
            tracing::warn!(tenant = tenant_id, zone = %other, "[timezone] unsupported zone");
            tz::DEFAULT_TZ.to_string()
        }
        None => tz::DEFAULT_TZ.to_string(),
    })
}

/// `timezone.timezone_of` + `zoneinfo_of`: the tenant's supported zone.
pub(crate) async fn tenant_zone(state: &AppState, tenant_id: &str) -> Result<tz::Zone, ApiError> {
    let row: Option<(Option<Value>,)> = sqlx::query_as("SELECT settings FROM tenants WHERE id = $1")
        .bind(tenant_id)
        .fetch_optional(&state.pool)
        .await?;
    let name = row
        .and_then(|r| r.0)
        .and_then(|s| s.get("timezone").cloned())
        .filter(crate::pycompat::truthy);
    let name = match name {
        Some(Value::String(s)) => s,
        Some(other) => {
            tracing::warn!(tenant = tenant_id, zone = %other, "[timezone] unsupported zone");
            tz::DEFAULT_TZ.to_string()
        }
        None => tz::DEFAULT_TZ.to_string(),
    };
    Ok(match tz::Zone::from_name(&name) {
        Some(z) => z,
        None => {
            tracing::warn!(tenant = tenant_id, zone = %name, "[timezone] unsupported zone");
            tz::Zone::from_name(tz::DEFAULT_TZ).expect("default zone is supported")
        }
    })
}

/// `_next_run`: the next firing, read in the tenant's zone, as a UTC instant.
/// The +24h fallback is Python's last resort (no match within 50 years, e.g.
/// February 31st), logged as loudly as Python logs it.
pub(crate) fn next_run(cron_expr: &str, zone: &tz::Zone, now: DateTime<Utc>, tenant_id: &str) -> DateTime<Utc> {
    let computed = cron::expand(cron_expr)
        .map_err(|e| e.0)
        .and_then(|ex| cron::next_after(&ex, now.naive_utc(), zone).map_err(|_| "failed to find next date".into()));
    match computed {
        Ok(naive) => naive.and_utc(),
        Err(exc) => {
            tracing::error!("[schedule] could not read cron {cron_expr:?} for tenant {tenant_id} — falling back to \
                +24h, which is NOT the hour that was chosen: {exc}");
            now + Duration::hours(24)
        }
    }
}

// ── Audit row (AuditMiddleware for the two catalogued routes) ────────────────

/// The `audit.schedule.*` row, through the shared writer (`crate::audit`),
/// with the `audit.note(before=..., after=...)` the Python handlers set.
async fn write_audit(state: &AppState, actors: &RequestActors, method: &str, session_id: &str,
    before: Value, after: Value) {
    let note = Note { target_id: None, label: None, before: Some(before), after: Some(after) };
    audit::record(state, actors, method, SCHEDULE_PATH, Some(session_id), note, 200).await;
}

// ── Routes ───────────────────────────────────────────────────────────────────

pub fn router() -> axum::Router<AppState> {
    use axum::routing::get;
    axum::Router::new()
        .route("/api/v1/schedules", get(list_schedules))
        .route("/api/v1/schedules/history", get(schedule_history))
        .route("/api/v1/sessions/{session_id}/schedule", get(get_schedule).post(save_schedule).delete(delete_schedule))
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn list_schedules(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let rows = sqlx::query(
        "SELECT j.id, j.session_id, s.name AS session_name, j.cron_expr,
                j.next_run, j.enabled, j.last_run, j.last_error, j.last_error_at,
                j.retrain_mode
           FROM scheduled_jobs j
           JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
          WHERE j.tenant_id = $1
          ORDER BY j.next_run NULLS LAST",
    )
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        out.push(Value::Object(row_json(r)?));
    }
    Ok(ok(Value::Array(out)))
}

pub async fn schedule_history(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let limit = int_query(&mut errs, &q, "limit", None, None);
    errs.into_result()?;
    let limit = limit.unwrap_or(20).clamp(1, 100);

    let rows = sqlx::query(
        "SELECT j.id, j.session_id, s.name AS session_name, j.status,
                j.created_at, j.started_at, j.completed_at, j.error
           FROM jobs j
           JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
          WHERE j.tenant_id = $1 AND j.created_by = 'scheduler'
          ORDER BY j.created_at DESC
          LIMIT $2",
    )
    .bind(&user.tenant_id)
    .bind(limit)
    .fetch_all(&state.pool)
    .await?;
    let mut entries: Vec<(DateTime<Utc>, Map<String, Value>)> = Vec::new();
    for r in &rows {
        let mut e = row_json(r)?;
        e.insert("reason".into(), Value::Null);
        e.insert("reason_params".into(), json!({}));
        entries.push((r.try_get("created_at")?, e));
    }
    let extra = sqlx::query(
        "SELECT r.id, r.session_id, COALESCE(s.name, t.name, '') AS session_name,
                UPPER(r.outcome) AS status, r.ran_at AS created_at,
                r.reason, r.reason_params
           FROM schedule_runs r
           LEFT JOIN scheduled_jobs j ON j.id = r.schedule_id AND j.tenant_id = r.tenant_id
           LEFT JOIN sessions t ON t.id = j.session_id AND t.tenant_id = r.tenant_id
           LEFT JOIN sessions s ON s.id = r.session_id AND s.tenant_id = r.tenant_id
          WHERE r.tenant_id = $1 AND r.outcome <> 'launched'
          ORDER BY r.ran_at DESC LIMIT $2",
    )
    .bind(&user.tenant_id)
    .bind(limit)
    .fetch_all(&state.pool)
    .await?;
    for r in &extra {
        let mut e = row_json(r)?;
        e.insert("started_at".into(), Value::Null);
        e.insert("completed_at".into(), Value::Null);
        e.insert("error".into(), Value::Null);
        entries.push((r.try_get("created_at")?, e));
    }
    // `entries.sort(key=created_at, reverse=True)`: stable, newest first.
    entries.sort_by(|a, b| b.0.cmp(&a.0));
    let out: Vec<Value> = entries.into_iter().take(limit as usize).map(|(_, e)| Value::Object(e)).collect();
    Ok(ok(Value::Array(out)))
}

pub async fn get_schedule(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    if get_session(&state.pool, &user.tenant_id, &session_id).await?.is_none() {
        return Err(session_not_found());
    }
    let row = sqlx::query(
        "SELECT id, session_id, cron_expr, next_run, enabled, last_run, last_error, last_error_at, retrain_mode \
         FROM scheduled_jobs WHERE session_id = $1 AND tenant_id = $2",
    )
    .bind(&session_id)
    .bind(&user.tenant_id)
    .fetch_optional(&state.pool)
    .await?;
    match row {
        Some(r) => Ok(ok(Value::Object(row_json(&r)?))),
        None => Err(ApiError::app("schedule_not_configured", "No schedule configured for this session", 404, json!({}))),
    }
}

pub async fn save_schedule(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE_WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let obj = validation::body_object(&body)?;
    let body = validate_body(&obj)?;

    if get_session(&state.pool, &user.tenant_id, &session_id).await?.is_none() {
        return Err(session_not_found());
    }
    let zone = tenant_zone(&state, &user.tenant_id).await?;
    let next = next_run(&body.cron_expr, &zone, Utc::now(), &user.tenant_id);
    let existing = sqlx::query(
        "SELECT id, cron_expr, enabled, retrain_mode FROM scheduled_jobs WHERE session_id = $1 AND tenant_id = $2",
    )
    .bind(&session_id)
    .bind(&user.tenant_id)
    .fetch_optional(&state.pool)
    .await?
    .map(|r| row_json(&r))
    .transpose()?;

    let effective_mode: Value = match &body.retrain_mode {
        Some(m) => json!(m),
        None => existing.as_ref().map(|e| e["retrain_mode"].clone()).unwrap_or_else(|| json!("refit")),
    };
    let before = match &existing {
        Some(e) => json!({"cron_expr": e["cron_expr"], "enabled": e["enabled"], "retrain_mode": e["retrain_mode"]}),
        None => Value::Null,
    };
    let after = json!({"cron_expr": body.cron_expr, "enabled": body.enabled, "retrain_mode": effective_mode});

    let schedule_id: Value = if let Some(e) = &existing {
        sqlx::query(
            "UPDATE scheduled_jobs SET cron_expr=$1, next_run=$2, enabled=$3, \
             retrain_mode=COALESCE($4, retrain_mode) WHERE id=$5",
        )
        .bind(&body.cron_expr)
        .bind(next)
        .bind(body.enabled)
        .bind(&body.retrain_mode)
        .bind(e["id"].as_str())
        .execute(&state.pool)
        .await?;
        e["id"].clone()
    } else {
        let row: Option<(String,)> = sqlx::query_as(
            "INSERT INTO scheduled_jobs (id, tenant_id, session_id, cron_expr, next_run, enabled, retrain_mode)
             VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6) RETURNING id",
        )
        .bind(&user.tenant_id)
        .bind(&session_id)
        .bind(&body.cron_expr)
        .bind(next)
        .bind(body.enabled)
        .bind(body.retrain_mode.as_deref().unwrap_or("refit"))
        .fetch_optional(&state.pool)
        .await?;
        row.map(|r| Value::String(r.0)).unwrap_or(Value::Null)
    };
    tracing::info!("[schedule] saved session={session_id} cron={}", body.cron_expr);
    write_audit(&state, &actors, "POST", &session_id, before, after).await;
    Ok(ok(json!({
        "id": schedule_id,
        "session_id": session_id,
        "cron_expr": body.cron_expr,
        "next_run": isoformat_utc(&next),
        "enabled": body.enabled,
        "retrain_mode": effective_mode,
    })))
}

pub async fn delete_schedule(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    if get_session(&state.pool, &user.tenant_id, &session_id).await?.is_none() {
        return Err(session_not_found());
    }
    let previous = sqlx::query("SELECT cron_expr, enabled FROM scheduled_jobs WHERE session_id = $1 AND tenant_id = $2")
        .bind(&session_id)
        .bind(&user.tenant_id)
        .fetch_optional(&state.pool)
        .await?
        .map(|r| row_json(&r))
        .transpose()?;
    sqlx::query("DELETE FROM scheduled_jobs WHERE session_id = $1 AND tenant_id = $2")
        .bind(&session_id)
        .bind(&user.tenant_id)
        .execute(&state.pool)
        .await?;
    let before = previous.map(Value::Object).unwrap_or(Value::Null);
    write_audit(&state, &actors, "DELETE", &session_id, before, Value::Null).await;
    Ok(ok(json!({"deleted": session_id})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cron_validator_messages() {
        assert_eq!(validate_cron("  0 6 * * 1 ").unwrap(), "0 6 * * 1");
        assert_eq!(validate_cron("0 6 * *").unwrap_err(), "cron_expr must have exactly 5 fields (e.g. '0 6 * * 1')");
        assert_eq!(validate_cron("0 99 * * 1").unwrap_err(),
            "cron_expr is not a valid cron expression: [0 99 * * 1] is not acceptable, out of range");
    }

    #[test]
    fn body_validation_shapes() {
        let obj = json!({"cron_expr": "0 6 * *", "enabled": "maybe", "retrain_mode": "x"});
        let e = validate_body(obj.as_object().unwrap()).err().unwrap();
        let d = &e.body["detail"];
        assert_eq!(d[0]["type"], "value_error");
        assert_eq!(d[0]["msg"], "Value error, cron_expr must have exactly 5 fields (e.g. '0 6 * * 1')");
        assert_eq!(d[0]["ctx"]["error"], "cron_expr must have exactly 5 fields (e.g. '0 6 * * 1')");
        assert_eq!(d[1]["type"], "bool_parsing");
        assert_eq!(d[2]["loc"], json!(["body", "retrain_mode"]));
        let ok = validate_body(json!({"cron_expr": "0 6 * * 1"}).as_object().unwrap()).ok().unwrap();
        assert!(ok.enabled);
        assert!(ok.retrain_mode.is_none());
    }

    #[test]
    fn dumps_length_matches_python() {
        // len(json.dumps({"cron_expr": "0 6 * * 1", "enabled": True, "retrain_mode": "refit"})) == 68
        let v = json!({"cron_expr": "0 6 * * 1", "enabled": true, "retrain_mode": "refit"});
        assert_eq!(crate::pyjson::dumps(&v).chars().count(), 68);
        assert_eq!(crate::pyjson::dumps(&json!("é")).chars().count(), 8);
    }

    #[test]
    fn impossible_date_falls_back_to_a_day_later() {
        let now = Utc::now();
        let z = tz::Zone::Fixed(0);
        assert_eq!(next_run("0 6 31 2 *", &z, now, "t"), now + Duration::hours(24));
    }
}
