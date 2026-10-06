//! Physical stock counts: `backend/api/v1/stock_counts.py` and
//! `backend/inventory/stock_count_service.py` (all nine routes).
//!
//! A count is a session (`stock_counts`) with one line per SKU. Applying writes
//! the DIFFERENCE between counted and the system quantity at the first scan,
//! never the counted number, so a reception booked after the scan survives.
//! Everything the apply writes goes through the shared `upsert_stock`
//! chokepoint on ONE connection under the tenant's advisory lock: every
//! selected adjustment lands or none does.
//!
//! Guard order is FastAPI's: JSON decode, then the person/key, the role (and
//! the trial read-only guard), then the `count_guard` scope dependency, then
//! pydantic validation of the body, then the service's own 404/409/422.

use axum::body::Bytes;
use axum::extract::{Path, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use serde_json::{json, Map, Value};
use sqlx::{PgConnection, Row};

use crate::auth::warehouse_scope::{self, Scope};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::inventory::calc::{py_max, py_round, py_sum};
use crate::inventory::events::{self, STOCK_COUNT_APPLIED};
use crate::inventory::stock::{self, StockData, Val};
use crate::inventory::validate::{float_ge, literal_field, literal_query};
use crate::inventory::{scope, warehouses};
use crate::limits::take_tenant_lock;
use crate::routes::ok;
use crate::routes::r1::query_params::{sql_int, QueryParams};
use crate::routes::sessions::row_json;
use crate::state::AppState;
use crate::validation::{self, body_object, list_field, str_field, Bound, Errors, Field, StrRules};

/// Router tag `inventory`: exposed to keys, a write key for anything that writes.
const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
const WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

const MAX_QTY: i64 = 1_000_000_000;
const EPS: f64 = 1e-9;
const ADJUSTMENT_REASON: &str = "physical_count";

fn not_open(status: &str) -> ApiError {
    ApiError::app("count_not_open", "This count is no longer open for changes", 409, json!({"status": status}))
}

fn invalid_quantity() -> ApiError {
    ApiError::app("count_invalid_quantity", "Quantity out of range", 422, json!({}))
}

fn str_of(row: &Map<String, Value>, key: &str) -> String {
    row.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
}

/// `_get_count_row(.., conn, lock)`: `SELECT *` of one count of this tenant.
async fn get_count_row(
    conn: &mut PgConnection,
    tenant_id: &str,
    count_id: &str,
    lock: bool,
) -> Result<Map<String, Value>, ApiError> {
    let sql = if lock {
        "SELECT * FROM stock_counts WHERE tenant_id = $1 AND id = $2 FOR UPDATE"
    } else {
        "SELECT * FROM stock_counts WHERE tenant_id = $1 AND id = $2"
    };
    let row = sqlx::query(sql).bind(tenant_id).bind(count_id).fetch_optional(&mut *conn).await?;
    match row {
        Some(r) => Ok(row_json(&r)?),
        None => Err(scope::count_not_found(count_id)),
    }
}

/// Person + role guards, then (for `{count_id}` routes) the scope dependency.
async fn writer(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    count_id: Option<&str>,
) -> Result<(CurrentUser, Scope), ApiError> {
    let user = auth::current_user(state, headers, WRITE, actors).await?;
    auth::require_analyst_or_above(state, &user).await?;
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    if let Some(id) = count_id {
        scope::require_count_in_scope(&state.pool, &sc, &user.tenant_id, id).await?;
    }
    Ok((user, sc))
}

async fn reader(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    count_id: Option<&str>,
) -> Result<(CurrentUser, Scope), ApiError> {
    let user = auth::current_user(state, headers, READ, actors).await?;
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    if let Some(id) = count_id {
        scope::require_count_in_scope(&state.pool, &sc, &user.tenant_id, id).await?;
    }
    Ok((user, sc))
}

fn json_body(headers: &HeaderMap, bytes: &Bytes) -> Result<validation::Body, ApiError> {
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    validation::read_body(ct, bytes)
}

// ── POST /inventory/stock-counts ─────────────────────────────────────────────

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let body = json_body(&headers, &bytes)?;
    let user = auth::current_user(&state, &headers, WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let obj = body_object(&body)?;

    let mut errs = Errors::default();
    let p = [json!("body")];
    let opt = |max: usize| StrRules { min_length: None, max_length: Some(max), pattern: None };
    let warehouse = str_field(&mut errs, &obj, &p, "warehouse", false, true, &opt(100));
    let scope_category = str_field(&mut errs, &obj, &p, "scope_category", false, true, &opt(100));
    let scope_supplier = str_field(&mut errs, &obj, &p, "scope_supplier", false, true, &opt(200));
    let notes = str_field(&mut errs, &obj, &p, "notes", false, true, &opt(500));
    errs.into_result()?;
    let take = |f: Field<String>| match f { Field::Value(v) => Some(v), _ => None };
    let (warehouse, scope_category, scope_supplier, notes) =
        (take(warehouse), take(scope_category), take(scope_supplier), take(notes));

    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    let mut conn = state.pool.acquire().await?;
    // The warehouse the count will walk must be the caller's, resolved exactly
    // as the service resolves it.
    let resolved = warehouses::resolve_canonical_name(&mut conn, &user.tenant_id, warehouse.as_deref()).await?;
    scope::require_in_scope(&sc, Some(&resolved))?;

    // create_count
    let name = warehouses::resolve_canonical_name(&mut conn, &user.tenant_id, warehouse.as_deref()).await?;
    if !warehouses::exists(&mut conn, &user.tenant_id, &name).await? && name != warehouses::DEFAULT_WAREHOUSE {
        return Err(ApiError::app("warehouse_not_found", format!("Warehouse '{name}' not found"), 404,
            json!({"warehouse": name})));
    }
    let non_empty = |v: Option<String>| v.filter(|s| !s.is_empty());
    let row = sqlx::query(
        "INSERT INTO stock_counts (tenant_id, warehouse, scope_category, scope_supplier, notes, created_by)
         VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
    )
    .bind(&user.tenant_id)
    .bind(&name)
    .bind(non_empty(scope_category))
    .bind(non_empty(scope_supplier))
    .bind(non_empty(notes))
    .bind(&user.user_id)
    .fetch_one(&mut *conn)
    .await?;
    Ok((StatusCode::CREATED, ok(Value::Object(row_json(&row)?))))
}

// ── GET /inventory/stock-counts ──────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, sc) = reader(&state, &actors, &headers, None).await?;
    let q = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let status = literal_query(&mut errs, q.get("status"), "status", &["open", "closed", "applied", "cancelled"]);
    let limit = q.int(&mut errs, "limit", 50, Some(1), Some(200));
    errs.into_result()?;
    let limit = sql_int(limit)?;

    // A scoped caller reads wide, filters, then cuts (filtering after the cut
    // would return fewer than `limit` rows).
    let fetch_limit = if sc.is_some() { 10_000 } else { limit };
    let mut sql = String::from(
        "SELECT c.*, (SELECT COUNT(*) FROM stock_count_lines l WHERE l.count_id = c.id) AS lines_count
         FROM stock_counts c WHERE c.tenant_id = $1",
    );
    if status.is_some() {
        sql.push_str(" AND c.status = $2 ORDER BY c.created_at DESC LIMIT $3");
    } else {
        sql.push_str(" ORDER BY c.created_at DESC LIMIT $2");
    }
    let mut query = sqlx::query(&sql).bind(&user.tenant_id);
    if let Some(s) = &status {
        query = query.bind(s);
    }
    let rows = query.bind(fetch_limit).fetch_all(&state.pool).await?;
    let rows: Vec<Map<String, Value>> = rows.iter().map(row_json).collect::<Result<_, _>>()?;
    let rows = if sc.is_some() {
        scope::filter_rows(&sc, rows, "warehouse").into_iter().take(limit as usize).collect()
    } else {
        rows
    };
    Ok(ok(Value::Array(rows.into_iter().map(Value::Object).collect())))
}

