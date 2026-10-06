//! Plan ceilings on writes: `limit_guard` / `take_tenant_lock` /
//! `enforce_limit` from `backend/entitlements/service.py`.
//!
//! Every ceiling is "count, then write", which is only correct when the two
//! cannot interleave with another request of the same tenant. Python holds a
//! transaction-scoped advisory lock keyed on the tenant id for both; this does
//! the same with the same key (`hashtext(tenant_id)`), so a Python writer and
//! a Rust writer of the same tenant also queue behind each other.

use serde_json::{json, Value};
use sqlx::{PgConnection, PgPool};

use crate::entitlements::{tenant_limits, tenant_tier, TenantRow};
use crate::error::ApiError;

/// `take_tenant_lock`: call FIRST inside the transaction, before the count.
pub async fn take_tenant_lock(conn: &mut PgConnection, tenant_id: &str) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(tenant_id)
        .execute(&mut *conn)
        .await?;
    Ok(())
}

/// What `enforce_limit` decides for one ceiling, without side effects.
#[derive(Debug, PartialEq)]
pub enum Verdict {
    Allowed,
    /// Over the ceiling: the `max` exactly as the plan/quota holds it.
    Refused(Value),
    /// The quota holds something Python cannot compare (`current + adding >
    /// "abc"` raises): a 500 there, a 500 here.
    Uncomparable,
}

/// `max_allowed is not None and current + adding > max_allowed`.
pub fn decide(max_allowed: &Value, current: i64, adding: i64) -> Verdict {
    match max_allowed {
        Value::Null => Verdict::Allowed,
        Value::Bool(b) => {
            // bool is an int in Python.
            if current + adding > i64::from(*b) { Verdict::Refused(max_allowed.clone()) } else { Verdict::Allowed }
        }
        Value::Number(n) => {
            let over = match n.as_i64() {
                Some(i) => current + adding > i,
                None => (current + adding) as f64 > n.as_f64().unwrap_or(f64::INFINITY),
            };
            if over { Verdict::Refused(max_allowed.clone()) } else { Verdict::Allowed }
        }
        _ => Verdict::Uncomparable,
    }
}

/// `enforce_limit(tenant_id, limit_key, current, adding, conn=conn)`. A no-op
/// in testing mode. A refusal records `limit.reached` on its OWN connection
/// (it must survive the caller's rollback) and is the `PLAN_LIMIT_REACHED`
/// 403 with `{limit, current, max, tier}`.
pub async fn enforce_limit(
    pool: &PgPool,
    conn: &mut PgConnection,
    testing_mode: bool,
    tenant_id: &str,
    limit_key: &str,
    current: i64,
    adding: i64,
) -> Result<(), ApiError> {
    if testing_mode {
        return Ok(());
    }
    let row: Option<(String, Value, Option<chrono::DateTime<chrono::Utc>>)> =
        sqlx::query_as("SELECT tier, quota, trial_ends_at FROM tenants WHERE id = $1")
            .bind(tenant_id)
            .fetch_optional(&mut *conn)
            .await?;
    let tenant = row
        .map(|(tier, quota, trial_ends_at)| TenantRow { tier: Some(tier), quota, trial_ends_at })
        .unwrap_or_default();
    let max_allowed = tenant_limits(&tenant).get(limit_key).cloned().unwrap_or(Value::Null);
    match decide(&max_allowed, current, adding) {
        Verdict::Allowed => Ok(()),
        Verdict::Uncomparable => Err(ApiError::internal()),
        Verdict::Refused(max) => {
            let context = json!({
                "limit": limit_key,
                "ceiling": max,
                "severity": "warning",
                "kind": "limit",
                "reason": "plan_limit_reached",
                "reason_params": {"limit": limit_key, "current": current, "max": max},
            });
            if let Err(e) = crate::activity::log_action(pool, tenant_id, "system", "limit.reached",
                Some(limit_key), &context, "error").await
            {
                tracing::error!(error = %e, tenant = tenant_id, "could not record the ceiling");
            }
            Err(ApiError::http_dict(403, json!({
                "code": "PLAN_LIMIT_REACHED",
                "limit": limit_key,
                "current": current,
                "max": max,
                "tier": tenant_tier(&tenant),
            })))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ceiling_comparisons_match_python() {
        assert_eq!(decide(&Value::Null, 1_000, 1), Verdict::Allowed);
        assert_eq!(decide(&json!(3), 2, 1), Verdict::Allowed);
        assert_eq!(decide(&json!(3), 3, 1), Verdict::Refused(json!(3)));
        // free tier: no keys at all.
        assert_eq!(decide(&json!(0), 0, 1), Verdict::Refused(json!(0)));
        assert_eq!(decide(&json!(2.5), 2, 1), Verdict::Refused(json!(2.5)));
        assert_eq!(decide(&json!(3.5), 2, 1), Verdict::Allowed);
        assert_eq!(decide(&json!("5"), 0, 1), Verdict::Uncomparable);
    }
}
