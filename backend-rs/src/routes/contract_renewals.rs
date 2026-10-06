//! Contract renewal and expiry tracking for blanket supply contracts
//! (`backend/inventory/contract_renewal.py` is the reference of the maths,
//! `backend/inventory/supply_contract_service.py` owns the contracts).
//!
//! New routes, Rust only (no Python implementation, so no failover):
//!
//! * `GET  /supply-contracts/renewals`            the renewals list: active
//!   contracts with their end date, notice deadline, bucket (overdue, notice
//!   passed, due soon, upcoming), auto-renew flag and a committed-vs-delivered
//!   summary.
//! * `GET  /supply-contracts/{root_id}/comparison` committed units against
//!   delivered units over the term: fill rate, late deliveries, overdue.
//! * `POST /supply-contracts/{root_id}/renew`      creates the next term as a
//!   NEW DRAFT contract (see the module doc of `contract_renewal.py` for why it
//!   is a new lineage and not a new revision). A draft materialises nothing:
//!   the purchase maths does not move until a person activates it through the
//!   existing Python status route.
//!
//! Warehouse scope is the contract service's (`visible` / `require_writable`),
//! ported line for line: a scoped caller sees and renews only contracts that
//! name one of their warehouses; a company-wide contract is for company-wide
//! callers only.

