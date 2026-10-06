//! The four consolidated, READ-ONLY views: committed demand, stock by signal,
//! purchase orders, budget vs spend.
//!
//! Every statement here is a constant that takes the entitled tenant ids as
//! `$1` (`tenant_id = ANY($1)`) and nothing the request sent; a test pins that
//! for each one. The pure functions below turn rows into the response, and carry
//! the rules that keep a consolidated number from lying:
//!
//! * money is NEVER added across currencies: totals are a list, one entry per
//!   currency;
//! * a stock-status snapshot is counted only when it is fresh by the rule
//!   Python's `status_snapshot.is_fresh` applies (inputs unchanged, computed
//!   today, under an hour old), and a snapshot whose row count disagrees with
//!   its meta is not trusted. A tenant that is not counted is LISTED with the
//!   reason, never folded in stale and never dropped silently;
//! * a figure that is missing a cost or a value says how many lines lack it;
//! * a budget in a currency other than its tenant's is shown but not totalled.
//!
//! Units from different tenants are summed as "units", but SKU codes are not
//! matched across tenants: two tenants' `SKU-1` are two products.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};

use super::scope::{Member, OrgScope};

// ── Committed demand ─────────────────────────────────────────────────────────

/// Open commitments per tenant. A withdrawn contract release is history and is
/// left out, as is anything not `open`.
pub const COMMITTED_TOTALS_SQL: &str = "\
SELECT tenant_id,
       COUNT(*)::bigint,
       COUNT(DISTINCT sku)::bigint,
       COALESCE(SUM(quantity), 0)::float8,
       COALESCE(SUM(quantity * probability), 0)::float8,
       COALESCE(SUM(quantity) FILTER (WHERE on_top_of_base), 0)::float8,
       COALESCE(SUM(quantity * probability) FILTER (WHERE on_top_of_base), 0)::float8,
       (COUNT(*) FILTER (WHERE delivery_date < CURRENT_DATE))::bigint,
       MIN(delivery_date)::text,
       MAX(delivery_date)::text
  FROM committed_demand
 WHERE tenant_id = ANY($1)
   AND status = 'open'
   AND contract_withdrawn_at IS NULL
 GROUP BY tenant_id";

pub const COMMITTED_MONTHS_SQL: &str = "\
SELECT tenant_id,
       to_char(delivery_date, 'YYYY-MM'),
       COUNT(*)::bigint,
       COALESCE(SUM(quantity), 0)::float8,
       COALESCE(SUM(quantity * probability), 0)::float8
  FROM committed_demand
 WHERE tenant_id = ANY($1)
   AND status = 'open'
   AND contract_withdrawn_at IS NULL
 GROUP BY tenant_id, to_char(delivery_date, 'YYYY-MM')
 ORDER BY 2, 1";

pub type CommittedTotalsRow = (String, i64, i64, f64, f64, f64, f64, i64, Option<String>, Option<String>);
pub type CommittedMonthRow = (String, String, i64, f64, f64);

pub fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

