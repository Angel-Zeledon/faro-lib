//! `backend/audit/service.py`: reads the trail, filterable, paginated and
//! exportable as CSV, in one normalised shape for every source:
//!
//! ```text
//! {id, at, actor: {id, kind, label}, action, target: {type, id, label},
//!  before, after, status}
//! ```

use std::collections::{HashMap, HashSet};

use chrono::{DateTime, Duration, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sqlx::postgres::PgArguments;
use sqlx::{Arguments, PgPool};

use super::catalog::{self, PREFIX};
use crate::pycompat::{isoformat_utc, truthy};
use crate::pyjson;

pub const MAX_PAGE: i64 = 200;
pub const EXPORT_BATCH: i64 = MAX_PAGE;
pub const EXPORT_MAX_ROWS: i64 = 50_000;

const LABEL_KEYS: [&str; 6] = ["reference", "key_name", "email", "session_name", "name", "sku"];
const NOISE_KEYS: [&str; 4] = ["severity", "kind", "reason", "reason_params"];

/// `_filters`: every value as the query gave it (validated), `None` = absent.
#[derive(Debug, Clone, Default)]
pub struct Filters {
    pub actor: Option<String>,
    pub target_type: Option<String>,
    pub action: Option<String>,
    pub target_id: Option<String>,
    pub status: Option<String>,
    pub date_from: Option<NaiveDate>,
    pub date_to: Option<NaiveDate>,
}

impl Filters {
    /// `{k: str(v) for k, v in filters.items() if v is not None}`, the export
    /// note's `filters`, in `_filters` order.
    pub fn as_note(&self) -> Map<String, Value> {
        let mut m = Map::new();
        let mut put = |k: &str, v: Option<String>| {
            if let Some(v) = v {
                m.insert(k.into(), Value::String(v));
            }
        };
        put("actor", self.actor.clone());
        put("target_type", self.target_type.clone());
        put("action", self.action.clone());
        put("target_id", self.target_id.clone());
        put("status", self.status.clone());
        put("date_from", self.date_from.map(|d| d.format("%Y-%m-%d").to_string()));
        put("date_to", self.date_to.map(|d| d.format("%Y-%m-%d").to_string()));
        m
    }
}

enum Bind {
    Text(String),
    TextArr(Vec<String>),
    Ts(DateTime<Utc>),
}

/// `_where`: the clause and its parameters, or `None` for Python's "FALSE"
/// (no stored action can match, so nothing is queried).
fn where_clause(tenant_id: &str, f: &Filters) -> Option<(String, Vec<Bind>)> {
    let mut clauses = vec!["tenant_id = $1".to_string()];
    let mut binds = vec![Bind::Text(tenant_id.to_string())];
    // Python tests truthiness: an empty `?action=` falls through.
    let stored = match (&f.action, &f.target_type) {
        (Some(a), _) if !a.is_empty() => catalog::stored_for_action(a),
        (_, Some(t)) if !t.is_empty() => catalog::actions_for_target_type(t),
        _ => catalog::all_stored_actions().into_iter().filter(|a| a != "api_write").collect(),
    };
    if stored.is_empty() {
        return None;
    }
    binds.push(Bind::TextArr(stored));
    clauses.push(format!("action = ANY(${})", binds.len()));
    if let Some(actor) = f.actor.as_ref().filter(|s| !s.is_empty()) {
        binds.push(Bind::Text(actor.clone()));
        clauses.push(format!("user_id = ${}", binds.len()));
    }
    if let Some(t) = f.target_id.as_ref().filter(|s| !s.is_empty()) {
        binds.push(Bind::Text(t.clone()));
        let n = binds.len();
        clauses.push(format!("(resource = ${n} OR context->>'target_id' = ${n})"));
    }
    if let Some(s) = f.status.as_ref().filter(|s| *s == "success" || *s == "error") {
        binds.push(Bind::Text(s.clone()));
        clauses.push(format!("status = ${}", binds.len()));
    }
    if let Some(d) = f.date_from {
        binds.push(Bind::Ts(d.and_hms_opt(0, 0, 0).unwrap_or_default().and_utc()));
        clauses.push(format!("created_at >= ${}", binds.len()));
    }
    if let Some(d) = f.date_to {
        // `date_to` is inclusive: the whole of that day.
        binds.push(Bind::Ts(d.and_hms_opt(0, 0, 0).unwrap_or_default().and_utc() + Duration::days(1)));
        clauses.push(format!("created_at < ${}", binds.len()));
    }
    Some((clauses.join(" AND "), binds))
}

fn args(binds: &[Bind]) -> Result<PgArguments, sqlx::Error> {
    let mut a = PgArguments::default();
    for b in binds {
        let r = match b {
            Bind::Text(s) => a.add(s.clone()),
            Bind::TextArr(v) => a.add(v.clone()),
            Bind::Ts(t) => a.add(*t),
        };
        r.map_err(sqlx::Error::Encode)?;
    }
    Ok(a)
}

/// `_actor_labels`: users by e-mail, keys by name.
async fn actor_labels(pool: &PgPool, tenant_id: &str, ids: &HashSet<String>) -> Result<HashMap<String, String>, sqlx::Error> {
    let mut labels = HashMap::new();
    let user_ids: Vec<String> = ids
        .iter()
        .filter(|i| !i.starts_with("api_key:") && *i != "scheduler" && *i != "system" && *i != "scim")
        .cloned()
        .collect();
    if !user_ids.is_empty() {
        let rows: Vec<(String, Option<String>)> =
            sqlx::query_as("SELECT id, email FROM users WHERE tenant_id = $1 AND id = ANY($2)")
                .bind(tenant_id)
                .bind(&user_ids)
                .fetch_all(pool)
                .await?;
        for (id, email) in rows {
            if let Some(e) = email {
                labels.insert(id, e);
            }
        }
    }
    let key_ids: Vec<String> = ids
        .iter()
        .filter_map(|i| i.strip_prefix("api_key:").map(str::to_string))
        .collect();
    if !key_ids.is_empty() {
        let rows: Vec<(String, String)> =
            sqlx::query_as("SELECT id, name FROM api_keys WHERE tenant_id = $1 AND id = ANY($2)")
                .bind(tenant_id)
                .bind(&key_ids)
                .fetch_all(pool)
                .await?;
        for (id, name) in rows {
            labels.insert(format!("api_key:{id}"), name);
        }
    }
    Ok(labels)
}

pub fn actor_kind(actor_id: &str) -> &'static str {
    if actor_id.starts_with("api_key:") {
        "api_key"
    } else if actor_id == "scheduler" {
        "schedule"
    } else if actor_id == "system" {
        "system"
    } else if actor_id == "scim" {
        // The company's identity provider, through the tenant's SCIM token.
        "scim"
    } else {
        "user"
    }
}

