//! SAML 2.0 single sign-on, configuration routes. NEW in Rust: they have no
//! Python twin and no failover (docs/rust-migration.md, "SAML").
//!
//!   GET    /auth/saml/config        the tenant's SAML configuration + what the IdP needs (admin)
//!   PUT    /auth/saml/config        save it; IdP given as pasted metadata XML or explicit fields (admin)
//!   DELETE /auth/saml/config        remove it (admin)
//!   GET    /auth/saml/sp-metadata   our service provider metadata, for the IdP's administrator (admin)
//!
//! The sign-in endpoints (`/auth/saml/start`, `/auth/saml/acs`) and the
//! assertion validation are Python (`backend/auth/saml/`); this side only
//! writes the rows they read. The tenant rules are the OIDC ones
//! (`backend/auth/sso/__init__.py`): a domain belongs to one tenant, new
//! people are never administrators, roles map to analyst or viewer only, and
//! "require SSO" can only be switched on after an administrator signed in
//! through this very configuration.
//!
//! Beyond the OIDC rules, one more lock-out guard: while SSO is required there
//! is no password door, so a request that would change WHICH identity provider
//! is trusted (entity id, sign-on URL, or a certificate set sharing nothing
//! with the stored one) cannot also require SSO. Turn enforcement off, change
//! the provider, sign in once, turn it back on.

use axum::body::Bytes;
use axum::extract::State;
use axum::http::{header, HeaderMap};
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use chrono::Utc;
use serde_json::{json, Map, Value};
use sqlx::{PgConnection, Row};

use crate::activity::{record_event_with_reason, Event};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::limits::take_tenant_lock;
use crate::pycompat::isoformat_utc;
use crate::routes::ok;
use crate::saml::cert::{self, CertError, CertInfo};
use crate::saml::metadata::{self, MetadataError};
use crate::saml::rules;
use crate::service_config;
use crate::state::AppState;
use crate::validation::{self, Errors, StrRules, NO_STR_RULES};

/// `INTERNAL_TAGS["auth"]`, as `exposure()` words the reason.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'auth': login, signup and session tokens: a machine holds a key, it never logs in"),
    is_mcp: false,
};

const MAX_CERTIFICATES: usize = 5;
const REASON: &str = "changed_by_an_account_admin";

// ── Where the IdP's endpoints live (shared formulas with Python) ─────────────

/// `backend/auth/saml/service.py::acs_url`.
pub fn acs_url(frontend_url: &str) -> String {
    format!("{}/api/v1/auth/saml/acs", frontend_url.trim_end_matches('/'))
}

/// `backend/auth/saml/service.py::sp_entity_id`.
pub fn sp_entity_id(frontend_url: &str, tenant_id: &str) -> String {
    format!("{}/api/v1/auth/saml/sp/{tenant_id}", frontend_url.trim_end_matches('/'))
}

// ── Stored row ───────────────────────────────────────────────────────────────

struct Stored {
    idp_entity_id: String,
    sso_url: String,
    certificates: Vec<String>,
    allowed_domains: Vec<String>,
    default_role: String,
    enforce_sso: bool,
    email_attribute: Option<String>,
    groups_attribute: Option<String>,
    group_roles: Map<String, Value>,
    enabled: bool,
    updated_at: Option<chrono::DateTime<Utc>>,
}

