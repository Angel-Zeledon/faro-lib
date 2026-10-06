//! Small re-implementations of Python behaviour the wire format depends on.
//!
//! The compatibility contract is "the same bytes the Python app would have
//! sent", and a few of those bytes come from Python built-ins rather than from
//! StockAI code: `datetime.isoformat()`, `date.fromisoformat()`, `str.strip()`
//! and truthiness. Each helper names the built-in it mirrors.

use chrono::{DateTime, Datelike, NaiveDate, Timelike, Utc};
use serde_json::Value;

/// `datetime.isoformat()` of an aware UTC datetime: microseconds only when
/// non-zero, offset spelled `+00:00`.
///
/// The Python side renders timestamps in the session time zone of its
/// connection, which is UTC in every deployment of this product (the Postgres
/// images default to it, and the dev database reports `UTC`). A database set
/// to another zone would make Python print a different offset for the same
/// instant; see docs/rust-migration.md, "Known divergences".
pub fn isoformat_utc(dt: &DateTime<Utc>) -> String {
    let micros = dt.nanosecond() / 1_000;
    let base = dt.format("%Y-%m-%dT%H:%M:%S").to_string();
    if micros == 0 {
        format!("{base}+00:00")
    } else {
        format!("{base}.{micros:06}+00:00")
    }
}

/// `date.isoformat()`.
pub fn isoformat_date(d: &NaiveDate) -> String {
    format!("{:04}-{:02}-{:02}", d.year(), d.month(), d.day())
}

/// `datetime.now(timezone.utc).isoformat()`, the envelope's `meta.timestamp`.
pub fn now_isoformat() -> String {
    isoformat_utc(&Utc::now())
}

/// `datetime.timestamp()` of an aware datetime, as a float.
pub fn timestamp_f64(dt: &DateTime<Utc>) -> f64 {
    dt.timestamp() as f64 + f64::from(dt.timestamp_subsec_micros()) / 1_000_000.0
}

fn all_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

/// CPython 3.12 `date.fromisoformat()`: `YYYY-MM-DD`, `YYYYMMDD`, and the ISO
/// week forms `YYYY-Www`, `YYYYWww`, `YYYY-Www-D`, `YYYYWwwD`. Anything else is
/// `None`, which the callers turn into the same error code Python raises.
pub fn date_fromisoformat(s: &str) -> Option<NaiveDate> {
    if !s.is_ascii() || !matches!(s.len(), 7 | 8 | 10) {
        return None;
    }
    let b = s.as_bytes();
    let year_s = &s[0..4];
    if !all_digits(year_s) {
        return None;
    }
    let year: i32 = year_s.parse().ok()?;
    if year < 1 {
        return None;
    }
    let has_sep = b[4] == b'-';
    let mut pos = 4 + usize::from(has_sep);
    if s.get(pos..pos + 1) == Some("W") {
        pos += 1;
        let week_s = s.get(pos..pos + 2)?;
        if !all_digits(week_s) {
            return None;
        }
        let week: u32 = week_s.parse().ok()?;
        pos += 2;
        let mut day: u32 = 1;
        if s.len() > pos {
            if (s.get(pos..pos + 1) == Some("-")) != has_sep {
                return None;
            }
            pos += usize::from(has_sep);
            let day_s = s.get(pos..pos + 1)?;
            if !all_digits(day_s) || s.len() != pos + 1 {
                return None;
            }
            day = day_s.parse().ok()?;
        }
        if !(1..=7).contains(&day) {
            return None;
        }
        let weekday = match day {
            1 => chrono::Weekday::Mon,
            2 => chrono::Weekday::Tue,
            3 => chrono::Weekday::Wed,
            4 => chrono::Weekday::Thu,
            5 => chrono::Weekday::Fri,
            6 => chrono::Weekday::Sat,
            _ => chrono::Weekday::Sun,
        };
        NaiveDate::from_isoywd_opt(year, week, weekday)
    } else {
        let month_s = s.get(pos..pos + 2)?;
        pos += 2;
        if (s.get(pos..pos + 1) == Some("-")) != has_sep {
            return None;
        }
        pos += usize::from(has_sep);
        let day_s = s.get(pos..pos + 2)?;
        if !all_digits(month_s) || !all_digits(day_s) || s.len() != pos + 2 {
            return None;
        }
        NaiveDate::from_ymd_opt(year, month_s.parse().ok()?, day_s.parse().ok()?)
    }
}

/// `str[:n]`: the first `n` code points.
pub fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `str.isspace()` for one character. Rust's `char::is_whitespace` is the
/// Unicode White_Space property; Python also counts the four ASCII information
/// separators (U+001C..U+001F) as whitespace.
fn py_isspace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()` with no argument.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_isspace)
}

/// Python truthiness of a JSON value (`bool(value)`).
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `repr()` of a Python list of strings, e.g. `['admin', 'analyst']`, which is
/// what f-strings print for a list in the English fallback messages.
pub fn py_list_repr(items: &[&str]) -> String {
    let inner: Vec<String> = items.iter().map(|s| format!("'{s}'")).collect();
    format!("[{}]", inner.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn isoformat_omits_zero_microseconds() {
        let dt = Utc.with_ymd_and_hms(2026, 10, 5, 20, 41, 28).unwrap();
        assert_eq!(isoformat_utc(&dt), "2026-10-05T20:41:28+00:00");
        let dt2 = dt + chrono::Duration::microseconds(309_924);
        assert_eq!(isoformat_utc(&dt2), "2026-10-05T20:41:28.309924+00:00");
        let dt3 = dt + chrono::Duration::microseconds(5);
        assert_eq!(isoformat_utc(&dt3), "2026-10-05T20:41:28.000005+00:00");
    }

    #[test]
    fn fromisoformat_accepts_what_cpython_accepts() {
        let d = |s| date_fromisoformat(s).map(|d| isoformat_date(&d));
        assert_eq!(d("2026-12-01").as_deref(), Some("2026-12-01"));
        assert_eq!(d("20261201").as_deref(), Some("2026-12-01"));
        // ISO week dates: 2026-W01 starts on Monday 2025-12-29.
        assert_eq!(d("2026-W01").as_deref(), Some("2025-12-29"));
        assert_eq!(d("2026W01").as_deref(), Some("2025-12-29"));
        assert_eq!(d("2026-W01-3").as_deref(), Some("2025-12-31"));
        assert_eq!(d("2026W013").as_deref(), Some("2025-12-31"));
    }

    #[test]
    fn fromisoformat_rejects_what_cpython_rejects() {
        for bad in ["2026-13-01", "2026-02-30", "2026-1-01", "2026/12/01", "None", "",
                    "2026-12-0x", "0000-01-01", "2026-W54", "2026-W01-8", "2026-W011",
                    "202612-01", "２０２６-12-01"] {
            assert!(date_fromisoformat(bad).is_none(), "{bad} should be rejected");
        }
    }

    #[test]
    fn strip_matches_python() {
        assert_eq!(py_strip("  a b \t\n"), "a b");
        assert_eq!(py_strip("\u{1c}x\u{1f}"), "x");
        assert_eq!(py_strip("\u{3000}x\u{a0}"), "x");
    }

    #[test]
    fn truthiness_matches_python() {
        assert!(!truthy(&serde_json::json!(0)));
        assert!(!truthy(&serde_json::json!("")));
        assert!(!truthy(&serde_json::json!([])));
        assert!(truthy(&serde_json::json!("false")));
        assert!(truthy(&serde_json::json!(0.5)));
    }
}
