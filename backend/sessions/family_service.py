"""
Session family fan-out (multi-period planning, Phase A).

A single training launch produces one session per supported granularity
(daily/weekly/monthly, gated by how much history the data holds), all sharing
a family_id, each pre-forecast to a generous reach. The engine is unchanged:
each sibling just carries a different granularity_cfg (aggregate + target_freq)
and forecast_cfg.horizon, which runner.py already consumes.
"""

from __future__ import annotations

import logging
import math

from backend.utils.temporal_agg import detect_frequency, planning_granularities

log = logging.getLogger(__name__)

# Steps of the grain to pre-forecast, so the admin's chosen horizon (Phase B)
# is a window into an already-computed reach rather than a re-train.
GENEROUS_REACH = {"daily": 90, "weekly": 26, "monthly": 12}
# A grain is offered only if the history spans >= this many of its buckets.
MIN_BUCKETS_FOR_GRANULARITY = 20
# pandas resample rule each grain trains at; None = native (no aggregation).
TARGET_FREQ = {"daily": None, "weekly": "W-MON", "monthly": "MS"}
# Calendar days one bucket of each grain covers, for converting the user's
# horizon-in-days (Quick Start wizard) into per-grain forecast steps.
DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}
# Fewer than 2 forecast steps makes the forecast useless for reordering.
MIN_HORIZON_STEPS = 2

USER_GRANULARITIES = ("auto", "daily", "weekly", "monthly")


def _horizon_steps(granularity: str, user_horizon_days: int | None) -> int:
    """Forecast steps for a grain: the user's horizon-in-days converted into
    that grain's buckets, capped by GENEROUS_REACH and floored at
    MIN_HORIZON_STEPS. Without a user horizon, the generous reach itself."""
    if not user_horizon_days:
        return GENEROUS_REACH[granularity]
    steps = math.ceil(user_horizon_days / DAYS_PER_PERIOD[granularity])
    return max(MIN_HORIZON_STEPS, min(steps, GENEROUS_REACH[granularity]))


def plan_family(
    dates: list[str],
    user_granularity: str = "auto",
    user_horizon_days: int | None = None,
) -> list[dict]:
    """Decide which granularities to train and with what config. Pure — no DB.

    Returns finest-first, one dict per available grain:
      {granularity, target_freq, horizon, is_base}.
    The base (finest detected) grain trains natively (target_freq None).

    When the user picked an explicit granularity (Quick Start wizard) and the
    data can support it, only that grain is planned. A non-viable pick (too few
    buckets, or finer than the data's native grain) falls back to the auto
    fan-out — never fails the run.
    """
    base_freq = detect_frequency(dates)
    if base_freq not in GENEROUS_REACH:
        base_freq = "daily"
    grains = planning_granularities(base_freq, dates, MIN_BUCKETS_FOR_GRANULARITY)
    if user_granularity != "auto" and user_granularity in grains:
        grains = [user_granularity]
    specs = []
    for g in grains:
        specs.append({
            "granularity": g,
            "target_freq": None if g == base_freq else TARGET_FREQ[g],
            "horizon": _horizon_steps(g, user_horizon_days),
            "is_base": g == base_freq,
        })
    return specs


def _read_dataset_dates(tenant_id: str, session_id: str) -> list[str]:
    """Read just the date column of the session's dataset (via the dataframes
    boundary) so granularities can be gated before enqueue."""
    from backend.datasets.service import get_dataset
    from backend.db import session_store
    from backend.sessions import service as session_svc

    s = session_svc.get_session(tenant_id, session_id)
    ds = get_dataset(tenant_id, s["dataset_id"]) if s and s.get("dataset_id") else None
    if not ds or not ds.get("file_path"):
        return []
    cols = session_store.get_field(tenant_id, session_id, "columns_cfg") or {}
    if cols.get("schema_version") == "canonical_v1":
        date_col = (cols.get("canonical_mapping") or {}).get("date")
    else:
        date_col = cols.get("date_column") or cols.get("date")
    if not date_col:
        return []
    from backend.dataframes.io import read_columns
    try:
        rows = read_columns(ds["file_path"], [date_col])
        return [str(r[date_col])[:10] for r in rows if r.get(date_col) is not None]
    except Exception as e:
        log.warning("family: could not read dates for session=%s: %s", session_id, e)
        return []


def _enqueue(tenant_id: str, session_id: str, user_id: str) -> str:
    """create_job + set_last_job + transition to QUEUED; returns job_id."""
    from backend.training import job_service
    from backend.sessions import service as session_svc

    job = job_service.create_job(tenant_id, session_id, user_id)
    session_svc.set_last_job(tenant_id, session_id, job["id"])
    try:
        session_svc.transition(tenant_id, session_id, "QUEUED", "training")
    except ValueError:
        pass
    return job["id"]


