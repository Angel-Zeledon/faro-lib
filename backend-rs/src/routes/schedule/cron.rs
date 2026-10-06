//! A port of the slice of `croniter` 6.2.2 that `backend/api/v1/schedule.py`
//! uses: `croniter(expr)` to validate a FIVE-field expression (the request
//! model refuses any other field count before croniter sees it), and
//! `croniter(expr, now_in_tenant_tz).get_next(datetime)` to compute the next
//! firing instant. Defaults only: `day_or=True`, no `hash_id`,
//! `max_years_between_matches=50`, forward search.
//!
//! The error TEXTS are part of the contract (the 422 echoes them), so every
//! check runs in croniter's order with croniter's wording, quirks included:
//! list elements are processed from the END (`e_list.pop()`), a range whose
//! low bound is above its high bound wraps around, `7` in day-of-week is
//! Sunday, and a zero step fails with `range()`'s own message.
//!
//! Known gaps (docs/rust-migration.md, wave 1 R2): Python's `\d` and
//! `int()` accept non-ASCII decimal digits; this port treats only ASCII
//! digits as digits. An `r` (random) field is random on both sides, so the
//! two can never agree on its value.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use chrono::{Datelike, Duration, NaiveDate, NaiveDateTime, Timelike};
use regex::Regex;

use super::tz::Zone;

const MINUTE: usize = 0;
const HOUR: usize = 1;
const DAY: usize = 2;
const MONTH: usize = 3;
const DOW: usize = 4;

const RANGES: [(i64, i64); 5] = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)];
const LEN_MEANS_ALL: [usize; 5] = [60, 24, 31, 12, 7];
/// Ints parsed from digit strings saturate here: far above every bound, far
/// below overflow in the arithmetic below.
const SAT: i64 = 1 << 40;

/// One expanded value: a number, `*` or `l` (last day of month).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum V {
    Int(i64),
    Star,
    L,
}

/// An nth-weekday marker: `2#3` (third Tuesday) or `l2` (last Tuesday).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Nth {
    N(i64),
    L,
}

#[derive(Debug, Clone)]
pub struct Expanded {
    pub fields: [Vec<V>; 5],
    /// Insertion-ordered (a Python dict) weekday -> set of nth markers.
    pub nth: Vec<(i64, BTreeSet<Nth>)>,
    pub nearest_weekday: BTreeSet<i64>,
}

/// `CroniterBadCronError` & co: the message is what reaches the 422.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CronError(pub String);

/// `CroniterBadDateError` (no match within 50 years).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BadDate;

fn re(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("valid regex"))
}

fn hash_re() -> &'static Regex {
    static C: OnceLock<Regex> = OnceLock::new();
    re(&C, r"^(?P<hash_type>h|r)(\((?P<range_begin>[0-9]+)-(?P<range_end>[0-9]+)\))?(/(?P<divisor>[0-9]+))?$")
}

fn special_dow_re() -> &'static Regex {
    static C: OnceLock<Regex> = OnceLock::new();
    let wd = "sun|mon|tue|wed|thu|fri|sat";
    let mo = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec";
    re(&C, &format!(
        r"^(?P<pre>((?P<he>(({wd})(-({wd}))?)|(({mo})(-({mo}))?)|\w+)#)|l)(?P<last>[0-9]+)$"
    ))
}

fn nearest_weekday_re() -> &'static Regex {
    static C: OnceLock<Regex> = OnceLock::new();
    re(&C, r"^(?:([0-9]+)w|w([0-9]+))$")
}

fn step_search_re() -> &'static Regex {
    static C: OnceLock<Regex> = OnceLock::new();
    re(&C, r"^([^-]+)-([^-/]+)(/([0-9]+))?$")
}

