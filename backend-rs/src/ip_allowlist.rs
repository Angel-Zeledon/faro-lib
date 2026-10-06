//! Per-tenant IP allowlist: the client address, the CIDR match and the
//! refusal. The Python twin is `backend/ip_allowlist/service.py`; the two
//! implement the same rules and read the same rows (the schema is Python's).
//!
//! THE CLIENT ADDRESS IS THE WHOLE FEATURE. `X-Forwarded-For` is written by
//! the caller, so its first entry proves nothing. Proxies APPEND the address
//! they saw to the right end of the chain; only the entries the deployment's
//! own proxies wrote can be trusted, counted from the right as many as
//! `TRUSTED_PROXY_HOPS` says. Anything a client put further left is ignored.
//! With the default of 0 the header is not read at all and the socket peer is
//! the client.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

use axum::http::HeaderMap;
use serde_json::{json, Map};
use sqlx::PgPool;

use crate::activity::{record_event_with_reason, Event};
use crate::auth::RequestActors;
use crate::error::ApiError;
use crate::state::AppState;

/// One refusal row per (tenant, address) per window: a stolen key hammering the
/// API would otherwise write a row per call.
const REFUSAL_EVENT_WINDOW_MINUTES: i64 = 10;
pub const MAX_ENTRIES: i64 = 100;

/// A bare IPv4 / IPv6 literal. An IPv4-mapped IPv6 address (`::ffff:1.2.3.4`)
/// is the IPv4 address: a dual-stack listener reports IPv4 clients that way and
/// an entry says `1.2.3.4`.
pub fn parse_ip(text: &str) -> Option<IpAddr> {
    let text = text.trim();
    if text.is_empty() || text.chars().any(|c| " ,/[]%".contains(c)) {
        return None;
    }
    match text.parse::<IpAddr>().ok()? {
        IpAddr::V6(v6) => Some(match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => IpAddr::V6(v6),
        }),
        v4 => Some(v4),
    }
}

/// The caller's address: the chain is the `X-Forwarded-For` entries followed by
/// the socket peer, and the client is `hops` entries from its right end. A
/// chain shorter than that resolves to its first entry. `None` means the value
/// is not an IP at all, which an enabled allowlist treats as outside.
pub fn client_ip(forwarded_for: &str, peer: Option<IpAddr>, hops: u32) -> Option<IpAddr> {
    let mut chain: Vec<String> = if hops > 0 {
        forwarded_for.split(',').map(|e| e.trim().to_string()).filter(|e| !e.is_empty()).collect()
    } else {
        Vec::new()
    };
    chain.push(peer.map(|p| p.to_string()).unwrap_or_default());
    let index = (chain.len() - 1).saturating_sub(hops as usize);
    parse_ip(&chain[index])
}

/// The address of the request being served.
pub fn request_ip(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Option<IpAddr> {
    let forwarded = headers
        .get("x-forwarded-for")
        .map(|v| v.as_bytes().iter().map(|&b| b as char).collect::<String>())
        .unwrap_or_default();
    client_ip(&forwarded, actors.peer(), state.settings.trusted_proxy_hops)
}

/// A network, stored normalised (`network-address/prefix`).
#[derive(Debug, Clone, PartialEq)]
pub struct Cidr {
    addr: IpAddr,
    prefix: u8,
}

impl Cidr {
    /// `1.2.3.4`, `10.0.0.0/8`, `2001:db8::/32`. A bare address is a single
    /// host; host bits are masked off (`10.0.0.5/24` is `10.0.0.0/24`).
    /// IPv4-mapped IPv6 notation is refused: clients are matched in IPv4 form.
    pub fn parse(text: &str) -> Option<Cidr> {
        let text = text.trim();
        let (addr_text, prefix_text) = match text.split_once('/') {
            Some((a, p)) => (a, Some(p)),
            None => (text, None),
        };
        if addr_text.is_empty() || addr_text.chars().any(|c| " ,[]%".contains(c)) {
            return None;
        }
        let addr: IpAddr = addr_text.parse().ok()?;
        let max = if addr.is_ipv4() { 32 } else { 128 };
        if matches!(addr, IpAddr::V6(v6) if v6.to_ipv4_mapped().is_some()) {
            return None;
        }
        let prefix = match prefix_text {
            None => max,
            Some(p) => {
                if p.is_empty() || p.len() > 3 || !p.bytes().all(|b| b.is_ascii_digit()) {
                    return None;
                }
                let n: u8 = p.parse().ok()?;
                if n > max {
                    return None;
                }
                n
            }
        };
        Some(Cidr { addr: mask(addr, prefix), prefix })
    }

    pub fn contains(&self, ip: IpAddr) -> bool {
        match (self.addr, ip) {
            (IpAddr::V4(_), IpAddr::V4(_)) | (IpAddr::V6(_), IpAddr::V6(_)) => mask(ip, self.prefix) == self.addr,
            _ => false,
        }
    }
}

impl std::fmt::Display for Cidr {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}/{}", self.addr, self.prefix)
    }
}

