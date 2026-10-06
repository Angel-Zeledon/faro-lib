//! The read half of `backend/billing/entitlement.py` that the tenant erasure
//! needs: does a subscription still grant access. Pure, no I/O. (The decision
//! of which tier a subscription implies, `decide_tier`, is not ported: only the
//! billing webhooks and the sweep call it, and both stay Python.)

use chrono::{DateTime, Duration, Utc};

pub const GRACE_DAYS: i64 = 7;

#[derive(Debug, Clone, Default)]
pub struct SubscriptionState {
    pub status: String,
    pub current_period_end: Option<DateTime<Utc>>,
    pub cancel_at_period_end: bool,
    pub past_due_since: Option<DateTime<Utc>>,
    pub ended_at: Option<DateTime<Utc>>,
}

fn entitled(status: &str) -> bool {
    matches!(status, "active" | "trialing")
}

/// `grace_until`: end of the past-due window (a missing start is graced from `now`).
fn grace_until(sub: &SubscriptionState, now: DateTime<Utc>) -> DateTime<Utc> {
    sub.past_due_since.unwrap_or(now) + Duration::days(GRACE_DAYS)
}

/// `access_until` for the `canceled` status.
fn ended_until(sub: &SubscriptionState) -> Option<DateTime<Utc>> {
    sub.ended_at.or(sub.current_period_end)
}

/// `grants_access`: fail closed for lapsed, pending and unknown statuses.
pub fn grants_access(sub: &SubscriptionState, now: DateTime<Utc>) -> bool {
    let status = sub.status.as_str();
    if entitled(status) {
        return match (sub.cancel_at_period_end, sub.current_period_end) {
            (true, Some(end)) => now < end,
            _ => true,
        };
    }
    if status == "past_due" {
        return now < grace_until(sub, now);
    }
    if status == "canceled" {
        return ended_until(sub).map(|until| now < until).unwrap_or(false);
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn at(d: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 10, d, 0, 0, 0).unwrap()
    }
    fn sub(status: &str) -> SubscriptionState {
        SubscriptionState { status: status.into(), ..Default::default() }
    }

    #[test]
    fn active_renews_until_told_otherwise() {
        assert!(grants_access(&sub("active"), at(6)));
        let mut s = sub("trialing");
        s.cancel_at_period_end = true;
        assert!(grants_access(&s, at(6)), "no period end known: still granted");
        s.current_period_end = Some(at(10));
        assert!(grants_access(&s, at(6)));
        assert!(!grants_access(&s, at(10)), "the period end itself is out");
    }

    #[test]
    fn past_due_has_a_week_of_grace() {
        let mut s = sub("past_due");
        assert!(grants_access(&s, at(6)), "start unknown: graced from now");
        s.past_due_since = Some(at(1));
        assert!(grants_access(&s, at(7)));
        assert!(!grants_access(&s, at(8)));
    }

    #[test]
    fn canceled_grants_until_it_ended_and_the_rest_never() {
        let mut s = sub("canceled");
        assert!(!grants_access(&s, at(6)));
        s.current_period_end = Some(at(9));
        assert!(grants_access(&s, at(6)));
        s.ended_at = Some(at(5));
        assert!(!grants_access(&s, at(6)), "ended_at wins over the period end");
        for lapsed in ["unpaid", "expired", "incomplete", "approval_pending", "whatever", ""] {
            assert!(!grants_access(&sub(lapsed), at(6)), "{lapsed}");
        }
    }
}
