//! The alert bell and the system activity history:
//! backend/api/v1/alerts.py over backend/notifications/alert_history.py.
//!
//! * `GET /alerts`           -- the bell: scheduled deliveries (fan-out
//!   grouped) plus critical / warning system events, with unread derived
//!   from the caller's last `alerts_marked_read` marker.
//! * `GET /alerts/activity`  -- everything, ungrouped, filterable.
//! * `GET /alerts/kinds`     -- the filter vocabulary.
//! * `POST /alerts/read`     -- writes the caller's marker row.
//!
//! The three reads are exposed to read keys (`alerts` tag); `POST /read` is a
//! per-route exception (`INTERNAL_ROUTES`). Every role may call all four.
//!
//! The event registry (`backend/activity/events.py::EVENTS`) is copied here
//! as data; a unit test re-reads the Python file so the two cannot drift.

use std::collections::BTreeSet;

use axum::extract::{RawQuery, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::log_action;
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{isoformat_utc, truthy};
use crate::routes::ok;
use crate::routes::r1::query_params::{sql_int, QueryParams};
use crate::state::AppState;
use crate::validation::Errors;

pub const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
pub const MARK_READ: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("marks alerts read for the signed-in person"),
    is_mcp: false,
};

const CRITICAL: &str = "critical";
const WARNING: &str = "warning";
const INFO: &str = "info";

/// `ALERT_ACTIONS`: scheduled sends -> kind.
const ALERT_ACTIONS: [(&str, &str); 6] = [
    ("inventory_alert_email", "stockout_digest"),
    ("inventory_alert_whatsapp", "stockout_digest"),
    ("supplier_lead_time_alert_email", "supplier_lead_time"),
    ("data_freshness_reminder_email", "data_freshness"),
    ("data_freshness_reminder_whatsapp", "data_freshness"),
    ("monthly_roi_email", "monthly_roi"),
];
const MARK_READ_ACTION: &str = "alerts_marked_read";
const FANOUT_WINDOW_SECONDS: i64 = 900;
const ROWS_PER_ENTRY: i64 = 20;
const MAX_ROWS: i64 = 400;

/// `_DETAIL_KEYS` per delivery kind.
fn delivery_detail_keys(kind: &str) -> &'static [&'static str] {
    match kind {
        "stockout_digest" => &["critical", "warning"],
        "supplier_lead_time" => &["suppliers"],
        "data_freshness" => &["sales_age_days", "stock_age_days", "silent_warehouses"],
        "monthly_roi" => &["month"],
        _ => &[],
    }
}

