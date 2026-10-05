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


def _extend_for_need(spec: dict, need: dict | None) -> None:
    """Raise `spec["horizon"]` to cover the buying need, never lower it.

    `need` is `horizon_need.derive_need` output. The configured horizon (the
    user's pick, or the grain's generous reach) is kept whenever it already
    covers the need; otherwise the spec gains `horizon_extension` — the facts
    the run, the wizard and the logs state out loud."""
    if not need:
        return
    grain = spec["granularity"]
    needed_steps = math.ceil(need["need_days"] / DAYS_PER_PERIOD[grain])
    configured = spec["horizon"]
    if needed_steps <= configured:
        return
    spec["horizon"] = needed_steps
    spec["horizon_extension"] = {
        "granularity": grain,
        "configured_steps": configured,
        "steps": needed_steps,
        "need_days": need["need_days"],
        "required_days": need["required_days"],
        "lead_time_days": need["lead_time_days"],
        "review_period_days": need["review_period_days"],
        "supplier": need.get("supplier"),
        "sku": need.get("sku"),
        "capped": bool(need.get("capped")),
    }


def describe_extension(ext: dict) -> str:
    """The sentence a run prints when its horizon was raised (English; the UI
    renders its own localized copy from the structured fields)."""
    who = f"supplier {ext['supplier']}" if ext.get("supplier") else f"SKU {ext.get('sku')}"
    text = (
        f"Horizon extended to {ext['steps']} {ext['granularity']} steps "
        f"(was {ext['configured_steps']}) because {who} needs lead time + review = "
        f"{ext['required_days']} days ({ext['lead_time_days']} + {ext['review_period_days']}), "
        f"{ext['need_days']} days with the safety margin."
    )
    if ext.get("capped"):
        text += " The need exceeds the 365-day ceiling, so the horizon stops there."
    return text


