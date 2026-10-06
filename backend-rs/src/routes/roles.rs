//! Custom roles: definition, assignment and the caller's own view.
//!
//! These routes exist ONLY in Rust (no Python failover): the table and the
//! enforcement live in Python (`backend/auth/permissions.py`, the migration in
//! `backend/db/migrations.py`) and in `auth/permissions.rs`; this module is the
//! management surface.
//!
//! * `GET /roles`, `GET /roles/permissions`, `GET /roles/me`, `GET /roles/{id}`
//! * `POST /roles`, `PATCH /roles/{id}`, `DELETE /roles/{id}`
//! * `PUT /users/{user_id}/custom-role`
//!
//! Rules that make editing roles safe, each one tested end to end:
//! * a custom role NARROWS the built-in role, it never widens it;
//! * only a built-in admin may call these routes, and a user whose own custom
//!   role lacks `manage_roles` is refused by the guard before the handler;
//! * nobody grants what they do not hold: a role's permissions, the role being
//!   assigned, the role being replaced and the role being removed must all be
//!   inside the caller's own set (an unrestricted admin holds the whole
//!   catalogue, so "remove the role of somebody unrestricted" needs one);
//! * nobody edits, deletes or assigns the role they hold, or assigns one to
//!   themselves;
//! * the last active admin without a custom role cannot be given one;
//! * a role in use cannot be deleted (unassigning would silently WIDEN people);
//! * every change is serialised under the tenant advisory lock, so two admins
//!   cannot race each other past the last-admin or escalation checks.

use std::collections::BTreeSet;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::routing::{get, put};
use axum::{Extension, Json, Router};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};

use crate::activity::{record_event_with_reason, Event};
use crate::audit::{self, Note};
use crate::auth::permissions::{self, Effective};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, loc, Errors, Field, StrRules};

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'roles': custom roles define who may do what; a key must never edit its own limits"),
    is_mcp: false,
};

/// A sanity bound against abuse, not a plan ceiling.
const MAX_ROLES_PER_TENANT: i64 = 50;
const MAX_NAME: usize = 60;
const MAX_DESCRIPTION: usize = 300;
const REASON: &str = "changed_by_an_account_admin";

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/roles", get(list).post(create))
        .route("/api/v1/roles/permissions", get(catalogue))
        .route("/api/v1/roles/me", get(me))
        .route("/api/v1/roles/{role_id}", get(get_one).patch(update).delete(delete))
        .route("/api/v1/users/{user_id}/custom-role", put(assign))
}

// ── Shared helpers ───────────────────────────────────────────────────────────

type RoleRow = (String, String, String, Vec<String>, Option<String>, DateTime<Utc>, DateTime<Utc>, i64);

const ROLE_COLUMNS: &str = "r.id, r.name, r.description, r.permissions, r.created_by, r.created_at, r.updated_at,
     (SELECT COUNT(*) FROM users u WHERE u.custom_role_id = r.id AND u.tenant_id = r.tenant_id)";

fn sorted_known(perms: &[String]) -> Vec<String> {
    let set: BTreeSet<&String> = perms.iter().filter(|p| permissions::is_known(p)).collect();
    set.into_iter().cloned().collect()
}

fn role_json(r: &RoleRow) -> Value {
    json!({
        "id": r.0,
        "name": r.1,
        "description": r.2,
        // Names outside the catalogue are stored but never granted; showing
        // them would suggest otherwise.
        "permissions": sorted_known(&r.3),
        "created_by": r.4,
        "created_at": isoformat_utc(&r.5),
        "updated_at": isoformat_utc(&r.6),
        "user_count": r.7,
    })
}

fn snapshot(r: &RoleRow) -> Value {
    json!({"name": r.1, "description": r.2, "permissions": sorted_known(&r.3)})
}

/// A signed-in built-in admin. Writes also honour the expired-trial read-only
/// rule, like every other mutating route.
async fn admin(state: &AppState, headers: &HeaderMap, actors: &RequestActors, write: bool)
    -> Result<CurrentUser, ApiError>
{
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    if write {
        auth::require_analyst_or_above(state, &user).await?;
    }
    Ok(user)
}

