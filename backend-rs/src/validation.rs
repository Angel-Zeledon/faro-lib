//! Request-body validation that fails the way FastAPI + pydantic v2 fail.
//!
//! A 422 from the Python app is `{"detail": [<pydantic error>, ...],
//! "error_code": "validation_error"}`, and each error carries `type`, `loc`,
//! `msg`, `input` and sometimes `ctx`. The frontend only reads
//! `error_code`, but API clients may read `detail[*].loc`/`type`, so the
//! errors are rebuilt with pydantic's own types, messages and lax coercions
//! (a numeric string is a float, `"no"` is a boolean, a number is NOT a
//! string) for the field shapes the migrated models use.
//!
//! The JSON decoding step mirrors FastAPI's `get_request_handler`: it happens
//! BEFORE authentication, so malformed JSON is a 422 even without a token.

use serde_json::{json, Map, Value};

use crate::error::ApiError;

/// What FastAPI hands to the model: `None` (no body), parsed JSON, or the
/// raw bytes when the content type is not JSON. The bytes are kept as their
/// Python `repr` (`b'...'`), which is what the 422's `input` shows.
pub enum Body {
    Missing,
    Json(Value),
    NotJson(String),
}

/// `repr(bytes)`: single quotes unless the bytes hold `'` and no `"`; tab,
/// newline, carriage return, the quote and the backslash escaped; any other
/// byte outside printable ASCII as `\xNN`.
pub fn py_bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') { b'"' } else { b'\'' };
    let mut out = String::from("b");
    out.push(quote as char);
    for &b in bytes {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            _ if b == quote => {
                out.push('\\');
                out.push(b as char);
            }
            0x20..=0x7e => out.push(b as char),
            _ => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote as char);
    out
}

/// FastAPI's body read: JSON when the content type is absent or
/// `application/json` / `application/*+json`, raw otherwise. A JSON syntax
/// error is the `json_invalid` 422.
pub fn read_body(content_type: Option<&str>, bytes: &[u8]) -> Result<Body, ApiError> {
    if bytes.is_empty() {
        return Ok(Body::Missing);
    }
    let is_json = match content_type {
        None => true,
        Some(ct) => {
            let main = ct.split(';').next().unwrap_or("").trim().to_ascii_lowercase();
            match main.split_once('/') {
                Some(("application", sub)) => sub == "json" || sub.ends_with("+json"),
                _ => false,
            }
        }
    };
    if !is_json {
        return Ok(Body::NotJson(py_bytes_repr(bytes)));
    }
    match serde_json::from_slice::<Value>(bytes) {
        Ok(v) => Ok(Body::Json(v)),
        Err(e) => {
            // Python reports the character offset (`e.pos`) and json's own
            // message; serde gives line/column and its own wording. The type,
            // the message and the `body` loc prefix match; the offset and
            // ctx text are approximations (see docs/rust-migration.md).
            let pos = offset_of(bytes, e.line(), e.column());
            Err(ApiError::validation(vec![json!({
                "type": "json_invalid",
                "loc": ["body", pos],
                "msg": "JSON decode error",
                "input": {},
                "ctx": {"error": e.to_string()},
            })]))
        }
    }
}

fn offset_of(bytes: &[u8], line: usize, column: usize) -> usize {
    let text = String::from_utf8_lossy(bytes);
    let mut offset = 0usize;
    for (i, l) in text.split('\n').enumerate() {
        if i + 1 == line {
            return offset + column.saturating_sub(1);
        }
        offset += l.chars().count() + 1;
    }
    offset
}

/// Collects pydantic errors for one request.
#[derive(Default)]
pub struct Errors(pub Vec<Value>);

impl Errors {
    pub fn push(&mut self, typ: &str, loc: &[Value], msg: String, input: &Value, ctx: Option<Value>) {
        let mut e = Map::new();
        e.insert("type".into(), Value::String(typ.into()));
        e.insert("loc".into(), Value::Array(loc.to_vec()));
        e.insert("msg".into(), Value::String(msg));
        e.insert("input".into(), input.clone());
        if let Some(c) = ctx {
            e.insert("ctx".into(), c);
        }
        self.0.push(Value::Object(e));
    }

