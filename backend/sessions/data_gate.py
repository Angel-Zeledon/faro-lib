"""Enforcement of the pre-training gate.

The rule the product states is that a file either meets the standard or gets no
forecast and an explanation. Until now that rule lived in one place: a disabled
button in the browser. The API, the ERP sync path and any client with a token
walked straight past it — which makes it a suggestion, not a standard.

This module is where it becomes enforceable. `enforce()` is called from
`family_service.launch_training_family`, the single function every launch path
goes through (the REST endpoint, `POST /demo/quickstart`, the integrations sync
and the seed script), so there is no route left that can start a run on a file
the gate rejected.

Two properties matter and both are deliberate:

* The gate is evaluated against the columns the user CONFIRMED, not the ones the
  profiler guessed at upload time. The browser sees the guess; the API must not
  enforce a different computation than the one the user was shown to answer, so
  both go through `DataProfiler.evaluate_gate`.
* It is evaluated FRESH at launch. A cached verdict from before the user
  remapped a column is a verdict about a different question.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from backend.datasets.service import get_dataset
from backend.db import session_store
from backend.errors import AppError

log = logging.getLogger(__name__)


def resolve_columns(columns_cfg: dict) -> dict:
    """The date / demand / product columns this session actually trains on.

    Mirrors `build_engine_config`'s branch on `schema_version` and is imported
    BY it, so the gate can never judge one mapping while the runner trains
    another.
    """
    columns_cfg = columns_cfg or {}
    if columns_cfg.get("schema_version") == "canonical_v1":
        cmap = columns_cfg.get("canonical_mapping") or {}
        sku_col = cmap.get("sku")
        store_col = cmap.get("store")
        return {
            "date": cmap.get("date"),
            "target": cmap.get("demand"),
            "group": sku_col,
            "group_keys": [c for c in (sku_col, store_col) if c],
            "canonical_mapping": cmap,
        }
    sku_col = columns_cfg.get("sku_column")
    return {
        "date": columns_cfg.get("date_column") or None,
        "target": columns_cfg.get("target_column") or None,
        "group": sku_col or None,
        "group_keys": [sku_col] if sku_col else [],
        "canonical_mapping": None,
    }


def evaluate(tenant_id: str, session_id: str) -> Optional[dict]:
    """Run the gate over this session's dataset and return the data_quality dict.

    Deliberately NOT cached. A verdict computed before the user remapped a
    column is a verdict about a different question, and a stale "clear" is the
    one outcome this gate must never produce. Reading an SMB's sales file costs
    milliseconds; being wrong about it costs a purchase order.

    Returns None only when there is genuinely nothing to judge — no dataset
    attached at all. Every other failure raises, because "we could not check"
    reported as "it passed" is the exact shape of bug this gate exists to stop.
    """
    from backend.sessions import service as session_svc

    s = session_svc.get_session(tenant_id, session_id)
    if not s:
        raise AppError("session_not_found", "Session not found", status_code=404)

    dataset_id = s.get("dataset_id")
    if not dataset_id:
        return None

    ds_meta = get_dataset(tenant_id, dataset_id)
    if not ds_meta or not os.path.exists(ds_meta.get("file_path") or ""):
        raise AppError(
            "dataset_file_missing",
            f"The data file for session '{s['name']}' is no longer on disk, so its quality "
            f"cannot be checked. Re-upload it before training.",
            status_code=422,
            params={"session": s["name"]},
        )

    columns_cfg = session_store.get_field(tenant_id, session_id, "columns_cfg") or {}
    cols = resolve_columns(columns_cfg)

    from forecasting_core.data.profiler import DataProfiler
    from forecasting_core.engine import ForecastEngine

    engine = ForecastEngine()
    engine.load_data(ds_meta["file_path"])
    df = engine._df

    profiler = DataProfiler()
    # No confirmed mapping yet: judge what the profiler would pick, which is
    # exactly what the wizard showed.
    if not cols["date"] or not cols["target"]:
        recommended = (profiler.profile(df) or {}).get("recommended", {})
        cols["date"] = cols["date"] or recommended.get("date")
        cols["target"] = cols["target"] or recommended.get("target")
        cols["group"] = cols["group"] or recommended.get("group")

    data_quality = profiler.evaluate_gate(
        df,
        date_col=cols["date"],
        target_col=cols["target"],
        group_col=cols["group"],
        # EVERY series key. Judging duplicates by SKU alone would ask a
        # two-branch distributor to resolve a "duplicate" that is just the same
        # product selling in two places on the same day — a question with no
        # right answer, on every single sync.
        group_cols=cols["group_keys"],
        canonical_mapping=cols["canonical_mapping"],
    )
    data_quality["evaluated_columns"] = {
        "date": cols["date"], "target": cols["target"], "group": cols["group"],
    }
    return data_quality


def chosen_remediations(tenant_id: str, session_id: str) -> dict:
    cfg = session_store.get_field(tenant_id, session_id, "columns_cfg") or {}
    chosen = cfg.get("remediations") or {}
    return chosen if isinstance(chosen, dict) else {}


def enforce(tenant_id: str, session_id: str) -> Optional[dict]:
    """Refuse to launch a run the data cannot honestly support.

    Raises `AppError` with a stable code and the offending issue types as
    params; the frontend renders the Spanish from the catalogue, per CLAUDE.md.
    """
    from forecasting_core.data import gate as gate_mod

    data_quality = evaluate(tenant_id, session_id)
    if data_quality is None:
        return None

    fatal = data_quality.get("blocking_fatal") or []
    if fatal:
        raise AppError(
            "training_blocked_data_fatal",
            "This file cannot produce a forecast and no correction we can apply changes "
            f"that: {', '.join(fatal)}.",
            status_code=422,
            params={"issues": ", ".join(fatal), "count": len(fatal)},
        )

    missing = gate_mod.unresolved(data_quality, chosen_remediations(tenant_id, session_id))
    if missing:
        raise AppError(
            "training_blocked_unresolved",
            "This file would train a forecast that is wrong in a way we can already see. "
            f"Choose how to handle each of these first: {', '.join(missing)}.",
            status_code=422,
            params={"issues": ", ".join(missing), "count": len(missing)},
        )
    return data_quality
