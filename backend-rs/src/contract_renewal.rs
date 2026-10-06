//! Renewal and expiry tracking for blanket supply contracts: the PURE half
//! (no database, no HTTP). The reference is `backend/inventory/contract_renewal.py`
//! (and `supply_contract_service.expand_releases` for the schedule); every
//! function here is differentially tested against it by replaying
//! `tests/contract/renewal_diff_cases.json`, which Python generates from its
//! own implementation (`backend/tests/test_contract_renewal_pure.py` fails when
//! that file is stale). Change a rule on one side and the other side's suite
//! goes red.
//!
//! Numbers follow Python's: sums in the order given, 4 decimals for units and
//! 1 for percentages, rounded from the exact decimal value (`{:.N}` here,
//! `round(x, N)` there).

use std::collections::{BTreeMap, HashSet};

use chrono::{Datelike, Duration, NaiveDate};
use serde_json::{json, Value};

pub const DEFAULT_LEAD_DAYS: [i64; 3] = [60, 30, 7];
pub const MAX_RELEASES: usize = 2000;
pub const MAX_YEARS: i32 = 10;
pub const MAX_WITHIN_DAYS: i64 = 730;
pub const DEFAULT_WITHIN_DAYS: i64 = 90;
const EPS: f64 = 1e-6;

/// `round(x, nd)` for the values the maths reports: the exact decimal value of
/// the double, correctly rounded.
pub fn round_nd(x: f64, nd: usize) -> f64 {
    format!("{:.*}", nd, x).parse().unwrap_or(x)
}

fn r4(x: f64) -> f64 {
    round_nd(x, 4)
}

fn pct(num: f64, den: f64) -> Option<f64> {
    if den > 0.0 {
        Some(round_nd(num / den * 100.0, 1))
    } else {
        None
    }
}

fn num(v: Option<f64>) -> Value {
    v.map(|x| json!(x)).unwrap_or(Value::Null)
}

// ---- dates -----------------------------------------------------------------

fn days_in_month(y: i32, m: u32) -> u32 {
    let (ny, nm) = if m == 12 { (y + 1, 1) } else { (y, m + 1) };
    NaiveDate::from_ymd_opt(ny, nm, 1)
        .and_then(|d| d.pred_opt())
        .map(|d| d.day())
        .unwrap_or(28)
}

/// `_add_months`: `start` moved `k` months, the day clamped to the month's
/// last day. None past year 9999 (where Python's `date` raises).
pub fn add_months(start: NaiveDate, k: i32) -> Option<NaiveDate> {
    let m0 = start.month() as i32 - 1 + k;
    let y = start.year() + m0.div_euclid(12);
    let m = (m0.rem_euclid(12) + 1) as u32;
    if !(1..=9999).contains(&y) {
        return None;
    }
    NaiveDate::from_ymd_opt(y, m, start.day().min(days_in_month(y, m)))
}

fn plus_days(d: NaiveDate, n: i64) -> Option<NaiveDate> {
    d.checked_add_signed(Duration::days(n))
}

fn days_between(later: NaiveDate, earlier: NaiveDate) -> i64 {
    (later - earlier).num_days()
}

// ---- the schedule ----------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct Release {
    pub sku: String,
    pub date: NaiveDate,
    pub quantity: f64,
}

#[derive(Debug, Clone)]
pub struct Line {
    pub sku: String,
    pub total_quantity: f64,
}

/// `release_dates`: every month (or week) from `start` while on or before `end`.
pub fn release_dates(kind: &str, start: NaiveDate, end: NaiveDate) -> Vec<NaiveDate> {
    let mut out = Vec::new();
    let mut k: i32 = 0;
    loop {
        let d = if kind == "monthly" { add_months(start, k) } else { plus_days(start, 7 * k as i64) };
        let Some(d) = d else { return out };
        if d > end {
            return out;
        }
        out.push(d);
        k += 1;
        if out.len() > MAX_RELEASES {
            return out;
        }
    }
}

