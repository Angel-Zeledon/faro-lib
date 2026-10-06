//! Recurring delivery schedules: `/api/v1/recurring-deliveries` (Rust-only;
//! there is no Python implementation of these routes and so no failover).
//!
//! A schedule is a standing instruction on a corporate contract: "`quantity`
//! units of `sku` for `customer` every week / fortnight / half month / month
//! from `start_date` to `end_date`", with a weekday or day-of-month rule and
//! optional holidays. The materialiser (`crate::recurring`) turns it into
//! ordinary, contract-locked `committed_demand` rows for a rolling horizon.
//!
//! * `GET    /recurring-deliveries`              list (any signed-in user)
//! * `GET    /recurring-deliveries/{id}`         one, with the rows it made
//! * `POST   /recurring-deliveries/preview`      the dates a set of terms
//!                                               produces; writes nothing
//! * `POST   /recurring-deliveries`              create (analyst)
//! * `PATCH  /recurring-deliveries/{id}`         change the terms (analyst);
//!                                               revises future open rows only
//! * `POST   /recurring-deliveries/{id}/status`  active / paused / cancelled
//!
//! No DELETE: a schedule that ended is history. Cancel it instead; its future
//! open rows are withdrawn and the past stays.
//!
//! Warehouse scope is the committed-demand rule: a schedule names a warehouse
//! by id or none (company-wide); a caller limited to some warehouses sees and
//! writes only schedules naming one of THEIR warehouses, and any other one is
//! the same 404 a missing one gets.
//!
//! The tag is INTERNAL: a standing delivery instruction moves purchase
//! decisions and is recorded under a person's name, so no API key reaches it.

use std::collections::{BTreeSet, HashMap};

use axum::body::Bytes;
use axum::extract::{Path, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::{record_event, Event};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, isoformat_utc, py_strip, take_chars};
use crate::query::Query;
use crate::recurring::dates::{nominal_dates, occurrences, DateSpec, Frequency, ShiftRule};
use crate::recurring::materialise::{
    self, effective_horizon, fill, horizon_for, load, longest_lead, revise, today, wanted, Outcome, Schedule,
};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, bool_field, float_field, str_field, Body, Bound, Errors, Field, StrRules};

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'recurring-deliveries': a standing delivery instruction moves purchase decisions, so it is recorded under a person's name",
    ),
    is_mcp: false,
};

pub const STATUSES: [&str; 3] = ["active", "paused", "cancelled"];
const MAX_CUSTOMER_LENGTH: usize = 200;
const MAX_REFERENCE_LENGTH: usize = 100;
const MAX_NOTE_LENGTH: usize = 300;
const MAX_SKU_LENGTH: usize = 200;
const MAX_QUANTITY: f64 = 1e9;
const MAX_YEARS: i32 = 10;
const MAX_HOLIDAYS: usize = 50;
const MAX_PREVIEW_ROWS: usize = 200;
const MAX_DELIVERY_ROWS: i64 = 1000;
const DEFAULT_HORIZON_DAYS: i64 = 180;

fn err(code: &str, message: &str, status: u16, params: Value) -> ApiError {
    ApiError::app(code, message, status, params)
}

fn not_found() -> ApiError {
    err("recurring_delivery_not_found", "Recurring delivery not found", 404, json!({}))
}

// ── Terms: parsing and validation ────────────────────────────────────────────

/// The validated terms of a schedule (create, preview and edit share them).
#[derive(Debug, Clone)]
pub struct Terms {
    pub customer: String,
    pub reference: Option<String>,
    pub sku: String,
    pub warehouse_id: Option<String>,
    pub quantity: f64,
    pub spec: DateSpec,
    pub frequency: &'static str,
    pub horizon_days: i32,
    pub on_top_of_base: bool,
    pub note: Option<String>,
}

fn as_date(value: &str, field: &str) -> Result<NaiveDate, ApiError> {
    date_fromisoformat(&take_chars(value, 10)).ok_or_else(|| {
        err("date_invalid_iso", &format!("{field} must be an ISO date (YYYY-MM-DD)"), 422, json!({"field": field}))
    })
}

fn opt_str(f: Field<String>) -> Option<String> {
    match f {
        Field::Value(v) => Some(v),
        _ => None,
    }
}

/// An optional integer field in `[min, max]`; anything else (a float, a
/// string, a bool) is refused with a code the screen renders.
fn int_field(obj: &Map<String, Value>, name: &str, min: i64, max: i64) -> Result<Option<i64>, ApiError> {
    match obj.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => match n.as_i64() {
            Some(v) if v >= min && v <= max => Ok(Some(v)),
            _ => Err(err("recurring_delivery_field_invalid", "A number is out of range", 422,
                json!({"field": name, "min": min, "max": max}))),
        },
        Some(_) => Err(err("recurring_delivery_field_invalid", "A number is out of range", 422,
            json!({"field": name, "min": min, "max": max}))),
    }
}

