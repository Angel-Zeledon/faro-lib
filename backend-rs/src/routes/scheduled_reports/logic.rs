//! The pure rules of scheduled reports: nothing here touches the database or
//! the network, so each rule has a unit test and the Python tests pin the same
//! vectors (`backend/tests/test_scheduled_reports.py`).

use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use sha2::Sha256;

use crate::routes::schedule::{cron, tz};

/// `backend/scheduled_reports/catalog.py::SECTIONS`.
pub const SECTIONS: [&str; 4] = ["purchasing_summary", "budget_vs_spend", "committed_demand", "supplier_scorecard"];
/// `catalog.FREQUENCIES`.
pub const FREQUENCIES: [&str; 2] = ["weekly", "monthly"];

/// Sanity bounds, not plan gates: they keep one tenant from turning the report
/// worker into a mail cannon, and apply to every tier alike.
pub const MAX_SCHEDULES: i64 = 10;
pub const MAX_RECIPIENTS: usize = 25;
pub const MAX_ALLOWLIST: i64 = 50;
pub const MAX_NAME: usize = 100;

/// `schedule_math.cron_for`: ISO weekday 1 (Monday) .. 7 (Sunday) -> cron day
/// of week (Sunday is 0).
pub fn cron_for(frequency: &str, weekday: Option<i64>, day_of_month: Option<i64>, hour: i64) -> Result<String, String> {
    match frequency {
        "weekly" => match weekday {
            Some(w) if (1..=7).contains(&w) => Ok(format!("0 {hour} * * {}", w % 7)),
            _ => Err("weekly needs a weekday from 1 to 7".into()),
        },
        "monthly" => match day_of_month {
            Some(d) if (1..=28).contains(&d) => Ok(format!("0 {hour} {d} * *")),
            _ => Err("monthly needs a day of the month from 1 to 28".into()),
        },
        other => Err(format!("unknown frequency {other:?}")),
    }
}

/// The next firing strictly after `after`, read in `zone_name`, as a UTC
/// instant. Unlike the retrain scheduler's `next_run` there is NO "+24 h"
/// fallback: a schedule that cannot say when it runs next is an error, not a
/// guess at a different hour.
pub fn next_run_after(cron_expr: &str, zone_name: &str, after: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    let zone = tz::Zone::from_name(zone_name).ok_or_else(|| format!("unsupported zone {zone_name}"))?;
    let ex = cron::expand(cron_expr).map_err(|e| e.0)?;
    let naive = cron::next_after(&ex, after.naive_utc(), &zone).map_err(|_| "no next date".to_string())?;
    Ok(naive.and_utc())
}

/// A deliberately plain address check: one `@`, something on each side, a dot
/// in the domain, no spaces or control characters, at most 254 characters.
/// Lower-cased and trimmed, because the allow-list compares exactly.
pub fn normalize_email(raw: &str) -> Option<String> {
    let e = raw.trim().to_lowercase();
    if e.is_empty() || e.chars().count() > 254 || e.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return None;
    }
    let (local, domain) = e.split_once('@')?;
    if local.is_empty() || domain.is_empty() || domain.contains('@') || !domain.contains('.')
        || domain.starts_with('.') || domain.ends_with('.') || domain.contains("..")
        || local.len() > 64 || e.contains(['<', '>', ',', ';', '"', '\\', '(', ')'])
    {
        return None;
    }
    Some(e)
}

// ── The unsubscribe link ─────────────────────────────────────────────────────
// `backend/scheduled_reports/tokens.py`: `<recipient row id>.<base64url(HMAC)>`.

fn signature(secret: &str, recipient_id: &str) -> String {
    use base64::Engine;
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("hmac accepts any key length");
    mac.update(format!("report-unsubscribe|{recipient_id}").as_bytes());
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes())
}

#[allow(dead_code)] // the worker mints in Python; kept for the parity test and a future Rust sender
pub fn mint(secret: &str, recipient_id: &str) -> String {
    format!("{recipient_id}.{}", signature(secret, recipient_id))
}

