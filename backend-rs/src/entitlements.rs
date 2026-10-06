//! `backend/entitlements/plans.py` + `service.py`: tiers, ceilings, the three
//! paid-only features, and the trial read-only state.
//!
//! The plan table is DATA here and data there, copied value for value. A test
//! below pins every number, and `tests/contract` compares the live
//! `GET /entitlements` of both services, so a change made on one side only
//! turns something red.

use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::PgPool;

use crate::pycompat::truthy;

pub const FREE: &str = "free";
pub const PAID: &str = "paid";
pub const CORPORATE: &str = "corporate";
pub const DEMO: &str = "demo";
pub const DEFAULT_TIER: &str = FREE;
/// The cheapest tier that includes every paid-only feature (`required_plan`).
pub const FEATURE_REQUIRED_PLAN: &str = PAID;

#[derive(Debug, Clone, Copy)]
pub struct PlanDef {
    pub max_skus: Option<i64>,
    pub max_users: Option<i64>,
    pub max_locations: Option<i64>,
    pub max_sessions: Option<i64>,
    pub max_api_keys: Option<i64>,
    pub max_api_calls_per_day: Option<i64>,
    pub max_concurrent_jobs: Option<i64>,
    pub max_dataset_size_mb: Option<i64>,
    /// Training launches per calendar day in the tenant's timezone.
    pub max_trainings_per_day: Option<i64>,
    pub api_access: bool,
    pub mcp_access: bool,
    pub whatsapp_bot: bool,
}

const MAX_CONCURRENT_JOBS: i64 = 8;

pub fn plan(tier: &str) -> Option<PlanDef> {
    Some(match tier {
        FREE => PlanDef {
            max_skus: Some(100),
            max_users: Some(2),
            max_locations: Some(1),
            max_sessions: Some(3),
            max_api_keys: Some(0),
            max_api_calls_per_day: Some(0),
            max_concurrent_jobs: Some(MAX_CONCURRENT_JOBS),
            max_dataset_size_mb: Some(25),
            max_trainings_per_day: Some(1),
            api_access: false,
            mcp_access: false,
            whatsapp_bot: false,
        },
        PAID => PlanDef {
            max_skus: Some(1000),
            max_users: Some(5),
            max_locations: Some(3),
            max_sessions: Some(20),
            max_api_keys: Some(3),
            max_api_calls_per_day: Some(2000),
            max_concurrent_jobs: Some(MAX_CONCURRENT_JOBS),
            max_dataset_size_mb: Some(100),
            max_trainings_per_day: Some(10),
            api_access: true,
            mcp_access: true,
            whatsapp_bot: true,
        },
        CORPORATE => PlanDef {
            max_skus: None,
            max_users: None,
            max_locations: None,
            max_sessions: None,
            max_api_keys: None,
            max_api_calls_per_day: None,
            max_concurrent_jobs: Some(MAX_CONCURRENT_JOBS),
            max_dataset_size_mb: Some(2000),
            max_trainings_per_day: None,
            api_access: true,
            mcp_access: true,
            whatsapp_bot: true,
        },
        DEMO => PlanDef {
            max_skus: Some(30),
            max_users: Some(1),
            max_locations: Some(2),
            max_sessions: Some(2),
            max_api_keys: Some(0),
            max_api_calls_per_day: Some(0),
            max_concurrent_jobs: Some(1),
            max_dataset_size_mb: Some(5),
            max_trainings_per_day: Some(1),
            api_access: false,
            mcp_access: false,
            whatsapp_bot: false,
        },
        _ => return None,
    })
}

impl PlanDef {
    /// `_LIMIT_FIELDS` in declaration order, with their values.
    fn limit_fields(&self) -> [(&'static str, Option<i64>); 9] {
        [
            ("max_skus", self.max_skus),
            ("max_users", self.max_users),
            ("max_locations", self.max_locations),
            ("max_sessions", self.max_sessions),
            ("max_api_keys", self.max_api_keys),
            ("max_api_calls_per_day", self.max_api_calls_per_day),
            ("max_concurrent_jobs", self.max_concurrent_jobs),
            ("max_dataset_size_mb", self.max_dataset_size_mb),
            ("max_trainings_per_day", self.max_trainings_per_day),
        ]
    }

    /// `FEATURE_FIELDS`: feature key -> (PlanDef field, value).
    fn feature_fields(&self) -> [(&'static str, &'static str, bool); 3] {
        [
            ("api", "api_access", self.api_access),
            ("mcp", "mcp_access", self.mcp_access),
            ("whatsapp_bot", "whatsapp_bot", self.whatsapp_bot),
        ]
    }
}

/// The columns of `tenants` that entitlements read.
#[derive(Debug, Clone, Default)]
pub struct TenantRow {
    pub tier: Option<String>,
    pub quota: Value,
    pub trial_ends_at: Option<DateTime<Utc>>,
}

impl TenantRow {
    pub async fn load(pool: &PgPool, tenant_id: &str) -> Result<Option<TenantRow>, sqlx::Error> {
        let row: Option<(String, Value, Option<DateTime<Utc>>)> = sqlx::query_as(
            "SELECT tier, quota, trial_ends_at FROM tenants WHERE id = $1",
        )
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
        Ok(row.map(|(tier, quota, trial_ends_at)| TenantRow { tier: Some(tier), quota, trial_ends_at }))
    }

