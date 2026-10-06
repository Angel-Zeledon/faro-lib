//! S&OP forecast consensus. New routes, written in Rust only (no Python twin,
//! so no failover: see the "Forecast consensus" section of
//! `docs/rust-migration.md`).
//!
//! Each function (sales, finance, operations) submits an adjustment, in basis
//! points, to the statistical forecast of a SKU for a period, with a reason.
//! Every revision stays (who, when, why). A tenant rule turns the current
//! submissions into one consensus percentage per SKU and period
//! (`consensus::math`), a person with the authority approves the frozen result,
//! and ONLY an approved version reaches planning: Python reads it where it
//! decides purchase demand (`inventory/forecast_adjustment_service.py`). Nothing
//! here retrains or touches the engine's output; with nothing submitted and
//! nothing approved, every number in the product is what it was.
//!
//! * `GET  /consensus/settings`, `PUT /consensus/settings` (admin)
//! * `GET|POST /sessions/{id}/consensus/submissions`
//! * `GET  /sessions/{id}/consensus/preview`
//! * `POST /sessions/{id}/consensus/versions` (propose and freeze)
//! * `GET  /consensus/versions`, `GET /consensus/versions/{id}`
//! * `POST /consensus/versions/{id}/approve | reject | withdraw`
//! * `GET  /sessions/{id}/consensus/fva` (statistical vs adjusted vs actual)
//!
//! The tag is INTERNAL: a function's judgement and the sign-off are recorded
//! under a person's name, so no API key reaches these routes. Every route needs
//! company-wide access (a consensus moves every warehouse's purchase demand).

use std::collections::{BTreeMap, HashMap, HashSet};

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::{Extension, Json};
use chrono::{DateTime, Local, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::{record_event, Event};
use crate::auth::warehouse_scope;
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::consensus::math::{
    self, consensus_lines, forecast_value_added, forecast_value_added_by, Config, FvaPoint, Line, Rule, Submission,
    FUNCTIONS,
};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, isoformat_utc, py_strip, take_chars};
use crate::routes::ok;
use crate::routes::sessions::{
    bool_query, get_session, int_query, query_map, query_pairs, row_json, session_not_found, str_query,
};
use crate::state::AppState;
use crate::validation::{self, str_field, Errors, Field, StrRules, NO_STR_RULES};

/// `INTERNAL_TAGS["consensus"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'consensus': a consensus adjustment is one function's judgement and its publication a sign-off by a person with the authority; both are recorded under a person's name and graded per function",
    ),
    is_mcp: false,
};

/// The list a function chooses from. Stable codes: the frontend renders the
/// label. The first nine are the forecast-adjustment reasons.
pub const REASONS: [&str; 11] = [
    "promotion", "price_change", "new_customer", "lost_customer", "seasonality", "supply_issue",
    "market_news", "data_error", "budget_target", "capacity_constraint", "other",
];
const MAX_NOTE_LENGTH: usize = 300;
const MAX_SPAN_DAYS: i64 = 400;
const MIN_BP: i64 = -10_000;
const MAX_BP: i64 = 100_000;
/// Current submissions one forecast may hold, lines one version may freeze, and
/// versions one company keeps.
const MAX_CURRENT_SUBMISSIONS: i64 = 20_000;
const MAX_LINES: usize = 50_000;
const MAX_VERSIONS: i64 = 1000;
const MAX_NAME_LENGTH: usize = 120;
const MAX_COMMENT_LENGTH: usize = 1000;
const MIN_DECISION_COMMENT: usize = 3;
const STATUSES: [&str; 5] = ["proposed", "approved", "rejected", "withdrawn", "superseded"];

const NAME_SQL: &str = "COALESCE(NULLIF(u.full_name, ''), split_part(u.email, '@', 1))";

fn err(code: &str, message: &str, status: u16, params: Value) -> ApiError {
    ApiError::app(code, message, status, params)
}

fn today() -> NaiveDate {
    Local::now().date_naive()
}

fn day_number(d: &NaiveDate) -> i64 {
    use chrono::Datelike;
    d.num_days_from_ce() as i64
}

fn from_day_number(n: i64) -> NaiveDate {
    NaiveDate::from_num_days_from_ce_opt(n as i32).unwrap_or(NaiveDate::MIN)
}

/// "+15.00%" from basis points.
fn pct_label(bp: i64) -> String {
    format!("{:+.2}%", bp as f64 / 100.0)
}

fn details(pairs: &[(&str, Value)]) -> Map<String, Value> {
    pairs.iter().map(|(k, v)| ((*k).to_string(), v.clone())).collect()
}

/// Serialise every write of one tenant's consensus ledger, so two approvals can
/// never both leave a version published and the ceilings cannot be overrun by
/// parallel writes.
async fn lock(tx: &mut sqlx::PgConnection, tenant_id: &str) -> Result<(), ApiError> {
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(format!("consensus:{tenant_id}"))
        .execute(&mut *tx)
        .await?;
    Ok(())
}

/// The caller, with the guards every consensus route shares: person (never a
/// key), and for writes analyst-or-above; always company-wide.
async fn caller(
    state: &AppState,
    headers: &HeaderMap,
    actors: &RequestActors,
    write: bool,
) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    if write {
        auth::require_analyst_or_above(state, &user).await?;
    }
    warehouse_scope::require_company_wide(&state.pool, &user).await?;
    Ok(user)
}

// -- Settings -------------------------------------------------------------------

struct Settings {
    rule: Rule,
    priority: [usize; 3],
    weights: [i64; 3],
    cap_down_bp: i64,
    cap_up_bp: i64,
    updated_by: String,
    updated_at: DateTime<Utc>,
}

impl Settings {
    fn config(&self) -> Config {
        Config {
            rule: self.rule,
            priority: self.priority,
            weights: self.weights,
            cap_down_bp: self.cap_down_bp,
            cap_up_bp: self.cap_up_bp,
        }
    }

    fn rule_json(&self) -> Value {
        rule_json(&self.config())
    }
}

/// The rule as it is frozen into a version and shown on screen.
fn rule_json(c: &Config) -> Value {
    json!({
        "rule": c.rule.as_str(),
        "priority": c.priority.iter().map(|i| FUNCTIONS[*i]).collect::<Vec<_>>(),
        "weights": {"sales": c.weights[0], "finance": c.weights[1], "operations": c.weights[2]},
        "cap_down_bp": c.cap_down_bp,
        "cap_up_bp": c.cap_up_bp,
    })
}

async fn load_settings(pool: &PgPool, tenant_id: &str) -> Result<Option<Settings>, ApiError> {
    let row = sqlx::query(
        "SELECT rule, priority, weight_sales, weight_finance, weight_operations, cap_down_bp, cap_up_bp,
                updated_by, updated_at
           FROM consensus_settings WHERE tenant_id = $1",
    )
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    let Some(r) = row else { return Ok(None) };
    let rule: String = r.try_get("rule")?;
    let priority: Value = r.try_get("priority")?;
    let parsed = (|| {
        let names = priority.as_array()?;
        if names.len() != 3 {
            return None;
        }
        let mut p = [0usize; 3];
        for (k, n) in names.iter().enumerate() {
            p[k] = math::function_index(n.as_str()?)?;
        }
        Some((Rule::parse(&rule)?, p))
    })();
    let Some((rule, priority)) = parsed else {
        tracing::error!(tenant = tenant_id, "consensus_settings holds an unreadable rule");
        return Err(ApiError::internal());
    };
    Ok(Some(Settings {
        rule,
        priority,
        weights: [
            r.try_get::<i32, _>("weight_sales")? as i64,
            r.try_get::<i32, _>("weight_finance")? as i64,
            r.try_get::<i32, _>("weight_operations")? as i64,
        ],
        cap_down_bp: r.try_get::<i32, _>("cap_down_bp")? as i64,
        cap_up_bp: r.try_get::<i32, _>("cap_up_bp")? as i64,
        updated_by: r.try_get("updated_by")?,
        updated_at: r.try_get("updated_at")?,
    }))
}

fn not_configured() -> ApiError {
    err(
        "consensus_rule_not_configured",
        "Choose how the consensus is decided before proposing one",
        409,
        json!({}),
    )
}

