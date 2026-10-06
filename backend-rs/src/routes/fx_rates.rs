//! Exchange rates and the conversion preview (multi-currency):
//!
//! * `GET    /tenant/currency/rates`          the tenant's rates (current base currency)
//! * `POST   /tenant/currency/rates`          enter a rate (admin)
//! * `PATCH  /tenant/currency/rates/{id}`     correct a rate or its source note (admin)
//! * `DELETE /tenant/currency/rates/{id}`     remove a rate (admin)
//! * `GET    /tenant/currency/rates/resolve`  the rate in force for a currency on a date
//! * `POST   /tenant/currency/convert`        convert one amount, saying which rate was used
//!
//! NEW routes: Python has no twin, so there is no Python failover for them
//! (docs/rust-migration.md). Python only READS the `exchange_rates` table, with
//! the same rules, in `backend/fx/` (purchase orders, budgets, the cash
//! calendar), so a purchase order written by Python uses the rates entered here.
//!
//! The rules are in `crate::fx` (exact decimals, one half-up rounding to 2
//! decimals, the latest rate not after the date, no rate means no conversion,
//! never 1.0). Rates are typed by a person, never fetched from a feed; each has
//! an optional source note ("central bank, 2026-10-05"). A rate is stored for
//! currency -> the tenant's base currency AT THE TIME, so relabelling the base
//! currency later makes the old rates stop applying (a missing rate, said out
//! loud) instead of silently meaning something else.
//!
//! Reads are every role and a read key. Writes are admin only, like the base
//! currency setting itself: a rate moves every converted total.

use axum::body::Bytes;
use axum::extract::{Path, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::{Extension, Json};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::{record_event, Event};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::fx::{self, Dec, FxError, RateRow};
use crate::pycompat::{date_fromisoformat, isoformat_date, isoformat_utc, py_strip};
use crate::query::{int_param, opt_str, PyInt, Query};
use crate::routes::ok;
use crate::routes::r1::currency::{currency_code_of, is_supported, sorted_codes};
use crate::state::AppState;
use crate::validation::{self, body_object, str_field, Errors, Field, StrRules};

pub const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
pub const ADMIN_WRITE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("admin only: no API key can hold the admin role"),
    is_mcp: false,
};

const MAX_NOTE_LENGTH: usize = 200;
/// A rate may be entered ahead of its date (it is not used until then), but not
/// years ahead; and nothing before the product existed.
const MAX_DAYS_AHEAD: i64 = 366;
const EARLIEST: (i32, u32, u32) = (2000, 1, 1);

fn today() -> NaiveDate {
    Utc::now().date_naive()
}

// ── Errors ───────────────────────────────────────────────────────────────────

fn rate_invalid(err: &FxError) -> ApiError {
    ApiError::app(
        "fx_rate_invalid",
        "The exchange rate must be a positive number with at most 10 decimals (between 0.0000000001 and 1000000000).",
        422,
        json!({"field": "rate", "reason": err.code()}),
    )
}

fn not_found() -> ApiError {
    ApiError::app("fx_rate_not_found", "Exchange rate not found", 404, json!({}))
}

fn missing_rate(currency: &str, base: &str, on: NaiveDate) -> ApiError {
    ApiError::app(
        "fx_rate_missing",
        format!("There is no {currency} to {base} exchange rate in force on {on}."),
        404,
        json!({"currency": currency, "base_currency": base, "on": isoformat_date(&on)}),
    )
}

fn code_of(raw: &str) -> String {
    py_strip(&raw.to_uppercase()).to_string()
}

fn unsupported(code: &str) -> ApiError {
    ApiError::app(
        "currency_not_supported",
        format!("Currency '{code}' is not supported."),
        400,
        json!({"code": code, "supported": sorted_codes()}),
    )
}

// ── Inputs ───────────────────────────────────────────────────────────────────

