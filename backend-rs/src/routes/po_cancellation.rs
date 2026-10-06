//! Purchase-order cancellation: `backend/api/v1/po_cancellation.py` and
//! `backend/inventory/po_cancel_service.py`.
//!
//! Migrated: `POST /inventory/po/{po_log_id}/cancel` and `/uncancel`.
//!
//! The cancellation is a marker (`cancelled_at`, `cancelled_by`,
//! `cancel_reason` on `inventory_po_log`). Every reader that matters - units
//! "on the way", overdue receptions, open orders per supplier, payables, the
//! recap - filters on `cancelled_at IS NULL` at query time, and the
//! `status_bump_inventory_po_log_update` trigger invalidates the inventory
//! snapshot whichever service wrote the row. So writing the marker is the
//! whole effect, and those readers can stay in Python.
//!
//! Order of checks (FastAPI): the JSON body is decoded before anything else
//! (a syntax error is a 422 even without a token), then the user, the role +
//! trial guard and the warehouse scope (`po_guard`), and only then is the
//! optional body validated against `CancelRequest`.

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::activity::{record_event_with_reason, Event};
use crate::auth::{Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{isoformat_utc, py_strip, take_chars};
use crate::routes::ok;
use crate::routes::po_payments::{format_po_number, po_not_found, po_number_json, po_writer};
use crate::state::AppState;
use crate::validation::{self, as_object, str_field, Body, Errors, Field, StrRules};

/// `INTERNAL_TAGS["inventory-cancellation"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'inventory-cancellation': cancelling / reopening a PO from the /pedidos screen",
    ),
    is_mcp: false,
};

/// `po_cancel_service.MAX_REASON_LENGTH`.
pub const MAX_REASON_LENGTH: usize = 500;

/// `repr(bytes)`. A non-JSON body reaches this OPTIONAL model as raw bytes,
/// and the 422's `input` is their `str()` (measured: `"b'stop it'"`), unlike
/// a required model where FastAPI hands over the decoded text.
fn py_bytes_repr(raw: &[u8]) -> String {
    let quote = if raw.contains(&b'\'') && !raw.contains(&b'"') { '"' } else { '\'' };
    let mut out = String::from("b");
    out.push(quote);
    for &b in raw {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b if b as char == quote => {
                out.push('\\');
                out.push(quote);
            }
            0x20..=0x7e => out.push(b as char),
            _ => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote);
    out
}

/// `body: Optional[CancelRequest] = None`: `Ok(None)` for no body or JSON
/// null, the reason otherwise, or the pydantic 422.
fn validate_cancel_body(body: &Body, raw: &[u8]) -> Result<Option<String>, ApiError> {
    let at = [Value::String("body".into())];
    let mut errs = Errors::default();
    let value = match body {
        Body::Missing | Body::Json(Value::Null) => return Ok(None),
        Body::NotJson(_) => Value::String(py_bytes_repr(raw)),
        Body::Json(v) => v.clone(),
    };
    let Some(obj) = as_object(&mut errs, &at, &value) else {
        errs.into_result()?;
        return Err(ApiError::internal());
    };
    let reason = str_field(&mut errs, obj, &at, "reason", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_REASON_LENGTH), pattern: None });
    errs.into_result()?;
    Ok(match reason {
        Field::Value(r) => Some(r),
        _ => None,
    })
}

struct CancelPo {
    po_number: Option<i32>,
    reception_status: String,
    paid_at: Option<DateTime<Utc>>,
    cancelled_at: Option<DateTime<Utc>>,
    cancel_reason: Option<String>,
    received_units: f64,
}

