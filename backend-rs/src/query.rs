//! Query-string parameters the way Starlette reads them and FastAPI +
//! pydantic v2 validate them.
//!
//! * Parsing is `urllib.parse.parse_qsl(qs, keep_blank_values=True)`: split
//!   on `&`, `+` is a space, percent-decoding with replacement characters, a
//!   pair without `=` has an empty value, and when a name repeats the LAST
//!   value wins (`ImmutableMultiDict.get`).
//! * Validation errors carry `loc: ["query", name]` and the raw string as
//!   `input`, with pydantic's types and messages (`int_parsing`,
//!   `greater_than_equal`, `string_too_long`, `date_from_datetime_parsing`...).

use std::collections::HashMap;

use chrono::{Datelike, NaiveDate};
use percent_encoding::percent_decode_str;
use serde_json::{json, Value};

use crate::validation::Errors;

pub struct Query(HashMap<String, String>);

impl Query {
    pub fn parse(raw: Option<&str>) -> Query {
        let mut map = HashMap::new();
        for part in raw.unwrap_or("").split('&') {
            if part.is_empty() {
                continue;
            }
            let (k, v) = part.split_once('=').unwrap_or((part, ""));
            let dec = |s: &str| percent_decode_str(&s.replace('+', " ")).decode_utf8_lossy().into_owned();
            map.insert(dec(k), dec(v));
        }
        Query(map)
    }

    pub fn get(&self, name: &str) -> Option<&str> {
        self.0.get(name).map(String::as_str)
    }
}

fn loc(name: &str) -> Vec<Value> {
    vec![json!("query"), json!(name)]
}

/// An optional `str` query parameter with an optional `max_length`.
pub fn opt_str(errs: &mut Errors, q: &Query, name: &str, max_length: Option<usize>) -> Option<String> {
    let v = q.get(name)?;
    if let Some(max) = max_length {
        if v.chars().count() > max {
            errs.push("string_too_long", &loc(name),
                format!("String should have at most {max} character{}", if max == 1 { "" } else { "s" }),
                &json!(v), Some(json!({"max_length": max})));
            return None;
        }
    }
    Some(v.to_string())
}

/// An optional `str` with `pattern=`; `matches` decides the pattern.
pub fn opt_pattern(errs: &mut Errors, q: &Query, name: &str, pattern: &str, matches: fn(&str) -> bool) -> Option<String> {
    let v = q.get(name)?;
    if !matches(v) {
        errs.push("string_pattern_mismatch", &loc(name), format!("String should match pattern '{pattern}'"),
            &json!(v), Some(json!({"pattern": pattern})));
        return None;
    }
    Some(v.to_string())
}

/// A parsed integer: pydantic accepts arbitrarily large ints, which only the
/// database then refuses. `Big` keeps the sign of one that does not fit.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum PyInt {
    Small(i64),
    Big(bool), // true = positive
}

/// pydantic-core `str_as_int`: trimmed, optional sign, ASCII digits, and a
/// decimal part of zeros only ("5.0", "5.") is dropped.
pub fn parse_py_int(s: &str) -> Option<PyInt> {
    let t = s.trim();
    let parse = |x: &str| -> Option<PyInt> {
        let (neg, digits) = match x.as_bytes().first() {
            Some(b'-') => (true, &x[1..]),
            Some(b'+') => (false, &x[1..]),
            _ => (false, x),
        };
        if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        match x.parse::<i64>() {
            Ok(i) => Some(PyInt::Small(i)),
            Err(_) => Some(PyInt::Big(!neg)),
        }
    };
    if let Some(i) = parse(t) {
        return Some(i);
    }
    if let Some(dot) = t.find('.') {
        if t[dot + 1..].chars().all(|c| c == '0') {
            return parse(&t[..dot]);
        }
    }
    None
}

/// An `int` query parameter with a default and `ge` / `le` bounds. `None`
/// means an error was pushed.
pub fn int_param(errs: &mut Errors, q: &Query, name: &str, default: i64, ge: Option<i64>, le: Option<i64>) -> Option<PyInt> {
    let Some(raw) = q.get(name) else { return Some(PyInt::Small(default)) };
    let Some(v) = parse_py_int(raw) else {
        errs.push("int_parsing", &loc(name), "Input should be a valid integer, unable to parse string as an integer".into(),
            &json!(raw), None);
        return None;
    };
    let (below, above) = match v {
        PyInt::Small(i) => (ge.is_some_and(|g| i < g), le.is_some_and(|l| i > l)),
        PyInt::Big(pos) => (!pos && ge.is_some(), pos && le.is_some()),
    };
    if below {
        let g = ge.unwrap_or(0);
        errs.push("greater_than_equal", &loc(name), format!("Input should be greater than or equal to {g}"),
            &json!(raw), Some(json!({"ge": g})));
        return None;
    }
    if above {
        let l = le.unwrap_or(0);
        errs.push("less_than_equal", &loc(name), format!("Input should be less than or equal to {l}"),
            &json!(raw), Some(json!({"le": l})));
        return None;
    }
    Some(v)
}

