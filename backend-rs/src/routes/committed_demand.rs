//! Committed demand writes: `backend/api/v1/committed_demand.py` and the
//! write half of `backend/inventory/committed_demand_service.py`.
//!
//! Migrated: `POST /committed-demand`, `POST /committed-demand/bulk`,
//! `PATCH /committed-demand/{id}`, `POST /committed-demand/{id}/status`.
//!
//! NOT migrated: `GET /committed-demand`. The list annotates every open
//! commitment with an at-risk verdict computed from stock, open purchase
//! orders, transfers and the lead-time cascade (`resolve_planning_inputs`,
//! learned lead times, supplier and stock-default rules). That is the core
//! of the inventory hub and moves with it, in a later wave; until then the
//! proxy keeps sending GET to Python (method-level routing, see the doc).
//!
//! Every rule below is copied from Python in Python's order, because the
//! order decides which error a request with several problems gets.
//!
//! Warehouse scope (`backend/api/v1/committed_demand.py`): a commitment names
//! its warehouse by id, or none (a company-wide promise). A caller limited to
//! some warehouses enters and changes only commitments naming one of THEIR
//! warehouses; any other commitment is the same 404 a missing one gets.
//!
//! Contracts (`supply_contract_service`): a commitment materialised from a
//! blanket contract (`source = 'contract'`) keeps the contract's sku,
//! warehouse, customer, probability and on-top flag; a withdrawn one cannot
//! change status; one whose contract is no longer active cannot be reopened.
//! Those are row and table reads, no contract logic, so they are ported here.
//!
//! Marking a commitment fulfilled queues the `commitment.fulfilled` webhook
//! (`crate::webhook_events`), once per real transition.

use std::collections::HashSet;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use chrono::{DateTime, Duration, Local, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::postgres::PgRow;
use sqlx::{PgPool, Row};

use crate::activity::{record_event, Event};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, isoformat_utc, py_strip, take_chars};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{
    self, body_object, bool_field, float_field, list_field, str_field, Body, Bound, Errors, Field, StrRules,
    NO_STR_RULES,
};

/// `INTERNAL_TAGS["committed-demand"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'committed-demand': a commitment is a customer order a person entered; it moves purchase decisions, so it is recorded under a person's name",
    ),
    is_mcp: false,
};

pub const STATUSES: [&str; 3] = ["open", "fulfilled", "cancelled"];
const MAX_NOTE_LENGTH: usize = 300;
const MAX_CUSTOMER_LENGTH: usize = 200;
const MAX_QUANTITY: f64 = 1e9;
const MAX_YEARS_AHEAD: i64 = 10;
const MAX_BULK_ROWS: usize = 1000;

// ── Pydantic models ──────────────────────────────────────────────────────────

/// One validated `CommitmentBody` (create and bulk rows).
#[derive(Debug, Clone)]
struct CommitmentBody {
    sku: String,
    delivery_date: String,
    quantity: f64,
    customer: Option<String>,
    probability: f64,
    warehouse_id: Option<String>,
    on_top_of_base: bool,
    note: Option<String>,
}

fn opt(f: Field<String>) -> Option<String> {
    match f {
        Field::Value(v) => Some(v),
        _ => None,
    }
}

fn rules(min: Option<usize>, max: Option<usize>) -> StrRules {
    StrRules { min_length: min, max_length: max, pattern: None }
}

/// `CommitmentBody` validation; `None` when any field failed (errors pushed).
fn validate_commitment(errs: &mut Errors, v: &Value, prefix: &[Value]) -> Option<CommitmentBody> {
    let obj = validation::as_object(errs, prefix, v)?;
    let before = errs.0.len();
    let sku = str_field(errs, obj, prefix, "sku", true, false, &rules(Some(1), Some(200)));
    let delivery_date = str_field(errs, obj, prefix, "delivery_date", true, false, &NO_STR_RULES);
    let quantity = float_field(errs, obj, prefix, "quantity", true, false,
        Some(Bound::Int(0)), Some(Bound::Float(MAX_QUANTITY)));
    let customer = str_field(errs, obj, prefix, "customer", false, true, &rules(None, Some(MAX_CUSTOMER_LENGTH)));
    let probability = float_field(errs, obj, prefix, "probability", false, false,
        Some(Bound::Int(0)), Some(Bound::Int(1)));
    let warehouse_id = str_field(errs, obj, prefix, "warehouse_id", false, true, &rules(None, Some(64)));
    let on_top_of_base = bool_field(errs, obj, prefix, "on_top_of_base", false);
    let note = str_field(errs, obj, prefix, "note", false, true, &rules(None, Some(MAX_NOTE_LENGTH)));
    if errs.0.len() != before {
        return None;
    }
    Some(CommitmentBody {
        sku: opt(sku)?,
        delivery_date: opt(delivery_date)?,
        quantity: match quantity { Field::Value(q) => q, _ => return None },
        customer: opt(customer),
        probability: match probability { Field::Value(p) => p, _ => 1.0 },
        warehouse_id: opt(warehouse_id),
        on_top_of_base: match on_top_of_base { Field::Value(b) => b, _ => true },
        note: opt(note),
    })
}

