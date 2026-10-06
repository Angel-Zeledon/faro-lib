//! Purchase-order approval: `backend/api/v1/po_approvals.py` and
//! `backend/inventory/po_approval_service.py`.
//!
//! Migrated (9 of the router's 10 routes):
//! * `GET    /inventory/po-approval/settings`
//! * `POST   /inventory/po-approval/rules`            (admin, company-wide)
//! * `PATCH  /inventory/po-approval/rules/{rule_id}`  (admin, company-wide)
//! * `DELETE /inventory/po-approval/rules/{rule_id}`  (admin, company-wide)
//! * `PUT    /inventory/po-approval/approvers/{user_id}`
//! * `GET    /inventory/po-approval/pending`
//! * `GET    /inventory/po/{po_log_id}/approval`
//! * `POST   /inventory/po/{po_log_id}/approval/approve`
//! * `POST   /inventory/po/{po_log_id}/approval/reject`
//!
//! NOT migrated: `POST /inventory/po/{po_log_id}/approval/request`. Its answer
//! carries `notified`, the number of approver mails that actually LEFT the
//! building, counted while the request runs. Mail now goes through the
//! outbox (`crate::outbox`), which answers "queued", not "sent": keeping that
//! contract would need Rust to send mail itself, which is the one thing wave 2
//! rules out. The decisions (approve / reject) do not report mail, so they
//! move, and queue the requester's email on the outbox.
//!
//! Everything is DB-only apart from that email and the `purchase_order.approved
//! / rejected` webhook rows, which `crate::webhook_events` queues for the
//! Python delivery loop, as for every other migrated write.

use std::collections::HashSet;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::{PgPool, Row};

use crate::activity::{record_event_with_reason, Event};
use crate::audit::{self, Note};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::outbox::{self, Channel, Message};
use crate::pycompat::{isoformat_utc, py_strip, take_chars};
use crate::routes::ok;
use crate::routes::po_payments::{format_po_number, po_not_found};
use crate::state::AppState;
use crate::validation::{self, as_object, bool_field, float_field, str_field, Body, Bound, Errors, Field, StrRules};

const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'inventory-approvals': purchase-order approval rules and decisions belong to the people who hold that authority",
    ),
    is_mcp: false,
};

pub(crate) const PENDING: &str = "pending_approval";
const APPROVED: &str = "approved";
const REJECTED: &str = "rejected";
const MAX_COMMENT_LENGTH: usize = 500;
const MIN_REJECT_REASON_LENGTH: usize = 3;
const EPS: f64 = 0.005;
const MAX_MONEY: f64 = 1e12;
const DEFAULT_WAREHOUSE: &str = "principal";

pub fn router() -> axum::Router<AppState> {
    use axum::routing::{get, patch, post, put};
    axum::Router::new()
        .route("/api/v1/inventory/po-approval/settings", get(settings))
        .route("/api/v1/inventory/po-approval/rules", post(create_rule))
        .route("/api/v1/inventory/po-approval/rules/{rule_id}", patch(update_rule).delete(delete_rule))
        .route("/api/v1/inventory/po-approval/approvers/{user_id}", put(set_approver))
        .route("/api/v1/inventory/po-approval/pending", get(pending))
        .route("/api/v1/inventory/po/{po_log_id}/approval", get(get_approval))
        .route("/api/v1/inventory/po/{po_log_id}/approval/approve", post(approve))
        .route("/api/v1/inventory/po/{po_log_id}/approval/reject", post(reject))
}

fn app_err(code: &str, msg: &str, status: u16, params: Value) -> ApiError {
    ApiError::app(code, msg, status, params)
}

fn iso(v: Option<DateTime<Utc>>) -> Value {
    v.map(|d| Value::String(isoformat_utc(&d))).unwrap_or(Value::Null)
}

fn opt_f(v: Option<f64>) -> Value {
    v.map(|f| json!(f)).unwrap_or(Value::Null)
}

// ── Rules ────────────────────────────────────────────────────────────────────

struct Rule {
    id: String,
    threshold: f64,
    warehouse: Option<String>,
    supplier_id: Option<String>,
    supplier_name: Option<String>,
    self_approve_below: Option<f64>,
    active: bool,
    created_by: String,
    created_at: Option<DateTime<Utc>>,
    updated_at: Option<DateTime<Utc>>,
}

impl Rule {
    fn json(&self) -> Value {
        json!({
            "id": self.id, "threshold": self.threshold, "warehouse": self.warehouse,
            "supplier_id": self.supplier_id, "supplier_name": self.supplier_name,
            "self_approve_below": self.self_approve_below, "active": self.active,
            "created_by": self.created_by, "created_at": iso(self.created_at), "updated_at": iso(self.updated_at),
        })
    }
}

/// `list_rules`: strictest first by threshold, then by age.
async fn list_rules(pool: &PgPool, tenant_id: &str) -> Result<Vec<Rule>, ApiError> {
    let rows = sqlx::query(
        "SELECT r.id, r.threshold, r.warehouse, r.supplier_id, s.name AS supplier_name, r.self_approve_below, \
                r.active, r.created_by, r.created_at, r.updated_at \
           FROM po_approval_rules r \
           LEFT JOIN suppliers s ON s.id = r.supplier_id AND s.tenant_id = r.tenant_id \
          WHERE r.tenant_id = $1 \
          ORDER BY r.threshold, r.created_at",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        out.push(Rule {
            id: r.try_get("id")?,
            threshold: r.try_get("threshold")?,
            warehouse: r.try_get("warehouse")?,
            supplier_id: r.try_get("supplier_id")?,
            supplier_name: r.try_get("supplier_name")?,
            self_approve_below: r.try_get("self_approve_below")?,
            active: r.try_get("active")?,
            created_by: r.try_get("created_by")?,
            created_at: r.try_get("created_at")?,
            updated_at: r.try_get("updated_at")?,
        });
    }
    Ok(out)
}

