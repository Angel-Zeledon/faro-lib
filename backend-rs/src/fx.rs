//! The multi-currency conversion core: exact decimal arithmetic and the one
//! rounding rule. The Rust twin of `backend/fx/reference.py`, which is the
//! spec; `tests/fx_vectors.json` (generated from the Python reference) and the
//! `fx-eval` subcommand (replayed by `tests/contract/fx_differential.py`) keep
//! the two equal.
//!
//! The rules (same numbering as the Python module):
//!
//! 1. Exact arithmetic. No float touches a conversion: a float that comes in
//!    (a JSON number, a database FLOAT) is first turned into the decimal its
//!    shortest round-trip text spells, which is what Python's `repr` prints.
//! 2. One rounding, at the end: `qty x unit_cost x rate` rounded ONCE to 2
//!    decimals, half up (ties away from zero; amounts are non-negative).
//! 3. The rate is looked up as of the document's date: the latest
//!    `effective_date` not after it. A future-dated rate is never used.
//! 4. A missing rate is never 1.0: [`resolve`] returns `None`.
//! 5. A line already in the base currency is not converted.
//! 6. History is not re-converted (the callers record the rate on the document).
//!
//! The number type is a digit string, not a machine integer: three in-range
//! factors (17 digits each, a 24-digit rate) overflow `i128`, and an overflow
//! that wraps would be a silent wrong total. Sizes here are tiny, so schoolbook
//! arithmetic on digits is fast enough and cannot overflow.

use chrono::NaiveDate;

/// Inputs must be below 1e30 and spell at most 40 decimals (shared with Python).
pub const MAX_INT_DIGITS: usize = 30;
pub const MAX_DECIMALS: i64 = 40;
/// A stored rate carries NUMERIC(24, 10).
pub const RATE_DECIMALS: u32 = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FxError {
    NotANumber,
    TooLarge,
    TooManyDecimals,
    RateOutOfRange,
    RateTooPrecise,
}

impl FxError {
    pub fn code(&self) -> &'static str {
        match self {
            FxError::NotANumber => "not_a_number",
            FxError::TooLarge => "too_large",
            FxError::TooManyDecimals => "too_many_decimals",
            FxError::RateOutOfRange => "rate_out_of_range",
            FxError::RateTooPrecise => "rate_too_precise",
        }
    }
}

/// A non-negative decimal: `digits / 10^scale`. `digits` is most significant
/// first with no leading zeros (zero is `[0]`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dec {
    digits: Vec<u8>,
    scale: u32,
}

fn strip_leading_zeros(mut d: Vec<u8>) -> Vec<u8> {
    let first = d.iter().position(|&x| x != 0).unwrap_or(d.len());
    d.drain(..first);
    if d.is_empty() {
        d.push(0);
    }
    d
}

impl Dec {
    pub fn zero() -> Dec {
        Dec { digits: vec![0], scale: 0 }
    }

    fn is_zero(&self) -> bool {
        self.digits == [0]
    }

    /// The shared grammar: optional `+`, `digits[.digits]` or `.digits`,
    /// optional exponent. No minus sign, no underscores, no `NaN`.
    pub fn parse(text: &str) -> Result<Dec, FxError> {
        let t = text.trim();
        let t = t.strip_prefix('+').unwrap_or(t);
        let (mant, exp) = match t.find(['e', 'E']) {
            Some(i) => (&t[..i], Some(&t[i + 1..])),
            None => (t, None),
        };
        let (int_part, frac_part) = match mant.split_once('.') {
            Some((a, b)) => (a, b),
            None => (mant, ""),
        };
        let all_digits = |s: &str| s.bytes().all(|b| b.is_ascii_digit());
        if !all_digits(int_part) || !all_digits(frac_part) || (int_part.is_empty() && frac_part.is_empty()) {
            return Err(FxError::NotANumber);
        }
        let exp_val: i64 = match exp {
            None => 0,
            Some(e) => {
                let (sign, body) = match e.strip_prefix('-') {
                    Some(b) => (-1i64, b),
                    None => (1i64, e.strip_prefix('+').unwrap_or(e)),
                };
                if body.is_empty() || !all_digits(body) {
                    return Err(FxError::NotANumber);
                }
                // Anything this far out is refused below anyway; cap before parsing.
                match body.parse::<i64>() {
                    Ok(v) if v <= 10_000 => sign * v,
                    _ => {
                        // 1e+huge is too large; 1e-huge has too many decimals.
                        return Err(if sign > 0 { FxError::TooLarge } else { FxError::TooManyDecimals });
                    }
                }
            }
        };
        let mut digits: Vec<u8> = int_part.bytes().chain(frac_part.bytes()).map(|b| b - b'0').collect();
        // Python's Decimal exponent is exp - len(frac); the decimals check is on that.
        let decimals = frac_part.len() as i64 - exp_val;
        if decimals > MAX_DECIMALS {
            return Err(FxError::TooManyDecimals);
        }
        let digits_clean = strip_leading_zeros(std::mem::take(&mut digits));
        let zero = digits_clean == [0];
        let mut scale = decimals.max(0) as u32;
        let mut digits = digits_clean;
        if decimals < 0 && !zero {
            // A positive exponent: append zeros, after checking the size so a
            // huge exponent cannot allocate.
            let int_digits = digits.len() as i64 + (-decimals);
            if int_digits > MAX_INT_DIGITS as i64 {
                return Err(FxError::TooLarge);
            }
            digits.extend(std::iter::repeat(0).take((-decimals) as usize));
            scale = 0;
        }
        let d = Dec { digits, scale: if zero { 0 } else { scale } };
        if d.int_digit_count() > MAX_INT_DIGITS {
            return Err(FxError::TooLarge);
        }
        Ok(d)
    }