fn parse_terms(obj: &Map<String, Value>, today: NaiveDate) -> Result<Terms, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let rules = |min: Option<usize>, max: Option<usize>| StrRules { min_length: min, max_length: max, pattern: None };
    let customer = str_field(&mut errs, obj, &p, "customer", true, false, &rules(Some(1), Some(MAX_CUSTOMER_LENGTH)));
    let reference = str_field(&mut errs, obj, &p, "reference", false, true, &rules(None, Some(MAX_REFERENCE_LENGTH)));
    let sku = str_field(&mut errs, obj, &p, "sku", true, false, &rules(Some(1), Some(MAX_SKU_LENGTH)));
    let warehouse_id = str_field(&mut errs, obj, &p, "warehouse_id", false, true, &rules(None, Some(64)));
    let quantity = float_field(&mut errs, obj, &p, "quantity", true, false,
        Some(Bound::Int(0)), Some(Bound::Float(MAX_QUANTITY)));
    let frequency = str_field(&mut errs, obj, &p, "frequency", true, false, &rules(None, Some(20)));
    let start = str_field(&mut errs, obj, &p, "start_date", true, false, &rules(None, Some(40)));
    let end = str_field(&mut errs, obj, &p, "end_date", true, false, &rules(None, Some(40)));
    let shift = str_field(&mut errs, obj, &p, "shift_rule", false, false, &rules(None, Some(20)));
    let avoid = bool_field(&mut errs, obj, &p, "avoid_weekends", false);
    let on_top = bool_field(&mut errs, obj, &p, "on_top_of_base", false);
    let note = str_field(&mut errs, obj, &p, "note", false, true, &rules(None, Some(MAX_NOTE_LENGTH)));
    errs.into_result()?;

    let customer = py_strip(&opt_str(customer).unwrap_or_default()).to_string();
    if customer.is_empty() {
        return Err(err("recurring_delivery_customer_required", "Name the customer", 422, json!({})));
    }
    let sku = py_strip(&opt_str(sku).unwrap_or_default()).to_string();
    if sku.is_empty() {
        return Err(err("recurring_delivery_sku_required", "Choose a product", 422, json!({})));
    }
    let quantity = match quantity {
        Field::Value(q) => q,
        _ => return Err(ApiError::internal()),
    };
    let freq_text = opt_str(frequency).unwrap_or_default();
    let frequency = Frequency::parse(&freq_text).ok_or_else(|| {
        err("recurring_delivery_frequency_invalid",
            "The frequency must be weekly, fortnightly, semimonthly or monthly", 422,
            json!({"frequency": take_chars(&freq_text, 20)}))
    })?;
    let freq_name: &'static str = match frequency {
        Frequency::Weekly => "weekly",
        Frequency::Fortnightly => "fortnightly",
        Frequency::Semimonthly => "semimonthly",
        Frequency::Monthly => "monthly",
    };

    let weekday = int_field(obj, "weekday", 0, 6)?;
    let day_of_month = int_field(obj, "day_of_month", 1, 31)?;
    let (weekday, day_of_month) = match frequency {
        Frequency::Weekly | Frequency::Fortnightly => {
            let w = weekday.ok_or_else(|| err("recurring_delivery_weekday_required",
                "Choose the weekday of the delivery", 422, json!({"frequency": freq_name})))?;
            (Some(w as u32), None)
        }
        Frequency::Monthly => {
            let d = day_of_month.ok_or_else(|| err("recurring_delivery_day_of_month_required",
                "Choose the day of the month of the delivery", 422, json!({})))?;
            (None, Some(d as u32))
        }
        Frequency::Semimonthly => (None, None),
    };

    let start = as_date(&opt_str(start).unwrap_or_default(), "start_date")?;
    let end = as_date(&opt_str(end).unwrap_or_default(), "end_date")?;
    if end < start {
        return Err(err("recurring_delivery_period_invalid", "The schedule ends before it starts", 422,
            json!({"start_date": isoformat_date(&start), "end_date": isoformat_date(&end)})));
    }
    let limit = |from: NaiveDate| from.checked_add_months(chrono::Months::new(12 * MAX_YEARS as u32));
    if limit(start).map_or(true, |l| end > l) || limit(today).map_or(true, |l| start > l) {
        return Err(err("recurring_delivery_period_too_long", "A schedule is limited to ten years", 422,
            json!({"years": MAX_YEARS})));
    }
    if end < today {
        return Err(err("recurring_delivery_period_ended", "The schedule has already ended", 422,
            json!({"end_date": isoformat_date(&end)})));
    }

    let mut holidays: BTreeSet<NaiveDate> = BTreeSet::new();
    match obj.get("holiday_dates") {
        None | Some(Value::Null) => {}
        Some(Value::Array(items)) => {
            if items.len() > MAX_HOLIDAYS {
                return Err(err("recurring_delivery_too_many_holidays", "At most 50 holidays can be listed", 422,
                    json!({"max": MAX_HOLIDAYS})));
            }
            for item in items {
                let text = item.as_str().unwrap_or_default();
                let d = date_fromisoformat(&take_chars(text, 10)).filter(|_| item.is_string());
                match d {
                    Some(d) => {
                        holidays.insert(d);
                    }
                    None => {
                        return Err(err("recurring_delivery_holiday_invalid",
                            "A holiday needs a valid date (YYYY-MM-DD)", 422,
                            json!({"value": take_chars(&item.to_string(), 40)})));
                    }
                }
            }
        }
        Some(_) => {
            return Err(err("recurring_delivery_holiday_invalid", "A holiday needs a valid date (YYYY-MM-DD)", 422,
                json!({"value": ""})));
        }
    }

    let avoid_weekends = match avoid { Field::Value(b) => b, _ => false };
    if avoid_weekends && matches!(frequency, Frequency::Weekly | Frequency::Fortnightly) {
        return Err(err("recurring_delivery_weekend_rule_invalid",
            "Avoiding weekends only applies to monthly and semimonthly schedules; pick a weekday instead", 422,
            json!({"frequency": freq_name})));
    }
    let shift_text = opt_str(shift).unwrap_or_else(|| "after".into());
    let shift_rule = ShiftRule::parse(&shift_text).ok_or_else(|| {
        err("recurring_delivery_shift_rule_invalid", "The holiday rule must be skip, before or after", 422,
            json!({"shift_rule": take_chars(&shift_text, 20)}))
    })?;

    let horizon = int_field(obj, "horizon_days", 1, 730)?.unwrap_or(DEFAULT_HORIZON_DAYS);

    let spec = DateSpec {
        frequency, weekday, day_of_month, start, end, holidays, avoid_weekends, shift_rule,
    };
    if nominal_dates(&spec, start, end).is_empty() {
        return Err(err("recurring_delivery_no_deliveries", "The period and rule produce no delivery", 422, json!({})));
    }
    Ok(Terms {
        customer,
        reference: opt_str(reference).map(|r| py_strip(&r).to_string()).filter(|r| !r.is_empty()),
        sku,
        warehouse_id: opt_str(warehouse_id).map(|w| py_strip(&w).to_string()).filter(|w| !w.is_empty()),
        quantity,
        spec,
        frequency: freq_name,
        horizon_days: horizon as i32,
        on_top_of_base: match on_top { Field::Value(b) => b, _ => true },
        note: opt_str(note).map(|n| py_strip(&n).to_string()).filter(|n| !n.is_empty()),
    })
}