/// The recipient row id the token was minted for, or `None`. Constant-time on
/// the signature.
pub fn verify(secret: &str, token: &str) -> Option<String> {
    let (id, sig) = token.split_once('.')?;
    if id.is_empty() || sig.is_empty() {
        return None;
    }
    let expected = signature(secret, id);
    let mut diff = (expected.len() ^ sig.len()) as u8;
    for (a, b) in expected.bytes().zip(sig.bytes()) {
        diff |= a ^ b;
    }
    if diff == 0 { Some(id.to_string()) } else { None }
}

// ── Who would receive it ─────────────────────────────────────────────────────

/// What the worker sees about one recipient row (mirrors
/// `service.resolve_recipients`).
pub struct RecipientFacts<'a> {
    pub kind: &'a str,
    pub unsubscribed: bool,
    pub user_found: bool,
    pub user_active: bool,
    pub user_scoped: bool,
    pub user_has_email: bool,
    pub external_allowed: bool,
}

/// Why the worker would skip this recipient, or `None` when it would send.
/// Same codes, same order as Python's `resolve_recipients`.
pub fn skip_reason(f: &RecipientFacts) -> Option<&'static str> {
    if f.unsubscribed {
        return Some("unsubscribed");
    }
    if f.kind == "user" {
        if !f.user_found {
            Some("user_removed")
        } else if !f.user_active {
            Some("user_inactive")
        } else if f.user_scoped {
            Some("user_warehouse_scoped")
        } else if !f.user_has_email {
            Some("user_without_email")
        } else {
            None
        }
    } else if !f.external_allowed {
        Some("external_not_allowed")
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn u(s: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(&format!("{s}Z")).unwrap().with_timezone(&Utc)
    }

    /// The vectors of `NEXT_RUN_VECTORS` in the Python tests, computed there
    /// with the real croniter. The port must agree on every one, DST included.
    #[test]
    fn next_run_vectors_match_croniter() {
        let v = [
            ("0 6 * * 1", "America/Costa_Rica", "2026-10-06T00:00:00", "2026-10-12T12:00:00"),
            ("0 8 15 * *", "America/Bogota", "2026-10-15T13:00:00", "2026-11-15T13:00:00"),
            ("0 2 * * 0", "Europe/Madrid", "2026-03-23T00:00:00", "2026-03-29T01:00:00"),
            ("0 2 * * 0", "Europe/Madrid", "2026-10-20T00:00:00", "2026-10-25T00:00:00"),
            ("0 2 * * 0", "America/New_York", "2026-03-02T00:00:00", "2026-03-08T07:00:00"),
            ("0 1 * * 0", "America/New_York", "2026-10-26T00:00:00", "2026-11-01T05:00:00"),
            ("0 0 28 * *", "America/Santiago", "2026-08-01T00:00:00", "2026-08-28T04:00:00"),
        ];
        for (cron, zone, after, expected) in v {
            assert_eq!(next_run_after(cron, zone, u(after)).unwrap(), u(expected), "{cron} {zone} {after}");
        }
    }

    /// The Python pass handles the repeated hour by key; this pins that the port
    /// shows the same repeat, so a Rust-computed `next_run_at` and a Python
    /// advance can never disagree about it.
    #[test]
    fn the_repeated_hour_is_visible_to_both_implementations() {
        // croniter (Python) fires a second time an hour after the first.
        assert_eq!(next_run_after("0 2 * * 0", "Europe/Madrid", u("2026-10-25T00:00:00")).unwrap(), u("2026-10-25T01:00:00"));
        assert_eq!(next_run_after("0 1 * * 0", "America/New_York", u("2026-11-01T05:00:00")).unwrap(), u("2026-11-01T06:00:00"));
    }

    #[test]
    fn cron_for_matches_python() {
        assert_eq!(cron_for("weekly", Some(1), None, 6).unwrap(), "0 6 * * 1");
        assert_eq!(cron_for("weekly", Some(7), None, 6).unwrap(), "0 6 * * 0");
        assert_eq!(cron_for("monthly", None, Some(15), 8).unwrap(), "0 8 15 * *");
        assert!(cron_for("weekly", None, None, 6).is_err());
        assert!(cron_for("weekly", Some(8), None, 6).is_err());
        assert!(cron_for("monthly", None, Some(29), 6).is_err());
        assert!(cron_for("daily", Some(1), None, 6).is_err());
    }

    #[test]
    fn no_second_guess_when_the_zone_is_unknown() {
        assert!(next_run_after("0 6 * * 1", "Mars/Olympus", u("2026-10-06T00:00:00")).is_err());
    }

    #[test]
    fn the_token_is_the_python_token() {
        // Pinned in backend/tests/test_scheduled_reports.py::TestUnsubscribeToken.
        let id = "11111111-2222-3333-4444-555555555555";
        let pinned = format!("{id}.P6npnbIEQgFwI0JuS9STwjLzGFfrxpufdgYihtYiXfg");
        assert_eq!(mint("test-secret", id), pinned);
        assert_eq!(verify("test-secret", &pinned).as_deref(), Some(id));
        assert_eq!(verify("another-secret", &pinned), None);
        let mut tampered = pinned.clone();
        tampered.pop();
        tampered.push('A');
        assert_eq!(verify("test-secret", &tampered), None);
        assert_eq!(verify("test-secret", &format!("2222.{}", pinned.split('.').nth(1).unwrap())), None);
        assert_eq!(verify("test-secret", ""), None);
        assert_eq!(verify("test-secret", "nodot"), None);
        assert_eq!(verify("test-secret", ".sig"), None);
    }

    #[test]
    fn addresses() {
        assert_eq!(normalize_email("  Board@Outside.EXAMPLE ").as_deref(), Some("board@outside.example"));
        for bad in ["", "a", "a@b", "@b.com", "a@", "a b@c.com", "a@b@c.com", "a@.com", "a@com.", "a@b..com",
                    "a@b.com, c@d.com", "<a@b.com>", "a@b.com;c@d.com"] {
            assert_eq!(normalize_email(bad), None, "{bad:?}");
        }
        assert_eq!(normalize_email(&format!("{}@b.com", "x".repeat(65))), None);
    }

    #[test]
    fn skip_reasons_are_the_python_reasons_in_the_same_order() {
        let ok = RecipientFacts { kind: "user", unsubscribed: false, user_found: true, user_active: true,
            user_scoped: false, user_has_email: true, external_allowed: false };
        assert_eq!(skip_reason(&ok), None);
        let f = |g: fn(&mut RecipientFacts)| {
            let mut x = RecipientFacts { ..ok };
            g(&mut x);
            skip_reason(&x)
        };
        assert_eq!(f(|x| x.unsubscribed = true), Some("unsubscribed"));
        assert_eq!(f(|x| { x.unsubscribed = true; x.user_found = false }), Some("unsubscribed"));
        assert_eq!(f(|x| x.user_found = false), Some("user_removed"));
        assert_eq!(f(|x| x.user_active = false), Some("user_inactive"));
        assert_eq!(f(|x| x.user_scoped = true), Some("user_warehouse_scoped"));
        assert_eq!(f(|x| x.user_has_email = false), Some("user_without_email"));
        assert_eq!(f(|x| x.kind = "external"), Some("external_not_allowed"));
        assert_eq!(f(|x| { x.kind = "external"; x.external_allowed = true }), None);
    }

    #[test]
    fn the_catalogue_is_the_python_catalogue() {
        let src = include_str!("../../../../backend/scheduled_reports/catalog.py");
        let tuple = |name: &str| -> Vec<String> {
            let body = src.split(&format!("{name}: tuple[str, ...] = (")).nth(1).unwrap().split(')').next().unwrap();
            body.split(',')
                .map(|p| p.trim().trim_matches('"').to_string())
                .filter(|p| !p.is_empty())
                .collect()
        };
        let constants: std::collections::HashMap<String, String> = src
            .lines()
            .filter_map(|l| l.split_once(" = "))
            .map(|(k, v)| (k.trim().to_string(), v.trim().trim_matches('"').to_string()))
            .collect();
        let sections: Vec<String> = tuple("SECTIONS").iter().map(|n| constants[n].clone()).collect();
        assert_eq!(sections, SECTIONS.to_vec());
        assert_eq!(tuple("FREQUENCIES"), FREQUENCIES.to_vec());
    }
}
