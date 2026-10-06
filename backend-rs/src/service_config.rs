//! The read side of `backend/service_config/` (resolver + store + status),
//! limited to what the migrated routes need:
//!
//! * `effective().contact_whatsapp / contact_email` for `GET /entitlements`;
//! * the per-service `state` map that `GET /health` reports.
//!
//! Precedence is Python's: an instance override row in `service_config`
//! (decrypted with the same Fernet key when it is a secret) beats the
//! environment, which beats the Settings default; only fields whose owning
//! service is panel-editable can be overridden. Tenant-scoped overrides are
//! not needed by any migrated route and are not read.
//!
//! Python caches overrides for 10 s per process; this reads them per request.
//! The values are the same, the Rust side is simply never staler.

use std::collections::HashMap;

use serde_json::{Map, Value};
use sqlx::PgPool;

use crate::config::{parse_bool, Settings};

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Str,
    Bool,
}

struct Field {
    key: &'static str,
    env: &'static str,
    kind: Kind,
    default: &'static str,
    /// Whether the owning service is editable from the panel (`owner.editable`).
    overridable: bool,
}

const fn f(key: &'static str, env: &'static str, kind: Kind, default: &'static str, overridable: bool) -> Field {
    Field { key, env, kind, default, overridable }
}

/// The registry fields the health states and the contact block depend on, as
/// declared in `backend/service_config/registry.py`.
const FIELDS: &[Field] = &[
    f("secret_key", "SECRET_KEY", Kind::Str, "", false),
    f("database_url", "DATABASE_URL", Kind::Str, "", false),
    f("frontend_url", "FRONTEND_URL", Kind::Str, "", false),
    f("deepseek_api_key", "DEEPSEEK_API_KEY", Kind::Str, "", true),
    f("resend_api_key", "RESEND_API_KEY", Kind::Str, "", true),
    f("smtp_user", "SMTP_USER", Kind::Str, "", true),
    f("smtp_pass", "SMTP_PASS", Kind::Str, "", true),
    f("twilio_account_sid", "TWILIO_ACCOUNT_SID", Kind::Str, "", true),
    f("twilio_auth_token", "TWILIO_AUTH_TOKEN", Kind::Str, "", true),
    f("twilio_whatsapp_from", "TWILIO_WHATSAPP_FROM", Kind::Str, "", true),
    f("twilio_sms_from", "TWILIO_SMS_FROM", Kind::Str, "", true),
    f("voyageai_api_key", "VOYAGEAI_API_KEY", Kind::Str, "", true),
    f("pinecone_api_key", "PINECONE_API_KEY", Kind::Str, "", true),
    f("pinecone_index", "PINECONE_INDEX", Kind::Str, "", true),
    f("integrations_secret_key", "INTEGRATIONS_SECRET_KEY", Kind::Str, "", false),
    f("contact_whatsapp", "CONTACT_WHATSAPP", Kind::Str, "", true),
    f("contact_email", "CONTACT_EMAIL", Kind::Str, "", true),
    f("social_login_enabled", "SOCIAL_LOGIN_ENABLED", Kind::Bool, "false", true),
    f("google_oauth_client_id", "GOOGLE_OAUTH_CLIENT_ID", Kind::Str, "", true),
    f("google_oauth_client_secret", "GOOGLE_OAUTH_CLIENT_SECRET", Kind::Str, "", true),
    f("microsoft_oauth_client_id", "MICROSOFT_OAUTH_CLIENT_ID", Kind::Str, "", true),
    f("microsoft_oauth_client_secret", "MICROSOFT_OAUTH_CLIENT_SECRET", Kind::Str, "", true),
    f("apple_oauth_service_id", "APPLE_OAUTH_SERVICE_ID", Kind::Str, "", true),
    f("apple_oauth_team_id", "APPLE_OAUTH_TEAM_ID", Kind::Str, "", true),
    f("apple_oauth_key_id", "APPLE_OAUTH_KEY_ID", Kind::Str, "", true),
    f("apple_oauth_private_key", "APPLE_OAUTH_PRIVATE_KEY", Kind::Str, "", true),
    f("enterprise_sso_enabled", "ENTERPRISE_SSO_ENABLED", Kind::Bool, "false", true),
    f("worker_enabled", "WORKER_ENABLED", Kind::Bool, "true", false),
    f("scheduler_enabled", "SCHEDULER_ENABLED", Kind::Bool, "true", false),
    f("public_api_only", "PUBLIC_API_ONLY", Kind::Bool, "false", false),
];

