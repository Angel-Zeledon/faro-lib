//! Stock rows: the `/inventory/stock*` routes of `backend/api/v1/inventory.py`
//! and the scan lookup of `stock_count_service.lookup`.
//!
//! Migrated: `GET /stock`, `GET /stock/page`, `GET /stock/lookup`,
//! `GET /stock/{sku}`, `PUT /stock/{sku}`, `PATCH /stock/{sku}`,
//! `DELETE /stock/{sku}`. Not migrated (they stay on Python, and the gateway
//! matchers must name exact paths): `/stock/{sku}/history`,
//! `/stock/{sku}/suppliers*`, `/stock/{sku}/product-type`.
//!
//! The write routes go through `inventory::stock::upsert_stock`, the port of
//! the Python chokepoint (canonical warehouse, SKU / location ceilings,
//! provenance stamps, snapshot).

use axum::body::Bytes;
use axum::extract::{Path, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use serde_json::{json, Map, Value};

use crate::auth::warehouse_scope::{self, Scope};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::inventory::calc::py_sum;
use crate::inventory::stock::{self, StockData, Val};
use crate::inventory::validate::{float_ge, int_field};
use crate::inventory::{scope, warehouses};
use crate::limits::{enforce_limit, take_tenant_lock};
use crate::pycompat::py_strip;
use crate::routes::ok;
use crate::routes::r1::query_params::{sql_int, QueryParams};
use crate::routes::sessions::row_json;
use crate::state::AppState;
use crate::validation::{self, body_object, str_field, Bound, Errors, Field, StrRules, NO_STR_RULES};

const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
const WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

// `_MAX_QTY`, `_MAX_MONEY` (1e9) and `_MAX_MOQ` (1e6) are ints in Python.
const MAX_QTY: i64 = 1_000_000_000;
const MAX_MONEY: i64 = 1_000_000_000;
const MAX_MOQ: i64 = 1_000_000;

fn sku_not_found(sku: &str) -> ApiError {
    ApiError::app("stock_sku_not_found", format!("SKU '{sku}' not found in inventory"), 404, json!({"sku": sku}))
}

async fn reader(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<(CurrentUser, Scope), ApiError> {
    let user = auth::current_user(state, headers, READ, actors).await?;
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    Ok((user, sc))
}

// ── GET /inventory/stock ─────────────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, sc) = reader(&state, &actors, &headers).await?;
    let mut conn = state.pool.acquire().await?;
    let rows = stock::list_stock(&mut conn, &user.tenant_id).await?;
    let rows = scope::filter_rows(&sc, rows, "warehouse");
    Ok(ok(Value::Array(rows.into_iter().map(Value::Object).collect())))
}

// ── GET /inventory/stock/page ────────────────────────────────────────────────

/// `list_stock_page`: the filtered, ordered, windowed rows plus the filtered total.
async fn page_rows(
    conn: &mut sqlx::PgConnection,
    tenant_id: &str,
    limit: i64,
    offset: i64,
    q: Option<&str>,
    warehouse: Option<&str>,
) -> Result<(Vec<Map<String, Value>>, i64), sqlx::Error> {
    let mut where_sql = String::from("tenant_id = $1");
    let mut binds: Vec<String> = Vec::new();
    let like = q.filter(|s| !py_strip(s).is_empty()).map(|s| format!("%{}%", py_strip(s)));
    if let Some(l) = &like {
        where_sql.push_str(" AND (sku ILIKE $2 OR display_name ILIKE $3 OR category ILIKE $4 OR supplier ILIKE $5)");
        binds.extend([l.clone(), l.clone(), l.clone(), l.clone()]);
    }
    if let Some(w) = warehouse.filter(|w| !w.is_empty()) {
        where_sql.push_str(&format!(" AND warehouse = ${}", binds.len() + 2));
        binds.push(w.to_string());
    }
    let n = binds.len();
    let rows_sql = format!(
        "SELECT * FROM inventory_stock WHERE {where_sql} ORDER BY sku, warehouse LIMIT ${} OFFSET ${}",
        n + 2, n + 3
    );
    let mut rq = sqlx::query(&rows_sql).bind(tenant_id);
    for b in &binds { rq = rq.bind(b); }
    let rows = rq.bind(limit).bind(offset).fetch_all(&mut *conn).await?;
    let count_sql = format!("SELECT COUNT(*) FROM inventory_stock WHERE {where_sql}");
    let mut cq = sqlx::query_as::<_, (i64,)>(&count_sql).bind(tenant_id);
    for b in &binds { cq = cq.bind(b); }
    let (total,) = cq.fetch_one(&mut *conn).await?;
    let rows = rows.iter().map(row_json).collect::<Result<Vec<_>, _>>()?;
    Ok((rows, total))
}

pub async fn page(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, sc) = reader(&state, &actors, &headers).await?;
    let qp = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let limit = qp.int(&mut errs, "limit", 50, Some(1), Some(500));
    let offset = qp.int(&mut errs, "offset", 0, Some(0), None);
    let q = qp.opt_str(&mut errs, "q", None);
    let q = match q {
        Some(v) if v.chars().count() > 100 => {
            errs.push("string_too_long", &[json!("query"), json!("q")],
                "String should have at most 100 characters".into(), &json!(v), Some(json!({"max_length": 100})));
            None
        }
        other => other,
    };
    let warehouse = qp.opt_str(&mut errs, "warehouse", None);
    let warehouse = match warehouse {
        Some(v) if v.chars().count() > 100 => {
            errs.push("string_too_long", &[json!("query"), json!("warehouse")],
                "String should have at most 100 characters".into(), &json!(v), Some(json!({"max_length": 100})));
            None
        }
        other => other,
    };
    errs.into_result()?;
    let (limit, offset) = (sql_int(limit)?, sql_int(offset)?);

    let mut conn = state.pool.acquire().await?;
    if sc.is_some() {
        // Paged over the caller's warehouses only: `total` must not count rows they cannot see.
        if let Some(w) = warehouse.as_deref().filter(|w| !w.is_empty()) {
            let canon = warehouses::resolve_canonical_name(&mut conn, &user.tenant_id, Some(w)).await?;
            scope::require_in_scope(&sc, Some(&canon))?;
        }
        let (everything, _) = page_rows(&mut conn, &user.tenant_id, 1_000_000_000, 0, q.as_deref(), warehouse.as_deref()).await?;
        let rows = scope::filter_rows(&sc, everything, "warehouse");
        let total = rows.len();
        let items: Vec<Value> = rows.into_iter().skip(offset as usize).take(limit as usize).map(Value::Object).collect();
        return Ok(ok(json!({"items": items, "total": total, "limit": limit, "offset": offset})));
    }
    let (rows, total) = page_rows(&mut conn, &user.tenant_id, limit, offset, q.as_deref(), warehouse.as_deref()).await?;
    Ok(ok(json!({
        "items": rows.into_iter().map(Value::Object).collect::<Vec<_>>(),
        "total": total, "limit": limit, "offset": offset,
    })))
}