fn only_int(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

fn star_or_int(s: &str) -> bool {
    s == "*" || only_int(s)
}

/// `int(digits)`, saturating.
fn int_of(digits: &str) -> i64 {
    let mut v: i64 = 0;
    for b in digits.bytes() {
        v = (v * 10 + i64::from(b - b'0')).min(SAT);
    }
    v
}

/// `str(int(digits))`: what an f-string prints for a parsed number.
fn int_repr(digits: &str) -> String {
    let t = digits.trim_start_matches('0');
    if t.is_empty() { "0".into() } else { t.into() }
}

/// Python's `str.isspace`, for `str.split()`.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.split()` with no separator.
pub fn py_split(s: &str) -> Vec<String> {
    s.split(py_space).filter(|p| !p.is_empty()).map(str::to_string).collect()
}

/// `str.strip()`.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// `ALPHACONV[field][key]`, or `CroniterNotAlphaError`.
fn alphaconv(field: usize, key: &str, expressions: &[String]) -> Result<V, CronError> {
    let months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
    let days = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
    let hit = match field {
        DAY if key == "l" => Some(V::L),
        MONTH => months.iter().position(|m| *m == key).map(|i| V::Int(i as i64 + 1)),
        DOW => days.iter().position(|d| *d == key).map(|i| V::Int(i as i64)),
        _ => None,
    };
    hit.ok_or_else(|| CronError(format!("[{}] is not acceptable", expressions.join(" "))))
}

/// `value_alias` for a 5-field expression: only day-of-week 7 -> 0.
fn value_alias(v: i64, field: usize) -> i64 {
    if field == DOW && v == 7 { 0 } else { v }
}

/// An element of `e_list`: the split strings, plus what range expansion
/// appends (ints, or `"k#n"` strings). `a not in e_list` compares by type.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Item {
    S(String),
    I(i64),
}

impl Item {
    fn text(&self) -> String {
        match self {
            Item::S(s) => s.clone(),
            Item::I(i) => i.to_string(),
        }
    }
}

/// A random 32-bit value for an `r` field.
fn random_u32() -> u32 {
    (uuid::Uuid::new_v4().as_u128() & 0xFFFF_FFFF) as u32
}

/// `HashExpander.expand`.
fn hash_expand(idx: usize, expr: &str) -> Result<String, CronError> {
    let Some(m) = hash_re().captures(expr) else { return Ok(expr.to_string()) };
    if &m["hash_type"] == "h" {
        return Err(CronError("Hashed definitions must include hash_id".into()));
    }
    let rb = m.name("range_begin").map(|x| x.as_str());
    let rend = m.name("range_end").map(|x| x.as_str());
    let div = m.name("divisor").map(|x| x.as_str());
    let (min, max) = RANGES[idx];
    let do_ = |begin: i64, end: i64| -> i64 {
        let crc = i64::from(random_u32());
        let span = (end - begin + 1).max(1);
        ((crc >> idx) % span) + begin
    };
    if let (Some(b), Some(e)) = (rb, rend) {
        if int_of(b) >= int_of(e) {
            return Err(CronError("Range end must be greater than range begin".into()));
        }
    }
    if let (Some(b), Some(e), Some(d)) = (rb, rend, div) {
        if int_of(d) == 0 {
            return Err(CronError(format!("Bad expression: {expr}")));
        }
        let x = do_(int_of(b), int_of(d) - 1 + int_of(b));
        return Ok(format!("{x}-{}/{}", int_repr(e), int_repr(d)));
    }
    if let (Some(b), Some(e)) = (rb, rend) {
        return Ok(do_(int_of(b), int_of(e)).to_string());
    }
    if let Some(d) = div {
        if int_of(d) == 0 {
            return Err(CronError(format!("Bad expression: {expr}")));
        }
        let x = do_(min, int_of(d) - 1 + min);
        return Ok(format!("{x}-{max}/{}", int_repr(d)));
    }
    Ok(do_(min, max).to_string())
}

/// Python's `range(start, stop, step)` as a list, or `range()`'s error.
fn py_range(start: i64, stop: i64, step: i64) -> Result<Vec<i64>, String> {
    if step == 0 {
        return Err("range() arg 3 must not be zero".into());
    }
    let mut v = Vec::new();
    let mut x = start;
    while x < stop {
        v.push(x);
        x = x.saturating_add(step);
    }
    Ok(v)
}

