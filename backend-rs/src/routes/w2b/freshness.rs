//! `GET /api/v1/data-freshness` (backend/api/v1/freshness.py over
//! backend/notifications/freshness_service.py): how old the data behind the
//! semaforo is. Any role and a read key (`freshness` is an exposed tag).
//!
//! Only the READ half moved. The daily reminder loop
//! (`run_daily_freshness_reminders`) sends email and WhatsApp, so it stays in
//! the Python worker; the thresholds below are the same constants it uses and
//! the contract file compares them to the Python source.
//!
//! Ages are whole days between "now" and a timestamp (`timedelta.days`, floor,
//! never negative). The two services each read their own clock, so a case
//! seeded within a few seconds of a day boundary could differ; the harness
//! seeds half a day past the boundary.

use std::collections::HashMap;

use axum::extract::State;
use axum::http::HeaderMap;
use axum::{Extension, Json};
use chrono::{DateTime, NaiveDate, NaiveDateTime, NaiveTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::Row;

use crate::auth::{self, warehouse_scope as wscope, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{date_fromisoformat, isoformat_utc, truthy};
use crate::pyjson;
use crate::routes::ok;
use crate::state::AppState;

pub const READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };

pub const SALES_STALE_DAYS: i64 = 14;
pub const SALES_REMINDER_DAYS: i64 = 35;
pub const SALES_BLIND_DAYS: i64 = 45;
pub const STOCK_STALE_DAYS: i64 = 7;
pub const STOCK_BLIND_DAYS: i64 = 21;

/// `_age_days`: `max(0, (now - ts).days)`; `timedelta.days` floors.
fn age_days(ts: Option<DateTime<Utc>>, now: DateTime<Utc>) -> Option<i64> {
    let ts = ts?;
    let micros = (now - ts).num_microseconds().unwrap_or_else(|| (now - ts).num_seconds().saturating_mul(1_000_000));
    Some(micros.div_euclid(86_400_000_000).max(0))
}

/// `_state`.
fn state(age: Option<i64>, stale_at: i64, blind_at: i64) -> &'static str {
    match age {
        None => "unknown",
        Some(a) if a >= blind_at => "blind",
        Some(a) if a >= stale_at => "stale",
        Some(_) => "fresh",
    }
}

fn opt<T: Into<Value>>(v: Option<T>) -> Value {
    v.map(Into::into).unwrap_or(Value::Null)
}

/// `datetime.fromisoformat(s).replace(tzinfo=timezone.utc)` for the shapes a
/// profiler writes: a date (any form `date.fromisoformat` takes), optionally
/// followed by `T` or a space and `HH[:MM[:SS[.ffffff]]]` (or the compact
/// `HHMMSS`), optionally followed by `Z` / `+HH:MM`, which `.replace(tzinfo=)`
/// discards. `None` is Python's `ValueError`.
fn from_isoformat_as_utc(s: &str) -> Option<DateTime<Utc>> {
    if !s.is_ascii() {
        return None;
    }
    let date_len = if s.len() >= 10 && s.as_bytes()[4] == b'-' && !s[5..].starts_with('W') {
        10
    } else if s.len() >= 8 && s.as_bytes()[..8].iter().all(u8::is_ascii_digit) {
        8
    } else {
        // Week dates, or a bare date of 7 or 8 characters of the week form.
        s.find(['T', ' ']).unwrap_or(s.len())
    };
    let (date_part, rest) = s.split_at(date_len.min(s.len()));
    let date: NaiveDate = date_fromisoformat(date_part)?;
    let time = if rest.is_empty() {
        NaiveTime::from_hms_opt(0, 0, 0)?
    } else {
        parse_time(&rest[1..])?
    };
    Some(NaiveDateTime::new(date, time).and_utc())
}