/// `EVENTS`: (action, kind, severity, detail_keys), in declaration order.
type Spec = (&'static str, &'static str, &'static str, &'static [&'static str]);
const ROWS4: &[&str] = &["rows_read", "rows_written", "duplicate_rows", "rejected_rows"];
const EVENTS: &[Spec] = &[
    ("training.completed", "training", INFO, &["session_id", "session_name", "skus", "best_model", "period"]),
    ("training.failed", "training", CRITICAL, &["session_id", "session_name", "started_by"]),
    ("training.blocked", "training", WARNING, &["session_id", "session_name", "issues"]),
    ("forecast.accuracy_degraded", "training", WARNING, &["session_id", "session_name", "degradation_pct"]),
    ("forecast.adjusted", "training", INFO, &["sku", "adjustment", "adjustment_reason"]),
    ("forecast.spike_excluded", "training", INFO, &["sku", "period", "spike_reason"]),
    ("forecast.spike_restored", "training", INFO, &["sku", "period", "spike_reason"]),
    ("forecast.analogy_defined", "training", INFO, &["sku", "references"]),
    ("forecast.analogy_reverted", "training", INFO, &["sku", "references"]),
    ("demand_plan.created", "training", INFO, &["plan_name", "skus", "periods"]),
    ("demand_plan.submitted", "training", INFO, &["plan_name"]),
    ("demand_plan.approved", "training", INFO, &["plan_name", "decision_comment", "superseded"]),
    ("demand_plan.rejected", "training", INFO, &["plan_name", "decision_comment"]),
    ("demand_plan.commented", "training", INFO, &["plan_name"]),
    ("committed_demand.created", "purchase", INFO, &["sku", "quantity", "delivery_date", "customer"]),
    ("committed_demand.imported", "purchase", INFO, &["rows"]),
    ("committed_demand.changed", "purchase", INFO, &["sku", "status"]),
    ("supply_contract.created", "purchase", INFO, &["customer", "lines", "revision", "status"]),
    ("supply_contract.revised", "purchase", INFO, &["customer", "lines", "revision", "status"]),
    ("supply_contract.status_changed", "purchase", INFO, &["customer", "lines", "revision", "status"]),
    ("purchase_budget.created", "purchase", INFO, &["budget_scope", "amount", "period"]),
    ("purchase_budget.revised", "purchase", INFO, &["budget_scope", "amount", "period"]),
    ("purchase_budget.exceeded", "purchase", INFO, &["budget_scope", "over_by", "override_reason", "reference"]),
    ("purchase_budget.override", "purchase", INFO, &["budget_scope", "over_by", "override_reason", "reference"]),
    ("purchase.order_generated", "purchase", INFO, &["reference", "lines", "value", "suppliers"]),
    ("purchase.order_sent", "purchase", INFO, &["reference", "sent", "skipped"]),
    ("purchase.order_not_sent", "purchase", CRITICAL, &["reference", "skipped"]),
    ("purchase.supplier_confirmed", "purchase", INFO, &["reference", "supplier", "confirmed"]),
    ("purchase.supplier_changes_proposed", "purchase", WARNING, &["reference", "supplier", "confirmed", "changed", "declined"]),
    ("purchase.supplier_change_accepted", "purchase", INFO, &["reference", "supplier", "sku", "promised_date"]),
    ("purchase.supplier_link_reopened", "purchase", INFO, &["reference", "supplier"]),
    ("purchase.supplier_link_revoked", "purchase", INFO, &["reference", "supplier"]),
    ("purchase.reception_recorded", "purchase", INFO, &["reference", "sku_count", "units", "warehouse"]),
    ("purchase.reception_undone", "purchase", WARNING, &["reference", "sku_count", "units", "warehouse"]),
    ("purchase.order_unsent", "purchase", WARNING, &["reference"]),
    ("purchase.order_paid", "purchase", INFO, &["reference"]),
    ("purchase.order_unpaid", "purchase", WARNING, &["reference"]),
    ("purchase.order_cancelled", "purchase", WARNING, &["reference", "cancel_reason"]),
    ("purchase.order_uncancelled", "purchase", WARNING, &["reference"]),
    ("purchase.approval_requested", "purchase", INFO, &["reference", "value"]),
    ("purchase.approval_approved", "purchase", INFO, &["reference", "value", "decision_comment"]),
    ("purchase.approval_rejected", "purchase", INFO, &["reference", "value", "decision_comment"]),
    ("inbound_email.ingested", "data", INFO, &["filename", "email"]),
    ("inbound_email.needs_review", "data", WARNING, &["filename", "email"]),
    ("inbound_email.rejected", "data", WARNING, &["filename", "email"]),
    ("data.sql_refresh_failed", "data", CRITICAL, &["source_name"]),
    ("data.stock_imported", "data", INFO, ROWS4),
    ("data.stock_import_partial", "data", WARNING, ROWS4),
    ("data.suppliers_imported", "data", INFO, ROWS4),
    ("data.suppliers_import_partial", "data", WARNING, ROWS4),
    ("data.orders_imported", "data", INFO, ROWS4),
    ("data.orders_import_partial", "data", WARNING, ROWS4),
    ("data.shrinkage_recorded", "data", INFO, &["sku", "quantity", "warehouse", "shrinkage_reason"]),
    ("data.stock_count_applied", "data", INFO, &["warehouse", "lines", "units"]),
    ("data.transfer_created", "data", INFO, &["sku_count", "units", "from_warehouse", "to_warehouse"]),
    ("limit.reached", "limit", WARNING, &["limit", "ceiling", "attempted_by"]),
    ("account.user_invited", "account", INFO, &["email", "role"]),
    ("account.user_role_changed", "account", WARNING, &["email", "role", "previous_role"]),
    ("account.user_deactivated", "account", WARNING, &["email"]),
    ("account.api_key_created", "account", WARNING, &["key_name", "role"]),
    ("account.api_key_revoked", "account", WARNING, &["key_name"]),
    ("account.signed_up_with_provider", "account", INFO, &["provider", "email"]),
    ("account.provider_linked", "account", WARNING, &["provider", "email"]),
    ("account.provider_unlinked", "account", WARNING, &["provider", "email"]),
    ("account.sso_sign_in", "account", INFO, &["email"]),
    ("account.sso_user_created", "account", INFO, &["email", "role"]),
    ("account.sso_role_mapped", "account", WARNING, &["email", "role", "previous_role"]),
    ("account.sso_sign_in_refused", "account", WARNING, &["email"]),
    ("account.sso_config_changed", "account", WARNING, &["issuer", "enabled", "enforce_sso", "domains"]),
    ("account.sso_config_removed", "account", WARNING, &["issuer"]),
    ("account.warehouse_scope_changed", "account", WARNING, &["email", "warehouses"]),
    ("webhook.auto_disabled", "account", WARNING, &["host"]),
    ("account.scim_user_created", "account", INFO, &["email", "role"]),
    ("account.scim_user_updated", "account", INFO, &["email", "changes"]),
    ("account.scim_user_deactivated", "account", WARNING, &["email"]),
    ("account.scim_user_reactivated", "account", WARNING, &["email"]),
    ("account.scim_role_changed", "account", WARNING, &["email", "role", "previous_role"]),
    ("account.scim_request_refused", "account", WARNING, &["email", "operation"]),
    ("account.scim_token_created", "account", WARNING, &["manage_admins", "rotated"]),
    ("account.scim_token_revoked", "account", WARNING, &[]),
    ("account.scim_settings_changed", "account", WARNING, &["manage_admins"]),
    ("billing.plan_activated", "billing", INFO, &["provider", "tier", "previous_tier"]),
    ("billing.plan_downgraded", "billing", CRITICAL, &["provider", "tier", "previous_tier"]),
    ("billing.payment_failed", "billing", WARNING, &["provider", "grace_until"]),
    ("billing.subscription_changed", "billing", INFO, &["provider", "status", "renews_at"]),
];

fn event(action: &str) -> Option<&'static Spec> {
    EVENTS.iter().find(|e| e.0 == action)
}

fn alert_kind(action: &str) -> Option<&'static str> {
    ALERT_ACTIONS.iter().find(|(a, _)| *a == action).map(|(_, k)| *k)
}

