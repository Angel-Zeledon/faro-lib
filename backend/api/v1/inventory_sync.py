"""
Inventory sync API (see `backend/inventory_sync/`).

Two routers, two exposure decisions (`api/public_surface.py`):

* `status_router` (tag ``inventory-sync-status``, EXPOSED): reading the
  profiles, their status and their run history. An integration watching
  "is my stock fresh?" may do that with a read key.
* `router` (tag ``inventory-sync``, INTERNAL): everything that opens a
  connection to the customer's database on demand, or changes what is
  written to inventory — discovery, dry runs, profile edits, enabling, "sync
  now". A person decides those, on the screen, with the preview in front of
  them.

Every mutation requires analyst-or-above; the audit middleware records each
one (`audit/catalog.py`).
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from backend import audit
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.errors import AppError
from backend.config import settings
from backend.inventory_sync import engine, profiles, reader, scheduler, strategies, suggest
from backend.inventory_sync import fields as F
from backend.schemas.common import ok

router = APIRouter(tags=["inventory-sync"])
status_router = APIRouter(tags=["inventory-sync-status"])


class ProfileBody(BaseModel):
    name:                   Optional[str] = Field(default=None, max_length=profiles.MAX_NAME)
    data_source_id:         Optional[str] = Field(default=None, max_length=64)
    source_kind:            Optional[str] = Field(default=None, max_length=20)
    source_schema:          Optional[str] = Field(default=None, max_length=256)
    source_object:          Optional[str] = Field(default=None, max_length=256)
    source_query:           Optional[str] = Field(default=None, max_length=100_000)
    mapping:                Optional[dict[str, Any]] = None
    filters:                Optional[list[dict[str, Any]]] = None
    default_warehouse:      Optional[str] = Field(default=None, max_length=F.MAX_TEXT)
    mode:                   Optional[str] = Field(default=None, max_length=20)
    watermark_column:       Optional[str] = Field(default=None, max_length=256)
    overlap_minutes:        Optional[int] = None
    interval_minutes:       Optional[int] = None
    missing_policy:         Optional[str] = Field(default=None, max_length=20)
    negative_policy:        Optional[str] = Field(default=None, max_length=20)
    duplicate_policy:       Optional[str] = Field(default=None, max_length=20)
    allow_new_warehouses:   Optional[bool] = None
    brake_max_vanished_pct: Optional[float] = None
    brake_min_row_ratio:    Optional[float] = None
    brake_max_rejected_pct: Optional[float] = None
    brake_max_jump_pct:     Optional[float] = None
    slug:                   Optional[str] = Field(default=None, max_length=63)
    priority:               Optional[int] = None
    stock_model:            Optional[str] = Field(default=None, max_length=20)
    model_config_:          Optional[dict[str, Any]] = Field(default=None, alias="model_config")
    freshness:              Optional[str] = Field(default=None, max_length=20)
    poll_seconds:           Optional[int] = None
    reconcile_minutes:      Optional[int] = None
    strategy:               Optional[str] = Field(default=None, max_length=30)

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


class RefreshBody(BaseModel):
    # One profile, or every live profile of the account when omitted.
    profile_id:   Optional[str] = Field(default=None, max_length=64)
    # Refresh only these SKUs (a page, a product card): fast, skips the cooldown.
    skus:         Optional[list[str]] = Field(default=None, max_length=1000)
    # How long to wait for the result before answering "still running".
    wait_seconds: Optional[float] = Field(default=None, ge=0, le=60)


class InspectBody(BaseModel):
    source_kind:   str = Field(max_length=20)
    source_schema: Optional[str] = Field(default=None, max_length=256)
    source_object: Optional[str] = Field(default=None, max_length=256)
    source_query:  Optional[str] = Field(default=None, max_length=100_000)


class RunBody(BaseModel):
    # Run despite the safety brake, once. A person looked at what tripped it
    # and decided the change is real (a warehouse closed, a catalogue purge).
    override_brake: bool = False


def _body(body: ProfileBody) -> dict:
    out = body.model_dump(exclude_unset=True, by_alias=True)
    out.pop("model_config_", None)
    return out


def _source_or_404(user: CurrentUser, source_id: str) -> dict:
    from backend.datasources.service import get_source
    src = get_source(user.tenant_id, source_id)
    if not src:
        raise AppError("data_source_not_found", f"Data source {source_id} not found",
                       status_code=404, params={"source_id": source_id})
    if src.get("source_type") != "sql":
        raise AppError("inventory_sync_source_not_database",
                       "Inventory sync reads a connected database, not an uploaded file.",
                       status_code=422)
    return src


# ── Reads (exposed) ───────────────────────────────────────────────────────────

@status_router.get("/inventory-sync/fields")
def get_fields(user: CurrentUser = Depends(get_current_user)):
    """The canonical fields, transforms, policies and limits the editor offers."""
    return ok({
        "fields": list(F.CANONICAL_FIELDS), "required": list(F.REQUIRED_FIELDS),
        "number_fields": list(F.NUMBER_FIELDS + F.INTEGER_FIELDS),
        "casts": list(F.CASTS), "cases": list(F.CASES), "decimals": list(F.DECIMALS),
        "filter_ops": list(F.FILTER_OPS), "source_kinds": list(profiles.SOURCE_KINDS),
        "modes": list(profiles.MODES), "missing_policies": list(profiles.MISSING_POLICIES),
        "negative_policies": list(profiles.NEGATIVE_POLICIES),
        "duplicate_policies": list(profiles.DUPLICATE_POLICIES),
        "freshness_modes": list(profiles.FRESHNESS),
        "stock_models": list(strategies.STOCK_MODELS),
        "strategies": list(strategies.STRATEGIES),
        "aggregates": list(strategies.AGGREGATES),
        "egress_ips": [ip.strip() for ip in (getattr(settings, "inventory_sync_egress_ips", "") or "").split(",")
                       if ip.strip()],
        "limits": {"min_interval_minutes": profiles.min_interval(),
                   "min_poll_seconds": profiles.min_poll(),
                   "max_profiles": profiles.max_profiles(),
                   "max_rows_per_run": engine.max_rows_per_run(),
                   "dry_run_sample": engine.DRY_RUN_SAMPLE},
    })


@status_router.get("/inventory-sync/freshness")
def get_freshness(user: CurrentUser = Depends(get_current_user)):
    """How current the stock is: `state` is `fresh`, `stale` (a profile has
    not succeeded within its expected window), `blocked` (a profile is paused
    or blocked and needs someone) or `none` (no sync). `data_as_of` is the
    oldest last success among live profiles: the stock is at least that fresh.
    Each profile lists its own state and the reason code when not fresh."""
    return ok(scheduler.freshness(user.tenant_id))


@status_router.get("/inventory-sync/profiles")
def list_profiles(user: CurrentUser = Depends(get_current_user)):
    """Every inventory sync profile with its status: enabled, last success and
    its age, `stale` (no success within twice the interval), `braked` (the
    safety brake stopped the last run), the last error code and the fields the
    source owns."""
    return ok([profiles.public(r) for r in profiles.list_for_tenant(user.tenant_id)])


@status_router.get("/inventory-sync/profiles/{profile_id}")
def get_profile(profile_id: str, user: CurrentUser = Depends(get_current_user)):
    """One inventory sync profile with the same status fields as the list."""
    return ok(profiles.public(profiles.require(user.tenant_id, profile_id)))


@status_router.get("/inventory-sync/profiles/{profile_id}/runs")
def list_runs(profile_id: str, limit: int = Query(default=30, ge=1, le=200),
              user: CurrentUser = Depends(get_current_user)):
    """The run log of a profile, newest first: rows read, created, updated,
    unchanged and rejected, the outcome (success / failed / braked), the error
    code and the watermark each run started from and reached."""
    return ok(profiles.list_runs(user.tenant_id, profile_id, limit))


# ── Discovery (internal: opens connections on demand) ─────────────────────────

@router.get("/inventory-sync/sources")
def list_database_sources(user: CurrentUser = Depends(require_analyst_or_above)):
    """The connected databases a profile can read from."""
    from backend.db.connection import query
    rows = query(
        "SELECT id, name, connection_status, sql_config->>'engine' AS engine, "
        "sql_config->>'host' AS host, sql_config->>'database' AS database "
        "FROM datasets WHERE tenant_id = %s AND source_type = 'sql' ORDER BY name",
        (user.tenant_id,))
    return ok(rows)


@router.get("/inventory-sync/sources/{source_id}/discover")
def discover(source_id: str, refresh: bool = Query(False),
             user: CurrentUser = Depends(require_analyst_or_above)):
    """Every table / view / collection, plus the best stock candidates first."""
    from backend.datasources.service import get_schema
    _source_or_404(user, source_id)
    schema = get_schema(user.tenant_id, source_id, refresh=refresh)
    tables = schema.get("tables") or []
    return ok({"tables": tables, "truncated": schema.get("truncated"),
               "candidates": suggest.rank_tables(tables), "cached_at": schema.get("cached_at")})


@router.get("/inventory-sync/sources/{source_id}/columns")
def columns(source_id: str, table: str = Query(..., min_length=1, max_length=256),
            schema: str = Query("", max_length=256),
            user: CurrentUser = Depends(require_analyst_or_above)):
    """A table's columns with sample values and the proposed mapping."""
    from backend.datasources.service import get_table_columns
    src = _source_or_404(user, source_id)
    engine_name = (src.get("sql_config") or {}).get("engine")
    listing = get_table_columns(user.tenant_id, source_id, schema, table)
    cols = [dict(c) for c in listing.get("columns") or []]
    if engine_name != "mongodb":
        kind = {"data_source_id": source_id, "source_kind": "table", "source_schema": schema or None,
                "source_object": table, "mode": "full"}
        sampled = reader.sample_columns(user.tenant_id, kind, rows=20)
        samples = {c["name"]: c["samples"] for c in sampled["columns"]}
        for c in cols:
            c["samples"] = samples.get(c["name"], [])
    return ok({"columns": cols, "suggestion": suggest.suggest_mapping(cols),
               "truncated": listing.get("truncated")})