/// A rate or an amount as the request spells it: a decimal string (exact) or a
/// JSON number (taken through its shortest text; clients that need exactness
/// send a string).
fn dec_text(v: &Value) -> Result<String, FxError> {
    match v {
        Value::String(s) => Ok(s.clone()),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.to_string())
            } else if let Some(f) = n.as_f64() {
                Ok(Dec::from_f64(f)?.to_text_trimmed(0))
            } else {
                Err(FxError::NotANumber)
            }
        }
        _ => Err(FxError::NotANumber),
    }
}

fn parse_date_field(raw: &str) -> Result<NaiveDate, ApiError> {
    let d = date_fromisoformat(py_strip(raw)).ok_or_else(|| {
        ApiError::app(
            "fx_rate_date_invalid",
            "The effective date must be a calendar date like 2026-10-05.",
            422,
            json!({"field": "effective_date"}),
        )
    })?;
    let earliest = NaiveDate::from_ymd_opt(EARLIEST.0, EARLIEST.1, EARLIEST.2).unwrap_or(d);
    if d < earliest || d > today() + Duration::days(MAX_DAYS_AHEAD) {
        return Err(ApiError::app(
            "fx_rate_date_invalid",
            "The effective date is outside the allowed range (from 2000-01-01 to one year ahead).",
            422,
            json!({"field": "effective_date"}),
        ));
    }
    Ok(d)
}

const NOTE_RULES: StrRules = StrRules { min_length: None, max_length: Some(MAX_NOTE_LENGTH), pattern: None };

// ── Storage ──────────────────────────────────────────────────────────────────

const COLS: &str = "id, currency, base_currency, rate::text AS rate, effective_date, source_note,
                    created_by, created_at, updated_at";

fn row_json(r: &sqlx::postgres::PgRow) -> Result<Map<String, Value>, ApiError> {
    let rate: String = r.try_get("rate")?;
    let rate_text = Dec::parse(&rate).map(|d| d.to_text_trimmed(0)).map_err(|_| ApiError::internal())?;
    let eff: NaiveDate = r.try_get("effective_date")?;
    let created: DateTime<Utc> = r.try_get("created_at")?;
    let updated: DateTime<Utc> = r.try_get("updated_at")?;
    let note: Option<String> = r.try_get("source_note")?;
    let mut m = Map::new();
    m.insert("id".into(), json!(r.try_get::<String, _>("id")?));
    m.insert("currency".into(), json!(r.try_get::<String, _>("currency")?));
    m.insert("base_currency".into(), json!(r.try_get::<String, _>("base_currency")?));
    m.insert("rate".into(), json!(rate_text));
    m.insert("effective_date".into(), json!(isoformat_date(&eff)));
    m.insert("source_note".into(), note.map(Value::String).unwrap_or(Value::Null));
    m.insert("created_by".into(), json!(r.try_get::<String, _>("created_by")?));
    m.insert("created_at".into(), json!(isoformat_utc(&created)));
    m.insert("updated_at".into(), json!(isoformat_utc(&updated)));
    Ok(m)
}

async fn get_row(pool: &PgPool, tenant_id: &str, id: &str) -> Result<Option<Map<String, Value>>, ApiError> {
    let row = sqlx::query(&format!("SELECT {COLS} FROM exchange_rates WHERE tenant_id = $1 AND id = $2"))
        .bind(tenant_id)
        .bind(id)
        .fetch_optional(pool)
        .await?;
    row.as_ref().map(row_json).transpose()
}

/// The rate in force for currency -> base on `on`, with its exact text.
/// `None` when there is none (rule 4: never a substitute).
async fn resolve_rate(
    pool: &PgPool,
    tenant_id: &str,
    currency: &str,
    base: &str,
    on: NaiveDate,
) -> Result<Option<Map<String, Value>>, ApiError> {
    let rows = sqlx::query(&format!(
        "SELECT {COLS} FROM exchange_rates
          WHERE tenant_id = $1 AND currency = $2 AND base_currency = $3 AND effective_date <= $4
          ORDER BY effective_date DESC"
    ))
    .bind(tenant_id)
    .bind(currency)
    .bind(base)
    .bind(on)
    .fetch_all(pool)
    .await?;
    // The SQL already orders and filters; the shared resolver makes the choice
    // so the rule has one implementation.
    let mut maps = Vec::new();
    let mut ids = Vec::new();
    for r in &rows {
        let m = row_json(r)?;
        ids.push(RateRow {
            id: m["id"].as_str().unwrap_or("").to_string(),
            currency: currency.to_string(),
            base_currency: base.to_string(),
            effective_date: r.try_get("effective_date")?,
        });
        maps.push(m);
    }
    Ok(fx::resolve(&ids, currency, base, on)
        .and_then(|found| maps.into_iter().find(|m| m["id"].as_str() == Some(found.id.as_str()))))
}