fn parse_time(t: &str) -> Option<NaiveTime> {
    // Strip a UTC designator or numeric offset (discarded by `.replace`).
    let end = t.find(['Z', 'z', '+', '-']).unwrap_or(t.len());
    let (clock, zone) = t.split_at(end);
    if !zone.is_empty() && !matches!(zone.as_bytes()[0], b'Z' | b'z' | b'+' | b'-') {
        return None;
    }
    let (clock, frac) = match clock.split_once('.').or_else(|| clock.split_once(',')) {
        Some((c, f)) => (c, Some(f)),
        None => (clock, None),
    };
    let digits = |x: &str| !x.is_empty() && x.bytes().all(|b| b.is_ascii_digit());
    let (h, m, sec) = if clock.contains(':') {
        let mut it = clock.split(':');
        let h = it.next()?;
        let m = it.next();
        let sec = it.next();
        if it.next().is_some() || h.len() != 2 || !digits(h) {
            return None;
        }
        if let Some(m) = m {
            if m.len() != 2 || !digits(m) {
                return None;
            }
        }
        if let Some(sec) = sec {
            if sec.len() != 2 || !digits(sec) {
                return None;
            }
        }
        (h, m.unwrap_or("00"), sec.unwrap_or("00"))
    } else {
        match clock.len() {
            2 if digits(clock) => (&clock[0..2], "00", "00"),
            4 if digits(clock) => (&clock[0..2], &clock[2..4], "00"),
            6 if digits(clock) => (&clock[0..2], &clock[2..4], &clock[4..6]),
            _ => return None,
        }
    };
    let micros: u32 = match frac {
        None => 0,
        Some(f) if digits(f) => {
            let mut f6: String = f.chars().take(6).collect();
            while f6.len() < 6 {
                f6.push('0');
            }
            f6.parse().ok()?
        }
        Some(_) => return None,
    };
    NaiveTime::from_hms_micro_opt(h.parse().ok()?, m.parse().ok()?, sec.parse().ok()?, micros)
}

async fn stock_freshness(pool: &sqlx::PgPool, tenant_id: &str, now: DateTime<Utc>) -> Result<Value, sqlx::Error> {
    let row = sqlx::query("SELECT MAX(updated_at) AS last_update, COUNT(*) AS n FROM inventory_stock WHERE tenant_id = $1")
        .bind(tenant_id)
        .fetch_one(pool)
        .await?;
    let last: Option<DateTime<Utc>> = row.try_get("last_update")?;
    let tracked: i64 = row.try_get("n")?;
    let age = age_days(last, now);
    Ok(json!({
        "tracked_skus": tracked,
        "updated_at": opt(last.map(|d| isoformat_utc(&d))),
        "age_days": opt(age),
        "state": state(age, STOCK_STALE_DAYS, STOCK_BLIND_DAYS),
        "stale_days": STOCK_STALE_DAYS,
        "blind_days": STOCK_BLIND_DAYS,
    }))
}

async fn sales_freshness(pool: &sqlx::PgPool, tenant_id: &str, now: DateTime<Utc>) -> Result<Value, sqlx::Error> {
    let row = sqlx::query(
        "SELECT s.id AS session_id, s.name AS session_name, s.updated_at,
                sc.inspection #>> '{profile,stats,date_max}' AS data_through
         FROM sessions s
         LEFT JOIN session_configs sc ON sc.session_id = s.id
         WHERE s.tenant_id = $1 AND s.status = 'COMPLETED'
           AND s.archived_at IS NULL AND NOT s.is_backtest
         ORDER BY s.updated_at DESC LIMIT 1",
    )
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    let Some(row) = row else {
        return Ok(json!({
            "session_id": null, "session_name": null, "trained_at": null,
            "data_through": null, "basis": null, "age_days": null,
            "state": "unknown",
            "stale_days": SALES_STALE_DAYS, "reminder_days": SALES_REMINDER_DAYS,
            "blind_days": SALES_BLIND_DAYS,
        }));
    };
    let session_id: String = row.try_get("session_id")?;
    let session_name: String = row.try_get("session_name")?;
    let updated_at: Option<DateTime<Utc>> = row.try_get("updated_at")?;
    let data_through: Option<String> = row.try_get("data_through")?;

    let mut age = None;
    let mut basis = None;
    if let Some(dt) = data_through.as_deref().filter(|s| !s.is_empty()) {
        if let Some(last) = from_isoformat_as_utc(dt) {
            age = age_days(Some(last), now);
            basis = Some("data_date");
        }
    }
    if age.is_none() {
        age = age_days(updated_at, now);
        basis = Some("upload_date");
    }
    Ok(json!({
        "session_id": session_id,
        "session_name": session_name,
        "trained_at": opt(updated_at.map(|d| isoformat_utc(&d))),
        "data_through": opt(data_through.filter(|s| !s.is_empty())),
        "basis": opt(basis),
        "age_days": opt(age),
        "state": state(age, SALES_STALE_DAYS, SALES_BLIND_DAYS),
        "stale_days": SALES_STALE_DAYS,
        "reminder_days": SALES_REMINDER_DAYS,
        "blind_days": SALES_BLIND_DAYS,
    }))
}