    pub fn into_result(self) -> Result<(), ApiError> {
        if self.0.is_empty() {
            Ok(())
        } else {
            Err(ApiError::validation(self.0))
        }
    }
}

pub fn loc(prefix: &[Value], name: &str) -> Vec<Value> {
    let mut l = prefix.to_vec();
    l.push(Value::String(name.into()));
    l
}

/// A field as the model sees it: absent, explicitly null, or a value.
#[derive(Debug, Clone, PartialEq)]
pub enum Field<T> {
    Absent,
    Null,
    Value(T),
}

/// The object the model is built from, or the `model_attributes_type` error.
pub fn as_object<'a>(errs: &mut Errors, at: &[Value], v: &'a Value) -> Option<&'a Map<String, Value>> {
    match v {
        Value::Object(m) => Some(m),
        other => {
            errs.push(
                "model_attributes_type",
                at,
                "Input should be a valid dictionary or object to extract fields from".into(),
                other,
                None,
            );
            None
        }
    }
}

/// The whole-body check FastAPI makes before the model sees anything.
pub fn body_object(body: &Body) -> Result<Map<String, Value>, ApiError> {
    let mut errs = Errors::default();
    let at = [Value::String("body".into())];
    match body {
        Body::Missing | Body::Json(Value::Null) => {
            errs.push("missing", &at, "Field required".into(), &Value::Null, None);
        }
        Body::NotJson(text) => {
            errs.push(
                "model_attributes_type",
                &at,
                "Input should be a valid dictionary or object to extract fields from".into(),
                &Value::String(text.clone()),
                None,
            );
        }
        Body::Json(v) => {
            if let Some(m) = as_object(&mut errs, &at, v) {
                return Ok(m.clone());
            }
        }
    }
    Err(ApiError::validation(errs.0))
}

pub struct StrRules {
    pub min_length: Option<usize>,
    pub max_length: Option<usize>,
    pub pattern: Option<(&'static str, fn(&str) -> bool)>,
}

pub const NO_STR_RULES: StrRules = StrRules { min_length: None, max_length: None, pattern: None };

fn plural(n: usize) -> &'static str {
    if n == 1 { "" } else { "s" }
}

/// A `str` field. `nullable` is `Optional[str]`; `required` means no default.
pub fn str_field(
    errs: &mut Errors,
    obj: &Map<String, Value>,
    prefix: &[Value],
    name: &str,
    required: bool,
    nullable: bool,
    rules: &StrRules,
) -> Field<String> {
    let at = loc(prefix, name);
    match obj.get(name) {
        None => {
            if required {
                errs.push("missing", &at, "Field required".into(), &Value::Object(obj.clone()), None);
            }
            Field::Absent
        }
        Some(Value::Null) if nullable => Field::Null,
        Some(Value::String(s)) => {
            let n = s.chars().count();
            let mut ok = true;
            if let Some(min) = rules.min_length {
                if n < min {
                    errs.push("string_too_short", &at,
                        format!("String should have at least {min} character{}", plural(min)),
                        &Value::String(s.clone()), Some(json!({"min_length": min})));
                    ok = false;
                }
            }
            if let Some(max) = rules.max_length {
                if n > max {
                    errs.push("string_too_long", &at,
                        format!("String should have at most {max} character{}", plural(max)),
                        &Value::String(s.clone()), Some(json!({"max_length": max})));
                    ok = false;
                }
            }
            if ok {
                if let Some((pat, matches)) = rules.pattern {
                    if !matches(s) {
                        errs.push("string_pattern_mismatch", &at,
                            format!("String should match pattern '{pat}'"),
                            &Value::String(s.clone()), Some(json!({"pattern": pat})));
                        ok = false;
                    }
                }
            }
            if ok { Field::Value(s.clone()) } else { Field::Absent }
        }
        Some(other) => {
            errs.push("string_type", &at, "Input should be a valid string".into(), other, None);
            Field::Absent
        }
    }
}

/// A numeric bound as pydantic prints and reports it: the Python literal
/// used in `Field(gt=0)` is an int, `le=1e9` a float.
#[derive(Clone, Copy)]
pub enum Bound {
    Int(i64),
    Float(f64),
}

