//! Custom-role permissions: `backend/auth/permissions.py`, the other half.
//!
//! One catalogue, two languages. `permissions.json` here is byte-identical to
//! `backend/auth/permissions.json` (`backend/tests/test_permission_catalogue_
//! parity.py` fails when they differ), and both implementations run the same
//! `probes` table in their tests, so a route cannot need one permission on the
//! Python side and another on the Rust side.
//!
//! A user with no custom role is never touched: the built-in role is the only
//! gate, as before. A user WITH one is checked, on every request, against the
//! permission the route needs, read from the database right now (no cache, so
//! a permission removed takes effect on the next request). Fail closed: a role
//! id that points at nothing, a permission name outside the catalogue, a
//! mutating route no rule covers, or a row that cannot be read all answer 403.
//! API keys never come through here (they keep their read / write scope).

use std::collections::BTreeSet;
use std::sync::OnceLock;

use serde_json::{json, Value};
use sqlx::PgPool;

use crate::error::ApiError;

const CATALOGUE_JSON: &str = include_str!("permissions.json");
const API_PREFIX: &str = "/api/v1";
pub const OPEN: &str = "open";
pub const NONE: &str = "none";

#[derive(Debug)]
struct Rule {
    write: bool,
    prefix: Vec<String>,
    permission: String,
}

#[derive(Debug)]
pub struct Catalogue {
    pub permissions: Vec<(String, String)>,
    rules: Vec<Rule>,
    /// Read by the unit tests; the Python parity test runs the same table.
    #[allow(dead_code)]
    pub probes: Vec<(String, String, String)>,
}

pub fn catalogue() -> &'static Catalogue {
    static C: OnceLock<Catalogue> = OnceLock::new();
    C.get_or_init(|| parse(CATALOGUE_JSON).expect("permissions.json is embedded at build time and tested"))
}

fn parse(src: &str) -> Result<Catalogue, String> {
    let v: Value = serde_json::from_str(src).map_err(|e| e.to_string())?;
    let s = |x: &Value, k: &str| -> Result<String, String> {
        x.get(k).and_then(Value::as_str).map(str::to_string).ok_or_else(|| format!("missing {k}"))
    };
    let mut permissions = Vec::new();
    for p in v["permissions"].as_array().ok_or("permissions")? {
        permissions.push((s(p, "name")?, s(p, "description")?));
    }
    let names: BTreeSet<&str> = permissions.iter().map(|(n, _)| n.as_str()).collect();
    let mut rules = Vec::new();
    for r in v["rules"].as_array().ok_or("rules")? {
        let class = s(r, "class")?;
        let permission = s(r, "permission")?;
        if permission != OPEN && !names.contains(permission.as_str()) {
            return Err(format!("rule names unknown permission {permission}"));
        }
        let write = match class.as_str() {
            "write" => true,
            "read" => false,
            other => return Err(format!("unknown rule class {other}")),
        };
        let prefix = segments(&s(r, "prefix")?);
        if rules.iter().any(|x: &Rule| x.write == write && x.prefix == prefix) {
            return Err(format!("duplicate rule {prefix:?}"));
        }
        rules.push(Rule { write, prefix, permission });
    }
    let mut probes = Vec::new();
    for p in v["probes"].as_array().ok_or("probes")? {
        let a = p.as_array().ok_or("probe")?;
        let g = |i: usize| a.get(i).and_then(Value::as_str).map(str::to_string).ok_or("probe field");
        probes.push((g(0)?, g(1)?, g(2)?));
    }
    Ok(Catalogue { permissions, rules, probes })
}

/// `permissions.segments`: the API prefix goes, every `{param}` becomes `{}`.
pub fn segments(path: &str) -> Vec<String> {
    let mut p = path.split('?').next().unwrap_or("");
    if let Some(rest) = p.strip_prefix(API_PREFIX) {
        p = rest;
    }
    p.split('/')
        .filter(|s| !s.is_empty())
        .map(|s| if s.starts_with('{') && s.ends_with('}') { "{}".to_string() } else { s.to_string() })
        .collect()
}

