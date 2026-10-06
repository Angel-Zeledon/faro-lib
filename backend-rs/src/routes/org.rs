//! Organization links and grants: `/org/overview`, `/org/links`,
//! `/org/links/accept`, `/org/links/{id}`, `/org/links/{id}/members[/{user}]`.
//!
//! Rust only, no Python route behind it. The rules this module enforces are the
//! grant model in `backend/organizations/__init__.py`:
//!
//! * a link needs an administrator on BOTH sides: the holding's admin mints a
//!   one-time code (only its SHA-256 is stored, the code is shown once), the
//!   subsidiary's admin redeems it from the subsidiary's own session, so the
//!   child tenant is always the redeeming caller's tenant, never a field;
//! * one level only, and a subsidiary has one live parent;
//! * either side can end the link at any moment, which deletes every grant;
//! * every state change runs under one advisory lock, re-reads what it depends
//!   on inside the transaction, and writes an activity row on each side that
//!   is told (the other side's row names nobody).
//!
//! Every handler authenticates a signed-in administrator (never a key), checks
//! the role in the database as well as in the token, and takes the tenant from
//! the verified identity.

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgConnection, PgPool};

use crate::activity::{generate_id, record_event_with_reason, Event};
use crate::auth::{self, RequestActors};
use crate::error::ApiError;
use crate::org::scope::{self, ROUTE};
use crate::pycompat::{isoformat_utc, py_strip};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, str_field, Errors, Field, StrRules};

const LABEL_RULES: StrRules =
    StrRules { min_length: Some(1), max_length: Some(scope::MAX_LABEL_CHARS), pattern: None };

/// One lock for every link and grant change: low volume, and it makes "check
/// the state, then change it" one step. Held until the transaction ends.
const LOCK_SQL: &str = "SELECT pg_advisory_xact_lock(hashtext('org_links'))";

fn ts(v: Option<DateTime<Utc>>) -> Value {
    v.map(|d| json!(isoformat_utc(&d))).unwrap_or(Value::Null)
}

// ── Errors ───────────────────────────────────────────────────────────────────

fn link_not_found() -> ApiError {
    ApiError::app("org_link_not_found", "Link not found", 404, json!({}))
}
fn code_invalid() -> ApiError {
    ApiError::app("org_link_code_invalid", "The link code is not valid or has expired", 404, json!({}))
}
fn link_not_active() -> ApiError {
    ApiError::app("org_link_not_active", "The link is not active", 409, json!({}))
}
fn member_not_found() -> ApiError {
    ApiError::app("org_member_not_found", "User not found", 404, json!({}))
}
fn nesting() -> ApiError {
    ApiError::app(
        "org_nesting_not_allowed",
        "An organization has one level: a subsidiary cannot also be a holding",
        409,
        json!({}),
    )
}
fn not_allowed() -> ApiError {
    ApiError::app("org_link_not_allowed", "This account cannot take part in an organization", 409, json!({}))
}

// ── Shared checks ────────────────────────────────────────────────────────────

/// The caller's tenant may take part: active, and not a trial tenant (a
/// throwaway account that a reaper erases in 24 hours must not be bridged to a
/// real holding).
async fn ensure_eligible(conn: &mut PgConnection, tenant_id: &str) -> Result<(), ApiError> {
    let row: Option<(String, String)> = sqlx::query_as("SELECT status, tier FROM tenants WHERE id = $1")
        .bind(tenant_id)
        .fetch_optional(&mut *conn)
        .await?;
    match row {
        Some((status, tier)) if status == "active" && tier != crate::entitlements::DEMO => Ok(()),
        _ => Err(not_allowed()),
    }
}

async fn has_active(conn: &mut PgConnection, sql: &'static str, tenant_id: &str) -> Result<bool, ApiError> {
    let row: Option<(i32,)> = sqlx::query_as(sql).bind(tenant_id).fetch_optional(&mut *conn).await?;
    Ok(row.is_some())
}

