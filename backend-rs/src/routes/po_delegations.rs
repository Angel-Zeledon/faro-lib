//! Purchase-order approval delegation: a substitute approver for a date range.
//!
//! NEW routes, written in Rust only. There is NO Python implementation, so
//! there is no Python failover: with the Rust service down (or its gateway
//! file in `routes.d/off/`) these three paths answer Python's own 404 and the
//! screen says so. What Python DOES do is honour the rows these routes write:
//! `backend/inventory/po_delegation_service.py` is consulted by
//! `po_approval_service.decide`, which Python still serves, with the rules
//! restated below (dates read at decision time, delegator still an approver,
//! the delegator's warehouse scope, the self-approval limit).
//!
//! * `GET  /inventory/po-approval/delegations`            mine (given or received)
//! * `POST /inventory/po-approval/delegations`            name a substitute
//! * `POST /inventory/po-approval/delegations/{id}/revoke` end one now
//!
//! The tag is INTERNAL (a person handing over their authority is a person's
//! act, recorded under their name), so no API key reaches these routes.
//! Dates are inclusive UTC days compared in SQL against `NOW()`; nothing ever
//! flips a flag, so an expired delegation stops working by itself.

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::{Extension, Json};
use chrono::NaiveDate;
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::activity::{record_event, Event};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, py_strip, take_chars};
use crate::routes::ok;
use crate::routes::sessions::{bool_query, query_map, query_pairs, row_json};
use crate::state::AppState;
use crate::validation::{self, str_field, Errors, Field, StrRules, NO_STR_RULES};

/// `INTERNAL_TAGS["inventory-approvals"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'inventory-approvals': delegating approval authority is a person's act, recorded under their name",
    ),
    is_mcp: false,
};

/// Longest span one delegation may cover (a vacation is weeks; a year is a
/// standing hand-over, which is an admin flagging a second approver instead).
pub const MAX_DAYS: i64 = 366;
pub const MAX_NOTE_LENGTH: usize = 500;

/// UTC "today", the same clock the Python decision path reads.
const TODAY: &str = "(NOW() AT TIME ZONE 'UTC')::date";

fn select_sql() -> String {
    format!(
        "SELECT d.id, d.delegator_id,
                COALESCE(NULLIF(dr.full_name, ''), split_part(dr.email, '@', 1)) AS delegator_name,
                d.delegate_id,
                COALESCE(NULLIF(de.full_name, ''), split_part(de.email, '@', 1)) AS delegate_name,
                d.starts_on, d.ends_on, d.note,
                CASE WHEN d.revoked_at IS NOT NULL THEN 'revoked'
                     WHEN d.ends_on < {TODAY} THEN 'expired'
                     WHEN d.starts_on > {TODAY} THEN 'scheduled'
                     ELSE 'active' END AS status,
                d.created_by, d.created_at, d.revoked_at, d.revoked_by
           FROM po_approval_delegations d
           LEFT JOIN users dr ON dr.id = d.delegator_id AND dr.tenant_id = d.tenant_id
           LEFT JOIN users de ON de.id = d.delegate_id AND de.tenant_id = d.tenant_id"
    )
}

fn not_found() -> ApiError {
    ApiError::app("po_delegation_not_found", "Delegation not found", 404, json!({}))
}

async fn get(pool: &PgPool, tenant_id: &str, id: &str) -> Result<Map<String, Value>, ApiError> {
    let sql = format!("{} WHERE d.tenant_id = $1 AND d.id = $2", select_sql());
    let row = sqlx::query(&sql).bind(tenant_id).bind(id).fetch_optional(pool).await?;
    match row {
        Some(r) => Ok(row_json(&r)?),
        None => Err(not_found()),
    }
}

// ── The pure rules (unit-tested) ─────────────────────────────────────────────

fn dates_invalid(message: &str, extra: Value) -> ApiError {
    let mut params = Map::new();
    params.insert("field".into(), json!("ends_on"));
    if let Value::Object(m) = extra {
        params.extend(m);
    }
    ApiError::app("po_delegation_dates_invalid", message, 422, Value::Object(params))
}

