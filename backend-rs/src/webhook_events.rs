//! The EMIT half of `backend/webhooks/service.py`: queueing a business event
//! for every webhook that should get it.
//!
//! Emitting only INSERTS rows into `webhook_deliveries`; the Python
//! `webhook-deliveries` worker loop claims due rows (it polls the table), signs
//! and posts them. So a Rust route that causes an event queues exactly what
//! the Python route would, and delivery stays one implementation.
//!
//! Like Python, an emitter never fails the action that caused it: a failure
//! to enqueue is logged at ERROR and swallowed.

use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::auth::warehouse_scope::tenant_default;
use crate::pycompat::{isoformat_utc, py_strip};
use crate::pyjson;
use crate::routes::webhooks::{event_types, parse_scope, API_VERSION};

/// `subscribers`: enabled hooks of the tenant subscribed to `event_type`.
async fn subscribers(pool: &PgPool, tenant_id: &str, event_type: &str)
    -> Result<Vec<(String, Option<Value>)>, sqlx::Error>
{
    sqlx::query_as(
        "SELECT id, warehouse_scope FROM webhooks
          WHERE tenant_id = $1 AND disabled_at IS NULL AND $2 = ANY(events)",
    )
    .bind(tenant_id)
    .bind(event_type)
    .fetch_all(pool)
    .await
}

/// `policy.scope_allows`.
fn scope_allows(hook_scope: Option<&[String]>, warehouse_id: Option<&str>, warehouse_aware: bool) -> bool {
    match hook_scope {
        None => true,
        Some(_) if !warehouse_aware => true,
        Some(scope) => warehouse_id.is_some_and(|w| scope.iter().any(|s| s == w)),
    }
}

