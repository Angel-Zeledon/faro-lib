//! The semáforo's lead-time multipliers: `backend/api/v1/signal_thresholds.py`
//! and the storage half of `backend/inventory/signal_thresholds.py`.
//!
//! Migrated: `GET`, `PUT` and `DELETE /inventory/signal-thresholds`. They read
//! and write the factor columns of `stock_defaults` rows (the planning-rule
//! cascade's table) and nothing else; the `status_bump_stock_defaults_*`
//! triggers invalidate the inventory snapshot whichever service wrote.
//!
//! NOT migrated: `POST /inventory/signal-thresholds/preview`. It runs the real
//! semáforo twice (`service._compute_inventory_status`), which is the inventory
//! hub; it stays Python and the proxy keeps that path there.
//!
//! No warehouse scope: the Python router applies none to these three (the
//! factors are tenant rules, not per-warehouse figures), and neither does this
//! port. `PUT` and `DELETE` are catalogued routes, so a success also writes the
//! `audit.config.changed` row `AuditMiddleware` writes.

use axum::body::Bytes;
use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use percent_encoding::percent_decode_str;
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::audit::{self, Note};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{isoformat_utc, py_strip};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, float_field, loc, str_field, Errors, Field, NO_STR_RULES};

/// `INTERNAL_TAGS["inventory-signal-thresholds"]`, as `exposure()` words it.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'inventory-signal-thresholds': the traffic-light cut-offs move every recommendation the tenant sees; a person changes them on the rules panel, with its preview",
    ),
    is_mcp: false,
};

const DEFAULT_ORDER_NOW_FACTOR: f64 = 0.5;
const DEFAULT_OVERSTOCK_FACTOR: f64 = 3.0;
const OVERSTOCK_REORDER_POINT_MULTIPLE: f64 = 2.0;
/// (field, low, high, Python's `str(low)`, `str(high)`) - the message prints
/// the bounds with Python's float repr ("12.0", not "12").
const FACTOR_BOUNDS: [(&str, f64, f64, &str, &str); 2] = [
    ("order_now_factor", 0.1, 0.95, "0.1", "0.95"),
    ("overstock_factor", 1.5, 12.0, "1.5", "12.0"),
];
const SCOPE_TYPES: [&str; 3] = ["global", "supplier", "category"];
const SOURCE_DEFAULT: &str = "default";

// ── Pure rules ───────────────────────────────────────────────────────────────

/// Python's `round(x, 2)`: correctly rounded on the exact binary value, ties
/// to even. Rust's `{:.2}` formats the exact binary value too, and its tie
/// rule is checked against Python in the tests below.
fn py_round2(x: f64) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.2}").parse().unwrap_or(x)
}

/// `str(float)` for the values `validate_thresholds` can still reject after
/// pydantic coerced them to floats: the non-finite ones.
fn py_float_str(x: f64) -> String {
    if x.is_nan() {
        "nan".into()
    } else if x == f64::INFINITY {
        "inf".into()
    } else if x == f64::NEG_INFINITY {
        "-inf".into()
    } else {
        // Not reachable from the routes (a finite value never fails here).
        format!("{x:?}")
    }
}

/// `validate_thresholds`: both factors, finite, in bounds, increasing.
fn validate_thresholds(order_now: Option<f64>, overstock: Option<f64>) -> Result<(f64, f64), ApiError> {
    let mut clean = [0.0f64; 2];
    for (i, raw) in [order_now, overstock].into_iter().enumerate() {
        let (field, low, high, low_s, high_s) = FACTOR_BOUNDS[i];
        let Some(num) = raw else {
            return Err(ApiError::app("signal_thresholds_missing_field",
                format!("{field} is required: the factors are saved together"), 422,
                json!({"field": field})));
        };
        if !num.is_finite() {
            return Err(ApiError::app("signal_thresholds_not_a_number",
                format!("{field} must be a finite number"), 422,
                json!({"field": field, "value": py_float_str(num)})));
        }
        if num < low || num > high {
            return Err(ApiError::app("signal_thresholds_out_of_range",
                format!("{field} must be between {low_s} and {high_s}"), 422,
                json!({"field": field, "value": num, "min": low, "max": high})));
        }
        clean[i] = py_round2(num);
    }
    if !(clean[0] < clean[1]) {
        return Err(ApiError::app("signal_thresholds_not_increasing",
            "The 'order now' factor must be below the overstock factor", 422,
            json!({"lower": "order_now_factor", "upper": "overstock_factor",
                   "lower_value": clean[0], "upper_value": clean[1]})));
    }
    Ok((clean[0], clean[1]))
}

