//! The inverses of `receive_po` and `mark_po_sent`:
//! `backend/api/v1/reception_reversals.py` and the two reversal functions of
//! `backend/inventory/reception_service.py`.
//!
//! * `POST /inventory/po/{po_log_id}/unreceive`   take the received units back
//!   out of stock, reset the lines and the header, forget the lead-time
//!   observations that reception taught (all-or-nothing, 409 when a touched
//!   (sku, warehouse) no longer holds the units)
//! * `POST /inventory/po/{po_log_id}/unsend`      clear `sent_at`
//!
//! Both are DB-only (`inventory_stock`, `inventory_snapshots`,
//! `inventory_po_items`, `inventory_po_log`, `supplier_lead_time_obs`). They
//! never call the optimizer or the semaforo: the `status_bump_*` triggers
//! invalidate the inventory snapshot whichever service wrote the rows.
//!
//! Guard order is FastAPI's: the person / key, the role + trial guard, the
//! warehouse scope of the order (`po_guard`), then the service's own 404 / 409.
//! No body is declared, so none is read.

use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::activity::{record_event_with_reason, Event};
use crate::auth::{Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::limits;
use crate::routes::ok;
use crate::routes::po_payments::{format_po_number, po_not_found, po_writer};
use crate::routes::sessions::row_json;
use crate::state::AppState;

/// `inventory-reversals` is an exposed tag: a write key acts as analyst.
pub const ROUTE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

/// `warehouse_service.DEFAULT_WAREHOUSE`.
const DEFAULT_WAREHOUSE: &str = "principal";

pub fn router() -> axum::Router<AppState> {
    use axum::routing::post;
    axum::Router::new()
        .route("/api/v1/inventory/po/{po_log_id}/unreceive", post(unreceive))
        .route("/api/v1/inventory/po/{po_log_id}/unsend", post(unsend))
}

struct Po {
    po_number: Option<i32>,
    sent_at: Option<DateTime<Utc>>,
    paid_at: Option<DateTime<Utc>>,
    received_at: Option<DateTime<Utc>>,
    reception_status: Option<String>,
    destination_warehouse: Option<String>,
}

/// `reception_service.get_po` (only the columns the reversals read).
async fn get_po(pool: &PgPool, tenant_id: &str, po_log_id: &str) -> Result<Option<Po>, ApiError> {
    #[allow(clippy::type_complexity)]
    let row: Option<(
        Option<i32>,
        Option<DateTime<Utc>>,
        Option<DateTime<Utc>>,
        Option<DateTime<Utc>>,
        Option<String>,
        Option<String>,
    )> = sqlx::query_as(
        "SELECT po_number, sent_at, paid_at, received_at, reception_status, destination_warehouse \
         FROM inventory_po_log WHERE id = $1 AND tenant_id = $2",
    )
    .bind(po_log_id)
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|(po_number, sent_at, paid_at, received_at, reception_status, destination_warehouse)| Po {
        po_number,
        sent_at,
        paid_at,
        received_at,
        reception_status,
        destination_warehouse,
    }))
}

/// `reception_service.get_po_items`.
async fn get_po_items(pool: &PgPool, tenant_id: &str, po_log_id: &str) -> Result<Vec<Map<String, Value>>, ApiError> {
    let rows = sqlx::query(
        "SELECT id, sku, display_name, supplier, supplier_id, signal, status, \
                recommended_qty, final_qty, received_qty, unit_cost, warehouse \
           FROM inventory_po_items \
          WHERE po_log_id = $1 AND tenant_id = $2 \
          ORDER BY supplier NULLS LAST, sku",
    )
    .bind(po_log_id)
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        out.push(row_json(r)?);
    }
    Ok(out)
}

fn conflict(code: &str, msg: &str, params: Value) -> ApiError {
    ApiError::app(code, msg, 409, params)
}

/// `sum(to_remove.values())`: Python's `sum` starts from the int 0, so an
/// empty reception reports `0` and not `0.0`.
fn py_sum(values: &[f64]) -> Value {
    if values.is_empty() {
        return json!(0);
    }
    let mut total = 0.0;
    for v in values {
        total += v;
    }
    json!(total)
}

