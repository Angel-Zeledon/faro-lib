//! The inverses of `receive_po` and `mark_po_sent`:
//! `backend/api/v1/reception_reversals.py` and
//! `reception_service.unreceive_po` / `unsend_po`.
//!
//! `POST /inventory/po/{id}/unreceive` takes a reception's units back out of
//! stock, resets the lines and the order, and removes the lead-time
//! observations that reception taught the supplier scorecard, all in one
//! transaction under the tenant's lock, and refuses (409, naming the short
//! SKU/warehouse) rather than go negative. `POST .../unsend` clears `sent_at`.
//! `receive_po` itself (learned lead times, price breaks, webhooks) stays Python.

use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::auth::{Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::inventory::calc::py_sum;
use crate::inventory::events::{self, ORDER_UNSENT, RECEPTION_UNDONE};
use crate::inventory::stock;
use crate::inventory::warehouses::DEFAULT_WAREHOUSE;
use crate::limits::take_tenant_lock;
use crate::routes::ok;
use crate::routes::po_payments::{format_po_number, po_not_found, po_writer};
use crate::routes::sessions::row_json;
use crate::state::AppState;

/// Router tag `inventory-reversals` is exposed to keys (write scope).
const ROUTE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

async fn get_po(pool: &PgPool, tenant_id: &str, po_log_id: &str) -> Result<Option<Map<String, Value>>, ApiError> {
    let row = sqlx::query("SELECT * FROM inventory_po_log WHERE id = $1 AND tenant_id = $2")
        .bind(po_log_id)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    Ok(row.map(|r| row_json(&r)).transpose()?)
}

/// `get_po_items`.
async fn get_po_items(conn: &mut sqlx::PgConnection, tenant_id: &str, po_log_id: &str) -> Result<Vec<Map<String, Value>>, ApiError> {
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

fn str_or_none(row: &Map<String, Value>, key: &str) -> Option<String> {
    row.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string)
}

/// `_line_warehouse`: the line's own, else the order's destination, else the default.
fn line_warehouse(item: &Map<String, Value>, po: &Map<String, Value>) -> String {
    str_or_none(item, "warehouse")
        .or_else(|| str_or_none(po, "destination_warehouse"))
        .unwrap_or_else(|| DEFAULT_WAREHOUSE.to_string())
}

pub async fn unreceive(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&po_log_id])?;
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let pool = &state.pool;
    let po = get_po(pool, &user.tenant_id, &po_log_id).await?;
    // The router reads the order BEFORE the service, for the event's reference.
    let po_before = po.clone().unwrap_or_default();
    let Some(po) = po else { return Err(po_not_found()) };
    if po.get("received_at").map(Value::is_null).unwrap_or(true) {
        return Err(ApiError::app("reception_nothing_to_undo", "This order has no recorded reception to undo", 409, json!({})));
    }

    let mut conn = pool.acquire().await?;
    let items = get_po_items(&mut conn, &user.tenant_id, &po_log_id).await?;
    drop(conn);
    let ordered: Vec<&Map<String, Value>> = items
        .iter()
        .filter(|i| matches!(i.get("status").and_then(Value::as_str), Some("approved") | Some("modified")))
        .collect();

    // Aggregate by (sku, warehouse), keeping first-seen order like a dict.
    let mut to_remove: Vec<((String, String), f64)> = Vec::new();
    for i in &ordered {
        let qty = i.get("received_qty").and_then(Value::as_f64).unwrap_or(0.0);
        if qty > 0.0 {
            let key = (i["sku"].as_str().unwrap_or("").to_string(), line_warehouse(i, &po));
            match to_remove.iter_mut().find(|(k, _)| *k == key) {
                Some(slot) => slot.1 += qty,
                None => to_remove.push((key, qty)),
            }
        }
    }

    let mut tx = pool.begin().await?;
    take_tenant_lock(&mut tx, &user.tenant_id).await?;

    // Pre-check EVERY touched row before writing anything: all or nothing.
    let mut shortfalls: Vec<Value> = Vec::new();
    for ((sku, warehouse), qty) in &to_remove {
        let row = stock::get_stock(&mut tx, &user.tenant_id, sku, Some(warehouse)).await?;
        let available = row.as_ref().and_then(|r| r.get("current_stock")).and_then(Value::as_f64).unwrap_or(0.0);
        if available < *qty {
            shortfalls.push(json!({"sku": sku, "warehouse": warehouse, "available": available, "needed": qty}));
        }
    }
    if !shortfalls.is_empty() {
        return Err(ApiError::app(
            "reception_undo_insufficient_stock",
            "Cannot undo this reception: some of the received units are no longer in stock (sold, transferred, or written off since the reception)",
            409,
            json!({"shortfalls": shortfalls}),
        ));
    }

    for ((sku, warehouse), qty) in &to_remove {
        sqlx::query(
            "UPDATE inventory_stock SET current_stock = current_stock - $1, updated_at = NOW()
              WHERE tenant_id = $2 AND sku = $3 AND warehouse = $4",
        )
        .bind(qty)
        .bind(&user.tenant_id)
        .bind(sku)
        .bind(warehouse)
        .execute(&mut *tx)
        .await?;
        // A point-in-time snapshot of the reversal, so the history shows an event, not a gap.
        let new_row = stock::get_stock(&mut tx, &user.tenant_id, sku, Some(warehouse)).await?;
        let level = new_row.as_ref().and_then(|r| r.get("current_stock")).and_then(Value::as_f64);
        let Some(level) = level else { return Err(ApiError::internal()) };
        sqlx::query("INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) VALUES ($1, $2, $3, $4)")
            .bind(&user.tenant_id)
            .bind(sku)
            .bind(level)
            .bind(warehouse)
            .execute(&mut *tx)
            .await?;
    }

    for i in &ordered {
        sqlx::query("UPDATE inventory_po_items SET received_qty = 0 WHERE id = $1 AND tenant_id = $2")
            .bind(i["id"].as_str().unwrap_or(""))
            .bind(&user.tenant_id)
            .execute(&mut *tx)
            .await?;
    }
    sqlx::query(
        "UPDATE inventory_po_log SET reception_status = 'pending', received_at = NULL, received_by = NULL
          WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&po_log_id)
    .bind(&user.tenant_id)
    .execute(&mut *tx)
    .await?;

    // Un-teach the lead time this reception taught.
    let unlearned: Vec<(Option<String>,)> = sqlx::query_as(
        "SELECT DISTINCT supplier FROM supplier_lead_time_obs WHERE tenant_id = $1 AND po_log_id = $2",
    )
    .bind(&user.tenant_id)
    .bind(&po_log_id)
    .fetch_all(&mut *tx)
    .await?;
    sqlx::query("DELETE FROM supplier_lead_time_obs WHERE tenant_id = $1 AND po_log_id = $2")
        .bind(&user.tenant_id)
        .bind(&po_log_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    // `sum(to_remove.values())`: the int 0 when nothing was removed.
    let qtys: Vec<f64> = to_remove.iter().map(|(_, q)| *q).collect();
    let units_removed = if qtys.is_empty() { json!(0) } else { json!(py_sum(&qtys)) };
    let mut skus: Vec<&String> = to_remove.iter().map(|((s, _), _)| s).collect();
    skus.sort();
    skus.dedup();
    tracing::info!("[reception] UNDO tenant={} po={} undone_by={} units={}", user.tenant_id, po_log_id, user.user_id, units_removed);

    let mut conn = pool.acquire().await?;
    let items_after = get_po_items(&mut conn, &user.tenant_id, &po_log_id).await?;
    let result = json!({
        "po_log_id": po_log_id,
        "reception_status": "pending",
        "units_removed": units_removed,
        "sku_count": skus.len(),
        "suppliers_lead_time_unlearned": unlearned.iter().map(|(s,)| json!(s)).collect::<Vec<_>>(),
        "items": items_after.into_iter().map(Value::Object).collect::<Vec<_>>(),
    });

    let po_number = po_before.get("po_number").and_then(Value::as_i64).map(|n| n as i32);
    let mut details = Map::new();
    details.insert("reference".into(), json!(format_po_number(po_number, &po_log_id)));
    details.insert("sku_count".into(), result["sku_count"].clone());
    details.insert("units".into(), result["units_removed"].clone());
    details.insert("warehouse".into(), po_before.get("destination_warehouse").cloned().unwrap_or(Value::Null));
    events::record(pool, &user.tenant_id, &user.user_id, &RECEPTION_UNDONE, Some(&po_log_id), details, Some("reversed_by_user")).await;
    Ok(ok(result))
}