/// `CommitmentPatch` with `model_dump(exclude_unset=True)` semantics: only the
/// keys the client sent, explicit nulls included.
#[derive(Debug, Default)]
struct CommitmentPatch {
    sku: Option<Option<String>>,
    delivery_date: Option<Option<String>>,
    quantity: Option<Option<f64>>,
    customer: Option<Option<String>>,
    probability: Option<Option<f64>>,
    warehouse_id: Option<Option<String>>,
    on_top_of_base: Option<Option<bool>>,
    note: Option<Option<String>>,
}

fn set<T>(f: Field<T>) -> Option<Option<T>> {
    match f {
        Field::Absent => None,
        Field::Null => Some(None),
        Field::Value(v) => Some(Some(v)),
    }
}

fn validate_patch(obj: &Map<String, Value>) -> Result<CommitmentPatch, ApiError> {
    let mut errs = Errors::default();
    let p = [Value::String("body".into())];
    let sku = str_field(&mut errs, obj, &p, "sku", false, true, &rules(Some(1), Some(200)));
    let delivery_date = str_field(&mut errs, obj, &p, "delivery_date", false, true, &NO_STR_RULES);
    let quantity = float_field(&mut errs, obj, &p, "quantity", false, true,
        Some(Bound::Int(0)), Some(Bound::Float(MAX_QUANTITY)));
    let customer = str_field(&mut errs, obj, &p, "customer", false, true, &rules(None, Some(MAX_CUSTOMER_LENGTH)));
    let probability = float_field(&mut errs, obj, &p, "probability", false, true,
        Some(Bound::Int(0)), Some(Bound::Int(1)));
    let warehouse_id = str_field(&mut errs, obj, &p, "warehouse_id", false, true, &rules(None, Some(64)));
    let on_top_of_base = bool_field(&mut errs, obj, &p, "on_top_of_base", true);
    let note = str_field(&mut errs, obj, &p, "note", false, true, &rules(None, Some(MAX_NOTE_LENGTH)));
    errs.into_result()?;
    Ok(CommitmentPatch {
        sku: set(sku),
        delivery_date: set(delivery_date),
        quantity: set(quantity),
        customer: set(customer),
        probability: set(probability),
        warehouse_id: set(warehouse_id),
        on_top_of_base: set(on_top_of_base),
        note: set(note),
    })
}

fn status_pattern(s: &str) -> bool {
    STATUSES.contains(&s)
}

// ── Service: _clean, get, insert ─────────────────────────────────────────────

/// What `_clean` receives. `delivery_date: None` and `quantity: None` stand
/// for a Python `None` reaching it (an explicit null in a PATCH).
struct CleanInput {
    sku: Option<String>,
    delivery_date: Option<String>,
    quantity: Option<f64>,
    customer: Option<String>,
    probability: Option<f64>,
    warehouse_id: Option<String>,
    on_top_of_base: Option<bool>,
    note: Option<String>,
}

#[derive(Debug, Clone)]
struct Clean {
    sku: String,
    delivery_date: NaiveDate,
    quantity: f64,
    customer: Option<String>,
    probability: f64,
    warehouse_id: Option<String>,
    on_top_of_base: bool,
    note: Option<String>,
}

fn non_empty(s: String) -> Option<String> {
    if s.is_empty() { None } else { Some(s) }
}

