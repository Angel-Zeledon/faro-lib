//! Continuous audit export to the customer's SIEM: configuration, cursor and
//! delivery-log routes. NEW routes (no Python twin, so no Python failover).
//!
//! * `GET    /audit-stream`              destination, state and lag
//! * `PUT    /audit-stream`              create / change the destination
//! * `DELETE /audit-stream`              remove it (and its delivery log)
//! * `POST   /audit-stream/enable`       turn it on (clears an auto-disable)
//! * `POST   /audit-stream/disable`      turn it off; the cursor is kept
//! * `POST   /audit-stream/rotate-secret` new HMAC secret, returned once
//! * `POST   /audit-stream/replay`       move the cursor BACK (by cursor or time)
//! * `POST   /audit-stream/test`         one signed test record
//! * `GET    /audit-stream/deliveries`   the delivery log
//!
//! These routes only write `audit_streams`. Delivery is the Python worker loop
//! `backend/audit_stream/service.py` (why: it sends through the webhook SSRF
//! guard and reuses the webhook signing and retry code). The loop and these
//! routes meet only through that table: the cursor moves forward by the loop's
//! compare-and-set and backward by `replay`, and the loop discards a late
//! success whose cursor was replayed under it.
//!
//! Admin only, never an API key (the trail names people), and refused to an
//! admin limited to some warehouses, exactly like `GET /audit`. A destination
//! is per tenant. The signing secret is shown once, never read back.

use axum::body::Bytes;
use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::postgres::PgRow;
use sqlx::Row;

use crate::audit::{self, Note};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::query::{self, PyInt, Query};
use crate::routes::ok;
use crate::ssrf;
use crate::state::AppState;
use crate::validation::{self, str_field, Errors, Field, StrRules};

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal: the audit trail names the tenant's people; an administrator manages its export on the screen",
    ),
    is_mcp: false,
};

pub const DEFAULT_BATCH_SIZE: i64 = 500;
pub const MAX_BATCH_SIZE: i64 = 1000;
pub const MAX_LOG_PAGE: i64 = 200;
const PENDING_CAP: i64 = 100_000;

// -- Pure helpers (unit-tested below) ------------------------------------------

/// `"<xid>:<seq>"`, the same text the Python loop writes in every record.
pub fn format_cursor(xid: i64, seq: i64) -> String {
    format!("{xid}:{seq}")
}

pub fn parse_cursor(text: &str) -> Option<(i64, i64)> {
    let (head, tail) = text.split_once(':')?;
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if !digits(head) || !digits(tail) {
        return None;
    }
    let (xid, seq) = (head.parse::<i64>().ok()?, tail.parse::<i64>().ok()?);
    let limit = 1i64 << 62;
    (xid <= limit && seq <= limit).then_some((xid, seq))
}

/// `since` as an instant: an RFC 3339 timestamp, or a date (midnight UTC).
pub fn parse_since(text: &str) -> Option<DateTime<Utc>> {
    let t = text.trim();
    if let Ok(dt) = DateTime::parse_from_rfc3339(t) {
        return Some(dt.with_timezone(&Utc));
    }
    NaiveDate::parse_from_str(t, "%Y-%m-%d").ok().and_then(|d| d.and_hms_opt(0, 0, 0)).map(|n| n.and_utc())
}

