//! Unit tests (pure) and database tests (`#[ignore]`, run with
//! `APPROVAL_LINKS_TEST_DATABASE_URL=postgres://... cargo test approval_links -- --ignored`)
//! for `routes/approval_links.rs`.
//!
//! The database tests seed their own tenant with plain SQL, call the same
//! functions the handlers call, and assert STATE with direct queries (the
//! testing mandate), not only the answer. They need a database the Python app
//! already migrated (`po_approval_links`, `decided_channel`).

use std::collections::HashMap;
use std::sync::Arc;

use super::*;
use crate::config::Settings;

// ── Pure ─────────────────────────────────────────────────────────────────────

#[test]
fn a_token_is_43_urlsafe_chars_from_256_random_bits_and_never_repeats() {
    let a = new_token().unwrap();
    let b = new_token().unwrap();
    assert_eq!(a.len(), 43);
    assert!(token_is_wellformed(&a) && token_is_wellformed(&b));
    assert_ne!(a, b);
}

#[test]
fn malformed_tokens_never_reach_the_table() {
    for bad in ["", "short", &"a".repeat(42), &"a".repeat(129), &format!("{}!", "a".repeat(43)),
                &format!("{} ", "a".repeat(43)), &format!("{}\u{e9}", "a".repeat(43)), "../../etc/passwd"] {
        assert!(!token_is_wellformed(bad), "{bad:?}");
    }
    assert!(token_is_wellformed(&"a".repeat(43)));
    assert!(token_is_wellformed(&format!("{}-_", "A".repeat(41))));
}