// ── Request plumbing ─────────────────────────────────────────────────────────

async fn reader(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    auth::current_user(state, headers, ROUTE, actors).await
}

/// FastAPI's order: JSON decode (before auth) -> auth + role guards -> body.
async fn writer_and_body(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    bytes: &Bytes,
) -> Result<(CurrentUser, Map<String, Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: Body = validation::read_body(content_type, bytes)?;
    let user = reader(state, actors, headers).await?;
    auth::require_analyst_or_above(state, &user).await?;
    let obj = body_object(&body)?;
    Ok((user, obj))
}

// ── Warehouse scope ──────────────────────────────────────────────────────────

fn visible(allowed: &Option<Vec<String>>, warehouse_id: Option<&str>) -> bool {
    match allowed {
        None => true,
        Some(ids) => warehouse_id.filter(|w| !w.is_empty()).is_some_and(|w| ids.iter().any(|i| i == w)),
    }
}

async fn require_writable_warehouse(
    pool: &PgPool,
    tenant_id: &str,
    allowed: &Option<Vec<String>>,
    warehouse_id: Option<&str>,
) -> Result<(), ApiError> {
    let Some(ids) = allowed else { return Ok(()) };
    let wid = warehouse_id.unwrap_or("");
    if wid.is_empty() {
        return Err(err("recurring_delivery_warehouse_required",
            "A user limited to some warehouses must assign the schedule to one of them.", 422, json!({})));
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

async fn check_warehouse(pool: &PgPool, tenant_id: &str, warehouse_id: Option<&str>) -> Result<(), ApiError> {
    let Some(w) = warehouse_id else { return Ok(()) };
    let found: Option<(String,)> = sqlx::query_as("SELECT id FROM warehouses WHERE id = $1 AND tenant_id = $2")
        .bind(w)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    if found.is_none() {
        return Err(err("recurring_delivery_warehouse_unknown", "That warehouse does not exist", 404,
            json!({"warehouse_id": w})));
    }
    Ok(())
}

// ── Presenting ───────────────────────────────────────────────────────────────

struct LiveRow {
    id: String,
    schedule_id: String,
    nominal: Option<NaiveDate>,
    delivery: NaiveDate,
    quantity: f64,
    status: String,
}

async fn live_rows(pool: &PgPool, tenant_id: &str, ids: &[String], limit_per_schedule: Option<i64>) -> Result<Vec<LiveRow>, sqlx::Error> {
    let rows = sqlx::query(
        "SELECT id, contract_root_id, contract_release_date, delivery_date, quantity, status
           FROM committed_demand
          WHERE tenant_id = $1 AND contract_root_id = ANY($2) AND contract_withdrawn_at IS NULL
          ORDER BY delivery_date, id",
    )
    .bind(tenant_id)
    .bind(ids)
    .fetch_all(pool)
    .await?;
    let mut per: HashMap<String, i64> = HashMap::new();
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        let schedule_id: String = r.try_get("contract_root_id")?;
        if let Some(limit) = limit_per_schedule {
            let n = per.entry(schedule_id.clone()).or_insert(0);
            *n += 1;
            if *n > limit {
                continue;
            }
        }
        out.push(LiveRow {
            id: r.try_get("id")?,
            schedule_id,
            nominal: r.try_get("contract_release_date")?,
            delivery: r.try_get("delivery_date")?,
            quantity: r.try_get("quantity")?,
            status: r.try_get("status")?,
        });
    }
    Ok(out)
}

fn ts(v: &Option<DateTime<Utc>>) -> Value {
    v.as_ref().map_or(Value::Null, |d| json!(isoformat_utc(d)))
}

fn round4(x: f64) -> f64 {
    (x * 10000.0).round() / 10000.0
}

/// One schedule as the screens read it. `rows` are its live commitments.
fn present(s: &Schedule, rows: &[&LiveRow], today: NaiveDate, horizon: i64, warehouse_name: Option<&str>,
           created_at: &DateTime<Utc>, updated_at: &DateTime<Utc>, with_rows: bool) -> Value {
    let mut open = 0u32;
    let mut open_units = 0.0;
    let mut fulfilled = 0u32;
    let mut delivered = 0.0;
    let mut cancelled = 0u32;
    let mut overdue = 0u32;
    let mut next: Option<(NaiveDate, f64)> = None;
    let mut keys: BTreeSet<NaiveDate> = BTreeSet::new();
    for r in rows {
        if let Some(n) = r.nominal {
            keys.insert(n);
        }
        match r.status.as_str() {
            "open" => {
                open += 1;
                open_units += r.quantity;
                if r.delivery < today {
                    overdue += 1;
                } else if next.map_or(true, |(d, _)| r.delivery < d) {
                    next = Some((r.delivery, r.quantity));
                }
            }
            "fulfilled" => {
                fulfilled += 1;
                delivered += r.quantity;
            }
            _ => cancelled += 1,
        }
    }
    // Deliveries the schedule is owed inside its horizon and does not hold
    // yet: non-zero means the materialiser is behind (never hidden).
    let missing = if s.status == "active" {
        wanted(s, today, horizon).iter().filter(|o| !keys.contains(&o.nominal)).count()
    } else {
        0
    };
    let mut v = json!({
        "id": s.id,
        "customer": s.customer,
        "reference": s.reference,
        "sku": s.sku,
        "warehouse_id": s.warehouse_id,
        "warehouse_name": warehouse_name,
        "quantity": s.quantity,
        "frequency": match s.spec.frequency {
            Frequency::Weekly => "weekly", Frequency::Fortnightly => "fortnightly",
            Frequency::Semimonthly => "semimonthly", Frequency::Monthly => "monthly" },
        "weekday": s.spec.weekday,
        "day_of_month": s.spec.day_of_month,
        "start_date": isoformat_date(&s.spec.start),
        "end_date": isoformat_date(&s.spec.end),
        "holiday_dates": s.spec.holidays.iter().map(isoformat_date).collect::<Vec<_>>(),
        "avoid_weekends": s.spec.avoid_weekends,
        "shift_rule": match s.spec.shift_rule {
            ShiftRule::Skip => "skip", ShiftRule::Before => "before", ShiftRule::After => "after" },
        "horizon_days": s.horizon_days,
        "effective_horizon_days": horizon,
        "on_top_of_base": s.on_top_of_base,
        "note": s.note,
        "status": s.status,
        "revision": s.revision,
        "created_by": s.created_by,
        "created_at": isoformat_utc(created_at),
        "updated_at": isoformat_utc(updated_at),
        "last_materialised_at": ts(&s.last_materialised_at),
        "last_materialise_error": s.last_materialise_error,
        "period_ended": s.spec.end < today,
        "progress": {
            "open": open,
            "open_units": round4(open_units),
            "fulfilled": fulfilled,
            "delivered_units": round4(delivered),
            "cancelled": cancelled,
            "overdue": overdue,
            "missing": missing,
            "next_delivery": next.map(|(d, q)| json!({"date": isoformat_date(&d), "quantity": q})),
        },
    });
    if with_rows {
        let deliveries: Vec<Value> = rows
            .iter()
            .take(MAX_DELIVERY_ROWS as usize)
            .map(|r| json!({
                "id": r.id,
                "nominal_date": r.nominal.map(|d| isoformat_date(&d)),
                "delivery_date": isoformat_date(&r.delivery),
                "quantity": r.quantity,
                "status": r.status,
            }))
            .collect();
        v["deliveries"] = Value::Array(deliveries);
    }
    v
}

type Stamps = HashMap<String, (DateTime<Utc>, DateTime<Utc>, Option<String>)>;

/// Present schedules with their rows, timestamps and warehouse names.
async fn present_many(pool: &PgPool, tenant_id: &str, schedules: &[Schedule], with_rows: bool) -> Result<Vec<Value>, ApiError> {
    if schedules.is_empty() {
        return Ok(Vec::new());
    }
    let ids: Vec<String> = schedules.iter().map(|s| s.id.clone()).collect();
    let rows = live_rows(pool, tenant_id, &ids, if with_rows { None } else { None }).await?;
    let stamp_rows = sqlx::query(
        "SELECT s.id, s.created_at, s.updated_at, w.name AS warehouse_name
           FROM recurring_delivery_schedules s
           LEFT JOIN warehouses w ON w.id = s.warehouse_id AND w.tenant_id = s.tenant_id
          WHERE s.tenant_id = $1 AND s.id = ANY($2)",
    )
    .bind(tenant_id)
    .bind(&ids)
    .fetch_all(pool)
    .await?;
    let mut stamps: Stamps = HashMap::new();
    for r in &stamp_rows {
        stamps.insert(r.try_get("id")?, (r.try_get("created_at")?, r.try_get("updated_at")?, r.try_get("warehouse_name")?));
    }
    let today = today();
    let mut out = Vec::with_capacity(schedules.len());
    for s in schedules {
        let mine: Vec<&LiveRow> = rows.iter().filter(|r| r.schedule_id == s.id).collect();
        let horizon = effective_horizon(pool, s).await?;
        let (created, updated, wh) = stamps.get(&s.id).cloned().unwrap_or_else(|| (Utc::now(), Utc::now(), None));
        out.push(present(s, &mine, today, horizon, wh.as_deref(), &created, &updated, with_rows));
    }
    Ok(out)
}

async fn get_schedule(pool: &PgPool, tenant_id: &str, allowed: &Option<Vec<String>>, id: &str) -> Result<Schedule, ApiError> {
    let mut conn = pool.acquire().await?;
    let s = load(&mut conn, tenant_id, id, false).await?.ok_or_else(not_found)?;
    if !visible(allowed, s.warehouse_id.as_deref()) {
        return Err(not_found());
    }
    Ok(s)
}

fn outcome_json(o: &Outcome) -> Value {
    json!({"materialised": o.created, "updated": o.updated, "withdrawn": o.withdrawn})
}

fn event_details(s: &Schedule) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("customer".into(), json!(s.customer));
    m.insert("sku".into(), json!(s.sku));
    m.insert("quantity".into(), json!(s.quantity));
    m.insert("frequency".into(), json!(match s.spec.frequency {
        Frequency::Weekly => "weekly", Frequency::Fortnightly => "fortnightly",
        Frequency::Semimonthly => "semimonthly", Frequency::Monthly => "monthly" }));
    m.insert("revision".into(), json!(s.revision));
    m.insert("status".into(), json!(s.status));
    m
}

