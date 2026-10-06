//! The numeric core of the inventory hub, ported from Python with the goal of
//! EXACT equality (same f64 bits), not "close enough": a one-unit difference
//! in a recommended quantity is a purchase order that is one unit wrong.
//!
//! Sources, function by function:
//!   * `backend/inventory/service.py`: `_z_for`, `_inverse_normal_cdf`,
//!     `_point_sigma`, `_avg_daily_forecast`, the period helpers, `_calc_signal`,
//!     `_measured_safety_stock`, `_safety_stock`, `_calc_recommended`,
//!     `_gate_recommended_by_signal`, `_resolve_lead_time_std`,
//!     `_resolve_review_period_days`, `resolve_lead_time`, `calc_unit_margin`.
//!   * `backend/inventory/abc_xyz.py`: classification and `class_summary`.
//!   * `backend/inventory/reception_service.py`: `_fill_rate`.
//!
//! Where Python's arithmetic is not plain IEEE operations the port reproduces
//! the exact algorithm instead of approximating it:
//!   * `round(x, n)` is correctly rounded on the exact binary value, ties to
//!     even (`py_round`): format with `n` decimals (Rust's formatter is exact)
//!     and parse back.
//!   * `round(x)` is ties-to-even (`round_ties_even`).
//!   * `sum()` of floats is Neumaier-compensated since CPython 3.12 (the
//!     production image is `python:3.12-slim`): `py_sum`.
//!   * `max` / `min` return the FIRST argument unless the second compares
//!     strictly better, which differs from `f64::max` on NaN and on -0.0.
//!   * `x ** 2` is libm `pow(x, 2.0)`, which is not always the same bits as
//!     `x * x`; the exponent goes through `black_box` so LLVM cannot rewrite
//!     the call into a multiplication, and both sides then ask the same libm.
//!   * `math.log` is libm `log`; `f64::ln` calls it too.
//!
//! The differential test at the bottom replays a fixture written by the
//! PYTHON implementation (`tests/contract/gen_inventory_fixtures.py`) and
//! demands bit equality for every number.

use serde_json::{Map, Value};
use std::hint::black_box;

// ── Python float semantics ───────────────────────────────────────────────────

/// Python's `max(a, b)`: `b` only when `b > a`.
pub fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Python's `min(a, b)`: `b` only when `b < a`.
pub fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// `round(x, ndigits)` for floats: correctly rounded, ties to even.
pub fn py_round(x: f64, ndigits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", ndigits, x).parse::<f64>().unwrap_or(x)
}

/// `sum(iterable_of_floats)` as CPython 3.12 computes it.
pub fn py_sum(items: &[f64]) -> f64 {
    let mut f_result = 0.0_f64;
    let mut c = 0.0_f64;
    for &x in items {
        let t = f_result + x;
        if f_result.abs() >= x.abs() {
            c += (f_result - t) + x;
        } else {
            c += (x - t) + f_result;
        }
        f_result = t;
    }
    if c != 0.0 && c.is_finite() {
        f_result += c;
    }
    f_result
}

/// `x ** 2` with `x` a float: libm `pow`, never rewritten to `x * x`.
fn py_square(x: f64) -> f64 {
    x.powf(black_box(2.0))
}

// ── Service-level quantile ───────────────────────────────────────────────────

// Acklam's coefficients, verbatim from service.py.
const A: [f64; 6] = [
    -3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
    1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00,
];
const B: [f64; 5] = [
    -5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
    6.680131188771972e+01, -1.328068155288572e+01,
];
const C: [f64; 6] = [
    -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
    -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00,
];
const D: [f64; 4] = [
    7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
    3.754408661907416e+00,
];
const P_LOW: f64 = 0.02425;

