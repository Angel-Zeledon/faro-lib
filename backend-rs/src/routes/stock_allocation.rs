//! Stock allocation among committed customers. NEW routes, written in Rust only
//! (no Python twin, so no Python failover; see docs/rust-migration.md).
//!
//! When the stock on hand plus the arrivals that can be counted cannot cover
//! every open commitment of a SKU, these routes say who gets what: by the
//! tenant's customer priority tiers, optionally proportionally inside a tier,
//! never serving a commitment from supply that arrives after its date
//! (`allocation::core`). A what-if preview changes priorities, fair-share tiers
//! or adds a hypothetical order WITHOUT saving anything; "apply" records the
//! result as reservations.
//!
//! Everything here is ADVISORY DATA. No route writes `inventory_stock`, and
//! the semaforo keeps planning on `quantity x probability` of every open
//! commitment exactly as before (`committed_demand_service.committed_units`):
//! there is one authority for how committed demand moves the recommendation,
//! and this feature does not add a second. A reservation records "if the stock
//! on hand is shared like this"; a later edit or closing of the commitment
//! shows it as stale at read time instead of silently standing for a different
//! order.
//!
//! Routes (all `/api/v1/allocation/...`):
//! * `GET  /priorities`  tiers, fair-share tiers, customers with no tier yet
//! * `PUT  /priorities`  analyst: set / clear customer tiers, set fair-share tiers
//! * `POST /preview`     any signed-in role: one SKU's allocation, with what-if
//! * `POST /apply`       analyst: record reservations (needs the preview's hash)
//! * `POST /release`     analyst: release a SKU's active reservations
//! * `GET  /reservations`  active reservations, flagged stale when the order moved
//! * `GET  /overview`    every SKU with open commitments: who is short
//!
//! Stock is a company-wide figure, so a caller limited to some warehouses is
//! refused on every route (`warehouse_scope_company_totals`), like the other
//! company-wide totals.

use std::collections::{BTreeMap, HashMap, HashSet};

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Duration, Local, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::{generate_id, record_event, Event};
use crate::allocation::core::to_micro;
use crate::allocation::engine::{
    self, compute_sku, customer_key, Policy, SkuResult, DEFAULT_TIER, MAX_CLAIMS_PER_SKU, MAX_TIER, MIN_TIER,
};
use crate::allocation::supply::{self, ArrivalDetail, OpenCommitment, SkuSupply};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, isoformat_utc, py_strip, take_chars};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, list_field, str_field, Body, Errors, Field, StrRules};

/// Not an `INTERNAL_TAGS` entry of the Python app (it has no such router):
/// an allocation is a decision a person takes in the screen, so no API key.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal: sharing scarce stock among customers is a decision recorded under a person's name",
    ),
    is_mcp: false,
};

const MAX_CUSTOMER_LENGTH: usize = 200;
const MAX_PRIORITY_ROWS: usize = 500;
const MAX_EXTRA_ARRIVALS: usize = 20;
const MAX_YEARS_AHEAD: i64 = 10;
const MAX_QUANTITY: f64 = 1e9;

fn today() -> NaiveDate {
    Local::now().date_naive()
}

fn details(pairs: &[(&str, Value)]) -> Map<String, Value> {
    pairs.iter().map(|(k, v)| ((*k).to_string(), v.clone())).collect()
}

fn rules(min: Option<usize>, max: Option<usize>) -> StrRules {
    StrRules { min_length: min, max_length: max, pattern: None }
}

// ── Guards ───────────────────────────────────────────────────────────────────

/// Any signed-in role, company-wide callers only.
async fn reader(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    wscope::require_company_wide(&state.pool, &user).await?;
    Ok(user)
}

/// Analyst or above (and the expired-trial read-only guard), company-wide only.
async fn writer(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_analyst_or_above(state, &user).await?;
    wscope::require_company_wide(&state.pool, &user).await?;
    Ok(user)
}

fn body_of(headers: &HeaderMap, bytes: &Bytes) -> Result<Map<String, Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body: Body = validation::read_body(content_type, bytes)?;
    body_object(&body)
}

// ── Body parsing ─────────────────────────────────────────────────────────────

fn body_loc() -> Vec<Value> {
    vec![Value::String("body".into())]
}

