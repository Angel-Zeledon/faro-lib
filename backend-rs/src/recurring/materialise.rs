//! Materialising a recurring delivery schedule into `committed_demand` rows.
//!
//! Rules that keep it honest (the same ones blanket contracts follow in
//! `backend/inventory/supply_contract_service.py`, which this reuses):
//!
//! 1. **Ordinary commitments.** A row is a plain `committed_demand` row with
//!    `source = 'contract'`, `contract_id = contract_root_id = <schedule id>`
//!    and `contract_release_date = <nominal date>`. The planning maths, the
//!    at-risk check and the assistant see it through the code path they
//!    already use, and the existing lock (sku, warehouse, customer,
//!    probability and the on-top flag cannot be edited on the row) applies.
//! 2. **Idempotent.** One live row per schedule, SKU and nominal date, backed
//!    by the partial unique index; every insert is `ON CONFLICT DO NOTHING`.
//!    Running a pass twice, or a pass racing an edit, never duplicates.
//! 3. **Only the future is revised.** An edit rewrites or withdraws a row only
//!    when it is still `open` and its delivery date is today or later.
//!    Fulfilled and cancelled rows, withdrawn rows and past (overdue) rows are
//!    history and are never touched; they still hold their nominal date's
//!    slot, so a fulfilled delivery is not called off twice.
//! 4. **Withdraw, never delete.** A row the schedule no longer wants (shorter
//!    end date, other weekday, paused, cancelled) is cancelled and stamped
//!    `contract_withdrawn_at`; the stamp frees its slot, so resuming the
//!    schedule materialises it again.
//! 5. **Nothing past today.** Only deliveries from today on are created: a
//!    schedule that starts in the past does not invent a year of overdue
//!    orders.
//! 6. **Failures are visible.** The periodic pass records each schedule's
//!    failure on the schedule row and reports the pass `failed` in `/health`.

use std::collections::{BTreeSet, HashMap, HashSet};

use chrono::{DateTime, Duration, Local, NaiveDate, Utc};
use serde_json::Value;
use sqlx::postgres::PgRow;
use sqlx::{PgConnection, PgPool, Row};

use super::dates::{occurrences, DateSpec, Frequency, Occurrence, ShiftRule};

/// The server's local date, as Python's `date.today()` reads it.
pub fn today() -> NaiveDate {
    Local::now().date_naive()
}

/// A stored schedule.
#[derive(Debug, Clone)]
pub struct Schedule {
    pub id: String,
    pub tenant_id: String,
    pub customer: String,
    pub reference: Option<String>,
    pub sku: String,
    pub warehouse_id: Option<String>,
    pub quantity: f64,
    pub spec: DateSpec,
    pub horizon_days: i32,
    pub on_top_of_base: bool,
    pub note: Option<String>,
    pub status: String,
    pub revision: i32,
    pub created_by: String,
    pub last_materialised_at: Option<DateTime<Utc>>,
    pub last_materialise_error: Option<String>,
}

pub const COLUMNS: &str = "id, tenant_id, customer, reference, sku, warehouse_id, quantity, frequency, weekday,
    day_of_month, start_date, end_date, holiday_dates, avoid_weekends, shift_rule, horizon_days,
    on_top_of_base, note, status, revision, created_by, last_materialised_at, last_materialise_error";

