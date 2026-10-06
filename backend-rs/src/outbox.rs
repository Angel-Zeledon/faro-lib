//! The WRITE half of the message outbox (`backend/notifications/outbox.py`).
//!
//! The Rust API never talks to Resend, SMTP or Twilio. A route that wants an
//! email or a WhatsApp message INSERTS a row into `outbound_messages`; the
//! Python `outbox-drain` worker loop renders it with the existing senders
//! (one sender implementation, one place that refuses the made-up
//! `@stockai.demo` trial addresses) and records the outcome.
//!
//! A row names WHAT to send (an English `kind` from the registry below) and
//! the data it needs, never the text: the Spanish copy stays in the Python
//! locale catalog. The registry here is a copy of `outbox.KINDS`, and a unit
//! test reads the Python source and fails when the two differ, so a kind added
//! on one side only cannot ship.
//!
//! Like Python's `enqueue`, this never fails the action that caused it: it
//! returns `None` and logs when the row cannot be written. A route that must
//! answer "sent: true" cannot use an outbox. (That is why
//! `POST /po/{id}/approval/request`, whose answer says how many mails left,
//! stays on Python: see docs/rust-migration.md.)

#![allow(dead_code)] // the first writers are the wave-2 routes that move next

use serde_json::Value;
use sqlx::PgPool;

pub const DEFAULT_TTL_SECONDS: i64 = 3600;
pub const MAX_TTL_SECONDS: i64 = 7 * 24 * 3600;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Channel {
    Email,
    Whatsapp,
}

impl Channel {
    pub fn as_str(self) -> &'static str {
        match self {
            Channel::Email => "email",
            Channel::Whatsapp => "whatsapp",
        }
    }
}

/// One registered kind: (channel, name, required params, optional params).
pub struct Kind {
    pub channel: Channel,
    pub name: &'static str,
    pub required: &'static [&'static str],
    pub optional: &'static [&'static str],
}

/// `outbox.KINDS`.
pub const KINDS: &[Kind] = &[
    Kind { channel: Channel::Email, name: "verification", required: &["verify_url"], optional: &["full_name"] },
    Kind { channel: Channel::Email, name: "password_reset", required: &["reset_url"], optional: &[] },
    Kind { channel: Channel::Email, name: "change_password_code", required: &["code"], optional: &[] },
    Kind { channel: Channel::Email, name: "password_reset_otp", required: &["code"], optional: &[] },
    Kind { channel: Channel::Email, name: "account_setup", required: &["setup_url"], optional: &["full_name"] },
    Kind {
        channel: Channel::Email,
        name: "po_approval_request",
        required: &["po_log_id", "amount"],
        optional: &["approver_id", "requester_id", "decision_token"],
    },
    Kind {
        channel: Channel::Email,
        name: "po_approval_decision",
        required: &["po_log_id", "amount", "approved"],
        optional: &["requester_id", "decider_id", "comment"],
    },
    Kind { channel: Channel::Email, name: "scheduled_report", required: &["run_id", "recipient_id"], optional: &[] },
    Kind { channel: Channel::Whatsapp, name: "verification_code", required: &["code"], optional: &[] },
    Kind {
        channel: Channel::Whatsapp,
        name: "po_approval_link",
        required: &["po_log_id", "amount", "decision_token"],
        optional: &["approver_id"],
    },
];

/// `outbox.check_params`: why a request is not acceptable, or `None`.
pub fn check_params(channel: Channel, kind: &str, params: &Value) -> Option<String> {
    let Some(spec) = KINDS.iter().find(|k| k.channel == channel && k.name == kind) else {
        return Some("unknown_kind".into());
    };
    let Some(map) = params.as_object() else { return Some("params_not_an_object".into()) };
    if let Some(missing) = spec.required.iter().find(|k| map.get(**k).map_or(true, Value::is_null)) {
        return Some(format!("missing_param:{missing}"));
    }
    let mut extra: Vec<&String> = map
        .keys()
        .filter(|k| !spec.required.contains(&k.as_str()) && !spec.optional.contains(&k.as_str()))
        .collect();
    extra.sort();
    extra.first().map(|k| format!("unexpected_param:{k}"))
}

