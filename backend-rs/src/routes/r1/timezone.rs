//! `GET /api/v1/tenant/timezone` (backend/api/v1/timezone.py): the tenant's
//! clock. Every role and a read key (`timezone` is an exposed tag).
//!
//! NOT migrated: `PATCH /tenant/timezone`. After saving the zone it
//! re-anchors every armed schedule (`_reanchor_schedules`), recomputing
//! `next_run` with `croniter` in the new zone through `zoneinfo`. Matching
//! that byte for byte needs croniter's exact semantics (day-of-month /
//! day-of-week OR rule, DST gaps and folds) and the same tz database Python
//! has installed, which is the schedule router's job, not this one's. Until
//! the schedule group ships a cron engine with its own contract cases, the
//! proxy keeps PATCH on Python (method-level routing).

use axum::extract::State;
use axum::http::HeaderMap;
use axum::{Extension, Json};
use serde_json::{json, Map, Value};

use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::routes::r1::currency::{get_settings, supported_or_default};
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
