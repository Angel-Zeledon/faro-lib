//! Glue between the rows (`supply`) and the arithmetic (`core`): who each
//! commitment belongs to (tier), what the SKU's supply timeline is, and the
//! per-line verdict with a hash that names exactly the inputs and outputs, so
//! "apply" can refuse a preview that no longer describes the data.

use std::collections::HashMap;

use chrono::NaiveDate;
use sha2::{Digest, Sha256};

use crate::allocation::core::{allocate, micro_to_units, to_micro, Arrival, Claim};
use crate::allocation::supply::{day_number, ArrivalDetail, OpenCommitment, SkuSupply};
use crate::auth::warehouse_scope::py_casefold;
use crate::pycompat::py_strip;

pub const DEFAULT_TIER: u8 = 5;
pub const MIN_TIER: u8 = 1;
pub const MAX_TIER: u8 = 9;
/// More commitments than this on one SKU is refused rather than answered
/// slowly (the allocation is quadratic in the number of claims).
pub const MAX_CLAIMS_PER_SKU: usize = 5000;

/// The key a free-text customer name is matched with.
pub fn customer_key(name: &str) -> String {
    py_casefold(py_strip(name))
}

#[derive(Clone, Debug, Default)]
pub struct Policy {
    /// customer_key -> (display name, tier)
    pub priorities: HashMap<String, (String, u8)>,
    pub fair_tiers: Vec<u8>,
}

impl Policy {
    /// The saved policy with what-if overrides laid over it.
    pub fn with_overrides(&self, tier_overrides: &[(String, u8)], fair_tiers: Option<&[u8]>) -> Policy {
        let mut p = self.clone();
        for (customer, tier) in tier_overrides {
            p.priorities.insert(customer_key(customer), (py_strip(customer).to_string(), *tier));
        }
        if let Some(f) = fair_tiers {
            let mut f = f.to_vec();
            f.sort_unstable();
            f.dedup();
            p.fair_tiers = f;
        }
        p
    }