fn mask(ip: IpAddr, prefix: u8) -> IpAddr {
    match ip {
        IpAddr::V4(v4) => {
            let m: u32 = if prefix == 0 { 0 } else { u32::MAX << (32 - u32::from(prefix)) };
            IpAddr::V4(Ipv4Addr::from(u32::from(v4) & m))
        }
        IpAddr::V6(v6) => {
            let m: u128 = if prefix == 0 { 0 } else { u128::MAX << (128 - u32::from(prefix)) };
            IpAddr::V6(Ipv6Addr::from(u128::from(v6) & m))
        }
    }
}

/// True when `ip` is inside any of the stored CIDR texts. An unreadable row
/// covers nothing (it can only narrow access).
pub fn any_covers(cidrs: &[String], ip: IpAddr) -> bool {
    cidrs.iter().filter_map(|c| Cidr::parse(c)).any(|c| c.contains(ip))
}

/// The tenant's CIDRs when its policy is ENABLED, else `None` (no filtering).
pub async fn enabled_entries(pool: &PgPool, tenant_id: &str) -> Result<Option<Vec<String>>, sqlx::Error> {
    let rows: Vec<(bool, Option<String>)> = sqlx::query_as(
        "SELECT p.enabled, e.cidr FROM ip_allowlist_policies p
         LEFT JOIN ip_allowlist_entries e ON e.tenant_id = p.tenant_id
         WHERE p.tenant_id = $1",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    match rows.first() {
        Some((true, _)) => Ok(Some(rows.into_iter().filter_map(|r| r.1).collect())),
        _ => Ok(None),
    }
}

/// Refuse (403 `ip_not_allowed`) a caller outside the tenant's enabled
/// allowlist. `actor_id` is the person, or `api_key:<id>` for an integration.
pub async fn enforce(
    state: &AppState,
    actors: &RequestActors,
    headers: &HeaderMap,
    tenant_id: &str,
    actor_id: &str,
) -> Result<(), ApiError> {
    let Some(entries) = enabled_entries(&state.pool, tenant_id).await? else { return Ok(()) };
    let ip = request_ip(state, actors, headers);
    if ip.is_some_and(|ip| any_covers(&entries, ip)) {
        return Ok(());
    }
    let ip_text = ip.map(|i| i.to_string()).unwrap_or_else(|| "unknown".into());
    record_refusal(state, tenant_id, actor_id, &ip_text).await;
    Err(ApiError::app(
        "ip_not_allowed",
        "This account only accepts access from approved IP addresses.",
        403,
        json!({"ip": ip_text}),
    ))
}