const IS_ACTIVE_CHILD: &str = "SELECT 1 FROM org_links WHERE child_tenant_id = $1 AND status = 'active' LIMIT 1";
const IS_ACTIVE_PARENT: &str = "SELECT 1 FROM org_links WHERE parent_tenant_id = $1 AND status = 'active' LIMIT 1";

async fn event(
    pool: &PgPool,
    tenant_id: &str,
    actor: &str,
    ev: Event,
    link_id: &str,
    label: Option<&str>,
    member: Option<&str>,
    reason: Option<&str>,
) {
    let mut d = Map::new();
    if let Some(l) = label {
        d.insert("label".into(), json!(l));
    }
    if let Some(m) = member {
        d.insert("member".into(), json!(m));
    }
    record_event_with_reason(pool, tenant_id, actor, ev, Some(link_id), d, reason).await;
}

fn label_from(body: &validation::Body) -> Result<String, ApiError> {
    let obj = body_object(body)?;
    let mut errs = Errors::default();
    let at = vec![json!("body"), json!("label")];
    let f = str_field(&mut errs, &obj, &[json!("body")], "label", true, false, &LABEL_RULES);
    let label = match f {
        Field::Value(s) => {
            let t = py_strip(&s).to_string();
            if t.is_empty() {
                errs.push("string_too_short", &at, "String should have at least 1 character".into(), &json!(s),
                    Some(json!({"min_length": 1})));
                None
            } else {
                Some(t)
            }
        }
        _ => None,
    };
    errs.into_result()?;
    label.ok_or_else(ApiError::internal)
}

// ── POST /org/links ──────────────────────────────────────────────────────────

pub async fn create_link(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let (user, _) = scope::admin(&state, &actors, &headers).await?;
    let label = label_from(&body)?;

    let code = scope::new_code()?;
    let link_id = generate_id("olnk");
    let mut tx = state.pool.begin().await?;
    sqlx::query(LOCK_SQL).execute(&mut *tx).await?;
    ensure_eligible(&mut tx, &user.tenant_id).await?;
    if has_active(&mut tx, IS_ACTIVE_CHILD, &user.tenant_id).await? {
        return Err(nesting());
    }
    let (pending,): (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM org_links WHERE parent_tenant_id = $1 AND status = 'pending' AND code_expires_at > NOW()",
    )
    .bind(&user.tenant_id)
    .fetch_one(&mut *tx)
    .await?;
    if pending >= scope::MAX_PENDING_LINKS {
        return Err(ApiError::app(
            "org_too_many_pending_links",
            "Too many link codes are waiting to be used; revoke one first",
            409,
            json!({"max": scope::MAX_PENDING_LINKS}),
        ));
    }
    let (expires,): (DateTime<Utc>,) = sqlx::query_as(
        "INSERT INTO org_links (id, parent_tenant_id, label, status, code_hash, code_expires_at, created_by)
         VALUES ($1, $2, $3, 'pending', $4, NOW() + ($5::bigint * INTERVAL '1 day'), $6)
         RETURNING code_expires_at",
    )
    .bind(&link_id)
    .bind(&user.tenant_id)
    .bind(&label)
    .bind(scope::hash_code(&code))
    .bind(scope::CODE_TTL_DAYS)
    .bind(&user.user_id)
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;

    // The label is safe to log; the code never is.
    tracing::info!("[org] link created id={} tenant={}", link_id, user.tenant_id);
    event(&state.pool, &user.tenant_id, &user.user_id, Event::OrgLinkCreated, &link_id, Some(&label), None, None).await;
    Ok(ok(json!({
        "id": link_id,
        "label": label,
        "status": "pending",
        "code": code,
        "expires_at": isoformat_utc(&expires),
    })))
}

// ── POST /org/links/accept ───────────────────────────────────────────────────