fn alert_actions() -> Vec<String> {
    ALERT_ACTIONS.iter().map(|(a, _)| a.to_string()).collect()
}

/// `_bell_event_actions`: critical and warning events.
fn bell_event_actions() -> Vec<String> {
    EVENTS.iter().filter(|e| e.2 == CRITICAL || e.2 == WARNING).map(|e| e.0.to_string()).collect()
}

// ── Rows and entries ─────────────────────────────────────────────────────────

struct LogRow {
    id: Value,
    action: String,
    context: Value,
    status: Option<String>,
    created_at: Option<DateTime<Utc>>,
}

impl LogRow {
    fn from_pg(r: &sqlx::postgres::PgRow) -> Result<Self, sqlx::Error> {
        Ok(LogRow {
            id: r.try_get::<Option<String>, _>("id")?.map(Value::String).unwrap_or(Value::Null),
            action: r.try_get("action")?,
            context: r.try_get::<Option<Value>, _>("context")?.unwrap_or(Value::Null),
            status: r.try_get("status")?,
            created_at: r.try_get("created_at")?,
        })
    }

    /// `row.get("context") or {}`, read as a dict. A truthy non-object
    /// context would be an AttributeError in Python (a 500).
    fn ctx(&self) -> Result<Map<String, Value>, ApiError> {
        match &self.context {
            Value::Object(m) => Ok(m.clone()),
            v if !truthy(v) => Ok(Map::new()),
            _ => Err(ApiError::internal()),
        }
    }
}

/// `context.get(k)`, treating a missing key and JSON null alike (Python's
/// `is not None` tests).
fn present<'a>(ctx: &'a Map<String, Value>, k: &str) -> Option<&'a Value> {
    ctx.get(k).filter(|v| !v.is_null())
}

fn pick(ctx: &Map<String, Value>, keys: &[&str]) -> Map<String, Value> {
    keys.iter().filter_map(|k| present(ctx, k).map(|v| (k.to_string(), v.clone()))).collect()
}

/// `str(value)` for the JSON values a context can hold.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True".into() } else { "False".into() },
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

#[derive(Debug, Clone)]
struct Entry {
    id: Value,
    source: &'static str,
    action: String,
    kind: String,
    severity: Option<Value>,
    reason: Option<Value>,
    reason_params: Value,
    created_at: Option<DateTime<Utc>>,
    channels: BTreeSet<String>,
    delivered_count: i64,
    failed_count: i64,
    failure_reason: Option<String>,
    details: Map<String, Value>,
    /// Only system entries decide it up front ("recorded" / "failed").
    status: Option<&'static str>,
}