async fn record_refusal(state: &AppState, tenant_id: &str, actor_id: &str, ip_text: &str) {
    let recent: Result<Option<(i32,)>, _> = sqlx::query_as(
        "SELECT 1 FROM activity_logs WHERE tenant_id = $1 AND action = 'account.ip_access_refused'
         AND context->>'ip' = $2 AND created_at > NOW() - ($3::bigint * INTERVAL '1 minute') LIMIT 1",
    )
    .bind(tenant_id)
    .bind(ip_text)
    .bind(REFUSAL_EVENT_WINDOW_MINUTES)
    .fetch_optional(&state.pool)
    .await;
    if matches!(recent, Ok(Some(_))) {
        return;
    }
    let mut details = Map::new();
    details.insert("ip".into(), json!(ip_text));
    record_event_with_reason(
        &state.pool, tenant_id, actor_id, Event::IpAccessRefused, None, details, Some("ip_not_in_allowlist"),
    )
    .await;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    #[test]
    fn the_entry_the_trusted_proxy_wrote_is_the_client() {
        assert_eq!(client_ip("198.51.100.7", Some(ip("10.0.0.2")), 1), Some(ip("198.51.100.7")));
    }

    #[test]
    fn entries_a_client_prepended_are_ignored() {
        assert_eq!(client_ip("203.0.113.9, 198.51.100.7", Some(ip("10.0.0.2")), 1), Some(ip("198.51.100.7")));
    }

    #[test]
    fn two_hops_skip_the_inner_proxy_entry() {
        let got = client_ip("203.0.113.9, 198.51.100.7, 10.1.1.1", Some(ip("10.0.0.2")), 2);
        assert_eq!(got, Some(ip("198.51.100.7")));
    }

    #[test]
    fn zero_hops_ignores_the_header_entirely() {
        assert_eq!(client_ip("203.0.113.9", Some(ip("198.51.100.7")), 0), Some(ip("198.51.100.7")));
    }

    #[test]
    fn a_chain_shorter_than_the_hops_falls_back_to_its_first_entry() {
        assert_eq!(client_ip("", Some(ip("198.51.100.7")), 3), Some(ip("198.51.100.7")));
    }

    #[test]
    fn ipv4_mapped_ipv6_is_the_ipv4_address() {
        assert_eq!(client_ip("::ffff:203.0.113.5", Some(ip("10.0.0.2")), 1), Some(ip("203.0.113.5")));
    }

    #[test]
    fn garbage_and_missing_peers_are_no_address() {
        assert_eq!(client_ip("not-an-ip", Some(ip("10.0.0.2")), 1), None);
        assert_eq!(client_ip("203.0.113.5:8080", Some(ip("10.0.0.2")), 1), None);
        assert_eq!(client_ip("", None, 0), None);
    }

    #[test]
    fn cidr_normalises_and_matches() {
        assert_eq!(Cidr::parse("10.0.0.5/24").unwrap().to_string(), "10.0.0.0/24");
        assert_eq!(Cidr::parse("203.0.113.9").unwrap().to_string(), "203.0.113.9/32");
        assert_eq!(Cidr::parse(" 2001:DB8::1/32 ").unwrap().to_string(), "2001:db8::/32");
        let office = Cidr::parse("203.0.113.0/24").unwrap();
        assert!(office.contains(ip("203.0.113.255")));
        assert!(!office.contains(ip("203.0.114.0")));
        assert!(!office.contains(ip("2001:db8::1")), "families never match each other");
        assert!(Cidr::parse("0.0.0.0/0").unwrap().contains(ip("198.51.100.7")));
        assert!(Cidr::parse("2001:db8::/32").unwrap().contains(ip("2001:db8:1::5")));
        assert!(!Cidr::parse("2001:db8::/32").unwrap().contains(ip("2001:db9::5")));
    }

    #[test]
    fn cidr_refuses_what_is_not_a_network() {
        for bad in [
            "", "/24", "10.0.0.0/", "10.0.0.0/33", "10.0.0.0/-1", "10.0.0.0/+8", "10.0.0.0/255.0.0.0",
            "10.0.0.0/24/8", "10.0.0", "1.2.3.4.5", "256.1.1.1", "::ffff:1.2.3.4", "::ffff:1.2.3.0/120",
            "2001:db8::/129", "fe80::1%eth0", "[::1]", "a, b", "example.com", "01.2.3.4",
        ] {
            assert!(Cidr::parse(bad).is_none(), "{bad:?} must be refused");
        }
    }

    #[test]
    fn an_unreadable_row_covers_nothing() {
        let rows = vec!["garbage".to_string(), "203.0.113.0/24".to_string()];
        assert!(any_covers(&rows, ip("203.0.113.1")));
        assert!(!any_covers(&["garbage".to_string()], ip("203.0.113.1")));
        assert!(!any_covers(&[], ip("203.0.113.1")));
    }
}
