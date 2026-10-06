//! Date rules of a recurring delivery schedule. Pure: no database, no clock.
//!
//! The independent statement of the same rules is
//! `backend/inventory/recurring_delivery_dates.py` (the Python reference);
//! the differential test below replays its answers
//! (`tests/contract/gen_recurring_fixtures.py`).
//!
//! `weekly` every 7 days on a weekday; `fortnightly` every 14 days counted
//! from the first such weekday on or after the start; `semimonthly` the 15th
//! and the last day of each month; `monthly` a day of the month clamped to the
//! month's last day. Nominal dates lie in `[start, end]`. A nominal date that
//! is a holiday (or a weekend, when `avoid_weekends`) is skipped or moved to
//! the previous / next working day by `shift_rule`. The NOMINAL date is the
//! occurrence's identity; the DELIVERY date is where the commitment sits.

use std::collections::BTreeSet;

use chrono::{Datelike, Duration, NaiveDate};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Frequency {
    Weekly,
    Fortnightly,
    Semimonthly,
    Monthly,
}

impl Frequency {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "weekly" => Some(Self::Weekly),
            "fortnightly" => Some(Self::Fortnightly),
            "semimonthly" => Some(Self::Semimonthly),
            "monthly" => Some(Self::Monthly),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShiftRule {
    Skip,
    Before,
    After,
}

impl ShiftRule {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "skip" => Some(Self::Skip),
            "before" => Some(Self::Before),
            "after" => Some(Self::After),
            _ => None,
        }
    }
}

/// Longest search for a working day. The holiday list is capped well below
/// this (`MAX_HOLIDAYS` in the routes), so a working day is always found; the
/// bound only keeps the loop finite.
pub const MAX_SHIFT_DAYS: i64 = 60;
const SHIFT_PAD_DAYS: i64 = MAX_SHIFT_DAYS + 2;

#[derive(Debug, Clone)]
pub struct DateSpec {
    pub frequency: Frequency,
    /// 0 = Monday ... 6 = Sunday; weekly and fortnightly only.
    pub weekday: Option<u32>,
    /// 1..=31; monthly only (31 clamps to the last day).
    pub day_of_month: Option<u32>,
    pub start: NaiveDate,
    pub end: NaiveDate,
    pub holidays: BTreeSet<NaiveDate>,
    pub avoid_weekends: bool,
    pub shift_rule: ShiftRule,
}

/// One scheduled delivery: its identity and where it lands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Occurrence {
    pub nominal: NaiveDate,
    pub delivery: NaiveDate,
}

fn last_day(year: i32, month: u32) -> u32 {
    let (ny, nm) = if month == 12 { (year + 1, 1) } else { (year, month + 1) };
    NaiveDate::from_ymd_opt(ny, nm, 1)
        .and_then(|d| d.pred_opt())
        .map(|d| d.day())
        .unwrap_or(28)
}

/// Nominal dates inside `[lo, hi]` and inside the schedule's own start/end.
pub fn nominal_dates(spec: &DateSpec, lo: NaiveDate, hi: NaiveDate) -> Vec<NaiveDate> {
    let lo = lo.max(spec.start);
    let hi = hi.min(spec.end);
    let mut out = Vec::new();
    if lo > hi {
        return out;
    }
    match spec.frequency {
        Frequency::Weekly | Frequency::Fortnightly => {
            let step: i64 = if spec.frequency == Frequency::Weekly { 7 } else { 14 };
            let weekday = i64::from(spec.weekday.unwrap_or(0));
            let start_wd = i64::from(spec.start.weekday().num_days_from_monday());
            let anchor = spec.start + Duration::days((weekday - start_wd).rem_euclid(7));
            let mut k = 0;
            if lo > anchor {
                let days = (lo - anchor).num_days();
                k = (days + step - 1) / step;
            }
            let mut d = anchor + Duration::days(step * k);
            while d <= hi {
                if d >= lo {
                    out.push(d);
                }
                d += Duration::days(step);
            }
        }
        Frequency::Semimonthly | Frequency::Monthly => {
            let (mut y, mut m) = (lo.year(), lo.month());
            while (y, m) <= (hi.year(), hi.month()) {
                let last = last_day(y, m);
                let days: Vec<u32> = if spec.frequency == Frequency::Semimonthly {
                    vec![15, last]
                } else {
                    vec![spec.day_of_month.unwrap_or(1).min(last)]
                };
                for day in days {
                    if let Some(d) = NaiveDate::from_ymd_opt(y, m, day) {
                        if d >= lo && d <= hi {
                            out.push(d);
                        }
                    }
                }
                if m == 12 {
                    y += 1;
                    m = 1;
                } else {
                    m += 1;
                }
            }
        }
    }
    out
}