fn allow_private(state: &AppState) -> bool {
    state
        .settings
        .raw(ssrf::ALLOW_PRIVATE_SETTING)
        .and_then(crate::config::parse_bool)
        .unwrap_or(false)
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

fn not_configured() -> ApiError {
    ApiError::app("audit_stream_not_configured", "No audit stream is configured", 404, json!({}))
}

fn host_of(url: &str) -> Option<String> {
    ssrf::parse_https(url, "audit_stream").ok().map(|(h, _)| h)
}

// -- Auth ---------------------------------------------------------------------

async fn admin_read(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    wscope::require_company_wide(&state.pool, &user).await?;
    Ok(user)
}

async fn admin_write(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    auth::require_analyst_or_above(state, &user).await?;
    wscope::require_company_wide(&state.pool, &user).await?;
    Ok(user)
}

// -- Presenting ---------------------------------------------------------------

fn ts(row: &PgRow, col: &str) -> Result<Value, sqlx::Error> {
    let v: Option<DateTime<Utc>> = row.try_get(col)?;
    Ok(v.map(|d| json!(isoformat_utc(&d))).unwrap_or(Value::Null))
}

/// The status the screen shows: the destination, whether it is on, how far it
/// got, how far behind it is. Never the secret.
async fn present(state: &AppState, tenant_id: &str) -> Result<Value, ApiError> {
    let row = sqlx::query(
        "SELECT url, enabled, disabled_at, disabled_reason, cursor_xid, cursor_seq, batch_size,
                consecutive_failures, failure_days, last_error, last_status_code, last_attempt_at,
                last_success_at, delivered_records, next_attempt_at, test_requested_at,
                secret_rotated_at, created_at, updated_at
           FROM audit_streams WHERE tenant_id = $1",
    )
    .bind(tenant_id)
    .fetch_optional(&state.pool)
    .await?;
    let Some(row) = row else { return Ok(json!({"configured": false})) };
    let (xid, seq): (i64, i64) = (row.try_get("cursor_xid")?, row.try_get("cursor_seq")?);

    // Rows after the cursor, counted up to a cap, and the age of the oldest: a
    // stream held back by a slow writer or a failing destination shows up here
    // as growth, not as silence.
    let pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM (SELECT 1 FROM activity_logs
          WHERE tenant_id = $1 AND stream_xid IS NOT NULL AND (stream_xid, stream_seq) > ($2, $3)
          LIMIT $4) t",
    )
    .bind(tenant_id)
    .bind(xid)
    .bind(seq)
    .bind(PENDING_CAP + 1)
    .fetch_one(&state.pool)
    .await?;
    let oldest: Option<DateTime<Utc>> = sqlx::query_scalar(
        "SELECT created_at FROM activity_logs
          WHERE tenant_id = $1 AND stream_xid IS NOT NULL AND (stream_xid, stream_seq) > ($2, $3)
          ORDER BY stream_xid, stream_seq LIMIT 1",
    )
    .bind(tenant_id)
    .bind(xid)
    .bind(seq)
    .fetch_optional(&state.pool)
    .await?
    .flatten();
    let lag_seconds = oldest.map(|d| (Utc::now() - d).num_seconds().max(0));

    let url: String = row.try_get("url")?;
    Ok(json!({
        "configured": true,
        "url": url,
        "host": host_of(&url),
        "enabled": row.try_get::<bool, _>("enabled")?,
        "disabled_at": ts(&row, "disabled_at")?,
        "disabled_reason": row.try_get::<Option<String>, _>("disabled_reason")?,
        "batch_size": row.try_get::<i32, _>("batch_size")?,
        "cursor": format_cursor(xid, seq),
        "pending_records": pending.min(PENDING_CAP),
        "pending_capped": pending > PENDING_CAP,
        "lag_seconds": lag_seconds,
        "delivered_records": row.try_get::<i64, _>("delivered_records")?,
        "consecutive_failures": row.try_get::<i32, _>("consecutive_failures")?,
        "failure_days": row.try_get::<i32, _>("failure_days")?,
        "last_error": row.try_get::<Option<String>, _>("last_error")?,
        "last_status_code": row.try_get::<Option<i32>, _>("last_status_code")?,
        "last_attempt_at": ts(&row, "last_attempt_at")?,
        "last_success_at": ts(&row, "last_success_at")?,
        "next_attempt_at": ts(&row, "next_attempt_at")?,
        "test_pending": row.try_get::<Option<DateTime<Utc>>, _>("test_requested_at")?.is_some(),
        "secret_rotated_at": ts(&row, "secret_rotated_at")?,
        "created_at": ts(&row, "created_at")?,
        "updated_at": ts(&row, "updated_at")?,
    }))
}

/// The stream's position as it is right now: the snapshot's `xmin`, minus one,
/// with the last sequence value handed out so far (every row of a finished
/// transaction has a sequence at or below it). Everything at or below it is
/// "already happened";
/// everything written afterwards, including by a transaction that is open right
/// now, has a larger transaction id and is delivered. A new destination starts
/// here, so connecting it does not flood a SIEM with history (replay does that
/// on request).
async fn head_position(state: &AppState) -> Result<(i64, i64), ApiError> {
    let xmin: i64 = sqlx::query_scalar("SELECT pg_snapshot_xmin(pg_current_snapshot())::text::bigint")
        .fetch_one(&state.pool)
        .await?;
    let seq: i64 = sqlx::query_scalar("SELECT last_value FROM activity_logs_stream_seq")
        .fetch_one(&state.pool)
        .await?;
    Ok((xmin - 1, seq))
}