fn strings(v: Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

async fn load(conn: &mut PgConnection, tenant_id: &str, lock: bool) -> Result<Option<Stored>, ApiError> {
    let sql = format!(
        "SELECT idp_entity_id, sso_url, idp_certificates, allowed_domains, default_role, enforce_sso,
                email_attribute, groups_attribute, group_roles, enabled, updated_at
           FROM saml_providers WHERE tenant_id = $1{}",
        if lock { " FOR UPDATE" } else { "" }
    );
    let row = sqlx::query(&sql).bind(tenant_id).fetch_optional(&mut *conn).await?;
    let Some(r) = row else { return Ok(None) };
    Ok(Some(Stored {
        idp_entity_id: r.try_get("idp_entity_id")?,
        sso_url: r.try_get("sso_url")?,
        certificates: strings(r.try_get("idp_certificates")?),
        allowed_domains: strings(r.try_get("allowed_domains")?),
        default_role: r.try_get("default_role")?,
        enforce_sso: r.try_get("enforce_sso")?,
        email_attribute: r.try_get("email_attribute")?,
        groups_attribute: r.try_get("groups_attribute")?,
        group_roles: r.try_get::<Value, _>("group_roles")?.as_object().cloned().unwrap_or_default(),
        enabled: r.try_get("enabled")?,
        updated_at: r.try_get("updated_at")?,
    }))
}

/// An administrator of the tenant has signed in through THIS provider
/// (`user_identities.provider = 'saml:<tenant>'`).
async fn admin_has_signed_in(conn: &mut PgConnection, tenant_id: &str) -> Result<bool, ApiError> {
    let row = sqlx::query(
        "SELECT 1 FROM user_identities ui JOIN users u ON u.id = ui.user_id
          WHERE ui.provider = $1 AND u.tenant_id = $2 AND u.role = 'admin' AND u.status = 'active' LIMIT 1",
    )
    .bind(format!("saml:{tenant_id}"))
    .bind(tenant_id)
    .fetch_optional(&mut *conn)
    .await?;
    Ok(row.is_some())
}

fn view(s: &Stored, admin_signed_in: bool) -> Value {
    let certificates: Vec<Value> = s
        .certificates
        .iter()
        .map(|c| match cert::describe(c) {
            Ok(i) => json!({
                "fingerprint_sha256": i.fingerprint_sha256,
                "not_after": isoformat_utc(&i.not_after),
                "expired": i.not_after <= Utc::now(),
            }),
            // Python refuses to sign anyone in with a certificate it cannot read.
            Err(_) => json!({"fingerprint_sha256": null, "not_after": null, "expired": null}),
        })
        .collect();
    json!({
        "idp_entity_id": s.idp_entity_id,
        "sso_url": s.sso_url,
        "certificates": certificates,
        "allowed_domains": s.allowed_domains,
        "default_role": s.default_role,
        "enforce_sso": s.enforce_sso,
        "email_attribute": s.email_attribute,
        "groups_attribute": s.groups_attribute,
        "group_roles": s.group_roles,
        "enabled": s.enabled,
        "admin_signed_in": admin_signed_in,
        "updated_at": s.updated_at.map(|d| isoformat_utc(&d)),
    })
}

async fn admin(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    Ok(user)
}

async fn instance_enabled(state: &AppState) -> bool {
    service_config::enterprise_sso_enabled(&state.pool, &state.settings).await
}

// ── GET /auth/saml/config ────────────────────────────────────────────────────

pub async fn get_config(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    let mut conn = state.pool.acquire().await?;
    let stored = load(&mut conn, &user.tenant_id, false).await?;
    let signed_in = admin_has_signed_in(&mut conn, &user.tenant_id).await?;
    let fe = &state.settings.frontend_url;
    Ok(ok(json!({
        "instance_enabled": instance_enabled(&state).await,
        "sp": {
            "entity_id": sp_entity_id(fe, &user.tenant_id),
            "acs_url": acs_url(fe),
        },
        "config": stored.as_ref().map(|s| view(s, signed_in)),
    })))
}

// ── GET /auth/saml/sp-metadata ───────────────────────────────────────────────

pub async fn sp_metadata(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    if !instance_enabled(&state).await {
        return Err(sso_instance_disabled());
    }
    let fe = &state.settings.frontend_url;
    let xml = metadata::sp_metadata(&sp_entity_id(fe, &user.tenant_id), &acs_url(fe));
    Ok((
        [
            (header::CONTENT_TYPE, "application/samlmetadata+xml; charset=utf-8"),
            (header::CONTENT_DISPOSITION, "attachment; filename=\"stockai-saml-sp-metadata.xml\""),
            (header::CACHE_CONTROL, "no-store"),
        ],
        xml,
    )
        .into_response())
}

fn sso_instance_disabled() -> ApiError {
    ApiError::app("sso_instance_disabled", "Enterprise sign-in is not enabled on this installation.", 409, json!({}))
}

// ── PUT /auth/saml/config ────────────────────────────────────────────────────

/// The request body, validated for TYPE only; the business rules follow.
struct Put {
    metadata_xml: Option<String>,
    idp_entity_id: Option<String>,
    sso_url: Option<String>,
    certificates: Option<Vec<String>>,
    allowed_domains: Vec<String>,
    default_role: String,
    enforce_sso: bool,
    email_attribute: Option<String>,
    groups_attribute: Option<String>,
    group_roles: Map<String, Value>,
    enabled: bool,
}

fn opt_str(errs: &mut Errors, o: &Map<String, Value>, name: &str, max: usize) -> Option<String> {
    let rules = StrRules { min_length: None, max_length: Some(max), pattern: None };
    match validation::str_field(errs, o, &[json!("body")], name, false, true, &rules) {
        validation::Field::Value(s) => Some(s),
        _ => None,
    }
}

fn string_list(errs: &mut Errors, o: &Map<String, Value>, name: &str, min: usize, max: usize, required: bool)
    -> Option<Vec<String>>
{
    if !required && !o.contains_key(name) {
        return None;
    }
    if !required && o.get(name) == Some(&Value::Null) {
        return None;
    }
    let items = validation::list_field(errs, o, &[json!("body")], name, min, max)?;
    let mut out = Vec::new();
    for (i, it) in items.iter().enumerate() {
        match it {
            Value::String(s) => out.push(s.clone()),
            other => errs.push("string_type", &[json!("body"), json!(name), json!(i)],
                "Input should be a valid string".into(), other, None),
        }
    }
    Some(out)
}

fn parse_put(o: &Map<String, Value>) -> Result<Put, ApiError> {
    let mut errs = Errors::default();
    let metadata_xml = opt_str(&mut errs, o, "metadata_xml", metadata::MAX_METADATA_BYTES);
    let idp_entity_id = opt_str(&mut errs, o, "idp_entity_id", 1024);
    let sso_url = opt_str(&mut errs, o, "sso_url", 2048);
    let certificates = string_list(&mut errs, o, "certificates", 0, 20, false);
    let allowed_domains = string_list(&mut errs, o, "allowed_domains", 0, 50, true).unwrap_or_default();
    let default_role = match validation::str_field(&mut errs, o, &[json!("body")], "default_role", false, false,
        &StrRules { min_length: None, max_length: Some(20), pattern: None })
    {
        validation::Field::Value(s) => s,
        _ => "viewer".into(),
    };
    let enforce_sso = matches!(validation::bool_field(&mut errs, o, &[json!("body")], "enforce_sso", false),
        validation::Field::Value(true));
    let enabled = match validation::bool_field(&mut errs, o, &[json!("body")], "enabled", false) {
        validation::Field::Value(b) => b,
        _ => true,
    };
    let email_attribute = opt_str(&mut errs, o, "email_attribute", 300);
    let groups_attribute = opt_str(&mut errs, o, "groups_attribute", 300);
    let group_roles = match o.get("group_roles") {
        None | Some(Value::Null) => Map::new(),
        Some(Value::Object(m)) if m.len() <= 200 => m.clone(),
        Some(Value::Object(m)) => {
            errs.push("too_long", &[json!("body"), json!("group_roles")],
                format!("Dictionary should have at most 200 items after validation, not {}", m.len()),
                &Value::Null, Some(json!({"field_type": "Dictionary", "max_length": 200, "actual_length": m.len()})));
            Map::new()
        }
        Some(other) => {
            errs.push("dict_type", &[json!("body"), json!("group_roles")],
                "Input should be a valid dictionary".into(), other, None);
            Map::new()
        }
    };
    let _ = NO_STR_RULES;
    errs.into_result()?;
    Ok(Put {
        metadata_xml, idp_entity_id, sso_url, certificates, allowed_domains, default_role, enforce_sso,
        email_attribute, groups_attribute, group_roles, enabled,
    })
}

/// The identity provider a request names, after validation.
struct Idp {
    entity_id: String,
    sso_url: String,
    certs: Vec<CertInfo>,
}

fn incomplete() -> ApiError {
    ApiError::app("saml_config_incomplete",
        "The identity provider is required: paste its metadata, or give its entity ID, sign-on URL and certificate.",
        422, json!({}))
}

fn metadata_error(e: MetadataError) -> ApiError {
    let (code, msg) = match e {
        MetadataError::Invalid => ("saml_metadata_invalid", "That is not valid SAML metadata."),
        MetadataError::NoIdp => ("saml_metadata_no_idp",
            "The metadata must describe exactly one SAML 2.0 identity provider."),
        MetadataError::NoRedirectBinding => ("saml_metadata_no_redirect_binding",
            "The identity provider must offer the HTTP-Redirect sign-on binding."),
        MetadataError::NoCertificate => ("saml_metadata_no_certificate",
            "The metadata has no signing certificate."),
        MetadataError::Expired => ("saml_metadata_expired", "That metadata has expired."),
    };
    ApiError::app(code, msg, 422, json!({}))
}

fn validate_entity_id(raw: &str) -> Result<String, ApiError> {
    let id = raw.trim();
    if id.is_empty() || id.chars().count() > 1024 || id.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err(ApiError::app("saml_entity_id_invalid", "The entity ID is empty or not valid.", 422, json!({})));
    }
    Ok(id.to_string())
}

