//! Scheduled management reports by email. NEW routes: they exist only here, so
//! they have no Python failover (docs/rust-migration.md, "Scheduled reports").
//! Python owns the schema (`backend/scheduled_reports/migrations.py`), the
//! worker pass that claims due schedules and queues the mail through the
//! outbox, and the builder that computes every figure; this module owns the
//! API around them.
//!
//! * `GET    /scheduled-reports/catalog`            what can be chosen, the tenant's zone and the bounds
//! * `GET    /scheduled-reports`                    every schedule, with its recipients and who would be skipped
//! * `POST   /scheduled-reports`                    create (admin / analyst)
//! * `GET    /scheduled-reports/{id}`
//! * `PATCH  /scheduled-reports/{id}`
//! * `DELETE /scheduled-reports/{id}`
//! * `POST   /scheduled-reports/{id}/pause`         / `resume`
//! * `GET    /scheduled-reports/{id}/runs`          the run history, with what the outbox did with each mail
//! * `POST   /scheduled-reports/preview`            renders for the CALLER only, sends nothing, writes nothing
//! * `GET    /scheduled-reports/allowed-recipients` / `POST` / `DELETE .../{email}` (the external allow-list; writes are admin only)
//! * `GET|POST /public/report-unsubscribe`          the signed link in every email (no login)
//!
//! Rules this module keeps:
//! * recipients are users of the caller's tenant (active, not limited to some
//!   warehouses: the report is company-wide) plus external addresses an ADMIN
//!   put on the allow-list; the worker checks both again at send time;
//! * every query carries the caller's tenant, never one from the request;
//! * the preview never mails and never stores: it asks the Python builder (the
//!   same one the worker uses) and returns what it said.

pub mod logic;
pub mod pyclient;

use std::collections::{HashMap, HashSet};

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::postgres::PgRow;
use sqlx::Row;

use crate::activity::{record_event, record_event_with_reason, Event};
use crate::audit::{self, Note};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{isoformat_utc, py_strip};
use crate::routes::ok;
use crate::routes::schedule::tenant_zone_name;
use crate::routes::sessions::{int_query, query_map, query_pairs};
use crate::state::AppState;
use crate::validation::{self, list_field, str_field, Errors, Field, StrRules, NO_STR_RULES};

use logic::{FREQUENCIES, MAX_ALLOWLIST, MAX_NAME, MAX_RECIPIENTS, MAX_SCHEDULES, SECTIONS};

const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'scheduled-reports': a recurring report names company recipients and mails company figures; a person defines it, under their name",
    ),
    is_mcp: false,
};

#[allow(dead_code)] // read by the unit tests that tie it to backend/audit/catalog.py
/// The templates this module records in the audit trail. A unit test checks the
/// list against `RUST_ONLY` in `backend/audit/catalog.py`.
pub const AUDITED: [(&str, &str); 7] = [
    ("POST", "/scheduled-reports"),
    ("PATCH", "/scheduled-reports/{schedule_id}"),
    ("DELETE", "/scheduled-reports/{schedule_id}"),
    ("POST", "/scheduled-reports/{schedule_id}/pause"),
    ("POST", "/scheduled-reports/{schedule_id}/resume"),
    ("POST", "/scheduled-reports/allowed-recipients"),
    ("DELETE", "/scheduled-reports/allowed-recipients/{email}"),
];

pub fn router() -> axum::Router<AppState> {
    use axum::routing::{delete, get, post};
    axum::Router::new()
        .route("/api/v1/scheduled-reports", get(list).post(create))
        .route("/api/v1/scheduled-reports/catalog", get(catalog))
        .route("/api/v1/scheduled-reports/preview", post(preview))
        .route("/api/v1/scheduled-reports/allowed-recipients", get(list_allowed).post(add_allowed))
        .route("/api/v1/scheduled-reports/allowed-recipients/{email}", delete(remove_allowed))
        .route("/api/v1/scheduled-reports/{schedule_id}", get(get_one).patch(update).delete(remove))
        .route("/api/v1/scheduled-reports/{schedule_id}/pause", post(pause))
        .route("/api/v1/scheduled-reports/{schedule_id}/resume", post(resume))
        .route("/api/v1/scheduled-reports/{schedule_id}/runs", get(runs))
        .route("/api/v1/public/report-unsubscribe", get(unsubscribe_info).post(unsubscribe))
}

fn app_err(code: &str, msg: &str, status: u16, params: Value) -> ApiError {
    ApiError::app(code, msg, status, params)
}

fn not_found() -> ApiError {
    app_err("scheduled_report_not_found", "Scheduled report not found", 404, json!({}))
}

fn iso(v: Option<DateTime<Utc>>) -> Value {
    v.map(|d| Value::String(isoformat_utc(&d))).unwrap_or(Value::Null)
}

/// Who may call: admins and analysts. `write` adds the expired-trial guard.
async fn staff(state: &AppState, actors: &RequestActors, headers: &HeaderMap, write: bool) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    if write {
        auth::require_analyst_or_above(state, &user).await?;
    } else {
        auth::require_role(&user, &["admin", "analyst"])?;
    }
    Ok(user)
}

async fn admin(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    auth::require_analyst_or_above(state, &user).await?;
    Ok(user)
}

fn content_type(headers: &HeaderMap) -> Option<&str> {
    headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok())
}

// ── Rows ─────────────────────────────────────────────────────────────────────

struct Sched {
    id: String,
    name: String,
    sections: Value,
    frequency: String,
    weekday: Option<i32>,
    day_of_month: Option<i32>,
    hour: i32,
    cron_expr: String,
    anchored_tz: String,
    enabled: bool,
    paused_reason: Option<String>,
    paused_at: Option<DateTime<Utc>>,
    consecutive_failures: i32,
    next_run_at: DateTime<Utc>,
    last_run_at: Option<DateTime<Utc>>,
    last_status: Option<String>,
    last_error: Option<String>,
    created_by: String,
    created_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
}

impl Sched {
    fn from_row(r: &PgRow) -> Result<Sched, sqlx::Error> {
        Ok(Sched {
            id: r.try_get("id")?,
            name: r.try_get("name")?,
            sections: r.try_get("sections")?,
            frequency: r.try_get("frequency")?,
            weekday: r.try_get("weekday")?,
            day_of_month: r.try_get("day_of_month")?,
            hour: r.try_get("hour")?,
            cron_expr: r.try_get("cron_expr")?,
            anchored_tz: r.try_get("anchored_tz")?,
            enabled: r.try_get("enabled")?,
            paused_reason: r.try_get("paused_reason")?,
            paused_at: r.try_get("paused_at")?,
            consecutive_failures: r.try_get("consecutive_failures")?,
            next_run_at: r.try_get("next_run_at")?,
            last_run_at: r.try_get("last_run_at")?,
            last_status: r.try_get("last_status")?,
            last_error: r.try_get("last_error")?,
            created_by: r.try_get("created_by")?,
            created_at: r.try_get("created_at")?,
            updated_at: r.try_get("updated_at")?,
        })
    }

