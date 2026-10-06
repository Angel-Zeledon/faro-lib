//! Save-time check of an outbound destination URL: the Rust half of the SSRF
//! guard that `backend/datasources/network.py` + `backend/webhooks/service.py`
//! apply in Python.
//!
//! This is the FIRST of two checks and deliberately the stricter-or-equal one.
//! The authoritative one runs in the Python delivery loop on every delivery:
//! it resolves the host again, refuses a private / metadata address and then
//! connects to the checked address, so a DNS answer that changes after this
//! check cannot redirect a delivery. This check exists so an admin is told at
//! save time instead of finding a dead destination later.
//!
//! The classification is conservative on purpose: anything that is not clearly
//! a public unicast address is refused unless the installation allows private
//! hosts (`SQL_SOURCES_ALLOW_PRIVATE_HOSTS`, the same switch as Python), and
//! link-local, cloud-metadata, multicast, unspecified, broadcast and reserved
//! addresses are refused whatever the switch says.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::time::Duration;

use serde_json::json;

use crate::error::ApiError;

pub const MAX_URL_LENGTH: usize = 2048;
const DNS_TIMEOUT: Duration = Duration::from_secs(5);
pub const ALLOW_PRIVATE_SETTING: &str = "SQL_SOURCES_ALLOW_PRIVATE_HOSTS";

