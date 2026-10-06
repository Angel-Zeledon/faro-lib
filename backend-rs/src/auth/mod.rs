//! Authentication and role guards: `backend/auth/guards.py` and the trial
//! read-only half of `backend/entitlements/guards.py`.
//!
//! One `Authorization: Bearer` header, two kinds of caller. A person's JWT
//! and an integration's `sk_live_*` key both end up as the same
//! [`CurrentUser`], and every check runs in the order Python runs it, because
//! the order decides which error a caller sees when several apply.

pub mod api_key;
pub mod jwt;
pub mod session_policy;
pub mod warehouse_scope;

use std::sync::{Arc, Mutex};

use axum::http::HeaderMap;
use chrono::{DateTime, Utc};
use serde_json::json;

use crate::entitlements::{self, TenantRow};
use crate::error::ApiError;
use crate::pycompat::{py_list_repr, py_strip, timestamp_f64, truthy};
use crate::state::AppState;

#[derive(Debug, Clone)]
pub struct CurrentUser {
    pub user_id: String,
    pub tenant_id: String,
    pub role: String,
    /// Read by `require_verified_*`, which no wave-1 route needs yet; kept so
    /// the struct already carries everything `guards.CurrentUser` does.
    #[allow(dead_code)]
    pub email_verified: bool,
    pub api_key_id: Option<String>,
}

impl CurrentUser {
    pub fn is_machine(&self) -> bool {
        self.api_key_id.is_some()
    }
}

/// Who is acting on this request, readable by the middleware on the way out.
/// The Python app keeps the same two facts in the ASGI scope
/// (`backend/auth/actor_context.py`); this is the per-request equivalent,
/// inserted by the middleware and filled by the guard.
#[derive(Debug, Clone, Default)]
pub struct RequestActors(pub Arc<Mutex<ActorsInner>>);

#[derive(Debug, Default)]
pub struct ActorsInner {
    /// (tenant_id, "api_key:<id>") once a key passed every check.
    pub machine: Option<(String, String)>,
    /// (tenant_id, user_id) once a JWT passed every check.
    pub person: Option<(String, String)>,
}

impl RequestActors {
    pub fn machine(&self) -> Option<(String, String)> {
        self.0.lock().ok().and_then(|g| g.machine.clone())
    }
    pub fn person(&self) -> Option<(String, String)> {
        self.0.lock().ok().and_then(|g| g.person.clone())
    }
    fn set_machine(&self, tenant: &str, actor: &str) {
        if let Ok(mut g) = self.0.lock() {
            g.machine = Some((tenant.into(), actor.into()));
        }
    }
    fn set_person(&self, tenant: &str, user: &str) {
        if let Ok(mut g) = self.0.lock() {
            g.person = Some((tenant.into(), user.into()));
        }
    }
}