// ── Reads ────────────────────────────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = reader(&state, &actors, &headers).await?;
    let q = Query::parse(raw.as_deref());
    let status = q.get("status").filter(|s| !s.is_empty()).map(str::to_string);
    if let Some(st) = &status {
        if !STATUSES.contains(&st.as_str()) {
            return Err(err("recurring_delivery_status_invalid", "Unknown status", 422,
                json!({"status": take_chars(st, 20)})));
        }
    }
    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    let sql = format!(
        "SELECT {} FROM recurring_delivery_schedules WHERE tenant_id = $1 AND ($2::text IS NULL OR status = $2)
          ORDER BY start_date, created_at, id",
        materialise::COLUMNS
    );
    let rows = sqlx::query(&sql).bind(&user.tenant_id).bind(&status).fetch_all(&state.pool).await?;
    let mut schedules = Vec::new();
    for r in &rows {
        let s = materialise::row_schedule(r)?;
        if visible(&allowed, s.warehouse_id.as_deref()) {
            schedules.push(s);
        }
    }
    let items = present_many(&state.pool, &user.tenant_id, &schedules, false).await?;
    Ok(ok(json!({"statuses": STATUSES, "items": items})))
}

pub async fn get(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = reader(&state, &actors, &headers).await?;
    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    let s = get_schedule(&state.pool, &user.tenant_id, &allowed, &id).await?;
    let mut items = present_many(&state.pool, &user.tenant_id, &[s], true).await?;
    Ok(ok(items.remove(0)))
}