// ── GET /inventory/stock-counts/{id} ─────────────────────────────────────────

pub async fn get_one(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(count_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&count_id])?;
    let (user, _sc) = reader(&state, &actors, &headers, Some(&count_id)).await?;
    let mut conn = state.pool.acquire().await?;
    let mut count = get_count_row(&mut conn, &user.tenant_id, &count_id, false).await?;
    let lines = sqlx::query(
        "SELECT * FROM stock_count_lines WHERE tenant_id = $1 AND count_id = $2 ORDER BY scanned_at DESC",
    )
    .bind(&user.tenant_id)
    .bind(&count_id)
    .fetch_all(&mut *conn)
    .await?;
    let lines: Vec<Value> = lines.iter().map(|r| row_json(r).map(Value::Object)).collect::<Result<_, _>>()?;
    count.insert("lines".into(), Value::Array(lines));
    Ok(ok(Value::Object(count)))
}

// ── PUT /inventory/stock-counts/{id}/lines ───────────────────────────────────

pub async fn upsert_line(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(count_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&count_id])?;
    let body = json_body(&headers, &bytes)?;
    let (user, _sc) = writer(&state, &actors, &headers, Some(&count_id)).await?;
    let obj = body_object(&body)?;

    let mut errs = Errors::default();
    let p = [json!("body")];
    let sku = str_field(&mut errs, &obj, &p, "sku", true, false,
        &StrRules { min_length: Some(1), max_length: Some(200), pattern: None });
    let quantity = float_ge(&mut errs, &obj, &p, "quantity", true, false, Some(Bound::Int(0)), Some(Bound::Int(MAX_QTY)));
    let mode = literal_field(&mut errs, &obj, &p, "mode", &["add", "set"], false);
    let source = literal_field(&mut errs, &obj, &p, "source", &["scan", "manual"], false);
    let client_ref = str_field(&mut errs, &obj, &p, "client_ref", false, true,
        &StrRules { min_length: None, max_length: Some(100), pattern: None });
    errs.into_result()?;
    let (Field::Value(sku), Field::Value(qty)) = (sku, quantity) else { return Err(ApiError::internal()) };
    let mode = match mode { Field::Value(m) => m, _ => "add".to_string() };
    let source = match source { Field::Value(s) => s, _ => "manual".to_string() };
    let client_ref = match client_ref { Field::Value(c) => Some(c), _ => None };

    let mut tx = state.pool.begin().await?;
    let count = get_count_row(&mut tx, &user.tenant_id, &count_id, true).await?;
    let status = str_of(&count, "status");
    if status != "open" {
        return Err(not_open(&status));
    }

    if let Some(cref) = client_ref.as_deref().filter(|c| !c.is_empty()) {
        let fresh = sqlx::query(
            "INSERT INTO stock_count_ops (count_id, client_ref, tenant_id) VALUES ($1, $2, $3)
             ON CONFLICT DO NOTHING RETURNING client_ref",
        )
        .bind(&count_id)
        .bind(cref)
        .bind(&user.tenant_id)
        .fetch_optional(&mut *tx)
        .await?;
        if fresh.is_none() {
            let existing = sqlx::query("SELECT * FROM stock_count_lines WHERE count_id = $1 AND sku = $2")
                .bind(&count_id)
                .bind(&sku)
                .fetch_optional(&mut *tx)
                .await?;
            let line = match existing { Some(r) => Value::Object(row_json(&r)?), None => Value::Null };
            tx.commit().await?;
            return Ok(ok(json!({"duplicate": true, "line": line})));
        }
    }

    let stock_rows = sqlx::query("SELECT warehouse, current_stock FROM inventory_stock WHERE tenant_id = $1 AND sku = $2")
        .bind(&user.tenant_id)
        .bind(&sku)
        .fetch_all(&mut *tx)
        .await?;
    if stock_rows.is_empty() {
        return Err(ApiError::app("count_sku_not_found", format!("SKU '{sku}' not found in inventory"), 404,
            json!({"sku": sku})));
    }
    let count_wh = str_of(&count, "warehouse");
    let mut system_qty = 0.0_f64;
    for r in &stock_rows {
        if r.try_get::<String, _>("warehouse")? == count_wh {
            system_qty = r.try_get::<f64, _>("current_stock")?;
            break;
        }
    }

    let existing = sqlx::query("SELECT * FROM stock_count_lines WHERE count_id = $1 AND sku = $2")
        .bind(&count_id)
        .bind(&sku)
        .fetch_optional(&mut *tx)
        .await?;
    let line = if let Some(ex) = existing {
        let counted: f64 = ex.try_get("counted_qty")?;
        let new_qty = if mode == "add" { counted + qty } else { qty };
        if new_qty > MAX_QTY as f64 {
            return Err(invalid_quantity());
        }
        let id: String = ex.try_get("id")?;
        sqlx::query(
            "UPDATE stock_count_lines SET counted_qty = $1, source = $2, scanned_at = NOW(), scanned_by = $3
             WHERE id = $4 RETURNING *",
        )
        .bind(new_qty)
        .bind(&source)
        .bind(&user.user_id)
        .bind(id)
        .fetch_one(&mut *tx)
        .await?
    } else {
        sqlx::query(
            "INSERT INTO stock_count_lines (tenant_id, count_id, sku, counted_qty, system_qty_at_count, source, scanned_by)
             VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
        )
        .bind(&user.tenant_id)
        .bind(&count_id)
        .bind(&sku)
        .bind(qty)
        .bind(system_qty)
        .bind(&source)
        .bind(&user.user_id)
        .fetch_one(&mut *tx)
        .await?
    };
    let line = Value::Object(row_json(&line)?);
    tx.commit().await?;
    Ok(ok(json!({"duplicate": false, "line": line})))
}