/// `_channel_of`.
fn channel_of(action: &str, ctx: &Map<String, Value>) -> String {
    match ctx.get("channel") {
        Some(Value::String(c)) if c == "email" || c == "whatsapp" => c.clone(),
        _ => if action.ends_with("_whatsapp") { "whatsapp".into() } else { "email".into() },
    }
}

/// `_system_entry`.
fn system_entry(row: &LogRow) -> Result<Entry, ApiError> {
    let ctx = row.ctx()?;
    let spec = event(&row.action).ok_or_else(ApiError::internal)?;
    let severity = match ctx.get("severity") {
        Some(v) if truthy(v) => v.clone(),
        _ => json!(spec.2),
    };
    let reason_params = match ctx.get("reason_params") {
        Some(v) if truthy(v) => v.clone(),
        _ => json!({}),
    };
    Ok(Entry {
        id: row.id.clone(),
        source: "system",
        action: row.action.clone(),
        kind: spec.1.to_string(),
        severity: Some(severity),
        reason: Some(ctx.get("reason").cloned().unwrap_or(Value::Null)),
        reason_params,
        created_at: row.created_at,
        channels: BTreeSet::from(["system".to_string()]),
        delivered_count: 0,
        failed_count: 0,
        failure_reason: None,
        details: pick(&ctx, spec.3),
        status: Some(if row.status.as_deref() == Some("success") { "recorded" } else { "failed" }),
    })
}

/// `_group`: rows newest first; consecutive rows of the same KIND within the
/// fan-out window of the group's NEWEST row are one entry.
fn group(rows: &[&LogRow]) -> Result<Vec<Entry>, ApiError> {
    let mut entries: Vec<Entry> = Vec::new();
    for row in rows {
        let Some(kind) = alert_kind(&row.action) else { continue };
        let ctx = row.ctx()?;
        let delivered = row.status.as_deref() == Some("success");
        let same_fanout = match entries.last() {
            Some(cur) if cur.kind == kind => match (cur.created_at, row.created_at) {
                (Some(a), Some(b)) => (a - b).num_microseconds().unwrap_or(i64::MAX)
                    <= FANOUT_WINDOW_SECONDS * 1_000_000,
                // datetime - None is a TypeError in Python.
                _ => return Err(ApiError::internal()),
            },
            _ => false,
        };
        if !same_fanout {
            entries.push(Entry {
                id: row.id.clone(),
                source: "delivery",
                action: row.action.clone(),
                kind: kind.to_string(),
                severity: None,
                reason: None,
                reason_params: json!({}),
                created_at: row.created_at,
                channels: BTreeSet::new(),
                delivered_count: 0,
                failed_count: 0,
                failure_reason: None,
                details: pick(&ctx, delivery_detail_keys(kind)),
                status: None,
            });
        }
        let cur = entries.last_mut().ok_or_else(ApiError::internal)?;
        cur.channels.insert(channel_of(&row.action, &ctx));
        if delivered {
            cur.delivered_count += 1;
        } else {
            cur.failed_count += 1;
            if cur.failure_reason.is_none() {
                if let Some(r) = ctx.get("reason").filter(|v| truthy(v)) {
                    cur.failure_reason = Some(py_str(r));
                }
            }
        }
    }
    Ok(entries)
}