/// `split_evenly`: `total` over `n` releases, adding up to exactly `total`.
pub fn split_evenly(total: f64, n: usize) -> Vec<f64> {
    if n == 0 {
        return Vec::new();
    }
    if (total - total.round()).abs() < EPS {
        let t = total.round() as i64;
        let (base, rem) = (t.div_euclid(n as i64), t.rem_euclid(n as i64));
        return (0..n as i64).map(|i| (base + i64::from(i < rem)) as f64).collect();
    }
    let each = (total / n as f64 * 10000.0).floor() / 10000.0;
    let mut v = vec![each; n - 1];
    v.push(round_nd(total - each * (n as f64 - 1.0), 4));
    v
}

/// `expand_releases`: every scheduled release as `{sku, date, quantity}`,
/// sorted by date then SKU. `explicit` is the stored list of an explicit
/// schedule.
pub fn expand_releases(
    kind: &str,
    start: NaiveDate,
    end: NaiveDate,
    lines: &[Line],
    explicit: Option<&[Release]>,
) -> Vec<Release> {
    let mut out: Vec<Release> = Vec::new();
    if kind == "explicit" {
        out.extend(explicit.unwrap_or(&[]).iter().cloned());
    } else {
        let dates = release_dates(kind, start, end);
        for line in lines {
            for (d, q) in dates.iter().zip(split_evenly(line.total_quantity, dates.len())) {
                if q > 0.0 {
                    out.push(Release { sku: line.sku.clone(), date: *d, quantity: q });
                }
            }
        }
    }
    out.sort_by(|a, b| (a.date, &a.sku).cmp(&(b.date, &b.sku)));
    out
}

// ---- renewal terms ----------------------------------------------------------

/// `effective_lead_days`: the configured list, else 60/30/7; farthest first.
pub fn effective_lead_days(stored: Option<&[i64]>) -> Vec<i64> {
    match stored {
        Some(s) if !s.is_empty() => {
            let mut v: Vec<i64> = s.iter().copied().collect::<std::collections::BTreeSet<_>>().into_iter().collect();
            v.reverse();
            v
        }
        _ => DEFAULT_LEAD_DAYS.to_vec(),
    }
}

/// `renewal_view`.
pub fn renewal_view(
    period_end: NaiveDate,
    notice_days: Option<i64>,
    auto_renew: bool,
    lead_days: &[i64],
    today: NaiveDate,
) -> Value {
    let days_to_expiry = days_between(period_end, today);
    let notice_deadline = notice_days.and_then(|n| plus_days(period_end, -n));
    let days_to_notice = notice_deadline.map(|d| days_between(d, today));
    let max_lead = lead_days.iter().copied().max().unwrap_or(DEFAULT_LEAD_DAYS[0]);
    let bucket = if days_to_expiry < 0 {
        "expired"
    } else if days_to_notice.is_some_and(|d| d < 0) {
        "notice_passed"
    } else if days_to_expiry <= max_lead {
        "due_soon"
    } else {
        "upcoming"
    };
    json!({
        "expiry_date": period_end.to_string(),
        "days_to_expiry": days_to_expiry,
        "notice_days": notice_days,
        "notice_deadline": notice_deadline.map(|d| d.to_string()),
        "days_to_notice": days_to_notice,
        "auto_renew": auto_renew,
        "bucket": bucket,
    })
}