pub fn committed_demand(scope: &OrgScope, totals: Vec<CommittedTotalsRow>, months: Vec<CommittedMonthRow>) -> Value {
    let by_tenant: BTreeMap<&str, &CommittedTotalsRow> = totals.iter().map(|r| (r.0.as_str(), r)).collect();
    let mut tenants = Vec::new();
    let (mut n, mut qty, mut weighted, mut added, mut added_w, mut overdue) = (0i64, 0f64, 0f64, 0f64, 0f64, 0i64);
    for m in &scope.members {
        let mut o = member_header(m);
        match by_tenant.get(m.tenant_id.as_str()) {
            Some(r) => {
                n += r.1;
                qty += r.3;
                weighted += r.4;
                added += r.5;
                added_w += r.6;
                overdue += r.7;
                o.insert("commitments".into(), json!(r.1));
                o.insert("skus".into(), json!(r.2));
                o.insert("quantity".into(), json!(round2(r.3)));
                o.insert("weighted_quantity".into(), json!(round2(r.4)));
                o.insert("added_to_forecast_quantity".into(), json!(round2(r.5)));
                o.insert("added_to_forecast_weighted_quantity".into(), json!(round2(r.6)));
                o.insert("overdue".into(), json!(r.7));
                o.insert("earliest_delivery".into(), json!(r.8));
                o.insert("latest_delivery".into(), json!(r.9));
            }
            None => {
                for k in ["commitments", "skus", "overdue"] {
                    o.insert(k.into(), json!(0));
                }
                for k in [
                    "quantity",
                    "weighted_quantity",
                    "added_to_forecast_quantity",
                    "added_to_forecast_weighted_quantity",
                ] {
                    o.insert(k.into(), json!(0.0));
                }
                o.insert("earliest_delivery".into(), Value::Null);
                o.insert("latest_delivery".into(), Value::Null);
            }
        }
        tenants.push(Value::Object(o));
    }

    // Month roll-up across the covered tenants, each month with its split.
    let mut by_month: BTreeMap<&str, (i64, f64, f64, Vec<Value>)> = BTreeMap::new();
    for (tenant_id, month, count, q, w) in &months {
        let e = by_month.entry(month.as_str()).or_insert((0, 0.0, 0.0, Vec::new()));
        e.0 += count;
        e.1 += q;
        e.2 += w;
        e.3.push(json!({"tenant_id": tenant_id, "commitments": count, "quantity": round2(*q)}));
    }
    let months_json: Vec<Value> = by_month
        .into_iter()
        .map(|(month, (c, q, w, split))| {
            json!({"month": month, "commitments": c, "quantity": round2(q),
                   "weighted_quantity": round2(w), "tenants": split})
        })
        .collect();

    json!({
        "tenants": tenants,
        "totals": {
            "commitments": n,
            "quantity": round2(qty),
            "weighted_quantity": round2(weighted),
            "added_to_forecast_quantity": round2(added),
            "added_to_forecast_weighted_quantity": round2(added_w),
            "overdue": overdue,
        },
        "months": months_json,
        "unavailable": scope.unavailable_json(),
    })
}

fn member_header(m: &Member) -> Map<String, Value> {
    let mut o = Map::new();
    o.insert("tenant_id".into(), json!(m.tenant_id));
    o.insert("name".into(), json!(m.display()));
    o.insert("is_own".into(), json!(m.is_own));
    o
}

// ── Stock by signal ──────────────────────────────────────────────────────────

/// The newest generation each tenant has, how fresh it is, and its rows grouped
/// by signal. One row per (tenant, signal); `signal` is NULL for a generation
/// with no rows.
pub const STOCK_SIGNALS_SQL: &str = "\
WITH chosen AS (
  SELECT DISTINCT ON (m.tenant_id)
         m.tenant_id, m.session_id, m.period, m.service_level, m.generation,
         m.n_rows, m.computed_at,
         (m.inputs_version = COALESCE((SELECT MAX(b.id) FROM status_input_bumps b
                                        WHERE b.tenant_id = m.tenant_id), 0)
          AND m.computed_on = CURRENT_DATE
          AND NOW() - m.computed_at < INTERVAL '1 hour') AS fresh
    FROM inventory_status_snapshot_meta m
   WHERE m.tenant_id = ANY($1)
   ORDER BY m.tenant_id, m.computed_at DESC
)
SELECT c.tenant_id, c.session_id, c.period, c.service_level, c.n_rows, c.computed_at, c.fresh,
       s.signal,
       COUNT(s.sku)::bigint,
       COALESCE(SUM(s.inventory_value), 0)::float8,
       (COUNT(s.sku) FILTER (WHERE s.inventory_value IS NULL))::bigint
  FROM chosen c
  LEFT JOIN inventory_status_snapshot s
         ON s.tenant_id = c.tenant_id
        AND s.tenant_id = ANY($1)
        AND s.session_id = c.session_id
        AND s.period = c.period
        AND s.service_level = c.service_level
        AND s.generation = c.generation
 GROUP BY c.tenant_id, c.session_id, c.period, c.service_level, c.n_rows, c.computed_at, c.fresh, s.signal
 ORDER BY c.tenant_id, s.signal";

