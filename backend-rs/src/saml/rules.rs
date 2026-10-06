//! The tenant rules SAML shares with the OIDC provider, ported from
//! `backend/auth/sso/service.py`: which e-mail domains may be claimed, how
//! attribute-to-role mappings are cleaned, and that `admin` is never a target.
//!
//! One list of free-mail domains, one domain grammar, one role set. A unit test
//! re-reads the Python module and compares the free-mail list, so adding a
//! domain on one side only turns the build red instead of letting the two
//! protocols disagree about what a tenant may claim.

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::error::ApiError;

pub const ASSIGNABLE_ROLES: [&str; 2] = ["analyst", "viewer"];
pub const MAX_DOMAINS: usize = 20;
pub const MAX_GROUP_RULES: usize = 100;

pub const FREE_MAIL_DOMAINS: &[&str] = &[
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.es", "ymail.com", "icloud.com", "me.com",
    "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net",
    "mail.com", "zoho.com", "yandex.com", "qq.com", "163.com",
];

fn domain_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$").unwrap()
    })
}

/// `_DOMAIN_RE` (the Python one also has a `(?=.{4,253}$)` lookahead).
pub fn valid_domain(d: &str) -> bool {
    (4..=253).contains(&d.len()) && domain_re().is_match(d)
}

/// `email_domain`: the domain of an address with exactly one `@`.
pub fn email_domain(email: &str) -> Option<String> {
    if email.matches('@').count() != 1 {
        return None;
    }
    let lowered = email.trim().to_lowercase();
    let (local, domain) = lowered.split_once('@')?;
    if local.is_empty() || !valid_domain(domain) {
        return None;
    }
    Some(domain.to_string())
}

/// `normalize_domains`: every refusal is the same `AppError` Python raises.
pub fn normalize_domains(raw: &[String]) -> Result<Vec<String>, ApiError> {
    let mut seen: Vec<String> = Vec::new();
    for item in raw {
        let d = item.trim().to_lowercase();
        let d = d.trim_start_matches('@').to_string();
        if !valid_domain(&d) {
            let shown: String = item.chars().take(80).collect();
            return Err(ApiError::app("sso_domain_invalid", "Enter domains like example.com.", 422,
                json!({"domain": shown})));
        }
        if FREE_MAIL_DOMAINS.contains(&d.as_str()) {
            return Err(ApiError::app("sso_domain_not_allowed",
                "Public mailbox domains cannot be used for company sign-in.", 422, json!({"domain": d})));
        }
        if !seen.contains(&d) {
            seen.push(d);
        }
    }
    if seen.is_empty() {
        return Err(ApiError::app("sso_domain_required", "Add at least one e-mail domain.", 422, json!({})));
    }
    if seen.len() > MAX_DOMAINS {
        return Err(ApiError::app("sso_too_many_domains", "Too many domains.", 422, json!({"max": MAX_DOMAINS})));
    }
    Ok(seen)
}

fn attribute_name_ok(name: &str, max: usize) -> bool {
    !name.is_empty()
        && name.chars().count() <= max
        && name.chars().all(|c| c.is_ascii_alphanumeric() || "_.:/-".contains(c))
}

/// An attribute name as SAML writes them: a short word or a URN / URL.
pub fn clean_attribute_name(raw: Option<&str>, code: &str) -> Result<Option<String>, ApiError> {
    let name = raw.map(str::trim).filter(|s| !s.is_empty());
    match name {
        None => Ok(None),
        Some(n) if attribute_name_ok(n, 300) => Ok(Some(n.to_string())),
        Some(_) => Err(ApiError::app(code, "That attribute name is not valid.", 422, json!({}))),
    }
}

