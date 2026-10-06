//! `GET` / `PATCH /api/v1/tenant/currency` (backend/api/v1/currency.py):
//! which currency the company's own money is shown in.
//!
//! GET is every role and a read key (`currency` is an exposed tag). PATCH is
//! `require_role("admin")` only: no key can hold that role, so the exposure
//! rule refuses every key before the role guard runs. A successful PATCH is
//! catalogued in `backend/audit/catalog.py` (`config.changed` on target type
//! `setting`), so this module also writes the row `AuditMiddleware` writes.
//!
//! Also home of the tenant `settings` JSONB helpers (`get_settings` /
//! `update_settings` in backend/tenants/service.py) that `timezone.rs`
//! shares.

use axum::body::Bytes;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::{Extension, Json};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::audit::{self, Note};
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::py_strip;
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, str_field, Errors, Field, NO_STR_RULES};

pub const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
pub const ADMIN_WRITE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("admin only: no API key can hold the admin role"),
    is_mcp: false,
};

/// `SUPPORTED`, in insertion order: (code, symbol, locale, decimals).
const SUPPORTED: [(&str, &str, &str, u8); 13] = [
    ("CRC", "\u{20A1}", "es-CR", 0),
    ("USD", "$", "en-US", 2),
    ("MXN", "$", "es-MX", 2),
    ("GTQ", "Q", "es-GT", 2),
    ("COP", "$", "es-CO", 0),
    ("PAB", "B/.", "es-PA", 2),
    ("HNL", "L", "es-HN", 2),
    ("NIO", "C$", "es-NI", 2),
    ("DOP", "RD$", "es-DO", 2),
    ("PEN", "S/", "es-PE", 2),
    ("CLP", "$", "es-CL", 0),
    ("ARS", "$", "es-AR", 0),
    ("EUR", "\u{20AC}", "es-ES", 2),
];
const DEFAULT_CODE: &str = "CRC";

fn entry(code: &str) -> Option<Value> {
    SUPPORTED.iter().find(|(c, ..)| *c == code).map(|(c, symbol, locale, decimals)| {
        json!({"code": c, "symbol": symbol, "locale": locale, "decimals": decimals})
    })
}

fn sorted_codes() -> Vec<&'static str> {
    let mut v: Vec<&str> = SUPPORTED.iter().map(|(c, ..)| *c).collect();
    v.sort_unstable();
    v
}

// ── tenants.settings (backend/tenants/service.py) ────────────────────────────

/// `get_settings`: the parsed JSONB, `{}` when the tenant is missing or its
/// settings are empty / null.
pub(crate) async fn get_settings(pool: &PgPool, tenant_id: &str) -> Result<Map<String, Value>, sqlx::Error> {
    let row: Option<(Option<Value>,)> = sqlx::query_as("SELECT settings FROM tenants WHERE id = $1")
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    Ok(match row.and_then(|r| r.0) {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    })
}

/// `update_settings`: shallow merge, then one UPDATE of the whole blob (the
/// same read-modify-write Python does, race included).
pub(crate) async fn update_settings(pool: &PgPool, tenant_id: &str, key: &str, value: Value) -> Result<(), sqlx::Error> {
    let mut merged = get_settings(pool, tenant_id).await?;
    merged.insert(key.to_string(), value);
    sqlx::query("UPDATE tenants SET settings = $1 WHERE id = $2")
        .bind(Value::Object(merged))
        .bind(tenant_id)
        .execute(pool)
        .await?;
    Ok(())
}

/// `settings.get(key) or DEFAULT`, then `x not in SUPPORTED`. A list or dict
/// stored there is unhashable in Python: the membership test raises, i.e. a
/// 500. Any other non-string value is simply not supported.
pub(crate) fn supported_or_default<'a>(
    stored: Option<&Value>,
    is_supported: impl Fn(&str) -> bool,
    default: &'a str,
) -> Result<String, ApiError> {
    let v = match stored {
        Some(v) if crate::pycompat::truthy(v) => v,
        _ => return Ok(default.to_string()),
    };
    match v {
        Value::Array(_) | Value::Object(_) => Err(ApiError::internal()),
        Value::String(s) if is_supported(s) => Ok(s.clone()),
        _ => {
            tracing::warn!(stored = %v, default, "unsupported tenant setting, using the default");
            Ok(default.to_string())
        }
    }
}

