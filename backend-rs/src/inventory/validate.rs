//! Field shapes the inventory models use that `validation.rs` does not cover:
//! `float` with an inclusive lower bound (`ge`), `int` with bounds and
//! `Literal[...]` strings. Errors carry pydantic's types, messages and `ctx`.

use serde_json::{json, Map, Value};

use crate::query::{parse_py_int, PyInt};
use crate::validation::{float_field, loc, Bound, Errors, Field};

fn bound_json(b: Bound) -> Value {
    match b {
        Bound::Int(i) => json!(i),
        Bound::Float(f) => json!(f),
    }
}

fn bound_text(b: Bound) -> String {
    match b {
        Bound::Int(i) => i.to_string(),
        Bound::Float(f) => format!("{f}"),
    }
}

fn bound_value(b: Bound) -> f64 {
    match b {
        Bound::Int(i) => i as f64,
        Bound::Float(f) => f,
    }
}

/// A `float` field with `ge` / `le` (inclusive both ends), lax like pydantic.
#[allow(clippy::too_many_arguments)]
pub fn float_ge(
    errs: &mut Errors,
    obj: &Map<String, Value>,
    prefix: &[Value],
    name: &str,
    required: bool,
    nullable: bool,
    ge: Option<Bound>,
    le: Option<Bound>,
) -> Field<f64> {
    let parsed = float_field(errs, obj, prefix, name, required, nullable, None, None);
    let Field::Value(f) = parsed else { return parsed };
    let at = loc(prefix, name);
    let raw = obj.get(name).cloned().unwrap_or(Value::Null);
    if let Some(g) = ge {
        if !(f >= bound_value(g)) {
            errs.push("greater_than_equal", &at, format!("Input should be greater than or equal to {}", bound_text(g)),
                &raw, Some(json!({"ge": bound_json(g)})));
            return Field::Absent;
        }
    }
    if let Some(l) = le {
        if !(f <= bound_value(l)) {
            errs.push("less_than_equal", &at, format!("Input should be less than or equal to {}", bound_text(l)),
                &raw, Some(json!({"le": bound_json(l)})));
            return Field::Absent;
        }
    }
    Field::Value(f)
}

/// An `int` field with `ge` / `le`, lax like pydantic (`15.0` and `"15"` are 15,
/// `true` is 1, `15.5` is refused).
#[allow(clippy::too_many_arguments)]
pub fn int_field(
    errs: &mut Errors,
    obj: &Map<String, Value>,
    prefix: &[Value],
    name: &str,
    required: bool,
    nullable: bool,
    ge: Option<i64>,
    le: Option<i64>,
) -> Field<i64> {
    let at = loc(prefix, name);
    let raw = match obj.get(name) {
        None => {
            if required {
                errs.push("missing", &at, "Field required".into(), &Value::Object(obj.clone()), None);
            }
            return Field::Absent;
        }
        Some(Value::Null) if nullable => return Field::Null,
        Some(v) => v,
    };
    // Some(Ok(i)) a value, Some(Err(sign)) a value beyond i64 (true = positive).
    let value: Result<i64, bool> = match raw {
        Value::Bool(b) => Ok(i64::from(*b)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i)
            } else if n.is_u64() {
                Err(true)
            } else {
                let f = n.as_f64().unwrap_or(0.0);
                if f.fract() != 0.0 {
                    errs.push("int_from_float", &at,
                        "Input should be a valid integer, got a number with a fractional part".into(), raw, None);
                    return Field::Absent;
                }
                if f.abs() < 9.0e18 { Ok(f as i64) } else { Err(f > 0.0) }
            }
        }
        Value::String(s) => match parse_py_int(s) {
            Some(PyInt::Small(i)) => Ok(i),
            Some(PyInt::Big(pos)) => Err(pos),
            None => {
                errs.push("int_parsing", &at,
                    "Input should be a valid integer, unable to parse string as an integer".into(), raw, None);
                return Field::Absent;
            }
        },
        _ => {
            errs.push("int_type", &at, "Input should be a valid integer".into(), raw, None);
            return Field::Absent;
        }
    };
    let (below, above) = match value {
        Ok(i) => (ge.is_some_and(|g| i < g), le.is_some_and(|l| i > l)),
        Err(pos) => (!pos && ge.is_some(), pos && le.is_some()),
    };
    if below {
        let g = ge.unwrap_or(0);
        errs.push("greater_than_equal", &at, format!("Input should be greater than or equal to {g}"), raw,
            Some(json!({"ge": g})));
        return Field::Absent;
    }
    if above {
        let l = le.unwrap_or(0);
        errs.push("less_than_equal", &at, format!("Input should be less than or equal to {l}"), raw,
            Some(json!({"le": l})));
        return Field::Absent;
    }
    match value {
        Ok(i) => Field::Value(i),
        // Beyond i64 with no bound to refuse it: not reachable by the models here.
        Err(_) => Field::Absent,
    }
}