/// `_clean`: validate one commitment and normalise it.
fn clean(c: CleanInput, today: NaiveDate) -> Result<Clean, ApiError> {
    let sku = py_strip(c.sku.as_deref().unwrap_or("")).to_string();
    if sku.is_empty() {
        return Err(ApiError::app("committed_demand_sku_required", "Choose a product", 422, json!({})));
    }
    // `_as_date`: `date.fromisoformat(str(value)[:10])`; str(None) == "None".
    let raw_date = c.delivery_date.unwrap_or_else(|| "None".into());
    let delivery = date_fromisoformat(&take_chars(&raw_date, 10)).ok_or_else(|| {
        ApiError::app("date_invalid_iso", "delivery_date must be an ISO date (YYYY-MM-DD)", 422,
            json!({"field": "delivery_date"}))
    })?;
    if delivery > today + Duration::days(365 * MAX_YEARS_AHEAD) {
        return Err(ApiError::app("committed_demand_date_too_far",
            "That delivery date is more than ten years away", 422,
            json!({"delivery_date": isoformat_date(&delivery)})));
    }
    let qty = c.quantity.ok_or_else(|| {
        ApiError::app("committed_demand_quantity_invalid", "Quantity must be a number", 422, json!({}))
    })?;
    if !qty.is_finite() || qty <= 0.0 || qty > MAX_QUANTITY {
        return Err(ApiError::app("committed_demand_quantity_invalid",
            "Quantity must be greater than zero", 422, json!({"quantity": qty})));
    }
    let prob = c.probability.unwrap_or(1.0);
    if !prob.is_finite() || !(prob > 0.0 && prob <= 1.0) {
        return Err(ApiError::app("committed_demand_probability_invalid",
            "Probability must be above 0 and at most 1", 422, json!({"probability": prob})));
    }
    Ok(Clean {
        sku,
        delivery_date: delivery,
        quantity: qty,
        customer: non_empty(take_chars(py_strip(c.customer.as_deref().unwrap_or("")), MAX_CUSTOMER_LENGTH)),
        probability: prob,
        warehouse_id: non_empty(py_strip(c.warehouse_id.as_deref().unwrap_or("")).to_string()),
        on_top_of_base: c.on_top_of_base.unwrap_or(true),
        note: non_empty(take_chars(py_strip(c.note.as_deref().unwrap_or("")), MAX_NOTE_LENGTH)),
    })
}

impl From<CommitmentBody> for CleanInput {
    fn from(b: CommitmentBody) -> Self {
        CleanInput {
            sku: Some(b.sku),
            delivery_date: Some(b.delivery_date),
            quantity: Some(b.quantity),
            customer: b.customer,
            probability: Some(b.probability),
            warehouse_id: b.warehouse_id,
            on_top_of_base: Some(b.on_top_of_base),
            note: b.note,
        }
    }
}

async fn check_warehouse(pool: &PgPool, tenant_id: &str, warehouse_id: Option<&str>) -> Result<(), ApiError> {
    let Some(w) = warehouse_id else { return Ok(()) };
    let found: Option<(i32,)> = sqlx::query_as("SELECT 1 FROM warehouses WHERE id = $1 AND tenant_id = $2")
        .bind(w)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    if found.is_none() {
        return Err(ApiError::app("committed_demand_warehouse_unknown", "That warehouse does not exist", 404,
            json!({"warehouse_id": w})));
    }
    Ok(())
}

const COLS: &str = "c.id, c.sku, c.warehouse_id, c.delivery_date, c.quantity, c.customer,
    c.probability, c.on_top_of_base, c.status, c.note, c.created_by,
    c.created_at, c.updated_at, c.status_changed_by, c.status_changed_at,
    c.source, c.contract_id, c.contract_root_id, c.contract_release_date,
    c.contract_withdrawn_at";

/// `CONTRACT_LOCKED_FIELDS`: what a contract-materialised commitment takes
/// from its contract. Quantity, date and note stay editable.
const CONTRACT_LOCKED_FIELDS: [&str; 5] = ["sku", "warehouse_id", "customer", "probability", "on_top_of_base"];

