//! Purchase-order reception: `GET /inventory/po/{id}/items` and
//! `POST /inventory/po/{id}/receive`, from `backend/api/v1/inventory.py` and
//! `reception_service.receive_po`.
//!
//! A reception adds stock, accumulates `received_qty` on the lines, moves the
//! order's status and, when the order completes, teaches the supplier's REAL
//! lead time (`received_at - generated_at`, rounded to two decimals). All of it
//! is one transaction under the tenant's lock, so a failure anywhere leaves the
//! order exactly as it was.
//!
//! `received_at` is read with CPython's own `datetime.fromisoformat` grammar
//! (`inventory::pydt`, replayed against Python by the differential fixture):
//! a date is midnight UTC, `Z` and offsets shift the instant, naive is UTC.

use std::collections::{BTreeMap, HashMap};

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::PgConnection;

use crate::auth::warehouse_scope::{self};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::inventory::calc::{py_max, py_round, py_sum};
use crate::inventory::events::{self, RECEPTION_RECORDED};
use crate::inventory::pydt::{self, fmt_g, PyDateTime};
use crate::inventory::stock::{self, StockData, Val};
use crate::inventory::validate::float_ge;
use crate::inventory::warehouses::{self, DEFAULT_WAREHOUSE};
use crate::limits::{enforce_limit, take_tenant_lock};
use crate::routes::ok;
use crate::routes::po_payments::{format_po_number, po_not_found};
use crate::routes::sessions::row_json;
use crate::state::AppState;
use crate::validation::{self, as_object, str_field, Bound, Errors, Field, NO_STR_RULES};

const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
const WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

/// `RECEIVABLE_STATES`.
const RECEIVABLE: [&str; 3] = ["pending", "partial", "not_received"];

async fn get_po_items(conn: &mut PgConnection, tenant_id: &str, po_log_id: &str) -> Result<Vec<Map<String, Value>>, ApiError> {
    let rows = sqlx::query(
        "SELECT id, sku, display_name, supplier, supplier_id, signal, status,
                recommended_qty, final_qty, received_qty, unit_cost, warehouse
           FROM inventory_po_items
          WHERE po_log_id = $1 AND tenant_id = $2
          ORDER BY supplier NULLS LAST, sku",
    )
    .bind(po_log_id)
    .bind(tenant_id)
    .fetch_all(&mut *conn)
    .await?;
    Ok(rows.iter().map(row_json).collect::<Result<_, _>>()?)
}

fn f(m: &Map<String, Value>, k: &str) -> f64 {
    // `float(x or 0)`
    m.get(k).and_then(Value::as_f64).unwrap_or(0.0)
}

fn s(m: &Map<String, Value>, k: &str) -> String {
    m.get(k).and_then(Value::as_str).unwrap_or_default().to_string()
}

fn nonempty(m: &Map<String, Value>, k: &str) -> Option<String> {
    m.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(str::to_string)
}

/// `_line_warehouse`.
fn line_warehouse(item: &Map<String, Value>, po: &Map<String, Value>) -> String {
    nonempty(item, "warehouse")
        .or_else(|| nonempty(po, "destination_warehouse"))
        .unwrap_or_else(|| DEFAULT_WAREHOUSE.to_string())
}

fn ordered(items: &[Map<String, Value>]) -> Vec<&Map<String, Value>> {
    items
        .iter()
        .filter(|i| matches!(i.get("status").and_then(Value::as_str), Some("approved") | Some("modified")))
        .collect()
}

// ── GET /inventory/po/{id}/items ─────────────────────────────────────────────