    fn json(&self, recipients: Vec<Value>) -> Value {
        json!({
            "id": self.id, "name": self.name, "sections": self.sections, "frequency": self.frequency,
            "weekday": self.weekday, "day_of_month": self.day_of_month, "hour": self.hour,
            "timezone": self.anchored_tz,
            "enabled": self.enabled, "paused_reason": self.paused_reason, "paused_at": iso(self.paused_at),
            "consecutive_failures": self.consecutive_failures,
            "next_run_at": isoformat_utc(&self.next_run_at),
            "last_run_at": iso(self.last_run_at), "last_status": self.last_status, "last_error": self.last_error,
            "created_by": self.created_by,
            "created_at": isoformat_utc(&self.created_at), "updated_at": isoformat_utc(&self.updated_at),
            "recipients": recipients,
        })
    }
}

async fn load_schedule(pool: &sqlx::PgPool, tenant_id: &str, id: &str) -> Result<Sched, ApiError> {
    let row = sqlx::query("SELECT * FROM report_schedules WHERE id = $1 AND tenant_id = $2")
        .bind(id)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    match row {
        Some(r) => Ok(Sched::from_row(&r)?),
        None => Err(not_found()),
    }
}

/// The recipients of the given schedules, each with whether the worker would
/// skip it right now (and why): the same rule as `service.resolve_recipients`.
async fn load_recipients(pool: &sqlx::PgPool, tenant_id: &str, ids: &[String]) -> Result<HashMap<String, Vec<Value>>, ApiError> {
    let rows = sqlx::query(
        "SELECT r.schedule_id, r.id, r.kind, r.user_id, r.email, r.unsubscribed_at, \
                u.email AS user_email, u.full_name AS user_name, u.status AS user_status, \
                u.tenant_id AS user_tenant, \
                (u.warehouse_scope IS NOT NULL AND jsonb_typeof(u.warehouse_scope) <> 'null') AS user_scoped, \
                (a.email IS NOT NULL) AS allowed \
           FROM report_schedule_recipients r \
           LEFT JOIN users u ON r.kind = 'user' AND u.id = r.user_id \
           LEFT JOIN report_external_allowlist a \
                  ON r.kind = 'external' AND a.tenant_id = r.tenant_id AND a.email = r.email \
          WHERE r.tenant_id = $1 AND r.schedule_id = ANY($2) \
          ORDER BY r.created_at, r.id",
    )
    .bind(tenant_id)
    .bind(ids)
    .fetch_all(pool)
    .await?;
    let mut out: HashMap<String, Vec<Value>> = HashMap::new();
    for r in &rows {
        let kind: String = r.try_get("kind")?;
        let user_email: Option<String> = r.try_get("user_email")?;
        let user_tenant: Option<String> = r.try_get("user_tenant")?;
        let user_status: Option<String> = r.try_get("user_status")?;
        let facts = logic::RecipientFacts {
            kind: &kind,
            unsubscribed: r.try_get::<Option<DateTime<Utc>>, _>("unsubscribed_at")?.is_some(),
            user_found: user_email.is_some() && user_tenant.as_deref() == Some(tenant_id),
            user_active: user_status.as_deref() == Some("active"),
            user_scoped: r.try_get::<Option<bool>, _>("user_scoped")?.unwrap_or(false),
            user_has_email: user_email.as_deref().map(|e| !e.trim().is_empty()).unwrap_or(false),
            external_allowed: r.try_get::<Option<bool>, _>("allowed")?.unwrap_or(false),
        };
        let reason = logic::skip_reason(&facts);
        let schedule_id: String = r.try_get("schedule_id")?;
        let shown_email = if kind == "user" { user_email.clone() } else { r.try_get("email")? };
        out.entry(schedule_id).or_default().push(json!({
            "id": r.try_get::<String, _>("id")?,
            "kind": kind,
            "user_id": r.try_get::<Option<String>, _>("user_id")?,
            "email": shown_email,
            "full_name": r.try_get::<Option<String>, _>("user_name")?,
            "unsubscribed": facts.unsubscribed,
            "would_send": reason.is_none(),
            "skip_reason": reason,
        }));
    }
    Ok(out)
}

async fn schedule_json(pool: &sqlx::PgPool, tenant_id: &str, s: &Sched) -> Result<Value, ApiError> {
    let mut rec = load_recipients(pool, tenant_id, std::slice::from_ref(&s.id)).await?;
    Ok(s.json(rec.remove(&s.id).unwrap_or_default()))
}

// ── Request model ────────────────────────────────────────────────────────────

const NAME_RULES: StrRules = StrRules { min_length: Some(1), max_length: Some(MAX_NAME), pattern: None };

struct Parsed {
    name: Option<String>,
    sections: Option<Vec<String>>,
    frequency: Option<String>,
    weekday: Field<i64>,
    day_of_month: Field<i64>,
    hour: Field<i64>,
    user_ids: Option<Vec<String>>,
    externals: Option<Vec<String>>,
}

/// An integer field with bounds (JSON integers only; pydantic would also take
/// numeric strings, which no client of this API sends).
fn int_field(errs: &mut Errors, obj: &Map<String, Value>, name: &str, ge: i64, le: i64) -> Field<i64> {
    let at = validation::loc(&[json!("body")], name);
    match obj.get(name) {
        None => Field::Absent,
        Some(Value::Null) => Field::Null,
        Some(v) => match v.as_i64() {
            Some(n) if n < ge => {
                errs.push("greater_than_equal", &at, format!("Input should be greater than or equal to {ge}"), v, Some(json!({"ge": ge})));
                Field::Absent
            }
            Some(n) if n > le => {
                errs.push("less_than_equal", &at, format!("Input should be less than or equal to {le}"), v, Some(json!({"le": le})));
                Field::Absent
            }
            Some(n) => Field::Value(n),
            None => {
                errs.push("int_type", &at, "Input should be a valid integer".into(), v, None);
                Field::Absent
            }
        },
    }
}

fn string_list(errs: &mut Errors, obj: &Map<String, Value>, name: &str, max: usize) -> Option<Vec<String>> {
    let at = validation::loc(&[json!("body")], name);
    match obj.get(name) {
        None | Some(Value::Null) => None,
        Some(Value::Array(items)) => {
            if items.len() > max {
                errs.push("too_long", &at, format!("List should have at most {max} items after validation, not {}", items.len()),
                    &Value::Array(items.clone()), Some(json!({"field_type": "List", "max_length": max, "actual_length": items.len()})));
                return None;
            }
            let mut out = Vec::new();
            for (i, item) in items.iter().enumerate() {
                match item {
                    Value::String(s) => out.push(s.clone()),
                    other => {
                        let mut l = at.clone();
                        l.push(json!(i));
                        errs.push("string_type", &l, "Input should be a valid string".into(), other, None);
                    }
                }
            }
            Some(out)
        }
        Some(other) => {
            errs.push("list_type", &at, "Input should be a valid list".into(), other, None);
            None
        }
    }
}

