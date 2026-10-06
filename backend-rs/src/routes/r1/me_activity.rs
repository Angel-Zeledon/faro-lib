//! `GET /api/v1/me/activity` and `GET /api/v1/me/activity/action-types`
//! (backend/api/v1/activity.py and the read half of
//! backend/activity/service.py): the caller's OWN rows of `activity_logs`,
//! newest first, scoped by tenant AND user. Every role; keys are refused
//! (`activity` is an internal tag).

use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::Row;

use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::routes::ok;
use crate::routes::r1::query_params::{sql_int, QueryParams};
use crate::state::AppState;
use crate::validation::Errors;

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'activity': a person's own activity feed (/me/activity)"),
    is_mcp: false,
};

/// One `activity_logs` row as `dict(r)` renders it, in SELECT order.
pub(crate) fn log_row(r: &sqlx::postgres::PgRow) -> Result<Map<String, Value>, sqlx::Error> {
    let opt_s = |name: &str| -> Result<Value, sqlx::Error> {
        Ok(r.try_get::<Option<String>, _>(name)?.map(Value::String).unwrap_or(Value::Null))
    };
    let created: Option<DateTime<Utc>> = r.try_get("created_at")?;
    let mut m = Map::new();
    m.insert("id".into(), opt_s("id")?);
    m.insert("action".into(), opt_s("action")?);
    m.insert("resource".into(), opt_s("resource")?);
    m.insert("context".into(), r.try_get::<Option<Value>, _>("context")?.unwrap_or(Value::Null));
    m.insert("status".into(), opt_s("status")?);
    m.insert("created_at".into(), created.map(|d| Value::String(isoformat_utc(&d))).unwrap_or(Value::Null));
    Ok(m)
}

pub async fn get_activity(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let q = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let limit = q.int(&mut errs, "limit", 50, Some(1), Some(200));
    let offset = q.int(&mut errs, "offset", 0, Some(0), None);
    let action = q.opt_str(&mut errs, "action", None);
    errs.into_result()?;
    let (limit, offset) = (sql_int(limit)?, sql_int(offset)?);

    // `if action_filter:` -- an empty string is no filter.
    let action = action.filter(|a| !a.is_empty());
    let mut base = String::from("FROM activity_logs WHERE tenant_id = $1 AND user_id = $2");
    if action.is_some() {
        base.push_str(" AND action = $3");
    }
    let n = if action.is_some() { 4 } else { 3 };
    let list_sql = format!(
        "SELECT id, action, resource, context, status, created_at {base} ORDER BY created_at DESC LIMIT ${n} OFFSET ${}",
        n + 1
    );
    let mut list = sqlx::query(&list_sql).bind(&user.tenant_id).bind(&user.user_id);
    let count_sql = format!("SELECT COUNT(*) AS n {base}");
    let mut count = sqlx::query_as::<_, (i64,)>(&count_sql)
        .bind(&user.tenant_id)
        .bind(&user.user_id);
    if let Some(a) = &action {
        list = list.bind(a);
        count = count.bind(a);
    }
    let rows = list.bind(limit).bind(offset).fetch_all(&state.pool).await?;
    let items = rows.iter().map(log_row).collect::<Result<Vec<_>, _>>()?;
    let (total,) = count.fetch_one(&state.pool).await?;
    Ok(ok(json!({"items": items, "total": total})))
}

pub async fn get_action_types(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let rows: Vec<(String,)> = sqlx::query_as(
        "SELECT DISTINCT action FROM activity_logs WHERE tenant_id = $1 AND user_id = $2 ORDER BY action",
    )
    .bind(&user.tenant_id)
    .bind(&user.user_id)
    .fetch_all(&state.pool)
    .await?;
    Ok(ok(Value::Array(rows.into_iter().map(|(a,)| Value::String(a)).collect())))
}