pub struct Row {
    pub id: String,
    pub user_id: String,
    pub action: String,
    pub resource: Option<String>,
    pub context: Option<Value>,
    pub status: String,
    pub created_at: Option<DateTime<Utc>>,
}

fn get<'a>(ctx: &'a Map<String, Value>, k: &str) -> Value {
    ctx.get(k).cloned().unwrap_or(Value::Null)
}

/// `_normalise`.
pub fn normalise(row: &Row, labels: &HashMap<String, String>) -> Result<Value, ()> {
    let ctx: Map<String, Value> = match &row.context {
        Some(Value::Object(m)) => m.clone(),
        Some(v) if truthy(v) => return Err(()), // `ctx.get` on a non-dict raises
        _ => Map::new(),
    };
    let resource = row.resource.clone().map(Value::String).unwrap_or(Value::Null);
    let (action, target_type, target_id, target_label, before, after);
    if let Some(stripped) = row.action.strip_prefix(PREFIX) {
        action = stripped.to_string();
        target_type = get(&ctx, "target_type");
        let t = get(&ctx, "target_id");
        target_id = if truthy(&t) { t } else { resource };
        target_label = get(&ctx, "target_label");
        before = get(&ctx, "before");
        after = get(&ctx, "after");
    } else {
        let (tt, a) = catalog::legacy(&row.action).ok_or(())?;
        target_type = json!(tt);
        action = a.to_string();
        target_id = resource;
        let mut label = LABEL_KEYS
            .iter()
            .find_map(|k| ctx.get(*k).filter(|v| truthy(v)).cloned())
            .unwrap_or(Value::Null);
        let details: Map<String, Value> = ctx
            .iter()
            .filter(|(k, _)| !NOISE_KEYS.contains(&k.as_str()))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let (mut b, mut af) = (Value::Null, Value::Null);
        match row.action.as_str() {
            "session.delete" => {
                label = get(&ctx, "name");
                b = json!({"name": get(&ctx, "name"), "status": get(&ctx, "status_at_deletion")});
            }
            "session.archive" => {
                label = get(&ctx, "name");
                b = json!({"name": get(&ctx, "name"), "status": get(&ctx, "status_at_archive")});
            }
            "account.user_role_changed" | "account.scim_role_changed" => {
                b = json!({"role": get(&ctx, "previous_role")});
                af = json!({"role": get(&ctx, "role")});
            }
            _ => {
                if !details.is_empty() {
                    af = Value::Object(details);
                }
            }
        }
        target_label = label;
        before = b;
        after = af;
    }
    let kind = match ctx.get("actor_kind").filter(|v| truthy(v)) {
        Some(v) => v.clone(),
        None => json!(actor_kind(&row.user_id)),
    };
    let mut item = Map::new();
    item.insert("id".into(), json!(row.id));
    item.insert("at".into(), row.created_at.as_ref().map(|d| json!(isoformat_utc(d))).unwrap_or(Value::Null));
    item.insert("actor".into(), json!({
        "id": row.user_id,
        "kind": kind,
        "label": labels.get(&row.user_id).cloned().map(Value::String).unwrap_or(Value::Null),
    }));
    item.insert("action".into(), json!(action));
    item.insert("target".into(), json!({"type": target_type, "id": target_id, "label": target_label}));
    item.insert("before".into(), before);
    item.insert("after".into(), after);
    item.insert("status".into(), json!(row.status));
    Ok(Value::Object(item))
}

