//! Python's `datetime.fromisoformat` (CPython 3.12) and `datetime.isoformat`,
//! plus `format(x, "g")`.
//!
//! The reception route learns a supplier's lead time from
//! `received_at - generated_at`, so the exact instant the caller named matters
//! to the second: a date-only value is midnight UTC, `Z` and `+05:30` shift it,
//! a naive value is read as UTC. The grammar below follows CPython's C
//! implementation (`parse_isoformat_date`, `_find_isoformat_datetime_separator`,
//! `parse_isoformat_time`, `parse_hh_mm_ss_ff`); `tests/fixtures/inventory_calc.json`
//! replays thousands of valid and mutated strings through the real Python and
//! demands the same verdict and the same fields.

use chrono::{DateTime, NaiveDate, Utc};

use crate::pycompat::date_fromisoformat;

/// A Python `datetime`: wall-clock fields plus an optional UTC offset (naive
/// when `None`).
#[derive(Debug, Clone, PartialEq)]
pub struct PyDateTime {
    pub year: i32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
    pub micro: u32,
    /// `utcoffset()` in microseconds.
    pub offset_us: Option<i64>,
}

fn digits(b: &[u8], pos: usize, n: usize) -> Option<(i32, usize)> {
    if pos + n > b.len() {
        return None;
    }
    let mut v = 0i32;
    for &c in &b[pos..pos + n] {
        if !c.is_ascii_digit() {
            return None;
        }
        v = v * 10 + i32::from(c - b'0');
    }
    Some((v, pos + n))
}

/// `parse_hh_mm_ss_ff(tstr, tstr_end)`: `Ok((h, m, s, us, trailing))`, where
/// `trailing` is CPython's "1 if it is not the end of the string".
fn parse_hh_mm_ss_ff(b: &[u8]) -> Result<([i32; 3], i32, bool), ()> {
    let end = b.len();
    let mut p = 0usize;
    let mut vals = [0i32; 3];
    let mut has_separator = true;
    let mut broke_for_fraction = false;
    for i in 0..3 {
        let (v, np) = digits(b, p, 2).ok_or(())?;
        vals[i] = v;
        p = np;
        // `char c = *(p++)`: the byte after the digits, NUL at the end.
        let c = if p < end { b[p] } else { 0 };
        p += 1;
        if i == 0 {
            has_separator = c == b':';
        }
        if p >= end {
            // `return c != '\0'`
            return Ok((vals, 0, c != 0));
        } else if has_separator && c == b':' {
            continue;
        } else if c == b'.' || c == b',' {
            broke_for_fraction = true;
            break;
        } else if !has_separator {
            p -= 1;
        } else {
            return Err(());
        }
    }
    let _ = broke_for_fraction;
    // Fractional part.
    let len_remains = end.saturating_sub(p);
    let to_parse = if len_remains >= 6 { 6 } else { len_remains };
    let (mut micro, np) = if to_parse == 0 {
        // parse_digits with 0 digits parses nothing and succeeds with 0.
        (0, p)
    } else {
        digits(b, p, to_parse).ok_or(())?
    };
    p = np;
    if to_parse < 6 && to_parse > 0 {
        const CORRECTION: [i32; 5] = [100000, 10000, 1000, 100, 10];
        micro *= CORRECTION[to_parse - 1];
    }
    while p < end && b[p].is_ascii_digit() {
        p += 1;
    }
    Ok((vals, micro, p < end))
}