fn parse_body(obj: &Map<String, Value>, create: bool) -> Result<Parsed, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let name = match str_field(&mut errs, obj, &p, "name", create, false, &NAME_RULES) {
        Field::Value(v) if py_strip(&v).is_empty() => {
            errs.push("string_too_short", &validation::loc(&p, "name"), "String should have at least 1 character".into(),
                &json!(v), Some(json!({"min_length": 1})));
            None
        }
        Field::Value(v) => Some(py_strip(&v).to_string()),
        _ => None,
    };
    let sections = match obj.get("sections") {
        None if create => {
            errs.push("missing", &validation::loc(&p, "sections"), "Field required".into(), &Value::Object(obj.clone()), None);
            None
        }
        None => None,
        Some(_) => list_field(&mut errs, obj, &p, "sections", 1, SECTIONS.len().max(8)).map(|items| {
            items.iter().filter_map(|v| v.as_str().map(str::to_string)).collect::<Vec<_>>()
        }),
    };
    if let Some(Value::Array(items)) = obj.get("sections") {
        if items.iter().any(|v| !v.is_string()) {
            errs.push("string_type", &validation::loc(&p, "sections"), "Input should be a valid string".into(),
                &Value::Array(items.clone()), None);
        }
    }
    let frequency = match str_field(&mut errs, obj, &p, "frequency", create, false, &NO_STR_RULES) {
        Field::Value(v) if !FREQUENCIES.contains(&v.as_str()) => {
            errs.push("literal_error", &validation::loc(&p, "frequency"), "Input should be 'weekly' or 'monthly'".into(),
                &json!(v), Some(json!({"expected": "'weekly' or 'monthly'"})));
            None
        }
        Field::Value(v) => Some(v),
        _ => None,
    };
    let weekday = int_field(&mut errs, obj, "weekday", 1, 7);
    let day_of_month = int_field(&mut errs, obj, "day_of_month", 1, 28);
    let hour = int_field(&mut errs, obj, "hour", 0, 23);
    if create && matches!(hour, Field::Absent) && !errs.0.iter().any(|e| e["loc"][1] == "hour") {
        errs.push("missing", &validation::loc(&p, "hour"), "Field required".into(), &Value::Object(obj.clone()), None);
    }
    let user_ids = string_list(&mut errs, obj, "user_ids", MAX_RECIPIENTS);
    let externals = string_list(&mut errs, obj, "external_emails", MAX_RECIPIENTS);
    errs.into_result()?;
    Ok(Parsed { name, sections, frequency, weekday, day_of_month, hour, user_ids, externals })
}

fn value_error(field: &str, msg: &str, input: &Value) -> ApiError {
    let mut errs = Errors::default();
    errs.push("value_error", &validation::loc(&[json!("body")], field), format!("Value error, {msg}"), input,
        Some(json!({"error": msg})));
    ApiError::validation(errs.0)
}

/// Section codes in request order, unknown ones refused by name, duplicates dropped.
fn clean_sections(raw: &[String]) -> Result<Vec<String>, ApiError> {
    let mut out: Vec<String> = Vec::new();
    for s in raw {
        if !SECTIONS.contains(&s.as_str()) {
            return Err(app_err("scheduled_report_section_unknown", "Unknown report section", 422, json!({"section": s})));
        }
        if !out.contains(s) {
            out.push(s.clone());
        }
    }
    if out.is_empty() {
        return Err(value_error("sections", "choose at least one section", &json!(raw)));
    }
    Ok(out)
}

/// Each id must be an ACTIVE user of the caller's tenant and not limited to some
/// warehouses (the report is company-wide). Deduplicated, order kept.
async fn check_users(pool: &sqlx::PgPool, tenant_id: &str, ids: &[String]) -> Result<Vec<String>, ApiError> {
    let mut unique: Vec<String> = Vec::new();
    for id in ids {
        if !unique.contains(id) {
            unique.push(id.clone());
        }
    }
    let rows = sqlx::query(
        "SELECT id, status, (warehouse_scope IS NOT NULL AND jsonb_typeof(warehouse_scope) <> 'null') AS scoped \
           FROM users WHERE tenant_id = $1 AND id = ANY($2)",
    )
    .bind(tenant_id)
    .bind(&unique)
    .fetch_all(pool)
    .await?;
    let mut by_id: HashMap<String, (String, bool)> = HashMap::new();
    for r in &rows {
        by_id.insert(r.try_get("id")?, (r.try_get("status")?, r.try_get("scoped")?));
    }
    for id in &unique {
        match by_id.get(id) {
            None => {
                return Err(app_err("scheduled_report_recipient_not_in_tenant",
                    "That person is not a user of this account", 422, json!({"user_id": id})));
            }
            Some((status, _)) if status != "active" => {
                return Err(app_err("scheduled_report_recipient_not_in_tenant",
                    "That person is not an active user of this account", 422, json!({"user_id": id})));
            }
            Some((_, true)) => {
                return Err(app_err("scheduled_report_recipient_scoped",
                    "A user limited to some warehouses cannot receive a company-wide report", 422, json!({"user_id": id})));
            }
            _ => {}
        }
    }
    Ok(unique)
}

/// Each address must be well formed and on the tenant's allow-list.
async fn check_externals(pool: &sqlx::PgPool, tenant_id: &str, raw: &[String]) -> Result<Vec<String>, ApiError> {
    let mut clean: Vec<String> = Vec::new();
    for r in raw {
        let Some(e) = logic::normalize_email(r) else {
            return Err(app_err("scheduled_report_external_invalid", "That is not a valid email address", 422, json!({"email": r})));
        };
        if !clean.contains(&e) {
            clean.push(e);
        }
    }
    let rows: Vec<(String,)> = sqlx::query_as("SELECT email FROM report_external_allowlist WHERE tenant_id = $1 AND email = ANY($2)")
        .bind(tenant_id)
        .bind(&clean)
        .fetch_all(pool)
        .await?;
    let allowed: HashSet<String> = rows.into_iter().map(|r| r.0).collect();
    for e in &clean {
        if !allowed.contains(e) {
            return Err(app_err("scheduled_report_external_not_allowed",
                "An administrator has not allowed that address to receive reports", 422, json!({"email": e})));
        }
    }
    Ok(clean)
}

/// The next firing from now, in the tenant's CURRENT zone; also returns the
/// zone name the run was computed in (stored as `anchored_tz`).
async fn compute_next(state: &AppState, tenant_id: &str, cron_expr: &str) -> Result<(String, DateTime<Utc>), ApiError> {
    let zone = tenant_zone_name(state, tenant_id).await?;
    match logic::next_run_after(cron_expr, &zone, Utc::now()) {
        Ok(next) => Ok((zone, next)),
        Err(e) => {
            tracing::error!(tenant = tenant_id, cron = cron_expr, zone = %zone, "[scheduled-reports] next run unavailable: {e}");
            Err(ApiError::internal())
        }
    }
}

