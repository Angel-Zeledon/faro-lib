//! Spike exclusions: `backend/api/v1/spike_edits.py` and
//! `backend/inventory/spike_edit_service.py` (all three routes).
//!
//! * `GET /sessions/{id}/spike-edits`   the marks on the session's dataset,
//!                                      each with how the session's own run used it
//! * `POST /sessions/{id}/spike-edits`  mark a past period as a one-off
//! * `POST /spike-edits/{id}/revert`    undo a mark (the row stays, stamped)
//!
//! The tag is INTERNAL: a person's judgement about their history is recorded
//! under a person's name, so no API key reaches these routes. Append-only:
//! nothing here updates a mark except the revert stamp, and nothing retrains
//! (the next training, in Python, applies the marks).

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::{Extension, Json};
use chrono::{Local, NaiveDate};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::log_action;
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_date, py_strip, take_chars};
use crate::routes::ok;
use crate::routes::sessions::{bool_query, get_session, query_map, query_pairs, row_json, session_not_found, str_query};
use crate::state::AppState;
use crate::validation::{self, str_field, Errors, Field, StrRules, NO_STR_RULES};

/// `INTERNAL_TAGS["spike-edits"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'spike-edits': excluding a past spike is a person's judgement about their history, recorded under their name",
    ),
    is_mcp: false,
};

/// The list the analyst chooses from. Stable codes: the frontend renders the label.
pub const REASONS: [&str; 6] = [
    "one_off_order", "promotion", "backlog_catch_up", "data_error", "external_event", "other",
];
const MAX_NOTE_LENGTH: usize = 300;
const MAX_SPAN_DAYS: i64 = 366;

const COLS: &str = "e.id, e.dataset_id, e.sku, e.start_date, e.end_date, e.reason_code,
           e.reason_note, e.created_by, e.created_at, e.reverted_by, e.reverted_at,
           COALESCE(NULLIF(u.full_name, ''), split_part(u.email, '@', 1)) AS created_by_name";

// ── The two activity events (backend/activity/events.py) ─────────────────────

const DETAIL_KEYS: [&str; 3] = ["sku", "period", "spike_reason"];

/// `record_event` for `forecast.spike_excluded` / `forecast.spike_restored`
/// (kind `training`, INFO, detail keys sku / period / spike_reason). Never
/// fails the caller.
async fn record_spike_event(pool: &PgPool, tenant_id: &str, user_id: &str, action: &str, row: &Map<String, Value>) {
    let details = details(row);
    let mut ctx = Map::new();
    for k in DETAIL_KEYS {
        if let Some(v) = details.get(k).filter(|v| !v.is_null()) {
            ctx.insert(k.to_string(), v.clone());
        }
    }
    ctx.insert("severity".into(), json!("info"));
    ctx.insert("kind".into(), json!("training"));
    let resource = row.get("id").and_then(Value::as_str);
    if let Err(e) = log_action(pool, tenant_id, user_id, action, resource, &Value::Object(ctx), "success").await {
        tracing::error!(error = %e, action, tenant = tenant_id, "record_event: could not record");
    }
}

/// `_details`: ISO dates joined by "..", language-neutral.
fn details(row: &Map<String, Value>) -> Map<String, Value> {
    let s = |k: &str| row.get(k).and_then(Value::as_str).unwrap_or_default().to_string();
    let mut m = Map::new();
    m.insert("sku".into(), row.get("sku").cloned().unwrap_or(Value::Null));
    m.insert("period".into(), json!(format!("{}..{}", s("start_date"), s("end_date"))));
    m.insert("spike_reason".into(), row.get("reason_code").cloned().unwrap_or(Value::Null));
    m
}

// ── Service ──────────────────────────────────────────────────────────────────

/// `_as_date`: `date.fromisoformat(str(value)[:10])`.
fn as_date(value: &str, field: &str) -> Result<NaiveDate, ApiError> {
    date_fromisoformat(&take_chars(value, 10)).ok_or_else(|| {
        ApiError::app("date_invalid_iso", format!("{field} must be an ISO date (YYYY-MM-DD)"), 422,
            json!({"field": field}))
    })
}

/// `_dataset_of`: the session's dataset, or 404 / 409.
async fn dataset_of(pool: &PgPool, tenant_id: &str, session_id: &str) -> Result<String, ApiError> {
    let session = get_session(pool, tenant_id, session_id).await?.ok_or_else(session_not_found)?;
    match session.get("dataset_id") {
        Some(Value::String(d)) if !d.is_empty() => Ok(d.clone()),
        _ => Err(ApiError::app("spike_edit_no_dataset", "This session has no dataset to mark a period in", 409,
            json!({}))),
    }
}

