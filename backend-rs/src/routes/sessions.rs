//! Sessions: the read endpoints and archive / restore of
//! `backend/api/v1/sessions.py` (with `backend/sessions/service.py`).
//!
//! Migrated:
//! * `GET /sessions`                  list (active / archived / all)
//! * `GET /sessions/summary`          the sessions library (search, filters, sort)
//! * `GET /sessions/{id}`             one session
//! * `DELETE /sessions/{id}`          ARCHIVES (sessions are permanent)
//! * `POST /sessions/{id}/restore`    back into the working list, under the
//!                                    `max_sessions` ceiling
//!
//! NOT migrated, stays on Python: `POST /sessions` (it is catalogued in the
//! audit trail and creates the `session_configs` row the wizard owns),
//! `PATCH /sessions/{id}`, and everything under `/sessions/{id}/...` that
//! trains, configures or reads results (it touches the engine or `storage/`).
//!
//! The rule this file must never break (CLAUDE.md, 2026-10-04): nothing here
//! deletes a session row. DELETE stamps `archived_at`; restore clears it.
//!
//! The small query-string and row helpers at the bottom are shared by the
//! schedule and spike-edit modules, which read the same kind of rows.

use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use percent_encoding::percent_decode;
use regex::Regex;
use serde_json::{json, Map, Value};
use sqlx::postgres::PgRow;
use sqlx::{Column, PgPool, Row, TypeInfo};

use crate::activity::log_action;
use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::entitlements::{tenant_limits, tenant_tier, TenantRow};
use crate::error::ApiError;
use crate::pycompat::{isoformat_date, isoformat_utc, py_strip};
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{str_field, Errors, Field, StrRules};

/// The `sessions` tag is exposed: a read key reads, a write key archives.
pub const ROUTE_READ: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: false }, is_mcp: false };
pub const ROUTE_WRITE: RouteAuth = RouteAuth { exposure: Exposure::Exposed { write: true }, is_mcp: false };

// ── Shared helpers: query strings ────────────────────────────────────────────

/// `urllib.parse.parse_qsl(qs, keep_blank_values=True)` as Starlette uses it:
/// split on `&` only, `+` is a space, percent-decoding as UTF-8 with
/// replacement, a pair without `=` gets an empty value, empty pieces skipped.
pub fn query_pairs(uri: &Uri) -> Vec<(String, String)> {
    let Some(qs) = uri.query() else { return Vec::new() };
    let decode = |s: &str| -> String {
        let plus = s.replace('+', " ");
        percent_decode(plus.as_bytes()).decode_utf8_lossy().into_owned()
    };
    qs.split('&')
        .filter(|p| !p.is_empty())
        .map(|p| match p.split_once('=') {
            Some((k, v)) => (decode(k), decode(v)),
            None => (decode(p), String::new()),
        })
        .collect()
}

/// Scalar query parameters as FastAPI reads them: the LAST occurrence wins
/// (Starlette's `QueryParams.get`). Values are JSON strings, so the pydantic
/// helpers in `validation` apply to them unchanged.
pub fn query_map(pairs: &[(String, String)]) -> Map<String, Value> {
    let mut m = Map::new();
    for (k, v) in pairs {
        m.insert(k.clone(), Value::String(v.clone()));
    }
    m
}

fn query_loc(name: &str) -> Vec<Value> {
    vec![Value::String("query".into()), Value::String(name.into())]
}

/// A pydantic int parsed from a query string: `None` when absent.
/// `Err(())` after pushing the error. Values beyond i64 saturate (Python
/// keeps a bigint; every caller either bounds it or hands it to Postgres,
/// which refuses it with the same 500 Python gets).
pub fn int_query(
    errs: &mut Errors,
    q: &Map<String, Value>,
    name: &str,
    ge: Option<i64>,
    le: Option<i64>,
) -> Option<i64> {
    let raw = match q.get(name) {
        Some(Value::String(s)) => s.clone(),
        _ => return None,
    };
    let at = query_loc(name);
    let Some(v) = py_int_from_str(&raw) else {
        errs.push("int_parsing", &at, "Input should be a valid integer, unable to parse string as an integer".into(),
            &Value::String(raw), None);
        return None;
    };
    if let Some(g) = ge {
        if v < g as i128 {
            errs.push("greater_than_equal", &at, format!("Input should be greater than or equal to {g}"),
                &Value::String(raw), Some(json!({"ge": g})));
            return None;
        }
    }
    if let Some(l) = le {
        if v > l as i128 {
            errs.push("less_than_equal", &at, format!("Input should be less than or equal to {l}"),
                &Value::String(raw), Some(json!({"le": l})));
            return None;
        }
    }
    Some(v.clamp(i64::MIN as i128, i64::MAX as i128) as i64)
}

