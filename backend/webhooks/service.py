"""Outbound webhooks: emitting business events, and delivering them reliably.

Two halves joined by a table:

* **Emit** (`emit`, `emit_po_event`, `run_transition`): called from the code path
  that CAUSES an event. It only INSERTS rows into `webhook_deliveries` - one per
  matching webhook - so the request or worker that caused the event never waits
  for a customer's server, and a customer's server being down never fails it.
* **Deliver** (`process_due`, run by the `webhook-deliveries` worker loop): claims
  due rows with `FOR UPDATE SKIP LOCKED`, posts them, and records the outcome.
  A 5xx, a timeout, a 408 or a 429 is retried on the schedule in `policy.py`; the
  row keeps status, attempts, last status code and the (truncated) last error, so
  "did it arrive?" is answerable from `GET /webhooks/{id}/deliveries`.

Safety:

* Every delivery re-resolves the target host and refuses private, loopback,
  link-local and cloud-metadata addresses (`backend/datasources/network.py`, the
  same rule as SQL data sources, same `SQL_SOURCES_ALLOW_PRIVATE_HOSTS` switch for
  a self-hosted installation). The connection is then made to the checked
  address, not to the name, so a DNS answer that changes in between cannot
  redirect it. Redirects are never followed.
* A hook that gives up on several different days with no success between is
  switched off and the account is told (activity event `webhook.auto_disabled`).

An emitter never raises: a failure to enqueue is logged at ERROR and the business
action goes on. The once-per-transition emitters are the exception, see
`run_transition`.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import httpx

from backend.datasources import network
from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError
from backend.webhooks import catalog, policy, signing

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
CLAIM_BATCH = 20

_NETWORK_REFUSALS = {
    "data_source_host_not_allowed": "webhook_host_not_allowed",
    "data_source_host_forbidden":   "webhook_host_forbidden",
    "data_source_dns_failed":       "webhook_host_unresolvable",
}


@dataclass
class Attempt:
    status_code: Optional[int]
    error: Optional[str]
    # True when waiting cannot help (the address is refused): fail at once.
    permanent: bool = False


# ── Target validation ────────────────────────────────────────────────────────

def _port_of(parts) -> int:
    try:
        return parts.port or 443
    except ValueError:
        raise AppError("webhook_url_invalid", "The webhook URL is not valid.",
                       status_code=422)


def validate_target(url: str) -> None:
    """Refuse a URL we must never post to, at save time. A host name is
    resolved now and again at every delivery."""
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise AppError("webhook_url_invalid",
                       "The webhook URL must be https://host/path without credentials.",
                       status_code=422)
    port = _port_of(parts)
    try:
        network.resolve_allowed(host, port, allow_private=network.allow_private_hosts())
    except AppError as exc:
        code = _NETWORK_REFUSALS.get(exc.code)
        if code is None:
            raise
        raise AppError(code, exc.message, status_code=422, params=exc.params) from exc


# ── Emitting ─────────────────────────────────────────────────────────────────

def subscribers(tenant_id: str, event_type: str) -> list[dict]:
    """Enabled webhooks of the tenant subscribed to `event_type`."""
    return query(
        """SELECT id, warehouse_scope FROM webhooks
            WHERE tenant_id = %s AND disabled_at IS NULL AND %s = ANY(events)""",
        (tenant_id, event_type))


def select_recipients(hooks: list[dict], event_type: str,
                      warehouse_id: Optional[str]) -> list[dict]:
    """The hooks that may receive an event about `warehouse_id` (pure)."""
    aware = catalog.EVENT_TYPES[event_type].warehouse_aware
    return [h for h in hooks
            if policy.scope_allows(policy.parse_scope(h.get("warehouse_scope")),
                                   warehouse_id, aware)]


def _enqueue(tenant_id: str, webhook_id: str, envelope: dict, *, is_test: bool = False) -> str:
    row = query_one(
        """INSERT INTO webhook_deliveries
               (tenant_id, webhook_id, event_id, event_type, is_test, payload, next_attempt_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())
           RETURNING id""",
        (tenant_id, webhook_id, envelope["id"], envelope["type"], is_test,
         json.dumps(envelope, default=str, separators=(",", ":"))))
    return row["id"]


def emit(tenant_id: str, event_type: str, data: dict[str, Any], *,
         warehouse_id: Optional[str] = None, hooks: Optional[list[dict]] = None,
         strict: bool = False) -> int:
    """Queue `event_type` for every webhook that should get it. Returns how many
    deliveries were queued. Never raises unless `strict` (see `run_transition`)."""
    try:
        if hooks is None:
            hooks = subscribers(tenant_id, event_type)
        recipients = select_recipients(hooks, event_type, warehouse_id)
        if not recipients:
            return 0
        envelope = catalog.build_envelope(event_type, tenant_id, data)
        for hook in recipients:
            _enqueue(tenant_id, hook["id"], envelope)
        return len(recipients)
    except Exception:  # noqa: BLE001 - an event must never fail the action that caused it
        if strict:
            raise
        log.error("[webhooks] could not queue %s for tenant=%s", event_type, tenant_id,
                  exc_info=True)
        return 0


def warehouse_ref(tenant_id: str, name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(warehouse name, warehouse id) for a name that may be blank, meaning the
    tenant's default warehouse. The id is None when no such row exists, which
    keeps the event away from warehouse-scoped hooks (fail closed)."""
    from backend.inventory import warehouse_service as wh
    resolved = (name or "").strip() or wh.get_default_warehouse_name(tenant_id) or wh.DEFAULT_WAREHOUSE
    row = query_one(
        "SELECT id, name FROM warehouses WHERE tenant_id = %s AND lower(name) = lower(%s)",
        (tenant_id, resolved))
    return (row["name"], row["id"]) if row else (resolved, None)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _num(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def emit_po_event(tenant_id: str, event_type: str, po_log_id: str, *,
                  decided_by: Optional[str] = None) -> int:
    """A purchase order was approved / rejected / sent / cancelled. The payload is
    read from the order as it is NOW, after the change that caused the event."""
    try:
        if not subscribers(tenant_id, event_type):
            return 0
        po = query_one(
            """SELECT id, po_number, destination_warehouse, sku_count, total_units,
                      total_value, approved_amount, sent_at, cancelled_at, cancelled_by
                 FROM inventory_po_log WHERE id = %s AND tenant_id = %s""",
            (po_log_id, tenant_id))
        if not po:
            return 0
        wh_name, wh_id = warehouse_ref(tenant_id, po.get("destination_warehouse"))
        data: dict[str, Any] = {
            "po_log_id": po["id"], "po_number": po.get("po_number"),
            "warehouse": wh_name, "warehouse_id": wh_id,
            "sku_count": po.get("sku_count"), "total_units": _num(po.get("total_units")),
            "total_value": _num(po.get("total_value")),
        }
        spec = catalog.EVENT_TYPES[event_type]
        if "decided_by" in spec.data_keys:
            data["decided_by"] = decided_by
        if "approved_amount" in spec.data_keys:
            data["approved_amount"] = _num(po.get("approved_amount"))
        if "sent_at" in spec.data_keys:
            data["sent_at"] = _iso(po.get("sent_at"))
        if "cancelled_at" in spec.data_keys:
            data["cancelled_at"] = _iso(po.get("cancelled_at"))
            data["cancelled_by"] = po.get("cancelled_by")
        return emit(tenant_id, event_type, data, warehouse_id=wh_id)
    except Exception:  # noqa: BLE001
        log.error("[webhooks] could not build %s for po=%s", event_type, po_log_id, exc_info=True)
        return 0


def emit_commitment_event(tenant_id: str, event_type: str, commitment: dict,
                          **extra: Any) -> int:
    """`commitment` is a `committed_demand_service` row (dict)."""
    try:
        hooks = subscribers(tenant_id, event_type)
        if not hooks:
            return 0
        wh_id = commitment.get("warehouse_id") or None
        wh_name = None
        if wh_id:
            row = query_one("SELECT name FROM warehouses WHERE id = %s AND tenant_id = %s",
                            (wh_id, tenant_id))
            wh_name = row["name"] if row else None
            if row is None:
                wh_id = None
        data = {
            "commitment_id": commitment["id"], "sku": commitment.get("sku"),
            "warehouse": wh_name, "warehouse_id": wh_id,
            "customer": commitment.get("customer"),
            "delivery_date": _iso(commitment.get("delivery_date")),
            "quantity": _num(commitment.get("quantity")),
            **extra,
        }
        return emit(tenant_id, event_type, data, warehouse_id=wh_id, hooks=hooks)
    except Exception:  # noqa: BLE001
        log.error("[webhooks] could not build %s for commitment=%s", event_type,
                  commitment.get("id"), exc_info=True)
        return 0


def fire_webhooks(tenant_id: str, event: str, payload: dict) -> None:
    """The original entry point (job events). Same behaviour, now durable."""
    emit(tenant_id, event, payload)


# ── Once per transition ──────────────────────────────────────────────────────

def _load_state(tenant_id: str, kind: str) -> set[str]:
    return {r["state_key"] for r in query(
        "SELECT state_key FROM webhook_transition_state WHERE tenant_id = %s AND kind = %s",
        (tenant_id, kind))}


def _save_state(tenant_id: str, kind: str, current: set[str]) -> None:
    keys = sorted(current)
    with transaction() as conn:
        execute(
            """DELETE FROM webhook_transition_state
                WHERE tenant_id = %s AND kind = %s AND NOT (state_key = ANY(%s::text[]))""",
            (tenant_id, kind, keys), conn=conn)
        if keys:
            execute(
                """INSERT INTO webhook_transition_state (tenant_id, kind, state_key)
                   SELECT %s, %s, k FROM unnest(%s::text[]) AS k
                   ON CONFLICT DO NOTHING""",
                (tenant_id, kind, keys), conn=conn)


def run_transition(tenant_id: str, kind: str, event_type: str,
                   current: dict[str, tuple[dict[str, Any], Optional[str]]]) -> int:
    """Emit `event_type` for every key that ENTERED the state since the last pass,
    then remember the state. `current` maps a stable key to (data, warehouse_id).

    * A key that stays in the state is not emitted again; one that leaves and
      comes back is.
    * With no webhook subscribed, nothing is emitted and the remembered state is
      cleared, so a hook created later starts from "what is true now".
    * Emission is strict and happens BEFORE the state is saved: if queueing
      fails the state is untouched and the next pass tries again (an event may
      repeat after a crash; it is never lost). Raises on failure - the caller
      logs it per tenant.
    """
    hooks = subscribers(tenant_id, event_type)
    if not hooks:
        _save_state(tenant_id, kind, set())
        return 0
    new_keys = policy.newly_entered(_load_state(tenant_id, kind), current.keys())
    queued = 0
    for key in new_keys:
        data, warehouse_id = current[key]
        queued += emit(tenant_id, event_type, data, warehouse_id=warehouse_id,
                       hooks=hooks, strict=True)
    _save_state(tenant_id, kind, set(current))
    return queued


def stockout_transitions(tenant_id: str, rows: list[dict], *, default_warehouse: Optional[str],
                         day: Optional[str] = None) -> int:
    """`stockout.imminent`: a SKU in a warehouse newly enters PEDIR_YA.

    `rows` are the per-(sku, warehouse) status rows when the tenant has several
    warehouses, or the aggregated rows (no `warehouse` key) for a single one -
    then every row belongs to the default warehouse.
    """
    today = day or datetime.now(timezone.utc).date().isoformat()
    ids: dict[str, Optional[str]] = {}
    current: dict[str, tuple[dict[str, Any], Optional[str]]] = {}
    for r in rows:
        if r.get("signal") != "PEDIR_YA" or not r.get("sku"):
            continue
        name = r.get("warehouse") or default_warehouse
        if name not in ids:
            ids[name] = warehouse_ref(tenant_id, name)[1]
        wh_id = r.get("warehouse_id") or ids[name]
        current[f"{r['sku']}|{wh_id or name}"] = ({
            "sku": r["sku"], "warehouse": name, "warehouse_id": wh_id,
            "signal": "PEDIR_YA", "current_stock": _num(r.get("current_stock")),
            "coverage_days": _num(r.get("coverage_days")),
            "reorder_point": _num(r.get("reorder_point")),
            "detected_on": today,
        }, wh_id)
    return run_transition(tenant_id, "stockout", "stockout.imminent", current)


COMMITMENT_SCAN_LIMIT = 2000


def commitment_risk_transitions(tenant_id: str) -> int:
    """`commitment.at_risk`: an open commitment newly becomes at risk (the same
    company-wide verdict the Commitments screen shows)."""
    from backend.inventory import committed_demand_service as cd
    if not subscribers(tenant_id, "commitment.at_risk"):
        _save_state(tenant_id, "commitment_risk", set())
        return 0
    items = cd.list_for_tenant(tenant_id, status="open", limit=COMMITMENT_SCAN_LIMIT)
    if len(items) >= COMMITMENT_SCAN_LIMIT:
        # A truncated list would make commitments past the cut look as if they
        # had left the state and re-entered on the next pass: duplicates.
        log.warning("[webhooks] tenant=%s has >= %d open commitments; "
                    "commitment.at_risk skipped this pass", tenant_id, COMMITMENT_SCAN_LIMIT)
        return 0
    cd.annotate_risk(tenant_id, items)
    wh_names: dict[str, Optional[str]] = {}
    current: dict[str, tuple[dict[str, Any], Optional[str]]] = {}
    for i in items:
        if i.get("at_risk") is not True:
            continue
        wh_id = i.get("warehouse_id") or None
        if wh_id and wh_id not in wh_names:
            row = query_one("SELECT name FROM warehouses WHERE id = %s AND tenant_id = %s",
                            (wh_id, tenant_id))
            wh_names[wh_id] = row["name"] if row else None
        if wh_id and wh_names.get(wh_id) is None:
            wh_id = None
        current[i["id"]] = ({
            "commitment_id": i["id"], "sku": i.get("sku"),
            "warehouse": wh_names.get(wh_id) if wh_id else None, "warehouse_id": wh_id,
            "customer": i.get("customer"), "delivery_date": _iso(i.get("delivery_date")),
            "quantity": _num(i.get("quantity")), "shortfall": _num(i.get("shortfall")),
            "latest_safe_order_date": _iso(i.get("latest_safe_order_date")),
            "order_date_passed": i.get("order_date_passed"),
        }, wh_id)
    return run_transition(tenant_id, "commitment_risk", "commitment.at_risk", current)


def run_daily_commitment_transitions() -> None:
    """Daily pass over tenants that have a webhook for commitment risk."""
    tenants = query(
        """SELECT DISTINCT tenant_id FROM webhooks
            WHERE disabled_at IS NULL AND 'commitment.at_risk' = ANY(events)""")
    for t in tenants:
        try:
            commitment_risk_transitions(t["tenant_id"])
        except Exception:  # noqa: BLE001 - one tenant must not stop the others
            log.error("[webhooks] commitment risk pass failed tenant=%s", t["tenant_id"],
                      exc_info=True)


# ── Delivering ───────────────────────────────────────────────────────────────

def _send(url: str, body: bytes, headers: dict[str, str]) -> Attempt:
    """One POST. Resolves and checks the host, then connects to the checked
    address (SNI and certificate verification still use the host name)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = _port_of(parts)
        addresses = network.resolve_allowed(
            host, port, allow_private=network.allow_private_hosts())
    except AppError as exc:
        permanent = exc.code in ("data_source_host_not_allowed", "data_source_host_forbidden",
                                 "webhook_url_invalid")
        return Attempt(None, policy.truncate_error(f"{exc.code} {host}"), permanent)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    host_header = f"{host}:{parts.port}" if parts.port else host
    last_error = "connect_error"
    for ip in addresses:
        literal = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
        target = f"https://{literal}:{port}{path}"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
                with client.stream("POST", target, content=body,
                                   headers={**headers, "Host": host_header},
                                   extensions={"sni_hostname": host}) as response:
                    return Attempt(response.status_code, None)
        except httpx.ConnectError:
            last_error = "connect_error"
            continue
        except httpx.TimeoutException:
            return Attempt(None, "timeout")
        except httpx.HTTPError as exc:
            return Attempt(None, policy.truncate_error(type(exc).__name__))
    return Attempt(None, last_error)


def claim_due(limit: int = CLAIM_BATCH) -> list[dict]:
    """Take due deliveries. Claiming counts the attempt and pushes
    `next_attempt_at` out by a lease, so a worker that dies mid-delivery has its
    rows picked up again later, and two workers never take the same row."""
    rows = query(
        """UPDATE webhook_deliveries d
              SET attempts = d.attempts + 1,
                  last_attempt_at = NOW(),
                  next_attempt_at = NOW() + (%s || ' seconds')::interval
            WHERE d.id IN (
                SELECT id FROM webhook_deliveries
                 WHERE status = 'pending' AND next_attempt_at <= NOW()
                 ORDER BY next_attempt_at
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED)
        RETURNING d.*""",
        (str(policy.CLAIM_LEASE_SECONDS), limit))
    # UPDATE ... RETURNING has no row order; deliver oldest first.
    return sorted(rows, key=lambda r: r["created_at"])


def deliver_one(row: dict) -> str:
    """Post one claimed delivery and record what happened. Returns the new status."""
    hook = query_one(
        "SELECT id, url, secret, disabled_at FROM webhooks WHERE id = %s AND tenant_id = %s",
        (row["webhook_id"], row["tenant_id"]))
    if hook is None or (hook["disabled_at"] is not None and not row["is_test"]):
        execute(
            """UPDATE webhook_deliveries
                  SET status = 'abandoned', next_attempt_at = NULL,
                      last_error = %s
                WHERE id = %s""",
            ("webhook_removed" if hook is None else "webhook_disabled", row["id"]))
        return "abandoned"
    body = row["payload"].encode()
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "StockAI-Webhooks/1",
        "X-StockAI-Event": row["event_type"],
        "X-StockAI-Delivery": row["id"],
        "X-StockAI-Signature": signing.signature_header(hook["secret"], timestamp, body),
        # First-version headers, kept so existing receivers keep verifying.
        "X-Signature": signing.legacy_signature(hook["secret"], body),
        "X-Event": row["event_type"],
    }
    attempt = _send(hook["url"], body, headers)
    outcome = (policy.OUTCOME_FAIL if attempt.permanent
               else policy.classify_outcome(attempt.status_code, attempt.error))
    return record_attempt(row, attempt, outcome)


def record_attempt(row: dict, attempt: Attempt, outcome: str) -> str:
    error = attempt.error
    if error is None and outcome != policy.OUTCOME_SUCCESS and attempt.status_code is not None:
        error = f"http_{attempt.status_code}"
    delay = (policy.next_delay_seconds(int(row["attempts"]))
             if outcome == policy.OUTCOME_RETRY else None)
    if outcome == policy.OUTCOME_SUCCESS:
        execute(
            """UPDATE webhook_deliveries
                  SET status = 'delivered', delivered_at = NOW(), last_attempt_at = NOW(),
                      last_status_code = %s, last_error = NULL, next_attempt_at = NULL
                WHERE id = %s""",
            (attempt.status_code, row["id"]))
        if not row["is_test"]:
            execute(
                """UPDATE webhooks SET failure_days = 0, last_failure_on = NULL
                    WHERE id = %s AND tenant_id = %s AND (failure_days <> 0
                          OR last_failure_on IS NOT NULL)""",
                (row["webhook_id"], row["tenant_id"]))
        return "delivered"
    if delay is not None:
        execute(
            """UPDATE webhook_deliveries
                  SET last_attempt_at = NOW(), last_status_code = %s, last_error = %s,
                      next_attempt_at = NOW() + (%s || ' seconds')::interval
                WHERE id = %s""",
            (attempt.status_code, policy.truncate_error(error or ""), str(delay), row["id"]))
        return "pending"
    execute(
        """UPDATE webhook_deliveries
              SET status = 'failed', last_attempt_at = NOW(), last_status_code = %s,
                  last_error = %s, next_attempt_at = NULL
            WHERE id = %s""",
        (attempt.status_code, policy.truncate_error(error or ""), row["id"]))
    if not row["is_test"]:
        register_failure(row["tenant_id"], row["webhook_id"])
    return "failed"


def register_failure(tenant_id: str, webhook_id: str) -> bool:
    """A delivery gave up. Counts the day and switches the hook off after
    `policy.DISABLE_AFTER_FAILURE_DAYS` such days with no success between.
    Returns True when this call disabled it."""
    hook = query_one(
        """SELECT url, failure_days, last_failure_on, disabled_at
             FROM webhooks WHERE id = %s AND tenant_id = %s""",
        (webhook_id, tenant_id))
    if hook is None:
        return False
    today = datetime.now(timezone.utc).date()
    days, last_on = policy.advance_failure_days(
        int(hook["failure_days"] or 0), hook["last_failure_on"], today)
    disable = hook["disabled_at"] is None and policy.should_disable(days)
    execute(
        """UPDATE webhooks
              SET failure_days = %s, last_failure_on = %s,
                  disabled_at = CASE WHEN %s THEN NOW() ELSE disabled_at END,
                  disabled_reason = CASE WHEN %s THEN 'failing_for_days' ELSE disabled_reason END
            WHERE id = %s AND tenant_id = %s""",
        (days, last_on, disable, disable, webhook_id, tenant_id))
    if disable:
        from backend.activity.events import record_event
        record_event(
            tenant_id, "system", "webhook.auto_disabled", resource=webhook_id,
            reason="webhook_failing_for_days", reason_params={"days": days},
            details={"host": urlsplit(hook["url"]).hostname})
        log.warning("[webhooks] disabled webhook=%s tenant=%s after %d failing days",
                    webhook_id, tenant_id, days)
    return disable


def process_due(limit: int = CLAIM_BATCH,
                deliver: Callable[[dict], str] = deliver_one) -> int:
    """Claim and deliver one batch. Returns how many deliveries were attempted."""
    rows = claim_due(limit)
    for row in rows:
        try:
            deliver(row)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stall the batch
            log.error("[webhooks] delivery %s crashed", row.get("id"), exc_info=True)
            try:
                record_attempt(row, Attempt(None, policy.truncate_error(type(exc).__name__)),
                               policy.OUTCOME_RETRY)
            except Exception:  # noqa: BLE001
                log.error("[webhooks] could not record the crash of %s", row.get("id"),
                          exc_info=True)
    return len(rows)


def kick() -> None:
    """Try due deliveries now, off the request thread (the 'send test' button).
    The worker loop would reach them within seconds anyway."""
    def _run():
        try:
            process_due()
        except Exception:  # noqa: BLE001
            log.error("[webhooks] kick failed", exc_info=True)
    threading.Thread(target=_run, daemon=True, name="webhook-kick").start()


def enqueue_test(tenant_id: str, webhook_id: str) -> str:
    envelope = catalog.build_envelope("webhook.test", tenant_id, {"webhook_id": webhook_id})
    return _enqueue(tenant_id, webhook_id, envelope, is_test=True)