/// `_norm_name`: `str(name or "").strip().casefold()`.
fn norm_name(name: &str) -> String {
    wscope::py_casefold(crate::pycompat::py_strip(name))
}

/// `{store: ISO date}` from the newest completed session's result.
async fn store_data_through(pool: &sqlx::PgPool, tenant_id: &str) -> Result<Map<String, Value>, sqlx::Error> {
    let row: Option<(Option<Value>,)> = sqlx::query_as(
        "SELECT sr.training_result -> 'store_data_through' AS through
         FROM sessions s
         JOIN session_results sr ON sr.session_id = s.id
         WHERE s.tenant_id = $1 AND s.status = 'COMPLETED'
           AND s.archived_at IS NULL AND NOT s.is_backtest
         ORDER BY s.updated_at DESC LIMIT 1",
    )
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    Ok(match row.and_then(|r| r.0) {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    })
}

async fn warehouse_freshness(pool: &sqlx::PgPool, tenant_id: &str, now: DateTime<Utc>) -> Result<Value, sqlx::Error> {
    let stock_rows: Vec<(Option<String>, Option<DateTime<Utc>>, i64)> = sqlx::query_as(
        "SELECT warehouse, MAX(updated_at) AS last_update, COUNT(*) AS skus
         FROM inventory_stock WHERE tenant_id = $1 GROUP BY warehouse",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let registered: Vec<(String,)> = sqlx::query_as("SELECT name FROM warehouses WHERE tenant_id = $1")
        .bind(tenant_id)
        .fetch_all(pool)
        .await?;
    let mut sales_through: HashMap<String, Value> = HashMap::new();
    for (k, v) in store_data_through(pool, tenant_id).await? {
        sales_through.insert(norm_name(&k), v);
    }

    // `names.setdefault(norm, name)`: the first spelling seen wins.
    let mut names: Vec<(String, String)> = Vec::new();
    let all_names = registered
        .iter()
        .map(|(n,)| n.clone())
        .chain(stock_rows.iter().map(|(w, ..)| w.clone().unwrap_or_default()));
    for name in all_names {
        let key = norm_name(&name);
        if !names.iter().any(|(k, _)| *k == key) {
            names.push((key, name));
        }
    }
    let mut stock_by: HashMap<String, &(Option<String>, Option<DateTime<Utc>>, i64)> = HashMap::new();
    for r in &stock_rows {
        stock_by.insert(norm_name(r.0.as_deref().unwrap_or("")), r);
    }
    names.sort_by_key(|(_, name)| wscope::py_casefold(name));

    let mut items: Vec<Map<String, Value>> = Vec::new();
    for (key, name) in &names {
        let stock = stock_by.get(key);
        let stock_at = stock.and_then(|s| s.1);
        let stock_age = age_days(stock_at, now);
        let sales_date = sales_through.get(key).filter(|v| truthy(v));
        let sales_age = sales_date.and_then(|v| from_isoformat_as_utc(&pyjson::str_of(v))).and_then(|d| age_days(Some(d), now));
        let silent = [stock_age, sales_age].into_iter().flatten().min();
        let mut m = Map::new();
        m.insert("name".into(), json!(name));
        m.insert("tracked_skus".into(), json!(stock.map(|s| s.2).unwrap_or(0)));
        m.insert("stock_updated_at".into(), opt(stock_at.map(|d| isoformat_utc(&d))));
        m.insert("stock_age_days".into(), opt(stock_age));
        m.insert("sales_through".into(), opt(sales_date.map(pyjson::str_of)));
        m.insert("sales_age_days".into(), opt(sales_age));
        m.insert("silent_days".into(), opt(silent));
        m.insert(
            "state".into(),
            json!(match silent {
                None => "unknown",
                Some(d) if d >= STOCK_STALE_DAYS => "stale",
                Some(_) => "fresh",
            }),
        );
        items.push(m);
    }
    let multi = items.len() > 1;
    let any_current = items.iter().any(|i| i["state"] == "fresh");
    for i in &mut items {
        let lagging = multi && any_current && i["state"] == "stale";
        i.insert("lagging".into(), json!(lagging));
    }
    let lagging: Vec<Value> = items.iter().filter(|i| i["lagging"] == true).map(|i| i["name"].clone()).collect();
    Ok(json!({
        "multi": multi,
        "stale_days": STOCK_STALE_DAYS,
        "items": items,
        "lagging": lagging,
    }))
}

fn s_state<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key]["state"].as_str().unwrap_or("")
}