#[derive(Clone, Copy, PartialEq)]
enum ServiceKind {
    Core,
    External,
    Deployment,
}

struct ServiceDef {
    key: &'static str,
    kind: ServiceKind,
    switch: Option<&'static str>,
    /// `required_fields(service)`: own required fields, then borrowed ones.
    required: &'static [&'static str],
    /// `requires_any`: alternative groups, any one is enough.
    any_of: &'static [&'static [&'static str]],
}

/// `SERVICES`, in registry order (the order /health prints them in).
const SERVICES: &[ServiceDef] = &[
    ServiceDef { key: "core", kind: ServiceKind::Core, switch: None,
        required: &["secret_key", "database_url", "frontend_url"], any_of: &[] },
    ServiceDef { key: "llm", kind: ServiceKind::External, switch: None,
        required: &["deepseek_api_key"], any_of: &[] },
    ServiceDef { key: "email", kind: ServiceKind::External, switch: None, required: &[],
        any_of: &[&["resend_api_key"], &["smtp_user", "smtp_pass"]] },
    ServiceDef { key: "whatsapp", kind: ServiceKind::External, switch: None,
        required: &["twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_from"], any_of: &[] },
    ServiceDef { key: "sms", kind: ServiceKind::External, switch: None,
        required: &["twilio_sms_from", "twilio_account_sid", "twilio_auth_token"], any_of: &[] },
    ServiceDef { key: "rag", kind: ServiceKind::External, switch: None,
        required: &["voyageai_api_key", "pinecone_api_key", "pinecone_index"], any_of: &[] },
    ServiceDef { key: "secret_storage", kind: ServiceKind::Core, switch: None,
        required: &["integrations_secret_key"], any_of: &[] },
    ServiceDef { key: "contact", kind: ServiceKind::External, switch: None, required: &[],
        any_of: &[&["contact_whatsapp"], &["contact_email"]] },
    ServiceDef { key: "social_login", kind: ServiceKind::External, switch: Some("social_login_enabled"),
        required: &[],
        any_of: &[
            &["google_oauth_client_id", "google_oauth_client_secret"],
            &["microsoft_oauth_client_id", "microsoft_oauth_client_secret"],
            &["apple_oauth_service_id", "apple_oauth_team_id", "apple_oauth_key_id", "apple_oauth_private_key"],
        ] },
    ServiceDef { key: "inbound_email", kind: ServiceKind::Deployment, switch: None, required: &[], any_of: &[] },
    ServiceDef { key: "enterprise_sso", kind: ServiceKind::External, switch: Some("enterprise_sso_enabled"),
        required: &["frontend_url"], any_of: &[] },
    ServiceDef { key: "worker", kind: ServiceKind::Deployment, switch: None, required: &[], any_of: &[] },
    ServiceDef { key: "limits", kind: ServiceKind::Deployment, switch: None, required: &[], any_of: &[] },
    ServiceDef { key: "api_surface", kind: ServiceKind::Deployment, switch: None, required: &[], any_of: &[] },
    ServiceDef { key: "operations", kind: ServiceKind::Deployment, switch: None, required: &[], any_of: &[] },
];