/// `_clean_group_rules`, with the attribute that carries the groups.
pub fn clean_group_rules(
    attribute: Option<&str>,
    rules: &Map<String, Value>,
) -> Result<(Option<String>, Map<String, Value>), ApiError> {
    let attribute = clean_attribute_name(attribute, "saml_groups_attribute_invalid")?;
    let mut cleaned = Map::new();
    for (group, role) in rules {
        let g = group.trim();
        if g.is_empty() || g.chars().count() > 200 {
            return Err(ApiError::app("sso_group_invalid", "A group name is empty or too long.", 422, json!({})));
        }
        let role_ok = role.as_str().filter(|r| ASSIGNABLE_ROLES.contains(r));
        let Some(role) = role_ok else {
            // `admin` lands here too, on purpose.
            let shown: String = match role {
                Value::String(s) => s.chars().take(30).collect(),
                other => other.to_string().chars().take(30).collect(),
            };
            return Err(ApiError::app(
                "sso_group_role_invalid",
                "Groups can map to analyst or viewer only; administrators are never created by sign-in.",
                422,
                json!({"role": shown}),
            ));
        };
        cleaned.insert(g.to_string(), json!(role));
    }
    if cleaned.len() > MAX_GROUP_RULES {
        return Err(ApiError::app("sso_too_many_group_rules", "Too many group rules.", 422,
            json!({"max": MAX_GROUP_RULES})));
    }
    if !cleaned.is_empty() && attribute.is_none() {
        return Err(ApiError::app("saml_groups_attribute_required",
            "Name the attribute that carries the groups.", 422, json!({})));
    }
    Ok((attribute, cleaned))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strs(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn domains_are_normalised_deduplicated_and_free_mail_refused() {
        assert_eq!(
            normalize_domains(&strs(&[" @Example.COM ", "example.com", "sub.example.co"])).unwrap(),
            vec!["example.com", "sub.example.co"]
        );
        let code = |r: Result<Vec<String>, ApiError>| r.unwrap_err().body["error_code"].as_str().unwrap().to_string();
        assert_eq!(code(normalize_domains(&strs(&["gmail.com"]))), "sso_domain_not_allowed");
        assert_eq!(code(normalize_domains(&strs(&["nodot"]))), "sso_domain_invalid");
        assert_eq!(code(normalize_domains(&strs(&["a b.com"]))), "sso_domain_invalid");
        assert_eq!(code(normalize_domains(&strs(&["-x.com"]))), "sso_domain_invalid");
        assert_eq!(code(normalize_domains(&strs(&["bücher.de"]))), "sso_domain_invalid");
        assert_eq!(code(normalize_domains(&[])), "sso_domain_required");
        let many: Vec<String> = (0..21).map(|i| format!("d{i}.example.com")).collect();
        assert_eq!(code(normalize_domains(&many)), "sso_too_many_domains");
    }

    #[test]
    fn email_domain_needs_exactly_one_at() {
        assert_eq!(email_domain(" Ana@Example.com ").as_deref(), Some("example.com"));
        assert_eq!(email_domain("a@b@example.com"), None);
        assert_eq!(email_domain("@example.com"), None);
        assert_eq!(email_domain("a@localhost"), None);
    }

    #[test]
    fn admin_can_never_be_a_mapping_target() {
        let rules = json!({"Ops": "admin"});
        let e = clean_group_rules(Some("groups"), rules.as_object().unwrap()).unwrap_err();
        assert_eq!(e.body["error_code"], "sso_group_role_invalid");
        let rules = json!({"Ops": "analyst", " Viewers ": "viewer"});
        let (attr, cleaned) = clean_group_rules(Some(" groups "), rules.as_object().unwrap()).unwrap();
        assert_eq!(attr.as_deref(), Some("groups"));
        assert_eq!(cleaned.get("Viewers"), Some(&json!("viewer")));
        // Rules without an attribute to read them from are a mistake, not a no-op.
        let e = clean_group_rules(None, rules.as_object().unwrap()).unwrap_err();
        assert_eq!(e.body["error_code"], "saml_groups_attribute_required");
        let e = clean_group_rules(Some("bad name"), &Map::new()).unwrap_err();
        assert_eq!(e.body["error_code"], "saml_groups_attribute_invalid");
        // A URN is a fine attribute name.
        assert!(clean_attribute_name(Some("http://schemas.xmlsoap.org/claims/Group"), "x").unwrap().is_some());
        assert_eq!(clean_attribute_name(Some("  "), "x").unwrap(), None);
    }

    /// The list above and the Python one must be the same set.
    #[test]
    fn free_mail_list_matches_python() {
        let src = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../backend/auth/sso/service.py"))
            .expect("backend/auth/sso/service.py is next to backend-rs");
        let start = src.find("FREE_MAIL_DOMAINS = frozenset({").expect("list in Python") + "FREE_MAIL_DOMAINS = frozenset({".len();
        let end = start + src[start..].find("})").unwrap();
        let mut py: Vec<String> = Regex::new(r#""([^"]+)""#).unwrap().captures_iter(&src[start..end])
            .map(|c| c[1].to_string()).collect();
        let mut rs: Vec<String> = FREE_MAIL_DOMAINS.iter().map(|s| s.to_string()).collect();
        py.sort();
        rs.sort();
        assert_eq!(py, rs);
        assert!(src.contains("MAX_DOMAINS = 20") && src.contains("MAX_GROUP_RULES = 100"));
        assert!(src.contains("ASSIGNABLE_ROLES = (\"analyst\", \"viewer\")"));
    }
}