/// `_fmt`: the row in `_COLS` order, dates as ISO strings, plus `overdue`.
fn fmt_row(row: &PgRow, today: NaiveDate) -> Result<Map<String, Value>, sqlx::Error> {
    let delivery: NaiveDate = row.try_get("delivery_date")?;
    let status: String = row.try_get("status")?;
    let ts = |name: &str| -> Result<Value, sqlx::Error> {
        let v: Option<DateTime<Utc>> = row.try_get(name)?;
        Ok(v.map(|d| Value::String(isoformat_utc(&d))).unwrap_or(Value::Null))
    };
    let s = |name: &str| -> Result<Value, sqlx::Error> {
        let v: Option<String> = row.try_get(name)?;
        Ok(v.map(Value::String).unwrap_or(Value::Null))
    };
    let mut m = Map::new();
    m.insert("id".into(), s("id")?);
    m.insert("sku".into(), s("sku")?);
    m.insert("warehouse_id".into(), s("warehouse_id")?);
    m.insert("delivery_date".into(), Value::String(isoformat_date(&delivery)));
    m.insert("quantity".into(), json!(row.try_get::<f64, _>("quantity")?));
    m.insert("customer".into(), s("customer")?);
    m.insert("probability".into(), json!(row.try_get::<f64, _>("probability")?));
    m.insert("on_top_of_base".into(), json!(row.try_get::<bool, _>("on_top_of_base")?));
    m.insert("status".into(), Value::String(status.clone()));
    m.insert("note".into(), s("note")?);
    m.insert("created_by".into(), s("created_by")?);
    m.insert("created_at".into(), ts("created_at")?);
    m.insert("updated_at".into(), ts("updated_at")?);
    m.insert("status_changed_by".into(), s("status_changed_by")?);
    m.insert("status_changed_at".into(), ts("status_changed_at")?);
    m.insert("source".into(), s("source")?);
    m.insert("contract_id".into(), s("contract_id")?);
    m.insert("contract_root_id".into(), s("contract_root_id")?);
    let release: Option<NaiveDate> = row.try_get("contract_release_date")?;
    m.insert("contract_release_date".into(),
        release.map(|d| Value::String(isoformat_date(&d))).unwrap_or(Value::Null));
    m.insert("contract_withdrawn_at".into(), ts("contract_withdrawn_at")?);
    m.insert("overdue".into(), Value::Bool(status == "open" && delivery < today));
    Ok(m)
}

/// `get`: one commitment of this tenant, or 404 `committed_demand_not_found`.
async fn get(pool: &PgPool, tenant_id: &str, id: &str) -> Result<Map<String, Value>, ApiError> {
    let row = sqlx::query(&format!("SELECT {COLS} FROM committed_demand c WHERE c.id = $1 AND c.tenant_id = $2"))
        .bind(id)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    match row {
        Some(r) => Ok(fmt_row(&r, today())?),
        None => Err(ApiError::app("committed_demand_not_found", "Commitment not found", 404, json!({}))),
    }
}

// ── Warehouse scope (the API module's helpers) ──────────────────────────────

/// `_visible`: an unrestricted caller sees everything; a scoped one only rows
/// naming one of their warehouses (an unassigned row is company-wide).
fn visible(allowed: &Option<Vec<String>>, row: &Map<String, Value>) -> bool {
    match allowed {
        None => true,
        Some(ids) => row
            .get("warehouse_id")
            .and_then(Value::as_str)
            .filter(|w| !w.is_empty())
            .is_some_and(|w| ids.iter().any(|i| i == w)),
    }
}

/// `_require_writable_warehouse`: a scoped caller must name one of their own.
async fn require_writable_warehouse(pool: &PgPool, tenant_id: &str, allowed: &Option<Vec<String>>,
    warehouse_id: Option<&str>) -> Result<(), ApiError>
{
    let Some(ids) = allowed else { return Ok(()) };
    let wid = py_strip(warehouse_id.unwrap_or(""));
    if wid.is_empty() {
        return Err(ApiError::app("committed_demand_warehouse_required",
            "A user limited to some warehouses must assign the commitment to one of them.", 422, json!({})));
    }
    if !ids.iter().any(|i| i == wid) {
        let row: Option<(String,)> = sqlx::query_as("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2")
            .bind(wid)
            .bind(tenant_id)
            .fetch_optional(pool)
            .await?;
        let name = row.map(|r| r.0).unwrap_or_else(|| wid.to_string());
        return Err(wscope::denied(Some(&name)));
    }
    Ok(())
}