pub async fn accept(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let (user, _) = scope::admin(&state, &actors, &headers).await?;
    let obj = body_object(&body)?;
    let mut errs = Errors::default();
    let code = match str_field(&mut errs, &obj, &[json!("body")], "code", true, false,
        &StrRules { min_length: Some(1), max_length: Some(200), pattern: None })
    {
        Field::Value(s) => Some(py_strip(&s).to_string()),
        _ => None,
    };
    errs.into_result()?;
    let code = code.ok_or_else(ApiError::internal)?;
    // A string that cannot be one of our codes never reaches the database.
    if !scope::looks_like_code(&code) {
        return Err(code_invalid());
    }

    let mut tx = state.pool.begin().await?;
    sqlx::query(LOCK_SQL).execute(&mut *tx).await?;
    let link: Option<(String, String, String)> = sqlx::query_as(
        "SELECT id, parent_tenant_id, label FROM org_links
          WHERE code_hash = $1 AND status = 'pending' AND code_expires_at > NOW()
          FOR UPDATE",
    )
    .bind(scope::hash_code(&code))
    .fetch_optional(&mut *tx)
    .await?;
    let Some((link_id, parent_id, label)) = link else { return Err(code_invalid()) };

    if parent_id == user.tenant_id {
        return Err(ApiError::app("org_link_self", "A tenant cannot be linked to itself", 409, json!({})));
    }
    ensure_eligible(&mut tx, &user.tenant_id).await?;
    // The holding must still be eligible and still a top-level tenant.
    if ensure_eligible(&mut tx, &parent_id).await.is_err() {
        return Err(code_invalid());
    }
    if has_active(&mut tx, IS_ACTIVE_CHILD, &user.tenant_id).await? {
        return Err(ApiError::app(
            "org_already_linked",
            "This account already belongs to an organization",
            409,
            json!({}),
        ));
    }
    if has_active(&mut tx, IS_ACTIVE_PARENT, &user.tenant_id).await?
        || has_active(&mut tx, IS_ACTIVE_CHILD, &parent_id).await?
    {
        return Err(nesting());
    }
    let parent_name: Option<(String,)> = sqlx::query_as("SELECT name FROM tenants WHERE id = $1")
        .bind(&parent_id)
        .fetch_optional(&mut *tx)
        .await?;
    let (parent_name,) = parent_name.ok_or_else(code_invalid)?;

    sqlx::query(
        "UPDATE org_links
            SET child_tenant_id = $1, status = 'active', code_hash = NULL, code_expires_at = NULL,
                accepted_by = $2, accepted_at = NOW()
          WHERE id = $3",
    )
    .bind(&user.tenant_id)
    .bind(&user.user_id)
    .bind(&link_id)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;

    tracing::info!("[org] link accepted id={} child={}", link_id, user.tenant_id);
    // The holding's feed names no person of the subsidiary; the subsidiary's own
    // feed names its own admin and nothing of the holding's label.
    event(&state.pool, &parent_id, "system", Event::OrgLinkAccepted, &link_id, Some(&label), None, None).await;
    event(&state.pool, &user.tenant_id, &user.user_id, Event::OrgLinkAccepted, &link_id, None, None, None).await;
    Ok(ok(json!({"link_id": link_id, "parent_name": parent_name, "status": "active"})))
}

// ── DELETE /org/links/{id} ───────────────────────────────────────────────────

