//! The arithmetic of sharing scarce stock among committed customers.
//!
//! Pure integers, no I/O, no clock: every amount is in micro-units (1e-6 of a
//! unit) and every day is a plain day number, so the result is EXACT and the
//! same inputs give the same answer on every machine. The Python reference
//! (`tests/contract/allocation_reference.py`) is written independently with
//! `fractions.Fraction` and the differential test compares the two for exact
//! equality over thousands of seeded cases.
//!
//! The model. One SKU, one supply timeline `S(t)`: the stock on hand plus every
//! confirmed arrival dated on or before day `t`. A claim is a commitment's
//! expected units (quantity x probability) due on its delivery day. A claim can
//! only be served from supply that exists by its day, so an allocation `x` is
//! feasible when, for EVERY day `t`:
//!
//! ```text
//!     sum of x[i] over claims with effective day <= t   <=   S(t)
//! ```
//!
//! (a claim overdue today counts as due today). Supply that arrives after a
//! claim's day never serves it: that claim stays short, it does not queue for
//! later supply and starve a later claim of units it could have had. The order
//! of service:
//!
//! 1. Tiers, lowest number first (tier 1 is served before tier 2 ...). A tier
//!    is served completely, as far as the supply allows, before the next one
//!    sees a unit.
//! 2. Inside a tier, either earliest delivery date first (ties by id), or, for a
//!    fair-share tier, proportionally: every member gets the same fraction of
//!    its units, found by progressive filling so that a member whose date is
//!    capped by early supply does not hold back members due later.
//!
//! The result never exceeds a claim's units, never exceeds the supply at any
//! day, and is order-independent for a fair-share tier. It is ADVISORY: nothing
//! here (or in its callers) writes a stock row.

use std::collections::BTreeSet;

/// One commitment's claim on the stock of its SKU.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Claim {
    pub id: String,
    /// 1 (served first) .. 9.
    pub tier: u8,
    /// Delivery date as a day number.
    pub delivery_day: i64,
    /// Expected units (quantity x probability), micro-units, > 0.
    pub units: i64,
}

/// Units on their way. `day: None` is available now.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Arrival {
    pub day: Option<i64>,
    pub qty: i64,
}

/// What each claim gets, in the order the claims were given. `allocated` is
/// micro-units, `0 <= allocated <= units`.
pub fn allocate(claims: &[Claim], today: i64, stock: i64, arrivals: &[Arrival], fair_tiers: &[u8]) -> Vec<i64> {
    let n = claims.len();
    let eff: Vec<i64> = claims.iter().map(|c| c.delivery_day.max(today)).collect();
    let units: Vec<i128> = claims.iter().map(|c| i128::from(c.units.max(0))).collect();
    let mut alloc: Vec<i128> = vec![0; n];

    // Supply steps: (day, qty) with day clamped to today, non-positive ignored.
    let base = i128::from(stock.max(0));
    let steps: Vec<(i64, i128)> = arrivals
        .iter()
        .filter(|a| a.qty > 0)
        .map(|a| (a.day.unwrap_or(today).max(today), i128::from(a.qty)))
        .collect();

    // The only days a constraint can bind on: where S or the claimed total changes.
    let days: Vec<i64> = {
        let mut set: BTreeSet<i64> = eff.iter().copied().collect();
        set.extend(steps.iter().map(|(d, _)| *d));
        set.into_iter().collect()
    };
    let supply: Vec<i128> = days
        .iter()
        .map(|t| base + steps.iter().filter(|(d, _)| d <= t).map(|(_, q)| *q).sum::<i128>())
        .collect();
    // Position of each claim's effective day in `days`.
    let at: Vec<usize> = eff.iter().map(|e| days.binary_search(e).expect("every effective day is listed")).collect();
    // cum[k]: units allocated so far to claims due on or before days[k].
    let mut cum: Vec<i128> = vec![0; days.len()];

    let mut tiers: Vec<u8> = claims.iter().map(|c| c.tier).collect();
    tiers.sort_unstable();
    tiers.dedup();

    for tier in tiers {
        let mut members: Vec<usize> = (0..n).filter(|&i| claims[i].tier == tier).collect();
        if fair_tiers.contains(&tier) {
            fair_fill(&members, &at, &units, &mut alloc, &mut cum, &supply);
        } else {
            members.sort_by(|&a, &b| {
                (claims[a].delivery_day, &claims[a].id).cmp(&(claims[b].delivery_day, &claims[b].id))
            });
            for &i in &members {
                let mut x = units[i];
                for k in at[i]..days.len() {
                    x = x.min(supply[k] - cum[k]);
                }
                let x = x.max(0);
                alloc[i] = x;
                for c in cum.iter_mut().skip(at[i]) {
                    *c += x;
                }
            }
        }
    }
    alloc.into_iter().map(|a| a as i64).collect()
}

