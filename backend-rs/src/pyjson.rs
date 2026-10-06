//! Python's own text renderings of JSON-shaped values, where they reach the
//! wire or the database byte for byte:
//!
//! * `json.dumps(value)` with the default arguments (`ensure_ascii=True`,
//!   separators `", "` / `": "`), used by the audit CSV export's
//!   `before` / `after` columns and by the audit middleware's 4000-byte clamp;
//! * `repr(float)`, which `json.dumps` and `str()` both use for floats;
//! * `str(value)` / `repr(value)` of the scalars and containers psycopg2 hands
//!   back from a JSONB column, for the CSV cells `csv.writer` stringifies.
//!
//! JSONB never holds NaN or infinities, so those are not rendered.

use serde_json::{Number, Value};

/// `repr(float)`: shortest round-trip digits, fixed notation for exponents in
/// [-4, 16), scientific (`1e+16`, `1.5e-05`) outside it, and always a `.0` on
/// an integral fixed value.
pub fn float_repr(f: f64) -> String {
    if f == 0.0 {
        return if f.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    if !f.is_finite() {
        return if f.is_nan() { "nan".into() } else if f > 0.0 { "inf".into() } else { "-inf".into() };
    }
    // Rust's LowerExp without a precision prints the shortest digits that
    // round-trip, the same digits Python's repr chooses.
    let sci = format!("{:e}", f.abs());
    let (mant, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    let sign = if f < 0.0 { "-" } else { "" };
    if (-4..16).contains(&exp) {
        let pt = exp + 1;
        let body = if pt <= 0 {
            format!("0.{}{}", "0".repeat((-pt) as usize), digits)
        } else if pt as usize >= digits.len() {
            format!("{}{}.0", digits, "0".repeat(pt as usize - digits.len()))
        } else {
            format!("{}.{}", &digits[..pt as usize], &digits[pt as usize..])
        };
        format!("{sign}{body}")
    } else {
        let (first, rest) = digits.split_at(1);
        let frac = if rest.is_empty() { String::new() } else { format!(".{rest}") };
        let esign = if exp < 0 { "-" } else { "+" };
        format!("{sign}{first}{frac}e{esign}{:02}", exp.abs())
    }
}

fn number(n: &Number) -> String {
    if let Some(i) = n.as_i64() {
        i.to_string()
    } else if let Some(u) = n.as_u64() {
        u.to_string()
    } else {
        float_repr(n.as_f64().unwrap_or(0.0))
    }
}

/// `json.dumps(s)` of one string, `ensure_ascii=True`.
fn dumps_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{:04x}", unit));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn dumps_into(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => out.push_str(&number(n)),
        Value::String(s) => dumps_str(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                dumps_into(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (i, (k, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                dumps_str(k, out);
                out.push_str(": ");
                dumps_into(item, out);
            }
            out.push('}');
        }
    }
}

/// `json.dumps(value)` with Python's defaults.
pub fn dumps(v: &Value) -> String {
    let mut out = String::new();
    dumps_into(v, &mut out);
    out
}

/// `repr(s)` of a str: single quotes unless the text contains a single quote
/// and no double quote.
pub fn repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `repr(value)` of what psycopg2 builds from JSON (dict, list, str, int,
/// float, bool, None).
pub fn repr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => number(n),
        Value::String(s) => repr_str(s),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map.iter().map(|(k, x)| format!("{}: {}", repr_str(k), repr(x))).collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// `str(value)`: the text itself for a str, `repr` for everything else.
pub fn str_of(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => repr(other),
    }
}

/// `repr()` of a Python list of strings, e.g. `['a', "b'c"]` (the webhook
/// "Unsupported events" message, once `POST /webhooks` moves).
#[allow(dead_code)]
pub fn repr_str_list<S: AsRef<str>>(items: &[S]) -> String {
    let inner: Vec<String> = items.iter().map(|s| repr_str(s.as_ref())).collect();
    format!("[{}]", inner.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn float_repr_matches_python() {
        // python -c "print([repr(x) for x in (5.0, 0.1, 1e16, 1.5e-5, 123456.789, 1e-4, 1e15, -2.5, 1e22)])"
        let cases = [
            (5.0, "5.0"), (0.1, "0.1"), (1e16, "1e+16"), (1.5e-5, "1.5e-05"),
            (123456.789, "123456.789"), (1e-4, "0.0001"), (1e15, "1000000000000000.0"),
            (-2.5, "-2.5"), (1e22, "1e+22"), (0.0, "0.0"),
        ];
        for (f, want) in cases {
            assert_eq!(float_repr(f), want, "{f}");
        }
    }

    #[test]
    fn dumps_matches_python_defaults() {
        // python -c "import json;print(json.dumps({'a': [1, 2.0, None, True], 'b': 'ñ\"\n', 'c': {}}))"
        let v = json!({"a": [1, 2.0, null, true], "b": "ñ\"\n", "c": {}});
        let want = concat!(r#"{"a": [1, 2.0, null, true], "b": ""#, "\\", "u00f1", r#"\"\n", "c": {}}"#);
        assert_eq!(dumps(&v), want);
        let want = format!("\"{0}ud83d{0}ude00\"", "\\");
        assert_eq!(dumps(&json!("\u{1F600}")), want);
    }

    #[test]
    fn repr_picks_python_quotes() {
        assert_eq!(repr_str("a"), "'a'");
        assert_eq!(repr_str("b'c"), "\"b'c\"");
        assert_eq!(repr_str("b'c\""), "'b\\'c\"'");
        assert_eq!(repr_str_list(&["a", "b'c"]), "['a', \"b'c\"]");
        assert_eq!(repr(&json!({"k": [1, null, false, 1.5]})), "{'k': [1, None, False, 1.5]}");
    }
}