pub fn is_write(method: &str) -> bool {
    !matches!(method.to_ascii_uppercase().as_str(), "GET" | "HEAD" | "OPTIONS")
}

/// `permissions.requirement`: a catalogue name, `open`, or `none`.
pub fn requirement(method: &str, path: &str) -> String {
    let seg = segments(path);
    let write = is_write(method);
    let mut best: Option<(usize, &str)> = None;
    for r in &catalogue().rules {
        if r.write == write && r.prefix.len() <= seg.len() && seg[..r.prefix.len()] == r.prefix[..] {
            if best.map(|b| r.prefix.len() > b.0).unwrap_or(true) {
                best = Some((r.prefix.len(), &r.permission));
            }
        }
    }
    best.map(|b| b.1.to_string()).unwrap_or_else(|| NONE.to_string())
}

pub fn is_known(permission: &str) -> bool {
    catalogue().permissions.iter().any(|(n, _)| n == permission)
}

pub fn all_names() -> Vec<String> {
    catalogue().permissions.iter().map(|(n, _)| n.clone()).collect()
}

/// Route layer: remember which route template (and verb) matched, so the
/// guard can look up the permission it needs. The Python app reads the same
/// two facts from the ASGI scope.
pub async fn record_route(
    req: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    if let (Some(mp), Some(actors)) = (
        req.extensions().get::<axum::extract::MatchedPath>(),
        req.extensions().get::<crate::auth::RequestActors>(),
    ) {
        actors.set_route(req.method().as_str(), mp.as_str());
    }
    next.run(req).await
}

/// What a user may do right now.
#[derive(Debug, Clone, PartialEq)]
pub struct Effective {
    pub restricted: bool,
    pub permissions: BTreeSet<String>,
    pub role_id: Option<String>,
}

impl Effective {
    pub fn unrestricted() -> Self {
        Effective { restricted: false, permissions: BTreeSet::new(), role_id: None }
    }

    pub fn allows(&self, permission: &str) -> bool {
        !self.restricted || (is_known(permission) && self.permissions.contains(permission))
    }

    /// The set this user could grant to somebody else: everything when
    /// unrestricted, otherwise their own set.
    pub fn grantable(&self) -> BTreeSet<String> {
        if self.restricted { self.permissions.clone() } else { all_names().into_iter().collect() }
    }
}

/// The user's CURRENT custom role, read from the database right now.
pub async fn effective_for(pool: &PgPool, tenant_id: &str, user_id: &str) -> Result<Effective, sqlx::Error> {
    let row: Option<(Option<String>, Option<Vec<String>>)> = sqlx::query_as(
        "SELECT u.custom_role_id, r.permissions
           FROM users u
           LEFT JOIN custom_roles r ON r.id = u.custom_role_id AND r.tenant_id = u.tenant_id
          WHERE u.id = $1 AND u.tenant_id = $2",
    )
    .bind(user_id)
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    Ok(match row {
        Some((Some(role_id), perms)) => Effective {
            restricted: true,
            permissions: perms.unwrap_or_default().into_iter().filter(|p| is_known(p)).collect(),
            role_id: Some(role_id),
        },
        _ => Effective::unrestricted(),
    })
}

pub fn denied(permission: &str) -> ApiError {
    ApiError::app(
        "permission_denied",
        format!("Your custom role does not include the permission '{permission}'."),
        403,
        json!({"permission": permission}),
    )
}

/// The decision, separated from the database read so it is unit-testable.
pub fn decide(method: &str, path: &str, eff: &Effective) -> Result<(), ApiError> {
    let need = requirement(method, path);
    if need == OPEN || !eff.restricted {
        return Ok(());
    }
    if need == NONE {
        return if is_write(method) { Err(denied("unclassified_route")) } else { Ok(()) };
    }
    if eff.allows(&need) { Ok(()) } else { Err(denied(&need)) }
}