/// pydantic-core's lax `str -> int`: surrounding whitespace stripped, an
/// optional sign, ASCII digits with single underscores BETWEEN digits, and an
/// optional fraction made only of zeros (`"5.00"` is 5, `"5."` is not).
/// Saturates at about 1e30, which is beyond every bound a caller checks.
pub fn py_int_from_str(raw: &str) -> Option<i128> {
    let s = py_strip(raw);
    let (neg, body) = match s.as_bytes().first() {
        Some(b'-') => (true, &s[1..]),
        Some(b'+') => (false, &s[1..]),
        _ => (false, s),
    };
    let (int_part, frac) = match body.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (body, None),
    };
    if let Some(f) = frac {
        if f.is_empty() || !f.bytes().all(|b| b == b'0') {
            return None;
        }
    }
    if int_part.is_empty() {
        return None;
    }
    let bytes = int_part.as_bytes();
    if bytes[0] == b'_' || bytes[bytes.len() - 1] == b'_' {
        return None;
    }
    let mut value: i128 = 0;
    let mut prev_underscore = false;
    for &b in bytes {
        match b {
            b'0'..=b'9' => {
                value = (value * 10 + i128::from(b - b'0')).min(10i128.pow(30));
                prev_underscore = false;
            }
            b'_' if !prev_underscore => prev_underscore = true,
            _ => return None,
        }
    }
    Some(if neg { -value } else { value })
}

/// A pydantic `bool` from a query string (`true/1/yes/on/t/y`, case-blind,
/// no stripping).
pub fn bool_query(errs: &mut Errors, q: &Map<String, Value>, name: &str) -> Option<bool> {
    let raw = match q.get(name) {
        Some(Value::String(s)) => s.clone(),
        _ => return None,
    };
    match raw.to_lowercase().as_str() {
        "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
        "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
        _ => {
            errs.push("bool_parsing", &query_loc(name),
                "Input should be a valid boolean, unable to interpret input".into(), &Value::String(raw), None);
            None
        }
    }
}

