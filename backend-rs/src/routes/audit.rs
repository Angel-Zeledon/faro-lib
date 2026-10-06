//! The audit trail reads: `GET /audit`, `GET /audit/filters`,
//! `GET /audit/export` (`backend/api/v1/audit.py`).
//!
//! Admin only, never a key (`audit` is an internal tag), and refused to an
//! admin limited to some warehouses (`warehouse_scope_company_totals`): the
//! trail covers every warehouse and its rows carry none to filter on.
//!
//! NOT migrated from the same Python file: `GET /sessions/{id}/manifest` and
//! `GET /training/run-durations` (tag `sessions`, lineage manifests, not the
//! audit trail).

use axum::body::Body as HttpBody;
use axum::extract::{RawQuery, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use chrono::Local;
use serde_json::{json, Value};

use crate::audit::service::{self, ExportPhase, ExportState, Filters};
use crate::audit::{self as trail, Note};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::query::{self, PyInt, Query};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::Errors;

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'audit': the tenant's audit trail names its people; an administrator reads it on the screen",
    ),
    is_mcp: false,
};

fn status_pattern(s: &str) -> bool {
    s == "success" || s == "error"
}

/// `_filters`, validated; errors are collected, not raised (FastAPI resolves
/// the auth dependency after it and an auth failure still wins).
fn filters(errs: &mut Errors, q: &Query) -> Filters {
    Filters {
        actor: query::opt_str(errs, q, "actor", None),
        target_type: query::opt_str(errs, q, "target_type", None),
        action: query::opt_str(errs, q, "action", None),
        target_id: query::opt_str(errs, q, "target_id", Some(200)),
        status: query::opt_pattern(errs, q, "status", "^(success|error)$", status_pattern),
        date_from: query::opt_date(errs, q, "date_from"),
        date_to: query::opt_date(errs, q, "date_to"),
    }
}

/// Auth in FastAPI's order: `require_admin` (a key is refused inside it as
/// not exposed), then the company-wide refusal in the handler body.
async fn admin(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    Ok(user)
}

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    let mut errs = Errors::default();
    let f = filters(&mut errs, &q);
    let user = admin(&state, &actors, &headers).await?;
    let limit = query::int_param(&mut errs, &q, "limit", 50, Some(1), Some(service::MAX_PAGE));
    let offset = query::int_param(&mut errs, &q, "offset", 0, Some(0), None);
    errs.into_result()?;
    let (Some(PyInt::Small(limit)), Some(offset)) = (limit, offset) else { return Err(ApiError::internal()) };
    // An offset pydantic accepts but Postgres cannot hold is a 500 there too.
    let PyInt::Small(offset) = offset else { return Err(ApiError::internal()) };

    wscope::require_company_wide(&state.pool, &user).await?;
    let page = service::list_audit(&state.pool, &user.tenant_id, limit, offset, &f).await?;
    Ok(ok(page.to_json()))
}

pub async fn filters_vocabulary(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    wscope::require_company_wide(&state.pool, &user).await?;
    Ok(ok(json!({
        "target_types": trail::catalog::target_types(),
        "actions": trail::catalog::audit_actions(),
        "actors": service::actors(&state.pool, &user.tenant_id).await?,
    })))
}

pub async fn export(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let q = Query::parse(raw.as_deref());
    let mut errs = Errors::default();
    let f = filters(&mut errs, &q);
    let user = admin(&state, &actors, &headers).await?;
    errs.into_result()?;
    wscope::require_company_wide(&state.pool, &user).await?;

    // Who took the trail out, and how much of it: the count is read before the
    // file is written, and the row is committed before the first page is read
    // (Python's middleware writes it while the body is still unsent, so the
    // export lists its own row).
    let total = service::list_audit(&state.pool, &user.tenant_id, 1, 0, &f).await?.total;
    let note = Note {
        after: Some(json!({
            "rows": total.min(service::EXPORT_MAX_ROWS),
            "format": "csv",
            "filters": Value::Object(f.as_note()),
        })),
        ..Default::default()
    };
    trail::record(&state, &actors, "GET", "/audit/export", None, note, 200).await;

    let stamp = Local::now().date_naive().format("%Y-%m-%d").to_string();
    let st = ExportState {
        pool: state.pool.clone(),
        tenant_id: user.tenant_id.clone(),
        filters: f,
        emitted: 0,
        offset: 0,
        phase: ExportPhase::Header,
    };
    let stream = futures_util::stream::unfold(st, service::export_next);
    let mut resp = (StatusCode::OK, HttpBody::from_stream(stream)).into_response();
    let h = resp.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/csv; charset=utf-8"));
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"audit-{stamp}.csv\"")) {
        h.insert(header::CONTENT_DISPOSITION, v);
    }
    Ok(resp)
}