// ── pydantic `date` from a string (speedate, lax mode) ───────────────────────

fn days_in_month(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ => {
            if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 { 29 } else { 28 }
        }
    }
}

/// speedate `Date::parse_bytes_partial`: the first ten bytes as YYYY-MM-DD.
fn date_partial(b: &[u8]) -> Result<NaiveDate, &'static str> {
    if b.len() < 10 {
        return Err("input is too short");
    }
    let dig = |i: usize, err: &'static str| -> Result<u32, &'static str> {
        let c = b[i];
        if c.is_ascii_digit() { Ok(u32::from(c - b'0')) } else { Err(err) }
    };
    let mut year = 0u32;
    for i in 0..4 {
        year = year * 10 + dig(i, "invalid character in year")?;
    }
    if b[4] != b'-' {
        return Err("invalid date separator, expected `-`");
    }
    let month = dig(5, "invalid character in month")? * 10 + dig(6, "invalid character in month")?;
    if b[7] != b'-' {
        return Err("invalid date separator, expected `-`");
    }
    let day = dig(8, "invalid character in day")? * 10 + dig(9, "invalid character in day")?;
    if !(1..=12).contains(&month) {
        return Err("month value is outside expected range of 1-12");
    }
    if day < 1 || day > days_in_month(year as i32, month) {
        return Err("day value is outside expected range");
    }
    NaiveDate::from_ymd_opt(year as i32, month, day).ok_or("day value is outside expected range")
}

/// The time part of an RFC 3339 datetime from byte 11 on: whether it is
/// exactly midnight, or the speedate error.
fn time_is_zero(b: &[u8]) -> Result<bool, &'static str> {
    let t = &b[11..];
    let dig2 = |i: usize, err: &'static str| -> Result<u32, &'static str> {
        match (t.get(i), t.get(i + 1)) {
            (Some(x), Some(y)) if x.is_ascii_digit() && y.is_ascii_digit() => {
                Ok(u32::from(x - b'0') * 10 + u32::from(y - b'0'))
            }
            (None, _) | (_, None) => Err("input is too short"),
            _ => Err(err),
        }
    };
    let hour = dig2(0, "invalid character in hour")?;
    if t.get(2) != Some(&b':') {
        return Err(if t.len() <= 2 { "input is too short" } else { "invalid time separator, expected `:`" });
    }
    let minute = dig2(3, "invalid character in minute")?;
    let mut pos = 5;
    let mut second = 0;
    let mut frac_zero = true;
    if t.get(pos) == Some(&b':') {
        second = dig2(pos + 1, "invalid character in second")?;
        pos += 3;
        if matches!(t.get(pos), Some(b'.') | Some(b',')) {
            pos += 1;
            let start = pos;
            while t.get(pos).is_some_and(u8::is_ascii_digit) {
                if t[pos] != b'0' {
                    frac_zero = false;
                }
                pos += 1;
            }
            if pos == start {
                return Err("second fraction value is more than 6 digits long");
            }
        }
    }
    if hour > 23 {
        return Err("hour value is outside expected range of 0-23");
    }
    if minute > 59 {
        return Err("minute value is outside expected range of 0-59");
    }
    if second > 59 {
        return Err("second value is outside expected range of 0-59");
    }
    // Offset: Z, or +HH:MM / -HH:MM / +HHMM. Anything else left over is junk.
    match t.get(pos) {
        None => {}
        Some(b'Z') | Some(b'z') if t.len() == pos + 1 => {}
        Some(b'+') | Some(b'-') => {
            let rest = &t[pos + 1..];
            let ok = (rest.len() == 5 && rest[2] == b':' && rest[..2].iter().chain(&rest[3..]).all(u8::is_ascii_digit))
                || (rest.len() == 4 && rest.iter().all(u8::is_ascii_digit))
                || (rest.len() == 2 && rest.iter().all(u8::is_ascii_digit));
            if !ok {
                return Err("invalid timezone sign");
            }
        }
        Some(_) => return Err("unexpected extra characters at the end of the input"),
    }
    Ok(hour == 0 && minute == 0 && second == 0 && frac_zero)
}

