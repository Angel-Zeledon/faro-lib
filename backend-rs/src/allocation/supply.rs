//! Reads what an allocation needs: the open commitments and, per SKU, the stock
//! on hand and the arrivals that can be counted. Every statement is a SELECT.
//!
//! This reads the SAME inputs, by the SAME rules, as the commitment
//! fulfillment outlook (`feat/commitment-fulfillment`, `fulfillment/data.rs`),
//! because both answer "can the supply cover the open commitments":
//!
//! * commitments: every OPEN row (`on_top_of_base` or not: a customer who is
//!   already inside the baseline still wants the units), company-wide, the
//!   warehouse named on a commitment is ignored like `annotate_risk` ignores it;
//! * stock: `inventory_stock.current_stock` summed over every warehouse; a SKU
//!   with NO stock row has no figure (`None`) and gets no allocation, never a
//!   zero that would read as "everything is short";
//! * arrivals, per LINE: open purchase-order lines (`approved`/`modified` on a
//!   receivable, uncancelled order, units not yet received) dated by an
//!   accepted supplier promise, else generated + the supplier's lead time
//!   (learned from at least three real receptions, else the supplier card's
//!   DECLARED figure); a line with no date, or whose date already passed while
//!   the goods have not arrived, is NOT counted as supply (it is listed in
//!   `uncounted` so the user sees what was left out); transfers in transit are
//!   available now.
//!
//! When the fulfillment branch is merged this file should be replaced by a
//! call into its loader: both return `(day, qty)` arrivals, so
//! `allocation::core` does not change. Until then it is the one place that
//! duplicates those reads, and `docs/rust-migration.md` says so.

use std::collections::{BTreeMap, HashMap};

use chrono::{DateTime, Datelike, NaiveDate, Utc};
use sqlx::{PgPool, Row};

use crate::allocation::core::to_micro;
use crate::error::ApiError;
use crate::pycompat::py_strip;
use crate::routes::po_payments::format_po_number;

/// `service.MIN_LEAD_TIME_OBSERVATIONS`.
const MIN_LEAD_TIME_OBSERVATIONS: i64 = 3;

pub fn day_number(d: NaiveDate) -> i64 {
    i64::from(d.num_days_from_ce())
}

#[derive(Clone, Debug)]
pub struct OpenCommitment {
    pub id: String,
    pub sku: String,
    pub customer: Option<String>,
    pub delivery_date: NaiveDate,
    pub quantity: f64,
    pub probability: f64,
}

/// One arrival and how it was dated, for the response.
#[derive(Clone, Debug)]
pub struct ArrivalDetail {
    pub kind: &'static str, // "po" | "transfer"
    pub reference: String,
    pub qty_micro: i64,
    pub date: Option<NaiveDate>,
    /// supplier_promise | lead_time | transfer | no_lead_time | overdue
    pub source: &'static str,
}

#[derive(Clone, Debug, Default)]
pub struct SkuSupply {
    /// Micro-units on hand across warehouses; None = no stock row at all.
    pub stock_micro: Option<i64>,
    pub counted: Vec<ArrivalDetail>,
    pub uncounted: Vec<ArrivalDetail>,
}

/// Open commitments of the tenant, optionally one SKU, oldest delivery first.
pub async fn open_commitments(pool: &PgPool, tenant_id: &str, sku: Option<&str>) -> Result<Vec<OpenCommitment>, ApiError> {
    let rows = sqlx::query(
        "SELECT id, sku, customer, delivery_date, quantity::float8 AS quantity, probability::float8 AS probability
           FROM committed_demand
          WHERE tenant_id = $1 AND status = 'open' AND ($2::text IS NULL OR sku = $2)
          ORDER BY sku, delivery_date, created_at, id",
    )
    .bind(tenant_id)
    .bind(sku)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        out.push(OpenCommitment {
            id: r.try_get("id")?,
            sku: r.try_get("sku")?,
            customer: r.try_get("customer")?,
            delivery_date: r.try_get("delivery_date")?,
            quantity: r.try_get("quantity")?,
            probability: r.try_get("probability")?,
        });
    }
    Ok(out)
}

