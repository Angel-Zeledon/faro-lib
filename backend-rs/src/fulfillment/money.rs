//! Money at risk: the arithmetic, and nothing else.
//!
//! For a commitment the outlook calls `will_miss` or `at_risk`: the shortfall in
//! units times the unit selling price, and (when the unit cost is known) the
//! margin on those units. Whole-number arithmetic on exact decimals: units are
//! counted in hundredths, prices in ten-thousandths, amounts in cents, so no
//! float ever multiplies another float and a total is the exact sum of the rows
//! on screen.
//!
//! The same rules are written a second time, in the plainest Python with the
//! `decimal` module, in `tests/contract/money_reference.py`;
//! `differential_against_python` below replays thousands of seeded cases
//! (`tests/fixtures/money_cases.json`, made by
//! `tests/contract/gen_money_fixtures.py`) and demands EXACT equality.
//!
//! The rules (also in the Python module's header):
//!
//! * Only `at_risk` and `will_miss` rows are eligible; others are `NotApplicable`.
//! * Units = the shortfall rounded to 2 decimals, half to even on the float's
//!   exact binary value. No shortfall figure: `NoShortfall`, excluded.
//! * Price and cost round to 4 decimals the same way. Missing, not finite, not
//!   above zero (or rounding to zero) or above 1e9 is NOT AVAILABLE: a zero is
//!   not a price. No price: `NoPrice`, excluded, counted.
//! * amount = units x price, to cents half up (ties away from zero); margin =
//!   units x (price - cost), signed, to cents half away from zero. No cost: the
//!   amount counts, the margin does not.

pub const UNITS_PLACES: usize = 2;
pub const PRICE_PLACES: usize = 4;
pub const MAX_UNITS: f64 = 1e12;
pub const MAX_PRICE: f64 = 1e9;

/// units(1e-2) x price(1e-4) is 1e-6 of a currency unit; cents are 1e-2.
const MICRO_PER_CENT: i128 = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    NotApplicable,
    NoShortfall,
    NoPrice,
    Computed,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Status::NotApplicable => "not_applicable",
            Status::NoShortfall => "no_shortfall",
            Status::NoPrice => "no_price",
            Status::Computed => "computed",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MarginStatus {
    NotApplicable,
    NoShortfall,
    NoPrice,
    NoCost,
    Computed,
}

impl MarginStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            MarginStatus::NotApplicable => "not_applicable",
            MarginStatus::NoShortfall => "no_shortfall",
            MarginStatus::NoPrice => "no_price",
            MarginStatus::NoCost => "no_cost",
            MarginStatus::Computed => "computed",
        }
    }
}

/// What the commitment's own contract says about its SKU's unit price.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ContractPrice {
    /// Not a contract commitment, or the contract line carries no price.
    Absent,
    /// The contract line's price (it may still be unusable, e.g. zero).
    Set(f64),
    /// The contract names the SKU more than once with different prices, or a
    /// price that is not a number: nobody can say which one applies.
    Conflict,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PriceSource {
    Contract,
    Sku,
}

impl PriceSource {
    pub fn as_str(self) -> &'static str {
        match self {
            PriceSource::Contract => "contract",
            PriceSource::Sku => "sku",
        }
    }
}

