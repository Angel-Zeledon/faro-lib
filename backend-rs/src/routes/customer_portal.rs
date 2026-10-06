//! The customer portal: a private, read-only link where a corporate customer
//! sees ONLY their own commitments. New in Rust: there is no Python twin, so
//! these routes have no failover (docs/rust-migration.md, "Customer portal").
//!
//! It is the mirror of the supplier confirmation link
//! (`backend/api/v1/supplier_portal.py`, `inventory/po_confirmation_core.py`)
//! and keeps its security model:
//!
//! * the token is 256 random bits and only its SHA-256 is stored;
//! * every bad link (malformed, unknown, revoked, expired) answers the one
//!   identical 404, so links cannot be probed;
//! * rate limits per address AND per link, counted for every request whether
//!   or not the link is valid (the 429 reveals nothing either);
//! * the body is capped before it is parsed and every field is bounded;
//! * responses carry `Cache-Control: no-store`, `Referrer-Policy: no-referrer`
//!   and `X-Robots-Tag: noindex`: the URL is the secret.
//!
//! Tenant routes (`/customer-portal/...`, a person's JWT, never an API key):
//! list the customers that have commitments, create / list / read / revoke /
//! reopen a link, switch `share_dates`, and set the promised date the tenant
//! chooses to show. Public routes (`/customer-portal/public/{token}`): read the
//! page, and answer a commitment with "received" or "the date does not work".
//!
//! What the customer can see is a whitelist built in [`public_commitment`]:
//! SKU, its display name, quantity, requested date, status and, only when the
//! link has `share_dates` and the tenant set one, a promised date. Never stock,
//! cost, probability, notes, warehouses, other customers, verdicts or suppliers.
//! A customer's answer is an append-only event; it never edits a commitment.

use std::collections::HashMap;

use axum::body::Body;
use axum::extract::rejection::PathRejection;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Extension, Json, Router};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use chrono::{DateTime, Datelike, Duration, NaiveDate, Utc};
use hmac::{Hmac, Mac};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use sqlx::postgres::PgRow;
use sqlx::{PgPool, Row};

use crate::activity::{record_event, record_event_with_reason, Event};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, isoformat_utc, py_strip};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, bool_field, str_field, Body as ReqBody, Errors, Field, StrRules};

/// No key may manage portal links: handing a customer a private window onto
/// the order book is a person's decision, recorded under their name.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'customer-portal': a private link shows a customer their own commitments; creating or changing one is a person's decision, recorded under their name",
    ),
    is_mcp: false,
};

// ── Token and hashing (the supplier link's rules, `po_confirmation_core.py`) ─

const TOKEN_BYTES: usize = 32;
/// `token_urlsafe(32)` is 43 characters; the upper bound only refuses a
/// megabyte of garbage before it is hashed.
const TOKEN_MIN_LEN: usize = 43;
const TOKEN_MAX_LEN: usize = 128;

pub fn token_is_wellformed(token: &str) -> bool {
    (TOKEN_MIN_LEN..=TOKEN_MAX_LEN).contains(&token.len())
        && token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

pub fn hash_token(token: &str) -> String {
    hex::encode(Sha256::digest(token.as_bytes()))
}

/// Constant-time equality of two byte strings of any length (the length is
/// not secret: both are 64-character digests).
pub fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn new_token() -> Result<String, ApiError> {
    let mut buf = [0u8; TOKEN_BYTES];
    getrandom::getrandom(&mut buf).map_err(|e| {
        tracing::error!(error = %e, "OS randomness unavailable");
        ApiError::internal()
    })?;
    Ok(URL_SAFE_NO_PAD.encode(buf))
}

/// `hash_client_value`: a keyed hash of the address, worth comparing and not
/// worth keeping.
pub fn hash_client_value(value: &str, secret: &str) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("hmac accepts any key length");
    mac.update(value.as_bytes());
    hex::encode(mac.finalize().into_bytes())[..32].to_string()
}

/// `_client_address`: the first forwarded address (behind Caddy and the Next
/// proxy the socket peer is the proxy), else "unknown". A client can forge the
/// header, which is why the per-link ceiling is the real limit and this one is
/// a speed bump.
pub fn client_address(headers: &HeaderMap) -> String {
    let forwarded = headers.get("x-forwarded-for").and_then(|v| v.to_str().ok()).unwrap_or("");
    let first = py_strip(forwarded.split(',').next().unwrap_or(""));
    if first.is_empty() { "unknown".into() } else { first.chars().take(64).collect() }
}

// ── Rate limits (`supplier_portal.py`; same table as `auth._check_rate`) ─────

/// (max requests, window seconds).
const READ_PER_ADDRESS: (i64, f64) = (120, 600.0);
const READ_PER_LINK: (i64, f64) = (60, 600.0);
const WRITE_PER_ADDRESS: (i64, f64) = (20, 600.0);
const WRITE_PER_LINK: (i64, f64) = (10, 600.0);

#[derive(Clone, Copy, PartialEq, Debug)]
enum Kind {
    Read,
    Write,
}

impl Kind {
    fn name(self) -> &'static str {
        match self {
            Kind::Read => "read",
            Kind::Write => "write",
        }
    }
}

/// The rate keys one request is counted under. The link key is a digest, never
/// the token: rate rows must not become a second copy of the credential.
fn rate_keys(kind: Kind, address: &str, token: &str) -> Vec<(String, (i64, f64))> {
    let (per_address, per_link) = match kind {
        Kind::Read => (READ_PER_ADDRESS, READ_PER_LINK),
        Kind::Write => (WRITE_PER_ADDRESS, WRITE_PER_LINK),
    };
    let mut keys = vec![(format!("customer_portal:{}:ip:{address}", kind.name()), per_address)];
    if token_is_wellformed(token) {
        keys.push((format!("customer_portal:{}:link:{}", kind.name(), &hash_token(token)[..24]), per_link));
    }
    keys
}