/// Active admins, plus analysts flagged as purchase-order approvers: the same
/// people who decide on a demand plan.
async fn approvers(pool: &PgPool, tenant_id: &str) -> Result<Vec<String>, ApiError> {
    let rows: Vec<(String,)> = sqlx::query_as(
        "SELECT id FROM users
          WHERE tenant_id = $1 AND status = 'active'
            AND (role = 'admin' OR (role = 'analyst' AND can_approve_po))
          ORDER BY full_name NULLS LAST, email",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(|r| r.0).collect())
}

/// Who speaks for which function: `{function: [{user_id, name}]}`.
async fn members_json(pool: &PgPool, tenant_id: &str) -> Result<Value, ApiError> {
    let rows = sqlx::query(&format!(
        "SELECT m.function, m.user_id, {NAME_SQL} AS name
           FROM consensus_members m LEFT JOIN users u ON u.id = m.user_id AND u.tenant_id = m.tenant_id
          WHERE m.tenant_id = $1 ORDER BY name, m.user_id"
    ))
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut out: BTreeMap<&str, Vec<Value>> = FUNCTIONS.iter().map(|f| (*f, Vec::new())).collect();
    for r in &rows {
        let f: String = r.try_get("function")?;
        if let Some(list) = out.get_mut(f.as_str()) {
            list.push(json!({"user_id": r.try_get::<String, _>("user_id")?, "name": r.try_get::<Option<String>, _>("name")?}));
        }
    }
    let mut m = Map::new();
    for f in FUNCTIONS {
        m.insert(f.to_string(), Value::Array(out.remove(f).unwrap_or_default()));
    }
    Ok(Value::Object(m))
}

async fn my_functions(pool: &PgPool, user: &CurrentUser) -> Result<Vec<String>, ApiError> {
    let rows: Vec<(String,)> = sqlx::query_as(
        "SELECT function FROM consensus_members WHERE tenant_id = $1 AND user_id = $2",
    )
    .bind(&user.tenant_id)
    .bind(&user.user_id)
    .fetch_all(pool)
    .await?;
    let mut mine: Vec<String> = rows.into_iter().map(|r| r.0).collect();
    mine.sort_by_key(|f| math::function_index(f).unwrap_or(9));
    Ok(mine)
}

/// Who may submit for which function: members of it, and an administrator for any.
fn submit_rights(user: &CurrentUser, mine: &[String]) -> Value {
    let mut m = Map::new();
    for f in FUNCTIONS {
        m.insert(f.to_string(), json!(user.role == "admin" || mine.iter().any(|x| x == f)));
    }
    Value::Object(m)
}

async fn settings_response(state: &AppState, user: &CurrentUser) -> Result<Value, ApiError> {
    let pool = &state.pool;
    let settings = load_settings(pool, &user.tenant_id).await?;
    let mine = my_functions(pool, user).await?;
    let people = approvers(pool, &user.tenant_id).await?;
    let (rule, updated) = match &settings {
        Some(s) => (s.rule_json(), json!({"by": s.updated_by, "at": isoformat_utc(&s.updated_at)})),
        // Shown as a starting point in the form; `configured: false` says nobody chose it.
        None => (rule_json(&Config {
            rule: Rule::Priority, priority: [0, 1, 2], weights: [1, 1, 1], cap_down_bp: MIN_BP, cap_up_bp: MAX_BP,
        }), Value::Null),
    };
    let candidates: Value = if user.role == "admin" {
        let rows = sqlx::query(&format!(
            "SELECT u.id, {NAME_SQL} AS name, u.role FROM users u
              WHERE u.tenant_id = $1 AND u.status = 'active' AND u.role IN ('admin', 'analyst')
              ORDER BY name, u.id"
        ))
        .bind(&user.tenant_id)
        .fetch_all(pool)
        .await?;
        let mut v = Vec::new();
        for r in &rows {
            v.push(json!({"user_id": r.try_get::<String, _>("id")?, "name": r.try_get::<Option<String>, _>("name")?,
                          "role": r.try_get::<String, _>("role")?}));
        }
        Value::Array(v)
    } else {
        Value::Null
    };
    Ok(json!({
        "configured": settings.is_some(),
        "settings": rule,
        "updated": updated,
        "functions": FUNCTIONS,
        "reasons": REASONS,
        "members": members_json(pool, &user.tenant_id).await?,
        "my_functions": mine,
        "can_submit": submit_rights(user, &mine),
        "can_approve": people.iter().any(|id| *id == user.user_id),
        "approver_count": people.len(),
        "candidates": candidates,
    }))
}

pub async fn get_settings(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = caller(&state, &headers, &actors, false).await?;
    Ok(ok(settings_response(&state, &user).await?))
}

/// An integer JSON value (a float with no fractional part counts), with bounds.
fn int_value(errs: &mut Errors, at: &[Value], v: &Value, ge: i64, le: i64) -> Option<i64> {
    let n = match v {
        Value::Number(n) => match n.as_i64() {
            Some(i) => Some(i),
            None => n.as_f64().filter(|f| f.fract() == 0.0 && f.abs() < 9.0e15).map(|f| f as i64),
        },
        _ => None,
    };
    let Some(n) = n else {
        let (typ, msg) = match v {
            Value::Number(_) => ("int_from_float", "Input should be a valid integer, got a number with a fractional part"),
            _ => ("int_type", "Input should be a valid integer"),
        };
        errs.push(typ, at, msg.into(), v, None);
        return None;
    };
    if n < ge {
        errs.push("greater_than_equal", at, format!("Input should be greater than or equal to {ge}"), v,
            Some(json!({"ge": ge})));
        return None;
    }
    if n > le {
        errs.push("less_than_equal", at, format!("Input should be less than or equal to {le}"), v,
            Some(json!({"le": le})));
        return None;
    }
    Some(n)
}

fn int_field(errs: &mut Errors, obj: &Map<String, Value>, name: &str, ge: i64, le: i64) -> Option<i64> {
    let at = validation::loc(&[json!("body")], name);
    match obj.get(name) {
        None => {
            errs.push("missing", &at, "Field required".into(), &Value::Object(obj.clone()), None);
            None
        }
        Some(v) => int_value(errs, &at, v, ge, le),
    }
}

pub async fn put_settings(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = caller(&state, &headers, &actors, true).await?;
    auth::require_role(&user, &["admin"])?;
    let obj = validation::body_object(&body)?;

    let mut errs = Errors::default();
    let p = [json!("body")];
    let rule = str_field(&mut errs, &obj, &p, "rule", true, false, &NO_STR_RULES);
    let priority_at = validation::loc(&p, "priority");
    let priority_raw = match obj.get("priority") {
        Some(Value::Array(a)) => Some(a.clone()),
        Some(other) => {
            errs.push("list_type", &priority_at, "Input should be a valid list".into(), other, None);
            None
        }
        None => {
            errs.push("missing", &priority_at, "Field required".into(), &Value::Object(obj.clone()), None);
            None
        }
    };
    let mut weights = [0i64; 3];
    let weights_at = validation::loc(&p, "weights");
    match obj.get("weights") {
        Some(Value::Object(w)) => {
            for (k, f) in FUNCTIONS.iter().enumerate() {
                let mut at = weights_at.clone();
                at.push(json!(f));
                match w.get(*f) {
                    None => errs.push("missing", &at, "Field required".into(), &Value::Object(w.clone()), None),
                    Some(v) => {
                        if let Some(n) = int_value(&mut errs, &at, v, 0, 1000) {
                            weights[k] = n;
                        }
                    }
                }
            }
        }
        Some(other) => errs.push("dict_type", &weights_at, "Input should be a valid dictionary".into(), other, None),
        None => errs.push("missing", &weights_at, "Field required".into(), &Value::Object(obj.clone()), None),
    }
    let cap_down = int_field(&mut errs, &obj, "cap_down_bp", MIN_BP, 0);
    let cap_up = int_field(&mut errs, &obj, "cap_up_bp", 0, MAX_BP);
    // members: optional; when present it replaces the whole assignment.
    let members_at = validation::loc(&p, "members");
    let mut members: Option<Vec<(usize, String)>> = None;
    match obj.get("members") {
        None | Some(Value::Null) => {}
        Some(Value::Object(m)) => {
            let mut out = Vec::new();
            for (k, f) in FUNCTIONS.iter().enumerate() {
                let mut at = members_at.clone();
                at.push(json!(f));
                match m.get(*f) {
                    None => {}
                    Some(Value::Array(ids)) => {
                        for id in ids {
                            match id.as_str() {
                                Some(s) if !s.is_empty() && s.len() <= 64 => out.push((k, s.to_string())),
                                _ => errs.push("string_type", &at, "Input should be a valid string".into(), id, None),
                            }
                        }
                    }
                    Some(other) => errs.push("list_type", &at, "Input should be a valid list".into(), other, None),
                }
            }
            members = Some(out);
        }
        Some(other) => errs.push("dict_type", &members_at, "Input should be a valid dictionary".into(), other, None),
    }
    errs.into_result()?;

    let rule_s = match rule { Field::Value(v) => v, _ => return Err(ApiError::internal()) };
    let Some(rule) = Rule::parse(&rule_s) else {
        return Err(err("consensus_rule_invalid", "Choose priority or weighted", 422, json!({"rule": rule_s})));
    };
    let names: Vec<String> = priority_raw.unwrap_or_default().iter()
        .map(|v| v.as_str().unwrap_or("").to_string()).collect();
    let mut priority = [0usize; 3];
    let mut seen = HashSet::new();
    let valid_priority = names.len() == 3 && names.iter().enumerate().all(|(k, n)| match math::function_index(n) {
        Some(i) if seen.insert(i) => { priority[k] = i; true }
        _ => false,
    });
    if !valid_priority {
        return Err(err("consensus_priority_invalid",
            "List sales, finance and operations once each, in order of priority", 422, json!({"priority": names})));
    }
    if rule == Rule::Weighted && weights.iter().sum::<i64>() == 0 {
        return Err(err("consensus_weights_invalid", "At least one function needs a weight above zero", 422, json!({})));
    }
    let (cap_down, cap_up) = (cap_down.unwrap_or(MIN_BP), cap_up.unwrap_or(MAX_BP));

    if let Some(list) = &members {
        let ids: Vec<String> = list.iter().map(|m| m.1.clone()).collect();
        let found: Vec<(String,)> = sqlx::query_as(
            "SELECT id FROM users WHERE tenant_id = $1 AND id = ANY($2) AND status = 'active'
                AND role IN ('admin', 'analyst')",
        )
        .bind(&user.tenant_id)
        .bind(&ids)
        .fetch_all(&state.pool)
        .await?;
        let known: HashSet<String> = found.into_iter().map(|r| r.0).collect();
        if let Some((k, bad)) = list.iter().find(|m| !known.contains(&m.1)) {
            return Err(err("consensus_member_invalid",
                "Only an active administrator or analyst of this company can speak for a function", 422,
                json!({"user_id": bad, "function": FUNCTIONS[*k]})));
        }
    }

    let mut tx = state.pool.begin().await?;
    lock(&mut tx, &user.tenant_id).await?;
    sqlx::query(
        "INSERT INTO consensus_settings
             (tenant_id, rule, priority, weight_sales, weight_finance, weight_operations,
              cap_down_bp, cap_up_bp, updated_by, updated_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
         ON CONFLICT (tenant_id) DO UPDATE SET
             rule = EXCLUDED.rule, priority = EXCLUDED.priority,
             weight_sales = EXCLUDED.weight_sales, weight_finance = EXCLUDED.weight_finance,
             weight_operations = EXCLUDED.weight_operations, cap_down_bp = EXCLUDED.cap_down_bp,
             cap_up_bp = EXCLUDED.cap_up_bp, updated_by = EXCLUDED.updated_by, updated_at = NOW()",
    )
    .bind(&user.tenant_id)
    .bind(rule.as_str())
    .bind(json!(priority.iter().map(|i| FUNCTIONS[*i]).collect::<Vec<_>>()))
    .bind(weights[0] as i32)
    .bind(weights[1] as i32)
    .bind(weights[2] as i32)
    .bind(cap_down as i32)
    .bind(cap_up as i32)
    .bind(&user.user_id)
    .execute(&mut *tx)
    .await?;
    if let Some(list) = &members {
        sqlx::query("DELETE FROM consensus_members WHERE tenant_id = $1")
            .bind(&user.tenant_id)
            .execute(&mut *tx)
            .await?;
        let mut done = HashSet::new();
        for (k, id) in list {
            if !done.insert((*k, id.clone())) {
                continue;
            }
            sqlx::query(
                "INSERT INTO consensus_members (tenant_id, function, user_id, assigned_by) VALUES ($1, $2, $3, $4)",
            )
            .bind(&user.tenant_id)
            .bind(FUNCTIONS[*k])
            .bind(id)
            .bind(&user.user_id)
            .execute(&mut *tx)
            .await?;
        }
    }
    tx.commit().await?;
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::ConsensusRuleChanged, None,
        details(&[("rule", json!(rule.as_str()))])).await;
    Ok(ok(settings_response(&state, &user).await?))
}

// -- Sessions -------------------------------------------------------------------

/// A completed, active, real (not back-test) forecast: the only kind a
/// consensus is built on.
async fn usable_session(pool: &PgPool, tenant_id: &str, session_id: &str) -> Result<Map<String, Value>, ApiError> {
    let s = get_session(pool, tenant_id, session_id).await?.ok_or_else(session_not_found)?;
    let status = s.get("status").and_then(Value::as_str).unwrap_or_default().to_string();
    if status != "COMPLETED" {
        return Err(err("session_still_training", &format!("Session must be COMPLETED. Current: {status}"), 409,
            json!({"status": status})));
    }
    let reason = if !s.get("archived_at").map(Value::is_null).unwrap_or(true) {
        Some("archived")
    } else if s.get("is_backtest").and_then(Value::as_bool).unwrap_or(false) {
        Some("backtest")
    } else {
        None
    };
    if let Some(reason) = reason {
        return Err(err("consensus_session_not_usable",
            "A consensus is built on a completed, active forecast", 409, json!({"reason": reason})));
    }
    Ok(s)
}

// -- Submissions ------------------------------------------------------------------

const SUBMISSION_COLS: &str = "a.id, a.session_id, a.sku, a.function, a.start_date, a.end_date, a.pct_bp,
    a.reason_code, a.reason_note, a.revision, a.created_by, a.created_at, a.superseded_by, a.superseded_at";

fn submission_sql(extra_where: &str, tail: &str) -> String {
    format!(
        "SELECT {SUBMISSION_COLS}, {NAME_SQL} AS created_by_name FROM consensus_submissions a
           LEFT JOIN users u ON u.id = a.created_by AND u.tenant_id = a.tenant_id
          WHERE a.tenant_id = $1 AND a.session_id = $2 {extra_where} {tail}"
    )
}

pub async fn list_submissions(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = caller(&state, &headers, &actors, false).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let sku = str_query(&mut errs, &q, "sku", Some(200), None).filter(|s| !s.is_empty());
    let function = str_query(&mut errs, &q, "function", Some(20), None).filter(|s| !s.is_empty());
    let include_superseded = bool_query(&mut errs, &q, "include_superseded").unwrap_or(false);
    let limit = int_query(&mut errs, &q, "limit", Some(1), Some(5000)).unwrap_or(1000);
    let offset = int_query(&mut errs, &q, "offset", Some(0), None).unwrap_or(0);
    errs.into_result()?;
    get_session(&state.pool, &user.tenant_id, &session_id).await?.ok_or_else(session_not_found)?;

    let mut clauses = String::new();
    let mut next = 3;
    let mut binds: Vec<&str> = Vec::new();
    if let Some(s) = &sku {
        clauses.push_str(&format!(" AND a.sku = ${next}"));
        binds.push(s);
        next += 1;
    }
    if let Some(f) = &function {
        clauses.push_str(&format!(" AND a.function = ${next}"));
        binds.push(f);
    }
    if !include_superseded {
        clauses.push_str(" AND a.superseded_by IS NULL");
    }
    let sql = submission_sql(&clauses, "ORDER BY a.sku, a.start_date, a.function, a.created_at");
    let count_sql = format!(
        "SELECT COUNT(*) FROM consensus_submissions a WHERE a.tenant_id = $1 AND a.session_id = $2 {clauses}");
    let paged_sql = format!("{sql} LIMIT {limit} OFFSET {offset}");
    let mut qy = sqlx::query(&paged_sql).bind(&user.tenant_id).bind(&session_id);
    let mut cq = sqlx::query_as::<_, (i64,)>(&count_sql).bind(&user.tenant_id).bind(&session_id);
    for b in &binds {
        qy = qy.bind(*b);
        cq = cq.bind(*b);
    }
    let rows = qy.fetch_all(&state.pool).await?;
    let total = cq.fetch_one(&state.pool).await?.0;
    let items: Vec<Value> = rows.iter().map(|r| row_json(r).map(Value::Object)).collect::<Result<_, _>>()?;
    let mine = my_functions(&state.pool, &user).await?;
    Ok(ok(json!({
        "functions": FUNCTIONS, "reasons": REASONS, "items": items, "total": total,
        "limit": limit, "offset": offset,
        "my_functions": mine, "can_submit": submit_rights(&user, &mine),
    })))
}

fn as_date(value: &str, field: &str) -> Result<NaiveDate, ApiError> {
    date_fromisoformat(&take_chars(value, 10)).ok_or_else(|| {
        err("date_invalid_iso", &format!("{field} must be an ISO date (YYYY-MM-DD)"), 422, json!({"field": field}))
    })
}

fn dates_invalid(message: &str, start: &NaiveDate, end: &NaiveDate) -> ApiError {
    err("consensus_dates_invalid", message, 422,
        json!({"start_date": isoformat_date(start), "end_date": isoformat_date(end)}))
}

/// Does any series of the forecast belong to this SKU? (A series key is
/// `sku` or `sku│store`.) A typo must be refused, not become a consensus that
/// moves nothing.
async fn sku_in_forecast(pool: &PgPool, tenant_id: &str, session_id: &str, sku: &str) -> Result<bool, ApiError> {
    let (found,): (bool,) = sqlx::query_as(
        "SELECT EXISTS (
             SELECT 1 FROM session_results r,
                  jsonb_object_keys(CASE WHEN jsonb_typeof(r.forecasts) = 'object' THEN r.forecasts ELSE '{}'::jsonb END) k
              WHERE r.session_id = $1 AND r.tenant_id = $2 AND split_part(k, '│', 1) = $3)",
    )
    .bind(session_id)
    .bind(tenant_id)
    .bind(sku)
    .fetch_one(pool)
    .await?;
    Ok(found)
}