/// The caller's own permissions, read now. Fail closed: unreadable = refused.
async fn caller_effective(state: &AppState, user: &CurrentUser) -> Result<Effective, ApiError> {
    permissions::effective_for(&state.pool, &user.tenant_id, &user.user_id).await.map_err(|e| {
        tracing::error!(error = %e, "[roles] could not read the caller's role");
        ApiError::app(
            "permission_check_failed",
            "Permissions could not be verified, so the request was refused.",
            403,
            json!({}),
        )
    })
}

fn escalation(excess: &BTreeSet<String>) -> ApiError {
    ApiError::app(
        "custom_role_escalation",
        "You cannot grant, change or remove permissions you do not hold yourself.",
        403,
        json!({"permissions": excess.iter().collect::<Vec<_>>()}),
    )
}

fn require_within(grantable: &BTreeSet<String>, wanted: &BTreeSet<String>) -> Result<(), ApiError> {
    let excess: BTreeSet<String> = wanted.difference(grantable).cloned().collect();
    if excess.is_empty() { Ok(()) } else { Err(escalation(&excess)) }
}

fn not_found() -> ApiError {
    ApiError::app("custom_role_not_found", "Role not found", 404, json!({}))
}

fn name_taken() -> ApiError {
    ApiError::app("custom_role_name_taken", "A role with that name already exists", 409, json!({}))
}

fn is_unique_violation(e: &sqlx::Error) -> bool {
    matches!(e, sqlx::Error::Database(d) if d.code().as_deref() == Some("23505"))
}

async fn load_role(
    conn: &mut sqlx::PgConnection,
    tenant_id: &str,
    role_id: &str,
    lock: bool,
) -> Result<Option<RoleRow>, sqlx::Error> {
    let sql = format!(
        "SELECT {ROLE_COLUMNS} FROM custom_roles r WHERE r.tenant_id = $1 AND r.id = $2{}",
        if lock { " FOR UPDATE" } else { "" }
    );
    sqlx::query_as::<_, RoleRow>(&sql).bind(tenant_id).bind(role_id).fetch_optional(conn).await
}

async fn event(state: &AppState, user: &CurrentUser, ev: Event, resource: &str, details: &[(&str, &str)]) {
    let mut d = Map::new();
    for (k, v) in details {
        d.insert((*k).to_string(), json!(v));
    }
    record_event_with_reason(&state.pool, &user.tenant_id, &user.user_id, ev, Some(resource), d, Some(REASON)).await;
}

// ── Body validation ──────────────────────────────────────────────────────────

struct RoleInput {
    name: Field<String>,
    description: Field<String>,
    permissions: Option<Vec<String>>,
}

fn validate(obj: &Map<String, Value>, require_all: bool) -> Result<RoleInput, ApiError> {
    let mut errs = Errors::default();
    let name_rules = StrRules { min_length: None, max_length: Some(MAX_NAME * 4), pattern: None };
    let mut name = validation::str_field(&mut errs, obj, &[], "name", require_all, false, &name_rules);
    if let Field::Value(s) = &name {
        let t = s.trim().to_string();
        if t.is_empty() || t.chars().count() > MAX_NAME || t.chars().any(char::is_control) {
            errs.push(
                "value_error",
                &loc(&[], "name"),
                format!("Value error, the name must be 1 to {MAX_NAME} visible characters"),
                &json!(s),
                Some(json!({"error": "invalid role name"})),
            );
            name = Field::Absent;
        } else {
            name = Field::Value(t);
        }
    }
    let desc_rules = StrRules { min_length: None, max_length: Some(MAX_DESCRIPTION), pattern: None };
    let description = validation::str_field(&mut errs, obj, &[], "description", false, false, &desc_rules);
    let permissions = match obj.get("permissions") {
        None if require_all => {
            errs.push("missing", &loc(&[], "permissions"), "Field required".into(), &Value::Object(obj.clone()), None);
            None
        }
        None => None,
        Some(Value::Array(items)) => {
            let mut out = Vec::new();
            let mut ok_all = true;
            for (i, it) in items.iter().enumerate() {
                match it.as_str() {
                    Some(s) => out.push(s.to_string()),
                    None => {
                        let mut at = loc(&[], "permissions");
                        at.push(json!(i));
                        errs.push("string_type", &at, "Input should be a valid string".into(), it, None);
                        ok_all = false;
                    }
                }
            }
            if ok_all { Some(out) } else { None }
        }
        Some(other) => {
            errs.push("list_type", &loc(&[], "permissions"), "Input should be a valid list".into(), other, None);
            None
        }
    };
    errs.into_result()?;
    Ok(RoleInput { name, description, permissions })
}

