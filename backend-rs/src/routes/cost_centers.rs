//! Cost centers, approval chains and the order's cost-center attribution.
//!
//! NEW routes, written in Rust only. There is NO Python implementation, so
//! there is no Python failover: with the Rust service down (or its gateway file
//! in `routes.d/off/`) these paths answer Python's own 404 and the screen says
//! so. What Python DOES do is honour the rows these routes write:
//! `backend/inventory/po_chain_service.py` is consulted by
//! `po_approval_service` (requirement, send gate, request, decision), which
//! Python still serves, with the SAME rules as `crate::chain`.
//!
//! * `GET   /cost-centers`                      the tree (flat, with path and depth)
//! * `POST  /cost-centers`                      admin
//! * `PATCH /cost-centers/{id}`                 admin: code, name, parent, active
//! * `GET   /cost-centers/spend`                ordered value per center and period
//! * `GET   /approval-chains`                   chains, bands, levels with names
//! * `POST  /approval-chains`                   admin
//! * `PATCH /approval-chains/{id}`              admin: name, active, bands
//! * `POST  /approval-chains/evaluate`          what the chain says about a value
//! * `PUT   /inventory/po/{id}/cost-center`     attribute an order to a center
//!
//! Centers and chains are NEVER deleted (an order, a budget and an approval
//! keep pointing at them): they are deactivated. Writes to either are
//! company-wide settings, so a user limited to some warehouses is refused.
//! The tag is INTERNAL: who may approve what is a person's decision.

use std::collections::HashMap;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::{Extension, Json};
use chrono::{DateTime, Datelike, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Postgres, Transaction};

use crate::activity::{record_event, Event};
use crate::auth::{self, warehouse_scope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::chain::{self, Center, Chain};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, isoformat_utc, py_strip, take_chars};
use crate::routes::ok;
use crate::routes::po_payments::{format_po_number, po_not_found, po_writer};
use crate::routes::sessions::{query_map, query_pairs};
use crate::state::AppState;
use crate::validation::{self, bool_field, float_field, list_field, str_field, Bound, Errors, Field, StrRules};

/// `INTERNAL_TAGS["inventory-approvals"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'inventory-approvals': who may approve what, and where a purchase is charged, are a person's decisions, recorded under their name",
    ),
    is_mcp: false,
};

pub const MAX_NAME_LENGTH: usize = 120;
pub const MAX_CODE_LENGTH: usize = 40;
const MAX_AMOUNT: f64 = 1e12;
const MAX_SPAN_DAYS: i64 = 3660;
const PERSON: &str = "COALESCE(NULLIF(full_name, ''), split_part(email, '@', 1))";

// ── Small shared pieces ──────────────────────────────────────────────────────

fn err(code: &str, message: &str, status: u16, params: Value) -> ApiError {
    ApiError::app(code, message, status, params)
}

fn center_not_found() -> ApiError {
    err("cost_center_not_found", "Cost center not found", 404, json!({}))
}

fn chain_not_found() -> ApiError {
    err("approval_chain_not_found", "Approval chain not found", 404, json!({}))
}

fn is_unique_violation(e: &sqlx::Error) -> bool {
    e.as_database_error().and_then(|d| d.code()).as_deref() == Some("23505")
}

/// Centers and chains govern every warehouse: a user limited to some is refused
/// (the Python rule for approval rules, `require_company_setting`).
async fn require_company_setting(pool: &PgPool, user: &CurrentUser) -> Result<(), ApiError> {
    if warehouse_scope::is_scoped(pool, user).await? {
        return Err(err("warehouse_scope_company_setting",
            "Only a user with access to every warehouse can change this setting.", 403, json!({})));
    }
    Ok(())
}

/// Admin writer front half: authenticate, admin role, company-wide.
async fn admin_writer(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    require_company_setting(&state.pool, &user).await?;
    Ok(user)
}

async fn lock_tenant(tx: &mut Transaction<'_, Postgres>, tenant_id: &str, what: &str) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(format!("{what}:{tenant_id}"))
        .execute(&mut **tx)
        .await?;
    Ok(())
}

/// Every center of the tenant, as the core sees it.
pub async fn load_centers(pool: &PgPool, tenant_id: &str) -> Result<Vec<Center>, sqlx::Error> {
    let rows: Vec<(String, Option<String>, bool)> =
        sqlx::query_as("SELECT id, parent_id, active FROM cost_centers WHERE tenant_id = $1")
            .bind(tenant_id)
            .fetch_all(pool)
            .await?;
    Ok(rows.into_iter().map(|(id, parent_id, active)| Center { id, parent_id, active }).collect())
}

async fn load_centers_tx(tx: &mut Transaction<'_, Postgres>, tenant_id: &str) -> Result<Vec<Center>, sqlx::Error> {
    let rows: Vec<(String, Option<String>, bool)> =
        sqlx::query_as("SELECT id, parent_id, active FROM cost_centers WHERE tenant_id = $1")
            .bind(tenant_id)
            .fetch_all(&mut **tx)
            .await?;
    Ok(rows.into_iter().map(|(id, parent_id, active)| Center { id, parent_id, active }).collect())
}

pub async fn load_chains(pool: &PgPool, tenant_id: &str) -> Result<Vec<Chain>, sqlx::Error> {
    let rows: Vec<(String, Option<String>, bool)> =
        sqlx::query_as("SELECT id, cost_center_id, active FROM approval_chains WHERE tenant_id = $1 ORDER BY created_at, id")
            .bind(tenant_id)
            .fetch_all(pool)
            .await?;
    let bands: Vec<(String, f64, Value)> =
        sqlx::query_as("SELECT chain_id, min_amount, levels FROM approval_chain_bands WHERE tenant_id = $1")
            .bind(tenant_id)
            .fetch_all(pool)
            .await?;
    let mut by_chain: HashMap<String, Vec<Value>> = HashMap::new();
    for (chain_id, min_amount, levels) in bands {
        by_chain.entry(chain_id).or_default().push(json!({"min_amount": min_amount, "levels": levels}));
    }
    Ok(rows.into_iter().map(|(id, cost_center_id, active)| Chain {
        bands: Value::Array(by_chain.remove(&id).unwrap_or_default()),
        id, cost_center_id, active,
    }).collect())
}

fn ts(v: DateTime<Utc>) -> Value {
    Value::String(isoformat_utc(&v))
}

type CenterRow = (String, String, String, Option<String>, bool, DateTime<Utc>, DateTime<Utc>);