/// A `str` query parameter with `max_length` / `pattern` (pydantic-core's
/// regex engine: `$` is end-of-text only, `\d` is Unicode).
pub fn str_query(
    errs: &mut Errors,
    q: &Map<String, Value>,
    name: &str,
    max_length: Option<usize>,
    pattern: Option<(&'static str, fn(&str) -> bool)>,
) -> Option<String> {
    let rules = StrRules { min_length: None, max_length, pattern };
    match str_field(errs, q, &[Value::String("query".into())], name, false, false, &rules) {
        Field::Value(v) => Some(v),
        _ => None,
    }
}

// ── Shared helpers: rows ─────────────────────────────────────────────────────

/// One column as FastAPI's `jsonable_encoder` renders what psycopg2 returned
/// for it. Timestamps come back with the session time zone, which is UTC in
/// every StockAI database (docs/rust-migration.md, divergence 5).
pub fn column_json(row: &PgRow, i: usize) -> Result<Value, sqlx::Error> {
    let ty = row.columns()[i].type_info().name().to_ascii_uppercase();
    Ok(match ty.as_str() {
        "TEXT" | "VARCHAR" | "BPCHAR" | "NAME" | "CHAR" => {
            row.try_get::<Option<String>, _>(i)?.map(Value::String).unwrap_or(Value::Null)
        }
        "INT2" => row.try_get::<Option<i16>, _>(i)?.map(Value::from).unwrap_or(Value::Null),
        "INT4" => row.try_get::<Option<i32>, _>(i)?.map(Value::from).unwrap_or(Value::Null),
        "INT8" => row.try_get::<Option<i64>, _>(i)?.map(Value::from).unwrap_or(Value::Null),
        "FLOAT4" => row.try_get::<Option<f32>, _>(i)?.map(|f| json!(f64::from(f))).unwrap_or(Value::Null),
        "FLOAT8" => row.try_get::<Option<f64>, _>(i)?.map(|f| json!(f)).unwrap_or(Value::Null),
        "BOOL" => row.try_get::<Option<bool>, _>(i)?.map(Value::Bool).unwrap_or(Value::Null),
        "TIMESTAMPTZ" => row
            .try_get::<Option<DateTime<Utc>>, _>(i)?
            .map(|d| Value::String(isoformat_utc(&d)))
            .unwrap_or(Value::Null),
        "TIMESTAMP" => row
            .try_get::<Option<NaiveDateTime>, _>(i)?
            .map(|d| Value::String(naive_isoformat(&d)))
            .unwrap_or(Value::Null),
        "DATE" => row
            .try_get::<Option<NaiveDate>, _>(i)?
            .map(|d| Value::String(isoformat_date(&d)))
            .unwrap_or(Value::Null),
        "JSON" | "JSONB" => row.try_get::<Option<Value>, _>(i)?.unwrap_or(Value::Null),
        "TEXT[]" | "VARCHAR[]" => row
            .try_get::<Option<Vec<String>>, _>(i)?
            .map(|v| Value::Array(v.into_iter().map(Value::String).collect()))
            .unwrap_or(Value::Null),
        other => {
            // NUMERIC and friends: decoding them needs a sqlx feature this crate
            // does not enable. No migrated query selects one; a schema change
            // that adds one must fail loudly, not render something different.
            tracing::error!(column = row.columns()[i].name(), r#type = other, "unsupported column type");
            return Err(sqlx::Error::ColumnDecode {
                index: row.columns()[i].name().to_string(),
                source: format!("unsupported type {other}").into(),
            });
        }
    })
}

/// `datetime.isoformat()` of a naive datetime.
fn naive_isoformat(d: &NaiveDateTime) -> String {
    let micros = d.and_utc().timestamp_subsec_micros();
    let base = d.format("%Y-%m-%dT%H:%M:%S").to_string();
    if micros == 0 { base } else { format!("{base}.{micros:06}") }
}

/// A whole row, keys in column order (RealDictCursor).
pub fn row_json(row: &PgRow) -> Result<Map<String, Value>, sqlx::Error> {
    let mut m = Map::new();
    for (i, c) in row.columns().iter().enumerate() {
        m.insert(c.name().to_string(), column_json(row, i)?);
    }
    Ok(m)
}

/// `session_svc._fmt`: the row plus a `session_id` alias of `id`.
fn fmt_session(mut m: Map<String, Value>) -> Map<String, Value> {
    let id = m.get("id").cloned().unwrap_or(Value::Null);
    m.insert("session_id".into(), id);
    m
}

/// `session_svc.get_session`: `SELECT *` of one session of this tenant.
pub async fn get_session(pool: &PgPool, tenant_id: &str, session_id: &str) -> Result<Option<Map<String, Value>>, ApiError> {
    let row = sqlx::query("SELECT * FROM sessions WHERE id = $1 AND tenant_id = $2")
        .bind(session_id)
        .bind(tenant_id)
        .fetch_optional(pool)
        .await?;
    Ok(match row {
        Some(r) => Some(fmt_session(row_json(&r)?)),
        None => None,
    })
}

pub fn session_not_found() -> ApiError {
    ApiError::app("session_not_found", "Session not found", 404, json!({}))
}

// ── Library scope and filters ────────────────────────────────────────────────

const ARCHIVED_PATTERN: &str = "^(active|archived|all)$";

fn archived_ok(s: &str) -> bool {
    matches!(s, "active" | "archived" | "all")
}

fn date_pattern_ok(s: &str) -> bool {
    // `^\d{4}-\d{2}-\d{2}$` with Unicode \d, as pydantic-core's regex reads it.
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^\d{4}-\d{2}-\d{2}$").expect("valid")).is_match(s)
}

fn sort_ok(s: &str) -> bool {
    matches!(s, "created_at" | "updated_at" | "name" | "status" | "horizon" | "accuracy")
}

fn order_ok(s: &str) -> bool {
    matches!(s, "asc" | "desc")
}

/// `_archived_clause`.
fn archived_clause(archived: &str, alias: &str) -> String {
    match archived {
        "archived" => format!(" AND {alias}archived_at IS NOT NULL"),
        "all" => String::new(),
        _ => format!(" AND {alias}archived_at IS NULL"),
    }
}

/// `count_sessions`.
async fn count_sessions(
    conn: &mut sqlx::PgConnection,
    tenant_id: &str,
    archived: &str,
) -> Result<i64, sqlx::Error> {
    let (n,): (i64,) = sqlx::query_as(&format!(
        "SELECT COUNT(*) AS cnt FROM sessions WHERE tenant_id = $1{}",
        archived_clause(archived, "")
    ))
    .bind(tenant_id)
    .fetch_one(conn)
    .await?;
    Ok(n)
}

// ── Routes ───────────────────────────────────────────────────────────────────

/// The migrated session routes. `POST /sessions` and `PATCH /sessions/{id}`
/// are NOT here; the proxy routes them to Python by method.
pub fn router() -> axum::Router<AppState> {
    use axum::routing::{get, post};
    axum::Router::new()
        .route("/api/v1/sessions", get(list_sessions))
        // DELETE on the literal path: Starlette falls through to
        // DELETE /{session_id} with the id "summary" (a 404 session_not_found).
        .route("/api/v1/sessions/summary", get(list_session_summaries).delete(archive_summary_literal))
        .route("/api/v1/sessions/{session_id}", get(get_one).delete(archive))
        .route("/api/v1/sessions/{session_id}/restore", post(restore))
}

// ── Handlers ─────────────────────────────────────────────────────────────────

pub async fn archive_summary_literal(
    state: State<AppState>,
    actors: Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    archive(state, actors, Path("summary".to_string()), headers).await
}

pub async fn list_sessions(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let q = query_map(&query_pairs(&uri));
    let mut errs = Errors::default();
    let skip = int_query(&mut errs, &q, "skip", Some(0), None);
    let limit = int_query(&mut errs, &q, "limit", Some(1), Some(500));
    let archived = str_query(&mut errs, &q, "archived", None, Some((ARCHIVED_PATTERN, archived_ok)));
    errs.into_result()?;
    let skip = skip.unwrap_or(0);
    let limit = limit.unwrap_or(50);
    let archived = archived.unwrap_or_else(|| "active".into());

    let rows = sqlx::query(&format!(
        "SELECT * FROM sessions WHERE tenant_id = $1{} ORDER BY updated_at DESC LIMIT $2 OFFSET $3",
        archived_clause(&archived, "")
    ))
    .bind(&user.tenant_id)
    .bind(limit)
    .bind(skip)
    .fetch_all(&state.pool)
    .await?;
    let mut items = Vec::with_capacity(rows.len());
    for r in &rows {
        items.push(Value::Object(fmt_session(row_json(r)?)));
    }
    let mut conn = state.pool.acquire().await?;
    let total = count_sessions(&mut conn, &user.tenant_id, &archived).await?;
    Ok(ok(json!({"items": items, "total": total, "skip": skip, "limit": limit})))
}

/// `_ACCURACY_SQL`, `_MODELS_SQL`, `_HAS_ROWS` verbatim.
const ACCURACY_SQL: &str = "
    (SELECT 1 - AVG(best) FROM (
         SELECT MIN((e->>'wape')::numeric) AS best
         FROM jsonb_array_elements(r.training_result->'metrics'->'rows') AS e
         WHERE COALESCE(e->>'type', '') <> 'baseline'
           AND (e->>'wape') ~ '^-?[0-9]+([.][0-9]+)?([eE][-+]?[0-9]+)?$'
         GROUP BY e->>'sku') per_sku)
";
const MODELS_SQL: &str = "
    (SELECT ARRAY_AGG(DISTINCT e->>'model')
     FROM jsonb_array_elements(r.training_result->'metrics'->'rows') AS e
     WHERE COALESCE(e->>'type', '') <> 'baseline' AND e->>'model' IS NOT NULL)
";
const HAS_ROWS: &str = "jsonb_typeof(r.training_result->'metrics'->'rows') = 'array'";

fn sort_column(sort: &str) -> &'static str {
    match sort {
        "updated_at" => "s.updated_at",
        "name" => "LOWER(s.name)",
        "status" => "s.status",
        "horizon" => "horizon_value",
        "accuracy" => "accuracy_value",
        _ => "s.created_at",
    }
}

/// `_like_pattern`: `%text%` with the user's own `%`, `_` and `\` literal.
fn like_pattern(text: &str) -> String {
    let escaped = py_strip(text).replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_");
    format!("%{escaped}%")
}

/// A bind parameter of the library query, in placeholder order.
enum P {
    Text(String),
    TextList(Vec<String>),
}

pub async fn list_session_summaries(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    let pairs = query_pairs(&uri);
    let q = query_map(&pairs);
    let mut errs = Errors::default();
    // Declaration order of the Python signature: the order errors are listed.
    let skip = int_query(&mut errs, &q, "skip", Some(0), None);
    let limit = int_query(&mut errs, &q, "limit", Some(1), Some(500));
    let text = str_query(&mut errs, &q, "q", Some(200), None);
    let statuses: Option<Vec<String>> = {
        let v: Vec<String> = pairs.iter().filter(|(k, _)| k == "status").map(|(_, v)| v.clone()).collect();
        if v.is_empty() { None } else { Some(v) }
    };
    let dataset_id = str_query(&mut errs, &q, "dataset_id", Some(100), None);
    let archived = str_query(&mut errs, &q, "archived", None, Some((ARCHIVED_PATTERN, archived_ok)));
    let created_from = str_query(&mut errs, &q, "created_from", None, Some((r"^\d{4}-\d{2}-\d{2}$", date_pattern_ok)));
    let created_to = str_query(&mut errs, &q, "created_to", None, Some((r"^\d{4}-\d{2}-\d{2}$", date_pattern_ok)));
    let sort = str_query(&mut errs, &q, "sort", None,
        Some(("^(created_at|updated_at|name|status|horizon|accuracy)$", sort_ok)));
    let order = str_query(&mut errs, &q, "order", None, Some(("^(asc|desc)$", order_ok)));
    errs.into_result()?;
    let skip = skip.unwrap_or(0);
    let limit = limit.unwrap_or(50);
    let archived = archived.unwrap_or_else(|| "active".into());
    let sort = sort.unwrap_or_else(|| "created_at".into());
    let order = order.unwrap_or_else(|| "desc".into());

    // _library_filters
    let mut where_ = format!("s.tenant_id = $1{}", archived_clause(&archived, "s."));
    let mut params: Vec<P> = vec![P::Text(user.tenant_id.clone())];
    let next = |params: &mut Vec<P>, p: P| -> String {
        params.push(p);
        format!("${}", params.len())
    };
    if let Some(st) = statuses.filter(|s| !s.is_empty()) {
        let ph = next(&mut params, P::TextList(st));
        where_ += &format!(" AND s.status = ANY({ph})");
    }
    if let Some(d) = dataset_id.filter(|d| !d.is_empty()) {
        let ph = next(&mut params, P::Text(d));
        where_ += &format!(" AND s.dataset_id = {ph}");
    }
    if let Some(d) = created_from.filter(|d| !d.is_empty()) {
        let ph = next(&mut params, P::Text(d));
        where_ += &format!(" AND s.created_at >= {ph}::date");
    }
    if let Some(d) = created_to.filter(|d| !d.is_empty()) {
        let ph = next(&mut params, P::Text(d));
        where_ += &format!(" AND s.created_at < ({ph}::date + 1)");
    }
    if let Some(t) = text.filter(|t| !py_strip(t).is_empty()) {
        let like = like_pattern(&t);
        let a = next(&mut params, P::Text(like.clone()));
        let b = next(&mut params, P::Text(like.clone()));
        let c = next(&mut params, P::Text(like.clone()));
        let d = next(&mut params, P::Text(like));
        where_ += &format!(
            " AND (s.name ILIKE {a} OR COALESCE(s.description, '') ILIKE {b} \
             OR COALESCE(d.name, '') ILIKE {c} OR COALESCE(d.original_filename, '') ILIKE {d})"
        );
    }

    fn bind_all<'q>(
        mut qy: sqlx::query::Query<'q, sqlx::Postgres, sqlx::postgres::PgArguments>,
        params: &'q [P],
    ) -> sqlx::query::Query<'q, sqlx::Postgres, sqlx::postgres::PgArguments> {
        for p in params {
            qy = match p {
                P::Text(s) => qy.bind(s),
                P::TextList(v) => qy.bind(v),
            };
        }
        qy
    }

    let joins = "FROM sessions s LEFT JOIN datasets d ON d.id = s.dataset_id AND d.tenant_id = s.tenant_id";
    let count_sql = format!("SELECT COUNT(*) AS cnt {joins} WHERE {where_}");
    let total: i64 = bind_all(sqlx::query(&count_sql), &params)
        .fetch_optional(&state.pool)
        .await?
        .map(|r| r.try_get::<i64, _>(0))
        .transpose()?
        .unwrap_or(0);

    let (extra_select, extra_join) = match sort.as_str() {
        "accuracy" => (
            ", a.v AS accuracy_value".to_string(),
            format!("LEFT JOIN session_results r ON r.session_id = s.id \
                     LEFT JOIN LATERAL (SELECT CASE WHEN {HAS_ROWS} THEN {ACCURACY_SQL} END AS v) a ON TRUE "),
        ),
        "horizon" => (
            ", COALESCE((c.forecast_cfg->>'horizon')::numeric::int, \
             (c.validation_cfg->>'horizon')::numeric::int) AS horizon_value".to_string(),
            "LEFT JOIN session_configs c ON c.session_id = s.id ".to_string(),
        ),
        _ => (String::new(), String::new()),
    };
    let direction = if order.to_lowercase() == "asc" { "ASC" } else { "DESC" };
    let n = params.len();
    let id_sql = format!(
        "SELECT s.id{extra_select} {joins} {extra_join}WHERE {where_} \
         ORDER BY {} {direction} NULLS LAST, s.created_at DESC, s.id LIMIT ${} OFFSET ${}",
        sort_column(&sort), n + 1, n + 2
    );
    let id_rows = bind_all(sqlx::query(&id_sql), &params)
        .bind(limit)
        .bind(skip)
        .fetch_all(&state.pool)
        .await?;
    let ids: Vec<String> = id_rows.iter().map(|r| r.try_get::<String, _>("id")).collect::<Result<_, _>>()?;
    if ids.is_empty() {
        return Ok(ok(json!({"items": [], "total": total, "skip": skip, "limit": limit})));
    }

    let rows = sqlx::query(&format!(
        "SELECT s.id, s.name, s.description, s.status, s.pipeline_step,
                s.created_at, s.updated_at, s.dataset_id, s.tags,
                s.archived_at, s.is_backtest, s.backtest_source_dataset_id,
                s.backtest_holdout_periods,
                d.name AS dataset_name,
                d.original_filename AS dataset_filename,
                COALESCE((c.forecast_cfg->>'horizon')::numeric::int,
                         (c.validation_cfg->>'horizon')::numeric::int) AS horizon,
                COALESCE(s.granularity, c.granularity_cfg->>'target_freq') AS granularity,
                COALESCE(
                    (r.training_result->'metrics'->>'n_skus')::numeric::int,
                    CASE WHEN {HAS_ROWS}
                         THEN (SELECT COUNT(DISTINCT elem->>'sku')
                               FROM jsonb_array_elements(r.training_result->'metrics'->'rows') AS elem)::int
                    END
                ) AS sku_count,
                (CASE WHEN {HAS_ROWS} THEN {ACCURACY_SQL} END)::float8 AS accuracy,
                CASE WHEN {HAS_ROWS} THEN {MODELS_SQL} END AS models,
                j.error AS failure_reason
         FROM sessions s
         LEFT JOIN datasets d        ON d.id = s.dataset_id AND d.tenant_id = s.tenant_id
         LEFT JOIN session_configs c ON c.session_id = s.id
         LEFT JOIN session_results r ON r.session_id = s.id
         LEFT JOIN LATERAL (
             SELECT error FROM jobs
             WHERE session_id = s.id AND error IS NOT NULL
             ORDER BY created_at DESC LIMIT 1
         ) j ON TRUE
         WHERE s.tenant_id = $1 AND s.id = ANY($2)"
    ))
    .bind(&user.tenant_id)
    .bind(&ids)
    .fetch_all(&state.pool)
    .await?;

    let mut by_id: std::collections::HashMap<String, Map<String, Value>> = std::collections::HashMap::new();
    for r in &rows {
        let mut m = row_json(r)?;
        // `sorted(r["models"]) if r.get("models") else []`
        let mut models: Vec<String> = m
            .get("models")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
            .unwrap_or_default();
        models.sort();
        m.insert("models".into(), json!(models));
        let id = m.get("id").and_then(Value::as_str).unwrap_or_default().to_string();
        by_id.insert(id, m);
    }
    let mut out = Vec::with_capacity(ids.len());
    for sid in &ids {
        if let Some(m) = by_id.remove(sid) {
            out.push(Value::Object(fmt_session(m)));
        }
    }
    Ok(ok(json!({"items": out, "total": total, "skip": skip, "limit": limit})))
}

