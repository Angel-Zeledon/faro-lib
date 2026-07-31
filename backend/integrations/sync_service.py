"""Sync service — the core of the accounting-integrations feature.

`sync_connection` pulls a connected provider's catalog/stock/sales, imports
stock, builds a canonical sales dataset, and enqueues an auto-train job —
exactly the same dataset -> session -> train pipeline
`backend/api/v1/demo.py::demo_quickstart` uses, just fed by provider data
instead of a bundled CSV.
"""
import csv
import io
import logging

from backend.db.connection import execute, query, transaction
from backend.entitlements.service import enforce_limit
from backend.errors import AppError
from backend.integrations import registry, store
from backend.integrations.base import IntegrationSyncError, parse_provider_number
from backend.inventory import service as inv_svc
from backend.inventory import warehouse_service as wh_svc
from backend.db import session_store
from backend.sessions import service as session_svc
from backend.sessions.defaults import default_quickstart_configs
from backend.storage import paths
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

# create_session/create_job require a `created_by` user id, but an
# integration connection has no human attached to it — it's tenant-level,
# set up once by whoever configured the integration, and syncs can run on an
# unattended daily schedule. `jobs.created_by` is NOT NULL but (like
# `sessions.created_by`) has no FK constraint to users(id) — the daily
# training-completion path already uses this exact sentinel
# (backend/workers/runner.py: `_job.get("created_by") or "system"`), so this
# reuses that established convention instead of inventing a new one or
# resolving "the tenant's first admin user" (which a tenant could rename or
# delete, and which existing code doesn't do for the equivalent case).
_SYSTEM_USER_ID = "system"


_GATE_ERROR_CODES = frozenset({
    "training_blocked_data_fatal", "training_blocked_unresolved",
})


def _record_blocked_sync(connection_id: str, tenant_id: str, session_id: str,
                         error: AppError) -> None:
    """Put a gate refusal somewhere the tenant can act on it.

    The outer `except` already writes `str(e)` into `last_error`; that is one
    English sentence and the screen can do nothing with it but print it. This
    adds what the screen needs to be useful: which findings blocked the run,
    whether any of them can be answered at all, and — when they can — the exact
    options that were on offer, so the user is sent to the decision instead of
    to a support ticket.

    Deliberately never raises: this is the reporting path for a failure, and a
    failure inside it must not replace the real reason with its own.
    """
    if error.code not in _GATE_ERROR_CODES:
        return
    details = {
        "session_id": session_id,
        "issues": [i.strip() for i in
                   str((error.params or {}).get("issues", "")).split(",") if i.strip()],
        "remediable": error.code == "training_blocked_unresolved",
        "options": {},
    }
    try:
        from backend.sessions import data_gate as gate_svc
        data_quality = gate_svc.evaluate(tenant_id, session_id) or {}
        details["options"] = {
            issue["type"]: [o["code"] for o in issue.get("remediations", [])]
            for issue in data_quality.get("issues", [])
            if issue.get("classification") == "blocking_fixable"
        }
    except Exception as exc:      # pragma: no cover - reporting must not mask
        log.warning("[sync] could not attach gate options to connection=%s: %s",
                    connection_id, exc)
    try:
        store.mark_synced(connection_id, error=error.message,
                          error_code=error.code, error_details=details)
    except Exception as exc:      # pragma: no cover
        log.warning("[sync] could not record gate refusal on connection=%s: %s",
                    connection_id, exc)
    log.error(
        "[sync] tenant=%s connection=%s BLOCKED by the data gate (%s): %s. "
        "The forecast will stay at its last successful run until this is answered.",
        tenant_id, connection_id, error.code, details["issues"],
    )