fn center_json(r: &CenterRow) -> Value {
    json!({"id": r.0, "code": r.1, "name": r.2, "parent_id": r.3, "active": r.4,
           "created_at": ts(r.5), "updated_at": ts(r.6)})
}

// ── Cost centers ─────────────────────────────────────────────────────────────

pub async fn list_centers(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let pool = &state.pool;
    let rows: Vec<CenterRow> = sqlx::query_as(
        "SELECT id, code, name, parent_id, active, created_at, updated_at
           FROM cost_centers WHERE tenant_id = $1 ORDER BY lower(code), id")
        .bind(&user.tenant_id).fetch_all(pool).await?;
    let chains: Vec<(String, Option<String>)> = sqlx::query_as(
        "SELECT id, cost_center_id FROM approval_chains WHERE tenant_id = $1 AND active")
        .bind(&user.tenant_id).fetch_all(pool).await?;
    let chain_of: HashMap<String, String> = chains.into_iter()
        .filter_map(|(id, c)| c.map(|c| (c, id))).collect();
    let by_id: HashMap<&str, &CenterRow> = rows.iter().map(|r| (r.0.as_str(), r)).collect();
    // Depth-first so a child follows its parent; a cycle or a dangling parent
    // (the evaluator fails closed on those) is listed as a root, never dropped.
    let mut order: Vec<(&CenterRow, usize, String)> = Vec::new();
    let mut seen: std::collections::HashSet<&str> = std::collections::HashSet::new();
    fn walk<'a>(
        node: &'a CenterRow, depth: usize, path: &str, rows: &'a [CenterRow],
        seen: &mut std::collections::HashSet<&'a str>, out: &mut Vec<(&'a CenterRow, usize, String)>,
    ) {
        if !seen.insert(node.0.as_str()) || depth > chain::MAX_DEPTH {
            return;
        }
        let here = if path.is_empty() { node.1.clone() } else { format!("{path} / {}", node.1) };
        out.push((node, depth, here.clone()));
        for kid in rows.iter().filter(|r| r.3.as_deref() == Some(node.0.as_str())) {
            walk(kid, depth + 1, &here, rows, seen, out);
        }
    }
    for r in rows.iter().filter(|r| r.3.as_deref().map(|p| !by_id.contains_key(p)).unwrap_or(true)) {
        walk(r, 0, "", &rows, &mut seen, &mut order);
    }
    for r in &rows {                       // whatever a cycle left out
        if !seen.contains(r.0.as_str()) {
            walk(r, 0, "", &rows, &mut seen, &mut order);
        }
    }
    let items: Vec<Value> = order.iter().map(|(r, depth, path)| {
        let mut m = center_json(r).as_object().cloned().unwrap_or_default();
        m.insert("depth".into(), json!(depth));
        m.insert("path".into(), json!(path));
        m.insert("chain_id".into(), json!(chain_of.get(&r.0)));
        Value::Object(m)
    }).collect();
    Ok(ok(json!({"items": items})))
}

struct CenterBody {
    code: Field<String>,
    name: Field<String>,
    parent_id: Field<String>,
    active: Field<bool>,
}

fn center_body(obj: &Map<String, Value>, create: bool) -> Result<CenterBody, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let code = str_field(&mut errs, obj, &p, "code", create, false,
        &StrRules { min_length: Some(1), max_length: Some(MAX_CODE_LENGTH), pattern: None });
    let name = str_field(&mut errs, obj, &p, "name", create, false,
        &StrRules { min_length: Some(1), max_length: Some(MAX_NAME_LENGTH), pattern: None });
    let parent_id = str_field(&mut errs, obj, &p, "parent_id", false, true,
        &StrRules { min_length: None, max_length: Some(64), pattern: None });
    let active = bool_field(&mut errs, obj, &p, "active", false);
    errs.into_result()?;
    Ok(CenterBody { code, name, parent_id, active })
}

fn blank(field: &str) -> ApiError {
    err("cost_center_invalid", &format!("{field} cannot be blank"), 422, json!({"field": field}))
}

fn clean_text(f: &Field<String>, field: &str) -> Result<Option<String>, ApiError> {
    match f {
        Field::Value(v) => {
            let s = py_strip(v).to_string();
            if s.is_empty() { Err(blank(field)) } else { Ok(Some(s)) }
        }
        _ => Ok(None),
    }
}

fn parent_problem(p: &str) -> ApiError {
    match p {
        "cycle" => err("cost_center_parent_invalid", "A cost center cannot sit under itself or one of its own children", 409,
            json!({"problem": "cycle", "max_depth": chain::MAX_DEPTH})),
        _ => err("cost_center_parent_invalid", "The cost center tree would be too deep", 409,
            json!({"problem": "too_deep", "max_depth": chain::MAX_DEPTH})),
    }
}

pub async fn create_center(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = admin_writer(&state, &actors, &headers).await?;
    let b = center_body(&validation::body_object(&body)?, true)?;
    let code = clean_text(&b.code, "code")?.ok_or_else(ApiError::internal)?;
    let name = clean_text(&b.name, "name")?.ok_or_else(ApiError::internal)?;
    let parent = match &b.parent_id {
        Field::Value(p) if !py_strip(p).is_empty() => Some(py_strip(p).to_string()),
        _ => None,
    };

    let mut tx = state.pool.begin().await?;
    lock_tenant(&mut tx, &user.tenant_id, "cost_centers").await?;
    if let Some(parent) = &parent {
        let centers = load_centers_tx(&mut tx, &user.tenant_id).await?;
        let Some(p) = centers.iter().find(|c| &c.id == parent) else { return Err(center_not_found()) };
        if !p.active {
            return Err(err("cost_center_inactive", "That cost center is not active", 409, json!({})));
        }
        let mut with_new = centers.clone();
        with_new.push(Center { id: "\u{0}new".into(), parent_id: Some(parent.clone()), active: true });
        if let Some(problem) = chain::reparent_problem(&with_new, "\u{0}new", parent) {
            return Err(parent_problem(problem));
        }
    }
    let row: Result<CenterRow, sqlx::Error> = sqlx::query_as(
        "INSERT INTO cost_centers (tenant_id, code, name, parent_id, active, created_by)
         VALUES ($1, $2, $3, $4, COALESCE($5, TRUE), $6)
      RETURNING id, code, name, parent_id, active, created_at, updated_at")
        .bind(&user.tenant_id).bind(&code).bind(&name).bind(&parent)
        .bind(match &b.active { Field::Value(v) => Some(*v), _ => None })
        .bind(&user.user_id)
        .fetch_one(&mut *tx).await;
    let row = match row {
        Ok(r) => r,
        Err(e) if is_unique_violation(&e) => {
            return Err(err("cost_center_code_taken", "Another cost center already uses that code", 409,
                json!({"code": code})));
        }
        Err(e) => return Err(e.into()),
    };
    tx.commit().await?;
    tracing::info!("[cost-center] CREATED tenant={} id={} by={}", user.tenant_id, row.0, user.user_id);
    let mut d = Map::new();
    d.insert("code".into(), json!(code));
    d.insert("cost_center_name".into(), json!(name));
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CostCenterCreated, Some(&row.0), d).await;
    Ok((StatusCode::CREATED, ok(center_json(&row))))
}