#[derive(Debug, PartialEq, Eq)]
pub enum Verdict {
    Public,
    /// Refused unless the installation allows private networks.
    Private(&'static str),
    /// Refused always.
    Forbidden(&'static str),
}

fn v4_in(ip: Ipv4Addr, base: [u8; 4], prefix: u32) -> bool {
    let mask = if prefix == 0 { 0 } else { u32::MAX << (32 - prefix) };
    (u32::from(ip) & mask) == (u32::from(Ipv4Addr::from(base)) & mask)
}

fn classify_v4(ip: Ipv4Addr) -> Verdict {
    if ip == Ipv4Addr::new(100, 100, 100, 200) || ip == Ipv4Addr::new(168, 63, 129, 16) {
        return Verdict::Forbidden("metadata");
    }
    if ip.is_link_local() {
        return Verdict::Forbidden("link_local");
    }
    if ip.is_multicast() {
        return Verdict::Forbidden("multicast");
    }
    if ip.is_unspecified() {
        return Verdict::Forbidden("unspecified");
    }
    if ip.is_broadcast() || v4_in(ip, [240, 0, 0, 0], 4) {
        return Verdict::Forbidden("reserved");
    }
    if ip.is_loopback() {
        return Verdict::Private("loopback");
    }
    let private_ranges: [([u8; 4], u32); 9] = [
        ([0, 0, 0, 0], 8),
        ([10, 0, 0, 0], 8),
        ([100, 64, 0, 0], 10),
        ([172, 16, 0, 0], 12),
        ([192, 0, 0, 0], 24),
        ([192, 0, 2, 0], 24),
        ([192, 168, 0, 0], 16),
        ([198, 18, 0, 0], 15),
        ([198, 51, 100, 0], 24),
    ];
    if private_ranges.iter().any(|(b, p)| v4_in(ip, *b, *p)) || v4_in(ip, [203, 0, 113, 0], 24) {
        return Verdict::Private("private");
    }
    Verdict::Public
}

fn classify_v6(ip: Ipv6Addr) -> Verdict {
    if let Some(v4) = ip.to_ipv4_mapped() {
        return classify_v4(v4);
    }
    let seg = ip.segments();
    // fd00:ec2::254 (AWS IMDS over IPv6)
    if ip == Ipv6Addr::new(0xfd00, 0x0ec2, 0, 0, 0, 0, 0, 0x0254) {
        return Verdict::Forbidden("metadata");
    }
    // NAT64 64:ff9b::/96 and 6to4 2002::/16 carry an IPv4 address: judge that.
    if seg[0] == 0x0064 && seg[1] == 0xff9b && seg[2..6].iter().all(|s| *s == 0) {
        let [a, b] = seg[6].to_be_bytes();
        let [c, d] = seg[7].to_be_bytes();
        return classify_v4(Ipv4Addr::new(a, b, c, d));
    }
    if seg[0] == 0x2002 {
        let [a, b] = seg[1].to_be_bytes();
        let [c, d] = seg[2].to_be_bytes();
        return classify_v4(Ipv4Addr::new(a, b, c, d));
    }
    if ip.is_unspecified() {
        return Verdict::Forbidden("unspecified");
    }
    if ip.is_multicast() {
        return Verdict::Forbidden("multicast");
    }
    if (seg[0] & 0xffc0) == 0xfe80 {
        return Verdict::Forbidden("link_local");
    }
    if ip.is_loopback() {
        return Verdict::Private("loopback");
    }
    // Global unicast is 2000::/3, minus documentation (2001:db8::/32) and the
    // IETF special-purpose block (2001::/23).
    let global = (seg[0] & 0xe000) == 0x2000 && !(seg[0] == 0x2001 && (seg[1] == 0x0db8 || seg[1] < 0x0200));
    if global {
        Verdict::Public
    } else {
        Verdict::Private("private")
    }
}

pub fn classify(ip: IpAddr) -> Verdict {
    match ip {
        IpAddr::V4(v4) => classify_v4(v4),
        IpAddr::V6(v6) => classify_v6(v6),
    }
}

fn refuse(host: &str, ip: IpAddr, verdict: Verdict, allow_private: bool, prefix: &str) -> Option<ApiError> {
    match verdict {
        Verdict::Public => None,
        Verdict::Private(_) if allow_private => None,
        Verdict::Private(category) => Some(ApiError::app(
            &format!("{prefix}_host_not_allowed"),
            format!(
                "The host {host} resolves to {ip} ({category}), which this installation does not send to. \
                 A self-hosted installation can allow private networks with {ALLOW_PRIVATE_SETTING}=true."
            ),
            422,
            json!({"host": host, "address": ip.to_string(), "category": category, "setting": ALLOW_PRIVATE_SETTING}),
        )),
        Verdict::Forbidden(category) => Some(ApiError::app(
            &format!("{prefix}_host_forbidden"),
            format!("The host {host} resolves to {ip} ({category}), an address nothing may be sent to."),
            422,
            json!({"host": host, "address": ip.to_string(), "category": category}),
        )),
    }
}

/// `https://host[:port]/path`: the host (lowercased, without brackets) and the
/// port. Strict on purpose: a URL whose authority two parsers could read
/// differently is refused instead of guessed at.
pub fn parse_https(url: &str, prefix: &str) -> Result<(String, u16), ApiError> {
    let bad = || {
        ApiError::app(
            &format!("{prefix}_url_invalid"),
            "The URL must be https://host/path without credentials.",
            422,
            json!({}),
        )
    };
    if url.is_empty() || url.len() > MAX_URL_LENGTH || url.chars().any(|c| c.is_control() || c.is_whitespace() || c == '\\') {
        return Err(bad());
    }
    let rest = match url.get(..8) {
        Some(head) if head.eq_ignore_ascii_case("https://") => &url[8..],
        _ => return Err(bad()),
    };
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let authority = &rest[..end];
    if authority.is_empty() || authority.contains('@') || !authority.is_ascii() {
        return Err(bad());
    }
    let (host, port_text) = if let Some(inner) = authority.strip_prefix('[') {
        let close = inner.find(']').ok_or_else(bad)?;
        let after = &inner[close + 1..];
        let port = match after {
            "" => None,
            a => Some(a.strip_prefix(':').ok_or_else(bad)?),
        };
        (inner[..close].to_string(), port)
    } else {
        match authority.rsplit_once(':') {
            Some((h, p)) => (h.to_string(), Some(p)),
            None => (authority.to_string(), None),
        }
    };
    if host.is_empty() || host.contains([':', '[', ']']) && host.parse::<Ipv6Addr>().is_err() {
        return Err(bad());
    }
    let port = match port_text {
        None => 443,
        Some(p) => {
            if p.is_empty() || !p.chars().all(|c| c.is_ascii_digit()) {
                return Err(bad());
            }
            match p.parse::<u16>() {
                Ok(n) if n > 0 => n,
                _ => return Err(bad()),
            }
        }
    };
    Ok((host.to_ascii_lowercase(), port))
}

/// The save-time guard: parse, resolve, and refuse unless EVERY address the
/// host resolves to is allowed. `prefix` names the error codes
/// (`audit_stream` gives `audit_stream_url_invalid`, ...).
pub async fn check_destination(url: &str, allow_private: bool, prefix: &str) -> Result<(), ApiError> {
    let (host, port) = parse_https(url, prefix)?;
    let unresolvable = |reason: &str| {
        ApiError::app(
            &format!("{prefix}_host_unresolvable"),
            format!("The host name {host} does not resolve."),
            422,
            json!({"host": host, "reason": reason}),
        )
    };
    let addresses: Vec<IpAddr> = if let Ok(ip) = host.parse::<IpAddr>() {
        vec![ip]
    } else {
        let lookup = tokio::net::lookup_host((host.as_str(), port));
        match tokio::time::timeout(DNS_TIMEOUT, lookup).await {
            Err(_) => return Err(unresolvable("timeout")),
            Ok(Err(_)) => return Err(unresolvable("not_found")),
            Ok(Ok(found)) => {
                let mut seen: Vec<IpAddr> = Vec::new();
                for sock in found {
                    if !seen.contains(&sock.ip()) {
                        seen.push(sock.ip());
                    }
                }
                seen
            }
        }
    };
    if addresses.is_empty() {
        return Err(unresolvable("not_found"));
    }
    for ip in addresses {
        if let Some(err) = refuse(&host, ip, classify(ip), allow_private, prefix) {
            return Err(err);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    #[test]
    fn public_addresses_pass() {
        for s in ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "2001:4860:4860::8888"] {
            assert_eq!(classify(ip(s)), Verdict::Public, "{s}");
        }
    }

    #[test]
    fn cloud_metadata_and_link_local_are_never_allowed() {
        for s in ["169.254.169.254", "100.100.100.200", "168.63.129.16", "fd00:ec2::254", "fe80::1",
                  "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1"] {
            assert!(matches!(classify(ip(s)), Verdict::Forbidden(_)), "{s}");
        }
    }

    #[test]
    fn multicast_unspecified_broadcast_reserved_are_never_allowed() {
        for s in ["224.0.0.1", "0.0.0.0", "255.255.255.255", "240.0.0.1", "ff02::1", "::"] {
            assert!(matches!(classify(ip(s)), Verdict::Forbidden(_)), "{s}");
        }
    }

    #[test]
    fn private_ranges_wait_for_the_installation_switch() {
        for s in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1", "100.64.0.1",
                  "198.18.0.1", "192.0.2.1", "198.51.100.1", "203.0.113.9", "0.1.2.3", "::1", "fc00::1",
                  "fd12::1", "2001:db8::1", "2001:0:4136:e378::1", "::ffff:10.0.0.1", "64:ff9b::a00:1"] {
            assert!(matches!(classify(ip(s)), Verdict::Private(_)), "{s}: {:?}", classify(ip(s)));
        }
        assert_eq!(classify(ip("172.32.0.1")), Verdict::Public);
        assert_eq!(classify(ip("172.15.255.255")), Verdict::Public);
    }

    #[test]
    fn refuse_honours_the_switch_for_private_only() {
        let a = ip("10.0.0.1");
        assert!(refuse("h", a, classify(a), false, "audit_stream").is_some());
        assert!(refuse("h", a, classify(a), true, "audit_stream").is_none());
        let m = ip("169.254.169.254");
        let e = refuse("h", m, classify(m), true, "audit_stream").unwrap();
        assert_eq!(e.code(), Some("audit_stream_host_forbidden"));
    }

    #[test]
    fn urls_are_parsed_strictly() {
        let ok = |u: &str| parse_https(u, "audit_stream").unwrap();
        assert_eq!(ok("https://siem.example.com/in"), ("siem.example.com".to_string(), 443));
        assert_eq!(ok("HTTPS://SIEM.Example.com:8443/x?y=1#z"), ("siem.example.com".to_string(), 8443));
        assert_eq!(ok("https://[2606:4700::1111]:444/p"), ("2606:4700::1111".to_string(), 444));
        assert_eq!(ok("https://h.example.com"), ("h.example.com".to_string(), 443));
        assert_eq!(ok("https://h.example.com?q"), ("h.example.com".to_string(), 443));
        for bad in ["http://a.com/", "ftp://a.com", "a.com", "", "https://", "https:///x",
                    "https://user@a.com/", "https://user:pw@a.com/", "https://a.com:0/", "https://a.com:99999/",
                    "https://a.com:x/", "https://a.com:/", "https://a.com\\@b.com/", "https://a.com /x",
                    "https://a.com/\n", "https://[::1/x", "https://[::1]x/", "https://a.com@b.com/",
                    "https://exämple.com/"] {
            assert_eq!(parse_https(bad, "audit_stream").unwrap_err().code(), Some("audit_stream_url_invalid"), "{bad:?}");
        }
        let long = format!("https://a.com/{}", "x".repeat(MAX_URL_LENGTH));
        assert!(parse_https(&long, "audit_stream").is_err());
    }

    #[tokio::test]
    async fn literal_addresses_are_checked_without_dns() {
        let e = check_destination("https://169.254.169.254/latest", true, "audit_stream").await.unwrap_err();
        assert_eq!(e.code(), Some("audit_stream_host_forbidden"));
        let e = check_destination("https://10.0.0.5/in", false, "audit_stream").await.unwrap_err();
        assert_eq!(e.code(), Some("audit_stream_host_not_allowed"));
        assert!(check_destination("https://10.0.0.5/in", true, "audit_stream").await.is_ok());
        assert!(check_destination("https://8.8.8.8/in", false, "audit_stream").await.is_ok());
        let e = check_destination("https://localhost/in", false, "audit_stream").await.unwrap_err();
        assert_eq!(e.code(), Some("audit_stream_host_not_allowed"));
    }
}