/// A tier: an integer 1..=9 (a JSON bool, float or string is not one).
fn tier_value(errs: &mut Errors, at: &[Value], v: &Value) -> Option<u8> {
    match v.as_i64() {
        Some(n) if (i64::from(MIN_TIER)..=i64::from(MAX_TIER)).contains(&n) => Some(n as u8),
        Some(_) => {
            errs.push("value_error", at, format!("Tier must be between {MIN_TIER} and {MAX_TIER}"), v, None);
            None
        }
        None => {
            errs.push("int_type", at, "Input should be a valid integer".into(), v, None);
            None
        }
    }
}

/// `[{customer, tier}]`; `allow_null` lets a tier be null (clear it).
fn parse_priority_rows(
    errs: &mut Errors,
    obj: &Map<String, Value>,
    name: &str,
    allow_null: bool,
) -> Vec<(String, Option<u8>)> {
    let prefix = body_loc();
    let mut out = Vec::new();
    if !obj.contains_key(name) {
        return out;
    }
    let Some(list) = list_field(errs, obj, &prefix, name, 0, MAX_PRIORITY_ROWS) else {
        return out;
    };
    for (i, item) in list.iter().enumerate() {
        let p = [Value::String("body".into()), Value::String(name.into()), json!(i)];
        let Some(row) = validation::as_object(errs, &p, item) else { continue };
        let customer = str_field(errs, row, &p, "customer", true, false, &rules(Some(1), Some(MAX_CUSTOMER_LENGTH)));
        let at = validation::loc(&p, "tier");
        let tier = match row.get("tier") {
            None => {
                errs.push("missing", &at, "Field required".into(), item, None);
                continue;
            }
            Some(Value::Null) if allow_null => None,
            Some(v) => match tier_value(errs, &at, v) {
                Some(t) => Some(t),
                None => continue,
            },
        };
        if let Field::Value(c) = customer {
            let c = py_strip(&c).to_string();
            if c.is_empty() {
                errs.push("string_too_short", &validation::loc(&p, "customer"),
                    "String should have at least 1 character".into(), item, None);
                continue;
            }
            out.push((c, tier));
        }
    }
    out
}

fn parse_fair_tiers(errs: &mut Errors, obj: &Map<String, Value>) -> Option<Vec<u8>> {
    let v = obj.get("fair_share_tiers")?;
    if v.is_null() {
        return None;
    }
    let at = validation::loc(&body_loc(), "fair_share_tiers");
    let Some(list) = v.as_array() else {
        errs.push("list_type", &at, "Input should be a valid list".into(), v, None);
        return None;
    };
    let mut out = Vec::new();
    for (i, item) in list.iter().enumerate() {
        let mut p = at.clone();
        p.push(json!(i));
        if let Some(t) = tier_value(errs, &p, item) {
            out.push(t);
        }
    }
    out.sort_unstable();
    out.dedup();
    Some(out)
}

/// Hypothetical arrivals `[{date, quantity}]` for the what-if preview.
fn parse_extra_arrivals(errs: &mut Errors, obj: &Map<String, Value>, today: NaiveDate) -> Vec<(NaiveDate, i64)> {
    let mut out = Vec::new();
    let Some(v) = obj.get("extra_arrivals") else { return out };
    if v.is_null() {
        return out;
    }
    let prefix = body_loc();
    let Some(list) = list_field(errs, obj, &prefix, "extra_arrivals", 0, MAX_EXTRA_ARRIVALS) else {
        return out;
    };
    for (i, item) in list.iter().enumerate() {
        let p = [Value::String("body".into()), Value::String("extra_arrivals".into()), json!(i)];
        let Some(row) = validation::as_object(errs, &p, item) else { continue };
        let date = match row.get("date").and_then(Value::as_str).and_then(|s| date_fromisoformat(py_strip(s))) {
            Some(d) => d,
            None => {
                errs.push("date_from_datetime_parsing", &validation::loc(&p, "date"),
                    "Input should be a valid date in the format YYYY-MM-DD".into(),
                    row.get("date").unwrap_or(&Value::Null), None);
                continue;
            }
        };
        if date < today || date > today + Duration::days(365 * MAX_YEARS_AHEAD) {
            errs.push("value_error", &validation::loc(&p, "date"),
                "The date must be between today and ten years ahead".into(), &json!(isoformat_date(&date)), None);
            continue;
        }
        let q = row.get("quantity").and_then(Value::as_f64);
        match q {
            Some(q) if q.is_finite() && q > 0.0 && q <= MAX_QUANTITY => out.push((date, to_micro(q, 1.0))),
            _ => errs.push("greater_than", &validation::loc(&p, "quantity"),
                "Quantity must be greater than 0".into(), row.get("quantity").unwrap_or(&Value::Null), None),
        }
    }
    out
}

