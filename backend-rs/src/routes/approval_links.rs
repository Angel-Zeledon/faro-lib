//! Approve or reject a purchase order from a message (email, and WhatsApp where
//! a number is configured), without opening the app. NEW routes, written in
//! Rust only: there is no Python twin to fail over to (docs/rust-migration.md,
//! "Approve or reject from a message").
//!
//! Public (the credential is the link; no login):
//! * `GET  /approval-links/{token}`           the confirmation page's data
//! * `POST /approval-links/{token}/decision`  `{decision, comment?}`
//!
//! Internal (a signed-in analyst or admin, with the order's warehouse guard):
//! * `POST /inventory/po/{po_log_id}/approval/links`         (re)issue and queue the messages
//! * `POST /inventory/po/{po_log_id}/approval/links/revoke`  kill every open link
//!
//! What a link is. 256 random bits; only the SHA-256 hash is stored, compared
//! in constant time; bound to ONE approver, ONE approval request (and so one
//! order and tenant) and the scope `decide`; single use; expiring
//! ([`LINK_TTL_HOURS`]); re-issuing rotates the token and never reopens a used
//! link. A used, expired, revoked, unknown, malformed or no-longer-eligible link
//! all answer the same neutral 404 ([`not_found`]), so nobody can tell those
//! cases apart or probe for orders.
//!
//! What a GET does: nothing. It reads, it writes no row (not even "viewed"):
//! a mail scanner that prefetches the URL must not change anything, and
//! deciding needs an explicit POST from the page.
//!
//! How a decision is taken: through `po_approvals::decide_core`, the one
//! function the in-app approve and reject routes also use, so approver status,
//! warehouse scope, the self-approval limit and "a rejection needs a reason"
//! apply identically. The link is consumed INSIDE that transaction, the
//! decision is recorded with `decided_channel = 'message'` and the audit event
//! carries `channel: message`. Delegation (an approver handing their authority
//! to somebody else) does not exist on this base: `eligible_approver` is the one
//! place a delegate would be admitted, so adding it later cannot widen what a
//! link allows.
//!
//! With `APPROVAL_LINKS_ENABLED` off (the default) nothing issues a link and
//! these routes answer as if no link existed.

use std::net::SocketAddr;

use axum::body::Bytes;
use axum::extract::{ConnectInfo, Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use base64::Engine;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Row};

use crate::activity::{record_event, Event};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::config::parse_bool;
use crate::entitlements::{tenant_features, TenantRow};
use crate::error::ApiError;
use crate::outbox::{self, Channel, Message};
use crate::pycompat::{isoformat_utc, py_strip};
use crate::routes::ok;
use crate::routes::po_approvals::{self, ViaLink};
use crate::routes::po_payments::{format_po_number, po_writer};
use crate::state::AppState;

const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'inventory-approvals': sending or revoking decision links is an act of the people who hold approval authority",
    ),
    is_mcp: false,
};

/// How long an approver has to act on a message. The Python side carries the
/// same number (`po_approval_link_service.LINK_TTL_HOURS`); a unit test reads
/// that source and fails when the two differ.
pub const LINK_TTL_HOURS: i64 = 72;
const MAX_COMMENT_LENGTH: usize = 500;
const MAX_BODY_BYTES: usize = 8 * 1024;
/// Lines shown on the page. A longer order says how many it has.
const MAX_LINES_SHOWN: usize = 50;
const TOKEN_BYTES: usize = 32;

// (max requests, window seconds). Generous for a person on a phone, hopeless
// for a script: reading the page a few times, deciding once or twice.
const READ_PER_ADDRESS: (i64, i64) = (120, 600);
const READ_PER_LINK: (i64, i64) = (60, 600);
const WRITE_PER_ADDRESS: (i64, i64) = (20, 600);
const WRITE_PER_LINK: (i64, i64) = (10, 600);
/// Re-sending the links of one order: not a way to mail somebody over and over.
const ISSUE_PER_ORDER: (i64, i64) = (5, 3600);

pub fn router() -> axum::Router<AppState> {
    use axum::routing::{get, post};
    axum::Router::new()
        .route("/api/v1/approval-links/{token}", get(view))
        .route("/api/v1/approval-links/{token}/decision", post(decide))
        .route("/api/v1/inventory/po/{po_log_id}/approval/links", post(issue))
        .route("/api/v1/inventory/po/{po_log_id}/approval/links/revoke", post(revoke))
}