/// `_finalize`: the public shape, in Python's key order.
fn finalize(e: &Entry, last_read_at: Option<DateTime<Utc>>) -> Value {
    let status = match e.status {
        Some(s) if e.source == "system" => s,
        _ if e.failed_count == 0 => "delivered",
        _ if e.delivered_count == 0 => "failed",
        _ => "partial",
    };
    let severity = match &e.severity {
        Some(v) if truthy(v) => v.clone(),
        _ => json!(match status {
            "failed" => CRITICAL,
            "partial" => WARNING,
            _ => INFO,
        }),
    };
    let reason = match &e.reason {
        Some(v) if truthy(v) => v.clone(),
        _ => e.failure_reason.clone().map(Value::String).unwrap_or(Value::Null),
    };
    let reason_params = if truthy(&e.reason_params) { e.reason_params.clone() } else { json!({}) };
    let channel = if e.channels.len() == 1 {
        e.channels.iter().next().cloned().unwrap_or_default()
    } else {
        "mixed".to_string()
    };
    let unread = match (last_read_at, e.created_at) {
        (None, _) => true,
        (Some(read), Some(c)) => c > read,
        (Some(_), None) => false,
    };
    let mut m = Map::new();
    m.insert("id".into(), e.id.clone());
    m.insert("source".into(), json!(e.source));
    m.insert("action".into(), json!(e.action));
    m.insert("kind".into(), json!(e.kind));
    m.insert("severity".into(), severity);
    m.insert("reason".into(), reason);
    m.insert("reason_params".into(), reason_params);
    m.insert("created_at".into(), e.created_at.map(|d| json!(isoformat_utc(&d))).unwrap_or(Value::Null));
    m.insert("channel".into(), json!(channel));
    m.insert("status".into(), json!(status));
    m.insert("delivered_count".into(), json!(e.delivered_count));
    m.insert("failed_count".into(), json!(e.failed_count));
    m.insert("failure_reason".into(), e.failure_reason.clone().map(Value::String).unwrap_or(Value::Null));
    m.insert("details".into(), Value::Object(e.details.clone()));
    m.insert("unread".into(), json!(unread));
    Value::Object(m)
}

/// `_last_read_at`.
async fn last_read_at(pool: &PgPool, tenant_id: &str, user_id: &str) -> Result<Option<DateTime<Utc>>, sqlx::Error> {
    let (last,): (Option<DateTime<Utc>>,) = sqlx::query_as(
        "SELECT MAX(created_at) AS last_at FROM activity_logs
         WHERE tenant_id = $1 AND user_id = $2 AND action = $3",
    )
    .bind(tenant_id)
    .bind(user_id)
    .bind(MARK_READ_ACTION)
    .fetch_one(pool)
    .await?;
    Ok(last)
}