/// `parse_isoformat_time`: `Ok((h, m, s, us, tz_offset_us))`.
fn parse_time(b: &[u8]) -> Result<([i32; 3], i32, Option<i64>), ()> {
    let end = b.len();
    let mut tz_pos = 0usize;
    // do { if Z + - break } while (++tzinfo_pos < p_end);
    loop {
        if tz_pos < end && matches!(b[tz_pos], b'Z' | b'+' | b'-') {
            break;
        }
        tz_pos += 1;
        if tz_pos >= end {
            break;
        }
    }
    let tz_pos = tz_pos.min(end);
    let (vals, micro, trailing) = parse_hh_mm_ss_ff(&b[..tz_pos])?;
    if tz_pos == end {
        // No time zone: anything left over is an error.
        return if trailing { Err(()) } else { Ok((vals, micro, None)) };
    }
    if b[tz_pos] == b'Z' {
        return if tz_pos + 1 != end { Err(()) } else { Ok((vals, micro, Some(0))) };
    }
    let sign: i64 = if b[tz_pos] == b'-' { -1 } else { 1 };
    let (tv, tmicro, ttrailing) = parse_hh_mm_ss_ff(&b[tz_pos + 1..])?;
    if ttrailing {
        return Err(());
    }
    let off = sign * ((i64::from(tv[0]) * 3600 + i64::from(tv[1]) * 60 + i64::from(tv[2])) * 1_000_000 + i64::from(tmicro));
    Ok((vals, micro, Some(off)))
}

/// `_find_isoformat_datetime_separator`: where the date part ends, or `None`.
fn find_separator(b: &[u8]) -> Option<usize> {
    let len = b.len();
    let at = |i: usize| -> u8 { if i < len { b[i] } else { 0 } };
    if len == 7 {
        return Some(7);
    }
    if at(4) == b'-' {
        if at(5) == b'W' {
            if len < 8 {
                return None;
            }
            if len > 8 && at(8) == b'-' { Some(10) } else { Some(8) }
        } else {
            Some(10)
        }
    } else if at(4) == b'W' {
        let mut idx = 7;
        while idx < len && b[idx].is_ascii_digit() {
            idx += 1;
        }
        if idx < 9 {
            return Some(idx);
        }
        if idx > 9 {
            return Some(7);
        }
        Some(idx)
    } else {
        Some(8)
    }
}

/// `datetime.fromisoformat(s)`; `None` where Python raises `ValueError`.
pub fn fromisoformat(s: &str) -> Option<PyDateTime> {
    // Python encodes to UTF-8 and walks bytes; every digit it accepts is ASCII.
    let b = s.as_bytes();
    let sep = find_separator(b)?;
    if sep > b.len() {
        return None;
    }
    // Measured against CPython 3.12.10: a digit right after an `YYYY-Www-D`
    // date is never a separator there (`2026-W40-3610` is an error while
    // `2026-10-01610` is fine).
    if sep == 10 && b.get(5) == Some(&b'W') && b.get(10).is_some_and(|c| c.is_ascii_digit()) {
        return None;
    }
    let date_part = std::str::from_utf8(&b[..sep]).ok()?;
    let date: NaiveDate = date_fromisoformat(date_part)?;
    let (mut vals, mut micro, mut off) = ([0i32; 3], 0i32, None);
    if b.len() > sep {
        // Skip the separator: one UTF-8 character, whatever it is.
        let mut p = sep + 1;
        while p < b.len() && (b[p] & 0xC0) == 0x80 {
            p += 1;
        }
        let (v, m, o) = parse_time(&b[p..]).ok()?;
        vals = v;
        micro = m;
        off = o;
    }
    let (h, mi, sec) = (vals[0], vals[1], vals[2]);
    if !(0..=23).contains(&h) || !(0..=59).contains(&mi) || !(0..=59).contains(&sec) {
        return None;
    }
    if let Some(o) = off {
        // A timezone offset must be strictly within one day.
        if o <= -86_400_000_000 || o >= 86_400_000_000 {
            return None;
        }
    }
    use chrono::Datelike;
    Some(PyDateTime {
        year: date.year(),
        month: date.month(),
        day: date.day(),
        hour: h as u32,
        minute: mi as u32,
        second: sec as u32,
        micro: micro as u32,
        offset_us: off,
    })
}