/// `_get_visible`: the commitment, or the 404 a missing one gets.
async fn get_visible(pool: &PgPool, tenant_id: &str, allowed: &Option<Vec<String>>, id: &str)
    -> Result<Map<String, Value>, ApiError>
{
    let row = get(pool, tenant_id, id).await?;
    if !visible(allowed, &row) {
        return Err(ApiError::app("committed_demand_not_found", "Commitment not found", 404, json!({})));
    }
    Ok(row)
}

/// Python's `fields[k] != current.get(k)` for one locked field the PATCH sent.
fn locked_field_changed(patch: &CommitmentPatch, current: &Map<String, Value>, key: &str) -> bool {
    let cur = current.get(key).unwrap_or(&Value::Null);
    let s = |v: &Option<Option<String>>| v.as_ref().map(|v| match v {
        Some(x) => cur.as_str() != Some(x.as_str()),
        None => !cur.is_null(),
    });
    let changed = match key {
        "sku" => s(&patch.sku),
        "warehouse_id" => s(&patch.warehouse_id),
        "customer" => s(&patch.customer),
        "probability" => patch.probability.map(|v| match v {
            Some(x) => cur.as_f64() != Some(x),
            None => !cur.is_null(),
        }),
        "on_top_of_base" => patch.on_top_of_base.map(|v| match v {
            Some(x) => cur.as_bool() != Some(x),
            None => !cur.is_null(),
        }),
        _ => None,
    };
    changed.unwrap_or(false)
}

/// `date.today()`: the server's local date, as Python reads it.
fn today() -> NaiveDate {
    Local::now().date_naive()
}

async fn insert(conn: &mut sqlx::PgConnection, tenant_id: &str, user_id: &str, c: &Clean) -> Result<String, sqlx::Error> {
    let (id,): (String,) = sqlx::query_as(
        "INSERT INTO committed_demand
             (tenant_id, sku, warehouse_id, delivery_date, quantity, customer,
              probability, on_top_of_base, note, created_by)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
    )
    .bind(tenant_id)
    .bind(&c.sku)
    .bind(&c.warehouse_id)
    .bind(c.delivery_date)
    .bind(c.quantity)
    .bind(&c.customer)
    .bind(c.probability)
    .bind(c.on_top_of_base)
    .bind(&c.note)
    .bind(user_id)
    .fetch_one(&mut *conn)
    .await?;
    Ok(id)
}

// ── Request plumbing ─────────────────────────────────────────────────────────

/// FastAPI's order: JSON decode (before auth) -> auth + role guards -> model
/// validation. Returns the caller and the body object.
async fn writer_and_body(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    bytes: &Bytes,
) -> Result<(CurrentUser, Map<String, Value>), ApiError> {
    let content_type = headers
        .get(axum::http::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok());
    let body: Body = validation::read_body(content_type, bytes)?;
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_analyst_or_above(state, &user).await?;
    let obj = body_object(&body)?;
    Ok((user, obj))
}

fn details(pairs: &[(&str, Value)]) -> Map<String, Value> {
    pairs.iter().map(|(k, v)| ((*k).to_string(), v.clone())).collect()
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let mut errs = Errors::default();
    let body = validate_commitment(&mut errs, &Value::Object(obj), &[Value::String("body".into())]);
    errs.into_result()?;
    let body = body.ok_or_else(ApiError::internal)?;

    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    require_writable_warehouse(&state.pool, &user.tenant_id, &allowed, body.warehouse_id.as_deref()).await?;
    let c = clean(body.into(), today())?;
    check_warehouse(&state.pool, &user.tenant_id, c.warehouse_id.as_deref()).await?;
    let mut tx = state.pool.begin().await?;
    let new_id = insert(&mut tx, &user.tenant_id, &user.user_id, &c).await?;
    tx.commit().await?;
    let row = get(&state.pool, &user.tenant_id, &new_id).await?;

    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CommittedDemandCreated,
        row.get("id").and_then(Value::as_str),
        details(&[
            ("sku", row["sku"].clone()),
            ("quantity", row["quantity"].clone()),
            ("delivery_date", row["delivery_date"].clone()),
            // `row["customer"] or ""`
            ("customer", match &row["customer"] { Value::Null => json!(""), v => v.clone() }),
        ])).await;
    Ok((StatusCode::CREATED, ok(Value::Object(row))))
}

