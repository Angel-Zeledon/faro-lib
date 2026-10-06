//! Commitment fulfillment outlook: three read routes that answer, for each
//! open committed-demand row, "will it be met on its delivery date?".
//!
//! * `GET /committed-demand/outlook`                 the list, with filters
//! * `GET /committed-demand/outlook/summary`         the tenant roll-up
//! * `GET /committed-demand/{commitment_id}/outlook` one commitment in detail
//!
//! NEW routes: there is no Python implementation to fail over to, so the
//! gateway file for them (`deploy/rust-api/routes.d/commitment-outlook.caddy.example`)
//! has no `api` upstream. The arithmetic is `crate::fulfillment::core` (tested
//! against a plain-Python reference); the reads are `crate::fulfillment::data`.
//!
//! Not a second authority on risk: `GET /committed-demand` (Python) keeps its
//! at-risk flag, which assumes an open purchase order lands within the lead
//! time. This outlook uses the order's real expected date (a supplier promise a
//! person accepted, or generated + the supplier's lead time) and says
//! `insufficient_data` where that date does not exist. The units and the
//! earliest-first allocation are the same, which a test pins.
//!
//! Warehouse scope: a caller limited to some warehouses sees only commitments
//! naming one of theirs, judged against THEIR warehouses' stock and arrivals
//! (`scope` says which), exactly like the committed-demand list.
//!
//! The tag is internal, like the rest of committed demand: no API key.

use std::collections::BTreeMap;

use axum::extract::{Path, RawQuery, State};
use axum::http::HeaderMap;
use axum::routing::get;
use axum::{Extension, Json, Router};
use chrono::{Local, NaiveDate};
use serde_json::{json, Map, Value};

use crate::auth::{self, warehouse_scope as wscope, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::fulfillment::core::{self, Outcome, SummaryRow, Verdict};
use crate::fulfillment::data::{self, day_number, day_to_date, CommitmentRow, Loaded, SkuReport};
use crate::pycompat::{isoformat_date, py_strip};
use crate::query::{self, PyInt, Query};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::Errors;

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'committed-demand': a commitment is a customer order a person entered; it moves purchase decisions, so it is recorded under a person's name",
    ),
    is_mcp: false,
};

const MAX_LIMIT: i64 = 2000;
const DEFAULT_LIMIT: i64 = 500;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/committed-demand/outlook", get(list))
        .route("/api/v1/committed-demand/outlook/summary", get(summary))
        .route("/api/v1/committed-demand/{commitment_id}/outlook", get(detail))
}

// ── JSON helpers ─────────────────────────────────────────────────────────────

fn r2(x: f64) -> Value {
    if !x.is_finite() {
        return Value::Null;
    }
    json!((x * 100.0).round() / 100.0)
}

fn or2(x: Option<f64>) -> Value {
    x.map(r2).unwrap_or(Value::Null)
}

fn day_json(d: Option<i64>) -> Value {
    d.and_then(day_to_date).map(|d| Value::String(isoformat_date(&d))).unwrap_or(Value::Null)
}

fn opt_str_json(s: &Option<String>) -> Value {
    s.as_ref().map(|v| Value::String(v.clone())).unwrap_or(Value::Null)
}

fn severity(v: Verdict) -> u8 {
    match v {
        Verdict::WillMiss => 0,
        Verdict::AtRisk => 1,
        Verdict::InsufficientData => 2,
        Verdict::OnTrack => 3,
    }
}

fn summary_row(l: &Loaded, row_index: usize) -> Option<SummaryRow> {
    let (report, pos) = l.outcome_of(row_index)?;
    let o = &report.outcomes[pos];
    Some(SummaryRow {
        verdict: o.verdict,
        units: o.units,
        shortfall_units: o.shortfall_units,
        shortfall_is_minimum: o.shortfall_is_minimum,
        delivery_day: day_number(l.rows[row_index].delivery_date),
    })
}

fn summary_json(rows: &[SummaryRow]) -> Value {
    let s = core::summarize(rows);
    json!({
        "total": s.total,
        "on_track": s.on_track,
        "at_risk": s.at_risk,
        "will_miss": s.will_miss,
        "insufficient_data": s.insufficient_data,
        "units": r2(s.units),
        "shortfall_units": r2(s.shortfall_units),
        // True when some shortfall in the sum is a lower bound (undated units
        // were counted as arriving), so the total is "at least".
        "shortfall_has_minimum": s.shortfall_has_minimum,
        "first_problem_date": day_json(s.first_problem_day),
    })
}