    fn quota_map(&self) -> Map<String, Value> {
        // `(tenant or {}).get("quota") or {}`: a non-object quota is treated as
        // empty only when falsy in Python; a truthy non-dict would make Python
        // raise on `field in override`. Such a row cannot be written by the app.
        self.quota.as_object().cloned().unwrap_or_default()
    }
}

/// `tenant_tier`: anything unrecognised resolves to free, never to paid.
pub fn tenant_tier(t: &TenantRow) -> &'static str {
    match t.tier.as_deref() {
        Some(FREE) => FREE,
        Some(PAID) => PAID,
        Some(CORPORATE) => CORPORATE,
        Some(DEMO) => DEMO,
        _ => DEFAULT_TIER,
    }
}

fn opt_json(v: Option<i64>) -> Value {
    v.map(Value::from).unwrap_or(Value::Null)
}

/// `tenant_limits`: the tier's ceilings with `tenants.quota` on top.
pub fn tenant_limits(t: &TenantRow) -> Map<String, Value> {
    let p = plan(tenant_tier(t)).expect("known tier");
    let over = t.quota_map();
    let mut out = Map::new();
    for (field, value) in p.limit_fields() {
        let v = over.get(field).cloned().unwrap_or_else(|| opt_json(value));
        out.insert(field.to_string(), v);
    }
    out
}

/// `tenant_features`: `{"api": bool, "mcp": bool, "whatsapp_bot": bool}`.
pub fn tenant_features(t: &TenantRow) -> Map<String, Value> {
    let p = plan(tenant_tier(t)).expect("known tier");
    let over = t.quota_map();
    let mut out = Map::new();
    for (key, field, value) in p.feature_fields() {
        let v = match over.get(field) {
            Some(o) => truthy(o),
            None => value,
        };
        out.insert(key.to_string(), Value::Bool(v));
    }
    out
}

/// `trial_state`: "active" with no end date, "trialing" until it, then
/// "expired" (the end instant itself still counts as trialing).
pub fn trial_state(t: &TenantRow, now: DateTime<Utc>) -> &'static str {
    match t.trial_ends_at {
        None => "active",
        Some(ends) if ends >= now => "trialing",
        Some(_) => "expired",
    }
}

pub fn is_read_only(t: &TenantRow, now: DateTime<Utc>) -> bool {
    trial_state(t, now) == "expired"
}

/// `feature_locked_error`.
pub fn feature_locked_error(feature: &str, detail: &str) -> crate::error::ApiError {
    let mut message = format!("The {feature} feature is not included in this plan.");
    if !detail.is_empty() {
        message = format!("{message} {detail}");
    }
    crate::error::ApiError::app(
        "plan_feature_locked",
        message,
        403,
        json!({"feature": feature, "required_plan": FEATURE_REQUIRED_PLAN}),
    )
}