async fn get(pool: &PgPool, tenant_id: &str, id: &str) -> Result<Map<String, Value>, ApiError> {
    let row = sqlx::query(&format!(
        "SELECT {COLS} FROM spike_edits e LEFT JOIN users u ON u.id = e.created_by WHERE e.id = $1 AND e.tenant_id = $2"
    ))
    .bind(id)
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    match row {
        Some(r) => Ok(row_json(&r)?),
        None => Err(ApiError::app("spike_edit_not_found", "Exclusion not found", 404, json!({}))),
    }
}

/// The validated `SpikeEditBody`.
struct SpikeEditBody {
    sku: String,
    start_date: String,
    end_date: String,
    reason_code: String,
    reason_note: Option<String>,
}

fn validate_body(obj: &Map<String, Value>) -> Result<SpikeEditBody, ApiError> {
    let mut errs = Errors::default();
    let p = [json!("body")];
    let sku = str_field(&mut errs, obj, &p, "sku", true, false,
        &StrRules { min_length: Some(1), max_length: Some(200), pattern: None });
    let start = str_field(&mut errs, obj, &p, "start_date", true, false, &NO_STR_RULES);
    let end = str_field(&mut errs, obj, &p, "end_date", true, false, &NO_STR_RULES);
    let reason = str_field(&mut errs, obj, &p, "reason_code", true, false, &NO_STR_RULES);
    let note = str_field(&mut errs, obj, &p, "reason_note", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_NOTE_LENGTH), pattern: None });
    errs.into_result()?;
    let val = |f: Field<String>| match f {
        Field::Value(v) => Ok(v),
        _ => Err(ApiError::internal()),
    };
    Ok(SpikeEditBody {
        sku: val(sku)?,
        start_date: val(start)?,
        end_date: val(end)?,
        reason_code: val(reason)?,
        reason_note: match note { Field::Value(v) => Some(v), _ => None },
    })
}

fn dates_invalid(message: &str, start: &NaiveDate, end: &NaiveDate) -> ApiError {
    ApiError::app("spike_edit_dates_invalid", message, 422,
        json!({"start_date": isoformat_date(start), "end_date": isoformat_date(end)}))
}

// ── Routes ───────────────────────────────────────────────────────────────────

pub fn router() -> axum::Router<AppState> {
    use axum::routing::{get, post};
    axum::Router::new()
        .route("/api/v1/sessions/{session_id}/spike-edits", get(list).post(create))
        .route("/api/v1/spike-edits/{spike_edit_id}/revert", post(revert))
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let sku = str_query(&mut errs, &q, "sku", Some(200), None);
    let include_reverted = bool_query(&mut errs, &q, "include_reverted");
    errs.into_result()?;
    let include_reverted = include_reverted.unwrap_or(false);

    let dataset_id = dataset_of(&state.pool, &user.tenant_id, &session_id).await?;
    let mut clauses = vec!["e.tenant_id = $1".to_string(), "e.dataset_id = $2".to_string()];
    let sku = sku.filter(|s| !s.is_empty());
    if sku.is_some() {
        clauses.push("e.sku = $3".into());
    }
    if !include_reverted {
        clauses.push("e.reverted_at IS NULL".into());
    }
    let sql = format!(
        "SELECT {COLS} FROM spike_edits e LEFT JOIN users u ON u.id = e.created_by \
         WHERE {} ORDER BY e.start_date, e.created_at",
        clauses.join(" AND ")
    );
    let mut qy = sqlx::query(&sql).bind(&user.tenant_id).bind(&dataset_id);
    if let Some(s) = &sku {
        qy = qy.bind(s);
    }
    let rows = qy.fetch_all(&state.pool).await?;

    // `{r["spike_edit_id"]: r for r in ...}`: the last row per mark wins.
    let apps = sqlx::query(
        "SELECT spike_edit_id, status, points_treated, original_total, replacement_total, applied_at
           FROM spike_edit_applications WHERE tenant_id = $1 AND session_id = $2",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .fetch_all(&state.pool)
    .await?;
    let mut applied: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
    for a in &apps {
        let m = row_json(a)?;
        let key: String = a.try_get("spike_edit_id")?;
        applied.insert(key, json!({
            "status": m["status"],
            "points_treated": m["points_treated"],
            "original_total": m["original_total"],
            "replacement_total": m["replacement_total"],
            "applied_at": m["applied_at"],
        }));
    }
    let mut items = Vec::with_capacity(rows.len());
    for r in &rows {
        let mut item = row_json(r)?;
        let id = item.get("id").and_then(Value::as_str).unwrap_or_default().to_string();
        item.insert("applied".into(), applied.get(&id).cloned().unwrap_or(Value::Null));
        items.push(Value::Object(item));
    }
    Ok(ok(json!({"reasons": REASONS, "items": items})))
}

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let obj = validation::body_object(&body)?;
    let b = validate_body(&obj)?;

    // svc.create, check for check.
    let sku = py_strip(&b.sku).to_string();
    if sku.is_empty() {
        return Err(ApiError::app("spike_edit_sku_required", "Choose a product", 422, json!({})));
    }
    if !REASONS.contains(&b.reason_code.as_str()) {
        return Err(ApiError::app("spike_edit_reason_invalid", "Choose a reason from the list", 422,
            json!({"reason": b.reason_code})));
    }
    let note = take_chars(py_strip(b.reason_note.as_deref().unwrap_or("")), MAX_NOTE_LENGTH);
    let note = if note.is_empty() { None } else { Some(note) };
    if b.reason_code == "other" && note.is_none() {
        return Err(ApiError::app("spike_edit_note_required", "Say what the reason is when you choose 'other'",
            422, json!({})));
    }
    let start = as_date(&b.start_date, "start_date")?;
    let end = as_date(&b.end_date, "end_date")?;
    if end < start {
        return Err(dates_invalid("The end date is before the start", &start, &end));
    }
    if (end - start).num_days() > MAX_SPAN_DAYS {
        return Err(dates_invalid("That period is too long to exclude", &start, &end));
    }
    if end > Local::now().date_naive() {
        return Err(dates_invalid("Only a period that already happened can be excluded", &start, &end));
    }
    let dataset_id = dataset_of(&state.pool, &user.tenant_id, &session_id).await?;

    let mut tx = state.pool.begin().await?;
    let clash: Option<(String,)> = sqlx::query_as(
        "SELECT id FROM spike_edits
          WHERE tenant_id = $1 AND dataset_id = $2 AND sku = $3
            AND reverted_at IS NULL AND start_date <= $4 AND end_date >= $5
          LIMIT 1",
    )
    .bind(&user.tenant_id)
    .bind(&dataset_id)
    .bind(&sku)
    .bind(end)
    .bind(start)
    .fetch_optional(&mut *tx)
    .await?;
    if let Some((clash_id,)) = clash {
        return Err(ApiError::app("spike_edit_overlaps",
            "That period overlaps one already excluded for this product; undo it first to change it", 409,
            json!({"sku": sku, "spike_edit_id": clash_id})));
    }
    let (new_id,): (String,) = sqlx::query_as(
        "INSERT INTO spike_edits
             (tenant_id, dataset_id, sku, start_date, end_date, reason_code, reason_note, created_by)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
    )
    .bind(&user.tenant_id)
    .bind(&dataset_id)
    .bind(&sku)
    .bind(start)
    .bind(end)
    .bind(&b.reason_code)
    .bind(&note)
    .bind(&user.user_id)
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    let row = get(&state.pool, &user.tenant_id, &new_id).await?;
    record_spike_event(&state.pool, &user.tenant_id, &user.user_id, "forecast.spike_excluded", &row).await;
    Ok((StatusCode::CREATED, ok(Value::Object(row))))
}

