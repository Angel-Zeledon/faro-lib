//! Lineage manifests (`backend/api/v1/audit.py::manifest_router`, tag
//! `sessions`, exposed to read keys; the data lives in `session_manifests`,
//! written by the Python worker when a run ends):
//!
//! * `GET /sessions/{id}/manifest`   the latest manifest of a session
//! * `GET /training/run-durations`   median / p95 of the last N runs, from the
//!                                    manifests (`backend/lineage/run_metrics.py`)
//!
//! Read-only. Nothing here builds a manifest (that needs the engine's version
//! strings and the dataset hash, so it stays in the Python worker).

use axum::extract::{Path, State};
use axum::http::{HeaderMap, Uri};
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::Row;

use crate::auth::{self, RequestActors};
use crate::error::ApiError;
use crate::pycompat::{isoformat_utc, py_strip};
use crate::routes::ok;
use crate::routes::sessions::{get_session, int_query, query_map, query_pairs, session_not_found, ROUTE_READ};
use crate::state::AppState;
use crate::validation::Errors;

const DEFAULT_RUNS: i64 = 50;
const MAX_RUNS: i64 = 200;
/// `SIZE_BUCKETS`: (min series, max series or open-ended).
const SIZE_BUCKETS: [(i64, Option<i64>); 4] = [(1, Some(50)), (51, Some(200)), (201, Some(1000)), (1001, None)];

pub fn router() -> axum::Router<AppState> {
    use axum::routing::get;
    axum::Router::new()
        .route("/api/v1/sessions/{session_id}/manifest", get(get_manifest))
        .route("/api/v1/training/run-durations", get(run_durations))
}

pub async fn get_manifest(
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
        "SELECT id, session_id, job_id, outcome, manifest, created_at \
         FROM session_manifests WHERE tenant_id = $1 AND session_id = $2 \
         ORDER BY created_at DESC LIMIT 1",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .fetch_optional(&state.pool)
    .await?;
    let Some(row) = row else {
        return Err(ApiError::app(
            "manifest_not_available",
            "This session has no lineage manifest: it has not finished a training run since manifests were introduced.",
            404,
            json!({"session_id": session_id}),
        ));
    };
    let created_at: DateTime<Utc> = row.try_get("created_at")?;
    let mut out = Map::new();
    out.insert("id".into(), json!(row.try_get::<String, _>("id")?));
    out.insert("session_id".into(), json!(row.try_get::<String, _>("session_id")?));
    out.insert("job_id".into(), json!(row.try_get::<Option<String>, _>("job_id")?));
    out.insert("outcome".into(), json!(row.try_get::<String, _>("outcome")?));
    out.insert("created_at".into(), json!(isoformat_utc(&created_at)));
    out.insert("manifest".into(), row.try_get::<Value, _>("manifest")?);
    Ok(ok(Value::Object(out)))
}

/// Python's `float(str)`: strip, optional sign, digits with single
/// underscores between digits, `inf` / `infinity` / `nan` in any case.
fn py_float(raw: &str) -> Option<f64> {
    let s = py_strip(raw);
    if s.is_empty() {
        return None;
    }
    let bytes = s.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'_' {
            let digit = |j: Option<usize>| j.and_then(|j| bytes.get(j)).map_or(false, |c| c.is_ascii_digit());
            if !(digit(i.checked_sub(1)) && digit(Some(i + 1))) {
                return None;
            }
        }
    }
    let cleaned: String = s.chars().filter(|c| *c != '_').collect();
    let unsigned = cleaned.trim_start_matches(['+', '-']);
    let signs = cleaned.len() - unsigned.len();
    if signs > 1 {
        return None;
    }
    let lower = unsigned.to_ascii_lowercase();
    if lower == "inf" || lower == "infinity" || lower == "nan" {
        return cleaned.parse::<f64>().ok();
    }
    if !unsigned.bytes().all(|b| b.is_ascii_digit() || matches!(b, b'.' | b'e' | b'E' | b'+' | b'-')) {
        return None;
    }
    cleaned.parse::<f64>().ok()
}

/// Python's `int(str)`: strip, optional sign, digits with single underscores
/// between digits. No fraction (unlike pydantic's lax int).
fn py_int(raw: &str) -> Option<i64> {
    let s = py_strip(raw);
    let body = s.strip_prefix(['+', '-']).unwrap_or(s);
    let bytes = body.as_bytes();
    if bytes.is_empty() || bytes[0] == b'_' || bytes[bytes.len() - 1] == b'_' {
        return None;
    }
    let mut prev = false;
    for &b in bytes {
        match b {
            b'0'..=b'9' => prev = false,
            b'_' if !prev => prev = true,
            _ => return None,
        }
    }
    let cleaned: String = s.chars().filter(|c| *c != '_').collect();
    cleaned.parse::<i64>().ok()
}

/// `round(x, 1)`: correctly rounded, ties to even on the exact binary value,
/// which is what `{:.1}` does too.
fn round1(x: f64) -> f64 {
    format!("{x:.1}").parse().unwrap_or(x)
}

/// `_percentile`: nearest rank of a sorted, non-empty list.
fn percentile(sorted: &[f64], pct: f64) -> f64 {
    let rank = ((pct / 100.0 * sorted.len() as f64).ceil() as usize).max(1);
    sorted[rank - 1]
}