fn non_working(d: NaiveDate, holidays: &BTreeSet<NaiveDate>, avoid_weekends: bool) -> bool {
    holidays.contains(&d) || (avoid_weekends && d.weekday().num_days_from_monday() >= 5)
}

/// Where the delivery for `nominal` happens, or `None` when it is skipped.
pub fn delivery_date(
    nominal: NaiveDate,
    holidays: &BTreeSet<NaiveDate>,
    avoid_weekends: bool,
    rule: ShiftRule,
) -> Option<NaiveDate> {
    if !non_working(nominal, holidays, avoid_weekends) {
        return Some(nominal);
    }
    let step = match rule {
        ShiftRule::Skip => return None,
        ShiftRule::After => 1,
        ShiftRule::Before => -1,
    };
    let mut d = nominal;
    for _ in 0..MAX_SHIFT_DAYS {
        d += Duration::days(step);
        if !non_working(d, holidays, avoid_weekends) {
            return Some(d);
        }
    }
    None
}

/// Every delivery whose DELIVERY date is in `[lo, hi]`, ordered by nominal
/// date. Skipped occurrences are not listed.
pub fn occurrences(spec: &DateSpec, lo: NaiveDate, hi: NaiveDate) -> Vec<Occurrence> {
    let pad = Duration::days(SHIFT_PAD_DAYS);
    nominal_dates(spec, lo - pad, hi + pad)
        .into_iter()
        .filter_map(|nominal| {
            let delivery = delivery_date(nominal, &spec.holidays, spec.avoid_weekends, spec.shift_rule)?;
            (delivery >= lo && delivery <= hi).then_some(Occurrence { nominal, delivery })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> NaiveDate {
        NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()
    }

    fn spec(freq: Frequency, start: &str, end: &str) -> DateSpec {
        DateSpec {
            frequency: freq,
            weekday: None,
            day_of_month: None,
            start: d(start),
            end: d(end),
            holidays: BTreeSet::new(),
            avoid_weekends: false,
            shift_rule: ShiftRule::After,
        }
    }

    fn nominals(o: &[Occurrence]) -> Vec<String> {
        o.iter().map(|x| x.nominal.to_string()).collect()
    }

    #[test]
    fn weekly_starts_on_the_first_matching_weekday() {
        // 2026-10-06 is a Tuesday; first Thursday on/after it is 2026-10-08.
        let mut s = spec(Frequency::Weekly, "2026-10-06", "2026-11-05");
        s.weekday = Some(3);
        let o = occurrences(&s, d("2026-01-01"), d("2027-01-01"));
        assert_eq!(nominals(&o), ["2026-10-08", "2026-10-15", "2026-10-22", "2026-10-29", "2026-11-05"]);
    }

    #[test]
    fn fortnightly_is_anchored_on_the_start_not_on_the_window() {
        let mut s = spec(Frequency::Fortnightly, "2026-10-05", "2027-01-31");
        s.weekday = Some(0);
        let all = nominals(&occurrences(&s, d("2026-01-01"), d("2027-12-31")));
        let later = nominals(&occurrences(&s, d("2026-11-10"), d("2027-12-31")));
        assert_eq!(&all[..3], ["2026-10-05", "2026-10-19", "2026-11-02"]);
        // A window that begins mid-schedule keeps the same cadence.
        assert_eq!(later[0], "2026-11-16");
        assert!(all.ends_with(&later));
    }

    #[test]
    fn monthly_clamps_to_the_last_day() {
        let mut s = spec(Frequency::Monthly, "2027-01-01", "2027-05-31");
        s.day_of_month = Some(31);
        let o = occurrences(&s, d("2027-01-01"), d("2027-12-31"));
        assert_eq!(nominals(&o), ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"]);
        let o = occurrences(&s, d("2027-02-01"), d("2027-02-28"));
        assert_eq!(nominals(&o), ["2027-02-28"]);
        // Leap year.
        let mut s = spec(Frequency::Monthly, "2028-02-01", "2028-02-29");
        s.day_of_month = Some(30);
        assert_eq!(nominals(&occurrences(&s, d("2028-01-01"), d("2028-12-31"))), ["2028-02-29"]);
    }

    #[test]
    fn semimonthly_is_the_fifteenth_and_the_last_day() {
        let s = spec(Frequency::Semimonthly, "2026-10-16", "2026-12-15");
        let o = occurrences(&s, d("2026-01-01"), d("2027-01-01"));
        assert_eq!(nominals(&o), ["2026-10-31", "2026-11-15", "2026-11-30", "2026-12-15"]);
    }

    #[test]
    fn the_end_date_bounds_the_nominal_schedule() {
        let mut s = spec(Frequency::Monthly, "2026-10-01", "2026-12-10");
        s.day_of_month = Some(10);
        assert_eq!(
            nominals(&occurrences(&s, d("2026-01-01"), d("2030-01-01"))),
            ["2026-10-10", "2026-11-10", "2026-12-10"]
        );
        s.end = d("2026-12-09");
        assert_eq!(occurrences(&s, d("2026-01-01"), d("2030-01-01")).len(), 2);
    }

    #[test]
    fn holidays_skip_or_move_the_delivery() {
        let mut s = spec(Frequency::Monthly, "2026-10-01", "2026-12-31");
        s.day_of_month = Some(15);
        s.holidays = BTreeSet::from([d("2026-10-15"), d("2026-10-16")]);
        s.shift_rule = ShiftRule::After;
        let o = occurrences(&s, d("2026-10-01"), d("2026-12-31"));
        assert_eq!((o[0].nominal, o[0].delivery), (d("2026-10-15"), d("2026-10-17")));
        s.shift_rule = ShiftRule::Before;
        let o = occurrences(&s, d("2026-10-01"), d("2026-12-31"));
        assert_eq!(o[0].delivery, d("2026-10-14"));
        s.shift_rule = ShiftRule::Skip;
        let o = occurrences(&s, d("2026-10-01"), d("2026-12-31"));
        assert_eq!(nominals(&o), ["2026-11-15", "2026-12-15"]);
    }

    #[test]
    fn weekends_move_to_the_adjacent_working_day() {
        // 2026-11-15 is a Sunday.
        let mut s = spec(Frequency::Monthly, "2026-11-01", "2026-11-30");
        s.day_of_month = Some(15);
        s.avoid_weekends = true;
        s.shift_rule = ShiftRule::After;
        assert_eq!(occurrences(&s, d("2026-11-01"), d("2026-11-30"))[0].delivery, d("2026-11-16"));
        s.shift_rule = ShiftRule::Before;
        assert_eq!(occurrences(&s, d("2026-11-01"), d("2026-11-30"))[0].delivery, d("2026-11-13"));
    }

    #[test]
    fn the_window_is_in_delivery_dates() {
        // Nominal 2026-11-15 moves to 11-16; a window ending 11-15 excludes it,
        // one starting 11-16 includes it.
        let mut s = spec(Frequency::Monthly, "2026-11-01", "2026-11-30");
        s.day_of_month = Some(15);
        s.avoid_weekends = true;
        assert!(occurrences(&s, d("2026-11-01"), d("2026-11-15")).is_empty());
        assert_eq!(occurrences(&s, d("2026-11-16"), d("2026-11-30")).len(), 1);
    }

    #[test]
    fn a_window_outside_the_schedule_is_empty() {
        let mut s = spec(Frequency::Weekly, "2026-10-01", "2026-10-31");
        s.weekday = Some(0);
        assert!(occurrences(&s, d("2026-11-10"), d("2026-12-31")).is_empty());
        assert!(occurrences(&s, d("2026-01-01"), d("2026-09-30")).is_empty());
    }

    /// Differential check against the Python reference. Fixtures come from
    /// `tests/contract/gen_recurring_fixtures.py`:
    ///
    ///     RECURRING_FIXTURES=OUT/recurring_fixtures.jsonl cargo test -- --ignored recurring_dates_match_python
    #[test]
    #[ignore]
    fn recurring_dates_match_python_reference() {
        let path = std::env::var("RECURRING_FIXTURES").expect("set RECURRING_FIXTURES");
        let text = std::fs::read_to_string(path).unwrap();
        let mut n = 0;
        for line in text.lines().filter(|l| !l.trim().is_empty()) {
            let v: serde_json::Value = serde_json::from_str(line).unwrap();
            let s = &v["spec"];
            let date = |x: &serde_json::Value| d(x.as_str().unwrap());
            let spec = DateSpec {
                frequency: Frequency::parse(s["frequency"].as_str().unwrap()).unwrap(),
                weekday: s["weekday"].as_u64().map(|x| x as u32),
                day_of_month: s["day_of_month"].as_u64().map(|x| x as u32),
                start: date(&s["start_date"]),
                end: date(&s["end_date"]),
                holidays: s["holiday_dates"].as_array().unwrap().iter().map(date).collect(),
                avoid_weekends: s["avoid_weekends"].as_bool().unwrap(),
                shift_rule: ShiftRule::parse(s["shift_rule"].as_str().unwrap()).unwrap(),
            };
            let got: Vec<[String; 2]> = occurrences(&spec, date(&v["lo"]), date(&v["hi"]))
                .iter()
                .map(|o| [o.nominal.to_string(), o.delivery.to_string()])
                .collect();
            let want: Vec<[String; 2]> = v["result"]
                .as_array()
                .unwrap()
                .iter()
                .map(|p| [p[0].as_str().unwrap().to_string(), p[1].as_str().unwrap().to_string()])
                .collect();
            assert_eq!(got, want, "case {line}");
            n += 1;
        }
        assert!(n > 1000, "only {n} fixtures");
        eprintln!("{n} fixtures match");
    }
}