// ── GET /inventory/stock/lookup ──────────────────────────────────────────────

fn code_rank(barcode: Option<&str>, sku: &str, cleaned: &str) -> (u8, &'static str) {
    if barcode == Some(cleaned) {
        return (0, "barcode");
    }
    if sku == cleaned {
        return (1, "sku");
    }
    if barcode.unwrap_or("").to_lowercase() == cleaned.to_lowercase() {
        return (2, "barcode");
    }
    (3, "sku")
}

const LOOKUP_COLS: &str = "sku, warehouse, display_name, barcode, unit_of_measure, unit_cost, current_stock, category";

/// `stock_count_service.lookup`. `visible` is `Some(scope)` for a scoped caller
/// reading with no warehouse (rows outside the scope do not exist for them).
async fn lookup(
    conn: &mut sqlx::PgConnection,
    tenant_id: &str,
    code: &str,
    warehouse: Option<&str>,
    visible: &Scope,
) -> Result<Value, ApiError> {
    let cleaned = py_strip(code).to_string();
    if cleaned.is_empty() {
        return Err(ApiError::app("lookup_code_required", "A code is required", 422, json!({})));
    }
    let seen = |rows: Vec<Map<String, Value>>| scope::filter_rows(visible, rows, "warehouse");
    let rows = sqlx::query(&format!(
        "SELECT {LOOKUP_COLS} FROM inventory_stock
         WHERE tenant_id = $1
           AND (barcode = $2 OR sku = $2 OR LOWER(barcode) = LOWER($2) OR LOWER(sku) = LOWER($2))"
    ))
    .bind(tenant_id)
    .bind(&cleaned)
    .fetch_all(&mut *conn)
    .await?;
    let rows = seen(rows.iter().map(row_json).collect::<Result<_, _>>()?);
    if rows.is_empty() {
        return Err(ApiError::app("lookup_code_not_found", format!("No SKU or barcode matches '{cleaned}'"), 404,
            json!({"code": cleaned})));
    }
    let rank_of = |r: &Map<String, Value>| {
        code_rank(r.get("barcode").and_then(Value::as_str), r.get("sku").and_then(Value::as_str).unwrap_or(""), &cleaned)
    };
    let best = rows.iter().map(|r| rank_of(r).0).min().unwrap_or(3);
    let winners: Vec<&Map<String, Value>> = rows.iter().filter(|r| rank_of(r).0 == best).collect();
    let mut skus: Vec<String> = winners.iter().map(|r| r["sku"].as_str().unwrap_or("").to_string()).collect();
    skus.sort();
    skus.dedup();
    if skus.len() > 1 {
        let joined = skus.join(", ");
        return Err(ApiError::app("lookup_code_ambiguous",
            format!("'{cleaned}' matches more than one SKU: {joined}"), 409,
            json!({"code": cleaned, "skus": joined})));
    }
    let sku = skus[0].clone();
    let matched_by = rank_of(winners[0]).1;
    let sku_rows = sqlx::query(&format!(
        "SELECT {LOOKUP_COLS} FROM inventory_stock WHERE tenant_id = $1 AND sku = $2 ORDER BY warehouse"
    ))
    .bind(tenant_id)
    .bind(&sku)
    .fetch_all(&mut *conn)
    .await?;
    let sku_rows = seen(sku_rows.iter().map(row_json).collect::<Result<_, _>>()?);
    let wh_name = match warehouse.filter(|w| !w.is_empty()) {
        Some(w) => Some(warehouses::resolve_canonical_name(conn, tenant_id, Some(w)).await?),
        None => None,
    };
    let here = wh_name.as_ref().and_then(|n| sku_rows.iter().find(|r| r["warehouse"].as_str() == Some(n.as_str())));
    let base = here.unwrap_or(&sku_rows[0]);
    let qty = |r: &Map<String, Value>| r.get("current_stock").and_then(Value::as_f64).unwrap_or(0.0);
    let system_qty = if wh_name.is_some() {
        here.map(|r| qty(r)).unwrap_or(0.0)
    } else {
        py_sum(&sku_rows.iter().map(qty).collect::<Vec<f64>>())
    };
    let mut unit_cost = base.get("unit_cost").cloned().unwrap_or(Value::Null);
    if unit_cost.is_null() {
        unit_cost = sku_rows.iter().map(|r| r.get("unit_cost").cloned().unwrap_or(Value::Null))
            .find(|v| !v.is_null()).unwrap_or(Value::Null);
    }
    Ok(json!({
        "sku": sku,
        "display_name": base.get("display_name").cloned().unwrap_or(Value::Null),
        "barcode": base.get("barcode").cloned().unwrap_or(Value::Null),
        "unit_of_measure": base.get("unit_of_measure").cloned().unwrap_or(Value::Null),
        "category": base.get("category").cloned().unwrap_or(Value::Null),
        "unit_cost": unit_cost,
        "warehouse": wh_name,
        "system_qty": system_qty,
        "in_warehouse": if wh_name.is_some() { json!(here.is_some()) } else { Value::Null },
        "matched_by": matched_by,
    }))
}