/// `croniter._expand` for a 5-field expression. `expr_format` is the
/// stripped expression; messages quote it untouched.
pub fn expand(expr_format: &str) -> Result<Expanded, CronError> {
    let efl = expr_format.to_lowercase();
    let expressions = py_split(&efl);
    if expressions.len() != 5 {
        // Unreachable behind the request validator (it counts the same split).
        return Err(CronError("Exactly 5, 6 or 7 columns has to be specified for iterator expression.".into()));
    }
    let mut fields: [Vec<V>; 5] = Default::default();
    let mut nth_map: Vec<(i64, BTreeSet<Nth>)> = Vec::new();
    let mut nearest: BTreeSet<i64> = BTreeSet::new();

    for (field, raw_expr) in expressions.iter().enumerate() {
        let mut expr = hash_expand(field, raw_expr)?;
        if expr.contains('?') {
            if expr != "?" {
                return Err(CronError(format!(
                    "[{expr_format}] is not acceptable. Question mark can not used with other characters")));
            }
            if field != DAY && field != DOW {
                return Err(CronError(format!(
                    "[{expr_format}] is not acceptable. Question mark can only used in day_of_month or day_of_week")));
            }
            expr = "*".into();
        }
        let (fmin, fmax) = RANGES[field];
        let mut e_list: Vec<Item> = expr.split(',').map(|s| Item::S(s.to_string())).collect();
        let mut res: Vec<V> = Vec::new();

        while let Some(item) = e_list.pop() {
            let mut e = item.text();
            let mut nth: Option<Nth> = None;

            if field == DOW {
                if let Some(c) = special_dow_re().captures(&e) {
                    let he = c.name("he").map(|m| m.as_str().to_string()).unwrap_or_default();
                    let last = c.name("last").map(|m| m.as_str().to_string()).unwrap_or_default();
                    if !he.is_empty() {
                        let n = int_of(&last);
                        if !(1..=5).contains(&n) {
                            return Err(CronError(format!(
                                "[{expr_format}] is not acceptable. Invalid day_of_week value: '{}'",
                                int_repr(&last))));
                        }
                        e = he;
                        nth = Some(Nth::N(n));
                    } else if !last.is_empty() {
                        e = last;
                        nth = Some(Nth::L); // pre == "l"
                    }
                }
            }

            if field == DAY {
                if let Some(c) = nearest_weekday_re().captures(&e) {
                    let digits = c.get(1).or_else(|| c.get(2)).map(|m| m.as_str()).unwrap_or("0");
                    let w_day = int_of(digits);
                    if !(1..=31).contains(&w_day) {
                        return Err(CronError(format!(
                            "[{expr_format}] is not acceptable, nearest weekday day value '{}' out of range",
                            int_repr(digits))));
                    }
                    if !e_list.is_empty() || !res.is_empty() {
                        return Err(CronError(format!(
                            "[{expr_format}] is not acceptable. 'W' can only be used with a single day value, not in a list or range")));
                    }
                    nearest.insert(w_day);
                    res.push(V::Int(w_day));
                    continue;
                }
            }

            // "*/5" -> "0-59/5"
            let mut t = match e.strip_prefix('*') {
                Some(rest) if rest.starts_with('/') && rest.len() > 1 && !rest.contains('\n') => {
                    format!("{fmin}-{fmax}{rest}")
                }
                _ => e.clone(),
            };
            let mut m = step_search_re().captures(&t).map(|c| {
                (c[1].to_string(), c[2].to_string(), c.get(4).map(|x| x.as_str().to_string()))
            });
            if m.is_none() {
                // "10/5" -> "10-59/5": greedy `(.+)/(.+)`, so the LAST slash.
                if let Some(pos) = e.rfind('/') {
                    if pos > 0 && pos + 1 < e.len() && !e.contains('\n') {
                        t = format!("{}-{fmax}/{}", &e[..pos], &e[pos + 1..]);
                        m = step_search_re().captures(&t).map(|c| {
                            (c[1].to_string(), c[2].to_string(), c.get(4).map(|x| x.as_str().to_string()))
                        });
                    }
                }
            }

            if let Some((low_s, high_s, step_s)) = m {
                let mut low = low_s;
                let mut high = high_s;
                if field == DAY && high == "l" {
                    high = "31".into();
                }
                let as_text = |v: V| match v {
                    V::Int(i) => i.to_string(),
                    V::L => "l".to_string(),
                    V::Star => "*".to_string(),
                };
                if !only_int(&low) {
                    low = as_text(alphaconv(field, &low, &expressions)?);
                }
                if !only_int(&high) {
                    high = as_text(alphaconv(field, &high, &expressions)?);
                }
                let step = step_s.as_deref().map(int_of).unwrap_or(1);
                for band in [&low, &high] {
                    if !only_int(band) {
                        return Err(CronError(format!(
                            "[{expr_format}] bands '{low}-{high}' in field {field} are not acceptable")));
                    }
                }
                let lo = value_alias(int_of(&low), field);
                let hi = value_alias(int_of(&high), field);
                if lo.max(hi) > fmin.max(fmax) {
                    return Err(CronError(format!("{expr_format} is out of bands")));
                }
                let rng: Vec<i64> = if lo > hi {
                    let whole_len = fmax - fmin + 1;
                    let mut rng = py_range(lo, fmax + 1, step).map_err(CronError)?;
                    let mut to_skip = 0;
                    if let Some(&last) = rng.last() {
                        let already_skipped = fmax - last;
                        let curpos = last - fmin;
                        if curpos + step > whole_len && already_skipped < step {
                            to_skip = step - already_skipped;
                        }
                    }
                    rng.extend(py_range(fmin + to_skip, hi + 1, step).map_err(CronError)?);
                    rng
                } else if lo == hi {
                    py_range(fmin, fmax + 1, step).map_err(CronError)?
                } else {
                    py_range(lo, hi + 1, step).map_err(|m| CronError(format!("invalid range: {m}")))?
                };
                let new_items: Vec<Item> = match (&nth, field) {
                    (Some(Nth::N(n)), DOW) => rng.iter().map(|i| Item::S(format!("{i}#{n}"))).collect(),
                    _ => rng.iter().map(|i| Item::I(*i)).collect(),
                };
                for a in new_items {
                    if !e_list.contains(&a) {
                        e_list.push(a);
                    }
                }
            } else {
                if t.starts_with('-') {
                    return Err(CronError(format!(
                        "[{expr_format}] is not acceptable, negative numbers not allowed")));
                }
                let v = if star_or_int(&t) {
                    if t == "*" { V::Star } else { V::Int(int_of(&t)) }
                } else {
                    alphaconv(field, &t, &expressions)?
                };
                let v = match v {
                    V::Int(i) => V::Int(value_alias(i, field)),
                    other => other,
                };
                if let V::Int(i) = v {
                    if i < fmin || i > fmax {
                        return Err(CronError(format!("[{expr_format}] is not acceptable, out of range")));
                    }
                }
                res.push(v.clone());
                if field == DOW {
                    if let (Some(n), V::Int(day)) = (nth, &v) {
                        match nth_map.iter_mut().find(|(k, _)| k == day) {
                            Some((_, set)) => {
                                set.insert(n);
                            }
                            None => nth_map.push((*day, BTreeSet::from([n]))),
                        }
                    }
                }
            }
        }

        // set(), then sorted by f"{i:02}" for ints and the string itself.
        let mut uniq: Vec<V> = Vec::new();
        for v in res {
            if !uniq.contains(&v) {
                uniq.push(v);
            }
        }
        let key = |v: &V| match v {
            V::Int(i) => format!("{i:02}"),
            V::Star => "*".to_string(),
            V::L => "l".to_string(),
        };
        uniq.sort_by_key(key);
        if uniq.len() == LEN_MEANS_ALL[field] {
            let keep = (field == DAY && !expressions[DOW].contains('*'))
                || (field == DOW && !expressions[DAY].contains('*'));
            if !keep {
                uniq = vec![V::Star];
            }
        }
        fields[field] = if uniq.len() == 1 && uniq[0] == V::Star { vec![V::Star] } else { uniq };
    }

    if !nth_map.is_empty() {
        let all: BTreeSet<V> = fields[DOW].iter().cloned().collect();
        let dow_set: Vec<i64> = all
            .iter()
            .filter_map(|v| match v {
                V::Int(i) if !nth_map.iter().any(|(k, _)| k == i) => Some(*i),
                _ => None,
            })
            .collect();
        if !dow_set.is_empty() && all.len() != LEN_MEANS_ALL[DOW] {
            let dow_repr = format!("{{{}}}", dow_set.iter().map(i64::to_string).collect::<Vec<_>>().join(", "));
            let nth_repr = format!("{{{}}}", nth_map.iter().map(|(k, set)| {
                let inner: Vec<String> = set.iter().map(|n| match n {
                    Nth::N(i) => i.to_string(),
                    Nth::L => "'l'".to_string(),
                }).collect();
                format!("{k}: {{{}}}", inner.join(", "))
            }).collect::<Vec<_>>().join(", "));
            return Err(CronError(format!(
                "day-of-week field does not support mixing literal values and nth day of week syntax.  \
                 Cron: '{expr_format}'    dow={dow_repr} vs nth={nth_repr}")));
        }
    }
    Ok(Expanded { fields, nth: nth_map, nearest_weekday: nearest })
}