// -- Handlers -----------------------------------------------------------------

pub async fn get_config(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin_read(&state, &actors, &headers).await?;
    Ok(ok(present(&state, &user.tenant_id).await?))
}

struct PutBody {
    url: Option<String>,
    batch_size: Option<i64>,
}

fn validate_put(obj: &Map<String, Value>) -> Result<PutBody, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let url = str_field(&mut errs, obj, &p, "url", false, false,
        &StrRules { min_length: Some(1), max_length: Some(ssrf::MAX_URL_LENGTH), pattern: None });
    let mut batch_size = None;
    match obj.get("batch_size") {
        None | Some(Value::Null) => {}
        Some(v) => match v.as_i64() {
            Some(n) if (1..=MAX_BATCH_SIZE).contains(&n) => batch_size = Some(n),
            Some(n) => {
                let (typ, msg, ctx) = if n < 1 {
                    ("greater_than_equal", "Input should be greater than or equal to 1".to_string(), json!({"ge": 1}))
                } else {
                    ("less_than_equal", format!("Input should be less than or equal to {MAX_BATCH_SIZE}"),
                        json!({"le": MAX_BATCH_SIZE}))
                };
                errs.push(typ, &validation::loc(&p, "batch_size"), msg, v, Some(ctx));
            }
            None => errs.push("int_type", &validation::loc(&p, "batch_size"),
                "Input should be a valid integer".into(), v, None),
        },
    }
    errs.into_result()?;
    Ok(PutBody { url: if let Field::Value(u) = url { Some(u) } else { None }, batch_size })
}

pub async fn put_config(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = admin_write(&state, &actors, &headers).await?;
    let obj = validation::body_object(&body)?;
    let b = validate_put(&obj)?;
    let tid = &user.tenant_id;

    let existing = sqlx::query("SELECT url FROM audit_streams WHERE tenant_id = $1")
        .bind(tid)
        .fetch_optional(&state.pool)
        .await?;
    let mut shown_secret: Option<String> = None;
    let url = match (&b.url, &existing) {
        (Some(u), _) => Some(crate::pycompat::py_strip(u).to_string()),
        (None, Some(_)) => None,
        (None, None) => {
            let mut errs = Errors::default();
            errs.push("missing", &[json!("body"), json!("url")], "Field required".into(), &Value::Object(obj.clone()), None);
            return Err(errs.into_result().unwrap_err());
        }
    };
    if let Some(u) = &url {
        ssrf::check_destination(u, allow_private(&state), "audit_stream").await?;
    }

    if existing.is_none() {
        let url = url.clone().unwrap_or_default();
        let secret = new_secret()?;
        let (xid, seq) = head_position(&state).await?;
        // `ON CONFLICT DO NOTHING` + a re-read: two first PUTs racing leave ONE
        // row, and only the winner is shown a secret.
        let inserted = sqlx::query(
            "INSERT INTO audit_streams (tenant_id, url, secret, cursor_xid, cursor_seq, batch_size, created_by)
             VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (tenant_id) DO NOTHING",
        )
        .bind(tid)
        .bind(&url)
        .bind(&secret)
        .bind(xid)
        .bind(seq)
        .bind(b.batch_size.unwrap_or(DEFAULT_BATCH_SIZE) as i32)
        .bind(&user.user_id)
        .execute(&state.pool)
        .await?
        .rows_affected();
        if inserted == 1 {
            shown_secret = Some(secret);
        }
    }
    if shown_secret.is_none() {
        // An existing destination: change what was given. A new URL clears the
        // failure history of the old one (they say nothing about the new one)
        // and is tried at once; it does not turn a disabled stream back on.
        let url_changed = url.is_some();
        let res = sqlx::query(
            "UPDATE audit_streams
                SET url = COALESCE($2, url),
                    batch_size = COALESCE($3, batch_size),
                    consecutive_failures = CASE WHEN $4 THEN 0 ELSE consecutive_failures END,
                    failure_days = CASE WHEN $4 THEN 0 ELSE failure_days END,
                    last_failure_on = CASE WHEN $4 THEN NULL ELSE last_failure_on END,
                    last_error = CASE WHEN $4 THEN NULL ELSE last_error END,
                    next_attempt_at = CASE WHEN $4 THEN NOW() ELSE next_attempt_at END,
                    updated_at = NOW()
              WHERE tenant_id = $1",
        )
        .bind(tid)
        .bind(&url)
        .bind(b.batch_size.map(|n| n as i32))
        .bind(url_changed)
        .execute(&state.pool)
        .await?;
        if res.rows_affected() == 0 {
            return Err(not_configured());
        }
    }

    let host = url.as_deref().and_then(host_of);
    let note = Note {
        label: host.clone(),
        after: Some(json!({"host": host, "batch_size": b.batch_size, "created": shown_secret.is_some()})),
        ..Default::default()
    };
    audit::record(&state, &actors, "PUT", "/audit-stream", None, note, 200).await;
    let mut data = present(&state, tid).await?;
    if let (Some(secret), Value::Object(m)) = (shown_secret, &mut data) {
        m.insert("secret".into(), json!(secret));
    }
    Ok(ok(data))
}