/// `ensure_feature`: open in testing mode; an unknown tenant is locked.
pub async fn ensure_feature(
    pool: &PgPool,
    testing_mode: bool,
    tenant_id: &str,
    feature: &str,
    detail: &str,
) -> Result<(), crate::error::ApiError> {
    if testing_mode {
        return Ok(());
    }
    let row: Option<(String, Value)> = sqlx::query_as("SELECT tier, quota FROM tenants WHERE id = $1")
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    let t = row
        .map(|(tier, quota)| TenantRow { tier: Some(tier), quota, ..Default::default() })
        .unwrap_or_default();
    let has = tenant_features(&t).get(feature).and_then(Value::as_bool).unwrap_or(false);
    if has {
        Ok(())
    } else {
        Err(feature_locked_error(feature, detail))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    fn tenant(tier: &str, quota: Value) -> TenantRow {
        TenantRow { tier: Some(tier.into()), quota, ..Default::default() }
    }

    #[test]
    fn plan_numbers_match_plans_py() {
        let free = tenant_limits(&tenant("free", json!({})));
        assert_eq!(Value::Object(free), json!({
            "max_skus": 100, "max_users": 2, "max_locations": 1, "max_sessions": 3,
            "max_api_keys": 0, "max_api_calls_per_day": 0, "max_concurrent_jobs": 8,
            "max_dataset_size_mb": 25, "max_trainings_per_day": 1}));
        let paid = tenant_limits(&tenant("paid", json!({})));
        assert_eq!(Value::Object(paid), json!({
            "max_skus": 1000, "max_users": 5, "max_locations": 3, "max_sessions": 20,
            "max_api_keys": 3, "max_api_calls_per_day": 2000, "max_concurrent_jobs": 8,
            "max_dataset_size_mb": 100, "max_trainings_per_day": 10}));
        let corp = tenant_limits(&tenant("corporate", json!({})));
        assert_eq!(Value::Object(corp), json!({
            "max_skus": null, "max_users": null, "max_locations": null, "max_sessions": null,
            "max_api_keys": null, "max_api_calls_per_day": null, "max_concurrent_jobs": 8,
            "max_dataset_size_mb": 2000, "max_trainings_per_day": null}));
        let demo = tenant_limits(&tenant("demo", json!({})));
        assert_eq!(Value::Object(demo), json!({
            "max_skus": 30, "max_users": 1, "max_locations": 2, "max_sessions": 2,
            "max_api_keys": 0, "max_api_calls_per_day": 0, "max_concurrent_jobs": 1,
            "max_dataset_size_mb": 5, "max_trainings_per_day": 1}));
    }

    /// Every `PlanDef(...)` number in plans.py, re-read from the source.
    #[test]
    fn plan_table_matches_the_python_source() {
        let src = include_str!("../../backend/entitlements/plans.py");
        let body = src.split("PLANS: dict[str, PlanDef] = {").nth(1).unwrap();
        for (py_name, tier) in [("FREE", FREE), ("PAID", PAID), ("CORPORATE", CORPORATE), ("DEMO", DEMO)] {
            let block = body.split(&format!("{py_name}: PlanDef(")).nth(1).unwrap().split("
    ),").next().unwrap();
            let p = plan(tier).unwrap();
            let mut seen = 0;
            for (field, value) in p.limit_fields() {
                let line = block.lines().find(|l| l.trim_start().starts_with(&format!("{field}="))).unwrap();
                let raw = line.split('=').nth(1).unwrap().trim().trim_end_matches(',');
                let py = match raw {
                    "None" => None,
                    "_MAX_CONCURRENT_JOBS" => Some(MAX_CONCURRENT_JOBS),
                    n => Some(n.parse::<i64>().unwrap()),
                };
                assert_eq!(py, value, "{tier}.{field}");
                seen += 1;
            }
            assert_eq!(seen, 9);
        }
    }

    #[test]
    fn unknown_or_missing_tier_is_free() {
        assert_eq!(tenant_tier(&tenant("enterprise", json!({}))), "free");
        assert_eq!(tenant_tier(&TenantRow::default()), "free");
    }

    #[test]
    fn quota_overrides_win_in_both_directions() {
        let t = tenant("free", json!({"max_skus": 500, "max_users": null, "api_access": true}));
        let l = tenant_limits(&t);
        assert_eq!(l["max_skus"], 500);
        assert_eq!(l["max_users"], Value::Null);
        let f = tenant_features(&t);
        assert_eq!(f["api"], true);
        assert_eq!(f["mcp"], false);
        let paid_off = tenant("paid", json!({"mcp_access": 0}));
        assert_eq!(tenant_features(&paid_off)["mcp"], false);
    }

    #[test]
    fn trial_states() {
        let now = Utc::now();
        let mut t = tenant("free", json!({}));
        assert_eq!(trial_state(&t, now), "active");
        t.trial_ends_at = Some(now + Duration::days(1));
        assert_eq!(trial_state(&t, now), "trialing");
        t.trial_ends_at = Some(now - Duration::seconds(1));
        assert_eq!(trial_state(&t, now), "expired");
        assert!(is_read_only(&t, now));
    }

    #[test]
    fn locked_feature_error_shape() {
        let e = feature_locked_error("api", "Extra words.");
        assert_eq!(e.status.as_u16(), 403);
        assert_eq!(e.body["detail"], "The api feature is not included in this plan. Extra words.");
        assert_eq!(e.body["error_params"], json!({"feature": "api", "required_plan": "paid"}));
    }
}