// ── Errors ───────────────────────────────────────────────────────────────────

/// The one answer for every bad link. Same status, code and text whether the
/// token is malformed, unknown, used, revoked, expired, or its order, request
/// or approver is no longer in a state to decide.
pub fn not_found() -> ApiError {
    ApiError::app("approval_link_not_found", "This link is not valid or is no longer available", 404, json!({}))
}

fn invalid_request(field: &str) -> ApiError {
    ApiError::app("approval_link_invalid_request", "The request could not be accepted", 422, json!({"field": field}))
}

fn too_many() -> ApiError {
    ApiError::app("too_many_attempts", "Too many attempts. Please try again later.", 429, json!({}))
}

/// The URL is the secret: never cached, never sent on as a referrer, never indexed.
fn no_store(e: ApiError) -> ApiError {
    // Lower case: `HeaderName::from_static` panics on anything else, and a panic
    // here would drop the connection on exactly the error paths.
    e.with_header("cache-control", "no-store")
        .with_header("referrer-policy", "no-referrer")
        .with_header("x-robots-tag", "noindex, nofollow")
}

type Guarded = ([(&'static str, &'static str); 3], Json<Value>);

fn guarded(body: Json<Value>) -> Guarded {
    (
        [("cache-control", "no-store"), ("referrer-policy", "no-referrer"), ("x-robots-tag", "noindex, nofollow")],
        body,
    )
}

// ── The credential ───────────────────────────────────────────────────────────

/// `po_confirmation_core.token_is_wellformed`: `[A-Za-z0-9_-]{43,128}`. A
/// megabyte of garbage in the URL is refused before it is hashed.
pub fn token_is_wellformed(token: &str) -> bool {
    (43..=128).contains(&token.len()) && token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// SHA-256 hex. Salting buys nothing: the input is 256 random bits.
pub fn hash_token(token: &str) -> String {
    hex::encode(Sha256::digest(token.as_bytes()))
}

/// A fresh link credential: 32 random bytes, URL-safe, 43 characters.
pub fn new_token() -> Result<String, ApiError> {
    let mut b = [0u8; TOKEN_BYTES];
    getrandom::getrandom(&mut b).map_err(|_| ApiError::internal())?;
    Ok(base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(b))
}

/// Constant-time equality of two hex digests.
fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

// ── Switch, address, rate limits ─────────────────────────────────────────────

fn enabled(state: &AppState) -> bool {
    state.settings.raw("APPROVAL_LINKS_ENABLED").and_then(parse_bool).unwrap_or(false)
}

/// Best effort, like `trial._client_address`: behind Caddy and the Next proxy
/// the socket peer is the proxy, so the first forwarded address is the closest
/// thing to the visitor there is. A client can forge it, which is why the
/// per-LINK limit is the one that matters.
pub fn client_address(headers: &HeaderMap, peer: Option<SocketAddr>) -> String {
    let forwarded = headers.get("x-forwarded-for").and_then(|v| v.to_str().ok()).unwrap_or("");
    let first = py_strip(forwarded.split(',').next().unwrap_or(""));
    if !first.is_empty() {
        return first.to_string();
    }
    peer.map(|p| p.ip().to_string()).unwrap_or_else(|| "unknown".into())
}

/// `auth._check_rate`: at most `max` events with this key in the window,
/// counted and recorded under a per-key advisory lock. FAILS CLOSED: a public
/// credential door that cannot count is refused, not left open.
async fn rate_allows(pool: &PgPool, key: &str, max: i64, window_secs: i64) -> Result<bool, sqlx::Error> {
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))").bind(format!("ratelimit:{key}")).execute(&mut *tx).await?;
    sqlx::query("DELETE FROM auth_rate_events WHERE key = $1 AND created_at < NOW() - make_interval(secs => $2)")
        .bind(key)
        .bind(window_secs as f64)
        .execute(&mut *tx)
        .await?;
    let (n,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM auth_rate_events WHERE key = $1").bind(key).fetch_one(&mut *tx).await?;
    let allowed = n < max;
    if allowed {
        sqlx::query("INSERT INTO auth_rate_events (key) VALUES ($1)").bind(key).execute(&mut *tx).await?;
    }
    tx.commit().await?;
    Ok(allowed)
}