pub async fn revoke(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(link_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, _) = scope::admin(&state, &actors, &headers).await?;
    let mut tx = state.pool.begin().await?;
    sqlx::query(LOCK_SQL).execute(&mut *tx).await?;
    // A link the caller's tenant is not part of is a link that does not exist.
    let row: Option<(String, Option<String>, String, String)> = sqlx::query_as(
        "SELECT parent_tenant_id, child_tenant_id, label, status FROM org_links
          WHERE id = $1 AND (parent_tenant_id = $2 OR child_tenant_id = $2)
          FOR UPDATE",
    )
    .bind(&link_id)
    .bind(&user.tenant_id)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((parent, child, label, status)) = row else { return Err(link_not_found()) };
    if status == "revoked" {
        return Ok(ok(json!({"id": link_id, "status": "revoked", "changed": false})));
    }
    let by_parent = parent == user.tenant_id;
    let side = if by_parent { "parent" } else { "child" };
    sqlx::query("DELETE FROM org_link_grants WHERE link_id = $1").bind(&link_id).execute(&mut *tx).await?;
    sqlx::query(
        "UPDATE org_links
            SET status = 'revoked', code_hash = NULL, code_expires_at = NULL,
                revoked_by = $1, revoked_at = NOW(), revoked_side = $2
          WHERE id = $3",
    )
    .bind(&user.user_id)
    .bind(side)
    .bind(&link_id)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;

    tracing::info!("[org] link revoked id={} by={}", link_id, side);
    let reason = if by_parent { "org_revoked_by_parent" } else { "org_revoked_by_child" };
    if by_parent {
        event(&state.pool, &parent, &user.user_id, Event::OrgLinkRevoked, &link_id, Some(&label), None, Some(reason)).await;
        if let Some(child) = child.filter(|_| status == "active") {
            event(&state.pool, &child, "system", Event::OrgLinkRevoked, &link_id, None, None, Some(reason)).await;
        }
    } else {
        event(&state.pool, &user.tenant_id, &user.user_id, Event::OrgLinkRevoked, &link_id, None, None, Some(reason)).await;
        event(&state.pool, &parent, "system", Event::OrgLinkRevoked, &link_id, Some(&label), None, Some(reason)).await;
    }
    Ok(ok(json!({"id": link_id, "status": "revoked", "changed": true})))
}

// ── PUT / DELETE /org/links/{id}/members/{user_id} ───────────────────────────

/// The link, if it is the caller's own as a holding, locked for this change.
async fn own_link_for_update(
    tx: &mut PgConnection,
    link_id: &str,
    tenant_id: &str,
) -> Result<(String, String), ApiError> {
    let row: Option<(String, String)> = sqlx::query_as(
        "SELECT status, label FROM org_links WHERE id = $1 AND parent_tenant_id = $2 FOR UPDATE",
    )
    .bind(link_id)
    .bind(tenant_id)
    .fetch_optional(&mut *tx)
    .await?;
    row.ok_or_else(link_not_found)
}

async fn member_email(tx: &mut PgConnection, user_id: &str, tenant_id: &str) -> Result<String, ApiError> {
    let row: Option<(String,)> =
        sqlx::query_as("SELECT email FROM users WHERE id = $1 AND tenant_id = $2 AND status = 'active'")
            .bind(user_id)
            .bind(tenant_id)
            .fetch_optional(&mut *tx)
            .await?;
    row.map(|r| r.0).ok_or_else(member_not_found)
}

pub async fn grant(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path((link_id, member_id)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, _) = scope::admin(&state, &actors, &headers).await?;
    let mut tx = state.pool.begin().await?;
    sqlx::query(LOCK_SQL).execute(&mut *tx).await?;
    let (status, label) = own_link_for_update(&mut tx, &link_id, &user.tenant_id).await?;
    if status != "active" {
        return Err(link_not_active());
    }
    let email = member_email(&mut tx, &member_id, &user.tenant_id).await?;
    let inserted = sqlx::query(
        "INSERT INTO org_link_grants (link_id, user_id, granted_by) VALUES ($1, $2, $3)
         ON CONFLICT (link_id, user_id) DO NOTHING",
    )
    .bind(&link_id)
    .bind(&member_id)
    .bind(&user.user_id)
    .execute(&mut *tx)
    .await?
    .rows_affected();
    tx.commit().await?;
    if inserted > 0 {
        event(&state.pool, &user.tenant_id, &user.user_id, Event::OrgGrantAdded, &link_id, Some(&label), Some(&email), None).await;
    }
    Ok(ok(json!({"link_id": link_id, "user_id": member_id, "changed": inserted > 0})))
}