pub async fn lookup_route(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, sc) = reader(&state, &actors, &headers).await?;
    let qp = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let at = |n: &str| [json!("query"), json!(n)];
    let code = match qp.get("code") {
        None => {
            errs.push("missing", &at("code"), "Field required".into(), &Value::Null, None);
            None
        }
        Some(c) => {
            let n = c.chars().count();
            if n < 1 {
                errs.push("string_too_short", &at("code"), "String should have at least 1 character".into(),
                    &json!(c), Some(json!({"min_length": 1})));
                None
            } else if n > 200 {
                errs.push("string_too_long", &at("code"), "String should have at most 200 characters".into(),
                    &json!(c), Some(json!({"max_length": 200})));
                None
            } else {
                Some(c.to_string())
            }
        }
    };
    let warehouse = match qp.get("warehouse") {
        Some(w) if w.chars().count() > 100 => {
            errs.push("string_too_long", &at("warehouse"), "String should have at most 100 characters".into(),
                &json!(w), Some(json!({"max_length": 100})));
            None
        }
        other => other.map(str::to_string),
    };
    errs.into_result()?;
    let code = code.ok_or_else(ApiError::internal)?;

    let mut conn = state.pool.acquire().await?;
    if sc.is_none() {
        return Ok(ok(lookup(&mut conn, &user.tenant_id, &code, warehouse.as_deref(), &None).await?));
    }
    // A scoped caller. A named warehouse must be theirs; without one the
    // quantity is summed over THEIR warehouses only.
    if !py_strip(warehouse.as_deref().unwrap_or("")).is_empty() {
        let canon = warehouses::resolve_canonical_name(&mut conn, &user.tenant_id, warehouse.as_deref()).await?;
        scope::require_in_scope(&sc, Some(&canon))?;
        return Ok(ok(lookup(&mut conn, &user.tenant_id, &code, warehouse.as_deref(), &None).await?));
    }
    Ok(ok(lookup(&mut conn, &user.tenant_id, &code, None, &sc).await?))
}