/// Per address AND per link, counted for every request whether or not the link
/// is valid, so the 429 reveals nothing either. The link key is a digest, never
/// the token: rate rows must not become a second copy of the credential.
async fn limit(state: &AppState, headers: &HeaderMap, peer: Option<SocketAddr>, token: &str, write: bool)
    -> Result<(), ApiError>
{
    if state.settings.testing_mode {
        return Ok(());
    }
    let kind = if write { "write" } else { "read" };
    let (per_address, per_link) = if write { (WRITE_PER_ADDRESS, WRITE_PER_LINK) } else { (READ_PER_ADDRESS, READ_PER_LINK) };
    let address = client_address(headers, peer);
    if !rate_allows(&state.pool, &format!("approval_link:{kind}:ip:{address}"), per_address.0, per_address.1).await? {
        return Err(too_many());
    }
    if token_is_wellformed(token) {
        let key = format!("approval_link:{kind}:link:{}", &hash_token(token)[..24]);
        if !rate_allows(&state.pool, &key, per_link.0, per_link.1).await? {
            return Err(too_many());
        }
    }
    Ok(())
}

// ── The door: a token to the one request it may act on ───────────────────────

struct Resolved {
    link_id: String,
    user: CurrentUser,
    approver_name: Option<String>,
    po: po_approvals::Po,
    amount: f64,
    requested_by: String,
    requested_at: Option<chrono::DateTime<chrono::Utc>>,
    request_note: Option<String>,
    expires_at: Option<chrono::DateTime<chrono::Utc>>,
}

/// `eligible_approver`: may THIS user decide approvals right now? The one place
/// a delegate (somebody an approver handed their authority to) would be
/// admitted, if delegation is ever added: a link cannot be wider than this.
async fn eligible_approver(state: &AppState, tenant: &str, user_id: &str, po_log_id: &str)
    -> Result<Option<(CurrentUser, Option<String>)>, ApiError>
{
    let pool = &state.pool;
    let row = sqlx::query(
        "SELECT id, email, full_name, role, email_verified FROM users \
          WHERE id = $1 AND tenant_id = $2 AND status = 'active' AND can_approve_po AND role IN ('admin', 'analyst')",
    )
    .bind(user_id)
    .bind(tenant)
    .fetch_optional(pool)
    .await?;
    let Some(row) = row else { return Ok(None) };
    let user = CurrentUser {
        user_id: row.try_get("id")?,
        tenant_id: tenant.to_string(),
        role: row.try_get("role")?,
        email_verified: row.try_get::<Option<bool>, _>("email_verified")?.unwrap_or(false),
        api_key_id: None,
    };
    // The same two checks the in-app route runs before it decides: the trial
    // read-only rule and the warehouse scope of the order. A refusal is the
    // neutral not-found here: nothing about the order is shown to somebody who
    // could not act on it.
    if auth::require_analyst_or_above(state, &user).await.is_err() {
        return Ok(None);
    }
    if wscope::po_guard(pool, &user, po_log_id).await.is_err() {
        return Ok(None);
    }
    let name = po_approvals::name_of(row.try_get::<Option<String>, _>("full_name")?.as_deref(),
        row.try_get::<Option<String>, _>("email")?.as_deref());
    Ok(Some((user, name)))
}