@router.post("/inventory-sync/sources/{source_id}/inspect")
def inspect(source_id: str, body: InspectBody,
            user: CurrentUser = Depends(require_analyst_or_above)):
    """Columns (from sample rows) and the proposed mapping for a saved query
    or a MongoDB pipeline — what the table listing cannot describe."""
    src = _source_or_404(user, source_id)
    engine_name = (src.get("sql_config") or {}).get("engine") or "postgresql"
    probe = {"data_source_id": source_id, "source_kind": body.source_kind,
             "source_schema": body.source_schema, "source_object": body.source_object,
             "source_query": body.source_query, "mode": "full"}
    if body.source_kind == "query":
        from backend.datasources.sql_guard import validate_read_only_sql
        validate_read_only_sql(body.source_query or "", engine_name)
    elif body.source_kind in ("collection", "pipeline"):
        profiles._parse_json(body.source_query, list if body.source_kind == "pipeline" else dict) \
            if body.source_query or body.source_kind == "pipeline" else None
    sampled = reader.sample_columns(user.tenant_id, probe, rows=50)
    return ok({**sampled, "suggestion": suggest.suggest_mapping(sampled["columns"])})


@router.post("/inventory-sync/dry-run")
def dry_run_unsaved(body: ProfileBody, user: CurrentUser = Depends(require_analyst_or_above)):
    """Preview a configuration that is not saved yet. Writes nothing."""
    cfg = profiles.normalize(user.tenant_id, _body(body))
    return ok(engine.dry_run(user.tenant_id, cfg))


