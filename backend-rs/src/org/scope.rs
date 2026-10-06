//! The organization hierarchy's single decision point: WHICH tenants may this
//! caller read, derived on the server, from the database, on every request.
//!
//! The model (schema and rules in `backend/organizations/__init__.py`):
//! a parent tenant links subsidiaries through a two-sided handshake
//! (`org_links`), and each parent user needs an explicit `org_link_grants` row
//! for each subsidiary. Nothing else opens a tenant. This module turns that
//! into a list of tenant ids, and every consolidated query binds exactly that
//! list (`tenant_id = ANY($1)`), never anything the request sent.
//!
//! Fail closed, in the order [`resolve`] runs it:
//!
//! 1. A machine caller (an `sk_live_*` key, MCP) never resolves, even if a route
//!    forgot to be internal: refused again here.
//! 2. The person is read from the database NOW: unknown, or any status but
//!    `active`, resolves to "not entitled". A 15-minute token for a person who
//!    was suspended a minute ago gets nothing.
//! 3. A user limited to some warehouses is refused (company-wide totals).
//! 4. The caller's own tenant must be active.
//! 5. A subsidiary appears only through a grant of THIS user on an `active`
//!    link whose parent is the caller's tenant and whose grantee is an active
//!    person of that same tenant; a subsidiary that is itself a parent, or a
//!    parent that is itself a subsidiary, is excluded (one level only).
//! 6. A granted subsidiary that is not `active` is not read, and is reported as
//!    `unavailable` so totals are never silently short of it.
//!
//! An erased subsidiary has no tenant row, so its link cascades away and it is
//! simply not there; whole-tenant erasure tells the holding in its activity
//! feed (`backend/organizations/service.py`).

use axum::http::HeaderMap;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use sqlx::PgPool;

use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::routes::r1::currency::effective_code;
use crate::state::AppState;

/// Every organization route is internal: no API key and no MCP client ever
/// reaches a tenant other than the one that minted the key.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal(
        "internal tag 'organization': subsidiary links and consolidated views belong to a signed-in person; an API key or MCP client never reaches beyond its own tenant",
    ),
    is_mcp: false,
};

pub const CODE_PREFIX: &str = "orgl_";
/// 24 random bytes as hex.
const CODE_HEX_LEN: usize = 48;
pub const CODE_TTL_DAYS: i64 = 7;
pub const MAX_LABEL_CHARS: usize = 80;
/// Most subsidiaries one person can read through grants (the SQL LIMIT is one
/// more, so a list that hits it is detected, not truncated silently).
pub const MAX_SUBSIDIARIES: usize = 200;
pub const MAX_PENDING_LINKS: i64 = 20;
pub const MAX_NARROW_IDS: usize = 200;
const MAX_ID_CHARS: usize = 100;

/// One tenant a consolidated read covers.
#[derive(Debug, Clone, PartialEq)]
pub struct Member {
    pub tenant_id: String,
    pub name: String,
    /// The holding's own name for the subsidiary; `None` for the caller's tenant.
    pub label: Option<String>,
    pub link_id: Option<String>,
    pub is_own: bool,
    pub currency: String,
}

impl Member {
    /// What the screens call it: the holding's label, else the tenant name.
    pub fn display(&self) -> &str {
        self.label.as_deref().filter(|l| !l.is_empty()).unwrap_or(&self.name)
    }

    pub fn to_json(&self) -> Value {
        json!({
            "tenant_id": self.tenant_id,
            "name": self.name,
            "label": self.label,
            "link_id": self.link_id,
            "is_own": self.is_own,
            "currency": self.currency,
        })
    }
}

/// A subsidiary the caller holds a grant for but that cannot be read now.
#[derive(Debug, Clone, PartialEq)]
pub struct Unavailable {
    pub link_id: String,
    pub label: String,
    pub reason: &'static str,
}

#[derive(Debug, Clone)]
pub struct OrgScope {
    /// The caller's own tenant first, then the subsidiaries in link order.
    pub members: Vec<Member>,
    pub unavailable: Vec<Unavailable>,
}

