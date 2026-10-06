//! `sk_live_*` machine credentials: the database half, identical to
//! `backend/auth/api_key_auth.py` (hashing, lookup, the two rate windows,
//! billing meter, `last_used` throttle). The HTTP half is in `auth/mod.rs`.

use serde_json::Value;
use sha2::{Digest, Sha256};
use sqlx::PgPool;

use crate::entitlements::{tenant_limits, TenantRow};

pub const KEY_PREFIX: &str = "sk_live_";
pub const RATE_MAX_PER_MINUTE: i64 = 120;
pub const RATE_WINDOW_SECONDS: i64 = 60;
pub const DAY_WINDOW_SECONDS: i64 = 86_400;

/// Unsalted SHA-256 hex: 32 random bytes need no salt, and an unsalted digest
/// keeps the lookup a single indexed equality (same reasoning as Python).
pub fn hash_key(raw: &str) -> String {
    hex::encode(Sha256::digest(raw.as_bytes()))
}

pub fn looks_like_api_key(credential: &str) -> bool {
    credential.starts_with(KEY_PREFIX)
}

/// The identity a key acts under (never its creator).
pub fn actor_id(key_id: &str) -> String {
    format!("api_key:{key_id}")
}

#[derive(Debug, Clone)]
pub struct ApiKeyRow {
    pub id: String,
    pub tenant_id: String,
    pub name: String,
    pub role: String,
}

/// `resolve`: unknown, deleted and expired are the same `None`.
pub async fn resolve(pool: &PgPool, credential: &str) -> Result<Option<ApiKeyRow>, sqlx::Error> {
    let row: Option<(String, String, String, String)> = sqlx::query_as(
        "SELECT id, tenant_id, name, role
           FROM api_keys
          WHERE key_hash = $1
            AND (expires_at IS NULL OR expires_at > NOW())",
    )
    .bind(hash_key(credential))
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|(id, tenant_id, name, role)| ApiKeyRow { id, tenant_id, name, role }))
}

/// `touch`: one conditional UPDATE, at most one real write per minute.
pub async fn touch(pool: &PgPool, key_id: &str) {
    let res = sqlx::query(
        "UPDATE api_keys
            SET last_used = NOW()
          WHERE id = $1
            AND (last_used IS NULL OR last_used < NOW() - INTERVAL '1 minute')",
    )
    .bind(key_id)
    .execute(pool)
    .await;
    if let Err(e) = res {
        // Python lets this raise (a 500). It never has in practice; logging is
        // the conservative port, and the divergence is listed in the doc.
        tracing::warn!(error = %e, key_id, "could not stamp api_keys.last_used");
    }
}

/// `meter`: one upsert per call; never fails the call, logs loudly instead.
pub async fn meter(pool: &PgPool, key_id: &str, tenant_id: &str, key_name: &str) -> bool {
    let res = sqlx::query(
        "INSERT INTO api_usage_daily (tenant_id, api_key_id, key_name, day, calls)
         VALUES ($1, $2, $3, (NOW() AT TIME ZONE 'UTC')::date, 1)
         ON CONFLICT (tenant_id, api_key_id, day)
         DO UPDATE SET calls = api_usage_daily.calls + 1,
                       key_name = EXCLUDED.key_name",
    )
    .bind(tenant_id)
    .bind(key_id)
    .bind(key_name)
    .execute(pool)
    .await;
    match res {
        Ok(_) => true,
        Err(e) => {
            tracing::error!(
                error = %e, tenant = tenant_id, key = key_id,
                "[api-metering] UNMETERED API CALL: could not count a call -- \
                 this call will be missing from the usage and the bill"
            );
            false
        }
    }
}

/// `_daily_ceiling`. `Err(())` stands for "Python would have raised", which
/// `check_rate` turns into fail-open exactly like its `except Exception`.
async fn daily_ceiling(pool: &PgPool, tenant_id: &str) -> Result<Option<i64>, ()> {
    let row: Option<(String, Value)> = sqlx::query_as("SELECT tier, quota FROM tenants WHERE id = $1")
        .bind(tenant_id)
        .fetch_optional(pool)
        .await
        .map_err(|_| ())?;
    let Some((tier, quota)) = row else { return Ok(None) };
    let t = TenantRow { tier: Some(tier), quota, ..Default::default() };
    match tenant_limits(&t).get("max_api_calls_per_day") {
        None | Some(Value::Null) => Ok(None),
        Some(v) => v.as_f64().map(|f| Some(f.ceil() as i64)).ok_or(()),
    }
}

/// `check_rate`: both windows decided under a per-key advisory lock, the call
/// recorded only once both agreed, and FAIL OPEN on any database problem.
pub async fn check_rate(pool: &PgPool, key_id: &str, tenant_id: &str) -> bool {
    match check_rate_inner(pool, key_id, tenant_id).await {
        Ok(allowed) => allowed,
        Err(()) => true,
    }
}

async fn within(
    conn: &mut sqlx::PgConnection,
    bucket: &str,
    ceiling: i64,
    window_secs: i64,
) -> Result<bool, ()> {
    sqlx::query("DELETE FROM auth_rate_events WHERE key = $1 AND created_at < NOW() - make_interval(secs => $2)")
        .bind(bucket)
        .bind(window_secs as f64)
        .execute(&mut *conn)
        .await
        .map_err(|_| ())?;
    let (used,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM auth_rate_events WHERE key = $1")
        .bind(bucket)
        .fetch_one(&mut *conn)
        .await
        .map_err(|_| ())?;
    Ok(used < ceiling)
}

async fn check_rate_inner(pool: &PgPool, key_id: &str, tenant_id: &str) -> Result<bool, ()> {
    let daily = daily_ceiling(pool, tenant_id).await?;
    let mut tx = pool.begin().await.map_err(|_| ())?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(format!("ratelimit:{key_id}"))
        .execute(&mut *tx)
        .await
        .map_err(|_| ())?;
    let minute_bucket = format!("apikey:{key_id}");
    let day_bucket = format!("apikeyday:{key_id}");
    if !within(&mut tx, &minute_bucket, RATE_MAX_PER_MINUTE, RATE_WINDOW_SECONDS).await? {
        // Python returns from inside `with transaction()`, which COMMITS the
        // pruning DELETE. Same here.
        tx.commit().await.map_err(|_| ())?;
        return Ok(false);
    }
    if let Some(d) = daily {
        if !within(&mut tx, &day_bucket, d, DAY_WINDOW_SECONDS).await? {
            tx.commit().await.map_err(|_| ())?;
            return Ok(false);
        }
    }
    sqlx::query("INSERT INTO auth_rate_events (key) VALUES ($1)")
        .bind(&minute_bucket)
        .execute(&mut *tx)
        .await
        .map_err(|_| ())?;
    if daily.is_some() {
        sqlx::query("INSERT INTO auth_rate_events (key) VALUES ($1)")
            .bind(&day_bucket)
            .execute(&mut *tx)
            .await
            .map_err(|_| ())?;
    }
    tx.commit().await.map_err(|_| ())?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_is_sha256_hex_of_the_raw_key() {
        // python -c "import hashlib;print(hashlib.sha256(b'sk_live_abc').hexdigest())"
        assert_eq!(
            hash_key("sk_live_abc"),
            "b2817799acd7f3337c32f967a7c4ca32a767c94190298bace3e0447120385c09"
        );
    }

    #[test]
    fn prefix_dispatch() {
        assert!(looks_like_api_key("sk_live_x"));
        assert!(!looks_like_api_key("eyJhbGciOi"));
        assert_eq!(actor_id("k1"), "api_key:k1");
    }
}
