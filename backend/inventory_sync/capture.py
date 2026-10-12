"""Engine change-capture strategies (filled in by the capture commit)."""

from backend.errors import AppError


def read(tenant_id, profile, strategy, **kw):
    raise AppError("inventory_sync_strategy_unavailable", "This change-capture strategy is not available.",
                   status_code=422, params={"strategy": strategy})
    yield  # pragma: no cover