async fn resolve(state: &AppState, token: &str) -> Result<Resolved, ApiError> {
    if !enabled(state) || !token_is_wellformed(token) {
        return Err(not_found());
    }
    let pool = &state.pool;
    let hash = hash_token(token);
    let row = sqlx::query(
        "SELECT id, tenant_id, po_log_id, approval_id, approver_id, scope, token_hash, expires_at, \
                (expires_at > NOW()) AS live, used_at IS NOT NULL AS used, revoked_at IS NOT NULL AS revoked \
           FROM po_approval_links WHERE token_hash = $1",
    )
    .bind(&hash)
    .fetch_optional(pool)
    .await?;
    let Some(row) = row else { return Err(not_found()) };
    let stored: String = row.try_get("token_hash")?;
    let scope: String = row.try_get("scope")?;
    if !ct_eq(&hash, &stored)
        || scope != "decide"
        || !row.try_get::<bool, _>("live")?
        || row.try_get::<bool, _>("used")?
        || row.try_get::<bool, _>("revoked")?
    {
        return Err(not_found());
    }
    let link_id: String = row.try_get("id")?;
    let tenant: String = row.try_get("tenant_id")?;
    let po_log_id: String = row.try_get("po_log_id")?;
    let approval_id: String = row.try_get("approval_id")?;
    let approver_id: String = row.try_get("approver_id")?;

    // The request must still be open, and be the one this link was made for.
    let approval = sqlx::query(
        "SELECT amount, requested_by, requested_at, request_note FROM po_approvals \
          WHERE id = $1 AND tenant_id = $2 AND po_log_id = $3 AND status = 'requested'",
    )
    .bind(&approval_id)
    .bind(&tenant)
    .bind(&po_log_id)
    .fetch_optional(pool)
    .await?;
    let Some(approval) = approval else { return Err(not_found()) };
    // An order cancelled after the request is not decided from a message.
    let cancelled: Option<(bool,)> =
        sqlx::query_as("SELECT cancelled_at IS NOT NULL FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
            .bind(&po_log_id)
            .bind(&tenant)
            .fetch_optional(pool)
            .await?;
    if cancelled.map_or(true, |(c,)| c) {
        return Err(not_found());
    }
    let Some((user, approver_name)) = eligible_approver(state, &tenant, &approver_id, &po_log_id).await? else {
        return Err(not_found());
    };
    let po = po_approvals::get_po(pool, &tenant, &po_log_id).await?;
    Ok(Resolved {
        link_id,
        user,
        approver_name,
        po,
        amount: approval.try_get("amount")?,
        requested_by: approval.try_get("requested_by")?,
        requested_at: approval.try_get("requested_at")?,
        request_note: approval.try_get("request_note")?,
        expires_at: row.try_get("expires_at")?,
    })
}

// ── Public: the page's data ──────────────────────────────────────────────────

/// What the approver may read. A WHITELIST: each field below is chosen, nothing
/// is copied from the order wholesale, so a column added to the order tables
/// tomorrow cannot leak through here. The money is the order's TOTAL (the thing
/// being decided) and nothing finer: no unit cost, no line value, no stock, no
/// margin. The people are named only as the app names them to an approver.
async fn view_data(state: &AppState, r: &Resolved) -> Result<Value, ApiError> {
    let pool = &state.pool;
    let tenant = &r.user.tenant_id;
    let lines = sqlx::query(
        "SELECT sku, display_name, supplier, final_qty FROM inventory_po_items \
          WHERE po_log_id = $1 AND tenant_id = $2 AND status IN ('approved', 'modified') \
          ORDER BY supplier NULLS LAST, sku",
    )
    .bind(&r.po.id)
    .bind(tenant)
    .fetch_all(pool)
    .await?;
    let mut suppliers: Vec<String> = Vec::new();
    let mut shown: Vec<Value> = Vec::new();
    for l in &lines {
        let supplier: Option<String> = l.try_get("supplier")?;
        if let Some(s) = supplier.as_deref().map(py_strip).filter(|s| !s.is_empty()) {
            if !suppliers.iter().any(|x| x == s) {
                suppliers.push(s.to_string());
            }
        }
        if shown.len() < MAX_LINES_SHOWN {
            let sku: String = l.try_get("sku")?;
            let name: Option<String> = l.try_get("display_name")?;
            let qty: Option<f64> = l.try_get("final_qty")?;
            shown.push(json!({
                "sku": sku,
                "name": name.filter(|n| !n.is_empty()).unwrap_or_else(|| sku.clone()),
                "quantity": qty.unwrap_or(0.0),
                "supplier": supplier,
            }));
        }
    }
    let requester: Option<(Option<String>, Option<String>)> =
        sqlx::query_as("SELECT full_name, email FROM users WHERE id = $1 AND tenant_id = $2")
            .bind(&r.requested_by)
            .bind(tenant)
            .fetch_optional(pool)
            .await?;
    let requested_by_name = requester.and_then(|(n, e)| po_approvals::name_of(n.as_deref(), e.as_deref()));
    let buyer: Option<(String,)> = sqlx::query_as("SELECT name FROM tenants WHERE id = $1").bind(tenant).fetch_optional(pool).await?;
    let currency = crate::routes::r1::currency::currency_of(pool, tenant).await?;
    // The request's own amount is what the in-app panel shows the approver; the
    // self-approval rule is the same function the decision applies.
    let req = po_approvals::requirement_of(pool, tenant, &r.po).await?;
    let can_approve = po_approvals::may_decide_as_approver(&r.user.user_id, &r.requested_by, r.amount, &req);
    Ok(json!({
        "reference": format_po_number(r.po.po_number, &r.po.id),
        "buyer": buyer.map(|(n,)| n),
        "amount": r.amount,
        "currency": currency,
        "requested_by_name": requested_by_name,
        "requested_at": r.requested_at.map(|d| isoformat_utc(&d)),
        "note": r.request_note,
        "warehouse": r.po.destination_warehouse,
        "suppliers": suppliers,
        "line_count": lines.len(),
        "lines": shown,
        "can_approve": can_approve,
        "can_reject": true,
        "approver_name": r.approver_name,
        "expires_at": r.expires_at.map(|d| isoformat_utc(&d)),
        "comment_max": MAX_COMMENT_LENGTH,
    }))
}

/// The page's data, after the rate limit. Reads only: it writes no row.
pub(crate) async fn view_with_token(state: &AppState, token: &str) -> Result<Value, ApiError> {
    let r = resolve(state, token).await?;
    view_data(state, &r).await
}

pub async fn view(
    State(state): State<AppState>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(token): Path<String>,
    headers: HeaderMap,
) -> Result<Guarded, ApiError> {
    let peer = peer.map(|Extension(ConnectInfo(a))| a);
    async {
        limit(&state, &headers, peer, &token, false).await?;
        Ok(guarded(ok(view_with_token(&state, &token).await?)))
    }
    .await
    .map_err(no_store)
}

// ── Public: the decision ─────────────────────────────────────────────────────

/// `{decision: 'approved'|'rejected', comment?: string|null}`, nothing else.
pub fn parse_decision(raw: &[u8]) -> Result<(&'static str, Option<String>), ApiError> {
    if raw.len() > MAX_BODY_BYTES {
        return Err(ApiError::app("approval_link_body_too_large", "The answer is too large", 413,
            json!({"max_kb": MAX_BODY_BYTES / 1024})));
    }
    let body: Value = serde_json::from_slice(raw).map_err(|_| invalid_request("body"))?;
    let Some(obj) = body.as_object() else { return Err(invalid_request("body")) };
    let decision = match obj.get("decision").and_then(Value::as_str) {
        Some("approved") => "approved",
        Some("rejected") => "rejected",
        _ => return Err(invalid_request("decision")),
    };
    let comment = match obj.get("comment") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if s.chars().count() <= MAX_COMMENT_LENGTH => Some(s.clone()),
        Some(_) => return Err(invalid_request("comment")),
    };
    Ok((decision, comment))
}