/// What `backend/api/public_surface.py::exposure` decides for a route. Rust
/// routes declare it statically next to their handler; the contract tests
/// check the decision against Python's by calling with real keys.
#[derive(Debug, Clone, Copy)]
pub enum Exposure {
    /// A key may call it; `true` means it needs a write key.
    Exposed { write: bool },
    /// No key may call it, with the reason Python reports in `error_params`.
    Internal(&'static str),
}

#[derive(Debug, Clone, Copy)]
pub struct RouteAuth {
    pub exposure: Exposure,
    /// The MCP endpoint is gated by the `mcp` feature instead of `api`.
    pub is_mcp: bool,
}

/// Starlette decodes headers as latin-1, so any byte string is a value.
fn latin1_header(headers: &HeaderMap, name: &str) -> String {
    headers
        .get(name)
        .map(|v| v.as_bytes().iter().map(|&b| b as char).collect::<String>())
        .unwrap_or_default()
}

/// `guards._BearerOrApiKey`: `X-API-Key: sk_live_...` is the credential when
/// no (or an empty) `Authorization` header is sent; anything else in
/// X-API-Key is ignored. Otherwise `fastapi.security.HTTPBearer`: the
/// credential, or the 401 it raises.
fn bearer_credential(headers: &HeaderMap) -> Result<String, ApiError> {
    let header_key = py_strip(&latin1_header(headers, "x-api-key")).to_string();
    let raw = latin1_header(headers, "authorization");
    if !header_key.is_empty() && api_key::looks_like_api_key(&header_key) && raw.is_empty() {
        return Ok(header_key);
    }
    let not_authenticated =
        || ApiError::http(401, "Not authenticated").with_header("www-authenticate", "Bearer");
    if raw.is_empty() {
        return Err(not_authenticated());
    }
    let (scheme, param) = match raw.split_once(' ') {
        Some((s, p)) => (s.to_string(), py_strip(p).to_string()),
        None => (raw.clone(), String::new()),
    };
    if scheme.is_empty() || param.is_empty() {
        return Err(not_authenticated());
    }
    if scheme.to_lowercase() != "bearer" {
        return Err(not_authenticated());
    }
    Ok(param)
}

fn now_f64() -> f64 {
    timestamp_f64(&Utc::now())
}

/// `get_current_user`.
pub async fn current_user(
    state: &AppState,
    headers: &HeaderMap,
    route: RouteAuth,
    actors: &RequestActors,
) -> Result<CurrentUser, ApiError> {
    let credential = bearer_credential(headers)?;
    if api_key::looks_like_api_key(&credential) {
        return authenticate_api_key(state, &credential, route, actors).await;
    }

    let payload = jwt::decode_token(&credential, state.settings.secret_key.as_bytes(), now_f64())
        .map_err(|r| ApiError::http(401, r.0))?;

    if payload.get("type").and_then(|v| v.as_str()) != Some("access") {
        return Err(ApiError::http(401, "Invalid token type"));
    }

    if let Some(jti) = payload.get("jti").filter(|v| truthy(v)) {
        let jti = jti.as_str().map(str::to_string).unwrap_or_else(|| jti.to_string());
        let revoked: Option<(i32,)> =
            sqlx::query_as("SELECT 1 FROM revoked_tokens WHERE jti = $1 AND expires_at > NOW()")
                .bind(&jti)
                .fetch_optional(&state.pool)
                .await?;
        if revoked.is_some() {
            return Err(ApiError::http(401, "Token has been revoked"));
        }
    }

    reject_if_predates_password_change(state, &payload).await?;

    // The tenant's session policy (maximum lifetime, idle timeout), in the
    // order backend/auth/guards.py runs it: after the password-change cut.
    session_policy::enforce_access_token(
        &state.pool, &payload, session_policy::is_background(headers), now_f64(),
    )
    .await?;

    // payload["sub"] / ["tenant_id"] / ["role"]: a KeyError in Python, i.e. a
    // 500. Only a token signed with our secret can get this far.
    let field = |k: &str| -> Result<String, ApiError> {
        match payload.get(k) {
            Some(serde_json::Value::String(s)) => Ok(s.clone()),
            Some(other) => Ok(other.to_string()),
            None => Err(ApiError::internal()),
        }
    };
    let user_id = field("sub")?;
    let tenant_id = field("tenant_id")?;
    let role = field("role")?;
    actors.set_person(&tenant_id, &user_id);
    Ok(CurrentUser {
        user_id,
        tenant_id,
        role,
        email_verified: payload.get("email_verified").map(truthy).unwrap_or(true),
        api_key_id: None,
    })
}

/// `_reject_if_predates_password_change`.
async fn reject_if_predates_password_change(
    state: &AppState,
    payload: &serde_json::Map<String, serde_json::Value>,
) -> Result<(), ApiError> {
    let Some(user_id) = payload.get("sub").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) else {
        return Ok(());
    };
    let row: Option<(Option<DateTime<Utc>>,)> =
        sqlx::query_as("SELECT sessions_invalid_before FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await?;
    let Some(cut) = row.and_then(|r| r.0) else { return Ok(()) };
    if let Some(iat) = payload.get("iat").and_then(|v| v.as_f64()) {
        if iat >= timestamp_f64(&cut) {
            return Ok(());
        }
    }
    Err(ApiError::http(401, "Session ended by a password change"))
}

/// `_authenticate_api_key`, check for check.
async fn authenticate_api_key(
    state: &AppState,
    credential: &str,
    route: RouteAuth,
    actors: &RequestActors,
) -> Result<CurrentUser, ApiError> {
    let pool = &state.pool;
    let key = api_key::resolve(pool, credential)
        .await?
        .ok_or_else(|| ApiError::http(401, "API key is invalid or expired"))?;

    if route.is_mcp {
        entitlements::ensure_feature(
            pool, state.settings.testing_mode, &key.tenant_id, "mcp",
            "Existing API keys stop working on plans without MCP access.",
        )
        .await?;
    } else {
        entitlements::ensure_feature(
            pool, state.settings.testing_mode, &key.tenant_id, "api",
            "This key still exists but the tenant's plan no longer includes API access, so it cannot be used.",
        )
        .await?;
    }

    let needs_write = match route.exposure {
        Exposure::Internal(reason) => {
            return Err(ApiError::app(
                "api_key_route_not_exposed",
                "This endpoint cannot be called with an API key.",
                403,
                json!({"reason": reason}),
            ));
        }
        Exposure::Exposed { write } => write,
    };
    // ROLE_SCOPE: analyst -> write, viewer -> read, anything else reads.
    let key_scope = if key.role == "analyst" { "write" } else { "read" };
    if needs_write && key_scope != "write" {
        return Err(ApiError::app(
            "api_key_scope_insufficient",
            "This endpoint writes and the API key is read-only. Use a write key.",
            403,
            json!({"required_scope": "write", "key_scope": key_scope}),
        ));
    }

    if !state.settings.testing_mode && !api_key::check_rate(pool, &key.id, &key.tenant_id).await {
        return Err(ApiError::http(
            429,
            format!(
                "Rate limit exceeded: {} requests per minute per API key, and the daily ceiling of this tenant's plan.",
                api_key::RATE_MAX_PER_MINUTE
            ),
        )
        .with_header("retry-after", &api_key::RATE_WINDOW_SECONDS.to_string()));
    }

    api_key::meter(pool, &key.id, &key.tenant_id, &key.name).await;
    api_key::touch(pool, &key.id).await;
    let actor = api_key::actor_id(&key.id);
    actors.set_machine(&key.tenant_id, &actor);
    Ok(CurrentUser {
        user_id: actor,
        tenant_id: key.tenant_id,
        role: key.role,
        email_verified: true,
        api_key_id: Some(key.id),
    })
}

/// `require_role(*roles)`.
pub fn require_role(user: &CurrentUser, roles: &[&str]) -> Result<(), ApiError> {
    if roles.contains(&user.role.as_str()) {
        return Ok(());
    }
    if user.is_machine() {
        return Err(ApiError::app(
            "api_key_scope_insufficient",
            "This endpoint writes and the API key is read-only. Use a write key.",
            403,
            json!({"required_scope": "write", "key_scope": "read"}),
        ));
    }
    let mut required: Vec<&str> = roles.to_vec();
    required.sort_unstable();
    Err(ApiError::app(
        "role_not_permitted",
        format!("Role '{}' not permitted. Required: {}", user.role, py_list_repr(roles)),
        403,
        json!({"role": user.role, "required": required}),
    ))
}

/// `require_analyst_or_above`: role, then the expired-trial read-only guard
/// (skipped in testing mode, like Python).
pub async fn require_analyst_or_above(state: &AppState, user: &CurrentUser) -> Result<(), ApiError> {
    require_role(user, &["admin", "analyst"])?;
    if state.settings.testing_mode {
        return Ok(());
    }
    let tenant = TenantRow::load(&state.pool, &user.tenant_id).await?.unwrap_or_default();
    if entitlements::is_read_only(&tenant, Utc::now()) {
        let ends = tenant.trial_ends_at.map(|d| crate::pycompat::isoformat_utc(&d));
        return Err(ApiError::http_dict(403, json!({"code": "TRIAL_EXPIRED", "trial_ends_at": ends})));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    fn headers(v: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert(axum::http::header::AUTHORIZATION, HeaderValue::from_str(v).unwrap());
        h
    }

    #[test]
    fn bearer_parsing_matches_httpbearer() {
        assert_eq!(bearer_credential(&headers("Bearer abc")).unwrap(), "abc");
        assert_eq!(bearer_credential(&headers("bearer   abc  ")).unwrap(), "abc");
        for bad in ["Basic abc", "Bearer", "Bearer ", "abc"] {
            let e = bearer_credential(&headers(bad)).unwrap_err();
            assert_eq!(e.status.as_u16(), 401);
            assert_eq!(e.code(), Some("unauthenticated"));
        }
        let e = bearer_credential(&HeaderMap::new()).unwrap_err();
        assert_eq!(e.headers[0].1, "Bearer");
    }

    #[test]
    fn x_api_key_header_only_carries_keys_and_loses_to_authorization() {
        let mut h = HeaderMap::new();
        h.insert("x-api-key", HeaderValue::from_static("  sk_live_abc "));
        assert_eq!(bearer_credential(&h).unwrap(), "sk_live_abc");
        // A JWT in X-API-Key is no credential at all.
        let mut jwt = HeaderMap::new();
        jwt.insert("x-api-key", HeaderValue::from_static("eyJ.a.b"));
        assert_eq!(bearer_credential(&jwt).unwrap_err().status.as_u16(), 401);
        // Authorization wins when both are sent.
        h.insert(axum::http::header::AUTHORIZATION, HeaderValue::from_static("Bearer tok"));
        assert_eq!(bearer_credential(&h).unwrap(), "tok");
        // An empty Authorization header counts as absent.
        h.insert(axum::http::header::AUTHORIZATION, HeaderValue::from_static(""));
        assert_eq!(bearer_credential(&h).unwrap(), "sk_live_abc");
    }

    fn user(role: &str, machine: bool) -> CurrentUser {
        CurrentUser {
            user_id: "u".into(),
            tenant_id: "t".into(),
            role: role.into(),
            email_verified: true,
            api_key_id: machine.then(|| "k".into()),
        }
    }

    #[test]
    fn role_guard_wording_and_params() {
        let e = require_role(&user("viewer", false), &["admin", "analyst"]).unwrap_err();
        assert_eq!(e.body["detail"], "Role 'viewer' not permitted. Required: ['admin', 'analyst']");
        assert_eq!(e.body["error_params"], json!({"role": "viewer", "required": ["admin", "analyst"]}));
        let e = require_role(&user("viewer", true), &["admin", "analyst"]).unwrap_err();
        assert_eq!(e.code(), Some("api_key_scope_insufficient"));
        assert!(require_role(&user("analyst", false), &["admin", "analyst"]).is_ok());
    }
}
