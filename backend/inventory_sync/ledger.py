"""Movement-ledger stock model (filled in by the ledger commit)."""

from backend.errors import AppError


def read_balances(tenant_id, profile, **kw):
    raise AppError("inventory_sync_strategy_unavailable", "Ledger profiles are not available yet.",
                   status_code=422, params={"strategy": "ledger"})
    yield  # pragma: no cover


def normalize_config(cfg, engine):
    raise AppError("inventory_sync_strategy_unavailable", "Ledger profiles are not available yet.",
                   status_code=422, params={"strategy": "ledger"})