/// pydantic's rendering of the allowed literals: `'a'`, `'a' or 'b'`,
/// `'a', 'b' or 'c'`.
pub fn literal_expected(options: &[&str]) -> String {
    let quoted: Vec<String> = options.iter().map(|o| format!("'{o}'")).collect();
    match quoted.len() {
        0 => String::new(),
        1 => quoted[0].clone(),
        n => format!("{} or {}", quoted[..n - 1].join(", "), quoted[n - 1]),
    }
}

/// A `Literal[...]` JSON field. Anything not exactly one of the options (a
/// number, `"SET"`) is `literal_error`.
pub fn literal_field(
    errs: &mut Errors,
    obj: &Map<String, Value>,
    prefix: &[Value],
    name: &str,
    options: &[&str],
    nullable: bool,
) -> Field<String> {
    let at = loc(prefix, name);
    match obj.get(name) {
        None => Field::Absent,
        Some(Value::Null) if nullable => Field::Null,
        Some(Value::String(s)) if options.contains(&s.as_str()) => Field::Value(s.clone()),
        Some(other) => {
            let expected = literal_expected(options);
            errs.push("literal_error", &at, format!("Input should be {expected}"), other,
                Some(json!({"expected": expected})));
            Field::Absent
        }
    }
}

/// A `Literal[...]` query parameter (`loc` is `["query", name]`).
pub fn literal_query(errs: &mut Errors, raw: Option<&str>, name: &str, options: &[&str]) -> Option<String> {
    let raw = raw?;
    if options.contains(&raw) {
        return Some(raw.to_string());
    }
    let expected = literal_expected(options);
    errs.push("literal_error", &[json!("query"), json!(name)], format!("Input should be {expected}"),
        &json!(raw), Some(json!({"expected": expected})));
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn int_is_lax_like_pydantic() {
        let o = obj(json!({"a": 15, "b": 15.0, "c": "15", "d": "15.0", "e": true, "f": 15.5, "g": "abc", "h": [],
            "i": 0, "j": 366, "k": 1e30, "l": " 15 "}));
        let mut e = Errors::default();
        let get = |e: &mut Errors, n: &str| int_field(e, &o, &[], n, false, true, Some(1), Some(365));
        for n in ["a", "b", "c", "d", "e", "l"] {
            assert!(matches!(get(&mut e, n), Field::Value(_)), "{n}");
        }
        assert!(e.0.is_empty());
        for n in ["f", "g", "h", "i", "j", "k"] {
            assert_eq!(get(&mut e, n), Field::Absent);
        }
        let types: Vec<&str> = e.0.iter().map(|x| x["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["int_from_float", "int_parsing", "int_type", "greater_than_equal",
            "less_than_equal", "less_than_equal"]);
    }

    #[test]
    fn literals_render_like_pydantic() {
        assert_eq!(literal_expected(&["add", "set"]), "'add' or 'set'");
        assert_eq!(literal_expected(&["open", "closed", "applied", "cancelled"]),
            "'open', 'closed', 'applied' or 'cancelled'");
    }

    #[test]
    fn float_ge_is_inclusive() {
        let o = obj(json!({"a": 0, "b": -0.5, "c": 1e9, "d": 2e9}));
        let mut e = Errors::default();
        let g = |e: &mut Errors, n: &str| float_ge(e, &o, &[], n, true, false, Some(Bound::Int(0)), Some(Bound::Int(1_000_000_000)));
        assert_eq!(g(&mut e, "a"), Field::Value(0.0));
        assert_eq!(g(&mut e, "c"), Field::Value(1e9));
        assert_eq!(g(&mut e, "b"), Field::Absent);
        assert_eq!(g(&mut e, "d"), Field::Absent);
        assert_eq!(e.0[0]["msg"], "Input should be greater than or equal to 0");
        assert_eq!(e.0[1]["msg"], "Input should be less than or equal to 1000000000");
    }
}
