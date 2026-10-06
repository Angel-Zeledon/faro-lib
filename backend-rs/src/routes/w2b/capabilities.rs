//! `GET /api/v1/service-config/capabilities`
//! (backend/api/v1/service_config.py::get_capabilities over
//! backend/service_config/status.py::capabilities): which user-visible
//! features can answer right now, for the caller's tenant. Any signed-in
//! person; booleans only, never a variable name or a reason.
//!
//! This is the ONE service-config route that moved. The others stay Python
//! because the panel shows the result of the last probe, a fact held in the
//! memory of the Python process (`status._last_probe`), and every write
//! forgets it there: a Rust write or a Rust read of `/services` would show a
//! stale or empty "last check" next to a Python probe. `/capabilities` reads no
//! probe state, so it can live on either side.
//!
//! Resolution is `resolver.resolve(field, tenant_id)`: a tenant row (only for
//! the fields of the tenant-scoped channels: email, whatsapp, sms) beats the
//! instance row, which beats the environment, which beats the default. Stored
//! secrets are decrypted with the same Fernet key. A stored value that does not
//! parse for its kind is ignored, as `store.coerce` does.

use std::collections::HashMap;

use axum::extract::State;
use axum::http::HeaderMap;
use axum::{Extension, Json};
use serde_json::{json, Value};
use sqlx::PgPool;

use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::config::{parse_bool, Settings};
use crate::error::ApiError;
use crate::routes::ok;
use crate::state::AppState;

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'service-config': instance and channel configuration (operators and tenant admins only)",
    ),
    is_mcp: false,
};

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Str,
    Bool,
    Float,
}

struct Field {
    key: &'static str,
    env: &'static str,
    kind: Kind,
    default: &'static str,
    /// Owned by a panel-editable service (an instance row can override it).
    editable: bool,
    /// Owned by a tenant-scoped service (a tenant row can override it).
    tenant_scoped: bool,
}

const fn f(key: &'static str, env: &'static str, kind: Kind, default: &'static str, editable: bool, tenant_scoped: bool) -> Field {
    Field { key, env, kind, default, editable, tenant_scoped }
}

/// The registry fields `capabilities()` reads, as declared in
/// `backend/service_config/registry.py` (the contract harness fails if a
/// declaration here drifts from the Python source: see `w2b_cases.py`).
const FIELDS: &[Field] = &[
    f("deepseek_api_key", "DEEPSEEK_API_KEY", Kind::Str, "", true, false),
    f("voyageai_api_key", "VOYAGEAI_API_KEY", Kind::Str, "", true, false),
    f("pinecone_api_key", "PINECONE_API_KEY", Kind::Str, "", true, false),
    f("pinecone_index", "PINECONE_INDEX", Kind::Str, "", true, false),
    f("resend_api_key", "RESEND_API_KEY", Kind::Str, "", true, true),
    f("smtp_user", "SMTP_USER", Kind::Str, "", true, true),
    f("smtp_pass", "SMTP_PASS", Kind::Str, "", true, true),
    f("twilio_account_sid", "TWILIO_ACCOUNT_SID", Kind::Str, "", true, true),
    f("twilio_auth_token", "TWILIO_AUTH_TOKEN", Kind::Str, "", true, true),
    f("twilio_whatsapp_from", "TWILIO_WHATSAPP_FROM", Kind::Str, "", true, true),
    f("twilio_sms_from", "TWILIO_SMS_FROM", Kind::Str, "", true, true),
    f("whatsapp_bot_generic_mode", "WHATSAPP_BOT_GENERIC_MODE", Kind::Bool, "false", true, true),
    f("contact_whatsapp", "CONTACT_WHATSAPP", Kind::Str, "", true, false),
    f("contact_email", "CONTACT_EMAIL", Kind::Str, "", true, false),
    f("stripe_secret_key", "STRIPE_SECRET_KEY", Kind::Str, "", true, false),
    f("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", Kind::Str, "", true, false),
    f("stripe_price_id_full", "STRIPE_PRICE_ID_FULL", Kind::Str, "", true, false),
    f("paypal_client_id", "PAYPAL_CLIENT_ID", Kind::Str, "", true, false),
    f("paypal_client_secret", "PAYPAL_CLIENT_SECRET", Kind::Str, "", true, false),
    f("paypal_webhook_id", "PAYPAL_WEBHOOK_ID", Kind::Str, "", true, false),
    f("paypal_plan_id_full", "PAYPAL_PLAN_ID_FULL", Kind::Str, "", true, false),
    f("paypal_mode", "PAYPAL_MODE", Kind::Str, "sandbox", true, false),
    f("billing_price_usd_full", "BILLING_PRICE_USD_FULL", Kind::Float, "59.0", true, false),
    // `editable=False` services: the environment is the only layer.
    f("worker_enabled", "WORKER_ENABLED", Kind::Bool, "true", false, false),
    f("scheduler_enabled", "SCHEDULER_ENABLED", Kind::Bool, "true", false, false),
];