/// Unknown names are refused, never silently dropped: a typo must not become
/// a role that quietly grants less (or, for a rewrite, more) than intended.
fn checked_permissions(perms: &[String]) -> Result<BTreeSet<String>, ApiError> {
    if let Some(bad) = perms.iter().find(|p| !permissions::is_known(p)) {
        return Err(ApiError::app(
            "custom_role_unknown_permission",
            format!("Unknown permission '{bad}'"),
            422,
            json!({"permission": bad}),
        ));
    }
    Ok(perms.iter().cloned().collect())
}

// ── Reads ────────────────────────────────────────────────────────────────────

pub async fn catalogue(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    admin(&state, &headers, &actors, false).await?;
    let list: Vec<Value> = permissions::catalogue()
        .permissions
        .iter()
        .map(|(n, d)| json!({"name": n, "description": d}))
        .collect();
    Ok(ok(json!({"permissions": list})))
}

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &headers, &actors, false).await?;
    let sql = format!("SELECT {ROLE_COLUMNS} FROM custom_roles r WHERE r.tenant_id = $1 ORDER BY lower(r.name), r.id");
    let rows: Vec<RoleRow> = sqlx::query_as(&sql).bind(&user.tenant_id).fetch_all(&state.pool).await?;
    Ok(ok(json!({"roles": rows.iter().map(role_json).collect::<Vec<_>>()})))
}

pub async fn get_one(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(role_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &headers, &actors, false).await?;
    let mut conn = state.pool.acquire().await?;
    let row = load_role(&mut conn, &user.tenant_id, &role_id, false).await?.ok_or_else(not_found)?;
    Ok(ok(role_json(&row)))
}

/// What the signed-in person may do, for the frontend to hide what would only
/// answer 403. Any signed-in person (never a key: the tag is internal).
pub async fn me(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let eff = caller_effective(&state, &user).await?;
    let custom_role = match &eff.role_id {
        None => Value::Null,
        Some(id) => {
            let name: Option<(String,)> =
                sqlx::query_as("SELECT name FROM custom_roles WHERE id = $1 AND tenant_id = $2")
                    .bind(id)
                    .bind(&user.tenant_id)
                    .fetch_optional(&state.pool)
                    .await?;
            json!({"id": id, "name": name.map(|n| n.0)})
        }
    };
    Ok(ok(json!({
        "role": user.role,
        "restricted": eff.restricted,
        "custom_role": custom_role,
        "permissions": eff.grantable().into_iter().collect::<Vec<_>>(),
    })))
}