def plan_family(
    dates: list[str],
    user_granularity: str = "auto",
    user_horizon_days: int | None = None,
    need: dict | None = None,
) -> list[dict]:
    """Decide which granularities to train and with what config. Pure — no DB.

    Returns finest-first, one dict per available grain:
      {granularity, target_freq, horizon, is_base} plus `horizon_extension`
      when `need` (see `horizon_need.derive_need`) raised the horizon.
    The base (finest detected) grain trains natively (target_freq None).
    `need=None` is exactly the behaviour before the buying need existed.

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
        _extend_for_need(specs[-1], need)
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


def _enqueue(tenant_id: str, session_id: str, user_id: str,
             count_as_training: bool = False) -> str:
    """create_job + set_last_job + transition to QUEUED; returns job_id.

    `count_as_training=True` is the head of a launch (the base session of a
    family): the daily training ceiling is checked and the job inserted under
    the tenant's advisory lock, so two simultaneous launches cannot both pass
    a ceiling of one. Raises 403 PLAN_LIMIT_REACHED, creating nothing."""
    from backend.training import job_service
    from backend.sessions import service as session_svc

    if count_as_training:
        from backend.entitlements.service import limit_guard
        from backend.training import daily_cap
        with limit_guard(tenant_id) as conn:
            daily_cap.ensure_can_train(tenant_id, conn=conn)
            job = job_service.create_job(tenant_id, session_id, user_id, conn=conn)
    else:
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
    extend_for_buying_need: bool = True,
) -> dict:
    """Fan a ready-to-train base session out into its granularity family and
    enqueue every member. The base session must already be validated and in a
    pre-train state (callers guarantee this). Returns the family descriptor.

    `user_horizon_days` / `user_granularity` come from the Quick Start wizard:
    they narrow the fan-out (single explicit grain when viable) and size each
    grain's horizon (see plan_family). Both are persisted into the base
    session's forecast_cfg for auditability.

    The horizon is then RAISED (never lowered) when the tenant's declared lead
    times + review periods need more than it covers (`horizon_need`); every
    session whose horizon was raised carries `forecast_cfg["horizon_extension"]`
    and its first log line says why. `extend_for_buying_need=False` is for runs
    whose horizon is a measurement, not a purchase window (back-tests: the
    horizon IS the held-out span).
    """
    from backend.activity.events import record_event
    from backend.db.connection import execute
    from backend.db import session_store
    from backend.errors import AppError
    from backend.sessions import data_gate
    from backend.sessions import service as session_svc

    # THE gate, and it lives here on purpose. Every launch path goes through
    # this function — POST /sessions/{id}/train, POST /demo/quickstart, the
    # scheduled retrain and the seed script — so a caller cannot start a run on
    # data the gate rejected by talking to a different endpoint. Enforcing it in
    # the REST handler alone is what made it a suggestion.
    #
    # Refusing is recorded for the same reason it is enforced here: a person at
    # the wizard reads the 422 and knows, but the launches nobody is watching
    # (a scheduled run) refused into silence, and the tenant's only symptom was
    # numbers that stopped moving.
    try:
        data_gate.enforce(tenant_id, base_session_id)
    except AppError as exc:
        record_event(
            tenant_id, user_id, "training.blocked",
            resource=base_session_id, reason="data_gate_blocked",
            reason_params={"detail": (exc.params or {}).get("issues", "")},
            details={
                "session_id": base_session_id,
                "session_name": (session_svc.get_session(tenant_id, base_session_id)
                                  or {}).get("name"),
                "issues": (exc.params or {}).get("issues"),
            },
            status="error",
        )
        raise

    # The daily training ceiling, as a PRE-check: refusing here creates nothing
    # (no sibling sessions, no rewritten config). The check that actually holds
    # against two simultaneous launches is the one taken with the base job's
    # insert in `_enqueue`. Back-tests are verification runs and are exempt.
    counts_as_training = not (session_svc.get_session(tenant_id, base_session_id)
                              or {}).get("is_backtest")
    if counts_as_training:
        from backend.training import daily_cap
        daily_cap.ensure_can_train(tenant_id)

    dates = _read_dataset_dates(tenant_id, base_session_id)
    need = None
    need_error = False
    if extend_for_buying_need:
        try:
            from backend.sessions import horizon_need
            need = horizon_need.tenant_need(tenant_id)
        except Exception:
            # Training must not depend on this lookup, but the run must not
            # pretend it was consulted either: the failure is logged here and
            # written to the run's own log below.
            need_error = True
            log.warning("family: could not derive the buying-need horizon for "
                        "tenant=%s session=%s", tenant_id, base_session_id, exc_info=True)
    specs = plan_family(dates, user_granularity, user_horizon_days, need)  # always >= 1
    base_spec = specs[0]
    family_id = base_session_id

    # Tag + finalize the base session.
    execute(
        "UPDATE sessions SET family_id=%s, granularity=%s, updated_at=NOW() "
        "WHERE id=%s AND tenant_id=%s",
        (family_id, base_spec["granularity"], base_session_id, tenant_id))
    base_fcfg = dict(session_store.get_field(tenant_id, base_session_id, "forecast_cfg") or {})
    base_fcfg["horizon"] = base_spec["horizon"]
    # Always rewritten, so a relaunch never inherits the previous run's note.
    base_fcfg.pop("horizon_extension", None)
    if base_spec.get("horizon_extension"):
        base_fcfg["horizon_extension"] = base_spec["horizon_extension"]
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
                    val.pop("horizon_extension", None)
                    if spec.get("horizon_extension"):
                        val["horizon_extension"] = spec["horizon_extension"]
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
    try:
        base_job_id = _enqueue(tenant_id, base_session_id, user_id,
                               count_as_training=counts_as_training)
    except Exception:
        # Lost the race for the last training of the day (or the insert failed):
        # the siblings made above would hold saved-forecast slots for a run
        # that will never start. Archived, never deleted (sessions are permanent).
        for m in members[1:]:
            try:
                session_svc.archive_session(tenant_id, m["session_id"], user_id)
            except Exception:  # noqa: BLE001
                log.warning("family: could not archive the unlaunched sibling %s",
                            m["session_id"], exc_info=True)
        raise
    members[0]["job_id"] = base_job_id
    for m in members[1:]:
        m["job_id"] = _enqueue(tenant_id, m["session_id"], user_id)

    # The run says what it did to the horizon. Best effort: a log write that
    # fails must not undo a queued run, but it is not swallowed silently.
    by_session = {m["session_id"]: spec for m, spec in zip(members, specs)}
    for m in members:
        spec = by_session[m["session_id"]]
        ext = spec.get("horizon_extension")
        if ext:
            m["horizon_extension"] = ext
        line = describe_extension(ext) if ext else (
            "Could not derive the horizon needed by lead time + review period; "
            "the configured horizon was used." if need_error else None)
        if line:
            try:
                session_store.append_log(tenant_id, m["session_id"], m["job_id"], line)
            except Exception:
                log.warning("family: could not write the horizon note for job=%s",
                            m["job_id"], exc_info=True)

    log.info("[family] tenant=%s family=%s members=%d",
             tenant_id, family_id, len(members))
    return {"family_id": family_id, "base_job_id": base_job_id, "sessions": members}