pub async fn items(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&po_log_id])?;
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    warehouse_scope::require_po_in_scope(&state.pool, &sc, &user.tenant_id, &po_log_id).await?;
    let row = sqlx::query("SELECT * FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
        .bind(&po_log_id)
        .bind(&user.tenant_id)
        .fetch_optional(&state.pool)
        .await?;
    let Some(row) = row else { return Err(po_not_found()) };
    let po = row_json(&row)?;
    let mut conn = state.pool.acquire().await?;
    let lines = get_po_items(&mut conn, &user.tenant_id, &po_log_id).await?;
    let status = match po.get("reception_status") {
        Some(Value::Null) | None => json!("pending"),
        Some(v) => v.clone(),
    };
    Ok(ok(json!({
        "po_log_id": po_log_id,
        "reception_status": status,
        "generated_at": po.get("generated_at").cloned().unwrap_or(Value::Null),
        "received_at": po.get("received_at").cloned().unwrap_or(Value::Null),
        "items": lines.into_iter().map(Value::Object).collect::<Vec<_>>(),
    })))
}

// ── POST /inventory/po/{id}/receive ──────────────────────────────────────────

struct ReceiveLine {
    sku: String,
    received_qty: f64,
}

struct ReceiveBody {
    lines: Option<Vec<ReceiveLine>>,
    received_at: Option<String>,
}

fn validate_body(body: &validation::Body) -> Result<ReceiveBody, ApiError> {
    // `body: Optional[ReceptionRequest] = None`.
    let obj = match body {
        validation::Body::Missing | validation::Body::Json(Value::Null) => {
            return Ok(ReceiveBody { lines: None, received_at: None });
        }
        other => validation::body_object(other)?,
    };
    let mut errs = Errors::default();
    let p = [json!("body")];
    let mut lines: Option<Vec<ReceiveLine>> = None;
    match obj.get("lines") {
        None | Some(Value::Null) => {}
        Some(Value::Array(items)) => {
            let mut out = Vec::new();
            for (i, item) in items.iter().enumerate() {
                let at = [json!("body"), json!("lines"), json!(i)];
                let Some(o) = as_object(&mut errs, &at, item) else { continue };
                let sku = str_field(&mut errs, o, &at, "sku", true, false, &NO_STR_RULES);
                let qty = float_ge(&mut errs, o, &at, "received_qty", true, false, Some(Bound::Int(0)), None);
                if let (Field::Value(sku), Field::Value(received_qty)) = (sku, qty) {
                    out.push(ReceiveLine { sku, received_qty });
                }
            }
            lines = Some(out);
        }
        Some(other) => {
            errs.push("list_type", &[json!("body"), json!("lines")], "Input should be a valid list".into(), other, None);
        }
    }
    let received_at = match str_field(&mut errs, &obj, &p, "received_at", false, true, &NO_STR_RULES) {
        Field::Value(v) => Some(v),
        _ => None,
    };
    errs.into_result()?;
    Ok(ReceiveBody { lines, received_at })
}

