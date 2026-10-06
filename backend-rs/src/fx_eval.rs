//! `stockai-api fx-eval`: the conversion core as a stdin/stdout filter, so the
//! Python reference (`backend/fx/reference.py`) can be replayed against the
//! real Rust code over any number of seeded cases
//! (`tests/contract/fx_differential.py`), and so the committed golden vectors
//! (`tests/fx_vectors.json`) can be checked by `cargo test` without Python.
//!
//! One JSON object per input line, one JSON object per output line:
//! `{"ok": <value>}` or `{"error": true}`. Input numbers are a JSON string
//! (decimal text), a JSON integer, or `{"f": "<text>"}` (a float parsed from
//! text, so the parser is Rust's correctly rounded one, not serde's).

use chrono::NaiveDate;
use serde_json::{json, Value};

use crate::fx::{self, Dec, FxError, RateRow};

fn dec_of(v: &Value) -> Result<Dec, FxError> {
    match v {
        Value::String(s) => Dec::parse(s),
        Value::Number(n) => match n.as_i64() {
            Some(i) => Dec::from_i64(i),
            None => Err(FxError::NotANumber),
        },
        Value::Object(o) => match o.get("f").and_then(|x| x.as_str()) {
            Some(t) => match t.trim().parse::<f64>() {
                Ok(x) => Dec::from_f64(x),
                Err(_) => Err(FxError::NotANumber),
            },
            None => Err(FxError::NotANumber),
        },
        _ => Err(FxError::NotANumber),
    }
}

fn field<'a>(c: &'a Value, k: &str) -> &'a Value {
    c.get(k).unwrap_or(&Value::Null)
}

fn run(c: &Value) -> Result<Value, FxError> {
    let op = c.get("op").and_then(|x| x.as_str()).unwrap_or("");
    match op {
        "convert" => {
            let (a, r) = (dec_of(field(c, "amount"))?, dec_of(field(c, "rate"))?);
            Ok(json!(fx::convert(&a, &r).to_text()))
        }
        "convert_line" => {
            let (q, u, r) = (dec_of(field(c, "qty"))?, dec_of(field(c, "cost"))?, dec_of(field(c, "rate"))?);
            Ok(json!(fx::convert_line(&q, &u, &r).to_text()))
        }
        "base_line" => {
            let (q, u) = (dec_of(field(c, "qty"))?, dec_of(field(c, "cost"))?);
            Ok(json!(fx::base_line_value(&q, &u).to_text()))
        }
        "total_lines" => {
            let mut vals = Vec::new();
            for l in field(c, "lines").as_array().ok_or(FxError::NotANumber)? {
                let (q, u) = (dec_of(field(l, "qty"))?, dec_of(field(l, "cost"))?);
                vals.push(match field(l, "rate") {
                    Value::Null => fx::base_line_value(&q, &u),
                    r => fx::convert_line(&q, &u, &dec_of(r)?),
                });
            }
            Ok(json!(fx::total(&vals).to_text()))
        }
        "parse_rate" => {
            let text = match field(c, "value") {
                Value::String(s) => s.clone(),
                other => {
                    // Numbers and floats are canonicalised the way the route does.
                    let d = dec_of(other)?;
                    d.to_text_trimmed(0)
                }
            };
            Ok(json!(fx::parse_rate(&text)?.to_text_trimmed(0)))
        }
        "resolve" => {
            let mut rows = Vec::new();
            for r in field(c, "rates").as_array().ok_or(FxError::NotANumber)? {
                let date = |k: &str| {
                    r.get(k).and_then(|x| x.as_str()).and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
                };
                rows.push(RateRow {
                    id: r.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    currency: r.get("currency").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    base_currency: r.get("base_currency").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    effective_date: date("effective_date").ok_or(FxError::NotANumber)?,
                });
            }
            let as_of = field(c, "as_of")
                .as_str()
                .and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
                .ok_or(FxError::NotANumber)?;
            let cur = field(c, "currency").as_str().unwrap_or("");
            let base = field(c, "base").as_str().unwrap_or("");
            Ok(match fx::resolve(&rows, cur, base, as_of) {
                Some(r) => json!(r.id),
                None => Value::Null,
            })
        }
        _ => Err(FxError::NotANumber),
    }
}

/// One case to its `{"ok": ...}` / `{"error": true}` answer.
pub fn eval(case: &Value) -> Value {
    match run(case) {
        Ok(v) => json!({"ok": v}),
        Err(_) => json!({"error": true}),
    }
}

/// The subcommand: stdin lines to stdout lines.
pub fn main() -> i32 {
    use std::io::{BufRead, Write};
    let stdin = std::io::stdin();
    let mut out = std::io::BufWriter::new(std::io::stdout());
    for line in stdin.lock().lines() {
        let Ok(line) = line else { return 1 };
        if line.trim().is_empty() {
            continue;
        }
        let answer = match serde_json::from_str::<Value>(&line) {
            Ok(c) => eval(&c),
            Err(_) => json!({"error": true}),
        };
        if writeln!(out, "{answer}").is_err() {
            return 1;
        }
    }
    let _ = out.flush();
    0
}