/// `auth._check_rate`. Skipped in testing mode. A database failure lets the
/// request through (the Python code falls back to a process-local window; with
/// the database down nothing below this can answer anyway).
async fn check_rate(state: &AppState, key: &str, max_attempts: i64, window_secs: f64) -> Result<(), ApiError> {
    if state.settings.testing_mode {
        return Ok(());
    }
    let pool = &state.pool;
    let allowed: Result<bool, sqlx::Error> = async {
        sqlx::query("DELETE FROM auth_rate_events WHERE key = $1 AND created_at < NOW() - make_interval(secs => $2)")
            .bind(key)
            .bind(window_secs)
            .execute(pool)
            .await?;
        let (n,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM auth_rate_events WHERE key = $1")
            .bind(key)
            .fetch_one(pool)
            .await?;
        if n < max_attempts {
            sqlx::query("INSERT INTO auth_rate_events (key) VALUES ($1)").bind(key).execute(pool).await?;
            return Ok(true);
        }
        Ok(false)
    }
    .await;
    match allowed {
        Ok(false) => Err(ApiError::app(
            "too_many_attempts", "Too many attempts. Please try again later.", 429, json!({}),
        )),
        Ok(true) => Ok(()),
        Err(e) => {
            tracing::warn!(error = %e, "customer portal rate limiter unavailable; letting the request through");
            Ok(())
        }
    }
}

async fn limit(state: &AppState, headers: &HeaderMap, token: &str, kind: Kind) -> Result<(), ApiError> {
    for (key, (max, window)) in rate_keys(kind, &client_address(headers), token) {
        check_rate(state, &key, max, window).await?;
    }
    Ok(())
}

// ── What the customer may see ────────────────────────────────────────────────

/// The only keys a commitment has on the public page. A test pins this list:
/// adding a field to the page means editing it on purpose.
#[cfg_attr(not(test), allow(dead_code))] // read by the tests that pin the page
pub const PUBLIC_COMMITMENT_KEYS: [&str; 8] =
    ["id", "sku", "description", "quantity", "requested_date", "status", "promised_date", "my_response"];
/// The only top-level keys of the public page.
#[cfg_attr(not(test), allow(dead_code))]
pub const PUBLIC_PAGE_KEYS: [&str; 6] = ["company", "customer", "language", "share_dates", "expires_at", "commitments"];

/// One commitment row as the customer sees it. `promised_date` appears only
/// when the link shares dates AND the tenant set one.
fn public_commitment(c: &Commitment, share_dates: bool, my_response: Option<&str>) -> Value {
    let mut m = Map::new();
    m.insert("id".into(), json!(c.id));
    m.insert("sku".into(), json!(c.sku));
    m.insert("description".into(), json!(c.description));
    m.insert("quantity".into(), json!(c.quantity));
    m.insert("requested_date".into(), json!(isoformat_date(&c.delivery_date)));
    m.insert("status".into(), json!(c.status));
    if share_dates {
        if let Some(d) = c.promised_date {
            m.insert("promised_date".into(), json!(isoformat_date(&d)));
        }
    }
    m.insert("my_response".into(), my_response.map(|r| json!(r)).unwrap_or(Value::Null));
    Value::Object(m)
}

#[derive(Debug, Clone)]
struct Commitment {
    id: String,
    sku: String,
    description: String,
    quantity: f64,
    delivery_date: NaiveDate,
    status: String,
    promised_date: Option<NaiveDate>,
}

const MAX_COMMITMENTS: i64 = 500;

/// A customer's commitments: matched on the lower-cased, trimmed name (the
/// database folds both sides), withdrawn contract rows left out. The display
/// name comes from the stock table (one row per warehouse, so ONE name is
/// picked, never a join that would repeat the commitment); no other column of
/// it is read.
async fn commitments_of(pool: &PgPool, tenant_id: &str, customer_key: &str) -> Result<Vec<Commitment>, sqlx::Error> {
    let rows = sqlx::query(
        "SELECT c.id, c.sku, COALESCE(s.name, c.sku) AS description,
                c.quantity, c.delivery_date, c.status, p.promised_date
           FROM committed_demand c
           LEFT JOIN LATERAL (
                SELECT btrim(s.display_name) AS name FROM inventory_stock s
                 WHERE s.tenant_id = c.tenant_id AND s.sku = c.sku AND btrim(s.display_name) <> ''
                 ORDER BY s.warehouse LIMIT 1) s ON TRUE
           LEFT JOIN customer_portal_promised_dates p ON p.commitment_id = c.id
          WHERE c.tenant_id = $1 AND lower(btrim(c.customer)) = $2 AND c.contract_withdrawn_at IS NULL
          ORDER BY c.delivery_date, c.sku, c.id
          LIMIT $3",
    )
    .bind(tenant_id)
    .bind(customer_key)
    .bind(MAX_COMMITMENTS)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(|r| {
            Ok(Commitment {
                id: r.try_get("id")?,
                sku: r.try_get("sku")?,
                description: r.try_get("description")?,
                quantity: r.try_get("quantity")?,
                delivery_date: r.try_get("delivery_date")?,
                status: r.try_get("status")?,
                promised_date: r.try_get("promised_date")?,
            })
        })
        .collect()
}

/// The latest response of one link per commitment.
async fn latest_responses(pool: &PgPool, link_id: &str) -> Result<HashMap<String, (String, Option<String>, DateTime<Utc>)>, sqlx::Error> {
    let rows = sqlx::query(
        "SELECT DISTINCT ON (commitment_id) commitment_id, response, comment, created_at
           FROM customer_portal_events WHERE link_id = $1
          ORDER BY commitment_id, created_at DESC, id DESC",
    )
    .bind(link_id)
    .fetch_all(pool)
    .await?;
    let mut out = HashMap::new();
    for r in rows {
        out.insert(r.try_get("commitment_id")?, (r.try_get("response")?, r.try_get("comment")?, r.try_get("created_at")?));
    }
    Ok(out)
}

// ── Public routes ────────────────────────────────────────────────────────────

const MAX_BODY_BYTES: usize = 16 * 1024;
const MAX_COMMENT_CHARS: usize = 500;
/// Answers one link may file about one commitment before it is refused.
const MAX_EVENTS_PER_COMMITMENT: i64 = 20;

/// The one answer for every bad link.
fn unavailable() -> ApiError {
    ApiError::app("customer_portal_unavailable", "This link is not valid", 404, json!({}))
}

fn hardened(mut response: Response) -> Response {
    let h = response.headers_mut();
    h.insert(HeaderName::from_static("cache-control"), HeaderValue::from_static("no-store"));
    h.insert(HeaderName::from_static("referrer-policy"), HeaderValue::from_static("no-referrer"));
    h.insert(HeaderName::from_static("x-robots-tag"), HeaderValue::from_static("noindex, nofollow"));
    response
}

fn finish(result: Result<(StatusCode, Value), ApiError>) -> Response {
    hardened(match result {
        Ok((status, data)) => (status, ok(data)).into_response(),
        Err(e) => e.into_response(),
    })
}

struct PortalLink {
    id: String,
    tenant_id: String,
    customer: String,
    customer_key: String,
    language: String,
    share_dates: bool,
    expires_at: DateTime<Utc>,
    created_by: String,
}

/// Look a presented token up by the hash of it, then compare in constant time.
/// Malformed, unknown, revoked and expired are all `None`. A malformed token
/// takes the same path (it is hashed and looked up, and simply matches
/// nothing); an over-long one is replaced by a fixed string so nobody can make
/// the server hash megabytes.
async fn resolve(pool: &PgPool, presented: &str) -> Result<Option<PortalLink>, sqlx::Error> {
    let wellformed = token_is_wellformed(presented);
    let hashed = hash_token(if presented.len() <= TOKEN_MAX_LEN { presented } else { "overlong" });
    let row = sqlx::query(
        "SELECT id, tenant_id, customer, customer_key, token_hash, language, share_dates, expires_at, created_by,
                (revoked_at IS NULL AND expires_at > NOW()) AS usable
           FROM customer_portal_links WHERE token_hash = $1",
    )
    .bind(&hashed)
    .fetch_optional(pool)
    .await?;
    let Some(row) = row else { return Ok(None) };
    let stored: String = row.try_get("token_hash")?;
    let usable: bool = row.try_get("usable")?;
    if !wellformed || !usable || !constant_time_eq(stored.as_bytes(), hashed.as_bytes()) {
        return Ok(None);
    }
    Ok(Some(PortalLink {
        id: row.try_get("id")?,
        tenant_id: row.try_get("tenant_id")?,
        customer: row.try_get("customer")?,
        customer_key: row.try_get("customer_key")?,
        language: row.try_get("language")?,
        share_dates: row.try_get("share_dates")?,
        expires_at: row.try_get("expires_at")?,
        created_by: row.try_get("created_by")?,
    }))
}

fn token_of(path: Result<Path<String>, PathRejection>) -> String {
    path.map(|Path(t)| t).unwrap_or_default()
}

pub async fn public_view(
    State(state): State<AppState>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
) -> Response {
    let token = token_of(path);
    finish(public_view_inner(&state, &headers, &token).await.map(|v| (StatusCode::OK, v)))
}

async fn public_view_inner(state: &AppState, headers: &HeaderMap, token: &str) -> Result<Value, ApiError> {
    limit(state, headers, token, Kind::Read).await?;
    let link = resolve(&state.pool, token).await?.ok_or_else(unavailable)?;
    // Best effort and at most once a minute: "when did they last look".
    if let Err(e) = sqlx::query(
        "UPDATE customer_portal_links SET last_viewed_at = NOW()
          WHERE id = $1 AND (last_viewed_at IS NULL OR last_viewed_at < NOW() - interval '1 minute')",
    )
    .bind(&link.id)
    .execute(&state.pool)
    .await
    {
        tracing::warn!(error = %e, "customer portal: last_viewed_at not updated");
    }
    let company: Option<(String,)> = sqlx::query_as("SELECT name FROM tenants WHERE id = $1")
        .bind(&link.tenant_id)
        .fetch_optional(&state.pool)
        .await?;
    let commitments = commitments_of(&state.pool, &link.tenant_id, &link.customer_key).await?;
    let responses = latest_responses(&state.pool, &link.id).await?;
    let items: Vec<Value> = commitments
        .iter()
        .map(|c| public_commitment(c, link.share_dates, responses.get(&c.id).map(|r| r.0.as_str())))
        .collect();
    Ok(json!({
        "company": company.map(|c| c.0).unwrap_or_default(),
        "customer": link.customer,
        "language": link.language,
        "share_dates": link.share_dates,
        "expires_at": isoformat_utc(&link.expires_at),
        "commitments": items,
    }))
}

#[derive(Debug, PartialEq)]
pub struct Ack {
    pub commitment_id: String,
    pub response: String,
    pub comment: Option<String>,
}

fn invalid_answer(reason: &str) -> ApiError {
    ApiError::app("customer_portal_invalid_response", "The answer could not be accepted", 422,
        json!({"reason": reason}))
}

fn too_large() -> ApiError {
    ApiError::app("customer_portal_body_too_large", "The answer is too large", 413,
        json!({"max_kb": MAX_BODY_BYTES / 1024}))
}

/// Strict validation of the customer's answer: a JSON object with exactly
/// `commitment_id`, `response` and an optional `comment`.
pub fn parse_ack(raw: &[u8]) -> Result<Ack, ApiError> {
    if raw.len() > MAX_BODY_BYTES {
        return Err(too_large());
    }
    let Ok(Value::Object(obj)) = serde_json::from_slice::<Value>(raw) else {
        return Err(invalid_answer("body_invalid"));
    };
    if obj.keys().any(|k| !matches!(k.as_str(), "commitment_id" | "response" | "comment")) {
        return Err(invalid_answer("unknown_field"));
    }
    let commitment_id = match obj.get("commitment_id") {
        Some(Value::String(s))
            if (1..=64).contains(&s.len())
                && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') => s.clone(),
        _ => return Err(invalid_answer("commitment_invalid")),
    };
    let response = match obj.get("response") {
        Some(Value::String(s)) if s == "received" || s == "date_objection" => s.clone(),
        _ => return Err(invalid_answer("response_invalid")),
    };
    let comment = match obj.get("comment") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => {
            let t = py_strip(s);
            if t.chars().any(|c| c.is_control() && c != '\n' && c != '\t') {
                return Err(invalid_answer("comment_invalid"));
            }
            if t.chars().count() > MAX_COMMENT_CHARS {
                return Err(invalid_answer("comment_too_long"));
            }
            if t.is_empty() { None } else { Some(t.to_string()) }
        }
        Some(_) => return Err(invalid_answer("comment_invalid")),
    };
    if response == "date_objection" && comment.is_none() {
        return Err(invalid_answer("comment_required"));
    }
    Ok(Ack { commitment_id, response, comment })
}