// ── GET /inventory/stock/{sku} ───────────────────────────────────────────────

pub async fn get_one(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(sku): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&sku])?;
    let (user, sc) = reader(&state, &actors, &headers).await?;
    let mut conn = state.pool.acquire().await?;
    let mut row = stock::get_stock(&mut conn, &user.tenant_id, &sku, None).await?;
    if sc.is_some() {
        // The row returned must be one of the caller's, not whichever the database finds first.
        let mut mine = Vec::new();
        for w in stock::list_stock_warehouses(&mut conn, &user.tenant_id, &sku).await? {
            if let Some(r) = stock::get_stock(&mut conn, &user.tenant_id, &sku, Some(&w)).await? {
                mine.push(r);
            }
        }
        row = scope::filter_rows(&sc, mine, "warehouse").into_iter().next();
    }
    match row {
        Some(r) => Ok(ok(Value::Object(r))),
        None => Err(sku_not_found(&sku)),
    }
}

// ── PUT / PATCH bodies ───────────────────────────────────────────────────────

/// The fields of `StockUpsert` / `StockPatch` after validation: what was sent
/// and is not null, in the model's field order (`model_dump(exclude_none=True)`
/// narrowed to `model_fields_set`).
struct StockBody {
    data: StockData,
    /// `body.warehouse` as sent (None when absent or null).
    warehouse: Option<String>,
}