async fn get_rule(pool: &PgPool, tenant_id: &str, rule_id: &str) -> Result<Rule, ApiError> {
    list_rules(pool, tenant_id)
        .await?
        .into_iter()
        .find(|r| r.id == rule_id)
        .ok_or_else(|| app_err("po_approval_rule_not_found", "Approval rule not found", 404, json!({})))
}

struct CleanRule {
    threshold: f64,
    self_approve_below: Option<f64>,
    warehouse: Option<String>,
    supplier_id: Option<String>,
}

fn rule_invalid(msg: &str, field: &str) -> ApiError {
    app_err("po_approval_rule_invalid", msg, 422, json!({"field": field}))
}

/// `warehouse_service.resolve_canonical_name`: zero-width and bidi controls
/// stripped, an existing spelling of the name reused (case-insensitive), the
/// default warehouse when nothing is left.
async fn resolve_canonical_name(pool: &PgPool, tenant_id: &str, name: &str) -> Result<String, ApiError> {
    const INVISIBLE: [char; 13] = [
        '\u{200B}', '\u{200C}', '\u{200D}', '\u{FEFF}', '\u{202A}', '\u{202B}', '\u{202C}', '\u{202D}', '\u{202E}',
        '\u{2066}', '\u{2067}', '\u{2068}', '\u{2069}',
    ];
    let cleaned: String = name.chars().filter(|c| !INVISIBLE.contains(c)).collect();
    let stripped = py_strip(&cleaned).to_string();
    if stripped.is_empty() {
        return Ok(DEFAULT_WAREHOUSE.to_string());
    }
    let row: Option<(String,)> = sqlx::query_as(
        "SELECT name FROM warehouses WHERE tenant_id = $1 AND LOWER(name) = LOWER($2) ORDER BY name LIMIT 1",
    )
    .bind(tenant_id)
    .bind(&stripped)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|r| r.0).unwrap_or(stripped))
}

/// `_clean_rule`. `threshold` is `None` when Python would hit `float(None)`.
async fn clean_rule(
    pool: &PgPool,
    tenant_id: &str,
    threshold: Option<f64>,
    below: Option<f64>,
    warehouse: Option<&str>,
    supplier_id: Option<&str>,
) -> Result<CleanRule, ApiError> {
    let threshold = threshold.ok_or_else(ApiError::internal)?;
    if !(threshold > 0.0) {
        return Err(rule_invalid("The threshold must be above zero", "threshold"));
    }
    if let Some(b) = below {
        if !(b > threshold) {
            return Err(rule_invalid("The self-approval limit must be above the threshold", "self_approve_below"));
        }
    }
    let wh = py_strip(warehouse.unwrap_or("")).to_string();
    let warehouse = if wh.is_empty() { None } else { Some(resolve_canonical_name(pool, tenant_id, &wh).await?) };
    let sup = py_strip(supplier_id.unwrap_or("")).to_string();
    let supplier_id = if sup.is_empty() {
        None
    } else {
        let known: Option<(i32,)> = sqlx::query_as("SELECT 1 AS x FROM suppliers WHERE id = $1 AND tenant_id = $2")
            .bind(&sup)
            .bind(tenant_id)
            .fetch_optional(pool)
            .await?;
        if known.is_none() {
            return Err(app_err("supplier_not_found", "Supplier not found", 404, json!({})));
        }
        Some(sup)
    };
    Ok(CleanRule { threshold, self_approve_below: below, warehouse, supplier_id })
}

// ── Approvers ────────────────────────────────────────────────────────────────

pub(crate) struct Approver {
    pub(crate) id: String,
    pub(crate) email: Option<String>,
    pub(crate) full_name: Option<String>,
    pub(crate) role: String,
}

impl Approver {
    fn json(&self) -> Value {
        json!({"id": self.id, "email": self.email, "full_name": self.full_name, "role": self.role})
    }
}

/// `list_approvers`: flagged, active, able to act.
pub(crate) async fn list_approvers(pool: &PgPool, tenant_id: &str) -> Result<Vec<Approver>, ApiError> {
    let rows = sqlx::query(
        "SELECT id, email, full_name, role FROM users \
          WHERE tenant_id = $1 AND can_approve_po AND status = 'active' AND role IN ('admin', 'analyst') \
          ORDER BY full_name NULLS LAST, email",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        out.push(Approver {
            id: r.try_get("id")?,
            email: r.try_get("email")?,
            full_name: r.try_get("full_name")?,
            role: r.try_get("role")?,
        });
    }
    Ok(out)
}

// ── The requirement ──────────────────────────────────────────────────────────

pub(crate) struct Po {
    pub(crate) id: String,
    pub(crate) po_number: Option<i32>,
    pub(crate) destination_warehouse: Option<String>,
    pub(crate) approval_status: Option<String>,
    pub(crate) approved_amount: Option<f64>,
}

/// `_get_po`.
pub(crate) async fn get_po(pool: &PgPool, tenant_id: &str, po_log_id: &str) -> Result<Po, ApiError> {
    let row = sqlx::query(
        "SELECT id, po_number, destination_warehouse, approval_status, approved_amount \
           FROM inventory_po_log WHERE id = $1 AND tenant_id = $2",
    )
    .bind(po_log_id)
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?
    .ok_or_else(po_not_found)?;
    Ok(Po {
        id: row.try_get("id")?,
        po_number: row.try_get("po_number")?,
        destination_warehouse: row.try_get("destination_warehouse")?,
        approval_status: row.try_get("approval_status")?,
        approved_amount: row.try_get("approved_amount")?,
    })
}

struct Facts {
    amount: Option<f64>,
    warehouses: HashSet<String>,
    supplier_ids: HashSet<String>,
    supplier_names: HashSet<String>,
}

fn lower_stripped(s: &str) -> String {
    py_strip(s).to_lowercase()
}