/// Progressive filling for one fair-share tier. All still-active members share
/// one ratio `r`; the binding day is the one whose slack (supply minus what is
/// already fixed on claims due by then) divided by the active units due by
/// then is smallest. Members due by that day are frozen at `floor(r * units)`,
/// the rest continue with the slack that is left. Floors keep every constraint
/// satisfied; the few micro-units they drop are never handed to someone else.
fn fair_fill(members: &[usize], at: &[usize], units: &[i128], alloc: &mut [i128], cum: &mut [i128], supply: &[i128]) {
    let mut active: Vec<usize> = members.to_vec();
    while !active.is_empty() {
        // Active units due on or before each day.
        let mut u_act: Vec<i128> = vec![0; cum.len()];
        for &m in &active {
            u_act[at[m]] += units[m];
        }
        for k in 1..u_act.len() {
            u_act[k] += u_act[k - 1];
        }
        // (slack, active units, day index) of the tightest day; ties keep the LATER day.
        let mut best: Option<(i128, i128, usize)> = None;
        for k in 0..cum.len() {
            if u_act[k] == 0 {
                continue;
            }
            let slack = (supply[k] - cum[k]).max(0);
            if slack >= u_act[k] {
                continue; // everyone due by this day fits in full
            }
            let better = match best {
                None => true,
                // slack/u_act <= best.0/best.1
                Some((bs, bu, _)) => slack * bu <= bs * u_act[k],
            };
            if better {
                best = Some((slack, u_act[k], k));
            }
        }
        match best {
            None => {
                for &m in &active {
                    alloc[m] = units[m];
                    for c in cum.iter_mut().skip(at[m]) {
                        *c += units[m];
                    }
                }
                return;
            }
            Some((slack, total, k_star)) => {
                let mut still = Vec::new();
                for &m in &active {
                    if at[m] <= k_star {
                        let x = slack * units[m] / total;
                        alloc[m] = x;
                        for c in cum.iter_mut().skip(at[m]) {
                            *c += x;
                        }
                    } else {
                        still.push(m);
                    }
                }
                active = still;
            }
        }
    }
}

/// Micro-units of a commitment's expected units: `round(quantity x probability
/// x 1e6)`, never below 1 so a tiny claim still shows up (and is never a
/// silent zero). Callers have already validated both numbers.
pub fn to_micro(quantity: f64, probability: f64) -> i64 {
    let v = (quantity * probability * 1e6).round();
    if !v.is_finite() || v < 1.0 {
        return 1;
    }
    if v >= 9.0e15 { 9_000_000_000_000_000 } else { v as i64 }
}