pub async fn get_one(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_READ, &actors).await?;
    match get_session(&state.pool, &user.tenant_id, &session_id).await? {
        Some(s) => Ok(ok(Value::Object(s))),
        None => Err(session_not_found()),
    }
}

/// `DELETE /sessions/{id}`: ARCHIVE. Nothing is erased.
pub async fn archive(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let s = get_session(&state.pool, &user.tenant_id, &session_id)
        .await?
        .ok_or_else(session_not_found)?;
    // job_service.has_in_flight_job: ask the JOB, not the session.
    let in_flight: Option<(i32,)> = sqlx::query_as(
        "SELECT 1 AS hit FROM jobs WHERE tenant_id = $1 AND session_id = $2 \
         AND status IN ('QUEUED', 'RUNNING') LIMIT 1",
    )
    .bind(&user.tenant_id)
    .bind(&session_id)
    .fetch_optional(&state.pool)
    .await?;
    if in_flight.is_some() {
        return Err(ApiError::app(
            "session_running_cannot_delete",
            "Cannot archive a session while its training is queued or running",
            409,
            json!({}),
        ));
    }
    if s.get("archived_at").map_or(true, Value::is_null) {
        sqlx::query(
            "UPDATE sessions SET archived_at = NOW(), archived_by = $1, updated_at = NOW() \
             WHERE id = $2 AND tenant_id = $3 AND archived_at IS NULL",
        )
        .bind(&user.user_id)
        .bind(&session_id)
        .bind(&user.tenant_id)
        .execute(&state.pool)
        .await?;
        // archive_session re-reads the row; Python discards it.
        get_session(&state.pool, &user.tenant_id, &session_id).await?;
        // log_action (not record_event): a plain row, raised on failure.
        let context = json!({
            "name": s.get("name").cloned().unwrap_or(Value::Null),
            "status_at_archive": s.get("status").cloned().unwrap_or(Value::Null),
            "dataset_id": s.get("dataset_id").cloned().unwrap_or(Value::Null),
        });
        log_action(&state.pool, &user.tenant_id, &user.user_id, "session.archive",
            Some(&session_id), &context, "success").await?;
    }
    Ok(StatusCode::NO_CONTENT.into_response())
}