/// `_inverse_normal_cdf`.
pub fn inverse_normal_cdf(p: f64) -> f64 {
    if p < P_LOW {
        let q = (-2.0 * p.ln()).sqrt();
        return (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0);
    }
    if p <= 1.0 - P_LOW {
        let q = p - 0.5;
        let r = q * q;
        return (((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q
            / (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1.0);
    }
    let q = (-2.0 * (1.0 - p).ln()).sqrt();
    -(((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
        / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
}

/// `_z_for`: the four historical points verbatim, Acklam for anything else.
pub fn z_for(service_level: f64) -> f64 {
    // `service_level in _Z`: float equality against the four keys.
    if service_level == 0.90 { return 1.282; }
    if service_level == 0.95 { return 1.645; }
    if service_level == 0.97 { return 1.881; }
    if service_level == 0.99 { return 2.326; }
    let p = py_min(py_max(service_level, 0.5), 0.999999);
    inverse_normal_cdf(p)
}

// ── Period helpers ───────────────────────────────────────────────────────────

/// `_days_per_period`: unknown or missing is daily.
pub fn days_per_period(period: Option<&str>) -> i64 {
    match period {
        Some("weekly") => 7,
        Some("monthly") => 30,
        _ => 1,
    }
}

/// `_coverage_unit`.
pub fn coverage_unit(period: Option<&str>) -> &'static str {
    match period {
        Some("weekly") => "week",
        Some("monthly") => "month",
        _ => "day",
    }
}

/// `_lead_time_in_periods`.
pub fn lead_time_in_periods(lead_time_days: f64, period: &str) -> f64 {
    lead_time_days / days_per_period(Some(period)) as f64
}

/// `_steps_for_lead_time`: `max(1, ceil(lead_time / days_per_period))`.
pub fn steps_for_lead_time(lead_time_days: f64, period: &str) -> i64 {
    let c = (lead_time_days / days_per_period(Some(period)) as f64).ceil();
    std::cmp::max(1, c as i64)
}

// ── The semaforo ─────────────────────────────────────────────────────────────

/// `signal_thresholds.OVERSTOCK_REORDER_POINT_MULTIPLE`.
pub const OVERSTOCK_REORDER_POINT_MULTIPLE: f64 = 2.0;
/// `signal_thresholds.DEFAULT_THRESHOLDS`.
pub const DEFAULT_ORDER_NOW_FACTOR: f64 = 0.5;
pub const DEFAULT_OVERSTOCK_FACTOR: f64 = 3.0;

#[derive(Debug, Clone, Copy)]
pub struct Thresholds {
    pub order_now_factor: f64,
    pub overstock_factor: f64,
}

impl Default for Thresholds {
    fn default() -> Self {
        Thresholds { order_now_factor: DEFAULT_ORDER_NOW_FACTOR, overstock_factor: DEFAULT_OVERSTOCK_FACTOR }
    }
}

/// `_calc_signal`.
pub fn calc_signal(coverage_days: f64, lead_time: f64, reorder_point_days: f64, th: Option<Thresholds>) -> &'static str {
    let th = th.unwrap_or_default();
    if coverage_days >= 9990.0 {
        return "SOBRESTOCK";
    }
    if coverage_days < lead_time * th.order_now_factor {
        return "PEDIR_YA";
    }
    if coverage_days <= reorder_point_days {
        return "PEDIR_PRONTO";
    }
    let sobrestock_at = py_max(
        lead_time * th.overstock_factor,
        reorder_point_days * OVERSTOCK_REORDER_POINT_MULTIPLE,
    );
    if coverage_days < sobrestock_at {
        return "OK";
    }
    "SOBRESTOCK"
}

/// `_gate_recommended_by_signal`.
pub fn gate_recommended_by_signal(signal: &str, recommended: f64) -> f64 {
    if signal == "PEDIR_YA" || signal == "PEDIR_PRONTO" { recommended } else { 0.0 }
}

// ── Safety stock and the recommended quantity ────────────────────────────────

/// Python's `float(x)` for a JSON value that came from the engine: a number,
/// a bool, or a numeric string. `None` where Python would raise.
fn py_float(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    }
}

/// `str.isdigit()` restricted to ASCII (what the engine writes); `int()` of it.
fn ascii_horizon(key: &str) -> Option<i64> {
    if !key.is_empty() && key.bytes().all(|b| b.is_ascii_digit()) {
        key.parse::<i64>().ok()
    } else {
        None
    }
}

/// `_measured_safety_stock`: the backtest-measured cushion, or `None`.
pub fn measured_safety_stock(risk: Option<&Value>, lead_time: f64, service_level: f64) -> Option<f64> {
    let risk = risk?;
    let risk_obj = risk.as_object()?;
    if risk_obj.is_empty() {
        return None;
    }
    let offsets = match risk_obj.get("cumulative_offsets") {
        Some(Value::Object(o)) if !o.is_empty() => o,
        _ => return None,
    };
    let mut horizons: Vec<i64> = offsets.keys().filter_map(|k| ascii_horizon(k)).collect();
    horizons.sort_unstable();
    if horizons.is_empty() {
        return None;
    }
    let wanted = std::cmp::max(1, lead_time.ceil() as i64);
    let key = if horizons.contains(&wanted) {
        wanted
    } else {
        let below: Vec<i64> = horizons.iter().copied().filter(|h| *h <= wanted).collect();
        if below.is_empty() { horizons[0] } else { *below.iter().max().unwrap() }
    };
    let band = match offsets.get(&key.to_string()) {
        Some(Value::Object(b)) if !b.is_empty() => b,
        _ => return None,
    };
    let target = service_level;
    // dict {float(q): float(v)}: later duplicates overwrite in place.
    let mut levels: Vec<(f64, f64)> = Vec::new();
    for (q, v) in band {
        let (qf, vf) = (q.trim().parse::<f64>().ok()?, py_float(v)?);
        match levels.iter_mut().find(|(k, _)| *k == qf) {
            Some(slot) => slot.1 = vf,
            None => levels.push((qf, vf)),
        }
    }
    let exact = levels.iter().find(|(q, _)| (q - target).abs() < 1e-6);
    let offset = if let Some((_, v)) = exact {
        *v
    } else {
        let upper: Vec<&(f64, f64)> = levels.iter().filter(|(q, _)| *q > 0.5).collect();
        if upper.is_empty() {
            return None;
        }
        // min(upper, key=abs(q - target)): the FIRST of equal keys.
        let mut nearest = upper[0];
        for cand in &upper[1..] {
            if (cand.0 - target).abs() < (nearest.0 - target).abs() {
                nearest = cand;
            }
        }
        nearest.1 * (z_for(target) / z_for(nearest.0))
    };
    let offset = if key != wanted { offset * (wanted as f64 / key as f64).sqrt() } else { offset };
    Some(py_max(0.0, offset))
}

/// `_safety_stock`.
pub fn safety_stock(
    avg_std: f64,
    lead_time: f64,
    service_level: f64,
    risk: Option<&Value>,
    risk_scale: f64,
    avg_daily: f64,
    lead_time_std: f64,
) -> f64 {
    let z = z_for(service_level);
    let lead_time_term = z * avg_daily * py_max(0.0, lead_time_std);
    let demand_term = match measured_safety_stock(risk, lead_time, service_level) {
        Some(m) => m * risk_scale,
        None => z * avg_std * lead_time.sqrt(),
    };
    (py_square(demand_term) + py_square(lead_time_term)).sqrt()
}

/// `_calc_recommended`.
#[allow(clippy::too_many_arguments)]
pub fn calc_recommended(
    current_stock: f64,
    avg_daily: f64,
    avg_std: f64,
    lead_time: f64,
    moq: Option<f64>,
    service_level: f64,
    risk: Option<&Value>,
    risk_scale: f64,
    incoming: f64,
    lead_time_std: f64,
    review_period: f64,
) -> f64 {
    let protection_interval = lead_time + py_max(0.0, review_period);
    let lead_time_demand = avg_daily * protection_interval;
    let ss = safety_stock(avg_std, protection_interval, service_level, risk, risk_scale, avg_daily, lead_time_std);
    // max(0.0, lead_time_demand + safety_stock - current_stock - max(0.0, incoming))
    let mut raw = py_max(0.0, lead_time_demand + ss - current_stock - py_max(0.0, incoming));
    if let Some(m) = moq {
        if m != 0.0 && m > 0.0 && raw > 0.0 {
            raw = py_max(raw.ceil(), m);
        }
    }
    py_round(raw, 2)
}

// ── Forecast points ──────────────────────────────────────────────────────────

/// `_Q90_Z`.
pub const Q90_Z: f64 = 1.2816;

/// `p.get(key) or 0.0` then `float()`: JSON null, missing and 0 are 0.0.
fn num_or_zero(p: &Map<String, Value>, key: &str) -> f64 {
    match p.get(key) {
        None | Some(Value::Null) => 0.0,
        Some(v) => py_float(v).unwrap_or(0.0),
    }
}

/// `_point_sigma`.
pub fn point_sigma(p: &Map<String, Value>) -> f64 {
    let value = num_or_zero(p, "value");
    if let Some(q90) = p.get("q90").filter(|v| !v.is_null()) {
        return py_max(0.0, (py_float(q90).unwrap_or(0.0) - value) / Q90_Z);
    }
    match p.get("upper").filter(|v| !v.is_null()) {
        None => 0.0,
        Some(u) => py_max(0.0, (py_float(u).unwrap_or(0.0) - value) / Q90_Z),
    }
}

/// `_pick_model`: the preferred model when it carries points, else all.
fn pick_model<'a>(model_forecasts: &'a Map<String, Value>, preferred: Option<&str>) -> Vec<&'a Value> {
    if let Some(name) = preferred.filter(|n| !n.is_empty()) {
        if let Some(chosen) = model_forecasts.get(name) {
            let has_points = chosen
                .get("forecast")
                .and_then(Value::as_array)
                .map(|a| !a.is_empty())
                .unwrap_or(false);
            if crate::pycompat::truthy(chosen) && has_points {
                return vec![chosen];
            }
        }
    }
    model_forecasts.values().collect()
}

/// `_avg_daily_forecast`: (avg demand per step, avg sigma per step).
pub fn avg_daily_forecast(model_forecasts: &Map<String, Value>, lead_time: usize, preferred: Option<&str>) -> (f64, f64) {
    let mut values: Vec<f64> = Vec::new();
    let mut stds: Vec<f64> = Vec::new();
    for model in pick_model(model_forecasts, preferred) {
        let pts = match model.get("forecast").and_then(Value::as_array) {
            Some(a) => a,
            None => continue,
        };
        let pts = &pts[..pts.len().min(lead_time)];
        if pts.is_empty() {
            continue;
        }
        for p in pts {
            let empty = Map::new();
            let obj = p.as_object().unwrap_or(&empty);
            values.push(num_or_zero(obj, "value"));
            stds.push(point_sigma(obj));
        }
    }
    if values.is_empty() {
        return (0.0, 0.0);
    }
    let avg_daily = py_sum(&values) / values.len() as f64;
    let avg_std = if stds.is_empty() { 0.0 } else { py_sum(&stds) / stds.len() as f64 };
    (py_max(0.0, avg_daily), py_max(0.0, avg_std))
}

// ── Lead time cascade ────────────────────────────────────────────────────────

/// `MIN_LEAD_TIME_OBSERVATIONS`.
#[allow(dead_code)]
pub const MIN_LEAD_TIME_OBSERVATIONS: i64 = 3;

/// `supplier.strip().lower()`.
fn supplier_key(s: &str) -> String {
    crate::pycompat::py_strip(s).to_lowercase()
}

/// `_resolve_lead_time_std`: learned, then configured, then 0.0.
pub fn resolve_lead_time_std(
    supplier: Option<&str>,
    learned: &std::collections::HashMap<String, f64>,
    configured: &std::collections::HashMap<String, f64>,
) -> f64 {
    let Some(s) = supplier.filter(|s| !s.is_empty()) else { return 0.0 };
    let key = supplier_key(s);
    if let Some(v) = learned.get(&key) {
        return py_max(0.0, *v);
    }
    if let Some(v) = configured.get(&key) {
        return py_max(0.0, *v);
    }
    0.0
}

/// `_resolve_review_period_days`.
pub fn resolve_review_period_days(
    supplier: Option<&str>,
    map: &std::collections::HashMap<String, f64>,
) -> f64 {
    let Some(s) = supplier.filter(|s| !s.is_empty()) else { return 0.0 };
    py_max(0.0, map.get(&supplier_key(s)).copied().unwrap_or(0.0))
}

/// `resolve_lead_time`: (lead_time_days, source, learned_raw).
pub fn resolve_lead_time(
    configured: i64,
    supplier: Option<&str>,
    learned_by_supplier: &std::collections::HashMap<String, f64>,
    configured_source: &str,
) -> (i64, String, Option<f64>) {
    if let Some(s) = supplier.filter(|s| !s.is_empty()) {
        if let Some(learned) = learned_by_supplier.get(&supplier_key(s)) {
            if *learned > 0.0 {
                return (
                    std::cmp::max(1, learned.round_ties_even() as i64),
                    "learned".to_string(),
                    Some(py_round(*learned, 1)),
                );
            }
        }
    }
    (configured, configured_source.to_string(), None)
}

/// `calc_unit_margin`.
pub fn calc_unit_margin(sale_price: Option<f64>, unit_cost: Option<f64>) -> Option<f64> {
    Some(py_round(sale_price? - unit_cost?, 2))
}

/// `reception_service._fill_rate`.
pub fn fill_rate(total_received: f64, order_total: f64) -> Option<f64> {
    if order_total <= 0.0 {
        return None;
    }
    Some(py_round(py_min(1.0, total_received / order_total), 3))
}

// ── ABC / XYZ ────────────────────────────────────────────────────────────────

pub const ABC_A_CUTOFF: f64 = 0.80;
pub const ABC_B_CUTOFF: f64 = 0.95;
pub const XYZ_X_CUTOFF: f64 = 0.5;
pub const XYZ_Y_CUTOFF: f64 = 1.0;
pub const ABC_CLASSES: [&str; 3] = ["A", "B", "C"];

/// `SUGGESTED_SERVICE_LEVEL`.
pub fn suggested_service_level(cls: &str) -> f64 {
    match cls {
        "A" => 0.98,
        "B" => 0.95,
        _ => 0.90,
    }
}

/// `classify_xyz`.
pub fn classify_xyz(cv: Option<f64>) -> &'static str {
    match cv {
        None => "?",
        Some(c) if c < XYZ_X_CUTOFF => "X",
        Some(c) if c < XYZ_Y_CUTOFF => "Y",
        Some(_) => "Z",
    }
}