pub fn micro_to_units(m: i64) -> f64 {
    m as f64 / 1e6
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn claim(id: &str, tier: u8, day: i64, units: i64) -> Claim {
        Claim { id: id.into(), tier, delivery_day: day, units }
    }
    fn arr(day: Option<i64>, qty: i64) -> Arrival {
        Arrival { day, qty }
    }

    #[test]
    fn plenty_of_stock_serves_everyone() {
        let c = vec![claim("a", 1, 10, 5_000_000), claim("b", 2, 12, 7_000_000)];
        assert_eq!(allocate(&c, 0, 100_000_000, &[], &[]), vec![5_000_000, 7_000_000]);
    }

    #[test]
    fn higher_tier_is_served_first_even_if_due_later() {
        // 10 units on hand: tier 1 is due on day 30, tier 2 on day 5.
        let c = vec![claim("late_vip", 1, 30, 8_000_000), claim("early", 2, 5, 8_000_000)];
        let got = allocate(&c, 0, 10_000_000, &[], &[]);
        assert_eq!(got, vec![8_000_000, 2_000_000]);
    }

    #[test]
    fn same_tier_serves_the_earliest_date_first_then_the_id() {
        let c = vec![claim("b", 3, 10, 6_000_000), claim("a", 3, 10, 6_000_000), claim("c", 3, 5, 6_000_000)];
        // c (day 5) first, then a before b on the tie: b gets what is left. The
        // result is in the order the claims were given: [b, a, c].
        assert_eq!(allocate(&c, 0, 14_000_000, &[], &[]), vec![2_000_000, 6_000_000, 6_000_000]);
    }

    #[test]
    fn arrival_after_a_date_never_serves_that_claim() {
        // 5 on hand, +20 arrives on day 20. Claim 1 (day 10, 8 units) gets 5; the
        // 3 missing units are NOT served by the day-20 arrival. Claim 2 (day 25)
        // then has 5 + 20 - 5 = 20 available and gets its 8 in full.
        let c = vec![claim("one", 1, 10, 8_000_000), claim("two", 1, 25, 8_000_000)];
        let got = allocate(&c, 0, 5_000_000, &[arr(Some(20), 20_000_000)], &[]);
        assert_eq!(got, vec![5_000_000, 8_000_000]);
    }

    #[test]
    fn a_low_priority_claim_due_before_the_arrival_cannot_borrow_from_it() {
        // 4 on hand, +10 on day 20. VIP due day 20 wants 10: it takes 4 + 10 -> 10, leaving
        // 4 for the low claim due day 3 (stock exists at day 3 but the VIP reserved
        // against day 20 supply only as far as needed).
        let c = vec![claim("vip", 1, 20, 10_000_000), claim("low", 2, 3, 6_000_000)];
        let got = allocate(&c, 0, 4_000_000, &[arr(Some(20), 10_000_000)], &[]);
        // VIP: min(10, S(20)=14) = 10. Low (day 3): min(6, S(3)-0=4, S(20)-10=4) = 4.
        assert_eq!(got, vec![10_000_000, 4_000_000]);
    }

    #[test]
    fn overdue_counts_as_due_today_and_none_arrival_is_now() {
        let c = vec![claim("old", 1, -30, 5_000_000)];
        assert_eq!(allocate(&c, 0, 0, &[arr(None, 3_000_000)], &[]), vec![3_000_000]);
        // an arrival dated in the past is available today
        assert_eq!(allocate(&c, 0, 0, &[arr(Some(-5), 4_000_000)], &[]), vec![4_000_000]);
    }

    #[test]
    fn no_stock_and_negative_inputs_give_zero_not_a_panic() {
        let c = vec![claim("a", 1, 1, 5_000_000)];
        assert_eq!(allocate(&c, 0, -5, &[arr(Some(1), -7)], &[]), vec![0]);
        assert_eq!(allocate(&[], 0, 5, &[], &[]), Vec::<i64>::new());
    }

    #[test]
    fn fair_share_splits_proportionally_to_units() {
        let c = vec![claim("a", 1, 10, 6_000_000), claim("b", 1, 10, 3_000_000)];
        // 3 units for 9 wanted: one third each.
        assert_eq!(allocate(&c, 0, 3_000_000, &[], &[1]), vec![2_000_000, 1_000_000]);
    }

    #[test]
    fn fair_share_does_not_let_an_early_cap_starve_a_later_member() {
        // Day 5 has only 2 units of supply (stock), day 10 gets +10. a (day 5, 4 units)
        // is capped by day 5; b (day 10, 4 units) is not held to the same ratio.
        let c = vec![claim("a", 1, 5, 4_000_000), claim("b", 1, 10, 4_000_000)];
        let got = allocate(&c, 0, 2_000_000, &[arr(Some(10), 10_000_000)], &[1]);
        assert_eq!(got, vec![2_000_000, 4_000_000]);
    }

    #[test]
    fn fair_share_tier_still_yields_to_a_higher_tier() {
        let c = vec![claim("vip", 1, 10, 6_000_000), claim("x", 2, 10, 4_000_000), claim("y", 2, 10, 4_000_000)];
        let got = allocate(&c, 0, 10_000_000, &[], &[2]);
        assert_eq!(got, vec![6_000_000, 2_000_000, 2_000_000]);
    }

    #[test]
    fn fair_share_floors_never_oversubscribe() {
        let c = vec![claim("a", 1, 1, 1_000_001), claim("b", 1, 1, 1_000_001), claim("c", 1, 1, 1_000_001)];
        let got = allocate(&c, 0, 1_000_000, &[], &[1]);
        assert!(got.iter().sum::<i64>() <= 1_000_000);
        assert!(got.iter().all(|g| *g >= 333_000));
    }

    #[test]
    fn to_micro_rounds_and_never_returns_zero() {
        assert_eq!(to_micro(10.0, 0.5), 5_000_000);
        assert_eq!(to_micro(0.1, 0.1), 10_000);
        assert_eq!(to_micro(1e-12, 1.0), 1);
        assert_eq!(micro_to_units(2_500_000), 2.5);
    }

    fn i(v: &Value) -> i64 {
        v.as_i64().expect("integer in fixture")
    }

    /// The differential test: every case in `allocation_cases.json` was solved by
    /// the independent Python reference (`gen_allocation_fixtures.py`); the Rust
    /// core must return EXACTLY the same integers.
    #[test]
    fn matches_the_python_reference_exactly() {
        let doc: Value = serde_json::from_str(include_str!("../../tests/fixtures/allocation_cases.json"))
            .expect("fixture parses");
        let cases = doc["cases"].as_array().expect("cases");
        assert!(cases.len() >= 2000, "the fixture should hold thousands of seeded cases, has {}", cases.len());
        let mut contested = 0usize;
        let mut fair = 0usize;
        for (n, case) in cases.iter().enumerate() {
            let claims: Vec<Claim> = case["claims"].as_array().unwrap().iter().map(|c| Claim {
                id: c["id"].as_str().unwrap().to_string(),
                tier: i(&c["tier"]) as u8,
                delivery_day: i(&c["day"]),
                units: i(&c["units"]),
            }).collect();
            let arrivals: Vec<Arrival> = case["arrivals"].as_array().unwrap().iter().map(|a| Arrival {
                day: if a["day"].is_null() { None } else { Some(i(&a["day"])) },
                qty: i(&a["qty"]),
            }).collect();
            let fair_tiers: Vec<u8> = case["fair_tiers"].as_array().unwrap().iter().map(|t| i(t) as u8).collect();
            let expected: Vec<i64> = case["expected"].as_array().unwrap().iter().map(i).collect();
            let got = allocate(&claims, i(&case["today"]), i(&case["stock"]), &arrivals, &fair_tiers);
            assert_eq!(got, expected, "case {n} differs from the Python reference: {case}");
            if got.iter().zip(&claims).any(|(g, c)| *g < c.units) {
                contested += 1;
            }
            if !fair_tiers.is_empty() {
                fair += 1;
            }
        }
        // A fixture where nothing is ever short would pass any implementation.
        assert!(contested > cases.len() / 4, "only {contested} contested cases");
        assert!(fair > cases.len() / 4, "only {fair} fair-share cases");
    }
}
