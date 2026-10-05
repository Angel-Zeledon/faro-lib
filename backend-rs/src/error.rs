//! The error half of the wire contract.
//!
//! Python answers every failure with one of three body shapes, decided by the
//! exception handlers in `backend/main.py`:
//!
//! * `AppError`        -> `{"detail": msg, "error_code": code, "error_params": {...}}`
//! * `HTTPException`   -> `{"detail": ...}` plus `error_code`/`error_params` when
//!                        the detail is a known sentence (`backend/error_codes.py`)
//!                        or a dict carrying a string `code`
//! * validation        -> `{"detail": [pydantic errors], "error_code": "validation_error"}`
//!
//! plus the 500 `internal_error` and 503 `server_busy` fallbacks. [`ApiError`]
//! builds each of them from the same inputs the Python raise sites use, so a
//! handler ported from Python keeps its error code, its params AND its English
//! fallback sentence.

use std::sync::OnceLock;

use axum::http::{HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use regex::Regex;
use serde_json::{json, Map, Value};

#[derive(Debug, Clone)]
pub struct ApiError {
    pub status: StatusCode,
    pub body: Value,
    pub headers: Vec<(HeaderName, HeaderValue)>,
}

impl ApiError {
    /// `raise AppError(code, message, status_code=..., params=...)`.
    pub fn app(code: &str, message: impl Into<String>, status: u16, params: Value) -> Self {
        let params = if params.is_null() { json!({}) } else { params };
        ApiError {
            status: StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            body: json!({"detail": message.into(), "error_code": code, "error_params": params}),
            headers: Vec::new(),
        }
    }

    /// `raise HTTPException(status, detail="sentence")`, run through
    /// `describe_http_error` exactly as `http_exception_handler` does.
    pub fn http(status: u16, detail: impl Into<String>) -> Self {
        let detail = detail.into();
        let mut body = Map::new();
        body.insert("detail".into(), Value::String(detail.clone()));
        if let Some((code, params)) = describe_http_error(&detail) {
            body.insert("error_code".into(), Value::String(code));
            body.insert("error_params".into(), Value::Object(params));
        }
        ApiError {
            status: StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            body: Value::Object(body),
            headers: Vec::new(),
        }
    }

    /// `raise HTTPException(status, detail={"code": ..., ...})`: the code is
    /// lifted to `error_code` and the rest to `error_params`; `detail` is kept.
    pub fn http_dict(status: u16, detail: Value) -> Self {
        let mut body = Map::new();
        body.insert("detail".into(), detail.clone());
        if let Some(obj) = detail.as_object() {
            if let Some(Value::String(code)) = obj.get("code") {
                body.insert("error_code".into(), Value::String(code.clone()));
                let params: Map<String, Value> = obj
                    .iter()
                    .filter(|(k, _)| k.as_str() != "code")
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect();
                body.insert("error_params".into(), Value::Object(params));
            }
        }
        ApiError {
            status: StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            body: Value::Object(body),
            headers: Vec::new(),
        }
    }

    /// FastAPI's `RequestValidationError`, as `validation_error_handler`
    /// serializes it (note: no `error_params`, on purpose, like Python).
    pub fn validation(errors: Vec<Value>) -> Self {
        ApiError {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            body: json!({"detail": errors, "error_code": "validation_error"}),
            headers: Vec::new(),
        }
    }

    /// `unhandled_error_handler`: a stable code and nothing about the internals.
    pub fn internal() -> Self {
        ApiError {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            body: json!({
                "detail": "An unexpected error occurred.",
                "error_code": "internal_error",
                "error_params": {},
            }),
            headers: Vec::new(),
        }
    }

    /// `pool_exhausted_handler`: busy is not broken.
    pub fn server_busy(detail: &str) -> Self {
        ApiError {
            status: StatusCode::SERVICE_UNAVAILABLE,
            body: json!({"detail": detail, "error_code": "server_busy", "error_params": {}}),
            headers: vec![(HeaderName::from_static("retry-after"), HeaderValue::from_static("2"))],
        }
    }

    pub fn with_header(mut self, name: &'static str, value: &str) -> Self {
        if let Ok(v) = HeaderValue::from_str(value) {
            self.headers.push((HeaderName::from_static(name), v));
        }
        self
    }

    #[cfg(test)]
    pub fn code(&self) -> Option<&str> {
        self.body.get("error_code").and_then(Value::as_str)
    }
}

impl From<sqlx::Error> for ApiError {
    fn from(err: sqlx::Error) -> Self {
        match err {
            // The Python pool raises PoolExhausted when every connection is out;
            // sqlx's equivalent is a checkout that timed out.
            sqlx::Error::PoolTimedOut => {
                tracing::warn!("database pool exhausted");
                ApiError::server_busy(
                    "All database connections were busy for 10s. The request was not attempted.",
                )
            }
            other => {
                tracing::error!(error = %other, "unhandled database error");
                ApiError::internal()
            }
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let mut resp = (self.status, Json(self.body)).into_response();
        for (k, v) in self.headers {
            resp.headers_mut().insert(k, v);
        }
        resp
    }
}

// ── backend/error_codes.py, ported rule for rule ─────────────────────────────

const RULES: &[(&str, &str)] = &[
    (r"API key is invalid or expired", "api_key_invalid"),
    (r"Not authenticated", "unauthenticated"),
    (r"Not Found", "not_found"),
    (r"Method Not Allowed", "method_not_allowed"),
    (r"Rate limit exceeded: \d+ requests per minute per API key, .*", "rate_limited"),
    (r"Token expired", "token_expired"),
    (r"Invalid token( type)?(: .*)?", "token_invalid"),
    (r"Token has been revoked", "token_revoked"),
    (r"Access denied", "access_denied"),
    (r"API key not found", "api_key_not_found"),
    (r"Session not found", "session_not_found"),
    (r"Session .+ not found", "session_not_found"),
    (r"Chat not found", "chat_not_found"),
    (r"Document not found", "document_not_found"),
    (r"File not found on disk", "document_file_missing"),
    (r"Artifact not found", "artifact_not_found"),
    (r"Webhook not found", "webhook_not_found"),
    (r"Tenant not found.*", "tenant_not_found"),
    (r"Transfer not found", "transfer_not_found"),
    (r"User not found", "user_not_found"),
    (r"Warehouse '(?P<warehouse>.+)' not found", "warehouse_not_found"),
    (r"No filename provided", "file_name_missing"),
    (r"File is empty .*", "file_empty"),
    (r"Unsupported file type '\.(?P<ext>.*)'\. Allowed: .*", "file_type_unsupported"),
    (r"File type '(?P<ext>.*)' not supported\. Allowed: .*", "file_type_unsupported"),
    (r"File too large \((?P<size_mb>\d+) MB\)\. Max (?P<max_mb>\d+) MB\.", "file_too_large"),
    (r"File size (?P<size_mb>[\d.]+) MB exceeds limit of (?P<max_mb>\d+) MB", "file_too_large"),
    (r"Session must be COMPLETED to query the analyst", "analyst_session_not_completed"),
    (r"Invalid state transition: (?P<current>\w+) → (?P<target>\w+)\..*", "session_invalid_transition"),
    (r"Too many active training jobs \((?P<active>\d+)\)\..*", "too_many_active_jobs"),
    (r"Demo dataset not bundled on this server", "demo_dataset_unavailable"),
    (r"'question' (field )?is required", "question_required"),
    (r"'question' exceeds maximum length of (?P<max>\d+) characters", "question_too_long"),
    (r"Rate limit exceeded: max (?P<max>\d+) messages per (?P<window>\d+)s .*", "chat_rate_limited"),
    (r"(ML library|forecasting_core) not available", "ml_library_unavailable"),
    (r"(Inspection|Analysis|Quality check) failed.*", "analysis_failed"),
    (r"Could not load dataset.*", "dataset_load_failed"),
    (r"AI generation failed", "ai_generation_failed"),
    (r"No valid fields to update", "no_valid_fields"),
    (r"A SKU cannot be its own component", "bom_self_component"),
    (r"multiplier must be greater than 0", "multiplier_must_be_positive"),
    (r"Origin and destination warehouses must differ", "transfer_same_warehouse"),
    (r"Origin and destination warehouses are required", "transfer_warehouses_required"),
    (r"A transfer needs at least one item", "transfer_no_items"),
    (r"Every transfer line needs a SKU", "transfer_line_sku_required"),
    (r"Quantity for '(?P<sku>.+)' must be positive", "transfer_qty_must_be_positive"),
    (r"Insufficient stock of '(?P<sku>.+)' in '(?P<warehouse>.+)' \((?P<available>[\d.]+) available, (?P<requested>[\d.]+) requested\)", "transfer_insufficient_stock"),
    (r"Insufficient stock of '(?P<sku>.+)' in '(?P<warehouse>.+)' .*", "transfer_insufficient_stock_concurrent"),
    (r"This transfer cannot be received \(status: (?P<status>\w+)\)", "transfer_not_receivable"),
    (r"Nothing to receive", "transfer_nothing_to_receive"),
    (r"SKU '(?P<sku>.+)' is not part of this transfer", "transfer_sku_not_in_transfer"),
    (r"'(?P<sku>.+)': receiving (?P<qty>[\d.]+) but only (?P<outstanding>[\d.]+) outstanding", "transfer_over_outstanding"),
    (r"Only in-transit transfers with nothing received can be cancelled", "transfer_not_cancellable"),
    (r"Invalid role\. Options: .*", "role_invalid"),
    (r"Invalid status\. Options: .*", "status_invalid"),
    (r"Invalid language\. Options: .*", "language_invalid"),
    (r"Invalid theme\. Options: .*", "theme_invalid"),
    (r"Message body is empty", "message_empty"),
    (r"Cannot send a message to yourself", "message_to_self"),
    (r"Recipient is not active", "message_recipient_inactive"),
    (r"Confirmation required: .*", "confirmation_required"),
    (r"Invalid report type\. Options: .*", "report_type_invalid"),
    (r"(formats must be a non-empty list|Unknown format.*)", "report_format_invalid"),
];

fn compiled_rules() -> &'static Vec<(Regex, &'static str)> {
    static CELL: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    CELL.get_or_init(|| {
        RULES
            .iter()
            // `re.fullmatch` with DOTALL.
            .map(|(p, c)| (Regex::new(&format!("(?s)^(?:{p})$")).expect("valid rule"), *c))
            .collect()
    })
}

/// `describe_http_error`: `(code, params)` for a known sentence.
pub fn describe_http_error(detail: &str) -> Option<(String, Map<String, Value>)> {
    for (re, code) in compiled_rules() {
        if let Some(caps) = re.captures(detail) {
            let mut params = Map::new();
            for name in re.capture_names().flatten() {
                if let Some(m) = caps.name(name) {
                    params.insert(name.to_string(), Value::String(m.as_str().to_string()));
                }
            }
            return Some(((*code).to_string(), params));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_sentences_get_their_code() {
        assert_eq!(describe_http_error("Not authenticated").unwrap().0, "unauthenticated");
        assert_eq!(
            describe_http_error("Invalid token: Signature verification failed").unwrap().0,
            "token_invalid"
        );
        assert_eq!(describe_http_error("Invalid token type").unwrap().0, "token_invalid");
        let (code, params) = describe_http_error("Warehouse 'north' not found").unwrap();
        assert_eq!(code, "warehouse_not_found");
        assert_eq!(params["warehouse"], "north");
    }

    #[test]
    fn fullmatch_not_search() {
        assert!(describe_http_error("Token expired soon").is_none());
        assert!(describe_http_error("x Not Found").is_none());
    }

    #[test]
    fn http_without_rule_has_detail_only() {
        let e = ApiError::http(400, "something nobody catalogued");
        assert_eq!(e.body, json!({"detail": "something nobody catalogued"}));
    }

    #[test]
    fn dict_detail_is_lifted() {
        let e = ApiError::http_dict(403, json!({"code": "TRIAL_EXPIRED", "trial_ends_at": null}));
        assert_eq!(e.body["error_code"], "TRIAL_EXPIRED");
        assert_eq!(e.body["error_params"], json!({"trial_ends_at": null}));
        assert_eq!(e.body["detail"]["code"], "TRIAL_EXPIRED");
    }
}
