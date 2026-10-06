//! Reads the rows the outlook needs and hands plain numbers to `core`.
//!
//! One pass per request, never per SKU. Everything here is a SELECT; the rules
//! that decide anything live in `core`. Where a number already has an owner in
//! Python this reads it the way that owner does, and says so:
//!
//! * which commitments exist and who may see them: `committed_demand_service.
//!   list_for_tenant` (open rows, a warehouse-scoped caller sees only their own);
//! * stock: `list_stock`, summed over the caller's warehouses like
//!   `annotate_risk`; a SKU with no stock row has no figure (None, not 0);
//! * what is on its way: `service.get_incoming_detail` (open purchase-order
//!   lines `approved`/`modified` on a receivable, uncancelled order; transfers
//!   in transit credited to the destination), but read per LINE so a line the
//!   supplier promised a date for keeps its own date;
//! * the date of a purchase-order line: `reception_service.get_overdue_receptions`
//!   (an accepted supplier promise, else generated_at + the supplier's lead
//!   time learned from real receptions or declared on the supplier card);
//! * the lead time of a NEW order for a SKU: `optimizer_service.
//!   resolve_planning_inputs` (the semaforo's cascade: learned from the
//!   supplier's receptions > set on the SKU > supplier / category / global
//!   rule). The system default of 15 days is NOT used: it is an assumption.

use std::collections::{BTreeMap, HashMap, HashSet};

use chrono::{DateTime, Datelike, NaiveDate, Utc};
use sqlx::{PgPool, Row};

use crate::auth::warehouse_scope::{self as wscope, py_casefold, Scope};
use crate::error::ApiError;
use crate::fulfillment::core::{
    self, Arrival, ArrivalKind, Commitment, Outcome, SkuLeadSource,
};
use crate::pycompat::py_strip;
use crate::routes::po_payments::format_po_number;

pub fn day_number(d: NaiveDate) -> i64 {
    d.num_days_from_ce() as i64
}

pub fn day_to_date(n: i64) -> Option<NaiveDate> {
    i32::try_from(n).ok().and_then(NaiveDate::from_num_days_from_ce_opt)
}

/// An open commitment row, as stored.
#[derive(Clone, Debug)]
pub struct CommitmentRow {
    pub id: String,
    pub sku: String,
    pub warehouse_id: Option<String>,
    pub delivery_date: NaiveDate,
    pub quantity: f64,
    pub probability: f64,
    pub customer: Option<String>,
    pub on_top_of_base: bool,
    pub note: Option<String>,
    pub source: String,
    pub contract_root_id: Option<String>,
    pub contract_reference: Option<String>,
    pub contract_customer: Option<String>,
}

/// What stands behind one arrival, for the detail view.
#[derive(Clone, Debug)]
pub struct ArrivalMeta {
    pub kind: &'static str, // "po" | "transfer"
    pub reference: String,
    pub supplier: Option<String>,
    pub warehouse: String,
    pub qty: f64,
    pub source: &'static str, // supplier_promise | lead_time | overdue | no_lead_time | transfer
    pub expected_day: Option<i64>,
    pub source_id: String,
}

#[derive(Clone, Debug)]
pub struct StockRow {
    pub warehouse: String,
    pub current_stock: f64,
}

/// Everything known about one SKU, and the verdict of each of its commitments.
#[derive(Clone, Debug)]
pub struct SkuReport {
    pub stock: Option<f64>,
    pub stock_rows: Vec<StockRow>,
    pub supplier: Option<String>,
    pub lead: Option<(i64, SkuLeadSource)>,
    pub arrivals: Vec<Arrival>,
    pub meta: Vec<ArrivalMeta>,
    /// Row indexes into `Loaded::rows`, in served order (earliest delivery first).
    pub served: Vec<usize>,
    /// One outcome per entry of `served`.
    pub outcomes: Vec<Outcome>,
}

pub struct Loaded {
    pub today: NaiveDate,
    pub rows: Vec<CommitmentRow>,
    pub reports: BTreeMap<String, SkuReport>,
    /// "company" or "warehouses": whose stock and arrivals the verdicts used.
    pub scope_label: &'static str,
}