pub async fn ungrant(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path((link_id, member_id)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, _) = scope::admin(&state, &actors, &headers).await?;
    let mut tx = state.pool.begin().await?;
    sqlx::query(LOCK_SQL).execute(&mut *tx).await?;
    let (_status, label) = own_link_for_update(&mut tx, &link_id, &user.tenant_id).await?;
    // The person may have been deactivated since: removing their grant must
    // still work, so the email is read without the active filter.
    let email: Option<(String,)> = sqlx::query_as("SELECT email FROM users WHERE id = $1 AND tenant_id = $2")
        .bind(&member_id)
        .bind(&user.tenant_id)
        .fetch_optional(&mut *tx)
        .await?;
    let (email,) = email.ok_or_else(member_not_found)?;
    let removed = sqlx::query("DELETE FROM org_link_grants WHERE link_id = $1 AND user_id = $2")
        .bind(&link_id)
        .bind(&member_id)
        .execute(&mut *tx)
        .await?
        .rows_affected();
    tx.commit().await?;
    if removed > 0 {
        event(&state.pool, &user.tenant_id, &user.user_id, Event::OrgGrantRemoved, &link_id, Some(&label), Some(&email), None).await;
    }
    Ok(ok(json!({"link_id": link_id, "user_id": member_id, "changed": removed > 0})))
}

// ── GET /org/links, GET /org/links/{id}/members, GET /org/overview ──────────

type ParentRow = (String, String, String, bool, Option<DateTime<Utc>>, DateTime<Utc>, Option<DateTime<Utc>>,
                  Option<DateTime<Utc>>, Option<String>, Option<String>, i64);
type ChildRow = (String, String, Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<String>, Option<String>);

pub async fn list_links(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, _) = scope::admin(&state, &actors, &headers).await?;
    let parent: Vec<ParentRow> = sqlx::query_as(
        "SELECT l.id, l.label, l.status, (l.status = 'pending' AND l.code_expires_at <= NOW()),
                l.code_expires_at, l.created_at, l.accepted_at, l.revoked_at, l.revoked_side,
                CASE WHEN l.status = 'active' THEN t.name END,
                (SELECT COUNT(*) FROM org_link_grants g WHERE g.link_id = l.id)::bigint
           FROM org_links l LEFT JOIN tenants t ON t.id = l.child_tenant_id
          WHERE l.parent_tenant_id = $1
          ORDER BY l.created_at DESC, l.id LIMIT 200",
    )
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    // The subsidiary's view of the link: that it exists, who the holding is by
    // name (it must know whom it gave access to), since when. Never the label,
    // who in the holding holds a grant, or any sibling.
    let child: Vec<ChildRow> = sqlx::query_as(
        "SELECT l.id, l.status, l.accepted_at, l.revoked_at, l.revoked_side,
                CASE WHEN l.status = 'active' THEN p.name END
           FROM org_links l JOIN tenants p ON p.id = l.parent_tenant_id
          WHERE l.child_tenant_id = $1
          ORDER BY l.created_at DESC, l.id LIMIT 20",
    )
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    let as_parent: Vec<Value> = parent
        .into_iter()
        .map(|(id, label, status, expired, expires, created, accepted, revoked, side, child_name, grants)| {
            json!({
                "id": id, "label": label, "status": status, "expired": expired,
                "code_expires_at": ts(if status == "pending" { expires } else { None }),
                "created_at": ts(Some(created)), "accepted_at": ts(accepted), "revoked_at": ts(revoked),
                "revoked_side": side, "subsidiary_name": child_name, "grants": grants,
            })
        })
        .collect();
    let as_child: Vec<Value> = child
        .into_iter()
        .map(|(id, status, accepted, revoked, side, parent_name)| {
            json!({
                "id": id, "status": status, "accepted_at": ts(accepted), "revoked_at": ts(revoked),
                "revoked_side": side, "parent_name": parent_name,
            })
        })
        .collect();
    Ok(ok(json!({"as_parent": as_parent, "as_child": as_child})))
}