fn parse_sku(errs: &mut Errors, obj: &Map<String, Value>) -> Option<String> {
    match str_field(errs, obj, &body_loc(), "sku", true, false, &rules(Some(1), Some(200))) {
        Field::Value(s) => {
            let s = py_strip(&s).to_string();
            if s.is_empty() {
                errs.push("string_too_short", &validation::loc(&body_loc(), "sku"),
                    "String should have at least 1 character".into(), &Value::String(s), None);
                None
            } else {
                Some(s)
            }
        }
        _ => None,
    }
}

// ── Policy ───────────────────────────────────────────────────────────────────

async fn load_policy(pool: &PgPool, tenant_id: &str) -> Result<Policy, ApiError> {
    let rows = sqlx::query("SELECT customer_key, customer, tier FROM allocation_customer_priorities WHERE tenant_id = $1")
        .bind(tenant_id)
        .fetch_all(pool)
        .await?;
    let mut policy = Policy::default();
    for r in &rows {
        let tier: i32 = r.try_get("tier")?;
        policy.priorities.insert(r.try_get("customer_key")?, (r.try_get("customer")?, tier as u8));
    }
    let fair = sqlx::query("SELECT tier FROM allocation_tier_policy WHERE tenant_id = $1 AND fair_share ORDER BY tier")
        .bind(tenant_id)
        .fetch_all(pool)
        .await?;
    for r in &fair {
        policy.fair_tiers.push(r.try_get::<i32, _>("tier")? as u8);
    }
    Ok(policy)
}

fn tier_rows_json(policy: &Policy) -> Vec<Value> {
    let mut rows: Vec<(&String, &(String, u8))> = policy.priorities.iter().collect();
    rows.sort_by(|a, b| (a.1 .1, &a.1 .0).cmp(&(b.1 .1, &b.1 .0)));
    rows.into_iter()
        .map(|(k, (name, tier))| json!({"customer": name, "customer_key": k, "tier": tier}))
        .collect()
}

pub async fn get_priorities(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = reader(&state, &actors, &headers).await?;
    let policy = load_policy(&state.pool, &user.tenant_id).await?;
    let commitments = supply::open_commitments(&state.pool, &user.tenant_id, None).await?;

    // Customers on open commitments that have no tier yet.
    let mut unassigned: BTreeMap<String, (String, usize, i64)> = BTreeMap::new();
    let mut without_customer = 0usize;
    for c in &commitments {
        let name = c.customer.as_deref().map(py_strip).unwrap_or("");
        if name.is_empty() {
            without_customer += 1;
            continue;
        }
        let key = customer_key(name);
        if policy.priorities.contains_key(&key) {
            continue;
        }
        let e = unassigned.entry(key).or_insert_with(|| (name.to_string(), 0, 0));
        e.1 += 1;
        e.2 += to_micro(c.quantity, c.probability);
    }
    let unassigned_json: Vec<Value> = unassigned
        .into_iter()
        .map(|(key, (name, n, micro))| json!({
            "customer": name, "customer_key": key, "open_commitments": n, "units": engine::units(micro),
        }))
        .collect();
    Ok(ok(json!({
        "default_tier": DEFAULT_TIER,
        "tiers": (MIN_TIER..=MAX_TIER).collect::<Vec<u8>>(),
        "priorities": tier_rows_json(&policy),
        "fair_share_tiers": policy.fair_tiers,
        "unassigned_customers": unassigned_json,
        "commitments_without_customer": without_customer,
    })))
}