fn change_summary(s: &Sched) -> Value {
    json!({"name": s.name, "sections": s.sections, "frequency": s.frequency, "weekday": s.weekday,
           "day_of_month": s.day_of_month, "hour": s.hour, "enabled": s.enabled})
}

// ── Catalogue ────────────────────────────────────────────────────────────────

pub async fn catalog(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, false).await?;
    let zone = tenant_zone_name(&state, &user.tenant_id).await?;
    Ok(ok(json!({
        "sections": SECTIONS, "frequencies": FREQUENCIES, "timezone": zone,
        "limits": {"schedules": MAX_SCHEDULES, "recipients": MAX_RECIPIENTS, "allowed_external": MAX_ALLOWLIST,
                   "name_length": MAX_NAME},
    })))
}

// ── Schedules ────────────────────────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, false).await?;
    let rows = sqlx::query("SELECT * FROM report_schedules WHERE tenant_id = $1 ORDER BY created_at DESC, id")
        .bind(&user.tenant_id)
        .fetch_all(&state.pool)
        .await?;
    let mut schedules = Vec::with_capacity(rows.len());
    for r in &rows {
        schedules.push(Sched::from_row(r)?);
    }
    let ids: Vec<String> = schedules.iter().map(|s| s.id.clone()).collect();
    let mut recipients = load_recipients(&state.pool, &user.tenant_id, &ids).await?;
    let items: Vec<Value> = schedules.iter().map(|s| s.json(recipients.remove(&s.id).unwrap_or_default())).collect();
    Ok(ok(json!({"items": items})))
}

pub async fn get_one(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(schedule_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, false).await?;
    let s = load_schedule(&state.pool, &user.tenant_id, &schedule_id).await?;
    Ok(ok(schedule_json(&state.pool, &user.tenant_id, &s).await?))
}

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user = staff(&state, &actors, &headers, true).await?;
    let body = validation::read_body(content_type(&headers), &bytes)?;
    let obj = validation::body_object(&body)?;
    let parsed = parse_body(&obj, true)?;
    let tenant = &user.tenant_id;
    let frequency = parsed.frequency.clone().expect("required");
    let weekday = match (&parsed.weekday, frequency.as_str()) {
        (Field::Value(w), "weekly") => Some(*w),
        (Field::Value(_), _) => return Err(value_error("weekday", "weekday is only for a weekly report", &obj["weekday"])),
        (_, "weekly") => return Err(value_error("weekday", "a weekly report needs a weekday from 1 to 7", &Value::Null)),
        _ => None,
    };
    let day_of_month = match (&parsed.day_of_month, frequency.as_str()) {
        (Field::Value(d), "monthly") => Some(*d),
        (Field::Value(_), _) => return Err(value_error("day_of_month", "day_of_month is only for a monthly report", &obj["day_of_month"])),
        (_, "monthly") => return Err(value_error("day_of_month", "a monthly report needs a day of the month from 1 to 28", &Value::Null)),
        _ => None,
    };
    let hour = match parsed.hour { Field::Value(h) => h, _ => return Err(value_error("hour", "hour is required", &Value::Null)) };
    let sections = clean_sections(parsed.sections.as_deref().unwrap_or(&[]))?;
    let users = check_users(&state.pool, tenant, parsed.user_ids.as_deref().unwrap_or(&[])).await?;
    let externals = check_externals(&state.pool, tenant, parsed.externals.as_deref().unwrap_or(&[])).await?;
    if users.len() + externals.len() > MAX_RECIPIENTS {
        return Err(app_err("scheduled_report_too_many_recipients", "Too many recipients", 422, json!({"max": MAX_RECIPIENTS})));
    }
    if users.is_empty() && externals.is_empty() {
        return Err(app_err("scheduled_report_no_recipients", "A report needs at least one recipient", 422, json!({})));
    }
    let cron_expr = logic::cron_for(&frequency, weekday, day_of_month, hour).map_err(|e| value_error("frequency", &e, &Value::Null))?;
    let (zone, next) = compute_next(&state, tenant, &cron_expr).await?;

    // The ceiling is counted and the row inserted under one tenant-scoped
    // advisory lock, so two simultaneous creates cannot both pass it.
    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))").bind(format!("report_schedules:{tenant}")).execute(&mut *tx).await?;
    let (count,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM report_schedules WHERE tenant_id = $1")
        .bind(tenant).fetch_one(&mut *tx).await?;
    if count >= MAX_SCHEDULES {
        return Err(app_err("scheduled_report_limit_reached", "This account already has the maximum number of scheduled reports",
            409, json!({"max": MAX_SCHEDULES})));
    }
    let (id,): (String,) = sqlx::query_as(
        "INSERT INTO report_schedules (tenant_id, name, sections, frequency, weekday, day_of_month, hour, cron_expr, \
                                       anchored_tz, next_run_at, created_by) \
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id",
    )
    .bind(tenant)
    .bind(parsed.name.as_deref().expect("required"))
    .bind(json!(sections))
    .bind(&frequency)
    .bind(weekday.map(|v| v as i32))
    .bind(day_of_month.map(|v| v as i32))
    .bind(hour as i32)
    .bind(&cron_expr)
    .bind(&zone)
    .bind(next)
    .bind(&user.user_id)
    .fetch_one(&mut *tx)
    .await?;
    for uid in &users {
        sqlx::query("INSERT INTO report_schedule_recipients (schedule_id, tenant_id, kind, user_id) VALUES ($1, $2, 'user', $3)")
            .bind(&id).bind(tenant).bind(uid).execute(&mut *tx).await?;
    }
    for email in &externals {
        sqlx::query("INSERT INTO report_schedule_recipients (schedule_id, tenant_id, kind, email) VALUES ($1, $2, 'external', $3)")
            .bind(&id).bind(tenant).bind(email).execute(&mut *tx).await?;
    }
    tx.commit().await?;
    let s = load_schedule(&state.pool, tenant, &id).await?;
    let out = schedule_json(&state.pool, tenant, &s).await?;
    let note = Note { target_id: Some(id.clone()), label: Some(s.name.clone()), before: None, after: Some(change_summary(&s)) };
    audit::record(&state, &actors, "POST", "/scheduled-reports", None, note, 201).await;
    Ok((StatusCode::CREATED, ok(out)))
}