fn validate_stock_body(obj: &Map<String, Value>, upsert: bool) -> Result<StockBody, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let s = |errs: &mut Errors, name: &str| str_field(errs, obj, &p, name, false, true, &NO_STR_RULES);
    let _ = StrRules { min_length: None, max_length: None, pattern: None };

    let display_name = s(&mut errs, "display_name");
    let current_stock = float_ge(&mut errs, obj, &p, "current_stock", upsert, !upsert,
        Some(Bound::Int(0)), Some(Bound::Int(MAX_QTY)));
    let min_stock = float_ge(&mut errs, obj, &p, "min_stock", false, !upsert, Some(Bound::Int(0)), Some(Bound::Int(MAX_QTY)));
    let lead_time = int_field(&mut errs, obj, &p, "lead_time_days", false, !upsert, Some(1), Some(365));
    let unit_cost = float_ge(&mut errs, obj, &p, "unit_cost", false, true, Some(Bound::Int(0)), Some(Bound::Int(MAX_MONEY)));
    let moq = float_ge(&mut errs, obj, &p, "moq", false, !upsert, Some(Bound::Int(1)), Some(Bound::Int(MAX_MOQ)));
    let supplier = s(&mut errs, "supplier");
    let notes = s(&mut errs, "notes");
    let sale_price = float_ge(&mut errs, obj, &p, "sale_price", false, true, Some(Bound::Int(0)), Some(Bound::Int(MAX_MONEY)));
    let category = s(&mut errs, "category");
    let family = s(&mut errs, "family");
    let brand = s(&mut errs, "brand");
    let unit_of_measure = s(&mut errs, "unit_of_measure");
    let barcode = s(&mut errs, "barcode");
    let warehouse = s(&mut errs, "warehouse");
    errs.into_result()?;

    let mut data = StockData::default();
    let text = |f: Field<String>| match f { Field::Value(v) => Some(Val::S(v)), _ => None };
    let num = |f: Field<f64>| match f { Field::Value(v) => Some(Val::F(v)), _ => None };
    if let Some(v) = text(display_name) { data.set("display_name", v); }
    if let Some(v) = num(current_stock) { data.set("current_stock", v); }
    if let Some(v) = num(min_stock) { data.set("min_stock", v); }
    if let Field::Value(v) = lead_time { data.set("lead_time_days", Val::I(v as i32)); }
    if let Some(v) = num(unit_cost) { data.set("unit_cost", v); }
    if let Some(v) = num(moq) { data.set("moq", v); }
    if let Some(v) = text(supplier) { data.set("supplier", v); }
    if let Some(v) = text(notes) { data.set("notes", v); }
    if let Some(v) = num(sale_price) { data.set("sale_price", v); }
    if let Some(v) = text(category) { data.set("category", v); }
    if let Some(v) = text(family) { data.set("family", v); }
    if let Some(v) = text(brand) { data.set("brand", v); }
    if let Some(v) = text(unit_of_measure) { data.set("unit_of_measure", v); }
    if let Some(v) = text(barcode) { data.set("barcode", v); }
    let warehouse = match warehouse { Field::Value(w) => Some(w), _ => None };
    Ok(StockBody { data, warehouse })
}

async fn writer(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    bytes: &Bytes,
) -> Result<(CurrentUser, Option<Map<String, Value>>), ApiError> {
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(ct, bytes)?;
    let user = auth::current_user(state, headers, WRITE, actors).await?;
    auth::require_analyst_or_above(state, &user).await?;
    Ok((user, Some(body_object(&body)?)))
}

// ── PUT /inventory/stock/{sku} ───────────────────────────────────────────────

pub async fn put(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(sku): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&sku])?;
    let (user, obj) = writer(&state, &actors, &headers, &bytes).await?;
    let body = validate_stock_body(&obj.ok_or_else(ApiError::internal)?, true)?;
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;

    // Resolve to the canonical spelling FIRST so the pre-checks judge the same
    // (sku, warehouse) row the write lands on.
    let mut conn = state.pool.acquire().await?;
    let warehouse = warehouses::resolve_canonical_name(&mut conn, &user.tenant_id, body.warehouse.as_deref()).await?;
    drop(conn);
    scope::require_in_scope(&sc, Some(&warehouse))?;

    let mut data = body.data;
    // `data["warehouse"]` stays as sent (upsert resolves it); not sent means absent.
    data.warehouse = body.warehouse.clone();

    // The ceiling and the write, in one transaction holding one per-tenant lock.
    let mut tx = state.pool.begin().await?;
    take_tenant_lock(&mut tx, &user.tenant_id).await?;
    if stock::get_stock(&mut tx, &user.tenant_id, &sku, Some(&warehouse)).await?.is_none() {
        let current = stock::count_stock(&mut tx, &user.tenant_id).await?;
        enforce_limit(&state.pool, &mut tx, state.settings.testing_mode, &user.tenant_id, "max_skus", current, 1).await?;
    }
    if !warehouses::exists(&mut tx, &user.tenant_id, &warehouse).await? {
        let current = warehouses::count(&mut tx, &user.tenant_id).await?;
        enforce_limit(&state.pool, &mut tx, state.settings.testing_mode, &user.tenant_id, "max_locations", current, 1).await?;
    }
    let row = stock::upsert_stock(&state.pool, &mut tx, state.settings.testing_mode, &user.tenant_id, &sku, &data, "user").await?;
    tx.commit().await?;
    Ok(ok(row.map(Value::Object).unwrap_or(Value::Null)))
}