async fn read_capped(headers: &HeaderMap, body: Body) -> Result<Vec<u8>, ApiError> {
    let declared = headers
        .get(axum::http::header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<usize>().ok());
    if declared.is_some_and(|n| n > MAX_BODY_BYTES) {
        return Err(too_large());
    }
    axum::body::to_bytes(body, MAX_BODY_BYTES).await.map(|b| b.to_vec()).map_err(|_| too_large())
}

pub async fn public_respond(
    State(state): State<AppState>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let token = token_of(path);
    finish(public_respond_inner(&state, &headers, &token, body).await)
}

async fn public_respond_inner(
    state: &AppState,
    headers: &HeaderMap,
    token: &str,
    body: Body,
) -> Result<(StatusCode, Value), ApiError> {
    limit(state, headers, token, Kind::Write).await?;
    // The link first: an unknown token is the same 404 whatever the body is.
    let link = resolve(&state.pool, token).await?.ok_or_else(unavailable)?;
    let raw = read_capped(headers, body).await?;
    let ack = parse_ack(&raw)?;
    let pool = &state.pool;

    // The commitment must be one of THIS customer's, and open for the answer.
    let row = sqlx::query(
        "SELECT c.sku, c.status, c.delivery_date FROM committed_demand c
          WHERE c.id = $1 AND c.tenant_id = $2 AND lower(btrim(c.customer)) = $3
            AND c.contract_withdrawn_at IS NULL",
    )
    .bind(&ack.commitment_id)
    .bind(&link.tenant_id)
    .bind(&link.customer_key)
    .fetch_optional(pool)
    .await?;
    let Some(row) = row else {
        return Err(ApiError::app("customer_portal_commitment_not_found", "That commitment is not on this page", 404,
            json!({})));
    };
    let sku: String = row.try_get("sku")?;
    let status: String = row.try_get("status")?;
    let delivery: NaiveDate = row.try_get("delivery_date")?;
    if status == "cancelled" || (ack.response == "date_objection" && status != "open") {
        return Err(ApiError::app("customer_portal_commitment_closed", "That commitment is closed", 409,
            json!({"status": status})));
    }

    let (seen, received_before): (i64, i64) = sqlx::query_as(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE response = 'received')
           FROM customer_portal_events WHERE link_id = $1 AND commitment_id = $2",
    )
    .bind(&link.id)
    .bind(&ack.commitment_id)
    .fetch_one(pool)
    .await?;
    if ack.response == "received" && received_before > 0 {
        return Ok((StatusCode::OK, json!({"recorded": true, "duplicate": true, "response": ack.response})));
    }
    if seen >= MAX_EVENTS_PER_COMMITMENT {
        return Err(ApiError::app("too_many_attempts", "Too many attempts. Please try again later.", 429, json!({})));
    }
    let ip_hash = hash_client_value(&client_address(headers), &state.settings.secret_key);
    sqlx::query(
        "INSERT INTO customer_portal_events (tenant_id, link_id, commitment_id, response, comment, ip_hash)
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(&link.tenant_id)
    .bind(&link.id)
    .bind(&ack.commitment_id)
    .bind(&ack.response)
    .bind(&ack.comment)
    .bind(&ip_hash)
    .execute(pool)
    .await?;

    // The bell: recorded under the person who made the link. The row above is
    // already stored; a lost activity row is logged, never reported as a failure.
    if ack.response == "date_objection" {
        record_event_with_reason(pool, &link.tenant_id, &link.created_by, Event::CustomerPortalDateObjected,
            Some(&link.id),
            details(&[("customer", json!(link.customer)), ("sku", json!(sku)),
                      ("delivery_date", json!(isoformat_date(&delivery)))]),
            Some("customer_date_objection")).await;
    } else {
        record_event(pool, &link.tenant_id, &link.created_by, Event::CustomerPortalReceived, Some(&link.id),
            details(&[("customer", json!(link.customer)), ("sku", json!(sku))])).await;
    }
    Ok((StatusCode::CREATED, json!({"recorded": true, "duplicate": false, "response": ack.response})))
}