/// The window rules: not backwards, not longer than [`MAX_DAYS`], not already
/// over. `today` is the UTC date.
pub fn check_window(starts_on: NaiveDate, ends_on: NaiveDate, today: NaiveDate) -> Result<(), ApiError> {
    if ends_on < starts_on {
        return Err(dates_invalid("The end date cannot be before the start date", json!({})));
    }
    if (ends_on - starts_on).num_days() + 1 > MAX_DAYS {
        return Err(dates_invalid(
            &format!("A delegation cannot cover more than {MAX_DAYS} days"),
            json!({"max_days": MAX_DAYS}),
        ));
    }
    if ends_on < today {
        return Err(dates_invalid("The delegation would already be over", json!({})));
    }
    Ok(())
}

/// Who may stand in: somebody active who can act (admin or analyst).
pub fn check_delegate(role: &str, status: &str) -> Result<(), ApiError> {
    if role != "admin" && role != "analyst" {
        return Err(ApiError::app("po_delegation_delegate_role",
            "Only an admin or an analyst can stand in as an approver", 409, json!({})));
    }
    if status != "active" {
        return Err(ApiError::app("po_delegation_delegate_inactive", "That user is not active", 409, json!({})));
    }
    Ok(())
}

pub fn self_delegation() -> ApiError {
    ApiError::app("po_delegation_self", "You cannot delegate to yourself", 422, json!({}))
}

fn as_date(value: &str, field: &str) -> Result<NaiveDate, ApiError> {
    date_fromisoformat(&take_chars(value, 10)).ok_or_else(|| {
        ApiError::app("date_invalid_iso", format!("{field} must be an ISO date (YYYY-MM-DD)"), 422,
            json!({"field": field}))
    })
}

struct DelegationBody {
    delegate_id: String,
    starts_on: String,
    ends_on: String,
    note: Option<String>,
}

fn validate_body(obj: &Map<String, Value>) -> Result<DelegationBody, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let delegate = str_field(&mut errs, obj, &p, "delegate_id", true, false,
        &StrRules { min_length: Some(1), max_length: Some(64), pattern: None });
    let starts = str_field(&mut errs, obj, &p, "starts_on", true, false, &NO_STR_RULES);
    let ends = str_field(&mut errs, obj, &p, "ends_on", true, false, &NO_STR_RULES);
    let note = str_field(&mut errs, obj, &p, "note", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_NOTE_LENGTH), pattern: None });
    errs.into_result()?;
    let val = |f: Field<String>| match f {
        Field::Value(v) => Ok(v),
        _ => Err(ApiError::internal()),
    };
    Ok(DelegationBody {
        delegate_id: val(delegate)?,
        starts_on: val(starts)?,
        ends_on: val(ends)?,
        note: match note { Field::Value(v) => Some(v), _ => None },
    })
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let all = bool_query(&mut errs, &q, "all");
    errs.into_result()?;
    let all = all.unwrap_or(false);
    if all {
        // The whole company's delegations: an admin's view.
        auth::require_role(&user, &["admin"])?;
    }
    let mut sql = format!("{} WHERE d.tenant_id = $1", select_sql());
    if !all {
        sql.push_str(" AND (d.delegator_id = $2 OR d.delegate_id = $2)");
    }
    sql.push_str(" ORDER BY d.created_at DESC, d.id DESC");
    let mut qy = sqlx::query(&sql).bind(&user.tenant_id);
    if !all {
        qy = qy.bind(&user.user_id);
    }
    let rows = qy.fetch_all(&state.pool).await?;
    let mut items = Vec::with_capacity(rows.len());
    for r in &rows {
        items.push(Value::Object(row_json(r)?));
    }
    // Who the caller could name: only a current approver has anything to
    // hand over, and only they are shown the colleagues who could stand in
    // (active admins and analysts, never themselves).
    let candidates = candidates_for(&state.pool, &user.tenant_id, &user.user_id).await?;
    Ok(ok(json!({"items": items, "candidates": candidates})))
}