pub async fn decide(
    State(state): State<AppState>,
    peer: Option<Extension<ConnectInfo<SocketAddr>>>,
    Path(token): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Guarded, ApiError> {
    let peer = peer.map(|Extension(ConnectInfo(a))| a);
    async {
        limit(&state, &headers, peer, &token, true).await?;
        Ok(guarded(ok(decide_with_token(&state, &token, &bytes).await?)))
    }
    .await
    .map_err(no_store)
}

/// The decision itself, after the rate limit: resolve the link, validate the
/// body, decide through the in-app path with the link spent in the same
/// transaction.
pub(crate) async fn decide_with_token(state: &AppState, token: &str, raw: &[u8]) -> Result<Value, ApiError> {
    let r = resolve(state, token).await?;
    let (decision, comment) = parse_decision(raw)?;
    let result = po_approvals::decide_core(
        state, &r.user.tenant_id, &r.user.user_id, &r.po.id, decision, comment,
        Some(ViaLink { link_id: &r.link_id }),
    )
    .await;
    match result {
        Ok(_) => Ok(json!({
            "decided": true,
            "decision": decision,
            "reference": format_po_number(r.po.po_number, &r.po.id),
        })),
        // A request that is no longer open is, for this link, a used link.
        Err(e) if matches!(e.code(), Some("po_approval_already_decided" | "po_approval_not_requested" | "po_not_found")) => {
            Err(not_found())
        }
        Err(e) => Err(e),
    }
}

// ── Internal: issue and revoke ───────────────────────────────────────────────

struct OpenRequest {
    id: String,
    amount: f64,
    requested_by: String,
}

async fn open_request(pool: &PgPool, tenant: &str, po_log_id: &str) -> Result<OpenRequest, ApiError> {
    let row = sqlx::query(
        "SELECT id, amount, requested_by FROM po_approvals WHERE po_log_id = $1 AND tenant_id = $2 AND status = 'requested'",
    )
    .bind(po_log_id)
    .bind(tenant)
    .fetch_optional(pool)
    .await?;
    let Some(row) = row else {
        return Err(ApiError::app("po_approval_not_requested", "Nobody has asked for approval on this order", 409, json!({})));
    };
    Ok(OpenRequest { id: row.try_get("id")?, amount: row.try_get("amount")?, requested_by: row.try_get("requested_by")? })
}

fn disabled() -> ApiError {
    ApiError::app("approval_links_disabled", "Decision links are not enabled on this installation", 409, json!({}))
}

/// approver id -> verified WhatsApp number, only when a message can actually go
/// out: the tenant's channel is configured, its plan includes the WhatsApp bot
/// (the existing gate; this adds none) and the number is the person's VERIFIED
/// one. An unverified number may belong to somebody else; a link is a credential.
async fn whatsapp_numbers(state: &AppState, tenant: &str, ids: &[String]) -> Result<Vec<(String, String)>, ApiError> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let pool = &state.pool;
    let tenant_row = TenantRow::load(pool, tenant).await?.unwrap_or_default();
    let has_bot = state.settings.testing_mode
        || tenant_features(&tenant_row).get("whatsapp_bot").and_then(Value::as_bool).unwrap_or(false);
    let states = crate::service_config::service_states(pool, &state.settings).await;
    if !has_bot || states.get("whatsapp").and_then(Value::as_str) != Some("ready") {
        return Ok(Vec::new());
    }
    let rows: Vec<(String, String)> = sqlx::query_as(
        "SELECT id, TRIM(whatsapp_number) FROM users WHERE tenant_id = $1 AND id = ANY($2) \
            AND COALESCE(TRIM(whatsapp_number), '') <> '' AND whatsapp_verified_at IS NOT NULL",
    )
    .bind(tenant)
    .bind(ids)
    .fetch_all(pool)
    .await?;
    Ok(rows)
}