/// (service, required fields in AND, alternative groups in OR), the readiness
/// rule `_service_is_ready` applies (`required_fields` includes the fields a
/// service borrows).
const LLM: (&[&str], &[&[&str]]) = (&["deepseek_api_key"], &[]);
const RAG: (&[&str], &[&[&str]]) = (&["voyageai_api_key", "pinecone_api_key", "pinecone_index"], &[]);
const EMAIL: (&[&str], &[&[&str]]) = (&[], &[&["resend_api_key"], &["smtp_user", "smtp_pass"]]);
const WHATSAPP: (&[&str], &[&[&str]]) =
    (&["twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_from"], &[]);
const SMS: (&[&str], &[&[&str]]) = (&["twilio_sms_from", "twilio_account_sid", "twilio_auth_token"], &[]);

#[derive(Clone, Debug, PartialEq)]
enum Val {
    Str(String),
    Bool(bool),
    Float(f64),
}

impl Val {
    fn truthy(&self) -> bool {
        match self {
            Val::Str(s) => !s.is_empty(),
            Val::Bool(b) => *b,
            Val::Float(x) => *x != 0.0,
        }
    }
    fn text(&self) -> String {
        match self {
            Val::Str(s) => s.clone(),
            Val::Bool(b) => if *b { "True".into() } else { "False".into() },
            Val::Float(x) => crate::pyjson::float_repr(*x),
        }
    }
}

fn find(key: &str) -> &'static Field {
    FIELDS.iter().find(|f| f.key == key).expect("field declared in FIELDS")
}

/// `store.coerce`: an unparseable stored value is ignored.
fn coerce(fd: &Field, raw: &str) -> Option<Val> {
    match fd.kind {
        Kind::Str => Some(Val::Str(raw.to_string())),
        Kind::Bool => match raw.trim().to_lowercase().as_str() {
            "1" | "true" | "yes" | "on" | "t" => Some(Val::Bool(true)),
            "0" | "false" | "no" | "off" | "f" | "" => Some(Val::Bool(false)),
            _ => None,
        },
        Kind::Float => raw.trim().parse::<f64>().ok().map(Val::Float),
    }
}

/// Every stored row of one scope, decrypted, as raw text. `None` scope is the
/// instance (`tenant_id` NULL or empty). Unreadable table: no overrides.
async fn rows(pool: &PgPool, settings: &Settings, tenant: Option<&str>) -> HashMap<String, String> {
    let q = match tenant {
        None => sqlx::query_as::<_, (String, Option<String>, Option<String>)>(
            "SELECT field, value_plain, value_encrypted FROM service_config WHERE tenant_id IS NULL OR tenant_id = ''",
        ),
        Some(t) => sqlx::query_as::<_, (String, Option<String>, Option<String>)>(
            "SELECT field, value_plain, value_encrypted FROM service_config WHERE tenant_id = $1",
        )
        .bind(t.to_string()),
    };
    let Ok(found) = q.fetch_all(pool).await else { return HashMap::new() };
    let mut fer: Option<Option<fernet::Fernet>> = None;
    let mut out = HashMap::new();
    for (field, plain, encrypted) in found {
        let raw = match (plain, encrypted) {
            (Some(p), _) => Some(p),
            (None, Some(token)) if !token.is_empty() => {
                let fx = fer.get_or_insert_with(|| crate::service_config::fernet(settings));
                fx.as_ref().and_then(|fx| fx.decrypt(&token).ok()).and_then(|b| String::from_utf8(b).ok())
            }
            _ => None,
        };
        if let Some(raw) = raw {
            out.insert(field, raw);
        }
    }
    out
}