fn details(pairs: &[(&str, Value)]) -> Map<String, Value> {
    pairs.iter().map(|(k, v)| ((*k).to_string(), v.clone())).collect()
}

// ── Tenant routes ────────────────────────────────────────────────────────────

const MAX_CUSTOMER_LENGTH: usize = 200;
const DEFAULT_EXPIRES_DAYS: i64 = 90;
const MAX_EXPIRES_DAYS: i64 = 365;
const REOPEN_DAYS: i64 = 30;
/// A tenant-wide safety cap on stored links, not a plan gate.
const MAX_LINKS_PER_TENANT: i64 = 500;

fn link_not_found() -> ApiError {
    ApiError::app("customer_portal_link_not_found", "Customer portal link not found", 404, json!({}))
}

/// FastAPI's order: body decode, then the person, role and trial guards.
/// Portal routes are company-wide: a user limited to some warehouses cannot
/// open every warehouse's orders to a customer, so they are refused.
async fn manager(state: &AppState, actors: &RequestActors, headers: &HeaderMap, write: bool)
    -> Result<CurrentUser, ApiError>
{
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    if write {
        auth::require_analyst_or_above(state, &user).await?;
    }
    wscope::require_company_wide(&state.pool, &user).await?;
    Ok(user)
}

fn bad_field(errs: &mut Errors, name: &str, typ: &str, msg: &str, input: &Value, ctx: Option<Value>) {
    errs.push(typ, &validation::loc(&[Value::String("body".into())], name), msg.into(), input, ctx);
}

pub fn today() -> NaiveDate {
    Utc::now().date_naive()
}

/// The window a promised date may sit in (a typo guard like the commitment's own).
pub fn check_promised_date(d: NaiveDate, today: NaiveDate) -> Result<(), ApiError> {
    let earliest = NaiveDate::from_ymd_opt(2000, 1, 1).expect("valid date");
    let latest = today.with_year(today.year() + 10).unwrap_or(today + Duration::days(3650));
    if d < earliest || d > latest {
        return Err(ApiError::app("customer_portal_date_invalid", "That promised date is out of range", 422,
            json!({"promised_date": isoformat_date(&d)})));
    }
    Ok(())
}

const LINK_SELECT: &str = "SELECT l.id, l.customer, l.language, l.share_dates, l.expires_at, l.revoked_at,
        l.created_by, l.created_at, l.last_viewed_at, l.reopened_at,
        CASE WHEN l.revoked_at IS NOT NULL THEN 'revoked'
             WHEN l.expires_at <= NOW() THEN 'expired' ELSE 'active' END AS status,
        (SELECT COUNT(*) FROM committed_demand c WHERE c.tenant_id = l.tenant_id
            AND lower(btrim(c.customer)) = l.customer_key AND c.contract_withdrawn_at IS NULL) AS commitments,
        (SELECT COUNT(*) FROM customer_portal_events e WHERE e.link_id = l.id AND e.response = 'received') AS received,
        (SELECT COUNT(*) FROM customer_portal_events e WHERE e.link_id = l.id AND e.response = 'date_objection') AS objections
   FROM customer_portal_links l";

fn ts(row: &PgRow, col: &str) -> Result<Value, sqlx::Error> {
    let v: Option<DateTime<Utc>> = row.try_get(col)?;
    Ok(v.map(|d| json!(isoformat_utc(&d))).unwrap_or(Value::Null))
}

/// What the tenant's screens show of a link. Never the token or its hash.
fn present_link(row: &PgRow) -> Result<Value, sqlx::Error> {
    Ok(json!({
        "id": row.try_get::<String, _>("id")?,
        "customer": row.try_get::<String, _>("customer")?,
        "language": row.try_get::<String, _>("language")?,
        "share_dates": row.try_get::<bool, _>("share_dates")?,
        "status": row.try_get::<String, _>("status")?,
        "expires_at": ts(row, "expires_at")?,
        "revoked_at": ts(row, "revoked_at")?,
        "created_by": row.try_get::<String, _>("created_by")?,
        "created_at": ts(row, "created_at")?,
        "last_viewed_at": ts(row, "last_viewed_at")?,
        "reopened_at": ts(row, "reopened_at")?,
        "commitments": row.try_get::<i64, _>("commitments")?,
        "received": row.try_get::<i64, _>("received")?,
        "objections": row.try_get::<i64, _>("objections")?,
    }))
}