    /// Digits of the integer part (0 when the value is below 1).
    fn int_digit_count(&self) -> usize {
        let n = self.digits.len() as i64 - self.scale as i64;
        if self.is_zero() || n <= 0 { 0 } else { n as usize }
    }

    /// A float as the decimal its shortest round-trip text spells.
    pub fn from_f64(x: f64) -> Result<Dec, FxError> {
        if !x.is_finite() || x < 0.0 {
            return Err(FxError::NotANumber);
        }
        if x == 0.0 {
            return Ok(Dec::zero());
        }
        // Display for f64 is the shortest text that round-trips and never uses
        // an exponent: the same digits as Python's repr.
        Dec::parse(&format!("{x}"))
    }

    pub fn from_i64(x: i64) -> Result<Dec, FxError> {
        if x < 0 {
            return Err(FxError::NotANumber);
        }
        Dec::parse(&x.to_string())
    }

    /// Exact product.
    pub fn mul(&self, other: &Dec) -> Dec {
        if self.is_zero() || other.is_zero() {
            return Dec::zero();
        }
        let (a, b) = (&self.digits, &other.digits);
        let mut acc = vec![0u32; a.len() + b.len()];
        for (i, &da) in a.iter().rev().enumerate() {
            for (j, &db) in b.iter().rev().enumerate() {
                acc[i + j] += da as u32 * db as u32;
            }
        }
        let mut carry = 0u32;
        for v in acc.iter_mut() {
            let t = *v + carry;
            *v = t % 10;
            carry = t / 10;
        }
        debug_assert_eq!(carry, 0);
        acc.reverse();
        Dec { digits: strip_leading_zeros(acc.into_iter().map(|x| x as u8).collect()), scale: self.scale + other.scale }
    }

    /// The one rounding: 2 decimals, half up. Returns the value at scale 2.
    pub fn round_money(&self) -> Dec {
        const TARGET: u32 = 2;
        if self.is_zero() {
            return Dec { digits: vec![0], scale: TARGET };
        }
        if self.scale <= TARGET {
            let mut d = self.digits.clone();
            d.extend(std::iter::repeat(0).take((TARGET - self.scale) as usize));
            return Dec { digits: d, scale: TARGET };
        }
        let drop = (self.scale - TARGET) as usize;
        let len = self.digits.len();
        // The first dropped digit decides half-up on a non-negative value.
        let first_dropped = if drop > len {
            0
        } else {
            self.digits[len - drop]
        };
        let mut kept: Vec<u8> = if drop >= len { vec![0] } else { self.digits[..len - drop].to_vec() };
        if first_dropped >= 5 {
            let mut i = kept.len();
            loop {
                if i == 0 {
                    kept.insert(0, 1);
                    break;
                }
                i -= 1;
                if kept[i] == 9 {
                    kept[i] = 0;
                } else {
                    kept[i] += 1;
                    break;
                }
            }
        }
        Dec { digits: strip_leading_zeros(kept), scale: TARGET }
    }