pub async fn receive(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&po_log_id])?;
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let raw = validation::read_body(ct, &bytes)?;
    let user = auth::current_user(&state, &headers, WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let body = validate_body(&raw)?;

    // Receiving adds stock to the order's destination warehouse.
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    warehouse_scope::require_po_in_scope(&state.pool, &sc, &user.tenant_id, &po_log_id).await?;
    let received_at: Option<PyDateTime> = match body.received_at.as_deref().filter(|v| !v.is_empty()) {
        Some(v) => Some(pydt::fromisoformat(v).ok_or_else(|| {
            ApiError::app("date_invalid_iso", "received_at must be an ISO date (YYYY-MM-DD)", 422,
                json!({"field": "received_at"}))
        })?),
        None => None,
    };

    let pool = &state.pool;
    let tenant = user.tenant_id.as_str();
    let mut conn = pool.acquire().await?;
    let po_row = sqlx::query("SELECT * FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
        .bind(&po_log_id)
        .bind(tenant)
        .fetch_optional(&mut *conn)
        .await?;
    let Some(po_row) = po_row else { return Err(po_not_found()) };
    let po = row_json(&po_row)?;
    if !po.get("cancelled_at").map(Value::is_null).unwrap_or(true) {
        return Err(ApiError::app("po_cancelled", "This order was cancelled; reopen it before recording a reception", 409, json!({})));
    }
    let status_now = po.get("reception_status").cloned().unwrap_or(Value::Null);
    if !status_now.as_str().map(|v| RECEIVABLE.contains(&v)).unwrap_or(false) {
        let shown = match &status_now { Value::String(v) => v.clone(), Value::Null => "None".into(), o => o.to_string() };
        return Err(ApiError::app("reception_already_received",
            format!("This order was already received (status: {shown})"), 409, json!({"status": status_now})));
    }

    let items = get_po_items(&mut conn, tenant, &po_log_id).await?;
    let ord = ordered(&items);
    if ord.is_empty() {
        return Err(ApiError::app("reception_no_ordered_lines", "This order has no ordered lines to receive", 422, json!({})));
    }

    let received = received_at.unwrap_or_else(|| {
        let now = Utc::now();
        use chrono::{Datelike, Timelike};
        PyDateTime { year: now.year(), month: now.month(), day: now.day(), hour: now.hour(), minute: now.minute(),
            second: now.second(), micro: now.timestamp_subsec_micros(), offset_us: Some(0) }
    }).assume_utc();
    let generated: DateTime<Utc> = {
        let r: (Option<DateTime<Utc>>,) = sqlx::query_as("SELECT generated_at FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
            .bind(&po_log_id)
            .bind(tenant)
            .fetch_one(&mut *conn)
            .await?;
        r.0.ok_or_else(ApiError::internal)?
    };
    // Compare by calendar day: a date-only reception on the day the order was
    // generated is valid even if the order carries a later timestamp.
    if received.local_date() < generated.date_naive() {
        return Err(ApiError::app("reception_date_before_order", "The reception date cannot be earlier than the order date", 422, json!({})));
    }

    // Quantity per ordered LINE (keyed by item id, not sku).
    let mut by_item: HashMap<String, f64> = HashMap::new();
    match &body.lines {
        None => {
            // "Everything arrived" books only what is still outstanding per line.
            for i in &ord {
                by_item.insert(s(i, "id"), py_max(0.0, f(i, "final_qty") - f(i, "received_qty")));
            }
        }
        Some(lines) => {
            let mut sku_to_items: BTreeMap<String, Vec<&Map<String, Value>>> = BTreeMap::new();
            for i in &ord {
                sku_to_items.entry(s(i, "sku")).or_default().push(i);
            }
            for ln in lines {
                let sku = ln.sku.clone();
                let Some(matches) = sku_to_items.get(&sku).filter(|m| !m.is_empty()) else {
                    return Err(ApiError::app("reception_sku_not_in_order", format!("SKU '{sku}' is not in this order"), 422,
                        json!({"sku": sku})));
                };
                if matches.len() > 1 {
                    return Err(ApiError::app("reception_sku_multiple_warehouses",
                        format!("SKU '{sku}' appears in more than one warehouse on this order; it cannot be received by SKU, record the full reception instead"),
                        422, json!({"sku": sku})));
                }
                let qty = ln.received_qty;
                let item = matches[0];
                let outstanding = f(item, "final_qty") - f(item, "received_qty");
                if qty > outstanding {
                    return Err(ApiError::app("reception_over_pending",
                        format!("Received quantity of '{sku}' ({}) exceeds the amount pending on this order ({})",
                            fmt_g(qty), fmt_g(outstanding)),
                        422, json!({"sku": sku, "qty": qty, "pending": outstanding})));
                }
                by_item.insert(s(item, "id"), qty);
            }
            for i in &ord {
                by_item.entry(s(i, "id")).or_insert(0.0);
            }
        }
    }
    drop(conn);

    let mut tx = pool.begin().await?;
    take_tenant_lock(&mut tx, tenant).await?;

    // 0. The ceilings, under the lock.
    let existing_wh: Vec<(String,)> = sqlx::query_as("SELECT name FROM warehouses WHERE tenant_id = $1")
        .bind(tenant)
        .fetch_all(&mut *tx)
        .await?;
    let existing_wh: Vec<String> = existing_wh.into_iter().map(|(n,)| n).collect();
    let mut new_wh: Vec<String> = Vec::new();
    for i in &ord {
        if by_item[&s(i, "id")] > 0.0 {
            let w = line_warehouse(i, &po);
            if !existing_wh.contains(&w) && !new_wh.contains(&w) {
                new_wh.push(w);
            }
        }
    }
    if !new_wh.is_empty() {
        let current = warehouses::count(&mut tx, tenant).await?;
        enforce_limit(pool, &mut tx, state.settings.testing_mode, tenant, "max_locations", current, new_wh.len() as i64).await?;
    }
    let keys: Vec<(String, String)> = sqlx::query_as("SELECT sku, warehouse FROM inventory_stock WHERE tenant_id = $1")
        .bind(tenant)
        .fetch_all(&mut *tx)
        .await?;
    let mut new_pairs: Vec<(String, String)> = Vec::new();
    for i in &ord {
        if by_item[&s(i, "id")] > 0.0 {
            let pair = (s(i, "sku"), line_warehouse(i, &po));
            if !keys.contains(&pair) && !new_pairs.contains(&pair) {
                new_pairs.push(pair);
            }
        }
    }
    if !new_pairs.is_empty() {
        let current = stock::count_stock(&mut tx, tenant).await?;
        enforce_limit(pool, &mut tx, state.settings.testing_mode, tenant, "max_skus", current, new_pairs.len() as i64).await?;
    }

    // 1. Accumulate received_qty per line (partial receptions add up).
    for i in &ord {
        let qty = by_item[&s(i, "id")];
        sqlx::query("UPDATE inventory_po_items SET received_qty = COALESCE(received_qty, 0) + $1 WHERE id = $2 AND tenant_id = $3")
            .bind(qty)
            .bind(s(i, "id"))
            .bind(tenant)
            .execute(&mut *tx)
            .await?;
    }

    // 2. Stock: add the received units; create the row when the pair is new.
    for i in &ord {
        let qty = by_item[&s(i, "id")];
        if qty <= 0.0 {
            continue;
        }
        let warehouse = line_warehouse(i, &po);
        let sku = s(i, "sku");
        if stock::get_stock(&mut tx, tenant, &sku, Some(&warehouse)).await?.is_some() {
            sqlx::query("UPDATE inventory_stock SET current_stock = current_stock + $1, updated_at = NOW()
                          WHERE tenant_id = $2 AND sku = $3 AND warehouse = $4")
                .bind(qty)
                .bind(tenant)
                .bind(&sku)
                .bind(&warehouse)
                .execute(&mut *tx)
                .await?;
        } else {
            let mut data = StockData::default();
            data.set("current_stock", Val::F(qty));
            data.set("display_name", i.get("display_name").and_then(Value::as_str).map(|v| Val::S(v.into())).unwrap_or(Val::Null));
            data.set("supplier", i.get("supplier").and_then(Value::as_str).map(|v| Val::S(v.into())).unwrap_or(Val::Null));
            data.warehouse = Some(warehouse.clone());
            stock::upsert_stock(pool, &mut tx, state.settings.testing_mode, tenant, &sku, &data, "user").await?;
        }
        let new_row = stock::get_stock(&mut tx, tenant, &sku, Some(&warehouse)).await?;
        let level = new_row.as_ref().map(|r| f(r, "current_stock")).ok_or_else(ApiError::internal)?;
        sqlx::query("INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) VALUES ($1, $2, $3, $4)")
            .bind(tenant)
            .bind(&sku)
            .bind(level)
            .bind(&warehouse)
            .execute(&mut *tx)
            .await?;
    }

    // 3. Header status.
    let fresh = get_po_items(&mut tx, tenant, &po_log_id).await?;
    let fresh_ordered = ordered(&fresh);
    let fully = fresh_ordered.iter().all(|i| f(i, "received_qty") >= f(i, "final_qty"));
    let any_received = fresh_ordered.iter().any(|i| f(i, "received_qty") > 0.0);
    let status = if fully { "received" } else if any_received { "partial" } else { "not_received" };
    sqlx::query("UPDATE inventory_po_log SET reception_status = $1, received_at = $2, received_by = $3 WHERE id = $4 AND tenant_id = $5")
        .bind(status)
        .bind(received.to_utc())
        .bind(&user.user_id)
        .bind(&po_log_id)
        .bind(tenant)
        .execute(&mut *tx)
        .await?;

    // 4. Learn real lead times: one observation per supplier, only when the
    // order reaches 'received', dated by the event that completed it.
    let micros = received.utc_micros() - i128::from(generated.timestamp_micros());
    let lead_days = py_max(0.0, (micros as f64 / 1e6) / 86400.0);
    let mut observed: Vec<String> = Vec::new();
    if status == "received" {
        let mut supplier_by_key: BTreeMap<String, String> = BTreeMap::new();
        for i in &fresh_ordered {
            let name = crate::pycompat::py_strip(i.get("supplier").and_then(Value::as_str).unwrap_or("")).to_string();
            if name.is_empty() || f(i, "received_qty") <= 0.0 {
                continue;
            }
            let key = name.to_lowercase();
            match supplier_by_key.get(&key) {
                Some(existing) if name >= *existing => {}
                _ => {
                    supplier_by_key.insert(key, name);
                }
            }
        }
        let seen: Vec<(Option<String>,)> = sqlx::query_as(
            "SELECT DISTINCT supplier FROM supplier_lead_time_obs WHERE tenant_id = $1 AND po_log_id = $2",
        )
        .bind(tenant)
        .bind(&po_log_id)
        .fetch_all(&mut *tx)
        .await?;
        let already: Vec<String> = seen
            .into_iter()
            .map(|(sup,)| crate::pycompat::py_strip(&sup.unwrap_or_default()).to_lowercase())
            .collect();
        for (key, prov) in &supplier_by_key {
            if already.contains(key) {
                continue;
            }
            sqlx::query("INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days) VALUES ($1, $2, $3, $4)")
                .bind(tenant)
                .bind(prov)
                .bind(&po_log_id)
                .bind(py_round(lead_days, 2))
                .execute(&mut *tx)
                .await?;
            observed.push(prov.clone());
        }
    }
    tx.commit().await?;
    tracing::info!("[reception] tenant={tenant} po={po_log_id} status={status} lead_days={lead_days:.1} suppliers={observed:?}");

    let mut conn = pool.acquire().await?;
    let items_after = get_po_items(&mut conn, tenant, &po_log_id).await?;
    let result = json!({
        "po_log_id": po_log_id,
        "reception_status": status,
        "received_at": received.isoformat(),
        "lead_time_days": py_round(lead_days, 2),
        "suppliers_observed": observed,
        "items": items_after.iter().cloned().map(Value::Object).collect::<Vec<_>>(),
    });

    // Stock moved and a lead time was learned: the feed says so.
    let po_after = sqlx::query("SELECT * FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
        .bind(&po_log_id)
        .bind(tenant)
        .fetch_optional(&mut *conn)
        .await?;
    let po_after = po_after.map(|r| row_json(&r)).transpose()?.unwrap_or_default();
    let received_items: Vec<&Map<String, Value>> = items_after.iter().filter(|i| f(i, "received_qty") > 0.0).collect();
    let qtys: Vec<f64> = received_items.iter().map(|i| f(i, "received_qty")).collect();
    let po_number = po_after.get("po_number").and_then(Value::as_i64).map(|n| n as i32);
    let mut details = Map::new();
    details.insert("reference".into(), json!(format_po_number(po_number, &po_log_id)));
    details.insert("sku_count".into(), json!(received_items.len()));
    details.insert("units".into(), if qtys.is_empty() { json!(0) } else { json!(py_sum(&qtys)) });
    details.insert("warehouse".into(), po_after.get("destination_warehouse").cloned().unwrap_or(Value::Null));
    events::record(pool, tenant, &user.user_id, &RECEPTION_RECORDED, Some(&po_log_id), details, None).await;
    Ok(ok(result))
}