pub enum DateOutcome {
    Date(NaiveDate),
    Inexact,
    Parsing(&'static str),
}

/// pydantic's lax `date` validation of a string: an exact date, else a
/// datetime (RFC 3339 or a unix timestamp) that must fall on midnight.
pub fn validate_date_str(s: &str) -> DateOutcome {
    let b = s.as_bytes();
    // Date::parse_bytes: exact YYYY-MM-DD, or an integer timestamp on a day.
    if b.len() == 10 {
        if let Ok(d) = date_partial(b) {
            return DateOutcome::Date(d);
        }
    }
    let as_int = s.parse::<i64>().ok().filter(|_| !s.starts_with('+'));
    // DateTime::parse_bytes: RFC 3339 first, then a number as a timestamp.
    let rfc = (|| -> Result<DateOutcome, &'static str> {
        let d = date_partial(b)?;
        if b.len() == 10 {
            return Ok(DateOutcome::Date(d));
        }
        if !matches!(b[10], b'T' | b't' | b' ' | b'_') {
            return Err("invalid datetime separator, expected `T`, `t`, `_` or space");
        }
        Ok(if time_is_zero(b)? { DateOutcome::Date(d) } else { DateOutcome::Inexact })
    })();
    match rfc {
        Ok(o) => o,
        Err(e) => match as_int {
            Some(ts) => {
                // speedate: magnitudes above 2e10 are milliseconds.
                let secs = if ts.abs() > 20_000_000_000 { ts.div_euclid(1000) } else { ts };
                let ms_rem = ts.abs() > 20_000_000_000 && ts.rem_euclid(1000) != 0;
                if secs.rem_euclid(86_400) == 0 && !ms_rem {
                    match chrono::DateTime::from_timestamp(secs, 0) {
                        Some(dt) => DateOutcome::Date(dt.date_naive()),
                        None => DateOutcome::Parsing(e),
                    }
                } else {
                    DateOutcome::Inexact
                }
            }
            None => DateOutcome::Parsing(e),
        },
    }
}

/// An optional `date` query parameter.
pub fn opt_date(errs: &mut Errors, q: &Query, name: &str) -> Option<NaiveDate> {
    let raw = q.get(name)?;
    match validate_date_str(raw) {
        DateOutcome::Date(d) if d.year() >= 1 => Some(d),
        DateOutcome::Date(_) => {
            errs.push("date_from_datetime_parsing", &loc(name),
                "Input should be a valid date or datetime, year value is outside expected range".into(),
                &json!(raw), Some(json!({"error": "year value is outside expected range"})));
            None
        }
        DateOutcome::Inexact => {
            errs.push("date_from_datetime_inexact", &loc(name),
                "Datetimes provided to dates should have zero time - e.g. be exact dates".into(), &json!(raw), None);
            None
        }
        DateOutcome::Parsing(e) => {
            errs.push("date_from_datetime_parsing", &loc(name),
                format!("Input should be a valid date or datetime, {e}"), &json!(raw), Some(json!({"error": e})));
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn last_value_wins_and_blanks_are_kept() {
        let q = Query::parse(Some("a=1&a=2&b=&c&d=x+y%20z"));
        assert_eq!(q.get("a"), Some("2"));
        assert_eq!(q.get("b"), Some(""));
        assert_eq!(q.get("c"), Some(""));
        assert_eq!(q.get("d"), Some("x y z"));
        assert_eq!(q.get("e"), None);
    }

    #[test]
    fn ints_parse_like_pydantic() {
        assert_eq!(parse_py_int(" 5 "), Some(PyInt::Small(5)));
        assert_eq!(parse_py_int("5.0"), Some(PyInt::Small(5)));
        assert_eq!(parse_py_int("+5"), Some(PyInt::Small(5)));
        assert_eq!(parse_py_int("2.5"), None);
        assert_eq!(parse_py_int("abc"), None);
        assert_eq!(parse_py_int("99999999999999999999"), Some(PyInt::Big(true)));
    }

    fn outcome(s: &str) -> String {
        match validate_date_str(s) {
            DateOutcome::Date(d) => d.to_string(),
            DateOutcome::Inexact => "inexact".into(),
            DateOutcome::Parsing(e) => e.into(),
        }
    }

    #[test]
    fn dates_match_the_live_api() {
        // Observed on the Python API (tests/contract probe, 2026-10-05).
        assert_eq!(outcome(""), "input is too short");
        assert_eq!(outcome("2026-13-01"), "month value is outside expected range of 1-12");
        assert_eq!(outcome("2026-10-05T00:00:00"), "2026-10-05");
        assert_eq!(outcome("2026-10-05T01:00:00"), "inexact");
        assert_eq!(outcome("1700000000"), "inexact");
        assert_eq!(outcome("20261005"), "inexact");
        assert_eq!(outcome("0"), "1970-01-01");
        assert_eq!(outcome("2026-10-05"), "2026-10-05");
        assert_eq!(outcome("2026-02-30"), "day value is outside expected range");
    }
}