fn event_details(row: &Map<String, Value>) -> Map<String, Value> {
    ["currency", "rate", "effective_date"]
        .iter()
        .map(|k| ((*k).to_string(), row.get(*k).cloned().unwrap_or(Value::Null)))
        .collect()
}

async fn writer_and_body(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    bytes: &Bytes,
) -> Result<(auth::CurrentUser, Map<String, Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, bytes)?;
    let user = auth::current_user(state, headers, ADMIN_WRITE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    let obj = body_object(&body)?;
    Ok((user, obj))
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn list(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let q = Query::parse(raw.as_deref());
    let mut errs = Errors::default();
    let currency = opt_str(&mut errs, &q, "currency", Some(8));
    let limit = int_param(&mut errs, &q, "limit", 100, Some(1), Some(500));
    let offset = int_param(&mut errs, &q, "offset", 0, Some(0), None);
    errs.into_result()?;
    // An offset beyond 64 bits is simply past the end of any list.
    let int = |p: Option<PyInt>| match p {
        Some(PyInt::Small(i)) => i,
        _ => i64::MAX,
    };
    let (limit, offset) = (int(limit), int(offset));
    let base = currency_code_of(&state.pool, &user.tenant_id).await?;
    let wanted = currency.map(|c| code_of(&c));

    let rows = sqlx::query(&format!(
        "SELECT {COLS} FROM exchange_rates
          WHERE tenant_id = $1 AND base_currency = $2 AND ($3::text IS NULL OR currency = $3)
          ORDER BY currency, effective_date DESC, id
          LIMIT $4 OFFSET $5"
    ))
    .bind(&user.tenant_id)
    .bind(&base)
    .bind(wanted.as_deref())
    .bind(limit)
    .bind(offset)
    .fetch_all(&state.pool)
    .await?;
    let total: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM exchange_rates
          WHERE tenant_id = $1 AND base_currency = $2 AND ($3::text IS NULL OR currency = $3)",
    )
    .bind(&user.tenant_id)
    .bind(&base)
    .bind(wanted.as_deref())
    .fetch_one(&state.pool)
    .await?;
    // Rates entered under an earlier base currency no longer apply; say how
    // many, so the screen can explain why a currency shows no rate.
    let other_base: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM exchange_rates WHERE tenant_id = $1 AND base_currency <> $2",
    )
    .bind(&user.tenant_id)
    .bind(&base)
    .fetch_one(&state.pool)
    .await?;

    // Which row is in force today, per currency (the first row of each
    // currency whose date is not in the future).
    let day = today();
    let mut items = Vec::new();
    let mut seen_in_force: Vec<String> = Vec::new();
    for r in &rows {
        let mut m = row_json(r)?;
        let eff: NaiveDate = r.try_get("effective_date")?;
        let cur = m["currency"].as_str().unwrap_or("").to_string();
        let in_force = eff <= day && !seen_in_force.contains(&cur);
        if in_force {
            seen_in_force.push(cur);
        }
        m.insert("in_force".into(), json!(in_force));
        items.push(Value::Object(m));
    }
    Ok(ok(json!({
        "base_currency": base,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "other_base_count": other_base,
        "supported": sorted_codes(),
    })))
}