# ── Profiles (internal: they decide what is written to inventory) ─────────────

@router.post("/inventory-sync/profiles", status_code=201)
def create_profile(body: ProfileBody, request: Request,
                   user: CurrentUser = Depends(require_analyst_or_above)):
    row = profiles.create(user.tenant_id, user.user_id, _body(body))
    audit.note(request, target_id=row["id"], label=row["name"],
               after={"data_source_id": row["data_source_id"], "source_kind": row["source_kind"],
                      "mode": row["mode"], "fields": ",".join(F.owned_fields(row["mapping"]))})
    return ok(profiles.public(row))


@router.patch("/inventory-sync/profiles/{profile_id}")
def update_profile(profile_id: str, body: ProfileBody, request: Request,
                   user: CurrentUser = Depends(require_analyst_or_above)):
    row, disabled = profiles.update(user.tenant_id, profile_id, _body(body))
    audit.note(request, label=row["name"], after={"disabled_by_edit": disabled,
                                                  "config_hash": row["config_hash"]})
    return ok({**profiles.public(row), "disabled_by_edit": disabled})


@router.delete("/inventory-sync/profiles/{profile_id}")
def delete_profile(profile_id: str, request: Request,
                   user: CurrentUser = Depends(require_analyst_or_above)):
    row = profiles.delete(user.tenant_id, profile_id)
    audit.note(request, label=row["name"])
    return ok({"deleted": profile_id})