impl Loaded {
    pub fn outcome_of(&self, row_index: usize) -> Option<(&SkuReport, usize)> {
        let report = self.reports.get(&self.rows[row_index].sku)?;
        let pos = report.served.iter().position(|&i| i == row_index)?;
        Some((report, pos))
    }
}

fn lower_strip(s: &str) -> String {
    py_strip(s).to_lowercase()
}

/// `service._aggregate_stock_rows_by_sku`' representative row: the tenant's
/// anchored default warehouse first, then `name_precedence_key` (the literal
/// default name, then casefolded alphabetical). The first minimum wins.
fn pick_representative<'a, T>(rows: &'a [(String, T)], default_wh: &str) -> Option<&'a (String, T)> {
    rows.iter().min_by_key(|(wh, _)| (wh != default_wh, wh != "principal", py_casefold(wh)))
}

struct RawStock {
    sku: String,
    warehouse: String,
    current_stock: f64,
    supplier: Option<String>,
    category: Option<String>,
    lead_time_days: Option<i64>,
    lead_time_declared: bool,
}

pub async fn load(
    pool: &PgPool,
    tenant_id: &str,
    scope_names: &Scope,
    scope_warehouse_ids: &Option<Vec<String>>,
    today: NaiveDate,
) -> Result<Loaded, ApiError> {
    let scope_label = if scope_names.is_some() || scope_warehouse_ids.is_some() { "warehouses" } else { "company" };
    let mut loaded = Loaded { today, rows: Vec::new(), reports: BTreeMap::new(), scope_label };

    // ── Commitments ─────────────────────────────────────────────────────────
    if let Some(ids) = scope_warehouse_ids {
        if ids.is_empty() {
            return Ok(loaded);
        }
    }
    let rows = sqlx::query(
        "SELECT c.id, c.sku, c.warehouse_id, c.delivery_date, c.quantity::float8 AS quantity,
                c.probability::float8 AS probability, c.customer, c.on_top_of_base, c.note, c.source,
                c.contract_root_id, sc.reference AS contract_reference,
                sc.customer AS contract_customer
           FROM committed_demand c
           LEFT JOIN supply_contracts sc
                  ON sc.tenant_id = c.tenant_id AND sc.root_id = c.contract_root_id AND sc.superseded_by IS NULL
          WHERE c.tenant_id = $1 AND c.status = 'open'
            AND ($2::text[] IS NULL OR c.warehouse_id = ANY($2))
          ORDER BY c.delivery_date, c.created_at, c.id",
    )
    .bind(tenant_id)
    .bind(scope_warehouse_ids.as_ref())
    .fetch_all(pool)
    .await?;
    for r in &rows {
        loaded.rows.push(CommitmentRow {
            id: r.try_get("id")?,
            sku: r.try_get("sku")?,
            warehouse_id: r.try_get("warehouse_id")?,
            delivery_date: r.try_get("delivery_date")?,
            quantity: r.try_get("quantity")?,
            probability: r.try_get("probability")?,
            customer: r.try_get("customer")?,
            on_top_of_base: r.try_get("on_top_of_base")?,
            note: r.try_get("note")?,
            source: r.try_get("source")?,
            contract_root_id: r.try_get("contract_root_id")?,
            contract_reference: r.try_get("contract_reference")?,
            contract_customer: r.try_get("contract_customer")?,
        });
    }
    if loaded.rows.is_empty() {
        return Ok(loaded);
    }
    let skus: Vec<String> = {
        let mut seen = HashSet::new();
        loaded.rows.iter().filter(|r| seen.insert(r.sku.clone())).map(|r| r.sku.clone()).collect()
    };

    // ── Stock, summed over the caller's warehouses ──────────────────────────
    let stock_rows = sqlx::query(
        "SELECT sku, warehouse, current_stock::float8 AS current_stock, supplier, category,
                lead_time_days, lead_time_set_by
           FROM inventory_stock WHERE tenant_id = $1 AND sku = ANY($2) ORDER BY sku, warehouse",
    )
    .bind(tenant_id)
    .bind(&skus)
    .fetch_all(pool)
    .await?;
    let mut raw: Vec<RawStock> = Vec::new();
    for r in &stock_rows {
        let warehouse: String = r.try_get::<Option<String>, _>("warehouse")?.unwrap_or_default();
        if !wscope::in_scope(scope_names, Some(&warehouse)) {
            continue;
        }
        let declared: Option<String> = r.try_get("lead_time_set_by")?;
        raw.push(RawStock {
            sku: r.try_get("sku")?,
            warehouse,
            current_stock: r.try_get("current_stock")?,
            supplier: r.try_get("supplier")?,
            category: r.try_get("category")?,
            lead_time_days: r.try_get::<Option<i32>, _>("lead_time_days")?.map(i64::from),
            lead_time_declared: declared.is_some_and(|s| !s.is_empty()),
        });
    }
    let default_wh = wscope::tenant_default(pool, tenant_id).await?;

    // ── Lead-time evidence and rules (one read each) ────────────────────────
    let learned = learned_lead_times(pool, tenant_id).await?;
    let primary = primary_suppliers(pool, tenant_id, &skus).await?;
    let rules = lead_rules(pool, tenant_id).await?;
    let cards = supplier_cards(pool, tenant_id).await?;

    // ── Arrivals ────────────────────────────────────────────────────────────
    let day_today = day_number(today);
    let po_lines = open_po_lines(pool, tenant_id, &skus).await?;
    let promises = accepted_promises(pool, tenant_id, &po_lines).await?;
    let transfers = transfers_in_transit(pool, tenant_id, &skus).await?;

    for sku in &skus {
        // Representative row and lead time of a NEW order.
        let per_wh: Vec<(String, &RawStock)> =
            raw.iter().filter(|s| &s.sku == sku).map(|s| (s.warehouse.clone(), s)).collect();
        let stock_rows: Vec<StockRow> =
            per_wh.iter().map(|(w, s)| StockRow { warehouse: w.clone(), current_stock: s.current_stock }).collect();
        let stock = if per_wh.is_empty() {
            None
        } else {
            let mut t = 0.0_f64;
            for (_, s) in &per_wh {
                t += s.current_stock;
            }
            Some(t)
        };
        let mut supplier: Option<String> = None;
        let mut lead = None;
        if let Some((_, rep)) = pick_representative(&per_wh, &default_wh) {
            supplier = rep.supplier.clone().filter(|s| !s.is_empty()).or_else(|| primary.get(sku).cloned());
            let rule = rule_hit(&rules, supplier.as_deref(), rep.category.as_deref());
            let learned_avg = supplier.as_deref().and_then(|s| {
                learned.get(&lower_strip(s)).filter(|(_, n)| *n >= core::MIN_LEAD_TIME_OBSERVATIONS).map(|(a, _)| *a)
            });
            lead = core::sku_lead_days(rep.lead_time_days, rep.lead_time_declared, rule, learned_avg);
        }

        // Arrivals of this SKU inside the caller's scope.
        let mut arrivals: Vec<Arrival> = Vec::new();
        let mut meta: Vec<ArrivalMeta> = Vec::new();
        for line in po_lines.iter().filter(|l| &l.sku == sku) {
            if !wscope::in_scope(scope_names, line.warehouse.as_deref()) || line.qty <= 0.0 {
                continue;
            }
            let supplier_name = line.supplier.as_deref().map(py_strip).filter(|s| !s.is_empty()).map(str::to_string);
            let po_lead = supplier_name.as_deref().and_then(|s| {
                let key = lower_strip(s);
                let obs = learned.get(&key).copied();
                let card = cards.by_name.get(&key);
                core::po_lead_days(
                    obs.map(|(a, _)| a),
                    obs.map(|(_, n)| n).unwrap_or(0),
                    card.map(|c| c.0),
                    card.map(|c| c.1).unwrap_or(false),
                )
            });
            let generated = day_number(line.generated_at.date_naive());
            let promise = promises.get(&line.item_id).copied().map(day_number);
            let a = core::po_line_arrival(generated, po_lead, promise, day_today);
            arrivals.push(Arrival { day: a.day, qty: line.qty, kind: ArrivalKind::Po });
            meta.push(ArrivalMeta {
                kind: "po",
                reference: format_po_number(line.po_number, &line.po_id),
                supplier: supplier_name,
                warehouse: wscope::effective_name(line.warehouse.as_deref()),
                qty: line.qty,
                source: a.source.as_str(),
                expected_day: a.expected_day,
                source_id: line.po_id.clone(),
            });
        }
        for t in transfers.iter().filter(|t| &t.sku == sku) {
            if !wscope::in_scope(scope_names, Some(&t.to_warehouse)) || t.qty <= 0.0 {
                continue;
            }
            arrivals.push(Arrival { day: Some(day_today), qty: t.qty, kind: ArrivalKind::Transfer });
            meta.push(ArrivalMeta {
                kind: "transfer",
                reference: t.from_warehouse.clone(),
                supplier: None,
                warehouse: t.to_warehouse.clone(),
                qty: t.qty,
                source: "transfer",
                expected_day: Some(day_today),
                source_id: t.transfer_id.clone(),
            });
        }

        let idx: Vec<usize> = loaded.rows.iter().enumerate().filter(|(_, r)| &r.sku == sku).map(|(i, _)| i).collect();
        let commitments: Vec<Commitment> = idx
            .iter()
            .map(|&i| {
                let r = &loaded.rows[i];
                Commitment {
                    id: r.id.clone(),
                    delivery_day: day_number(r.delivery_date),
                    quantity: r.quantity,
                    probability: r.probability,
                }
            })
            .collect();
        let outcomes = core::evaluate_sku(&commitments, stock, lead.map(|l| l.0), &arrivals, day_today);
        // `evaluate_sku` returns served order; map each outcome back to its row.
        let by_id: HashMap<&str, usize> = idx.iter().map(|&i| (loaded.rows[i].id.as_str(), i)).collect();
        let served: Vec<usize> = outcomes.iter().map(|o| by_id[o.id.as_str()]).collect();
        loaded.reports.insert(
            sku.clone(),
            SkuReport { stock, stock_rows, supplier, lead, arrivals, meta, served, outcomes },
        );
    }
    Ok(loaded)
}

