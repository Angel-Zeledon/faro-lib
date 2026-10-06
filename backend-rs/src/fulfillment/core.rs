//! Commitment fulfillment outlook: the arithmetic, and nothing else.
//!
//! No database, no clock, no HTTP: every function takes plain numbers (days are
//! integer day numbers, the caller turns dates into them) and returns plain
//! values, so the whole rule set can be tested without a server. The same rules
//! are written a second time, in the plainest Python, in
//! `tests/contract/fulfillment_reference.py`; `differential_against_python`
//! below replays thousands of seeded cases (`tests/fixtures/fulfillment_cases.json`,
//! made by `tests/contract/gen_fulfillment_fixtures.py`) through this module and
//! demands exact equality, floats included. Change a rule in one place and that
//! test goes red until the other agrees.
//!
//! The question, per open commitment: will it be met on its delivery date, given
//! the stock, the open purchase orders with their expected arrival dates and the
//! other commitments competing for the same stock? The rules, in the order that
//! decides which reason a row gets:
//!
//! * Units are `quantity * probability`, the expected units
//!   `committed_demand_service.allocate_risk` plans on, so this agrees with the
//!   at-risk flag the committed-demand list already shows. Commitments of one
//!   SKU are served earliest delivery first (ties: input order); commitment i
//!   competes with every earlier one through `cumulative_units`.
//! * Supply at delivery is stock plus the arrivals with a KNOWN date on or
//!   before `max(delivery, today)`. An arrival with no usable date (overdue, or
//!   no lead time to date it with) is never counted as certain.
//! * No stock figure is `insufficient_data`; stock is never assumed to be zero.
//! * Covered by known supply: `on_track`, unless the cover hangs on a purchase
//!   order landing fewer than [`AT_RISK_SLACK_DAYS`] days before delivery
//!   (`at_risk`, `covered_tight`).
//! * Short, but the undated units would close the gap: `insufficient_data`
//!   (`unknown_arrival`): the data cannot tell a miss from a late-but-fine order.
//! * Otherwise a real shortfall (undated units counted as arriving, so with any
//!   undated units it is the MINIMUM miss). The lead time then decides whether
//!   a new order placed today still arrives in time.

use std::cmp::Ordering;

/// Float dust tolerance, the same `1e-9` `allocate_risk` uses.
pub const EPS: f64 = 1e-9;
/// A purchase order that lands fewer days than this before delivery is not
/// slack: one slipped day and the commitment is late. A chosen threshold, not a
/// measured one (see docs/rust-migration.md "Decision rules to confirm").
pub const AT_RISK_SLACK_DAYS: i64 = 3;
/// Receptions needed before a supplier's observed average is trusted, the same
/// `service.MIN_LEAD_TIME_OBSERVATIONS`.
pub const MIN_LEAD_TIME_OBSERVATIONS: i64 = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    OnTrack,
    AtRisk,
    WillMiss,
    InsufficientData,
}