/// The dates a set of terms produces. Writes nothing and reads no stored row.
pub async fn preview(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: Body = validation::read_body(content_type, &bytes)?;
    reader(&state, &actors, &headers).await?;
    let obj = body_object(&body)?;
    let t = parse_terms(&obj, today())?;
    let pad = Duration::days(crate::recurring::dates::MAX_SHIFT_DAYS + 2);
    let all = occurrences(&t.spec, t.spec.start - pad, t.spec.end + pad);
    let nominal = nominal_dates(&t.spec, t.spec.start, t.spec.end).len();
    let t0 = today();
    let items: Vec<Value> = all
        .iter()
        .take(MAX_PREVIEW_ROWS)
        .map(|o| json!({
            "nominal_date": isoformat_date(&o.nominal),
            "delivery_date": isoformat_date(&o.delivery),
            "shifted": o.nominal != o.delivery,
            "past": o.delivery < t0,
        }))
        .collect();
    Ok(ok(json!({
        "deliveries": items,
        "total": all.len(),
        "truncated": all.len() > MAX_PREVIEW_ROWS,
        "skipped": nominal.saturating_sub(all.len()),
        "quantity": t.quantity,
        "total_units": round4(t.quantity * all.len() as f64),
    })))
}

// ── Writes ───────────────────────────────────────────────────────────────────