/// `POST /sessions/{id}/restore`, under the `max_sessions` ceiling.
pub async fn restore(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE_WRITE, &actors).await?;
    auth::require_analyst_or_above(&state, &user).await?;
    let s = get_session(&state.pool, &user.tenant_id, &session_id)
        .await?
        .ok_or_else(session_not_found)?;
    if s.get("archived_at").map_or(true, Value::is_null) {
        return Ok(ok(Value::Object(s)));
    }
    // limit_guard: one transaction holding the tenant's advisory lock for the
    // count and the check.
    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
        .bind(&user.tenant_id)
        .execute(&mut *tx)
        .await?;
    let current = count_sessions(&mut tx, &user.tenant_id, "active").await?;
    if let Err(e) = enforce_limit(&state, &mut tx, &user.tenant_id, "max_sessions", current).await {
        drop(tx); // rollback, like the raising `with` block
        return Err(e);
    }
    // session_svc.restore_session runs `execute` on its own pooled
    // connection (no conn= argument), i.e. outside the guard's transaction.
    sqlx::query(
        "UPDATE sessions SET archived_at = NULL, archived_by = NULL, updated_at = NOW() \
         WHERE id = $1 AND tenant_id = $2",
    )
    .bind(&session_id)
    .bind(&user.tenant_id)
    .execute(&state.pool)
    .await?;
    let restored = get_session(&state.pool, &user.tenant_id, &session_id).await?;
    tx.commit().await?;
    log_action(&state.pool, &user.tenant_id, &user.user_id, "session.restore", Some(&session_id),
        &json!({"name": s.get("name").cloned().unwrap_or(Value::Null)}), "success").await?;
    Ok(ok(restored.map(Value::Object).unwrap_or(Value::Null)))
}