/// https, a host, no credentials in the URL, no fragment.
fn validate_sso_url(raw: &str) -> Result<String, ApiError> {
    let bad = || ApiError::app("saml_sso_url_invalid",
        "The sign-on URL must be an https address.", 422, json!({}));
    let url = raw.trim();
    let rest = url.strip_prefix("https://").ok_or_else(bad)?;
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if url.len() > 2048
        || authority.is_empty()
        || authority.contains('@')
        || url.contains('#')
        || url.chars().any(|c| c.is_control() || c.is_whitespace() || c == '\\')
    {
        return Err(bad());
    }
    let host = authority.rsplit_once(':').map(|(h, p)| if p.chars().all(|c| c.is_ascii_digit()) { h } else { authority }).unwrap_or(authority);
    if host.is_empty() || host.starts_with('.') {
        return Err(bad());
    }
    Ok(url.to_string())
}

fn validate_certificates(inputs: &[String]) -> Result<Vec<CertInfo>, ApiError> {
    if inputs.is_empty() {
        return Err(ApiError::app("saml_metadata_no_certificate", "At least one signing certificate is required.",
            422, json!({})));
    }
    let now = Utc::now();
    let mut out: Vec<CertInfo> = Vec::new();
    for (i, raw) in inputs.iter().enumerate() {
        let info = cert::inspect(raw, now).map_err(|e| match e {
            CertError::Unreadable => ApiError::app("saml_certificate_invalid",
                "A certificate could not be read. Paste it as PEM or base64.", 422, json!({"index": i + 1})),
            CertError::NotRsa => ApiError::app("saml_certificate_unsupported",
                "Only RSA signing certificates are supported.", 422, json!({"index": i + 1})),
            CertError::Weak(bits) => ApiError::app("saml_certificate_weak",
                "The certificate key is too short (2048 bits or more is required).", 422,
                json!({"index": i + 1, "bits": bits})),
            CertError::Expired => ApiError::app("saml_certificate_expired",
                "A certificate has already expired.", 422, json!({"index": i + 1})),
        })?;
        if !out.iter().any(|c| c.fingerprint_sha256 == info.fingerprint_sha256) {
            out.push(info);
        }
    }
    if out.len() > MAX_CERTIFICATES {
        return Err(ApiError::app("saml_too_many_certificates", "Too many certificates.", 422,
            json!({"max": MAX_CERTIFICATES})));
    }
    Ok(out)
}