pub fn row_schedule(r: &PgRow) -> Result<Schedule, sqlx::Error> {
    let freq: String = r.try_get("frequency")?;
    let rule: String = r.try_get("shift_rule")?;
    let holidays_json: Value = r.try_get("holiday_dates")?;
    let holidays: BTreeSet<NaiveDate> = holidays_json
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .filter_map(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
                .collect()
        })
        .unwrap_or_default();
    let weekday: Option<i32> = r.try_get("weekday")?;
    let day_of_month: Option<i32> = r.try_get("day_of_month")?;
    let spec = DateSpec {
        frequency: Frequency::parse(&freq).ok_or_else(|| sqlx::Error::Decode(format!("frequency {freq}").into()))?,
        weekday: weekday.map(|w| w as u32),
        day_of_month: day_of_month.map(|w| w as u32),
        start: r.try_get("start_date")?,
        end: r.try_get("end_date")?,
        holidays,
        avoid_weekends: r.try_get("avoid_weekends")?,
        shift_rule: ShiftRule::parse(&rule).ok_or_else(|| sqlx::Error::Decode(format!("shift_rule {rule}").into()))?,
    };
    Ok(Schedule {
        id: r.try_get("id")?,
        tenant_id: r.try_get("tenant_id")?,
        customer: r.try_get("customer")?,
        reference: r.try_get("reference")?,
        sku: r.try_get("sku")?,
        warehouse_id: r.try_get("warehouse_id")?,
        quantity: r.try_get("quantity")?,
        spec,
        horizon_days: r.try_get("horizon_days")?,
        on_top_of_base: r.try_get("on_top_of_base")?,
        note: r.try_get("note")?,
        status: r.try_get("status")?,
        revision: r.try_get("revision")?,
        created_by: r.try_get("created_by")?,
        last_materialised_at: r.try_get("last_materialised_at")?,
        last_materialise_error: r.try_get("last_materialise_error")?,
    })
}

/// Load one schedule of a tenant, optionally locking its row for the rest of
/// the transaction.
pub async fn load(conn: &mut PgConnection, tenant_id: &str, id: &str, lock: bool) -> Result<Option<Schedule>, sqlx::Error> {
    let sql = format!(
        "SELECT {COLUMNS} FROM recurring_delivery_schedules WHERE tenant_id = $1 AND id = $2{}",
        if lock { " FOR UPDATE" } else { "" }
    );
    match sqlx::query(&sql).bind(tenant_id).bind(id).fetch_optional(&mut *conn).await? {
        Some(r) => Ok(Some(row_schedule(&r)?)),
        None => Ok(None),
    }
}

/// How far ahead rows are materialised, as `materialise_horizon_days` in
/// `supply_contract_service.py`: at least the schedule's own horizon, and
/// always the longest lead time of its SKU plus a review margin. A delivery
/// the protection interval can see but that was not materialised yet would be
/// under-bought, silently. Capped so a typo'd lead time cannot dump years of
/// deliveries into the ledger.
pub const HORIZON_REVIEW_MARGIN_DAYS: i64 = 60;
pub const MAX_HORIZON_DAYS: i64 = 730;

pub fn horizon_for(own_days: i32, longest_lead_days: f64) -> i64 {
    let lead = if longest_lead_days.is_finite() { longest_lead_days.max(0.0) } else { 0.0 };
    let want = i64::from(own_days).max(lead.ceil() as i64 + HORIZON_REVIEW_MARGIN_DAYS);
    want.min(MAX_HORIZON_DAYS)
}

/// `_longest_lead`: the longest lead time recorded for the SKU, from stock
/// defaults and supplier terms.
pub async fn longest_lead(pool: &PgPool, tenant_id: &str, sku: &str) -> Result<f64, sqlx::Error> {
    let (lead,): (Option<f64>,) = sqlx::query_as(
        "SELECT GREATEST(
              COALESCE((SELECT MAX(lead_time_days) FROM inventory_stock
                         WHERE tenant_id = $1 AND sku = $2), 0),
              COALESCE((SELECT MAX(lead_time_days) FROM sku_suppliers
                         WHERE tenant_id = $1 AND sku = $2), 0))::float8",
    )
    .bind(tenant_id)
    .bind(sku)
    .fetch_one(pool)
    .await?;
    Ok(lead.unwrap_or(0.0))
}

pub async fn effective_horizon(pool: &PgPool, s: &Schedule) -> Result<i64, sqlx::Error> {
    Ok(horizon_for(s.horizon_days, longest_lead(pool, &s.tenant_id, &s.sku).await?))
}