// ── get_next ─────────────────────────────────────────────────────────────────

fn is_leap(y: i32) -> bool {
    y % 400 == 0 || (y % 4 == 0 && y % 100 != 0)
}

fn last_day_of_month(y: i32, m: u32) -> u32 {
    const DAYS: [u32; 12] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    DAYS[(m - 1) as usize] + u32::from(m == 2 && is_leap(y))
}

/// `_get_next_nearest_diff` with a looping range (never `None`).
fn next_nearest_diff(x: i64, to_check: &[V], range_val: i64) -> i64 {
    for d in to_check {
        let d = match d {
            V::L => range_val,
            V::Int(i) if *i > range_val => continue,
            V::Int(i) => *i,
            V::Star => continue, // never reached: callers skip lists holding '*'
        };
        if d >= x {
            return d - x;
        }
    }
    match to_check.first() {
        Some(V::Int(i)) => i - x + range_val,
        // `'l' - x`: a TypeError in Python; a list starting with 'l' always
        // returned inside the loop above.
        _ => range_val - x,
    }
}

/// `datetime.replace(hour=0, minute=0, second=0)` then `+ timedelta(days=n)`.
fn days_then_midnight(d: NaiveDateTime, days: i64) -> NaiveDateTime {
    d.date().and_hms_opt(0, 0, 0).expect("valid") + Duration::days(days)
}