pub async fn create_bulk(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let mut errs = Errors::default();
    let prefix = [Value::String("body".into())];
    let mut rows: Vec<CommitmentBody> = Vec::new();
    if let Some(items) = list_field(&mut errs, &obj, &prefix, "rows", 1, MAX_BULK_ROWS) {
        for (i, item) in items.iter().enumerate() {
            let p = [Value::String("body".into()), Value::String("rows".into()), json!(i)];
            if let Some(r) = validate_commitment(&mut errs, item, &p) {
                rows.push(r);
            }
        }
    }
    errs.into_result()?;

    // Every row's warehouse is checked BEFORE anything is written.
    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    for r in &rows {
        require_writable_warehouse(&state.pool, &user.tenant_id, &allowed, r.warehouse_id.as_deref()).await?;
    }

    // create_many
    if rows.is_empty() {
        return Err(ApiError::app("committed_demand_bulk_empty", "There are no rows to import", 422, json!({})));
    }
    if rows.len() > MAX_BULK_ROWS {
        return Err(ApiError::app("committed_demand_bulk_too_large", "Import at most 1,000 rows at a time",
            422, json!({"max": MAX_BULK_ROWS, "rows": rows.len()})));
    }
    let today = today();
    let mut cleaned: Vec<Clean> = Vec::new();
    let mut row_errors: Vec<Value> = Vec::new();
    for (i, r) in rows.into_iter().enumerate() {
        match clean(r.into(), today) {
            Ok(c) => cleaned.push(c),
            Err(e) => row_errors.push(json!({
                "row": i + 1,
                "code": e.body["error_code"],
                "params": e.body["error_params"],
            })),
        }
    }
    if !row_errors.is_empty() {
        let bad = row_errors.len();
        row_errors.truncate(50);
        return Err(ApiError::app("committed_demand_bulk_invalid",
            "Some rows are not valid; nothing was imported", 422,
            json!({"errors": row_errors, "bad_rows": bad})));
    }
    // Python iterates a set (arbitrary order); first appearance is the
    // deterministic choice when several warehouses are unknown.
    let mut seen = HashSet::new();
    for c in &cleaned {
        if let Some(w) = c.warehouse_id.as_deref() {
            if seen.insert(w.to_string()) {
                check_warehouse(&state.pool, &user.tenant_id, Some(w)).await?;
            }
        }
    }
    let mut tx = state.pool.begin().await?;
    let mut ids = Vec::with_capacity(cleaned.len());
    for c in &cleaned {
        ids.push(insert(&mut tx, &user.tenant_id, &user.user_id, c).await?);
    }
    tx.commit().await?;

    let created = ids.len();
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CommittedDemandImported,
        Some("bulk"), details(&[("rows", json!(created))])).await;
    Ok((StatusCode::CREATED, ok(json!({"created": created, "ids": ids}))))
}