/// The people an approver may pick as a substitute; empty for anybody who is
/// not a current approver (same test as `po_approval_service.list_approvers`).
async fn candidates_for(pool: &PgPool, tenant_id: &str, user_id: &str) -> Result<Vec<Value>, ApiError> {
    let approver: Option<(String,)> = sqlx::query_as(
        "SELECT id FROM users
          WHERE id = $1 AND tenant_id = $2 AND can_approve_po AND status = 'active'
            AND role IN ('admin', 'analyst')",
    )
    .bind(user_id)
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    if approver.is_none() {
        return Ok(Vec::new());
    }
    let rows: Vec<(String, String, String)> = sqlx::query_as(
        "SELECT id, COALESCE(NULLIF(full_name, ''), split_part(email, '@', 1)) AS name, role
           FROM users
          WHERE tenant_id = $1 AND status = 'active' AND role IN ('admin', 'analyst') AND id <> $2
          ORDER BY full_name NULLS LAST, email",
    )
    .bind(tenant_id)
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(|(id, name, role)| json!({"id": id, "name": name, "role": role})).collect())
}

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let obj = validation::body_object(&body)?;
    let b = validate_body(&obj)?;
    let pool = &state.pool;

    let delegate_id = py_strip(&b.delegate_id).to_string();
    let starts_on = as_date(&b.starts_on, "starts_on")?;
    let ends_on = as_date(&b.ends_on, "ends_on")?;
    if delegate_id == user.user_id {
        return Err(self_delegation());
    }
    let (today,): (NaiveDate,) = sqlx::query_as(&format!("SELECT {TODAY}")).fetch_one(pool).await?;
    check_window(starts_on, ends_on, today)?;

    // Only a CURRENT approver has anything to hand over (same list as
    // `po_approval_service.list_approvers`).
    let approver: Option<(String,)> = sqlx::query_as(
        "SELECT id FROM users
          WHERE id = $1 AND tenant_id = $2 AND can_approve_po AND status = 'active'
            AND role IN ('admin', 'analyst')",
    )
    .bind(&user.user_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    if approver.is_none() {
        return Err(ApiError::app("po_delegation_not_approver",
            "Only an approver can delegate approvals", 403, json!({})));
    }
    let delegate: Option<(String, String)> = sqlx::query_as(
        "SELECT role, status FROM users WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&delegate_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    let Some((role, status)) = delegate else {
        return Err(ApiError::app("user_not_found", "User not found", 404, json!({})));
    };
    check_delegate(&role, &status)?;

    let note = take_chars(py_strip(b.note.as_deref().unwrap_or("")), MAX_NOTE_LENGTH);
    let note = if note.is_empty() { None } else { Some(note) };

    let overlap: Option<(i32,)> = sqlx::query_as(
        "SELECT 1 FROM po_approval_delegations
          WHERE tenant_id = $1 AND delegator_id = $2 AND delegate_id = $3
            AND revoked_at IS NULL AND starts_on <= $4 AND ends_on >= $5 LIMIT 1",
    )
    .bind(&user.tenant_id)
    .bind(&user.user_id)
    .bind(&delegate_id)
    .bind(ends_on)
    .bind(starts_on)
    .fetch_optional(pool)
    .await?;
    if overlap.is_some() {
        return Err(ApiError::app("po_delegation_overlap",
            "You already delegate to that person in those dates", 409, json!({})));
    }
    let (id,): (String,) = sqlx::query_as(
        "INSERT INTO po_approval_delegations
             (tenant_id, delegator_id, delegate_id, starts_on, ends_on, note, created_by)
         VALUES ($1, $2, $3, $4, $5, $6, $2) RETURNING id",
    )
    .bind(&user.tenant_id)
    .bind(&user.user_id)
    .bind(&delegate_id)
    .bind(starts_on)
    .bind(ends_on)
    .bind(&note)
    .fetch_one(pool)
    .await?;
    let row = get(pool, &user.tenant_id, &id).await?;
    tracing::info!("[po-delegation] CREATED tenant={} id={} from={} to={}", user.tenant_id, id, user.user_id, delegate_id);
    let mut details = Map::new();
    details.insert("delegate".into(), row["delegate_name"].clone());
    details.insert("starts_on".into(), json!(isoformat_date(&starts_on)));
    details.insert("ends_on".into(), json!(isoformat_date(&ends_on)));
    record_event(pool, &user.tenant_id, &user.user_id, Event::ApprovalDelegationCreated, Some(&id), details).await;
    Ok((StatusCode::CREATED, ok(Value::Object(row))))
}