// ── Writes ───────────────────────────────────────────────────────────────────

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(ct, &bytes)?;
    let user = admin(&state, &headers, &actors, true).await?;
    let input = validate(&body_object(&body)?, true)?;
    let (Field::Value(name), Some(perms)) = (input.name, input.permissions) else { return Err(ApiError::internal()) };
    let description = match input.description { Field::Value(d) => d.trim().to_string(), _ => String::new() };
    let wanted = checked_permissions(&perms)?;

    let eff = caller_effective(&state, &user).await?;
    require_within(&eff.grantable(), &wanted)?;

    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let (existing,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM custom_roles WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_one(&mut *tx)
        .await?;
    if existing >= MAX_ROLES_PER_TENANT {
        return Err(ApiError::app(
            "custom_role_limit",
            "This account already has the maximum number of custom roles",
            409,
            json!({"max": MAX_ROLES_PER_TENANT}),
        ));
    }
    let inserted: Result<(String,), sqlx::Error> = sqlx::query_as(
        "INSERT INTO custom_roles (id, tenant_id, name, description, permissions, created_by)
         VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5) RETURNING id",
    )
    .bind(&user.tenant_id)
    .bind(&name)
    .bind(&description)
    .bind(wanted.iter().cloned().collect::<Vec<String>>())
    .bind(&user.user_id)
    .fetch_one(&mut *tx)
    .await;
    let (id,) = match inserted {
        Ok(r) => r,
        Err(e) if is_unique_violation(&e) => return Err(name_taken()),
        Err(e) => return Err(e.into()),
    };
    let row = load_role(&mut tx, &user.tenant_id, &id, false).await?.ok_or_else(ApiError::internal)?;
    tx.commit().await?;

    event(&state, &user, Event::CustomRoleCreated, &id, &[("role_name", &name)]).await;
    audit::record(&state, &actors, "POST", "/roles", None,
        Note { target_id: Some(id.clone()), label: Some(name.clone()), before: None, after: Some(snapshot(&row)) }, 200).await;
    Ok(ok(role_json(&row)))
}