def sync_connection(connection_id: str) -> dict:
    """Fetch provider data for `connection_id`, import stock, build a sales
    dataset, and enqueue a training job.

    Returns {session_id, job_id, dataset_id, stock_synced}.

    On any failure: records the error on the connection
    (`store.mark_synced(id, error=...)`, status='error') and re-raises, so a
    manual "sync now" endpoint can surface the failure to the caller. The
    daily loop (`run_daily_integration_syncs`) catches per-connection instead
    so one tenant's broken credentials never block another's sync.
    """
    conn_row = store.get_connection(connection_id)
    if conn_row is None:
        raise ValueError(f"No integration connection with id={connection_id!r}")
    tenant_id = conn_row["tenant_id"]

    try:
        creds = store.get_credentials(connection_id)
        provider = registry.get_provider(conn_row["provider"], creds)

        products = provider.fetch_products()
        stock = provider.fetch_stock()
        # The TRAINING dataset must always be built from the tenant's FULL
        # sales history, not just what changed since the last sync. Passing
        # `last_sync_at` here would starve every sync after the first down to
        # ~1 day of invoices — well under `validation_cfg.min_history`
        # (backend/sessions/defaults.py), so the trainer would skip every SKU
        # and auto-train would refresh nothing. There is no persistent sales
        # store to merge incremental fetches into (out of scope), so the
        # simplest correct approach is: every sync re-fetches and re-trains on
        # the complete history. `last_sync_at` is still recorded by
        # `store.mark_synced` below for display purposes only.
        sales = provider.fetch_sales(since=None)

        merged = _merge_products_and_stock(products, stock)

        # Normalize-at-write: resolve every provider warehouse/store spelling
        # to the tenant's canonical one BEFORE the limit pre-check, the stock
        # upserts, and the sales CSV — a provider sending 'norte' must land on
        # an existing 'Norte' location, not create a case-variant duplicate.
        # The lowercase-keyed cache avoids one query per row AND keeps a
        # single sync internally consistent: stock 'Norte' and sales 'norte'
        # first seen in the SAME sync resolve to one spelling even though
        # neither is committed to `warehouses` yet.
        _canonical_cache: dict[str, str] = {}

        def _canonical(raw: str | None) -> str:
            key = (raw or "").strip().lower() or wh_svc.DEFAULT_WAREHOUSE
            if key not in _canonical_cache:
                _canonical_cache[key] = wh_svc.resolve_canonical_name(tenant_id, raw)
            return _canonical_cache[key]

        for fields in merged.values():
            fields["warehouse"] = _canonical(fields["warehouse"])

        # Pre-check max_skus/max_locations for the NEW (sku, warehouse) pairs
        # before any write — mirrors demo_quickstart's pre-loop check, so a
        # tenant near its plan cap is rejected cleanly instead of failing
        # mid-loop inside upsert_stock's own per-row chokepoint (which would
        # otherwise leave a "committed prefix, aborted suffix" of partially
        # imported stock rows).
        existing_keys = inv_svc.list_stock_keys(tenant_id)
        new_pairs = {(sku, fields["warehouse"]) for sku, fields in merged.items()} - existing_keys
        if new_pairs:
            enforce_limit(tenant_id, "max_skus", inv_svc.count_stock(tenant_id), adding=len(new_pairs))
            new_warehouses = {wh for _, wh in new_pairs} - wh_svc.list_warehouse_names(tenant_id)
            if new_warehouses:
                enforce_limit(tenant_id, "max_locations", wh_svc.count_warehouses(tenant_id),
                              adding=len(new_warehouses))

        dataset_id = generate_id("ds")
        # When the provider exposed a branch/warehouse on any sale line, the
        # dataset gains a `store` column and the session's canonical mapping
        # maps it, so training groups per (sku, store) — see runner.py's
        # group_keys assembly. Store-less providers keep today's exact output.
        has_store = any(line.store is not None for line in sales)
        unreadable: list[dict] = []
        csv_bytes = _build_sales_csv(sales, with_store=has_store,
                                     resolve_store=_canonical, unreadable=unreadable)
        # Every line unreadable means the provider changed how it reports
        # quantities. Training on the empty CSV that produces would replace a
        # working forecast with nothing and still report the sync as a success,
        # so this fails loudly and the connection carries the reason.
        if sales and len(unreadable) == len(sales):
            raise IntegrationSyncError(
                f"None of the {len(sales)} sale line(s) had a readable quantity "
                f"(first: {unreadable[0]['value']!r}). Nothing was imported."
            )
        if unreadable:
            log.warning(
                "[sync] tenant=%s connection=%s skipped %d of %d sale line(s) with "
                "an unreadable quantity, e.g. %s",
                tenant_id, connection_id, len(unreadable), len(sales), unreadable[:3],
            )
        dst_dir = paths.dataset_dir(tenant_id, dataset_id)
        dst_dir.mkdir(parents=True, exist_ok=True)
        file_path = dst_dir / "data.csv"
        file_path.write_bytes(csv_bytes)

        # ── Transactional portion: stock upserts + dataset row insert ──────
        # upsert_stock/execute accept `conn=` and run on this one connection,
        # committed together at the end of the `with` block (or rolled back
        # together if anything inside raises).
        #
        # Transaction boundary (documented, not faked): session/job creation
        # below — session_svc.create_session/attach_dataset,
        # session_store.set_field, session_svc.force_status,
        # job_service.create_job/set_last_job/transition — do NOT accept
        # `conn=`. None of those helpers were changed to take one (the brief
        # explicitly forbids changing those signatures here), so they run
        # sequentially on their own auto-committing connections, exactly like
        # demo_quickstart already does today (demo.py has no transaction
        # around its own session/job sequence either). A crash between the
        # transactional block below and the session/job sequence would leave
        # imported stock + a dataset row without a session attached — no
        # worse than the pre-existing demo flow's atomicity, and recoverable
        # by re-running the sync (upsert_stock is idempotent per sku).
        with transaction() as db:
            for sku, fields in merged.items():
                inv_svc.upsert_stock(tenant_id, sku, fields, conn=db)

            execute(
                """INSERT INTO datasets
                   (id, tenant_id, name, original_filename, file_type, file_path,
                    size_bytes, uploaded_by, uploaded_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                (dataset_id, tenant_id, f"{conn_row['provider']}_sync",
                 f"{conn_row['provider']}_sales.csv", "csv", str(file_path),
                 len(csv_bytes), _SYSTEM_USER_ID),
                conn=db,
            )

        # ── Session + configs + auto-train (sequential — see boundary note) ─
        s = session_svc.create_session(
            tenant_id, _SYSTEM_USER_ID, f"{conn_row['provider'].capitalize()} sync"
        )
        session_id = s.get("session_id") or s["id"]
        session_svc.attach_dataset(tenant_id, session_id, dataset_id)
        configs = default_quickstart_configs()
        if has_store:
            configs["columns_cfg"]["canonical_mapping"]["store"] = "store"
        for field, cfg in configs.items():
            session_store.set_field(tenant_id, session_id, field, cfg)
        session_svc.force_status(tenant_id, session_id, "MODELS_CONFIGURED")

        from backend.sessions import family_service as fam
        try:
            family = fam.launch_training_family(tenant_id, session_id, _SYSTEM_USER_ID)
        except AppError as gate_error:
            # The pre-training gate holds ERP data to the same standard as an
            # upload, which is the point. But the upload screen has a human in
            # front of it who can choose a remediation, and this runs at 3 a.m.
            # with nobody watching: left as a bare re-raise, a tenant whose ERP
            # started reporting one row per invoice would see a red dot, keep
            # yesterday's forecast forever, and never be told which decision was
            # waiting. So the verdict — and the options that were on offer —
            # travel to the connection row for the integrations screen to show.
            _record_blocked_sync(connection_id, tenant_id, session_id, gate_error)
            raise
        job_id = family["base_job_id"]

        store.mark_synced(connection_id)
        log.info(
            "[sync] tenant=%s connection=%s provider=%s session=%s job=%s stock=%d sales_rows=%d",
            tenant_id, connection_id, conn_row["provider"], session_id, job_id,
            len(merged), len(sales),
        )
        return {
            "session_id": session_id,
            "job_id": job_id,
            "dataset_id": dataset_id,
            "stock_synced": list(merged.keys()),
            # Reaches the "sync now" caller so a partial import is visible
            # instead of being a number that quietly came out low.
            "skipped_sale_lines": len(unreadable),
        }
    except AppError as e:
        if e.code in _GATE_ERROR_CODES:
            # `_record_blocked_sync` already wrote the row, with the structured
            # detail this handler cannot reconstruct. Re-recording here would
            # overwrite `last_error_code`/`last_error_details` with NULL and
            # leave the screen holding the same English sentence it had before.
            raise
        store.mark_synced(connection_id, error=str(e))
        raise
    except Exception as e:
        store.mark_synced(connection_id, error=str(e))
        raise


def _merge_products_and_stock(products, stock) -> dict[str, dict]:
    """Merge ProviderProduct + ProviderStock lists by sku into the field
    dict `upsert_stock` expects. Products contribute display_name/unit_cost;
    stock contributes current_stock/warehouse. A sku present in only one
    list still gets a row — partial data beats no data."""
    merged: dict[str, dict] = {}
    for p in products:
        fields = merged.setdefault(p.sku, {})
        fields["display_name"] = p.name
        # Same boundary, same rule as the sale quantities: an ERP that reports
        # its cost as "1.234,56" would otherwise reach the DB as text. A cost
        # that cannot be read is left unset — `upsert_stock` keeps whatever the
        # tenant already had, which beats overwriting a real cost with a zero
        # and quietly reporting every margin on this SKU as pure profit.
        unit_cost = parse_provider_number(p.unit_cost)
        if unit_cost is not None:
            fields["unit_cost"] = unit_cost
    for s in stock:
        fields = merged.setdefault(s.sku, {})
        quantity = parse_provider_number(s.quantity)
        if quantity is not None:
            fields["current_stock"] = quantity
        fields["warehouse"] = s.warehouse
    from backend.inventory.warehouse_service import DEFAULT_WAREHOUSE
    for fields in merged.values():
        fields.setdefault("warehouse", DEFAULT_WAREHOUSE)
    return merged


def _build_sales_csv(sales, with_store: bool = False, resolve_store=None,
                     unreadable: "list | None" = None) -> bytes:
    """Aggregate ProviderSaleLine rows to (date, sku) -> summed quantity and
    write the canonical CSV header the wizard/demo default config expects:
    `default_quickstart_configs()["columns_cfg"]["canonical_mapping"]` maps
    canonical sku/date/demand to actual columns "sku"/"fecha"/"cantidad"
    (same header shape as backend/resources/demo_ventas.csv).

    With `with_store=True` (some sale line carried a provider warehouse) the
    aggregation key and the CSV gain a `store` column; lines whose payload had
    no warehouse fall back to 'principal' (the same default warehouse name the
    stock import uses) so every row keeps a concrete store value. With
    `with_store=False` the output is byte-identical to the pre-store format.

    `resolve_store`: optional (raw name | None) -> canonical name mapper —
    sync_connection passes its normalize-at-write resolver so store values
    written to the dataset use the tenant's canonical warehouse spellings
    ('norte' -> existing 'Norte'). Defaults to the plain
    'raw or DEFAULT_WAREHOUSE' fallback.

    `unreadable`: optional list the caller passes in to receive one
    `{sku, date, value}` entry per sale line whose quantity could not be read as
    a number (see `parse_provider_number`). Those lines are left OUT of the
    totals rather than counted as zero — a line nobody could read is not a day
    with no sales, and writing a zero would teach the model a stockout that
    never happened. The caller decides what to do with the list; dropping it on
    the floor here is what would make this silent.
    """
    from backend.inventory.warehouse_service import DEFAULT_WAREHOUSE
    if resolve_store is None:
        resolve_store = lambda raw: raw or DEFAULT_WAREHOUSE  # noqa: E731
    totals: dict[tuple, float] = {}
    for line in sales:
        quantity = parse_provider_number(line.quantity)
        if quantity is None:
            if unreadable is not None:
                unreadable.append({"sku": line.sku, "date": line.date.isoformat(),
                                   "value": str(line.quantity)[:32]})
            continue
        key = ((line.date, line.sku, resolve_store(line.store))
               if with_store else (line.date, line.sku))
        totals[key] = totals.get(key, 0.0) + quantity

    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["fecha", "sku", "cantidad"] + (["store"] if with_store else [])
    writer.writerow(header)
    for key, qty in sorted(totals.items(), key=lambda kv: (kv[0][0].isoformat(),) + kv[0][1:]):
        if with_store:
            (d, sku, store_name) = key
            writer.writerow([d.isoformat(), sku, qty, store_name])
        else:
            (d, sku) = key
            writer.writerow([d.isoformat(), sku, qty])
    return buf.getvalue().encode("utf-8")


def run_daily_integration_syncs() -> None:
    """Sync every integration connection, one at a time.

    Swallows and logs per-connection failures (already recorded on the
    connection row via `mark_synced`'s error path inside `sync_connection`)
    so one tenant's broken/expired credentials never block another tenant's
    daily sync run.
    """
    rows = query("SELECT id FROM integration_connections")
    for row in rows:
        try:
            sync_connection(row["id"])
        except Exception as e:
            log.warning("[sync] daily sync failed for connection=%s: %s", row["id"], e)