pub async fn update(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(commitment_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    update_inner(state, actors, commitment_id, headers, bytes).await
}

/// `PATCH /committed-demand/bulk`. Starlette tries routes in order: the POST
/// `/bulk` route matches the path but not the method, so it falls through to
/// `PATCH /{commitment_id}` with the id "bulk" (a 404 for a commitment that
/// does not exist). The Rust router matches the static segment first and would
/// answer 405, so this keeps Python's behaviour explicitly.
pub async fn update_bulk_literal(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    update_inner(state, actors, "bulk".to_string(), headers, bytes).await
}

async fn update_inner(
    state: AppState,
    actors: RequestActors,
    commitment_id: String,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let patch = validate_patch(&obj)?;

    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    get_visible(&state.pool, &user.tenant_id, &allowed, &commitment_id).await?;
    if let Some(w) = &patch.warehouse_id {
        // Moving it: the destination must be theirs too.
        require_writable_warehouse(&state.pool, &user.tenant_id, &allowed, w.as_deref()).await?;
    }

    let current = get(&state.pool, &user.tenant_id, &commitment_id).await?;
    if current["status"] != "open" {
        return Err(ApiError::app("committed_demand_closed",
            "A fulfilled or cancelled commitment cannot be edited", 409,
            json!({"status": current["status"]})));
    }
    if current.get("source").and_then(Value::as_str) == Some("contract") {
        if let Some(field) = CONTRACT_LOCKED_FIELDS.iter().find(|k| locked_field_changed(&patch, &current, k)) {
            return Err(ApiError::app("committed_demand_contract_locked",
                "This commitment comes from a contract; change the contract instead", 409,
                json!({"field": field})));
        }
    }
    let cur_s = |k: &str| current.get(k).and_then(Value::as_str).map(str::to_string);
    let mut input = CleanInput {
        sku: cur_s("sku"),
        delivery_date: cur_s("delivery_date"),
        quantity: current.get("quantity").and_then(Value::as_f64),
        customer: cur_s("customer"),
        probability: current.get("probability").and_then(Value::as_f64),
        warehouse_id: cur_s("warehouse_id"),
        on_top_of_base: current.get("on_top_of_base").and_then(Value::as_bool),
        note: cur_s("note"),
    };
    if let Some(v) = patch.sku { input.sku = v; }
    if let Some(v) = patch.delivery_date { input.delivery_date = v; }
    if let Some(v) = patch.quantity { input.quantity = v; }
    if let Some(v) = patch.customer { input.customer = v; }
    if let Some(v) = patch.probability { input.probability = v; }
    if let Some(v) = patch.warehouse_id { input.warehouse_id = v; }
    if let Some(v) = patch.on_top_of_base { input.on_top_of_base = v; }
    if let Some(v) = patch.note { input.note = v; }

    let c = clean(input, today())?;
    check_warehouse(&state.pool, &user.tenant_id, c.warehouse_id.as_deref()).await?;
    sqlx::query(
        "UPDATE committed_demand
            SET sku = $1, warehouse_id = $2, delivery_date = $3, quantity = $4,
                customer = $5, probability = $6, on_top_of_base = $7, note = $8,
                updated_at = NOW()
          WHERE id = $9 AND tenant_id = $10 AND status = 'open'",
    )
    .bind(&c.sku)
    .bind(&c.warehouse_id)
    .bind(c.delivery_date)
    .bind(c.quantity)
    .bind(&c.customer)
    .bind(c.probability)
    .bind(c.on_top_of_base)
    .bind(&c.note)
    .bind(&commitment_id)
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    let row = get(&state.pool, &user.tenant_id, &commitment_id).await?;
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CommittedDemandChanged,
        row.get("id").and_then(Value::as_str),
        details(&[("sku", row["sku"].clone()), ("status", row["status"].clone())])).await;
    Ok(ok(Value::Object(row)))
}