impl OrgScope {
    /// The ONLY list of tenant ids a consolidated query may bind.
    pub fn tenant_ids(&self) -> Vec<String> {
        self.members.iter().map(|m| m.tenant_id.clone()).collect()
    }

    pub fn unavailable_json(&self) -> Value {
        Value::Array(
            self.unavailable
                .iter()
                .map(|u| json!({"link_id": u.link_id, "label": u.label, "reason": u.reason}))
                .collect(),
        )
    }

    /// `?tenant_ids=a,b`: a request may only NARROW what it is entitled to.
    /// Every id is looked up in the entitled members and the member is taken
    /// from there; an id that is not entitled is refused with the same
    /// `org_tenant_not_found` whether or not such a tenant exists, so the
    /// parameter cannot be used to probe for tenants.
    pub fn narrowed_to(self, requested: &[String]) -> Result<OrgScope, ApiError> {
        if requested.is_empty() {
            return Ok(self);
        }
        let members = narrow_members(&self.members, requested).map_err(|_| tenant_not_found())?;
        Ok(OrgScope { members, unavailable: Vec::new() })
    }
}

/// Pure half of [`OrgScope::narrowed_to`]: the entitled members that were
/// asked for, in entitlement order, or `Err(())` when ANY requested id is not
/// entitled. Nothing is ever copied from the request.
pub fn narrow_members(entitled: &[Member], requested: &[String]) -> Result<Vec<Member>, ()> {
    if requested.iter().any(|r| !entitled.iter().any(|m| &m.tenant_id == r)) {
        return Err(());
    }
    Ok(entitled.iter().filter(|m| requested.iter().any(|r| r == &m.tenant_id)).cloned().collect())
}

/// `tenant_ids=a,b` parsed: trimmed, blanks dropped, bounded. `None` for an
/// absent or blank parameter.
pub fn parse_requested(raw: Option<&str>) -> Result<Vec<String>, ApiError> {
    let Some(raw) = raw else { return Ok(Vec::new()) };
    let ids: Vec<String> = raw.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
    if ids.len() > MAX_NARROW_IDS || ids.iter().any(|s| s.chars().count() > MAX_ID_CHARS) {
        // Same refusal as an unknown id: an oversized list cannot match anything.
        return Err(tenant_not_found());
    }
    Ok(ids)
}

// ── Errors ───────────────────────────────────────────────────────────────────

pub fn not_entitled() -> ApiError {
    ApiError::app(
        "org_not_entitled",
        "You have not been given access to any subsidiary",
        403,
        json!({}),
    )
}

pub fn tenant_not_found() -> ApiError {
    ApiError::app("org_tenant_not_found", "Subsidiary not found", 404, json!({}))
}

fn machine_refused() -> ApiError {
    ApiError::app(
        "api_key_route_not_exposed",
        "This endpoint cannot be called with an API key.",
        403,
        json!({"reason": "internal tag 'organization'"}),
    )
}

// ── Link codes ───────────────────────────────────────────────────────────────

/// `orgl_` + 48 hex characters from the OS (24 random bytes).
pub fn new_code() -> Result<String, ApiError> {
    let mut buf = [0u8; CODE_HEX_LEN / 2];
    getrandom::getrandom(&mut buf).map_err(|e| {
        tracing::error!(error = %e, "OS randomness unavailable");
        ApiError::internal()
    })?;
    Ok(format!("{CODE_PREFIX}{}", hex::encode(buf)))
}

/// The only form of a code the database ever holds: SHA-256 hex.
pub fn hash_code(raw: &str) -> String {
    hex::encode(Sha256::digest(raw.as_bytes()))
}

/// Cheap shape check before any lookup, so a malformed string never reaches SQL.
pub fn looks_like_code(raw: &str) -> bool {
    match raw.strip_prefix(CODE_PREFIX) {
        Some(rest) => rest.len() == CODE_HEX_LEN && rest.bytes().all(|b| b.is_ascii_hexdigit()),
        None => false,
    }
}