pub async fn get_data_freshness(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, READ, &actors).await?;
    let pool = &state.pool;
    let now = Utc::now();
    let sales = sales_freshness(pool, &user.tenant_id, now).await?;
    let stock = stock_freshness(pool, &user.tenant_id, now).await?;
    let warehouses = warehouse_freshness(pool, &user.tenant_id, now).await?;

    let mut reasons: Vec<&str> = Vec::new();
    if stock["state"] == "blind" {
        reasons.push("stock");
    }
    if sales["state"] == "blind" {
        reasons.push("sales");
    }
    let warn = !reasons.is_empty()
        || sales["state"] == "stale"
        || stock["state"] == "stale"
        || warehouses["lagging"].as_array().map(|a| !a.is_empty()).unwrap_or(false);
    let mut data = json!({
        "sales": sales,
        "stock": stock,
        "warehouses": warehouses,
        "semaphore": if reasons.is_empty() { "current" } else { "degraded" },
        "degraded_by": reasons,
        "warn": warn,
    });

    if let Some(names) = wscope::scope_names(pool, &user).await? {
        let scope = Some(names);
        let block = data["warehouses"].as_object().cloned().unwrap_or_default();
        let items: Vec<Value> = block
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|r| wscope::in_scope(&scope, r["name"].as_str()))
            .collect();
        let lagging: Vec<Value> = items.iter().filter(|i| truthy(&i["lagging"])).map(|i| i["name"].clone()).collect();
        let mut new_block = block;
        new_block.insert("items".into(), json!(items));
        new_block.insert("multi".into(), json!(items.len() > 1));
        new_block.insert("lagging".into(), json!(lagging));
        let any_lagging = !lagging.is_empty();
        data["warehouses"] = Value::Object(new_block);
        data["warn"] = json!(
            truthy(&data["degraded_by"])
                || s_state(&data, "sales") == "stale"
                || s_state(&data, "stock") == "stale"
                || any_lagging
        );
    }
    Ok(ok(data))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn ages_floor_and_clamp() {
        let now = Utc.with_ymd_and_hms(2026, 10, 6, 12, 0, 0).unwrap();
        let t = |d, h| Utc.with_ymd_and_hms(2026, 10, d, h, 0, 0).unwrap();
        assert_eq!(age_days(Some(t(5, 13)), now), Some(0));
        assert_eq!(age_days(Some(t(5, 12)), now), Some(1));
        assert_eq!(age_days(Some(t(7, 0)), now), Some(0)); // future, clamped
        assert_eq!(age_days(None, now), None);
    }

    #[test]
    fn states_use_the_two_clocks() {
        assert_eq!(state(None, 7, 21), "unknown");
        assert_eq!(state(Some(6), 7, 21), "fresh");
        assert_eq!(state(Some(7), 7, 21), "stale");
        assert_eq!(state(Some(21), 7, 21), "blind");
    }

    #[test]
    fn iso_dates_and_datetimes_parse_like_python() {
        let d = |s: &str| from_isoformat_as_utc(s).map(|x| x.to_rfc3339());
        assert_eq!(d("2026-09-30").as_deref(), Some("2026-09-30T00:00:00+00:00"));
        assert_eq!(d("20260930").as_deref(), Some("2026-09-30T00:00:00+00:00"));
        assert_eq!(d("2026-09-30 10:30:15").as_deref(), Some("2026-09-30T10:30:15+00:00"));
        assert_eq!(d("2026-09-30T10:30:15.5Z").as_deref(), Some("2026-09-30T10:30:15.500+00:00"));
        assert_eq!(d("2026-09-30T10:30+05:00").as_deref(), Some("2026-09-30T10:30:00+00:00"));
        assert_eq!(d("not a date"), None);
        assert_eq!(d("2026-13-01"), None);
        assert_eq!(d("2026-09-30T25:00"), None);
    }
}