const INSERT_SCHEDULE: &str = "
    INSERT INTO recurring_delivery_schedules
        (id, tenant_id, customer, reference, sku, warehouse_id, quantity, frequency, weekday,
         day_of_month, start_date, end_date, holiday_dates, avoid_weekends, shift_rule,
         horizon_days, on_top_of_base, note, status, revision, created_by)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18,
            'active', 1, $19)";

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let t = parse_terms(&obj, today())?;
    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    require_writable_warehouse(&state.pool, &user.tenant_id, &allowed, t.warehouse_id.as_deref()).await?;
    check_warehouse(&state.pool, &user.tenant_id, t.warehouse_id.as_deref()).await?;

    let id = crate::activity::generate_id("rds");
    let holidays: Vec<String> = t.spec.holidays.iter().map(isoformat_date).collect();
    let lead = longest_lead(&state.pool, &user.tenant_id, &t.sku).await?;
    let horizon = horizon_for(t.horizon_days, lead);
    let mut tx = state.pool.begin().await?;
    sqlx::query(INSERT_SCHEDULE)
        .bind(&id)
        .bind(&user.tenant_id)
        .bind(&t.customer)
        .bind(&t.reference)
        .bind(&t.sku)
        .bind(&t.warehouse_id)
        .bind(t.quantity)
        .bind(t.frequency)
        .bind(t.spec.weekday.map(|w| w as i32))
        .bind(t.spec.day_of_month.map(|d| d as i32))
        .bind(t.spec.start)
        .bind(t.spec.end)
        .bind(json!(holidays))
        .bind(t.spec.avoid_weekends)
        .bind(match t.spec.shift_rule {
            ShiftRule::Skip => "skip", ShiftRule::Before => "before", ShiftRule::After => "after" })
        .bind(t.horizon_days)
        .bind(t.on_top_of_base)
        .bind(&t.note)
        .bind(&user.user_id)
        .execute(&mut *tx)
        .await?;
    let s = load(&mut tx, &user.tenant_id, &id, true).await?.ok_or_else(ApiError::internal)?;
    let outcome = fill(&mut tx, &s, today(), horizon).await?;
    sqlx::query("UPDATE recurring_delivery_schedules SET last_materialised_at = NOW() WHERE tenant_id = $1 AND id = $2")
        .bind(&user.tenant_id)
        .bind(&id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::RecurringDeliveryCreated,
        Some(&id), event_details(&s)).await;
    let s = get_schedule(&state.pool, &user.tenant_id, &None, &id).await?;
    let mut out = present_many(&state.pool, &user.tenant_id, &[s], true).await?.remove(0);
    merge(&mut out, outcome_json(&outcome));
    Ok((StatusCode::CREATED, ok(out)))
}

fn merge(target: &mut Value, extra: Value) {
    if let (Value::Object(t), Value::Object(e)) = (target, extra) {
        t.extend(e);
    }
}

fn expected_revision(obj: &Map<String, Value>) -> Result<i32, ApiError> {
    let mut errs = Errors::default();
    let rev = match obj.get("expected_revision") {
        None => {
            errs.push("missing", &[json!("body"), json!("expected_revision")], "Field required".into(),
                &Value::Object(obj.clone()), None);
            None
        }
        Some(Value::Number(n)) if n.as_i64().is_some_and(|v| v >= 1 && v <= i64::from(i32::MAX)) => n.as_i64(),
        Some(other) => {
            errs.push("int_parsing", &[json!("body"), json!("expected_revision")],
                "Input should be a valid integer".into(), other, None);
            None
        }
    };
    errs.into_result()?;
    Ok(rev.unwrap_or(1) as i32)
}

fn stale(current: i32) -> ApiError {
    err("recurring_delivery_stale", "Somebody changed this schedule meanwhile; reload it and try again", 409,
        json!({"revision": current}))
}