// ── DELETE /inventory/stock-counts/{id}/lines/{sku} ──────────────────────────

pub async fn delete_line(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path((count_id, sku)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<StatusCode, ApiError> {
    crate::inventory::scope::reject_slash(&[&count_id, &sku])?;
    let (user, _sc) = writer(&state, &actors, &headers, Some(&count_id)).await?;
    let mut tx = state.pool.begin().await?;
    let count = get_count_row(&mut tx, &user.tenant_id, &count_id, true).await?;
    let status = str_of(&count, "status");
    if status != "open" {
        return Err(not_open(&status));
    }
    let gone = sqlx::query("DELETE FROM stock_count_lines WHERE count_id = $1 AND sku = $2 RETURNING id")
        .bind(&count_id)
        .bind(&sku)
        .fetch_optional(&mut *tx)
        .await?;
    if gone.is_none() {
        return Err(ApiError::app("count_line_not_found", format!("SKU '{sku}' is not in this count"), 404,
            json!({"sku": sku})));
    }
    tx.commit().await?;
    Ok(StatusCode::NO_CONTENT)
}

// ── POST /inventory/stock-counts/{id}/close ──────────────────────────────────

pub async fn close(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(count_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&count_id])?;
    let (user, _sc) = writer(&state, &actors, &headers, Some(&count_id)).await?;
    let mut tx = state.pool.begin().await?;
    let count = get_count_row(&mut tx, &user.tenant_id, &count_id, true).await?;
    let status = str_of(&count, "status");
    if status != "open" {
        return Err(not_open(&status));
    }
    let (n,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM stock_count_lines WHERE count_id = $1")
        .bind(&count_id)
        .fetch_one(&mut *tx)
        .await?;
    if n == 0 {
        return Err(ApiError::app("count_empty", "Count at least one item before closing", 409, json!({})));
    }
    let row = sqlx::query(
        "UPDATE stock_counts SET status = 'closed', closed_at = NOW(), closed_by = $1 WHERE id = $2 RETURNING *",
    )
    .bind(&user.user_id)
    .bind(&count_id)
    .fetch_one(&mut *tx)
    .await?;
    let out = Value::Object(row_json(&row)?);
    tx.commit().await?;
    Ok(ok(out))
}

// ── POST /inventory/stock-counts/{id}/cancel ─────────────────────────────────

pub async fn cancel(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(count_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&count_id])?;
    let (user, _sc) = writer(&state, &actors, &headers, Some(&count_id)).await?;
    let mut tx = state.pool.begin().await?;
    let count = get_count_row(&mut tx, &user.tenant_id, &count_id, true).await?;
    let status = str_of(&count, "status");
    if status != "open" && status != "closed" {
        return Err(ApiError::app("count_cannot_cancel", "Only an open or closed count can be cancelled", 409,
            json!({"status": status})));
    }
    let row = sqlx::query(
        "UPDATE stock_counts SET status = 'cancelled', cancelled_at = NOW(), cancelled_by = $1 WHERE id = $2 RETURNING *",
    )
    .bind(&user.user_id)
    .bind(&count_id)
    .fetch_one(&mut *tx)
    .await?;
    let out = Value::Object(row_json(&row)?);
    tx.commit().await?;
    Ok(ok(out))
}

// ── GET /inventory/stock-counts/{id}/preview ─────────────────────────────────

const LINE_VIEW_SQL: &str = "
    SELECT l.*, s.display_name,
           s.current_stock AS current_qty,
           COALESCE(s.unit_cost, (SELECT x.unit_cost FROM inventory_stock x
                                  WHERE x.tenant_id = l.tenant_id AND x.sku = l.sku
                                    AND x.unit_cost IS NOT NULL LIMIT 1)) AS unit_cost
    FROM stock_count_lines l
    JOIN stock_counts c ON c.id = l.count_id
    LEFT JOIN inventory_stock s
           ON s.tenant_id = l.tenant_id AND s.sku = l.sku AND s.warehouse = c.warehouse
    WHERE l.tenant_id = $1 AND l.count_id = $2";

struct Shaped {
    json: Map<String, Value>,
    difference: f64,
    value_impact: Option<f64>,
}

/// `_shape_line`.
fn shape_line(r: &Map<String, Value>) -> Shaped {
    let f = |k: &str| r.get(k).and_then(Value::as_f64).unwrap_or(0.0);
    let counted = f("counted_qty");
    let system = f("system_qty_at_count");
    let diff = counted - system;
    let cost = r.get("unit_cost").and_then(Value::as_f64);
    let current = match r.get("current_qty") { Some(Value::Null) | None => 0.0, Some(v) => v.as_f64().unwrap_or(0.0) };
    let value_impact = cost.map(|c| py_round(diff * c, 2));
    let mut m = Map::new();
    m.insert("sku".into(), r["sku"].clone());
    m.insert("display_name".into(), r.get("display_name").cloned().unwrap_or(Value::Null));
    m.insert("counted_qty".into(), json!(counted));
    m.insert("system_qty_at_count".into(), json!(system));
    m.insert("current_qty".into(), json!(current));
    m.insert("difference".into(), json!(diff));
    m.insert("unit_cost".into(), r.get("unit_cost").cloned().unwrap_or(Value::Null));
    m.insert("value_impact".into(), value_impact.map(|v| json!(v)).unwrap_or(Value::Null));
    m.insert("moved_since_count".into(), json!((current - system).abs() > EPS));
    for k in ["source", "scanned_at", "applied_at", "applied_from", "applied_to"] {
        m.insert(k.into(), r.get(k).cloned().unwrap_or(Value::Null));
    }
    Shaped { json: m, difference: diff, value_impact }
}

/// Python's `sum([])` is the int 0, any non-empty float sum is a float.
fn py_sum_json(items: &[f64]) -> Value {
    if items.is_empty() { json!(0) } else { json!(py_sum(items)) }
}

/// `round(sum(...), 2)` of the same.
fn py_round_sum_json(items: &[f64]) -> Value {
    if items.is_empty() { json!(0) } else { json!(py_round(py_sum(items), 2)) }
}

pub async fn preview(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(count_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&count_id])?;
    let (user, _sc) = reader(&state, &actors, &headers, Some(&count_id)).await?;
    let mut conn = state.pool.acquire().await?;
    let count = get_count_row(&mut conn, &user.tenant_id, &count_id, false).await?;
    let rows = sqlx::query(LINE_VIEW_SQL).bind(&user.tenant_id).bind(&count_id).fetch_all(&mut *conn).await?;
    let mut lines: Vec<Shaped> = rows
        .iter()
        .map(|r| row_json(r).map(|m| shape_line(&m)))
        .collect::<Result<_, _>>()?;
    // Priced lines by the size of the money at stake, then the unpriced ones by
    // units. `list.sort` is stable, so is this.
    let key = |l: &Shaped| -> (u8, f64) {
        match l.value_impact {
            Some(v) => (0, -v.abs()),
            None => (1, -l.difference.abs()),
        }
    };
    lines.sort_by(|a, b| {
        let (ka, kb) = (key(a), key(b));
        ka.0.cmp(&kb.0).then(ka.1.partial_cmp(&kb.1).unwrap_or(std::cmp::Ordering::Equal))
    });
    let diffs: Vec<&Shaped> = lines.iter().filter(|l| l.difference.abs() > EPS).collect();
    let over: Vec<&Shaped> = diffs.iter().copied().filter(|l| l.difference > 0.0).collect();
    let short: Vec<&Shaped> = diffs.iter().copied().filter(|l| l.difference < 0.0).collect();
    let priced: Vec<&Shaped> = diffs.iter().copied().filter(|l| l.value_impact.is_some()).collect();

    let warehouse = str_of(&count, "warehouse");
    let mut sql = String::from("SELECT COUNT(*) FROM inventory_stock WHERE tenant_id = $1 AND warehouse = $2");
    let mut next = 3;
    let category = count.get("scope_category").and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string);
    let supplier = count.get("scope_supplier").and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string);
    if category.is_some() {
        sql.push_str(&format!(" AND category = ${next}"));
        next += 1;
    }
    if supplier.is_some() {
        sql.push_str(&format!(" AND supplier = ${next}"));
        next += 1;
    }
    sql.push_str(&format!(" AND sku NOT IN (SELECT sku FROM stock_count_lines WHERE count_id = ${next})"));
    let mut q = sqlx::query_as::<_, (i64,)>(&sql).bind(&user.tenant_id).bind(&warehouse);
    if let Some(c) = &category { q = q.bind(c); }
    if let Some(s) = &supplier { q = q.bind(s); }
    let (uncounted,) = q.bind(&count_id).fetch_one(&mut *conn).await?;

    let diff_of = |ls: &[&Shaped]| ls.iter().map(|l| l.difference).collect::<Vec<f64>>();
    let impact_of = |ls: &[&Shaped]| ls.iter().map(|l| l.value_impact.unwrap_or(0.0)).collect::<Vec<f64>>();
    let priced_over: Vec<&Shaped> = priced.iter().copied().filter(|l| l.difference > 0.0).collect();
    let priced_short: Vec<&Shaped> = priced.iter().copied().filter(|l| l.difference < 0.0).collect();
    // `-sum(...)`: the int 0 negates to 0, a float sum to its negation.
    let neg = |items: &[f64]| if items.is_empty() { json!(0) } else { json!(-py_sum(items)) };
    // `round(-sum(...), 2)`.
    let round_neg = |items: &[f64]| if items.is_empty() { json!(0) } else { json!(py_round(-py_sum(items), 2)) };
    let totals = json!({
        "lines": lines.len(),
        "lines_with_difference": diffs.len(),
        "units_over": py_sum_json(&diff_of(&over)),
        "units_short": neg(&diff_of(&short)),
        "value_over": py_round_sum_json(&impact_of(&priced_over)),
        "value_short": round_neg(&impact_of(&priced_short)),
        "net_value": py_round_sum_json(&impact_of(&priced)),
        "unpriced_lines": diffs.len() - priced.len(),
        "uncounted_skus": uncounted,
    });
    Ok(ok(json!({
        "count": Value::Object(count),
        "lines": lines.into_iter().map(|l| Value::Object(l.json)).collect::<Vec<_>>(),
        "totals": totals,
    })))
}