pub async fn update(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(role_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(ct, &bytes)?;
    let user = admin(&state, &headers, &actors, true).await?;
    let input = validate(&body_object(&body)?, false)?;
    if matches!(input.name, Field::Absent) && matches!(input.description, Field::Absent) && input.permissions.is_none() {
        return Err(ApiError::app("custom_role_nothing_to_change", "Nothing to change", 422, json!({})));
    }
    let wanted = match &input.permissions { Some(p) => Some(checked_permissions(p)?), None => None };

    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let before = load_role(&mut tx, &user.tenant_id, &role_id, true).await?.ok_or_else(not_found)?;
    let eff = caller_effective(&state, &user).await?;
    if eff.role_id.as_deref() == Some(role_id.as_str()) {
        return Err(ApiError::app("custom_role_self_edit", "You cannot change the role you hold yourself", 403, json!({})));
    }
    let grantable = eff.grantable();
    // The role as it stands is above the caller when it holds more than they do.
    let old: BTreeSet<String> = sorted_known(&before.3).into_iter().collect();
    require_within(&grantable, &old)?;
    if let Some(w) = &wanted {
        require_within(&grantable, w)?;
    }

    let name = match &input.name { Field::Value(n) => n.clone(), _ => before.1.clone() };
    let description = match &input.description { Field::Value(d) => d.trim().to_string(), _ => before.2.clone() };
    let perms: Vec<String> = match &wanted { Some(w) => w.iter().cloned().collect(), None => sorted_known(&before.3) };
    let updated = sqlx::query(
        "UPDATE custom_roles SET name = $3, description = $4, permissions = $5, updated_at = NOW()
          WHERE tenant_id = $1 AND id = $2",
    )
    .bind(&user.tenant_id)
    .bind(&role_id)
    .bind(&name)
    .bind(&description)
    .bind(&perms)
    .execute(&mut *tx)
    .await;
    match updated {
        Ok(_) => {}
        Err(e) if is_unique_violation(&e) => return Err(name_taken()),
        Err(e) => return Err(e.into()),
    }
    let after = load_role(&mut tx, &user.tenant_id, &role_id, false).await?.ok_or_else(ApiError::internal)?;
    tx.commit().await?;

    event(&state, &user, Event::CustomRoleUpdated, &role_id, &[("role_name", &name)]).await;
    audit::record(&state, &actors, "PATCH", "/roles/{role_id}", Some(&role_id),
        Note { target_id: None, label: Some(name.clone()), before: Some(snapshot(&before)), after: Some(snapshot(&after)) }, 200).await;
    Ok(ok(role_json(&after)))
}

pub async fn delete(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(role_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &headers, &actors, true).await?;
    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let before = load_role(&mut tx, &user.tenant_id, &role_id, true).await?.ok_or_else(not_found)?;
    let eff = caller_effective(&state, &user).await?;
    if eff.role_id.as_deref() == Some(role_id.as_str()) {
        return Err(ApiError::app("custom_role_self_edit", "You cannot delete the role you hold yourself", 403, json!({})));
    }
    let old: BTreeSet<String> = sorted_known(&before.3).into_iter().collect();
    require_within(&eff.grantable(), &old)?;
    if before.7 > 0 {
        // Unassigning would widen every holder to their built-in role at once.
        return Err(ApiError::app(
            "custom_role_in_use",
            "The role is assigned to users; reassign them first",
            409,
            json!({"users": before.7}),
        ));
    }
    sqlx::query("DELETE FROM custom_roles WHERE tenant_id = $1 AND id = $2")
        .bind(&user.tenant_id)
        .bind(&role_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    event(&state, &user, Event::CustomRoleDeleted, &role_id, &[("role_name", &before.1)]).await;
    audit::record(&state, &actors, "DELETE", "/roles/{role_id}", Some(&role_id),
        Note { target_id: None, label: Some(before.1.clone()), before: Some(snapshot(&before)), after: None }, 200).await;
    Ok(ok(json!({"deleted": role_id})))
}

pub async fn assign(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(target_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(ct, &bytes)?;
    let user = admin(&state, &headers, &actors, true).await?;
    let obj = body_object(&body)?;
    let new_role: Option<String> = match obj.get("custom_role_id") {
        Some(Value::Null) => None,
        Some(Value::String(s)) if !s.trim().is_empty() => Some(s.trim().to_string()),
        other => {
            let mut errs = Errors::default();
            match other {
                None => errs.push("missing", &loc(&[], "custom_role_id"), "Field required".into(), &Value::Object(obj.clone()), None),
                Some(v) => errs.push("string_type", &loc(&[], "custom_role_id"),
                    "Input should be a valid string".into(), v, None),
            }
            return Err(ApiError::validation(errs.0));
        }
    };

    if target_id == user.user_id {
        return Err(ApiError::app("custom_role_self_assign", "You cannot change your own role", 403, json!({})));
    }

    let mut tx = state.pool.begin().await?;
    crate::limits::take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let target: Option<(String, String, String, Option<String>)> = sqlx::query_as(
        "SELECT email, role, status, custom_role_id FROM users WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
    )
    .bind(&target_id)
    .bind(&user.tenant_id)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((email, base_role, status, current_role_id)) = target else {
        return Err(ApiError::app("user_not_found", "User not found", 404, json!({})));
    };

    let new_row = match &new_role {
        None => None,
        Some(id) => Some(load_role(&mut tx, &user.tenant_id, id, false).await?.ok_or_else(not_found)?),
    };
    let old_row = match &current_role_id {
        None => None,
        Some(id) => load_role(&mut tx, &user.tenant_id, id, false).await?,
    };

    if current_role_id == new_role {
        tx.rollback().await?;
        return Ok(ok(assignment_json(&target_id, new_row.as_ref())));
    }

    let eff = caller_effective(&state, &user).await?;
    let grantable = eff.grantable();
    // What the target can do now (everything when unrestricted; a dangling
    // role id reads as nothing) and what they will be able to do after.
    let current_set: BTreeSet<String> = if current_role_id.is_none() {
        permissions::all_names().into_iter().collect()
    } else {
        old_row.as_ref().map(|r| sorted_known(&r.3).into_iter().collect()).unwrap_or_default()
    };
    let new_set: BTreeSet<String> = match &new_row {
        None => permissions::all_names().into_iter().collect(),
        Some(r) => sorted_known(&r.3).into_iter().collect(),
    };
    require_within(&grantable, &current_set)?;
    require_within(&grantable, &new_set)?;

    if new_row.is_some() && base_role == "admin" && current_role_id.is_none() && status == "active" {
        // The company keeps somebody who is not limited by a role.
        let (others,): (i64,) = sqlx::query_as(
            "SELECT COUNT(*) FROM users
              WHERE tenant_id = $1 AND role = 'admin' AND status = 'active'
                AND custom_role_id IS NULL AND id <> $2",
        )
        .bind(&user.tenant_id)
        .bind(&target_id)
        .fetch_one(&mut *tx)
        .await?;
        if others == 0 {
            return Err(ApiError::app(
                "custom_role_last_admin",
                "At least one active administrator must stay without a custom role.",
                409,
                json!({}),
            ));
        }
    }

    sqlx::query("UPDATE users SET custom_role_id = $1, updated_at = NOW() WHERE id = $2 AND tenant_id = $3")
        .bind(&new_role)
        .bind(&target_id)
        .bind(&user.tenant_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    let shown = |r: &Option<RoleRow>, id: &Option<String>| -> Value {
        match (r, id) {
            (Some(r), _) => json!({"custom_role": {"id": r.0, "name": r.1}}),
            (None, Some(id)) => json!({"custom_role": {"id": id, "name": null}}),
            (None, None) => json!({"custom_role": null}),
        }
    };
    let role_name = new_row.as_ref().map(|r| r.1.clone()).unwrap_or_default();
    event(&state, &user, Event::CustomRoleAssigned, &target_id,
        &[("email", &email), ("role_name", if role_name.is_empty() { "-" } else { &role_name })]).await;
    audit::record(&state, &actors, "PUT", "/users/{user_id}/custom-role", Some(&target_id),
        Note { target_id: None, label: Some(email.clone()),
               before: Some(shown(&old_row, &current_role_id)), after: Some(shown(&new_row, &new_role)) }, 200).await;
    Ok(ok(assignment_json(&target_id, new_row.as_ref())))
}

fn assignment_json(user_id: &str, role: Option<&RoleRow>) -> Value {
    json!({
        "user_id": user_id,
        "custom_role": role.map(|r| json!({"id": r.0, "name": r.1})),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set(v: &[&str]) -> BTreeSet<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn nobody_grants_what_they_do_not_hold() {
        let mine = set(&["view_users", "manage_roles"]);
        assert!(require_within(&mine, &set(&["view_users"])).is_ok());
        assert!(require_within(&mine, &set(&[])).is_ok());
        let e = require_within(&mine, &set(&["view_users", "manage_settings"])).unwrap_err();
        assert_eq!(e.code(), Some("custom_role_escalation"));
        assert_eq!(e.status.as_u16(), 403);
        assert_eq!(e.body["error_params"]["permissions"], json!(["manage_settings"]));
    }

    #[test]
    fn unknown_permissions_are_refused_not_dropped() {
        assert!(checked_permissions(&["view_users".into(), "view_users".into()]).is_ok());
        let e = checked_permissions(&["view_users".into(), "root".into()]).unwrap_err();
        assert_eq!(e.code(), Some("custom_role_unknown_permission"));
        assert_eq!(e.status.as_u16(), 422);
    }

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn create_requires_name_and_permissions() {
        assert!(validate(&obj(json!({})), true).is_err());
        assert!(validate(&obj(json!({"name": "Buyer"})), true).is_err());
        assert!(validate(&obj(json!({"permissions": []})), true).is_err());
        let ok = validate(&obj(json!({"name": "  Buyer  ", "permissions": ["view_inventory"]})), true).unwrap();
        assert_eq!(ok.name, Field::Value("Buyer".into()));
        // A patch may carry any subset.
        assert!(validate(&obj(json!({"description": "x"})), false).is_ok());
    }

    #[test]
    fn names_are_visible_and_bounded() {
        for bad in [json!(""), json!("   "), json!("a\u{0}b"), json!("x".repeat(61)), json!(5)] {
            assert!(validate(&obj(json!({"name": bad, "permissions": []})), true).is_err(), "{bad}");
        }
        assert!(validate(&obj(json!({"name": "x".repeat(60), "permissions": []})), true).is_ok());
    }

    #[test]
    fn permissions_must_be_a_list_of_strings() {
        assert!(validate(&obj(json!({"name": "A", "permissions": "view_users"})), true).is_err());
        assert!(validate(&obj(json!({"name": "A", "permissions": [1]})), true).is_err());
    }

    #[test]
    fn dangling_names_never_show_as_granted() {
        assert_eq!(sorted_known(&["view_users".into(), "root".into(), "view_users".into()]), ["view_users"]);
    }
}