pub async fn create(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let p = [Value::String("body".into())];
    let mut errs = Errors::default();
    let currency = str_field(&mut errs, &obj, &p, "currency", true, false, &StrRules { min_length: Some(1), max_length: Some(8), pattern: None });
    let note = str_field(&mut errs, &obj, &p, "source_note", false, true, &NOTE_RULES);
    let date = str_field(&mut errs, &obj, &p, "effective_date", false, true, &StrRules { min_length: None, max_length: Some(32), pattern: None });
    if !obj.contains_key("rate") {
        errs.push("missing", &validation::loc(&p, "rate"), "Field required".into(), &Value::Object(obj.clone()), None);
    }
    errs.into_result()?;
    let Field::Value(currency) = currency else { return Err(ApiError::internal()) };

    let base = currency_code_of(&state.pool, &user.tenant_id).await?;
    let code = code_of(&currency);
    if !is_supported(&code) {
        return Err(unsupported(&code));
    }
    if code == base {
        return Err(ApiError::app(
            "fx_rate_currency_is_base",
            "A rate is for a currency other than the company's own; the company's currency needs none.",
            422,
            json!({"currency": code, "base_currency": base}),
        ));
    }
    let rate_dec = dec_text(&obj["rate"]).and_then(|t| fx::parse_rate(&t)).map_err(|e| rate_invalid(&e))?;
    let rate_text = rate_dec.to_text_trimmed(0);
    let effective = match &date {
        Field::Value(d) => parse_date_field(d)?,
        _ => today(),
    };
    let note = match note {
        Field::Value(n) => {
            let n = py_strip(&n).to_string();
            if n.is_empty() { None } else { Some(n) }
        }
        _ => None,
    };

    let id = uuid::Uuid::new_v4().simple().to_string();
    let inserted = sqlx::query(&format!(
        "INSERT INTO exchange_rates
             (id, tenant_id, currency, base_currency, rate, effective_date, source_note, created_by)
         VALUES ($1, $2, $3, $4, $5::numeric, $6, $7, $8)
         ON CONFLICT (tenant_id, currency, base_currency, effective_date) DO NOTHING
         RETURNING {COLS}"
    ))
    .bind(&id)
    .bind(&user.tenant_id)
    .bind(&code)
    .bind(&base)
    .bind(&rate_text)
    .bind(effective)
    .bind(note.as_deref())
    .bind(&user.user_id)
    .fetch_optional(&state.pool)
    .await?;
    let Some(inserted) = inserted else {
        return Err(ApiError::app(
            "fx_rate_exists",
            "There is already a rate for that currency on that date; edit it instead.",
            409,
            json!({"currency": code, "effective_date": isoformat_date(&effective)}),
        ));
    };
    let row = row_json(&inserted)?;
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CurrencyRateCreated,
        row.get("id").and_then(Value::as_str), event_details(&row)).await;
    Ok((StatusCode::CREATED, ok(Value::Object(row))))
}

pub async fn update(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(rate_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let (user, obj) = writer_and_body(&state, &actors, &headers, &bytes).await?;
    let p = [Value::String("body".into())];
    let mut errs = Errors::default();
    let note = str_field(&mut errs, &obj, &p, "source_note", false, true, &NOTE_RULES);
    errs.into_result()?;
    let new_rate = match obj.get("rate") {
        None | Some(Value::Null) => None,
        Some(v) => Some(dec_text(v).and_then(|t| fx::parse_rate(&t)).map_err(|e| rate_invalid(&e))?),
    };
    if new_rate.is_none() && matches!(note, Field::Absent) {
        return Err(ApiError::app(
            "fx_rate_nothing_to_change",
            "Send a new rate or a new source note.",
            422,
            json!({}),
        ));
    }
    let rate_text = new_rate.map(|d| d.to_text_trimmed(0));
    let (set_note, note_value) = match &note {
        Field::Value(n) => {
            let n = py_strip(n).to_string();
            (true, if n.is_empty() { None } else { Some(n) })
        }
        Field::Null => (true, None),
        Field::Absent => (false, None),
    };
    let updated = sqlx::query(&format!(
        "UPDATE exchange_rates
            SET rate = COALESCE($3::numeric, rate),
                source_note = CASE WHEN $4 THEN $5 ELSE source_note END,
                updated_at = NOW()
          WHERE tenant_id = $1 AND id = $2
          RETURNING {COLS}"
    ))
    .bind(&user.tenant_id)
    .bind(&rate_id)
    .bind(rate_text.as_deref())
    .bind(set_note)
    .bind(note_value.as_deref())
    .fetch_optional(&state.pool)
    .await?;
    let Some(updated) = updated else { return Err(not_found()) };
    let row = row_json(&updated)?;
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CurrencyRateChanged,
        Some(&rate_id), event_details(&row)).await;
    Ok(ok(Value::Object(row)))
}