/// `_normalize_scope_value`: global is "", an override is stripped + lowered.
fn normalize_scope_value(scope_type: &str, scope_value: Option<&str>) -> String {
    if scope_type == "global" {
        return String::new();
    }
    py_strip(scope_value.unwrap_or("")).to_lowercase()
}

/// `_validate_scope`. `scope_type` already passed the Literal, so only the
/// missing override name can fail here.
fn validate_scope(scope_type: &str, scope_value: Option<&str>) -> Result<String, ApiError> {
    if !SCOPE_TYPES.contains(&scope_type) {
        return Err(ApiError::app("signal_thresholds_bad_scope",
            format!("scope_type must be one of {}", SCOPE_TYPES.join(", ")), 422,
            json!({"scope_type": scope_type, "allowed": SCOPE_TYPES})));
    }
    let value = normalize_scope_value(scope_type, scope_value);
    if scope_type != "global" && value.is_empty() {
        return Err(ApiError::app("signal_thresholds_missing_scope_value",
            "A supplier or category override needs a name", 422, json!({"scope_type": scope_type})));
    }
    Ok(value)
}

/// pydantic's error for `Literal["global", "supplier", "category"]`.
fn literal_error(errs: &mut Errors, at: &[Value], input: &Value) {
    errs.push("literal_error", at, "Input should be 'global', 'supplier' or 'category'".into(), input,
        Some(json!({"expected": "'global', 'supplier' or 'category'"})));
}

fn scope_literal(v: &str) -> bool {
    SCOPE_TYPES.contains(&v)
}

// ── Storage ──────────────────────────────────────────────────────────────────

type FactorRow = (String, String, f64, f64, Option<DateTime<Utc>>);

const SELECT_COLS: &str = "scope_type, scope_value, order_now_factor, overstock_factor, updated_at";

/// `_row_public`.
fn row_public(r: &FactorRow) -> Value {
    json!({
        "scope_type": r.0,
        "scope_value": if r.1.is_empty() { Value::Null } else { Value::String(r.1.clone()) },
        "order_now_factor": r.2,
        "overstock_factor": r.3,
        "updated_at": r.4.map(|d| isoformat_utc(&d)),
    })
}

fn factors(order_now: f64, overstock: f64) -> Value {
    json!({"order_now_factor": order_now, "overstock_factor": overstock})
}

/// `get_signal_thresholds`.
async fn get_signal_thresholds(pool: &PgPool, tenant_id: &str) -> Result<Map<String, Value>, ApiError> {
    let rows: Vec<FactorRow> = sqlx::query_as(&format!(
        "SELECT {SELECT_COLS}
           FROM stock_defaults
          WHERE tenant_id = $1
            AND order_now_factor IS NOT NULL
            AND overstock_factor IS NOT NULL
          ORDER BY scope_type, scope_value"
    ))
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let tenant_row = rows.iter().find(|r| r.0 == "global");
    let overrides: Vec<Value> = rows.iter().filter(|r| r.0 != "global").map(row_public).collect();
    let effective = match tenant_row {
        Some(r) => factors(r.2, r.3),
        None => factors(DEFAULT_ORDER_NOW_FACTOR, DEFAULT_OVERSTOCK_FACTOR),
    };
    let mut bounds = Map::new();
    for (field, low, high, ..) in FACTOR_BOUNDS {
        bounds.insert(field.into(), json!({"min": low, "max": high}));
    }
    let mut out = Map::new();
    out.insert("defaults".into(), factors(DEFAULT_ORDER_NOW_FACTOR, DEFAULT_OVERSTOCK_FACTOR));
    out.insert("tenant".into(), tenant_row.map(row_public).unwrap_or(Value::Null));
    out.insert("effective".into(), effective);
    out.insert("source".into(), json!(if tenant_row.is_some() { "global" } else { SOURCE_DEFAULT }));
    out.insert("overrides".into(), Value::Array(overrides));
    out.insert("bounds".into(), Value::Object(bounds));
    out.insert("overstock_reorder_point_multiple".into(), json!(OVERSTOCK_REORDER_POINT_MULTIPLE));
    Ok(out)
}