/// `_summary`.
fn summary(durations: &[f64]) -> Map<String, Value> {
    let mut ordered = durations.to_vec();
    ordered.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = ordered.len();
    let median = if n % 2 == 1 { ordered[n / 2] } else { (ordered[n / 2 - 1] + ordered[n / 2]) / 2.0 };
    let mut m = Map::new();
    m.insert("runs".into(), json!(n));
    m.insert("median_seconds".into(), json!(round1(median)));
    m.insert("p95_seconds".into(), json!(round1(percentile(&ordered, 95.0))));
    m.insert("max_seconds".into(), json!(round1(ordered[n - 1])));
    m
}

fn bucket_of(series: i64) -> Option<(i64, Option<i64>)> {
    SIZE_BUCKETS.iter().copied().find(|(low, high)| series >= *low && high.map_or(true, |h| series <= h))
}

pub async fn run_durations(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let limit = int_query(&mut errs, &q, "limit", Some(1), Some(MAX_RUNS));
    errs.into_result()?;
    let limit = limit.unwrap_or(DEFAULT_RUNS).clamp(1, MAX_RUNS);

    let rows = sqlx::query(
        "SELECT outcome, \
                manifest #>> '{timing,duration_seconds}' AS duration, \
                manifest #>> '{counts,skus_forecast}'    AS series, \
                manifest #>> '{session,granularity}'     AS granularity, \
                created_at \
         FROM session_manifests WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2",
    )
    .bind(&user.tenant_id)
    .bind(limit)
    .fetch_all(&state.pool)
    .await?;

    let mut timed: Vec<(f64, i64, String)> = Vec::new();
    let (mut failed, mut untimed) = (0, 0);
    for r in &rows {
        let outcome: String = r.try_get("outcome")?;
        if outcome != "COMPLETED" {
            failed += 1;
            continue;
        }
        let duration: Option<String> = r.try_get("duration")?;
        let series: Option<String> = r.try_get("series")?;
        let granularity: Option<String> = r.try_get("granularity")?;
        // float(None) is a TypeError, which `except (TypeError, ValueError)` also catches.
        let seconds = duration.as_deref().and_then(py_float);
        // `int(r["series"] or 0)`: an empty string counts as 0.
        let n = match series.as_deref() {
            None | Some("") => Some(0),
            Some(s) => py_int(s),
        };
        match (seconds, n) {
            (Some(s), Some(n)) => {
                // `r["granularity"] or "unknown"`.
                let g = granularity.filter(|g| !g.is_empty()).unwrap_or_else(|| "unknown".into());
                timed.push((s, n, g));
            }
            _ => untimed += 1,
        }
    }

    let mut by_size: Vec<Value> = Vec::new();
    for (low, high) in SIZE_BUCKETS {
        let in_bucket: Vec<f64> = timed.iter().filter(|(_, n, _)| bucket_of(*n) == Some((low, high))).map(|t| t.0).collect();
        if !in_bucket.is_empty() {
            let mut m = Map::new();
            m.insert("min_series".into(), json!(low));
            m.insert("max_series".into(), json!(high));
            m.extend(summary(&in_bucket));
            by_size.push(Value::Object(m));
        }
    }
    let mut granularities: Vec<&String> = timed.iter().map(|t| &t.2).collect();
    granularities.sort();
    granularities.dedup();
    let mut by_granularity: Vec<Value> = Vec::new();
    for g in granularities {
        let in_group: Vec<f64> = timed.iter().filter(|t| &t.2 == g).map(|t| t.0).collect();
        let mut m = Map::new();
        m.insert("granularity".into(), json!(g));
        m.extend(summary(&in_group));
        by_granularity.push(Value::Object(m));
    }
    let all: Vec<f64> = timed.iter().map(|t| t.0).collect();
    Ok(ok(json!({
        "window_runs": rows.len(),
        "limit": limit,
        "completed_runs": timed.len(),
        "failed_runs": failed,
        "untimed_runs": untimed,
        "overall": if timed.is_empty() { Value::Null } else { Value::Object(summary(&all)) },
        "by_size": by_size,
        "by_granularity": by_granularity,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_number_parsing() {
        assert_eq!(py_float(" 12.5 "), Some(12.5));
        assert_eq!(py_float("1_0.5"), Some(10.5));
        assert_eq!(py_float("1e3"), Some(1000.0));
        assert_eq!(py_float("5."), Some(5.0));
        assert_eq!(py_float("abc"), None);
        assert_eq!(py_float("1__0"), None);
        assert_eq!(py_float("--1"), None);
        assert!(py_float("inf").unwrap().is_infinite());
        assert_eq!(py_int("12"), Some(12));
        assert_eq!(py_int(" +1_2 "), Some(12));
        assert_eq!(py_int("12.0"), None);
        assert_eq!(py_int("_1"), None);
        assert_eq!(py_int(""), None);
    }

    #[test]
    fn rounding_and_percentiles_match_python() {
        assert_eq!(round1(0.25), 0.2);
        assert_eq!(round1(0.35), 0.3); // 0.35 is 0.34999... in binary
        assert_eq!(round1(2.675), 2.7);
        let v = [1.0, 2.0, 3.0, 4.0];
        assert_eq!(percentile(&v, 95.0), 4.0);
        let s = summary(&v);
        assert_eq!(s["median_seconds"], json!(2.5));
        assert_eq!(bucket_of(50), Some((1, Some(50))));
        assert_eq!(bucket_of(0), None);
        assert_eq!(bucket_of(5000), Some((1001, None)));
    }
}