    pub fn tier_of(&self, customer: Option<&str>) -> (u8, &'static str) {
        match customer.map(customer_key).filter(|k| !k.is_empty()).and_then(|k| self.priorities.get(&k)) {
            Some((_, t)) => (*t, "customer"),
            None => (DEFAULT_TIER, "default"),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Line {
    pub commitment_id: String,
    pub customer: Option<String>,
    pub tier: u8,
    pub tier_source: &'static str,
    pub delivery_date: NaiveDate,
    pub overdue: bool,
    pub quantity: f64,
    pub probability: f64,
    pub units_micro: i64,
    pub allocated_micro: i64,
}

impl Line {
    pub fn short_micro(&self) -> i64 {
        self.units_micro - self.allocated_micro
    }
}

#[derive(Clone, Debug)]
pub struct SkuResult {
    pub sku: String,
    /// "ok", or "stock_unknown" when the SKU has no stock row (nothing is allocated).
    pub status: &'static str,
    pub lines: Vec<Line>,
    pub stock_micro: Option<i64>,
    pub counted: Vec<ArrivalDetail>,
    pub uncounted: Vec<ArrivalDetail>,
    pub fair_tiers: Vec<u8>,
    pub result_hash: String,
}

impl SkuResult {
    pub fn demand_micro(&self) -> i64 {
        self.lines.iter().map(|l| l.units_micro).sum()
    }
    pub fn allocated_micro(&self) -> i64 {
        self.lines.iter().map(|l| l.allocated_micro).sum()
    }
    pub fn short_micro(&self) -> i64 {
        self.demand_micro() - self.allocated_micro()
    }
    pub fn contested(&self) -> bool {
        self.status == "ok" && self.short_micro() > 0
    }
}

/// One SKU's allocation. `extra` are what-if arrivals (a hypothetical order).
/// Lines come back in service order: tier, then delivery date, then id.
pub fn compute_sku(
    sku: &str,
    commitments: &[OpenCommitment],
    supply: &SkuSupply,
    extra: &[(NaiveDate, i64)],
    policy: &Policy,
    today: NaiveDate,
) -> SkuResult {
    let mut lines: Vec<Line> = commitments
        .iter()
        .map(|c| {
            let (tier, tier_source) = policy.tier_of(c.customer.as_deref());
            Line {
                commitment_id: c.id.clone(),
                customer: c.customer.as_deref().map(|s| py_strip(s).to_string()).filter(|s| !s.is_empty()),
                tier,
                tier_source,
                delivery_date: c.delivery_date,
                overdue: c.delivery_date < today,
                quantity: c.quantity,
                probability: c.probability,
                units_micro: to_micro(c.quantity, c.probability),
                allocated_micro: 0,
            }
        })
        .collect();
    lines.sort_by(|a, b| (a.tier, a.delivery_date, &a.commitment_id).cmp(&(b.tier, b.delivery_date, &b.commitment_id)));

    let mut result = SkuResult {
        sku: sku.to_string(),
        status: "ok",
        lines,
        stock_micro: supply.stock_micro,
        counted: supply.counted.clone(),
        uncounted: supply.uncounted.clone(),
        fair_tiers: policy.fair_tiers.clone(),
        result_hash: String::new(),
    };
    for (date, qty_micro) in extra {
        result.counted.push(ArrivalDetail {
            kind: "what_if",
            reference: String::new(),
            qty_micro: *qty_micro,
            date: Some(*date),
            source: "what_if",
        });
    }
    let Some(stock) = supply.stock_micro else {
        // No stock row: no verdict. Never a zero that reads as "all short".
        result.status = "stock_unknown";
        result.result_hash = hash_of(&result, today);
        return result;
    };
    let claims: Vec<Claim> = result
        .lines
        .iter()
        .map(|l| Claim {
            id: l.commitment_id.clone(),
            tier: l.tier,
            delivery_day: day_number(l.delivery_date),
            units: l.units_micro,
        })
        .collect();
    let arrivals: Vec<Arrival> = result
        .counted
        .iter()
        .map(|a| Arrival { day: a.date.map(day_number), qty: a.qty_micro })
        .collect();
    let got = allocate(&claims, day_number(today), stock, &arrivals, &policy.fair_tiers);
    for (line, a) in result.lines.iter_mut().zip(got) {
        line.allocated_micro = a;
    }
    result.result_hash = hash_of(&result, today);
    result
}

/// SHA-256 over every input the allocation read and every number it produced.
fn hash_of(r: &SkuResult, today: NaiveDate) -> String {
    let mut s = format!("v1|{}|{}|{:?}|{:?}", r.sku, day_number(today), r.status, r.stock_micro);
    for a in &r.counted {
        s.push_str(&format!("|a:{}:{:?}:{}", a.kind, a.date.map(day_number), a.qty_micro));
    }
    s.push_str(&format!("|f:{:?}", r.fair_tiers));
    for l in &r.lines {
        s.push_str(&format!(
            "|l:{}:{}:{}:{}:{}",
            l.commitment_id, l.tier, day_number(l.delivery_date), l.units_micro, l.allocated_micro
        ));
    }
    hex::encode(Sha256::digest(s.as_bytes()))
}

pub fn units(m: i64) -> f64 {
    micro_to_units(m)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(2026, 10, day).unwrap()
    }

    fn commitment(id: &str, customer: Option<&str>, day: u32, qty: f64, prob: f64) -> OpenCommitment {
        OpenCommitment {
            id: id.into(), sku: "S".into(), customer: customer.map(str::to_string),
            delivery_date: d(day), quantity: qty, probability: prob,
        }
    }

    fn supply(stock: f64) -> SkuSupply {
        SkuSupply { stock_micro: Some(to_micro(stock, 1.0)), ..Default::default() }
    }

    fn policy(pairs: &[(&str, u8)], fair: &[u8]) -> Policy {
        Policy {
            priorities: pairs.iter().map(|(c, t)| (customer_key(c), ((*c).to_string(), *t))).collect(),
            fair_tiers: fair.to_vec(),
        }
    }

    #[test]
    fn tiers_decide_who_is_short_and_default_is_five() {
        let c = vec![
            commitment("1", Some("Acme"), 20, 10.0, 1.0),
            commitment("2", Some("  GLOBEX "), 20, 10.0, 1.0),
            commitment("3", None, 20, 10.0, 1.0),
        ];
        let p = policy(&[("globex", 1), ("acme", 9)], &[]);
        let r = compute_sku("S", &c, &supply(15.0), &[], &p, d(6));
        let by: HashMap<_, _> = r.lines.iter().map(|l| (l.commitment_id.as_str(), l)).collect();
        // GLOBEX (casefold + strip match) tier 1 gets 10, the unassigned one (tier 5) 5, Acme (9) nothing.
        assert_eq!(by["2"].allocated_micro, 10_000_000);
        assert_eq!((by["2"].tier, by["2"].tier_source), (1, "customer"));
        assert_eq!(by["3"].allocated_micro, 5_000_000);
        assert_eq!((by["3"].tier, by["3"].tier_source), (5, "default"));
        assert_eq!(by["1"].allocated_micro, 0);
        assert_eq!(r.short_micro(), 15_000_000);
        assert!(r.contested());
    }

    #[test]
    fn expected_units_are_quantity_times_probability_like_the_semaforo() {
        let c = vec![commitment("1", Some("A"), 20, 100.0, 0.5)];
        let r = compute_sku("S", &c, &supply(30.0), &[], &Policy::default(), d(6));
        assert_eq!(r.lines[0].units_micro, 50_000_000);
        assert_eq!(r.lines[0].allocated_micro, 30_000_000);
    }

    #[test]
    fn no_stock_row_means_no_verdict_not_everyone_short() {
        let c = vec![commitment("1", Some("A"), 20, 5.0, 1.0)];
        let r = compute_sku("S", &c, &SkuSupply::default(), &[], &Policy::default(), d(6));
        assert_eq!(r.status, "stock_unknown");
        assert!(!r.contested());
        assert_eq!(r.lines[0].allocated_micro, 0);
    }

    #[test]
    fn counted_arrivals_serve_only_the_dates_after_them_and_a_what_if_order_helps() {
        let c = vec![commitment("early", Some("A"), 8, 10.0, 1.0), commitment("late", Some("B"), 25, 10.0, 1.0)];
        let mut s = supply(4.0);
        s.counted.push(ArrivalDetail { kind: "po", reference: "OC-1".into(), qty_micro: 10_000_000, date: Some(d(20)), source: "lead_time" });
        let r = compute_sku("S", &c, &s, &[], &Policy::default(), d(6));
        let early = r.lines.iter().find(|l| l.commitment_id == "early").unwrap();
        let late = r.lines.iter().find(|l| l.commitment_id == "late").unwrap();
        assert_eq!(early.allocated_micro, 4_000_000);   // only the stock exists by day 8
        assert_eq!(late.allocated_micro, 10_000_000);   // 4 + 10 - 4 by day 25
        // What-if: another 6 units landing on day 7 cover the early one.
        let w = compute_sku("S", &c, &s, &[(d(7), 6_000_000)], &Policy::default(), d(6));
        assert_eq!(w.lines.iter().find(|l| l.commitment_id == "early").unwrap().allocated_micro, 10_000_000);
        assert_ne!(r.result_hash, w.result_hash);
    }

    #[test]
    fn hash_changes_with_any_input_and_is_stable_otherwise() {
        let c = vec![commitment("1", Some("A"), 20, 5.0, 1.0)];
        let a = compute_sku("S", &c, &supply(3.0), &[], &Policy::default(), d(6));
        let b = compute_sku("S", &c, &supply(3.0), &[], &Policy::default(), d(6));
        assert_eq!(a.result_hash, b.result_hash);
        assert_ne!(a.result_hash, compute_sku("S", &c, &supply(3.5), &[], &Policy::default(), d(6)).result_hash);
        assert_ne!(a.result_hash, compute_sku("S", &c, &supply(3.0), &[], &Policy::default(), d(7)).result_hash);
        let c2 = vec![commitment("1", Some("A"), 20, 6.0, 1.0)];
        assert_ne!(a.result_hash, compute_sku("S", &c2, &supply(3.0), &[], &Policy::default(), d(6)).result_hash);
        assert_eq!(a.result_hash.len(), 64);
    }

    #[test]
    fn overrides_win_over_saved_priorities() {
        let c = vec![commitment("1", Some("Acme"), 20, 10.0, 1.0), commitment("2", Some("Globex"), 20, 10.0, 1.0)];
        let saved = policy(&[("acme", 1)], &[]);
        let what_if = saved.with_overrides(&[("ACME".to_string(), 9), ("globex".to_string(), 1)], Some(&[2, 2]));
        assert_eq!(what_if.fair_tiers, vec![2]);
        let r = compute_sku("S", &c, &supply(10.0), &[], &what_if, d(6));
        assert_eq!(r.lines.iter().find(|l| l.commitment_id == "2").unwrap().allocated_micro, 10_000_000);
        assert_eq!(saved.tier_of(Some("acme")).0, 1); // the saved policy is untouched
    }
}