/// `merged.sort(key=created_at or datetime.min, reverse=True)`: stable, so
/// ties keep their order (deliveries first, then system events).
fn sort_newest_first(entries: &mut [Entry]) {
    entries.sort_by(|a, b| b.created_at.cmp(&a.created_at));
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn list_alerts(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let q = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let limit = q.int(&mut errs, "limit", 20, Some(1), Some(100));
    errs.into_result()?;
    let limit = sql_int(limit)?;

    let mut actions = alert_actions();
    actions.extend(bell_event_actions());
    let rows = sqlx::query(
        "SELECT id, action, context, status, created_at FROM activity_logs
         WHERE tenant_id = $1 AND action = ANY($2)
         ORDER BY created_at DESC LIMIT $3",
    )
    .bind(&user.tenant_id)
    .bind(&actions)
    .bind((limit * ROWS_PER_ENTRY).min(MAX_ROWS))
    .fetch_all(&state.pool)
    .await?;
    let rows = rows.iter().map(LogRow::from_pg).collect::<Result<Vec<_>, _>>()?;

    let read_at = last_read_at(&state.pool, &user.tenant_id, &user.user_id).await?;
    let deliveries: Vec<&LogRow> = rows.iter().filter(|r| alert_kind(&r.action).is_some()).collect();
    let mut merged = group(&deliveries)?;
    for r in rows.iter().filter(|r| event(&r.action).is_some()) {
        merged.push(system_entry(r)?);
    }
    sort_newest_first(&mut merged);
    let entries: Vec<Value> = merged.iter().map(|e| finalize(e, read_at)).take(limit as usize).collect();
    let unread = entries.iter().filter(|e| e["unread"] == Value::Bool(true)).count();
    Ok(ok(json!({
        "items": entries,
        "unread_count": unread,
        "last_read_at": read_at.map(|d| isoformat_utc(&d)),
        "limit": limit,
    })))
}

fn severity_pattern(s: &str) -> bool {
    matches!(s, "critical" | "warning" | "info")
}

pub async fn list_activity(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    RawQuery(raw): RawQuery,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let q = QueryParams::parse(raw.as_deref());
    let mut errs = Errors::default();
    let limit = q.int(&mut errs, "limit", 50, Some(1), Some(200));
    let offset = q.int(&mut errs, "offset", 0, Some(0), None);
    let kind = q.opt_str(&mut errs, "kind", None);
    let severity = q.opt_str(&mut errs, "severity", Some(("^(critical|warning|info)$", severity_pattern)));
    errs.into_result()?;
    let (limit, offset) = (sql_int(limit)?, sql_int(offset)?);

    let mut all_actions = alert_actions();
    all_actions.extend(EVENTS.iter().map(|e| e.0.to_string()));
    // Parameters by position: $1 tenant, $2 actions, then the optional ones.
    let mut clauses = vec!["tenant_id = $1".to_string(), "action = ANY($2)".to_string()];
    let mut text_arrays: Vec<Vec<String>> = Vec::new();
    let mut texts: Vec<String> = Vec::new();
    let mut next = 3;
    enum P {
        Arr(usize),
        Txt(usize),
    }
    let mut order: Vec<P> = Vec::new();
    if let Some(k) = kind.as_deref().filter(|k| !k.is_empty()) {
        let mut kind_actions: Vec<String> = EVENTS.iter().filter(|e| e.1 == k).map(|e| e.0.to_string()).collect();
        kind_actions.extend(ALERT_ACTIONS.iter().filter(|(_, ak)| *ak == k).map(|(a, _)| a.to_string()));
        if kind_actions.is_empty() {
            return Ok(ok(json!({"items": [], "total": 0, "limit": limit, "offset": offset})));
        }
        clauses.push(format!("action = ANY(${next})"));
        next += 1;
        order.push(P::Arr(text_arrays.len()));
        text_arrays.push(kind_actions);
    }
    if let Some(s) = severity.as_deref().filter(|s| !s.is_empty()) {
        if s == CRITICAL {
            clauses.push(format!(
                "(context->>'severity' = ${next} OR (action = ANY(${}) AND status <> 'success'))",
                next + 1
            ));
            next += 2;
            order.push(P::Txt(texts.len()));
            texts.push(s.to_string());
            order.push(P::Arr(text_arrays.len()));
            text_arrays.push(alert_actions());
        } else {
            clauses.push(format!("context->>'severity' = ${next}"));
            next += 1;
            order.push(P::Txt(texts.len()));
            texts.push(s.to_string());
        }
    }
    let where_ = clauses.join(" AND ");
    let list_sql = format!(
        "SELECT id, action, context, status, created_at FROM activity_logs WHERE {where_}
         ORDER BY created_at DESC LIMIT ${next} OFFSET ${}",
        next + 1
    );
    let count_sql = format!("SELECT COUNT(*) AS n FROM activity_logs WHERE {where_}");

    let mut list = sqlx::query(&list_sql).bind(&user.tenant_id).bind(&all_actions);
    let mut count = sqlx::query_as::<_, (i64,)>(&count_sql).bind(&user.tenant_id).bind(&all_actions);
    for p in &order {
        match p {
            P::Arr(i) => {
                list = list.bind(&text_arrays[*i]);
                count = count.bind(&text_arrays[*i]);
            }
            P::Txt(i) => {
                list = list.bind(&texts[*i]);
                count = count.bind(&texts[*i]);
            }
        }
    }
    let rows = list.bind(limit).bind(offset).fetch_all(&state.pool).await?;
    let (total,) = count.fetch_one(&state.pool).await?;

    let mut items = Vec::new();
    for r in &rows {
        let row = LogRow::from_pg(r)?;
        if event(&row.action).is_some() {
            items.push(finalize(&system_entry(&row)?, None));
        } else if let Some(e) = group(&[&row])?.first() {
            items.push(finalize(e, None));
        }
    }
    Ok(ok(json!({"items": items, "total": total, "limit": limit, "offset": offset})))
}

/// `activity_kinds`: sorted set of event kinds and delivery kinds.
fn activity_kinds() -> Vec<&'static str> {
    let set: BTreeSet<&str> = EVENTS.iter().map(|e| e.1).chain(ALERT_ACTIONS.iter().map(|(_, k)| *k)).collect();
    set.into_iter().collect()
}

pub async fn list_kinds(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    auth::current_user(&state, &headers, READ, &actors).await?;
    Ok(ok(json!({"kinds": activity_kinds()})))
}