def launch_training_family(
    tenant_id: str,
    base_session_id: str,
    user_id: str,
    user_horizon_days: int | None = None,
    user_granularity: str = "auto",
) -> dict:
    """Fan a ready-to-train base session out into its granularity family and
    enqueue every member. The base session must already be validated and in a
    pre-train state (callers guarantee this). Returns the family descriptor.

    `user_horizon_days` / `user_granularity` come from the Quick Start wizard:
    they narrow the fan-out (single explicit grain when viable) and size each
    grain's horizon (see plan_family). Both are persisted into the base
    session's forecast_cfg for auditability.
    """
    from backend.db.connection import execute
    from backend.db import session_store
    from backend.sessions import data_gate
    from backend.sessions import service as session_svc

    # THE gate, and it lives here on purpose. Every launch path goes through
    # this function — POST /sessions/{id}/train, POST /demo/quickstart, the ERP
    # integrations sync and the seed script — so a caller cannot start a run on
    # data the gate rejected by talking to a different endpoint. Enforcing it in
    # the REST handler alone is what made it a suggestion.
    data_gate.enforce(tenant_id, base_session_id)

    dates = _read_dataset_dates(tenant_id, base_session_id)
    specs = plan_family(dates, user_granularity, user_horizon_days)  # always >= 1
    base_spec = specs[0]
    family_id = base_session_id

    # Tag + finalize the base session.
    execute(
        "UPDATE sessions SET family_id=%s, granularity=%s, updated_at=NOW() "
        "WHERE id=%s AND tenant_id=%s",
        (family_id, base_spec["granularity"], base_session_id, tenant_id))
    base_fcfg = dict(session_store.get_field(tenant_id, base_session_id, "forecast_cfg") or {})
    base_fcfg["horizon"] = base_spec["horizon"]
    # Audit trail of what the user actually asked for in the wizard.
    if user_horizon_days is not None:
        base_fcfg["user_horizon_days"] = user_horizon_days
    if user_granularity != "auto":
        base_fcfg["user_granularity"] = user_granularity
    session_store.set_field(tenant_id, base_session_id, "forecast_cfg", base_fcfg)
    # A user-picked grain coarser than the data's native grain means the base
    # session itself must aggregate; set granularity_cfg explicitly either way
    # so a re-launch never inherits a stale aggregation.
    session_store.set_field(
        tenant_id, base_session_id, "granularity_cfg",
        {"strategy": "aggregate" if base_spec["target_freq"] else "native",
         "target_freq": base_spec["target_freq"]})

    base_session = session_svc.get_session(tenant_id, base_session_id)
    dataset_id = base_session.get("dataset_id")

    members = [{"session_id": base_session_id, "granularity": base_spec["granularity"]}]

    # Coarser siblings: clone configs, override granularity + horizon, enqueue.
    for spec in specs[1:]:
        sib = session_svc.create_session(
            tenant_id, user_id, f"{base_session['name']} · {spec['granularity']}")
        sib_id = sib["id"]
        if dataset_id:
            session_svc.attach_dataset(tenant_id, sib_id, dataset_id)
        for field in ("columns_cfg", "features_cfg", "models_cfg",
                      "validation_cfg", "business_cfg", "forecast_cfg"):
            val = session_store.get_field(tenant_id, base_session_id, field)
            if val is not None:
                if field == "forecast_cfg":
                    val = {**dict(val), "horizon": spec["horizon"]}
                session_store.set_field(tenant_id, sib_id, field, val)
        session_store.set_field(tenant_id, sib_id, "granularity_cfg",
                                {"strategy": "aggregate", "target_freq": spec["target_freq"]})
        execute(
            "UPDATE sessions SET family_id=%s, granularity=%s, updated_at=NOW() "
            "WHERE id=%s AND tenant_id=%s",
            (family_id, spec["granularity"], sib_id, tenant_id))
        session_svc.force_status(tenant_id, sib_id, "MODELS_CONFIGURED")
        members.append({"session_id": sib_id, "granularity": spec["granularity"]})

    # Enqueue base FIRST (finest grain -> semaforo usable soonest), then siblings.
    base_job_id = _enqueue(tenant_id, base_session_id, user_id)
    members[0]["job_id"] = base_job_id
    for m in members[1:]:
        m["job_id"] = _enqueue(tenant_id, m["session_id"], user_id)

    log.info("[family] tenant=%s family=%s members=%d",
             tenant_id, family_id, len(members))
    return {"family_id": family_id, "base_job_id": base_job_id, "sessions": members}