/// `_order_facts`: value, warehouses and suppliers of the ordered lines.
async fn order_facts(pool: &PgPool, tenant_id: &str, po: &Po) -> Result<Facts, ApiError> {
    let lines = sqlx::query(
        "SELECT i.supplier, i.supplier_id, i.warehouse, i.final_qty, i.unit_cost \
           FROM inventory_po_items i \
          WHERE i.po_log_id = $1 AND i.tenant_id = $2 AND i.status IN ('approved', 'modified')",
    )
    .bind(&po.id)
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut priced: Vec<f64> = Vec::new();
    let mut warehouses: HashSet<String> = HashSet::new();
    let mut supplier_ids = HashSet::new();
    let mut supplier_names = HashSet::new();
    for l in &lines {
        let supplier: Option<String> = l.try_get("supplier")?;
        let supplier_id: Option<String> = l.try_get("supplier_id")?;
        let warehouse: Option<String> = l.try_get("warehouse")?;
        let final_qty: Option<f64> = l.try_get("final_qty")?;
        let unit_cost: Option<f64> = l.try_get("unit_cost")?;
        if let Some(c) = unit_cost {
            priced.push(final_qty.unwrap_or(0.0) * c);
        }
        warehouses.insert(lower_stripped(warehouse.as_deref().unwrap_or("")));
        if let Some(s) = supplier_id.filter(|s| !s.is_empty()) {
            supplier_ids.insert(s);
        }
        if let Some(s) = supplier.as_deref().filter(|s| !py_strip(s).is_empty()) {
            supplier_names.insert(lower_stripped(s));
        }
    }
    if let Some(d) = po.destination_warehouse.as_deref().filter(|d| !d.is_empty()) {
        warehouses.insert(lower_stripped(d));
    }
    warehouses.retain(|w| !w.is_empty());
    // `sum(priced)` starts from the int 0: adding the first float is exact.
    let amount = if priced.is_empty() { None } else { Some(priced.iter().fold(0.0, |a, b| a + b)) };
    Ok(Facts { amount, warehouses, supplier_ids, supplier_names })
}

fn rule_matches(rule: &Rule, amount: f64, facts: &Facts) -> bool {
    if !rule.active || amount + EPS < rule.threshold {
        return false;
    }
    if let Some(w) = rule.warehouse.as_deref().filter(|w| !w.is_empty()) {
        if !facts.warehouses.contains(&lower_stripped(w)) {
            return false;
        }
    }
    if let Some(sid) = rule.supplier_id.as_deref().filter(|s| !s.is_empty()) {
        let name = lower_stripped(rule.supplier_name.as_deref().unwrap_or(""));
        if !facts.supplier_ids.contains(sid) && !facts.supplier_names.contains(&name) {
            return false;
        }
    }
    true
}

pub(crate) struct Req {
    pub(crate) required: bool,
    pub(crate) status: String,
    pub(crate) amount: Option<f64>,
    pub(crate) amount_known: Option<bool>,
    pub(crate) rule_id: Option<String>,
    pub(crate) self_approve_below: Option<f64>,
}

impl Req {
    fn none(amount: Option<f64>, amount_known: Option<bool>) -> Req {
        Req { required: false, status: "not_required".into(), amount, amount_known, rule_id: None,
            self_approve_below: None }
    }
    fn put(&self, m: &mut Map<String, Value>) {
        m.insert("required".into(), json!(self.required));
        m.insert("status".into(), json!(self.status));
        m.insert("amount".into(), opt_f(self.amount));
        m.insert("amount_known".into(), json!(self.amount_known));
        m.insert("rule_id".into(), json!(self.rule_id));
        m.insert("self_approve_below".into(), opt_f(self.self_approve_below));
    }
}

/// `requirement`: what approval means for this order right now.
fn requirement(rules: &[Rule], po: &Po, facts: &Facts) -> Req {
    if !rules.iter().any(|r| r.active) {
        return Req::none(None, None);
    }
    let Some(amount) = facts.amount else { return Req::none(None, Some(false)) };
    let matching: Vec<&Rule> = rules.iter().filter(|r| rule_matches(r, amount, facts)).collect();
    if matching.is_empty() {
        return Req::none(Some(amount), Some(true));
    }
    // Strictest: a missing self-approval limit beats a present one, then the
    // highest threshold; the first of equals wins (stable sort).
    let mut rule = matching[0];
    for r in &matching[1..] {
        let key = (r.self_approve_below.is_some(), -r.threshold);
        let best = (rule.self_approve_below.is_some(), -rule.threshold);
        if key.0 < best.0 || (key.0 == best.0 && key.1 < best.1) {
            rule = r;
        }
    }
    let (status, required) = match (po.approval_status.as_deref(), po.approved_amount) {
        (Some(s), Some(a)) if s == APPROVED && amount <= a + EPS => (APPROVED, false),
        (Some(s), _) if s == PENDING => (PENDING, true),
        (Some(s), _) if s == REJECTED => (REJECTED, true),
        _ => ("approval_needed", true),
    };
    Req {
        required,
        status: status.to_string(),
        amount: Some(amount),
        amount_known: Some(true),
        rule_id: Some(rule.id.clone()),
        self_approve_below: rule.self_approve_below,
    }
}

pub(crate) async fn requirement_of(pool: &PgPool, tenant_id: &str, po: &Po) -> Result<Req, ApiError> {
    let rules = list_rules(pool, tenant_id).await?;
    if !rules.iter().any(|r| r.active) {
        return Ok(Req::none(None, None));
    }
    let facts = order_facts(pool, tenant_id, po).await?;
    Ok(requirement(&rules, po, &facts))
}

// ── History and describe ─────────────────────────────────────────────────────

/// `_name_of`: full name, else the local part of the email, else null.
pub(crate) fn name_of(full_name: Option<&str>, email: Option<&str>) -> Option<String> {
    if let Some(n) = full_name.filter(|n| !n.is_empty()) {
        return Some(n.to_string());
    }
    let local = email.unwrap_or("").split('@').next().unwrap_or("");
    if local.is_empty() { None } else { Some(local.to_string()) }
}