pub async fn unreceive(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let pool = &state.pool;
    let tenant = &user.tenant_id;

    // The route reads the order first (`po_before`), the service again.
    let po_before = get_po(pool, tenant, &po_log_id).await?;
    let po = po_before.as_ref().ok_or_else(po_not_found)?;
    if po.received_at.is_none() {
        return Err(conflict("reception_nothing_to_undo", "This order has no recorded reception to undo", json!({})));
    }

    let items = get_po_items(pool, tenant, &po_log_id).await?;
    let ordered: Vec<&Map<String, Value>> = items
        .iter()
        .filter(|i| matches!(i.get("status").and_then(Value::as_str), Some("approved") | Some("modified")))
        .collect();

    // Aggregate by (sku, warehouse), in insertion order like the Python dict.
    let mut to_remove: Vec<((String, String), f64)> = Vec::new();
    for i in &ordered {
        let qty = i.get("received_qty").and_then(Value::as_f64).unwrap_or(0.0);
        if qty > 0.0 {
            let sku = i.get("sku").and_then(Value::as_str).unwrap_or_default().to_string();
            let warehouse = i
                .get("warehouse")
                .and_then(Value::as_str)
                .filter(|w| !w.is_empty())
                .or(po.destination_warehouse.as_deref().filter(|w| !w.is_empty()))
                .unwrap_or(DEFAULT_WAREHOUSE)
                .to_string();
            let key = (sku, warehouse);
            match to_remove.iter_mut().find(|(k, _)| *k == key) {
                Some((_, q)) => *q += qty,
                None => to_remove.push((key, qty)),
            }
        }
    }

    let mut tx = pool.begin().await?;
    limits::take_tenant_lock(&mut tx, tenant).await?;

    // Pre-check every touched row before writing anything.
    let mut shortfalls: Vec<Value> = Vec::new();
    for ((sku, warehouse), qty) in &to_remove {
        let available: Option<(f64,)> = sqlx::query_as(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = $1 AND sku = $2 AND warehouse = $3",
        )
        .bind(tenant)
        .bind(sku)
        .bind(warehouse)
        .fetch_optional(&mut *tx)
        .await?;
        let available = available.map_or(0.0, |r| r.0);
        if available < *qty {
            shortfalls.push(json!({"sku": sku, "warehouse": warehouse, "available": available, "needed": qty}));
        }
    }
    if !shortfalls.is_empty() {
        drop(tx);
        return Err(conflict(
            "reception_undo_insufficient_stock",
            "Cannot undo this reception: some of the received units are no longer in stock (sold, transferred, or written off since the reception)",
            json!({"shortfalls": shortfalls}),
        ));
    }

    for ((sku, warehouse), qty) in &to_remove {
        sqlx::query(
            "UPDATE inventory_stock SET current_stock = current_stock - $1, updated_at = NOW() \
             WHERE tenant_id = $2 AND sku = $3 AND warehouse = $4",
        )
        .bind(qty)
        .bind(tenant)
        .bind(sku)
        .bind(warehouse)
        .execute(&mut *tx)
        .await?;
        // `new_row["current_stock"]`: a missing row cannot be here, the
        // pre-check found at least `qty` > 0 in it.
        let (stock,): (f64,) = sqlx::query_as(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = $1 AND sku = $2 AND warehouse = $3",
        )
        .bind(tenant)
        .bind(sku)
        .bind(warehouse)
        .fetch_one(&mut *tx)
        .await?;
        sqlx::query("INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) VALUES ($1, $2, $3, $4)")
            .bind(tenant)
            .bind(sku)
            .bind(stock)
            .bind(warehouse)
            .execute(&mut *tx)
            .await?;
    }

    for i in &ordered {
        sqlx::query("UPDATE inventory_po_items SET received_qty = 0 WHERE id = $1 AND tenant_id = $2")
            .bind(i.get("id").and_then(Value::as_str))
            .bind(tenant)
            .execute(&mut *tx)
            .await?;
    }
    sqlx::query(
        "UPDATE inventory_po_log SET reception_status = 'pending', received_at = NULL, received_by = NULL \
         WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&po_log_id)
    .bind(tenant)
    .execute(&mut *tx)
    .await?;

    let unlearned: Vec<(Option<String>,)> = sqlx::query_as(
        "SELECT DISTINCT supplier FROM supplier_lead_time_obs WHERE tenant_id = $1 AND po_log_id = $2",
    )
    .bind(tenant)
    .bind(&po_log_id)
    .fetch_all(&mut *tx)
    .await?;
    sqlx::query("DELETE FROM supplier_lead_time_obs WHERE tenant_id = $1 AND po_log_id = $2")
        .bind(tenant)
        .bind(&po_log_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    let quantities: Vec<f64> = to_remove.iter().map(|(_, q)| *q).collect();
    let mut skus: Vec<&String> = to_remove.iter().map(|((s, _), _)| s).collect();
    skus.sort();
    skus.dedup();
    let sku_count = skus.len();
    let suppliers: Vec<Value> = unlearned.into_iter().map(|(s,)| s.map(Value::String).unwrap_or(Value::Null)).collect();
    tracing::info!(
        "[reception] UNDO tenant={tenant} po={po_log_id} undone_by={} units={:.2} suppliers_unlearned={:?}",
        user.user_id, quantities.iter().sum::<f64>(), suppliers
    );
    let units_removed = py_sum(&quantities);
    let items_after = get_po_items(pool, tenant, &po_log_id).await?;

    let mut details = Map::new();
    details.insert("reference".into(), json!(format_po_number(po.po_number, &po_log_id)));
    details.insert("sku_count".into(), json!(sku_count));
    details.insert("units".into(), units_removed.clone());
    details.insert("warehouse".into(), po.destination_warehouse.clone().map(Value::String).unwrap_or(Value::Null));
    record_event_with_reason(pool, tenant, &user.user_id, Event::ReceptionUndone, Some(&po_log_id), details,
        Some("reversed_by_user")).await;

    Ok(ok(json!({
        "po_log_id": po_log_id,
        "reception_status": "pending",
        "units_removed": units_removed,
        "sku_count": sku_count,
        "suppliers_lead_time_unlearned": suppliers,
        "items": items_after,
    })))
}

pub async fn unsend(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let pool = &state.pool;
    let tenant = &user.tenant_id;

    let po_before = get_po(pool, tenant, &po_log_id).await?;
    let po = po_before.as_ref().ok_or_else(po_not_found)?;
    if po.sent_at.is_none() {
        return Err(conflict("po_not_sent", "This order has not been marked as sent; there is nothing to undo", json!({})));
    }
    if po.reception_status.as_deref() != Some("pending") {
        return Err(conflict(
            "po_unsend_after_reception",
            "This order already has a recorded reception, which is evidence it reached the supplier; undo the reception before un-sending",
            json!({"reception_status": po.reception_status}),
        ));
    }
    if po.paid_at.is_some() {
        return Err(conflict(
            "po_unsend_after_payment",
            "This order is marked as paid; mark it as unpaid before un-sending",
            json!({}),
        ));
    }
    sqlx::query("UPDATE inventory_po_log SET sent_at = NULL WHERE id = $1 AND tenant_id = $2 AND sent_at IS NOT NULL")
        .bind(&po_log_id)
        .bind(tenant)
        .execute(pool)
        .await?;
    tracing::info!("[reception] UNSEND tenant={tenant} po={po_log_id} undone_by={}", user.user_id);

    let mut details = Map::new();
    details.insert("reference".into(), json!(format_po_number(po.po_number, &po_log_id)));
    record_event_with_reason(pool, tenant, &user.user_id, Event::OrderUnsent, Some(&po_log_id), details,
        Some("reversed_by_user")).await;
    Ok(ok(json!({"po_log_id": po_log_id, "sent_at": null})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_sum_is_the_int_zero() {
        assert_eq!(py_sum(&[]).to_string(), "0");
        assert_eq!(py_sum(&[2.0, 3.5]).to_string(), "5.5");
        assert_eq!(py_sum(&[2.0]).to_string(), "2.0");
    }
}
