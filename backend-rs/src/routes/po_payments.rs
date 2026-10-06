//! Purchase-order payments: `backend/api/v1/po_payments.py` and
//! `backend/inventory/po_payment_service.py`.
//!
//! Migrated: `POST /inventory/po/{po_log_id}/mark-paid` and `/mark-unpaid`.
//! Both are DB-only: two columns on `inventory_po_log` (`paid_at`, `paid_by`)
//! that the cash calendar reads at query time, plus one activity row on a real
//! change. Nothing to recompute, so nothing of the inventory hub is needed.
//!
//! Guard order is FastAPI's dependency order in the Python signature: the
//! person/key (`get_current_user`), the role and trial read-only guard
//! (`require_analyst_or_above`), then the warehouse scope (`po_guard`), then the
//! service's own 404 / 409. No request body is declared, so none is read: a
//! malformed body is ignored on both sides.

use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::activity::{record_event_with_reason, Event};
use crate::auth::{self, warehouse_scope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::isoformat_utc;
use crate::routes::ok;
use crate::state::AppState;

/// `INTERNAL_TAGS["inventory-payments"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'inventory-payments': marking a PO's invoice paid/unpaid from the /pedidos screen",
    ),
    is_mcp: false,
};

/// `roi_service.format_po_number`: `OC-000123`, or the raw id when the order
/// has no number (`if po_number` - zero counts as no number, like Python).
pub fn format_po_number(po_number: Option<i32>, fallback: &str) -> String {
    match po_number {
        // f"{int(n):06d}" and Rust's `{:06}` agree, sign included ("-00005").
        Some(n) if n != 0 => format!("OC-{n:06}"),
        _ => fallback.to_string(),
    }
}

/// `po_number` as JSON: an int or null.
pub fn po_number_json(n: Option<i32>) -> Value {
    n.map(Value::from).unwrap_or(Value::Null)
}

fn ts_json(v: Option<DateTime<Utc>>) -> Value {
    v.map(|d| Value::String(isoformat_utc(&d))).unwrap_or(Value::Null)
}

/// The common front half of every `{po_log_id}` write: authenticate, role +
/// trial guard, then the warehouse scope of the order.
pub async fn po_writer(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    route: RouteAuth,
    po_log_id: &str,
) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, route, actors).await?;
    auth::require_analyst_or_above(state, &user).await?;
    warehouse_scope::po_guard(&state.pool, &user, po_log_id).await?;
    Ok(user)
}

pub fn po_not_found() -> ApiError {
    ApiError::app("po_not_found", "Purchase order not found", 404, json!({}))
}

struct PayPo {
    po_number: Option<i32>,
    sent_at: Option<DateTime<Utc>>,
    paid_at: Option<DateTime<Utc>>,
    paid_by: Option<String>,
    cancelled_at: Option<DateTime<Utc>>,
}

/// `po_payment_service._get_po`.
async fn get_po(pool: &PgPool, tenant_id: &str, po_log_id: &str) -> Result<PayPo, ApiError> {
    #[allow(clippy::type_complexity)]
    let row: Option<(Option<i32>, Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<String>, Option<DateTime<Utc>>)> =
        sqlx::query_as(
            "SELECT po_number, sent_at, paid_at, paid_by, cancelled_at
               FROM inventory_po_log WHERE id = $1 AND tenant_id = $2",
        )
        .bind(po_log_id)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    let (po_number, sent_at, paid_at, paid_by, cancelled_at) = row.ok_or_else(po_not_found)?;
    Ok(PayPo { po_number, sent_at, paid_at, paid_by, cancelled_at })
}

fn reference(po_number: Option<i32>, po_log_id: &str) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("reference".into(), Value::String(format_po_number(po_number, po_log_id)));
    m
}

pub async fn mark_paid(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let pool = &state.pool;

    let po = get_po(pool, &user.tenant_id, &po_log_id).await?;
    if po.cancelled_at.is_some() {
        return Err(ApiError::app("po_cancelled",
            "This order was cancelled; reopen it before marking it as paid", 409, json!({})));
    }
    if po.sent_at.is_none() {
        return Err(ApiError::app("po_paid_requires_sent",
            "Only an order that was sent to the supplier can be marked as paid", 409, json!({})));
    }
    let updated: Option<(Option<DateTime<Utc>>, Option<String>)> = sqlx::query_as(
        "UPDATE inventory_po_log
            SET paid_at = NOW(), paid_by = $1
          WHERE id = $2 AND tenant_id = $3 AND paid_at IS NULL
      RETURNING paid_at, paid_by",
    )
    .bind(&user.user_id)
    .bind(&po_log_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;

    let Some((paid_at, paid_by)) = updated else {
        let current = get_po(pool, &user.tenant_id, &po_log_id).await?;
        return Ok(ok(json!({
            "po_log_id": po_log_id,
            "po_number": po_number_json(current.po_number),
            "paid_at": ts_json(current.paid_at),
            "paid_by": current.paid_by,
            "changed": false,
        })));
    };
    tracing::info!("[po-payment] PAID tenant={} po={} by={}", user.tenant_id, po_log_id, user.user_id);
    record_event_with_reason(pool, &user.tenant_id, &user.user_id, Event::PurchaseOrderPaid,
        Some(&po_log_id), reference(po.po_number, &po_log_id), None).await;
    Ok(ok(json!({
        "po_log_id": po_log_id,
        "po_number": po_number_json(po.po_number),
        "paid_at": ts_json(paid_at),
        "paid_by": paid_by,
        "changed": true,
    })))
}

pub async fn mark_unpaid(
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
            SET paid_at = NULL, paid_by = NULL
          WHERE id = $1 AND tenant_id = $2 AND paid_at IS NOT NULL
      RETURNING id",
    )
    .bind(&po_log_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    let changed = updated.is_some();
    if changed {
        tracing::info!("[po-payment] UNPAID tenant={} po={} by={}", user.tenant_id, po_log_id, user.user_id);
        record_event_with_reason(pool, &user.tenant_id, &user.user_id, Event::PurchaseOrderUnpaid,
            Some(&po_log_id), reference(po.po_number, &po_log_id), Some("reversed_by_user")).await;
    }
    Ok(ok(json!({
        "po_log_id": po_log_id,
        "po_number": po_number_json(po.po_number),
        "paid_at": null,
        "paid_by": null,
        "changed": changed,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn po_reference_matches_python_format() {
        assert_eq!(format_po_number(Some(123), "x"), "OC-000123");
        assert_eq!(format_po_number(Some(1234567), "x"), "OC-1234567");
        assert_eq!(format_po_number(Some(0), "raw-id"), "raw-id");
        assert_eq!(format_po_number(None, "raw-id"), "raw-id");
        // f"{-5:06d}" == "-00005"
        assert_eq!(format_po_number(Some(-5), "x"), "OC--00005");
    }
}