/// `due_alert`: the one alert an active contract owes today (the Python alert
/// pass is what emits it; this exists so the Rust screens can show "next alert"
/// and so the rule has two implementations that must agree).
pub fn due_alert(
    period_end: NaiveDate,
    notice_days: Option<i64>,
    lead_days: &[i64],
    today: NaiveDate,
    sent: &HashSet<(String, Option<i64>)>,
) -> Option<Value> {
    let expiry = period_end.to_string();
    let days_to_expiry = days_between(period_end, today);
    if days_to_expiry < 0 {
        if sent.contains(&(expiry.clone(), None)) {
            return None;
        }
        return Some(json!({"reason": "contract_expired", "lead_days": null,
                           "expiry_date": expiry, "days_left": days_to_expiry}));
    }
    let action_date = match notice_days {
        None => period_end,
        Some(n) => plus_days(period_end, -n)?,
    };
    let action_days = days_between(action_date, today);
    let lead = lead_days.iter().copied().filter(|t| action_days <= *t).min()?;
    if sent.contains(&(expiry.clone(), Some(lead))) {
        return None;
    }
    Some(json!({
        "reason": if notice_days.is_none() { "contract_expiring" } else { "contract_notice_deadline" },
        "lead_days": lead, "expiry_date": expiry, "days_left": days_to_expiry,
    }))
}

/// `_whole_months`.
fn whole_months(start: NaiveDate, end: NaiveDate) -> Option<i32> {
    let n = (end.year() - start.year()) * 12 + end.month() as i32 - start.month() as i32 + 1;
    [n, n - 1, n + 1].into_iter().find(|&k| {
        k >= 1 && add_months(start, k).and_then(|d| d.pred_opt()) == Some(end)
    })
}

/// `next_term`: (new_start, new_end, months). None past year 9999.
pub fn next_term(start: NaiveDate, end: NaiveDate) -> Option<(NaiveDate, NaiveDate, Option<i32>)> {
    let new_start = end.succ_opt()?;
    let months = whole_months(start, end);
    let new_end = match months {
        Some(m) => add_months(new_start, m)?.pred_opt()?,
        None => plus_days(new_start, days_between(end, start))?,
    };
    Some((new_start, new_end, months))
}

/// `shift_release_date`.
pub fn shift_release_date(d: NaiveDate, start: NaiveDate, new_start: NaiveDate, months: Option<i32>) -> Option<NaiveDate> {
    match months {
        Some(_) => {
            let k = (new_start.year() - start.year()) * 12 + new_start.month() as i32 - start.month() as i32;
            add_months(d, k)
        }
        None => plus_days(d, days_between(new_start, start)),
    }
}

#[derive(Debug, PartialEq)]
pub enum RenewalError {
    /// A shifted release does not fall inside the renewed term.
    ReleaseOutOfPeriod { sku: String, date: NaiveDate, shifted: NaiveDate },
    /// The renewed term is longer than ten years or past year 9999.
    PeriodTooLong,
}

/// `renewal_terms`: the renewed term and, for an explicit schedule, the
/// shifted releases (sorted by date then SKU).
pub fn renewal_terms(
    start: NaiveDate,
    end: NaiveDate,
    releases: Option<&[Release]>,
) -> Result<(NaiveDate, NaiveDate, Option<Vec<Release>>), RenewalError> {
    let (new_start, new_end, months) = next_term(start, end).ok_or(RenewalError::PeriodTooLong)?;
    match add_months(new_start, 12 * MAX_YEARS) {
        Some(limit) if new_end <= limit => {}
        _ => return Err(RenewalError::PeriodTooLong),
    }
    let shifted = match releases {
        None => None,
        Some(rs) => {
            let mut out = Vec::with_capacity(rs.len());
            for r in rs {
                let nd = shift_release_date(r.date, start, new_start, months).ok_or(RenewalError::PeriodTooLong)?;
                if nd < new_start || nd > new_end {
                    return Err(RenewalError::ReleaseOutOfPeriod { sku: r.sku.clone(), date: r.date, shifted: nd });
                }
                out.push(Release { sku: r.sku.clone(), date: nd, quantity: r.quantity });
            }
            out.sort_by(|a, b| (a.date, &a.sku).cmp(&(b.date, &b.sku)));
            Some(out)
        }
    };
    Ok((new_start, new_end, shifted))
}

// ---- committed against delivered ----------------------------------------------

