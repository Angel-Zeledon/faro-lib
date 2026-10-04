"""Queryable history of per-model training accuracy, across sessions.

`session_results.training_result` holds the full metrics blob for a session,
but only its latest run — there was nowhere to ask "is this session's LightGBM
worse than the one two weeks ago". `runner.py` calls `record_training_metrics`
right after `engine.get_metrics()`, so this is a plain persist of numbers the
engine already computed, not a new measurement.
"""
from backend.db.connection import execute, query
from backend.utils.ids import generate_id

_METRIC_FIELDS = ("avg_mae", "avg_rmse", "avg_wape", "avg_bias", "avg_mape", "avg_smape")


def record_training_metrics(tenant_id: str, session_id: str, by_model: dict) -> None:
    """Upsert one row per model from `engine.get_metrics()["by_model"]`.

    `by_model` maps model name -> {avg_mae, avg_rmse, avg_wape, avg_bias,
    avg_mape, avg_smape}, exactly the columns here. Retraining the same
    session overwrites its existing rows (see the migration's UNIQUE).
    """
    for model, agg in by_model.items():
        values = [agg.get(field) for field in _METRIC_FIELDS]
        execute(
            """INSERT INTO training_run_metrics
               (id, tenant_id, session_id, model, avg_mae, avg_rmse, avg_wape,
                avg_bias, avg_mape, avg_smape, trained_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
               ON CONFLICT (session_id, model) DO UPDATE SET
                 avg_mae = EXCLUDED.avg_mae, avg_rmse = EXCLUDED.avg_rmse,
                 avg_wape = EXCLUDED.avg_wape, avg_bias = EXCLUDED.avg_bias,
                 avg_mape = EXCLUDED.avg_mape, avg_smape = EXCLUDED.avg_smape,
                 trained_at = NOW()""",
            (generate_id("trm"), tenant_id, session_id, model, *values),
        )


def list_metrics_for_tenant(tenant_id: str, model: str | None = None, limit: int = 200) -> list[dict]:
    """Recent training-run metrics for a tenant, newest first."""
    if model:
        return query(
            """SELECT * FROM training_run_metrics
               WHERE tenant_id = %s AND model = %s
               ORDER BY trained_at DESC LIMIT %s""",
            (tenant_id, model, limit),
        )
    return query(
        """SELECT * FROM training_run_metrics
           WHERE tenant_id = %s ORDER BY trained_at DESC LIMIT %s""",
        (tenant_id, limit),
    )