struct HistoryRow {
    id: String,
    status: String,
    amount: f64,
    requested_by: String,
    requested_at: Option<DateTime<Utc>>,
    request_note: Option<String>,
    decided_by: Option<String>,
    decided_at: Option<DateTime<Utc>>,
    comment: Option<String>,
    decided_channel: Option<String>,
    requested_by_name: Option<String>,
    decided_by_name: Option<String>,
}

impl HistoryRow {
    fn json(&self) -> Value {
        json!({
            "id": self.id, "status": self.status, "amount": self.amount, "requested_by": self.requested_by,
            "requested_at": iso(self.requested_at), "request_note": self.request_note,
            "decided_by": self.decided_by, "decided_at": iso(self.decided_at), "comment": self.comment,
            "decided_channel": self.decided_channel, "requested_by_name": self.requested_by_name, "decided_by_name": self.decided_by_name,
        })
    }
}

async fn history(pool: &PgPool, tenant_id: &str, po_log_id: &str) -> Result<Vec<HistoryRow>, ApiError> {
    let rows = sqlx::query(
        "SELECT a.id, a.status, a.amount, a.requested_by, a.requested_at, a.request_note, \
                a.decided_by, a.decided_at, a.comment, a.decided_channel, \
                rq.full_name AS requested_by_name, rq.email AS requested_by_email, \
                dc.full_name AS decided_by_name, dc.email AS decided_by_email \
           FROM po_approvals a \
           LEFT JOIN users rq ON rq.id = a.requested_by \
           LEFT JOIN users dc ON dc.id = a.decided_by \
          WHERE a.po_log_id = $1 AND a.tenant_id = $2 \
          ORDER BY a.requested_at DESC, a.id DESC",
    )
    .bind(po_log_id)
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        let rq_name: Option<String> = r.try_get("requested_by_name")?;
        let rq_email: Option<String> = r.try_get("requested_by_email")?;
        let dc_name: Option<String> = r.try_get("decided_by_name")?;
        let dc_email: Option<String> = r.try_get("decided_by_email")?;
        out.push(HistoryRow {
            id: r.try_get("id")?,
            status: r.try_get("status")?,
            amount: r.try_get("amount")?,
            requested_by: r.try_get("requested_by")?,
            requested_at: r.try_get("requested_at")?,
            request_note: r.try_get("request_note")?,
            decided_by: r.try_get("decided_by")?,
            decided_at: r.try_get("decided_at")?,
            comment: r.try_get("comment")?,
            decided_channel: r.try_get("decided_channel")?,
            requested_by_name: name_of(rq_name.as_deref(), rq_email.as_deref()),
            decided_by_name: name_of(dc_name.as_deref(), dc_email.as_deref()),
        });
    }
    Ok(out)
}

/// `_may_decide`.
pub(crate) fn may_decide(is_approver: bool, user_id: &str, requested_by: &str, amount: f64, req: &Req) -> bool {
    if !is_approver {
        return false;
    }
    if requested_by != user_id {
        return true;
    }
    req.self_approve_below.is_some_and(|below| amount < below)
}

pub(crate) async fn is_approver(pool: &PgPool, tenant_id: &str, user_id: &str) -> Result<bool, ApiError> {
    Ok(list_approvers(pool, tenant_id).await?.iter().any(|a| a.id == user_id))
}

/// `describe`: everything the PO's approval panel needs.
async fn describe(pool: &PgPool, tenant_id: &str, po_log_id: &str, user_id: &str) -> Result<Map<String, Value>, ApiError> {
    let po = get_po(pool, tenant_id, po_log_id).await?;
    let req = requirement_of(pool, tenant_id, &po).await?;
    let hist = history(pool, tenant_id, po_log_id).await?;
    let open = hist.iter().find(|h| h.status == "requested");
    let can_decide = match open {
        Some(o) => may_decide(is_approver(pool, tenant_id, user_id).await?, user_id, &o.requested_by, o.amount, &req),
        None => false,
    };
    let mut m = Map::new();
    m.insert("po_log_id".into(), json!(po_log_id));
    req.put(&mut m);
    m.insert("history".into(), Value::Array(hist.iter().map(HistoryRow::json).collect()));
    m.insert("open_request".into(), open.map(HistoryRow::json).unwrap_or(Value::Null));
    m.insert("can_decide".into(), json!(can_decide));
    Ok(m)
}

// ── Reads ────────────────────────────────────────────────────────────────────

pub async fn settings(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let pool = &state.pool;
    let rules = list_rules(pool, &user.tenant_id).await?;
    let scope = wscope::scope_names(pool, &user).await?;
    let shown: Vec<Value> = rules
        .iter()
        .filter(|r| {
            scope.is_none()
                || py_strip(r.warehouse.as_deref().unwrap_or("")).is_empty()
                || wscope::in_scope(&scope, r.warehouse.as_deref())
        })
        .map(Rule::json)
        .collect();
    let approvers = list_approvers(pool, &user.tenant_id).await?;
    Ok(ok(json!({
        "rules": shown,
        "enabled": rules.iter().any(|r| r.active),
        "approvers": approvers.iter().map(Approver::json).collect::<Vec<_>>(),
        "is_approver": approvers.iter().any(|a| a.id == user.user_id),
    })))
}

/// `wscope.allowed_po_ids`: the orders a scoped caller may see (`None` = all).
async fn allowed_po_ids(pool: &PgPool, user: &CurrentUser) -> Result<Option<HashSet<String>>, ApiError> {
    let scope = wscope::scope_names(pool, user).await?;
    if scope.is_none() {
        return Ok(None);
    }
    let default = wscope::tenant_default(pool, &user.tenant_id).await?;
    let rows: Vec<(String, Option<String>)> =
        sqlx::query_as("SELECT id, destination_warehouse FROM inventory_po_log WHERE tenant_id = $1")
            .bind(&user.tenant_id)
            .fetch_all(pool)
            .await?;
    Ok(Some(
        rows.into_iter()
            .filter(|(_, dest)| {
                let named = py_strip(dest.as_deref().unwrap_or("")).to_string();
                let target = if named.is_empty() { default.clone() } else { named };
                wscope::in_scope(&scope, Some(&target))
            })
            .map(|(id, _)| id)
            .collect(),
    ))
}