/// The Fernet key in effect: `INTEGRATIONS_SECRET_KEY`, else the file the
/// Python app generates under `storage/`. Never created from here: a second
/// writer of that file is exactly what `crypto.py` warns against.
fn fernet(settings: &Settings) -> Option<fernet::Fernet> {
    let key = if !settings.integrations_secret_key.is_empty() {
        settings.integrations_secret_key.clone()
    } else {
        std::fs::read_to_string(settings.storage_path.join("instance_secret.key"))
            .ok()?
            .trim()
            .to_string()
    };
    fernet::Fernet::new(&key)
}

/// Instance-scope override rows, decrypted, as raw TEXT. `Err` means the
/// table could not be read, which (like Python) silently means "use the
/// environment".
pub async fn instance_overrides(pool: &PgPool, settings: &Settings) -> Result<HashMap<String, String>, ()> {
    let rows: Vec<(String, Option<String>, Option<String>)> = sqlx::query_as(
        "SELECT field, value_plain, value_encrypted FROM service_config
          WHERE tenant_id IS NULL OR tenant_id = ''",
    )
    .fetch_all(pool)
    .await
    .map_err(|_| ())?;
    let mut out = HashMap::new();
    let mut fer: Option<Option<fernet::Fernet>> = None;
    for (field, plain, encrypted) in rows {
        let raw = match (plain, encrypted) {
            (Some(p), _) => Some(p),
            (None, Some(token)) if !token.is_empty() => {
                let fx = fer.get_or_insert_with(|| fernet(settings));
                match fx.as_ref().and_then(|fx| fx.decrypt(&token).ok()) {
                    Some(bytes) => String::from_utf8(bytes).ok(),
                    None => {
                        tracing::error!(field = %field, "cannot decrypt stored override; INTEGRATIONS_SECRET_KEY may have changed");
                        None
                    }
                }
            }
            _ => None,
        };
        if let Some(raw) = raw {
            out.insert(field, raw);
        }
    }
    Ok(out)
}

#[derive(Clone, Debug, PartialEq)]
enum Resolved {
    Str(String),
    Bool(bool),
}

impl Resolved {
    fn truthy(&self) -> bool {
        match self {
            Resolved::Str(s) => !s.is_empty(),
            Resolved::Bool(b) => *b,
        }
    }
}

fn field(key: &str) -> &'static Field {
    FIELDS.iter().find(|f| f.key == key).expect("field declared in FIELDS")
}

fn coerce(fd: &Field, raw: &str) -> Option<Resolved> {
    match fd.kind {
        Kind::Str => Some(Resolved::Str(raw.to_string())),
        Kind::Bool => {
            // store.coerce: an unparseable stored value is ignored.
            let low = raw.trim().to_lowercase();
            match low.as_str() {
                "1" | "true" | "yes" | "on" | "t" => Some(Resolved::Bool(true)),
                "0" | "false" | "no" | "off" | "f" | "" => Some(Resolved::Bool(false)),
                _ => None,
            }
        }
    }
}

/// `resolve(field_key)` at instance scope.
fn resolve(settings: &Settings, overrides: &HashMap<String, String>, key: &str) -> Resolved {
    let fd = field(key);
    if fd.overridable {
        if let Some(v) = overrides.get(key).and_then(|raw| coerce(fd, raw)) {
            return v;
        }
    }
    let raw = settings.raw(fd.env);
    match fd.kind {
        Kind::Str => Resolved::Str(raw.unwrap_or(fd.default).to_string()),
        Kind::Bool => Resolved::Bool(
            raw.and_then(parse_bool).unwrap_or_else(|| parse_bool(fd.default).unwrap_or(false)),
        ),
    }
}

/// `effective().contact_whatsapp` / `contact_email`, as `_contact()` sends them.
pub async fn contact(pool: &PgPool, settings: &Settings) -> Map<String, Value> {
    let overrides = instance_overrides(pool, settings).await.unwrap_or_default();
    let mut m = Map::new();
    for (out_key, field_key) in [("whatsapp", "contact_whatsapp"), ("email", "contact_email")] {
        let v = match resolve(settings, &overrides, field_key) {
            Resolved::Str(s) => Value::String(s),
            Resolved::Bool(b) => Value::Bool(b),
        };
        m.insert(out_key.into(), v);
    }
    m
}