/// The numbers behind a verdict, for the frontend to put in a sentence (the
/// backend returns the code and the figures, never Spanish prose).
fn reason_json(l: &Loaded, row: &CommitmentRow, report: &SkuReport, o: &Outcome) -> Value {
    let mut p = Map::new();
    p.insert("delivery_date".into(), Value::String(isoformat_date(&row.delivery_date)));
    p.insert("units".into(), r2(o.units));
    p.insert("earlier_units".into(), r2(o.cumulative_units - o.units));
    p.insert("cumulative_units".into(), r2(o.cumulative_units));
    p.insert("stock".into(), or2(report.stock));
    p.insert("supply_units".into(), or2(o.supply_units));
    p.insert("shortfall_units".into(), or2(o.shortfall_units));
    p.insert("shortfall_worst_case".into(), or2(o.shortfall_worst_case));
    p.insert("undated_units".into(), r2(o.undated_units));
    p.insert("lead_time_days".into(), report.lead.map(|(d, _)| json!(d)).unwrap_or(Value::Null));
    p.insert("lead_time_source".into(), report.lead.map(|(_, s)| json!(s.as_str())).unwrap_or(Value::Null));
    p.insert("latest_safe_order_date".into(), day_json(o.latest_safe_order_day));
    p.insert("cover_date".into(), day_json(o.cover_day));
    p.insert("cover_source".into(), o.cover_source.map(|s| json!(s.as_str())).unwrap_or(Value::Null));
    p.insert("late_days".into(), o.late_days.map(|d| json!(d)).unwrap_or(Value::Null));
    p.insert("slack_days".into(), o.slack_days.map(|d| json!(d)).unwrap_or(Value::Null));
    let driver = o.driver_index.and_then(|i| report.meta.get(i));
    p.insert("driver_reference".into(), driver.map(|m| json!(m.reference)).unwrap_or(Value::Null));
    p.insert("driver_supplier".into(), driver.and_then(|m| m.supplier.clone()).map(Value::String).unwrap_or(Value::Null));
    p.insert("driver_date".into(), day_json(o.driver_index.and_then(|i| report.arrivals.get(i)).and_then(|a| a.day)));
    p.insert("driver_source".into(), driver.map(|m| json!(m.source)).unwrap_or(Value::Null));
    let undated: Vec<Value> = report
        .arrivals
        .iter()
        .zip(&report.meta)
        .filter(|(a, _)| a.day.is_none())
        .take(5)
        .map(|(a, m)| json!({"reference": m.reference, "supplier": m.supplier, "qty": r2(a.qty), "why": m.source}))
        .collect();
    p.insert("undated_orders".into(), Value::Array(undated));
    let _ = l;
    json!({"code": o.reason.as_str(), "params": Value::Object(p)})
}

fn item_json(l: &Loaded, row_index: usize) -> Option<Value> {
    let row = &l.rows[row_index];
    let (report, pos) = l.outcome_of(row_index)?;
    let o = &report.outcomes[pos];
    Some(json!({
        "id": row.id,
        "sku": row.sku,
        "warehouse_id": opt_str_json(&row.warehouse_id),
        "delivery_date": isoformat_date(&row.delivery_date),
        "overdue": row.delivery_date < l.today,
        "quantity": r2(row.quantity),
        "probability": r2(row.probability),
        "customer": opt_str_json(&row.customer),
        "note": opt_str_json(&row.note),
        "source": row.source,
        "on_top_of_base": row.on_top_of_base,
        "contract_root_id": opt_str_json(&row.contract_root_id),
        "contract_reference": opt_str_json(&row.contract_reference),
        "verdict": o.verdict.as_str(),
        "reason": reason_json(l, row, report, o),
        "expected_units": r2(o.units),
        "cumulative_units": r2(o.cumulative_units),
        "supply_units": or2(o.supply_units),
        "shortfall_units": or2(o.shortfall_units),
        "shortfall_is_minimum": o.shortfall_is_minimum,
        "shortfall_worst_case": or2(o.shortfall_worst_case),
        "undated_units": r2(o.undated_units),
        "cover_date": day_json(o.cover_day),
        "cover_source": o.cover_source.map(|s| json!(s.as_str())).unwrap_or(Value::Null),
        "slack_days": o.slack_days.map(|d| json!(d)).unwrap_or(Value::Null),
        "late_days": o.late_days.map(|d| json!(d)).unwrap_or(Value::Null),
        "latest_safe_order_date": day_json(o.latest_safe_order_day),
        "order_date_passed": o.order_date_passed.map(Value::Bool).unwrap_or(Value::Null),
        "stock": or2(report.stock),
        "lead_time_days": report.lead.map(|(d, _)| json!(d)).unwrap_or(Value::Null),
        "lead_time_source": report.lead.map(|(_, s)| json!(s.as_str())).unwrap_or(Value::Null),
    }))
}