fn check(channel: Channel, kind: &str, params: &Value) -> Option<String> {
    check_params(channel, kind, params)
}

/// One message to queue.
pub struct Message<'a> {
    pub tenant_id: &'a str,
    pub channel: Channel,
    pub kind: &'a str,
    pub recipient: &'a str,
    pub params: Value,
    pub created_by: Option<&'a str>,
    /// One message per (tenant, key): a retried request queues it once.
    pub dedupe_key: Option<&'a str>,
    pub ttl_seconds: i64,
}

impl<'a> Message<'a> {
    pub fn new(tenant_id: &'a str, channel: Channel, kind: &'a str, recipient: &'a str, params: Value) -> Self {
        Message { tenant_id, channel, kind, recipient, params, created_by: None, dedupe_key: None,
            ttl_seconds: DEFAULT_TTL_SECONDS }
    }
}

/// `outbox.enqueue`: the id of the new row. `None` for a duplicate
/// `dedupe_key`, for a refused request and for a failed write (the last two
/// are logged at ERROR). Never raises.
pub async fn enqueue(pool: &PgPool, msg: Message<'_>) -> Option<String> {
    let recipient = crate::pycompat::py_strip(msg.recipient);
    let mut problem = check(msg.channel, msg.kind, &msg.params);
    if problem.is_none() && recipient.is_empty() {
        problem = Some("recipient_missing".into());
    }
    if let Some(p) = problem {
        tracing::error!(channel = msg.channel.as_str(), kind = msg.kind, tenant = msg.tenant_id,
            "[outbox] refused: {p}");
        return None;
    }
    let ttl = msg.ttl_seconds.clamp(1, MAX_TTL_SECONDS);
    let row: Result<Option<(String,)>, sqlx::Error> = sqlx::query_as(
        "INSERT INTO outbound_messages \
             (tenant_id, channel, kind, recipient, params, created_by, dedupe_key, next_attempt_at, expires_at) \
         VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW() + ($8 || ' seconds')::interval) \
         ON CONFLICT (tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING \
         RETURNING id",
    )
    .bind(msg.tenant_id)
    .bind(msg.channel.as_str())
    .bind(msg.kind)
    .bind(recipient)
    .bind(&msg.params)
    .bind(msg.created_by)
    .bind(msg.dedupe_key)
    .bind(ttl.to_string())
    .fetch_optional(pool)
    .await;
    match row {
        Ok(r) => r.map(|(id,)| id),
        Err(e) => {
            tracing::error!(error = %e, channel = msg.channel.as_str(), kind = msg.kind, tenant = msg.tenant_id,
                "[outbox] could not queue");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// The Python registry, parsed from its source: `_kind("email", "name", (required), (optional), fn)`.
    fn python_kinds() -> Vec<(String, String, Vec<String>, Vec<String>)> {
        let src = include_str!("../../backend/notifications/outbox.py");
        let re = regex::Regex::new(r#"_kind\(\s*"(\w+)",\s*"(\w+)",\s*\(([^)]*)\),\s*\(([^)]*)\)"#).unwrap();
        let names = |s: &str| -> Vec<String> {
            s.split(',').map(|p| p.trim().trim_matches('"').to_string()).filter(|p| !p.is_empty()).collect()
        };
        re.captures_iter(src).map(|c| (c[1].to_string(), c[2].to_string(), names(&c[3]), names(&c[4]))).collect()
    }

    #[test]
    fn the_registry_is_the_python_registry() {
        let py = python_kinds();
        assert_eq!(py.len(), KINDS.len(), "kinds added on one side only: {py:?}");
        for (i, (channel, name, required, optional)) in py.iter().enumerate() {
            let k = &KINDS[i];
            assert_eq!(k.channel.as_str(), channel, "{name}");
            assert_eq!(k.name, name);
            assert_eq!(k.required.iter().map(|s| s.to_string()).collect::<Vec<_>>(), *required, "{name}");
            assert_eq!(k.optional.iter().map(|s| s.to_string()).collect::<Vec<_>>(), *optional, "{name}");
        }
    }

    #[test]
    fn the_limits_are_the_python_limits() {
        let src = include_str!("../../backend/notifications/outbox.py");
        assert!(src.contains(&format!("DEFAULT_TTL_SECONDS = {DEFAULT_TTL_SECONDS}")));
        assert!(src.contains("MAX_TTL_SECONDS = 7 * 24 * 3600"));
    }

    /// Writes the scenarios in `OUTBOX_TEST_SCENARIOS` (a JSON list of
    /// `{label, channel, kind, recipient, params, dedupe_key?, ttl_seconds?, created_by?}`)
    /// to the database in `OUTBOX_TEST_DATABASE_URL`, for tenant
    /// `OUTBOX_TEST_TENANT`, and prints `ROW <label> <id or none>` per scenario.
    /// Run by tests/contract/contract_test.py, which compares the rows with the
    /// ones Python's `outbox.enqueue` writes for the same scenarios.
    #[tokio::test]
    #[ignore = "needs a database: run by tests/contract/contract_test.py"]
    async fn writes_what_python_writes() {
        let url = std::env::var("OUTBOX_TEST_DATABASE_URL").expect("OUTBOX_TEST_DATABASE_URL");
        let tenant = std::env::var("OUTBOX_TEST_TENANT").expect("OUTBOX_TEST_TENANT");
        let scenarios: Vec<Value> =
            serde_json::from_str(&std::env::var("OUTBOX_TEST_SCENARIOS").expect("OUTBOX_TEST_SCENARIOS")).unwrap();
        let pool = sqlx::postgres::PgPoolOptions::new().max_connections(2).connect(&url).await.unwrap();
        for s in scenarios {
            let channel = match s["channel"].as_str().unwrap() {
                "email" => Channel::Email,
                "whatsapp" => Channel::Whatsapp,
                // A channel the registry does not have: Python refuses it too.
                _ => {
                    println!("ROW {} none", s["label"].as_str().unwrap());
                    continue;
                }
            };
            let mut m = Message::new(&tenant, channel, s["kind"].as_str().unwrap(), s["recipient"].as_str().unwrap(),
                s["params"].clone());
            m.created_by = s["created_by"].as_str();
            m.dedupe_key = s["dedupe_key"].as_str();
            if let Some(t) = s["ttl_seconds"].as_i64() {
                m.ttl_seconds = t;
            }
            let id = enqueue(&pool, m).await;
            println!("ROW {} {}", s["label"].as_str().unwrap(), id.as_deref().unwrap_or("none"));
        }
    }

    #[test]
    fn params_are_checked_like_python() {
        let e = Channel::Email;
        assert_eq!(check(e, "password_reset_otp", &json!({"code": "1"})), None);
        assert_eq!(check(e, "nope", &json!({})).as_deref(), Some("unknown_kind"));
        assert_eq!(check(Channel::Whatsapp, "password_reset_otp", &json!({"code": "1"})).as_deref(), Some("unknown_kind"));
        assert_eq!(check(e, "password_reset_otp", &json!({})).as_deref(), Some("missing_param:code"));
        assert_eq!(check(e, "password_reset_otp", &json!({"code": null})).as_deref(), Some("missing_param:code"));
        assert_eq!(check(e, "password_reset_otp", &json!({"code": "1", "x": 1})).as_deref(), Some("unexpected_param:x"));
        assert_eq!(check(e, "password_reset_otp", &json!(["code"])).as_deref(), Some("params_not_an_object"));
    }
}