#[derive(Debug, Clone)]
pub struct Commitment {
    pub sku: String,
    pub release_date: Option<NaiveDate>,
    pub quantity: f64,
    pub status: String,
    /// The date the commitment was marked fulfilled (None = unknown).
    pub fulfilled_on: Option<NaiveDate>,
}

/// `commitment_comparison`.
pub fn commitment_comparison(releases: &[Release], commitments: &[Commitment], today: NaiveDate) -> Value {
    let mut by_key: BTreeMap<(&str, NaiveDate), &Commitment> = BTreeMap::new();
    for c in commitments {
        if let Some(d) = c.release_date {
            by_key.insert((c.sku.as_str(), d), c); // later rows win, like a dict
        }
    }
    let mut line_order: Vec<&str> = Vec::new();
    let mut committed: BTreeMap<&str, f64> = BTreeMap::new();
    let mut due: BTreeMap<&str, f64> = BTreeMap::new();
    let (mut overdue_n, mut overdue_units) = (0i64, 0.0f64);
    for r in releases {
        let sku = r.sku.as_str();
        if !committed.contains_key(sku) {
            line_order.push(sku);
            committed.insert(sku, 0.0);
            due.insert(sku, 0.0);
        }
        *committed.get_mut(sku).unwrap() += r.quantity;
        if r.date <= today {
            *due.get_mut(sku).unwrap() += r.quantity;
        }
        let state = by_key.get(&(sku, r.date)).map(|c| c.status.as_str()).unwrap_or("missing");
        if r.date < today && (state == "open" || state == "missing") {
            overdue_n += 1;
            overdue_units += r.quantity;
        }
    }

    let mut delivered: BTreeMap<&str, f64> = BTreeMap::new();
    let mut late_n: BTreeMap<&str, i64> = BTreeMap::new();
    let mut late_units: BTreeMap<&str, f64> = BTreeMap::new();
    let (mut max_late, mut undated) = (0i64, 0i64);
    for c in commitments {
        if c.status != "fulfilled" || !committed.contains_key(c.sku.as_str()) {
            continue;
        }
        let sku = c.sku.as_str();
        *delivered.entry(sku).or_insert(0.0) += c.quantity;
        let Some(rel) = c.release_date else { continue };
        match c.fulfilled_on {
            None => undated += 1,
            Some(done) if done > rel => {
                *late_n.entry(sku).or_insert(0) += 1;
                *late_units.entry(sku).or_insert(0.0) += c.quantity;
                max_late = max_late.max(days_between(done, rel));
            }
            Some(_) => {}
        }
    }

    let get = |m: &BTreeMap<&str, f64>, k: &str| m.get(k).copied().unwrap_or(0.0);
    let mut lines = Vec::new();
    for sku in &line_order {
        let d = get(&delivered, sku);
        lines.push(json!({
            "sku": sku,
            "committed": r4(get(&committed, sku)),
            "due_to_date": r4(get(&due, sku)),
            "delivered": r4(d),
            "fill_rate_pct": num(pct(d, get(&due, sku))),
            "late_deliveries": late_n.get(sku).copied().unwrap_or(0),
            "late_units": r4(get(&late_units, sku)),
        }));
    }
    let committed_total: f64 = line_order.iter().map(|s| get(&committed, s)).sum();
    let due_total: f64 = line_order.iter().map(|s| get(&due, s)).sum();
    let delivered_total: f64 = line_order.iter().map(|s| get(&delivered, s)).sum();
    let late_total: i64 = line_order.iter().map(|s| late_n.get(s).copied().unwrap_or(0)).sum();
    let late_units_total: f64 = line_order.iter().map(|s| get(&late_units, s)).sum();
    json!({
        "committed_units": r4(committed_total),
        "due_to_date": r4(due_total),
        "delivered_units": r4(delivered_total),
        "remaining_units": r4((committed_total - delivered_total).max(0.0)),
        "shortfall_to_date": r4((due_total - delivered_total).max(0.0)),
        "fill_rate_pct": num(pct(delivered_total, due_total)),
        "term_fill_pct": num(pct(delivered_total, committed_total)),
        "late_deliveries": late_total,
        "late_units": r4(late_units_total),
        "max_days_late": max_late,
        "fulfilled_undated": undated,
        "overdue_open": overdue_n,
        "overdue_open_units": r4(overdue_units),
        "lines": lines,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> NaiveDate {
        NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()
    }

    fn rel(sku: &str, date: &str, q: f64) -> Release {
        Release { sku: sku.into(), date: d(date), quantity: q }
    }

    fn com(sku: &str, release: &str, q: f64, status: &str, done: Option<&str>) -> Commitment {
        Commitment {
            sku: sku.into(), release_date: Some(d(release)), quantity: q, status: status.into(),
            fulfilled_on: done.map(d),
        }
    }

    #[test]
    fn month_arithmetic_clamps_like_python() {
        assert_eq!(add_months(d("2027-01-31"), 1), Some(d("2027-02-28")));
        assert_eq!(add_months(d("2028-01-31"), 1), Some(d("2028-02-29")));
        assert_eq!(add_months(d("2027-11-30"), 3), Some(d("2028-02-29")));
        assert_eq!(add_months(d("9999-12-01"), 1), None);
    }

    #[test]
    fn split_keeps_whole_units_whole_and_sums_exactly() {
        assert_eq!(split_evenly(120.0, 7), vec![18.0, 17.0, 17.0, 17.0, 17.0, 17.0, 17.0]);
        let v = split_evenly(100.0, 3);
        assert_eq!(v.iter().sum::<f64>(), 100.0);
        let f = split_evenly(10.5, 4);
        assert_eq!(f, vec![2.625, 2.625, 2.625, 2.625]);
        assert!((split_evenly(10.1, 3).iter().sum::<f64>() - 10.1).abs() < 1e-9);
        assert!(split_evenly(5.0, 0).is_empty());
    }

    #[test]
    fn rounding_is_exact_decimal_half_even() {
        // 0.125 and 2.5 are exactly representable: ties go to even, like Python.
        assert_eq!(round_nd(0.125, 2), 0.12);
        assert_eq!(round_nd(2.5, 0), 2.0);
        assert_eq!(round_nd(0.375, 2), 0.38);
        // 1.005 is below the tie in binary: Python gives 1.0 too.
        assert_eq!(round_nd(1.005, 2), 1.0);
    }

    #[test]
    fn even_schedule_expands_sorted() {
        let lines = vec![Line { sku: "B".into(), total_quantity: 120.0 }, Line { sku: "A".into(), total_quantity: 3.0 }];
        let r = expand_releases("monthly", d("2027-01-15"), d("2027-04-14"), &lines, None);
        // 3 monthly dates: A gets 1 each, B 40 each; sorted by date then sku.
        assert_eq!(r.len(), 6);
        assert_eq!((r[0].sku.as_str(), r[1].sku.as_str()), ("A", "B"));
        assert_eq!(r[1].quantity, 40.0);
        // fewer units than dates: a zero release is not a release.
        let lines = vec![Line { sku: "A".into(), total_quantity: 2.0 }];
        assert_eq!(expand_releases("monthly", d("2027-01-01"), d("2027-04-30"), &lines, None).len(), 2);
    }

    #[test]
    fn buckets() {
        let leads = [60, 30, 7];
        let today = d("2027-06-01");
        let b = |end: &str, notice: Option<i64>| {
            renewal_view(d(end), notice, false, &leads, today)["bucket"].as_str().unwrap().to_string()
        };
        assert_eq!(b("2027-05-31", None), "expired");
        assert_eq!(b("2027-06-01", None), "due_soon");
        assert_eq!(b("2027-07-31", None), "due_soon"); // 60 days
        assert_eq!(b("2027-08-01", None), "upcoming"); // 61 days
        // 20 days left but the 30-day notice deadline already passed
        assert_eq!(b("2027-06-21", Some(30)), "notice_passed");
        let v = renewal_view(d("2027-08-01"), Some(30), true, &leads, today);
        assert_eq!(v["notice_deadline"], "2027-07-02");
        assert_eq!(v["days_to_notice"], 31);
        assert_eq!(v["auto_renew"], true);
    }

    #[test]
    fn alert_is_the_nearest_crossed_lead_and_fires_once() {
        let leads = [60, 30, 7];
        let none = HashSet::new();
        // 5 days left: one alert (7), not three.
        let a = due_alert(d("2027-06-06"), None, &leads, d("2027-06-01"), &none).unwrap();
        assert_eq!(a["lead_days"], 7);
        assert_eq!(a["reason"], "contract_expiring");
        let mut sent = HashSet::new();
        sent.insert(("2027-06-06".to_string(), Some(7)));
        assert!(due_alert(d("2027-06-06"), None, &leads, d("2027-06-02"), &sent).is_none());
        // 61 days left: nothing yet.
        assert!(due_alert(d("2027-08-01"), None, &leads, d("2027-06-01"), &none).is_none());
        // the notice deadline drives the countdown when there is one
        let a = due_alert(d("2027-07-31"), Some(30), &leads, d("2027-06-01"), &none).unwrap();
        assert_eq!(a["lead_days"], 30);
        assert_eq!(a["reason"], "contract_notice_deadline");
        // expired once
        let a = due_alert(d("2027-05-01"), None, &leads, d("2027-06-01"), &none).unwrap();
        assert_eq!(a["reason"], "contract_expired");
        assert!(a["lead_days"].is_null());
        sent.insert(("2027-05-01".to_string(), None));
        assert!(due_alert(d("2027-05-01"), None, &leads, d("2027-06-02"), &sent).is_none());
    }

    #[test]
    fn next_term_keeps_month_boundaries_across_leap_years() {
        let (s, e, m) = next_term(d("2027-01-01"), d("2027-12-31")).unwrap();
        assert_eq!((s, e, m), (d("2028-01-01"), d("2028-12-31"), Some(12)));
        let (s, e, _) = next_term(d("2028-01-01"), d("2028-12-31")).unwrap();
        assert_eq!((s, e), (d("2029-01-01"), d("2029-12-31")));
        // a term that is not a whole number of months keeps its day count
        let (s, e, m) = next_term(d("2027-01-01"), d("2027-02-10")).unwrap();
        assert_eq!((s, e, m), (d("2027-02-11"), d("2027-03-23"), None));
        // Feb 15 .. Mar 14 is exactly one month
        let (_, e, m) = next_term(d("2027-02-15"), d("2027-03-14")).unwrap();
        assert_eq!((e, m), (d("2027-04-14"), Some(1)));
    }

    #[test]
    fn explicit_releases_shift_with_the_term_or_refuse() {
        let rs = vec![rel("A", "2027-12-31", 5.0), rel("A", "2027-01-31", 5.0)];
        let (s, e, shifted) = renewal_terms(d("2027-01-01"), d("2027-12-31"), Some(&rs)).unwrap();
        assert_eq!((s, e), (d("2028-01-01"), d("2028-12-31")));
        let dates: Vec<_> = shifted.unwrap().iter().map(|r| r.date).collect();
        assert_eq!(dates, vec![d("2028-01-31"), d("2028-12-31")]);
        // a day-count term whose last release lands after the renewed end
        let rs = vec![rel("A", "2027-02-10", 5.0)];
        let ok = renewal_terms(d("2027-01-01"), d("2027-02-10"), Some(&rs));
        assert!(ok.is_ok());
        let bad = vec![rel("A", "2027-01-31", 5.0)];
        // month-aligned term 2027-01-01..2027-01-31, release on the last day
        let (_, e2, _) = renewal_terms(d("2027-01-01"), d("2027-01-31"), Some(&bad)).unwrap();
        assert_eq!(e2, d("2027-02-28"));
    }

    #[test]
    fn comparison_counts_late_overdue_and_undated() {
        let today = d("2027-03-15");
        let releases = vec![
            rel("A", "2027-01-01", 100.0), rel("A", "2027-02-01", 100.0),
            rel("A", "2027-03-01", 100.0), rel("A", "2027-04-01", 100.0),
        ];
        let commitments = vec![
            com("A", "2027-01-01", 100.0, "fulfilled", Some("2027-01-01")), // on time
            com("A", "2027-02-01", 90.0, "fulfilled", Some("2027-02-05")),  // late, short
            com("A", "2027-03-01", 100.0, "open", None),                    // overdue
            com("A", "2027-04-01", 100.0, "open", None),                    // future
        ];
        let v = commitment_comparison(&releases, &commitments, today);
        assert_eq!(v["committed_units"], 400.0);
        assert_eq!(v["due_to_date"], 300.0);
        assert_eq!(v["delivered_units"], 190.0);
        assert_eq!(v["fill_rate_pct"], 63.3);
        assert_eq!(v["term_fill_pct"], 47.5);
        assert_eq!(v["late_deliveries"], 1);
        assert_eq!(v["late_units"], 90.0);
        assert_eq!(v["max_days_late"], 4);
        assert_eq!(v["overdue_open"], 1);
        assert_eq!(v["overdue_open_units"], 100.0);
        assert_eq!(v["fulfilled_undated"], 0);
        assert_eq!(v["shortfall_to_date"], 110.0);
    }

    #[test]
    fn nothing_due_is_not_a_hundred_percent() {
        let v = commitment_comparison(&[rel("A", "2030-01-01", 10.0)], &[], d("2027-01-01"));
        assert!(v["fill_rate_pct"].is_null());
        assert_eq!(v["term_fill_pct"], 0.0);
    }

    #[test]
    fn fulfilled_without_a_date_is_neither_late_nor_on_time() {
        let v = commitment_comparison(
            &[rel("A", "2027-01-01", 10.0)],
            &[com("A", "2027-01-01", 10.0, "fulfilled", None)],
            d("2027-02-01"),
        );
        assert_eq!(v["fulfilled_undated"], 1);
        assert_eq!(v["late_deliveries"], 0);
        assert_eq!(v["delivered_units"], 10.0);
    }

    #[test]
    fn a_sku_off_the_schedule_is_not_counted() {
        let v = commitment_comparison(
            &[rel("A", "2027-01-01", 10.0)],
            &[com("Z", "2027-01-01", 99.0, "fulfilled", Some("2027-01-01"))],
            d("2027-02-01"),
        );
        assert_eq!(v["delivered_units"], 0.0);
    }

    /// The differential test: every case Python generated from its own
    /// implementation (`backend/tests/test_contract_renewal_pure.py`) is
    /// replayed here and must give the same numbers.
    #[test]
    fn matches_the_python_reference() {
        let raw = include_str!("../../tests/contract/renewal_diff_cases.json");
        let doc: Value = serde_json::from_str(raw).unwrap();
        let mut checked = 0;
        for case in doc["cases"].as_array().unwrap() {
            let kind = case["fn"].as_str().unwrap();
            let input = &case["input"];
            let expect = &case["output"];
            let got = run_case(kind, input);
            assert!(same(&got, expect), "{kind} {input}\n rust:   {got}\n python: {expect}");
            checked += 1;
        }
        assert!(checked >= 300, "the fixture should carry hundreds of cases, found {checked}");
    }

    fn dv(v: &Value) -> NaiveDate {
        d(v.as_str().unwrap())
    }
    fn rels(v: &Value) -> Vec<Release> {
        v.as_array().unwrap().iter().map(|r| Release {
            sku: r["sku"].as_str().unwrap().into(), date: dv(&r["date"]), quantity: r["quantity"].as_f64().unwrap(),
        }).collect()
    }
    fn opt_i(v: &Value) -> Option<i64> {
        v.as_i64()
    }

    fn run_case(kind: &str, i: &Value) -> Value {
        match kind {
            "expand_releases" => {
                let lines: Vec<Line> = i["lines"].as_array().unwrap().iter().map(|l| Line {
                    sku: l["sku"].as_str().unwrap().into(), total_quantity: l["total_quantity"].as_f64().unwrap(),
                }).collect();
                let explicit = if i["releases"].is_null() { None } else { Some(rels(&i["releases"])) };
                let out = expand_releases(i["schedule_kind"].as_str().unwrap(), dv(&i["period_start"]),
                    dv(&i["period_end"]), &lines, explicit.as_deref());
                Value::Array(out.iter().map(|r| json!({"sku": r.sku, "date": r.date.to_string(), "quantity": r.quantity})).collect())
            }
            "renewal_view" => {
                let leads: Vec<i64> = i["lead_days"].as_array().unwrap().iter().map(|x| x.as_i64().unwrap()).collect();
                renewal_view(dv(&i["period_end"]), opt_i(&i["notice_days"]), i["auto_renew"].as_bool().unwrap(), &leads, dv(&i["today"]))
            }
            "due_alert" => {
                let leads: Vec<i64> = i["lead_days"].as_array().unwrap().iter().map(|x| x.as_i64().unwrap()).collect();
                let sent: HashSet<(String, Option<i64>)> = i["sent"].as_array().unwrap().iter()
                    .map(|p| (p[0].as_str().unwrap().to_string(), p[1].as_i64())).collect();
                due_alert(dv(&i["period_end"]), opt_i(&i["notice_days"]), &leads, dv(&i["today"]), &sent).unwrap_or(Value::Null)
            }
            "renewal_terms" => {
                let explicit = if i["releases"].is_null() { None } else { Some(rels(&i["releases"])) };
                match renewal_terms(dv(&i["period_start"]), dv(&i["period_end"]), explicit.as_deref()) {
                    Ok((s, e, sh)) => json!({
                        "period_start": s.to_string(), "period_end": e.to_string(),
                        "releases": sh.map(|v| v.iter().map(|r| json!({"sku": r.sku, "date": r.date.to_string(), "quantity": r.quantity})).collect::<Vec<_>>()),
                    }),
                    Err(RenewalError::ReleaseOutOfPeriod { sku, date, shifted }) => json!({
                        "error": "supply_contract_renewal_release_out_of_period",
                        "sku": sku, "date": date.to_string(), "shifted": shifted.to_string(),
                    }),
                    Err(RenewalError::PeriodTooLong) => json!({"error": "supply_contract_period_too_long"}),
                }
            }
            "commitment_comparison" => {
                let commitments: Vec<Commitment> = i["commitments"].as_array().unwrap().iter().map(|c| Commitment {
                    sku: c["sku"].as_str().unwrap().into(),
                    release_date: c["contract_release_date"].as_str().map(d),
                    quantity: c["quantity"].as_f64().unwrap(),
                    status: c["status"].as_str().unwrap().into(),
                    fulfilled_on: c["fulfilled_on"].as_str().map(d),
                }).collect();
                commitment_comparison(&rels(&i["releases"]), &commitments, dv(&i["today"]))
            }
            other => panic!("unknown fixture function {other}"),
        }
    }

    /// Structural equality where numbers compare as numbers within 1e-9
    /// (Python writes `100.0` and `100` for the same quantity).
    fn same(a: &Value, b: &Value) -> bool {
        match (a, b) {
            (Value::Number(x), Value::Number(y)) => (x.as_f64().unwrap() - y.as_f64().unwrap()).abs() < 1e-9,
            (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| same(p, q)),
            (Value::Object(x), Value::Object(y)) => {
                x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| same(v, w)))
            }
            _ => a == b,
        }
    }
}