pub type StockRow = (String, String, String, f64, i32, DateTime<Utc>, bool, Option<String>, i64, f64, i64);

/// The persisted signal values (Spanish on purpose, see CLAUDE.md); any other
/// value found in a snapshot is reported under its own name.
pub const SIGNALS: [&str; 4] = ["PEDIR_YA", "PEDIR_PRONTO", "OK", "SOBRESTOCK"];

#[derive(Debug, PartialEq)]
pub enum StockState {
    Counted,
    Stale,
    Inconsistent,
    Missing,
}

impl StockState {
    pub fn as_str(&self) -> &'static str {
        match self {
            StockState::Counted => "counted",
            StockState::Stale => "stale",
            StockState::Inconsistent => "inconsistent",
            StockState::Missing => "missing",
        }
    }
}

/// Fresh by Python's rule AND the signal rows add up to the meta's `n_rows`.
pub fn stock_state(fresh: bool, rows_total: i64, n_rows: i32) -> StockState {
    if rows_total != i64::from(n_rows) {
        StockState::Inconsistent
    } else if !fresh {
        StockState::Stale
    } else {
        StockState::Counted
    }
}

/// Adds `value` to the entry of `currency` in a currency-keyed total.
pub fn add_money(totals: &mut BTreeMap<String, f64>, currency: &str, value: f64) {
    *totals.entry(currency.to_string()).or_insert(0.0) += value;
}

pub fn money_json(totals: &BTreeMap<String, f64>) -> Value {
    Value::Array(totals.iter().map(|(c, v)| json!({"currency": c, "amount": round2(*v)})).collect())
}

pub fn stock_signals(scope: &OrgScope, rows: Vec<StockRow>) -> Value {
    struct Acc {
        session_id: String,
        period: String,
        service_level: f64,
        n_rows: i32,
        computed_at: DateTime<Utc>,
        fresh: bool,
        signals: BTreeMap<String, i64>,
        value: f64,
        without_value: i64,
    }
    let mut acc: BTreeMap<String, Acc> = BTreeMap::new();
    for (tenant, session_id, period, service_level, n_rows, computed_at, fresh, signal, count, value, without) in rows {
        let a = acc.entry(tenant).or_insert_with(|| Acc {
            session_id,
            period,
            service_level,
            n_rows,
            computed_at,
            fresh,
            signals: SIGNALS.iter().map(|s| (s.to_string(), 0)).collect(),
            value: 0.0,
            without_value: 0,
        });
        if let Some(sig) = signal {
            *a.signals.entry(sig).or_insert(0) += count;
            a.value += value;
            a.without_value += without;
        }
    }

    let mut tenants = Vec::new();
    let mut excluded = Vec::new();
    let mut totals_signals: BTreeMap<String, i64> = SIGNALS.iter().map(|s| (s.to_string(), 0)).collect();
    let mut total_skus = 0i64;
    let mut total_without_value = 0i64;
    let mut value: BTreeMap<String, f64> = BTreeMap::new();
    for m in &scope.members {
        let mut o = member_header(m);
        match acc.get(&m.tenant_id) {
            Some(a) => {
                let rows_total: i64 = a.signals.values().sum();
                let state = stock_state(a.fresh, rows_total, a.n_rows);
                o.insert("state".into(), json!(state.as_str()));
                o.insert("counted".into(), json!(state == StockState::Counted));
                o.insert("computed_at".into(), json!(crate::pycompat::isoformat_utc(&a.computed_at)));
                o.insert("session_id".into(), json!(a.session_id));
                o.insert("period".into(), json!(a.period));
                o.insert("service_level".into(), json!(a.service_level));
                o.insert("skus".into(), json!(rows_total));
                o.insert("signals".into(), json!(a.signals));
                o.insert("inventory_value".into(), money_json(&BTreeMap::from([(m.currency.clone(), a.value)])));
                o.insert("skus_without_value".into(), json!(a.without_value));
                if state == StockState::Counted {
                    for (k, v) in &a.signals {
                        *totals_signals.entry(k.clone()).or_insert(0) += v;
                    }
                    total_skus += rows_total;
                    total_without_value += a.without_value;
                    add_money(&mut value, &m.currency, a.value);
                } else {
                    excluded.push(json!({"tenant_id": m.tenant_id, "name": m.display(), "reason": state.as_str()}));
                }
            }
            None => {
                o.insert("state".into(), json!(StockState::Missing.as_str()));
                o.insert("counted".into(), json!(false));
                excluded.push(json!({"tenant_id": m.tenant_id, "name": m.display(), "reason": "missing"}));
            }
        }
        tenants.push(Value::Object(o));
    }
    json!({
        "tenants": tenants,
        "totals": {
            "skus": total_skus,
            "signals": totals_signals,
            "inventory_value": money_json(&value),
            "skus_without_value": total_without_value,
            "tenants_counted": scope.members.len() - excluded.len(),
            "tenants_excluded": excluded.len(),
        },
        "excluded": excluded,
        "unavailable": scope.unavailable_json(),
    })
}

