//! The periodic materialiser: a background task of the Rust service.
//!
//! Why here and not in the Python worker: the schedules' routes and their
//! materialisation core are Rust, and a loop that called into them from
//! Python would need a second implementation of the date rules. The loop only
//! calls `materialise::run_pass`, which is idempotent (unique index, inserts
//! `ON CONFLICT DO NOTHING`), so two replicas, or a pass racing an edit, are
//! harmless.
//!
//! Freshness: every pass is recorded in `system_loop_runs` under the loop
//! name `recurring_deliveries` (the table `/health` reads for the Python
//! loops), with status `completed`, or `failed` and the number of schedules
//! that failed. A loop that has not run shows a stale `last_run_at`; an
//! installation that never schedules anything shows `completed` passes of
//! zero schedules. Schedules created or edited through the API materialise
//! immediately, in the request.
//!
//! Environment (read here, not in `Settings`: Rust-only knobs):
//! * `RECURRING_MATERIALISER_ENABLED`  `false` turns the loop off (default on)
//! * `RECURRING_MATERIALISER_INTERVAL_SECS`  seconds between passes, default
//!   21600 (6 h), minimum 1

use std::time::Duration;

use chrono::{DateTime, DurationRound, TimeDelta, Utc};
use sqlx::PgPool;

use super::materialise::{run_pass, today, PassSummary};

pub const LOOP_NAME: &str = "recurring_deliveries";
const DEFAULT_INTERVAL_SECS: u64 = 6 * 3600;

pub fn enabled() -> bool {
    !matches!(
        std::env::var("RECURRING_MATERIALISER_ENABLED").ok().as_deref().map(str::trim),
        Some("false") | Some("0") | Some("no") | Some("off")
    )
}

pub fn interval_secs() -> u64 {
    std::env::var("RECURRING_MATERIALISER_INTERVAL_SECS")
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .map(|v| v.max(1))
        .unwrap_or(DEFAULT_INTERVAL_SECS)
}

/// `(status, error)` a pass is recorded with.
pub fn verdict(summary: &PassSummary) -> (&'static str, Option<String>) {
    if summary.failed == 0 {
        ("completed", None)
    } else {
        (
            "failed",
            Some(format!("{} of {} schedules failed to materialise", summary.failed, summary.schedules)),
        )
    }
}

/// `loop_state.mark_run`: one row per loop. Never takes the loop down.
pub async fn mark_run(pool: &PgPool, boundary: DateTime<Utc>, status: &str, error: Option<&str>) {
    let r = sqlx::query(
        "INSERT INTO system_loop_runs (loop, last_boundary, last_run_at, last_status, last_error)
         VALUES ($1, $2, NOW(), $3, $4)
         ON CONFLICT (loop) DO UPDATE
            SET last_boundary = EXCLUDED.last_boundary, last_run_at = EXCLUDED.last_run_at,
                last_status = EXCLUDED.last_status, last_error = EXCLUDED.last_error",
    )
    .bind(LOOP_NAME)
    .bind(boundary)
    .bind(status)
    .bind(error)
    .execute(pool)
    .await;
    if let Err(e) = r {
        tracing::error!(error = %e, "recurring_deliveries: could not record the run");
    }
}

async fn last_run(pool: &PgPool) -> Option<DateTime<Utc>> {
    sqlx::query_as::<_, (Option<DateTime<Utc>>,)>("SELECT last_run_at FROM system_loop_runs WHERE loop = $1")
        .bind(LOOP_NAME)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
        .and_then(|r| r.0)
}

/// One pass, recorded. Returns what it did.
pub async fn run_once(pool: &PgPool) -> Result<PassSummary, sqlx::Error> {
    let boundary = Utc::now().duration_trunc(TimeDelta::hours(1)).unwrap_or_else(|_| Utc::now());
    match run_pass(pool, None, today()).await {
        Ok(summary) => {
            let (status, error) = verdict(&summary);
            mark_run(pool, boundary, status, error.as_deref()).await;
            tracing::info!(schedules = summary.schedules, created = summary.created,
                failed = summary.failed, "recurring delivery materialisation");
            Ok(summary)
        }
        Err(e) => {
            mark_run(pool, boundary, "failed", Some(&e.to_string().chars().take(300).collect::<String>())).await;
            Err(e)
        }
    }
}

/// Start the loop. A pass runs at start when the last one is older than the
/// interval (or never happened), then whenever the interval has elapsed since
/// the last recorded run, so a restart neither skips nor doubles a pass.
pub fn spawn(pool: PgPool) {
    if !enabled() {
        tracing::warn!("recurring delivery materialiser is OFF (RECURRING_MATERIALISER_ENABLED=false)");
        return;
    }
    let interval = interval_secs();
    tokio::spawn(async move {
        let tick = Duration::from_secs(interval.min(600));
        tracing::info!(interval_secs = interval, "recurring delivery materialiser started");
        loop {
            let due = match last_run(&pool).await {
                Some(at) => Utc::now().signed_duration_since(at).num_seconds() >= interval as i64,
                None => true,
            };
            if due {
                if let Err(e) = run_once(&pool).await {
                    tracing::error!(error = %e, "recurring delivery materialiser pass failed");
                }
            }
            tokio::time::sleep(tick).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pass_with_failures_is_reported_failed() {
        let ok = PassSummary { schedules: 3, created: 5, failed: 0 };
        assert_eq!(verdict(&ok), ("completed", None));
        let bad = PassSummary { schedules: 3, created: 1, failed: 2 };
        let (status, err) = verdict(&bad);
        assert_eq!(status, "failed");
        assert_eq!(err.as_deref(), Some("2 of 3 schedules failed to materialise"));
    }
}