/// The days of `year-month` that fall on `wday` (0 = Sunday), ascending.
fn nth_weekday_days(year: i32, month: u32, wday: i64) -> Vec<u32> {
    (1..=last_day_of_month(year, month))
        .filter(|&d| {
            let date = NaiveDate::from_ymd_opt(year, month, d).expect("valid");
            i64::from(date.weekday().num_days_from_sunday()) == wday
        })
        .collect()
}

/// `_get_nearest_weekday`.
fn nearest_weekday(year: i32, month: u32, day: i64) -> i64 {
    let last = i64::from(last_day_of_month(year, month));
    let day = day.min(last);
    let wd = NaiveDate::from_ymd_opt(year, month, day as u32).expect("valid").weekday().num_days_from_monday();
    match wd {
        0..=4 => day,
        5 => if day > 1 { day - 1 } else { day + 2 },
        _ => if day < last { day + 1 } else { day - 2 },
    }
}

fn has_star(v: &[V]) -> bool {
    v.contains(&V::Star)
}

/// `_calc` without the time-zone tail: the first matching wall-clock minute
/// strictly after `now` (naive).
pub fn calc_naive(now: NaiveDateTime, ex: &Expanded, fields: &[Vec<V>; 5]) -> Result<NaiveDateTime, BadDate> {
    let start = now + Duration::minutes(1);
    let mut d = start.date().and_hms_opt(start.hour(), start.minute(), 0).expect("valid");
    let current_year = d.year();
    let mut year = current_year;
    let use_nth = !ex.nth.is_empty();
    let use_nearest = !ex.nearest_weekday.is_empty();

    while (year - current_year).abs() <= 50 {
        let mut changed = false;
        // proc_month
        if !has_star(&fields[MONTH]) {
            let diff = next_nearest_diff(i64::from(d.month()), &fields[MONTH], 12);
            if diff != 0 {
                let total = i64::from(d.year()) * 12 + i64::from(d.month()) - 1 + diff;
                let (y, m) = ((total.div_euclid(12)) as i32, (total.rem_euclid(12) + 1) as u32);
                d = NaiveDate::from_ymd_opt(y, m, 1).expect("valid").and_hms_opt(0, 0, 0).expect("valid");
                changed = true;
            }
        }
        // proc_day_of_month / proc_nearest_weekday
        if !changed {
            let days = i64::from(last_day_of_month(d.year(), d.month()));
            let dd = i64::from(d.day());
            if use_nearest {
                let mut cands: Vec<i64> = ex
                    .nearest_weekday
                    .iter()
                    .map(|&w| nearest_weekday(d.year(), d.month(), w))
                    .filter(|&c| dd <= c)
                    .collect();
                if cands.is_empty() {
                    d = days_then_midnight(d, days - dd + 1);
                    changed = true;
                } else {
                    cands.sort_unstable();
                    let diff = cands[0] - dd;
                    if diff != 0 {
                        d = days_then_midnight(d, diff);
                        changed = true;
                    }
                }
            } else if !has_star(&fields[DAY]) && !(fields[DAY].contains(&V::L) && days == dd) {
                let diff = next_nearest_diff(dd, &fields[DAY], days);
                if diff != 0 {
                    d = days_then_midnight(d, diff);
                    changed = true;
                }
            }
        }
        // proc_day_of_week / proc_day_of_week_nth
        if !changed {
            let dd = i64::from(d.day());
            if use_nth {
                let mut cands: Vec<i64> = Vec::new();
                for (wday, set) in &ex.nth {
                    let c = nth_weekday_days(d.year(), d.month(), *wday);
                    for n in set {
                        let cand = match n {
                            Nth::L => match c.last() {
                                Some(&x) => i64::from(x),
                                None => continue,
                            },
                            Nth::N(k) if (c.len() as i64) < *k => continue,
                            Nth::N(k) => i64::from(c[(*k - 1) as usize]),
                        };
                        if dd <= cand {
                            cands.push(cand);
                        }
                    }
                }
                if cands.is_empty() {
                    let days = i64::from(last_day_of_month(d.year(), d.month()));
                    d = days_then_midnight(d, days - dd + 1);
                    changed = true;
                } else {
                    cands.sort_unstable();
                    let diff = cands[0] - dd;
                    if diff != 0 {
                        d = days_then_midnight(d, diff);
                        changed = true;
                    }
                }
            } else if !has_star(&fields[DOW]) {
                let wd = i64::from(d.weekday().num_days_from_sunday());
                let diff = next_nearest_diff(wd, &fields[DOW], 7);
                if diff != 0 {
                    d = days_then_midnight(d, diff);
                    changed = true;
                }
            }
        }
        // proc_hour
        if !changed && !has_star(&fields[HOUR]) {
            let diff = next_nearest_diff(i64::from(d.hour()), &fields[HOUR], 24);
            if diff != 0 {
                d = d.date().and_hms_opt(d.hour(), 0, 0).expect("valid") + Duration::hours(diff);
                changed = true;
            }
        }
        // proc_minute
        if !changed && !has_star(&fields[MINUTE]) {
            let diff = next_nearest_diff(i64::from(d.minute()), &fields[MINUTE], 60);
            if diff != 0 {
                d = d.date().and_hms_opt(d.hour(), d.minute(), 0).expect("valid") + Duration::minutes(diff);
                changed = true;
            }
        }
        if changed {
            year = d.year();
            continue;
        }
        return Ok(d.with_second(0).expect("valid").with_nanosecond(0).expect("valid"));
    }
    Err(BadDate)
}