pub async fn mark_alerts_read(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, MARK_READ, &actors).await?;
    // `log_action(tenant, user, MARK_READ_ACTION)`: no resource, `{}` context.
    log_action(&state.pool, &user.tenant_id, &user.user_id, MARK_READ_ACTION, None, &json!({}), "success").await?;
    let read_at = last_read_at(&state.pool, &user.tenant_id, &user.user_id).await?;
    Ok(ok(json!({"last_read_at": read_at.map(|d| isoformat_utc(&d)), "unread_count": 0})))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    /// `EVENTS` re-read from the Python source: same actions, same order,
    /// same kind, severity and detail keys.
    #[test]
    fn registry_matches_events_py() {
        let src = include_str!("../../../../backend/activity/events.py");
        let body = src.split("EVENTS: dict[str, EventSpec] = {").nth(1).unwrap().split("\n}\n").next().unwrap();
        let mut parsed: Vec<(String, String, String, Vec<String>)> = Vec::new();
        for chunk in body.split("EventSpec(").skip(1).zip(body.split("EventSpec(")) {
            let (spec, before) = chunk;
            let action = before.rsplit('"').nth(1).unwrap().to_string();
            let spec = spec.split(')').next().unwrap();
            let field = |name: &str| -> String {
                let rest = spec.split(&format!("{name}=")).nth(1).unwrap();
                rest.split([',', '\n']).next().unwrap().trim().trim_matches('"').to_string()
            };
            let sev = match field("severity").as_str() {
                "CRITICAL" => "critical",
                "WARNING" => "warning",
                "INFO" => "info",
                other => panic!("severity {other}"),
            }
            .to_string();
            let keys_src = src_keys(spec);
            parsed.push((action, field("kind"), sev, keys_src));
        }
        let ours: Vec<(String, String, String, Vec<String>)> = EVENTS
            .iter()
            .map(|e| (e.0.into(), e.1.into(), e.2.into(), e.3.iter().map(|s| s.to_string()).collect()))
            .collect();
        assert_eq!(parsed, ours);
    }

    fn src_keys(spec: &str) -> Vec<String> {
        // detail_keys=( "a", "b" ) -- the tuple is the last parenthesis-free
        // run after `detail_keys=(`; `split(')')` above already cut it off.
        let Some(rest) = spec.split("detail_keys=(").nth(1) else { return vec![] };
        rest.split(',').map(|s| s.trim().trim_matches('"').to_string()).filter(|s| !s.is_empty()).collect()
    }

    fn row(action: &str, ctx: Value, status: &str, secs: i64) -> LogRow {
        LogRow {
            id: json!(format!("act_{secs}")),
            action: action.into(),
            context: ctx,
            status: Some(status.into()),
            created_at: Some(Utc.timestamp_opt(1_800_000_000 + secs, 0).unwrap()),
        }
    }

    #[test]
    fn fanout_groups_by_kind_against_the_newest_row() {
        let rows = [
            row("inventory_alert_email", json!({"critical": 3, "warning": 1, "recipient": "x"}), "success", 1000),
            row("inventory_alert_whatsapp", json!({"reason": "transport_error"}), "failed", 990),
            // 901 s older than the newest row: a new entry, even though it is
            // within 900 s of the previous row.
            row("inventory_alert_email", json!({}), "success", 99),
            row("monthly_roi_email", json!({"month": "2026-09"}), "success", 50),
        ];
        let refs: Vec<&LogRow> = rows.iter().collect();
        let g = group(&refs).unwrap();
        assert_eq!(g.len(), 3);
        let first = finalize(&g[0], None);
        assert_eq!(first["status"], "partial");
        assert_eq!(first["severity"], "warning");
        assert_eq!(first["channel"], "mixed");
        assert_eq!(first["reason"], "transport_error");
        assert_eq!(first["details"], json!({"critical": 3, "warning": 1}));
        assert_eq!(finalize(&g[2], None)["details"], json!({"month": "2026-09"}));
    }

    #[test]
    fn system_entries_keep_their_own_status_and_severity() {
        let r = row("training.failed",
            json!({"severity": "critical", "kind": "training", "reason": "engine_error", "session_id": "s1", "x": 1}),
            "success", 10);
        let e = finalize(&system_entry(&r).unwrap(), Some(Utc.timestamp_opt(1_800_000_000 + 10, 0).unwrap()));
        assert_eq!(e["status"], "recorded");
        assert_eq!(e["channel"], "system");
        assert_eq!(e["details"], json!({"session_id": "s1"}));
        assert_eq!(e["unread"], false);
        assert_eq!(e["reason_params"], json!({}));
    }

    #[test]
    fn kinds_are_sorted_and_unique() {
        assert_eq!(activity_kinds(), ["account", "billing", "data", "data_freshness", "limit", "monthly_roi", "purchase",
            "stockout_digest", "supplier_lead_time", "training"]);
    }
}