// ── PATCH /inventory/stock/{sku} ─────────────────────────────────────────────

pub async fn patch(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(sku): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&sku])?;
    let (user, obj) = writer(&state, &actors, &headers, &bytes).await?;
    let body = validate_stock_body(&obj.ok_or_else(ApiError::internal)?, false)?;
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    let mut conn = state.pool.acquire().await?;

    let mut whs = stock::list_stock_warehouses(&mut conn, &user.tenant_id, &sku).await?;
    if sc.is_some() {
        // Only the caller's rows exist as far as they are concerned.
        whs.retain(|w| crate::auth::warehouse_scope::in_scope(&sc, Some(w)));
    }
    if whs.is_empty() {
        return Err(sku_not_found(&sku));
    }

    let target = if let Some(w) = body.warehouse.as_deref() {
        let target = warehouses::resolve_canonical_name(&mut conn, &user.tenant_id, Some(w)).await?;
        scope::require_in_scope(&sc, Some(&target))?;
        if !whs.contains(&target) {
            return Err(ApiError::app("stock_sku_not_found_in_warehouse",
                format!("SKU '{sku}' has no stock in warehouse '{target}'"), 404,
                json!({"sku": sku, "warehouse": target})));
        }
        target
    } else if whs.len() == 1 {
        whs[0].clone()
    } else if whs.iter().any(|w| w == warehouses::DEFAULT_WAREHOUSE) {
        warehouses::DEFAULT_WAREHOUSE.to_string()
    } else {
        return Err(ApiError::app("stock_warehouse_required",
            format!("SKU '{sku}' exists in more than one warehouse; name the one to update"), 422,
            json!({"sku": sku, "warehouses": whs.join(", ")})));
    };

    scope::require_in_scope(&sc, Some(&target))?;
    if body.data.fields.is_empty() {
        let row = stock::get_stock(&mut conn, &user.tenant_id, &sku, Some(&target)).await?;
        return Ok(ok(row.map(Value::Object).unwrap_or(Value::Null)));
    }
    drop(conn);
    let mut data = body.data;
    data.warehouse = Some(target);
    let mut tx = state.pool.begin().await?;
    let row = stock::upsert_stock(&state.pool, &mut tx, state.settings.testing_mode, &user.tenant_id, &sku, &data, "user").await?;
    tx.commit().await?;
    Ok(ok(row.map(Value::Object).unwrap_or(Value::Null)))
}

// ── DELETE /inventory/stock/{sku} ────────────────────────────────────────────

pub async fn delete(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(sku): Path<String>,
    headers: HeaderMap,
) -> Result<StatusCode, ApiError> {
    crate::inventory::scope::reject_slash(&[&sku])?;
    let user = auth::current_user(&state, &headers, WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    // Deleting a SKU removes its rows in EVERY warehouse.
    warehouse_scope::require_company_wide(&state.pool, &user).await?;
    let mut conn = state.pool.acquire().await?;
    if stock::get_stock(&mut conn, &user.tenant_id, &sku, None).await?.is_none() {
        return Err(sku_not_found(&sku));
    }
    stock::delete_stock(&mut conn, &user.tenant_id, &sku).await?;
    Ok(StatusCode::NO_CONTENT)
}