// ── Loading ──────────────────────────────────────────────────────────────────

async fn load_for(state: &AppState, user: &auth::CurrentUser) -> Result<Loaded, ApiError> {
    let ids = wscope::scope_warehouse_ids(&state.pool, user).await?;
    let names = wscope::scope_names(&state.pool, user).await?;
    let today: NaiveDate = Local::now().date_naive();
    data::load(&state.pool, &user.tenant_id, &names, &ids, today).await
}

fn verdict_ok(s: &str) -> bool {
    Verdict::parse(s).is_some()
}

// ── GET /committed-demand/outlook ────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let q = Query::parse(raw.as_deref());
    let mut errs = Errors::default();
    let sku = query::opt_str(&mut errs, &q, "sku", Some(200));
    let customer = query::opt_str(&mut errs, &q, "customer", Some(200));
    let verdict = query::opt_pattern(
        &mut errs, &q, "verdict", "^(on_track|at_risk|will_miss|insufficient_data)$", verdict_ok);
    let contract = query::opt_str(&mut errs, &q, "contract_root_id", Some(200));
    let warehouse = query::opt_str(&mut errs, &q, "warehouse_id", Some(64));
    let from = query::opt_date(&mut errs, &q, "delivery_from");
    let to = query::opt_date(&mut errs, &q, "delivery_to");
    let limit = query::int_param(&mut errs, &q, "limit", DEFAULT_LIMIT, Some(1), Some(MAX_LIMIT));
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    errs.into_result()?;
    let Some(PyInt::Small(limit)) = limit else { return Err(ApiError::internal()) };

    let l = load_for(&state, &user).await?;
    let want_customer = customer.as_deref().map(|c| wscope::py_casefold(py_strip(c)));
    let want_verdict = verdict.as_deref().and_then(Verdict::parse);

    let mut picked: Vec<usize> = Vec::new();
    for (i, row) in l.rows.iter().enumerate() {
        if sku.as_deref().is_some_and(|s| row.sku != s)
            || warehouse.as_deref().is_some_and(|w| row.warehouse_id.as_deref() != Some(w))
            || contract.as_deref().is_some_and(|c| row.contract_root_id.as_deref() != Some(c))
            || from.is_some_and(|d| row.delivery_date < d)
            || to.is_some_and(|d| row.delivery_date > d)
        {
            continue;
        }
        if let Some(want) = &want_customer {
            let have = row.customer.as_deref().map(|c| wscope::py_casefold(py_strip(c)));
            if have.as_deref() != Some(want.as_str()) {
                continue;
            }
        }
        let Some((report, pos)) = l.outcome_of(i) else { continue };
        if want_verdict.is_some_and(|v| report.outcomes[pos].verdict != v) {
            continue;
        }
        picked.push(i);
    }
    picked.sort_by(|&a, &b| {
        let va = l.outcome_of(a).map(|(r, p)| severity(r.outcomes[p].verdict)).unwrap_or(9);
        let vb = l.outcome_of(b).map(|(r, p)| severity(r.outcomes[p].verdict)).unwrap_or(9);
        va.cmp(&vb)
            .then(l.rows[a].delivery_date.cmp(&l.rows[b].delivery_date))
            .then(l.rows[a].sku.cmp(&l.rows[b].sku))
            .then(l.rows[a].id.cmp(&l.rows[b].id))
    });
    let rows: Vec<SummaryRow> = picked.iter().filter_map(|&i| summary_row(&l, i)).collect();
    let total = picked.len();
    let items: Vec<Value> = picked.iter().take(limit as usize).filter_map(|&i| item_json(&l, i)).collect();
    Ok(ok(json!({
        "as_of": isoformat_date(&l.today),
        "scope": l.scope_label,
        "total": total,
        "limit": limit,
        "items": items,
        "summary": summary_json(&rows),
    })))
}