pub async fn revoke(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(delegation_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let pool = &state.pool;
    let current = get(pool, &user.tenant_id, &delegation_id).await?;
    // Its giver or an admin may end it; for anybody else it does not exist.
    let giver = current.get("delegator_id").and_then(Value::as_str) == Some(user.user_id.as_str());
    if !giver && user.role != "admin" {
        return Err(not_found());
    }
    if !current["revoked_at"].is_null() {
        let mut out = current;
        out.insert("changed".into(), json!(false));
        return Ok(ok(Value::Object(out)));
    }
    // Conditional update: two simultaneous revocations stamp it once.
    let won: Option<(String,)> = sqlx::query_as(
        "UPDATE po_approval_delegations SET revoked_at = NOW(), revoked_by = $1
          WHERE id = $2 AND tenant_id = $3 AND revoked_at IS NULL RETURNING id",
    )
    .bind(&user.user_id)
    .bind(&delegation_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    let mut row = get(pool, &user.tenant_id, &delegation_id).await?;
    let changed = won.is_some();
    if changed {
        tracing::info!("[po-delegation] REVOKED tenant={} id={} by={}", user.tenant_id, delegation_id, user.user_id);
        let mut details = Map::new();
        details.insert("delegate".into(), row["delegate_name"].clone());
        record_event(pool, &user.tenant_id, &user.user_id, Event::ApprovalDelegationRevoked,
            Some(&delegation_id), details).await;
    }
    row.insert("changed".into(), json!(changed));
    Ok(ok(Value::Object(row)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> NaiveDate {
        NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()
    }

    fn code(e: &ApiError) -> String {
        e.body["error_code"].as_str().unwrap_or_default().to_string()
    }

    #[test]
    fn a_window_may_start_today_and_end_today() {
        assert!(check_window(d("2026-10-06"), d("2026-10-06"), d("2026-10-06")).is_ok());
    }

    #[test]
    fn a_backwards_window_is_refused() {
        let e = check_window(d("2026-10-10"), d("2026-10-09"), d("2026-10-01")).unwrap_err();
        assert_eq!(code(&e), "po_delegation_dates_invalid");
    }

    #[test]
    fn a_window_already_over_is_refused() {
        let e = check_window(d("2026-09-01"), d("2026-09-30"), d("2026-10-01")).unwrap_err();
        assert_eq!(code(&e), "po_delegation_dates_invalid");
        assert_eq!(e.body["detail"], "The delegation would already be over");
    }

    #[test]
    fn a_window_may_have_started_already_if_it_is_still_running() {
        assert!(check_window(d("2026-09-01"), d("2026-10-01"), d("2026-10-01")).is_ok());
    }

    #[test]
    fn the_span_is_capped_inclusively() {
        // 366 days inclusive is allowed, 367 is not.
        assert!(check_window(d("2026-01-01"), d("2027-01-01"), d("2026-01-01")).is_ok());
        let e = check_window(d("2026-01-01"), d("2027-01-02"), d("2026-01-01")).unwrap_err();
        assert_eq!(e.body["error_params"]["max_days"], 366);
    }

    #[test]
    fn only_an_active_admin_or_analyst_can_stand_in() {
        assert!(check_delegate("analyst", "active").is_ok());
        assert!(check_delegate("admin", "active").is_ok());
        assert_eq!(code(&check_delegate("viewer", "active").unwrap_err()), "po_delegation_delegate_role");
        assert_eq!(code(&check_delegate("analyst", "inactive").unwrap_err()), "po_delegation_delegate_inactive");
        // A viewer is refused for being a viewer even when also inactive.
        assert_eq!(code(&check_delegate("viewer", "inactive").unwrap_err()), "po_delegation_delegate_role");
    }

    #[test]
    fn self_delegation_is_a_422() {
        let e = self_delegation();
        assert_eq!(code(&e), "po_delegation_self");
        assert_eq!(e.status, 422);
    }

    #[test]
    fn dates_must_be_iso() {
        let e = as_date("06/10/2026", "starts_on").unwrap_err();
        assert_eq!(e.body["error_params"], json!({"field": "starts_on"}));
        assert_eq!(isoformat_date(&as_date("2026-10-06T10:00", "x").unwrap()), "2026-10-06");
    }

    #[test]
    fn body_validation_reports_every_missing_field_in_order() {
        let e = validate_body(json!({}).as_object().unwrap()).err().unwrap();
        let locs: Vec<String> = e.body["detail"].as_array().unwrap().iter()
            .map(|x| x["loc"][1].as_str().unwrap().to_string()).collect();
        assert_eq!(locs, ["delegate_id", "starts_on", "ends_on"]);
        let e = validate_body(json!({"delegate_id": "u", "starts_on": "a", "ends_on": "b",
                                     "note": "x".repeat(501)}).as_object().unwrap()).err().unwrap();
        assert_eq!(e.body["detail"][0]["type"], "string_too_long");
    }
}