async fn fetch_link(pool: &PgPool, tenant_id: &str, link_id: &str) -> Result<PgRow, ApiError> {
    sqlx::query(&format!("{LINK_SELECT} WHERE l.tenant_id = $1 AND l.id = $2"))
        .bind(tenant_id)
        .bind(link_id)
        .fetch_optional(pool)
        .await?
        .ok_or_else(link_not_found)
}

pub async fn customers(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = manager(&state, &actors, &headers, false).await?;
    // The names as typed (the most recent spelling), with how many commitments
    // each has; supply-contract customers with no commitment yet are listed too.
    let rows = sqlx::query(
        "SELECT key, (array_agg(name ORDER BY last_seen DESC))[1] AS customer,
                SUM(open_n)::bigint AS open_commitments, SUM(total_n)::bigint AS commitments
           FROM (
             SELECT lower(btrim(customer)) AS key, btrim(customer) AS name, created_at AS last_seen,
                    (status = 'open')::int AS open_n, 1 AS total_n
               FROM committed_demand
              WHERE tenant_id = $1 AND customer IS NOT NULL AND btrim(customer) <> ''
                AND contract_withdrawn_at IS NULL
             UNION ALL
             SELECT lower(btrim(customer)), btrim(customer), created_at, 0, 0
               FROM supply_contracts
              WHERE tenant_id = $1 AND superseded_by IS NULL AND btrim(customer) <> ''
           ) t GROUP BY key ORDER BY lower((array_agg(name ORDER BY last_seen DESC))[1])
          LIMIT 1000",
    )
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    let mut out = Vec::new();
    for r in &rows {
        out.push(json!({
            "customer": r.try_get::<String, _>("customer")?,
            "open_commitments": r.try_get::<i64, _>("open_commitments")?,
            "commitments": r.try_get::<i64, _>("commitments")?,
        }));
    }
    Ok(ok(Value::Array(out)))
}

pub async fn list_links(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = manager(&state, &actors, &headers, false).await?;
    let rows = sqlx::query(&format!("{LINK_SELECT} WHERE l.tenant_id = $1 ORDER BY l.created_at DESC LIMIT 500"))
        .bind(&user.tenant_id)
        .fetch_all(&state.pool)
        .await?;
    let mut out = Vec::new();
    for r in &rows {
        out.push(present_link(r)?);
    }
    Ok(ok(Value::Array(out)))
}

/// One int field `lo..=hi` the way pydantic words it; `None` when absent.
fn opt_int(errs: &mut Errors, obj: &Map<String, Value>, name: &str, lo: i64, hi: i64) -> Option<i64> {
    let v = obj.get(name)?;
    if v.is_null() {
        return None;
    }
    let as_int = v.as_i64().or_else(|| v.as_f64().filter(|f| f.fract() == 0.0 && f.abs() < 1e15).map(|f| f as i64));
    match as_int {
        None => {
            bad_field(errs, name, "int_type", "Input should be a valid integer", v, None);
            None
        }
        Some(n) if n < lo => {
            bad_field(errs, name, "greater_than_equal", &format!("Input should be greater than or equal to {lo}"),
                v, Some(json!({"ge": lo})));
            None
        }
        Some(n) if n > hi => {
            bad_field(errs, name, "less_than_equal", &format!("Input should be less than or equal to {hi}"),
                v, Some(json!({"le": hi})));
            None
        }
        Some(n) => Some(n),
    }
}

fn language_ok(s: &str) -> bool {
    s == "es" || s == "en"
}

pub async fn create_link(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: axum::body::Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: ReqBody = validation::read_body(content_type, &bytes)?;
    let user = manager(&state, &actors, &headers, true).await?;
    let obj = body_object(&body)?;

    let mut errs = Errors::default();
    let p = [Value::String("body".into())];
    let customer = str_field(&mut errs, &obj, &p, "customer", true, false,
        &StrRules { min_length: Some(1), max_length: Some(MAX_CUSTOMER_LENGTH), pattern: None });
    let language = str_field(&mut errs, &obj, &p, "language", false, false,
        &StrRules { min_length: None, max_length: None, pattern: Some(("^(es|en)$", language_ok)) });
    let share_dates = bool_field(&mut errs, &obj, &p, "share_dates", false);
    let expires_in_days = opt_int(&mut errs, &obj, "expires_in_days", 1, MAX_EXPIRES_DAYS);
    errs.into_result()?;
    let customer = match customer { Field::Value(c) => py_strip(&c).to_string(), _ => return Err(ApiError::internal()) };
    if customer.is_empty() {
        return Err(ApiError::app("customer_portal_customer_required", "Choose a customer", 422, json!({})));
    }
    let language = match language { Field::Value(l) => l, _ => "es".into() };
    let share_dates = match share_dates { Field::Value(b) => b, _ => false };
    let days = expires_in_days.unwrap_or(DEFAULT_EXPIRES_DAYS);

    let pool = &state.pool;
    let (known,): (bool,) = sqlx::query_as(
        "SELECT EXISTS (SELECT 1 FROM committed_demand WHERE tenant_id = $1
                          AND lower(btrim(customer)) = lower(btrim($2::text)) AND contract_withdrawn_at IS NULL)
             OR EXISTS (SELECT 1 FROM supply_contracts WHERE tenant_id = $1 AND superseded_by IS NULL
                          AND lower(btrim(customer)) = lower(btrim($2::text)))",
    )
    .bind(&user.tenant_id)
    .bind(&customer)
    .fetch_one(pool)
    .await?;
    if !known {
        return Err(ApiError::app("customer_portal_customer_unknown",
            "No commitment or contract names that customer", 422, json!({"customer": customer})));
    }
    let (existing,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM customer_portal_links WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_one(pool)
        .await?;
    if existing >= MAX_LINKS_PER_TENANT {
        return Err(ApiError::app("customer_portal_link_limit", "Too many portal links; revoke some first", 422,
            json!({"max": MAX_LINKS_PER_TENANT})));
    }

    let token = new_token()?;
    let (id,): (String,) = sqlx::query_as(
        "INSERT INTO customer_portal_links
             (tenant_id, customer, customer_key, token_hash, language, share_dates, expires_at, created_by)
         VALUES ($1, $2::text, lower(btrim($2::text)), $3, $4, $5, NOW() + make_interval(days => $6::int), $7)
         RETURNING id",
    )
    .bind(&user.tenant_id)
    .bind(&customer)
    .bind(hash_token(&token))
    .bind(&language)
    .bind(share_dates)
    .bind(days as i32)
    .bind(&user.user_id)
    .fetch_one(pool)
    .await?;
    record_event(pool, &user.tenant_id, &user.user_id, Event::CustomerPortalLinkCreated, Some(&id),
        details(&[("customer", json!(customer))])).await;
    let row = fetch_link(pool, &user.tenant_id, &id).await?;
    // The token is shown ONCE, here: only its hash is kept.
    let url = format!("{}/cliente/{}", state.settings.frontend_url.trim_end_matches('/'), token);
    Ok((StatusCode::CREATED, ok(json!({"link": present_link(&row)?, "token": token, "url": url}))))
}