/// `classify_abc`: `(sku, value)` pairs to `(sku, class)` in dict order.
pub fn classify_abc(scored: &[(String, f64)]) -> Vec<(String, &'static str)> {
    let mut ordered: Vec<&(String, f64)> = scored.iter().collect();
    // sorted(key=value, reverse=True) is stable: equal values keep input order.
    ordered.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let total = py_sum(&ordered.iter().map(|(_, v)| py_max(0.0, *v)).collect::<Vec<_>>());
    let mut out: Vec<(String, &'static str)> = Vec::new();
    let put = |sku: &str, cls: &'static str, out: &mut Vec<(String, &'static str)>| {
        match out.iter_mut().find(|(s, _)| s == sku) {
            Some(slot) => slot.1 = cls,
            None => out.push((sku.to_string(), cls)),
        }
    };
    if total <= 0.0 {
        for (sku, _) in &ordered {
            put(sku, "C", &mut out);
        }
        return out;
    }
    let mut cumulative = 0.0_f64;
    for (sku, value) in &ordered {
        let cls = if cumulative < ABC_A_CUTOFF {
            "A"
        } else if cumulative < ABC_B_CUTOFF {
            "B"
        } else {
            "C"
        };
        put(sku, cls, &mut out);
        cumulative += py_max(0.0, *value) / total;
    }
    out
}