/// The IdP the request names, or `None` to keep the stored one.
fn resolve_idp(p: &Put) -> Result<Option<Idp>, ApiError> {
    let blank = |s: &Option<String>| s.as_deref().map(|x| x.trim().is_empty()).unwrap_or(true);
    let has_metadata = !blank(&p.metadata_xml);
    let has_explicit = !blank(&p.idp_entity_id) || !blank(&p.sso_url) || p.certificates.as_ref().is_some_and(|c| !c.is_empty());
    match (has_metadata, has_explicit) {
        (true, true) => Err(ApiError::app("saml_idp_source_ambiguous",
            "Give the identity provider as metadata OR as separate fields, not both.", 422, json!({}))),
        (true, false) => {
            let md = metadata::parse_idp_metadata(p.metadata_xml.as_deref().unwrap_or(""), Utc::now())
                .map_err(metadata_error)?;
            Ok(Some(Idp {
                entity_id: validate_entity_id(&md.entity_id)?,
                sso_url: validate_sso_url(&md.sso_url)?,
                certs: validate_certificates(&md.certificates)?,
            }))
        }
        (false, true) => {
            let (Some(id), Some(url), Some(certs)) = (&p.idp_entity_id, &p.sso_url, &p.certificates) else {
                return Err(incomplete());
            };
            Ok(Some(Idp {
                entity_id: validate_entity_id(id)?,
                sso_url: validate_sso_url(url)?,
                certs: validate_certificates(certs)?,
            }))
        }
        (false, false) => Ok(None),
    }
}