pub async fn pending(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let pool = &state.pool;
    let tenant = &user.tenant_id;
    let approver = is_approver(pool, tenant, &user.user_id).await?;
    if !approver {
        return Ok(ok(json!({"is_approver": false, "items": []})));
    }
    let rows = sqlx::query(
        "SELECT a.id AS approval_id, a.po_log_id, a.amount, a.requested_by, a.requested_at, a.request_note, \
                l.po_number, l.sku_count, l.destination_warehouse, rq.full_name AS requested_by_name, \
                rq.email AS requested_by_email, \
                (SELECT string_agg(DISTINCT i.supplier, ', ') FROM inventory_po_items i \
                  WHERE i.po_log_id = l.id AND i.status IN ('approved', 'modified') AND i.supplier IS NOT NULL) \
                  AS suppliers \
           FROM po_approvals a \
           JOIN inventory_po_log l ON l.id = a.po_log_id \
           LEFT JOIN users rq ON rq.id = a.requested_by \
          WHERE a.tenant_id = $1 AND a.status = 'requested' AND l.cancelled_at IS NULL \
          ORDER BY a.requested_at",
    )
    .bind(tenant)
    .fetch_all(pool)
    .await?;
    let allowed = allowed_po_ids(pool, &user).await?;
    let mut items = Vec::new();
    for r in &rows {
        let po_log_id: String = r.try_get("po_log_id")?;
        let po = get_po(pool, tenant, &po_log_id).await?;
        let req = requirement_of(pool, tenant, &po).await?;
        let amount: f64 = r.try_get("amount")?;
        let requested_by: String = r.try_get("requested_by")?;
        let po_number: Option<i32> = r.try_get("po_number")?;
        let rq_name: Option<String> = r.try_get("requested_by_name")?;
        let rq_email: Option<String> = r.try_get("requested_by_email")?;
        let item = json!({
            "approval_id": r.try_get::<String, _>("approval_id")?,
            "po_log_id": po_log_id,
            "reference": format_po_number(po_number, &po_log_id),
            "amount": amount,
            "sku_count": r.try_get::<Option<i32>, _>("sku_count")?,
            "suppliers": r.try_get::<Option<String>, _>("suppliers")?,
            "warehouse": r.try_get::<Option<String>, _>("destination_warehouse")?,
            "requested_by_name": name_of(rq_name.as_deref(), rq_email.as_deref()),
            "requested_at": iso(r.try_get("requested_at")?),
            "note": r.try_get::<Option<String>, _>("request_note")?,
            "can_decide": may_decide(true, &user.user_id, &requested_by, amount, &req),
        });
        if allowed.as_ref().map_or(true, |a| a.contains(&po_log_id)) {
            items.push(item);
        }
    }
    Ok(ok(json!({"is_approver": true, "items": items})))
}

pub async fn get_approval(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    wscope::po_guard(&state.pool, &user, &po_log_id).await?;
    Ok(ok(Value::Object(describe(&state.pool, &user.tenant_id, &po_log_id, &user.user_id).await?)))
}

// ── Configuration (admin) ────────────────────────────────────────────────────

/// `require_admin` then `wscope.require_company_setting`.
async fn company_admin(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    Ok(user)
}

async fn require_company_setting(pool: &PgPool, user: &CurrentUser) -> Result<(), ApiError> {
    if wscope::is_scoped(pool, user).await? {
        return Err(app_err(
            "warehouse_scope_company_setting",
            "Only a user with access to every warehouse can change this setting.",
            403,
            json!({}),
        ));
    }
    Ok(())
}

fn money_bound() -> Option<Bound> {
    Some(Bound::Float(MAX_MONEY))
}

const WAREHOUSE_RULES: StrRules = StrRules { min_length: None, max_length: Some(120), pattern: None };
const SUPPLIER_RULES: StrRules = StrRules { min_length: None, max_length: Some(64), pattern: None };

fn set_field<T>(f: &Field<T>) -> bool {
    !matches!(f, Field::Absent)
}

fn note_audit(target: Option<&str>) -> Note {
    Note { target_id: target.map(str::to_string), label: None, before: None, after: None }
}

pub async fn create_rule(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<(axum::http::StatusCode, Json<Value>), ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = company_admin(&state, &actors, &headers).await?;
    let obj = validation::body_object(&body)?;
    let p = [json!("body")];
    let mut errs = Errors::default();
    let threshold = float_field(&mut errs, &obj, &p, "threshold", true, false, Some(Bound::Int(0)), money_bound());
    let warehouse = str_field(&mut errs, &obj, &p, "warehouse", false, true, &WAREHOUSE_RULES);
    let supplier = str_field(&mut errs, &obj, &p, "supplier_id", false, true, &SUPPLIER_RULES);
    let below = float_field(&mut errs, &obj, &p, "self_approve_below", false, true, Some(Bound::Int(0)), money_bound());
    errs.into_result()?;
    let pool = &state.pool;
    require_company_setting(pool, &user).await?;

    let clean = clean_rule(
        pool,
        &user.tenant_id,
        match threshold { Field::Value(v) => Some(v), _ => None },
        match below { Field::Value(v) => Some(v), _ => None },
        match &warehouse { Field::Value(v) => Some(v.as_str()), _ => None },
        match &supplier { Field::Value(v) => Some(v.as_str()), _ => None },
    )
    .await?;
    // A rule with nobody to approve would freeze every order it matches.
    if list_approvers(pool, &user.tenant_id).await?.is_empty() {
        return Err(app_err("po_approval_no_approver",
            "Flag at least one person who can approve before creating a rule", 409, json!({})));
    }
    let (id,): (String,) = sqlx::query_as(
        "INSERT INTO po_approval_rules (tenant_id, threshold, warehouse, supplier_id, self_approve_below, created_by) \
         VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
    )
    .bind(&user.tenant_id)
    .bind(clean.threshold)
    .bind(&clean.warehouse)
    .bind(&clean.supplier_id)
    .bind(clean.self_approve_below)
    .bind(&user.user_id)
    .fetch_one(pool)
    .await?;
    let rule = get_rule(pool, &user.tenant_id, &id).await?;
    audit::record(&state, &actors, "POST", "/inventory/po-approval/rules", None, note_audit(None), 201).await;
    Ok((axum::http::StatusCode::CREATED, ok(rule.json())))
}