pub async fn put_priorities(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let user = writer(&state, &actors, &headers).await?;
    let obj = body_of(&headers, &bytes)?;
    let mut errs = Errors::default();
    let rows = parse_priority_rows(&mut errs, &obj, "priorities", true);
    let fair = parse_fair_tiers(&mut errs, &obj);
    if !obj.contains_key("priorities") && fair.is_none() && errs.0.is_empty() {
        errs.push("missing", &validation::loc(&body_loc(), "priorities"), "Field required".into(), &Value::Object(obj.clone()), None);
    }
    errs.into_result()?;

    let mut seen = HashSet::new();
    for (c, _) in &rows {
        if !seen.insert(customer_key(c)) {
            return Err(ApiError::app("allocation_duplicate_customer",
                "A customer appears twice in the same request", 422, json!({"customer": c})));
        }
    }

    let before = load_policy(&state.pool, &user.tenant_id).await?;
    let mut tx = state.pool.begin().await?;
    let mut changed = 0usize;
    for (customer, tier) in &rows {
        let key = customer_key(customer);
        match tier {
            Some(t) => {
                let same = before.priorities.get(&key).is_some_and(|(name, bt)| bt == t && name == customer);
                if same {
                    continue;
                }
                sqlx::query(
                    "INSERT INTO allocation_customer_priorities (tenant_id, customer_key, customer, tier, updated_by)
                     VALUES ($1, $2, $3, $4, $5)
                     ON CONFLICT (tenant_id, customer_key)
                     DO UPDATE SET customer = EXCLUDED.customer, tier = EXCLUDED.tier,
                                   updated_by = EXCLUDED.updated_by, updated_at = NOW()",
                )
                .bind(&user.tenant_id).bind(&key).bind(customer).bind(i32::from(*t)).bind(&user.user_id)
                .execute(&mut *tx).await?;
                changed += 1;
            }
            None => {
                let r = sqlx::query("DELETE FROM allocation_customer_priorities WHERE tenant_id = $1 AND customer_key = $2")
                    .bind(&user.tenant_id).bind(&key).execute(&mut *tx).await?;
                changed += r.rows_affected() as usize;
            }
        }
    }
    if let Some(fair) = &fair {
        if *fair != before.fair_tiers {
            let wanted: Vec<i32> = fair.iter().map(|t| i32::from(*t)).collect();
            sqlx::query("DELETE FROM allocation_tier_policy WHERE tenant_id = $1 AND NOT (tier = ANY($2))")
                .bind(&user.tenant_id).bind(&wanted).execute(&mut *tx).await?;
            for t in &wanted {
                sqlx::query(
                    "INSERT INTO allocation_tier_policy (tenant_id, tier, fair_share, updated_by) VALUES ($1, $2, TRUE, $3)
                     ON CONFLICT (tenant_id, tier) DO UPDATE SET fair_share = TRUE, updated_by = EXCLUDED.updated_by, updated_at = NOW()",
                )
                .bind(&user.tenant_id).bind(t).bind(&user.user_id).execute(&mut *tx).await?;
            }
            changed += 1;
        }
    }
    tx.commit().await?;

    if changed > 0 {
        record_event(&state.pool, &user.tenant_id, &user.user_id, Event::AllocationPrioritiesChanged, None,
            details(&[("customers", json!(changed))])).await;
    }
    let policy = load_policy(&state.pool, &user.tenant_id).await?;
    Ok(ok(json!({
        "priorities": tier_rows_json(&policy),
        "fair_share_tiers": policy.fair_tiers,
        "changed": changed,
    })))
}

// ── Computing one SKU ────────────────────────────────────────────────────────

struct SkuInputs {
    commitments: Vec<OpenCommitment>,
    supply: SkuSupply,
}

async fn sku_inputs(pool: &PgPool, tenant_id: &str, sku: &str, today: NaiveDate) -> Result<SkuInputs, ApiError> {
    let commitments = supply::open_commitments(pool, tenant_id, Some(sku)).await?;
    if commitments.is_empty() {
        return Err(ApiError::app("allocation_no_commitments",
            "That product has no open customer commitments", 404, json!({"sku": sku})));
    }
    if commitments.len() > MAX_CLAIMS_PER_SKU {
        return Err(ApiError::app("allocation_too_many_commitments",
            "That product has too many open commitments to allocate at once", 422,
            json!({"sku": sku, "open": commitments.len(), "max": MAX_CLAIMS_PER_SKU})));
    }
    let mut map = supply::load_supply(pool, tenant_id, &[sku.to_string()], today).await?;
    let supply = map.remove(sku).unwrap_or_default();
    Ok(SkuInputs { commitments, supply })
}

struct ReservationRow {
    id: String,
    run_id: String,
    sku: String,
    commitment_id: String,
    customer: Option<String>,
    tier: i32,
    units_micro: i64,
    reserved_micro: i64,
    short_micro: i64,
    delivery_date: NaiveDate,
    created_by: String,
    created_at: DateTime<Utc>,
    stale_reason: Option<&'static str>,
}

