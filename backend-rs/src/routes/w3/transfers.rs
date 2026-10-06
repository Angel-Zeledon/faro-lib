//! Inter-warehouse transfers: the five `/inventory/transfers*` routes of
//! `backend/api/v1/inventory.py` and `backend/inventory/transfer_service.py`.
//!
//! A transfer is sent (stock leaves the origin inside one transaction, the
//! goods are "in transit", owned by no warehouse), then received at the
//! destination, possibly in parts; an unreceived one can be cancelled (stock
//! goes home), a partial one closed with the missing units written off to the
//! shrinkage ledger. Every stock move is one conditional UPDATE plus a
//! warehouse-stamped snapshot, like Python's `_adjust_stock`.
//!
//! Service errors are Python `ValueError`s that the router turns into
//! `HTTPException(404 if "not found" in msg.lower() else 422, detail=msg)`; the
//! messages, including the `{x:g}` quantities, are reproduced word for word and
//! pass through the same code bridge.

use std::collections::BTreeMap;

use axum::body::Bytes;
use axum::extract::{Path, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use serde_json::{json, Map, Value};
use sqlx::PgConnection;

use crate::auth::warehouse_scope::{self, in_scope, Scope};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::inventory::calc::{py_round, py_sum};
use crate::inventory::events::{self, TRANSFER_CREATED};
use crate::inventory::pydt::fmt_g;
use crate::inventory::scope::{self, reject_slash, require_in_scope};
use crate::inventory::stock::{self, record_snapshot, StockData, Val};
use crate::inventory::validate::float_ge;
use crate::inventory::warehouses;
use crate::limits::{enforce_limit, take_tenant_lock};
use crate::pycompat::py_strip;
use crate::routes::r1::query_params::QueryParams;
use crate::routes::ok;
use crate::routes::sessions::row_json;
use crate::state::AppState;
use crate::validation::{self, as_object, body_object, list_field, str_field, Bound, Errors, Field, StrRules, NO_STR_RULES};

const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
const WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

const LIST_LIMIT: i64 = 200;

/// `_svc_error`: a service `ValueError` becomes 404 when its text says "not
/// found", else 422, with the sentence as `detail`.
fn svc_error(msg: String) -> ApiError {
    let status = if msg.to_lowercase().contains("not found") { 404 } else { 422 };
    ApiError::http(status, msg)
}

fn f(m: &Map<String, Value>, k: &str) -> f64 {
    m.get(k).and_then(Value::as_f64).unwrap_or(0.0)
}

fn s(m: &Map<String, Value>, k: &str) -> String {
    m.get(k).and_then(Value::as_str).unwrap_or_default().to_string()
}

/// `get_transfer`: the header row plus its lines.
async fn get_transfer(conn: &mut PgConnection, tenant_id: &str, id: &str) -> Result<Option<Map<String, Value>>, ApiError> {
    let header = sqlx::query("SELECT * FROM inventory_transfer_log WHERE id = $1 AND tenant_id = $2")
        .bind(id)
        .bind(tenant_id)
        .fetch_optional(&mut *conn)
        .await?;
    let Some(header) = header else { return Ok(None) };
    let mut header = row_json(&header)?;
    let items = sqlx::query(
        "SELECT id, sku, qty_sent, qty_received FROM inventory_transfer_items
          WHERE transfer_id = $1 AND tenant_id = $2 ORDER BY sku",
    )
    .bind(id)
    .bind(tenant_id)
    .fetch_all(&mut *conn)
    .await?;
    let items: Vec<Value> = items.iter().map(|r| row_json(r).map(Value::Object)).collect::<Result<_, _>>()?;
    header.insert("items".into(), Value::Array(items));
    Ok(Some(header))
}

/// `_adjust_stock`: apply a delta and record the warehouse-stamped snapshot.
/// A decrement carries an atomic floor so two concurrent sends cannot drive
/// the row negative.
async fn adjust_stock(
    conn: &mut PgConnection,
    tenant_id: &str,
    sku: &str,
    warehouse: &str,
    delta: f64,
) -> Result<f64, ApiError> {
    let row: Option<(f64,)> = if delta < 0.0 {
        sqlx::query_as(
            "UPDATE inventory_stock SET current_stock = current_stock + $1, updated_at = NOW()
              WHERE tenant_id = $2 AND sku = $3 AND warehouse = $4 AND current_stock >= $5
              RETURNING current_stock",
        )
        .bind(delta)
        .bind(tenant_id)
        .bind(sku)
        .bind(warehouse)
        .bind(-delta)
        .fetch_optional(&mut *conn)
        .await?
    } else {
        sqlx::query_as(
            "UPDATE inventory_stock SET current_stock = current_stock + $1, updated_at = NOW()
              WHERE tenant_id = $2 AND sku = $3 AND warehouse = $4
              RETURNING current_stock",
        )
        .bind(delta)
        .bind(tenant_id)
        .bind(sku)
        .bind(warehouse)
        .fetch_optional(&mut *conn)
        .await?
    };
    let Some((new_stock,)) = row else {
        if delta < 0.0 {
            return Err(svc_error(format!(
                "Insufficient stock of '{sku}' in '{warehouse}' (it may have changed from another operation)"
            )));
        }
        // `row["current_stock"]` on None: an unhandled TypeError in Python.
        return Err(ApiError::internal());
    };
    record_snapshot(conn, tenant_id, sku, new_stock, warehouse).await?;
    Ok(new_stock)
}

async fn guard(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    write: bool,
) -> Result<(CurrentUser, Scope), ApiError> {
    let user = auth::current_user(state, headers, if write { WRITE } else { READ }, actors).await?;
    if write {
        auth::require_analyst_or_above(state, &user).await?;
    }
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    Ok((user, sc))
}

/// `_require_transfer_end_in_scope`: 403 unless the end this action works on is
/// the caller's; a transfer that does not exist is left for the service's 404.
async fn require_end_in_scope(
    state: &AppState,
    user: &CurrentUser,
    sc: &Scope,
    transfer_id: &str,
    end: &str,
) -> Result<(), ApiError> {
    if sc.is_none() {
        return Ok(());
    }
    let mut conn = state.pool.acquire().await?;
    if let Some(t) = get_transfer(&mut conn, &user.tenant_id, transfer_id).await? {
        require_in_scope(sc, t.get(end).and_then(Value::as_str))?;
    }
    Ok(())
}

// ── POST /inventory/transfers ────────────────────────────────────────────────

struct Line {
    sku: String,
    qty: f64,
}

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let raw = validation::read_body(ct, &bytes)?;
    let user = auth::current_user(&state, &headers, WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let obj = body_object(&raw)?;

    let mut errs = Errors::default();
    let p = [json!("body")];
    let from = str_field(&mut errs, &obj, &p, "from_warehouse", true, false, &NO_STR_RULES);
    let to = str_field(&mut errs, &obj, &p, "to_warehouse", true, false, &NO_STR_RULES);
    let mut lines: Vec<Line> = Vec::new();
    match obj.get("items") {
        None => errs.push("missing", &[json!("body"), json!("items")], "Field required".into(), &Value::Object(obj.clone()), None),
        Some(Value::Array(items)) => {
            for (i, item) in items.iter().enumerate() {
                let at = [json!("body"), json!("items"), json!(i)];
                let Some(o) = as_object(&mut errs, &at, item) else { continue };
                let sku = str_field(&mut errs, o, &at, "sku", true, false, &NO_STR_RULES);
                let qty = crate::validation::float_field(&mut errs, o, &at, "qty", true, false, Some(Bound::Int(0)), None);
                if let (Field::Value(sku), Field::Value(qty)) = (sku, qty) {
                    lines.push(Line { sku, qty });
                }
            }
        }
        Some(other) => errs.push("list_type", &[json!("body"), json!("items")], "Input should be a valid list".into(), other, None),
    }
    let notes = str_field(&mut errs, &obj, &p, "notes", false, true,
        &StrRules { min_length: None, max_length: Some(2000), pattern: None });
    errs.into_result()?;
    let (Field::Value(from), Field::Value(to)) = (from, to) else { return Err(ApiError::internal()) };
    let notes = match notes { Field::Value(n) => Some(n), _ => None };

    let tenant = user.tenant_id.as_str();
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    let mut conn = state.pool.acquire().await?;
    // Shipping takes stock OUT of the origin: that is the warehouse being acted on.
    let canon = warehouses::resolve_canonical_name(&mut conn, tenant, Some(&from)).await?;
    require_in_scope(&sc, Some(&canon))?;

    // ── transfer_service.create_transfer ───────────────────────────────────
    let from_wh = py_strip(&from).to_string();
    let to_wh = py_strip(&to).to_string();
    if from_wh.is_empty() || to_wh.is_empty() {
        return Err(svc_error("Origin and destination warehouses are required".into()));
    }
    if from_wh == to_wh {
        return Err(svc_error("Origin and destination warehouses must differ".into()));
    }
    if !warehouses::exists(&mut conn, tenant, &from_wh).await? {
        return Err(svc_error(format!("Warehouse '{from_wh}' not found")));
    }
    if !warehouses::exists(&mut conn, tenant, &to_wh).await? {
        return Err(svc_error(format!("Warehouse '{to_wh}' not found")));
    }
    if lines.is_empty() {
        return Err(svc_error("A transfer needs at least one item".into()));
    }
    // Merge duplicate SKUs so the availability check sees the real total.
    let mut qty_by_sku: Vec<(String, f64)> = Vec::new();
    for ln in &lines {
        let sku = py_strip(&ln.sku).to_string();
        if sku.is_empty() {
            return Err(svc_error("Every transfer line needs a SKU".into()));
        }
        if ln.qty <= 0.0 {
            return Err(svc_error(format!("Quantity for '{sku}' must be positive")));
        }
        match qty_by_sku.iter_mut().find(|(k, _)| *k == sku) {
            Some(slot) => slot.1 += ln.qty,
            None => qty_by_sku.push((sku, ln.qty)),
        }
    }
    let skus: Vec<String> = qty_by_sku.iter().map(|(k, _)| k.clone()).collect();
    let avail_rows: Vec<(String, f64)> = sqlx::query_as(
        "SELECT sku, current_stock FROM inventory_stock WHERE tenant_id = $1 AND warehouse = $2 AND sku = ANY($3)",
    )
    .bind(tenant)
    .bind(&from_wh)
    .bind(&skus)
    .fetch_all(&mut *conn)
    .await?;
    for (sku, qty) in &qty_by_sku {
        let available = avail_rows.iter().find(|(k, _)| k == sku).map(|(_, v)| *v).unwrap_or(0.0);
        if *qty > available {
            return Err(svc_error(format!(
                "Insufficient stock of '{sku}' in '{from_wh}' ({} available, {} requested)",
                fmt_g(available), fmt_g(*qty)
            )));
        }
    }
    let lane: Option<(i32,)> = sqlx::query_as(
        "SELECT lead_time_days FROM transfer_lanes WHERE tenant_id = $1 AND from_warehouse = $2 AND to_warehouse = $3",
    )
    .bind(tenant)
    .bind(&from_wh)
    .bind(&to_wh)
    .fetch_optional(&mut *conn)
    .await?;
    // An unconfigured lane is the documented default: one day.
    let lead_time_days = lane.map(|(d,)| d).unwrap_or(1);
    drop(conn);

    let mut tx = state.pool.begin().await?;
    let header = sqlx::query(
        "INSERT INTO inventory_transfer_log
             (tenant_id, from_warehouse, to_warehouse, status, notes, created_by, lead_time_days, expected_arrival)
         VALUES ($1, $2, $3, 'in_transit', $4, $5, $6, NOW() + ($7::double precision * INTERVAL '1 day'))
         RETURNING *",
    )
    .bind(tenant)
    .bind(&from_wh)
    .bind(&to_wh)
    .bind(&notes)
    .bind(&user.user_id)
    .bind(lead_time_days)
    .bind(f64::from(lead_time_days))
    .fetch_one(&mut *tx)
    .await?;
    let header = row_json(&header)?;
    let transfer_id = s(&header, "id");
    let mut sorted = qty_by_sku.clone();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    for (sku, qty) in &sorted {
        sqlx::query("INSERT INTO inventory_transfer_items (tenant_id, transfer_id, sku, qty_sent) VALUES ($1, $2, $3, $4)")
            .bind(tenant)
            .bind(&transfer_id)
            .bind(sku)
            .bind(qty)
            .execute(&mut *tx)
            .await?;
        adjust_stock(&mut tx, tenant, sku, &from_wh, -qty).await?;
    }
    let result = get_transfer(&mut tx, tenant, &transfer_id).await?.ok_or_else(ApiError::internal)?;
    tx.commit().await?;
    tracing::info!("[transfer] sent tenant={tenant} id={transfer_id} {from_wh}->{to_wh} skus={}", qty_by_sku.len());

    let units: Vec<f64> = lines.iter().map(|l| l.qty).collect();
    let mut details = Map::new();
    details.insert("sku_count".into(), json!(lines.len()));
    details.insert("units".into(), if units.is_empty() { json!(0) } else { json!(py_sum(&units)) });
    let or_body = |k: &str, fallback: &str| match result.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()) {
        Some(v) => json!(v),
        None => json!(fallback),
    };
    details.insert("from_warehouse".into(), or_body("from_warehouse", &from));
    details.insert("to_warehouse".into(), or_body("to_warehouse", &to));
    events::record(&state.pool, tenant, &user.user_id, &TRANSFER_CREATED, Some(&transfer_id), details, None).await;
    Ok((StatusCode::CREATED, ok(Value::Object(result))))
}