/// `catalog._iso_utc`.
fn iso_utc_z(moment: &DateTime<Utc>) -> String {
    moment.format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// `catalog.build_envelope`: `data` reduced to exactly the declared keys,
/// missing ones as null. `None` for an unknown event type or an undeclared
/// data key (Python raises there, and `emit` logs and queues nothing).
fn build_envelope(event_type: &str, tenant_id: &str, data: &Map<String, Value>) -> Option<Value> {
    let (_, keys, _, _) = event_types().into_iter().find(|(n, ..)| *n == event_type)?;
    if let Some(extra) = data.keys().find(|k| !keys.contains(&k.as_str())) {
        tracing::error!(event_type, key = %extra, "webhook event: undeclared data key");
        return None;
    }
    let mut shaped = Map::new();
    for k in keys {
        shaped.insert(k.to_string(), data.get(k).cloned().unwrap_or(Value::Null));
    }
    Some(json!({
        "id": format!("evt_{}", uuid::Uuid::new_v4().simple()),
        "type": event_type,
        "api_version": API_VERSION,
        "occurred_at": iso_utc_z(&Utc::now()),
        "tenant_id": tenant_id,
        "data": shaped,
    }))
}

/// `emit(tenant_id, event_type, data, warehouse_id=..., hooks=...)`: queue
/// the event for every hook whose scope allows it; how many were queued. The
/// public emitters below swallow its error, as Python's `emit` does.
async fn emit_inner(pool: &PgPool, tenant_id: &str, event_type: &str, data: &Map<String, Value>,
    warehouse_id: Option<&str>, hooks: Option<Vec<(String, Option<Value>)>>) -> Result<usize, sqlx::Error>
{
    let hooks = match hooks {
        Some(h) => h,
        None => subscribers(pool, tenant_id, event_type).await?,
    };
    let aware = event_types().into_iter().find(|(n, ..)| *n == event_type).map(|t| t.2).unwrap_or(true);
    let recipients: Vec<&String> = hooks
        .iter()
        .filter(|(_, scope)| scope_allows(parse_scope(scope.as_ref()).as_deref(), warehouse_id, aware))
        .map(|(id, _)| id)
        .collect();
    if recipients.is_empty() {
        return Ok(0);
    }
    let Some(envelope) = build_envelope(event_type, tenant_id, data) else { return Ok(0) };
    let payload = pyjson::dumps_compact(&envelope);
    let event_id = envelope["id"].as_str().unwrap_or_default();
    for hook_id in &recipients {
        sqlx::query(
            "INSERT INTO webhook_deliveries
                 (tenant_id, webhook_id, event_id, event_type, is_test, payload, next_attempt_at)
             VALUES ($1, $2, $3, $4, FALSE, $5, NOW())",
        )
        .bind(tenant_id)
        .bind(hook_id.as_str())
        .bind(event_id)
        .bind(event_type)
        .bind(&payload)
        .execute(pool)
        .await?;
    }
    Ok(recipients.len())
}

/// `warehouse_ref`: (warehouse name, warehouse id) for a name that may be
/// blank (the tenant's default warehouse). The id is None when no such row
/// exists, which keeps the event away from warehouse-scoped hooks.
async fn warehouse_ref(pool: &PgPool, tenant_id: &str, name: Option<&str>)
    -> Result<(String, Option<String>), crate::error::ApiError>
{
    let named = py_strip(name.unwrap_or("")).to_string();
    let resolved = if named.is_empty() { tenant_default(pool, tenant_id).await? } else { named };
    let row: Option<(String, String)> = sqlx::query_as(
        "SELECT id, name FROM warehouses WHERE tenant_id = $1 AND lower(name) = lower($2)",
    )
    .bind(tenant_id)
    .bind(&resolved)
    .fetch_optional(pool)
    .await?;
    Ok(match row {
        Some((id, name)) => (name, Some(id)),
        None => (resolved, None),
    })
}

fn num(v: Option<f64>) -> Value {
    v.map(|f| json!(f)).unwrap_or(Value::Null)
}

fn ts(v: Option<DateTime<Utc>>) -> Value {
    v.map(|t| Value::String(isoformat_utc(&t))).unwrap_or(Value::Null)
}

/// `emit_po_event`: the order as it is NOW, after the change that caused it.
pub async fn emit_po_event(pool: &PgPool, tenant_id: &str, event_type: &str, po_log_id: &str,
    decided_by: Option<&str>) -> usize
{
    match emit_po_inner(pool, tenant_id, event_type, po_log_id, decided_by).await {
        Ok(n) => n,
        Err(e) => {
            tracing::error!(error = ?e, event_type, po = po_log_id, "[webhooks] could not build the event");
            0
        }
    }
}

type PoRow = (String, Option<i64>, Option<String>, Option<i64>, Option<f64>, Option<f64>, Option<f64>,
    Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<String>);

async fn emit_po_inner(pool: &PgPool, tenant_id: &str, event_type: &str, po_log_id: &str,
    decided_by: Option<&str>) -> Result<usize, crate::error::ApiError>
{
    let hooks = subscribers(pool, tenant_id, event_type).await?;
    if hooks.is_empty() {
        return Ok(0);
    }
    let po: Option<PoRow> = sqlx::query_as(
        "SELECT id, po_number::bigint, destination_warehouse, sku_count::bigint, total_units::float8,
                total_value::float8, approved_amount::float8, sent_at, cancelled_at, cancelled_by
           FROM inventory_po_log WHERE id = $1 AND tenant_id = $2",
    )
    .bind(po_log_id)
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    let Some((id, po_number, destination, sku_count, total_units, total_value, approved_amount, sent_at,
        cancelled_at, cancelled_by)) = po else { return Ok(0) };
    let (wh_name, wh_id) = warehouse_ref(pool, tenant_id, destination.as_deref()).await?;
    let keys = event_types().into_iter().find(|(n, ..)| *n == event_type).map(|t| t.1).unwrap_or_default();
    let mut data = Map::new();
    data.insert("po_log_id".into(), json!(id));
    data.insert("po_number".into(), json!(po_number));
    data.insert("warehouse".into(), json!(wh_name));
    data.insert("warehouse_id".into(), json!(wh_id));
    data.insert("sku_count".into(), json!(sku_count));
    data.insert("total_units".into(), num(total_units));
    data.insert("total_value".into(), num(total_value));
    if keys.contains(&"decided_by") {
        data.insert("decided_by".into(), json!(decided_by));
    }
    if keys.contains(&"approved_amount") {
        data.insert("approved_amount".into(), num(approved_amount));
    }
    if keys.contains(&"sent_at") {
        data.insert("sent_at".into(), ts(sent_at));
    }
    if keys.contains(&"cancelled_at") {
        data.insert("cancelled_at".into(), ts(cancelled_at));
        data.insert("cancelled_by".into(), json!(cancelled_by));
    }
    Ok(emit_inner(pool, tenant_id, event_type, &data, wh_id.as_deref(), Some(hooks)).await?)
}

/// `emit_commitment_event`: `commitment` is a formatted committed-demand row
/// (dates already ISO strings); `extra` adds the event's own keys.
pub async fn emit_commitment_event(pool: &PgPool, tenant_id: &str, event_type: &str,
    commitment: &Map<String, Value>, extra: Map<String, Value>) -> usize
{
    let inner = async {
        let hooks = subscribers(pool, tenant_id, event_type).await?;
        if hooks.is_empty() {
            return Ok::<usize, sqlx::Error>(0);
        }
        let mut wh_id = commitment.get("warehouse_id").and_then(Value::as_str)
            .filter(|s| !s.is_empty()).map(str::to_string);
        let mut wh_name: Option<String> = None;
        if let Some(id) = wh_id.clone() {
            let row: Option<(String,)> = sqlx::query_as("SELECT name FROM warehouses WHERE id = $1 AND tenant_id = $2")
                .bind(&id)
                .bind(tenant_id)
                .fetch_optional(pool)
                .await?;
            match row {
                Some((n,)) => wh_name = Some(n),
                None => wh_id = None,
            }
        }
        let quantity = commitment.get("quantity").and_then(Value::as_f64);
        let mut data = Map::new();
        data.insert("commitment_id".into(), commitment.get("id").cloned().unwrap_or(Value::Null));
        data.insert("sku".into(), commitment.get("sku").cloned().unwrap_or(Value::Null));
        data.insert("warehouse".into(), json!(wh_name));
        data.insert("warehouse_id".into(), json!(wh_id));
        data.insert("customer".into(), commitment.get("customer").cloned().unwrap_or(Value::Null));
        data.insert("delivery_date".into(), commitment.get("delivery_date").cloned().unwrap_or(Value::Null));
        data.insert("quantity".into(), num(quantity));
        for (k, v) in extra {
            data.insert(k, v);
        }
        emit_inner(pool, tenant_id, event_type, &data, wh_id.as_deref(), Some(hooks)).await
    };
    match inner.await {
        Ok(n) => n,
        Err(e) => {
            tracing::error!(error = %e, event_type, "[webhooks] could not build the commitment event");
            0
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scope_policy_matches_policy_py() {
        let s = vec!["w1".to_string()];
        assert!(scope_allows(None, None, true));
        assert!(scope_allows(Some(&s), None, false));
        assert!(!scope_allows(Some(&s), None, true));
        assert!(scope_allows(Some(&s), Some("w1"), true));
        assert!(!scope_allows(Some(&s), Some("w2"), true));
        assert!(!scope_allows(Some(&[]), Some("w1"), true));
    }

    #[test]
    fn envelope_shapes_data_and_refuses_undeclared_keys() {
        let mut d = Map::new();
        d.insert("commitment_id".into(), json!("c1"));
        d.insert("quantity".into(), json!(10.0));
        let e = build_envelope("commitment.fulfilled", "t1", &d).unwrap();
        assert_eq!(e["type"], "commitment.fulfilled");
        assert_eq!(e["api_version"], API_VERSION);
        let keys: Vec<&String> = e["data"].as_object().unwrap().keys().collect();
        assert_eq!(keys, ["commitment_id", "sku", "warehouse", "warehouse_id", "customer", "delivery_date",
            "quantity", "fulfilled_at"]);
        assert!(e["id"].as_str().unwrap().starts_with("evt_") && e["id"].as_str().unwrap().len() == 36);
        let payload = pyjson::dumps_compact(&e);
        assert!(payload.contains("\"quantity\":10.0,"));
        d.insert("note".into(), json!("x"));
        assert!(build_envelope("commitment.fulfilled", "t1", &d).is_none());
        assert!(build_envelope("nope", "t1", &Map::new()).is_none());
    }
}