impl PyDateTime {
    /// `dt.replace(tzinfo=utc)` when naive.
    pub fn assume_utc(mut self) -> PyDateTime {
        if self.offset_us.is_none() {
            self.offset_us = Some(0);
        }
        self
    }

    fn naive_micros(&self) -> i128 {
        let d = NaiveDate::from_ymd_opt(self.year, self.month, self.day).expect("valid date");
        let days = i128::from(d.signed_duration_since(NaiveDate::from_ymd_opt(1970, 1, 1).unwrap()).num_days());
        ((days * 86_400 + i128::from(self.hour) * 3600 + i128::from(self.minute) * 60 + i128::from(self.second)) * 1_000_000)
            + i128::from(self.micro)
    }

    /// The instant, as microseconds since the epoch (naive counts as UTC).
    pub fn utc_micros(&self) -> i128 {
        self.naive_micros() - i128::from(self.offset_us.unwrap_or(0))
    }

    /// The instant as a UTC `DateTime` for binding to a `timestamptz`.
    pub fn to_utc(&self) -> DateTime<Utc> {
        let us = self.utc_micros();
        DateTime::from_timestamp_micros(us as i64).expect("in range")
    }

    /// `dt.date()`: the wall-clock date in the datetime's own offset.
    pub fn local_date(&self) -> NaiveDate {
        NaiveDate::from_ymd_opt(self.year, self.month, self.day).expect("valid date")
    }

    /// `dt.isoformat()`.
    pub fn isoformat(&self) -> String {
        let mut out = format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        );
        if self.micro != 0 {
            out.push_str(&format!(".{:06}", self.micro));
        }
        if let Some(off) = self.offset_us {
            let sign = if off < 0 { '-' } else { '+' };
            let a = off.abs();
            let (secs, us) = (a / 1_000_000, a % 1_000_000);
            let (h, m, s) = (secs / 3600, (secs % 3600) / 60, secs % 60);
            out.push_str(&format!("{sign}{h:02}:{m:02}"));
            if s != 0 || us != 0 {
                out.push_str(&format!(":{s:02}"));
                if us != 0 {
                    out.push_str(&format!(".{us:06}"));
                }
            }
        }
        out
    }
}

// ── float(str) ───────────────────────────────────────────────────────────────

/// Python's `float(text)` for a string: surrounding whitespace is ignored,
/// single underscores between digits are allowed (`"1_0.5"`), and `inf`,
/// `infinity` and `nan` (any case, optional sign) are numbers. `None` is the
/// `ValueError`.
pub fn py_float_from_str(text: &str) -> Option<f64> {
    let t = crate::pycompat::py_strip(text);
    if t.is_empty() {
        return None;
    }
    let b = t.as_bytes();
    let mut cleaned = String::with_capacity(t.len());
    for (i, &c) in b.iter().enumerate() {
        if c == b'_' {
            let prev_digit = i > 0 && b[i - 1].is_ascii_digit();
            let next_digit = i + 1 < b.len() && b[i + 1].is_ascii_digit();
            if !(prev_digit && next_digit) {
                return None;
            }
        } else {
            cleaned.push(c as char);
        }
    }
    // Rust accepts a few forms Python rejects ("infinit", "+" alone are
    // already errors on both sides); reject the leading-dot-only shapes the
    // same way: both languages refuse "." and "e5".
    if !t.is_ascii() {
        return None;
    }
    cleaned.parse::<f64>().ok()
}

// ── format(x, "g") ───────────────────────────────────────────────────────────