pub async fn revert(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(spike_edit_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    // Conditional update so two simultaneous undos cannot both claim the stamp.
    let changed: Option<(String,)> = sqlx::query_as(
        "UPDATE spike_edits SET reverted_by = $1, reverted_at = NOW()
          WHERE id = $2 AND tenant_id = $3 AND reverted_at IS NULL
        RETURNING id",
    )
    .bind(&user.user_id)
    .bind(&spike_edit_id)
    .bind(&user.tenant_id)
    .fetch_optional(&state.pool)
    .await?;
    if changed.is_none() {
        let existing = get(&state.pool, &user.tenant_id, &spike_edit_id).await?;
        return Err(ApiError::app("spike_edit_already_reverted", "That exclusion was already undone", 409,
            json!({"spike_edit_id": existing["id"]})));
    }
    let row = get(&state.pool, &user.tenant_id, &spike_edit_id).await?;
    record_spike_event(&state.pool, &user.tenant_id, &user.user_id, "forecast.spike_restored", &row).await;
    Ok(ok(Value::Object(row)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn details_join_iso_dates() {
        let row = json!({"id": "x", "sku": "A", "start_date": "2026-01-01", "end_date": "2026-01-31",
                         "reason_code": "promotion"});
        let d = details(row.as_object().unwrap());
        assert_eq!(serde_json::to_string(&d).unwrap(),
            r#"{"sku":"A","period":"2026-01-01..2026-01-31","spike_reason":"promotion"}"#);
    }

    #[test]
    fn date_errors_name_the_field() {
        let e = as_date("2026/01/01", "end_date").unwrap_err();
        assert_eq!(e.body["detail"], "end_date must be an ISO date (YYYY-MM-DD)");
        assert_eq!(e.body["error_params"], json!({"field": "end_date"}));
        assert_eq!(isoformat_date(&as_date("2026-01-05T10:00", "x").unwrap()), "2026-01-05");
    }

    #[test]
    fn body_validation_order() {
        let e = validate_body(json!({"sku": "", "reason_note": "x".repeat(301)}).as_object().unwrap())
            .err().unwrap();
        let types: Vec<&str> = e.body["detail"].as_array().unwrap().iter()
            .map(|x| x["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["string_too_short", "missing", "missing", "missing", "string_too_long"]);
    }
}