impl Verdict {
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::OnTrack => "on_track",
            Verdict::AtRisk => "at_risk",
            Verdict::WillMiss => "will_miss",
            Verdict::InsufficientData => "insufficient_data",
        }
    }
    pub fn parse(s: &str) -> Option<Verdict> {
        Some(match s {
            "on_track" => Verdict::OnTrack,
            "at_risk" => Verdict::AtRisk,
            "will_miss" => Verdict::WillMiss,
            "insufficient_data" => Verdict::InsufficientData,
            _ => return None,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    NoStockFigure,
    CoveredByStock,
    CoveredByArrivals,
    CoveredTight,
    UnknownArrival,
    DeliveryPassed,
    LeadTimeUnknown,
    OrderInTime,
    OrderTooLate,
}

impl Reason {
    pub fn as_str(self) -> &'static str {
        match self {
            Reason::NoStockFigure => "no_stock_figure",
            Reason::CoveredByStock => "covered_by_stock",
            Reason::CoveredByArrivals => "covered_by_arrivals",
            Reason::CoveredTight => "covered_tight",
            Reason::UnknownArrival => "unknown_arrival",
            Reason::DeliveryPassed => "delivery_passed",
            Reason::LeadTimeUnknown => "lead_time_unknown",
            Reason::OrderInTime => "order_in_time",
            Reason::OrderTooLate => "order_too_late",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CoverSource {
    Stock,
    Incoming,
    PurchaseOrder,
    NewOrder,
}

impl CoverSource {
    pub fn as_str(self) -> &'static str {
        match self {
            CoverSource::Stock => "stock",
            CoverSource::Incoming => "incoming",
            CoverSource::PurchaseOrder => "purchase_order",
            CoverSource::NewOrder => "new_order",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArrivalKind {
    Po,
    Transfer,
}

#[derive(Clone, Debug)]
pub struct Commitment {
    pub id: String,
    pub delivery_day: i64,
    pub quantity: f64,
    pub probability: f64,
}

/// Units on their way. `day` is None when no usable date exists.
#[derive(Clone, Debug)]
pub struct Arrival {
    pub day: Option<i64>,
    pub qty: f64,
    pub kind: ArrivalKind,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Outcome {
    pub id: String,
    pub verdict: Verdict,
    pub reason: Reason,
    pub units: f64,
    pub cumulative_units: f64,
    pub supply_units: Option<f64>,
    pub shortfall_units: Option<f64>,
    pub shortfall_is_minimum: bool,
    pub shortfall_worst_case: Option<f64>,
    pub undated_units: f64,
    pub cover_day: Option<i64>,
    pub cover_source: Option<CoverSource>,
    pub slack_days: Option<i64>,
    pub late_days: Option<i64>,
    pub latest_safe_order_day: Option<i64>,
    pub order_date_passed: Option<bool>,
    /// Position, in the input arrivals, of the arrival that closes the gap
    /// (set only when that is a purchase order or a transfer).
    pub driver_index: Option<usize>,
}

fn sum_known(arrivals: &[Arrival], as_of: i64) -> f64 {
    let mut total = 0.0_f64;
    for a in arrivals {
        if let Some(d) = a.day {
            if d <= as_of {
                total += a.qty.max(0.0);
            }
        }
    }
    total
}

fn sum_undated(arrivals: &[Arrival]) -> f64 {
    let mut total = 0.0_f64;
    for a in arrivals {
        if a.day.is_none() {
            total += a.qty.max(0.0);
        }
    }
    total
}

/// Earliest day known supply reaches `need`, and what closed the gap.
fn cover(base: f64, need: f64, arrivals: &[Arrival], today: i64) -> Option<(i64, CoverSource, Option<usize>)> {
    if base >= need - EPS {
        return Some((today, CoverSource::Stock, None));
    }
    let mut known: Vec<(i64, usize)> = arrivals
        .iter()
        .enumerate()
        .filter_map(|(i, a)| a.day.map(|d| (d, i)))
        .collect();
    known.sort();
    let mut acc = base;
    for (day, i) in known {
        let a = &arrivals[i];
        acc += a.qty.max(0.0);
        if acc >= need - EPS {
            let src = if a.kind == ArrivalKind::Po { CoverSource::PurchaseOrder } else { CoverSource::Incoming };
            return Some((day.max(today), src, Some(i)));
        }
    }
    None
}

/// One SKU's open commitments against its stock and arrivals. `lead_days` is
/// what a NEW order would take (None: nobody told us). Returns one outcome per
/// commitment in served order (earliest delivery first, ties by input order).
pub fn evaluate_sku(
    commitments: &[Commitment],
    stock: Option<f64>,
    lead_days: Option<i64>,
    arrivals: &[Arrival],
    today: i64,
) -> Vec<Outcome> {
    let mut order: Vec<usize> = (0..commitments.len()).collect();
    order.sort_by(|&a, &b| {
        match commitments[a].delivery_day.cmp(&commitments[b].delivery_day) {
            Ordering::Equal => a.cmp(&b),
            o => o,
        }
    });
    let base = stock.map(|s| s.max(0.0));
    let undated = sum_undated(arrivals);
    let mut cumulative = 0.0_f64;
    let mut out = Vec::with_capacity(commitments.len());
    for idx in order {
        let c = &commitments[idx];
        let units = c.quantity * c.probability;
        cumulative += units;
        let delivery = c.delivery_day;
        let mut row = Outcome {
            id: c.id.clone(),
            verdict: Verdict::InsufficientData,
            reason: Reason::NoStockFigure,
            units,
            cumulative_units: cumulative,
            supply_units: None,
            shortfall_units: None,
            shortfall_is_minimum: false,
            shortfall_worst_case: None,
            undated_units: undated,
            cover_day: None,
            cover_source: None,
            slack_days: None,
            late_days: None,
            latest_safe_order_day: None,
            order_date_passed: None,
            driver_index: None,
        };
        let Some(base) = base else {
            out.push(row);
            continue;
        };
        let as_of = delivery.max(today);
        let supply_known = base + sum_known(arrivals, as_of);
        row.supply_units = Some(supply_known);
        let short_known = units.min((cumulative - supply_known).max(0.0));
        if short_known <= EPS {
            row.shortfall_units = Some(0.0);
            let (cday, csrc, drv) = cover(base, cumulative, arrivals, today).unwrap_or((as_of, CoverSource::Incoming, None));
            row.cover_day = Some(cday);
            row.driver_index = drv;
            row.cover_source = Some(csrc);
            if csrc == CoverSource::PurchaseOrder {
                row.slack_days = Some(delivery - cday);
            }
            if csrc == CoverSource::PurchaseOrder && delivery - cday < AT_RISK_SLACK_DAYS {
                row.verdict = Verdict::AtRisk;
                row.reason = Reason::CoveredTight;
            } else {
                row.verdict = Verdict::OnTrack;
                row.reason = if csrc == CoverSource::Stock { Reason::CoveredByStock } else { Reason::CoveredByArrivals };
            }
            out.push(row);
            continue;
        }
        let supply_best = supply_known + undated;
        let short_best = units.min((cumulative - supply_best).max(0.0));
        if short_best <= EPS {
            row.verdict = Verdict::InsufficientData;
            row.reason = Reason::UnknownArrival;
            row.shortfall_worst_case = Some(short_known);
            out.push(row);
            continue;
        }
        row.shortfall_units = Some(short_best);
        row.shortfall_is_minimum = undated > 0.0;
        row.shortfall_worst_case = Some(short_known);
        if let Some(l) = lead_days {
            row.latest_safe_order_day = Some(delivery - l);
            row.order_date_passed = Some(delivery - l < today);
        }
        let late = cover(base, cumulative, arrivals, today);
        let new_order = lead_days.map(|l| (today + l, CoverSource::NewOrder, None));
        let best = match (late, new_order) {
            (Some(a), Some(b)) => Some(if a.0 <= b.0 { a } else { b }),
            (Some(a), None) => Some(a),
            (None, Some(b)) => Some(b),
            (None, None) => None,
        };
        if let Some((d, s, drv)) = best {
            row.cover_day = Some(d);
            row.cover_source = Some(s);
            row.driver_index = drv;
            if d > delivery {
                row.late_days = Some(d - delivery);
            }
        }
        if delivery < today {
            row.verdict = Verdict::WillMiss;
            row.reason = Reason::DeliveryPassed;
        } else if let Some(l) = lead_days {
            if today + l <= delivery {
                row.verdict = Verdict::AtRisk;
                row.reason = Reason::OrderInTime;
            } else {
                row.verdict = Verdict::WillMiss;
                row.reason = Reason::OrderTooLate;
            }
        } else {
            row.verdict = Verdict::InsufficientData;
            row.reason = Reason::LeadTimeUnknown;
        }
        out.push(row);
    }
    out
}

// ── Arrival of one purchase-order line ───────────────────────────────────────

/// The supplier's lead time for dating an open order: learned from at least
/// three real receptions (average above zero, rounded up), else the supplier
/// card's figure when somebody declared it, else None. Never the 15-day system
/// default: an assumed number is not a date.
pub fn po_lead_days(obs_avg: Option<f64>, obs_n: i64, card_days: Option<i64>, card_declared: bool) -> Option<i64> {
    if obs_n >= MIN_LEAD_TIME_OBSERVATIONS {
        if let Some(avg) = obs_avg {
            if avg > 0.0 {
                return Some(avg.ceil() as i64);
            }
        }
    }
    match card_days {
        Some(d) if card_declared => Some(d),
        _ => None,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArrivalSource {
    SupplierPromise,
    LeadTime,
    Overdue,
    NoLeadTime,
}

impl ArrivalSource {
    pub fn as_str(self) -> &'static str {
        match self {
            ArrivalSource::SupplierPromise => "supplier_promise",
            ArrivalSource::LeadTime => "lead_time",
            ArrivalSource::Overdue => "overdue",
            ArrivalSource::NoLeadTime => "no_lead_time",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LineArrival {
    /// The date to plan on; None when there is none.
    pub day: Option<i64>,
    /// The date the order was expected, even when it is already past.
    pub expected_day: Option<i64>,
    pub source: ArrivalSource,
}

/// Where one order line stands: a date a person accepted from the supplier
/// wins; else generated + lead time; no lead time, no date. A date already in
/// the past while the goods have not arrived is not a date to plan on.
pub fn po_line_arrival(generated_day: i64, lead_days: Option<i64>, promise_day: Option<i64>, today: i64) -> LineArrival {
    let (expected, source) = match (promise_day, lead_days) {
        (Some(p), _) => (p, ArrivalSource::SupplierPromise),
        (None, Some(l)) => (generated_day + l, ArrivalSource::LeadTime),
        (None, None) => return LineArrival { day: None, expected_day: None, source: ArrivalSource::NoLeadTime },
    };
    if expected < today {
        return LineArrival { day: None, expected_day: Some(expected), source: ArrivalSource::Overdue };
    }
    LineArrival { day: Some(expected), expected_day: Some(expected), source }
}

// ── The lead time of a SKU (what a new order would take) ─────────────────────

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SkuLeadSource {
    Learned,
    Sku,
    Rule,
}

impl SkuLeadSource {
    pub fn as_str(self) -> &'static str {
        match self {
            SkuLeadSource::Learned => "learned",
            SkuLeadSource::Sku => "sku",
            SkuLeadSource::Rule => "rule",
        }
    }
}

/// Same precedence as the semaforo's cascade (`resolve_planning_inputs`): the
/// lead time learned from the supplier's real receptions, else a value somebody
/// set on the SKU, else the narrowest supplier / category / global rule. The
/// system default is None: nobody told us, and a guess is not a date.
pub fn sku_lead_days(
    row_days: Option<i64>,
    row_declared: bool,
    rule_days: Option<i64>,
    learned_avg: Option<f64>,
) -> Option<(i64, SkuLeadSource)> {
    if let Some(avg) = learned_avg {
        if avg > 0.0 {
            return Some(((avg.round_ties_even() as i64).max(1), SkuLeadSource::Learned));
        }
    }
    if row_declared {
        if let Some(d) = row_days {
            return Some((d.max(1), SkuLeadSource::Sku));
        }
    }
    rule_days.map(|d| (d.max(1), SkuLeadSource::Rule))
}

// ── Roll-up ───────────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct SummaryRow {
    pub verdict: Verdict,
    pub units: f64,
    pub shortfall_units: Option<f64>,
    pub shortfall_is_minimum: bool,
    pub delivery_day: i64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Summary {
    pub total: i64,
    pub on_track: i64,
    pub at_risk: i64,
    pub will_miss: i64,
    pub insufficient_data: i64,
    pub units: f64,
    pub shortfall_units: f64,
    pub shortfall_has_minimum: bool,
    pub first_problem_day: Option<i64>,
}

pub fn summarize(rows: &[SummaryRow]) -> Summary {
    let mut s = Summary {
        total: rows.len() as i64,
        on_track: 0,
        at_risk: 0,
        will_miss: 0,
        insufficient_data: 0,
        units: 0.0,
        shortfall_units: 0.0,
        shortfall_has_minimum: false,
        first_problem_day: None,
    };
    for r in rows {
        match r.verdict {
            Verdict::OnTrack => s.on_track += 1,
            Verdict::AtRisk => s.at_risk += 1,
            Verdict::WillMiss => s.will_miss += 1,
            Verdict::InsufficientData => s.insufficient_data += 1,
        }
        s.units += r.units;
        if let Some(sf) = r.shortfall_units {
            s.shortfall_units += sf;
            if r.shortfall_is_minimum {
                s.shortfall_has_minimum = true;
            }
        }
        if matches!(r.verdict, Verdict::AtRisk | Verdict::WillMiss) {
            let d = r.delivery_day;
            if s.first_problem_day.map_or(true, |f| d < f) {
                s.first_problem_day = Some(d);
            }
        }
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    const T: i64 = 1000;

    fn c(id: &str, day: i64, qty: f64, p: f64) -> Commitment {
        Commitment { id: id.into(), delivery_day: day, quantity: qty, probability: p }
    }
    fn po(day: Option<i64>, qty: f64) -> Arrival {
        Arrival { day, qty, kind: ArrivalKind::Po }
    }
    fn one(cs: &[Commitment], stock: Option<f64>, lead: Option<i64>, arr: &[Arrival]) -> Outcome {
        evaluate_sku(cs, stock, lead, arr, T).remove(0)
    }

    #[test]
    fn stock_alone_covers() {
        let o = one(&[c("a", T + 30, 100.0, 1.0)], Some(150.0), Some(10), &[]);
        assert_eq!(o.verdict, Verdict::OnTrack);
        assert_eq!(o.reason, Reason::CoveredByStock);
        assert_eq!(o.cover_day, Some(T));
        assert_eq!(o.shortfall_units, Some(0.0));
    }

    #[test]
    fn missing_stock_is_never_a_zero() {
        let o = one(&[c("a", T + 30, 100.0, 1.0)], None, Some(10), &[po(Some(T + 5), 500.0)]);
        assert_eq!(o.verdict, Verdict::InsufficientData);
        assert_eq!(o.reason, Reason::NoStockFigure);
        assert_eq!(o.shortfall_units, None);
        assert_eq!(o.supply_units, None);
    }

    #[test]
    fn stock_of_zero_is_a_figure() {
        let o = one(&[c("a", T + 5, 100.0, 1.0)], Some(0.0), Some(10), &[]);
        assert_eq!(o.verdict, Verdict::WillMiss);
        assert_eq!(o.reason, Reason::OrderTooLate);
        assert_eq!(o.shortfall_units, Some(100.0));
    }

    #[test]
    fn purchase_order_in_time_is_on_track() {
        let o = one(&[c("a", T + 30, 100.0, 1.0)], Some(20.0), Some(10), &[po(Some(T + 10), 100.0)]);
        assert_eq!(o.verdict, Verdict::OnTrack);
        assert_eq!(o.reason, Reason::CoveredByArrivals);
        assert_eq!(o.cover_source, Some(CoverSource::PurchaseOrder));
        assert_eq!(o.slack_days, Some(20));
    }

    #[test]
    fn purchase_order_landing_the_day_before_is_tight() {
        let o = one(&[c("a", T + 30, 100.0, 1.0)], Some(0.0), Some(10), &[po(Some(T + 29), 100.0)]);
        assert_eq!(o.verdict, Verdict::AtRisk);
        assert_eq!(o.reason, Reason::CoveredTight);
        assert_eq!(o.slack_days, Some(1));
        let o = one(&[c("a", T + 30, 100.0, 1.0)], Some(0.0), Some(10), &[po(Some(T + 27), 100.0)]);
        assert_eq!(o.verdict, Verdict::OnTrack, "exactly the slack is enough");
    }

    #[test]
    fn purchase_order_after_delivery_with_time_to_reorder_is_at_risk() {
        let o = one(&[c("a", T + 20, 100.0, 1.0)], Some(0.0), Some(10), &[po(Some(T + 25), 100.0)]);
        assert_eq!(o.verdict, Verdict::AtRisk);
        assert_eq!(o.reason, Reason::OrderInTime);
        assert_eq!(o.latest_safe_order_day, Some(T + 10));
        assert_eq!(o.cover_day, Some(T + 10), "a new order today beats the late purchase order");
        assert_eq!(o.cover_source, Some(CoverSource::NewOrder));
        assert_eq!(o.late_days, None);
    }

    #[test]
    fn nothing_arrives_in_time_will_miss_and_says_how_late() {
        let o = one(&[c("a", T + 20, 100.0, 1.0)], Some(0.0), Some(30), &[po(Some(T + 45), 100.0)]);
        assert_eq!(o.verdict, Verdict::WillMiss);
        assert_eq!(o.reason, Reason::OrderTooLate);
        assert_eq!(o.cover_day, Some(T + 30));
        assert_eq!(o.late_days, Some(10));
        assert_eq!(o.order_date_passed, Some(true));
    }

    #[test]
    fn competing_commitments_share_the_stock_earliest_first() {
        let cs = [c("late", T + 40, 60.0, 1.0), c("early", T + 10, 60.0, 1.0)];
        let out = evaluate_sku(&cs, Some(80.0), Some(5), &[], T);
        assert_eq!(out[0].id, "early");
        assert_eq!(out[0].verdict, Verdict::OnTrack);
        assert_eq!(out[1].id, "late");
        assert_eq!(out[1].shortfall_units, Some(40.0));
        assert_eq!(out[1].cumulative_units, 120.0);
        assert_eq!(out[1].verdict, Verdict::AtRisk);
    }

    #[test]
    fn probability_scales_units() {
        let o = one(&[c("a", T + 10, 100.0, 0.5)], Some(50.0), Some(5), &[]);
        assert_eq!(o.units, 50.0);
        assert_eq!(o.verdict, Verdict::OnTrack);
    }

    #[test]
    fn undated_units_that_could_cover_are_insufficient_data() {
        let o = one(&[c("a", T + 30, 100.0, 1.0)], Some(10.0), Some(5), &[po(None, 200.0)]);
        assert_eq!(o.verdict, Verdict::InsufficientData);
        assert_eq!(o.reason, Reason::UnknownArrival);
        assert_eq!(o.shortfall_units, None);
        assert_eq!(o.shortfall_worst_case, Some(90.0));
        assert_eq!(o.undated_units, 200.0);
    }

    #[test]
    fn undated_units_that_cannot_cover_leave_a_minimum_shortfall() {
        let o = one(&[c("a", T + 30, 100.0, 1.0)], Some(10.0), Some(5), &[po(None, 30.0)]);
        assert_eq!(o.verdict, Verdict::AtRisk);
        assert_eq!(o.shortfall_units, Some(60.0));
        assert!(o.shortfall_is_minimum);
        assert_eq!(o.shortfall_worst_case, Some(90.0));
    }

    #[test]
    fn unknown_lead_time_with_a_real_shortfall_keeps_the_shortfall_but_not_the_verdict() {
        let o = one(&[c("a", T + 30, 100.0, 1.0)], Some(40.0), None, &[]);
        assert_eq!(o.verdict, Verdict::InsufficientData);
        assert_eq!(o.reason, Reason::LeadTimeUnknown);
        assert_eq!(o.shortfall_units, Some(60.0));
        assert_eq!(o.cover_day, None);
        assert_eq!(o.latest_safe_order_day, None);
    }

    #[test]
    fn overdue_commitment_without_stock_will_miss_without_needing_a_lead_time() {
        let o = one(&[c("a", T - 4, 100.0, 1.0)], Some(10.0), None, &[]);
        assert_eq!(o.verdict, Verdict::WillMiss);
        assert_eq!(o.reason, Reason::DeliveryPassed);
    }

    #[test]
    fn overdue_commitment_covered_by_stock_is_on_track() {
        let o = one(&[c("a", T - 4, 100.0, 1.0)], Some(100.0), None, &[]);
        assert_eq!(o.verdict, Verdict::OnTrack);
    }

    #[test]
    fn transfer_in_transit_counts_as_available_now() {
        let t = Arrival { day: Some(T), qty: 100.0, kind: ArrivalKind::Transfer };
        let o = one(&[c("a", T + 3, 100.0, 1.0)], Some(0.0), Some(10), &[t]);
        assert_eq!(o.verdict, Verdict::OnTrack);
        assert_eq!(o.cover_source, Some(CoverSource::Incoming));
    }

    #[test]
    fn same_result_as_allocate_risk_when_every_arrival_is_dated() {
        // allocate_risk: short_i = min(units_i, max(0, cumulative_i - supply_at_date_i)).
        let cs = [c("a", T + 10, 50.0, 1.0), c("b", T + 20, 50.0, 1.0), c("c", T + 30, 50.0, 1.0)];
        let arr = [po(Some(T + 15), 40.0)];
        let out = evaluate_sku(&cs, Some(60.0), Some(1), &arr, T);
        let short: Vec<f64> = out.iter().map(|o| o.shortfall_units.unwrap()).collect();
        assert_eq!(short, vec![0.0, 0.0, 50.0]);
    }

    #[test]
    fn po_lead_prefers_evidence_then_a_declared_card_and_never_the_default() {
        assert_eq!(po_lead_days(Some(6.2), 3, Some(20), true), Some(7));
        assert_eq!(po_lead_days(Some(6.2), 2, Some(20), true), Some(20));
        assert_eq!(po_lead_days(Some(6.2), 2, Some(15), false), None);
        assert_eq!(po_lead_days(Some(0.0), 5, None, false), None);
    }

    #[test]
    fn po_line_arrival_rules() {
        let a = po_line_arrival(990, Some(20), None, T);
        assert_eq!((a.day, a.source), (Some(1010), ArrivalSource::LeadTime));
        let a = po_line_arrival(990, Some(20), Some(1030), T);
        assert_eq!((a.day, a.source), (Some(1030), ArrivalSource::SupplierPromise));
        let a = po_line_arrival(900, Some(20), None, T);
        assert_eq!((a.day, a.expected_day, a.source), (None, Some(920), ArrivalSource::Overdue));
        let a = po_line_arrival(990, None, None, T);
        assert_eq!((a.day, a.source), (None, ArrivalSource::NoLeadTime));
        let a = po_line_arrival(990, None, Some(T), T);
        assert_eq!(a.day, Some(T), "a promise for today is still ahead of us");
    }

    #[test]
    fn sku_lead_precedence_and_banker_rounding() {
        assert_eq!(sku_lead_days(Some(9), true, Some(12), Some(7.5)), Some((8, SkuLeadSource::Learned)));
        assert_eq!(sku_lead_days(Some(9), true, Some(12), Some(6.5)), Some((6, SkuLeadSource::Learned)));
        assert_eq!(sku_lead_days(Some(9), true, Some(12), None), Some((9, SkuLeadSource::Sku)));
        assert_eq!(sku_lead_days(Some(15), false, Some(12), None), Some((12, SkuLeadSource::Rule)));
        assert_eq!(sku_lead_days(Some(15), false, None, None), None);
        assert_eq!(sku_lead_days(None, false, None, Some(0.2)), Some((1, SkuLeadSource::Learned)), "never below one day");
    }

    #[test]
    fn summary_counts_and_first_problem() {
        let rows = vec![
            SummaryRow { verdict: Verdict::OnTrack, units: 10.0, shortfall_units: Some(0.0), shortfall_is_minimum: false, delivery_day: 5 },
            SummaryRow { verdict: Verdict::AtRisk, units: 20.0, shortfall_units: Some(7.0), shortfall_is_minimum: true, delivery_day: 9 },
            SummaryRow { verdict: Verdict::WillMiss, units: 5.0, shortfall_units: Some(5.0), shortfall_is_minimum: false, delivery_day: 3 },
            SummaryRow { verdict: Verdict::InsufficientData, units: 1.0, shortfall_units: None, shortfall_is_minimum: false, delivery_day: 1 },
        ];
        let s = summarize(&rows);
        assert_eq!((s.total, s.on_track, s.at_risk, s.will_miss, s.insufficient_data), (4, 1, 1, 1, 1));
        assert_eq!(s.shortfall_units, 12.0);
        assert!(s.shortfall_has_minimum);
        assert_eq!(s.first_problem_day, Some(3));
        assert_eq!(summarize(&[]).total, 0);
    }

    // ── Differential test against the plain-Python reference ────────────────

    const FIXTURES: &str = include_str!("../../tests/fixtures/fulfillment_cases.json");

    fn fl(v: &Value) -> f64 {
        v.as_str().expect("float as string").parse::<f64>().expect("float")
    }
    fn ofl(v: &Value) -> Option<f64> {
        if v.is_null() { None } else { Some(fl(v)) }
    }
    fn oi(v: &Value) -> Option<i64> {
        v.as_i64()
    }

    #[test]
    fn differential_against_python() {
        let root: Value = serde_json::from_str(FIXTURES).expect("fixtures parse");
        assert_eq!(root["at_risk_slack_days"].as_i64(), Some(AT_RISK_SLACK_DAYS), "constants drifted");

        let mut n_sku = 0;
        let mut n_rows = 0;
        for case in root["sku_cases"].as_array().unwrap() {
            let today = case["today"].as_i64().unwrap();
            let cs: Vec<Commitment> = case["commitments"].as_array().unwrap().iter().map(|x| Commitment {
                id: x["id"].as_str().unwrap().to_string(),
                delivery_day: x["delivery_day"].as_i64().unwrap(),
                quantity: fl(&x["quantity"]),
                probability: fl(&x["probability"]),
            }).collect();
            let arr: Vec<Arrival> = case["arrivals"].as_array().unwrap().iter().map(|x| Arrival {
                day: oi(&x["day"]),
                qty: fl(&x["qty"]),
                kind: if x["kind"].as_str() == Some("po") { ArrivalKind::Po } else { ArrivalKind::Transfer },
            }).collect();
            let got = evaluate_sku(&cs, ofl(&case["stock"]), oi(&case["lead"]), &arr, today);
            let want = case["expected"].as_array().unwrap();
            assert_eq!(got.len(), want.len());
            for (g, w) in got.iter().zip(want) {
                let ctx = format!("case {n_sku} commitment {}: {case}", g.id);
                assert_eq!(g.id, w["id"].as_str().unwrap(), "{ctx}");
                assert_eq!(g.verdict.as_str(), w["verdict"].as_str().unwrap(), "verdict {ctx}");
                assert_eq!(g.reason.as_str(), w["reason"].as_str().unwrap(), "reason {ctx}");
                assert_eq!(g.units.to_bits(), fl(&w["units"]).to_bits(), "units {ctx}");
                assert_eq!(g.cumulative_units.to_bits(), fl(&w["cumulative_units"]).to_bits(), "cumulative {ctx}");
                assert_eq!(g.supply_units.map(f64::to_bits), ofl(&w["supply_units"]).map(f64::to_bits), "supply {ctx}");
                assert_eq!(g.shortfall_units.map(f64::to_bits), ofl(&w["shortfall_units"]).map(f64::to_bits), "shortfall {ctx}");
                assert_eq!(g.shortfall_is_minimum, w["shortfall_is_minimum"].as_bool().unwrap(), "minimum {ctx}");
                assert_eq!(g.shortfall_worst_case.map(f64::to_bits), ofl(&w["shortfall_worst_case"]).map(f64::to_bits), "worst {ctx}");
                assert_eq!(g.undated_units.to_bits(), fl(&w["undated_units"]).to_bits(), "undated {ctx}");
                assert_eq!(g.cover_day, oi(&w["cover_day"]), "cover_day {ctx}");
                assert_eq!(g.cover_source.map(CoverSource::as_str), w["cover_source"].as_str(), "cover_source {ctx}");
                assert_eq!(g.slack_days, oi(&w["slack_days"]), "slack {ctx}");
                assert_eq!(g.late_days, oi(&w["late_days"]), "late {ctx}");
                assert_eq!(g.latest_safe_order_day, oi(&w["latest_safe_order_day"]), "safe {ctx}");
                assert_eq!(g.order_date_passed, w["order_date_passed"].as_bool(), "passed {ctx}");
                assert_eq!(g.driver_index.map(|i| i as i64), oi(&w["driver_index"]), "driver {ctx}");
                n_rows += 1;
            }
            n_sku += 1;
        }
        assert!(n_sku >= 800 && n_rows >= 2000, "fixtures shrank: {n_sku} cases, {n_rows} rows");

        for (i, case) in root["arrival_cases"].as_array().unwrap().iter().enumerate() {
            let g = po_line_arrival(
                case["generated_day"].as_i64().unwrap(), oi(&case["lead"]), oi(&case["promise_day"]),
                case["today"].as_i64().unwrap());
            let w = &case["expected"];
            assert_eq!(g.day, oi(&w["day"]), "arrival {i}: {case}");
            assert_eq!(g.expected_day, oi(&w["expected_day"]), "arrival {i}: {case}");
            assert_eq!(g.source.as_str(), w["source"].as_str().unwrap(), "arrival {i}: {case}");
        }

        for (i, case) in root["po_lead_cases"].as_array().unwrap().iter().enumerate() {
            let g = po_lead_days(ofl(&case["avg"]), case["n"].as_i64().unwrap(), oi(&case["card"]),
                case["declared"].as_bool().unwrap());
            assert_eq!(g, oi(&case["expected"]), "po lead {i}: {case}");
        }

        for (i, case) in root["sku_lead_cases"].as_array().unwrap().iter().enumerate() {
            let g = sku_lead_days(oi(&case["row"]), case["declared"].as_bool().unwrap(), oi(&case["rule"]),
                ofl(&case["avg"]));
            let w = &case["expected"];
            match g {
                None => assert!(w.is_null(), "sku lead {i}: {case}"),
                Some((d, s)) => {
                    assert_eq!(Some(d), oi(&w["days"]), "sku lead {i}: {case}");
                    assert_eq!(Some(s.as_str()), w["source"].as_str(), "sku lead {i}: {case}");
                }
            }
        }

        for (i, case) in root["summary_cases"].as_array().unwrap().iter().enumerate() {
            let rows: Vec<SummaryRow> = case["rows"].as_array().unwrap().iter().map(|x| SummaryRow {
                verdict: Verdict::parse(x["verdict"].as_str().unwrap()).unwrap(),
                units: fl(&x["units"]),
                shortfall_units: ofl(&x["shortfall_units"]),
                shortfall_is_minimum: x["shortfall_is_minimum"].as_bool().unwrap(),
                delivery_day: x["delivery_day"].as_i64().unwrap(),
            }).collect();
            let g = summarize(&rows);
            let w = &case["expected"];
            assert_eq!(g.total, w["total"].as_i64().unwrap(), "summary {i}");
            assert_eq!(g.on_track, w["on_track"].as_i64().unwrap(), "summary {i}");
            assert_eq!(g.at_risk, w["at_risk"].as_i64().unwrap(), "summary {i}");
            assert_eq!(g.will_miss, w["will_miss"].as_i64().unwrap(), "summary {i}");
            assert_eq!(g.insufficient_data, w["insufficient_data"].as_i64().unwrap(), "summary {i}");
            assert_eq!(g.units.to_bits(), fl(&w["units"]).to_bits(), "summary {i}");
            assert_eq!(g.shortfall_units.to_bits(), fl(&w["shortfall_units"]).to_bits(), "summary {i}");
            assert_eq!(g.shortfall_has_minimum, w["shortfall_has_minimum"].as_bool().unwrap(), "summary {i}");
            assert_eq!(g.first_problem_day, oi(&w["first_problem_day"]), "summary {i}");
        }
    }
}