// ── GET /inventory/transfers ─────────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let (user, sc) = guard(&state, &actors, &headers, false).await?;
    let q = QueryParams::parse(raw.as_deref());
    let status = q.get("status").filter(|v| !v.is_empty()).map(str::to_string);

    let mut sql = String::from("SELECT * FROM inventory_transfer_log WHERE tenant_id = $1");
    if status.is_some() {
        sql.push_str(" AND status = $2");
    }
    sql.push_str(&format!(" ORDER BY created_at DESC LIMIT {LIST_LIMIT}"));
    let mut query = sqlx::query(&sql).bind(&user.tenant_id);
    if let Some(st) = &status {
        query = query.bind(st);
    }
    let headers_rows = query.fetch_all(&state.pool).await?;
    let mut out: Vec<Map<String, Value>> = Vec::new();
    if !headers_rows.is_empty() {
        let hs: Vec<Map<String, Value>> = headers_rows.iter().map(row_json).collect::<Result<_, _>>()?;
        let ids: Vec<String> = hs.iter().map(|h| s(h, "id")).collect();
        let items = sqlx::query(
            "SELECT id, transfer_id, sku, qty_sent, qty_received FROM inventory_transfer_items
              WHERE tenant_id = $1 AND transfer_id = ANY($2) ORDER BY sku",
        )
        .bind(&user.tenant_id)
        .bind(&ids)
        .fetch_all(&state.pool)
        .await?;
        let mut by_transfer: BTreeMap<String, Vec<Value>> = BTreeMap::new();
        for r in &items {
            let m = row_json(r)?;
            by_transfer.entry(s(&m, "transfer_id")).or_default().push(Value::Object(m));
        }
        for mut h in hs {
            let id = s(&h, "id");
            h.insert("items".into(), Value::Array(by_transfer.remove(&id).unwrap_or_default()));
            out.push(h);
        }
    }
    if sc.is_some() {
        // Visible from either end: the receiving side must see what is coming.
        out.retain(|t| {
            in_scope(&sc, t.get("from_warehouse").and_then(Value::as_str))
                || in_scope(&sc, t.get("to_warehouse").and_then(Value::as_str))
        });
    }
    Ok(ok(Value::Array(out.into_iter().map(Value::Object).collect())))
}

