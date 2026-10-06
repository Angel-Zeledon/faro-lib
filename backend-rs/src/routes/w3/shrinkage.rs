//! Shrinkage (non-sale stock-outs): `POST /inventory/shrinkage`,
//! `GET /inventory/shrinkage`, `GET /inventory/shrinkage/reasons`, from
//! `backend/api/v1/inventory.py` and `backend/inventory/shrinkage_service.py`.
//!
//! Recording takes the units out of stock with the same conditional UPDATE
//! every other stock move uses (never below zero), writes the warehouse-stamped
//! snapshot, and appends the ledger row with its cost captured at that moment.
//! Python runs those three writes on separate autocommitting connections; here
//! they are one transaction, which can only be stronger.

use axum::body::Bytes;
use axum::extract::{RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use chrono::Utc;
use serde_json::{json, Map, Value};

use crate::auth::warehouse_scope::{self};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::inventory::calc::py_round;
use crate::inventory::events::{self, SHRINKAGE_RECORDED};
use crate::inventory::pydt;
use crate::inventory::scope::{filter_rows, require_in_scope};
use crate::inventory::stock::{self, record_snapshot};
use crate::inventory::warehouses;
use crate::pyjson::float_repr;
use crate::routes::r1::query_params::{sql_int, QueryParams};
use crate::routes::ok;
use crate::routes::sessions::row_json;
use crate::state::AppState;
use crate::validation::{self, body_object, float_field, str_field, Bound, Errors, Field, NO_STR_RULES};

const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
const WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

/// `REASONS`.
const REASONS: [&str; 4] = ["breakage", "expiry", "self_consumption", "gift"];

pub async fn reasons(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    auth::current_user(&state, &headers, READ, &actors).await?;
    Ok(ok(json!(REASONS)))
}

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let q = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let sku = q.opt_str(&mut errs, "sku", None);
    let limit = q.int(&mut errs, "limit", 50, Some(1), Some(200));
    errs.into_result()?;
    let limit = sql_int(limit)?;
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    // A scoped caller reads wide, filters, then cuts.
    let fetch = if sc.is_some() { 200 } else { limit };
    let sku = sku.filter(|s| !s.is_empty());
    let rows = if let Some(sku) = &sku {
        sqlx::query("SELECT * FROM inventory_shrinkage WHERE tenant_id = $1 AND sku = $2 ORDER BY created_at DESC LIMIT $3")
            .bind(&user.tenant_id)
            .bind(sku)
            .bind(fetch)
            .fetch_all(&state.pool)
            .await?
    } else {
        sqlx::query("SELECT * FROM inventory_shrinkage WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2")
            .bind(&user.tenant_id)
            .bind(fetch)
            .fetch_all(&state.pool)
            .await?
    };
    let rows: Vec<Map<String, Value>> = rows.iter().map(row_json).collect::<Result<_, _>>()?;
    let rows = if sc.is_some() {
        filter_rows(&sc, rows, "warehouse").into_iter().take(limit as usize).collect()
    } else {
        rows
    };
    Ok(ok(Value::Array(rows.into_iter().map(Value::Object).collect())))
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
    let sku = str_field(&mut errs, &obj, &p, "sku", true, false, &NO_STR_RULES);
    let quantity = float_field(&mut errs, &obj, &p, "quantity", true, false, Some(Bound::Int(0)), None);
    let reason = str_field(&mut errs, &obj, &p, "reason", true, false, &NO_STR_RULES);
    let warehouse = str_field(&mut errs, &obj, &p, "warehouse", false, true, &NO_STR_RULES);
    let notes = str_field(&mut errs, &obj, &p, "notes", false, true, &NO_STR_RULES);
    let occurred = str_field(&mut errs, &obj, &p, "occurred_at", false, true, &NO_STR_RULES);
    errs.into_result()?;
    let (Field::Value(sku), Field::Value(quantity), Field::Value(reason)) = (sku, quantity, reason) else {
        return Err(ApiError::internal());
    };
    let opt = |f: Field<String>| match f { Field::Value(v) => Some(v), _ => None };
    let (warehouse, notes, occurred) = (opt(warehouse), opt(notes), opt(occurred));

    let occurred_at = match occurred.as_deref().filter(|v| !v.is_empty()) {
        Some(v) => Some(pydt::fromisoformat(v).ok_or_else(|| {
            ApiError::app("date_invalid_iso", "occurred_at must be an ISO date (YYYY-MM-DD)", 422,
                json!({"field": "occurred_at"}))
        })?),
        None => None,
    };
    let tenant = user.tenant_id.as_str();
    let sc = warehouse_scope::scope_names(&state.pool, &user).await?;
    let mut conn = state.pool.acquire().await?;
    let canon = warehouses::resolve_canonical_name(&mut conn, tenant, warehouse.as_deref()).await?;
    require_in_scope(&sc, Some(&canon))?;

    // ── shrinkage_service.record_shrinkage ─────────────────────────────────
    if quantity <= 0.0 {
        return Err(ApiError::app("shrinkage_qty_must_be_positive", "Quantity must be greater than 0", 422, json!({})));
    }
    if !REASONS.contains(&reason.as_str()) {
        let options = REASONS.join(", ");
        return Err(ApiError::app("shrinkage_invalid_reason", format!("Invalid reason. Options: {options}"), 422,
            json!({"options": options})));
    }
    let warehouse = warehouses::resolve_canonical_name(&mut conn, tenant, warehouse.as_deref()).await?;
    let existing = stock::get_stock(&mut conn, tenant, &sku, Some(&warehouse)).await?;
    let Some(existing) = existing else {
        return Err(ApiError::app("shrinkage_sku_not_found",
            format!("SKU '{sku}' not found in inventory (warehouse '{warehouse}')"), 404,
            json!({"sku": sku, "warehouse": warehouse})));
    };
    let current = existing.get("current_stock").and_then(Value::as_f64).unwrap_or(0.0);
    if quantity > current {
        return Err(ApiError::app("shrinkage_qty_exceeds_stock",
            format!("Quantity ({}) exceeds current stock ({}) of '{sku}'", float_repr(quantity), float_repr(current)),
            422, json!({"quantity": quantity, "current": current, "sku": sku})));
    }
    let unit_cost = existing.get("unit_cost").and_then(Value::as_f64);
    let occurred_utc = occurred_at.map(|d| d.assume_utc().to_utc()).unwrap_or_else(Utc::now);
    drop(conn);

    let mut tx = state.pool.begin().await?;
    // The WHERE clause re-checks the stock atomically: two concurrent records
    // cannot both pass the early guard against one stale read.
    let updated: Option<(f64,)> = sqlx::query_as(
        "UPDATE inventory_stock SET current_stock = current_stock - $1, updated_at = NOW()
          WHERE tenant_id = $2 AND sku = $3 AND warehouse = $4 AND current_stock >= $5
          RETURNING current_stock",
    )
    .bind(quantity)
    .bind(tenant)
    .bind(&sku)
    .bind(&warehouse)
    .bind(quantity)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((left,)) = updated else {
        return Err(ApiError::app("shrinkage_qty_exceeds_stock_concurrent",
            format!("Quantity ({}) exceeds current stock of '{sku}' (it may have changed from another concurrent operation)",
                float_repr(quantity)),
            422, json!({"quantity": quantity, "sku": sku})));
    };
    record_snapshot(&mut tx, tenant, &sku, left, &warehouse).await?;
    let total_cost = unit_cost.map(|c| py_round(quantity * c, 2));
    let row = sqlx::query(
        "INSERT INTO inventory_shrinkage
             (tenant_id, sku, warehouse, quantity, reason, unit_cost, total_cost, notes, created_by, created_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
         RETURNING *",
    )
    .bind(tenant)
    .bind(&sku)
    .bind(&warehouse)
    .bind(quantity)
    .bind(&reason)
    .bind(unit_cost)
    .bind(total_cost)
    .bind(&notes)
    .bind(&user.user_id)
    .bind(occurred_utc)
    .fetch_one(&mut *tx)
    .await?;
    let row = row_json(&row)?;
    tx.commit().await?;
    tracing::info!("[shrinkage] tenant={tenant} sku={sku} warehouse={warehouse} qty={quantity} reason={reason} total_cost={total_cost:?}");

    // The warehouse recorded is the one the service RESOLVED, not the one the form sent.
    let mut details = Map::new();
    details.insert("sku".into(), json!(sku));
    details.insert("quantity".into(), json!(quantity));
    details.insert("warehouse".into(), row.get("warehouse").cloned().unwrap_or(Value::Null));
    details.insert("shrinkage_reason".into(), json!(reason));
    events::record(&state.pool, tenant, &user.user_id, &SHRINKAGE_RECORDED, Some(&sku), details, None).await;
    Ok((StatusCode::CREATED, ok(Value::Object(row))))
}