pub async fn update_rule(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(rule_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = company_admin(&state, &actors, &headers).await?;
    let obj = validation::body_object(&body)?;
    let p = [json!("body")];
    let mut errs = Errors::default();
    let threshold = float_field(&mut errs, &obj, &p, "threshold", false, true, Some(Bound::Int(0)), money_bound());
    let warehouse = str_field(&mut errs, &obj, &p, "warehouse", false, true, &WAREHOUSE_RULES);
    let supplier = str_field(&mut errs, &obj, &p, "supplier_id", false, true, &SUPPLIER_RULES);
    let below = float_field(&mut errs, &obj, &p, "self_approve_below", false, true, Some(Bound::Int(0)), money_bound());
    let active = bool_field(&mut errs, &obj, &p, "active", true);
    errs.into_result()?;
    let pool = &state.pool;
    require_company_setting(pool, &user).await?;

    let current = get_rule(pool, &user.tenant_id, &rule_id).await?;
    // `{**current, **{k: v for k, v in data.items()}}`: a key that was SENT
    // (null included) replaces the stored value; one that was not keeps it.
    let merged_threshold = if set_field(&threshold) {
        match threshold { Field::Value(v) => Some(v), _ => None } // explicit null: float(None) is a 500
    } else {
        Some(current.threshold)
    };
    let merged_below = if set_field(&below) { match below { Field::Value(v) => Some(v), _ => None } }
        else { current.self_approve_below };
    let merged_wh = if set_field(&warehouse) { match warehouse { Field::Value(v) => Some(v), _ => None } }
        else { current.warehouse.clone() };
    let merged_sup = if set_field(&supplier) { match supplier { Field::Value(v) => Some(v), _ => None } }
        else { current.supplier_id.clone() };
    let clean = clean_rule(pool, &user.tenant_id, merged_threshold, merged_below, merged_wh.as_deref(),
        merged_sup.as_deref()).await?;
    let active = match active { Field::Value(b) => b, _ => current.active };
    if active && list_approvers(pool, &user.tenant_id).await?.is_empty() {
        return Err(app_err("po_approval_no_approver",
            "Flag at least one person who can approve before creating a rule", 409, json!({})));
    }
    sqlx::query(
        "UPDATE po_approval_rules SET threshold = $1, warehouse = $2, supplier_id = $3, \
                self_approve_below = $4, active = $5, updated_at = NOW() \
          WHERE id = $6 AND tenant_id = $7",
    )
    .bind(clean.threshold)
    .bind(&clean.warehouse)
    .bind(&clean.supplier_id)
    .bind(clean.self_approve_below)
    .bind(active)
    .bind(&rule_id)
    .bind(&user.tenant_id)
    .execute(pool)
    .await?;
    let rule = get_rule(pool, &user.tenant_id, &rule_id).await?;
    audit::record(&state, &actors, "PATCH", "/inventory/po-approval/rules/{rule_id}", Some(&rule_id),
        note_audit(None), 200).await;
    Ok(ok(rule.json()))
}

pub async fn delete_rule(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(rule_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = company_admin(&state, &actors, &headers).await?;
    let pool = &state.pool;
    require_company_setting(pool, &user).await?;
    get_rule(pool, &user.tenant_id, &rule_id).await?;
    sqlx::query("DELETE FROM po_approval_rules WHERE id = $1 AND tenant_id = $2")
        .bind(&rule_id)
        .bind(&user.tenant_id)
        .execute(pool)
        .await?;
    audit::record(&state, &actors, "DELETE", "/inventory/po-approval/rules/{rule_id}", Some(&rule_id),
        note_audit(None), 200).await;
    Ok(ok(json!({"deleted": true})))
}

pub async fn set_approver(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(user_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = company_admin(&state, &actors, &headers).await?;
    let obj = validation::body_object(&body)?;
    let p = [json!("body")];
    let mut errs = Errors::default();
    let can_approve = if obj.contains_key("can_approve") {
        bool_field(&mut errs, &obj, &p, "can_approve", false)
    } else {
        errs.push("missing", &validation::loc(&p, "can_approve"), "Field required".into(),
            &Value::Object(obj.clone()), None);
        Field::Absent
    };
    errs.into_result()?;
    let Field::Value(can_approve) = can_approve else { return Err(ApiError::internal()) };
    let pool = &state.pool;
    require_company_setting(pool, &user).await?;

    let target: Option<(String, String, Option<bool>)> = sqlx::query_as(
        "SELECT id, role, can_approve_po FROM users WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&user_id)
    .bind(&user.tenant_id)
    .fetch_optional(pool)
    .await?;
    let Some((_, role, _)) = target else {
        return Err(app_err("user_not_found", "User not found", 404, json!({})));
    };
    if can_approve && role != "admin" && role != "analyst" {
        return Err(app_err("po_approval_approver_role", "Only an admin or an analyst can be an approver", 409,
            json!({})));
    }
    if !can_approve {
        let active: Option<(i32,)> = sqlx::query_as(
            "SELECT 1 AS x FROM po_approval_rules WHERE tenant_id = $1 AND active LIMIT 1",
        )
        .bind(&user.tenant_id)
        .fetch_optional(pool)
        .await?;
        if active.is_some() {
            let remaining = list_approvers(pool, &user.tenant_id).await?.into_iter().filter(|a| a.id != user_id).count();
            if remaining == 0 {
                return Err(app_err("po_approval_last_approver",
                    "Approval rules are active; keep at least one approver or turn the rules off first", 409,
                    json!({})));
            }
        }
    }
    sqlx::query("UPDATE users SET can_approve_po = $1 WHERE id = $2 AND tenant_id = $3")
        .bind(can_approve)
        .bind(&user_id)
        .bind(&user.tenant_id)
        .execute(pool)
        .await?;
    audit::record(&state, &actors, "PUT", "/inventory/po-approval/approvers/{user_id}", Some(&user_id),
        note_audit(None), 200).await;
    Ok(ok(json!({"user_id": user_id, "can_approve": can_approve})))
}

// ── Decisions ────────────────────────────────────────────────────────────────

fn py_bytes_repr(raw: &[u8]) -> String {
    validation::py_bytes_repr(raw)
}

/// `body: Optional[DecisionBody] = None`: no body or JSON null is no comment.
fn optional_comment(body: &Body, raw: &[u8]) -> Result<Option<String>, ApiError> {
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
    let c = str_field(&mut errs, obj, &at, "comment", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_COMMENT_LENGTH), pattern: None });
    errs.into_result()?;
    Ok(match c { Field::Value(c) => Some(c), _ => None })
}

/// `body: DecisionBody` (required).
fn required_comment(body: &Body) -> Result<Option<String>, ApiError> {
    let obj = validation::body_object(body)?;
    let at = [Value::String("body".into())];
    let mut errs = Errors::default();
    let c = str_field(&mut errs, &obj, &at, "comment", false, true,
        &StrRules { min_length: None, max_length: Some(MAX_COMMENT_LENGTH), pattern: None });
    errs.into_result()?;
    Ok(match c { Field::Value(c) => Some(c), _ => None })
}

pub async fn approve(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = crate::routes::po_payments::po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let comment = optional_comment(&body, &bytes)?;
    decide(&state, &user, &po_log_id, "approved", comment).await
}

pub async fn reject(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(po_log_id): Path<String>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = crate::routes::po_payments::po_writer(&state, &actors, &headers, ROUTE, &po_log_id).await?;
    let comment = required_comment(&body)?;
    decide(&state, &user, &po_log_id, "rejected", comment).await
}

/// `(comment or "").strip()[:500] or None`.
fn clean_comment(comment: Option<&str>) -> Option<String> {
    let c = take_chars(py_strip(comment.unwrap_or("")), MAX_COMMENT_LENGTH);
    if c.is_empty() { None } else { Some(c) }
}

struct Latest {
    id: String,
    status: String,
    amount: f64,
    requested_by: String,
}

async fn latest(pool: &PgPool, po_log_id: &str) -> Result<Option<Latest>, ApiError> {
    let row = sqlx::query(
        "SELECT id, status, amount, requested_by FROM po_approvals WHERE po_log_id = $1 \
          ORDER BY requested_at DESC, id DESC LIMIT 1",
    )
    .bind(po_log_id)
    .fetch_optional(pool)
    .await?;
    Ok(match row {
        Some(r) => Some(Latest {
            id: r.try_get("id")?,
            status: r.try_get("status")?,
            amount: r.try_get("amount")?,
            requested_by: r.try_get("requested_by")?,
        }),
        None => None,
    })
}

/// `{**describe(...), "changed": False}`.
async fn unchanged(pool: &PgPool, tenant_id: &str, po_log_id: &str, user_id: &str) -> Result<Json<Value>, ApiError> {
    let mut m = describe(pool, tenant_id, po_log_id, user_id).await?;
    m.insert("changed".into(), json!(false));
    Ok(ok(Value::Object(m)))
}

fn already_decided(decision: &str) -> ApiError {
    app_err("po_approval_already_decided", "This request was already decided", 409, json!({"decision": decision}))
}

/// `decide` + the route's event. Approve and reject share it.
async fn decide(
    state: &AppState,
    user: &CurrentUser,
    po_log_id: &str,
    decision: &str,
    comment: Option<String>,
) -> Result<Json<Value>, ApiError> {
    decide_core(state, &user.tenant_id, &user.user_id, po_log_id, decision, comment, None).await
}

/// How a decision reached us, beyond the app: the decision link in a message
/// (`routes/approval_links.rs`). The rules below are the SAME for both: this is
/// the only function that decides an approval, so a link can never do what the
/// app would refuse.
pub(crate) struct ViaLink<'a> {
    /// The `po_approval_links` row being spent by this decision.
    pub link_id: &'a str,
}

/// `po_approval_service.decide(..., channel=...)` + the route's event.
///
/// `link` is `Some` for a decision taken through a message. Then the link is
/// consumed INSIDE the decision's transaction (so "used" and "decided" are one
/// fact, and a crash can neither burn a link without deciding nor decide
/// without burning it), and any answer that would be an idempotent "already
/// done" for the app is instead the one neutral not-found: a replayed link must
/// not get a 200.
pub(crate) async fn decide_core(
    state: &AppState,
    tenant: &str,
    user_id: &str,
    po_log_id: &str,
    decision: &str,
    comment: Option<String>,
    link: Option<ViaLink<'_>>,
) -> Result<Json<Value>, ApiError> {
    let pool = &state.pool;
    let channel: Option<&str> = link.as_ref().map(|_| "message");
    let po = get_po(pool, tenant, po_log_id).await?;
    let clean = clean_comment(comment.as_deref());
    if decision == "rejected" && clean.as_deref().map_or(true, |c| c.chars().count() < MIN_REJECT_REASON_LENGTH) {
        return Err(app_err("po_approval_reason_required", "Say why you are rejecting this order", 422, json!({})));
    }
    let Some(last) = latest(pool, po_log_id).await? else {
        return Err(app_err("po_approval_not_requested", "Nobody has asked for approval on this order", 409, json!({})));
    };
    if last.status != "requested" {
        if link.is_some() {
            return Err(crate::routes::approval_links::not_found());
        }
        if last.status == decision {
            return unchanged(pool, tenant, po_log_id, user_id).await;
        }
        return Err(already_decided(&last.status));
    }
    let req = requirement_of(pool, tenant, &po).await?;
    if !is_approver(pool, tenant, user_id).await? {
        return Err(app_err("po_approval_not_approver", "You are not allowed to approve orders", 403, json!({})));
    }
    let own = last.requested_by == user_id;
    if own && decision == "approved" && !req.self_approve_below.is_some_and(|b| last.amount < b) {
        return Err(app_err("po_approval_self_approval", "You cannot approve your own order at this value", 403,
            json!({})));
    }
    let amount_now = req.amount.unwrap_or(last.amount);

    let mut tx = pool.begin().await?;
    if let Some(l) = &link {
        // Spend the link first: a concurrent second use blocks here on the row
        // lock, then finds it used and is refused, whatever it asked for.
        let spent: Option<(String,)> = sqlx::query_as(
            "UPDATE po_approval_links SET used_at = NOW(), used_decision = $2 \
              WHERE id = $1 AND used_at IS NULL AND revoked_at IS NULL AND expires_at > NOW() RETURNING id",
        )
        .bind(l.link_id)
        .bind(decision)
        .fetch_optional(&mut *tx)
        .await?;
        if spent.is_none() {
            tx.rollback().await?;
            return Err(crate::routes::approval_links::not_found());
        }
    }
    let won: Option<(String,)> = sqlx::query_as(
        "UPDATE po_approvals SET status = $1, decided_by = $2, decided_at = NOW(), comment = $3, \
                decided_channel = $6 \
          WHERE id = $4 AND tenant_id = $5 AND status = 'requested' RETURNING id",
    )
    .bind(decision)
    .bind(user_id)
    .bind(&clean)
    .bind(&last.id)
    .bind(tenant)
    .bind(channel)
    .fetch_optional(&mut *tx)
    .await?;
    if won.is_some() {
        // A decided request needs no more links: every open one, for every
        // approver and channel, dies with the decision, in this transaction.
        sqlx::query(
            "UPDATE po_approval_links SET revoked_at = NOW(), revoked_by = NULL, revoked_reason = 'decided' \
              WHERE tenant_id = $1 AND approval_id = $2 AND used_at IS NULL AND revoked_at IS NULL",
        )
        .bind(tenant)
        .bind(&last.id)
        .execute(&mut *tx)
        .await?;
        sqlx::query("UPDATE inventory_po_log SET approval_status = $1, approved_amount = $2 WHERE id = $3 AND tenant_id = $4")
            .bind(if decision == "approved" { APPROVED } else { REJECTED })
            .bind(if decision == "approved" { Some(amount_now) } else { None })
            .bind(po_log_id)
            .bind(tenant)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
    } else {
        // Another decision landed between the read and the write: nothing of
        // this call survives (the link, if any, is not spent).
        tx.rollback().await?;
        if link.is_some() {
            return Err(crate::routes::approval_links::not_found());
        }
        let now = latest(pool, po_log_id).await?;
        let status = now.map(|l| l.status);
        if status.as_deref() == Some(decision) {
            return unchanged(pool, tenant, po_log_id, user_id).await;
        }
        return Err(already_decided(status.as_deref().unwrap_or("")));
    }
    tracing::info!("[po-approval] {} tenant={tenant} po={po_log_id} by={user_id}{}", decision.to_uppercase(),
        if link.is_some() { " via=message" } else { "" });

    // Only the decision that won the race gets here: one decision, one event.
    crate::webhook_events::emit_po_event(pool, tenant, &format!("purchase_order.{decision}"), po_log_id,
        Some(user_id)).await;
    notify_requester(pool, tenant, &po, &last, decision, clean.as_deref(), user_id).await;

    let mut m = describe(pool, tenant, po_log_id, user_id).await?;
    m.insert("changed".into(), json!(true));
    m.insert("po_number".into(), json!(po.po_number));
    m.insert("amount".into(), json!(last.amount));
    m.insert("comment".into(), json!(clean));
    m.insert("channel".into(), json!(channel));

    let mut details = Map::new();
    details.insert("reference".into(), json!(format_po_number(po.po_number, po_log_id)));
    details.insert("value".into(), json!(last.amount));
    details.insert("decision_comment".into(), json!(clean));
    if let Some(c) = channel {
        details.insert("channel".into(), json!(c));
    }
    let event = if decision == "approved" { Event::ApprovalApproved } else { Event::ApprovalRejected };
    record_event_with_reason(pool, tenant, user_id, event, Some(po_log_id), details, None).await;
    Ok(ok(Value::Object(m)))
}

/// `_notify_requester`: the person who asked hears the decision, through the
/// outbox (the Python worker renders and sends it). One mail per approval
/// request, however many times a decision is repeated.
async fn notify_requester(pool: &PgPool, tenant_id: &str, po: &Po, last: &Latest, decision: &str,
    comment: Option<&str>, decider_id: &str)
{
    if last.requested_by == decider_id {
        return;
    }
    let requester: Result<Option<(Option<String>,)>, sqlx::Error> =
        sqlx::query_as("SELECT email FROM users WHERE id = $1 AND tenant_id = $2")
            .bind(&last.requested_by)
            .bind(tenant_id)
            .fetch_optional(pool)
            .await;
    let email = match requester {
        Ok(Some((Some(e),))) if !e.is_empty() => e,
        Ok(_) => return,
        Err(e) => {
            tracing::error!(error = %e, "[po-approval] requester lookup failed tenant={tenant_id} po={}", po.id);
            return;
        }
    };
    let mut params = json!({
        "po_log_id": po.id, "amount": last.amount, "approved": decision == "approved",
        "requester_id": last.requested_by, "decider_id": decider_id,
    });
    if let Some(c) = comment {
        params["comment"] = json!(c);
    }
    let key = format!("po_approval_decision:{}", last.id);
    let mut m = Message::new(tenant_id, Channel::Email, "po_approval_decision", &email, params);
    m.created_by = Some(decider_id);
    m.dedupe_key = Some(&key);
    outbox::enqueue(pool, m).await;
}