// ── The live person ──────────────────────────────────────────────────────────

pub struct LivePerson {
    pub role: String,
    pub status: String,
}

/// The person as the database says NOW; the JWT only names them.
pub async fn live_person(pool: &PgPool, user: &CurrentUser) -> Result<Option<LivePerson>, ApiError> {
    let row: Option<(String, String)> =
        sqlx::query_as("SELECT role, status FROM users WHERE id = $1 AND tenant_id = $2")
            .bind(&user.user_id)
            .bind(&user.tenant_id)
            .fetch_optional(pool)
            .await?;
    Ok(row.map(|(role, status)| LivePerson { role, status }))
}

/// Authenticate for a management route: a signed-in person (never a key), an
/// administrator in the JWT AND in the database right now, still active.
pub async fn admin(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
) -> Result<(CurrentUser, LivePerson), ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    if user.is_machine() {
        return Err(machine_refused());
    }
    auth::require_role(&user, &["admin"])?;
    let Some(person) = live_person(&state.pool, &user).await? else { return Err(not_entitled()) };
    if person.status != "active" {
        return Err(not_entitled());
    }
    // A demotion made after the token was issued still takes effect here.
    let live = CurrentUser { role: person.role.clone(), ..user.clone() };
    auth::require_role(&live, &["admin"])?;
    Ok((user, person))
}

// ── Resolving the scope ──────────────────────────────────────────────────────

/// The grant query. `$1` is the caller's user id, `$2` the caller's tenant id,
/// both from the verified identity; there is no request value in it.
pub const GRANTS_SQL: &str = "\
SELECT l.id, l.child_tenant_id, l.label, t.name, t.status, t.settings->>'currency'
  FROM org_link_grants g
  JOIN org_links l ON l.id = g.link_id
  JOIN users u ON u.id = g.user_id AND u.tenant_id = l.parent_tenant_id AND u.status = 'active'
  JOIN tenants t ON t.id = l.child_tenant_id
 WHERE g.user_id = $1
   AND l.parent_tenant_id = $2
   AND l.status = 'active'
   AND l.child_tenant_id IS NOT NULL
   AND l.child_tenant_id <> l.parent_tenant_id
   AND NOT EXISTS (SELECT 1 FROM org_links up
                    WHERE up.child_tenant_id = l.parent_tenant_id AND up.status = 'active')
   AND NOT EXISTS (SELECT 1 FROM org_links dn
                    WHERE dn.parent_tenant_id = l.child_tenant_id AND dn.status = 'active')
 ORDER BY l.created_at, l.id
 LIMIT 201";

pub async fn resolve(state: &AppState, user: &CurrentUser) -> Result<OrgScope, ApiError> {
    if user.is_machine() {
        return Err(machine_refused());
    }
    let pool = &state.pool;
    match live_person(pool, user).await? {
        Some(p) if p.status == "active" => {}
        _ => return Err(not_entitled()),
    }
    wscope::require_company_wide(pool, user).await?;

    let own: Option<(String, String, Option<String>)> =
        sqlx::query_as("SELECT name, status, settings->>'currency' FROM tenants WHERE id = $1")
            .bind(&user.tenant_id)
            .fetch_optional(pool)
            .await?;
    let Some((own_name, own_status, own_currency)) = own else { return Err(not_entitled()) };
    if own_status != "active" {
        return Err(not_entitled());
    }

    type GrantRow = (String, String, String, String, String, Option<String>);
    let rows: Vec<GrantRow> = sqlx::query_as(GRANTS_SQL)
        .bind(&user.user_id)
        .bind(&user.tenant_id)
        .fetch_all(pool)
        .await?;
    if rows.is_empty() {
        return Err(not_entitled());
    }
    if rows.len() > MAX_SUBSIDIARIES {
        // Fail closed rather than answer with a silently truncated holding.
        tracing::error!(tenant = %user.tenant_id, "more subsidiaries granted than one view can cover");
        return Err(ApiError::app(
            "org_too_many_subsidiaries",
            "Too many subsidiaries to consolidate in one view",
            409,
            json!({"max": MAX_SUBSIDIARIES}),
        ));
    }
    Ok(build_scope(
        Member {
            tenant_id: user.tenant_id.clone(),
            name: own_name,
            label: None,
            link_id: None,
            is_own: true,
            currency: effective_code(own_currency.as_deref()),
        },
        rows,
    ))
}