pub async fn update(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let expected = expected_revision(&obj)?;
    let t = parse_terms(&obj, today())?;
    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    // The schedule must be one the caller may see before anything else is said.
    let current = get_schedule(&state.pool, &user.tenant_id, &allowed, &id).await?;
    require_writable_warehouse(&state.pool, &user.tenant_id, &allowed, t.warehouse_id.as_deref()).await?;
    check_warehouse(&state.pool, &user.tenant_id, t.warehouse_id.as_deref()).await?;
    if t.sku != current.sku {
        return Err(err("recurring_delivery_sku_locked",
            "The product of a schedule cannot change; cancel it and create another", 409, json!({})));
    }

    let holidays: Vec<String> = t.spec.holidays.iter().map(isoformat_date).collect();
    let lead = longest_lead(&state.pool, &user.tenant_id, &t.sku).await?;
    let horizon = horizon_for(t.horizon_days, lead);
    let mut tx = state.pool.begin().await?;
    let locked = load(&mut tx, &user.tenant_id, &id, true).await?.ok_or_else(not_found)?;
    if locked.revision != expected {
        return Err(stale(locked.revision));
    }
    if locked.status == "cancelled" {
        return Err(err("recurring_delivery_final", "A cancelled schedule can no longer be changed", 409,
            json!({"status": locked.status})));
    }
    sqlx::query(
        "UPDATE recurring_delivery_schedules
            SET customer = $1, reference = $2, warehouse_id = $3, quantity = $4, frequency = $5,
                weekday = $6, day_of_month = $7, start_date = $8, end_date = $9, holiday_dates = $10,
                avoid_weekends = $11, shift_rule = $12, horizon_days = $13, on_top_of_base = $14,
                note = $15, revision = revision + 1, updated_at = NOW()
          WHERE tenant_id = $16 AND id = $17",
    )
    .bind(&t.customer)
    .bind(&t.reference)
    .bind(&t.warehouse_id)
    .bind(t.quantity)
    .bind(t.frequency)
    .bind(t.spec.weekday.map(|w| w as i32))
    .bind(t.spec.day_of_month.map(|d| d as i32))
    .bind(t.spec.start)
    .bind(t.spec.end)
    .bind(json!(holidays))
    .bind(t.spec.avoid_weekends)
    .bind(match t.spec.shift_rule { ShiftRule::Skip => "skip", ShiftRule::Before => "before", ShiftRule::After => "after" })
    .bind(t.horizon_days)
    .bind(t.on_top_of_base)
    .bind(&t.note)
    .bind(&user.tenant_id)
    .bind(&id)
    .execute(&mut *tx)
    .await?;
    let s = load(&mut tx, &user.tenant_id, &id, false).await?.ok_or_else(ApiError::internal)?;
    let outcome = revise(&mut tx, &s, today(), horizon).await?;
    sqlx::query("UPDATE recurring_delivery_schedules SET last_materialised_at = NOW(), last_materialise_error = NULL WHERE tenant_id = $1 AND id = $2")
        .bind(&user.tenant_id)
        .bind(&id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::RecurringDeliveryRevised,
        Some(&id), event_details(&s)).await;
    let s = get_schedule(&state.pool, &user.tenant_id, &None, &id).await?;
    let mut out = present_many(&state.pool, &user.tenant_id, &[s], true).await?.remove(0);
    merge(&mut out, outcome_json(&outcome));
    Ok(ok(out))
}

/// Which status a schedule may move to from each status. Cancelled is final.
pub fn transition_allowed(from: &str, to: &str) -> bool {
    matches!((from, to), ("active", "paused") | ("active", "cancelled") | ("paused", "active") | ("paused", "cancelled"))
}