    /// Sum of two values that are both at scale 2 (rounded money).
    pub fn add_money(&self, other: &Dec) -> Dec {
        debug_assert!(self.scale == 2 && other.scale == 2);
        let (a, b) = (&self.digits, &other.digits);
        let n = a.len().max(b.len());
        let mut out = Vec::with_capacity(n + 1);
        let mut carry = 0u8;
        for k in 0..n {
            let x = if k < a.len() { a[a.len() - 1 - k] } else { 0 };
            let y = if k < b.len() { b[b.len() - 1 - k] } else { 0 };
            let t = x + y + carry;
            out.push(t % 10);
            carry = t / 10;
        }
        if carry > 0 {
            out.push(carry);
        }
        out.reverse();
        Dec { digits: strip_leading_zeros(out), scale: 2 }
    }

    /// Compare two decimals by value.
    pub fn cmp_value(&self, other: &Dec) -> std::cmp::Ordering {
        let sc = self.scale.max(other.scale);
        let a = self.padded(sc);
        let b = other.padded(sc);
        a.len().cmp(&b.len()).then_with(|| a.cmp(&b))
    }

    fn padded(&self, scale: u32) -> Vec<u8> {
        if self.is_zero() {
            return vec![0];
        }
        let mut d = self.digits.clone();
        d.extend(std::iter::repeat(0).take((scale - self.scale) as usize));
        d
    }

    /// Decimals the value really needs (trailing zeros do not count).
    pub fn significant_scale(&self) -> u32 {
        if self.is_zero() {
            return 0;
        }
        let mut s = self.scale;
        let mut end = self.digits.len();
        while s > 0 && end > 0 && self.digits[end - 1] == 0 {
            s -= 1;
            end -= 1;
        }
        s
    }

    /// Plain text with exactly `scale` decimals for a value at that scale
    /// ("1234.50"); for other scales the natural digits.
    pub fn to_text(&self) -> String {
        let mut s: Vec<u8> = self.digits.iter().map(|d| b'0' + d).collect();
        if self.scale == 0 {
            return String::from_utf8(s).unwrap_or_default();
        }
        let sc = self.scale as usize;
        while s.len() <= sc {
            s.insert(0, b'0');
        }
        let split = s.len() - sc;
        let (i, f) = s.split_at(split);
        format!("{}.{}", String::from_utf8_lossy(i), String::from_utf8_lossy(f))
    }

    /// Text with trailing zeros trimmed past `min` decimals; used for stored
    /// rates ("520.5", not "520.5000000000").
    pub fn to_text_trimmed(&self, min: u32) -> String {
        let keep = self.significant_scale().max(min);
        let mut d = self.clone();
        if d.scale > keep {
            let cut = (d.scale - keep) as usize;
            d.digits.truncate(d.digits.len() - cut);
            d.scale = keep;
            if d.digits.is_empty() {
                d.digits.push(0);
            }
        }
        d.to_text()
    }
}

/// `convert`: `amount x rate`, rounded once.
pub fn convert(amount: &Dec, rate: &Dec) -> Dec {
    amount.mul(rate).round_money()
}

/// `convert_line`: `qty x unit_cost x rate`, exact until the single rounding.
pub fn convert_line(qty: &Dec, unit_cost: &Dec, rate: &Dec) -> Dec {
    qty.mul(unit_cost).mul(rate).round_money()
}

/// `base_line_value`: a base-currency line at the precision totals use.
pub fn base_line_value(qty: &Dec, unit_cost: &Dec) -> Dec {
    qty.mul(unit_cost).round_money()
}

/// Exact sum of 2-decimal values.
pub fn total(values: &[Dec]) -> Dec {
    values.iter().fold(Dec { digits: vec![0], scale: 2 }, |acc, v| acc.add_money(v))
}

/// A user-entered rate: positive, at most 10 decimals (refused, not rounded),
/// within [1e-10, 1e9].
pub fn parse_rate(text: &str) -> Result<Dec, FxError> {
    let d = Dec::parse(text)?;
    let min = Dec::parse("0.0000000001")?;
    let max = Dec::parse("1000000000")?;
    if d.cmp_value(&min) == std::cmp::Ordering::Less || d.cmp_value(&max) == std::cmp::Ordering::Greater {
        return Err(FxError::RateOutOfRange);
    }
    if d.significant_scale() > RATE_DECIMALS {
        return Err(FxError::RateTooPrecise);
    }
    Ok(d)
}

/// One stored rate, as the resolver needs it.
#[derive(Debug, Clone)]
pub struct RateRow {
    pub id: String,
    pub currency: String,
    pub base_currency: String,
    pub effective_date: NaiveDate,
}