pub struct Page {
    pub items: Vec<Value>,
    pub total: i64,
    pub limit: i64,
    pub offset: i64,
}

impl Page {
    pub fn to_json(&self) -> Value {
        json!({"items": self.items, "total": self.total, "limit": self.limit, "offset": self.offset})
    }
}

/// An error from the reader: a database error, or a row Python would have
/// raised on (a 500 either way).
pub enum ReadError {
    Db(sqlx::Error),
    Internal,
}

impl From<sqlx::Error> for ReadError {
    fn from(e: sqlx::Error) -> Self {
        ReadError::Db(e)
    }
}

impl From<ReadError> for crate::error::ApiError {
    fn from(e: ReadError) -> Self {
        match e {
            ReadError::Db(e) => e.into(),
            ReadError::Internal => crate::error::ApiError::internal(),
        }
    }
}

/// `list_audit`.
pub async fn list_audit(pool: &PgPool, tenant_id: &str, limit: i64, offset: i64, f: &Filters) -> Result<Page, ReadError> {
    let limit = limit.clamp(1, MAX_PAGE);
    let Some((clause, binds)) = where_clause(tenant_id, f) else {
        return Ok(Page { items: Vec::new(), total: 0, limit, offset });
    };
    let n = binds.len();
    let sql = format!(
        "SELECT id, user_id, action, resource, context, status, created_at
           FROM activity_logs WHERE {clause}
          ORDER BY created_at DESC, id DESC LIMIT ${} OFFSET ${}",
        n + 1,
        n + 2
    );
    let mut a = args(&binds)?;
    a.add(limit).map_err(sqlx::Error::Encode)?;
    a.add(offset).map_err(sqlx::Error::Encode)?;
    #[allow(clippy::type_complexity)]
    let raw: Vec<(String, String, String, Option<String>, Option<Value>, String, Option<DateTime<Utc>>)> =
        sqlx::query_as_with(&sql, a).fetch_all(pool).await?;
    let (total,): (i64,) = sqlx::query_as_with(
        &format!("SELECT COUNT(*) AS n FROM activity_logs WHERE {clause}"),
        args(&binds)?,
    )
    .fetch_one(pool)
    .await?;
    let rows: Vec<Row> = raw
        .into_iter()
        .map(|(id, user_id, action, resource, context, status, created_at)| Row {
            id, user_id, action, resource, context, status, created_at,
        })
        .collect();
    let ids: HashSet<String> = rows.iter().map(|r| r.user_id.clone()).collect();
    let labels = actor_labels(pool, tenant_id, &ids).await?;
    let items = rows
        .iter()
        .map(|r| normalise(r, &labels))
        .collect::<Result<Vec<_>, ()>>()
        .map_err(|_| ReadError::Internal)?;
    Ok(Page { items, total, limit, offset })
}