pub async fn set_status(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let mut errs = Errors::default();
    let status = str_field(&mut errs, &obj, &[json!("body")], "status", true, false,
        &StrRules { min_length: None, max_length: Some(20), pattern: None });
    let expected = if errs.0.is_empty() { Some(expected_revision(&obj)) } else { None };
    errs.into_result()?;
    let expected = expected.ok_or_else(ApiError::internal)??;
    let status = opt_str(status).unwrap_or_default();
    if !STATUSES.contains(&status.as_str()) {
        return Err(err("recurring_delivery_status_invalid", "Unknown status", 422,
            json!({"status": take_chars(&status, 20)})));
    }
    let allowed = wscope::scope_warehouse_ids(&state.pool, &user).await?;
    let current = get_schedule(&state.pool, &user.tenant_id, &allowed, &id).await?;
    require_writable_warehouse(&state.pool, &user.tenant_id, &allowed, current.warehouse_id.as_deref()).await?;
    let horizon = effective_horizon(&state.pool, &current).await?;

    let mut tx = state.pool.begin().await?;
    let locked = load(&mut tx, &user.tenant_id, &id, true).await?.ok_or_else(not_found)?;
    if locked.revision != expected {
        return Err(stale(locked.revision));
    }
    if !transition_allowed(&locked.status, &status) {
        return Err(err("recurring_delivery_transition_invalid",
            "A schedule cannot move to that status from its current one", 409,
            json!({"from": locked.status, "to": status})));
    }
    sqlx::query("UPDATE recurring_delivery_schedules SET status = $1, revision = revision + 1, updated_at = NOW() WHERE tenant_id = $2 AND id = $3")
        .bind(&status)
        .bind(&user.tenant_id)
        .bind(&id)
        .execute(&mut *tx)
        .await?;
    let s = load(&mut tx, &user.tenant_id, &id, false).await?.ok_or_else(ApiError::internal)?;
    let outcome = revise(&mut tx, &s, today(), horizon).await?;
    if s.status == "active" {
        sqlx::query("UPDATE recurring_delivery_schedules SET last_materialised_at = NOW(), last_materialise_error = NULL WHERE tenant_id = $1 AND id = $2")
            .bind(&user.tenant_id)
            .bind(&id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;

    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::RecurringDeliveryStatusChanged,
        Some(&id), event_details(&s)).await;
    let s = get_schedule(&state.pool, &user.tenant_id, &None, &id).await?;
    let mut out = present_many(&state.pool, &user.tenant_id, &[s], true).await?.remove(0);
    merge(&mut out, outcome_json(&outcome));
    Ok(ok(out))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn today_fixed() -> NaiveDate {
        NaiveDate::from_ymd_opt(2026, 10, 6).unwrap()
    }

    fn body(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    fn base() -> Value {
        json!({"customer": "ACME", "sku": "A1", "quantity": 25, "frequency": "weekly", "weekday": 2,
               "start_date": "2026-10-01", "end_date": "2027-03-31"})
    }

    fn code(r: Result<Terms, ApiError>) -> String {
        r.unwrap_err().code().unwrap_or("").to_string()
    }

    #[test]
    fn a_complete_body_parses_with_defaults() {
        let t = parse_terms(&body(base()), today_fixed()).unwrap();
        assert_eq!(t.horizon_days, 180);
        assert!(t.on_top_of_base);
        assert_eq!(t.spec.shift_rule, ShiftRule::After);
        assert_eq!(t.spec.weekday, Some(2));
        assert_eq!(t.spec.day_of_month, None);
    }

    #[test]
    fn the_rule_for_each_frequency_is_required() {
        let mut b = base();
        b.as_object_mut().unwrap().remove("weekday");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_weekday_required");
        let mut m = base();
        m["frequency"] = json!("monthly");
        assert_eq!(code(parse_terms(&body(m.clone()), today_fixed())), "recurring_delivery_day_of_month_required");
        m["day_of_month"] = json!(15);
        let t = parse_terms(&body(m), today_fixed()).unwrap();
        // The rule that does not apply is dropped, not stored.
        assert_eq!((t.spec.weekday, t.spec.day_of_month), (None, Some(15)));
    }

    #[test]
    fn bad_terms_are_named() {
        let mut b = base();
        b["frequency"] = json!("daily");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_frequency_invalid");
        let mut b = base();
        b["end_date"] = json!("2026-09-30");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_period_invalid");
        let mut b = base();
        b["start_date"] = json!("2026-01-01");
        b["end_date"] = json!("2026-10-05");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_period_ended");
        let mut b = base();
        b["end_date"] = json!("2040-01-01");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_period_too_long");
        let mut b = base();
        b["weekday"] = json!(7);
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_field_invalid");
        let mut b = base();
        b["weekday"] = json!(1.5);
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_field_invalid");
        let mut b = base();
        b["holiday_dates"] = json!(["2026-12-25", "nope"]);
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_holiday_invalid");
        let mut b = base();
        b["avoid_weekends"] = json!(true);
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_weekend_rule_invalid");
        let mut b = base();
        b["shift_rule"] = json!("never");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_shift_rule_invalid");
        let mut b = base();
        b["customer"] = json!("   ");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_customer_required");
    }

    #[test]
    fn a_window_that_holds_no_delivery_is_refused() {
        // A Wednesday schedule whose whole period is a Thursday.
        let mut b = base();
        b["start_date"] = json!("2026-10-08");
        b["end_date"] = json!("2026-10-08");
        assert_eq!(code(parse_terms(&body(b), today_fixed())), "recurring_delivery_no_deliveries");
    }

    #[test]
    fn quantity_must_be_positive() {
        let mut b = base();
        b["quantity"] = json!(0);
        assert_eq!(parse_terms(&body(b), today_fixed()).unwrap_err().status.as_u16(), 422);
        let mut b = base();
        b["quantity"] = json!(2e9);
        assert_eq!(parse_terms(&body(b), today_fixed()).unwrap_err().status.as_u16(), 422);
    }

    #[test]
    fn only_the_allowed_status_moves_exist() {
        assert!(transition_allowed("active", "paused"));
        assert!(transition_allowed("paused", "active"));
        assert!(transition_allowed("paused", "cancelled"));
        assert!(!transition_allowed("cancelled", "active"));
        assert!(!transition_allowed("active", "active"));
    }

    #[test]
    fn visibility_follows_the_committed_demand_rule() {
        assert!(visible(&None, None));
        let scoped = Some(vec!["w1".to_string()]);
        assert!(visible(&scoped, Some("w1")));
        assert!(!visible(&scoped, Some("w2")));
        assert!(!visible(&scoped, None));
    }
}