/// `{s.key: service_report(s)["state"] for s in SERVICES}`.
///
/// One Python state cannot be reproduced: `degraded` comes from the last
/// probe result kept IN MEMORY of the Python process. The Rust service runs
/// no probes, so it reports `ready` where Python may say `degraded` after a
/// failed probe. Listed in docs/rust-migration.md.
pub async fn service_states(pool: &PgPool, settings: &Settings) -> Map<String, Value> {
    let overrides = instance_overrides(pool, settings).await.unwrap_or_default();
    let r = |k: &str| resolve(settings, &overrides, k);
    let mut out = Map::new();
    for s in SERVICES {
        let state = match s.kind {
            ServiceKind::Deployment => match s.key {
                "worker" => {
                    if r("worker_enabled").truthy() || r("scheduler_enabled").truthy() {
                        "ready"
                    } else {
                        "off"
                    }
                }
                "api_surface" => {
                    if r("public_api_only").truthy() {
                        "on"
                    } else {
                        "off"
                    }
                }
                _ => "ready",
            },
            _ => {
                if s.switch.map(|sw| !r(sw).truthy()).unwrap_or(false) {
                    "off"
                } else {
                    let missing_required = s.required.iter().any(|k| !r(k).truthy());
                    let any_unsatisfied = !s.any_of.is_empty()
                        && !s.any_of.iter().any(|g| g.iter().all(|k| r(k).truthy()));
                    if missing_required || any_unsatisfied {
                        "not_configured"
                    } else {
                        "ready"
                    }
                }
            }
        };
        out.insert(s.key.to_string(), Value::String(state.to_string()));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings(extra: &[(&str, &str)]) -> Settings {
        let mut m = HashMap::new();
        m.insert("SECRET_KEY".to_string(), "s".to_string());
        m.insert("DATABASE_URL".to_string(), "postgres://x".to_string());
        m.insert("FRONTEND_URL".to_string(), "http://f".to_string());
        for (k, v) in extra {
            m.insert(k.to_string(), v.to_string());
        }
        Settings::from_map(m).unwrap()
    }

    #[test]
    fn env_then_default() {
        let s = settings(&[("CONTACT_EMAIL", "hola@example.com")]);
        let o = HashMap::new();
        assert_eq!(resolve(&s, &o, "contact_email"), Resolved::Str("hola@example.com".into()));
        assert_eq!(resolve(&s, &o, "contact_whatsapp"), Resolved::Str(String::new()));
        assert_eq!(resolve(&s, &o, "worker_enabled"), Resolved::Bool(true));
    }

    #[test]
    fn instance_override_wins_only_for_editable_services() {
        let s = settings(&[("CONTACT_EMAIL", "env@example.com"), ("PUBLIC_API_ONLY", "false")]);
        let mut o = HashMap::new();
        o.insert("contact_email".to_string(), "panel@example.com".to_string());
        o.insert("public_api_only".to_string(), "true".to_string());
        assert_eq!(resolve(&s, &o, "contact_email"), Resolved::Str("panel@example.com".into()));
        // api_surface is not editable: the stored row is ignored.
        assert_eq!(resolve(&s, &o, "public_api_only"), Resolved::Bool(false));
    }

    #[test]
    fn unparseable_stored_bool_falls_back_to_env() {
        let s = settings(&[("SOCIAL_LOGIN_ENABLED", "true")]);
        let mut o = HashMap::new();
        o.insert("social_login_enabled".to_string(), "perhaps".to_string());
        assert_eq!(resolve(&s, &o, "social_login_enabled"), Resolved::Bool(true));
    }
}
