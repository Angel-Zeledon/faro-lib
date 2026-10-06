//! `GET /api/v1/tenant/timezone` (backend/api/v1/timezone.py): the tenant's
//! clock. Every role and a read key (`timezone` is an exposed tag).
//!
//! `PATCH /tenant/timezone` (admin only) saves the zone and then re-anchors
//! every armed schedule (`_reanchor_schedules`) with the croniter port of
//! `routes/schedule/`, so `next_run` is recomputed in the new zone.

use axum::body::Bytes;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::{Extension, Json};
use serde_json::{json, Map, Value};

use crate::audit::{self, Note};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::pycompat::py_strip;
use crate::routes::schedule::{next_run, tenant_zone};
use crate::validation::{self, body_object, str_field, Errors, Field, NO_STR_RULES};
use chrono::Utc;
use crate::error::ApiError;
use crate::routes::r1::currency::{get_settings, supported_or_default, update_settings};
use crate::routes::ok;
use crate::state::AppState;

pub const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };

/// `SUPPORTED`, in insertion order: (zone, English fallback label, country).
const SUPPORTED: [(&str, &str, Option<&str>); 13] = [
    ("America/Costa_Rica", "Costa Rica", Some("CR")),
    ("America/Bogota", "Colombia", Some("CO")),
    ("America/Mexico_City", "Mexico", Some("MX")),
    ("America/Lima", "Peru", Some("PE")),
    ("America/Santiago", "Chile", Some("CL")),
    ("America/Argentina/Buenos_Aires", "Argentina", Some("AR")),
    ("America/Guayaquil", "Ecuador", Some("EC")),
    ("America/Guatemala", "Guatemala", Some("GT")),
    ("America/Panama", "Panama", Some("PA")),
    ("America/Santo_Domingo", "Dominican Republic", Some("DO")),
    ("Europe/Madrid", "Spain", Some("ES")),
    ("America/New_York", "United States (east)", Some("US")),
    ("UTC", "UTC", None),
];
const DEFAULT_TZ: &str = "America/Costa_Rica";

/// `{"timezone": tz, **SUPPORTED[tz]}`.
fn entry(tz: &str) -> Option<Value> {
    SUPPORTED.iter().find(|(z, ..)| *z == tz).map(|(z, label, country)| {
        let mut m = Map::new();
        m.insert("timezone".into(), json!(z));
        m.insert("label".into(), json!(label));
        m.insert("country".into(), country.map(|c| json!(c)).unwrap_or(Value::Null));
        Value::Object(m)
    })
}

/// `timezone_of`: the tenant's IANA zone, always one of `SUPPORTED`.
pub async fn timezone_of(pool: &sqlx::PgPool, tenant_id: &str) -> Result<String, ApiError> {
    let settings = get_settings(pool, tenant_id).await?;
    supported_or_default(settings.get("timezone"), |z| entry(z).is_some(), DEFAULT_TZ)
}

pub async fn get_timezone(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let current = timezone_of(&state.pool, &user.tenant_id).await?;
    let supported: Vec<Value> = SUPPORTED.iter().filter_map(|(z, ..)| entry(z)).collect();
    Ok(ok(json!({
        "current": entry(&current).ok_or_else(ApiError::internal)?,
        "supported": supported,
    })))
}

pub const ADMIN_WRITE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("admin only: no API key can hold the admin role"),
    is_mcp: false,
};

pub async fn set_timezone(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ADMIN_WRITE, &actors).await?;
    auth::require_role(&user, &["admin"])?;
    let obj = body_object(&body)?;
    let mut errs = Errors::default();
    let tz = str_field(&mut errs, &obj, &[Value::String("body".into())], "timezone", true, false, &NO_STR_RULES);
    errs.into_result()?;
    let Field::Value(tz) = tz else { return Err(ApiError::internal()) };

    let tz = py_strip(&tz).to_string();
    if entry(&tz).is_none() {
        let mut sorted: Vec<&str> = SUPPORTED.iter().map(|(z, ..)| *z).collect();
        sorted.sort_unstable();
        return Err(ApiError::app(
            "timezone_not_supported",
            format!("Timezone '{tz}' is not supported."),
            400,
            json!({"timezone": tz, "supported": sorted}),
        ));
    }
    let previous = timezone_of(&state.pool, &user.tenant_id).await?;
    update_settings(&state.pool, &user.tenant_id, "timezone", Value::String(tz.clone())).await?;
    let note = Note {
        target_id: Some("timezone".into()),
        label: Some("timezone".into()),
        before: Some(json!({"timezone": previous})),
        after: Some(json!({"timezone": tz})),
    };
    audit::record(&state, &actors, "PATCH", "/tenant/timezone", None, note, 200).await;
    tracing::info!("[timezone] tenant {} -> {} (by {})", user.tenant_id, tz, user.user_id);
    let moved = reanchor_schedules(&state, &user.tenant_id).await?;
    Ok(ok(json!({"current": entry(&tz).ok_or_else(ApiError::internal)?, "schedules_rescheduled": moved})))
}

/// `_reanchor_schedules`: recompute `next_run` of the armed schedules in the
/// zone just saved. One failing row never blocks the others (as in Python).
async fn reanchor_schedules(state: &AppState, tenant_id: &str) -> Result<i64, ApiError> {
    let rows: Vec<(String, String)> =
        sqlx::query_as("SELECT id, cron_expr FROM scheduled_jobs WHERE tenant_id = $1 AND enabled = true")
            .bind(tenant_id)
            .fetch_all(&state.pool)
            .await?;
    let zone = tenant_zone(state, tenant_id).await?;
    let mut moved = 0;
    for (id, cron_expr) in rows {
        let next = next_run(&cron_expr, &zone, Utc::now(), tenant_id);
        match sqlx::query("UPDATE scheduled_jobs SET next_run = $1 WHERE id = $2")
            .bind(next)
            .bind(&id)
            .execute(&state.pool)
            .await
        {
            Ok(_) => moved += 1,
            Err(e) => tracing::error!("[timezone] tenant {tenant_id}: could not re-anchor schedule {id} ({cron_expr:?}): {e}"),
        }
    }
    Ok(moved)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zones_match_the_python_source() {
        let src = include_str!("../../../../backend/api/v1/timezone.py");
        for (zone, label, country) in SUPPORTED {
            let line = src.lines().find(|l| l.trim_start().starts_with(&format!("\"{zone}\":"))).unwrap();
            assert!(line.contains(&format!("\"label\": \"{label}\"")), "{zone}");
            let c = country.map(|c| format!("\"{c}\"")).unwrap_or_else(|| "None".into());
            assert!(line.contains(&format!("\"country\": {c}")), "{zone}");
        }
        let n = src.lines().filter(|l| l.contains("\"label\":") && l.contains("\"country\":")).count();
        assert_eq!(n, SUPPORTED.len());
    }
}