pub async fn issue(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    issue_core(&state, &user, &po_log_id).await.map(ok)
}

/// Rotate and queue the links of the open request of one order.
pub(crate) async fn issue_core(state: &AppState, user: &CurrentUser, po_log_id: &str) -> Result<Value, ApiError> {
    let po_log_id = po_log_id.to_string();
    let pool = &state.pool;
    let tenant = &user.tenant_id;
    if !enabled(state) {
        return Err(disabled());
    }
    let po = po_approvals::get_po(pool, tenant, &po_log_id).await?;
    let open = open_request(pool, tenant, &po_log_id).await?;
    if !state.settings.testing_mode
        && !rate_allows(pool, &format!("approval_link:issue:{po_log_id}"), ISSUE_PER_ORDER.0, ISSUE_PER_ORDER.1).await?
    {
        return Err(too_many());
    }
    // The people who may decide this request, never the person who asked (they
    // know it is theirs, and have the app): the same set the request mail goes to.
    let recipients: Vec<po_approvals::Approver> = po_approvals::list_approvers(pool, tenant)
        .await?
        .into_iter()
        .filter(|a| a.id != open.requested_by)
        .collect();
    let ids: Vec<String> = recipients.iter().map(|a| a.id.clone()).collect();
    let numbers = whatsapp_numbers(state, tenant, &ids).await?;

    let (mut email_queued, mut wa_queued, mut links) = (0usize, 0usize, 0usize);
    for a in &recipients {
        let mut wanted: Vec<(Channel, String)> = Vec::new();
        if let Some(e) = a.email.as_deref().map(py_strip).filter(|e| !e.is_empty()) {
            wanted.push((Channel::Email, e.to_string()));
        }
        if let Some((_, n)) = numbers.iter().find(|(id, _)| *id == a.id) {
            wanted.push((Channel::Whatsapp, n.clone()));
        }
        for (channel, recipient) in wanted {
            let token = new_token()?;
            let hash = hash_token(&token);
            // Rotate: the earlier token stops working. A link already USED is
            // left alone (no row comes back): a decision was taken with it.
            let link: Option<(String,)> = sqlx::query_as(
                r#"INSERT INTO po_approval_links
                       (tenant_id, po_log_id, approval_id, approver_id, token_hash, channel, expires_at, created_by)
                   VALUES ($1, $2, $3, $4, $5, $6, NOW() + make_interval(hours => $7), $8)
                   ON CONFLICT (approval_id, approver_id, channel) DO UPDATE
                      SET token_hash = EXCLUDED.token_hash, issued_at = NOW(), expires_at = EXCLUDED.expires_at,
                          revoked_at = NULL, revoked_by = NULL, revoked_reason = NULL
                    WHERE po_approval_links.used_at IS NULL
                RETURNING id"#,
            )
            .bind(tenant)
            .bind(&po_log_id)
            .bind(&open.id)
            .bind(&a.id)
            .bind(&hash)
            .bind(channel.as_str())
            .bind(LINK_TTL_HOURS as i32)
            .bind(&user.user_id)
            .fetch_optional(pool)
            .await?;
            let Some((link_id,)) = link else { continue };
            links += 1;
            let key = format!("po_approval_link:{link_id}:{}", &hash[..12]);
            let (kind, params) = match channel {
                Channel::Email => ("po_approval_request", json!({
                    "po_log_id": po_log_id, "amount": open.amount, "approver_id": a.id,
                    "requester_id": open.requested_by, "decision_token": token,
                })),
                Channel::Whatsapp => ("po_approval_link", json!({
                    "po_log_id": po_log_id, "amount": open.amount, "decision_token": token, "approver_id": a.id,
                })),
            };
            let mut m = Message::new(tenant, channel, kind, &recipient, params);
            m.created_by = Some(&user.user_id);
            m.dedupe_key = Some(&key);
            m.ttl_seconds = LINK_TTL_HOURS * 3600;
            if outbox::enqueue(pool, m).await.is_some() {
                match channel {
                    Channel::Email => email_queued += 1,
                    Channel::Whatsapp => wa_queued += 1,
                }
            }
        }
    }
    if links > 0 {
        let mut d = Map::new();
        d.insert("reference".into(), json!(format_po_number(po.po_number, &po_log_id)));
        d.insert("count".into(), json!(links));
        record_event(pool, tenant, &user.user_id, Event::ApprovalLinksSent, Some(&po_log_id), d).await;
    }
    // "queued", not "sent": the Python worker delivers (docs/rust-migration.md, outbox).
    Ok(json!({
        "po_log_id": po_log_id,
        "approvers": recipients.len(),
        "links": links,
        "queued": {"email": email_queued, "whatsapp": wa_queued},
    }))
}