pub async fn get_link(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(link_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = manager(&state, &actors, &headers, false).await?;
    let pool = &state.pool;
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    let (customer_key,): (String,) =
        sqlx::query_as("SELECT customer_key FROM customer_portal_links WHERE id = $1 AND tenant_id = $2")
            .bind(&link_id)
            .bind(&user.tenant_id)
            .fetch_one(pool)
            .await?;
    let commitments = commitments_of(pool, &user.tenant_id, &customer_key).await?;
    let latest = latest_responses(pool, &link_id).await?;
    let items: Vec<Value> = commitments
        .iter()
        .map(|c| {
            let last = latest.get(&c.id);
            json!({
                "id": c.id, "sku": c.sku, "description": c.description, "quantity": c.quantity,
                "requested_date": isoformat_date(&c.delivery_date), "status": c.status,
                "promised_date": c.promised_date.map(|d| isoformat_date(&d)),
                "response": last.map(|l| l.0.clone()),
                "response_comment": last.and_then(|l| l.1.clone()),
                "responded_at": last.map(|l| isoformat_utc(&l.2)),
            })
        })
        .collect();
    let events = sqlx::query(
        "SELECT id, commitment_id, response, comment, created_at FROM customer_portal_events
          WHERE link_id = $1 AND tenant_id = $2 ORDER BY created_at DESC, id DESC LIMIT 100",
    )
    .bind(&link_id)
    .bind(&user.tenant_id)
    .fetch_all(pool)
    .await?;
    let mut ev = Vec::new();
    for e in &events {
        ev.push(json!({
            "id": e.try_get::<String, _>("id")?,
            "commitment_id": e.try_get::<String, _>("commitment_id")?,
            "response": e.try_get::<String, _>("response")?,
            "comment": e.try_get::<Option<String>, _>("comment")?,
            "created_at": ts(e, "created_at")?,
        }));
    }
    Ok(ok(json!({"link": present_link(&row)?, "commitments": items, "events": ev})))
}

pub async fn update_link(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(link_id): Path<String>,
    headers: HeaderMap,
    bytes: axum::body::Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: ReqBody = validation::read_body(content_type, &bytes)?;
    let user = manager(&state, &actors, &headers, true).await?;
    let obj = body_object(&body)?;
    let mut errs = Errors::default();
    let share = bool_field(&mut errs, &obj, &[Value::String("body".into())], "share_dates", false);
    errs.into_result()?;
    let Field::Value(share) = share else {
        let mut e = Errors::default();
        e.push("missing", &validation::loc(&[Value::String("body".into())], "share_dates"), "Field required".into(),
            &Value::Object(obj.clone()), None);
        return e.into_result().map(|_| ok(Value::Null));
    };
    let pool = &state.pool;
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    let customer: String = row.try_get("customer")?;
    let changed: Option<(String,)> = sqlx::query_as(
        "UPDATE customer_portal_links SET share_dates = $1
          WHERE id = $2 AND tenant_id = $3 AND share_dates <> $1 RETURNING id",
    )
    .bind(share)
    .bind(&link_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    if changed.is_some() {
        record_event(pool, &user.tenant_id, &user.user_id, Event::CustomerPortalLinkUpdated, Some(&link_id),
            details(&[("customer", json!(customer))])).await;
    }
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    Ok(ok(json!({"link": present_link(&row)?, "changed": changed.is_some()})))
}

pub async fn revoke_link(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(link_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = manager(&state, &actors, &headers, true).await?;
    let pool = &state.pool;
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    let customer: String = row.try_get("customer")?;
    let changed: Option<(String,)> = sqlx::query_as(
        "UPDATE customer_portal_links SET revoked_at = NOW(), revoked_by = $1
          WHERE id = $2 AND tenant_id = $3 AND revoked_at IS NULL RETURNING id",
    )
    .bind(&user.user_id)
    .bind(&link_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    if changed.is_some() {
        record_event(pool, &user.tenant_id, &user.user_id, Event::CustomerPortalLinkRevoked, Some(&link_id),
            details(&[("customer", json!(customer))])).await;
    }
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    Ok(ok(json!({"link": present_link(&row)?, "changed": changed.is_some()})))
}

/// Bring a revoked or expired link back: same token, expiry pushed to at least
/// 30 days from now. Answers already given stay as history.
pub async fn reopen_link(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(link_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = manager(&state, &actors, &headers, true).await?;
    let pool = &state.pool;
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    let customer: String = row.try_get("customer")?;
    let changed: Option<(String,)> = sqlx::query_as(
        "UPDATE customer_portal_links
            SET revoked_at = NULL, revoked_by = NULL, reopened_at = NOW(), reopened_by = $1,
                expires_at = GREATEST(expires_at, NOW() + make_interval(days => $4::int))
          WHERE id = $2 AND tenant_id = $3 AND (revoked_at IS NOT NULL OR expires_at <= NOW()) RETURNING id",
    )
    .bind(&user.user_id)
    .bind(&link_id)
    .bind(&user.tenant_id)
    .bind(REOPEN_DAYS as i32)
    .fetch_optional(pool)
    .await?;
    if changed.is_some() {
        record_event(pool, &user.tenant_id, &user.user_id, Event::CustomerPortalLinkReopened, Some(&link_id),
            details(&[("customer", json!(customer))])).await;
    }
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    Ok(ok(json!({"link": present_link(&row)?, "changed": changed.is_some()})))
}

/// Set (or clear, with null) the promised date the tenant chooses to show for
/// one commitment of the link's customer. The customer sees it only when the
/// link has `share_dates`. It does not touch the commitment's own date.
pub async fn set_promised_date(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(link_id): Path<String>,
    headers: HeaderMap,
    bytes: axum::body::Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: ReqBody = validation::read_body(content_type, &bytes)?;
    let user = manager(&state, &actors, &headers, true).await?;
    let obj = body_object(&body)?;
    let mut errs = Errors::default();
    let p = [Value::String("body".into())];
    let commitment_id = str_field(&mut errs, &obj, &p, "commitment_id", true, false,
        &StrRules { min_length: Some(1), max_length: Some(64), pattern: None });
    let promised = str_field(&mut errs, &obj, &p, "promised_date", true, true,
        &StrRules { min_length: None, max_length: Some(32), pattern: None });
    errs.into_result()?;
    let Field::Value(commitment_id) = commitment_id else { return Err(ApiError::internal()) };
    let promised_date = match promised {
        Field::Value(s) => {
            let d = date_fromisoformat(py_strip(&s)).ok_or_else(|| ApiError::app("date_invalid_iso",
                "promised_date must be an ISO date (YYYY-MM-DD)", 422, json!({"field": "promised_date"})))?;
            check_promised_date(d, today())?;
            Some(d)
        }
        _ => None,
    };

    let pool = &state.pool;
    let row = fetch_link(pool, &user.tenant_id, &link_id).await?;
    let customer: String = row.try_get("customer")?;
    let found: Option<(String,)> = sqlx::query_as(
        "SELECT c.sku FROM committed_demand c
           JOIN customer_portal_links l ON l.id = $1 AND l.tenant_id = c.tenant_id
                                       AND l.customer_key = lower(btrim(c.customer))
          WHERE c.id = $2 AND c.tenant_id = $3 AND c.contract_withdrawn_at IS NULL",
    )
    .bind(&link_id)
    .bind(&commitment_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    let Some((sku,)) = found else {
        return Err(ApiError::app("customer_portal_commitment_not_found",
            "That commitment is not on this page", 404, json!({})));
    };
    match promised_date {
        Some(d) => {
            sqlx::query(
                "INSERT INTO customer_portal_promised_dates (commitment_id, tenant_id, promised_date, set_by)
                 VALUES ($1, $2, $3, $4)
                 ON CONFLICT (commitment_id) DO UPDATE
                   SET promised_date = EXCLUDED.promised_date, set_by = EXCLUDED.set_by, set_at = NOW()",
            )
            .bind(&commitment_id)
            .bind(&user.tenant_id)
            .bind(d)
            .bind(&user.user_id)
            .execute(pool)
            .await?;
        }
        None => {
            sqlx::query("DELETE FROM customer_portal_promised_dates WHERE commitment_id = $1 AND tenant_id = $2")
                .bind(&commitment_id)
                .bind(&user.tenant_id)
                .execute(pool)
                .await?;
        }
    }
    record_event(pool, &user.tenant_id, &user.user_id, Event::CustomerPortalPromiseSet, Some(&link_id),
        details(&[("customer", json!(customer)), ("sku", json!(sku)),
                  ("promised_date", promised_date.map(|d| json!(isoformat_date(&d))).unwrap_or(Value::Null))])).await;
    Ok(ok(json!({"commitment_id": commitment_id,
                 "promised_date": promised_date.map(|d| isoformat_date(&d))})))
}

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/customer-portal/customers", get(customers))
        .route("/api/v1/customer-portal/links", get(list_links).post(create_link))
        .route("/api/v1/customer-portal/links/{link_id}", get(get_link).patch(update_link))
        .route("/api/v1/customer-portal/links/{link_id}/revoke", post(revoke_link))
        .route("/api/v1/customer-portal/links/{link_id}/reopen", post(reopen_link))
        .route("/api/v1/customer-portal/links/{link_id}/promised-dates", put(set_promised_date))
        .route("/api/v1/customer-portal/public/{token}", get(public_view))
        .route("/api/v1/customer-portal/public/{token}/respond", post(public_respond))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Commitment {
        Commitment {
            id: "c1".into(), sku: "SKU-1".into(), description: "Widget".into(), quantity: 40.0,
            delivery_date: NaiveDate::from_ymd_opt(2027, 3, 1).unwrap(), status: "open".into(),
            promised_date: Some(NaiveDate::from_ymd_opt(2027, 3, 8).unwrap()),
        }
    }

    #[test]
    fn token_shape_is_the_supplier_links() {
        let t = new_token().unwrap();
        assert_eq!(t.len(), 43);
        assert!(token_is_wellformed(&t));
        assert_ne!(t, new_token().unwrap());
        assert!(!token_is_wellformed(""));
        assert!(!token_is_wellformed(&"a".repeat(42)));
        assert!(!token_is_wellformed(&"a".repeat(129)));
        assert!(!token_is_wellformed(&format!("{}!", "a".repeat(43))));
        assert!(!token_is_wellformed(&format!("{}%00", "a".repeat(43))));
        assert!(token_is_wellformed(&"a".repeat(43)));
    }

    #[test]
    fn only_the_hash_is_derived_and_it_is_sha256_hex() {
        let h = hash_token("abc");
        assert_eq!(h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(h.len(), 64);
    }

    #[test]
    fn constant_time_eq_compares_whole_strings() {
        assert!(constant_time_eq(b"abcd", b"abcd"));
        assert!(!constant_time_eq(b"abcd", b"abce"));
        assert!(!constant_time_eq(b"abcd", b"abc"));
        assert!(constant_time_eq(b"", b""));
    }

    #[test]
    fn client_hash_is_keyed_and_stable() {
        let a = hash_client_value("1.2.3.4", "secret");
        assert_eq!(a.len(), 32);
        assert_eq!(a, hash_client_value("1.2.3.4", "secret"));
        assert_ne!(a, hash_client_value("1.2.3.5", "secret"));
        assert_ne!(a, hash_client_value("1.2.3.4", "other"));
        assert!(!a.contains("1.2.3.4"));
    }

    #[test]
    fn client_address_takes_the_first_forwarded_hop() {
        let mut h = HeaderMap::new();
        assert_eq!(client_address(&h), "unknown");
        h.insert("x-forwarded-for", HeaderValue::from_static(" 9.9.9.9 , 10.0.0.1"));
        assert_eq!(client_address(&h), "9.9.9.9");
        h.insert("x-forwarded-for", HeaderValue::from_static(" , 10.0.0.1"));
        assert_eq!(client_address(&h), "unknown");
    }

    #[test]
    fn rate_keys_never_contain_the_token() {
        let t = new_token().unwrap();
        let keys = rate_keys(Kind::Write, "1.1.1.1", &t);
        assert_eq!(keys.len(), 2);
        assert_eq!(keys[0].0, "customer_portal:write:ip:1.1.1.1");
        assert_eq!(keys[0].1, WRITE_PER_ADDRESS);
        assert!(keys[1].0.starts_with("customer_portal:write:link:"));
        assert!(!keys[1].0.contains(&t));
        assert_eq!(keys[1].1, WRITE_PER_LINK);
        // A malformed token is still counted per address, and per nothing else.
        assert_eq!(rate_keys(Kind::Read, "1.1.1.1", "nope").len(), 1);
        assert_eq!(rate_keys(Kind::Read, "1.1.1.1", &t)[1].1, READ_PER_LINK);
    }

    #[test]
    fn a_public_commitment_has_exactly_the_whitelisted_keys() {
        let c = sample();
        let shown = public_commitment(&c, true, Some("received"));
        let mut keys: Vec<&str> = shown.as_object().unwrap().keys().map(String::as_str).collect();
        keys.sort_unstable();
        let mut want = PUBLIC_COMMITMENT_KEYS.to_vec();
        want.sort_unstable();
        assert_eq!(keys, want);
        assert_eq!(shown["requested_date"], "2027-03-01");
        assert_eq!(shown["promised_date"], "2027-03-08");
        assert_eq!(shown["my_response"], "received");
    }

    #[test]
    fn promised_date_is_hidden_unless_shared_and_set() {
        let c = sample();
        assert!(public_commitment(&c, false, None).get("promised_date").is_none());
        let mut unset = sample();
        unset.promised_date = None;
        assert!(public_commitment(&unset, true, None).get("promised_date").is_none());
        assert_eq!(public_commitment(&c, true, None)["my_response"], Value::Null);
    }

    #[test]
    fn no_internal_field_name_is_on_the_whitelists() {
        for banned in ["stock", "cost", "unit_cost", "probability", "note", "warehouse", "supplier",
                       "warehouse_id", "customer_key", "token", "token_hash", "created_by", "verdict",
                       "on_top_of_base", "contract_id", "source", "min_stock", "current_stock"] {
            assert!(!PUBLIC_COMMITMENT_KEYS.contains(&banned), "{banned} must not reach the customer");
            assert!(!PUBLIC_PAGE_KEYS.contains(&banned), "{banned} must not reach the customer");
        }
    }

    fn err_reason(raw: &str) -> (u16, String, String) {
        let e = parse_ack(raw.as_bytes()).unwrap_err();
        (e.status.as_u16(), e.body["error_code"].as_str().unwrap().into(),
         e.body["error_params"]["reason"].as_str().unwrap_or("").into())
    }

    #[test]
    fn ack_accepts_the_two_answers() {
        let a = parse_ack(br#"{"commitment_id":"abc-123","response":"received"}"#).unwrap();
        assert_eq!(a, Ack { commitment_id: "abc-123".into(), response: "received".into(), comment: None });
        let b = parse_ack(r#"{"commitment_id":"x","response":"date_objection","comment":"  Needs to be May á "}"#.as_bytes()).unwrap();
        assert_eq!(b.comment.as_deref(), Some("Needs to be May á"));
        // an empty comment on "received" is simply no comment
        assert_eq!(parse_ack(br#"{"commitment_id":"x","response":"received","comment":"  "}"#).unwrap().comment, None);
    }

    #[test]
    fn ack_validation_is_strict() {
        assert_eq!(err_reason("not json"), (422, "customer_portal_invalid_response".into(), "body_invalid".into()));
        assert_eq!(err_reason("[1]").2, "body_invalid");
        assert_eq!(err_reason("null").2, "body_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a","response":"received","extra":1}"#).2, "unknown_field");
        assert_eq!(err_reason(r#"{"response":"received"}"#).2, "commitment_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a b","response":"received"}"#).2, "commitment_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a'; DROP","response":"received"}"#).2, "commitment_invalid");
        assert_eq!(err_reason(&format!(r#"{{"commitment_id":"{}","response":"received"}}"#, "a".repeat(65))).2, "commitment_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":5,"response":"received"}"#).2, "commitment_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a"}"#).2, "response_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a","response":"fulfilled"}"#).2, "response_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a","response":"date_objection"}"#).2, "comment_required");
        assert_eq!(err_reason(r#"{"commitment_id":"a","response":"date_objection","comment":" "}"#).2, "comment_required");
        assert_eq!(err_reason(r#"{"commitment_id":"a","response":"received","comment":7}"#).2, "comment_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a","response":"received","comment":"x\u0000y"}"#).2, "comment_invalid");
        assert_eq!(err_reason(r#"{"commitment_id":"a","response":"received","comment":"x\u001by"}"#).2, "comment_invalid");
        let long = "x".repeat(501);
        assert_eq!(err_reason(&format!(r#"{{"commitment_id":"a","response":"received","comment":"{long}"}}"#)).2, "comment_too_long");
        let ok500 = "x".repeat(500);
        assert!(parse_ack(format!(r#"{{"commitment_id":"a","response":"received","comment":"{ok500}"}}"#).as_bytes()).is_ok());
    }

    #[test]
    fn ack_body_over_the_cap_is_413_before_parsing() {
        let big = vec![b' '; MAX_BODY_BYTES + 1];
        let e = parse_ack(&big).unwrap_err();
        assert_eq!(e.status.as_u16(), 413);
        assert_eq!(e.body["error_code"], "customer_portal_body_too_large");
        assert_eq!(e.body["error_params"]["max_kb"], 16);
    }

    #[test]
    fn the_unknown_link_answer_is_one_fixed_404() {
        let a = unavailable();
        assert_eq!(a.status.as_u16(), 404);
        assert_eq!(a.body["error_code"], "customer_portal_unavailable");
        // identical for every reason: it carries no parameter that could differ
        assert_eq!(a.body["error_params"], json!({}));
    }

    #[test]
    fn hardened_adds_the_three_privacy_headers() {
        let r = hardened(StatusCode::OK.into_response());
        assert_eq!(r.headers()["cache-control"], "no-store");
        assert_eq!(r.headers()["referrer-policy"], "no-referrer");
        assert_eq!(r.headers()["x-robots-tag"], "noindex, nofollow");
    }

    #[test]
    fn promised_dates_outside_the_window_are_refused() {
        let today = NaiveDate::from_ymd_opt(2026, 10, 6).unwrap();
        assert!(check_promised_date(NaiveDate::from_ymd_opt(2027, 1, 1).unwrap(), today).is_ok());
        assert!(check_promised_date(NaiveDate::from_ymd_opt(2036, 10, 6).unwrap(), today).is_ok());
        assert!(check_promised_date(NaiveDate::from_ymd_opt(2036, 10, 7).unwrap(), today).is_err());
        assert!(check_promised_date(NaiveDate::from_ymd_opt(1999, 12, 31).unwrap(), today).is_err());
    }

    #[test]
    fn optional_int_is_bounded() {
        let mut errs = Errors::default();
        let obj = json!({"a": 5, "b": 0, "c": 400, "d": 1.5, "e": "7", "f": null, "g": 30.0});
        let o = obj.as_object().unwrap();
        assert_eq!(opt_int(&mut errs, o, "a", 1, 365), Some(5));
        assert_eq!(opt_int(&mut errs, o, "b", 1, 365), None);
        assert_eq!(opt_int(&mut errs, o, "c", 1, 365), None);
        assert_eq!(opt_int(&mut errs, o, "d", 1, 365), None);
        assert_eq!(opt_int(&mut errs, o, "e", 1, 365), None);
        assert_eq!(opt_int(&mut errs, o, "f", 1, 365), None);
        assert_eq!(opt_int(&mut errs, o, "g", 1, 365), Some(30));
        assert_eq!(opt_int(&mut errs, o, "missing", 1, 365), None);
        assert_eq!(errs.0.len(), 4);
    }
}