impl Bound {
    fn value(self) -> f64 {
        match self {
            Bound::Int(i) => i as f64,
            Bound::Float(f) => f,
        }
    }
    fn json(self) -> Value {
        match self {
            Bound::Int(i) => json!(i),
            Bound::Float(f) => json!(f),
        }
    }
    fn text(self) -> String {
        match self {
            Bound::Int(i) => i.to_string(),
            // pydantic-core formats the bound with Rust's own float Display
            // (1e9 -> "1000000000"), not Python's str() ("1000000000.0");
            // measured against the live API by tests/contract.
            Bound::Float(f) => format!("{f}"),
        }
    }
}

/// A `float` field with `gt` / `le` constraints, lax like pydantic: numbers,
/// booleans and numeric strings are accepted.
#[allow(clippy::too_many_arguments)]
pub fn float_field(
    errs: &mut Errors,
    obj: &Map<String, Value>,
    prefix: &[Value],
    name: &str,
    required: bool,
    nullable: bool,
    gt: Option<Bound>,
    le: Option<Bound>,
) -> Field<f64> {
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
    let parsed = match raw {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::String(s) => match s.trim().parse::<f64>() {
            Ok(f) => Some(f),
            Err(_) => {
                errs.push("float_parsing", &at,
                    "Input should be a valid number, unable to parse string as a number".into(), raw, None);
                return Field::Absent;
            }
        },
        _ => None,
    };
    let Some(f) = parsed else {
        errs.push("float_type", &at, "Input should be a valid number".into(), raw, None);
        return Field::Absent;
    };
    if let Some(g) = gt {
        if !(f > g.value()) {
            errs.push("greater_than", &at, format!("Input should be greater than {}", g.text()), raw,
                Some(json!({"gt": g.json()})));
            return Field::Absent;
        }
    }
    if let Some(l) = le {
        if !(f <= l.value()) {
            errs.push("less_than_equal", &at, format!("Input should be less than or equal to {}", l.text()),
                raw, Some(json!({"le": l.json()})));
            return Field::Absent;
        }
    }
    Field::Value(f)
}

/// A `bool` field, lax like pydantic.
pub fn bool_field(
    errs: &mut Errors,
    obj: &Map<String, Value>,
    prefix: &[Value],
    name: &str,
    nullable: bool,
) -> Field<bool> {
    let at = loc(prefix, name);
    let raw = match obj.get(name) {
        None => return Field::Absent,
        Some(Value::Null) if nullable => return Field::Null,
        Some(v) => v,
    };
    let unable = |errs: &mut Errors| {
        errs.push("bool_parsing", &at, "Input should be a valid boolean, unable to interpret input".into(),
            raw, None);
    };
    match raw {
        Value::Bool(b) => Field::Value(*b),
        Value::Number(n) => match n.as_f64() {
            Some(x) if x == 0.0 => Field::Value(false),
            Some(x) if x == 1.0 => Field::Value(true),
            _ => {
                unable(errs);
                Field::Absent
            }
        },
        Value::String(s) => match s.to_ascii_lowercase().as_str() {
            "0" | "off" | "f" | "false" | "n" | "no" => Field::Value(false),
            "1" | "on" | "t" | "true" | "y" | "yes" => Field::Value(true),
            _ => {
                unable(errs);
                Field::Absent
            }
        },
        other => {
            errs.push("bool_type", &at, "Input should be a valid boolean".into(), other, None);
            Field::Absent
        }
    }
}