// ── Purchase orders ──────────────────────────────────────────────────────────

/// Orders generated in the last `$2` days, per tenant. `value` skips cancelled
/// orders; an order with no total is counted in `without_value`, never as zero.
pub const PURCHASE_ORDERS_SQL: &str = "\
SELECT tenant_id,
       COUNT(*)::bigint,
       (COUNT(*) FILTER (WHERE cancelled_at IS NOT NULL))::bigint,
       (COUNT(*) FILTER (WHERE cancelled_at IS NULL))::bigint,
       (COUNT(*) FILTER (WHERE cancelled_at IS NULL AND sent_at IS NOT NULL))::bigint,
       (COUNT(*) FILTER (WHERE cancelled_at IS NULL AND sent_at IS NULL))::bigint,
       (COUNT(*) FILTER (WHERE cancelled_at IS NULL AND paid_at IS NOT NULL))::bigint,
       (COUNT(*) FILTER (WHERE cancelled_at IS NULL AND sent_at IS NOT NULL AND paid_at IS NULL))::bigint,
       (COUNT(*) FILTER (WHERE cancelled_at IS NULL AND sent_at IS NOT NULL
                          AND COALESCE(reception_status, 'pending') IN ('pending', 'partial', 'not_received')))::bigint,
       COALESCE(SUM(total_value) FILTER (WHERE cancelled_at IS NULL), 0)::float8,
       (COUNT(*) FILTER (WHERE cancelled_at IS NULL AND total_value IS NULL))::bigint
  FROM inventory_po_log
 WHERE tenant_id = ANY($1)
   AND generated_at >= NOW() - ($2::bigint * INTERVAL '1 day')
 GROUP BY tenant_id";

pub type PoRow = (String, i64, i64, i64, i64, i64, i64, i64, i64, f64, i64);