pub async fn update(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(schedule_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, true).await?;
    let body = validation::read_body(content_type(&headers), &bytes)?;
    let obj = validation::body_object(&body)?;
    let parsed = parse_body(&obj, false)?;
    let tenant = &user.tenant_id;
    let current = load_schedule(&state.pool, tenant, &schedule_id).await?;

    let frequency = parsed.frequency.clone().unwrap_or_else(|| current.frequency.clone());
    let weekday = match frequency.as_str() {
        "weekly" => match &parsed.weekday {
            Field::Value(w) => Some(*w),
            _ if current.frequency == "weekly" => current.weekday.map(i64::from),
            _ => return Err(value_error("weekday", "a weekly report needs a weekday from 1 to 7", &Value::Null)),
        },
        _ => {
            if let Field::Value(_) = parsed.weekday {
                return Err(value_error("weekday", "weekday is only for a weekly report", &obj["weekday"]));
            }
            None
        }
    };
    let day_of_month = match frequency.as_str() {
        "monthly" => match &parsed.day_of_month {
            Field::Value(d) => Some(*d),
            _ if current.frequency == "monthly" => current.day_of_month.map(i64::from),
            _ => return Err(value_error("day_of_month", "a monthly report needs a day of the month from 1 to 28", &Value::Null)),
        },
        _ => {
            if let Field::Value(_) = parsed.day_of_month {
                return Err(value_error("day_of_month", "day_of_month is only for a monthly report", &obj["day_of_month"]));
            }
            None
        }
    };
    let hour = match parsed.hour { Field::Value(h) => h, _ => i64::from(current.hour) };
    let name = parsed.name.clone().unwrap_or_else(|| current.name.clone());
    let sections = match &parsed.sections {
        Some(s) => clean_sections(s)?,
        None => current.sections.as_array().map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect()).unwrap_or_default(),
    };
    let new_users = match &parsed.user_ids { Some(ids) => Some(check_users(&state.pool, tenant, ids).await?), None => None };
    let new_externals = match &parsed.externals { Some(e) => Some(check_externals(&state.pool, tenant, e).await?), None => None };
    let cron_expr = logic::cron_for(&frequency, weekday, day_of_month, hour).map_err(|e| value_error("frequency", &e, &Value::Null))?;
    let zone_now = tenant_zone_name(&state, tenant).await?;
    let timing_changed = cron_expr != current.cron_expr || zone_now != current.anchored_tz;
    let (zone, next) = if timing_changed {
        compute_next(&state, tenant, &cron_expr).await?
    } else {
        (current.anchored_tz.clone(), current.next_run_at)
    };

    let mut tx = state.pool.begin().await?;
    // Serialise edits of one schedule so two replacements of the recipient set cannot interleave.
    sqlx::query("SELECT id FROM report_schedules WHERE id = $1 AND tenant_id = $2 FOR UPDATE")
        .bind(&schedule_id).bind(tenant).fetch_optional(&mut *tx).await?.ok_or_else(not_found)?;
    sqlx::query(
        "UPDATE report_schedules SET name = $3, sections = $4, frequency = $5, weekday = $6, day_of_month = $7, \
                hour = $8, cron_expr = $9, anchored_tz = $10, next_run_at = $11, updated_at = NOW() \
          WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&schedule_id).bind(tenant).bind(&name).bind(json!(sections)).bind(&frequency)
    .bind(weekday.map(|v| v as i32)).bind(day_of_month.map(|v| v as i32)).bind(hour as i32)
    .bind(&cron_expr).bind(&zone).bind(next)
    .execute(&mut *tx).await?;
    // Replace a kind's recipients, keeping the rows that stay (an unsubscribe is kept).
    if let Some(users) = &new_users {
        sqlx::query("DELETE FROM report_schedule_recipients WHERE schedule_id = $1 AND tenant_id = $2 AND kind = 'user' AND NOT (user_id = ANY($3))")
            .bind(&schedule_id).bind(tenant).bind(users).execute(&mut *tx).await?;
        for uid in users {
            sqlx::query("INSERT INTO report_schedule_recipients (schedule_id, tenant_id, kind, user_id) VALUES ($1, $2, 'user', $3) \
                         ON CONFLICT (schedule_id, user_id) WHERE user_id IS NOT NULL DO NOTHING")
                .bind(&schedule_id).bind(tenant).bind(uid).execute(&mut *tx).await?;
        }
    }
    if let Some(externals) = &new_externals {
        sqlx::query("DELETE FROM report_schedule_recipients WHERE schedule_id = $1 AND tenant_id = $2 AND kind = 'external' AND NOT (email = ANY($3))")
            .bind(&schedule_id).bind(tenant).bind(externals).execute(&mut *tx).await?;
        for e in externals {
            sqlx::query("INSERT INTO report_schedule_recipients (schedule_id, tenant_id, kind, email) VALUES ($1, $2, 'external', $3) \
                         ON CONFLICT (schedule_id, email) WHERE email IS NOT NULL DO NOTHING")
                .bind(&schedule_id).bind(tenant).bind(e).execute(&mut *tx).await?;
        }
    }
    let (total,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM report_schedule_recipients WHERE schedule_id = $1 AND tenant_id = $2")
        .bind(&schedule_id).bind(tenant).fetch_one(&mut *tx).await?;
    if total == 0 {
        return Err(app_err("scheduled_report_no_recipients", "A report needs at least one recipient", 422, json!({})));
    }
    if total as usize > MAX_RECIPIENTS {
        return Err(app_err("scheduled_report_too_many_recipients", "Too many recipients", 422, json!({"max": MAX_RECIPIENTS})));
    }
    tx.commit().await?;
    let updated = load_schedule(&state.pool, tenant, &schedule_id).await?;
    let out = schedule_json(&state.pool, tenant, &updated).await?;
    let note = Note { target_id: None, label: Some(updated.name.clone()), before: Some(change_summary(&current)),
                      after: Some(change_summary(&updated)) };
    audit::record(&state, &actors, "PATCH", "/scheduled-reports/{schedule_id}", Some(&schedule_id), note, 200).await;
    Ok(ok(out))
}

pub async fn remove(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(schedule_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, true).await?;
    let tenant = &user.tenant_id;
    let current = load_schedule(&state.pool, tenant, &schedule_id).await?;
    let mut tx = state.pool.begin().await?;
    sqlx::query("DELETE FROM report_schedule_runs WHERE schedule_id = $1 AND tenant_id = $2").bind(&schedule_id).bind(tenant).execute(&mut *tx).await?;
    sqlx::query("DELETE FROM report_schedule_recipients WHERE schedule_id = $1 AND tenant_id = $2").bind(&schedule_id).bind(tenant).execute(&mut *tx).await?;
    sqlx::query("DELETE FROM report_schedules WHERE id = $1 AND tenant_id = $2").bind(&schedule_id).bind(tenant).execute(&mut *tx).await?;
    tx.commit().await?;
    let note = Note { target_id: None, label: Some(current.name.clone()), before: Some(change_summary(&current)), after: None };
    audit::record(&state, &actors, "DELETE", "/scheduled-reports/{schedule_id}", Some(&schedule_id), note, 200).await;
    Ok(ok(json!({"deleted": true})))
}

pub async fn pause(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(schedule_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, true).await?;
    let tenant = &user.tenant_id;
    let current = load_schedule(&state.pool, tenant, &schedule_id).await?;
    // Idempotent: pausing a paused schedule keeps the ORIGINAL reason and time.
    sqlx::query("UPDATE report_schedules SET enabled = FALSE, paused_reason = 'user', paused_at = NOW(), updated_at = NOW() \
                  WHERE id = $1 AND tenant_id = $2 AND enabled")
        .bind(&schedule_id).bind(tenant).execute(&state.pool).await?;
    let s = load_schedule(&state.pool, tenant, &schedule_id).await?;
    let note = Note { target_id: None, label: Some(s.name.clone()), before: Some(json!({"enabled": current.enabled})),
                      after: Some(json!({"enabled": false})) };
    audit::record(&state, &actors, "POST", "/scheduled-reports/{schedule_id}/pause", Some(&schedule_id), note, 200).await;
    Ok(ok(schedule_json(&state.pool, tenant, &s).await?))
}

pub async fn resume(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(schedule_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, true).await?;
    let tenant = &user.tenant_id;
    let current = load_schedule(&state.pool, tenant, &schedule_id).await?;
    if current.enabled {
        return Ok(ok(schedule_json(&state.pool, tenant, &current).await?));
    }
    let (active,): (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM report_schedule_recipients WHERE schedule_id = $1 AND tenant_id = $2 AND unsubscribed_at IS NULL")
        .bind(&schedule_id).bind(tenant).fetch_one(&state.pool).await?;
    if active == 0 {
        return Err(app_err("scheduled_report_no_recipients",
            "Everyone unsubscribed or was removed; add a recipient before resuming", 409, json!({})));
    }
    // From NOW: a resumed report never "catches up" on the weeks it was paused.
    let (zone, next) = compute_next(&state, tenant, &current.cron_expr).await?;
    sqlx::query("UPDATE report_schedules SET enabled = TRUE, paused_reason = NULL, paused_at = NULL, consecutive_failures = 0, \
                        last_error = NULL, anchored_tz = $3, next_run_at = $4, updated_at = NOW() \
                  WHERE id = $1 AND tenant_id = $2")
        .bind(&schedule_id).bind(tenant).bind(&zone).bind(next).execute(&state.pool).await?;
    let s = load_schedule(&state.pool, tenant, &schedule_id).await?;
    let note = Note { target_id: None, label: Some(s.name.clone()), before: Some(json!({"enabled": false, "paused_reason": current.paused_reason})),
                      after: Some(json!({"enabled": true})) };
    audit::record(&state, &actors, "POST", "/scheduled-reports/{schedule_id}/resume", Some(&schedule_id), note, 200).await;
    Ok(ok(schedule_json(&state.pool, tenant, &s).await?))
}

// ── Run history ──────────────────────────────────────────────────────────────

pub async fn runs(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(schedule_id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, false).await?;
    let tenant = &user.tenant_id;
    let mut errs = Errors::default();
    let q = query_map(&query_pairs(&uri));
    let limit = int_query(&mut errs, &q, "limit", Some(1), Some(100)).unwrap_or(20);
    errs.into_result()?;
    load_schedule(&state.pool, tenant, &schedule_id).await?;
    let rows = sqlx::query(
        "SELECT id, period_key, due_at, status, attempts, recipients_queued, recipients_skipped, error, started_at, finished_at \
           FROM report_schedule_runs WHERE tenant_id = $1 AND schedule_id = $2 ORDER BY due_at DESC, started_at DESC LIMIT $3",
    )
    .bind(tenant).bind(&schedule_id).bind(limit).fetch_all(&state.pool).await?;
    let ids: Vec<String> = rows.iter().filter_map(|r| r.try_get::<String, _>("id").ok()).collect();
    // What the outbox did with each run's mails: the run says "queued", this says whether they LEFT.
    let delivery_rows = sqlx::query(
        "SELECT split_part(dedupe_key, ':', 2) AS run_id, status, COUNT(*) AS n FROM outbound_messages \
          WHERE tenant_id = $1 AND kind = 'scheduled_report' AND split_part(dedupe_key, ':', 2) = ANY($2) \
          GROUP BY 1, 2",
    )
    .bind(tenant).bind(&ids).fetch_all(&state.pool).await?;
    let mut delivery: HashMap<String, Map<String, Value>> = HashMap::new();
    for r in &delivery_rows {
        let run_id: String = r.try_get("run_id")?;
        let status: String = r.try_get("status")?;
        let n: i64 = r.try_get("n")?;
        delivery.entry(run_id).or_default().insert(status, json!(n));
    }
    let mut items = Vec::with_capacity(rows.len());
    for r in &rows {
        let id: String = r.try_get("id")?;
        let d = delivery.remove(&id).unwrap_or_default();
        let count = |k: &str| d.get(k).and_then(Value::as_i64).unwrap_or(0);
        items.push(json!({
            "id": id,
            "period": r.try_get::<String, _>("period_key")?,
            "due_at": isoformat_utc(&r.try_get::<DateTime<Utc>, _>("due_at")?),
            "status": r.try_get::<String, _>("status")?,
            "attempts": r.try_get::<i32, _>("attempts")?,
            "recipients_queued": r.try_get::<i32, _>("recipients_queued")?,
            "recipients_skipped": r.try_get::<Value, _>("recipients_skipped")?,
            "error": r.try_get::<Option<String>, _>("error")?,
            "started_at": isoformat_utc(&r.try_get::<DateTime<Utc>, _>("started_at")?),
            "finished_at": iso(r.try_get("finished_at")?),
            "delivery": {"sent": count("sent"), "pending": count("pending"), "failed": count("failed"), "abandoned": count("abandoned")},
        }));
    }
    Ok(ok(json!({"items": items})))
}

// ── Preview ──────────────────────────────────────────────────────────────────

pub async fn preview(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    // Reads company figures and writes nothing: no trial guard, like a GET.
    let user = staff(&state, &actors, &headers, false).await?;
    let body = validation::read_body(content_type(&headers), &bytes)?;
    let obj = validation::body_object(&body)?;
    let p = [json!("body")];
    let mut errs = Errors::default();
    let schedule_id = match str_field(&mut errs, &obj, &p, "schedule_id", false, true, &NO_STR_RULES) {
        Field::Value(v) => Some(v),
        _ => None,
    };
    let frequency_raw = match str_field(&mut errs, &obj, &p, "frequency", false, true, &NO_STR_RULES) {
        Field::Value(v) => Some(v),
        _ => None,
    };
    let sections_raw = string_list(&mut errs, &obj, "sections", SECTIONS.len().max(8));
    let name_raw = match str_field(&mut errs, &obj, &p, "name", false, true, &NAME_RULES) {
        Field::Value(v) => Some(v),
        _ => None,
    };
    errs.into_result()?;
    let (sections, frequency, name) = if let Some(id) = schedule_id {
        let s = load_schedule(&state.pool, &user.tenant_id, &id).await?;
        let sections: Vec<String> = s.sections.as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect()).unwrap_or_default();
        (sections, s.frequency.clone(), s.name.clone())
    } else {
        let Some(raw) = sections_raw else {
            return Err(value_error("sections", "send sections or a schedule_id", &Value::Null));
        };
        let frequency = frequency_raw.unwrap_or_else(|| "weekly".into());
        if !FREQUENCIES.contains(&frequency.as_str()) {
            return Err(value_error("frequency", "frequency must be 'weekly' or 'monthly'", &json!(frequency)));
        }
        (clean_sections(&raw)?, frequency, name_raw.unwrap_or_default())
    };
    let base = state.settings.raw("PYTHON_API_URL").unwrap_or(pyclient::DEFAULT_URL).to_string();
    let payload = json!({"tenant_id": user.tenant_id, "sections": sections, "frequency": frequency, "schedule_name": name});
    match pyclient::render(&base, &state.settings.secret_key, &payload).await {
        Ok(rendered) => Ok(ok(json!({
            "preview": true, "sent": false,
            "report": rendered.get("report").cloned().unwrap_or(Value::Null),
            "html": rendered.get("html").cloned().unwrap_or(Value::Null),
        }))),
        Err(e) => {
            let reason = match &e {
                pyclient::CallError::BadUrl => "bad_url".to_string(),
                pyclient::CallError::Unreachable(_) => "unreachable".to_string(),
                pyclient::CallError::Status(code, _) => format!("status_{code}"),
                pyclient::CallError::NotJson => "not_json".to_string(),
            };
            tracing::error!(tenant = %user.tenant_id, "[scheduled-reports] preview could not be rendered: {e:?}");
            Err(app_err("scheduled_report_preview_unavailable", "The report preview could not be built right now", 503,
                json!({"reason": reason})))
        }
    }
}

// ── The external allow-list ──────────────────────────────────────────────────

pub async fn list_allowed(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = staff(&state, &actors, &headers, false).await?;
    let rows = sqlx::query("SELECT email, added_by, created_at FROM report_external_allowlist WHERE tenant_id = $1 ORDER BY email")
        .bind(&user.tenant_id).fetch_all(&state.pool).await?;
    let mut items = Vec::new();
    for r in &rows {
        items.push(json!({
            "email": r.try_get::<String, _>("email")?,
            "added_by": r.try_get::<String, _>("added_by")?,
            "created_at": isoformat_utc(&r.try_get::<DateTime<Utc>, _>("created_at")?),
        }));
    }
    Ok(ok(json!({"items": items, "max": MAX_ALLOWLIST})))
}

pub async fn add_allowed(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    let body = validation::read_body(content_type(&headers), &bytes)?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let raw = match str_field(&mut errs, &obj, &[json!("body")], "email", true, false, &NO_STR_RULES) {
        Field::Value(v) => Some(v),
        _ => None,
    };
    errs.into_result()?;
    let raw = raw.expect("required");
    let Some(email) = logic::normalize_email(&raw) else {
        return Err(app_err("scheduled_report_external_invalid", "That is not a valid email address", 422, json!({"email": raw})));
    };
    let tenant = &user.tenant_id;
    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))").bind(format!("report_allowlist:{tenant}")).execute(&mut *tx).await?;
    let (count,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM report_external_allowlist WHERE tenant_id = $1")
        .bind(tenant).fetch_one(&mut *tx).await?;
    let inserted = sqlx::query("INSERT INTO report_external_allowlist (tenant_id, email, added_by) VALUES ($1, $2, $3) \
                                ON CONFLICT (tenant_id, email) DO NOTHING")
        .bind(tenant).bind(&email).bind(&user.user_id);
    // A duplicate does not count against the ceiling: check it only for a new address.
    let existing: Option<(String,)> = sqlx::query_as("SELECT email FROM report_external_allowlist WHERE tenant_id = $1 AND email = $2")
        .bind(tenant).bind(&email).fetch_optional(&mut *tx).await?;
    if existing.is_none() && count >= MAX_ALLOWLIST {
        return Err(app_err("scheduled_report_allowlist_full", "The list of allowed external addresses is full", 409, json!({"max": MAX_ALLOWLIST})));
    }
    inserted.execute(&mut *tx).await?;
    tx.commit().await?;
    let status = if existing.is_some() { StatusCode::OK } else { StatusCode::CREATED };
    if existing.is_none() {
        let note = Note { target_id: Some(email.clone()), label: Some(email.clone()), before: None, after: Some(json!({"allowed": true})) };
        audit::record(&state, &actors, "POST", "/scheduled-reports/allowed-recipients", None, note, 201).await;
    }
    Ok((status, ok(json!({"email": email, "created": existing.is_none()}))))
}