pub async fn delete(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(rate_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ADMIN_WRITE, &actors).await?;
    auth::require_role(&user, &["admin"])?;
    let gone = sqlx::query(&format!(
        "DELETE FROM exchange_rates WHERE tenant_id = $1 AND id = $2 RETURNING {COLS}"
    ))
    .bind(&user.tenant_id)
    .bind(&rate_id)
    .fetch_optional(&state.pool)
    .await?;
    let Some(gone) = gone else { return Err(not_found()) };
    let row = row_json(&gone)?;
    record_event(&state.pool, &user.tenant_id, &user.user_id, Event::CurrencyRateDeleted,
        Some(&rate_id), event_details(&row)).await;
    Ok(ok(json!({"deleted": true, "id": rate_id})))
}

/// `currency` and `on` from the query, shared by `resolve`.
fn currency_and_date(q: &Query, errs: &mut Errors) -> (Option<String>, NaiveDate) {
    let currency = opt_str(errs, q, "currency", Some(8));
    if currency.is_none() && errs.0.is_empty() {
        errs.push("missing", &[json!("query"), json!("currency")], "Field required".into(), &Value::Null, None);
    }
    let on = match q.get("on") {
        None => today(),
        Some(raw) => match date_fromisoformat(py_strip(raw)) {
            Some(d) => d,
            None => {
                errs.push("date_from_datetime_parsing", &[json!("query"), json!("on")],
                    "Input should be a valid date or datetime, invalid character in year".into(),
                    &json!(raw), Some(json!({"error": "invalid character in year"})));
                today()
            }
        },
    };
    (currency.map(|c| code_of(&c)), on)
}

pub async fn resolve(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let q = Query::parse(raw.as_deref());
    let mut errs = Errors::default();
    let (currency, on) = currency_and_date(&q, &mut errs);
    errs.into_result()?;
    let currency = currency.ok_or_else(ApiError::internal)?;
    let base = currency_code_of(&state.pool, &user.tenant_id).await?;
    if !is_supported(&currency) {
        return Err(unsupported(&currency));
    }
    if currency == base {
        return Ok(ok(json!({"currency": currency, "base_currency": base, "on": isoformat_date(&on),
                            "same_currency": true, "rate": null})));
    }
    match resolve_rate(&state.pool, &user.tenant_id, &currency, &base, on).await? {
        Some(mut row) => {
            let eff = row["effective_date"].as_str().and_then(date_fromisoformat);
            row.insert("age_days".into(), eff.map(|d| json!((on - d).num_days())).unwrap_or(Value::Null));
            Ok(ok(json!({"currency": currency, "base_currency": base, "on": isoformat_date(&on),
                         "same_currency": false, "rate": Value::Object(row)})))
        }
        None => Err(missing_rate(&currency, &base, on)),
    }
}