/// `resolve_rate`: the row in force on `as_of` for currency -> base, or `None`.
/// The latest `effective_date` that is not after `as_of` wins; the first of
/// equal dates (impossible under the table's unique key) is kept.
pub fn resolve<'a>(rates: &'a [RateRow], currency: &str, base: &str, as_of: NaiveDate) -> Option<&'a RateRow> {
    let mut best: Option<&RateRow> = None;
    for r in rates {
        if r.currency != currency || r.base_currency != base || r.effective_date > as_of {
            continue;
        }
        if best.map_or(true, |b| r.effective_date > b.effective_date) {
            best = Some(r);
        }
    }
    best
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> Dec {
        Dec::parse(s).unwrap()
    }

    #[test]
    fn parsing_follows_the_shared_grammar() {
        assert_eq!(d("0.1").to_text(), "0.1");
        assert_eq!(d("+5.").to_text(), "5");
        assert_eq!(d(".5").to_text(), "0.5");
        assert_eq!(d("1e2").to_text(), "100");
        assert_eq!(d("1.5E1").to_text(), "15");
        assert_eq!(d("1e-3").to_text(), "0.001");
        assert_eq!(d("  7  ").to_text(), "7");
        for bad in ["", ".", "-1", "1_000", "NaN", "Infinity", "1e", "e5", "--1", "1.2.3", "0x10", "1e+"] {
            assert_eq!(Dec::parse(bad), Err(FxError::NotANumber), "{bad}");
        }
        assert_eq!(Dec::parse("1e30"), Err(FxError::TooLarge));
        assert_eq!(Dec::parse("1000000000000000000000000000000"), Err(FxError::TooLarge));
        assert!(Dec::parse("999999999999999999999999999999").is_ok());
        assert_eq!(Dec::parse("1e-41"), Err(FxError::TooManyDecimals));
        assert!(Dec::parse("1e-40").is_ok());
        assert_eq!(Dec::parse("1e999999999999"), Err(FxError::TooLarge));
        assert_eq!(Dec::parse("1e-999999999999"), Err(FxError::TooManyDecimals));
    }

    #[test]
    fn floats_go_through_their_shortest_text() {
        // 0.1 is one tenth, not 0.1000000000000000055...
        assert_eq!(Dec::from_f64(0.1).unwrap().to_text(), "0.1");
        assert_eq!(Dec::from_f64(2.675).unwrap().to_text(), "2.675");
        assert_eq!(Dec::from_f64(0.0).unwrap().to_text(), "0");
        assert_eq!(Dec::from_f64(-0.0).unwrap().to_text(), "0");
        assert!(Dec::from_f64(f64::NAN).is_err());
        assert!(Dec::from_f64(f64::INFINITY).is_err());
        assert!(Dec::from_f64(-1.0).is_err());
        assert_eq!(Dec::from_f64(1e-7).unwrap().to_text(), "0.0000001");
    }

    #[test]
    fn the_one_rounding_is_half_up() {
        let r = |s: &str| d(s).round_money().to_text();
        assert_eq!(r("2.675"), "2.68"); // exact decimal: a tie goes up
        assert_eq!(r("2.665"), "2.67");
        assert_eq!(r("0.005"), "0.01");
        assert_eq!(r("0.004999999"), "0.00");
        assert_eq!(r("0.0049"), "0.00");
        assert_eq!(r("9.995"), "10.00");
        assert_eq!(r("99.999"), "100.00");
        assert_eq!(r("1"), "1.00");
        assert_eq!(r("0"), "0.00");
        assert_eq!(r("1234.5"), "1234.50");
        assert_eq!(r("0.5"), "0.50");
        assert_eq!(r("0.0000000001"), "0.00");
    }

    #[test]
    fn conversion_is_exact_until_the_single_rounding() {
        // 3 x 0.335 x 520.5: rounding each step would differ from rounding once.
        let v = convert_line(&d("3"), &d("0.335"), &d("520.5"));
        assert_eq!(v.to_text(), "523.10"); // 523.1025 exactly
        let per_step = d("0.335").mul(&d("520.5")).round_money(); // 174.37 (174.3675)
        assert_eq!(per_step.to_text(), "174.37");
        assert_eq!(per_step.mul(&d("3")).round_money().to_text(), "523.11"); // what early rounding would give
        // The classic float trap: 0.1 + 0.2 style totals are exact here.
        let t = total(&[d("0.10").round_money(), d("0.20").round_money()]);
        assert_eq!(t.to_text(), "0.30");
        assert_eq!(convert(&d("100"), &d("1")).to_text(), "100.00");
        assert_eq!(convert(&d("0"), &d("520.5")).to_text(), "0.00");
    }

    #[test]
    fn three_factors_that_overflow_i128_do_not_overflow_here() {
        // 29-digit qty x 29-digit cost x 20-digit rate: ~78 digits.
        let big = d("99999999999999999999999999999");
        let v = convert_line(&big, &big, &d("999999999.9999999999"));
        assert!(v.to_text().len() > 60, "{}", v.to_text());
        assert!(v.to_text().ends_with(".00"));
    }

    #[test]
    fn totals_are_exact() {
        let vals: Vec<Dec> = ["0.01", "0.02", "123456789.99", "0.99"].iter().map(|s| d(s).round_money()).collect();
        assert_eq!(total(&vals).to_text(), "123456791.01");
        assert_eq!(total(&[]).to_text(), "0.00");
        assert_eq!(total(&[d("99.99").round_money(), d("0.01").round_money()]).to_text(), "100.00");
    }

    #[test]
    fn rates_are_validated_not_rounded() {
        assert_eq!(parse_rate("520.5").unwrap().to_text_trimmed(0), "520.5");
        assert_eq!(parse_rate("0.0000000001").unwrap().to_text_trimmed(0), "0.0000000001");
        assert_eq!(parse_rate("1000000000").unwrap().to_text_trimmed(0), "1000000000");
        assert_eq!(parse_rate("0"), Err(FxError::RateOutOfRange));
        assert_eq!(parse_rate("0.00000000001"), Err(FxError::RateOutOfRange));
        assert_eq!(parse_rate("1000000000.0000000001"), Err(FxError::RateOutOfRange));
        assert_eq!(parse_rate("1.00000000001"), Err(FxError::RateTooPrecise));
        assert!(parse_rate("1.0000000000").is_ok()); // trailing zeros are not precision
        assert_eq!(parse_rate("-1"), Err(FxError::NotANumber));
        assert_eq!(parse_rate("abc"), Err(FxError::NotANumber));
        assert_eq!(parse_rate("520.50").unwrap().to_text_trimmed(2), "520.50");
    }

    fn row(id: &str, cur: &str, base: &str, date: &str) -> RateRow {
        RateRow {
            id: id.into(),
            currency: cur.into(),
            base_currency: base.into(),
            effective_date: NaiveDate::parse_from_str(date, "%Y-%m-%d").unwrap(),
        }
    }

    #[test]
    fn the_rate_in_force_is_the_latest_not_after_the_date() {
        let rates = vec![
            row("a", "USD", "CRC", "2026-01-01"),
            row("b", "USD", "CRC", "2026-03-01"),
            row("c", "USD", "CRC", "2026-06-01"),
            row("d", "EUR", "CRC", "2026-02-01"),
            row("e", "USD", "MXN", "2026-02-01"),
        ];
        let on = |s: &str| NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap();
        assert_eq!(resolve(&rates, "USD", "CRC", on("2026-03-01")).unwrap().id, "b"); // same day counts
        assert_eq!(resolve(&rates, "USD", "CRC", on("2026-05-31")).unwrap().id, "b");
        assert_eq!(resolve(&rates, "USD", "CRC", on("2026-12-31")).unwrap().id, "c");
        // Before the first rate: no rate, never the nearest future one.
        assert!(resolve(&rates, "USD", "CRC", on("2025-12-31")).is_none());
        // Another currency / another base never leaks in.
        assert_eq!(resolve(&rates, "EUR", "CRC", on("2026-02-01")).unwrap().id, "d");
        assert!(resolve(&rates, "EUR", "MXN", on("2026-12-31")).is_none());
        assert!(resolve(&rates, "GBP", "CRC", on("2026-12-31")).is_none());
    }

    /// The golden vectors are generated from the Python reference
    /// (`python -m backend.fx.vectors`); a Python test checks they are current.
    #[test]
    fn the_python_reference_vectors_are_reproduced_exactly() {
        let raw = include_str!("../tests/fx_vectors.json");
        let doc: serde_json::Value = serde_json::from_str(raw).unwrap();
        let cases = doc["cases"].as_array().unwrap();
        assert!(cases.len() >= 2000, "vectors file is too small to mean anything");
        let mut checked = 0;
        for c in cases {
            let got = crate::fx_eval::eval(c);
            assert_eq!(got, c["expect"], "case {c}");
            checked += 1;
        }
        assert_eq!(checked, cases.len());
    }
}