pub async fn create_submission(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = caller(&state, &headers, &actors, true).await?;
    let obj = validation::body_object(&body)?;

    let mut errs = Errors::default();
    let p = [json!("body")];
    let sku = str_field(&mut errs, &obj, &p, "sku", true, false,
        &StrRules { min_length: Some(1), max_length: Some(200), pattern: None });
    let function = str_field(&mut errs, &obj, &p, "function", true, false, &NO_STR_RULES);
    let start = str_field(&mut errs, &obj, &p, "start_date", true, false, &NO_STR_RULES);
    let end = str_field(&mut errs, &obj, &p, "end_date", true, false, &NO_STR_RULES);
    let pct_bp = int_field(&mut errs, &obj, "pct_bp", -1_000_000_000_000, 1_000_000_000_000);
    let reason = str_field(&mut errs, &obj, &p, "reason_code", true, false, &NO_STR_RULES);
    let note = str_field(&mut errs, &obj, &p, "reason_note", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_NOTE_LENGTH), pattern: None });
    errs.into_result()?;
    let val = |f: Field<String>| match f { Field::Value(v) => Ok(v), _ => Err(ApiError::internal()) };
    let (sku, function, start, end, reason) = (val(sku)?, val(function)?, val(start)?, val(end)?, val(reason)?);
    let pct_bp = pct_bp.ok_or_else(ApiError::internal)?;
    let note = match note { Field::Value(v) => Some(v), _ => None };

    let sku = py_strip(&sku).to_string();
    if sku.is_empty() {
        return Err(err("consensus_sku_required", "Choose a product to adjust", 422, json!({})));
    }
    let Some(f_idx) = math::function_index(&function) else {
        return Err(err("consensus_function_invalid", "Choose sales, finance or operations", 422,
            json!({"function": function})));
    };
    if !REASONS.contains(&reason.as_str()) {
        return Err(err("consensus_reason_invalid", "Choose a reason from the list", 422, json!({"reason": reason})));
    }
    let note = take_chars(py_strip(note.as_deref().unwrap_or("")), MAX_NOTE_LENGTH);
    let note = if note.is_empty() { None } else { Some(note) };
    if reason == "other" && note.is_none() {
        return Err(err("consensus_note_required", "Say what the reason is when you choose 'other'", 422, json!({})));
    }
    let start = as_date(&start, "start_date")?;
    let end = as_date(&end, "end_date")?;
    if end < start {
        return Err(dates_invalid("The end date is before the start", &start, &end));
    }
    if (end - start).num_days() > MAX_SPAN_DAYS {
        return Err(dates_invalid("That period is too long to adjust", &start, &end));
    }
    if !(MIN_BP..=MAX_BP).contains(&pct_bp) {
        return Err(err("consensus_out_of_range",
            "That change would take demand below zero or past ten times the forecast", 422, json!({"pct_bp": pct_bp})));
    }
    usable_session(&state.pool, &user.tenant_id, &session_id).await?;
    // Who may speak for a function: its members, and an administrator for any.
    if user.role != "admin" {
        let mine = my_functions(&state.pool, &user).await?;
        if !mine.iter().any(|f| f == FUNCTIONS[f_idx]) {
            return Err(err("consensus_not_member", "You do not speak for that function", 403,
                json!({"function": FUNCTIONS[f_idx]})));
        }
    }
    if !sku_in_forecast(&state.pool, &user.tenant_id, &session_id, &sku).await? {
        return Err(err("consensus_sku_unknown", "This forecast has no product with that code", 404,
            json!({"sku": sku})));
    }

    let mut tx = state.pool.begin().await?;
    lock(&mut tx, &user.tenant_id).await?;
    let (current,): (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM consensus_submissions
          WHERE tenant_id = $1 AND session_id = $2 AND superseded_by IS NULL",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .fetch_one(&mut *tx)
    .await?;
    let clashes = sqlx::query(
        "SELECT id, start_date, end_date FROM consensus_submissions
          WHERE tenant_id = $1 AND session_id = $2 AND sku = $3 AND function = $4
            AND superseded_by IS NULL AND start_date <= $6 AND end_date >= $5",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .bind(&sku)
    .bind(FUNCTIONS[f_idx])
    .bind(start)
    .bind(end)
    .fetch_all(&mut *tx)
    .await?;
    let mut replaces: Option<String> = None;
    for c in &clashes {
        let (cs, ce): (NaiveDate, NaiveDate) = (c.try_get("start_date")?, c.try_get("end_date")?);
        let id: String = c.try_get("id")?;
        if cs == start && ce == end {
            replaces = Some(id);
        } else {
            // Replacing part of a period would silently drop the rest of it.
            return Err(err("consensus_overlap",
                "That period overlaps one this function already submitted for the product; revise that exact period instead",
                409, json!({"sku": sku, "function": FUNCTIONS[f_idx], "submission_id": id,
                            "start_date": isoformat_date(&cs), "end_date": isoformat_date(&ce)})));
        }
    }
    if replaces.is_none() && current >= MAX_CURRENT_SUBMISSIONS {
        return Err(err("consensus_limit", "This forecast already holds the maximum number of adjustments", 409,
            json!({"max": MAX_CURRENT_SUBMISSIONS})));
    }
    let (revision,): (i32,) = sqlx::query_as(
        "SELECT COALESCE(MAX(revision), 0) + 1 FROM consensus_submissions
          WHERE tenant_id = $1 AND session_id = $2 AND sku = $3 AND function = $4
            AND start_date = $5 AND end_date = $6",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .bind(&sku)
    .bind(FUNCTIONS[f_idx])
    .bind(start)
    .bind(end)
    .fetch_one(&mut *tx)
    .await?;
    let (new_id,): (String,) = sqlx::query_as(
        "INSERT INTO consensus_submissions
             (tenant_id, session_id, sku, function, start_date, end_date, pct_bp, reason_code, reason_note,
              revision, created_by)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .bind(&sku)
    .bind(FUNCTIONS[f_idx])
    .bind(start)
    .bind(end)
    .bind(pct_bp as i32)
    .bind(&reason)
    .bind(&note)
    .bind(revision)
    .bind(&user.user_id)
    .fetch_one(&mut *tx)
    .await?;
    if let Some(old) = &replaces {
        sqlx::query("UPDATE consensus_submissions SET superseded_by = $1, superseded_at = NOW() WHERE id = $2 AND tenant_id = $3")
            .bind(&new_id)
            .bind(old)
            .bind(&user.tenant_id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;

    let row = sqlx::query(&submission_sql("AND a.id = $3", ""))
        .bind(&user.tenant_id)
        .bind(&session_id)
        .bind(&new_id)
        .fetch_one(&state.pool)
        .await?;
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::ConsensusAdjustmentSubmitted, Some(&new_id),
        details(&[("sku", json!(sku)), ("function", json!(FUNCTIONS[f_idx])), ("adjustment", json!(pct_label(pct_bp))),
                  ("adjustment_reason", json!(reason))])).await;
    Ok((StatusCode::CREATED, ok(Value::Object(row_json(&row)?))))
}

// -- Consensus lines -----------------------------------------------------------------

/// The session's current submissions, as the math reads them.
async fn current_submissions(pool: &PgPool, tenant_id: &str, session_id: &str) -> Result<Vec<Submission>, ApiError> {
    let rows = sqlx::query(
        "SELECT id, sku, function, start_date, end_date, pct_bp FROM consensus_submissions
          WHERE tenant_id = $1 AND session_id = $2 AND superseded_by IS NULL
          ORDER BY sku, start_date, function",
    )
    .bind(tenant_id)
    .bind(session_id)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        let function: String = r.try_get("function")?;
        let (s, e): (NaiveDate, NaiveDate) = (r.try_get("start_date")?, r.try_get("end_date")?);
        out.push(Submission {
            id: r.try_get("id")?,
            sku: r.try_get("sku")?,
            function: math::function_index(&function).ok_or_else(ApiError::internal)?,
            start: day_number(&s),
            end: day_number(&e),
            pct_bp: r.try_get::<i32, _>("pct_bp")? as i64,
        });
    }
    Ok(out)
}

fn compute(config: &Config, subs: &[Submission]) -> Result<Vec<Line>, ApiError> {
    consensus_lines(config, subs).map_err(|e| {
        err("consensus_inconsistent", "Two current adjustments of one function overlap on a product", 409,
            json!({"sku": e.sku}))
    })
}

fn line_json(l: &Line) -> Value {
    json!({
        "sku": l.sku,
        "start_date": isoformat_date(&from_day_number(l.start)),
        "end_date": isoformat_date(&from_day_number(l.end)),
        "pct_bp": l.pct_bp,
        "source": l.source.map(|f| FUNCTIONS[f]),
        "inputs": l.inputs.iter().map(|i| json!({
            "function": FUNCTIONS[i.function], "pct_bp": i.pct_bp,
            "submission_id": i.submission_id, "capped": i.capped,
        })).collect::<Vec<_>>(),
    })
}

fn page(lines: &[Value], sku: &Option<String>, offset: i64, limit: i64) -> Value {
    let filtered: Vec<&Value> = lines
        .iter()
        .filter(|l| sku.as_ref().map(|s| l["sku"].as_str() == Some(s.as_str())).unwrap_or(true))
        .collect();
    let total = filtered.len();
    let items: Vec<&Value> = filtered.into_iter().skip(offset as usize).take(limit as usize).collect();
    json!({"items": items, "total": total, "offset": offset, "limit": limit})
}

pub async fn preview(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = caller(&state, &headers, &actors, false).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let sku = str_query(&mut errs, &q, "sku", Some(200), None).filter(|s| !s.is_empty());
    let limit = int_query(&mut errs, &q, "limit", Some(1), Some(1000)).unwrap_or(100);
    let offset = int_query(&mut errs, &q, "offset", Some(0), None).unwrap_or(0);
    errs.into_result()?;
    get_session(&state.pool, &user.tenant_id, &session_id).await?.ok_or_else(session_not_found)?;
    let settings = load_settings(&state.pool, &user.tenant_id).await?.ok_or_else(not_configured)?;
    let subs = current_submissions(&state.pool, &user.tenant_id, &session_id).await?;
    let lines: Vec<Value> = compute(&settings.config(), &subs)?.iter().map(line_json).collect();
    let skus: HashSet<&str> = subs.iter().map(|s| s.sku.as_str()).collect();
    Ok(ok(json!({
        "rule": settings.rule_json(), "n_submissions": subs.len(), "sku_count": skus.len(),
        "line_count": lines.len(), "lines": page(&lines, &sku, offset, limit),
    })))
}

// -- Versions -------------------------------------------------------------------------

const VERSION_COLS: &str = "v.id, v.session_id, v.name, v.note, v.rule, v.line_count, v.sku_count, v.status,
    v.created_by, v.created_at, v.decided_by, v.decided_at, v.decision_comment, v.self_approved";

fn version_sql(where_clause: &str, tail: &str) -> String {
    format!(
        "SELECT {VERSION_COLS}, {NAME_SQL} AS created_by_name, d.name_ AS decided_by_name, s.name AS session_name
           FROM consensus_versions v
           LEFT JOIN users u ON u.id = v.created_by AND u.tenant_id = v.tenant_id
           LEFT JOIN (SELECT id, tenant_id, COALESCE(NULLIF(full_name, ''), split_part(email, '@', 1)) AS name_ FROM users) d
                  ON d.id = v.decided_by AND d.tenant_id = v.tenant_id
           LEFT JOIN sessions s ON s.id = v.session_id AND s.tenant_id = v.tenant_id
          WHERE v.tenant_id = $1 {where_clause} {tail}"
    )
}

fn not_found() -> ApiError {
    err("consensus_version_not_found", "Consensus version not found", 404, json!({}))
}

pub async fn propose(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = caller(&state, &headers, &actors, true).await?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let p = [json!("body")];
    let name = str_field(&mut errs, &obj, &p, "name", true, false,
        &StrRules { min_length: Some(1), max_length: Some(MAX_NAME_LENGTH), pattern: None });
    let note = str_field(&mut errs, &obj, &p, "note", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_COMMENT_LENGTH), pattern: None });
    errs.into_result()?;
    let name = match name { Field::Value(v) => py_strip(&v).to_string(), _ => return Err(ApiError::internal()) };
    if name.is_empty() {
        return Err(err("consensus_name_required", "Give the version a name", 422, json!({})));
    }
    let note = match note { Field::Value(v) => Some(py_strip(&v).to_string()).filter(|s| !s.is_empty()), _ => None };
    usable_session(&state.pool, &user.tenant_id, &session_id).await?;
    let settings = load_settings(&state.pool, &user.tenant_id).await?.ok_or_else(not_configured)?;

    let mut tx = state.pool.begin().await?;
    lock(&mut tx, &user.tenant_id).await?;
    let subs_rows = sqlx::query(
        "SELECT id, sku, function, start_date, end_date, pct_bp FROM consensus_submissions
          WHERE tenant_id = $1 AND session_id = $2 AND superseded_by IS NULL ORDER BY sku, start_date, function",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .fetch_all(&mut *tx)
    .await?;
    let mut subs = Vec::with_capacity(subs_rows.len());
    for r in &subs_rows {
        let function: String = r.try_get("function")?;
        let (s, e): (NaiveDate, NaiveDate) = (r.try_get("start_date")?, r.try_get("end_date")?);
        subs.push(Submission {
            id: r.try_get("id")?, sku: r.try_get("sku")?,
            function: math::function_index(&function).ok_or_else(ApiError::internal)?,
            start: day_number(&s), end: day_number(&e), pct_bp: r.try_get::<i32, _>("pct_bp")? as i64,
        });
    }
    let lines = compute(&settings.config(), &subs)?;
    if lines.is_empty() {
        return Err(err("consensus_nothing_to_propose",
            "No function has submitted an adjustment that counts under the current rule", 409, json!({})));
    }
    if lines.len() > MAX_LINES {
        return Err(err("consensus_too_large", "This consensus has too many lines to freeze as one version", 409,
            json!({"lines": lines.len(), "max": MAX_LINES})));
    }
    let (versions,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM consensus_versions WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_one(&mut *tx)
        .await?;
    if versions >= MAX_VERSIONS {
        return Err(err("consensus_version_limit", "This company already keeps the maximum number of consensus versions",
            409, json!({"max": MAX_VERSIONS})));
    }
    let skus: HashSet<&str> = lines.iter().map(|l| l.sku.as_str()).collect();
    let line_values: Vec<Value> = lines.iter().map(line_json).collect();
    let (id,): (String,) = sqlx::query_as(
        "INSERT INTO consensus_versions
             (tenant_id, session_id, name, note, rule, lines, line_count, sku_count, created_by)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .bind(&name)
    .bind(&note)
    .bind(settings.rule_json())
    .bind(Value::Array(line_values))
    .bind(lines.len() as i32)
    .bind(skus.len() as i32)
    .bind(&user.user_id)
    .fetch_one(&mut *tx)
    .await?;
    insert_event(&mut tx, &user.tenant_id, &id, None, "proposed", &user.user_id, note.as_deref(), json!({})).await?;
    tx.commit().await?;
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::ConsensusVersionProposed, Some(&id),
        details(&[("plan_name", json!(name)), ("skus", json!(skus.len())), ("lines", json!(lines.len()))])).await;
    Ok((StatusCode::CREATED, ok(version_detail(&state, &user, &id, None, 0, 100).await?)))
}

#[allow(clippy::too_many_arguments)]
async fn insert_event(
    tx: &mut sqlx::PgConnection,
    tenant_id: &str,
    version_id: &str,
    from: Option<&str>,
    to: &str,
    actor_id: &str,
    comment: Option<&str>,
    details: Value,
) -> Result<(), ApiError> {
    sqlx::query(
        "INSERT INTO consensus_version_events (tenant_id, version_id, from_status, to_status, actor_id, comment, details)
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(tenant_id)
    .bind(version_id)
    .bind(from)
    .bind(to)
    .bind(actor_id)
    .bind(comment)
    .bind(details)
    .execute(&mut *tx)
    .await?;
    Ok(())
}

pub async fn list_versions(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = caller(&state, &headers, &actors, false).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let session_id = str_query(&mut errs, &q, "session_id", Some(64), None).filter(|s| !s.is_empty());
    errs.into_result()?;
    let sql = match &session_id {
        Some(_) => version_sql("AND v.session_id = $2", "ORDER BY v.created_at DESC LIMIT 200"),
        None => version_sql("", "ORDER BY v.created_at DESC LIMIT 200"),
    };
    let mut qy = sqlx::query(&sql).bind(&user.tenant_id);
    if let Some(s) = &session_id {
        qy = qy.bind(s);
    }
    let rows = qy.fetch_all(&state.pool).await?;
    let items: Vec<Value> = rows.iter().map(|r| row_json(r).map(Value::Object)).collect::<Result<_, _>>()?;
    let people = approvers(&state.pool, &user.tenant_id).await?;
    Ok(ok(json!({
        "items": items, "statuses": STATUSES,
        "approver_count": people.len(), "can_approve": people.iter().any(|id| *id == user.user_id),
        "max_versions": MAX_VERSIONS,
    })))
}

/// The version, a page of its lines, its events, and what the approver must know:
/// has an input been revised since, and has every period already passed.
async fn version_detail(
    state: &AppState,
    user: &CurrentUser,
    version_id: &str,
    sku: Option<String>,
    offset: i64,
    limit: i64,
) -> Result<Value, ApiError> {
    let pool = &state.pool;
    let row = sqlx::query(&version_sql("AND v.id = $2", ""))
        .bind(&user.tenant_id)
        .bind(version_id)
        .fetch_optional(pool)
        .await?
        .ok_or_else(not_found)?;
    let mut out = row_json(&row)?;
    let lines_row: (Value,) = sqlx::query_as("SELECT lines FROM consensus_versions WHERE id = $1 AND tenant_id = $2")
        .bind(version_id)
        .bind(&user.tenant_id)
        .fetch_one(pool)
        .await?;
    let lines = lines_row.0.as_array().cloned().unwrap_or_default();
    let ids: Vec<String> = lines.iter()
        .flat_map(|l| l["inputs"].as_array().cloned().unwrap_or_default())
        .filter_map(|i| i["submission_id"].as_str().map(str::to_string))
        .collect();
    let (revised,): (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM consensus_submissions WHERE tenant_id = $1 AND id = ANY($2) AND superseded_by IS NOT NULL",
    )
    .bind(&user.tenant_id)
    .bind(&ids)
    .fetch_one(pool)
    .await?;
    let now = isoformat_date(&today());
    let expired = lines.iter().all(|l| l["end_date"].as_str().map(|e| e < now.as_str()).unwrap_or(true));
    out.insert("lines".into(), page(&lines, &sku, offset, limit));
    out.insert("revised_inputs".into(), json!(revised));
    out.insert("expired".into(), json!(expired));
    let events = sqlx::query(&format!(
        "SELECT e.id, e.from_status, e.to_status, e.actor_id, e.comment, e.details, e.created_at,
                {NAME_SQL} AS actor_name
           FROM consensus_version_events e LEFT JOIN users u ON u.id = e.actor_id AND u.tenant_id = e.tenant_id
          WHERE e.tenant_id = $1 AND e.version_id = $2 ORDER BY e.id"
    ))
    .bind(&user.tenant_id)
    .bind(version_id)
    .fetch_all(pool)
    .await?;
    out.insert("events".into(), Value::Array(
        events.iter().map(|r| row_json(r).map(Value::Object)).collect::<Result<_, _>>()?));
    let people = approvers(pool, &user.tenant_id).await?;
    out.insert("approver_count".into(), json!(people.len()));
    out.insert("can_approve".into(), json!(people.iter().any(|id| *id == user.user_id)));
    Ok(Value::Object(out))
}

pub async fn get_version(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(version_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = caller(&state, &headers, &actors, false).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let sku = str_query(&mut errs, &q, "sku", Some(200), None).filter(|s| !s.is_empty());
    let limit = int_query(&mut errs, &q, "limit", Some(1), Some(1000)).unwrap_or(100);
    let offset = int_query(&mut errs, &q, "offset", Some(0), None).unwrap_or(0);
    errs.into_result()?;
    Ok(ok(version_detail(&state, &user, &version_id, sku, offset, limit).await?))
}

#[derive(Clone, Copy, PartialEq)]
enum Decision {
    Approve,
    Reject,
    Withdraw,
}

async fn decide(
    state: AppState,
    actors: RequestActors,
    version_id: String,
    headers: HeaderMap,
    bytes: Bytes,
    decision: Decision,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = caller(&state, &headers, &actors, true).await?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let comment = str_field(&mut errs, &obj, &[json!("body")], "comment", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_COMMENT_LENGTH), pattern: None });
    errs.into_result()?;
    let comment = match comment {
        Field::Value(v) => Some(py_strip(&v).to_string()).filter(|s| !s.is_empty()),
        _ => None,
    };
    let (from, to) = match decision {
        Decision::Approve => ("proposed", "approved"),
        Decision::Reject => ("proposed", "rejected"),
        Decision::Withdraw => ("approved", "withdrawn"),
    };
    if decision != Decision::Approve && comment.as_deref().map(|c| c.chars().count()).unwrap_or(0) < MIN_DECISION_COMMENT {
        return Err(err("consensus_reason_required", "Say why", 422, json!({"min": MIN_DECISION_COMMENT})));
    }

    let people = approvers(&state.pool, &user.tenant_id).await?;
    if !people.iter().any(|id| *id == user.user_id) {
        return Err(err("consensus_not_approver",
            "Only an administrator or a flagged approver can decide on a consensus", 403, json!({})));
    }
    let mut tx = state.pool.begin().await?;
    lock(&mut tx, &user.tenant_id).await?;
    let current: Option<(String, String, String, String, Value)> = sqlx::query_as(
        "SELECT status, session_id, created_by, name, lines FROM consensus_versions
          WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
    )
    .bind(&version_id)
    .bind(&user.tenant_id)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((status, session_id, created_by, name, lines)) = current else { return Err(not_found()) };
    if status != from {
        return Err(err("consensus_transition_invalid", "This version cannot move to that status from where it is",
            409, json!({"from": status, "to": to})));
    }
    let mut self_approved = false;
    let mut superseded = 0usize;
    if decision == Decision::Approve {
        if created_by == user.user_id {
            if people.len() > 1 {
                return Err(err("consensus_self_approval",
                    "Somebody other than the person who proposed it must approve it", 403,
                    json!({"approvers": people.len()})));
            }
            self_approved = true; // the only approver there is; recorded as such
        }
        let lines = lines.as_array().cloned().unwrap_or_default();
        let ids: Vec<String> = lines.iter()
            .flat_map(|l| l["inputs"].as_array().cloned().unwrap_or_default())
            .filter_map(|i| i["submission_id"].as_str().map(str::to_string))
            .collect();
        let (revised,): (i64,) = sqlx::query_as(
            "SELECT COUNT(*) FROM consensus_submissions WHERE tenant_id = $1 AND id = ANY($2) AND superseded_by IS NOT NULL",
        )
        .bind(&user.tenant_id)
        .bind(&ids)
        .fetch_one(&mut *tx)
        .await?;
        if revised > 0 {
            // A function changed its number after this was proposed: publishing
            // it would publish a figure that function has since withdrawn.
            return Err(err("consensus_version_stale",
                "A function has revised its adjustment since this was proposed; propose it again", 409,
                json!({"revised": revised})));
        }
        let now = isoformat_date(&today());
        if lines.iter().all(|l| l["end_date"].as_str().map(|e| e < now.as_str()).unwrap_or(true)) {
            return Err(err("consensus_version_expired", "Every period of this consensus has already passed", 409,
                json!({})));
        }
        usable_session(&state.pool, &user.tenant_id, &session_id).await?;
        // One published consensus per forecast: the one it replaces stays, marked.
        let older: Vec<(String,)> = sqlx::query_as(
            "SELECT id FROM consensus_versions WHERE tenant_id = $1 AND session_id = $2 AND status = 'approved' AND id <> $3",
        )
        .bind(&user.tenant_id)
        .bind(&session_id)
        .bind(&version_id)
        .fetch_all(&mut *tx)
        .await?;
        for (old,) in &older {
            sqlx::query("UPDATE consensus_versions SET status = 'superseded' WHERE id = $1 AND tenant_id = $2")
                .bind(old)
                .bind(&user.tenant_id)
                .execute(&mut *tx)
                .await?;
            insert_event(&mut tx, &user.tenant_id, old, Some("approved"), "superseded", &user.user_id, None,
                json!({"superseded_by": version_id})).await?;
            superseded += 1;
        }
    }
    let changed = sqlx::query(
        "UPDATE consensus_versions SET status = $1, decided_by = $2, decided_at = NOW(), decision_comment = $3,
                self_approved = $4
          WHERE id = $5 AND tenant_id = $6 AND status = $7",
    )
    .bind(to)
    .bind(&user.user_id)
    .bind(&comment)
    .bind(self_approved)
    .bind(&version_id)
    .bind(&user.tenant_id)
    .bind(from)
    .execute(&mut *tx)
    .await?
    .rows_affected();
    if changed != 1 {
        return Err(err("consensus_transition_invalid", "This version cannot move to that status from where it is",
            409, json!({"from": status, "to": to})));
    }
    insert_event(&mut tx, &user.tenant_id, &version_id, Some(from), to, &user.user_id, comment.as_deref(),
        if self_approved { json!({"self_approved": true}) } else { json!({}) }).await?;
    tx.commit().await?;

    let (event, mut d) = match decision {
        Decision::Approve => (Event::ConsensusVersionApproved, details(&[("superseded", json!(superseded))])),
        Decision::Reject => (Event::ConsensusVersionRejected, Map::new()),
        Decision::Withdraw => (Event::ConsensusVersionWithdrawn, Map::new()),
    };
    d.insert("plan_name".into(), json!(name));
    d.insert("decision_comment".into(), json!(comment.clone().unwrap_or_default()));
    record_event(&state.pool, &user.tenant_id, &user.user_id, event, Some(&version_id), d).await;
    let mut out = version_detail(&state, &user, &version_id, None, 0, 100).await?;
    if let Value::Object(m) = &mut out {
        m.insert("superseded".into(), json!(superseded));
        m.insert("self_approved".into(), json!(self_approved));
    }
    Ok(ok(out))
}

pub async fn approve(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, Path(id): Path<String>,
    headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    decide(state, actors, id, headers, bytes, Decision::Approve).await
}

pub async fn reject(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, Path(id): Path<String>,
    headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    decide(state, actors, id, headers, bytes, Decision::Reject).await
}

pub async fn withdraw(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, Path(id): Path<String>,
    headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    decide(state, actors, id, headers, bytes, Decision::Withdraw).await
}

// -- Forecast value added ---------------------------------------------------------------

/// One graded SKU-period of the evidence table.
struct Evidence {
    day: i64,
    base: f64,
    actual: f64,
}

/// A graded adjustment point and the labels it can be grouped by.
struct Graded {
    function: String,
    user: String,
    reason: String,
    submission: String,
    point: FvaPoint,
}

fn evidence_in<'a>(ev: &'a HashMap<String, Vec<Evidence>>, sku: &str, start: i64, end: i64) -> &'a [Evidence] {
    let Some(rows) = ev.get(sku) else { return &[] };
    let lo = rows.partition_point(|e| e.day < start);
    let hi = rows.partition_point(|e| e.day <= end);
    &rows[lo..hi]
}

fn group_rows(items: &[Graded], label: &str, key: impl Fn(&Graded) -> String) -> Vec<Value> {
    forecast_value_added_by(items, key, |g| g.point.clone())
        .into_iter()
        .map(|(k, f)| {
            let mut m = Map::new();
            m.insert(label.to_string(), json!(k));
            if let Value::Object(fields) = f.to_json() {
                m.extend(fields);
            }
            Value::Object(m)
        })
        .collect()
}

pub async fn fva(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = caller(&state, &headers, &actors, false).await?;
    let pool = &state.pool;
    get_session(pool, &user.tenant_id, &session_id).await?.ok_or_else(session_not_found)?;

    let subs = sqlx::query(&submission_sql("AND a.superseded_by IS NULL", "ORDER BY a.sku, a.start_date, a.function"))
        .bind(&user.tenant_id)
        .bind(&session_id)
        .fetch_all(pool)
        .await?;
    let versions = sqlx::query(&version_sql(
        "AND v.session_id = $2 AND v.status IN ('approved', 'withdrawn', 'superseded')",
        "ORDER BY v.created_at"))
        .bind(&user.tenant_id)
        .bind(&session_id)
        .fetch_all(pool)
        .await?;
    let mut head = json!({
        "session_id": session_id, "n_submissions": subs.len(), "n_neutral": 0, "n_ungraded": 0,
        "n_graded_submissions": 0, "evidence": null,
        "by_function": [], "by_user": [], "by_reason": [], "by_submission": [], "consensus": [],
    });
    if subs.is_empty() && versions.is_empty() {
        head["status"] = json!("no_adjustments");
        return Ok(ok(head));
    }

    let rows = sqlx::query(
        "SELECT sku, period, base, actual, dataset_id, refreshed_at FROM consensus_evidence
          WHERE tenant_id = $1 AND session_id = $2 ORDER BY sku, period",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .fetch_all(pool)
    .await?;
    if rows.is_empty() {
        head["status"] = json!("no_evidence");
        return Ok(ok(head));
    }
    let mut ev: HashMap<String, Vec<Evidence>> = HashMap::new();
    let (mut first, mut last): (Option<NaiveDate>, Option<NaiveDate>) = (None, None);
    let (mut dataset, mut refreshed): (Option<String>, Option<DateTime<Utc>>) = (None, None);
    for r in &rows {
        let period: NaiveDate = r.try_get("period")?;
        first = Some(first.map_or(period, |f| f.min(period)));
        last = Some(last.map_or(period, |l| l.max(period)));
        dataset = r.try_get("dataset_id")?;
        let at: DateTime<Utc> = r.try_get("refreshed_at")?;
        refreshed = Some(refreshed.map_or(at, |x| x.max(at)));
        ev.entry(r.try_get("sku")?).or_default().push(Evidence {
            day: day_number(&period), base: r.try_get("base")?, actual: r.try_get("actual")?,
        });
    }
    head["evidence"] = json!({
        "rows": rows.len(), "first_period": first.map(|d| isoformat_date(&d)),
        "last_period": last.map(|d| isoformat_date(&d)), "dataset_id": dataset,
        "refreshed_at": refreshed.map(|d| isoformat_utc(&d)),
    });

    // Every current adjustment against the statistical forecast it adjusted.
    let (mut graded, mut per_submission): (Vec<Graded>, Vec<Graded>) = (Vec::new(), Vec::new());
    let (mut neutral, mut ungraded, mut graded_subs) = (0, 0, 0);
    let mut names: HashMap<String, Option<String>> = HashMap::new();
    let mut sub_info: HashMap<String, Value> = HashMap::new();
    for r in &subs {
        let m = row_json(r)?;
        let id = m["id"].as_str().unwrap_or_default().to_string();
        let uid = m["created_by"].as_str().unwrap_or_default().to_string();
        names.insert(uid.clone(), m["created_by_name"].as_str().map(str::to_string));
        sub_info.insert(id.clone(), Value::Object(m.clone()));
        let bp = m["pct_bp"].as_i64().unwrap_or(0);
        if bp == 0 {
            neutral += 1; // "agree with the model": not a bet, nothing to grade
            continue;
        }
        let start = day_number(&date_fromisoformat(m["start_date"].as_str().unwrap_or_default()).unwrap_or(NaiveDate::MIN));
        let end = day_number(&date_fromisoformat(m["end_date"].as_str().unwrap_or_default()).unwrap_or(NaiveDate::MIN));
        let sku = m["sku"].as_str().unwrap_or_default();
        let points = evidence_in(&ev, sku, start, end);
        if points.is_empty() {
            ungraded += 1;
            continue;
        }
        graded_subs += 1;
        for e in points {
            let g = |_: ()| Graded {
                function: m["function"].as_str().unwrap_or_default().to_string(),
                user: uid.clone(),
                reason: m["reason_code"].as_str().unwrap_or_default().to_string(),
                submission: id.clone(),
                point: FvaPoint { base: e.base, pct_bp: bp, actual: e.actual },
            };
            graded.push(g(()));
            per_submission.push(g(()));
        }
    }
    head["n_neutral"] = json!(neutral);
    head["n_ungraded"] = json!(ungraded);
    head["n_graded_submissions"] = json!(graded_subs);
    head["by_function"] = Value::Array(group_rows(&graded, "function", |g| g.function.clone()));
    let mut by_user = group_rows(&graded, "user", |g| g.user.clone());
    for row in &mut by_user {
        let name = row["user"].as_str().and_then(|u| names.get(u)).cloned().flatten();
        row["name"] = json!(name);
    }
    head["by_user"] = Value::Array(by_user);
    head["by_reason"] = Value::Array(group_rows(&graded, "reason", |g| g.reason.clone()));
    let mut by_sub = group_rows(&per_submission, "submission_id", |g| g.submission.clone());
    by_sub.truncate(300);
    for row in &mut by_sub {
        let info = row["submission_id"].as_str().and_then(|i| sub_info.get(i)).cloned();
        row["submission"] = info.unwrap_or(Value::Null);
    }
    head["by_submission"] = Value::Array(by_sub);

    // Each consensus that was published, against the same statistical forecast.
    let mut consensus = Vec::new();
    for v in &versions {
        let m = row_json(v)?;
        let id = m["id"].as_str().unwrap_or_default().to_string();
        let lines: (Value,) = sqlx::query_as("SELECT lines FROM consensus_versions WHERE id = $1 AND tenant_id = $2")
            .bind(&id)
            .bind(&user.tenant_id)
            .fetch_one(pool)
            .await?;
        let mut pts: Vec<FvaPoint> = Vec::new();
        for l in lines.0.as_array().cloned().unwrap_or_default() {
            let (Some(s), Some(e), Some(sku)) = (
                l["start_date"].as_str().and_then(date_fromisoformat),
                l["end_date"].as_str().and_then(date_fromisoformat),
                l["sku"].as_str(),
            ) else { continue };
            let bp = l["pct_bp"].as_i64().unwrap_or(0);
            for e in evidence_in(&ev, sku, day_number(&s), day_number(&e)) {
                pts.push(FvaPoint { base: e.base, pct_bp: bp, actual: e.actual });
            }
        }
        consensus.push(json!({
            "version_id": id, "name": m["name"], "status": m["status"], "decided_at": m["decided_at"],
            "decided_by_name": m["decided_by_name"], "line_count": m["line_count"],
            "fva": forecast_value_added(&pts).to_json(),
        }));
    }
    head["consensus"] = Value::Array(consensus);
    head["status"] = json!("ok");
    Ok(ok(head))
}

// -- Routes -------------------------------------------------------------------------------

pub fn router() -> axum::Router<AppState> {
    use axum::routing::{get, post};
    axum::Router::new()
        .route("/api/v1/consensus/settings", get(get_settings).put(put_settings))
        .route("/api/v1/consensus/versions", get(list_versions))
        .route("/api/v1/consensus/versions/{version_id}", get(get_version))
        .route("/api/v1/consensus/versions/{version_id}/approve", post(approve))
        .route("/api/v1/consensus/versions/{version_id}/reject", post(reject))
        .route("/api/v1/consensus/versions/{version_id}/withdraw", post(withdraw))
        .route("/api/v1/sessions/{session_id}/consensus/submissions", get(list_submissions).post(create_submission))
        .route("/api/v1/sessions/{session_id}/consensus/preview", get(preview))
        .route("/api/v1/sessions/{session_id}/consensus/versions", post(propose))
        .route("/api/v1/sessions/{session_id}/consensus/fva", get(fva))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_are_signed_percentages() {
        assert_eq!(pct_label(1500), "+15.00%");
        assert_eq!(pct_label(-250), "-2.50%");
        assert_eq!(pct_label(0), "+0.00%");
    }

    #[test]
    fn day_numbers_round_trip() {
        let d = NaiveDate::from_ymd_opt(2026, 10, 6).unwrap();
        assert_eq!(from_day_number(day_number(&d)), d);
        assert_eq!(day_number(&NaiveDate::from_ymd_opt(2026, 10, 7).unwrap()) - day_number(&d), 1);
    }

    #[test]
    fn integers_are_bounded_and_exact() {
        let mut e = Errors::default();
        let at = [json!("body"), json!("x")];
        assert_eq!(int_value(&mut e, &at, &json!(5), 0, 10), Some(5));
        assert_eq!(int_value(&mut e, &at, &json!(5.0), 0, 10), Some(5));
        assert_eq!(int_value(&mut e, &at, &json!(5.5), 0, 10), None);
        assert_eq!(int_value(&mut e, &at, &json!("5"), 0, 10), None);
        assert_eq!(int_value(&mut e, &at, &json!(11), 0, 10), None);
        assert_eq!(int_value(&mut e, &at, &json!(-1), 0, 10), None);
        let kinds: Vec<&str> = e.0.iter().map(|x| x["type"].as_str().unwrap()).collect();
        assert_eq!(kinds, ["int_from_float", "int_type", "less_than_equal", "greater_than_equal"]);
    }

    #[test]
    fn evidence_windows_are_inclusive() {
        let mut ev = HashMap::new();
        ev.insert("A".to_string(), (1..=10).map(|d| Evidence { day: d, base: 1.0, actual: 1.0 }).collect::<Vec<_>>());
        assert_eq!(evidence_in(&ev, "A", 3, 5).len(), 3);
        assert_eq!(evidence_in(&ev, "A", 11, 20).len(), 0);
        assert_eq!(evidence_in(&ev, "B", 1, 10).len(), 0);
        assert_eq!(evidence_in(&ev, "A", -5, 100).len(), 10);
    }

    #[test]
    fn the_settings_default_is_shown_but_never_assumed() {
        // `configured: false` is what stops a proposal; the rule shown is only a starting point.
        let e = not_configured();
        assert_eq!(e.body["error_code"], "consensus_rule_not_configured");
    }
}