pub async fn convert(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    // A read-only preview: any role, a read key included.
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let obj = body_object(&body)?;
    let p = [Value::String("body".into())];
    let mut errs = Errors::default();
    let currency = str_field(&mut errs, &obj, &p, "currency", true, false, &StrRules { min_length: Some(1), max_length: Some(8), pattern: None });
    let on_raw = str_field(&mut errs, &obj, &p, "on", false, true, &StrRules { min_length: None, max_length: Some(32), pattern: None });
    if !obj.contains_key("amount") {
        errs.push("missing", &validation::loc(&p, "amount"), "Field required".into(), &Value::Object(obj.clone()), None);
    }
    errs.into_result()?;
    let Field::Value(currency) = currency else { return Err(ApiError::internal()) };
    let amount = dec_text(&obj["amount"]).and_then(|t| Dec::parse(&t)).map_err(|e| {
        ApiError::app("fx_amount_invalid", "The amount must be a non-negative number.", 422,
            json!({"field": "amount", "reason": e.code()}))
    })?;
    let on = match &on_raw {
        Field::Value(d) => parse_date_field(d)?,
        _ => today(),
    };
    let code = code_of(&currency);
    if !is_supported(&code) {
        return Err(unsupported(&code));
    }
    let base = currency_code_of(&state.pool, &user.tenant_id).await?;
    if code == base {
        return Ok(ok(json!({
            "amount": amount.to_text_trimmed(0), "currency": code, "base_currency": base,
            "on": isoformat_date(&on), "same_currency": true,
            "converted": amount.round_money().to_text(), "rate": null,
        })));
    }
    let Some(row) = resolve_rate(&state.pool, &user.tenant_id, &code, &base, on).await? else {
        return Err(missing_rate(&code, &base, on));
    };
    let rate = Dec::parse(row["rate"].as_str().unwrap_or("")).map_err(|_| ApiError::internal())?;
    Ok(ok(json!({
        "amount": amount.to_text_trimmed(0), "currency": code, "base_currency": base,
        "on": isoformat_date(&on), "same_currency": false,
        "converted": fx::convert(&amount, &rate).to_text(),
        "rate": {"id": row["id"], "rate": row["rate"], "effective_date": row["effective_date"],
                 "source_note": row["source_note"]},
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_and_strings_reach_the_core_as_exact_text() {
        assert_eq!(dec_text(&json!("520.50")).unwrap(), "520.50");
        assert_eq!(dec_text(&json!(520)).unwrap(), "520");
        assert_eq!(dec_text(&json!(0.1)).unwrap(), "0.1");
        assert!(dec_text(&json!(null)).is_err());
        assert!(dec_text(&json!(true)).is_err());
        assert!(dec_text(&json!([1])).is_err());
    }

    #[test]
    fn a_rate_is_validated_not_rounded() {
        let ok = |t: &str| dec_text(&json!(t)).and_then(|t| fx::parse_rate(&t));
        assert!(ok("520.5").is_ok());
        assert_eq!(ok("0").unwrap_err().code(), "rate_out_of_range");
        assert_eq!(ok("1.00000000001").unwrap_err().code(), "rate_too_precise");
        assert_eq!(ok("-3").unwrap_err().code(), "not_a_number");
        assert_eq!(rate_invalid(&FxError::RateTooPrecise).body["error_params"]["reason"], "rate_too_precise");
    }

    #[test]
    fn dates_are_bounded() {
        assert!(parse_date_field("2026-10-05").is_ok());
        assert!(parse_date_field(" 2026-10-05 ").is_ok());
        assert!(parse_date_field("1999-12-31").is_err());
        assert!(parse_date_field("not a date").is_err());
        let far = (today() + Duration::days(MAX_DAYS_AHEAD + 1)).format("%Y-%m-%d").to_string();
        assert_eq!(parse_date_field(&far).unwrap_err().code(), Some("fx_rate_date_invalid"));
        let near = (today() + Duration::days(MAX_DAYS_AHEAD)).format("%Y-%m-%d").to_string();
        assert!(parse_date_field(&near).is_ok());
    }

    #[test]
    fn a_missing_rate_says_so_with_its_inputs() {
        let e = missing_rate("USD", "CRC", NaiveDate::from_ymd_opt(2026, 10, 5).unwrap());
        assert_eq!(e.status.as_u16(), 404);
        assert_eq!(e.body["error_code"], "fx_rate_missing");
        assert_eq!(e.body["error_params"], json!({"currency": "USD", "base_currency": "CRC", "on": "2026-10-05"}));
    }

    #[test]
    fn codes_are_normalised() {
        assert_eq!(code_of(" usd "), "USD");
    }
}