pub async fn list_members(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(link_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, _) = scope::admin(&state, &actors, &headers).await?;
    let own: Option<(String,)> =
        sqlx::query_as("SELECT status FROM org_links WHERE id = $1 AND parent_tenant_id = $2")
            .bind(&link_id)
            .bind(&user.tenant_id)
            .fetch_optional(&state.pool)
            .await?;
    own.ok_or_else(link_not_found)?;
    let rows: Vec<(String, String, Option<String>, String, DateTime<Utc>)> = sqlx::query_as(
        "SELECT u.id, u.email, u.full_name, u.status, g.granted_at
           FROM org_link_grants g JOIN users u ON u.id = g.user_id AND u.tenant_id = $2
          WHERE g.link_id = $1 ORDER BY g.granted_at, u.id",
    )
    .bind(&link_id)
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    let items: Vec<Value> = rows
        .into_iter()
        .map(|(id, email, name, status, at)| {
            json!({"user_id": id, "email": email, "full_name": name, "status": status, "granted_at": ts(Some(at))})
        })
        .collect();
    Ok(ok(Value::Array(items)))
}

fn error_code(e: &ApiError) -> Option<&str> {
    e.body.get("error_code").and_then(Value::as_str)
}

/// What the screen needs to decide what to show: whether the caller may read
/// consolidated views (and which tenants), and whether they administer links.
pub async fn overview(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    // A key never gets here (the route is internal); `resolve` refuses one anyway.
    let person = scope::live_person(&state.pool, &user).await?;
    let can_manage = user.role == "admin"
        && person.as_ref().is_some_and(|p| p.status == "active" && p.role == "admin");
    match scope::resolve(&state, &user).await {
        Ok(s) => Ok(ok(json!({
            "entitled": true,
            "reason": null,
            "can_manage": can_manage,
            "tenants": s.members.iter().map(|m| m.to_json()).collect::<Vec<_>>(),
            "unavailable": s.unavailable_json(),
        }))),
        Err(e) if matches!(error_code(&e), Some("org_not_entitled") | Some("warehouse_scope_company_totals")) => {
            Ok(ok(json!({
                "entitled": false,
                "reason": error_code(&e),
                "can_manage": can_manage,
                "tenants": [],
                "unavailable": [],
            })))
        }
        Err(e) => Err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_must_have_content_after_stripping() {
        let b = |v: Value| validation::Body::Json(v);
        assert_eq!(label_from(&b(json!({"label": "  Norte  "}))).unwrap(), "Norte");
        for bad in [json!({"label": "   "}), json!({"label": ""}), json!({}), json!({"label": 5}),
            json!({"label": "x".repeat(81)})]
        {
            let e = label_from(&b(bad)).unwrap_err();
            assert_eq!(e.status.as_u16(), 422);
        }
        assert!(label_from(&validation::Body::Missing).is_err());
    }

    #[test]
    fn the_lock_is_one_name_and_the_nesting_queries_are_reads() {
        assert!(LOCK_SQL.contains("pg_advisory_xact_lock") && LOCK_SQL.contains("'org_links'"));
        for q in [IS_ACTIVE_CHILD, IS_ACTIVE_PARENT] {
            assert!(q.starts_with("SELECT") && q.contains("status = 'active'"));
        }
    }

    #[test]
    fn route_errors_carry_codes_the_frontend_translates() {
        let cases = [
            (link_not_found(), 404, "org_link_not_found"),
            (code_invalid(), 404, "org_link_code_invalid"),
            (link_not_active(), 409, "org_link_not_active"),
            (member_not_found(), 404, "org_member_not_found"),
            (nesting(), 409, "org_nesting_not_allowed"),
            (not_allowed(), 409, "org_link_not_allowed"),
        ];
        for (e, status, code) in cases {
            assert_eq!(e.status.as_u16(), status);
            assert_eq!(e.code(), Some(code));
        }
    }
}