// ── POST /inventory/stock-counts/{id}/apply ──────────────────────────────────

pub async fn apply(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(count_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&count_id])?;
    let body = json_body(&headers, &bytes)?;
    let (user, _sc) = writer(&state, &actors, &headers, Some(&count_id)).await?;

    // `body: Optional[CountApply] = None`.
    let mut skus: Option<Vec<String>> = None;
    match &body {
        validation::Body::Missing | validation::Body::Json(Value::Null) => {}
        other => {
            let obj = body_object(other)?;
            let mut errs = Errors::default();
            let p = [json!("body")];
            if matches!(obj.get("skus"), Some(v) if !v.is_null()) {
                if let Some(items) = list_field(&mut errs, &obj, &p, "skus", 0, 5000) {
                    let mut out = Vec::new();
                    for (i, item) in items.iter().enumerate() {
                        let at = [json!("body"), json!("skus"), json!(i)];
                        match item {
                            Value::String(s) => out.push(s.clone()),
                            v => errs.push("string_type", &at, "Input should be a valid string".into(), v, None),
                        }
                    }
                    skus = Some(out);
                }
            }
            errs.into_result()?;
        }
    }

    if let Some(s) = &skus {
        if s.is_empty() {
            return Err(ApiError::app("count_no_lines_selected", "Select at least one line to apply", 422, json!({})));
        }
    }

    // limit_guard: one transaction holding the tenant's lock.
    let mut tx = state.pool.begin().await?;
    take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let count = get_count_row(&mut tx, &user.tenant_id, &count_id, true).await?;
    let status = str_of(&count, "status");
    if status == "applied" {
        return Err(ApiError::app("count_already_applied", "This count was already applied", 409, json!({})));
    }
    if status != "closed" {
        return Err(ApiError::app("count_not_closed", "Close the count before applying it", 409,
            json!({"status": status})));
    }
    let warehouse = str_of(&count, "warehouse");

    let rows = sqlx::query(
        "SELECT l.*, s.current_stock AS current_qty,
                COALESCE(s.unit_cost, (SELECT x.unit_cost FROM inventory_stock x
                                       WHERE x.tenant_id = l.tenant_id AND x.sku = l.sku
                                         AND x.unit_cost IS NOT NULL LIMIT 1)) AS unit_cost
           FROM stock_count_lines l
           LEFT JOIN inventory_stock s
                  ON s.tenant_id = l.tenant_id AND s.sku = l.sku AND s.warehouse = $1
          WHERE l.tenant_id = $2 AND l.count_id = $3 AND l.applied_at IS NULL
          ORDER BY l.sku
            FOR UPDATE OF l",
    )
    .bind(&warehouse)
    .bind(&user.tenant_id)
    .bind(&count_id)
    .fetch_all(&mut *tx)
    .await?;
    let mut lines: Vec<Map<String, Value>> = rows.iter().map(row_json).collect::<Result<_, _>>()?;
    if let Some(wanted) = &skus {
        let have: Vec<String> = lines.iter().map(|l| str_of(l, "sku")).collect();
        // `sorted(unknown)[0]`: the smallest unknown code point-wise.
        let mut unknown: Vec<&String> = wanted.iter().filter(|s| !have.contains(s)).collect();
        unknown.sort();
        if let Some(first) = unknown.first() {
            return Err(ApiError::app("count_line_not_found", format!("SKU '{first}' is not in this count"), 404,
                json!({"sku": first})));
        }
        lines.retain(|l| wanted.contains(&str_of(l, "sku")));
    }

    let (mut units_added, mut units_removed, mut value_net) = (0.0_f64, 0.0_f64, 0.0_f64);
    let (mut unpriced, mut adjusted, mut unchanged) = (0_i64, 0_i64, 0_i64);
    for l in &lines {
        let sku = str_of(l, "sku");
        let id = str_of(l, "id");
        let current = l.get("current_qty").and_then(Value::as_f64).unwrap_or(0.0);
        let counted = l.get("counted_qty").and_then(Value::as_f64).unwrap_or(0.0);
        let at_count = l.get("system_qty_at_count").and_then(Value::as_f64).unwrap_or(0.0);
        let diff = counted - at_count;
        let new_qty = py_max(0.0, current + diff);
        if (new_qty - current).abs() <= EPS {
            unchanged += 1;
            sqlx::query(
                "UPDATE stock_count_lines SET applied_at = NOW(), applied_by = $1, applied_from = $2, applied_to = $3
                 WHERE id = $4 RETURNING id",
            )
            .bind(&user.user_id)
            .bind(current)
            .bind(current)
            .bind(&id)
            .fetch_optional(&mut *tx)
            .await?;
            continue;
        }
        let mut data = StockData::default();
        data.set("current_stock", Val::F(new_qty));
        data.warehouse = Some(warehouse.clone());
        stock::upsert_stock(&state.pool, &mut tx, state.settings.testing_mode, &user.tenant_id, &sku, &data, "user").await?;
        let delta = new_qty - current;
        let cost = l.get("unit_cost").and_then(Value::as_f64);
        sqlx::query(
            "INSERT INTO stock_adjustments
                 (tenant_id, sku, warehouse, qty_before, qty_after, delta, unit_cost, reason, ref_id, created_by)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
        )
        .bind(&user.tenant_id)
        .bind(&sku)
        .bind(&warehouse)
        .bind(current)
        .bind(new_qty)
        .bind(delta)
        .bind(cost)
        .bind(ADJUSTMENT_REASON)
        .bind(&count_id)
        .bind(&user.user_id)
        .fetch_one(&mut *tx)
        .await?;
        sqlx::query(
            "UPDATE stock_count_lines SET applied_at = NOW(), applied_by = $1, applied_from = $2, applied_to = $3
             WHERE id = $4 RETURNING id",
        )
        .bind(&user.user_id)
        .bind(current)
        .bind(new_qty)
        .bind(&id)
        .fetch_optional(&mut *tx)
        .await?;
        adjusted += 1;
        if delta > 0.0 {
            units_added += delta;
        } else {
            units_removed += -delta;
        }
        match cost {
            Some(c) => value_net += delta * c,
            None => unpriced += 1,
        }
    }
    sqlx::query("UPDATE stock_counts SET status = 'applied', applied_at = NOW(), applied_by = $1 WHERE id = $2 RETURNING id")
        .bind(&user.user_id)
        .bind(&count_id)
        .fetch_optional(&mut *tx)
        .await?;
    tx.commit().await?;

    let mut details = Map::new();
    details.insert("warehouse".into(), json!(warehouse));
    details.insert("lines".into(), json!(adjusted));
    details.insert("units".into(), json!(units_added + units_removed));
    events::record(&state.pool, &user.tenant_id, &user.user_id, &STOCK_COUNT_APPLIED, Some(&count_id), details, None).await;
    Ok(ok(json!({
        "count_id": count_id,
        "warehouse": warehouse,
        "lines_applied": lines.len(),
        "lines_adjusted": adjusted,
        "lines_unchanged": unchanged,
        "units_added": units_added,
        "units_removed": units_removed,
        "net_value": py_round(value_net, 2),
        "unpriced_lines": unpriced,
    })))
}