struct Resolver<'a> {
    settings: &'a Settings,
    instance: HashMap<String, String>,
    tenant: HashMap<String, String>,
}

impl Resolver<'_> {
    /// `resolve(field, tenant_id).value`.
    fn get(&self, key: &str) -> Val {
        let fd = find(key);
        if fd.tenant_scoped && fd.editable {
            if let Some(v) = self.tenant.get(key).and_then(|raw| coerce(fd, raw)) {
                return v;
            }
        }
        if fd.editable {
            if let Some(v) = self.instance.get(key).and_then(|raw| coerce(fd, raw)) {
                return v;
            }
        }
        let raw = self.settings.raw(fd.env);
        match fd.kind {
            Kind::Str => Val::Str(raw.unwrap_or(fd.default).to_string()),
            Kind::Bool => Val::Bool(raw.and_then(parse_bool).unwrap_or_else(|| parse_bool(fd.default).unwrap_or(false))),
            Kind::Float => Val::Float(
                raw.and_then(|r| r.trim().parse().ok()).unwrap_or_else(|| fd.default.parse().unwrap_or(0.0)),
            ),
        }
    }
    fn on(&self, key: &str) -> bool {
        self.get(key).truthy()
    }
    /// `_service_is_ready` for a service without a switch.
    fn ready(&self, svc: (&[&str], &[&[&str]])) -> bool {
        let (required, any_of) = svc;
        required.iter().all(|k| self.on(k))
            && (any_of.is_empty() || any_of.iter().any(|g| g.iter().all(|k| self.on(k))))
    }
    /// `billing.providers.load().enabled_providers()` is non-empty. Billing
    /// reads the instance configuration and strips its values.
    fn online_payments(&self) -> bool {
        let s = |k: &str| self.get(k).text().trim().to_string();
        let price_ok = matches!(self.get("billing_price_usd_full"), Val::Float(p) if p > 0.0);
        if !price_ok {
            return false;
        }
        let stripe = ["stripe_secret_key", "stripe_webhook_secret", "stripe_price_id_full"]
            .iter()
            .all(|k| !s(k).is_empty());
        let mode = s("paypal_mode").to_lowercase();
        let paypal = ["paypal_client_id", "paypal_client_secret", "paypal_webhook_id", "paypal_plan_id_full"]
            .iter()
            .all(|k| !s(k).is_empty())
            && (mode == "sandbox" || mode == "live");
        stripe || paypal
    }
}

fn capabilities(r: &Resolver) -> Value {
    let llm = r.ready(LLM);
    let whatsapp = r.ready(WHATSAPP);
    json!({
        "assistant": llm,
        "ai_narrative": llm,
        "documents_search": r.ready(RAG),
        "email": r.ready(EMAIL),
        "whatsapp": whatsapp,
        "sms": r.ready(SMS),
        "whatsapp_bot": whatsapp && (llm || r.on("whatsapp_bot_generic_mode")),
        "contact_channels": {
            "whatsapp": r.on("contact_whatsapp"),
            "email": r.on("contact_email"),
        },
        "online_payments": r.online_payments(),
        "background_worker": r.on("worker_enabled"),
        "scheduled_jobs": r.on("scheduler_enabled"),
    })
}

