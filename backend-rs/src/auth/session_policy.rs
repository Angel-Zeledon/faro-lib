//! The Rust half of the per-tenant session policy: token validation.
//!
//! `backend/auth/session_policy.py::enforce_access_token`, check for check. The
//! policy itself (the `tenant_session_policies` row) is written by
//! `routes/session_policy.rs`; Python enforces it at login, refresh and password
//! entry (auth stays Python this pass), and BOTH guards enforce it on every
//! access token here and in `backend/auth/guards.py`.
//!
//! No policy row for the tenant (or no maximum lifetime and no idle limit set)
//! means: one joined read, no write, no refusal - exactly the behaviour before
//! this feature.

use axum::http::HeaderMap;
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::error::ApiError;

/// A request that says it is a page polling for a badge, not a person acting:
/// checked against the idle limit, but not counted as activity.
pub const BACKGROUND_HEADER: &str = "x-stockai-background";
/// Never write `last_activity_at` more often than this (seconds) per user.
pub const ACTIVITY_WRITE_THROTTLE_SECONDS: f64 = 30.0;
pub const DEFAULT_LOCKOUT_MINUTES: i32 = 15;

/// (field, min, max): shared with Python's `BOUNDS` and the table's CHECKs.
pub const BOUNDS: [(&str, i64, i64); 7] = [
    ("max_session_hours", 1, 168),
    ("idle_timeout_minutes", 5, 1440),
    ("min_password_length", 8, 64),
    ("password_max_age_days", 7, 730),
    ("max_concurrent_sessions", 1, 20),
    ("lockout_threshold", 3, 20),
    ("lockout_minutes", 1, 1440),
];

pub fn is_background(headers: &HeaderMap) -> bool {
    headers
        .get(BACKGROUND_HEADER)
        .and_then(|v| v.to_str().ok())
        .map(|v| v.trim() == "1")
        .unwrap_or(false)
}

fn max_lifetime_error(hours: i32) -> ApiError {
    ApiError::app(
        "session_max_lifetime",
        "Your session reached the maximum length your organization allows. Sign in again.",
        401,
        json!({"hours": hours}),
    )
}

fn idle_error(minutes: i32) -> ApiError {
    ApiError::app(
        "session_idle_timeout",
        "You were signed out after a period of inactivity. Sign in again.",
        401,
        json!({"minutes": minutes}),
    )
}

/// The pure decision, separated from the queries so it can be unit-tested.
/// `session_start` is the token's `sat` (else `iat`) in epoch seconds,
/// `idle_seconds` the time since the user's last counted request.
pub fn decide(
    max_hours: Option<i32>,
    idle_minutes: Option<i32>,
    session_start: Option<f64>,
    now: f64,
    idle_seconds: Option<f64>,
) -> Result<(), ApiError> {
    if let (Some(hours), Some(start)) = (max_hours, session_start) {
        if now - start > f64::from(hours) * 3600.0 {
            return Err(max_lifetime_error(hours));
        }
    }
    if let (Some(minutes), Some(idle)) = (idle_minutes, idle_seconds) {
        if idle > f64::from(minutes) * 60.0 {
            return Err(idle_error(minutes));
        }
    }
    Ok(())
}

/// `enforce_access_token`.
pub async fn enforce_access_token(
    pool: &PgPool,
    payload: &Map<String, Value>,
    background: bool,
    now: f64,
) -> Result<(), ApiError> {
    let Some(user_id) = payload.get("sub").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) else {
        return Ok(());
    };
    let row: Option<(Option<i32>, Option<i32>, Option<f64>)> = sqlx::query_as(
        "SELECT p.max_session_hours, p.idle_timeout_minutes,
                EXTRACT(EPOCH FROM (NOW() - u.last_activity_at))::float8
           FROM users u
           JOIN tenant_session_policies p ON p.tenant_id = u.tenant_id
          WHERE u.id = $1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    let Some((max_hours, idle_minutes, idle_seconds)) = row else { return Ok(()) };

    // A token with no clock claim cannot prove its age (it predates `iat`).
    let started = payload
        .get("sat")
        .and_then(|v| v.as_f64())
        .or_else(|| payload.get("iat").and_then(|v| v.as_f64()));
    decide(max_hours, idle_minutes, started, now, idle_seconds)?;

    if idle_minutes.is_some() && !background {
        sqlx::query(
            "UPDATE users SET last_activity_at = NOW()
              WHERE id = $1
                AND (last_activity_at IS NULL
                     OR last_activity_at < NOW() - make_interval(secs => $2))",
        )
        .bind(user_id)
        .bind(ACTIVITY_WRITE_THROTTLE_SECONDS)
        .execute(pool)
        .await?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_limits_never_refuses() {
        assert!(decide(None, None, Some(0.0), 1e9, Some(1e9)).is_ok());
    }

    #[test]
    fn max_lifetime_is_measured_from_the_session_start() {
        let h = 3600.0;
        assert!(decide(Some(8), None, Some(1000.0), 1000.0 + 8.0 * h, None).is_ok());
        let e = decide(Some(8), None, Some(1000.0), 1000.0 + 8.0 * h + 1.0, None).unwrap_err();
        assert_eq!(e.code(), Some("session_max_lifetime"));
        assert_eq!(e.status.as_u16(), 401);
        assert_eq!(e.body["error_params"], json!({"hours": 8}));
        // No clock claim at all: cannot prove an age, so it is not refused.
        assert!(decide(Some(8), None, None, 1e12, None).is_ok());
    }

    #[test]
    fn idle_limit_refuses_only_past_the_limit_and_only_with_a_last_activity() {
        assert!(decide(None, Some(30), None, 0.0, Some(1800.0)).is_ok());
        let e = decide(None, Some(30), None, 0.0, Some(1800.5)).unwrap_err();
        assert_eq!(e.code(), Some("session_idle_timeout"));
        assert_eq!(e.body["error_params"], json!({"minutes": 30}));
        // Never active since the policy was set: counts from now, not refused.
        assert!(decide(None, Some(30), None, 0.0, None).is_ok());
    }

    #[test]
    fn lifetime_is_checked_before_idle() {
        let e = decide(Some(1), Some(5), Some(0.0), 10_000.0, Some(10_000.0)).unwrap_err();
        assert_eq!(e.code(), Some("session_max_lifetime"));
    }

    #[test]
    fn background_header_must_be_exactly_one() {
        let mut h = HeaderMap::new();
        assert!(!is_background(&h));
        h.insert(BACKGROUND_HEADER, "1".parse().unwrap());
        assert!(is_background(&h));
        h.insert(BACKGROUND_HEADER, " 1 ".parse().unwrap());
        assert!(is_background(&h));
        h.insert(BACKGROUND_HEADER, "true".parse().unwrap());
        assert!(!is_background(&h));
    }
}