use axum::extract::{Path, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::body::Bytes;
use axum::{Extension, Json};
use chrono::{NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::{record_event, Event};
use crate::auth::warehouse_scope::{py_casefold, Scope};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::contract_renewal as cr;
use crate::error::ApiError;
use crate::routes::ok;
use crate::routes::r1::query_params::{sql_int, QueryParams};
use crate::state::AppState;
use crate::validation::{self, Errors};

/// `INTERNAL_TAGS["supply-contracts"]`: a contract moves purchase decisions and
/// is recorded under a person's name.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'supply-contracts': blanket contracts are managed from the app"),
    is_mcp: false,
};

pub const BUCKETS: [&str; 4] = ["expired", "notice_passed", "due_soon", "upcoming"];

fn bucket_pattern_ok(s: &str) -> bool {
    BUCKETS.contains(&s)
}

// ---- warehouse scope (supply_contract_service.visible / require_writable) ----

pub fn visible(scope: &Scope, warehouse_name: Option<&str>) -> bool {
    let Some(names) = scope else { return true };
    let name = warehouse_name.unwrap_or("").trim();
    if name.is_empty() {
        return false;
    }
    let target = py_casefold(name);
    names.iter().any(|n| py_casefold(n.trim()) == target)
}

pub fn require_writable(scope: &Scope, warehouse_name: Option<&str>) -> Result<(), ApiError> {
    if scope.is_none() {
        return Ok(());
    }
    let name = warehouse_name.unwrap_or("");
    if name.trim().is_empty() {
        return Err(ApiError::app(
            "supply_contract_scope_company_wide",
            "A contract for every warehouse is only for users who see every warehouse",
            403,
            json!({}),
        ));
    }
    if !visible(scope, warehouse_name) {
        return Err(ApiError::app(
            "warehouse_out_of_scope",
            "This user is not allowed to work with that warehouse.",
            403,
            json!({"warehouse": name}),
        ));
    }
    Ok(())
}

fn not_found() -> ApiError {
    ApiError::app("supply_contract_not_found", "Contract not found", 404, json!({}))
}

// ---- reading a contract row ------------------------------------------------

#[derive(Debug, Clone)]
struct Contract {
    id: String,
    root_id: String,
    revision: i32,
    customer: String,
    reference: Option<String>,
    lines: Value,
    period_start: NaiveDate,
    period_end: NaiveDate,
    schedule_kind: String,
    releases: Option<Value>,
    tolerance_pct: f64,
    status: String,
    warehouse_id: Option<String>,
    warehouse_name: Option<String>,
    on_top_of_base: bool,
    note: Option<String>,
    notice_days: Option<i32>,
    auto_renew: bool,
    renewal_lead_days: Option<Vec<i32>>,
}

const COLS: &str = "s.id, s.root_id, s.revision, s.customer, s.reference, s.lines, s.period_start,
    s.period_end, s.schedule_kind, s.releases, s.tolerance_pct, s.status, s.warehouse_id,
    w.name AS warehouse_name, s.on_top_of_base, s.note, s.notice_days, s.auto_renew,
    s.renewal_lead_days";

const FROM: &str = "FROM supply_contracts s
    LEFT JOIN warehouses w ON w.id = s.warehouse_id AND w.tenant_id = s.tenant_id";

fn contract_of(row: &sqlx::postgres::PgRow) -> Result<Contract, sqlx::Error> {
    Ok(Contract {
        id: row.try_get("id")?,
        root_id: row.try_get("root_id")?,
        revision: row.try_get("revision")?,
        customer: row.try_get("customer")?,
        reference: row.try_get("reference")?,
        lines: row.try_get("lines")?,
        period_start: row.try_get("period_start")?,
        period_end: row.try_get("period_end")?,
        schedule_kind: row.try_get("schedule_kind")?,
        releases: row.try_get("releases")?,
        tolerance_pct: row.try_get("tolerance_pct")?,
        status: row.try_get("status")?,
        warehouse_id: row.try_get("warehouse_id")?,
        warehouse_name: row.try_get("warehouse_name")?,
        on_top_of_base: row.try_get("on_top_of_base")?,
        note: row.try_get("note")?,
        notice_days: row.try_get("notice_days")?,
        auto_renew: row.try_get("auto_renew")?,
        renewal_lead_days: row.try_get("renewal_lead_days")?,
    })
}

fn parse_date(v: &Value) -> Result<NaiveDate, ApiError> {
    v.as_str()
        .and_then(|s| NaiveDate::parse_from_str(s.get(..10).unwrap_or(s), "%Y-%m-%d").ok())
        .ok_or_else(ApiError::internal)
}

impl Contract {
    fn lines(&self) -> Result<Vec<cr::Line>, ApiError> {
        let arr = self.lines.as_array().ok_or_else(ApiError::internal)?;
        arr.iter()
            .map(|l| {
                Ok(cr::Line {
                    sku: l["sku"].as_str().ok_or_else(ApiError::internal)?.to_string(),
                    total_quantity: l["total_quantity"].as_f64().ok_or_else(ApiError::internal)?,
                })
            })
            .collect()
    }

    fn explicit(&self) -> Result<Option<Vec<cr::Release>>, ApiError> {
        let Some(v) = &self.releases else { return Ok(None) };
        let arr = v.as_array().ok_or_else(ApiError::internal)?;
        arr.iter()
            .map(|r| {
                Ok(cr::Release {
                    sku: r["sku"].as_str().ok_or_else(ApiError::internal)?.to_string(),
                    date: parse_date(&r["date"])?,
                    quantity: r["quantity"].as_f64().ok_or_else(ApiError::internal)?,
                })
            })
            .collect::<Result<Vec<_>, ApiError>>()
            .map(Some)
    }

    fn releases_expanded(&self) -> Result<Vec<cr::Release>, ApiError> {
        let explicit = self.explicit()?;
        Ok(cr::expand_releases(&self.schedule_kind, self.period_start, self.period_end, &self.lines()?,
            explicit.as_deref()))
    }

    fn stored_leads(&self) -> Option<Vec<i64>> {
        self.renewal_lead_days.as_ref().map(|v| v.iter().map(|x| i64::from(*x)).collect())
    }

    fn leads(&self) -> Vec<i64> {
        cr::effective_lead_days(self.stored_leads().as_deref())
    }
}

/// The lineage's LIVE commitments, per root id.
async fn lineage_commitments(
    pool: &PgPool,
    tenant_id: &str,
    roots: &[String],
) -> Result<std::collections::HashMap<String, Vec<cr::Commitment>>, ApiError> {
    let rows = sqlx::query(
        "SELECT contract_root_id, sku, contract_release_date, quantity::float8 AS quantity, status,
                (status_changed_at AT TIME ZONE 'UTC')::date AS fulfilled_on
           FROM committed_demand
          WHERE tenant_id = $1 AND contract_root_id = ANY($2) AND contract_withdrawn_at IS NULL
          ORDER BY contract_root_id, sku, contract_release_date, id",
    )
    .bind(tenant_id)
    .bind(roots)
    .fetch_all(pool)
    .await?;
    let mut out: std::collections::HashMap<String, Vec<cr::Commitment>> = std::collections::HashMap::new();
    for r in rows {
        let status: String = r.try_get("status")?;
        let fulfilled_on: Option<NaiveDate> = r.try_get("fulfilled_on")?;
        out.entry(r.try_get("contract_root_id")?).or_default().push(cr::Commitment {
            sku: r.try_get("sku")?,
            release_date: r.try_get("contract_release_date")?,
            quantity: r.try_get("quantity")?,
            // The fulfilment time only means something for a fulfilled row.
            fulfilled_on: if status == "fulfilled" { fulfilled_on } else { None },
            status,
        });
    }
    Ok(out)
}

/// For each root, the root of the NON-cancelled contract that renews it.
async fn renewed_to(
    pool: &PgPool,
    tenant_id: &str,
    roots: &[String],
) -> Result<std::collections::HashMap<String, String>, ApiError> {
    let rows = sqlx::query(
        "SELECT renewed_from_root_id, root_id FROM supply_contracts
          WHERE tenant_id = $1 AND renewed_from_root_id = ANY($2)
            AND superseded_by IS NULL AND status <> 'cancelled'
          ORDER BY created_at",
    )
    .bind(tenant_id)
    .bind(roots)
    .fetch_all(pool)
    .await?;
    let mut out = std::collections::HashMap::new();
    for r in rows {
        let from: String = r.try_get("renewed_from_root_id")?;
        out.entry(from).or_insert(r.try_get::<String, _>("root_id")?);
    }
    Ok(out)
}

fn today() -> NaiveDate {
    Utc::now().date_naive()
}

fn summary(cmp: &Value) -> Value {
    let pick = |k: &str| cmp.get(k).cloned().unwrap_or(Value::Null);
    json!({
        "committed_units": pick("committed_units"),
        "due_to_date": pick("due_to_date"),
        "delivered_units": pick("delivered_units"),
        "shortfall_to_date": pick("shortfall_to_date"),
        "fill_rate_pct": pick("fill_rate_pct"),
        "term_fill_pct": pick("term_fill_pct"),
        "late_deliveries": pick("late_deliveries"),
        "overdue_open": pick("overdue_open"),
        "fulfilled_undated": pick("fulfilled_undated"),
    })
}

// ---- GET /supply-contracts/renewals ------------------------------------------

pub async fn renewals(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    RawQuery(raw): RawQuery,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let params = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let within = params.int(&mut errs, "within_days", cr::DEFAULT_WITHIN_DAYS, Some(1), Some(cr::MAX_WITHIN_DAYS));
    let bucket = params.opt_str(&mut errs, "bucket",
        Some(("^(expired|notice_passed|due_soon|upcoming)$", bucket_pattern_ok)));
    errs.into_result()?;
    let within = sql_int(within)?;
    let pool = &state.pool;
    let scope = crate::auth::warehouse_scope::scope_names(pool, &user).await?;
    let today = today();

    let rows = sqlx::query(&format!(
        "SELECT {COLS} {FROM}
          WHERE s.tenant_id = $1 AND s.superseded_by IS NULL AND s.status = 'active'
          ORDER BY s.period_end, s.customer, s.root_id"
    ))
    .bind(&user.tenant_id)
    .fetch_all(pool)
    .await?;
    let mut contracts = Vec::new();
    for r in &rows {
        let c = contract_of(r)?;
        if visible(&scope, c.warehouse_name.as_deref()) {
            contracts.push(c);
        }
    }
    let roots: Vec<String> = contracts.iter().map(|c| c.root_id.clone()).collect();
    let commitments = lineage_commitments(pool, &user.tenant_id, &roots).await?;
    let renewed = renewed_to(pool, &user.tenant_id, &roots).await?;

    let (mut hidden_renewed, mut later, mut filtered_out) = (0i64, 0i64, 0i64);
    let mut items = Vec::new();
    for c in &contracts {
        // Renewed contracts are done; they are counted, never silently dropped.
        if renewed.contains_key(&c.root_id) {
            hidden_renewed += 1;
            continue;
        }
        let leads = c.leads();
        let view = cr::renewal_view(c.period_end, c.notice_days.map(i64::from), c.auto_renew, &leads, today);
        if view["days_to_expiry"].as_i64().unwrap_or(0) > within {
            later += 1;
            continue;
        }
        if bucket.as_deref().is_some_and(|b| view["bucket"] != b) {
            filtered_out += 1;
            continue;
        }
        let empty = Vec::new();
        let cmp = cr::commitment_comparison(
            &c.releases_expanded()?,
            commitments.get(&c.root_id).unwrap_or(&empty),
            today,
        );
        items.push(json!({
            "root_id": c.root_id,
            "revision": c.revision,
            "customer": c.customer,
            "reference": c.reference,
            "status": c.status,
            "warehouse_id": c.warehouse_id,
            "warehouse_name": c.warehouse_name,
            "period_start": c.period_start.to_string(),
            "period_end": c.period_end.to_string(),
            "renewal": view,
            "renewal_lead_days": c.stored_leads(),
            "renewal_lead_days_effective": leads,
            "renewal_lead_days_is_default": c.renewal_lead_days.is_none(),
            "comparison": summary(&cmp),
        }));
    }
    Ok(ok(json!({
        "today": today.to_string(),
        "within_days": within,
        "default_lead_days": cr::DEFAULT_LEAD_DAYS,
        "items": items,
        "later_count": later,
        "hidden_renewed": hidden_renewed,
        "filtered_out_by_bucket": filtered_out,
    })))
}

// ---- GET /supply-contracts/{root_id}/comparison -------------------------------

async fn current_contract(
    pool: &PgPool,
    tenant_id: &str,
    root_id: &str,
    lock: bool,
) -> Result<Contract, ApiError> {
    let sql = format!(
        "SELECT {COLS} {FROM} WHERE s.tenant_id = $1 AND s.root_id = $2 AND s.superseded_by IS NULL{}",
        if lock { " FOR UPDATE OF s" } else { "" }
    );
    let row = sqlx::query(&sql).bind(tenant_id).bind(root_id).fetch_optional(pool).await?;
    match row {
        Some(r) => Ok(contract_of(&r)?),
        None => Err(not_found()),
    }
}

pub async fn comparison(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(root_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let pool = &state.pool;
    let c = current_contract(pool, &user.tenant_id, &root_id, false).await?;
    let scope = crate::auth::warehouse_scope::scope_names(pool, &user).await?;
    if !visible(&scope, c.warehouse_name.as_deref()) {
        require_writable(&scope, c.warehouse_name.as_deref())?; // raises the right 403
    }
    let today = today();
    let roots = vec![c.root_id.clone()];
    let commitments = lineage_commitments(pool, &user.tenant_id, &roots).await?;
    let empty = Vec::new();
    let cmp = cr::commitment_comparison(&c.releases_expanded()?, commitments.get(&c.root_id).unwrap_or(&empty), today);
    let leads = c.leads();
    let renewal = if c.status == "active" {
        cr::renewal_view(c.period_end, c.notice_days.map(i64::from), c.auto_renew, &leads, today)
    } else {
        Value::Null
    };
    let renewed = renewed_to(pool, &user.tenant_id, &roots).await?;
    let from: Option<String> = sqlx::query_scalar(
        "SELECT renewed_from_root_id FROM supply_contracts WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&c.id)
    .bind(&user.tenant_id)
    .fetch_one(pool)
    .await?;
    let total_days = (c.period_end - c.period_start).num_days() + 1;
    let elapsed = ((today - c.period_start).num_days() + 1).clamp(0, total_days);
    Ok(ok(json!({
        "root_id": c.root_id,
        "revision": c.revision,
        "customer": c.customer,
        "reference": c.reference,
        "status": c.status,
        "warehouse_name": c.warehouse_name,
        "period_start": c.period_start.to_string(),
        "period_end": c.period_end.to_string(),
        "tolerance_pct": c.tolerance_pct,
        "today": today.to_string(),
        "term_days": total_days,
        "elapsed_days": elapsed,
        "renewal": renewal,
        "renewal_lead_days_effective": leads,
        "renewal_lead_days_is_default": c.renewal_lead_days.is_none(),
        "renewed_to_root_id": renewed.get(&c.root_id),
        "renewed_from_root_id": from,
        "comparison": cmp,
    })))
}

// ---- POST /supply-contracts/{root_id}/renew -----------------------------------

/// `expected_revision: int = Field(ge=1)` of the request body, lax like pydantic.
fn validate_renew_body(body: &validation::Body) -> Result<i64, ApiError> {
    let obj: Map<String, Value> = validation::body_object(body)?;
    let at = [json!("body"), json!("expected_revision")];
    let mut errs = Errors::default();
    let mut out: Option<i64> = None;
    match obj.get("expected_revision") {
        None => errs.push("missing", &at, "Field required".into(), &Value::Object(obj.clone()), None),
        Some(v) => {
            let parsed: Option<i128> = match v {
                Value::Bool(b) => Some(i128::from(*b)),
                Value::Number(n) => {
                    if let Some(i) = n.as_i64() {
                        Some(i128::from(i))
                    } else if n.as_u64().is_some() {
                        Some(i128::from(n.as_u64().unwrap()))
                    } else {
                        let f = n.as_f64().unwrap_or(f64::NAN);
                        if f.is_finite() && f.fract() == 0.0 && f.abs() < 9.2e18 {
                            Some(f as i128)
                        } else {
                            errs.push("int_from_float", &at,
                                "Input should be a valid integer, got a number with a fractional part".into(), v, None);
                            None
                        }
                    }
                }
                Value::String(s) => match crate::query::parse_py_int(s) {
                    Some(crate::query::PyInt::Small(i)) => Some(i128::from(i)),
                    Some(crate::query::PyInt::Big(pos)) => Some(if pos { i128::MAX } else { i128::MIN }),
                    None => {
                        errs.push("int_parsing", &at,
                            "Input should be a valid integer, unable to parse string as an integer".into(), v, None);
                        None
                    }
                },
                other => {
                    errs.push("int_type", &at, "Input should be a valid integer".into(), other, None);
                    None
                }
            };
            if let Some(n) = parsed {
                if n < 1 {
                    errs.push("greater_than_equal", &at, "Input should be greater than or equal to 1".into(), v,
                        Some(json!({"ge": 1})));
                } else {
                    out = Some(i64::try_from(n).unwrap_or(i64::MAX));
                }
            }
        }
    }
    errs.into_result()?;
    out.ok_or_else(ApiError::internal)
}

fn out_of_period(sku: &str, date: NaiveDate, shifted: NaiveDate) -> ApiError {
    ApiError::app(
        "supply_contract_renewal_release_out_of_period",
        "A release does not fit the renewed term; create the next contract by hand",
        422,
        json!({"sku": sku, "date": date.to_string(), "shifted": shifted.to_string()}),
    )
}

pub async fn renew(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(root_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let expected_revision = validate_renew_body(&body)?;
    let pool = &state.pool;
    let scope = crate::auth::warehouse_scope::scope_names(pool, &user).await?;
    let today = today();

    let mut tx = pool.begin().await?;
    // The row lock is what serialises two people pressing Renew at once.
    let row = sqlx::query(&format!(
        "SELECT {COLS} {FROM}
          WHERE s.tenant_id = $1 AND s.root_id = $2 AND s.superseded_by IS NULL FOR UPDATE OF s"
    ))
    .bind(&user.tenant_id)
    .bind(&root_id)
    .fetch_optional(&mut *tx)
    .await?;
    let cur = match row {
        Some(r) => contract_of(&r)?,
        None => return Err(not_found()),
    };
    require_writable(&scope, cur.warehouse_name.as_deref())?;
    if i64::from(cur.revision) != expected_revision {
        return Err(ApiError::app(
            "supply_contract_stale",
            "Somebody changed this contract meanwhile; reload it and try again",
            409,
            json!({"revision": cur.revision}),
        ));
    }
    if !matches!(cur.status.as_str(), "active" | "closed") {
        return Err(ApiError::app(
            "supply_contract_renewal_status_invalid",
            "Only an active or closed contract can be renewed",
            409,
            json!({"status": cur.status}),
        ));
    }
    let existing: Option<String> = sqlx::query_scalar(
        "SELECT root_id FROM supply_contracts
          WHERE tenant_id = $1 AND renewed_from_root_id = $2
            AND superseded_by IS NULL AND status <> 'cancelled'
          ORDER BY created_at LIMIT 1",
    )
    .bind(&user.tenant_id)
    .bind(&root_id)
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(renewed_root) = existing {
        return Err(ApiError::app(
            "supply_contract_already_renewed",
            "This contract was already renewed",
            409,
            json!({"renewed_root_id": renewed_root}),
        ));
    }

    let explicit = cur.explicit()?;
    let (new_start, new_end, shifted) = cr::renewal_terms(cur.period_start, cur.period_end, explicit.as_deref())
        .map_err(|e| match e {
            cr::RenewalError::ReleaseOutOfPeriod { sku, date, shifted } => out_of_period(&sku, date, shifted),
            cr::RenewalError::PeriodTooLong => ApiError::app(
                "supply_contract_period_too_long",
                "A contract period is limited to ten years",
                422,
                json!({"years": cr::MAX_YEARS}),
            ),
        })?;
    if new_end < today {
        // Activating it would turn every release of the new term into an
        // overdue commitment at once. The next contract is made by hand, with
        // dates somebody chose.
        return Err(ApiError::app(
            "supply_contract_renewal_period_elapsed",
            "The renewed term already ended; create the next contract by hand",
            422,
            json!({"period_start": new_start.to_string(), "period_end": new_end.to_string()}),
        ));
    }
    let releases_json: Option<Value> = shifted.as_ref().map(|rs| {
        Value::Array(rs.iter().map(|r| json!({"sku": r.sku, "date": r.date.to_string(), "quantity": r.quantity})).collect())
    });

    let new_id = uuid::Uuid::new_v4().simple().to_string(); // the first revision names the lineage
    sqlx::query(
        "INSERT INTO supply_contracts
             (id, tenant_id, root_id, revision, customer, reference, lines, period_start, period_end,
              schedule_kind, releases, tolerance_pct, status, warehouse_id, on_top_of_base, note,
              created_by, notice_days, auto_renew, renewal_lead_days, renewed_from_root_id)
         VALUES ($1, $2, $1, 1, $3, $4, $5, $6, $7, $8, $9, $10, 'draft', $11, $12, $13, $14, $15, $16, $17, $18)",
    )
    .bind(&new_id)
    .bind(&user.tenant_id)
    .bind(&cur.customer)
    .bind(&cur.reference)
    .bind(&cur.lines)
    .bind(new_start)
    .bind(new_end)
    .bind(&cur.schedule_kind)
    .bind(&releases_json)
    .bind(cur.tolerance_pct)
    .bind(&cur.warehouse_id)
    .bind(cur.on_top_of_base)
    .bind(&cur.note)
    .bind(&user.user_id)
    .bind(cur.notice_days)
    .bind(cur.auto_renew)
    .bind(&cur.renewal_lead_days)
    .bind(&root_id)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    tracing::info!("[contract-renewal] RENEW tenant={} from={} to={} by={}", user.tenant_id, root_id, new_id, user.user_id);

    let line_count = cur.lines.as_array().map(|a| a.len()).unwrap_or(0);
    let mut details = Map::new();
    details.insert("customer".into(), json!(cur.customer));
    details.insert("lines".into(), json!(line_count));
    details.insert("revision".into(), json!(1));
    details.insert("status".into(), json!("draft"));
    details.insert("renewed_from".into(), json!(root_id));
    record_event(pool, &user.tenant_id, &user.user_id, Event::SupplyContractRenewed, Some(new_id.as_str()), details).await;

    let has_prices = cur.lines.as_array().is_some_and(|a| a.iter().any(|l| !l["unit_price"].is_null()));
    Ok((StatusCode::CREATED, ok(json!({
        "root_id": new_id,
        "id": new_id,
        "revision": 1,
        "status": "draft",
        "customer": cur.customer,
        "renewed_from_root_id": root_id,
        "period_start": new_start.to_string(),
        "period_end": new_end.to_string(),
        "schedule_kind": cur.schedule_kind,
        "releases": releases_json.as_ref().and_then(|v| v.as_array()).map(|a| a.len()),
        "prices_carried": has_prices,
        "materialised": 0,
    }))))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::validation::Body;

    fn s(names: &[&str]) -> Scope {
        Some(names.iter().map(|n| n.to_string()).collect())
    }

    #[test]
    fn scope_matches_the_contract_service() {
        assert!(visible(&None, None));
        assert!(!visible(&s(&["Norte"]), None), "a company-wide contract is not for a scoped user");
        assert!(!visible(&s(&["Norte"]), Some("  ")));
        assert!(visible(&s(&["Norte"]), Some(" norte ")));
        assert!(!visible(&s(&[]), Some("Norte")));
        assert!(require_writable(&None, None).is_ok());
        let e = require_writable(&s(&["Norte"]), None).unwrap_err();
        assert_eq!(e.body["error_code"], "supply_contract_scope_company_wide");
        assert_eq!(e.status.as_u16(), 403);
        let e = require_writable(&s(&["Norte"]), Some("Sur")).unwrap_err();
        assert_eq!(e.body["error_code"], "warehouse_out_of_scope");
        assert_eq!(e.body["error_params"]["warehouse"], "Sur");
    }

    #[test]
    fn renew_body_validation_is_pydantic_shaped() {
        assert_eq!(validate_renew_body(&Body::Json(json!({"expected_revision": 3}))).unwrap(), 3);
        assert_eq!(validate_renew_body(&Body::Json(json!({"expected_revision": "2"}))).unwrap(), 2);
        assert_eq!(validate_renew_body(&Body::Json(json!({"expected_revision": 4.0}))).unwrap(), 4);
        let code = |b: Value| validate_renew_body(&Body::Json(b)).unwrap_err().body["detail"][0]["type"].clone();
        assert_eq!(code(json!({})), "missing");
        assert_eq!(code(json!({"expected_revision": 0})), "greater_than_equal");
        assert_eq!(code(json!({"expected_revision": "x"})), "int_parsing");
        assert_eq!(code(json!({"expected_revision": 1.5})), "int_from_float");
        assert_eq!(code(json!({"expected_revision": null})), "int_type");
        assert_eq!(code(json!({"expected_revision": [1]})), "int_type");
        let e = validate_renew_body(&Body::Missing).unwrap_err();
        assert_eq!(e.body["detail"][0]["type"], "missing");
    }

    #[test]
    fn the_summary_carries_only_the_screen_fields() {
        let cmp = cr::commitment_comparison(&[], &[], NaiveDate::from_ymd_opt(2027, 1, 1).unwrap());
        let s = summary(&cmp);
        assert!(s.get("lines").is_none());
        assert!(s["fill_rate_pct"].is_null());
        assert_eq!(s["overdue_open"], 0);
    }

    #[test]
    fn bucket_filter_pattern() {
        assert!(bucket_pattern_ok("due_soon"));
        assert!(!bucket_pattern_ok("later"));
    }
}