/// `currency_of`.
pub(crate) async fn currency_of(pool: &PgPool, tenant_id: &str) -> Result<Value, ApiError> {
    let settings = get_settings(pool, tenant_id).await?;
    let code = supported_or_default(settings.get("currency"), |c| entry(c).is_some(), DEFAULT_CODE)?;
    entry(&code).ok_or_else(ApiError::internal)
}

// ── handlers ─────────────────────────────────────────────────────────────────

pub async fn get_currency(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let current = currency_of(&state.pool, &user.tenant_id).await?;
    let supported: Vec<Value> = SUPPORTED.iter().filter_map(|(c, ..)| entry(c)).collect();
    Ok(ok(json!({"current": current, "supported": supported})))
}

/// `code.upper().strip()`.
fn normalise_code(raw: &str) -> String {
    py_strip(&raw.to_uppercase()).to_string()
}

pub async fn set_currency(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ADMIN_WRITE, &actors).await?;
    auth::require_role(&user, &["admin"])?;
    let obj = body_object(&body)?;
    let mut errs = Errors::default();
    let code = str_field(&mut errs, &obj, &[Value::String("body".into())], "code", true, false, &NO_STR_RULES);
    errs.into_result()?;
    let Field::Value(code) = code else { return Err(ApiError::internal()) };

    let code = normalise_code(&code);
    if entry(&code).is_none() {
        return Err(ApiError::app(
            "currency_not_supported",
            format!("Currency '{code}' is not supported."),
            400,
            json!({"code": code, "supported": sorted_codes()}),
        ));
    }
    let previous = currency_of(&state.pool, &user.tenant_id).await?["code"].clone();
    update_settings(&state.pool, &user.tenant_id, "currency", Value::String(code.clone())).await?;
    tracing::info!("[currency] tenant {} -> {} (by {})", user.tenant_id, code, user.user_id);
    let current = currency_of(&state.pool, &user.tenant_id).await?;
    // AuditMiddleware's row, with the handler's audit.note.
    let note = Note {
        target_id: Some("currency".into()),
        label: Some("currency".into()),
        before: Some(json!({"currency": previous})),
        after: Some(json!({"currency": code})),
    };
    audit::record(&state, &actors, "PATCH", "/tenant/currency", None, note, 200).await;
    Ok(ok(json!({"current": current})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_normalise_like_python() {
        assert_eq!(normalise_code(" usd "), "USD");
        assert_eq!(normalise_code("\u{df}"), "SS");
        assert!(entry("EUR").is_some() && entry("eur").is_none());
        assert_eq!(sorted_codes()[0], "ARS");
    }

    #[test]
    fn stored_values_fall_back_or_fail_like_python() {
        let sup = |c: &str| entry(c).is_some();
        assert_eq!(supported_or_default(None, sup, "CRC").unwrap(), "CRC");
        assert_eq!(supported_or_default(Some(&json!("")), sup, "CRC").unwrap(), "CRC");
        assert_eq!(supported_or_default(Some(&json!("XXX")), sup, "CRC").unwrap(), "CRC");
        assert_eq!(supported_or_default(Some(&json!(5)), sup, "CRC").unwrap(), "CRC");
        assert_eq!(supported_or_default(Some(&json!("USD")), sup, "CRC").unwrap(), "USD");
        assert!(supported_or_default(Some(&json!(["USD"])), sup, "CRC").is_err());
        // An empty list is falsy: `or DEFAULT` wins before the membership test.
        assert_eq!(supported_or_default(Some(&json!([])), sup, "CRC").unwrap(), "CRC");
    }

    #[test]
    fn symbols_are_the_python_ones() {
        let src = include_str!("../../../../backend/api/v1/currency.py");
        for (code, symbol, locale, decimals) in SUPPORTED {
            let line = src.lines().find(|l| l.trim_start().starts_with(&format!("\"{code}\""))).unwrap();
            assert!(line.contains(&format!("\"symbol\": \"{symbol}\"")), "{code}");
            assert!(line.contains(&format!("\"locale\": \"{locale}\"")), "{code}");
            assert!(line.contains(&format!("\"decimals\": {decimals}")), "{code}");
        }
    }
}
