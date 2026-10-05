//! Cross-cutting request handling, in the order `backend/main.py` stacks it
//! (outermost first):
//!
//! 1. `reject_nul_in_path`: a `%00` in the path is a 400 `malformed_path`.
//! 2. request id + access log (`RequestLoggerMiddleware`, plus an
//!    `X-Request-Id` the Python app does not have; additive only).
//! 3. `MachineAuditMiddleware`: every write an API key performs leaves an
//!    `api_write` row in `activity_logs`, success or failure, except refusals
//!    by the auth layer (401/403/429) and the read-only MCP endpoint.
//!
//! The actor is carried in a per-request [`RequestActors`] slot that the auth
//! guard fills, which is the Rust equivalent of the ASGI-scope trick in
//! `backend/auth/actor_context.py`.

use std::time::Instant;

use axum::extract::{Request, State};
use axum::http::{HeaderValue, Method};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use percent_encoding::percent_decode_str;
use serde_json::json;

use crate::activity::log_action;
use crate::auth::RequestActors;
use crate::error::ApiError;
use crate::state::AppState;

const MUTATIONS: [Method; 4] = [Method::POST, Method::PUT, Method::PATCH, Method::DELETE];
const NOT_A_MUTATION_DESPITE_THE_VERB: [&str; 1] = ["/api/v1/mcp"];
const NOT_AN_ATTEMPT: [u16; 3] = [401, 403, 429];

pub async fn reject_nul_in_path(req: Request, next: Next) -> Response {
    let decoded = percent_decode_str(req.uri().path()).collect::<Vec<u8>>();
    if decoded.contains(&0u8) {
        return ApiError::app("malformed_path", "The request path contains a NUL byte.", 400, json!({}))
            .into_response();
    }
    next.run(req).await
}

pub async fn request_context(State(state): State<AppState>, mut req: Request, next: Next) -> Response {
    let started = Instant::now();
    let method = req.method().clone();
    let path = req.uri().path().to_string();
    let request_id = req
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .filter(|v| !v.is_empty() && v.len() <= 128)
        .map(str::to_string)
        .unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
    let actors = RequestActors::default();
    req.extensions_mut().insert(actors.clone());

    let mut response = next.run(req).await;
    let status = response.status().as_u16();
    let elapsed_ms = (started.elapsed().as_secs_f64() * 10_000.0).round() / 10.0;

    let machine = actors.machine();
    let person = actors.person();
    let tenant = machine
        .as_ref()
        .or(person.as_ref())
        .map(|a| a.0.clone())
        .unwrap_or_else(|| "-".into());
    let key = machine
        .as_ref()
        .map(|m| m.1.trim_start_matches("api_key:").to_string());
    tracing::info!(
        target: "access",
        request_id = %request_id,
        "{method} {path} -> {status} [{elapsed_ms}ms] tenant={tenant}{}",
        key.as_ref().map(|k| format!(" key={k}")).unwrap_or_default()
    );

    // MachineAuditMiddleware
    if MUTATIONS.contains(&method)
        && !NOT_A_MUTATION_DESPITE_THE_VERB.contains(&path.as_str())
        && !NOT_AN_ATTEMPT.contains(&status)
    {
        if let Some((tenant_id, actor_id)) = machine {
            let context = json!({"status_code": status});
            let outcome = if status < 400 { "success" } else { "error" };
            if let Err(e) = log_action(&state.pool, &tenant_id, &actor_id, "api_write",
                Some(&format!("{method} {path}")), &context, outcome).await
            {
                tracing::warn!(error = %e, "machine audit not recorded");
            }
        }
    }

    if let Ok(v) = HeaderValue::from_str(&request_id) {
        response.headers_mut().insert("x-request-id", v);
    }
    response
}