pub async fn remove_allowed(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(email): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    let email = email.trim().to_lowercase();
    let gone = sqlx::query("DELETE FROM report_external_allowlist WHERE tenant_id = $1 AND email = $2")
        .bind(&user.tenant_id).bind(&email).execute(&state.pool).await?;
    if gone.rows_affected() == 0 {
        return Err(app_err("scheduled_report_allowed_recipient_not_found", "That address is not on the list", 404, json!({})));
    }
    // Schedules that name it keep the row; the worker skips it (`external_not_allowed`).
    let note = Note { target_id: None, label: Some(email.clone()), before: Some(json!({"allowed": true})), after: None };
    audit::record(&state, &actors, "DELETE", "/scheduled-reports/allowed-recipients/{email}", Some(&email), note, 200).await;
    Ok(ok(json!({"removed": true})))
}

// ── The unsubscribe link (public) ────────────────────────────────────────────

fn invalid_link() -> ApiError {
    app_err("report_unsubscribe_link_invalid", "This link is not valid", 400, json!({}))
}

async fn schedule_name_of_recipient(pool: &sqlx::PgPool, recipient_id: &str) -> Result<Option<(String, String, bool)>, ApiError> {
    let row = sqlx::query(
        "SELECT s.name, s.id, (r.unsubscribed_at IS NOT NULL) AS done FROM report_schedule_recipients r \
           JOIN report_schedules s ON s.id = r.schedule_id AND s.tenant_id = r.tenant_id WHERE r.id = $1")
        .bind(recipient_id).fetch_optional(pool).await?;
    Ok(match row {
        Some(r) => Some((r.try_get("name")?, r.try_get("id")?, r.try_get("done")?)),
        None => None,
    })
}