/// Python's `f"{x:g}"`: six significant digits, trailing zeros removed,
/// exponent form when the decimal exponent is below -4 or at least 6.
pub fn fmt_g(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x < 0.0 { "-inf".into() } else { "inf".into() };
    }
    if x == 0.0 {
        return if x.is_sign_negative() { "-0".into() } else { "0".into() };
    }
    const P: i32 = 6;
    // `{:e}` rounds the exact value to P significant digits, ties to even,
    // like C's printf; its exponent is the one `%g` decides on.
    let sci = format!("{:.*e}", (P - 1) as usize, x);
    let (mantissa, exp) = sci.split_once('e').expect("exponent");
    let exp: i32 = exp.parse().expect("integer exponent");
    if (-4..P).contains(&exp) {
        let s = format!("{:.*}", (P - 1 - exp).max(0) as usize, x);
        if s.contains('.') {
            s.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            s
        }
    } else {
        let m = if mantissa.contains('.') {
            mantissa.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            mantissa.to_string()
        };
        format!("{m}e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn fixture() -> Value {
        serde_json::from_str(include_str!("../../tests/fixtures/inventory_calc.json")).expect("fixture parses")
    }

    #[test]
    fn g_format_examples() {
        assert_eq!(fmt_g(5.0), "5");
        assert_eq!(fmt_g(0.5), "0.5");
        assert_eq!(fmt_g(1234567.0), "1.23457e+06");
        assert_eq!(fmt_g(100000.0), "100000");
        assert_eq!(fmt_g(0.0001), "0.0001");
        assert_eq!(fmt_g(0.00001), "1e-05");
        assert_eq!(fmt_g(12.345678), "12.3457");
    }

    #[test]
    fn iso_examples() {
        let d = fromisoformat("2026-10-01").unwrap();
        assert_eq!(d.assume_utc().isoformat(), "2026-10-01T00:00:00+00:00");
        let d = fromisoformat("2026-10-01T10:30:15.5-05:00").unwrap();
        assert_eq!(d.isoformat(), "2026-10-01T10:30:15.500000-05:00");
        assert!(fromisoformat("2026-13-01").is_none());
        assert!(fromisoformat("nope").is_none());
    }

    #[test]
    fn float_from_str_matches_python() {
        assert_eq!(py_float_from_str(" 1_0.5 "), Some(10.5));
        assert_eq!(py_float_from_str("1__0"), None);
        assert_eq!(py_float_from_str("_1"), None);
        assert_eq!(py_float_from_str("1_"), None);
        assert_eq!(py_float_from_str("abc"), None);
        assert_eq!(py_float_from_str("1e3"), Some(1000.0));
        assert_eq!(py_float_from_str("-Infinity"), Some(f64::NEG_INFINITY));
        assert!(py_float_from_str("nan").unwrap().is_nan());
        assert_eq!(py_float_from_str(""), None);
        assert_eq!(py_float_from_str("0x10"), None);
    }

    #[test]
    fn differential_fmt_g() {
        for c in fixture()["fmt_g"].as_array().unwrap() {
            let x = f64::from_bits(c["x"].as_str().unwrap().parse::<u64>().unwrap());
            assert_eq!(fmt_g(x), c["out"].as_str().unwrap(), "{c}");
        }
    }

    #[test]
    fn differential_fromisoformat() {
        for c in fixture()["iso_datetime"].as_array().unwrap() {
            let s = c["s"].as_str().unwrap();
            match (fromisoformat(s), c["v"].as_array()) {
                (None, None) => {}
                (Some(d), Some(v)) => {
                    let want: Vec<i64> = v[..7].iter().map(|x| x.as_i64().unwrap()).collect();
                    let got = [i64::from(d.year), i64::from(d.month), i64::from(d.day), i64::from(d.hour),
                        i64::from(d.minute), i64::from(d.second), i64::from(d.micro)];
                    assert_eq!(got.to_vec(), want, "fields of {s:?}");
                    assert_eq!(d.offset_us, v[7].as_i64(), "offset of {s:?}");
                    assert_eq!(d.assume_utc().isoformat(), c["iso"].as_str().unwrap(), "isoformat of {s:?}");
                }
                (g, w) => panic!("{s:?}: Rust {g:?} vs Python {w:?}"),
            }
        }
    }
}