/// An aware wall-clock time: local naive value plus its UTC offset (seconds).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Aware {
    pub local: NaiveDateTime,
    pub offset: i32,
}

impl Aware {
    pub fn utc(&self) -> NaiveDateTime {
        self.local - Duration::seconds(i64::from(self.offset))
    }
}

/// `_add_tzinfo` for a zoneinfo zone, forward search.
fn add_tzinfo(date: NaiveDateTime, now: &Aware, zone: &Zone) -> (Aware, bool) {
    let mut local = date;
    if !zone.exists(local) {
        while !zone.exists(local) {
            local += Duration::minutes(1);
        }
        return (Aware { local, offset: zone.local_offset(local, 0) }, false);
    }
    let result = Aware { local, offset: zone.local_offset(local, 0) };
    let farther = Aware { local, offset: zone.local_offset(local, 1) };
    if result.offset != farther.offset && !(result.utc() > now.utc()) {
        return (farther, true);
    }
    (result, true)
}

/// `_calc` for an aware `now`, with croniter's DST handling.
fn calc_aware(now: &Aware, ex: &Expanded, fields: &[Vec<V>; 5], zone: &Zone) -> Result<Aware, BadDate> {
    let unaware = calc_naive(now.local, ex, fields)?;
    let (mut aware, mut exists) = add_tzinfo(unaware, now, zone);
    let mut unaware = unaware;
    if !exists && (!(aware.utc() > now.utc()) || has_star(&fields[HOUR])) {
        while !exists {
            unaware = calc_naive(unaware, ex, fields)?;
            let (a, e) = add_tzinfo(unaware, now, zone);
            aware = a;
            exists = e;
        }
    }
    let offset_delta = aware.offset - now.offset;
    if offset_delta == 0 {
        return Ok(aware);
    }
    let alt_unaware = calc_naive(now.local + Duration::seconds(i64::from(offset_delta)), ex, fields)?;
    let (alt, _) = add_tzinfo(alt_unaware, now, zone);
    if !(alt.utc() > now.utc()) {
        return Ok(aware);
    }
    if aware.utc() > alt.utc() {
        return Ok(alt);
    }
    Ok(aware)
}