pub async fn revoke(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    revoke_core(&state, &user, &po_log_id).await.map(ok)
}

/// Kill every open link of one order.
pub(crate) async fn revoke_core(state: &AppState, user: &CurrentUser, po_log_id: &str) -> Result<Value, ApiError> {
    let po_log_id = po_log_id.to_string();
    let pool = &state.pool;
    let tenant = &user.tenant_id;
    let po = po_approvals::get_po(pool, tenant, &po_log_id).await?;
    // Idempotent: no open request, or no open link, is "0 revoked", not an error.
    let revoked: Vec<(String,)> = sqlx::query_as(
        r#"UPDATE po_approval_links SET revoked_at = NOW(), revoked_by = $3, revoked_reason = 'revoked'
            WHERE tenant_id = $1 AND po_log_id = $2 AND used_at IS NULL AND revoked_at IS NULL
        RETURNING id"#,
    )
    .bind(tenant)
    .bind(&po_log_id)
    .bind(&user.user_id)
    .fetch_all(pool)
    .await?;
    if !revoked.is_empty() {
        let mut d = Map::new();
        d.insert("reference".into(), json!(format_po_number(po.po_number, &po_log_id)));
        d.insert("count".into(), json!(revoked.len()));
        record_event(pool, tenant, &user.user_id, Event::ApprovalLinksRevoked, Some(&po_log_id), d).await;
    }
    Ok(json!({"po_log_id": po_log_id, "revoked": revoked.len()}))
}

#[cfg(test)]
mod tests;