/// Pure half of [`resolve`]: the caller's own tenant first, each granted
/// subsidiary that is active as a member, the rest as `unavailable`. A tenant
/// id that repeats (it cannot, but a stray row must not double a total) is
/// taken once.
pub fn build_scope(
    own: Member,
    rows: Vec<(String, String, String, String, String, Option<String>)>,
) -> OrgScope {
    let mut members = vec![own];
    let mut unavailable = Vec::new();
    for (link_id, child_id, label, name, status, currency) in rows {
        if members.iter().any(|m| m.tenant_id == child_id) {
            continue;
        }
        if status == "active" {
            members.push(Member {
                tenant_id: child_id,
                name,
                label: Some(label),
                link_id: Some(link_id),
                is_own: false,
                currency: effective_code(currency.as_deref()),
            });
        } else {
            unavailable.push(Unavailable { link_id, label, reason: "tenant_inactive" });
        }
    }
    OrgScope { members, unavailable }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn member(id: &str, own: bool) -> Member {
        Member {
            tenant_id: id.into(),
            name: format!("name-{id}"),
            label: if own { None } else { Some(format!("label-{id}")) },
            link_id: if own { None } else { Some(format!("link-{id}")) },
            is_own: own,
            currency: "CRC".into(),
        }
    }

    fn grant(link: &str, child: &str, status: &str) -> (String, String, String, String, String, Option<String>) {
        (link.into(), child.into(), format!("label-{child}"), format!("name-{child}"), status.into(), None)
    }

    #[test]
    fn codes_have_one_shape_and_hash_to_sha256() {
        let code = new_code().unwrap();
        assert!(looks_like_code(&code), "{code}");
        assert_eq!(code.len(), CODE_PREFIX.len() + 48);
        assert_ne!(code, new_code().unwrap());
        let h = hash_code(&code);
        assert_eq!(h.len(), 64);
        assert!(!h.contains(&code));
        // SHA-256 of "abc"
        assert_eq!(hash_code("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn malformed_codes_never_reach_the_database() {
        for bad in ["", "orgl_", "orgl_zz", "sk_live_abc", "ORGL_00", &format!("orgl_{}", "g".repeat(48)),
            &format!("orgl_{}", "a".repeat(47)), &format!("orgl_{}", "a".repeat(49)),
            &format!(" orgl_{}", "a".repeat(48))]
        {
            assert!(!looks_like_code(bad), "{bad:?}");
        }
        assert!(looks_like_code(&format!("orgl_{}", "aB09".repeat(12))));
    }

    #[test]
    fn scope_lists_own_first_and_every_active_subsidiary_once() {
        let s = build_scope(
            member("P", true),
            vec![grant("l1", "C1", "active"), grant("l2", "C2", "active"), grant("l1b", "C1", "active")],
        );
        assert_eq!(s.tenant_ids(), vec!["P", "C1", "C2"]);
        assert!(s.members[0].is_own && !s.members[1].is_own);
        assert!(s.unavailable.is_empty());
    }

    #[test]
    fn a_suspended_subsidiary_is_reported_not_read_and_not_hidden() {
        let s = build_scope(member("P", true), vec![grant("l1", "C1", "suspended"), grant("l2", "C2", "active")]);
        assert_eq!(s.tenant_ids(), vec!["P", "C2"]);
        assert_eq!(s.unavailable, vec![Unavailable { link_id: "l1".into(), label: "label-C1".into(), reason: "tenant_inactive" }]);
    }

    #[test]
    fn narrowing_only_selects_from_the_entitled_list() {
        let entitled = vec![member("P", true), member("C1", false), member("C2", false)];
        let got = narrow_members(&entitled, &["C2".into()]).unwrap();
        assert_eq!(got.iter().map(|m| m.tenant_id.as_str()).collect::<Vec<_>>(), vec!["C2"]);
        // Entitlement order wins over request order.
        let got = narrow_members(&entitled, &["C2".into(), "P".into()]).unwrap();
        assert_eq!(got.iter().map(|m| m.tenant_id.as_str()).collect::<Vec<_>>(), vec!["P", "C2"]);
        // One foreign id poisons the whole request: no partial answer.
        assert!(narrow_members(&entitled, &["C1".into(), "SOMEONE-ELSE".into()]).is_err());
        // Case and whitespace tricks do not match.
        assert!(narrow_members(&entitled, &["c1".into()]).is_err());
        assert!(narrow_members(&entitled, &["C1 ".into()]).is_err());
        assert!(narrow_members(&entitled, &["C1' OR '1'='1".into()]).is_err());
    }

    #[test]
    fn narrowing_cannot_widen_and_hides_whether_the_tenant_exists() {
        let s = build_scope(member("P", true), vec![grant("l1", "C1", "active")]);
        let e = s.clone().narrowed_to(&["NOT-MINE".into()]).unwrap_err();
        assert_eq!(e.status.as_u16(), 404);
        assert_eq!(e.code(), Some("org_tenant_not_found"));
        // An empty request means "everything I am entitled to", nothing more.
        let all = s.clone().narrowed_to(&[]).unwrap();
        assert_eq!(all.tenant_ids(), vec!["P", "C1"]);
        let one = s.narrowed_to(&["C1".into()]).unwrap();
        assert_eq!(one.tenant_ids(), vec!["C1"]);
        assert!(one.unavailable.is_empty());
    }

    #[test]
    fn requested_ids_are_trimmed_bounded_and_blank_means_none() {
        assert!(parse_requested(None).unwrap().is_empty());
        assert!(parse_requested(Some("")).unwrap().is_empty());
        assert!(parse_requested(Some(" , ,")).unwrap().is_empty());
        assert_eq!(parse_requested(Some("a, b ,c")).unwrap(), vec!["a", "b", "c"]);
        let long = "x".repeat(MAX_ID_CHARS + 1);
        assert_eq!(parse_requested(Some(&long)).unwrap_err().code(), Some("org_tenant_not_found"));
        let many = vec!["a"; MAX_NARROW_IDS + 1].join(",");
        assert!(parse_requested(Some(&many)).is_err());
    }

    #[test]
    fn the_grant_query_binds_only_identity_and_demands_every_condition() {
        let sql = GRANTS_SQL;
        // Only two placeholders: the caller's user and tenant. A request value
        // has no way into this statement.
        assert!(sql.contains("$1") && sql.contains("$2") && !sql.contains("$3"));
        for needle in [
            "g.user_id = $1",
            "l.parent_tenant_id = $2",
            "l.status = 'active'",
            "u.status = 'active'",
            "u.tenant_id = l.parent_tenant_id",
            "l.child_tenant_id <> l.parent_tenant_id",
            "up.child_tenant_id = l.parent_tenant_id",
            "dn.parent_tenant_id = l.child_tenant_id",
        ] {
            assert!(sql.contains(needle), "grant query lost a condition: {needle}");
        }
    }

    #[test]
    fn route_is_internal_so_no_key_can_call_it() {
        assert!(matches!(ROUTE.exposure, Exposure::Internal(_)));
        assert!(!ROUTE.is_mcp);
    }

    #[test]
    fn member_display_prefers_the_holdings_label() {
        let mut m = member("C1", false);
        assert_eq!(m.display(), "label-C1");
        m.label = Some(String::new());
        assert_eq!(m.display(), "name-C1");
        assert_eq!(member("P", true).display(), "name-P");
    }
}