/// A `list[...]` with `min_length` / `max_length`.
pub fn list_field<'a>(
    errs: &mut Errors,
    obj: &'a Map<String, Value>,
    prefix: &[Value],
    name: &str,
    min_length: usize,
    max_length: usize,
) -> Option<&'a Vec<Value>> {
    let at = loc(prefix, name);
    match obj.get(name) {
        None => {
            errs.push("missing", &at, "Field required".into(), &Value::Object(obj.clone()), None);
            None
        }
        Some(Value::Array(items)) => {
            let n = items.len();
            if n < min_length {
                errs.push("too_short", &at,
                    format!("List should have at least {min_length} item{} after validation, not {n}", plural(min_length)),
                    &Value::Array(items.clone()),
                    Some(json!({"field_type": "List", "min_length": min_length, "actual_length": n})));
                return None;
            }
            if n > max_length {
                errs.push("too_long", &at,
                    format!("List should have at most {max_length} item{} after validation, not {n}", plural(max_length)),
                    &Value::Array(items.clone()),
                    Some(json!({"field_type": "List", "max_length": max_length, "actual_length": n})));
                return None;
            }
            Some(items)
        }
        Some(other) => {
            errs.push("list_type", &at, "Input should be a valid list".into(), other, None);
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn invalid_json_is_rejected_before_anything_else() {
        let e = read_body(Some("application/json"), b"{nope").err().unwrap();
        assert_eq!(e.status.as_u16(), 422);
        assert_eq!(e.body["detail"][0]["type"], "json_invalid");
        assert_eq!(e.body["error_code"], "validation_error");
    }

    #[test]
    fn non_json_content_type_is_raw() {
        assert!(matches!(read_body(Some("text/plain"), b"{}").unwrap(), Body::NotJson(_)));
        assert_eq!(py_bytes_repr(b"language=en"), "b'language=en'");
        assert_eq!(py_bytes_repr(b"it's"), r#"b"it's""#);
        assert_eq!(py_bytes_repr(b"'\"\\\t\x00\xc3\xa9"), r#"b'\'"\\\t\x00\xc3\xa9'"#);
        assert!(matches!(read_body(Some("application/vnd.x+json"), b"{}").unwrap(), Body::Json(_)));
        assert!(matches!(read_body(None, b"").unwrap(), Body::Missing));
    }

    #[test]
    fn floats_are_lax() {
        let mut e = Errors::default();
        let o = obj(json!({"a": "7.5", "b": true, "c": "x", "d": [1], "e": 0}));
        assert_eq!(float_field(&mut e, &o, &[], "a", true, false, None, None), Field::Value(7.5));
        assert_eq!(float_field(&mut e, &o, &[], "b", true, false, None, None), Field::Value(1.0));
        float_field(&mut e, &o, &[], "c", true, false, None, None);
        float_field(&mut e, &o, &[], "d", true, false, None, None);
        float_field(&mut e, &o, &[], "e", true, false, Some(Bound::Int(0)), None);
        let types: Vec<&str> = e.0.iter().map(|x| x["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["float_parsing", "float_type", "greater_than"]);
        assert_eq!(e.0[2]["msg"], "Input should be greater than 0");
    }

    #[test]
    fn float_bound_message_keeps_python_float_text() {
        let mut e = Errors::default();
        let o = obj(json!({"q": 2e9}));
        float_field(&mut e, &o, &[], "q", true, false, None, Some(Bound::Float(1e9)));
        assert_eq!(e.0[0]["msg"], "Input should be less than or equal to 1000000000");
        assert_eq!(e.0[0]["ctx"], json!({"le": 1e9}));
    }

    #[test]
    fn strings_are_not_coerced_from_numbers() {
        let mut e = Errors::default();
        let o = obj(json!({"s": 12, "t": ""}));
        str_field(&mut e, &o, &[], "s", true, false, &NO_STR_RULES);
        str_field(&mut e, &o, &[], "t", true, false,
            &StrRules { min_length: Some(1), max_length: None, pattern: None });
        assert_eq!(e.0[0]["type"], "string_type");
        assert_eq!(e.0[1]["msg"], "String should have at least 1 character");
    }

    #[test]
    fn booleans_are_lax() {
        let mut e = Errors::default();
        let o = obj(json!({"a": "no", "b": 1, "c": "maybe", "d": [true]}));
        assert_eq!(bool_field(&mut e, &o, &[], "a", false), Field::Value(false));
        assert_eq!(bool_field(&mut e, &o, &[], "b", false), Field::Value(true));
        bool_field(&mut e, &o, &[], "c", false);
        bool_field(&mut e, &o, &[], "d", false);
        let types: Vec<&str> = e.0.iter().map(|x| x["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["bool_parsing", "bool_type"]);
    }

    #[test]
    fn missing_body_and_wrong_shape() {
        let e = body_object(&Body::Missing).unwrap_err();
        assert_eq!(e.body["detail"][0]["type"], "missing");
        let e = body_object(&Body::Json(json!([1]))).unwrap_err();
        assert_eq!(e.body["detail"][0]["type"], "model_attributes_type");
    }
}