async fn load_reservations(pool: &PgPool, tenant_id: &str, sku: Option<&str>) -> Result<Vec<ReservationRow>, ApiError> {
    let rows = sqlx::query(
        "SELECT r.id, r.run_id, r.sku, r.commitment_id, r.customer, r.tier, r.units_micro, r.reserved_micro,
                r.short_micro, r.commitment_delivery_date, r.created_by, r.created_at,
                c.status AS c_status,
                (c.quantity IS DISTINCT FROM r.commitment_quantity
                 OR c.probability IS DISTINCT FROM r.commitment_probability
                 OR c.delivery_date IS DISTINCT FROM r.commitment_delivery_date
                 OR c.sku IS DISTINCT FROM r.sku) AS changed
           FROM stock_reservations r
           LEFT JOIN committed_demand c ON c.id = r.commitment_id AND c.tenant_id = r.tenant_id
          WHERE r.tenant_id = $1 AND r.status = 'active' AND ($2::text IS NULL OR r.sku = $2)
          ORDER BY r.sku, r.tier, r.commitment_delivery_date, r.commitment_id",
    )
    .bind(tenant_id)
    .bind(sku)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        let c_status: Option<String> = r.try_get("c_status")?;
        let changed: bool = r.try_get::<Option<bool>, _>("changed")?.unwrap_or(true);
        let stale_reason = match c_status.as_deref() {
            Some("open") if !changed => None,
            Some("open") => Some("commitment_changed"),
            _ => Some("commitment_closed"),
        };
        out.push(ReservationRow {
            id: r.try_get("id")?,
            run_id: r.try_get("run_id")?,
            sku: r.try_get("sku")?,
            commitment_id: r.try_get("commitment_id")?,
            customer: r.try_get("customer")?,
            tier: r.try_get("tier")?,
            units_micro: r.try_get("units_micro")?,
            reserved_micro: r.try_get("reserved_micro")?,
            short_micro: r.try_get("short_micro")?,
            delivery_date: r.try_get("commitment_delivery_date")?,
            created_by: r.try_get("created_by")?,
            created_at: r.try_get("created_at")?,
            stale_reason,
        });
    }
    Ok(out)
}

fn arrival_json(a: &ArrivalDetail) -> Value {
    json!({
        "kind": a.kind,
        "reference": if a.reference.is_empty() { Value::Null } else { Value::String(a.reference.clone()) },
        "quantity": engine::units(a.qty_micro),
        "date": a.date.map(|d| isoformat_date(&d)),
        "source": a.source,
    })
}