// ── GET /committed-demand/outlook/summary ────────────────────────────────────

pub async fn summary(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let l = load_for(&state, &user).await?;

    let mut all: Vec<SummaryRow> = Vec::new();
    let mut by_customer: BTreeMap<Option<String>, Vec<SummaryRow>> = BTreeMap::new();
    let mut by_contract: BTreeMap<String, (Option<String>, Option<String>, Vec<SummaryRow>)> = BTreeMap::new();
    let mut gaps: BTreeMap<&'static str, (i64, f64)> = BTreeMap::new();
    let mut gap_skus: BTreeMap<&'static str, Vec<String>> = BTreeMap::new();
    for (i, row) in l.rows.iter().enumerate() {
        let Some(sr) = summary_row(&l, i) else { continue };
        let (report, pos) = l.outcome_of(i).expect("row has an outcome");
        let o = &report.outcomes[pos];
        if o.verdict == Verdict::InsufficientData {
            let e = gaps.entry(o.reason.as_str()).or_insert((0, 0.0));
            e.0 += 1;
            e.1 += o.units;
            let skus = gap_skus.entry(o.reason.as_str()).or_default();
            if !skus.contains(&row.sku) && skus.len() < 10 {
                skus.push(row.sku.clone());
            }
        }
        by_customer.entry(row.customer.clone()).or_default().push(sr.clone());
        if let Some(root) = &row.contract_root_id {
            let e = by_contract
                .entry(root.clone())
                .or_insert_with(|| (row.contract_reference.clone(), row.contract_customer.clone(), Vec::new()));
            e.2.push(sr.clone());
        }
        all.push(sr);
    }

    let problems = |rows: &[SummaryRow]| {
        rows.iter().filter(|r| matches!(r.verdict, Verdict::AtRisk | Verdict::WillMiss)).count()
    };
    let mut customers: Vec<(&Option<String>, &Vec<SummaryRow>)> = by_customer.iter().collect();
    customers.sort_by(|a, b| {
        problems(b.1).cmp(&problems(a.1))
            .then(core::summarize(b.1).shortfall_units.total_cmp(&core::summarize(a.1).shortfall_units))
            .then(a.0.cmp(b.0))
    });
    let by_customer_json: Vec<Value> = customers
        .iter()
        .map(|(c, rows)| json!({"customer": opt_str_json(c), "summary": summary_json(rows)}))
        .collect();
    let by_contract_json: Vec<Value> = by_contract
        .iter()
        .map(|(root, (reference, customer, rows))| {
            json!({
                "contract_root_id": root,
                "reference": opt_str_json(reference),
                "customer": opt_str_json(customer),
                "summary": summary_json(rows),
            })
        })
        .collect();
    let gaps_json: Vec<Value> = gaps
        .iter()
        .map(|(code, (n, units))| json!({"reason": code, "commitments": n, "units": r2(*units), "skus": gap_skus.get(code)}))
        .collect();
    Ok(ok(json!({
        "as_of": isoformat_date(&l.today),
        "scope": l.scope_label,
        "summary": summary_json(&all),
        "by_customer": by_customer_json,
        "by_contract": by_contract_json,
        // What to fill in so an "insufficient data" can become an answer.
        "data_gaps": gaps_json,
    })))
}

// ── GET /committed-demand/{commitment_id}/outlook ────────────────────────────

