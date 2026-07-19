"""
Centralized path construction. Every storage path goes through here.
Changing storage layout = change only this file.
"""

from pathlib import Path


def _base() -> Path:
    from backend.config import settings
    return settings.storage_path


# ── Tenants ────────────────────────────────────────────────────────────────

def tenant_dir(tenant_id: str) -> Path:
    return _base() / "tenants" / tenant_id

def tenant_file(tenant_id: str) -> Path:
    return tenant_dir(tenant_id) / "tenant.json"


# ── Users ──────────────────────────────────────────────────────────────────

def users_dir(tenant_id: str) -> Path:
    return _base() / "users" / tenant_id

def user_file(tenant_id: str, user_id: str) -> Path:
    return users_dir(tenant_id) / f"{user_id}.json"


# ── Email index (global: email → tenant/user mapping) ─────────────────────

def email_index_file() -> Path:
    return _base() / "email_index.json"


# ── Sessions ───────────────────────────────────────────────────────────────

def sessions_dir(tenant_id: str) -> Path:
    return _base() / "sessions" / tenant_id

def session_dir(tenant_id: str, session_id: str) -> Path:
    return sessions_dir(tenant_id) / session_id

def session_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "session.json"

def session_dataset_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "dataset_ref.json"

def session_inspection_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "inspection.json"

def session_columns_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "columns.json"

def session_features_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "features.json"

def session_models_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "models.json"

def session_validation_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "validation.json"

def session_training_result_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "training_result.json"

def session_forecasts_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "forecasts.json"

def session_business_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "business.json"

def session_forecast_config_file(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / "forecast_config.json"


# ── Datasets ───────────────────────────────────────────────────────────────

def datasets_dir(tenant_id: str) -> Path:
    return _base() / "datasets" / tenant_id

def dataset_dir(tenant_id: str, dataset_id: str) -> Path:
    return datasets_dir(tenant_id) / dataset_id

def dataset_meta_file(tenant_id: str, dataset_id: str) -> Path:
    return dataset_dir(tenant_id, dataset_id) / "meta.json"

def datasets_registry_file(tenant_id: str) -> Path:
    return datasets_dir(tenant_id) / "registry.json"


# ── Jobs ───────────────────────────────────────────────────────────────────

def jobs_dir(tenant_id: str) -> Path:
    return _base() / "jobs" / tenant_id

def job_file(tenant_id: str, job_id: str) -> Path:
    return jobs_dir(tenant_id) / f"{job_id}.json"

def queue_file() -> Path:
    return _base() / "queue.json"


# ── Artifacts ──────────────────────────────────────────────────────────────

def artifacts_dir(tenant_id: str, session_id: str) -> Path:
    return _base() / "artifacts" / tenant_id / session_id

def models_artifact_dir(tenant_id: str, session_id: str) -> Path:
    return artifacts_dir(tenant_id, session_id) / "models"

def plots_artifact_dir(tenant_id: str, session_id: str) -> Path:
    return artifacts_dir(tenant_id, session_id) / "plots"

def reports_artifact_dir(tenant_id: str, session_id: str) -> Path:
    return artifacts_dir(tenant_id, session_id) / "reports"

def po_pdf_dir(tenant_id: str) -> Path:
    return _base() / "pos" / tenant_id

def po_pdf_file(tenant_id: str, po_log_id: str, supplier_slug: str) -> Path:
    return po_pdf_dir(tenant_id) / f"{po_log_id}_{supplier_slug}.pdf"


# ── Logs ───────────────────────────────────────────────────────────────────

def logs_dir(tenant_id: str, session_id: str) -> Path:
    return _base() / "logs" / tenant_id / session_id

def training_log_file(tenant_id: str, session_id: str, job_id: str) -> Path:
    return logs_dir(tenant_id, session_id) / f"training_{job_id}.log"