fn result_json(r: &SkuResult, reservations: &HashMap<String, (i64, Option<&'static str>)>, today: NaiveDate) -> Value {
    let lines: Vec<Value> = r
        .lines
        .iter()
        .map(|l| {
            let reserved = reservations.get(&l.commitment_id);
            json!({
                "commitment_id": l.commitment_id,
                "customer": l.customer,
                "tier": l.tier,
                "tier_source": l.tier_source,
                "delivery_date": isoformat_date(&l.delivery_date),
                "overdue": l.overdue,
                "quantity": l.quantity,
                "probability": l.probability,
                "units": engine::units(l.units_micro),
                "allocated": if r.status == "ok" { json!(engine::units(l.allocated_micro)) } else { Value::Null },
                "short": if r.status == "ok" { json!(engine::units(l.short_micro())) } else { Value::Null },
                "reserved": reserved.map(|(m, _)| engine::units(*m)),
                "reservation_stale": reserved.and_then(|(_, s)| *s),
            })
        })
        .collect();

    // One row per customer, served order (tier, then most short first).
    let mut groups: BTreeMap<String, (Option<String>, u8, i64, i64, usize)> = BTreeMap::new();
    for l in &r.lines {
        let key = l.customer.as_deref().map(customer_key).unwrap_or_default();
        let g = groups.entry(key).or_insert_with(|| (l.customer.clone(), l.tier, 0, 0, 0));
        g.2 += l.units_micro;
        g.3 += l.allocated_micro;
        g.4 += 1;
    }
    let mut customers: Vec<(Option<String>, u8, i64, i64, usize)> = groups.into_values().collect();
    customers.sort_by(|a, b| (a.1, -(a.2 - a.3), &a.0).cmp(&(b.1, -(b.2 - b.3), &b.0)));
    let customers_json: Vec<Value> = customers
        .iter()
        .map(|(name, tier, units, alloc, n)| json!({
            "customer": name, "tier": tier, "commitments": n,
            "units": engine::units(*units),
            "allocated": if r.status == "ok" { json!(engine::units(*alloc)) } else { Value::Null },
            "short": if r.status == "ok" { json!(engine::units(*units - *alloc)) } else { Value::Null },
        }))
        .collect();

    let ok_status = r.status == "ok";
    json!({
        "sku": r.sku,
        "as_of": isoformat_date(&today),
        "scope": "company",
        "status": r.status,
        "contested": r.contested(),
        "advisory": true,
        "stock": r.stock_micro.map(engine::units),
        "incoming": r.counted.iter().map(arrival_json).collect::<Vec<_>>(),
        "incoming_not_counted": r.uncounted.iter().map(arrival_json).collect::<Vec<_>>(),
        "fair_share_tiers": r.fair_tiers,
        "totals": {
            "demand": engine::units(r.demand_micro()),
            "allocated": if ok_status { json!(engine::units(r.allocated_micro())) } else { Value::Null },
            "short": if ok_status { json!(engine::units(r.short_micro())) } else { Value::Null },
            "commitments": r.lines.len(),
            "commitments_short": if ok_status { json!(r.lines.iter().filter(|l| l.short_micro() > 0).count()) } else { Value::Null },
        },
        "customers": customers_json,
        "lines": lines,
        "result_hash": r.result_hash,
    })
}

fn reservation_map(rows: &[ReservationRow]) -> HashMap<String, (i64, Option<&'static str>)> {
    rows.iter().map(|r| (r.commitment_id.clone(), (r.reserved_micro, r.stale_reason))).collect()
}

// ── Preview ──────────────────────────────────────────────────────────────────

pub async fn preview(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let user = reader(&state, &actors, &headers).await?;
    let obj = body_of(&headers, &bytes)?;
    let day = today();
    let mut errs = Errors::default();
    let sku = parse_sku(&mut errs, &obj);
    let overrides = parse_priority_rows(&mut errs, &obj, "tier_overrides", false);
    let fair = parse_fair_tiers(&mut errs, &obj);
    let extra = parse_extra_arrivals(&mut errs, &obj, day);
    errs.into_result()?;
    let sku = sku.ok_or_else(ApiError::internal)?;
    let overrides: Vec<(String, u8)> = overrides.into_iter().filter_map(|(c, t)| t.map(|t| (c, t))).collect();

    let saved = load_policy(&state.pool, &user.tenant_id).await?;
    let policy = saved.with_overrides(&overrides, fair.as_deref());
    let inputs = sku_inputs(&state.pool, &user.tenant_id, &sku, day).await?;
    let result = compute_sku(&sku, &inputs.commitments, &inputs.supply, &extra, &policy, day);
    let reservations = load_reservations(&state.pool, &user.tenant_id, Some(&sku)).await?;
    let mut data = result_json(&result, &reservation_map(&reservations), day);
    if let Value::Object(m) = &mut data {
        m.insert("what_if".into(), json!(!overrides.is_empty() || fair.is_some() || !extra.is_empty()));
    }
    Ok(ok(data))
}

// ── Apply / release ──────────────────────────────────────────────────────────

pub async fn apply(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let user = writer(&state, &actors, &headers).await?;
    let obj = body_of(&headers, &bytes)?;
    let day = today();
    let mut errs = Errors::default();
    let sku = parse_sku(&mut errs, &obj);
    let hash = str_field(&mut errs, &obj, &body_loc(), "result_hash", true, false, &rules(Some(64), Some(64)));
    let overrides = parse_priority_rows(&mut errs, &obj, "tier_overrides", false);
    let fair = parse_fair_tiers(&mut errs, &obj);
    errs.into_result()?;
    let sku = sku.ok_or_else(ApiError::internal)?;
    let Field::Value(hash) = hash else { return Err(ApiError::internal()) };
    if obj.get("extra_arrivals").is_some_and(|v| !v.is_null()) {
        return Err(ApiError::app("allocation_apply_hypothetical_supply",
            "A reservation cannot be recorded against a hypothetical order", 422, json!({})));
    }
    let overrides: Vec<(String, u8)> = overrides.into_iter().filter_map(|(c, t)| t.map(|t| (c, t))).collect();
    let saved = load_policy(&state.pool, &user.tenant_id).await?;
    let policy = saved.with_overrides(&overrides, fair.as_deref());

    // One apply per SKU at a time: the second waits, then recomputes on the data
    // the first left behind.
    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(format!("allocation:{}:{}", user.tenant_id, sku))
        .execute(&mut *tx)
        .await?;
    let inputs = sku_inputs(&state.pool, &user.tenant_id, &sku, day).await?;
    let result = compute_sku(&sku, &inputs.commitments, &inputs.supply, &[], &policy, day);
    if result.status != "ok" {
        return Err(ApiError::app("allocation_stock_unknown",
            "That product has no stock record, so there is nothing to allocate", 409, json!({"sku": sku})));
    }
    if result.result_hash != hash {
        return Err(ApiError::app("allocation_stale",
            "The stock, the arrivals or the commitments changed since this preview. Preview again before applying.",
            409, json!({"sku": sku})));
    }

    let commitment_ids: Vec<String> = result.lines.iter().map(|l| l.commitment_id.clone()).collect();
    sqlx::query(
        "UPDATE stock_reservations SET status = 'released', released_by = $1, released_at = NOW(), release_reason = 'replaced'
          WHERE tenant_id = $2 AND status = 'active' AND (sku = $3 OR commitment_id = ANY($4))",
    )
    .bind(&user.user_id).bind(&user.tenant_id).bind(&sku).bind(&commitment_ids)
    .execute(&mut *tx).await?;

    let run_id = generate_id("alr");
    let policy_snapshot = json!({
        "default_tier": DEFAULT_TIER,
        "fair_share_tiers": result.fair_tiers,
        "tiers": result.lines.iter().map(|l| json!({"commitment_id": l.commitment_id, "tier": l.tier, "source": l.tier_source})).collect::<Vec<_>>(),
        "overrides_applied": !overrides.is_empty() || fair.is_some(),
    });
    let supply_snapshot = json!({
        "as_of": isoformat_date(&day),
        "stock": result.stock_micro.map(engine::units),
        "incoming": result.counted.iter().map(arrival_json).collect::<Vec<_>>(),
        "incoming_not_counted": result.uncounted.iter().map(arrival_json).collect::<Vec<_>>(),
    });
    sqlx::query(
        "INSERT INTO allocation_runs (id, tenant_id, sku, result_hash, policy, supply, created_by)
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(&run_id).bind(&user.tenant_id).bind(&sku).bind(&result.result_hash)
    .bind(&policy_snapshot).bind(&supply_snapshot).bind(&user.user_id)
    .execute(&mut *tx).await?;

    let by_id: HashMap<&str, &OpenCommitment> = inputs.commitments.iter().map(|c| (c.id.as_str(), c)).collect();
    for line in &result.lines {
        let c = by_id.get(line.commitment_id.as_str()).ok_or_else(ApiError::internal)?;
        sqlx::query(
            "INSERT INTO stock_reservations
                 (id, tenant_id, run_id, sku, commitment_id, customer, tier, units_micro, reserved_micro, short_micro,
                  commitment_quantity, commitment_probability, commitment_delivery_date, created_by)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
        )
        .bind(generate_id("res")).bind(&user.tenant_id).bind(&run_id).bind(&sku).bind(&line.commitment_id)
        .bind(&line.customer).bind(i32::from(line.tier))
        .bind(line.units_micro).bind(line.allocated_micro).bind(line.short_micro())
        .bind(c.quantity).bind(c.probability).bind(c.delivery_date).bind(&user.user_id)
        .execute(&mut *tx).await?;
    }
    tx.commit().await?;

    let customers_short = result
        .lines
        .iter()
        .filter(|l| l.short_micro() > 0)
        .map(|l| l.customer.as_deref().map(customer_key).unwrap_or_default())
        .collect::<HashSet<_>>()
        .len();
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::AllocationApplied, Some(&run_id),
        details(&[
            ("sku", json!(sku)),
            ("reserved", json!(engine::units(result.allocated_micro()))),
            ("short", json!(engine::units(result.short_micro()))),
        ])).await;
    Ok(ok(json!({
        "run_id": run_id,
        "sku": sku,
        "reservations": result.lines.len(),
        "reserved": engine::units(result.allocated_micro()),
        "short": engine::units(result.short_micro()),
        "customers_short": customers_short,
        "result_hash": result.result_hash,
        "advisory": true,
    })))
}

pub async fn release(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let user = writer(&state, &actors, &headers).await?;
    let obj = body_of(&headers, &bytes)?;
    let mut errs = Errors::default();
    let sku = parse_sku(&mut errs, &obj);
    errs.into_result()?;
    let sku = sku.ok_or_else(ApiError::internal)?;
    let released = sqlx::query(
        "UPDATE stock_reservations SET status = 'released', released_by = $1, released_at = NOW(), release_reason = 'released_by_user'
          WHERE tenant_id = $2 AND sku = $3 AND status = 'active'",
    )
    .bind(&user.user_id).bind(&user.tenant_id).bind(&sku)
    .execute(&state.pool).await?
    .rows_affected();
    if released > 0 {
        record_event(&state.pool, &user.tenant_id, &user.user_id, Event::AllocationReleased, None,
            details(&[("sku", json!(sku)), ("reservations", json!(released))])).await;
    }
    Ok(ok(json!({"sku": sku, "released": released})))
}

// ── Reads ────────────────────────────────────────────────────────────────────

#[derive(serde::Deserialize, Default)]
pub struct SkuQuery {
    sku: Option<String>,
}

pub async fn reservations(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    Query(q): Query<SkuQuery>,
) -> Result<Json<Value>, ApiError> {
    let user = reader(&state, &actors, &headers).await?;
    let sku = q.sku.as_deref().map(py_strip).filter(|s| !s.is_empty()).map(|s| take_chars(s, 200));
    let rows = load_reservations(&state.pool, &user.tenant_id, sku.as_deref()).await?;
    let stale = rows.iter().filter(|r| r.stale_reason.is_some()).count();
    let items: Vec<Value> = rows
        .iter()
        .map(|r| json!({
            "id": r.id,
            "run_id": r.run_id,
            "sku": r.sku,
            "commitment_id": r.commitment_id,
            "customer": r.customer,
            "tier": r.tier,
            "delivery_date": isoformat_date(&r.delivery_date),
            "units": engine::units(r.units_micro),
            "reserved": engine::units(r.reserved_micro),
            "short": engine::units(r.short_micro),
            "created_by": r.created_by,
            "created_at": isoformat_utc(&r.created_at),
            "stale": r.stale_reason.is_some(),
            "stale_reason": r.stale_reason,
        }))
        .collect();
    Ok(ok(json!({"items": items, "stale": stale, "advisory": true})))
}

pub async fn overview(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = reader(&state, &actors, &headers).await?;
    let day = today();
    let policy = load_policy(&state.pool, &user.tenant_id).await?;
    let commitments = supply::open_commitments(&state.pool, &user.tenant_id, None).await?;
    let mut by_sku: BTreeMap<String, Vec<OpenCommitment>> = BTreeMap::new();
    for c in commitments {
        by_sku.entry(c.sku.clone()).or_default().push(c);
    }
    let skus: Vec<String> = by_sku.keys().cloned().collect();
    let supply = supply::load_supply(&state.pool, &user.tenant_id, &skus, day).await?;
    let reserved: HashSet<String> = sqlx::query("SELECT DISTINCT sku FROM stock_reservations WHERE tenant_id = $1 AND status = 'active'")
        .bind(&user.tenant_id)
        .fetch_all(&state.pool)
        .await?
        .iter()
        .filter_map(|r| r.try_get::<String, _>("sku").ok())
        .collect();

    let mut items: Vec<(i64, Value)> = Vec::new();
    let mut unknown: Vec<Value> = Vec::new();
    let mut too_many: Vec<Value> = Vec::new();
    for (sku, rows) in &by_sku {
        if rows.len() > MAX_CLAIMS_PER_SKU {
            too_many.push(json!({"sku": sku, "open": rows.len()}));
            continue;
        }
        let s = supply.get(sku).cloned().unwrap_or_default();
        let r = compute_sku(sku, rows, &s, &[], &policy, day);
        if r.status != "ok" {
            unknown.push(json!({"sku": sku, "commitments": rows.len(), "demand": engine::units(r.demand_micro())}));
            continue;
        }
        if !r.contested() {
            continue;
        }
        let customers_short = r.lines.iter().filter(|l| l.short_micro() > 0)
            .map(|l| l.customer.as_deref().map(customer_key).unwrap_or_default()).collect::<HashSet<_>>().len();
        items.push((r.short_micro(), json!({
            "sku": sku,
            "commitments": r.lines.len(),
            "commitments_short": r.lines.iter().filter(|l| l.short_micro() > 0).count(),
            "customers_short": customers_short,
            "demand": engine::units(r.demand_micro()),
            "allocated": engine::units(r.allocated_micro()),
            "short": engine::units(r.short_micro()),
            "has_reservations": reserved.contains(sku),
        })));
    }
    items.sort_by(|a, b| b.0.cmp(&a.0));
    Ok(ok(json!({
        "as_of": isoformat_date(&day),
        "advisory": true,
        "skus_with_commitments": by_sku.len(),
        "contested": items.into_iter().map(|(_, v)| v).collect::<Vec<_>>(),
        "stock_unknown": unknown,
        "too_many_commitments": too_many,
        "fair_share_tiers": policy.fair_tiers,
    })))
}