pub async fn detail(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(commitment_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let ids = wscope::scope_warehouse_ids(&state.pool, &user).await?;

    // The same 404 a missing commitment gets, also for one in a warehouse the
    // caller cannot see (they are not told it exists).
    let found: Option<(String, Option<String>)> =
        sqlx::query_as("SELECT status, warehouse_id FROM committed_demand WHERE id = $1 AND tenant_id = $2")
            .bind(&commitment_id)
            .bind(&user.tenant_id)
            .fetch_optional(&state.pool)
            .await?;
    let not_found = || ApiError::app("committed_demand_not_found", "Commitment not found", 404, json!({}));
    let Some((status, warehouse_id)) = found else { return Err(not_found()) };
    if let Some(allowed) = &ids {
        if !warehouse_id.as_ref().is_some_and(|w| allowed.contains(w)) {
            return Err(not_found());
        }
    }
    if status != "open" {
        return Err(ApiError::app(
            "commitment_outlook_not_open",
            "Only an open commitment has an outlook",
            409,
            json!({"status": status}),
        ));
    }

    let l = load_for(&state, &user).await?;
    let Some(row_index) = l.rows.iter().position(|r| r.id == commitment_id) else { return Err(not_found()) };
    let (report, pos) = l.outcome_of(row_index).ok_or_else(ApiError::internal)?;
    let this = &report.outcomes[pos];
    let delivery = day_number(l.rows[row_index].delivery_date);
    let as_of = delivery.max(day_number(l.today));

    // The commitments served up to and including this one compete for the stock.
    let competing: Vec<Value> = report
        .served
        .iter()
        .zip(&report.outcomes)
        .take(pos + 1)
        .map(|(&ri, o)| {
            let r = &l.rows[ri];
            json!({
                "id": r.id,
                "customer": opt_str_json(&r.customer),
                "delivery_date": isoformat_date(&r.delivery_date),
                "units": r2(o.units),
                "cumulative_units": r2(o.cumulative_units),
                "is_this": ri == row_index,
            })
        })
        .collect();
    let arrivals: Vec<Value> = report
        .arrivals
        .iter()
        .zip(&report.meta)
        .map(|(a, m)| {
            json!({
                "kind": m.kind,
                "reference": m.reference,
                "supplier": opt_str_json(&m.supplier),
                "warehouse": m.warehouse,
                "qty": r2(m.qty),
                "date": day_json(a.day),
                "expected_date": day_json(m.expected_day),
                "source": m.source,
                "source_id": m.source_id,
                // Whether the arrival counts toward THIS delivery date.
                "counted": a.day.is_some_and(|d| d <= as_of),
            })
        })
        .collect();
    let stock_rows: Vec<Value> = report
        .stock_rows
        .iter()
        .map(|s| json!({"warehouse": s.warehouse, "current_stock": r2(s.current_stock)}))
        .collect();
    let item = item_json(&l, row_index).ok_or_else(ApiError::internal)?;
    Ok(ok(json!({
        "as_of": isoformat_date(&l.today),
        "scope": l.scope_label,
        "commitment": item,
        "competing": competing,
        "later_commitments": report.served.len() - pos - 1,
        "supply": {
            "stock": or2(report.stock),
            "stock_by_warehouse": stock_rows,
            "arrivals": arrivals,
            "supplier": opt_str_json(&report.supplier),
            "lead_time_days": report.lead.map(|(d, _)| json!(d)).unwrap_or(Value::Null),
            "lead_time_source": report.lead.map(|(_, s)| json!(s.as_str())).unwrap_or(Value::Null),
        },
        "supply_units_at_delivery": or2(this.supply_units),
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounds_to_two_decimals_and_never_prints_nan() {
        assert_eq!(r2(1.234), json!(1.23));
        assert_eq!(r2(2.5), json!(2.5));
        assert_eq!(r2(f64::NAN), Value::Null);
        assert_eq!(or2(None), Value::Null);
    }

    #[test]
    fn severity_puts_misses_first_and_on_track_last() {
        assert!(severity(Verdict::WillMiss) < severity(Verdict::AtRisk));
        assert!(severity(Verdict::AtRisk) < severity(Verdict::InsufficientData));
        assert!(severity(Verdict::InsufficientData) < severity(Verdict::OnTrack));
    }

    #[test]
    fn a_day_number_round_trips_to_its_date() {
        let d = NaiveDate::from_ymd_opt(2026, 10, 6).unwrap();
        assert_eq!(day_to_date(day_number(d)), Some(d));
        assert_eq!(day_json(Some(day_number(d))), json!("2026-10-06"));
        assert_eq!(day_json(None), Value::Null);
    }
}