/// `actors`: everyone in the trail (machine writes included), by label.
pub async fn actors(pool: &PgPool, tenant_id: &str) -> Result<Vec<Value>, sqlx::Error> {
    let rows: Vec<(String,)> =
        sqlx::query_as("SELECT DISTINCT user_id FROM activity_logs WHERE tenant_id = $1 AND action = ANY($2)")
            .bind(tenant_id)
            .bind(catalog::all_stored_actions())
            .fetch_all(pool)
            .await?;
    let ids: HashSet<String> = rows.into_iter().map(|r| r.0).collect();
    let labels = actor_labels(pool, tenant_id, &ids).await?;
    let mut out: Vec<(String, Value)> = ids
        .iter()
        .map(|i| {
            let label = labels.get(i).cloned();
            // `(a["label"] or a["id"]).lower()`
            let sort_key = label.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| i.clone()).to_lowercase();
            (sort_key, json!({"id": i, "kind": actor_kind(i), "label": label}))
        })
        .collect();
    out.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(out.into_iter().map(|(_, v)| v).collect())
}

// ── CSV export ───────────────────────────────────────────────────────────────

pub const CSV_COLUMNS: [&str; 11] = [
    "at", "actor_kind", "actor_id", "actor_label", "action", "target_type", "target_id",
    "target_label", "before", "after", "status",
];

/// `backend/utils/csv_safe.py::csv_safe` for a str cell.
pub fn csv_safe(text: &str) -> String {
    match text.chars().next() {
        Some('=' | '+' | '-' | '@' | '\t' | '\r') => format!("'{text}"),
        _ => text.to_string(),
    }
}

/// One `csv.writer(...).writerow(cells)` line: QUOTE_MINIMAL, doubled quotes,
/// `\r\n` terminator.
pub fn csv_line<S: AsRef<str>>(cells: &[S]) -> String {
    let mut out = String::new();
    for (i, c) in cells.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        let c = c.as_ref();
        if c.contains([',', '"', '\r', '\n']) {
            out.push('"');
            out.push_str(&c.replace('"', "\"\""));
            out.push('"');
        } else {
            out.push_str(c);
        }
    }
    if cells.len() == 1 && cells[0].as_ref().is_empty() {
        // csv quotes a lone empty field so the row is not read as blank.
        out = "\"\"".into();
    }
    out.push_str("\r\n");
    out
}

/// A cell as `csv.writer` renders it: `csv_safe` for a str, "" for None,
/// `str()` for anything else.
fn cell(v: &Value) -> String {
    match v {
        Value::String(s) => csv_safe(s),
        Value::Null => String::new(),
        other => pyjson::str_of(other),
    }
}

/// `x or ""`.
fn or_empty(v: &Value) -> Value {
    if truthy(v) { v.clone() } else { json!("") }
}

/// The row an item becomes in the export.
pub fn csv_row(e: &Value) -> String {
    let json_or_empty = |v: &Value| if v.is_null() { json!("") } else { json!(pyjson::dumps(v)) };
    let cells = [
        cell(&e["at"]),
        cell(&e["actor"]["kind"]),
        cell(&e["actor"]["id"]),
        cell(&or_empty(&e["actor"]["label"])),
        cell(&e["action"]),
        cell(&or_empty(&e["target"]["type"])),
        cell(&or_empty(&e["target"]["id"])),
        cell(&or_empty(&e["target"]["label"])),
        cell(&json_or_empty(&e["before"])),
        cell(&json_or_empty(&e["after"])),
        cell(&e["status"]),
    ];
    csv_line(&cells)
}

/// Where the export generator is between two chunks.
pub struct ExportState {
    pub pool: PgPool,
    pub tenant_id: String,
    pub filters: Filters,
    pub emitted: i64,
    pub offset: i64,
    pub phase: ExportPhase,
}

#[derive(PartialEq)]
pub enum ExportPhase {
    Header,
    Pages,
    Truncated,
    Done,
}