// ── Lead-time reads ──────────────────────────────────────────────────────────

/// `{lower(supplier): (average days, receptions)}`.
async fn learned_lead_times(pool: &PgPool, tenant_id: &str) -> Result<HashMap<String, (f64, i64)>, ApiError> {
    let rows = sqlx::query(
        "SELECT LOWER(supplier) AS supplier, AVG(lead_time_days)::float8 AS avg_days, COUNT(*)::int8 AS n
           FROM supplier_lead_time_obs WHERE tenant_id = $1 GROUP BY LOWER(supplier)",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut out = HashMap::new();
    for r in &rows {
        let supplier: String = r.try_get("supplier")?;
        let avg: Option<f64> = r.try_get("avg_days")?;
        let n: i64 = r.try_get("n")?;
        if let Some(a) = avg {
            out.insert(supplier, (a, n));
        }
    }
    Ok(out)
}

/// `supplier_service.get_primary_suppliers_map`: first row per SKU.
async fn primary_suppliers(pool: &PgPool, tenant_id: &str, skus: &[String]) -> Result<HashMap<String, String>, ApiError> {
    let rows = sqlx::query(
        "SELECT ss.sku, s.name AS supplier_name
           FROM sku_suppliers ss JOIN suppliers s ON s.id = ss.supplier_id
          WHERE ss.tenant_id = $1 AND ss.is_primary = TRUE AND s.active = TRUE AND ss.sku = ANY($2)
          ORDER BY ss.is_primary DESC, ss.created_at ASC, ss.supplier_id ASC",
    )
    .bind(tenant_id)
    .bind(skus)
    .fetch_all(pool)
    .await?;
    let mut out = HashMap::new();
    for r in &rows {
        let sku: String = r.try_get("sku")?;
        let name: String = r.try_get("supplier_name")?;
        out.entry(sku).or_insert(name);
    }
    Ok(out)
}

struct SupplierCards {
    /// lower(name) -> (declared lead time, declared flag), ACTIVE suppliers only:
    /// `supplier_service.get_supplier_by_name`, what `_effective_lead_time` reads.
    by_name: HashMap<String, (i64, bool)>,
}

async fn supplier_cards(pool: &PgPool, tenant_id: &str) -> Result<SupplierCards, ApiError> {
    let rows = sqlx::query(
        "SELECT name, lead_time_days, lead_time_set_by FROM suppliers WHERE tenant_id = $1 AND active = TRUE ORDER BY name",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut by_name = HashMap::new();
    for r in &rows {
        let name: String = r.try_get("name")?;
        let days: Option<i32> = r.try_get("lead_time_days")?;
        let set_by: Option<String> = r.try_get("lead_time_set_by")?;
        if let Some(d) = days {
            by_name.entry(name.to_lowercase()).or_insert((i64::from(d), set_by.is_some_and(|s| !s.is_empty())));
        }
    }
    Ok(SupplierCards { by_name })
}

/// `stock_defaults_service.build_rule_index`, for the lead-time field only:
/// scope -> key -> Some(days) / None (a rule that says nothing about it).
struct LeadRules {
    supplier: HashMap<String, Option<i64>>,
    category: HashMap<String, Option<i64>>,
    global: Option<Option<i64>>,
}

async fn lead_rules(pool: &PgPool, tenant_id: &str) -> Result<LeadRules, ApiError> {
    let mut rules = LeadRules { supplier: HashMap::new(), category: HashMap::new(), global: None };
    // The supplier card's own lead time counts as a supplier rule, DEACTIVATED
    // suppliers included; only a declared one (`lead_time_set_by`), first
    // writer wins, active first.
    let cards = sqlx::query(
        "SELECT name, lead_time_days FROM suppliers
          WHERE tenant_id = $1 AND lead_time_days IS NOT NULL AND lead_time_set_by IS NOT NULL
          ORDER BY COALESCE(active, TRUE) DESC",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    for r in &cards {
        let name: String = r.try_get("name")?;
        let key = lower_strip(&name);
        let days: i32 = r.try_get("lead_time_days")?;
        if !key.is_empty() {
            rules.supplier.entry(key).or_insert(Some(i64::from(days)));
        }
    }
    let defaults = sqlx::query(
        "SELECT scope_type, scope_value, lead_time_days FROM stock_defaults
          WHERE tenant_id = $1 ORDER BY scope_type, scope_value",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    for r in &defaults {
        let scope: String = r.try_get("scope_type")?;
        let key: String = r.try_get::<Option<String>, _>("scope_value")?.unwrap_or_default();
        let days: Option<i64> = r.try_get::<Option<i32>, _>("lead_time_days")?.map(i64::from);
        match scope.as_str() {
            "supplier" => {
                // Merged over the card: a rule that leaves the lead time empty
                // keeps the card's.
                let merged = match rules.supplier.get(&key) {
                    Some(card) => days.or(*card),
                    None => days,
                };
                rules.supplier.insert(key, merged);
            }
            "category" => {
                rules.category.insert(key, days);
            }
            "global" => rules.global = Some(days),
            _ => {}
        }
    }
    Ok(rules)
}

/// `stock_defaults_service._rule_hit` for the lead-time field: the narrowest
/// rule that has a value; a rule without one falls through.
fn rule_hit(rules: &LeadRules, supplier: Option<&str>, category: Option<&str>) -> Option<i64> {
    let s = lower_strip(supplier.unwrap_or(""));
    if !s.is_empty() {
        if let Some(Some(d)) = rules.supplier.get(&s) {
            return Some(*d);
        }
    }
    let c = lower_strip(category.unwrap_or(""));
    if !c.is_empty() {
        if let Some(Some(d)) = rules.category.get(&c) {
            return Some(*d);
        }
    }
    rules.global.flatten()
}

// ── Arrival reads ────────────────────────────────────────────────────────────

struct PoLine {
    item_id: String,
    sku: String,
    warehouse: Option<String>,
    supplier: Option<String>,
    qty: f64,
    po_id: String,
    po_number: Option<i32>,
    generated_at: DateTime<Utc>,
}

async fn open_po_lines(pool: &PgPool, tenant_id: &str, skus: &[String]) -> Result<Vec<PoLine>, ApiError> {
    let rows = sqlx::query(
        "SELECT poi.id AS item_id, poi.sku, poi.warehouse, poi.supplier,
                GREATEST(poi.final_qty - COALESCE(poi.received_qty, 0), 0)::float8 AS qty,
                pol.id AS po_id, pol.po_number, pol.generated_at
           FROM inventory_po_items poi
           JOIN inventory_po_log pol ON pol.id = poi.po_log_id
          WHERE poi.tenant_id = $1 AND pol.tenant_id = $1
            AND pol.reception_status IN ('pending', 'partial', 'not_received')
            AND pol.cancelled_at IS NULL
            AND poi.status IN ('approved', 'modified')
            AND poi.sku = ANY($2)
          ORDER BY pol.po_number NULLS LAST, pol.id, poi.id",
    )
    .bind(tenant_id)
    .bind(skus)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        let generated: Option<DateTime<Utc>> = r.try_get("generated_at")?;
        let Some(generated_at) = generated else { continue };
        out.push(PoLine {
            item_id: r.try_get("item_id")?,
            sku: r.try_get("sku")?,
            warehouse: r.try_get("warehouse")?,
            supplier: r.try_get("supplier")?,
            qty: r.try_get("qty")?,
            po_id: r.try_get("po_id")?,
            po_number: r.try_get("po_number")?,
            generated_at,
        });
    }
    Ok(out)
}

/// `po_confirmation_service.accepted_promises`, keyed by line: the promised
/// date of a line whose LATEST supplier answer is a change a person accepted.
async fn accepted_promises(
    pool: &PgPool,
    tenant_id: &str,
    lines: &[PoLine],
) -> Result<HashMap<String, NaiveDate>, ApiError> {
    let mut po_ids: Vec<String> = lines.iter().map(|l| l.po_id.clone()).collect();
    po_ids.sort();
    po_ids.dedup();
    if po_ids.is_empty() {
        return Ok(HashMap::new());
    }
    let rows = sqlx::query(
        "SELECT c.po_item_id, c.promised_date
           FROM po_confirmation_acceptances a
           JOIN po_line_confirmations c ON c.id = a.confirmation_id
           JOIN po_confirmation_requests r ON r.id = c.request_id
          WHERE a.tenant_id = $1 AND c.po_log_id = ANY($2)
            AND c.status = 'changed' AND c.promised_date IS NOT NULL
            AND c.revision = (SELECT MAX(c2.revision) FROM po_line_confirmations c2
                               WHERE c2.request_id = c.request_id AND c2.po_item_id = c.po_item_id)",
    )
    .bind(tenant_id)
    .bind(&po_ids)
    .fetch_all(pool)
    .await?;
    let mut out = HashMap::new();
    for r in &rows {
        let item: String = r.try_get("po_item_id")?;
        let date: NaiveDate = r.try_get("promised_date")?;
        out.insert(item, date);
    }
    Ok(out)
}

struct TransferIn {
    sku: String,
    to_warehouse: String,
    from_warehouse: String,
    transfer_id: String,
    qty: f64,
}

async fn transfers_in_transit(pool: &PgPool, tenant_id: &str, skus: &[String]) -> Result<Vec<TransferIn>, ApiError> {
    let rows = sqlx::query(
        "SELECT tri.sku, trl.to_warehouse, trl.from_warehouse, trl.id AS transfer_id,
                SUM(GREATEST(tri.qty_sent - COALESCE(tri.qty_received, 0), 0))::float8 AS qty
           FROM inventory_transfer_items tri
           JOIN inventory_transfer_log trl ON trl.id = tri.transfer_id
          WHERE tri.tenant_id = $1 AND trl.status IN ('in_transit', 'partial') AND tri.sku = ANY($2)
          GROUP BY tri.sku, trl.to_warehouse, trl.id, trl.from_warehouse
          ORDER BY trl.id",
    )
    .bind(tenant_id)
    .bind(skus)
    .fetch_all(pool)
    .await?;
    let mut out = Vec::with_capacity(rows.len());
    for r in &rows {
        out.push(TransferIn {
            sku: r.try_get("sku")?,
            to_warehouse: r.try_get("to_warehouse")?,
            from_warehouse: r.try_get("from_warehouse")?,
            transfer_id: r.try_get("transfer_id")?,
            qty: r.try_get::<Option<f64>, _>("qty")?.unwrap_or(0.0),
        });
    }
    Ok(out)
}