pub async fn delete_config(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin_write(&state, &actors, &headers).await?;
    let tid = &user.tenant_id;
    let mut tx = state.pool.begin().await?;
    let removed = sqlx::query("DELETE FROM audit_streams WHERE tenant_id = $1 RETURNING url")
        .bind(tid)
        .fetch_optional(&mut *tx)
        .await?;
    let Some(removed) = removed else { return Err(not_configured()) };
    sqlx::query("DELETE FROM audit_stream_deliveries WHERE tenant_id = $1")
        .bind(tid)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    let host = host_of(&removed.try_get::<String, _>("url")?);
    let note = Note { label: host, ..Default::default() };
    audit::record(&state, &actors, "DELETE", "/audit-stream", None, note, 200).await;
    Ok(ok(json!({"deleted": true})))
}

pub async fn enable(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin_write(&state, &actors, &headers).await?;
    let res = sqlx::query(
        "UPDATE audit_streams
            SET enabled = TRUE, disabled_at = NULL, disabled_reason = NULL,
                consecutive_failures = 0, failure_days = 0, last_failure_on = NULL, last_error = NULL,
                next_attempt_at = NOW(), updated_at = NOW()
          WHERE tenant_id = $1",
    )
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    if res.rows_affected() == 0 {
        return Err(not_configured());
    }
    audit::record(&state, &actors, "POST", "/audit-stream/enable", None, Note::default(), 200).await;
    Ok(ok(present(&state, &user.tenant_id).await?))
}

pub async fn disable(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin_write(&state, &actors, &headers).await?;
    // Already off keeps its original reason and time (an auto-disable stays one).
    let res = sqlx::query(
        "UPDATE audit_streams
            SET enabled = FALSE,
                disabled_at = CASE WHEN enabled THEN NOW() ELSE disabled_at END,
                disabled_reason = CASE WHEN enabled THEN 'manual' ELSE disabled_reason END,
                updated_at = NOW()
          WHERE tenant_id = $1",
    )
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    if res.rows_affected() == 0 {
        return Err(not_configured());
    }
    audit::record(&state, &actors, "POST", "/audit-stream/disable", None, Note::default(), 200).await;
    Ok(ok(present(&state, &user.tenant_id).await?))
}