pub async fn get_capabilities(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let resolver = Resolver {
        settings: &state.settings,
        instance: rows(&state.pool, &state.settings, None).await,
        tenant: rows(&state.pool, &state.settings, Some(&user.tenant_id)).await,
    };
    Ok(ok(capabilities(&resolver)))
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

    fn resolver<'a>(s: &'a Settings, inst: &[(&str, &str)], ten: &[(&str, &str)]) -> Resolver<'a> {
        let m = |xs: &[(&str, &str)]| xs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        Resolver { settings: s, instance: m(inst), tenant: m(ten) }
    }

    #[test]
    fn nothing_configured_is_all_off_but_the_worker_defaults() {
        let s = settings(&[]);
        let c = capabilities(&resolver(&s, &[], &[]));
        assert_eq!(c["assistant"], false);
        assert_eq!(c["email"], false);
        assert_eq!(c["online_payments"], false);
        assert_eq!(c["background_worker"], true);
        assert_eq!(c["contact_channels"], json!({"whatsapp": false, "email": false}));
    }

    #[test]
    fn email_runs_on_resend_or_on_both_smtp_halves() {
        let s = settings(&[("SMTP_USER", "u")]);
        assert_eq!(capabilities(&resolver(&s, &[], &[]))["email"], false);
        assert_eq!(capabilities(&resolver(&s, &[("smtp_pass", "p")], &[]))["email"], true);
        assert_eq!(capabilities(&resolver(&settings(&[]), &[], &[("resend_api_key", "k")]))["email"], true);
    }

    #[test]
    fn a_tenant_row_counts_only_for_tenant_scoped_channels() {
        let s = settings(&[]);
        // llm is instance-only: a tenant row for it is ignored.
        assert_eq!(capabilities(&resolver(&s, &[], &[("deepseek_api_key", "k")]))["assistant"], false);
        let wa = [("twilio_account_sid", "a"), ("twilio_auth_token", "b"), ("twilio_whatsapp_from", "c")];
        let c = capabilities(&resolver(&s, &[], &wa));
        assert_eq!(c["whatsapp"], true);
        assert_eq!(c["whatsapp_bot"], false, "bot needs the LLM or generic mode");
        let mut with_generic = wa.to_vec();
        with_generic.push(("whatsapp_bot_generic_mode", "true"));
        assert_eq!(capabilities(&resolver(&s, &[], &with_generic))["whatsapp_bot"], true);
        // sms borrows the twilio credentials from whatsapp.
        assert_eq!(capabilities(&resolver(&s, &[], &[("twilio_sms_from", "x")]))["sms"], false);
    }

    #[test]
    fn online_payments_needs_a_complete_provider_a_price_and_a_valid_mode() {
        let stripe = [("stripe_secret_key", "a"), ("stripe_webhook_secret", "b"), ("stripe_price_id_full", "c")];
        let s = settings(&[]);
        assert_eq!(capabilities(&resolver(&s, &stripe, &[]))["online_payments"], true);
        assert_eq!(capabilities(&resolver(&s, &stripe[..2], &[]))["online_payments"], false);
        let mut zero_price = stripe.to_vec();
        zero_price.push(("billing_price_usd_full", "0"));
        assert_eq!(capabilities(&resolver(&s, &zero_price, &[]))["online_payments"], false);
        let paypal = [("paypal_client_id", "a"), ("paypal_client_secret", "b"), ("paypal_webhook_id", "c"),
            ("paypal_plan_id_full", "d")];
        assert_eq!(capabilities(&resolver(&s, &paypal, &[]))["online_payments"], true);
        let mut bad_mode = paypal.to_vec();
        bad_mode.push(("paypal_mode", "prod"));
        assert_eq!(capabilities(&resolver(&s, &bad_mode, &[]))["online_payments"], false);
        let mut live = paypal.to_vec();
        live.push(("paypal_mode", " LIVE "));
        assert_eq!(capabilities(&resolver(&s, &live, &[]))["online_payments"], true);
    }

    #[test]
    fn an_unparseable_stored_value_falls_back_to_the_environment() {
        let s = settings(&[("CONTACT_EMAIL", "env@x.com")]);
        // a Float field with junk stored: the default price applies.
        let r = resolver(&s, &[("billing_price_usd_full", "abc"), ("stripe_secret_key", "a"),
            ("stripe_webhook_secret", "b"), ("stripe_price_id_full", "c")], &[]);
        assert_eq!(capabilities(&r)["online_payments"], true);
        assert_eq!(capabilities(&r)["contact_channels"]["email"], true);
    }
}