pub async fn set_status(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(commitment_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let mut errs = Errors::default();
    let status = str_field(&mut errs, &obj, &[Value::String("body".into())], "status", true, false,
        &StrRules { min_length: None, max_length: None, pattern: Some(("^(open|fulfilled|cancelled)$", status_pattern)) });
    errs.into_result()?;
    let Field::Value(status) = status else { return Err(ApiError::internal()) };

    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    get_visible(&state.pool, &user.tenant_id, &allowed, &commitment_id).await?;

    // svc.set_status
    let current = get(&state.pool, &user.tenant_id, &commitment_id).await?;
    if !current.get("contract_withdrawn_at").map_or(true, Value::is_null) {
        return Err(ApiError::app("committed_demand_withdrawn",
            "This commitment was withdrawn when its contract changed", 409, json!({})));
    }
    if status == "open" && current.get("source").and_then(Value::as_str) == Some("contract") {
        let live: Option<(String,)> = sqlx::query_as(
            "SELECT status FROM supply_contracts
              WHERE tenant_id = $1 AND root_id = $2 AND superseded_by IS NULL",
        )
        .bind(&user.tenant_id)
        .bind(current.get("contract_root_id").and_then(Value::as_str))
        .fetch_optional(&state.pool)
        .await?;
        if live.map_or(true, |(s,)| s != "active") {
            return Err(ApiError::app("committed_demand_contract_inactive",
                "Its contract is no longer active, so it cannot be reopened", 409, json!({})));
        }
    }
    sqlx::query(
        "UPDATE committed_demand
            SET status = $1, status_changed_by = $2, status_changed_at = NOW(),
                updated_at = NOW()
          WHERE id = $3 AND tenant_id = $4 AND contract_withdrawn_at IS NULL",
    )
    .bind(&status)
    .bind(&user.user_id)
    .bind(&commitment_id)
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    let row = get(&state.pool, &user.tenant_id, &commitment_id).await?;
    if status == "fulfilled" && current.get("status").and_then(Value::as_str) != Some("fulfilled") {
        // Once per real transition: closing an already-fulfilled row emits nothing.
        let mut extra = Map::new();
        extra.insert("fulfilled_at".into(), row.get("status_changed_at").cloned().unwrap_or(Value::Null));
        crate::webhook_events::emit_commitment_event(&state.pool, &user.tenant_id, "commitment.fulfilled",
            &row, extra).await;
    }
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CommittedDemandChanged,
        row.get("id").and_then(Value::as_str),
        details(&[("sku", row["sku"].clone()), ("status", row["status"].clone())])).await;
    Ok(ok(Value::Object(row)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(sku: &str, date: Option<&str>, qty: Option<f64>) -> CleanInput {
        CleanInput {
            sku: Some(sku.into()),
            delivery_date: date.map(str::to_string),
            quantity: qty,
            customer: Some("  ACME  ".into()),
            probability: None,
            warehouse_id: Some("   ".into()),
            on_top_of_base: None,
            note: Some(" n ".into()),
        }
    }

    fn day(s: &str) -> NaiveDate {
        NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()
    }

    #[test]
    fn clean_normalises_like_python() {
        let c = clean(input(" A1 ", Some("2026-12-01T10:00:00"), Some(5.0)), day("2026-10-05")).unwrap();
        assert_eq!(c.sku, "A1");
        assert_eq!(isoformat_date(&c.delivery_date), "2026-12-01");
        assert_eq!(c.customer.as_deref(), Some("ACME"));
        assert_eq!(c.warehouse_id, None);
        assert_eq!(c.probability, 1.0);
        assert!(c.on_top_of_base);
        assert_eq!(c.note.as_deref(), Some("n"));
    }

    #[test]
    fn clean_error_order_and_codes() {
        let t = day("2026-10-05");
        let code = |r: Result<Clean, ApiError>| r.unwrap_err().code().unwrap().to_string();
        assert_eq!(code(clean(input("  ", Some("nope"), None), t)), "committed_demand_sku_required");
        assert_eq!(code(clean(input("A", Some("nope"), None), t)), "date_invalid_iso");
        assert_eq!(code(clean(input("A", None, Some(1.0)), t)), "date_invalid_iso");
        // 365 * 10 days, not ten calendar years: 2026-10-05 + 3650 d = 2036-10-02.
        assert_eq!(code(clean(input("A", Some("2036-10-03"), Some(1.0)), t)), "committed_demand_date_too_far");
        assert!(clean(input("A", Some("2036-10-02"), Some(1.0)), t).is_ok());
        let e = clean(input("A", Some("2026-11-01"), None), t).unwrap_err();
        assert_eq!(e.body["detail"], "Quantity must be a number");
    }

    #[test]
    fn date_too_far_boundary_is_3650_days() {
        // Python: delivery > today + timedelta(days=365 * 10)
        let t = day("2026-10-05");
        let edge = t + Duration::days(3650);
        assert!(clean(input("A", Some(&isoformat_date(&edge)), Some(1.0)), t).is_ok());
        let past = edge + Duration::days(1);
        assert!(clean(input("A", Some(&isoformat_date(&past)), Some(1.0)), t).is_err());
    }

    #[test]
    fn customer_and_note_are_cut_after_strip() {
        let mut i = input("A", Some("2026-11-01"), Some(1.0));
        i.customer = Some(format!("  {}  ", "x".repeat(250)));
        let c = clean(i, day("2026-10-05")).unwrap();
        assert_eq!(c.customer.unwrap().chars().count(), 200);
    }

    #[test]
    fn create_validation_collects_every_field_error() {
        let mut errs = Errors::default();
        let body = json!({"sku": "", "quantity": "abc", "probability": 1.5, "on_top_of_base": "maybe"});
        assert!(validate_commitment(&mut errs, &body, &[json!("body")]).is_none());
        let types: Vec<(String, Value)> = errs.0.iter()
            .map(|e| (e["type"].as_str().unwrap().to_string(), e["loc"].clone())).collect();
        assert_eq!(types, vec![
            ("string_too_short".into(), json!(["body", "sku"])),
            ("missing".into(), json!(["body", "delivery_date"])),
            ("float_parsing".into(), json!(["body", "quantity"])),
            ("less_than_equal".into(), json!(["body", "probability"])),
            ("bool_parsing".into(), json!(["body", "on_top_of_base"])),
        ]);
    }
}