/// `GET`: what the link is for. Changes nothing, so a mail scanner that
/// prefetches links cannot unsubscribe anyone.
pub async fn unsubscribe_info(State(state): State<AppState>, uri: Uri) -> Result<Json<Value>, ApiError> {
    let q = query_pairs(&uri);
    let token = q.iter().find(|(k, _)| k == "token").map(|(_, v)| v.as_str()).unwrap_or("");
    let rid = logic::verify(&state.settings.secret_key, token).ok_or_else(invalid_link)?;
    let found = schedule_name_of_recipient(&state.pool, &rid).await?;
    Ok(ok(json!({
        "valid": true,
        "schedule_name": found.as_ref().map(|f| f.0.clone()),
        "already_unsubscribed": found.as_ref().map(|f| f.2).unwrap_or(true),
    })))
}

/// `POST {token}`: stop sending this report to this recipient. Idempotent.
pub async fn unsubscribe(State(state): State<AppState>, headers: HeaderMap, bytes: Bytes) -> Result<Json<Value>, ApiError> {
    let body = validation::read_body(content_type(&headers), &bytes)?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let token = match str_field(&mut errs, &obj, &[json!("body")], "token", true, false, &NO_STR_RULES) {
        Field::Value(v) => Some(v),
        _ => None,
    };
    errs.into_result()?;
    let rid = logic::verify(&state.settings.secret_key, &token.expect("required")).ok_or_else(invalid_link)?;
    let marked = sqlx::query(
        "UPDATE report_schedule_recipients SET unsubscribed_at = NOW() WHERE id = $1 AND unsubscribed_at IS NULL \
         RETURNING schedule_id, tenant_id, kind, user_id, email")
        .bind(&rid).fetch_optional(&state.pool).await?;
    let Some(row) = marked else {
        // Already unsubscribed, or the schedule (and with it the row) is gone: either way nothing more is sent.
        let found = schedule_name_of_recipient(&state.pool, &rid).await?;
        return Ok(ok(json!({"unsubscribed": true, "already": true, "schedule_name": found.map(|f| f.0)})));
    };
    let schedule_id: String = row.try_get("schedule_id")?;
    let tenant_id: String = row.try_get("tenant_id")?;
    let kind: String = row.try_get("kind")?;
    let email: Option<String> = if kind == "user" {
        let uid: Option<String> = row.try_get("user_id")?;
        let r: Option<(String,)> = sqlx::query_as("SELECT email FROM users WHERE id = $1 AND tenant_id = $2")
            .bind(uid).bind(&tenant_id).fetch_optional(&state.pool).await?;
        r.map(|r| r.0)
    } else {
        row.try_get("email")?
    };
    let name: Option<(String,)> = sqlx::query_as("SELECT name FROM report_schedules WHERE id = $1 AND tenant_id = $2")
        .bind(&schedule_id).bind(&tenant_id).fetch_optional(&state.pool).await?;
    let schedule_name = name.map(|n| n.0).unwrap_or_default();
    let mut details = Map::new();
    details.insert("schedule_name".into(), json!(schedule_name));
    details.insert("email".into(), json!(email));
    record_event(&state.pool, &tenant_id, "system", Event::ScheduledReportUnsubscribed, Some(&schedule_id), details).await;
    // Nobody left: pause the schedule out loud instead of leaving it to run empty every week.
    let (left,): (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM report_schedule_recipients WHERE schedule_id = $1 AND tenant_id = $2 AND unsubscribed_at IS NULL")
        .bind(&schedule_id).bind(&tenant_id).fetch_one(&state.pool).await?;
    if left == 0 {
        let paused = sqlx::query(
            "UPDATE report_schedules SET enabled = FALSE, paused_reason = 'no_recipients', paused_at = NOW(), updated_at = NOW() \
              WHERE id = $1 AND tenant_id = $2 AND enabled")
            .bind(&schedule_id).bind(&tenant_id).execute(&state.pool).await?;
        if paused.rows_affected() > 0 {
            let mut d = Map::new();
            d.insert("schedule_name".into(), json!(schedule_name));
            record_event_with_reason(&state.pool, &tenant_id, "system", Event::ScheduledReportAutoPaused, Some(&schedule_id), d,
                Some("report_no_recipients")).await;
        }
    }
    Ok(ok(json!({"unsubscribed": true, "already": false, "schedule_name": schedule_name})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn audited_routes_are_the_python_rust_only_list_and_are_catalogued() {
        let src = include_str!("../../../../backend/audit/catalog.py").replace("\r\n", "\n");
        let body = src.split("RUST_ONLY: tuple[tuple[str, str], ...] = (").nth(1).unwrap().split("\n)\n").next().unwrap();
        let re = regex::Regex::new(r#"\("(GET|POST|PUT|PATCH|DELETE)", "([^"]+)"\)"#).unwrap();
        let py: Vec<(String, String)> = re.captures_iter(body).map(|c| (c[1].to_string(), c[2].to_string())).collect();
        let ours: Vec<(String, String)> = AUDITED.iter().map(|(m, t)| (m.to_string(), t.to_string())).collect();
        assert_eq!(py, ours);
        for (m, t) in AUDITED {
            assert!(crate::audit::catalog::route(m, t).is_some(), "{m} {t} is not in the Rust audit catalogue");
        }
    }

    /// Every audited template has a route registered here, by its path with the
    /// `{param}` names this router uses (`schedule_id`, `email`).
    #[test]
    fn audited_templates_are_served_by_this_router() {
        let src = include_str!("mod.rs");
        for (_, template) in AUDITED {
            let path = format!("\"/api/v1{template}\"");
            assert!(src.contains(&path), "no route registered for {template}");
        }
    }

    #[test]
    fn sections_are_cleaned_in_order_and_unknown_ones_named() {
        let v = clean_sections(&["committed_demand".into(), "purchasing_summary".into(), "committed_demand".into()]).unwrap();
        assert_eq!(v, vec!["committed_demand", "purchasing_summary"]);
        let e = clean_sections(&["salaries".into()]).unwrap_err();
        assert_eq!(e.code(), Some("scheduled_report_section_unknown"));
        assert_eq!(e.body["error_params"]["section"], "salaries");
        assert!(clean_sections(&[]).is_err());
    }

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn create_body_needs_name_sections_frequency_hour() {
        let e = parse_body(&obj(json!({})), true).err().unwrap();
        let locs: Vec<String> = e.body["detail"].as_array().unwrap().iter().map(|d| d["loc"][1].as_str().unwrap().to_string()).collect();
        for f in ["name", "sections", "frequency", "hour"] {
            assert!(locs.contains(&f.to_string()), "{f} not reported: {locs:?}");
        }
        assert!(parse_body(&obj(json!({"name": "A", "sections": ["committed_demand"], "frequency": "weekly", "weekday": 1, "hour": 6})), true).is_ok());
    }

    #[test]
    fn bounds_and_types_are_422() {
        for bad in [json!({"weekday": 0}), json!({"weekday": 8}), json!({"hour": 24}), json!({"hour": -1}),
                    json!({"day_of_month": 29}), json!({"hour": "6"}), json!({"hour": 6.5}),
                    json!({"frequency": "daily"}), json!({"name": ""}), json!({"name": 5}),
                    json!({"user_ids": "x"}), json!({"user_ids": [1]}), json!({"external_emails": {"a": 1}})] {
            let e = parse_body(&obj(bad.clone()), false).err().unwrap_or_else(|| panic!("{bad} was accepted"));
            assert_eq!(e.status.as_u16(), 422, "{bad}");
        }
        // Name is trimmed; an all-space name is empty.
        assert_eq!(parse_body(&obj(json!({"name": "  Weekly  "})), false).ok().unwrap().name.as_deref(), Some("Weekly"));
        assert!(parse_body(&obj(json!({"name": "   "})), false).is_err());
    }
}