pub fn purchase_orders(scope: &OrgScope, days: i64, rows: Vec<PoRow>) -> Value {
    let by_tenant: BTreeMap<&str, &PoRow> = rows.iter().map(|r| (r.0.as_str(), r)).collect();
    let mut tenants = Vec::new();
    let mut sums = [0i64; 8];
    let mut value: BTreeMap<String, f64> = BTreeMap::new();
    let mut without_value = 0i64;
    for m in &scope.members {
        let mut o = member_header(m);
        let names = ["orders", "cancelled", "active", "sent", "not_sent", "paid", "awaiting_payment", "awaiting_reception"];
        match by_tenant.get(m.tenant_id.as_str()) {
            Some(r) => {
                let counts = [r.1, r.2, r.3, r.4, r.5, r.6, r.7, r.8];
                for (i, (name, c)) in names.iter().zip(counts).enumerate() {
                    o.insert((*name).into(), json!(c));
                    sums[i] += c;
                }
                add_money(&mut value, &m.currency, r.9);
                without_value += r.10;
                o.insert("value".into(), money_json(&BTreeMap::from([(m.currency.clone(), r.9)])));
                o.insert("orders_without_value".into(), json!(r.10));
            }
            None => {
                for name in names {
                    o.insert(name.into(), json!(0));
                }
                o.insert("value".into(), money_json(&BTreeMap::from([(m.currency.clone(), 0.0)])));
                o.insert("orders_without_value".into(), json!(0));
            }
        }
        tenants.push(Value::Object(o));
    }
    let names = ["orders", "cancelled", "active", "sent", "not_sent", "paid", "awaiting_payment", "awaiting_reception"];
    let mut totals = Map::new();
    for (name, s) in names.iter().zip(sums) {
        totals.insert((*name).into(), json!(s));
    }
    totals.insert("value".into(), money_json(&value));
    totals.insert("orders_without_value".into(), json!(without_value));
    json!({
        "window_days": days,
        "tenants": tenants,
        "totals": Value::Object(totals),
        "unavailable": scope.unavailable_json(),
    })
}

// ── Budget vs spend ──────────────────────────────────────────────────────────

/// The running company-wide budgets (current revision, active) and what has
/// been ordered against each, by the rule `purchase_budget_service.usage` uses
/// for a company budget: lines of non-cancelled orders generated inside the
/// period, status approved or modified, `spent` = fully received orders,
/// `committed` = orders still open. A line without a unit cost is counted, never
/// valued at zero.
pub const BUDGETS_SQL: &str = "\
SELECT b.tenant_id, b.root_id, b.period_type, b.period_start::text, b.period_end::text,
       b.amount::float8, b.currency, b.hard_cap,
       COALESCE(SUM(i.final_qty * i.unit_cost) FILTER (
         WHERE i.unit_cost IS NOT NULL
           AND COALESCE(l.reception_status, 'pending') NOT IN ('pending', 'partial', 'not_received')), 0)::float8,
       COALESCE(SUM(i.final_qty * i.unit_cost) FILTER (
         WHERE i.unit_cost IS NOT NULL
           AND COALESCE(l.reception_status, 'pending') IN ('pending', 'partial', 'not_received')), 0)::float8,
       (COUNT(i.id) FILTER (WHERE i.unit_cost IS NULL))::bigint
  FROM purchase_budgets b
  LEFT JOIN inventory_po_log l
         ON l.tenant_id = b.tenant_id
        AND l.cancelled_at IS NULL
        AND l.generated_at >= b.period_start
        AND l.generated_at < (b.period_end + 1)
  LEFT JOIN inventory_po_items i
         ON i.po_log_id = l.id
        AND i.tenant_id = b.tenant_id
        AND i.status IN ('approved', 'modified')
 WHERE b.tenant_id = ANY($1)
   AND b.superseded_by IS NULL
   AND b.active
   AND b.scope_type = 'company'
   AND b.period_start <= CURRENT_DATE
   AND b.period_end >= CURRENT_DATE
 GROUP BY b.tenant_id, b.root_id, b.period_type, b.period_start, b.period_end, b.amount, b.currency, b.hard_cap
 ORDER BY b.tenant_id, b.period_start, b.root_id";

/// Running budgets scoped to a warehouse, supplier or category: not valued
/// here (their matching rules are the per-tenant screen's), but counted so the
/// consolidated view never claims to be the whole of a tenant's budgets.
pub const OTHER_SCOPE_BUDGETS_SQL: &str = "\
SELECT tenant_id, COUNT(*)::bigint
  FROM purchase_budgets
 WHERE tenant_id = ANY($1)
   AND superseded_by IS NULL
   AND active
   AND scope_type <> 'company'
   AND period_start <= CURRENT_DATE
   AND period_end >= CURRENT_DATE
 GROUP BY tenant_id";