#[test]
fn the_hash_is_the_python_hash() {
    // python -c "import hashlib;print(hashlib.sha256(('a'*43).encode()).hexdigest())"
    assert_eq!(hash_token(&"a".repeat(43)), hex::encode(Sha256::digest("a".repeat(43).as_bytes())));
    assert_eq!(hash_token("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

#[test]
fn constant_time_equality_needs_equal_length_and_content() {
    assert!(ct_eq("abcd", "abcd"));
    assert!(!ct_eq("abcd", "abce"));
    assert!(!ct_eq("abcd", "abc"));
    assert!(ct_eq("", ""));
}

#[test]
fn the_constants_are_the_python_constants() {
    let py = include_str!("../../../../backend/inventory/po_approval_link_service.py");
    assert!(py.contains(&format!("LINK_TTL_HOURS = {LINK_TTL_HOURS}")), "LINK_TTL_HOURS differs from Python");
    let core = include_str!("../../../../backend/inventory/po_confirmation_core.py");
    assert!(core.contains("TOKEN_BYTES = 32") && core.contains("{43,128}"));
}

#[test]
fn the_message_kinds_python_renders_are_the_ones_rust_queues() {
    let py = include_str!("../../../../backend/notifications/outbox.py");
    assert!(py.contains(r#""po_approval_link""#) && py.contains(r#""decision_token""#));
    // what issue_core queues passes the registry's own parameter check
    assert_eq!(outbox::check_params(Channel::Whatsapp, "po_approval_link",
        &json!({"po_log_id": "p", "amount": 1.0, "decision_token": "t", "approver_id": "u"})), None);
    assert_eq!(outbox::check_params(Channel::Email, "po_approval_request",
        &json!({"po_log_id": "p", "amount": 1.0, "approver_id": "u", "requester_id": "r", "decision_token": "t"})), None);
}

#[test]
fn the_decision_body_is_strict() {
    assert_eq!(parse_decision(br#"{"decision":"approved"}"#).unwrap(), ("approved", None));
    assert_eq!(parse_decision(br#"{"decision":"rejected","comment":"too much"}"#).unwrap(),
        ("rejected", Some("too much".to_string())));
    assert_eq!(parse_decision(br#"{"decision":"approved","comment":null}"#).unwrap(), ("approved", None));
    for bad in [&br#"{"decision":"maybe"}"#[..], br#"{"decision":1}"#, br#"{}"#, br#"[]"#, b"not json", b"",
                br#"{"decision":"approved","comment":5}"#] {
        let e = parse_decision(bad).unwrap_err();
        assert_eq!(e.status.as_u16(), 422, "{}", String::from_utf8_lossy(bad));
        assert_eq!(e.code(), Some("approval_link_invalid_request"));
    }
    let long = format!(r#"{{"decision":"rejected","comment":"{}"}}"#, "x".repeat(501));
    assert_eq!(parse_decision(long.as_bytes()).unwrap_err().code(), Some("approval_link_invalid_request"));
    let ok_len = format!(r#"{{"decision":"rejected","comment":"{}"}}"#, "x".repeat(500));
    assert!(parse_decision(ok_len.as_bytes()).is_ok());
    let huge = vec![b' '; MAX_BODY_BYTES + 1];
    let e = parse_decision(&huge).unwrap_err();
    assert_eq!((e.status.as_u16(), e.code()), (413, Some("approval_link_body_too_large")));
}

#[test]
fn the_client_address_is_the_first_forwarded_one() {
    let mut h = HeaderMap::new();
    h.insert("x-forwarded-for", " 203.0.113.9 , 10.0.0.1".parse().unwrap());
    assert_eq!(client_address(&h, Some("10.1.1.1:5".parse().unwrap())), "203.0.113.9");
    assert_eq!(client_address(&HeaderMap::new(), Some("10.1.1.1:5".parse().unwrap())), "10.1.1.1");
    assert_eq!(client_address(&HeaderMap::new(), None), "unknown");
}

#[test]
fn every_bad_link_is_the_same_answer() {
    let e = not_found();
    assert_eq!(e.status.as_u16(), 404);
    assert_eq!(e.code(), Some("approval_link_not_found"));
    // the answer carries nothing that could tell the cases apart
    assert_eq!(e.body["error_params"], json!({}));
}

// ── Database ─────────────────────────────────────────────────────────────────

fn url() -> String {
    std::env::var("APPROVAL_LINKS_TEST_DATABASE_URL").expect("APPROVAL_LINKS_TEST_DATABASE_URL")
}

async fn state_with(extra: &[(&str, &str)]) -> AppState {
    let mut m = HashMap::new();
    m.insert("SECRET_KEY".to_string(), "test-secret".to_string());
    m.insert("DATABASE_URL".to_string(), url());
    m.insert("FRONTEND_URL".to_string(), "http://localhost:5987".to_string());
    m.insert("APPROVAL_LINKS_ENABLED".to_string(), "true".to_string());
    for (k, v) in extra {
        m.insert(k.to_string(), v.to_string());
    }
    let settings = Settings::from_map(m).unwrap();
    let pool = sqlx::postgres::PgPoolOptions::new().max_connections(8).connect(&url()).await.unwrap();
    AppState { settings: Arc::new(settings), pool }
}

fn rid(prefix: &str) -> String {
    format!("{prefix}_{}", &uuid::Uuid::new_v4().simple().to_string()[..10])
}

struct World {
    state: AppState,
    tenant: String,
    requester: CurrentUser,
    a1: String,
    a1_email: String,
    a2: String,
    a2_email: String,
    po: String,
    approval: String,
    amount: f64,
}

async fn add_user(pool: &PgPool, tenant: &str, role: &str, approver: bool, whatsapp: Option<(&str, bool)>) -> (String, String) {
    let id = rid("usr");
    let email = format!("{id}@example.com");
    sqlx::query(
        "INSERT INTO users (id, tenant_id, email, hashed_password, role, status, can_approve_po, email_verified, full_name, \
                            whatsapp_number, whatsapp_verified_at) \
         VALUES ($1, $2, $3, 'x', $4, 'active', $5, TRUE, $6, $7, CASE WHEN $8 THEN NOW() END)",
    )
    .bind(&id)
    .bind(tenant)
    .bind(&email)
    .bind(role)
    .bind(approver)
    .bind(format!("The {role}"))
    .bind(whatsapp.map(|(n, _)| n))
    .bind(whatsapp.map(|(_, v)| v).unwrap_or(false))
    .execute(pool)
    .await
    .unwrap();
    (id, email)
}

/// An order of 6,000 (20 x 300, a unit cost nobody should ever see on the page)
/// with an open approval request from the requester.
async fn add_order(pool: &PgPool, tenant: &str, requester: &str, destination: Option<&str>) -> (String, String) {
    let po: (String,) = sqlx::query_as(
        "INSERT INTO inventory_po_log (tenant_id, sku_count, total_units, po_number, destination_warehouse, approval_status) \
         VALUES ($1, 1, 20, (SELECT COALESCE(MAX(po_number), 0) + 1 FROM inventory_po_log WHERE tenant_id = $1), $2, 'pending_approval') RETURNING id",
    )
    .bind(tenant)
    .bind(destination)
    .fetch_one(pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, display_name, supplier, recommended_qty, final_qty, \
                                         unit_cost, status, warehouse) \
         VALUES ($1, $2, 'SKU-LEAK', 'Widget', 'Acme', 20, 20, 300.0, 'approved', 'principal')",
    )
    .bind(&po.0)
    .bind(tenant)
    .execute(pool)
    .await
    .unwrap();
    let ap: (String,) = sqlx::query_as(
        "INSERT INTO po_approvals (tenant_id, po_log_id, status, amount, requested_by, request_note) \
         VALUES ($1, $2, 'requested', 6000, $3, 'urgent') RETURNING id",
    )
    .bind(tenant)
    .bind(&po.0)
    .bind(requester)
    .fetch_one(pool)
    .await
    .unwrap();
    (po.0, ap.0)
}

async fn world_with(state: AppState) -> World {
    let pool = &state.pool;
    let tenant = rid("ten");
    sqlx::query("INSERT INTO tenants (id, name, slug) VALUES ($1, 'Test Co', $1)").bind(&tenant).execute(pool).await.unwrap();
    let (requester, _) = add_user(pool, &tenant, "analyst", false, None).await;
    let (a1, a1_email) = add_user(pool, &tenant, "admin", true, None).await;
    let (a2, a2_email) = add_user(pool, &tenant, "analyst", true, None).await;
    sqlx::query("INSERT INTO po_approval_rules (tenant_id, threshold, created_by) VALUES ($1, 5000, 'test')")
        .bind(&tenant).execute(pool).await.unwrap();
    let (po, approval) = add_order(pool, &tenant, &requester, None).await;
    World {
        requester: CurrentUser { user_id: requester, tenant_id: tenant.clone(), role: "analyst".into(),
            email_verified: true, api_key_id: None },
        state, tenant, a1, a1_email, a2, a2_email, po, approval, amount: 6000.0,
    }
}

async fn world() -> World {
    world_with(state_with(&[]).await).await
}

impl World {
    async fn issue(&self) -> Value {
        issue_core(&self.state, &self.requester, &self.po).await.unwrap()
    }
    /// The raw token queued for this recipient, read from the (pending) outbox row.
    async fn token_for(&self, recipient: &str) -> String {
        let row: (Value,) = sqlx::query_as(
            "SELECT params FROM outbound_messages WHERE tenant_id = $1 AND recipient = $2 AND status = 'pending' \
              ORDER BY created_at DESC LIMIT 1",
        )
        .bind(&self.tenant)
        .bind(recipient)
        .fetch_one(&self.state.pool)
        .await
        .unwrap();
        row.0["decision_token"].as_str().unwrap().to_string()
    }
    async fn count(&self, sql: &str) -> i64 {
        let (n,): (i64,) = sqlx::query_as(sql).bind(&self.tenant).fetch_one(&self.state.pool).await.unwrap();
        n
    }
    async fn approval_row(&self) -> (String, Option<String>, Option<String>) {
        sqlx::query_as("SELECT status, decided_by, decided_channel FROM po_approvals WHERE id = $1")
            .bind(&self.approval).fetch_one(&self.state.pool).await.unwrap()
    }
    async fn snapshot(&self) -> (i64, i64, i64, i64, i64) {
        (
            self.count("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = $1").await,
            self.count("SELECT COUNT(*) FROM outbound_messages WHERE tenant_id = $1").await,
            self.count("SELECT COUNT(*) FROM po_approval_links WHERE tenant_id = $1 AND (used_at IS NOT NULL OR revoked_at IS NOT NULL)").await,
            self.count("SELECT COUNT(*) FROM po_approvals WHERE tenant_id = $1 AND status <> 'requested'").await,
            self.count("SELECT COUNT(*) FROM webhook_deliveries WHERE tenant_id = $1").await,
        )
    }
    async fn cleanup(self) {
        sqlx::query("DELETE FROM tenants WHERE id = $1").bind(&self.tenant).execute(&self.state.pool).await.unwrap();
    }
}

fn code(r: Result<Value, ApiError>) -> String {
    match r {
        Ok(v) => format!("OK {v}"),
        Err(e) => format!("{}", e.code().unwrap_or("?")),
    }
}

fn approve() -> &'static [u8] {
    br#"{"decision":"approved"}"#
}

// -- issuing ------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn issuing_makes_one_hashed_bound_expiring_link_per_approver_and_never_for_the_requester() {
    let w = world().await;
    let out = w.issue().await;
    assert_eq!(out["approvers"], 2);
    assert_eq!(out["links"], 2);
    assert_eq!(out["queued"], json!({"email": 2, "whatsapp": 0}));
    let rows: Vec<(String, String, String, String, f64, String, bool, bool)> = sqlx::query_as(
        "SELECT approver_id, approval_id, po_log_id, scope, (EXTRACT(EPOCH FROM (expires_at - issued_at)) / 3600)::float8, \
                token_hash, used_at IS NULL, revoked_at IS NULL FROM po_approval_links WHERE tenant_id = $1 ORDER BY approver_id",
    )
    .bind(&w.tenant)
    .fetch_all(&w.state.pool)
    .await
    .unwrap();
    assert_eq!(rows.len(), 2);
    let mut want = vec![w.a1.clone(), w.a2.clone()];
    want.sort();
    assert_eq!(rows.iter().map(|r| r.0.clone()).collect::<Vec<_>>(), want);
    for r in &rows {
        assert_eq!((r.1.as_str(), r.2.as_str(), r.3.as_str()), (w.approval.as_str(), w.po.as_str(), "decide"));
        assert!((r.4 - LINK_TTL_HOURS as f64).abs() < 0.01);
        assert_eq!(r.5.len(), 64);
        assert!(r.6 && r.7);
    }
    // the stored value is the hash of the token in the queued message, never the token
    for email in [&w.a1_email, &w.a2_email] {
        let token = w.token_for(email).await;
        assert!(token_is_wellformed(&token) && token.len() == 43);
        let (stored,): (String,) = sqlx::query_as("SELECT token_hash FROM po_approval_links l JOIN users u ON u.id = l.approver_id WHERE u.email = $1")
            .bind(email).fetch_one(&w.state.pool).await.unwrap();
        assert_eq!(stored, hash_token(&token));
        let (leaked,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM po_approval_links WHERE row(po_approval_links.*)::text LIKE $1")
            .bind(format!("%{token}%")).fetch_one(&w.state.pool).await.unwrap();
        assert_eq!(leaked, 0, "the token is in the links table");
    }
    // the outbox rows are the registered email kind, params checked by the registry
    let kinds: Vec<(String, String)> = sqlx::query_as(
        "SELECT channel, kind FROM outbound_messages WHERE tenant_id = $1").bind(&w.tenant).fetch_all(&w.state.pool).await.unwrap();
    assert!(kinds.iter().all(|k| *k == ("email".to_string(), "po_approval_request".to_string())));
    // the trail says how many links went out
    let (count,): (i64,) = sqlx::query_as(
        "SELECT (context->>'count')::bigint FROM activity_logs WHERE tenant_id = $1 AND action = 'purchase.approval_links_sent'")
        .bind(&w.tenant).fetch_one(&w.state.pool).await.unwrap();
    assert_eq!(count, 2);
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn issuing_again_rotates_the_token_and_the_old_link_dies() {
    let w = world().await;
    w.issue().await;
    let old = w.token_for(&w.a1_email).await;
    assert!(view_with_token(&w.state, &old).await.is_ok());
    w.issue().await;
    let new = w.token_for(&w.a1_email).await;
    assert_ne!(old, new);
    assert_eq!(code(view_with_token(&w.state, &old).await), "approval_link_not_found");
    assert!(view_with_token(&w.state, &new).await.is_ok());
    assert_eq!(w.count("SELECT COUNT(*) FROM po_approval_links WHERE tenant_id = $1").await, 2, "one row per approver and channel");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn the_switch_off_issues_nothing_and_answers_as_if_no_link_existed() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    let off = state_with(&[("APPROVAL_LINKS_ENABLED", "false")]).await;
    assert_eq!(code(issue_core(&off, &w.requester, &w.po).await), "approval_links_disabled");
    assert_eq!(code(view_with_token(&off, &token).await), "approval_link_not_found");
    assert_eq!(code(decide_with_token(&off, &token, approve()).await), "approval_link_not_found");
    assert_eq!(w.approval_row().await.0, "requested");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn issuing_needs_an_open_request() {
    let w = world().await;
    sqlx::query("UPDATE po_approvals SET status = 'approved', decided_by = $2, decided_at = NOW() WHERE id = $1")
        .bind(&w.approval).bind(&w.a1).execute(&w.state.pool).await.unwrap();
    assert_eq!(code(issue_core(&w.state, &w.requester, &w.po).await), "po_approval_not_requested");
    assert_eq!(w.count("SELECT COUNT(*) FROM po_approval_links WHERE tenant_id = $1").await, 0);
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn whatsapp_only_for_a_verified_number_on_a_plan_with_the_bot_and_only_the_link() {
    let twilio = [("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"), ("TWILIO_WHATSAPP_FROM", "+15550001111")];
    let w = world_with(state_with(&twilio).await).await;
    let pool = &w.state.pool;
    let (verified, _) = add_user(pool, &w.tenant, "admin", true, Some(("+50688887777", true))).await;
    let (_unverified, _) = add_user(pool, &w.tenant, "admin", true, Some(("+50688886666", false))).await;
    // free tier: no bot, no WhatsApp link (the existing gate; this adds none)
    let out = w.issue().await;
    assert_eq!(out["queued"]["whatsapp"], 0);
    sqlx::query("UPDATE tenants SET tier = 'paid' WHERE id = $1").bind(&w.tenant).execute(pool).await.unwrap();
    let out = w.issue().await;
    assert_eq!(out["queued"]["whatsapp"], 1, "{out}");
    let rows: Vec<(String, String, Value)> = sqlx::query_as(
        "SELECT recipient, kind, params FROM outbound_messages WHERE tenant_id = $1 AND channel = 'whatsapp'")
        .bind(&w.tenant).fetch_all(pool).await.unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!((rows[0].0.as_str(), rows[0].1.as_str()), ("+50688887777", "po_approval_link"));
    let mut keys: Vec<&str> = rows[0].2.as_object().unwrap().keys().map(String::as_str).collect();
    keys.sort();
    assert_eq!(keys, ["amount", "approver_id", "decision_token", "po_log_id"], "only the link message, no free text");
    assert_eq!(rows[0].2["approver_id"], json!(verified));
    // no channel configured: nothing, even on the paid plan
    let bare = state_with(&[]).await;
    sqlx::query("UPDATE po_approval_links SET used_at = NULL").execute(pool).await.unwrap();
    let out = issue_core(&bare, &w.requester, &w.po).await.unwrap();
    assert_eq!(out["queued"]["whatsapp"], 0);
    w.cleanup().await;
}

// -- the page -------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn the_page_shows_the_order_summary_and_no_cost_data_beyond_the_total() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    let v = view_with_token(&w.state, &token).await.unwrap();
    assert_eq!(v["amount"], 6000.0);
    assert_eq!(v["reference"], "OC-000001");
    assert_eq!(v["suppliers"], json!(["Acme"]));
    assert_eq!(v["lines"][0], json!({"sku": "SKU-LEAK", "name": "Widget", "quantity": 20.0, "supplier": "Acme"}));
    assert_eq!(v["note"], "urgent");
    assert_eq!((v["can_approve"].as_bool(), v["can_reject"].as_bool()), (Some(true), Some(true)));
    // the whitelist, exactly: a new column cannot appear here by accident
    let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(String::as_str).collect();
    keys.sort();
    assert_eq!(keys, ["amount", "approver_name", "buyer", "can_approve", "can_reject", "comment_max", "currency",
        "expires_at", "line_count", "lines", "note", "reference", "requested_at", "requested_by_name", "suppliers",
        "warehouse"]);
    let line_keys: Vec<&str> = v["lines"][0].as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(line_keys, ["sku", "name", "quantity", "supplier"]);
    // leak test: the unit cost (300), a line value (6000 is the total only), ids and the token hash
    let text = v.to_string();
    for forbidden in ["unit_cost", "cost", "margin", "price", "stock", "300.0", "token", "hash", &w.po, &w.approval, &w.a1, &w.tenant] {
        assert!(!text.contains(forbidden), "the page data leaks {forbidden:?}: {text}");
    }
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn a_get_changes_nothing_however_often_a_scanner_prefetches_it() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    let before = w.snapshot().await;
    let links_before: Vec<(String, Option<String>, bool, bool)> = sqlx::query_as(
        "SELECT token_hash, used_decision, used_at IS NULL, revoked_at IS NULL FROM po_approval_links WHERE tenant_id = $1 ORDER BY id")
        .bind(&w.tenant).fetch_all(&w.state.pool).await.unwrap();
    for _ in 0..5 {
        assert!(view_with_token(&w.state, &token).await.is_ok());
    }
    assert_eq!(w.snapshot().await, before);
    let links_after: Vec<(String, Option<String>, bool, bool)> = sqlx::query_as(
        "SELECT token_hash, used_decision, used_at IS NULL, revoked_at IS NULL FROM po_approval_links WHERE tenant_id = $1 ORDER BY id")
        .bind(&w.tenant).fetch_all(&w.state.pool).await.unwrap();
    assert_eq!(links_after, links_before);
    let (status, decided_by, channel) = w.approval_row().await;
    assert_eq!((status.as_str(), decided_by, channel), ("requested", None, None));
    let (st,): (Option<String>,) = sqlx::query_as("SELECT approval_status FROM inventory_po_log WHERE id = $1")
        .bind(&w.po).fetch_one(&w.state.pool).await.unwrap();
    assert_eq!(st.as_deref(), Some("pending_approval"));
    // and the link still works afterwards
    assert!(decide_with_token(&w.state, &token, approve()).await.is_ok());
    w.cleanup().await;
}

// -- deciding -------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn deciding_through_the_link_runs_the_in_app_path_and_leaves_the_audit_trail() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    let out = decide_with_token(&w.state, &token, br#"{"decision":"approved","comment":"ok by me"}"#).await.unwrap();
    assert_eq!(out, json!({"decided": true, "decision": "approved", "reference": "OC-000001"}));
    let (status, decided_by, channel) = w.approval_row().await;
    assert_eq!((status.as_str(), decided_by.as_deref(), channel.as_deref()), ("approved", Some(w.a1.as_str()), Some("message")));
    let (st, amount): (Option<String>, Option<f64>) =
        sqlx::query_as("SELECT approval_status, approved_amount FROM inventory_po_log WHERE id = $1")
            .bind(&w.po).fetch_one(&w.state.pool).await.unwrap();
    assert_eq!((st.as_deref(), amount), (Some("approved"), Some(w.amount)));
    // the link is spent; the other approver's link died with the decision
    let links: Vec<(String, bool, Option<String>, Option<String>)> = sqlx::query_as(
        "SELECT approver_id, used_at IS NOT NULL, used_decision, revoked_reason FROM po_approval_links WHERE tenant_id = $1 ORDER BY approver_id")
        .bind(&w.tenant).fetch_all(&w.state.pool).await.unwrap();
    for (approver, used, decision, reason) in links {
        if approver == w.a1 {
            assert_eq!((used, decision.as_deref(), reason), (true, Some("approved"), None));
        } else {
            assert_eq!((used, decision, reason.as_deref()), (false, None, Some("decided")));
        }
    }
    // the audit trail: the approver is the actor, channel=message
    let (actor, ctx): (String, Value) = sqlx::query_as(
        "SELECT user_id, context FROM activity_logs WHERE tenant_id = $1 AND action = 'purchase.approval_approved' AND resource = $2")
        .bind(&w.tenant).bind(&w.po).fetch_one(&w.state.pool).await.unwrap();
    assert_eq!(actor, w.a1);
    assert_eq!((ctx["channel"].as_str(), ctx["reference"].as_str(), ctx["decision_comment"].as_str()),
        (Some("message"), Some("OC-000001"), Some("ok by me")));
    assert_eq!(ctx["value"], 6000.0);
    // the requester hears about it, once, like for an in-app decision
    assert_eq!(w.count("SELECT COUNT(*) FROM outbound_messages WHERE tenant_id = $1 AND kind = 'po_approval_decision'").await, 1);
    assert_eq!(w.count("SELECT COUNT(*) FROM webhook_deliveries WHERE tenant_id = $1").await, 0, "no webhook subscribed");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn a_rejection_through_the_link_needs_its_reason_and_the_link_survives_the_refusal() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a2_email).await;
    assert_eq!(code(decide_with_token(&w.state, &token, br#"{"decision":"rejected"}"#).await), "po_approval_reason_required");
    assert_eq!(code(decide_with_token(&w.state, &token, br#"{"decision":"rejected","comment":"no"}"#).await), "po_approval_reason_required");
    assert_eq!(w.approval_row().await.0, "requested");
    assert_eq!(w.count("SELECT COUNT(*) FROM po_approval_links WHERE tenant_id = $1 AND used_at IS NOT NULL").await, 0);
    let out = decide_with_token(&w.state, &token, br#"{"decision":"rejected","comment":"over budget"}"#).await.unwrap();
    assert_eq!(out["decision"], "rejected");
    let (status, by, channel) = w.approval_row().await;
    assert_eq!((status.as_str(), by.as_deref(), channel.as_deref()), ("rejected", Some(w.a2.as_str()), Some("message")));
    let (st, amount): (Option<String>, Option<f64>) =
        sqlx::query_as("SELECT approval_status, approved_amount FROM inventory_po_log WHERE id = $1")
            .bind(&w.po).fetch_one(&w.state.pool).await.unwrap();
    assert_eq!((st.as_deref(), amount), (Some("rejected"), None));
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn replaying_a_used_link_is_the_neutral_answer_and_changes_nothing() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    assert!(decide_with_token(&w.state, &token, approve()).await.is_ok());
    let before = w.snapshot().await;
    // the same decision, the opposite one, and a view: all the same neutral answer
    for body in [approve(), br#"{"decision":"rejected","comment":"changed my mind"}"#] {
        assert_eq!(code(decide_with_token(&w.state, &token, body).await), "approval_link_not_found");
    }
    assert_eq!(code(view_with_token(&w.state, &token).await), "approval_link_not_found");
    assert_eq!(w.snapshot().await, before);
    assert_eq!(w.approval_row().await.0, "approved");
    w.cleanup().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn two_simultaneous_uses_of_one_link_decide_exactly_once() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    let (s1, s2) = (w.state.clone(), w.state.clone());
    let (t1, t2) = (token.clone(), token.clone());
    let a = tokio::spawn(async move { decide_with_token(&s1, &t1, approve()).await });
    let b = tokio::spawn(async move { decide_with_token(&s2, &t2, approve()).await });
    let (a, b) = (a.await.unwrap(), b.await.unwrap());
    let oks = [&a, &b].iter().filter(|r| r.is_ok()).count();
    assert_eq!(oks, 1, "{a:?} / {b:?}");
    let loser = if a.is_err() { a } else { b };
    assert_eq!(code(loser), "approval_link_not_found");
    assert_eq!(w.count("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = $1 AND action = 'purchase.approval_approved'").await, 1);
    assert_eq!(w.count("SELECT COUNT(*) FROM outbound_messages WHERE tenant_id = $1 AND kind = 'po_approval_decision'").await, 1);
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn an_expired_link_is_the_neutral_answer_and_decides_nothing() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    sqlx::query("UPDATE po_approval_links SET expires_at = NOW() - INTERVAL '1 second' WHERE tenant_id = $1")
        .bind(&w.tenant).execute(&w.state.pool).await.unwrap();
    assert_eq!(code(view_with_token(&w.state, &token).await), "approval_link_not_found");
    assert_eq!(code(decide_with_token(&w.state, &token, approve()).await), "approval_link_not_found");
    assert_eq!(w.approval_row().await.0, "requested");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn a_revoked_link_is_the_neutral_answer_and_revoking_is_idempotent_and_audited() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    let out = revoke_core(&w.state, &w.requester, &w.po).await.unwrap();
    assert_eq!(out["revoked"], 2);
    assert_eq!(revoke_core(&w.state, &w.requester, &w.po).await.unwrap()["revoked"], 0);
    assert_eq!(code(view_with_token(&w.state, &token).await), "approval_link_not_found");
    assert_eq!(code(decide_with_token(&w.state, &token, approve()).await), "approval_link_not_found");
    assert_eq!(w.approval_row().await.0, "requested");
    let rows: Vec<(Option<String>, Option<String>)> = sqlx::query_as(
        "SELECT revoked_by, revoked_reason FROM po_approval_links WHERE tenant_id = $1").bind(&w.tenant)
        .fetch_all(&w.state.pool).await.unwrap();
    assert!(rows.iter().all(|r| r.0.as_deref() == Some(w.requester.user_id.as_str()) && r.1.as_deref() == Some("revoked")));
    assert_eq!(w.count("SELECT COUNT(*) FROM activity_logs WHERE tenant_id = $1 AND action = 'purchase.approval_links_revoked'").await, 1);
    // re-sending is a deliberate act: it issues fresh links
    w.issue().await;
    let again = w.token_for(&w.a1_email).await;
    assert!(view_with_token(&w.state, &again).await.is_ok());
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn an_in_app_decision_kills_the_links() {
    let w = world().await;
    w.issue().await;
    let t1 = w.token_for(&w.a1_email).await;
    let t2 = w.token_for(&w.a2_email).await;
    let _ = po_approvals::decide_core(&w.state, &w.tenant, &w.a2, &w.po, "approved", None, None).await.unwrap();
    assert_eq!(w.approval_row().await.2, None, "decided in the app: no channel");
    assert_eq!(code(view_with_token(&w.state, &t1).await), "approval_link_not_found");
    assert_eq!(code(decide_with_token(&w.state, &t2, approve()).await), "approval_link_not_found");
    w.cleanup().await;
}

// -- who the link may act as, and on what -----------------------------------------

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn a_link_decides_as_its_own_approver_and_only_on_its_own_order() {
    let w = world().await;
    let (po2, approval2) = add_order(&w.state.pool, &w.tenant, &w.requester.user_id, None).await;
    // approval2's order needs its own links: issue for both orders
    issue_core(&w.state, &w.requester, &po2).await.unwrap();
    w.issue().await;
    let rows: Vec<(String, String, String)> = sqlx::query_as(
        "SELECT p.email, l.po_log_id, l.approval_id FROM po_approval_links l JOIN users p ON p.id = l.approver_id WHERE l.tenant_id = $1")
        .bind(&w.tenant).fetch_all(&w.state.pool).await.unwrap();
    assert_eq!(rows.len(), 4);
    // a2's token for the FIRST order decides the first order, as a2, and not the second
    let first = sqlx::query_as::<_, (Value,)>(
        "SELECT params FROM outbound_messages WHERE tenant_id = $1 AND recipient = $2 AND params->>'po_log_id' = $3")
        .bind(&w.tenant).bind(&w.a2_email).bind(&w.po).fetch_one(&w.state.pool).await.unwrap().0;
    let token = first["decision_token"].as_str().unwrap();
    decide_with_token(&w.state, token, approve()).await.unwrap();
    let (status, by, _) = w.approval_row().await;
    assert_eq!((status.as_str(), by.as_deref()), ("approved", Some(w.a2.as_str())));
    let (other,): (String,) = sqlx::query_as("SELECT status FROM po_approvals WHERE id = $1").bind(&approval2)
        .fetch_one(&w.state.pool).await.unwrap();
    assert_eq!(other, "requested", "the link for another order decided nothing there");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn an_approver_who_lost_the_flag_or_was_deactivated_can_no_longer_use_the_link() {
    let w = world().await;
    w.issue().await;
    let t1 = w.token_for(&w.a1_email).await;
    let t2 = w.token_for(&w.a2_email).await;
    sqlx::query("UPDATE users SET can_approve_po = FALSE WHERE id = $1").bind(&w.a1).execute(&w.state.pool).await.unwrap();
    sqlx::query("UPDATE users SET status = 'inactive' WHERE id = $1").bind(&w.a2).execute(&w.state.pool).await.unwrap();
    for t in [&t1, &t2] {
        assert_eq!(code(view_with_token(&w.state, t).await), "approval_link_not_found");
        assert_eq!(code(decide_with_token(&w.state, t, approve()).await), "approval_link_not_found");
    }
    assert_eq!(w.approval_row().await.0, "requested");
    assert_eq!(w.count("SELECT COUNT(*) FROM po_approval_links WHERE tenant_id = $1 AND used_at IS NOT NULL").await, 0);
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn a_demoted_approver_cannot_decide_even_with_a_valid_link() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    sqlx::query("UPDATE users SET role = 'viewer' WHERE id = $1").bind(&w.a1).execute(&w.state.pool).await.unwrap();
    assert_eq!(code(decide_with_token(&w.state, &token, approve()).await), "approval_link_not_found");
    assert_eq!(w.approval_row().await.0, "requested");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn self_approval_is_refused_through_a_link_as_it_is_in_the_app_but_rejecting_is_allowed() {
    let w = world().await;
    // the requester is ALSO an approver, and the rule has no self-approval limit
    sqlx::query("UPDATE users SET can_approve_po = TRUE WHERE id = $1").bind(&w.requester.user_id).execute(&w.state.pool).await.unwrap();
    // a link for the requester cannot come from issue (they are never a recipient): plant one
    let token = new_token().unwrap();
    sqlx::query("INSERT INTO po_approval_links (tenant_id, po_log_id, approval_id, approver_id, token_hash, channel, expires_at, created_by) \
                 VALUES ($1, $2, $3, $4, $5, 'email', NOW() + INTERVAL '1 hour', 'test')")
        .bind(&w.tenant).bind(&w.po).bind(&w.approval).bind(&w.requester.user_id).bind(hash_token(&token))
        .execute(&w.state.pool).await.unwrap();
    let v = view_with_token(&w.state, &token).await.unwrap();
    assert_eq!(v["can_approve"], false, "the page does not offer what the decision would refuse");
    assert_eq!(code(decide_with_token(&w.state, &token, approve()).await), "po_approval_self_approval");
    assert_eq!(w.approval_row().await.0, "requested");
    assert_eq!(w.count("SELECT COUNT(*) FROM po_approval_links WHERE tenant_id = $1 AND used_at IS NOT NULL").await, 0, "a refusal does not burn the link");
    // below the second threshold the same person may approve their own order
    sqlx::query("UPDATE po_approval_rules SET self_approve_below = 9000 WHERE tenant_id = $1").bind(&w.tenant).execute(&w.state.pool).await.unwrap();
    assert_eq!(view_with_token(&w.state, &token).await.unwrap()["can_approve"], true);
    assert!(decide_with_token(&w.state, &token, approve()).await.is_ok());
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn a_warehouse_scoped_approver_cannot_see_or_decide_an_order_outside_their_scope() {
    let w = world().await;
    let pool = &w.state.pool;
    let (wh_in, wh_out) = (rid("wh"), rid("wh"));
    for (id, name) in [(&wh_in, "Norte"), (&wh_out, "Sur")] {
        sqlx::query("INSERT INTO warehouses (id, tenant_id, name) VALUES ($1, $2, $3)").bind(id).bind(&w.tenant).bind(name)
            .execute(pool).await.unwrap();
    }
    let (po, _approval) = add_order(pool, &w.tenant, &w.requester.user_id, Some("Sur")).await;
    issue_core(&w.state, &w.requester, &po).await.unwrap();
    let row: (Value,) = sqlx::query_as("SELECT params FROM outbound_messages WHERE tenant_id = $1 AND recipient = $2 AND params->>'po_log_id' = $3")
        .bind(&w.tenant).bind(&w.a1_email).bind(&po).fetch_one(pool).await.unwrap();
    let token = row.0["decision_token"].as_str().unwrap().to_string();
    assert!(view_with_token(&w.state, &token).await.is_ok(), "unscoped: allowed");
    sqlx::query("UPDATE users SET warehouse_scope = $2 WHERE id = $1").bind(&w.a1).bind(json!([wh_in])).execute(pool).await.unwrap();
    assert_eq!(code(view_with_token(&w.state, &token).await), "approval_link_not_found");
    assert_eq!(code(decide_with_token(&w.state, &token, approve()).await), "approval_link_not_found");
    sqlx::query("UPDATE users SET warehouse_scope = $2 WHERE id = $1").bind(&w.a1).bind(json!([wh_in, wh_out])).execute(pool).await.unwrap();
    assert!(decide_with_token(&w.state, &token, approve()).await.is_ok());
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn a_cancelled_order_is_not_decided_from_a_message() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    sqlx::query("UPDATE inventory_po_log SET cancelled_at = NOW() WHERE id = $1").bind(&w.po).execute(&w.state.pool).await.unwrap();
    assert_eq!(code(view_with_token(&w.state, &token).await), "approval_link_not_found");
    assert_eq!(code(decide_with_token(&w.state, &token, approve()).await), "approval_link_not_found");
    assert_eq!(w.approval_row().await.0, "requested");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn links_do_not_cross_tenants() {
    let w = world().await;
    let other = world().await;
    w.issue().await;
    other.issue().await;
    let mine = w.token_for(&w.a1_email).await;
    let theirs = other.token_for(&other.a1_email).await;
    // each token acts on its own tenant's request only
    decide_with_token(&w.state, &mine, approve()).await.unwrap();
    assert_eq!(w.approval_row().await.0, "approved");
    assert_eq!(other.approval_row().await.0, "requested");
    let (decided_elsewhere,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM po_approvals WHERE tenant_id = $1 AND status <> 'requested'")
        .bind(&other.tenant).fetch_one(&w.state.pool).await.unwrap();
    assert_eq!(decided_elsewhere, 0);
    // a link row that mixes tenants (a corrupt or forged row) resolves to nothing:
    // the approver, the order and the request must all be the link's tenant's
    let forged = new_token().unwrap();
    sqlx::query("INSERT INTO po_approval_links (tenant_id, po_log_id, approval_id, approver_id, token_hash, channel, expires_at, created_by) \
                 VALUES ($1, $2, $3, $4, $5, 'whatsapp', NOW() + INTERVAL '1 hour', 'test')")
        .bind(&w.tenant).bind(&other.po).bind(&other.approval).bind(&other.a2).bind(hash_token(&forged))
        .execute(&w.state.pool).await.unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(code(view_with_token(&w.state, &forged).await), "approval_link_not_found");
    assert_eq!(code(decide_with_token(&w.state, &forged, approve()).await), "approval_link_not_found");
    assert_eq!(other.approval_row().await.0, "requested");
    // and the other tenant's own token still works
    assert!(view_with_token(&other.state, &theirs).await.is_ok());
    w.cleanup().await;
    other.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn an_expired_trial_tenant_cannot_decide_from_a_message() {
    let w = world().await;
    w.issue().await;
    let token = w.token_for(&w.a1_email).await;
    sqlx::query("UPDATE tenants SET trial_ends_at = NOW() - INTERVAL '1 day' WHERE id = $1").bind(&w.tenant).execute(&w.state.pool).await.unwrap();
    assert_eq!(code(decide_with_token(&w.state, &token, approve()).await), "approval_link_not_found");
    assert_eq!(w.approval_row().await.0, "requested");
    w.cleanup().await;
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn unknown_and_malformed_tokens_are_the_same_neutral_answer() {
    let w = world().await;
    w.issue().await;
    for t in [new_token().unwrap(), "short".to_string(), "a".repeat(200), "../etc".to_string()] {
        assert_eq!(code(view_with_token(&w.state, &t).await), "approval_link_not_found");
        assert_eq!(code(decide_with_token(&w.state, &t, approve()).await), "approval_link_not_found");
    }
    assert_eq!(w.snapshot().await.3, 0);
    w.cleanup().await;
}

// -- rate limits ----------------------------------------------------------------

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn the_rate_limits_count_per_link_and_per_address_even_for_unknown_links() {
    let state = state_with(&[("TESTING_MODE", "false")]).await;
    let token = new_token().unwrap();                          // not in the table: counted all the same
    let mut h = HeaderMap::new();
    h.insert("x-forwarded-for", format!("198.51.100.{}", rand_octet()).parse().unwrap());
    for i in 0..WRITE_PER_LINK.0 {
        assert!(limit(&state, &h, None, &token, true).await.is_ok(), "attempt {i}");
    }
    let e = limit(&state, &h, None, &token, true).await.unwrap_err();
    assert_eq!((e.status.as_u16(), e.code()), (429, Some("too_many_attempts")));
    // a different link from the same address is still inside the address budget
    let other = new_token().unwrap();
    assert!(limit(&state, &h, None, &other, true).await.is_ok());
    // the rate rows hold a digest, never the token
    let (leak,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM auth_rate_events WHERE key LIKE $1")
        .bind(format!("%{token}%")).fetch_one(&state.pool).await.unwrap();
    assert_eq!(leak, 0);
    // per address: one address, many different links
    let mut h2 = HeaderMap::new();
    h2.insert("x-forwarded-for", format!("198.51.100.{}", rand_octet()).parse().unwrap());
    for i in 0..WRITE_PER_ADDRESS.0 {
        assert!(limit(&state, &h2, None, &new_token().unwrap(), true).await.is_ok(), "attempt {i}");
    }
    assert_eq!(limit(&state, &h2, None, &new_token().unwrap(), true).await.unwrap_err().code(), Some("too_many_attempts"));
}

fn rand_octet() -> u8 {
    (uuid::Uuid::new_v4().as_u128() % 250) as u8 + 1
}

#[tokio::test]
#[ignore = "needs APPROVAL_LINKS_TEST_DATABASE_URL"]
async fn re_sending_the_links_of_one_order_is_rate_limited() {
    let w = world_with(state_with(&[("TESTING_MODE", "false")]).await).await;
    for _ in 0..ISSUE_PER_ORDER.0 {
        assert!(issue_core(&w.state, &w.requester, &w.po).await.is_ok());
    }
    assert_eq!(code(issue_core(&w.state, &w.requester, &w.po).await), "too_many_attempts");
    w.cleanup().await;
}