pub async fn update_center(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(center_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = admin_writer(&state, &actors, &headers).await?;
    let obj = validation::body_object(&body)?;
    let b = center_body(&obj, false)?;

    let mut tx = state.pool.begin().await?;
    lock_tenant(&mut tx, &user.tenant_id, "cost_centers").await?;
    let current: CenterRow = sqlx::query_as(
        "SELECT id, code, name, parent_id, active, created_at, updated_at
           FROM cost_centers WHERE id = $1 AND tenant_id = $2 FOR UPDATE")
        .bind(&center_id).bind(&user.tenant_id).fetch_optional(&mut *tx).await?
        .ok_or_else(center_not_found)?;
    let code = clean_text(&b.code, "code")?.unwrap_or_else(|| current.1.clone());
    let name = clean_text(&b.name, "name")?.unwrap_or_else(|| current.2.clone());
    let active = match &b.active { Field::Value(v) => *v, _ => current.4 };
    let parent: Option<String> = match &b.parent_id {
        Field::Absent => current.3.clone(),
        Field::Null => None,
        Field::Value(p) => {
            let p = py_strip(p);
            if p.is_empty() { None } else { Some(p.to_string()) }
        }
    };
    if parent != current.3 {
        if let Some(parent) = &parent {
            let centers = load_centers_tx(&mut tx, &user.tenant_id).await?;
            let Some(p) = centers.iter().find(|c| &c.id == parent) else { return Err(center_not_found()) };
            if !p.active {
                return Err(err("cost_center_inactive", "That cost center is not active", 409, json!({})));
            }
            if let Some(problem) = chain::reparent_problem(&centers, &center_id, parent) {
                return Err(parent_problem(problem));
            }
        }
    }
    let unchanged = code == current.1 && name == current.2 && active == current.4 && parent == current.3;
    if unchanged {
        tx.rollback().await?;
        let mut out = center_json(&current).as_object().cloned().unwrap_or_default();
        out.insert("changed".into(), json!(false));
        return Ok(ok(Value::Object(out)));
    }
    let row: Result<CenterRow, sqlx::Error> = sqlx::query_as(
        "UPDATE cost_centers SET code = $1, name = $2, parent_id = $3, active = $4, updated_at = NOW()
          WHERE id = $5 AND tenant_id = $6
      RETURNING id, code, name, parent_id, active, created_at, updated_at")
        .bind(&code).bind(&name).bind(&parent).bind(active).bind(&center_id).bind(&user.tenant_id)
        .fetch_one(&mut *tx).await;
    let row = match row {
        Ok(r) => r,
        Err(e) if is_unique_violation(&e) => {
            return Err(err("cost_center_code_taken", "Another cost center already uses that code", 409,
                json!({"code": code})));
        }
        Err(e) => return Err(e.into()),
    };
    tx.commit().await?;
    tracing::info!("[cost-center] UPDATED tenant={} id={} by={}", user.tenant_id, center_id, user.user_id);
    let mut d = Map::new();
    d.insert("code".into(), json!(row.1));
    d.insert("cost_center_name".into(), json!(row.2));
    d.insert("active".into(), json!(row.4));
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CostCenterUpdated, Some(&center_id), d).await;
    let mut out = center_json(&row).as_object().cloned().unwrap_or_default();
    out.insert("changed".into(), json!(true));
    Ok(ok(Value::Object(out)))
}

// ── Spend per center ─────────────────────────────────────────────────────────

fn parse_day(raw: &str, field: &str) -> Result<NaiveDate, ApiError> {
    date_fromisoformat(&take_chars(raw, 10)).ok_or_else(|| {
        err("date_invalid_iso", &format!("{field} must be an ISO date (YYYY-MM-DD)"), 422, json!({"field": field}))
    })
}

/// First and last day of the month `today` is in.
pub fn month_bounds(today: NaiveDate) -> (NaiveDate, NaiveDate) {
    let first = today.with_day(1).unwrap_or(today);
    let next = if first.month() == 12 {
        NaiveDate::from_ymd_opt(first.year() + 1, 1, 1)
    } else {
        NaiveDate::from_ymd_opt(first.year(), first.month() + 1, 1)
    };
    (first, next.and_then(|n| n.pred_opt()).unwrap_or(first))
}

/// Adds each center's own ordered value to every ancestor (cycle-safe).
pub fn roll_up(centers: &[Center], own: &HashMap<String, f64>) -> HashMap<String, f64> {
    let mut out: HashMap<String, f64> = HashMap::new();
    for c in centers {
        let total: f64 = chain::descendants(centers, &c.id).iter().map(|d| own.get(d).copied().unwrap_or(0.0)).sum();
        out.insert(c.id.clone(), total);
    }
    out
}

pub async fn spend(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    // The figures span every warehouse's orders: not for a user limited to some.
    warehouse_scope::require_company_wide(&state.pool, &user).await?;
    let q = query_map(&query_pairs(&uri));
    let today = Utc::now().date_naive();
    let (mut from, mut to) = month_bounds(today);
    if let Some(Value::String(s)) = q.get("from") {
        from = parse_day(s, "from")?;
    }
    if let Some(Value::String(s)) = q.get("to") {
        to = parse_day(s, "to")?;
    }
    if to < from || (to - from).num_days() + 1 > MAX_SPAN_DAYS {
        return Err(err("cost_center_period_invalid", "The period is backwards or longer than ten years", 422,
            json!({"field": "to"})));
    }
    let pool = &state.pool;
    let centers = load_centers(pool, &user.tenant_id).await?;
    // Ordered (approved / modified) lines of non-cancelled orders generated in
    // the period, per attributed center. A line with no cost is COUNTED, never
    // priced at zero.
    let rows: Vec<(Option<String>, f64, i64, i64)> = sqlx::query_as(
        "SELECT l.cost_center_id,
                COALESCE(SUM(i.final_qty * i.unit_cost) FILTER (WHERE i.unit_cost IS NOT NULL), 0)::float8,
                COUNT(*) FILTER (WHERE i.unit_cost IS NULL),
                COUNT(DISTINCT l.id)
           FROM inventory_po_log l
           JOIN inventory_po_items i ON i.po_log_id = l.id
          WHERE l.tenant_id = $1 AND l.cancelled_at IS NULL
            AND l.generated_at >= $2::date AND l.generated_at < ($3::date + 1)
            AND i.status IN ('approved', 'modified')
          GROUP BY l.cost_center_id")
        .bind(&user.tenant_id).bind(from).bind(to)
        .fetch_all(pool).await?;
    let mut own: HashMap<String, f64> = HashMap::new();
    let mut unknown: HashMap<String, i64> = HashMap::new();
    let mut orders: HashMap<String, i64> = HashMap::new();
    let mut unattributed = json!({"ordered": 0.0, "unknown_cost_lines": 0, "orders": 0});
    for (center, ordered, unk, n) in rows {
        match center {
            Some(c) if centers.iter().any(|x| x.id == c) => {
                own.insert(c.clone(), ordered);
                unknown.insert(c.clone(), unk);
                orders.insert(c, n);
            }
            // No center, or one that no longer exists: shown apart, never dropped.
            _ => {
                let o = unattributed["ordered"].as_f64().unwrap_or(0.0) + ordered;
                let u = unattributed["unknown_cost_lines"].as_i64().unwrap_or(0) + unk;
                let c = unattributed["orders"].as_i64().unwrap_or(0) + n;
                unattributed = json!({"ordered": o, "unknown_cost_lines": u, "orders": c});
            }
        }
    }
    let rolled = roll_up(&centers, &own);
    let budgets: Vec<(String, String, f64, String, bool, NaiveDate, NaiveDate)> = sqlx::query_as(
        "SELECT root_id, scope_value, amount, currency, hard_cap, period_start, period_end
           FROM purchase_budgets
          WHERE tenant_id = $1 AND scope_type = 'cost_center' AND active AND superseded_by IS NULL
            AND period_start <= $3::date AND period_end >= $2::date")
        .bind(&user.tenant_id).bind(from).bind(to).fetch_all(pool).await?;
    let names: Vec<(String, String, String, Option<String>, bool)> = sqlx::query_as(
        "SELECT id, code, name, parent_id, active FROM cost_centers WHERE tenant_id = $1 ORDER BY lower(code), id")
        .bind(&user.tenant_id).fetch_all(pool).await?;
    let items: Vec<Value> = names.iter().map(|(id, code, name, parent, active)| {
        let bs: Vec<Value> = budgets.iter().filter(|b| &b.1 == id).map(|b| json!({
            "root_id": b.0, "amount": b.2, "currency": b.3, "hard_cap": b.4,
            "period_start": isoformat_date(&b.5), "period_end": isoformat_date(&b.6)})).collect();
        json!({"id": id, "code": code, "name": name, "parent_id": parent, "active": active,
               "own_ordered": own.get(id).copied().unwrap_or(0.0),
               "rolled_up_ordered": rolled.get(id).copied().unwrap_or(0.0),
               "unknown_cost_lines": unknown.get(id).copied().unwrap_or(0),
               "orders": orders.get(id).copied().unwrap_or(0),
               "budgets": bs})
    }).collect();
    Ok(ok(json!({"from": isoformat_date(&from), "to": isoformat_date(&to), "items": items,
                 "unattributed": unattributed})))
}

// ── Approval chains ──────────────────────────────────────────────────────────

fn invalid(field: &str, problem: &str, message: &str) -> ApiError {
    err("approval_chain_invalid", message, 422, json!({"field": field, "problem": problem}))
}

/// One level as written by a client, checked field by field so the answer says
/// WHERE it is wrong. The result is what the evaluator will accept.
fn check_level(raw: &Value, at: &str) -> Result<(), ApiError> {
    let Some(obj) = raw.as_object() else {
        return Err(invalid(at, "not_an_object", "A level must be an object"));
    };
    match obj.get("kind").and_then(Value::as_str) {
        Some("role") => match obj.get("role").and_then(Value::as_str) {
            Some(r) if chain::role_rank(r).is_some() => Ok(()),
            _ => Err(invalid(&format!("{at}.role"), "role", "A role level takes the role admin or analyst")),
        },
        Some("users") => {
            let ids = obj.get("user_ids").and_then(Value::as_array);
            match ids {
                Some(ids) if !ids.is_empty() && ids.len() <= chain::MAX_USERS_PER_LEVEL
                    && ids.iter().all(|i| i.as_str().map(|s| !s.is_empty()).unwrap_or(false)) => Ok(()),
                _ => Err(invalid(&format!("{at}.user_ids"), "user_ids",
                    "A named level takes between 1 and 20 user ids")),
            }
        }
        _ => Err(invalid(&format!("{at}.kind"), "kind", "A level is a role or a list of named users")),
    }
}

/// The bands as the evaluator wants them, or the first thing wrong with them.
/// Amounts are rounded to cents; two bands may not collapse onto one amount.
fn check_bands(raw: &[Value]) -> Result<Vec<Value>, ApiError> {
    let mut out: Vec<Value> = Vec::with_capacity(raw.len());
    let mut seen: Vec<f64> = Vec::new();
    for (i, b) in raw.iter().enumerate() {
        let at = format!("bands[{i}]");
        let Some(obj) = b.as_object() else {
            return Err(invalid(&at, "not_an_object", "A band must be an object"));
        };
        let min = match obj.get("min_amount") {
            Some(Value::Number(n)) => n.as_f64().unwrap_or(f64::NAN),
            Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(f64::NAN),
            _ => f64::NAN,
        };
        if !min.is_finite() || !(0.0..=MAX_AMOUNT).contains(&min) {
            return Err(invalid(&format!("{at}.min_amount"), "min_amount",
                "A band starts at an amount between 0 and 1,000,000,000,000"));
        }
        let min = (min * 100.0).round() / 100.0;
        if seen.iter().any(|s| *s == min) {
            return Err(invalid(&format!("{at}.min_amount"), "duplicate", "Two bands start at the same amount"));
        }
        seen.push(min);
        let Some(levels) = obj.get("levels").and_then(Value::as_array) else {
            return Err(invalid(&format!("{at}.levels"), "levels", "A band needs its levels"));
        };
        if levels.is_empty() || levels.len() > chain::MAX_LEVELS {
            return Err(invalid(&format!("{at}.levels"), "levels",
                "A band takes between 1 and 5 levels"));
        }
        let mut canon: Vec<Value> = Vec::with_capacity(levels.len());
        for (j, l) in levels.iter().enumerate() {
            check_level(l, &format!("{at}.levels[{j}]"))?;
            canon.push(chain::normalize_level(l).map(|l| l.to_json()).ok_or_else(ApiError::internal)?);
        }
        out.push(json!({"min_amount": min, "levels": canon}));
    }
    out.sort_by(|a, b| a["min_amount"].as_f64().partial_cmp(&b["min_amount"].as_f64()).unwrap());
    // The evaluator is the judge: anything it would refuse is refused here too.
    let probe = Chain { id: "probe".into(), cost_center_id: None, active: true, bands: Value::Array(out.clone()) };
    if chain::valid_bands(&probe).is_none() {
        return Err(invalid("bands", "bands", "The bands are not valid"));
    }
    Ok(out)
}

/// Every named user must be an active admin or analyst of THIS tenant.
async fn check_named_users(pool: &PgPool, tenant_id: &str, bands: &[Value]) -> Result<(), ApiError> {
    let mut ids: Vec<String> = Vec::new();
    for b in bands {
        for l in b["levels"].as_array().into_iter().flatten() {
            for i in l["user_ids"].as_array().into_iter().flatten() {
                if let Some(s) = i.as_str() {
                    ids.push(s.to_string());
                }
            }
        }
    }
    ids.sort();
    ids.dedup();
    if ids.is_empty() {
        return Ok(());
    }
    let ok_rows: Vec<(String,)> = sqlx::query_as(
        "SELECT id FROM users WHERE tenant_id = $1 AND id = ANY($2) AND status = 'active'
            AND role IN ('admin', 'analyst')")
        .bind(tenant_id).bind(&ids).fetch_all(pool).await?;
    let good: std::collections::HashSet<String> = ok_rows.into_iter().map(|(i,)| i).collect();
    if let Some(bad) = ids.iter().find(|i| !good.contains(*i)) {
        return Err(err("approval_chain_user_invalid",
            "A named approver must be an active admin or analyst of this company", 422,
            json!({"user_id": bad})));
    }
    Ok(())
}

fn longest_band(bands: &[Value]) -> i64 {
    bands.iter().map(|b| b["levels"].as_array().map(|l| l.len()).unwrap_or(0) as i64).max().unwrap_or(0)
}

/// A chain as the screen needs it: names for people and the center.
async fn chain_view(pool: &PgPool, tenant_id: &str, chain_id: &str) -> Result<Value, ApiError> {
    let list = chains_json(pool, tenant_id, Some(chain_id)).await?;
    list.into_iter().next().ok_or_else(chain_not_found)
}

async fn chains_json(pool: &PgPool, tenant_id: &str, only: Option<&str>) -> Result<Vec<Value>, ApiError> {
    #[allow(clippy::type_complexity)]
    let chains: Vec<(String, String, Option<String>, bool, DateTime<Utc>, DateTime<Utc>, Option<String>, Option<String>)> =
        sqlx::query_as(
            "SELECT c.id, c.name, c.cost_center_id, c.active, c.created_at, c.updated_at, cc.code, cc.name
               FROM approval_chains c LEFT JOIN cost_centers cc ON cc.id = c.cost_center_id AND cc.tenant_id = c.tenant_id
              WHERE c.tenant_id = $1 AND ($2::text IS NULL OR c.id = $2)
              ORDER BY (c.cost_center_id IS NOT NULL), lower(cc.code), c.created_at, c.id")
            .bind(tenant_id).bind(only).fetch_all(pool).await?;
    let bands: Vec<(String, f64, Value)> = sqlx::query_as(
        "SELECT chain_id, min_amount, levels FROM approval_chain_bands WHERE tenant_id = $1 ORDER BY min_amount")
        .bind(tenant_id).fetch_all(pool).await?;
    let people: Vec<(String, String, String, String)> = sqlx::query_as(&format!(
        "SELECT id, {PERSON}, role, status FROM users WHERE tenant_id = $1"))
        .bind(tenant_id).fetch_all(pool).await?;
    let name_of: HashMap<&str, &str> = people.iter().map(|p| (p.0.as_str(), p.1.as_str())).collect();
    let view_level = |l: &Value| -> Value {
        match l.get("kind").and_then(Value::as_str) {
            Some("users") => {
                let users: Vec<Value> = l["user_ids"].as_array().into_iter().flatten().filter_map(Value::as_str)
                    .map(|id| json!({"id": id, "name": name_of.get(id)})).collect();
                json!({"kind": "users", "user_ids": l["user_ids"], "users": users})
            }
            _ => l.clone(),
        }
    };
    Ok(chains.into_iter().map(|(id, name, center, active, created, updated, code, cname)| {
        let bs: Vec<Value> = bands.iter().filter(|b| b.0 == id).map(|b| json!({
            "min_amount": b.1,
            "levels": b.2.as_array().map(|ls| ls.iter().map(&view_level).collect::<Vec<_>>()).unwrap_or_default(),
        })).collect();
        json!({"id": id, "name": name, "cost_center_id": center, "cost_center_code": code,
               "cost_center_name": cname, "active": active, "bands": bs,
               "created_at": ts(created), "updated_at": ts(updated)})
    }).collect())
}

pub async fn list_chains(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let items = chains_json(&state.pool, &user.tenant_id, None).await?;
    // Who a level can name: active admins and analysts.
    let candidates: Vec<(String, String, String)> = sqlx::query_as(&format!(
        "SELECT id, {PERSON}, role FROM users WHERE tenant_id = $1 AND status = 'active'
            AND role IN ('admin', 'analyst') ORDER BY full_name NULLS LAST, email"))
        .bind(&user.tenant_id).fetch_all(&state.pool).await?;
    let candidates: Vec<Value> = candidates.into_iter()
        .map(|(id, name, role)| json!({"id": id, "name": name, "role": role})).collect();
    Ok(ok(json!({"items": items, "candidates": candidates, "max_levels": chain::MAX_LEVELS,
                 "max_bands": chain::MAX_BANDS})))
}

struct ChainBody {
    name: Field<String>,
    cost_center_id: Field<String>,
    active: Field<bool>,
    bands: Option<Vec<Value>>,
}

fn chain_body(obj: &Map<String, Value>, create: bool) -> Result<ChainBody, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let name = str_field(&mut errs, obj, &p, "name", create, false,
        &StrRules { min_length: Some(1), max_length: Some(MAX_NAME_LENGTH), pattern: None });
    let cost_center_id = str_field(&mut errs, obj, &p, "cost_center_id", false, true,
        &StrRules { min_length: None, max_length: Some(64), pattern: None });
    let active = bool_field(&mut errs, obj, &p, "active", false);
    let bands = if create || obj.contains_key("bands") {
        list_field(&mut errs, obj, &p, "bands", 1, chain::MAX_BANDS).cloned()
    } else {
        None
    };
    errs.into_result()?;
    Ok(ChainBody { name, cost_center_id, active, bands })
}

async fn center_must_be_active(tx: &mut Transaction<'_, Postgres>, tenant_id: &str, id: &str) -> Result<(), ApiError> {
    let row: Option<(bool,)> = sqlx::query_as("SELECT active FROM cost_centers WHERE id = $1 AND tenant_id = $2")
        .bind(id).bind(tenant_id).fetch_optional(&mut **tx).await?;
    match row {
        None => Err(center_not_found()),
        Some((false,)) => Err(err("cost_center_inactive", "That cost center is not active", 409, json!({}))),
        Some(_) => Ok(()),
    }
}

fn center_taken(center: Option<&str>) -> ApiError {
    err("approval_chain_center_taken",
        "That cost center (or the default) already has an active approval chain", 409,
        json!({"cost_center_id": center}))
}

async fn write_bands(tx: &mut Transaction<'_, Postgres>, tenant_id: &str, chain_id: &str, bands: &[Value]) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM approval_chain_bands WHERE chain_id = $1 AND tenant_id = $2")
        .bind(chain_id).bind(tenant_id).execute(&mut **tx).await?;
    for b in bands {
        sqlx::query("INSERT INTO approval_chain_bands (tenant_id, chain_id, min_amount, levels) VALUES ($1, $2, $3, $4)")
            .bind(tenant_id).bind(chain_id).bind(b["min_amount"].as_f64().unwrap_or(0.0)).bind(&b["levels"])
            .execute(&mut **tx).await?;
    }
    Ok(())
}

pub async fn create_chain(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = admin_writer(&state, &actors, &headers).await?;
    let b = chain_body(&validation::body_object(&body)?, true)?;
    let name = clean_text(&b.name, "name").map_err(|_| invalid("name", "blank", "The name cannot be blank"))?
        .ok_or_else(ApiError::internal)?;
    let center = match &b.cost_center_id {
        Field::Value(c) if !py_strip(c).is_empty() => Some(py_strip(c).to_string()),
        _ => None,
    };
    let active = match &b.active { Field::Value(v) => *v, _ => true };
    let bands = check_bands(&b.bands.ok_or_else(ApiError::internal)?)?;
    check_named_users(&state.pool, &user.tenant_id, &bands).await?;

    let mut tx = state.pool.begin().await?;
    lock_tenant(&mut tx, &user.tenant_id, "approval_chains").await?;
    if let Some(c) = &center {
        center_must_be_active(&mut tx, &user.tenant_id, c).await?;
    }
    if active {
        let taken: Option<(String,)> = sqlx::query_as(
            "SELECT id FROM approval_chains WHERE tenant_id = $1 AND active
                AND COALESCE(cost_center_id, '') = COALESCE($2, '') LIMIT 1")
            .bind(&user.tenant_id).bind(&center).fetch_optional(&mut *tx).await?;
        if taken.is_some() {
            return Err(center_taken(center.as_deref()));
        }
    }
    let inserted: Result<(String,), sqlx::Error> = sqlx::query_as(
        "INSERT INTO approval_chains (tenant_id, name, cost_center_id, active, created_by)
         VALUES ($1, $2, $3, $4, $5) RETURNING id")
        .bind(&user.tenant_id).bind(&name).bind(&center).bind(active).bind(&user.user_id)
        .fetch_one(&mut *tx).await;
    let id = match inserted {
        Ok((id,)) => id,
        Err(e) if is_unique_violation(&e) => return Err(center_taken(center.as_deref())),
        Err(e) => return Err(e.into()),
    };
    write_bands(&mut tx, &user.tenant_id, &id, &bands).await?;
    tx.commit().await?;
    tracing::info!("[approval-chain] CREATED tenant={} id={} by={}", user.tenant_id, id, user.user_id);
    let mut d = Map::new();
    d.insert("chain_name".into(), json!(name));
    d.insert("levels".into(), json!(longest_band(&bands)));
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::ApprovalChainCreated, Some(&id), d).await;
    Ok((StatusCode::CREATED, ok(chain_view(&state.pool, &user.tenant_id, &id).await?)))
}

pub async fn update_chain(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(chain_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = admin_writer(&state, &actors, &headers).await?;
    let b = chain_body(&validation::body_object(&body)?, false)?;
    let new_name = clean_text(&b.name, "name").map_err(|_| invalid("name", "blank", "The name cannot be blank"))?;
    let new_bands = match &b.bands { Some(raw) => Some(check_bands(raw)?), None => None };
    if let Some(bands) = &new_bands {
        check_named_users(&state.pool, &user.tenant_id, bands).await?;
    }

    let mut tx = state.pool.begin().await?;
    lock_tenant(&mut tx, &user.tenant_id, "approval_chains").await?;
    let current: (String, String, Option<String>, bool) = sqlx::query_as(
        "SELECT id, name, cost_center_id, active FROM approval_chains
          WHERE id = $1 AND tenant_id = $2 FOR UPDATE")
        .bind(&chain_id).bind(&user.tenant_id).fetch_optional(&mut *tx).await?
        .ok_or_else(chain_not_found)?;
    let name = new_name.unwrap_or_else(|| current.1.clone());
    let active = match &b.active { Field::Value(v) => *v, _ => current.3 };
    if active && !current.3 {
        if let Some(c) = &current.2 {
            center_must_be_active(&mut tx, &user.tenant_id, c).await?;
        }
        let taken: Option<(String,)> = sqlx::query_as(
            "SELECT id FROM approval_chains WHERE tenant_id = $1 AND active AND id <> $3
                AND COALESCE(cost_center_id, '') = COALESCE($2, '') LIMIT 1")
            .bind(&user.tenant_id).bind(&current.2).bind(&chain_id).fetch_optional(&mut *tx).await?;
        if taken.is_some() {
            return Err(center_taken(current.2.as_deref()));
        }
    }
    let renamed = name != current.1 || active != current.3;
    if !renamed && new_bands.is_none() {
        tx.rollback().await?;
        let mut v = chain_view(&state.pool, &user.tenant_id, &chain_id).await?;
        v["changed"] = json!(false);
        return Ok(ok(v));
    }
    let upd = sqlx::query("UPDATE approval_chains SET name = $1, active = $2, updated_at = NOW() WHERE id = $3 AND tenant_id = $4")
        .bind(&name).bind(active).bind(&chain_id).bind(&user.tenant_id).execute(&mut *tx).await;
    match upd {
        Ok(_) => {}
        Err(e) if is_unique_violation(&e) => return Err(center_taken(current.2.as_deref())),
        Err(e) => return Err(e.into()),
    }
    if let Some(bands) = &new_bands {
        write_bands(&mut tx, &user.tenant_id, &chain_id, bands).await?;
    }
    let levels: Option<i64> = match &new_bands {
        Some(b) => Some(longest_band(b)),
        None => {
            let rows: Vec<(Value,)> = sqlx::query_as("SELECT levels FROM approval_chain_bands WHERE chain_id = $1")
                .bind(&chain_id).fetch_all(&mut *tx).await?;
            rows.iter().map(|(l,)| l.as_array().map(|a| a.len() as i64).unwrap_or(0)).max()
        }
    };
    tx.commit().await?;
    tracing::info!("[approval-chain] UPDATED tenant={} id={} by={}", user.tenant_id, chain_id, user.user_id);
    let mut d = Map::new();
    d.insert("chain_name".into(), json!(name));
    d.insert("levels".into(), json!(levels));
    d.insert("active".into(), json!(active));
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::ApprovalChainUpdated, Some(&chain_id), d).await;
    let mut v = chain_view(&state.pool, &user.tenant_id, &chain_id).await?;
    v["changed"] = json!(true);
    Ok(ok(v))
}

/// `POST /approval-chains/evaluate`: what the chain says about a value. A POST
/// because of the body, read-only: any signed-in person may ask (the order
/// screen previews it before anybody commits).
pub async fn evaluate(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let p = [json!("body")];
    let center = str_field(&mut errs, &obj, &p, "cost_center_id", false, true,
        &StrRules { min_length: None, max_length: Some(64), pattern: None });
    let amount = float_field(&mut errs, &obj, &p, "amount", false, true, None, Some(Bound::Float(MAX_AMOUNT)));
    let escalate = bool_field(&mut errs, &obj, &p, "escalate", false);
    errs.into_result()?;
    let center = match center { Field::Value(c) if !py_strip(&c).is_empty() => Some(py_strip(&c).to_string()), _ => None };
    let amount = match amount { Field::Value(a) => Some(a), _ => None };
    let escalate = matches!(escalate, Field::Value(true));
    let centers = load_centers(&state.pool, &user.tenant_id).await?;
    let chains = load_chains(&state.pool, &user.tenant_id).await?;
    let r = chain::resolve(&chains, &centers, center.as_deref(), amount, escalate);
    Ok(ok(r.to_json()))
}

// ── Attribution of an order ──────────────────────────────────────────────────

/// A center and every center above it.
fn with_ancestors(centers: &[Center], id: &str) -> Vec<String> {
    let by_id: HashMap<&str, &Center> = centers.iter().map(|c| (c.id.as_str(), c)).collect();
    let mut out = Vec::new();
    let mut cur = Some(id);
    while let Some(c) = cur {
        if out.iter().any(|x: &String| x == c) || out.len() > chain::MAX_DEPTH {
            break;
        }
        out.push(c.to_string());
        cur = by_id.get(c).and_then(|n| n.parent_id.as_deref()).filter(|p| !p.is_empty());
    }
    out
}

/// Is an ACTIVE cost-center budget in force on this center or one above it?
async fn budget_governed(pool: &PgPool, tenant_id: &str, centers: &[Center], center_id: &str) -> Result<bool, sqlx::Error> {
    let ids = with_ancestors(centers, center_id);
    let hit: Option<(i32,)> = sqlx::query_as(
        "SELECT 1 FROM purchase_budgets
          WHERE tenant_id = $1 AND scope_type = 'cost_center' AND scope_value = ANY($2)
            AND active AND superseded_by IS NULL LIMIT 1")
        .bind(tenant_id).bind(&ids).fetch_optional(pool).await?;
    Ok(hit.is_some())
}

/// `PUT /inventory/po/{id}/cost-center`: attribute an order to a center (or clear it).
///
/// What it refuses, and why:
/// * a cancelled order (`po_cancelled`);
/// * a SENT order, or one whose approval is pending or granted
///   (`po_cost_center_locked`): the approval was granted under a chain chosen by
///   the center, and the supplier already has the order;
/// * moving an order into or out of a center that has a cost-center budget
///   (`po_cost_center_budgeted`): the budget cap is checked when an order is
///   PLACED, so re-labelling afterwards would dodge a hard cap or free a cap.
///   Choose the center when the order is created.
pub async fn set_po_cost_center(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let p = [json!("body")];
    let target = str_field(&mut errs, &obj, &p, "cost_center_id", true, true,
        &StrRules { min_length: None, max_length: Some(64), pattern: None });
    errs.into_result()?;
    let target: Option<String> = match target {
        Field::Value(c) if !py_strip(&c).is_empty() => Some(py_strip(&c).to_string()),
        _ => None,
    };
    let pool = &state.pool;
    #[allow(clippy::type_complexity)]
    let po: Option<(Option<i32>, Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<String>, Option<String>)> =
        sqlx::query_as(
            "SELECT po_number, sent_at, cancelled_at, approval_status, cost_center_id
               FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
            .bind(&po_log_id).bind(&user.tenant_id).fetch_optional(pool).await?;
    let (po_number, sent_at, cancelled_at, approval_status, current) = po.ok_or_else(po_not_found)?;
    if cancelled_at.is_some() {
        return Err(err("po_cancelled", "This order was cancelled; reopen it before changing it", 409, json!({})));
    }
    let centers = load_centers(pool, &user.tenant_id).await?;
    let mut target_row: Option<(String, String, bool)> = None;
    if let Some(t) = &target {
        let row: Option<(String, String, bool)> = sqlx::query_as(
            "SELECT id, code, active FROM cost_centers WHERE id = $1 AND tenant_id = $2")
            .bind(t).bind(&user.tenant_id).fetch_optional(pool).await?;
        match row {
            None => return Err(center_not_found()),
            Some((_, _, false)) => return Err(err("cost_center_inactive", "That cost center is not active", 409, json!({}))),
            Some(r) => target_row = Some(r),
        }
    }
    if current == target {
        return Ok(ok(json!({"po_log_id": po_log_id, "cost_center_id": current, "changed": false})));
    }
    let locked_by = if sent_at.is_some() {
        Some("sent")
    } else if matches!(approval_status.as_deref(), Some("pending_approval") | Some("approved")) {
        Some("approval")
    } else {
        None
    };
    if let Some(reason) = locked_by {
        return Err(err("po_cost_center_locked",
            "This order was already sent or is in approval; its cost center can no longer change", 409,
            json!({"reason": reason})));
    }
    for c in [current.as_deref(), target.as_deref()].into_iter().flatten() {
        if budget_governed(pool, &user.tenant_id, &centers, c).await? {
            return Err(err("po_cost_center_budgeted",
                "A cost center with a budget is chosen when the order is created, not afterwards", 409,
                json!({"cost_center_id": c})));
        }
    }
    // Conditional on what we read: a send, an approval request or another
    // attribution that landed in between makes this write a no-op, not a lie.
    let done: Option<(String,)> = sqlx::query_as(
        "UPDATE inventory_po_log SET cost_center_id = $1
          WHERE id = $2 AND tenant_id = $3 AND sent_at IS NULL AND cancelled_at IS NULL
            AND COALESCE(approval_status, '') NOT IN ('pending_approval', 'approved')
            AND cost_center_id IS NOT DISTINCT FROM $4
      RETURNING id")
        .bind(&target).bind(&po_log_id).bind(&user.tenant_id).bind(&current)
        .fetch_optional(pool).await?;
    if done.is_none() {
        return Err(err("po_cost_center_locked",
            "This order changed while you were editing it; reload and try again", 409,
            json!({"reason": "changed"})));
    }
    tracing::info!("[po-cost-center] SET tenant={} po={} center={:?} by={}", user.tenant_id, po_log_id, target, user.user_id);
    let mut d = Map::new();
    d.insert("reference".into(), json!(format_po_number(po_number, &po_log_id)));
    d.insert("cost_center".into(), json!(target_row.as_ref().map(|r| r.1.clone())));
    record_event(pool, &user.tenant_id, &user.user_id, Event::PurchaseOrderCostCenterSet, Some(&po_log_id), d).await;
    Ok(ok(json!({"po_log_id": po_log_id, "cost_center_id": target, "changed": true})))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> NaiveDate {
        NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()
    }

    fn code(e: &ApiError) -> String {
        e.body["error_code"].as_str().unwrap_or_default().to_string()
    }

    #[test]
    fn month_bounds_cover_the_month() {
        assert_eq!(month_bounds(d("2026-10-06")), (d("2026-10-01"), d("2026-10-31")));
        assert_eq!(month_bounds(d("2026-12-31")), (d("2026-12-01"), d("2026-12-31")));
        assert_eq!(month_bounds(d("2028-02-10")), (d("2028-02-01"), d("2028-02-29")));
    }

    #[test]
    fn spend_rolls_up_to_every_ancestor() {
        let cs = vec![
            Center { id: "r".into(), parent_id: None, active: true },
            Center { id: "a".into(), parent_id: Some("r".into()), active: true },
            Center { id: "b".into(), parent_id: Some("a".into()), active: true },
        ];
        let own: HashMap<String, f64> = [("a".to_string(), 10.0), ("b".to_string(), 5.0)].into();
        let r = roll_up(&cs, &own);
        assert_eq!((r["r"], r["a"], r["b"]), (15.0, 15.0, 5.0));
    }

    #[test]
    fn bands_are_rounded_sorted_and_canonical() {
        let raw = vec![
            json!({"min_amount": 5000.004, "levels": [{"kind": "users", "user_ids": ["b", "a", "a"]}]}),
            json!({"min_amount": "100", "levels": [{"kind": "role", "role": "analyst"}, {"kind": "role", "role": "admin"}]}),
        ];
        let out = check_bands(&raw).unwrap();
        assert_eq!(out[0]["min_amount"], 100.0);
        assert_eq!(out[1]["min_amount"], 5000.0);
        assert_eq!(out[1]["levels"][0]["user_ids"], json!(["a", "b"]));
        assert_eq!(longest_band(&out), 2);
    }

    #[test]
    fn bad_bands_name_where_they_are_wrong() {
        let bad = |bands: Vec<Value>| check_bands(&bands).unwrap_err();
        let e = bad(vec![json!({"min_amount": -1, "levels": [{"kind": "role", "role": "admin"}]})]);
        assert_eq!((code(&e), e.body["error_params"]["field"].clone()), ("approval_chain_invalid".into(), json!("bands[0].min_amount")));
        let e = bad(vec![json!({"min_amount": 1, "levels": []})]);
        assert_eq!(e.body["error_params"]["field"], "bands[0].levels");
        let e = bad(vec![json!({"min_amount": 1, "levels": [{"kind": "role", "role": "viewer"}]})]);
        assert_eq!(e.body["error_params"]["field"], "bands[0].levels[0].role");
        let e = bad(vec![json!({"min_amount": 1, "levels": [{"kind": "users", "user_ids": []}]})]);
        assert_eq!(e.body["error_params"]["field"], "bands[0].levels[0].user_ids");
        let e = bad(vec![json!({"min_amount": 1, "levels": [{"kind": "x"}]})]);
        assert_eq!(e.body["error_params"]["field"], "bands[0].levels[0].kind");
        let ok_level = json!([{"kind": "role", "role": "admin"}]);
        let e = bad(vec![json!({"min_amount": 1, "levels": ok_level}), json!({"min_amount": 1.004, "levels": ok_level})]);
        assert_eq!(e.body["error_params"]["problem"], "duplicate");
        let e = bad(vec![json!({"min_amount": 1, "levels": vec![json!({"kind": "role", "role": "admin"}); 6]})]);
        assert_eq!(e.body["error_params"]["field"], "bands[0].levels");
        let e = bad(vec![json!("x")]);
        assert_eq!(e.body["error_params"]["problem"], "not_an_object");
    }

    #[test]
    fn what_attribution_checks_follow_the_ancestors() {
        let cs = vec![
            Center { id: "r".into(), parent_id: None, active: true },
            Center { id: "a".into(), parent_id: Some("r".into()), active: true },
        ];
        assert_eq!(with_ancestors(&cs, "a"), vec!["a", "r"]);
        let cyc = vec![
            Center { id: "x".into(), parent_id: Some("y".into()), active: true },
            Center { id: "y".into(), parent_id: Some("x".into()), active: true },
        ];
        assert_eq!(with_ancestors(&cyc, "x").len(), 2);
    }

    #[test]
    fn parent_problems_say_which() {
        assert_eq!(parent_problem("cycle").body["error_params"]["problem"], "cycle");
        assert_eq!(parent_problem("too_deep").body["error_params"]["max_depth"], chain::MAX_DEPTH);
    }
}