/// The unit selling price of a commitment: the contract's price when the
/// contract states one, else the SKU's price. A contract price that exists but
/// cannot be used is NOT replaced by the SKU price (that would quote the
/// customer a number the customer never agreed to); it is not available.
pub fn resolve_price(contract: ContractPrice, sku_price: Option<f64>) -> (Option<f64>, Option<PriceSource>) {
    match contract {
        ContractPrice::Set(p) => (Some(p), Some(PriceSource::Contract)),
        ContractPrice::Conflict => (None, None),
        ContractPrice::Absent => match sku_price {
            Some(p) => (Some(p), Some(PriceSource::Sku)),
            None => (None, None),
        },
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Money {
    pub status: Status,
    pub margin_status: MarginStatus,
    pub amount_cents: Option<i128>,
    pub margin_cents: Option<i128>,
    /// The shortfall is a lower bound, so the amount is "at least".
    pub is_minimum: bool,
}

/// What the row needs: its verdict and shortfall (from the outlook) and the
/// unit price and cost the data layer found, when it found them.
#[derive(Clone, Copy, Debug)]
pub struct Input {
    pub eligible: bool,
    pub shortfall: Option<f64>,
    pub shortfall_is_minimum: bool,
    pub price: Option<f64>,
    pub cost: Option<f64>,
}

/// `x` rounded half to even to `places` decimals, as a count of that unit. The
/// standard library formats a float with correct rounding of its exact value
/// (ties to even), the same as `Decimal(x).quantize(..., ROUND_HALF_EVEN)`.
/// None when `x` is not finite, negative or above `cap`.
pub fn scaled(x: f64, places: usize, cap: f64) -> Option<i128> {
    if !x.is_finite() || x < 0.0 || x > cap {
        return None;
    }
    let s = format!("{:.*}", places, x.abs());
    let (int, frac) = s.split_once('.')?;
    let mut digits = String::with_capacity(int.len() + frac.len());
    digits.push_str(int);
    digits.push_str(frac);
    digits.parse::<i128>().ok()
}

/// A usable price or cost in ten-thousandths: None for missing, zero or worse.
pub fn positive_price(x: Option<f64>) -> Option<i128> {
    match scaled(x?, PRICE_PLACES, MAX_PRICE) {
        Some(0) | None => None,
        some => some,
    }
}

/// Divide by 10,000, rounding half away from zero.
fn to_cents(micro: i128) -> i128 {
    let half = MICRO_PER_CENT / 2;
    if micro >= 0 {
        (micro + half) / MICRO_PER_CENT
    } else {
        -((-micro + half) / MICRO_PER_CENT)
    }
}

pub fn money(i: &Input) -> Money {
    let out = |status, margin_status| Money {
        status,
        margin_status,
        amount_cents: None,
        margin_cents: None,
        is_minimum: false,
    };
    if !i.eligible {
        return out(Status::NotApplicable, MarginStatus::NotApplicable);
    }
    let Some(units) = i.shortfall.and_then(|s| scaled(s, UNITS_PLACES, MAX_UNITS)) else {
        return out(Status::NoShortfall, MarginStatus::NoShortfall);
    };
    let Some(price) = positive_price(i.price) else {
        return out(Status::NoPrice, MarginStatus::NoPrice);
    };
    let (margin_cents, margin_status) = match positive_price(i.cost) {
        Some(cost) => (Some(to_cents(units * (price - cost))), MarginStatus::Computed),
        None => (None, MarginStatus::NoCost),
    };
    Money {
        status: Status::Computed,
        margin_status,
        amount_cents: Some(to_cents(units * price)),
        margin_cents,
        is_minimum: i.shortfall_is_minimum,
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Totals {
    /// At-risk and will-miss rows.
    pub eligible: i64,
    pub computed: i64,
    pub excluded_no_price: i64,
    pub excluded_no_shortfall: i64,
    pub amount_cents: i128,
    pub has_minimum: bool,
    pub margin_rows: i64,
    pub margin_cents: i128,
    /// Eligible rows with no margin figure (no price, no shortfall or no cost).
    pub margin_excluded: i64,
}

pub fn rollup(rows: &[Money]) -> Totals {
    let mut t = Totals::default();
    for r in rows {
        if r.status == Status::NotApplicable {
            continue;
        }
        t.eligible += 1;
        match r.status {
            Status::Computed => {
                t.computed += 1;
                t.amount_cents += r.amount_cents.unwrap_or(0);
                t.has_minimum |= r.is_minimum;
            }
            Status::NoPrice => t.excluded_no_price += 1,
            _ => t.excluded_no_shortfall += 1,
        }
        match (r.margin_status, r.margin_cents) {
            (MarginStatus::Computed, Some(m)) => {
                t.margin_rows += 1;
                t.margin_cents += m;
            }
            _ => t.margin_excluded += 1,
        }
    }
    t
}

/// Exact decimal text of a number of cents: -5 is "-0.05".
pub fn fmt_cents(c: i128) -> String {
    let sign = if c < 0 { "-" } else { "" };
    let a = c.unsigned_abs();
    format!("{sign}{}.{:02}", a / 100, a % 100)
}

/// A price or cost the way it is used: 4 decimals, exact text. None when it is
/// not available under the rules above.
pub fn fmt_price(x: Option<f64>) -> Option<String> {
    let p = positive_price(x)?;
    Some(format!("{}.{:04}", p / 10_000, p % 10_000))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    const FIXTURES: &str = include_str!("../../tests/fixtures/money_cases.json");

    fn inp(eligible: bool, shortfall: Option<f64>, price: Option<f64>, cost: Option<f64>) -> Input {
        Input { eligible, shortfall, shortfall_is_minimum: false, price, cost }
    }

    #[test]
    fn rounding_is_half_even_on_the_exact_value() {
        assert_eq!(scaled(0.125, 2, MAX_UNITS), Some(12));
        assert_eq!(scaled(0.375, 2, MAX_UNITS), Some(38));
        // 2.675 is stored a hair below, so it rounds down: exact, not "decimal looking".
        assert_eq!(scaled(2.675, 2, MAX_UNITS), Some(267));
        assert_eq!(scaled(-0.0, 2, MAX_UNITS), Some(0));
    }

    #[test]
    fn out_of_range_and_non_numbers_are_refused() {
        assert_eq!(scaled(f64::NAN, 2, MAX_UNITS), None);
        assert_eq!(scaled(f64::INFINITY, 2, MAX_UNITS), None);
        assert_eq!(scaled(-1.0, 2, MAX_UNITS), None);
        assert_eq!(scaled(1e12 + 1.0, 2, MAX_UNITS), None);
        assert_eq!(scaled(1e12, 2, MAX_UNITS), Some(100_000_000_000_000));
    }

    #[test]
    fn a_zero_or_missing_price_is_not_a_price() {
        assert_eq!(positive_price(None), None);
        assert_eq!(positive_price(Some(0.0)), None);
        assert_eq!(positive_price(Some(-3.0)), None);
        assert_eq!(positive_price(Some(0.00004)), None, "rounds to zero");
        assert_eq!(positive_price(Some(0.0001)), Some(1));
        assert_eq!(positive_price(Some(1.5)), Some(15_000));
    }

    #[test]
    fn amount_and_margin_by_hand() {
        // 40 units short at 12.50, cost 8.00: 500.00 at risk, 180.00 of margin.
        let m = money(&inp(true, Some(40.0), Some(12.5), Some(8.0)));
        assert_eq!(m.status, Status::Computed);
        assert_eq!(m.amount_cents, Some(50_000));
        assert_eq!(m.margin_cents, Some(18_000));
        assert_eq!(m.margin_status, MarginStatus::Computed);
        assert_eq!(fmt_cents(50_000), "500.00");
    }

    #[test]
    fn units_are_rounded_to_hundredths_before_they_multiply() {
        // 0.333 units show as 0.33; 0.33 x 10.00 = 3.30 (not 3.33).
        let m = money(&inp(true, Some(0.333), Some(10.0), None));
        assert_eq!(m.amount_cents, Some(330));
    }

    #[test]
    fn cents_round_half_up() {
        // 0.01 x 0.50 = 0.005 exactly -> 1 cent (half up); 0.01 x 0.4999 -> 0.
        assert_eq!(money(&inp(true, Some(0.01), Some(0.5), None)).amount_cents, Some(1));
        assert_eq!(money(&inp(true, Some(0.01), Some(0.4999), None)).amount_cents, Some(0));
    }

    #[test]
    fn a_cost_above_the_price_is_a_negative_margin_not_a_hidden_one() {
        let m = money(&inp(true, Some(10.0), Some(5.0), Some(7.5)));
        assert_eq!(m.margin_cents, Some(-2_500));
        assert_eq!(fmt_cents(-2_500), "-25.00");
        assert_eq!(fmt_cents(-5), "-0.05");
        // Negative ties round away from zero: -0.005 -> -1 cent.
        assert_eq!(to_cents(-5_000), -1);
        assert_eq!(to_cents(-4_999), 0);
    }

    #[test]
    fn missing_price_is_excluded_never_zero() {
        let m = money(&inp(true, Some(40.0), None, Some(8.0)));
        assert_eq!(m.status, Status::NoPrice);
        assert_eq!(m.amount_cents, None);
        assert_eq!(m.margin_status, MarginStatus::NoPrice);
        let z = money(&inp(true, Some(40.0), Some(0.0), None));
        assert_eq!(z.status, Status::NoPrice);
    }

    #[test]
    fn missing_cost_keeps_the_amount_and_drops_the_margin() {
        let m = money(&inp(true, Some(4.0), Some(2.0), None));
        assert_eq!(m.amount_cents, Some(800));
        assert_eq!(m.margin_cents, None);
        assert_eq!(m.margin_status, MarginStatus::NoCost);
    }

    #[test]
    fn no_shortfall_figure_and_ineligible_rows_are_excluded() {
        assert_eq!(money(&inp(true, None, Some(2.0), Some(1.0))).status, Status::NoShortfall);
        assert_eq!(money(&inp(false, Some(9.0), Some(2.0), Some(1.0))).status, Status::NotApplicable);
        // Zero units short is a real zero, and still needs a price to be stated.
        let z = money(&inp(true, Some(0.0), Some(2.0), None));
        assert_eq!((z.status, z.amount_cents), (Status::Computed, Some(0)));
    }

    #[test]
    fn minimum_flag_travels_to_the_total() {
        let mut a = inp(true, Some(3.0), Some(1.0), None);
        a.shortfall_is_minimum = true;
        let rows = [money(&a), money(&inp(true, Some(2.0), Some(1.0), None))];
        let t = rollup(&rows);
        assert!(t.has_minimum);
        assert_eq!(t.amount_cents, 500);
    }

    #[test]
    fn rollup_counts_what_it_excluded() {
        let rows = [
            money(&inp(true, Some(10.0), Some(2.0), Some(1.0))),   // 20.00, margin 10.00
            money(&inp(true, Some(10.0), None, Some(1.0))),        // no price
            money(&inp(true, Some(10.0), Some(3.0), None)),        // 30.00, no cost
            money(&inp(true, None, Some(3.0), None)),              // no shortfall
            money(&inp(false, Some(10.0), Some(3.0), None)),       // not eligible
        ];
        let t = rollup(&rows);
        assert_eq!(
            (t.eligible, t.computed, t.excluded_no_price, t.excluded_no_shortfall),
            (4, 2, 1, 1)
        );
        assert_eq!(t.amount_cents, 5_000);
        assert_eq!((t.margin_rows, t.margin_cents, t.margin_excluded), (1, 1_000, 3));
    }

    #[test]
    fn empty_rollup_is_a_real_zero_with_nothing_excluded() {
        let t = rollup(&[]);
        assert_eq!((t.eligible, t.amount_cents, t.excluded_no_price), (0, 0, 0));
    }

    #[test]
    fn the_contract_price_wins_and_is_never_silently_replaced() {
        use ContractPrice::*;
        assert_eq!(resolve_price(Set(9.0), Some(12.0)), (Some(9.0), Some(PriceSource::Contract)));
        assert_eq!(resolve_price(Absent, Some(12.0)), (Some(12.0), Some(PriceSource::Sku)));
        assert_eq!(resolve_price(Absent, None), (None, None));
        assert_eq!(resolve_price(Conflict, Some(12.0)), (None, None));
        // A stated but unusable contract price stays unusable.
        let (p, src) = resolve_price(Set(0.0), Some(12.0));
        assert_eq!((positive_price(p), src), (None, Some(PriceSource::Contract)));
    }

    #[test]
    fn price_text_is_exact() {
        assert_eq!(fmt_price(Some(12.5)).as_deref(), Some("12.5000"));
        assert_eq!(fmt_price(Some(0.0)), None);
        assert_eq!(fmt_price(None), None);
    }

    fn pf(v: &Value) -> Option<f64> {
        v.as_str().map(|s| s.parse::<f64>().expect("float text"))
    }

    fn input_of(c: &Value) -> Input {
        let v = c["verdict"].as_str().unwrap();
        Input {
            eligible: v == "at_risk" || v == "will_miss",
            shortfall: pf(&c["shortfall"]),
            shortfall_is_minimum: c["minimum"].as_bool().unwrap(),
            price: pf(&c["price"]),
            cost: pf(&c["cost"]),
        }
    }

    #[test]
    fn differential_against_python() {
        let root: Value = serde_json::from_str(FIXTURES).expect("fixtures parse");
        assert_eq!(root["units_places"].as_u64(), Some(UNITS_PLACES as u64), "constants drifted");
        assert_eq!(root["price_places"].as_u64(), Some(PRICE_PLACES as u64), "constants drifted");

        let cases = root["cases"].as_array().unwrap();
        for (n, c) in cases.iter().enumerate() {
            let m = money(&input_of(c));
            let w = &c["expected"];
            let ctx = format!("case {n}: {c}");
            assert_eq!(m.status.as_str(), w["status"].as_str().unwrap(), "status {ctx}");
            assert_eq!(m.margin_status.as_str(), w["margin_status"].as_str().unwrap(), "margin status {ctx}");
            assert_eq!(m.is_minimum, w["is_minimum"].as_bool().unwrap(), "minimum {ctx}");
            assert_eq!(m.amount_cents.map(fmt_cents).as_deref(), w["amount"].as_str(), "amount {ctx}");
            assert_eq!(m.margin_cents.map(fmt_cents).as_deref(), w["margin"].as_str(), "margin {ctx}");
        }
        let computed = cases.iter().filter(|c| c["expected"]["status"] == "computed").count();
        assert!(cases.len() >= 6000 && computed >= 1500, "fixtures shrank: {} cases, {computed} computed", cases.len());

        let rollups = root["rollups"].as_array().unwrap();
        for (n, r) in rollups.iter().enumerate() {
            let ms: Vec<Money> = r["rows"].as_array().unwrap().iter().map(|c| money(&input_of(c))).collect();
            let t = rollup(&ms);
            let w = &r["expected"];
            let ctx = format!("rollup {n}: {r}");
            assert_eq!(t.eligible, w["eligible"].as_i64().unwrap(), "eligible {ctx}");
            assert_eq!(t.computed, w["computed"].as_i64().unwrap(), "computed {ctx}");
            assert_eq!(t.excluded_no_price, w["excluded_no_price"].as_i64().unwrap(), "no price {ctx}");
            assert_eq!(t.excluded_no_shortfall, w["excluded_no_shortfall"].as_i64().unwrap(), "no shortfall {ctx}");
            assert_eq!(fmt_cents(t.amount_cents), w["amount"].as_str().unwrap(), "amount {ctx}");
            assert_eq!(t.has_minimum, w["has_minimum"].as_bool().unwrap(), "has minimum {ctx}");
            assert_eq!(t.margin_rows, w["margin_rows"].as_i64().unwrap(), "margin rows {ctx}");
            assert_eq!(fmt_cents(t.margin_cents), w["margin"].as_str().unwrap(), "margin {ctx}");
            assert_eq!(t.margin_excluded, w["margin_excluded"].as_i64().unwrap(), "margin excluded {ctx}");
        }
        assert!(rollups.len() >= 1200);
    }
}