/// Called by `auth::current_user` for a PERSON, after the token is valid.
pub async fn enforce(
    pool: &PgPool,
    route: Option<(String, String)>,
    tenant_id: &str,
    user_id: &str,
) -> Result<(), ApiError> {
    if let Some((method, path)) = &route {
        let need = requirement(method, path);
        if need == OPEN || (need == NONE && !is_write(method)) {
            return Ok(());
        }
    }
    let eff = effective_for(pool, tenant_id, user_id).await.map_err(|e| {
        tracing::error!(error = %e, user = user_id, "[permissions] could not read the role");
        ApiError::app(
            "permission_check_failed",
            "Permissions could not be verified, so the request was refused.",
            403,
            json!({}),
        )
    })?;
    if !eff.restricted {
        return Ok(());
    }
    match route {
        // No route recorded: a restricted user is refused rather than guessed at.
        None => Err(denied("unclassified_route")),
        Some((method, path)) => decide(&method, &path, &eff),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn eff(perms: &[&str]) -> Effective {
        Effective {
            restricted: true,
            permissions: perms.iter().map(|s| s.to_string()).collect(),
            role_id: Some("r1".into()),
        }
    }

    #[test]
    fn the_embedded_catalogue_parses_and_is_consistent() {
        let c = catalogue();
        assert_eq!(c.permissions.len(), 17);
        let names: BTreeSet<_> = c.permissions.iter().map(|(n, _)| n.clone()).collect();
        assert_eq!(names.len(), 17, "permission names are unique");
        assert!(c.rules.len() > 80);
    }

    #[test]
    fn every_probe_resolves_like_python() {
        // The same probes table runs in backend/tests/test_permission_catalogue_parity.py.
        for (method, path, expected) in &catalogue().probes {
            assert_eq!(&requirement(method, path), expected, "{method} {path}");
        }
    }

    #[test]
    fn param_names_and_the_api_prefix_do_not_matter() {
        assert_eq!(requirement("POST", "/api/v1/sessions/{id}/train"), "run_training");
        assert_eq!(requirement("POST", "/sessions/{session_id}/train"), "run_training");
        assert_eq!(segments("/api/v1/a/{x:path}/b"), ["a", "{}", "b"]);
    }

    #[test]
    fn a_prefix_matches_whole_segments_only() {
        assert_eq!(requirement("POST", "/sessions-archive"), NONE);
        assert_eq!(requirement("POST", "/sessionsx/1"), NONE);
    }

    #[test]
    fn unrestricted_users_pass_everything() {
        let free = Effective::unrestricted();
        assert!(decide("POST", "/brand-new/thing", &free).is_ok());
        assert!(decide("DELETE", "/roles/{id}", &free).is_ok());
    }

    #[test]
    fn restricted_users_fail_closed() {
        let e = eff(&["view_inventory"]);
        assert!(decide("GET", "/inventory/stock", &e).is_ok());
        let d = decide("POST", "/inventory/stock", &e).unwrap_err();
        assert_eq!(d.code(), Some("permission_denied"));
        assert_eq!(d.body["error_params"]["permission"], "manage_inventory");
        // A mutating route nobody classified.
        let d = decide("POST", "/brand-new/thing", &e).unwrap_err();
        assert_eq!(d.body["error_params"]["permission"], "unclassified_route");
        // An unclassified read stays readable; open routes always pass.
        assert!(decide("GET", "/models", &e).is_ok());
        assert!(decide("PATCH", "/users/me", &e).is_ok());
        // Not in the catalogue: never granted, whatever the row says.
        assert!(!eff(&["root"]).allows("root"));
    }

    #[test]
    fn an_empty_role_denies_every_permissioned_route() {
        let e = eff(&[]);
        assert!(decide("GET", "/sessions", &e).is_err());
        assert!(decide("POST", "/sessions", &e).is_err());
        assert!(decide("GET", "/roles/me", &e).is_ok());
    }

    #[test]
    fn grantable_is_the_whole_catalogue_only_when_unrestricted() {
        assert_eq!(Effective::unrestricted().grantable().len(), 17);
        assert_eq!(eff(&["view_users"]).grantable().len(), 1);
    }
}