@router.post("/inventory-sync/profiles/{profile_id}/dry-run")
def dry_run_saved(profile_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    """Preview a saved profile. A successful preview is what allows enabling
    it (until its configuration changes again)."""
    row = profiles.require(user.tenant_id, profile_id)
    result = engine.dry_run(user.tenant_id, row)
    profiles.mark_dry_run(user.tenant_id, profile_id, row["config_hash"])
    return ok(result)


@router.post("/inventory-sync/profiles/{profile_id}/enable")
def enable_profile(profile_id: str, request: Request,
                   user: CurrentUser = Depends(require_analyst_or_above)):
    row = profiles.set_enabled(user.tenant_id, profile_id, True)
    audit.note(request, label=row["name"])
    return ok(profiles.public(row))


@router.post("/inventory-sync/profiles/{profile_id}/disable")
def disable_profile(profile_id: str, request: Request,
                    user: CurrentUser = Depends(require_analyst_or_above)):
    row = profiles.set_enabled(user.tenant_id, profile_id, False)
    audit.note(request, label=row["name"])
    return ok(profiles.public(row))


@router.post("/inventory-sync/refresh")
def refresh(body: RefreshBody, request: Request,
            user: CurrentUser = Depends(require_analyst_or_above)):
    """'Refresh now': make the displayed stock current. Coalesces (a refresh
    that finds a run in flight joins it), honours a per-profile cooldown
    (`status: cooldown`, `retry_after_s`), refreshes only `skus` when given,
    and waits at most `wait_seconds` before answering `status: running` with
    the `data_as_of` of the last success — the stock shown is never blanked.
    Paused or blocked profiles answer with their reason instead of running."""
    result = scheduler.refresh(user.tenant_id, user.user_id, profile_id=body.profile_id,
                               skus=body.skus, wait_s=body.wait_seconds)
    audit.note(request, target_id=body.profile_id,
               after={"profiles": len(result["profiles"]), "scoped": bool(body.skus)})
    return ok(result)


@router.post("/inventory-sync/profiles/{profile_id}/ingest")
async def ingest_rows(profile_id: str, request: Request,
                      batch_id: Optional[str] = Query(default=None, max_length=128),
                      snapshot_id: Optional[str] = Query(default=None, max_length=128),
                      last: bool = Query(default=True),
                      dry_run: bool = Query(default=False),
                      user: CurrentUser = Depends(require_analyst_or_above)):
    """Push rows (or, for a ledger profile, movements) into a `push` profile.

    JSON body: `{"batch_id", "rows": [...], "snapshot_id"?, "last"?, "dry_run"?}`.
    NDJSON body (`Content-Type: application/x-ndjson`, one row per line): the
    same fields as query parameters. The rows go through the profile's mapping,
    validation, safety brake and run log, exactly like a pulled source.

    Idempotent per `batch_id`: a retry with the same rows answers the first
    run (`replayed: true`); the same id with different rows is refused
    (409 `inventory_sync_batch_id_reused`). Without `snapshot_id` a batch is
    partial (absent SKUs are not "gone"); parts of one `snapshot_id` are staged
    and the part with `last: true` applies the full snapshot with every brake."""
    import asyncio
    import json as _json_mod
    from backend.inventory_sync import ingest
    raw = await request.body()
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype in ("application/x-ndjson", "application/ndjson", "application/jsonl"):
        rows = []
        for n, line in enumerate(raw.decode("utf-8", "replace").splitlines(), start=1):
            if line.strip():
                try:
                    rows.append(_json_mod.loads(line))
                except ValueError:
                    raise AppError("inventory_sync_ingest_invalid", f"Line {n} is not JSON.", status_code=422,
                                   params={"reason": "not_json", "path": f"line {n}"})
        body = {"batch_id": batch_id, "rows": rows, "snapshot_id": snapshot_id, "last": last, "dry_run": dry_run}
    else:
        try:
            body = _json_mod.loads(raw or b"{}")
        except ValueError:
            raise AppError("inventory_sync_ingest_invalid", "The body is not JSON.", status_code=422,
                           params={"reason": "not_json", "path": "$"})
        if not isinstance(body, dict):
            raise AppError("inventory_sync_ingest_invalid", "The body must be an object.", status_code=422,
                           params={"reason": "not_object", "path": "$"})
    result = await asyncio.to_thread(
        ingest.ingest, user.tenant_id, user.user_id, profile_id,
        batch_id=body.get("batch_id") or batch_id, rows=body.get("rows"),
        snapshot_id=body.get("snapshot_id") or snapshot_id,
        last=bool(body.get("last", last)), dry_run=bool(body.get("dry_run", dry_run)))
    audit.note(request, target_id=profile_id,
               after={"batch_id": str(body.get("batch_id") or batch_id)[:128],
                      "rows": len(body.get("rows") or []), "dry_run": bool(body.get("dry_run", dry_run))})
    return ok(result)


@router.post("/inventory-sync/profiles/{profile_id}/run", status_code=202)
def run_now(profile_id: str, request: Request, body: Optional[RunBody] = None,
            user: CurrentUser = Depends(require_analyst_or_above)):
    """'Sync now'. Answers at once with the run row (status `running`); the
    run history shows how it ended."""
    override = bool(body and body.override_brake)
    run = scheduler.start_manual(user.tenant_id, profile_id, user.user_id, override_brake=override)
    audit.note(request, target_id=profile_id, after={"run_id": run["id"], "override_brake": override})
    return ok({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in run.items()})