/// `set_signal_thresholds`.
async fn set_signal_thresholds(
    pool: &PgPool,
    tenant_id: &str,
    scope_type: &str,
    scope_value: Option<&str>,
    order_now: Option<f64>,
    overstock: Option<f64>,
) -> Result<Value, ApiError> {
    let key = validate_scope(scope_type, scope_value)?;
    let (on, os) = validate_thresholds(order_now, overstock)?;
    sqlx::query(
        "INSERT INTO stock_defaults
             (tenant_id, scope_type, scope_value, order_now_factor, overstock_factor)
         VALUES ($1, $2, $3, $4, $5)
         ON CONFLICT (tenant_id, scope_type, scope_value)
         DO UPDATE SET order_now_factor = EXCLUDED.order_now_factor,
                       overstock_factor = EXCLUDED.overstock_factor,
                       updated_at = NOW()",
    )
    .bind(tenant_id)
    .bind(scope_type)
    .bind(&key)
    .bind(on)
    .bind(os)
    .execute(pool)
    .await?;
    let row: Option<(String, String, Option<f64>, Option<f64>, Option<DateTime<Utc>>)> = sqlx::query_as(&format!(
        "SELECT {SELECT_COLS}
           FROM stock_defaults
          WHERE tenant_id = $1 AND scope_type = $2 AND scope_value = $3"
    ))
    .bind(tenant_id)
    .bind(scope_type)
    .bind(&key)
    .fetch_optional(pool)
    .await?;
    // Never report a save that did not land.
    match row {
        Some((t, v, Some(a), Some(b), u)) if a == on && b == os => Ok(row_public(&(t, v, a, b, u))),
        _ => Err(ApiError::app("signal_thresholds_not_saved", "The thresholds could not be saved", 500, json!({}))),
    }
}

/// `clear_signal_thresholds`: clear the factor columns of one scope's row, and
/// drop the row when nothing else is left on it.
async fn clear_signal_thresholds(
    pool: &PgPool,
    tenant_id: &str,
    scope_type: &str,
    scope_value: Option<&str>,
) -> Result<bool, ApiError> {
    let key = validate_scope(scope_type, scope_value)?;
    let existing: Option<(String,)> = sqlx::query_as(
        "SELECT id FROM stock_defaults
          WHERE tenant_id = $1 AND scope_type = $2 AND scope_value = $3
            AND order_now_factor IS NOT NULL",
    )
    .bind(tenant_id)
    .bind(scope_type)
    .bind(&key)
    .fetch_optional(pool)
    .await?;
    let Some((id,)) = existing else { return Ok(false) };
    sqlx::query(
        "UPDATE stock_defaults
            SET order_now_factor = NULL, overstock_factor = NULL, updated_at = NOW()
          WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&id)
    .bind(tenant_id)
    .execute(pool)
    .await?;
    sqlx::query(
        "DELETE FROM stock_defaults
          WHERE id = $1 AND tenant_id = $2
            AND lead_time_days IS NULL AND service_level IS NULL
            AND moq IS NULL AND holding_cost_pct IS NULL
            AND order_now_factor IS NULL AND overstock_factor IS NULL",
    )
    .bind(&id)
    .bind(tenant_id)
    .execute(pool)
    .await?;
    Ok(true)
}