pub async fn put_config(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    let content_type = headers.get(header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let parsed = validation::read_body(content_type, &body)?;
    let put = parse_put(&validation::body_object(&parsed)?)?;

    if !instance_enabled(&state).await {
        return Err(sso_instance_disabled());
    }
    if !rules::ASSIGNABLE_ROLES.contains(&put.default_role.as_str()) {
        let shown: String = put.default_role.chars().take(30).collect();
        return Err(ApiError::app("sso_default_role_invalid",
            "The default role for new people is analyst or viewer.", 422, json!({"role": shown})));
    }
    let domains = rules::normalize_domains(&put.allowed_domains)?;
    let (groups_attribute, group_roles) =
        rules::clean_group_rules(put.groups_attribute.as_deref(), &put.group_roles)?;
    let email_attribute = rules::clean_attribute_name(put.email_attribute.as_deref(), "saml_email_attribute_invalid")?;
    let idp = resolve_idp(&put)?;

    // The configuring admin must own one of the domains they claim, with an
    // address we verified (same rule, same limits, as the OIDC provider).
    let admin_row: Option<(String, Option<bool>)> =
        sqlx::query_as("SELECT email, email_verified FROM users WHERE id = $1 AND tenant_id = $2")
            .bind(&user.user_id)
            .bind(&user.tenant_id)
            .fetch_optional(&state.pool)
            .await?;
    let admin_domain = admin_row.as_ref().and_then(|(e, _)| rules::email_domain(e));
    let verified = admin_row.as_ref().and_then(|(_, v)| *v).unwrap_or(false);
    if !verified || !admin_domain.as_ref().is_some_and(|d| domains.contains(d)) {
        return Err(ApiError::app("sso_domain_not_owned",
            "Include the domain of your own verified e-mail address.", 422,
            json!({"domain": admin_domain.unwrap_or_default()})));
    }

    let mut tx = state.pool.begin().await?;
    take_tenant_lock(&mut tx, &user.tenant_id).await?;

    // One protocol per tenant: an OIDC provider and a SAML one for the same
    // domains would be two enforcement rules with no good answer.
    let oidc: Option<(i32,)> = sqlx::query_as("SELECT 1 FROM sso_providers WHERE tenant_id = $1")
        .bind(&user.tenant_id)
        .fetch_optional(&mut *tx)
        .await?;
    if oidc.is_some() {
        return Err(ApiError::app("sso_protocol_conflict",
            "This account already uses OpenID Connect sign-in. Remove it before configuring SAML.", 409, json!({})));
    }

    let existing = load(&mut tx, &user.tenant_id, true).await?;
    let (entity_id, sso_url, cert_infos): (String, String, Vec<CertInfo>) = match (idp, &existing) {
        (Some(i), _) => (i.entity_id, i.sso_url, i.certs),
        (None, Some(e)) => {
            let certs = e.certificates.iter().filter_map(|c| cert::describe(c).ok()).collect::<Vec<_>>();
            if certs.len() != e.certificates.len() {
                // A stored certificate this build cannot read: do not carry it forward silently.
                return Err(ApiError::app("saml_certificate_invalid",
                    "A stored certificate could not be read. Provide the identity provider again.", 422,
                    json!({"index": 0})));
            }
            (e.idp_entity_id.clone(), e.sso_url.clone(), certs)
        }
        (None, None) => return Err(incomplete()),
    };

    for d in &domains {
        let owner: Option<(String,)> = sqlx::query_as("SELECT tenant_id FROM sso_domains WHERE domain = $1")
            .bind(d)
            .fetch_optional(&mut *tx)
            .await?;
        if owner.is_some_and(|(t,)| t != user.tenant_id) {
            return Err(domain_taken(d));
        }
    }

    // What is trusted, compared with what was trusted before.
    let (entity_changed, url_changed, certs_disjoint) = match &existing {
        None => (false, false, false),
        Some(e) => {
            let old: Vec<String> = e.certificates.iter()
                .filter_map(|c| cert::describe(c).ok()).map(|i| i.fingerprint_sha256).collect();
            (
                e.idp_entity_id != entity_id,
                e.sso_url != sso_url,
                !cert_infos.iter().any(|c| old.contains(&c.fingerprint_sha256)),
            )
        }
    };
    let idp_changed = entity_changed || url_changed || certs_disjoint;

    if put.enforce_sso {
        // Proof the configuration works and an admin has a way in: an admin
        // signed in through the provider, and THIS request does not swap the
        // provider underneath that proof.
        if idp_changed || !admin_has_signed_in(&mut tx, &user.tenant_id).await? {
            return Err(ApiError::app("sso_enforce_needs_admin_sign_in",
                "Before enforcing, an administrator must sign in through the provider at least once with this configuration.",
                409, json!({})));
        }
        if !put.enabled {
            return Err(ApiError::app("sso_enforce_needs_enabled",
                "Enforcement needs the provider to be enabled.", 422, json!({})));
        }
    }

    let cert_values: Vec<Value> = cert_infos.iter().map(|c| json!(c.der_b64)).collect();
    sqlx::query(
        "INSERT INTO saml_providers
             (tenant_id, idp_entity_id, sso_url, idp_certificates, allowed_domains, default_role,
              enforce_sso, email_attribute, groups_attribute, group_roles, enabled, updated_by)
         VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9,$10::jsonb,$11,$12)
         ON CONFLICT (tenant_id) DO UPDATE SET
             idp_entity_id = EXCLUDED.idp_entity_id, sso_url = EXCLUDED.sso_url,
             idp_certificates = EXCLUDED.idp_certificates, allowed_domains = EXCLUDED.allowed_domains,
             default_role = EXCLUDED.default_role, enforce_sso = EXCLUDED.enforce_sso,
             email_attribute = EXCLUDED.email_attribute, groups_attribute = EXCLUDED.groups_attribute,
             group_roles = EXCLUDED.group_roles, enabled = EXCLUDED.enabled,
             updated_at = NOW(), updated_by = EXCLUDED.updated_by",
    )
    .bind(&user.tenant_id)
    .bind(&entity_id)
    .bind(&sso_url)
    .bind(Value::Array(cert_values))
    .bind(json!(domains))
    .bind(&put.default_role)
    .bind(put.enforce_sso)
    .bind(&email_attribute)
    .bind(&groups_attribute)
    .bind(Value::Object(group_roles))
    .bind(put.enabled)
    .bind(&user.user_id)
    .execute(&mut *tx)
    .await?;

    sqlx::query("DELETE FROM sso_domains WHERE tenant_id = $1").bind(&user.tenant_id).execute(&mut *tx).await?;
    for d in &domains {
        let r = sqlx::query("INSERT INTO sso_domains (domain, tenant_id) VALUES ($1, $2)")
            .bind(d)
            .bind(&user.tenant_id)
            .execute(&mut *tx)
            .await;
        if let Err(sqlx::Error::Database(db)) = &r {
            if db.code().as_deref() == Some("23505") {
                // Another tenant claimed it between the check and the insert.
                return Err(domain_taken(d));
            }
        }
        r?;
    }
    if entity_changed {
        // A NameID means nothing outside the provider that issued it: people
        // re-link by e-mail on their next sign-in.
        sqlx::query("DELETE FROM user_identities WHERE provider = $1")
            .bind(format!("saml:{}", user.tenant_id))
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;

    let mut conn = state.pool.acquire().await?;
    let stored = load(&mut conn, &user.tenant_id, false).await?;
    let signed_in = admin_has_signed_in(&mut conn, &user.tenant_id).await?;
    drop(conn);

    let mut details = Map::new();
    details.insert("idp_entity_id".into(), json!(entity_id));
    details.insert("enabled".into(), json!(put.enabled));
    details.insert("enforce_sso".into(), json!(put.enforce_sso));
    details.insert("domains".into(), json!(domains.join(", ")));
    record_event_with_reason(&state.pool, &user.tenant_id, &user.user_id, Event::SamlConfigChanged,
        Some("saml"), details, Some(REASON)).await;

    Ok(ok(json!({"config": stored.as_ref().map(|s| view(s, signed_in))})))
}

fn domain_taken(domain: &str) -> ApiError {
    ApiError::app("sso_domain_taken", "That domain is already used by another account.", 409,
        json!({"domain": domain}))
}

// ── DELETE /auth/saml/config ─────────────────────────────────────────────────

pub async fn delete_config(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    let mut tx = state.pool.begin().await?;
    take_tenant_lock(&mut tx, &user.tenant_id).await?;
    let existing = load(&mut tx, &user.tenant_id, true).await?;
    let Some(existing) = existing else {
        return Err(ApiError::app("sso_not_configured", "No company sign-in is configured.", 404, json!({})));
    };
    sqlx::query("DELETE FROM sso_domains WHERE tenant_id = $1").bind(&user.tenant_id).execute(&mut *tx).await?;
    sqlx::query("DELETE FROM user_identities WHERE provider = $1")
        .bind(format!("saml:{}", user.tenant_id))
        .execute(&mut *tx)
        .await?;
    sqlx::query("DELETE FROM saml_providers WHERE tenant_id = $1").bind(&user.tenant_id).execute(&mut *tx).await?;
    tx.commit().await?;

    let mut details = Map::new();
    details.insert("idp_entity_id".into(), json!(existing.idp_entity_id));
    record_event_with_reason(&state.pool, &user.tenant_id, &user.user_id, Event::SamlConfigRemoved,
        Some("saml"), details, Some(REASON)).await;
    Ok(ok(json!({"removed": true})))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::saml::test_certs as fx;

    fn put(v: Value) -> Result<Put, ApiError> {
        parse_put(v.as_object().unwrap())
    }

    #[test]
    fn endpoints_have_the_shape_python_builds() {
        assert_eq!(acs_url("https://app.example.com/"), "https://app.example.com/api/v1/auth/saml/acs");
        assert_eq!(sp_entity_id("https://app.example.com", "ten_1"),
            "https://app.example.com/api/v1/auth/saml/sp/ten_1");
    }

    #[test]
    fn sso_url_must_be_plain_https() {
        assert!(validate_sso_url("https://idp.example.com/sso?x=1").is_ok());
        assert!(validate_sso_url("https://idp.example.com:8443/sso").is_ok());
        for bad in [
            "http://idp.example.com/sso", "javascript:alert(1)", "https://", "https://user:pw@idp.example.com/",
            "https://idp.example.com/a#frag", "https://idp.example.com/a b", "//idp.example.com", "", "https:///x",
            "https://idp.example.com\\@evil.test/",
        ] {
            assert_eq!(validate_sso_url(bad).unwrap_err().body["error_code"], "saml_sso_url_invalid", "{bad}");
        }
    }

    #[test]
    fn entity_id_is_one_token() {
        assert_eq!(validate_entity_id("  https://idp.example.com/meta ").unwrap(), "https://idp.example.com/meta");
        assert!(validate_entity_id("two words").is_err());
        assert!(validate_entity_id("").is_err());
    }

    #[test]
    fn certificates_are_judged_one_by_one_and_deduplicated() {
        assert_eq!(validate_certificates(&[fx::RSA2048.into(), fx::RSA2048.into()]).unwrap().len(), 1);
        let code = |v: Vec<String>| validate_certificates(&v).unwrap_err().body["error_code"].as_str().unwrap().to_string();
        assert_eq!(code(vec![]), "saml_metadata_no_certificate");
        assert_eq!(code(vec![fx::RSA1024.into()]), "saml_certificate_weak");
        assert_eq!(code(vec![fx::EC.into()]), "saml_certificate_unsupported");
        assert_eq!(code(vec![fx::EXPIRED.into()]), "saml_certificate_expired");
        assert_eq!(code(vec!["garbage".into()]), "saml_certificate_invalid");
        // The refusal says WHICH certificate.
        let e = validate_certificates(&[fx::RSA2048.into(), "garbage".into()]).unwrap_err();
        assert_eq!(e.body["error_params"]["index"], 2);
    }

    #[test]
    fn idp_source_is_metadata_or_fields_never_both_and_never_half() {
        let both = put(json!({"allowed_domains": ["a.com"], "metadata_xml": "<x/>", "idp_entity_id": "e"})).unwrap();
        assert_eq!(resolve_idp(&both).err().unwrap().body["error_code"], "saml_idp_source_ambiguous");
        let half = put(json!({"allowed_domains": ["a.com"], "idp_entity_id": "e"})).unwrap();
        assert_eq!(resolve_idp(&half).err().unwrap().body["error_code"], "saml_config_incomplete");
        let none = put(json!({"allowed_domains": ["a.com"]})).unwrap();
        assert!(resolve_idp(&none).unwrap().is_none());
        let junk = put(json!({"allowed_domains": ["a.com"], "metadata_xml": "not xml"})).unwrap();
        assert_eq!(resolve_idp(&junk).err().unwrap().body["error_code"], "saml_metadata_invalid");
        let full = put(json!({"allowed_domains": ["a.com"], "idp_entity_id": "https://e/m",
            "sso_url": "https://e/sso", "certificates": [fx::RSA2048]})).unwrap();
        let idp = resolve_idp(&full).unwrap().unwrap();
        assert_eq!((idp.entity_id.as_str(), idp.certs.len()), ("https://e/m", 1));
    }

    #[test]
    fn body_types_are_checked_before_any_rule() {
        let e = put(json!({"allowed_domains": "a.com"})).err().unwrap();
        assert_eq!(e.status.as_u16(), 422);
        assert_eq!(e.body["detail"][0]["type"], "list_type");
        let e = put(json!({})).err().unwrap();
        assert_eq!(e.body["detail"][0]["type"], "missing");
        let e = put(json!({"allowed_domains": ["a.com"], "enabled": "maybe"})).err().unwrap();
        assert_eq!(e.body["detail"][0]["type"], "bool_parsing");
        let p = put(json!({"allowed_domains": ["a.com"]})).unwrap();
        assert_eq!((p.default_role.as_str(), p.enabled, p.enforce_sso), ("viewer", true, false));
    }
}