/// Where the commitments of a schedule are wanted: delivery dates in
/// `[today, today + horizon]`.
pub fn wanted(s: &Schedule, today: NaiveDate, horizon: i64) -> Vec<Occurrence> {
    occurrences(&s.spec, today, today + Duration::days(horizon))
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Outcome {
    pub created: u32,
    pub updated: u32,
    pub withdrawn: u32,
}

const INSERT_ROW: &str = "
    INSERT INTO committed_demand
        (tenant_id, sku, warehouse_id, delivery_date, quantity, customer,
         probability, on_top_of_base, note, created_by, source, contract_id,
         contract_root_id, contract_release_date)
    VALUES ($1, $2, $3, $4, $5, $6, 1.0, $7, $8, $9, 'contract', $10, $10, $11)
    ON CONFLICT (tenant_id, contract_root_id, sku, contract_release_date)
        WHERE contract_root_id IS NOT NULL AND contract_withdrawn_at IS NULL
    DO NOTHING
    RETURNING id";

/// The note a materialised row carries: the schedule's reference, else its note.
fn row_note(s: &Schedule) -> Option<String> {
    s.reference.clone().filter(|r| !r.is_empty())
}

async fn insert_missing(
    conn: &mut PgConnection,
    s: &Schedule,
    wanted: &[Occurrence],
    existing: &HashSet<NaiveDate>,
) -> Result<u32, sqlx::Error> {
    let mut created = 0;
    for o in wanted.iter().filter(|o| !existing.contains(&o.nominal)) {
        let got = sqlx::query(INSERT_ROW)
            .bind(&s.tenant_id)
            .bind(&s.sku)
            .bind(&s.warehouse_id)
            .bind(o.delivery)
            .bind(s.quantity)
            .bind(&s.customer)
            .bind(s.on_top_of_base)
            .bind(row_note(s))
            .bind(&s.created_by)
            .bind(&s.id)
            .bind(o.nominal)
            .fetch_optional(&mut *conn)
            .await?;
        if got.is_some() {
            created += 1;
        }
    }
    Ok(created)
}

/// Nominal dates already held by a live (non-withdrawn) row of the schedule,
/// whatever its status.
async fn live_keys(conn: &mut PgConnection, s: &Schedule) -> Result<HashSet<NaiveDate>, sqlx::Error> {
    let rows: Vec<(NaiveDate,)> = sqlx::query_as(
        "SELECT contract_release_date FROM committed_demand
          WHERE tenant_id = $1 AND contract_root_id = $2 AND contract_withdrawn_at IS NULL
            AND contract_release_date IS NOT NULL",
    )
    .bind(&s.tenant_id)
    .bind(&s.id)
    .fetch_all(&mut *conn)
    .await?;
    Ok(rows.into_iter().map(|r| r.0).collect())
}

/// The periodic pass for one schedule: insert the commitments it is owed and
/// does not have. Never updates or withdraws anything. A no-op unless active.
pub async fn fill(conn: &mut PgConnection, s: &Schedule, today: NaiveDate, horizon: i64) -> Result<Outcome, sqlx::Error> {
    if s.status != "active" {
        return Ok(Outcome::default());
    }
    let existing = live_keys(conn, s).await?;
    let want = wanted(s, today, horizon);
    let created = insert_missing(conn, s, &want, &existing).await?;
    Ok(Outcome { created, ..Outcome::default() })
}

/// After an edit, a pause or a cancel: bring the future, still-open rows in
/// line with the schedule, then fill what is missing. Runs inside the
/// transaction that saved the schedule, with its row locked.
pub async fn revise(conn: &mut PgConnection, s: &Schedule, today: NaiveDate, horizon: i64) -> Result<Outcome, sqlx::Error> {
    #[allow(clippy::type_complexity)]
    let rows: Vec<(String, Option<NaiveDate>, NaiveDate, f64, Option<String>, Option<String>, bool, Option<String>, String)> =
        sqlx::query_as(
            "SELECT id, contract_release_date, delivery_date, quantity, customer, warehouse_id,
                    on_top_of_base, note, status
               FROM committed_demand
              WHERE tenant_id = $1 AND contract_root_id = $2 AND contract_withdrawn_at IS NULL
              FOR UPDATE",
        )
        .bind(&s.tenant_id)
        .bind(&s.id)
        .fetch_all(&mut *conn)
        .await?;
    let active = s.status == "active";
    let want: HashMap<NaiveDate, NaiveDate> = if active {
        wanted(s, today, horizon).into_iter().map(|o| (o.nominal, o.delivery)).collect()
    } else {
        HashMap::new()
    };
    let note = row_note(s);
    let mut out = Outcome::default();
    let mut keys: HashSet<NaiveDate> = HashSet::new();
    for (id, nominal, delivery, quantity, customer, warehouse_id, on_top, row_note_now, status) in rows {
        if let Some(n) = nominal {
            keys.insert(n);
        }
        // History is never touched: only an open row dated today or later.
        if status != "open" || delivery < today {
            continue;
        }
        match nominal.and_then(|n| want.get(&n)).copied() {
            Some(new_delivery) => {
                let same = new_delivery == delivery
                    && (quantity - s.quantity).abs() < f64::EPSILON
                    && customer.as_deref() == Some(s.customer.as_str())
                    && warehouse_id == s.warehouse_id
                    && on_top == s.on_top_of_base
                    && row_note_now == note;
                if !same {
                    let n = sqlx::query(
                        "UPDATE committed_demand
                            SET delivery_date = $1, quantity = $2, customer = $3, warehouse_id = $4,
                                on_top_of_base = $5, note = $6, updated_at = NOW()
                          WHERE id = $7 AND tenant_id = $8 AND status = 'open'
                            AND contract_withdrawn_at IS NULL",
                    )
                    .bind(new_delivery)
                    .bind(s.quantity)
                    .bind(&s.customer)
                    .bind(&s.warehouse_id)
                    .bind(s.on_top_of_base)
                    .bind(&note)
                    .bind(&id)
                    .bind(&s.tenant_id)
                    .execute(&mut *conn)
                    .await?
                    .rows_affected();
                    out.updated += n as u32;
                }
            }
            None => {
                let n = sqlx::query(
                    "UPDATE committed_demand
                        SET status = 'cancelled', contract_withdrawn_at = NOW(),
                            status_changed_by = $1, status_changed_at = NOW(), updated_at = NOW()
                      WHERE id = $2 AND tenant_id = $3 AND status = 'open'
                        AND contract_withdrawn_at IS NULL",
                )
                .bind(&s.created_by)
                .bind(&id)
                .bind(&s.tenant_id)
                .execute(&mut *conn)
                .await?
                .rows_affected();
                if n > 0 {
                    out.withdrawn += 1;
                    if let Some(nom) = nominal {
                        keys.remove(&nom); // its slot is free again
                    }
                }
            }
        }
    }
    if active {
        let want_list = wanted(s, today, horizon);
        out.created = insert_missing(conn, s, &want_list, &keys).await?;
    }
    Ok(out)
}

// ── The periodic pass ────────────────────────────────────────────────────────

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct PassSummary {
    pub schedules: u32,
    pub created: u32,
    pub failed: u32,
}

/// Materialise every active schedule (of one tenant, or all). One transaction
/// per schedule, so one failure never stops the others; each failure is
/// logged, stored on the schedule row (`last_materialise_error`) and counted.
pub async fn run_pass(pool: &PgPool, tenant_id: Option<&str>, today: NaiveDate) -> Result<PassSummary, sqlx::Error> {
    let targets: Vec<(String, String)> = sqlx::query_as(
        "SELECT tenant_id, id FROM recurring_delivery_schedules
          WHERE status = 'active' AND end_date >= $1 AND ($2::text IS NULL OR tenant_id = $2)
          ORDER BY tenant_id, id",
    )
    .bind(today)
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut summary = PassSummary { schedules: targets.len() as u32, ..PassSummary::default() };
    for (tenant, id) in targets {
        match one_schedule(pool, &tenant, &id, today).await {
            Ok(created) => summary.created += created,
            Err(e) => {
                summary.failed += 1;
                tracing::error!(tenant = %tenant, schedule = %id, error = %e,
                    "recurring delivery materialisation failed");
                let msg: String = e.to_string().chars().take(300).collect();
                let _ = sqlx::query(
                    "UPDATE recurring_delivery_schedules SET last_materialise_error = $1
                      WHERE tenant_id = $2 AND id = $3",
                )
                .bind(msg)
                .bind(&tenant)
                .bind(&id)
                .execute(pool)
                .await;
            }
        }
    }
    Ok(summary)
}

async fn one_schedule(pool: &PgPool, tenant: &str, id: &str, today: NaiveDate) -> Result<u32, sqlx::Error> {
    let mut tx = pool.begin().await?;
    let Some(s) = load(&mut tx, tenant, id, true).await? else {
        return Ok(0);
    };
    let horizon = effective_horizon(pool, &s).await?;
    let outcome = fill(&mut tx, &s, today, horizon).await?;
    sqlx::query(
        "UPDATE recurring_delivery_schedules
            SET last_materialised_at = NOW(), last_materialise_error = NULL
          WHERE tenant_id = $1 AND id = $2",
    )
    .bind(tenant)
    .bind(id)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(outcome.created)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schedule(status: &str) -> Schedule {
        Schedule {
            id: "s1".into(),
            tenant_id: "t1".into(),
            customer: "ACME".into(),
            reference: None,
            sku: "SKU-1".into(),
            warehouse_id: None,
            quantity: 10.0,
            spec: DateSpec {
                frequency: Frequency::Weekly,
                weekday: Some(0),
                day_of_month: None,
                start: NaiveDate::from_ymd_opt(2026, 1, 1).unwrap(),
                end: NaiveDate::from_ymd_opt(2026, 12, 31).unwrap(),
                holidays: BTreeSet::new(),
                avoid_weekends: false,
                shift_rule: ShiftRule::After,
            },
            horizon_days: 30,
            on_top_of_base: true,
            note: None,
            status: status.into(),
            revision: 1,
            created_by: "u1".into(),
            last_materialised_at: None,
            last_materialise_error: None,
        }
    }

    #[test]
    fn wanted_covers_exactly_today_through_the_horizon() {
        let s = schedule("active");
        let today = NaiveDate::from_ymd_opt(2026, 10, 6).unwrap(); // a Tuesday
        let w = wanted(&s, today, 30);
        let first = NaiveDate::from_ymd_opt(2026, 10, 12).unwrap();
        assert_eq!(w.first().unwrap().delivery, first);
        assert!(w.iter().all(|o| o.delivery >= today && o.delivery <= today + Duration::days(30)));
        assert_eq!(w.len(), 4); // Oct 12, 19, 26, Nov 2
    }

    #[test]
    fn nothing_before_today_is_wanted() {
        let s = schedule("active");
        let today = NaiveDate::from_ymd_opt(2026, 10, 6).unwrap();
        assert!(wanted(&s, today, 30).iter().all(|o| o.nominal >= s.spec.start));
        let after_end = NaiveDate::from_ymd_opt(2027, 2, 1).unwrap();
        assert!(wanted(&s, after_end, 30).is_empty());
    }

    #[test]
    fn the_horizon_follows_the_lead_time_with_a_margin() {
        assert_eq!(horizon_for(180, 0.0), 180);
        assert_eq!(horizon_for(180, 100.0), 180);
        assert_eq!(horizon_for(180, 150.5), 211);
        assert_eq!(horizon_for(30, 20.0), 80);
        assert_eq!(horizon_for(180, 9999.0), 730);
        assert_eq!(horizon_for(180, f64::NAN), 180);
    }
}
