//! `GET /api/v1/models` (backend/api/v1/models.py): the static catalogue of
//! model names. Unauthenticated in Python (no dependency at all), so it is
//! here too: an `Authorization` header of any kind, even a broken one, is
//! ignored, and no key check or metering runs.

use axum::Json;
use serde_json::{json, Value};

use crate::routes::ok;

/// `_MODELS`, in order. The dashes are U+2014, exactly as in the Python
/// source (including the two inside words: "high\u{2014}accuracy",
/// "non\u{2014}linear").
fn catalogue() -> Value {
    let m = |name: &str, category: &str, status: &str, description: &str| {
        json!({"name": name, "category": category, "status": status, "description": description})
    };
    json!([
        m("global_lgbm", "Global", "available",
          "Cross-learning model \u{2014} one fit across the whole catalogue, so short and new SKUs borrow the seasonality of the rest"),
        m("lightgbm", "ML", "available",
          "Gradient boosted trees \u{2014} fast, high\u{2014}accuracy for tabular data"),
        m("xgboost", "ML", "available",
          "Extreme gradient boosting \u{2014} strong baseline for structured series"),
        m("prophet", "Statistical", "available",
          "Facebook Prophet \u{2014} trend + seasonality decomposition"),
        m("arima", "Statistical", "available",
          "ARIMA \u{2014} classical statistical model for stationary series"),
        m("sarimax", "Statistical", "available",
          "SARIMAX \u{2014} seasonal ARIMA that can also read external drivers such as price or promotions"),
        m("ets", "Statistical", "available",
          "Exponential smoothing \u{2014} simple, robust seasonal decomposition"),
        m("croston", "Statistical", "available",
          "Croston's method \u{2014} specialized for intermittent/sparse demand"),
        json!({
            "name": "tsb",
            "category": "Statistical",
            "status": "available",
            "description": "TSB (Teunter-Syntetos-Babai) \u{2014} intermittent demand whose forecast decays when a product stops selling",
            "recommended_for": "Discontinued or end-of-life products and intermittent demand; not selected by default",
        }),
        m("lstm", "Deep Learning", "beta",
          "LSTM \u{2014} deep learning for complex non\u{2014}linear patterns"),
    ])
}

pub async fn list_models() -> Json<Value> {
    ok(catalogue())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The catalogue must not drift from the Python list it copies.
    #[test]
    fn names_match_the_python_source() {
        let src = include_str!("../../../../backend/api/v1/models.py");
        let py_names: Vec<&str> = src
            .lines()
            .filter_map(|l| l.trim().strip_prefix("\"name\":"))
            .map(|r| r.trim().trim_end_matches(',').trim_matches('"'))
            .collect();
        let rs = catalogue();
        let rs_names: Vec<&str> = rs.as_array().unwrap().iter().map(|m| m["name"].as_str().unwrap()).collect();
        assert_eq!(py_names, rs_names);
    }
}
