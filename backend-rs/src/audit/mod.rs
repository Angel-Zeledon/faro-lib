//! The audit trail: `backend/audit/` (catalog + reader) and the writer half of
//! `backend/middleware/audit_trail.py`.
//!
//! Python writes the `audit.<noun>.<verb>` row in a middleware, after any
//! response under 400 on a catalogued route, with the actor the auth guard
//! published. Here the handler calls [`record`] once it knows it succeeded,
//! with the same inputs the middleware reads: the matched route template, the
//! path parameter that names the object, and the handler's own [`Note`].

pub mod catalog;
pub mod service;

use serde_json::{json, Map, Value};

use crate::activity::log_action;
use crate::auth::RequestActors;
use crate::pyjson;
use crate::state::AppState;

/// `backend.audit.note(request, ...)`: what the handler knows beyond the path.
#[derive(Debug, Default, Clone)]
pub struct Note {
    pub target_id: Option<String>,
    pub label: Option<String>,
    pub before: Option<Value>,
    pub after: Option<Value>,
}

const MAX_SUMMARY_BYTES: usize = 4000;

/// `_clamp`: a summary longer than 4000 characters of `json.dumps` is cut to
/// `{"truncated": true}`.
pub fn clamp(value: Option<Value>) -> Value {
    match value {
        None => Value::Null,
        Some(v) => {
            if pyjson::dumps(&v).chars().count() <= MAX_SUMMARY_BYTES {
                v
            } else {
                json!({"truncated": true})
            }
        }
    }
}

/// The context `AuditMiddleware._record` stores, in its key order.
pub fn context(
    route: &catalog::AuditRoute,
    target_id: Option<String>,
    note: Note,
    machine: bool,
    status_code: u16,
) -> Map<String, Value> {
    let mut c = Map::new();
    c.insert("target_type".into(), json!(route.target_type));
    c.insert("target_id".into(), target_id.map(Value::String).unwrap_or(Value::Null));
    c.insert("target_label".into(), note.label.map(Value::String).unwrap_or(Value::Null));
    c.insert("before".into(), clamp(note.before));
    c.insert("after".into(), clamp(note.after));
    c.insert("actor_kind".into(), json!(if machine { "api_key" } else { "user" }));
    c.insert("method".into(), json!(route.method));
    c.insert("path".into(), json!(route.template));
    c.insert("status_code".into(), json!(status_code));
    c
}

/// Write the audit row for a successful call to a catalogued route. Never
/// fails the caller: like the middleware, a write failure is logged.
pub async fn record(
    state: &AppState,
    actors: &RequestActors,
    method: &str,
    template: &str,
    path_target: Option<&str>,
    note: Note,
    status_code: u16,
) {
    if status_code >= 400 {
        return;
    }
    let Some(route) = catalog::route(method, template) else { return };
    let machine = actors.machine();
    let is_machine = machine.is_some();
    let Some((tenant_id, actor_id)) = machine.or_else(|| actors.person()) else { return };
    let target_id = note
        .target_id
        .clone()
        .or_else(|| route.target_param.and(path_target).map(str::to_string));
    let ctx = context(route, target_id.clone(), note, is_machine, status_code);
    if let Err(e) = log_action(&state.pool, &tenant_id, &actor_id, &catalog::audit_action(route.action),
        target_id.as_deref(), &Value::Object(ctx), "success").await
    {
        tracing::warn!(error = %e, "audit trail not recorded");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn context_shape_matches_the_middleware() {
        let route = catalog::route("DELETE", "/webhooks/{webhook_id}").unwrap();
        let c = context(route, Some("wh1".into()), Note::default(), true, 200);
        assert_eq!(
            serde_json::to_string(&c).unwrap(),
            r#"{"target_type":"webhook","target_id":"wh1","target_label":null,"before":null,"after":null,"actor_kind":"api_key","method":"DELETE","path":"/webhooks/{webhook_id}","status_code":200}"#
        );
    }

    #[test]
    fn oversized_summaries_are_cut() {
        assert_eq!(clamp(Some(json!({"a": "x".repeat(3992)}))), json!({"truncated": true}));
        // {"a": "<3991 x>"} is exactly 4000 characters: kept.
        let edge = json!({"a": "x".repeat(3991)});
        assert_eq!(clamp(Some(edge.clone())), edge);
        let small = json!({"a": "x".repeat(3980)});
        assert_eq!(clamp(Some(small.clone())), small);
        assert_eq!(clamp(None), Value::Null);
    }
}