pub async fn rotate_secret(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin_write(&state, &actors, &headers).await?;
    let secret = new_secret()?;
    let res = sqlx::query("UPDATE audit_streams SET secret = $2, secret_rotated_at = NOW(), updated_at = NOW() WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .bind(&secret)
        .execute(&state.pool)
        .await?;
    if res.rows_affected() == 0 {
        return Err(not_configured());
    }
    audit::record(&state, &actors, "POST", "/audit-stream/rotate-secret", None, Note::default(), 200).await;
    Ok(ok(json!({"secret": secret})))
}

fn replay_invalid(reason: &str) -> ApiError {
    ApiError::app(
        "audit_stream_replay_invalid",
        "Give exactly one of `cursor` (as returned in a record) or `since` (an ISO date or time)",
        422,
        json!({"reason": reason}),
    )
}

pub async fn replay(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = admin_write(&state, &actors, &headers).await?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let p = [json!("body")];
    let cursor = str_field(&mut errs, &obj, &p, "cursor", false, true, &StrRules { min_length: None, max_length: Some(64), pattern: None });
    let since = str_field(&mut errs, &obj, &p, "since", false, true, &StrRules { min_length: None, max_length: Some(64), pattern: None });
    errs.into_result()?;
    let tid = &user.tenant_id;

    // The cursor to set: the position BEFORE the first row to replay.
    let target: Option<(i64, i64)> = match (&cursor, &since) {
        (Field::Value(c), Field::Absent | Field::Null) => Some(parse_cursor(c).ok_or_else(|| replay_invalid("cursor_malformed"))?),
        (Field::Absent | Field::Null, Field::Value(s)) => {
            let at = parse_since(s).ok_or_else(|| replay_invalid("since_malformed"))?;
            let first = sqlx::query(
                "SELECT stream_xid, stream_seq FROM activity_logs
                  WHERE tenant_id = $1 AND stream_xid IS NOT NULL AND created_at >= $2
                  ORDER BY stream_xid, stream_seq LIMIT 1",
            )
            .bind(tid)
            .bind(at)
            .fetch_optional(&state.pool)
            .await?;
            match first {
                // One step before it, so that row is the first one delivered.
                Some(r) => Some((r.try_get::<i64, _>("stream_xid")?, r.try_get::<i64, _>("stream_seq")? - 1)),
                None => None,
            }
        }
        _ => return Err(replay_invalid("exactly_one")),
    };

    let current = sqlx::query("SELECT cursor_xid, cursor_seq FROM audit_streams WHERE tenant_id = $1")
        .bind(tid)
        .fetch_optional(&state.pool)
        .await?;
    let Some(current) = current else { return Err(not_configured()) };
    let Some((xid, seq)) = target else {
        // `since` is after every row: there is nothing to replay.
        let (cx, cs): (i64, i64) = (current.try_get("cursor_xid")?, current.try_get("cursor_seq")?);
        return Ok(ok(json!({"cursor": format_cursor(cx, cs), "moved": false})));
    };
    // Backwards only, and atomically: a cursor ahead of the current one would
    // SKIP rows the destination never got, which is the one thing this stream
    // never does.
    let moved = sqlx::query(
        "UPDATE audit_streams SET cursor_xid = $2, cursor_seq = $3, next_attempt_at = NOW(), updated_at = NOW()
          WHERE tenant_id = $1 AND (cursor_xid, cursor_seq) >= ($2, $3)",
    )
    .bind(tid)
    .bind(xid)
    .bind(seq)
    .execute(&state.pool)
    .await?
    .rows_affected();
    if moved == 0 {
        return Err(ApiError::app(
            "audit_stream_cursor_ahead",
            "That cursor is ahead of the stream's position; a replay can only go back",
            422,
            json!({"cursor": format_cursor(xid, seq)}),
        ));
    }
    let note = Note { after: Some(json!({"cursor": format_cursor(xid, seq)})), ..Default::default() };
    audit::record(&state, &actors, "POST", "/audit-stream/replay", None, note, 200).await;
    Ok(ok(json!({"cursor": format_cursor(xid, seq), "moved": true})))
}

pub async fn test_delivery(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin_write(&state, &actors, &headers).await?;
    // Due now, so the loop picks it up within its poll, even in a retry backoff.
    let res = sqlx::query(
        "UPDATE audit_streams
            SET test_requested_at = NOW(), next_attempt_at = LEAST(next_attempt_at, NOW()), updated_at = NOW()
          WHERE tenant_id = $1",
    )
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    if res.rows_affected() == 0 {
        return Err(not_configured());
    }
    audit::record(&state, &actors, "POST", "/audit-stream/test", None, Note::default(), 200).await;
    Ok(ok(json!({"queued": true})))
}

fn log_status(s: &str) -> bool {
    matches!(s, "delivered" | "failed" | "superseded")
}

fn log_kind(s: &str) -> bool {
    matches!(s, "batch" | "test")
}

pub async fn deliveries(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    let user = admin_read(&state, &actors, &headers).await?;
    let mut errs = Errors::default();
    let status = query::opt_pattern(&mut errs, &q, "status", "^(delivered|failed|superseded)$", log_status);
    let kind = query::opt_pattern(&mut errs, &q, "kind", "^(batch|test)$", log_kind);
    let limit = query::int_param(&mut errs, &q, "limit", 50, Some(1), Some(MAX_LOG_PAGE));
    errs.into_result()?;
    let Some(PyInt::Small(limit)) = limit else { return Err(ApiError::internal()) };
    let status = status.filter(|s| !s.is_empty());
    let kind = kind.filter(|s| !s.is_empty());

    let rows = sqlx::query(
        "SELECT id, kind, status, records, bytes, first_cursor, last_cursor, status_code, error,
                duration_ms, created_at
           FROM audit_stream_deliveries
          WHERE tenant_id = $1 AND ($2::text IS NULL OR status = $2) AND ($3::text IS NULL OR kind = $3)
          ORDER BY created_at DESC, id LIMIT $4",
    )
    .bind(&user.tenant_id)
    .bind(status)
    .bind(kind)
    .bind(limit)
    .fetch_all(&state.pool)
    .await?;
    let mut out = Vec::new();
    for r in &rows {
        out.push(json!({
            "id": r.try_get::<String, _>("id")?,
            "kind": r.try_get::<String, _>("kind")?,
            "status": r.try_get::<String, _>("status")?,
            "records": r.try_get::<i32, _>("records")?,
            "bytes": r.try_get::<i32, _>("bytes")?,
            "first_cursor": r.try_get::<Option<String>, _>("first_cursor")?,
            "last_cursor": r.try_get::<Option<String>, _>("last_cursor")?,
            "status_code": r.try_get::<Option<i32>, _>("status_code")?,
            "error": r.try_get::<Option<String>, _>("error")?,
            "duration_ms": r.try_get::<Option<i32>, _>("duration_ms")?,
            "created_at": ts(r, "created_at")?,
        }));
    }
    Ok(ok(Value::Array(out)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cursor_text_round_trips_and_refuses_everything_else() {
        assert_eq!(parse_cursor(&format_cursor(123, 456)), Some((123, 456)));
        assert_eq!(parse_cursor("0:0"), Some((0, 0)));
        for bad in ["", "1", "1:", ":1", "a:1", "1:b", "-1:2", "1:-2", "1:2:3", "1.5:2", " 1:2", "1:2 ",
                    "\u{661}:2", "99999999999999999999:1", "9223372036854775807:1"] {
            assert_eq!(parse_cursor(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn since_accepts_a_date_or_an_rfc3339_instant() {
        assert_eq!(parse_since("2026-10-01").unwrap().to_rfc3339(), "2026-10-01T00:00:00+00:00");
        assert_eq!(parse_since("2026-10-01T12:30:00Z").unwrap().to_rfc3339(), "2026-10-01T12:30:00+00:00");
        assert_eq!(parse_since("2026-10-01T12:30:00-05:00").unwrap().to_rfc3339(), "2026-10-01T17:30:00+00:00");
        for bad in ["", "yesterday", "2026-13-01", "2026-10-01 12:00", "1696118400"] {
            assert!(parse_since(bad).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn put_body_bounds_batch_size_and_wants_a_string_url() {
        let ok = |v: Value| validate_put(v.as_object().unwrap());
        assert!(ok(json!({"url": "https://a.example.com/x"})).is_ok());
        assert_eq!(ok(json!({"batch_size": 1000})).unwrap().batch_size, Some(1000));
        assert!(ok(json!({})).is_ok());
        for bad in [json!({"batch_size": 0}), json!({"batch_size": 1001}), json!({"batch_size": "5"}),
                    json!({"batch_size": 1.5}), json!({"url": 5}), json!({"url": ""})] {
            assert!(ok(bad.clone()).is_err(), "{bad}");
        }
        let e = ok(json!({"batch_size": 0})).err().unwrap();
        assert_eq!(e.body["error_code"], "validation_error");
        assert_eq!(e.body["detail"][0]["type"], "greater_than_equal");
    }

    #[test]
    fn every_catalogued_route_of_this_file_exists() {
        use crate::audit::catalog;
        for (m, t) in [("PUT", "/audit-stream"), ("DELETE", "/audit-stream"), ("POST", "/audit-stream/enable"),
                       ("POST", "/audit-stream/disable"), ("POST", "/audit-stream/rotate-secret"),
                       ("POST", "/audit-stream/replay"), ("POST", "/audit-stream/test")] {
            assert!(catalog::route(m, t).is_some(), "{m} {t} is not in the audit catalogue");
        }
    }
}