pub async fn unsend(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::inventory::scope::reject_slash(&[&po_log_id])?;
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let pool = &state.pool;
    let Some(po) = get_po(pool, &user.tenant_id, &po_log_id).await? else { return Err(po_not_found()) };
    if po.get("sent_at").map(Value::is_null).unwrap_or(true) {
        return Err(ApiError::app("po_not_sent", "This order has not been marked as sent; there is nothing to undo", 409, json!({})));
    }
    let status = po.get("reception_status").cloned().unwrap_or(Value::Null);
    if status.as_str() != Some("pending") {
        return Err(ApiError::app(
            "po_unsend_after_reception",
            "This order already has a recorded reception, which is evidence it reached the supplier; undo the reception before un-sending",
            409,
            json!({"reception_status": status}),
        ));
    }
    if !po.get("paid_at").map(Value::is_null).unwrap_or(true) {
        return Err(ApiError::app("po_unsend_after_payment", "This order is marked as paid; mark it as unpaid before un-sending", 409, json!({})));
    }
    sqlx::query("UPDATE inventory_po_log SET sent_at = NULL WHERE id = $1 AND tenant_id = $2 AND sent_at IS NOT NULL")
        .bind(&po_log_id)
        .bind(&user.tenant_id)
        .execute(pool)
        .await?;
    tracing::info!("[reception] UNSEND tenant={} po={} undone_by={}", user.tenant_id, po_log_id, user.user_id);

    let po_number = po.get("po_number").and_then(Value::as_i64).map(|n| n as i32);
    let mut details = Map::new();
    details.insert("reference".into(), json!(format_po_number(po_number, &po_log_id)));
    events::record(pool, &user.tenant_id, &user.user_id, &ORDER_UNSENT, Some(&po_log_id), details, Some("reversed_by_user")).await;
    Ok(ok(json!({"po_log_id": po_log_id, "sent_at": null})))
}
