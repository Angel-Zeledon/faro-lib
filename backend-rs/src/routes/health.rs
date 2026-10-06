//! `GET /health` (backend/main.py::health): liveness plus which services can
//! work. Unauthenticated and outside the envelope, like Python.
//!
//! It answers 200 with `status: "degraded"` when the database is unreachable,
//! never a bodiless 500: a health check that dies with the thing it reports on
//! cannot report.

use axum::extract::State;
use axum::Json;
use chrono::{DateTime, Utc};
use serde_json::{json, Value};

use crate::pycompat::isoformat_utc;
use crate::service_config;
use crate::state::AppState;

/// `backend/workers/loop_state.py::LOOPS`, in order.
const LOOPS: [&str; 4] = ["inventory_alerts", "monthly_overstock", "operator_digest", "recurring_deliveries"];

type LoopRow = (String, Option<DateTime<Utc>>, Option<DateTime<Utc>>, Option<String>, Option<String>);

pub async fn health(State(state): State<AppState>) -> Json<Value> {
    let (database_ok, queued) = match sqlx::query_as::<_, (i64,)>(
        "SELECT COUNT(*) FROM jobs WHERE status IN ('RUNNING', 'QUEUED')",
    )
    .fetch_one(&state.pool)
    .await
    {
        Ok((n,)) => (true, n),
        Err(e) => {
            tracing::error!(error = %e, "Health check: database unreachable");
            (false, 0)
        }
    };

    let services = service_config::service_states(&state.pool, &state.settings).await;

    let mut loops: Vec<Value> = Vec::new();
    if database_ok {
        match sqlx::query_as::<_, LoopRow>(
            "SELECT loop, last_boundary, last_run_at, last_status, last_error
               FROM system_loop_runs ORDER BY loop",
        )
        .fetch_all(&state.pool)
        .await
        {
            Ok(rows) => {
                for name in LOOPS {
                    let row = rows.iter().find(|r| r.0 == name);
                    loops.push(json!({
                        "loop": name,
                        "last_boundary": row.and_then(|r| r.1.as_ref()).map(isoformat_utc),
                        "last_run_at": row.and_then(|r| r.2.as_ref()).map(isoformat_utc),
                        "last_status": row.and_then(|r| r.3.clone()),
                        "last_error": row.and_then(|r| r.4.clone()),
                    }));
                }
            }
            Err(e) => tracing::error!(error = %e, "Health check: loop status failed"),
        }
    }

    Json(json!({
        "status": if database_ok { "ok" } else { "degraded" },
        "version": state.settings.app_version,
        "queued_jobs": queued,
        "database": database_ok,
        "services": services,
        "loops": loops,
    }))
}