pub type BudgetRow = (String, String, String, String, String, f64, String, bool, f64, f64, i64);

pub fn budgets(scope: &OrgScope, rows: Vec<BudgetRow>, other_scopes: Vec<(String, i64)>) -> Value {
    #[derive(Default)]
    struct Sum {
        amount: f64,
        spent: f64,
        committed: f64,
    }
    let mut totals: BTreeMap<String, Sum> = BTreeMap::new();
    let mut not_totalled = 0i64;
    let mut unknown_cost_total = 0i64;
    let mut tenants = Vec::new();
    for m in &scope.members {
        let mut o = member_header(m);
        let mut list = Vec::new();
        for (tenant, root_id, period_type, start, end, amount, currency, hard_cap, spent, committed, unknown) in
            rows.iter().filter(|r| r.0 == m.tenant_id)
        {
            let _ = tenant;
            // The order totals are in the tenant's own currency; a budget in
            // another one cannot be compared with them, so it is shown and not
            // totalled (Python warns `currency_mismatch` for the same case).
            let mismatch = currency != &m.currency;
            let (spent, committed) = (round2(*spent), round2(*committed));
            let remaining = round2(amount - spent - committed);
            if !mismatch {
                let s = totals.entry(currency.clone()).or_default();
                s.amount += amount;
                s.spent += spent;
                s.committed += committed;
                unknown_cost_total += unknown;
            } else {
                not_totalled += 1;
            }
            list.push(json!({
                "root_id": root_id,
                "period_type": period_type,
                "period_start": start,
                "period_end": end,
                "currency": currency,
                "amount": round2(*amount),
                "spent": spent,
                "committed": committed,
                "ordered": round2(spent + committed),
                "remaining": remaining,
                "over_budget": remaining < 0.0,
                "hard_cap": hard_cap,
                "unknown_cost_lines": unknown,
                "currency_mismatch": mismatch,
            }));
        }
        let others = other_scopes.iter().find(|(t, _)| *t == m.tenant_id).map(|(_, n)| *n).unwrap_or(0);
        o.insert("budgets".into(), Value::Array(list));
        o.insert("other_scope_budgets_not_included".into(), json!(others));
        tenants.push(Value::Object(o));
    }
    let totals_json: Vec<Value> = totals
        .iter()
        .map(|(c, s)| {
            let remaining = round2(s.amount - s.spent - s.committed);
            json!({"currency": c, "amount": round2(s.amount), "spent": round2(s.spent),
                   "committed": round2(s.committed), "remaining": remaining})
        })
        .collect();
    json!({
        "tenants": tenants,
        "totals": {
            "by_currency": totals_json,
            "unknown_cost_lines": unknown_cost_total,
            "budgets_not_totalled": not_totalled,
        },
        "unavailable": scope.unavailable_json(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::org::scope::{build_scope, Member};

    fn own() -> Member {
        Member { tenant_id: "P".into(), name: "Holding".into(), label: None, link_id: None, is_own: true, currency: "CRC".into() }
    }

    fn scope_with(currencies: &[(&str, &str)]) -> OrgScope {
        let rows = currencies
            .iter()
            .map(|(id, cur)| {
                (format!("l-{id}"), id.to_string(), format!("Sub {id}"), format!("name {id}"), "active".to_string(),
                 Some(cur.to_string()))
            })
            .collect();
        build_scope(own(), rows)
    }

    #[test]
    fn every_statement_binds_the_entitled_ids_and_nothing_is_built_from_text() {
        for (name, sql) in [
            ("committed totals", COMMITTED_TOTALS_SQL),
            ("committed months", COMMITTED_MONTHS_SQL),
            ("stock signals", STOCK_SIGNALS_SQL),
            ("purchase orders", PURCHASE_ORDERS_SQL),
            ("budgets", BUDGETS_SQL),
            ("other budgets", OTHER_SCOPE_BUDGETS_SQL),
        ] {
            assert!(sql.contains("= ANY($1)"), "{name} does not filter by the entitled tenant ids");
            assert!(sql.trim_start().starts_with("SELECT") || sql.trim_start().starts_with("WITH"),
                "{name} is not a read");
            let up = sql.to_uppercase();
            for verb in ["INSERT ", "UPDATE ", "DELETE ", "TRUNCATE", "ALTER ", "DROP "] {
                assert!(!up.contains(verb), "{name} contains {verb}");
            }
        }
        // The stock join repeats the guard on the snapshot side, and the budget
        // join ties orders and lines to the budget's own tenant.
        assert!(STOCK_SIGNALS_SQL.contains("s.tenant_id = ANY($1)"));
        assert!(BUDGETS_SQL.contains("l.tenant_id = b.tenant_id"));
        assert!(BUDGETS_SQL.contains("i.tenant_id = b.tenant_id"));
        // Only the day count joins $1 in the order statement.
        assert!(PURCHASE_ORDERS_SQL.contains("$2::bigint") && !PURCHASE_ORDERS_SQL.contains("$3"));
    }

    #[test]
    fn money_is_summed_per_currency_never_across() {
        let mut t = BTreeMap::new();
        add_money(&mut t, "CRC", 100.0);
        add_money(&mut t, "USD", 5.0);
        add_money(&mut t, "CRC", 50.5);
        assert_eq!(money_json(&t), json!([{"currency": "CRC", "amount": 150.5}, {"currency": "USD", "amount": 5.0}]));
    }

    #[test]
    fn stock_snapshot_counts_only_when_fresh_and_complete() {
        assert_eq!(stock_state(true, 10, 10), StockState::Counted);
        assert_eq!(stock_state(false, 10, 10), StockState::Stale);
        // Rows that do not add up to the meta: not trusted, whatever the clock says.
        assert_eq!(stock_state(true, 9, 10), StockState::Inconsistent);
        assert_eq!(stock_state(false, 0, 10), StockState::Inconsistent);
        assert_eq!(stock_state(true, 0, 0), StockState::Counted);
    }

    #[test]
    fn stale_and_missing_tenants_are_listed_not_summed() {
        let scope = scope_with(&[("A", "CRC"), ("B", "USD"), ("C", "CRC")]);
        let now = Utc::now();
        let row = |t: &str, fresh: bool, sig: &str, count: i64, value: f64, n_rows: i32| -> StockRow {
            (t.into(), "s1".into(), "month".into(), 0.95, n_rows, now, fresh, Some(sig.into()), count, value, 0)
        };
        let out = stock_signals(
            &scope,
            vec![
                row("P", true, "PEDIR_YA", 2, 20.0, 2),
                row("A", true, "OK", 5, 500.0, 5),
                row("B", false, "PEDIR_YA", 9, 900.0, 9), // stale
                // C has no snapshot at all.
            ],
        );
        let t = &out["totals"];
        assert_eq!(t["skus"], 7);
        assert_eq!(t["signals"]["PEDIR_YA"], 2, "the stale tenant's 9 must not be counted");
        assert_eq!(t["signals"]["OK"], 5);
        assert_eq!(t["tenants_counted"], 2);
        assert_eq!(t["tenants_excluded"], 2);
        assert_eq!(t["inventory_value"], json!([{"currency": "CRC", "amount": 520.0}]));
        let reasons: Vec<(&str, &str)> = out["excluded"].as_array().unwrap().iter()
            .map(|e| (e["tenant_id"].as_str().unwrap(), e["reason"].as_str().unwrap())).collect();
        assert_eq!(reasons, vec![("B", "stale"), ("C", "missing")]);
        // The stale tenant still shows its own numbers, flagged as not counted.
        let b = out["tenants"].as_array().unwrap().iter().find(|x| x["tenant_id"] == "B").unwrap();
        assert_eq!(b["counted"], false);
        assert_eq!(b["signals"]["PEDIR_YA"], 9);
    }

    #[test]
    fn committed_demand_rolls_months_and_zero_fills_empty_tenants() {
        let scope = scope_with(&[("A", "CRC")]);
        let out = committed_demand(
            &scope,
            vec![("A".into(), 2, 2, 150.0, 100.0, 150.0, 100.0, 1, Some("2026-01-01".into()), Some("2027-02-01".into()))],
            vec![
                ("A".into(), "2026-01".into(), 1, 100.0, 80.0),
                ("A".into(), "2027-02".into(), 1, 50.0, 20.0),
            ],
        );
        assert_eq!(out["totals"]["commitments"], 2);
        assert_eq!(out["totals"]["overdue"], 1);
        let p = &out["tenants"][0];
        assert_eq!(p["tenant_id"], "P");
        assert_eq!(p["commitments"], 0);
        assert!(p["earliest_delivery"].is_null());
        assert_eq!(out["months"][0]["month"], "2026-01");
        assert_eq!(out["months"][1]["quantity"], 50.0);
    }

    #[test]
    fn budgets_in_the_wrong_currency_are_shown_but_not_totalled() {
        let scope = scope_with(&[("A", "CRC")]);
        let row = |t: &str, cur: &str, amount: f64, spent: f64, committed: f64| -> BudgetRow {
            (t.into(), format!("root-{t}"), "month".into(), "2026-10-01".into(), "2026-10-31".into(), amount,
             cur.into(), false, spent, committed, 1)
        };
        let out = budgets(
            &scope,
            vec![row("P", "CRC", 1000.0, 300.0, 200.0), row("A", "USD", 999.0, 1.0, 1.0)],
            vec![("A".into(), 2)],
        );
        let by = out["totals"]["by_currency"].as_array().unwrap();
        assert_eq!(by.len(), 1, "the USD budget of a CRC tenant must not be added to anything");
        assert_eq!(by[0], json!({"currency": "CRC", "amount": 1000.0, "spent": 300.0, "committed": 200.0, "remaining": 500.0}));
        assert_eq!(out["totals"]["budgets_not_totalled"], 1);
        assert_eq!(out["totals"]["unknown_cost_lines"], 1);
        let a = out["tenants"].as_array().unwrap().iter().find(|x| x["tenant_id"] == "A").unwrap();
        assert_eq!(a["budgets"][0]["currency_mismatch"], true);
        assert_eq!(a["other_scope_budgets_not_included"], 2);
    }

    #[test]
    fn an_overspent_budget_says_so() {
        let scope = scope_with(&[]);
        let out = budgets(
            &scope,
            vec![("P".into(), "r".into(), "month".into(), "2026-10-01".into(), "2026-10-31".into(), 100.0, "CRC".into(),
                  true, 90.0, 30.0, 0)],
            vec![],
        );
        let b = &out["tenants"][0]["budgets"][0];
        assert_eq!(b["remaining"], -20.0);
        assert_eq!(b["over_budget"], true);
        assert_eq!(b["hard_cap"], true);
    }

    #[test]
    fn purchase_orders_total_per_currency_and_flag_orders_without_value() {
        let scope = scope_with(&[("A", "USD")]);
        let out = purchase_orders(
            &scope,
            90,
            vec![
                ("P".into(), 4, 1, 3, 2, 1, 1, 1, 1, 300.0, 1),
                ("A".into(), 2, 0, 2, 2, 0, 0, 2, 2, 40.0, 0),
            ],
        );
        assert_eq!(out["window_days"], 90);
        assert_eq!(out["totals"]["orders"], 6);
        assert_eq!(out["totals"]["cancelled"], 1);
        assert_eq!(out["totals"]["orders_without_value"], 1);
        assert_eq!(out["totals"]["value"], json!([{"currency": "CRC", "amount": 300.0}, {"currency": "USD", "amount": 40.0}]));
    }
}