/// `entitlements.service.enforce_limit` (adding = 1). A no-op in testing
/// mode, like Python. A refusal records `limit.reached` on its own
/// connection first, so it survives the caller's rollback.
async fn enforce_limit(
    state: &AppState,
    conn: &mut sqlx::PgConnection,
    tenant_id: &str,
    limit_key: &str,
    current: i64,
) -> Result<(), ApiError> {
    if state.settings.testing_mode {
        return Ok(());
    }
    let row: Option<(String, Value, Option<DateTime<Utc>>)> =
        sqlx::query_as("SELECT tier, quota, trial_ends_at FROM tenants WHERE id = $1")
            .bind(tenant_id)
            .fetch_optional(&mut *conn)
            .await?;
    let tenant = row
        .map(|(tier, quota, trial_ends_at)| TenantRow { tier: Some(tier), quota, trial_ends_at })
        .unwrap_or_default();
    let max_allowed = tenant_limits(&tenant).get(limit_key).cloned().unwrap_or(Value::Null);
    let Some(max) = max_allowed.as_f64() else { return Ok(()) };
    if (current + 1) as f64 > max {
        let context = json!({
            "limit": limit_key,
            "ceiling": max_allowed,
            "severity": "warning",
            "kind": "limit",
            "reason": "plan_limit_reached",
            "reason_params": {"limit": limit_key, "current": current, "max": max_allowed},
        });
        if let Err(e) = log_action(&state.pool, tenant_id, "system", "limit.reached", Some(limit_key),
            &context, "error").await
        {
            tracing::error!(error = %e, tenant = tenant_id, "could not record the ceiling");
        }
        return Err(ApiError::http_dict(403, json!({
            "code": "PLAN_LIMIT_REACHED",
            "limit": limit_key,
            "current": current,
            "max": max_allowed,
            "tier": tenant_tier(&tenant),
        })));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_strings_parse_like_parse_qsl() {
        let uri: Uri = "/x?a=1&b=&c&&d=x+y%20z&a=2&e=%E2%82%AC".parse().unwrap();
        let p = query_pairs(&uri);
        assert_eq!(p, vec![
            ("a".into(), "1".into()), ("b".into(), "".into()), ("c".into(), "".into()),
            ("d".into(), "x y z".into()), ("a".into(), "2".into()), ("e".into(), "€".into()),
        ]);
        assert_eq!(query_map(&p)["a"], "2");
    }

    #[test]
    fn ints_are_parsed_like_pydantic() {
        for (s, v) in [("5", 5), (" 5 ", 5), ("+5", 5), ("-0", 0), ("5.0", 5), ("5.00", 5), ("1_000", 1000),
                       ("00012", 12), ("\t7\n", 7), ("-1", -1)] {
            assert_eq!(py_int_from_str(s), Some(v), "{s}");
        }
        for s in ["5.5", "1e3", "", "abc", "0x10", "1__0", "_1", "5.", ".5", "1_", "+", "-"] {
            assert_eq!(py_int_from_str(s), None, "{s}");
        }
        assert!(py_int_from_str("99999999999999999999999").unwrap() > 500);
    }

    #[test]
    fn int_errors_carry_pydantic_shapes() {
        let mut e = Errors::default();
        let q = query_map(&[("limit".into(), "501".into()), ("skip".into(), "-1".into())]);
        int_query(&mut e, &q, "skip", Some(0), None);
        int_query(&mut e, &q, "limit", Some(1), Some(500));
        assert_eq!(e.0[0]["type"], "greater_than_equal");
        assert_eq!(e.0[0]["loc"], json!(["query", "skip"]));
        assert_eq!(e.0[1]["msg"], "Input should be less than or equal to 500");
        assert_eq!(e.0[1]["ctx"], json!({"le": 500}));
    }

    #[test]
    fn like_pattern_escapes_wildcards() {
        assert_eq!(like_pattern("  50%_a\\b "), "%50\\%\\_a\\\\b%");
    }

    #[test]
    fn archived_clause_scopes() {
        assert_eq!(archived_clause("active", "s."), " AND s.archived_at IS NULL");
        assert_eq!(archived_clause("archived", ""), " AND archived_at IS NOT NULL");
        assert_eq!(archived_clause("all", ""), "");
    }

    #[test]
    fn unicode_digits_match_the_date_pattern() {
        assert!(date_pattern_ok("2026-01-02"));
        assert!(date_pattern_ok("\u{0662}026-01-02"));
        assert!(!date_pattern_ok("2026-1-02"));
        assert!(!date_pattern_ok("2026-01-02\n"));
    }
}
