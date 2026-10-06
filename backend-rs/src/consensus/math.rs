//! The S&OP consensus and forecast-value-added arithmetic.
//!
//! Pure functions, no I/O. The Python reference is
//! `tests/contract/consensus_reference.py`; `consensus_cases.json` (written by
//! `tests/contract/gen_consensus_fixtures.py`) holds what it answers for a few
//! hundred random inputs, and `matches_the_python_reference` below demands
//! exact equality: integers for the consensus, bit-for-bit f64 for the accuracy
//! figures.
//!
//! Percentages are integer basis points (1 bp = 0.01%), so the consensus never
//! touches a float.

use serde_json::{json, Value};

pub const FUNCTIONS: [&str; 3] = ["sales", "finance", "operations"];

/// `realized.FVA_MIN_POINTS` / `FVA_NEUTRAL_BAND_PCT`.
pub const FVA_MIN_POINTS: usize = 5;
pub const FVA_NEUTRAL_BAND_PCT: f64 = 2.0;

/// Index of a function name in [`FUNCTIONS`].
pub fn function_index(name: &str) -> Option<usize> {
    FUNCTIONS.iter().position(|f| *f == name)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rule {
    Priority,
    Weighted,
}

impl Rule {
    pub fn parse(s: &str) -> Option<Rule> {
        match s {
            "priority" => Some(Rule::Priority),
            "weighted" => Some(Rule::Weighted),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Rule::Priority => "priority",
            Rule::Weighted => "weighted",
        }
    }
}

/// The tenant's rule. `priority` lists function indexes, first wins; `weights`
/// is indexed like [`FUNCTIONS`].
#[derive(Debug, Clone)]
pub struct Config {
    pub rule: Rule,
    pub priority: [usize; 3],
    pub weights: [i64; 3],
    pub cap_down_bp: i64,
    pub cap_up_bp: i64,
}

#[derive(Debug, Clone)]
pub struct Submission {
    pub id: String,
    pub sku: String,
    pub function: usize,
    /// Inclusive day numbers.
    pub start: i64,
    pub end: i64,
    pub pct_bp: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Input {
    pub function: usize,
    /// What the function submitted, before the caps.
    pub pct_bp: i64,
    pub submission_id: String,
    pub capped: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub sku: String,
    pub start: i64,
    pub end: i64,
    pub pct_bp: i64,
    /// The deciding function under `priority`; `None` under `weighted`.
    pub source: Option<usize>,
    pub inputs: Vec<Input>,
}

/// Two current submissions of one function overlap on one SKU: the ledger the
/// routes keep never allows it, so this is a refusal, not a guess.
#[derive(Debug, PartialEq, Eq)]
pub struct Inconsistent {
    pub sku: String,
}

/// `num / den` rounded to the nearest integer, halves away from zero (`den > 0`).
pub fn div_round_half_away(num: i128, den: i128) -> i128 {
    let n = num.abs();
    let mut q = n / den;
    if 2 * (n % den) >= den {
        q += 1;
    }
    if num >= 0 {
        q
    } else {
        -q
    }
}

/// The consensus per SKU and period. See the Python reference's docstring for
/// the rule; every submitted figure is clamped to the caps first.
pub fn consensus_lines(cfg: &Config, submissions: &[Submission]) -> Result<Vec<Line>, Inconsistent> {
    let mut skus: Vec<&str> = submissions.iter().map(|s| s.sku.as_str()).collect();
    skus.sort_unstable();
    skus.dedup();
    let mut lines = Vec::new();
    for sku in skus {
        let subs: Vec<&Submission> = submissions.iter().filter(|s| s.sku == sku).collect();
        let mut points: Vec<i64> = subs.iter().flat_map(|s| [s.start, s.end + 1]).collect();
        points.sort_unstable();
        points.dedup();
        for pair in points.windows(2) {
            let (a, b) = (pair[0], pair[1]);
            let mut covering: Vec<&&Submission> = subs.iter().filter(|s| s.start <= a && a <= s.end).collect();
            if covering.is_empty() {
                continue;
            }
            let mut seen = [false; 3];
            for s in &covering {
                if std::mem::replace(&mut seen[s.function], true) {
                    return Err(Inconsistent { sku: sku.to_string() });
                }
            }
            covering.sort_by_key(|s| s.function);
            let clamped = |s: &Submission| s.pct_bp.clamp(cfg.cap_down_bp.min(cfg.cap_up_bp), cfg.cap_up_bp.max(cfg.cap_down_bp));
            let inputs: Vec<Input> = covering
                .iter()
                .map(|s| Input {
                    function: s.function,
                    pct_bp: s.pct_bp,
                    submission_id: s.id.clone(),
                    capped: clamped(s) != s.pct_bp,
                })
                .collect();
            let (source, pct) = match cfg.rule {
                Rule::Priority => {
                    let hit = cfg.priority.iter().find_map(|f| covering.iter().find(|s| s.function == *f));
                    match hit {
                        Some(s) => (Some(s.function), Some(clamped(s))),
                        None => (None, None),
                    }
                }
                Rule::Weighted => {
                    let voters: Vec<&&&Submission> = covering.iter().filter(|s| cfg.weights[s.function] > 0).collect();
                    if voters.is_empty() {
                        (None, None)
                    } else {
                        let num: i128 = voters.iter().map(|s| cfg.weights[s.function] as i128 * clamped(s) as i128).sum();
                        let den: i128 = voters.iter().map(|s| cfg.weights[s.function] as i128).sum();
                        (None, Some(div_round_half_away(num, den) as i64))
                    }
                }
            };
            if let Some(pct_bp) = pct {
                lines.push(Line { sku: sku.to_string(), start: a, end: b - 1, pct_bp, source, inputs });
            }
        }
    }
    Ok(lines)
}

// -- Forecast value added -------------------------------------------------------

/// One graded SKU-period: the statistical forecast, what sold, and the
/// percentage somebody applied to the forecast.
#[derive(Debug, Clone)]
pub struct FvaPoint {
    pub base: f64,
    pub pct_bp: i64,
    pub actual: f64,
}

/// `base * (1 + pct/100)` with the percentage given in basis points, evaluated
/// in the order the reference does.
pub fn adjusted_value(base: f64, pct_bp: i64) -> f64 {
    base * (1.0 + (pct_bp as f64 / 100.0) / 100.0)
}

#[derive(Debug, Clone, PartialEq)]
pub struct Fva {
    pub n_points: usize,
    pub base_error: f64,
    pub adjusted_error: f64,
    pub actual_total: f64,
    pub base_wape: Option<f64>,
    pub adjusted_wape: Option<f64>,
    pub base_bias: Option<f64>,
    pub adjusted_bias: Option<f64>,
    pub improvement_pct: Option<f64>,
    pub better_points: usize,
    pub worse_points: usize,
    pub verdict: &'static str,
}

fn safe_div(a: f64, b: f64) -> Option<f64> {
    if b == 0.0 {
        None
    } else {
        Some(a / b)
    }
}

/// Plain left-to-right addition, the order the reference uses.
fn seq_sum(values: impl Iterator<Item = f64>) -> f64 {
    let mut total = 0.0;
    for v in values {
        total += v;
    }
    total
}

pub fn forecast_value_added(points: &[FvaPoint]) -> Fva {
    let n = points.len();
    if n == 0 {
        return Fva {
            n_points: 0, base_error: 0.0, adjusted_error: 0.0, actual_total: 0.0, base_wape: None,
            adjusted_wape: None, base_bias: None, adjusted_bias: None, improvement_pct: None,
            better_points: 0, worse_points: 0, verdict: "no_data",
        };
    }
    let adj: Vec<f64> = points.iter().map(|p| adjusted_value(p.base, p.pct_bp)).collect();
    let base_err = seq_sum(points.iter().map(|p| (p.base - p.actual).abs()));
    let adj_err = seq_sum(adj.iter().zip(points).map(|(a, p)| (a - p.actual).abs()));
    let total = seq_sum(points.iter().map(|p| p.actual));
    let better = adj.iter().zip(points).filter(|(a, p)| (*a - p.actual).abs() < (p.base - p.actual).abs()).count();
    let worse = adj.iter().zip(points).filter(|(a, p)| (*a - p.actual).abs() > (p.base - p.actual).abs()).count();
    let base_bias = safe_div(seq_sum(points.iter().map(|p| p.base)) - total, total);
    let adjusted_bias = safe_div(seq_sum(adj.iter().copied()) - total, total);
    let improvement = if base_err <= 0.0 { None } else { Some((base_err - adj_err) / base_err * 100.0) };
    let verdict = if n < FVA_MIN_POINTS {
        "too_little"
    } else {
        match improvement {
            // The engine says "neutral"; a perfect statistical forecast that an
            // adjustment spoiled is "worsened" (see the Python reference).
            None if adj_err > 0.0 => "worsened",
            None => "neutral",
            Some(i) if i > FVA_NEUTRAL_BAND_PCT => "improved",
            Some(i) if i < -FVA_NEUTRAL_BAND_PCT => "worsened",
            Some(_) => "neutral",
        }
    };
    Fva {
        n_points: n, base_error: base_err, adjusted_error: adj_err, actual_total: total,
        base_wape: safe_div(base_err, total), adjusted_wape: safe_div(adj_err, total), base_bias,
        adjusted_bias, improvement_pct: improvement, better_points: better, worse_points: worse, verdict,
    }
}

/// One reading per distinct key, best improvement first, undefined last; the
/// sort is stable, so equal readings keep their first-seen order.
pub fn forecast_value_added_by<T>(items: &[T], key: impl Fn(&T) -> String, point: impl Fn(&T) -> FvaPoint) -> Vec<(String, Fva)> {
    let mut order: Vec<String> = Vec::new();
    let mut groups: std::collections::HashMap<String, Vec<FvaPoint>> = std::collections::HashMap::new();
    for it in items {
        let k = key(it);
        if !groups.contains_key(&k) {
            order.push(k.clone());
        }
        groups.entry(k).or_default().push(point(it));
    }
    let mut rows: Vec<(String, Fva)> = order.into_iter().map(|k| {
        let f = forecast_value_added(&groups[&k]);
        (k, f)
    }).collect();
    rows.sort_by(|a, b| {
        let ka = (a.1.improvement_pct.is_none(), -(a.1.improvement_pct.unwrap_or(0.0)));
        let kb = (b.1.improvement_pct.is_none(), -(b.1.improvement_pct.unwrap_or(0.0)));
        ka.0.cmp(&kb.0).then(ka.1.partial_cmp(&kb.1).unwrap_or(std::cmp::Ordering::Equal))
    });
    rows
}

impl Fva {
    /// The JSON the API returns (the keys the engine's `forecast_value_added` has).
    pub fn to_json(&self) -> Value {
        json!({
            "n_points": self.n_points, "base_error": self.base_error, "adjusted_error": self.adjusted_error,
            "actual_total": self.actual_total, "base_wape": self.base_wape, "adjusted_wape": self.adjusted_wape,
            "base_bias": self.base_bias, "adjusted_bias": self.adjusted_bias,
            "improvement_pct": self.improvement_pct, "better_points": self.better_points,
            "worse_points": self.worse_points, "verdict": self.verdict,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CASES: &str = include_str!("consensus_cases.json");

    fn s(id: &str, sku: &str, f: &str, start: i64, end: i64, bp: i64) -> Submission {
        Submission { id: id.into(), sku: sku.into(), function: function_index(f).unwrap(), start, end, pct_bp: bp }
    }

    fn cfg(rule: Rule) -> Config {
        Config { rule, priority: [0, 1, 2], weights: [1, 1, 1], cap_down_bp: -10000, cap_up_bp: 100000 }
    }

    #[test]
    fn rounding_is_half_away_from_zero() {
        assert_eq!(div_round_half_away(5, 2), 3);
        assert_eq!(div_round_half_away(-5, 2), -3);
        assert_eq!(div_round_half_away(4, 3), 1);
        assert_eq!(div_round_half_away(-4, 3), -1);
        assert_eq!(div_round_half_away(0, 7), 0);
        assert_eq!(div_round_half_away(1, 2), 1);
        assert_eq!(div_round_half_away(-1, 2), -1);
    }

    #[test]
    fn nothing_submitted_gives_no_lines() {
        assert_eq!(consensus_lines(&cfg(Rule::Priority), &[]).unwrap(), vec![]);
    }

    #[test]
    fn priority_takes_the_first_function_that_submitted() {
        let mut c = cfg(Rule::Priority);
        c.priority = [2, 0, 1]; // operations, sales, finance
        let subs = [s("a", "X", "sales", 0, 9, 1000), s("b", "X", "finance", 0, 9, -500)];
        let lines = consensus_lines(&c, &subs).unwrap();
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].pct_bp, 1000);
        assert_eq!(lines[0].source, Some(0));
        // operations speaks too: it now wins.
        let subs = [s("a", "X", "sales", 0, 9, 1000), s("c", "X", "operations", 5, 9, 200)];
        let lines = consensus_lines(&c, &subs).unwrap();
        assert_eq!(lines.iter().map(|l| (l.start, l.end, l.pct_bp, l.source)).collect::<Vec<_>>(),
            vec![(0, 4, 1000, Some(0)), (5, 9, 200, Some(2))]);
    }

    #[test]
    fn weighted_renormalises_over_the_functions_that_submitted() {
        let mut c = cfg(Rule::Weighted);
        c.weights = [3, 1, 0];
        let subs = [s("a", "X", "sales", 0, 4, 1000), s("b", "X", "finance", 0, 4, 0)];
        let lines = consensus_lines(&c, &subs).unwrap();
        // (3*1000 + 1*0) / 4 = 750
        assert_eq!(lines[0].pct_bp, 750);
        // finance alone: its own figure, not diluted by sales' silence.
        let lines = consensus_lines(&c, &[s("b", "X", "finance", 0, 4, 400)]).unwrap();
        assert_eq!(lines[0].pct_bp, 400);
        // a zero weight is no vote: operations alone has no line at all.
        assert_eq!(consensus_lines(&c, &[s("c", "X", "operations", 0, 4, 400)]).unwrap(), vec![]);
    }

    #[test]
    fn caps_clamp_each_figure_before_it_counts() {
        let mut c = cfg(Rule::Weighted);
        c.cap_up_bp = 2000;
        c.cap_down_bp = -1000;
        let subs = [s("a", "X", "sales", 0, 4, 9000), s("b", "X", "finance", 0, 4, -4000)];
        let lines = consensus_lines(&c, &subs).unwrap();
        assert_eq!(lines[0].pct_bp, 500); // (2000 + -1000) / 2
        assert!(lines[0].inputs.iter().all(|i| i.capped));
        assert_eq!(lines[0].inputs[0].pct_bp, 9000, "the submitted figure is kept as submitted");
    }

    #[test]
    fn periods_are_cut_where_the_submissions_change() {
        let c = cfg(Rule::Weighted);
        let subs = [s("a", "X", "sales", 0, 9, 1000), s("b", "X", "finance", 5, 14, 0)];
        let lines = consensus_lines(&c, &subs).unwrap();
        assert_eq!(lines.iter().map(|l| (l.start, l.end, l.pct_bp)).collect::<Vec<_>>(),
            vec![(0, 4, 1000), (5, 9, 500), (10, 14, 0)]);
    }

    #[test]
    fn a_function_overlapping_itself_is_refused() {
        let subs = [s("a", "X", "sales", 0, 9, 1000), s("b", "X", "sales", 5, 14, 0)];
        assert_eq!(consensus_lines(&cfg(Rule::Priority), &subs), Err(Inconsistent { sku: "X".into() }));
    }

    #[test]
    fn a_gap_between_submissions_has_no_line() {
        let subs = [s("a", "X", "sales", 0, 2, 100), s("b", "X", "sales", 6, 8, 100)];
        let lines = consensus_lines(&cfg(Rule::Priority), &subs).unwrap();
        assert_eq!(lines.iter().map(|l| (l.start, l.end)).collect::<Vec<_>>(), vec![(0, 2), (6, 8)]);
    }

    #[test]
    fn value_added_judges_an_adjustment_against_what_sold() {
        // Sales said +50% on a forecast that was right: it made things worse.
        let pts: Vec<FvaPoint> = (0..6).map(|_| FvaPoint { base: 100.0, pct_bp: 5000, actual: 100.0 }).collect();
        let f = forecast_value_added(&pts);
        assert_eq!(f.verdict, "worsened");
        assert_eq!(f.base_error, 0.0);
        assert_eq!(f.adjusted_error, 300.0);
        assert_eq!(f.improvement_pct, None, "no baseline error: nothing to improve on");
        assert_eq!(f.worse_points, 6);
        // The same +50% on a forecast that ran low: it helped.
        let pts: Vec<FvaPoint> = (0..6).map(|_| FvaPoint { base: 100.0, pct_bp: 5000, actual: 150.0 }).collect();
        let f = forecast_value_added(&pts);
        assert_eq!(f.verdict, "improved");
        assert_eq!(f.improvement_pct, Some(100.0));
        assert_eq!(f.better_points, 6);
        // Too few points to judge.
        assert_eq!(forecast_value_added(&pts[..4]).verdict, "too_little");
        assert_eq!(forecast_value_added(&[]).verdict, "no_data");
    }

    #[test]
    fn groups_come_out_best_first_and_undefined_last() {
        let mk = |k: &str, base: f64, actual: f64| (k.to_string(), FvaPoint { base, pct_bp: 1000, actual });
        let items = vec![
            mk("flat", 100.0, 100.0), mk("flat", 100.0, 100.0), // base error 0: improvement undefined
            mk("good", 100.0, 110.0), mk("bad", 100.0, 90.0),
        ];
        let rows = forecast_value_added_by(&items, |i| i.0.clone(), |i| i.1.clone());
        assert_eq!(rows.iter().map(|r| r.0.as_str()).collect::<Vec<_>>(), vec!["good", "bad", "flat"]);
    }

    fn i(v: &Value) -> i64 {
        v.as_i64().unwrap()
    }

    #[test]
    fn matches_the_python_reference() {
        let doc: Value = serde_json::from_str(CASES).unwrap();
        // Consensus: exact.
        let mut n_lines = 0;
        for (idx, case) in doc["consensus"].as_array().unwrap().iter().enumerate() {
            let c = &case["config"];
            let w = &c["weights"];
            let config = Config {
                rule: Rule::parse(c["rule"].as_str().unwrap()).unwrap(),
                priority: {
                    let p = c["priority"].as_array().unwrap();
                    [0, 1, 2].map(|k| function_index(p[k].as_str().unwrap()).unwrap())
                },
                weights: FUNCTIONS.map(|f| i(&w[f])),
                cap_down_bp: i(&c["cap_down_bp"]),
                cap_up_bp: i(&c["cap_up_bp"]),
            };
            let subs: Vec<Submission> = case["submissions"].as_array().unwrap().iter().map(|x| Submission {
                id: x["id"].as_str().unwrap().into(),
                sku: x["sku"].as_str().unwrap().into(),
                function: function_index(x["function"].as_str().unwrap()).unwrap(),
                start: i(&x["start"]),
                end: i(&x["end"]),
                pct_bp: i(&x["pct_bp"]),
            }).collect();
            let got = consensus_lines(&config, &subs).unwrap();
            let want = case["lines"].as_array().unwrap();
            assert_eq!(got.len(), want.len(), "case {idx}: number of lines");
            for (g, w) in got.iter().zip(want) {
                let ctx = format!("case {idx} sku {} {}..{}", g.sku, g.start, g.end);
                assert_eq!(g.sku, w["sku"].as_str().unwrap(), "{ctx}");
                assert_eq!((g.start, g.end, g.pct_bp), (i(&w["start"]), i(&w["end"]), i(&w["pct_bp"])), "{ctx}");
                assert_eq!(g.source.map(|f| FUNCTIONS[f]), w["source"].as_str(), "{ctx}: source");
                let wi = w["inputs"].as_array().unwrap();
                assert_eq!(g.inputs.len(), wi.len(), "{ctx}: inputs");
                for (gi, wi) in g.inputs.iter().zip(wi) {
                    assert_eq!(FUNCTIONS[gi.function], wi["function"].as_str().unwrap(), "{ctx}");
                    assert_eq!(gi.pct_bp, i(&wi["pct_bp"]), "{ctx}");
                    assert_eq!(gi.submission_id, wi["submission_id"].as_str().unwrap(), "{ctx}");
                    assert_eq!(gi.capped, wi["capped"].as_bool().unwrap(), "{ctx}");
                }
                n_lines += 1;
            }
        }
        assert!(n_lines > 500, "the fixture must exercise real consensus lines, got {n_lines}");

        // Forecast value added: bit for bit.
        let same = |got: &Fva, want: &Value, ctx: &str| {
            assert_eq!(got.to_json(), normalise(want), "{ctx}");
        };
        let mut graded = 0;
        for (idx, case) in doc["fva"].as_array().unwrap().iter().enumerate() {
            let pts: Vec<(String, String, FvaPoint)> = case["points"].as_array().unwrap().iter().map(|p| (
                p["function"].as_str().unwrap().to_string(),
                p["reason"].as_str().unwrap().to_string(),
                FvaPoint { base: p["base"].as_f64().unwrap(), pct_bp: i(&p["pct_bp"]), actual: p["actual"].as_f64().unwrap() },
            )).collect();
            let plain: Vec<FvaPoint> = pts.iter().map(|p| p.2.clone()).collect();
            same(&forecast_value_added(&plain), &case["aggregate"], &format!("fva {idx} aggregate"));
            for (key, pick) in [("by_function", 0usize), ("by_reason", 1usize)] {
                let got = forecast_value_added_by(&pts, |p| if pick == 0 { p.0.clone() } else { p.1.clone() }, |p| p.2.clone());
                let want = case[key].as_array().unwrap();
                assert_eq!(got.len(), want.len(), "fva {idx} {key}");
                let label = if pick == 0 { "function" } else { "reason" };
                for (g, w) in got.iter().zip(want) {
                    assert_eq!(g.0, w[label].as_str().unwrap(), "fva {idx} {key} order");
                    let mut w = w.clone();
                    w.as_object_mut().unwrap().remove(label);
                    same(&g.1, &w, &format!("fva {idx} {key} {}", g.0));
                    graded += 1;
                }
            }
        }
        assert!(graded > 200, "the fixture must exercise real groups, got {graded}");
    }

    /// Python writes `0.0` for a float and `3` for an int; the Rust side holds
    /// counts as integers and measures as floats. Compare the numbers, not the
    /// spelling of the JSON.
    fn normalise(v: &Value) -> Value {
        match v {
            Value::Object(m) => Value::Object(m.iter().map(|(k, x)| (k.clone(), normalise(x))).collect()),
            Value::Number(n) if n.is_f64() => json!(n.as_f64().unwrap()),
            other => other.clone(),
        }
    }
}