/// `croniter(expr, now).get_next(datetime)`: the next firing as a UTC
/// naive datetime. `now_utc` is the current instant.
pub fn next_after(ex: &Expanded, now_utc: NaiveDateTime, zone: &Zone) -> Result<NaiveDateTime, BadDate> {
    let offset = zone.offset_at_utc(now_utc);
    let now = Aware { local: now_utc + Duration::seconds(i64::from(offset)), offset };
    let fields = ex.fields.clone();
    // day_or: both day fields restricted means "either one".
    let result = if fields[DAY][0] != V::Star && fields[DOW][0] != V::Star {
        let mut f1 = fields.clone();
        f1[DOW] = vec![V::Star];
        let t1 = calc_aware(&now, ex, &f1, zone)?;
        let mut f2 = fields.clone();
        f2[DAY] = vec![V::Star];
        let t2 = calc_aware(&now, ex, &f2, zone)?;
        // Same tzinfo on both sides: Python compares the wall-clock values.
        if t1.local < t2.local { t1 } else { t2 }
    } else {
        calc_aware(&now, ex, &fields, zone)?
    };
    Ok(result.utc())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn err(e: &str) -> String {
        expand(e).unwrap_err().0
    }

    fn at(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S").unwrap()
    }

    #[test]
    fn messages_match_croniter() {
        assert_eq!(err("0 99 * * 1"), "[0 99 * * 1] is not acceptable, out of range");
        assert_eq!(err("a b c d e"), "[a b c d e] is not acceptable");
        assert_eq!(err("*/0 * * * *"), "invalid range: range() arg 3 must not be zero");
        assert_eq!(err("0 6 * * 8"), "[0 6 * * 8] is not acceptable, out of range");
        assert_eq!(err("0 6 * * 1#6"), "[0 6 * * 1#6] is not acceptable. Invalid day_of_week value: '6'");
        assert_eq!(err("60 * * * *"), "[60 * * * *] is not acceptable, out of range");
        assert_eq!(err("H * * * *"), "Hashed definitions must include hash_id");
        assert_eq!(err("0 0 ? * ?x"), "[0 0 ? * ?x] is not acceptable. Question mark can not used with other characters");
        assert_eq!(err("? 0 * * *"), "[? 0 * * *] is not acceptable. Question mark can only used in day_of_month or day_of_week");
        assert_eq!(err("1-70 * * * *"), "1-70 * * * * is out of bands");
        assert_eq!(err("-1 * * * *"), "[-1 * * * *] is not acceptable, negative numbers not allowed");
        assert_eq!(err("0 0 l-5 * *"), "[0 0 l-5 * *] bands 'l-5' in field 2 are not acceptable");
        assert_eq!(err("0 0 * * 1,2#3"),
            "day-of-week field does not support mixing literal values and nth day of week syntax.  \
             Cron: '0 0 * * 1,2#3'    dow={1} vs nth={2: {3}}");
    }

    #[test]
    fn accepted_forms() {
        for e in ["5-1 * * * *", "0 6 31 2 *", "0 6 L * *", "0 6 * * MON-FRI", "*/15 * * * *",
                  "0 0 15w * *", "0 0 * * l5", "0 0 * * mon#2", "0 6 * jan-mar *", "0 0 ? * 1", "10/5 * * * *",
                  "0 0 * * 0-7", "0 0 * * 5-2"] {
            assert!(expand(e).is_ok(), "{e}");
        }
        let x = expand("0-5,10 * * * mon-fri").unwrap();
        assert_eq!(x.fields[MINUTE], vec![V::Int(0), V::Int(1), V::Int(2), V::Int(3), V::Int(4), V::Int(5), V::Int(10)]);
        assert_eq!(x.fields[DOW], vec![V::Int(1), V::Int(2), V::Int(3), V::Int(4), V::Int(5)]);
        // 0-7 is 0-0 after the alias: the whole week.
        assert_eq!(expand("0 0 * * 0-7").unwrap().fields[DOW], vec![V::Star]);
    }

    #[test]
    fn next_in_a_fixed_zone() {
        let z = Zone::Fixed(-6 * 3600);
        let ex = expand("0 6 * * 1").unwrap();
        // 2026-10-05 is a Monday; 05:00 UTC is 23:00 Sunday in Costa Rica.
        assert_eq!(next_after(&ex, at("2026-10-05 05:00:00"), &z).unwrap(), at("2026-10-05 12:00:00"));
        assert_eq!(next_after(&ex, at("2026-10-05 12:00:00"), &z).unwrap(), at("2026-10-12 12:00:00"));
        let ex = expand("0 0 1 * *").unwrap();
        assert_eq!(next_after(&ex, at("2026-12-15 00:00:00"), &z).unwrap(), at("2027-01-01 06:00:00"));
        assert_eq!(next_after(&expand("0 6 31 2 *").unwrap(), at("2026-01-01 00:00:00"), &z), Err(BadDate));
    }

    /// Differential check against the real croniter + zoneinfo. The fixtures
    /// come from `tests/contract/gen_cron_fixtures.py` and are named by
    /// `CRON_FIXTURES`; `cargo test -- --ignored cron_matches_croniter`.
    #[test]
    #[ignore]
    fn cron_matches_croniter() {
        let path = std::env::var("CRON_FIXTURES").expect("set CRON_FIXTURES to the jsonl file");
        let text = std::fs::read_to_string(path).unwrap();
        let (mut n, mut bad) = (0, Vec::new());
        for line in text.lines() {
            let f: serde_json::Value = serde_json::from_str(line).unwrap();
            let expr = f["expr"].as_str().unwrap();
            let expected = f["result"].as_str().unwrap();
            let got = match super::super::validate_cron(expr) {
                Err(m) if expected == "COUNT" && m.starts_with("cron_expr must have") => "COUNT".to_string(),
                Err(m) => format!("ERR:{}", m.trim_start_matches("cron_expr is not a valid cron expression: ")),
                Ok(v) => {
                    let zone = Zone::from_name(f["zone"].as_str().unwrap()).unwrap();
                    let now = chrono::DateTime::parse_from_rfc3339(f["now"].as_str().unwrap()).unwrap().naive_utc();
                    match next_after(&expand(&v).unwrap(), now, &zone) {
                        Ok(t) => crate::pycompat::isoformat_utc(&t.and_utc()),
                        Err(BadDate) => "BADDATE".into(),
                    }
                }
            };
            n += 1;
            if got != expected {
                bad.push(format!("{expr:?} {} {}: python={expected} rust={got}", f["zone"], f["now"]));
            }
        }
        assert!(bad.is_empty(), "{} of {n} differ:\n{}", bad.len(), bad.join("\n"));
    }

    #[test]
    fn day_or_takes_the_earlier() {
        let z = Zone::Fixed(0);
        // The 20th OR any Monday: from Thu 2026-10-15, Monday the 19th wins.
        let ex = expand("0 0 20 * 1").unwrap();
        assert_eq!(next_after(&ex, at("2026-10-15 10:00:00"), &z).unwrap(), at("2026-10-19 00:00:00"));
    }
}