/// One input row of `class_summary`.
#[derive(Debug, Clone)]
pub struct ClassRow {
    pub abc: Option<String>,
    pub value: f64,
    pub service_level: Option<f64>,
    pub owned: bool,
}

/// One output row of `class_summary`.
#[derive(Debug, Clone, PartialEq)]
pub struct ClassSummary {
    pub abc: &'static str,
    pub skus: i64,
    pub value_share: f64,
    pub current_service_level: Option<f64>,
    pub suggested_service_level: f64,
    pub would_change: i64,
    pub owned: i64,
}

/// `class_summary`: always three rows, A, B, C.
pub fn class_summary(rows: &[ClassRow]) -> Vec<ClassSummary> {
    let total_value = py_sum(&rows.iter().map(|r| py_max(0.0, r.value)).collect::<Vec<_>>());
    ABC_CLASSES
        .iter()
        .map(|cls| {
            let members: Vec<&ClassRow> = rows.iter().filter(|r| r.abc.as_deref() == Some(cls)).collect();
            let value = py_sum(&members.iter().map(|r| py_max(0.0, r.value)).collect::<Vec<_>>());
            let levels: Vec<f64> = members.iter().filter_map(|r| r.service_level).collect();
            let suggested = suggested_service_level(cls);
            ClassSummary {
                abc: cls,
                skus: members.len() as i64,
                value_share: if total_value > 0.0 { py_round(value / total_value, 4) } else { 0.0 },
                current_service_level: if levels.is_empty() {
                    None
                } else {
                    Some(py_round(py_sum(&levels) / levels.len() as f64, 4))
                },
                suggested_service_level: suggested,
                would_change: members
                    .iter()
                    .filter(|r| !r.owned && r.service_level.map(|l| (l - suggested).abs() > 1e-9).unwrap_or(false))
                    .count() as i64,
                owned: members.iter().filter(|r| r.owned).count() as i64,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::collections::HashMap;

    fn bits(v: &Value) -> f64 {
        // Floats travel as the decimal string of their IEEE bits so that no
        // JSON parser can round them.
        match v {
            Value::String(s) => f64::from_bits(s.parse::<u64>().expect("bits")),
            Value::Null => panic!("null where a float was expected"),
            other => panic!("not a bit string: {other}"),
        }
    }

    fn opt(v: &Value) -> Option<f64> {
        if v.is_null() { None } else { Some(bits(v)) }
    }

    /// Replace every `"~<bits>"` leaf by the float the bits name, so
    /// nested engine payloads (risk bands, forecast points) arrive exact.
    fn thaw(v: &Value) -> Value {
        match v {
            Value::Object(m) => Value::Object(m.iter().map(|(k, x)| (k.clone(), thaw(x))).collect()),
            Value::Array(a) => Value::Array(a.iter().map(thaw).collect()),
            Value::String(s) if s.starts_with('~') => {
                json!(f64::from_bits(s[1..].parse::<u64>().expect("bits")))
            }
            other => other.clone(),
        }
    }

    fn same(label: &str, case: &Value, got: f64, want: f64) {
        assert!(
            got.to_bits() == want.to_bits() || (got.is_nan() && want.is_nan()),
            "{label}: Rust {got:?} ({:#x}) != Python {want:?} ({:#x}) for {case}",
            got.to_bits(), want.to_bits()
        );
    }

    fn fixture() -> Value {
        serde_json::from_str(include_str!("../../tests/fixtures/inventory_calc.json")).expect("fixture parses")
    }

    #[test]
    fn python_round_ties_and_signs() {
        assert_eq!(py_round(0.125, 2), 0.12);
        assert_eq!(py_round(0.375, 2), 0.38);
        assert_eq!(py_round(2.5, 0), 2.0);
        assert_eq!(py_round(-0.001, 2).to_bits(), (-0.0_f64).to_bits());
        assert_eq!(py_round(1.005, 2), 1.0);
        assert_eq!(py_round(2.675, 2), 2.67);
    }

    #[test]
    fn python_max_min_keep_the_first_on_ties_and_nan() {
        assert!(py_max(f64::NAN, 1.0).is_nan());
        assert_eq!(py_max(0.0, f64::NAN), 0.0);
        assert_eq!(py_max(0.0, -0.0).to_bits(), 0.0_f64.to_bits());
    }

    #[test]
    fn differential_z_and_quantile() {
        for c in fixture()["z_for"].as_array().unwrap() {
            same("z_for", c, z_for(bits(&c["sl"])), bits(&c["z"]));
        }
        for c in fixture()["inverse_normal_cdf"].as_array().unwrap() {
            same("inverse_normal_cdf", c, inverse_normal_cdf(bits(&c["p"])), bits(&c["out"]));
        }
    }

    #[test]
    fn differential_period_helpers() {
        for c in fixture()["periods"].as_array().unwrap() {
            let period = c["period"].as_str();
            assert_eq!(days_per_period(period), c["dpp"].as_i64().unwrap(), "{c}");
            assert_eq!(coverage_unit(period), c["unit"].as_str().unwrap(), "{c}");
            if let Some(p) = period {
                let lt = bits(&c["lt"]);
                same("lead_time_in_periods", c, lead_time_in_periods(lt, p), bits(&c["ltp"]));
                assert_eq!(steps_for_lead_time(lt, p), c["steps"].as_i64().unwrap(), "{c}");
            }
        }
    }

    #[test]
    fn differential_signal() {
        for c in fixture()["calc_signal"].as_array().unwrap() {
            let th = if c["th"].is_null() {
                None
            } else {
                Some(Thresholds { order_now_factor: bits(&c["th"]["order_now_factor"]), overstock_factor: bits(&c["th"]["overstock_factor"]) })
            };
            let got = calc_signal(bits(&c["cov"]), bits(&c["lt"]), bits(&c["rp"]), th);
            assert_eq!(got, c["out"].as_str().unwrap(), "{c}");
            let rec = bits(&c["rec"]);
            same("gate", c, gate_recommended_by_signal(got, rec), bits(&c["gated"]));
        }
    }

    #[test]
    fn differential_measured_safety_stock() {
        for c in fixture()["measured"].as_array().unwrap() {
            let risk = thaw(&c["risk"]);
            let risk_ref = if c["risk_none"].as_bool().unwrap() { None } else { Some(&risk) };
            let got = measured_safety_stock(risk_ref, bits(&c["lt"]), bits(&c["sl"]));
            match (got, opt(&c["out"])) {
                (None, None) => {}
                (Some(g), Some(w)) => same("measured", c, g, w),
                (g, w) => panic!("measured: Rust {g:?} vs Python {w:?} for {c}"),
            }
        }
    }

    #[test]
    fn differential_safety_stock_and_recommended() {
        for c in fixture()["recommended"].as_array().unwrap() {
            let risk = thaw(&c["risk"]);
            let risk_ref = if c["risk_none"].as_bool().unwrap() { None } else { Some(&risk) };
            let ss = safety_stock(
                bits(&c["avg_std"]), bits(&c["pi"]), bits(&c["sl"]), risk_ref,
                bits(&c["risk_scale"]), bits(&c["avg_daily"]), bits(&c["lt_std"]),
            );
            same("safety_stock", c, ss, bits(&c["ss"]));
            let rec = calc_recommended(
                bits(&c["current"]), bits(&c["avg_daily"]), bits(&c["avg_std"]), bits(&c["lt"]),
                opt(&c["moq"]), bits(&c["sl"]), risk_ref, bits(&c["risk_scale"]),
                bits(&c["incoming"]), bits(&c["lt_std"]), bits(&c["review"]),
            );
            same("calc_recommended", c, rec, bits(&c["rec"]));
        }
    }

    #[test]
    fn differential_forecast_averages() {
        for c in fixture()["avg_forecast"].as_array().unwrap() {
            let mf = thaw(&c["models"]);
            let (d, s) = avg_daily_forecast(mf.as_object().unwrap(), c["steps"].as_u64().unwrap() as usize,
                c["preferred"].as_str());
            same("avg_daily", c, d, bits(&c["avg"]));
            same("avg_std", c, s, bits(&c["std"]));
        }
        for c in fixture()["point_sigma"].as_array().unwrap() {
            let p = thaw(&c["point"]);
            same("point_sigma", c, point_sigma(p.as_object().unwrap()), bits(&c["out"]));
        }
    }

    #[test]
    fn differential_lead_time_cascade() {
        let map = |v: &Value| -> HashMap<String, f64> {
            v.as_object().unwrap().iter().map(|(k, x)| (k.clone(), bits(x))).collect()
        };
        for c in fixture()["lead_time"].as_array().unwrap() {
            let supplier = c["supplier"].as_str();
            let learned = map(&c["learned"]);
            let (days, source, raw) = resolve_lead_time(
                c["configured"].as_i64().unwrap(), supplier, &learned, c["source"].as_str().unwrap());
            assert_eq!(days, c["days"].as_i64().unwrap(), "{c}");
            assert_eq!(source, c["out_source"].as_str().unwrap(), "{c}");
            match (raw, opt(&c["raw"])) {
                (None, None) => {}
                (Some(g), Some(w)) => same("learned_raw", c, g, w),
                (g, w) => panic!("raw: Rust {g:?} vs Python {w:?} for {c}"),
            }
            let configured = map(&c["configured_std"]);
            same("lt_std", c, resolve_lead_time_std(supplier, &learned, &configured), bits(&c["lt_std"]));
            same("review", c, resolve_review_period_days(supplier, &configured), bits(&c["review"]));
        }
    }

    #[test]
    fn differential_margin_and_fill_rate() {
        for c in fixture()["margin"].as_array().unwrap() {
            match (calc_unit_margin(opt(&c["price"]), opt(&c["cost"])), opt(&c["out"])) {
                (None, None) => {}
                (Some(g), Some(w)) => same("margin", c, g, w),
                (g, w) => panic!("margin: Rust {g:?} vs Python {w:?} for {c}"),
            }
        }
        for c in fixture()["fill_rate"].as_array().unwrap() {
            match (fill_rate(bits(&c["received"]), bits(&c["total"])), opt(&c["out"])) {
                (None, None) => {}
                (Some(g), Some(w)) => same("fill_rate", c, g, w),
                (g, w) => panic!("fill_rate: Rust {g:?} vs Python {w:?} for {c}"),
            }
        }
    }

    #[test]
    fn differential_abc_xyz() {
        for c in fixture()["xyz"].as_array().unwrap() {
            assert_eq!(classify_xyz(opt(&c["cv"])), c["out"].as_str().unwrap(), "{c}");
        }
        for c in fixture()["abc"].as_array().unwrap() {
            let scored: Vec<(String, f64)> = c["scored"].as_array().unwrap().iter()
                .map(|p| (p[0].as_str().unwrap().to_string(), bits(&p[1]))).collect();
            let got = classify_abc(&scored);
            let want: Vec<(String, String)> = c["out"].as_array().unwrap().iter()
                .map(|p| (p[0].as_str().unwrap().to_string(), p[1].as_str().unwrap().to_string())).collect();
            let got: Vec<(String, String)> = got.into_iter().map(|(s, k)| (s, k.to_string())).collect();
            assert_eq!(got, want, "{c}");
        }
        for c in fixture()["class_summary"].as_array().unwrap() {
            let rows: Vec<ClassRow> = c["rows"].as_array().unwrap().iter().map(|r| ClassRow {
                abc: r["abc"].as_str().map(str::to_string),
                value: bits(&r["value"]),
                service_level: opt(&r["service_level"]),
                owned: r["owned"].as_bool().unwrap(),
            }).collect();
            let got = class_summary(&rows);
            let want = c["out"].as_array().unwrap();
            assert_eq!(got.len(), want.len());
            for (g, w) in got.iter().zip(want) {
                assert_eq!(g.abc, w["abc"].as_str().unwrap(), "{c}");
                assert_eq!(g.skus, w["skus"].as_i64().unwrap(), "{c}");
                same("value_share", c, g.value_share, bits(&w["value_share"]));
                match (g.current_service_level, opt(&w["current_service_level"])) {
                    (None, None) => {}
                    (Some(a), Some(b)) => same("current_service_level", c, a, b),
                    (a, b) => panic!("current_service_level: Rust {a:?} vs Python {b:?} for {c}"),
                }
                same("suggested", c, g.suggested_service_level, bits(&w["suggested_service_level"]));
                assert_eq!(g.would_change, w["would_change"].as_i64().unwrap(), "{c}");
                assert_eq!(g.owned, w["owned"].as_i64().unwrap(), "{c}");
            }
        }
    }
}