/// What `AuditMiddleware` writes after a successful catalogued call
/// (`ROUTES[(method, "/inventory/signal-thresholds")] = config.changed /
/// setting`). These handlers add no `audit.note`, so target, label, before
/// and after are all null.
async fn audit_config_changed(state: &AppState, actors: &RequestActors, method: &str) {
    audit::record(state, actors, method, "/inventory/signal-thresholds", None, Note::default(), 200).await;
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn get_thresholds(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    Ok(ok(Value::Object(get_signal_thresholds(&state.pool, &user.tenant_id).await?)))
}

pub async fn put_thresholds(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let obj = body_object(&body)?;

    // ThresholdsBody, in field order.
    let mut errs = Errors::default();
    let p = [Value::String("body".into())];
    let scope_type = match obj.get("scope_type") {
        None => Some("global".to_string()),
        Some(Value::String(s)) if scope_literal(s) => Some(s.clone()),
        Some(other) => {
            literal_error(&mut errs, &loc(&p, "scope_type"), other);
            None
        }
    };
    let scope_value = str_field(&mut errs, &obj, &p, "scope_value", false, true, &NO_STR_RULES);
    let order_now = float_field(&mut errs, &obj, &p, "order_now_factor", false, true, None, None);
    let overstock = float_field(&mut errs, &obj, &p, "overstock_factor", false, true, None, None);
    errs.into_result()?;
    let scope_type = scope_type.ok_or_else(ApiError::internal)?;
    let val = |f: Field<f64>| match f { Field::Value(v) => Some(v), _ => None };
    let scope_value = match scope_value { Field::Value(v) => Some(v), _ => None };

    let saved = set_signal_thresholds(&state.pool, &user.tenant_id, &scope_type, scope_value.as_deref(),
        val(order_now), val(overstock)).await?;
    let mut out = Map::new();
    out.insert("saved".into(), saved);
    out.extend(get_signal_thresholds(&state.pool, &user.tenant_id).await?);
    audit_config_changed(&state, &actors, "PUT").await;
    Ok(ok(Value::Object(out)))
}

/// Starlette's `parse_qsl(keep_blank_values=True)`, last value wins (what
/// `QueryParams.get` returns for a repeated key).
fn query_param(raw: Option<&str>, name: &str) -> Option<String> {
    let mut found = None;
    for pair in raw.unwrap_or("").split('&') {
        if pair.is_empty() {
            continue;
        }
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        let decode = |s: &str| percent_decode_str(&s.replace('+', " ")).decode_utf8_lossy().into_owned();
        if decode(k) == name {
            found = Some(decode(v));
        }
    }
    found
}

pub async fn reset_thresholds(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw_query): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;

    let mut errs = Errors::default();
    let q = [Value::String("query".into())];
    let scope_type = match query_param(raw_query.as_deref(), "scope_type") {
        None => Some("global".to_string()),
        Some(s) if scope_literal(&s) => Some(s),
        Some(other) => {
            literal_error(&mut errs, &loc(&q, "scope_type"), &Value::String(other));
            None
        }
    };
    let scope_value = query_param(raw_query.as_deref(), "scope_value");
    errs.into_result()?;
    let scope_type = scope_type.ok_or_else(ApiError::internal)?;

    let cleared = clear_signal_thresholds(&state.pool, &user.tenant_id, &scope_type, scope_value.as_deref()).await?;
    let mut out = Map::new();
    out.insert("cleared".into(), Value::Bool(cleared));
    out.extend(get_signal_thresholds(&state.pool, &user.tenant_id).await?);
    audit_config_changed(&state, &actors, "DELETE").await;
    Ok(ok(Value::Object(out)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round2_matches_python_round() {
        // Values printed by CPython 3.12: [round(x, 2) for x in ...]
        let cases = [
            (0.125, 0.12), (0.375, 0.38), (0.625, 0.62), (0.875, 0.88), (2.675, 2.67),
            (1.005, 1.0), (0.285, 0.28), (0.345, 0.34), (0.955, 0.95), (0.1, 0.1),
            (11.999, 12.0), (12.0, 12.0), (0.15, 0.15), (0.25, 0.25), (1.125, 1.12),
        ];
        for (x, want) in cases {
            assert_eq!(py_round2(x), want, "round({x}, 2)");
        }
    }

    #[test]
    fn validation_order_and_codes() {
        let code = |r: Result<(f64, f64), ApiError>| r.unwrap_err().code().unwrap().to_string();
        assert_eq!(code(validate_thresholds(None, None)), "signal_thresholds_missing_field");
        let e = validate_thresholds(Some(0.5), None).unwrap_err();
        assert_eq!(e.body["error_params"], json!({"field": "overstock_factor"}));
        let e = validate_thresholds(Some(f64::INFINITY), Some(3.0)).unwrap_err();
        assert_eq!(e.body["error_params"]["value"], "inf");
        assert_eq!(e.body["detail"], "order_now_factor must be a finite number");
        let e = validate_thresholds(Some(0.5), Some(13.0)).unwrap_err();
        assert_eq!(e.body["detail"], "overstock_factor must be between 1.5 and 12.0");
        assert_eq!(e.body["error_params"], json!({"field": "overstock_factor", "value": 13.0, "min": 1.5, "max": 12.0}));
        assert_eq!(validate_thresholds(Some(0.333), Some(2.999)).unwrap(), (0.33, 3.0));
    }

    #[test]
    fn scope_normalisation() {
        assert_eq!(validate_scope("global", Some("ignored")).unwrap(), "");
        assert_eq!(validate_scope("supplier", Some("  ACME ")).unwrap(), "acme");
        let e = validate_scope("category", Some("   ")).unwrap_err();
        assert_eq!(e.code(), Some("signal_thresholds_missing_scope_value"));
    }

    #[test]
    fn query_parsing_like_starlette() {
        assert_eq!(query_param(Some("scope_type=supplier&scope_value=Acme+Co"), "scope_value").as_deref(), Some("Acme Co"));
        assert_eq!(query_param(Some("scope_type=a&scope_type=b"), "scope_type").as_deref(), Some("b"));
        assert_eq!(query_param(Some("scope_value"), "scope_value").as_deref(), Some(""));
        assert_eq!(query_param(Some("x=%C3%A9"), "x").as_deref(), Some("é"));
        assert_eq!(query_param(None, "x"), None);
    }
}