/// `po_cancel_service._get_po`.
async fn get_po(pool: &PgPool, tenant_id: &str, po_log_id: &str) -> Result<CancelPo, ApiError> {
    #[allow(clippy::type_complexity)]
    let row: Option<(Option<i32>, String, Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<String>, f64)> =
        sqlx::query_as(
            "SELECT l.po_number, l.reception_status, l.paid_at,
                    l.cancelled_at, l.cancel_reason,
                    COALESCE((SELECT SUM(COALESCE(i.received_qty, 0))
                                FROM inventory_po_items i
                               WHERE i.po_log_id = l.id), 0)::float8 AS received_units
               FROM inventory_po_log l
              WHERE l.id = $1 AND l.tenant_id = $2",
        )
        .bind(po_log_id)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    let (po_number, reception_status, paid_at, cancelled_at, cancel_reason, received_units) =
        row.ok_or_else(po_not_found)?;
    Ok(CancelPo { po_number, reception_status, paid_at, cancelled_at, cancel_reason, received_units })
}

fn after_payment() -> ApiError {
    ApiError::app("po_cancel_after_payment",
        "This order is marked as paid; unmark the payment before cancelling it", 409, json!({}))
}

fn unchanged(po_log_id: &str, po: &CancelPo) -> Value {
    json!({
        "po_log_id": po_log_id,
        "po_number": po_number_json(po.po_number),
        "cancelled_at": po.cancelled_at.map(|d| isoformat_utc(&d)),
        "cancel_reason": po.cancel_reason,
        "changed": false,
    })
}

/// `(reason or "").strip()[:MAX_REASON_LENGTH] or None`.
fn clean_reason(reason: Option<&str>) -> Option<String> {
    let r = take_chars(py_strip(reason.unwrap_or("")), MAX_REASON_LENGTH);
    if r.is_empty() { None } else { Some(r) }
}

