//! The consolidated READ-ONLY views of an organization:
//! `GET /org/consolidated/{committed-demand,stock-signals,purchase-orders,budgets}`.
//!
//! Rust only: there is no Python route behind these, so the gateway has no
//! failover for them (docs/rust-migration.md). Every handler does the same
//! three things in the same order, and none of them can be told which tenants
//! to read:
//!
//! 1. authenticate a signed-in person (an API key or MCP client is refused: the
//!    route is internal);
//! 2. [`resolve`](crate::org::scope::resolve) the tenants this person may read,
//!    from the database, now;
//! 3. run the view's constant SQL with exactly those ids bound.
//!
//! `?tenant_ids=a,b` can only narrow the entitled list (see `OrgScope::narrowed_to`).

use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use serde_json::Value;

use crate::auth::{self, CurrentUser, RequestActors};
use crate::error::ApiError;
use crate::org::scope::{self, resolve, OrgScope, ROUTE};
use crate::org::views;
use crate::query::{self, PyInt, Query};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::Errors;

pub const MAX_WINDOW_DAYS: i64 = 730;
pub const DEFAULT_WINDOW_DAYS: i64 = 90;

/// Steps 1 and 2, then the optional narrowing. Query errors are collected
/// before authentication and raised after it, like every other route here, so
/// an unauthenticated caller learns nothing from a validation message.
async fn entitled(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    q: &Query,
) -> Result<(CurrentUser, OrgScope), ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    let requested = scope::parse_requested(q.get("tenant_ids"))?;
    let resolved = resolve(state, &user).await?;
    let narrowed = resolved.narrowed_to(&requested)?;
    Ok((user, narrowed))
}

pub async fn committed_demand(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    let (_user, scope) = entitled(&state, &actors, &headers, &q).await?;
    let ids = scope.tenant_ids();
    let totals: Vec<views::CommittedTotalsRow> =
        sqlx::query_as(views::COMMITTED_TOTALS_SQL).bind(&ids).fetch_all(&state.pool).await?;
    let months: Vec<views::CommittedMonthRow> =
        sqlx::query_as(views::COMMITTED_MONTHS_SQL).bind(&ids).fetch_all(&state.pool).await?;
    Ok(ok(views::committed_demand(&scope, totals, months)))
}

pub async fn stock_signals(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    let (_user, scope) = entitled(&state, &actors, &headers, &q).await?;
    let ids = scope.tenant_ids();
    let rows: Vec<views::StockRow> =
        sqlx::query_as(views::STOCK_SIGNALS_SQL).bind(&ids).fetch_all(&state.pool).await?;
    Ok(ok(views::stock_signals(&scope, rows)))
}

pub async fn purchase_orders(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    let mut errs = Errors::default();
    let days = query::int_param(&mut errs, &q, "days", DEFAULT_WINDOW_DAYS, Some(1), Some(MAX_WINDOW_DAYS));
    let (_user, scope) = entitled(&state, &actors, &headers, &q).await?;
    errs.into_result()?;
    let Some(PyInt::Small(days)) = days else { return Err(ApiError::internal()) };
    let ids = scope.tenant_ids();
    let rows: Vec<views::PoRow> = sqlx::query_as(views::PURCHASE_ORDERS_SQL)
        .bind(&ids)
        .bind(days)
        .fetch_all(&state.pool)
        .await?;
    Ok(ok(views::purchase_orders(&scope, days, rows)))
}

pub async fn budgets(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    let (_user, scope) = entitled(&state, &actors, &headers, &q).await?;
    let ids = scope.tenant_ids();
    let rows: Vec<views::BudgetRow> =
        sqlx::query_as(views::BUDGETS_SQL).bind(&ids).fetch_all(&state.pool).await?;
    let others: Vec<(String, i64)> =
        sqlx::query_as(views::OTHER_SCOPE_BUDGETS_SQL).bind(&ids).fetch_all(&state.pool).await?;
    Ok(ok(views::budgets(&scope, rows, others)))
}