/// `export_csv`, one chunk per page: header, pages of 200, then the
/// truncation line when the cap bit (with Python's exact stop conditions).
pub async fn export_next(mut st: ExportState) -> Option<(Result<String, std::io::Error>, ExportState)> {
    loop {
        match st.phase {
            ExportPhase::Done => return None,
            ExportPhase::Header => {
                st.phase = ExportPhase::Pages;
                return Some((Ok(csv_line(&CSV_COLUMNS)), st));
            }
            ExportPhase::Truncated => {
                st.phase = ExportPhase::Done;
                let line = csv_line(&[format!("# export truncated at {EXPORT_MAX_ROWS} rows; narrow the filters")]);
                return Some((Ok(line), st));
            }
            ExportPhase::Pages => {
                // `while emitted < EXPORT_MAX_ROWS`
                if st.emitted >= EXPORT_MAX_ROWS {
                    st.phase = ExportPhase::Truncated;
                    continue;
                }
                let page = match list_audit(&st.pool, &st.tenant_id, EXPORT_BATCH, st.offset, &st.filters).await {
                    Ok(p) => p,
                    Err(_) => {
                        st.phase = ExportPhase::Done;
                        return Some((Err(std::io::Error::other("audit export failed mid-stream")), st));
                    }
                };
                if page.items.is_empty() {
                    st.phase = ExportPhase::Done;
                    continue;
                }
                let mut chunk = String::new();
                for e in &page.items {
                    chunk.push_str(&csv_row(e));
                    st.emitted += 1;
                    if st.emitted >= EXPORT_MAX_ROWS {
                        break;
                    }
                }
                st.offset += page.items.len() as i64;
                if st.offset >= page.total {
                    st.phase = ExportPhase::Done;
                }
                return Some((Ok(chunk), st));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(action: &str, ctx: Value, resource: Option<&str>) -> Row {
        Row {
            id: "act_1".into(),
            user_id: "usr_1".into(),
            action: action.into(),
            resource: resource.map(str::to_string),
            context: Some(ctx),
            status: "success".into(),
            created_at: None,
        }
    }

    #[test]
    fn legacy_rows_take_their_label_and_details() {
        let r = row("account.api_key_created",
            json!({"key_name": "nightly", "role": "analyst", "severity": "warning", "kind": "account",
                   "reason": "changed_by_an_account_admin"}), Some("nightly"));
        let mut labels = HashMap::new();
        labels.insert("usr_1".to_string(), "a@b.c".to_string());
        let v = normalise(&r, &labels).unwrap();
        assert_eq!(v["action"], "api_key.created");
        assert_eq!(v["target"], json!({"type": "api_key", "id": "nightly", "label": "nightly"}));
        assert_eq!(v["after"], json!({"key_name": "nightly", "role": "analyst"}));
        assert_eq!(v["actor"], json!({"id": "usr_1", "kind": "user", "label": "a@b.c"}));
    }

    #[test]
    fn catalogued_rows_fall_back_to_the_resource() {
        let r = row("audit.webhook.deleted", json!({"target_type": "webhook", "target_id": "", "actor_kind": "api_key"}), Some("w1"));
        let v = normalise(&r, &HashMap::new()).unwrap();
        assert_eq!(v["target"]["id"], "w1");
        assert_eq!(v["actor"]["kind"], "api_key");
        assert_eq!(v["before"], Value::Null);
    }

    #[test]
    fn role_change_and_empty_details() {
        let r = row("account.user_role_changed", json!({"previous_role": "viewer", "role": "analyst", "email": "x@y"}), None);
        let v = normalise(&r, &HashMap::new()).unwrap();
        assert_eq!(v["before"], json!({"role": "viewer"}));
        assert_eq!(v["after"], json!({"role": "analyst"}));
        assert_eq!(v["target"]["label"], "x@y");
        let r = row("api_write", json!({"severity": "info"}), None);
        assert_eq!(normalise(&r, &HashMap::new()).unwrap()["after"], Value::Null);
    }

    #[test]
    fn csv_quoting_and_formula_guard() {
        // python -c "import csv,io;b=io.StringIO();csv.writer(b).writerow(['a,b','q\"','x',None]);print(repr(b.getvalue()))"
        assert_eq!(csv_line(&["a,b", "q\"", "x", ""]), "\"a,b\",\"q\"\"\",x,\r\n");
        assert_eq!(csv_safe("=cmd"), "'=cmd");
        assert_eq!(csv_safe("-5"), "'-5");
        assert_eq!(csv_safe("ok"), "ok");
        let e = json!({"at": "2026-10-05T00:00:00+00:00", "actor": {"id": "u", "kind": "user", "label": null},
                       "action": "x.y", "target": {"type": "t", "id": null, "label": 0},
                       "before": null, "after": {"rows": 3, "format": "csv"}, "status": "success"});
        assert_eq!(csv_row(&e),
            "2026-10-05T00:00:00+00:00,user,u,,x.y,t,,,,\"{\"\"rows\"\": 3, \"\"format\"\": \"\"csv\"\"}\",success\r\n");
    }

    #[test]
    fn note_filters_keep_blank_values() {
        let f = Filters { actor: Some(String::new()), date_to: NaiveDate::from_ymd_opt(2026, 10, 5), ..Default::default() };
        assert_eq!(Value::Object(f.as_note()), json!({"actor": "", "date_to": "2026-10-05"}));
    }
}
