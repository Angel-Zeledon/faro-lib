//! Query-string parameters the way FastAPI + pydantic v2 read them.
//!
//! * Parsing is Starlette's: `urllib.parse.parse_qsl(qs, keep_blank_values=True)`
//!   (split on `&` only, `+` is a space, percent-decoding as UTF-8 with
//!   replacement), and a repeated key keeps its LAST value, because
//!   `ImmutableMultiDict` builds its dict from the pair list.
//! * A scalar parameter is validated in declaration order AFTER the auth
//!   dependency, and every failing parameter adds one pydantic error with
//!   `loc: ["query", <name>]` and the raw string as `input`.
//!
//! Only the shapes the migrated routes declare are here: `int` with `ge` /
//! `le`, and `Optional[str]` with an optional anchored `pattern`.

use std::collections::HashMap;

use percent_encoding::percent_decode;
use serde_json::{json, Value};

use crate::validation::Errors;

/// The query string as FastAPI sees it: name -> last value.
pub struct QueryParams(HashMap<String, String>);

fn unquote_plus(s: &str) -> String {
    let spaced = s.replace('+', " ");
    String::from_utf8_lossy(&percent_decode(spaced.as_bytes()).collect::<Vec<u8>>()).into_owned()
}

impl QueryParams {
    pub fn parse(raw: Option<&str>) -> Self {
        let mut map = HashMap::new();
        for part in raw.unwrap_or("").split('&') {
            if part.is_empty() {
                continue;
            }
            let (k, v) = part.split_once('=').unwrap_or((part, ""));
            map.insert(unquote_plus(k), unquote_plus(v));
        }
        QueryParams(map)
    }

    pub fn get(&self, name: &str) -> Option<&str> {
        self.0.get(name).map(String::as_str)
    }

    /// `name: int = Query(default, ge=.., le=..)`. `None` when it failed (the
    /// error is pushed); the default when absent.
    pub fn int(&self, errs: &mut Errors, name: &str, default: i64, ge: Option<i64>, le: Option<i64>) -> Option<i128> {
        let Some(raw) = self.get(name) else { return Some(i128::from(default)) };
        let at = [Value::String("query".into()), Value::String(name.into())];
        let input = Value::String(raw.to_string());
        let Some(n) = parse_py_int(raw) else {
            errs.push("int_parsing", &at,
                "Input should be a valid integer, unable to parse string as an integer".into(), &input, None);
            return None;
        };
        if let Some(g) = ge {
            if n < i128::from(g) {
                errs.push("greater_than_equal", &at, format!("Input should be greater than or equal to {g}"),
                    &input, Some(json!({"ge": g})));
                return None;
            }
        }
        if let Some(l) = le {
            if n > i128::from(l) {
                errs.push("less_than_equal", &at, format!("Input should be less than or equal to {l}"),
                    &input, Some(json!({"le": l})));
                return None;
            }
        }
        // pydantic accepts arbitrarily large ints; Postgres then refuses a
        // LIMIT/OFFSET beyond bigint and Python answers 500. Callers convert
        // with [`sql_int`], which keeps that.
        Some(n)
    }

    /// `name: Optional[str] = Query(None, pattern=...)`.
    pub fn opt_str(&self, errs: &mut Errors, name: &str, pattern: Option<(&'static str, fn(&str) -> bool)>)
        -> Option<String>
    {
        let raw = self.get(name)?;
        if let Some((pat, matches)) = pattern {
            if !matches(raw) {
                let at = [Value::String("query".into()), Value::String(name.into())];
                errs.push("string_pattern_mismatch", &at, format!("String should match pattern '{pat}'"),
                    &Value::String(raw.to_string()), Some(json!({"pattern": pat})));
                return None;
            }
        }
        Some(raw.to_string())
    }
}

/// A validated query int as a SQL bigint; beyond bigint it is the 500 the
/// Python side gets from Postgres.
pub fn sql_int(v: Option<i128>) -> Result<i64, crate::error::ApiError> {
    v.and_then(|n| i64::try_from(n).ok()).ok_or_else(crate::error::ApiError::internal)
}

/// pydantic-core's str -> int in lax mode: surrounding whitespace stripped,
/// one optional sign, ASCII digits with single underscores BETWEEN digits,
/// and an optional fraction made only of zeros (`"5.00"` is 5, `"5."` and
/// `"5.5"` are not integers). Measured against pydantic 2.13.
pub fn parse_py_int(raw: &str) -> Option<i128> {
    let s = raw.trim();
    let (neg, body) = match s.as_bytes().first()? {
        b'+' => (false, &s[1..]),
        b'-' => (true, &s[1..]),
        _ => (false, s),
    };
    let (int_part, frac) = match body.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (body, None),
    };
    if let Some(f) = frac {
        if f.is_empty() || !f.bytes().all(|b| b == b'0') {
            return None;
        }
    }
    if int_part.is_empty() || int_part.starts_with('_') || int_part.ends_with('_') || int_part.contains("__") {
        return None;
    }
    let mut n: i128 = 0;
    for b in int_part.bytes() {
        match b {
            b'_' => continue,
            b'0'..=b'9' => n = n.checked_mul(10)?.checked_add(i128::from(b - b'0'))?,
            _ => return None,
        }
    }
    Some(if neg { -n } else { n })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ints_parse_like_pydantic() {
        for (s, want) in [("5", Some(5)), (" 5 ", Some(5)), ("5.0", Some(5)), ("5.00", Some(5)), ("1_0", Some(10)),
            ("+5", Some(5)), ("-0", Some(0)), ("05", Some(5)), ("\t7\n", Some(7)), ("-0.0", Some(0)),
            ("5.5", None), ("abc", None), ("", None), (" ", None), ("1e2", None), ("0x10", None), ("5.", None),
            (".5", None), ("1__0", None), ("_1", None), ("1_", None), ("+-5", None), ("\u{ff15}", None)]
        {
            assert_eq!(parse_py_int(s), want, "{s:?}");
        }
    }

    #[test]
    fn query_last_value_wins_and_blank_kept() {
        let q = QueryParams::parse(Some("limit=1&limit=7&action=&kind=a+b%20c&flag"));
        assert_eq!(q.get("limit"), Some("7"));
        assert_eq!(q.get("action"), Some(""));
        assert_eq!(q.get("kind"), Some("a b c"));
        assert_eq!(q.get("flag"), Some(""));
    }

    #[test]
    fn int_errors_carry_the_raw_string() {
        let q = QueryParams::parse(Some("limit=0&offset=x"));
        let mut e = Errors::default();
        assert_eq!(q.int(&mut e, "limit", 50, Some(1), Some(200)), None);
        assert_eq!(q.int(&mut e, "offset", 0, Some(0), None), None);
        assert_eq!(q.int(&mut e, "absent", 9, Some(0), None), Some(9));
        assert!(sql_int(Some(i128::from(i64::MAX) + 1)).is_err());
        assert_eq!(e.0[0]["type"], "greater_than_equal");
        assert_eq!(e.0[0]["input"], "0");
        assert_eq!(e.0[0]["ctx"], json!({"ge": 1}));
        assert_eq!(e.0[1]["type"], "int_parsing");
        assert_eq!(e.0[1]["loc"], json!(["query", "offset"]));
    }
}