/// Stock and arrivals of the given SKUs.
pub async fn load_supply(pool: &PgPool, tenant_id: &str, skus: &[String], today: NaiveDate) -> Result<BTreeMap<String, SkuSupply>, ApiError> {
    let mut out: BTreeMap<String, SkuSupply> = skus.iter().map(|s| (s.clone(), SkuSupply::default())).collect();
    if skus.is_empty() {
        return Ok(out);
    }

    // ── Stock ───────────────────────────────────────────────────────────────
    let rows = sqlx::query(
        "SELECT sku, SUM(current_stock)::float8 AS stock
           FROM inventory_stock WHERE tenant_id = $1 AND sku = ANY($2) GROUP BY sku",
    )
    .bind(tenant_id)
    .bind(skus)
    .fetch_all(pool)
    .await?;
    for r in &rows {
        let sku: String = r.try_get("sku")?;
        let stock: Option<f64> = r.try_get("stock")?;
        if let (Some(s), Some(entry)) = (stock, out.get_mut(&sku)) {
            if s.is_finite() {
                entry.stock_micro = Some((s.max(0.0) * 1e6).round().min(9.0e15) as i64);
            }
        }
    }

    // ── Lead-time evidence ──────────────────────────────────────────────────
    let learned = sqlx::query(
        "SELECT LOWER(supplier) AS supplier, AVG(lead_time_days)::float8 AS avg_days, COUNT(*)::int8 AS n
           FROM supplier_lead_time_obs WHERE tenant_id = $1 GROUP BY LOWER(supplier)",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut learned_by: HashMap<String, (f64, i64)> = HashMap::new();
    for r in &learned {
        let name: Option<String> = r.try_get("supplier")?;
        let avg: Option<f64> = r.try_get("avg_days")?;
        let n: i64 = r.try_get("n")?;
        if let (Some(name), Some(avg)) = (name, avg) {
            learned_by.insert(name, (avg, n));
        }
    }
    let cards = sqlx::query(
        "SELECT name, lead_time_days FROM suppliers
          WHERE tenant_id = $1 AND active = TRUE AND lead_time_days IS NOT NULL AND lead_time_set_by IS NOT NULL
          ORDER BY name",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut card_by: HashMap<String, i64> = HashMap::new();
    for r in &cards {
        let name: String = r.try_get("name")?;
        let days: i32 = r.try_get("lead_time_days")?;
        card_by.entry(py_strip(&name).to_lowercase()).or_insert(i64::from(days));
    }

    // ── Purchase-order lines ────────────────────────────────────────────────
    let lines = sqlx::query(
        "SELECT poi.id AS item_id, poi.sku, poi.supplier,
                GREATEST(poi.final_qty - COALESCE(poi.received_qty, 0), 0)::float8 AS qty,
                pol.id AS po_id, pol.po_number, pol.generated_at
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
          WHERE poi.tenant_id = $1 AND pol.tenant_id = $1
            AND pol.reception_status IN ('pending', 'partial', 'not_received')
            AND pol.cancelled_at IS NULL
            AND poi.status IN ('approved', 'modified')
            AND poi.sku = ANY($2)
          ORDER BY pol.po_number NULLS LAST, pol.id, poi.id",
    )
    .bind(tenant_id)
    .bind(skus)
    .fetch_all(pool)
    .await?;
    let po_ids: Vec<String> = {
        let mut v: Vec<String> = Vec::new();
        for r in &lines {
            v.push(r.try_get("po_id")?);
        }
        v.sort();
        v.dedup();
        v
    };
    let promises: HashMap<String, NaiveDate> = if po_ids.is_empty() {
        HashMap::new()
    } else {
        let rows = sqlx::query(
            "SELECT c.po_item_id, c.promised_date
               FROM po_confirmation_acceptances a
               JOIN po_line_confirmations c ON c.id = a.confirmation_id
              WHERE a.tenant_id = $1 AND c.po_log_id = ANY($2)
                AND c.status = 'changed' AND c.promised_date IS NOT NULL
                AND c.revision = (SELECT MAX(c2.revision) FROM po_line_confirmations c2
                                   WHERE c2.request_id = c.request_id AND c2.po_item_id = c.po_item_id)",
        )
        .bind(tenant_id)
        .bind(&po_ids)
        .fetch_all(pool)
        .await?;
        let mut m = HashMap::new();
        for r in &rows {
            m.insert(r.try_get::<String, _>("po_item_id")?, r.try_get::<NaiveDate, _>("promised_date")?);
        }
        m
    };
    for r in &lines {
        let sku: String = r.try_get("sku")?;
        let qty: f64 = r.try_get("qty")?;
        if qty <= 0.0 || !qty.is_finite() {
            continue;
        }
        let supplier: Option<String> = r.try_get("supplier")?;
        let item_id: String = r.try_get("item_id")?;
        let po_id: String = r.try_get("po_id")?;
        let po_number: Option<i32> = r.try_get("po_number")?;
        let generated: Option<DateTime<Utc>> = r.try_get("generated_at")?;
        let key = supplier.as_deref().map(|s| py_strip(s).to_lowercase()).unwrap_or_default();
        let lead = po_lead_days(learned_by.get(&key).copied(), card_by.get(&key).copied());
        let (date, source) = date_line(promises.get(&item_id).copied(), generated.map(|g| g.date_naive()), lead, today);
        let detail = ArrivalDetail {
            kind: "po",
            reference: format_po_number(po_number, &po_id),
            qty_micro: to_micro(qty, 1.0),
            date,
            source,
        };
        if let Some(entry) = out.get_mut(&sku) {
            if date.is_some() { entry.counted.push(detail) } else { entry.uncounted.push(detail) }
        }
    }

    // ── Transfers in transit: available now ─────────────────────────────────
    let transfers = sqlx::query(
        "SELECT tri.sku, trl.from_warehouse, trl.id AS transfer_id,
                SUM(GREATEST(tri.qty_sent - COALESCE(tri.qty_received, 0), 0))::float8 AS qty
           FROM inventory_transfer_items tri
           JOIN inventory_transfer_log trl ON trl.id = tri.transfer_id
          WHERE tri.tenant_id = $1 AND trl.status IN ('in_transit', 'partial') AND tri.sku = ANY($2)
          GROUP BY tri.sku, trl.to_warehouse, trl.id, trl.from_warehouse
          ORDER BY trl.id",
    )
    .bind(tenant_id)
    .bind(skus)
    .fetch_all(pool)
    .await?;
    for r in &transfers {
        let sku: String = r.try_get("sku")?;
        let qty: f64 = r.try_get::<Option<f64>, _>("qty")?.unwrap_or(0.0);
        if qty <= 0.0 || !qty.is_finite() {
            continue;
        }
        if let Some(entry) = out.get_mut(&sku) {
            entry.counted.push(ArrivalDetail {
                kind: "transfer",
                reference: r.try_get("from_warehouse")?,
                qty_micro: to_micro(qty, 1.0),
                date: Some(today),
                source: "transfer",
            });
        }
    }
    Ok(out)
}

/// `po_lead_days`: learned from at least three receptions (average above zero,
/// rounded up), else the supplier card's declared figure, else None. Never the
/// 15-day system default: an assumed number is not a date.
fn po_lead_days(learned: Option<(f64, i64)>, card: Option<i64>) -> Option<i64> {
    if let Some((avg, n)) = learned {
        if n >= MIN_LEAD_TIME_OBSERVATIONS && avg > 0.0 {
            return Some(avg.ceil() as i64);
        }
    }
    card
}

/// The date to plan a line on: an accepted supplier promise wins, else
/// generated + lead time, else no date; a date already past while the goods
/// have not arrived is not a date to plan on either.
fn date_line(promise: Option<NaiveDate>, generated: Option<NaiveDate>, lead: Option<i64>, today: NaiveDate)
    -> (Option<NaiveDate>, &'static str)
{
    let (expected, source) = match (promise, generated, lead) {
        (Some(p), _, _) => (p, "supplier_promise"),
        (None, Some(g), Some(l)) => (g + chrono::Duration::days(l), "lead_time"),
        _ => return (None, "no_lead_time"),
    };
    if expected < today {
        return (None, "overdue");
    }
    (Some(expected), source)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(y: i32, m: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, day).unwrap()
    }

    #[test]
    fn day_numbers_round_trip() {
        assert_eq!(NaiveDate::from_num_days_from_ce_opt(day_number(d(2026, 10, 6)) as i32), Some(d(2026, 10, 6)));
        assert_eq!(day_number(d(2026, 10, 7)) - day_number(d(2026, 10, 6)), 1);
    }

    #[test]
    fn lead_time_needs_evidence_and_never_defaults() {
        assert_eq!(po_lead_days(Some((10.2, 3)), Some(30)), Some(11));
        assert_eq!(po_lead_days(Some((10.2, 2)), Some(30)), Some(30)); // too few receptions: the card
        assert_eq!(po_lead_days(Some((10.2, 2)), None), None); // nothing declared: no date
        assert_eq!(po_lead_days(None, None), None);
        assert_eq!(po_lead_days(Some((0.0, 9)), None), None);
    }

    #[test]
    fn a_line_is_dated_by_promise_then_lead_time_and_never_by_a_guess() {
        let today = d(2026, 10, 6);
        assert_eq!(date_line(Some(d(2026, 11, 1)), Some(d(2026, 9, 1)), Some(5), today), (Some(d(2026, 11, 1)), "supplier_promise"));
        assert_eq!(date_line(None, Some(d(2026, 10, 1)), Some(10), today), (Some(d(2026, 10, 11)), "lead_time"));
        assert_eq!(date_line(None, Some(d(2026, 10, 1)), None, today), (None, "no_lead_time"));
        // expected on a day that already passed, goods not here: not supply to plan on
        assert_eq!(date_line(None, Some(d(2026, 9, 1)), Some(10), today), (None, "overdue"));
        // today itself is still plannable
        assert_eq!(date_line(None, Some(d(2026, 9, 26)), Some(10), today), (Some(today), "lead_time"));
    }
}