// ── POST /inventory/transfers/{id}/receive ───────────────────────────────────

/// Python's `float(x or 0)` for one value of a `list[dict]` line: falsy is 0,
/// numbers and numeric strings convert, anything else is the error Python
/// raises (a `ValueError` message the router turns into a 422, or a `TypeError`
/// that is a 500).
fn float_or_zero(v: Option<&Value>) -> Result<f64, ApiError> {
    match v {
        None | Some(Value::Null) | Some(Value::Bool(false)) => Ok(0.0),
        Some(Value::Bool(true)) => Ok(1.0),
        Some(Value::Number(n)) => Ok(n.as_f64().unwrap_or(0.0)),
        Some(Value::String(t)) if t.is_empty() => Ok(0.0),
        Some(Value::String(t)) => crate::inventory::pydt::py_float_from_str(t).ok_or_else(|| {
            svc_error(format!("could not convert string to float: {}", crate::pyjson::repr_str(t)))
        }),
        Some(Value::Array(a)) if a.is_empty() => Ok(0.0),
        Some(Value::Object(o)) if o.is_empty() => Ok(0.0),
        Some(_) => Err(ApiError::internal()),
    }
}

pub async fn receive(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(transfer_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    reject_slash(&[&transfer_id])?;
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let raw = validation::read_body(ct, &bytes)?;
    let user = auth::current_user(&state, &headers, WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    // `body: TransferReceive` (required): lines is Optional[list[dict]].
    let obj = body_object(&raw)?;
    let mut errs = Errors::default();
    let mut lines: Option<Vec<Map<String, Value>>> = None;
    match obj.get("lines") {
        None | Some(Value::Null) => {}
        Some(Value::Array(items)) => {
            let mut out = Vec::new();
            for (i, item) in items.iter().enumerate() {
                match item {
                    Value::Object(m) => out.push(m.clone()),
                    other => errs.push("dict_type", &[json!("body"), json!("lines"), json!(i)],
                        "Input should be a valid dictionary".into(), other, None),
                }
            }
            lines = Some(out);
        }
        Some(other) => errs.push("list_type", &[json!("body"), json!("lines")], "Input should be a valid list".into(), other, None),
    }
    errs.into_result()?;

    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    require_end_in_scope(&state, &user, &sc, &transfer_id, "to_warehouse").await?;

    let tenant = user.tenant_id.as_str();
    let mut conn = state.pool.acquire().await?;
    let t = get_transfer(&mut conn, tenant, &transfer_id).await?.ok_or_else(|| svc_error("Transfer not found".into()))?;
    let status = s(&t, "status");
    if status != "in_transit" && status != "partial" {
        return Err(svc_error(format!("This transfer cannot be received (status: {status})")));
    }
    let items: Vec<Map<String, Value>> = t["items"].as_array().cloned().unwrap_or_default().into_iter()
        .filter_map(|v| if let Value::Object(m) = v { Some(m) } else { None }).collect();
    // outstanding: sku -> sent - received (dict order = item order)
    let mut outstanding: Vec<(String, f64)> = Vec::new();
    for i in &items {
        let v = f(i, "qty_sent") - f(i, "qty_received");
        match outstanding.iter_mut().find(|(k, _)| *k == s(i, "sku")) {
            Some(slot) => slot.1 = v,
            None => outstanding.push((s(i, "sku"), v)),
        }
    }
    let mut to_receive: Vec<(String, f64)> = Vec::new();
    match &lines {
        None => {
            for (sku, qty) in &outstanding {
                if *qty > 0.0 {
                    to_receive.push((sku.clone(), *qty));
                }
            }
        }
        Some(lines) => {
            for ln in lines {
                let sku = match ln.get("sku") {
                    None | Some(Value::Null) | Some(Value::Bool(false)) => String::new(),
                    Some(Value::String(v)) => v.clone(),
                    Some(Value::Number(n)) if n.as_f64() == Some(0.0) => String::new(),
                    Some(v) => crate::pyjson::str_of(v),
                };
                let Some((_, out_qty)) = outstanding.iter().find(|(k, _)| *k == sku) else {
                    return Err(svc_error(format!("SKU '{sku}' is not part of this transfer")));
                };
                let qty = float_or_zero(ln.get("received_qty"))?;
                if qty < 0.0 {
                    return Err(svc_error(format!("Negative received quantity for '{sku}'")));
                }
                if qty > *out_qty {
                    return Err(svc_error(format!(
                        "'{sku}': receiving {} but only {} outstanding", fmt_g(qty), fmt_g(*out_qty)
                    )));
                }
                if qty > 0.0 {
                    match to_receive.iter_mut().find(|(k, _)| *k == sku) {
                        Some(slot) => slot.1 = qty,
                        None => to_receive.push((sku, qty)),
                    }
                }
            }
        }
    }
    if to_receive.is_empty() {
        return Err(svc_error("Nothing to receive".into()));
    }
    let dest = s(&t, "to_warehouse");
    let origin = s(&t, "from_warehouse");
    drop(conn);

    let mut tx = state.pool.begin().await?;
    take_tenant_lock(&mut tx, tenant).await?;
    let keys: Vec<(String, String)> = sqlx::query_as("SELECT sku, warehouse FROM inventory_stock WHERE tenant_id = $1")
        .bind(tenant)
        .fetch_all(&mut *tx)
        .await?;
    let new_pairs: Vec<String> = to_receive.iter().map(|(k, _)| k.clone())
        .filter(|sku| !keys.contains(&(sku.clone(), dest.clone()))).collect();
    if !new_pairs.is_empty() {
        let current = stock::count_stock(&mut tx, tenant).await?;
        enforce_limit(&state.pool, &mut tx, state.settings.testing_mode, tenant, "max_skus", current, new_pairs.len() as i64).await?;
    }
    let mut sorted = to_receive.clone();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    for (sku, qty) in &sorted {
        // Atomic cap: accept the receipt only if qty_received stays <= qty_sent.
        let accepted: Option<(f64,)> = sqlx::query_as(
            "UPDATE inventory_transfer_items SET qty_received = COALESCE(qty_received, 0) + $1
              WHERE transfer_id = $2 AND tenant_id = $3 AND sku = $4 AND COALESCE(qty_received, 0) + $5 <= qty_sent
              RETURNING qty_received",
        )
        .bind(qty)
        .bind(&transfer_id)
        .bind(tenant)
        .bind(sku)
        .bind(qty)
        .fetch_optional(&mut *tx)
        .await?;
        if accepted.is_none() {
            return Err(svc_error(format!(
                "'{sku}': received quantity exceeds what was sent (it may have been received from another operation)"
            )));
        }
        if new_pairs.contains(sku) {
            let origin_row = stock::get_stock(&mut tx, tenant, sku, Some(&origin)).await?;
            let origin_row = origin_row.unwrap_or_default();
            let mut data = StockData::default();
            data.set("current_stock", Val::F(*qty));
            data.set("display_name", origin_row.get("display_name").and_then(Value::as_str).map(|v| Val::S(v.into())).unwrap_or(Val::Null));
            data.set("supplier", origin_row.get("supplier").and_then(Value::as_str).map(|v| Val::S(v.into())).unwrap_or(Val::Null));
            data.warehouse = Some(dest.clone());
            stock::upsert_stock(&state.pool, &mut tx, state.settings.testing_mode, tenant, sku, &data, "user").await?;
        } else {
            adjust_stock(&mut tx, tenant, sku, &dest, *qty).await?;
        }
    }
    let fully = outstanding.iter().all(|(sku, out)| {
        let got = to_receive.iter().find(|(k, _)| k == sku).map(|(_, q)| *q).unwrap_or(0.0);
        out - got <= 0.0
    });
    let new_status = if fully { "received" } else { "partial" };
    sqlx::query(
        "UPDATE inventory_transfer_log SET status = $1, received_at = CASE WHEN $2 THEN NOW() ELSE received_at END
          WHERE id = $3 AND tenant_id = $4",
    )
    .bind(new_status)
    .bind(fully)
    .bind(&transfer_id)
    .bind(tenant)
    .execute(&mut *tx)
    .await?;
    let result = get_transfer(&mut tx, tenant, &transfer_id).await?.ok_or_else(ApiError::internal)?;
    tx.commit().await?;
    tracing::info!("[transfer] received tenant={tenant} id={transfer_id} status={new_status}");
    Ok(ok(Value::Object(result)))
}

// ── POST /inventory/transfers/{id}/cancel ────────────────────────────────────

pub async fn cancel(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(transfer_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    reject_slash(&[&transfer_id])?;
    let (user, sc) = guard(&state, &actors, &headers, true).await?;
    require_end_in_scope(&state, &user, &sc, &transfer_id, "from_warehouse").await?;
    let tenant = user.tenant_id.as_str();
    let mut conn = state.pool.acquire().await?;
    let t = get_transfer(&mut conn, tenant, &transfer_id).await?.ok_or_else(|| svc_error("Transfer not found".into()))?;
    let items: Vec<Map<String, Value>> = t["items"].as_array().cloned().unwrap_or_default().into_iter()
        .filter_map(|v| if let Value::Object(m) = v { Some(m) } else { None }).collect();
    if s(&t, "status") != "in_transit" || items.iter().any(|i| f(i, "qty_received") > 0.0) {
        return Err(svc_error("Only in-transit transfers with nothing received can be cancelled".into()));
    }
    drop(conn);
    let origin = s(&t, "from_warehouse");
    let mut tx = state.pool.begin().await?;
    for i in &items {
        adjust_stock(&mut tx, tenant, &s(i, "sku"), &origin, f(i, "qty_sent")).await?;
    }
    sqlx::query("UPDATE inventory_transfer_log SET status = 'cancelled' WHERE id = $1 AND tenant_id = $2")
        .bind(&transfer_id)
        .bind(tenant)
        .execute(&mut *tx)
        .await?;
    let result = get_transfer(&mut tx, tenant, &transfer_id).await?.ok_or_else(ApiError::internal)?;
    tx.commit().await?;
    tracing::info!("[transfer] cancelled tenant={tenant} id={transfer_id}");
    Ok(ok(Value::Object(result)))
}

// ── POST /inventory/transfers/{id}/close ─────────────────────────────────────

pub async fn close(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(transfer_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    reject_slash(&[&transfer_id])?;
    let (user, sc) = guard(&state, &actors, &headers, true).await?;
    require_end_in_scope(&state, &user, &sc, &transfer_id, "to_warehouse").await?;
    let tenant = user.tenant_id.as_str();
    let mut conn = state.pool.acquire().await?;
    let t = get_transfer(&mut conn, tenant, &transfer_id).await?.ok_or_else(|| svc_error("Transfer not found".into()))?;
    if s(&t, "status") != "partial" {
        return Err(svc_error(
            "Only partially received transfers can be closed with a loss (cancel an in-transit transfer instead)".into(),
        ));
    }
    let items: Vec<Map<String, Value>> = t["items"].as_array().cloned().unwrap_or_default().into_iter()
        .filter_map(|v| if let Value::Object(m) = v { Some(m) } else { None }).collect();
    let mut outstanding: Vec<(String, f64)> = Vec::new();
    for i in &items {
        let v = f(i, "qty_sent") - f(i, "qty_received");
        if v > 0.0 {
            match outstanding.iter_mut().find(|(k, _)| *k == s(i, "sku")) {
                Some(slot) => slot.1 = v,
                None => outstanding.push((s(i, "sku"), v)),
            }
        }
    }
    if outstanding.is_empty() {
        return Err(svc_error("Nothing outstanding to write off".into()));
    }
    let origin = s(&t, "from_warehouse");
    let skus: Vec<String> = outstanding.iter().map(|(k, _)| k.clone()).collect();
    let cost_rows: Vec<(String, Option<f64>)> = sqlx::query_as(
        "SELECT sku, unit_cost FROM inventory_stock WHERE tenant_id = $1 AND warehouse = $2 AND sku = ANY($3)",
    )
    .bind(tenant)
    .bind(&origin)
    .bind(&skus)
    .fetch_all(&mut *conn)
    .await?;
    drop(conn);

    let mut tx = state.pool.begin().await?;
    let mut sorted = outstanding.clone();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    let note = format!("{origin} \u{2192} {}", s(&t, "to_warehouse"));
    for (sku, qty) in &sorted {
        let unit_cost = cost_rows.iter().find(|(k, _)| k == sku).and_then(|(_, c)| *c);
        let total_cost = unit_cost.map(|c| py_round(qty * c, 2));
        sqlx::query(
            "INSERT INTO inventory_shrinkage
                 (tenant_id, sku, warehouse, quantity, reason, unit_cost, total_cost, notes, created_by, created_at)
             VALUES ($1, $2, $3, $4, 'transfer_loss', $5, $6, $7, $8, NOW())",
        )
        .bind(tenant)
        .bind(sku)
        .bind(&origin)
        .bind(qty)
        .bind(unit_cost)
        .bind(total_cost)
        .bind(&note)
        .bind(&user.user_id)
        .execute(&mut *tx)
        .await?;
    }
    sqlx::query("UPDATE inventory_transfer_log SET status = 'closed' WHERE id = $1 AND tenant_id = $2")
        .bind(&transfer_id)
        .bind(tenant)
        .execute(&mut *tx)
        .await?;
    let result = get_transfer(&mut tx, tenant, &transfer_id).await?.ok_or_else(ApiError::internal)?;
    tx.commit().await?;
    tracing::info!("[transfer] closed-with-loss tenant={tenant} id={transfer_id} skus={}", outstanding.len());
    let _ = (scope::count_not_found, list_field);
    Ok(ok(Value::Object(result)))
}