pub async fn cancel(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let reason = validate_cancel_body(&body, &bytes)?;
    let pool = &state.pool;

    let po = get_po(pool, &user.tenant_id, &po_log_id).await?;
    if po.cancelled_at.is_some() {
        return Ok(ok(unchanged(&po_log_id, &po)));
    }
    let received = po.received_units;
    if received > 0.0 || matches!(po.reception_status.as_str(), "partial" | "received") {
        return Err(ApiError::app("po_cancel_after_reception",
            "Goods were already received against this order; record what arrived instead of cancelling it",
            409, json!({"reception_status": po.reception_status, "received_units": received})));
    }
    if po.paid_at.is_some() {
        return Err(after_payment());
    }

    let clean = clean_reason(reason.as_deref());
    let updated: Option<(Option<DateTime<Utc>>,)> = sqlx::query_as(
        "UPDATE inventory_po_log
            SET cancelled_at = NOW(), cancelled_by = $1, cancel_reason = $2
          WHERE id = $3 AND tenant_id = $4 AND cancelled_at IS NULL
            AND paid_at IS NULL
      RETURNING cancelled_at",
    )
    .bind(&user.user_id)
    .bind(&clean)
    .bind(&po_log_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    let Some((cancelled_at,)) = updated else {
        // Lost a race with another cancel (or a payment): report what is there.
        let current = get_po(pool, &user.tenant_id, &po_log_id).await?;
        if current.cancelled_at.is_none() {
            return Err(after_payment());
        }
        return Ok(ok(unchanged(&po_log_id, &current)));
    };
    tracing::info!("[po-cancel] CANCEL tenant={} po={} by={}", user.tenant_id, po_log_id, user.user_id);
    // Past the conditional UPDATE: only the call that really cancelled emits.
    crate::webhook_events::emit_po_event(pool, &user.tenant_id, "purchase_order.cancelled", &po_log_id).await;

    let mut details = Map::new();
    details.insert("reference".into(), Value::String(format_po_number(po.po_number, &po_log_id)));
    details.insert("cancel_reason".into(), clean.clone().map(Value::String).unwrap_or(Value::Null));
    record_event_with_reason(pool, &user.tenant_id, &user.user_id, Event::PurchaseOrderCancelled,
        Some(&po_log_id), details, Some("cancelled_by_user")).await;
    Ok(ok(json!({
        "po_log_id": po_log_id,
        "po_number": po_number_json(po.po_number),
        "cancelled_at": cancelled_at.map(|d| isoformat_utc(&d)),
        "cancel_reason": clean,
        "changed": true,
    })))
}

pub async fn uncancel(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let pool = &state.pool;

    let po = get_po(pool, &user.tenant_id, &po_log_id).await?;
    let updated: Option<(String,)> = sqlx::query_as(
        "UPDATE inventory_po_log
            SET cancelled_at = NULL, cancelled_by = NULL, cancel_reason = NULL
          WHERE id = $1 AND tenant_id = $2 AND cancelled_at IS NOT NULL
      RETURNING id",
    )
    .bind(&po_log_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    let changed = updated.is_some();
    if changed {
        tracing::info!("[po-cancel] REOPEN tenant={} po={} by={}", user.tenant_id, po_log_id, user.user_id);
        let mut details = Map::new();
        details.insert("reference".into(), Value::String(format_po_number(po.po_number, &po_log_id)));
        record_event_with_reason(pool, &user.tenant_id, &user.user_id, Event::PurchaseOrderUncancelled,
            Some(&po_log_id), details, Some("reversed_by_user")).await;
    }
    Ok(ok(json!({
        "po_log_id": po_log_id,
        "po_number": po_number_json(po.po_number),
        "cancelled_at": null,
        "cancel_reason": null,
        "changed": changed,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reason_is_stripped_cut_and_emptied_like_python() {
        assert_eq!(clean_reason(None), None);
        assert_eq!(clean_reason(Some("   ")), None);
        assert_eq!(clean_reason(Some("  late supplier \n")).as_deref(), Some("late supplier"));
        let long = format!("  {}", "é".repeat(600));
        assert_eq!(clean_reason(Some(&long)).unwrap().chars().count(), 500);
    }

    #[test]
    fn bytes_repr_matches_python() {
        // Values printed by CPython: repr(b"..."), see the module doc.
        assert_eq!(py_bytes_repr(b"stop it"), r"b'stop it'");
        assert_eq!(py_bytes_repr(b"it's"), r#"b"it's""#);
        assert_eq!(py_bytes_repr(b"'\""), r#"b'\'"'"#);
        assert_eq!(py_bytes_repr("é\n\\".as_bytes()), r"b'\xc3\xa9\n\\'");
        assert_eq!(py_bytes_repr(b"\x7f\x00"), r"b'\x7f\x00'");
        let e = validate_cancel_body(&Body::NotJson("x".into()), b"x").unwrap_err();
        assert_eq!(e.body["detail"][0]["input"], "b'x'");
    }

    fn validate_cancel_body_t(body: &Body) -> Result<Option<String>, ApiError> {
        validate_cancel_body(body, b"")
    }

    #[test]
    fn optional_body_shapes() {
        let validate_cancel_body = validate_cancel_body_t;
        assert_eq!(validate_cancel_body(&Body::Missing).unwrap(), None);
        assert_eq!(validate_cancel_body(&Body::Json(Value::Null)).unwrap(), None);
        assert_eq!(validate_cancel_body(&Body::Json(json!({}))).unwrap(), None);
        assert_eq!(validate_cancel_body(&Body::Json(json!({"reason": null}))).unwrap(), None);
        assert_eq!(validate_cancel_body(&Body::Json(json!({"reason": "x"}))).unwrap().as_deref(), Some("x"));
        let e = validate_cancel_body(&Body::Json(json!([1]))).unwrap_err();
        assert_eq!(e.body["detail"][0]["type"], "model_attributes_type");
        assert_eq!(e.body["detail"][0]["loc"], json!(["body"]));
        let e = validate_cancel_body(&Body::Json(json!({"reason": 5}))).unwrap_err();
        assert_eq!(e.body["detail"][0]["loc"], json!(["body", "reason"]));
        let e = validate_cancel_body(&Body::Json(json!({"reason": "x".repeat(501)}))).unwrap_err();
        assert_eq!(e.body["detail"][0]["type"], "string_too_long");
    }
}
