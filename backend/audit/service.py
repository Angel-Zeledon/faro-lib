"""Reads the audit trail: filterable, paginated, exportable.

One normalised shape for every source (see `catalog.py`):

    {id, at, actor: {id, kind, label}, action, target: {type, id, label},
     before, after, status}

`action` is `<noun>.<verb>` (`dataset.deleted`); the frontend renders
`audit.action.<action>` and `audit.target.<type>` from the i18n catalogue, so
nothing here is prose.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterator, Optional

from backend.audit.catalog import (
    LEGACY, PREFIX, ROUTES, TARGET_TYPES, actions_for_target_type,
    all_stored_actions,
)
from backend.db.connection import query, query_one
from backend.utils.csv_safe import csv_safe

MAX_PAGE = 200
EXPORT_BATCH = MAX_PAGE
# A tenant's whole history in one download would be a denial of service by a
# button; the export is capped and says so in its last row.
EXPORT_MAX_ROWS = 50_000

_LABEL_KEYS = ("reference", "key_name", "email", "session_name", "name", "sku")
_NOISE_KEYS = {"severity", "kind", "reason", "reason_params"}


def audit_actions() -> list[str]:
    """Every normalised action name, for the filter."""
    names = {r.action for r in ROUTES.values()} | {n for _, n in LEGACY.values()}
    return sorted(names)


def _stored_for_action(action: str) -> list[str]:
    stored = []
    if any(r.action == action for r in ROUTES.values()):
        stored.append(PREFIX + action)
    stored += [legacy for legacy, (_, name) in LEGACY.items() if name == action]
    return stored


def _bounds(date_from: Optional[date], date_to: Optional[date]) -> tuple[Optional[datetime], Optional[datetime]]:
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc) if date_from else None
    # `date_to` is inclusive: the whole of that day.
    end = (datetime.combine(date_to, time.min, tzinfo=timezone.utc) + timedelta(days=1)
           if date_to else None)
    return start, end


def _where(
    tenant_id: str, *, actor: Optional[str], target_type: Optional[str],
    action: Optional[str], target_id: Optional[str], status: Optional[str],
    date_from: Optional[date], date_to: Optional[date],
) -> tuple[str, list[Any]]:
    clauses = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]

    if action:
        stored = _stored_for_action(action)
    elif target_type:
        stored = actions_for_target_type(target_type)
    else:
        # Machine writes the catalogue does not describe are noise by default;
        # asking for the `api_call` target type shows them.
        stored = [a for a in all_stored_actions() if a != "api_write"]
    if not stored:
        return "FALSE", []
    clauses.append("action IN %s")
    params.append(tuple(stored))

    if actor:
        clauses.append("user_id = %s")
        params.append(actor)
    if target_id:
        clauses.append("(resource = %s OR context->>'target_id' = %s)")
        params.extend([target_id, target_id])
    if status in ("success", "error"):
        clauses.append("status = %s")
        params.append(status)
    start, end = _bounds(date_from, date_to)
    if start:
        clauses.append("created_at >= %s")
        params.append(start)
    if end:
        clauses.append("created_at < %s")
        params.append(end)
    return " AND ".join(clauses), params


def _actor_labels(tenant_id: str, ids: set[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    user_ids = [i for i in ids if not i.startswith("api_key:") and i not in ("scheduler", "system")]
    if user_ids:
        for row in query("SELECT id, email FROM users WHERE tenant_id = %s AND id IN %s",
                         (tenant_id, tuple(user_ids))):
            labels[row["id"]] = row["email"]
    key_ids = [i.split(":", 1)[1] for i in ids if i.startswith("api_key:")]
    if key_ids:
        for row in query("SELECT id, name FROM api_keys WHERE tenant_id = %s AND id IN %s",
                         (tenant_id, tuple(key_ids))):
            labels[f"api_key:{row['id']}"] = row["name"]
    return labels


def _actor_kind(actor_id: str) -> str:
    if actor_id.startswith("api_key:"):
        return "api_key"
    if actor_id == "scheduler":
        return "schedule"
    if actor_id == "system":
        return "system"
    return "user"


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


def _normalise(row: dict, labels: dict[str, str]) -> dict:
    stored = row["action"]
    ctx = row.get("context") or {}
    if stored.startswith(PREFIX):
        action = stored[len(PREFIX):]
        target_type = ctx.get("target_type")
        target_id = ctx.get("target_id") or row.get("resource")
        target_label = ctx.get("target_label")
        before, after = ctx.get("before"), ctx.get("after")
    else:
        target_type, action = LEGACY[stored]
        target_id = row.get("resource")
        target_label = next((ctx[k] for k in _LABEL_KEYS if ctx.get(k)), None)
        before = after = None
        details = {k: v for k, v in ctx.items() if k not in _NOISE_KEYS}
        if stored == "session.delete":
            target_label = ctx.get("name")
            before = {"name": ctx.get("name"), "status": ctx.get("status_at_deletion")}
        elif stored == "account.user_role_changed":
            before, after = {"role": ctx.get("previous_role")}, {"role": ctx.get("role")}
        else:
            after = details or None
    actor_id = row["user_id"]
    return {
        "id": row["id"],
        "at": _iso(row["created_at"]),
        "actor": {"id": actor_id, "kind": ctx.get("actor_kind") or _actor_kind(actor_id),
                  "label": labels.get(actor_id)},
        "action": action,
        "target": {"type": target_type, "id": target_id, "label": target_label},
        "before": before,
        "after": after,
        "status": row["status"],
    }


def list_audit(
    tenant_id: str, *, limit: int = 50, offset: int = 0,
    actor: Optional[str] = None, target_type: Optional[str] = None,
    action: Optional[str] = None, target_id: Optional[str] = None,
    status: Optional[str] = None, date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    limit = max(1, min(int(limit), MAX_PAGE))
    where, params = _where(
        tenant_id, actor=actor, target_type=target_type, action=action,
        target_id=target_id, status=status, date_from=date_from, date_to=date_to)
    rows = query(
        f"""SELECT id, user_id, action, resource, context, status, created_at
              FROM activity_logs WHERE {where}
             ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s""",
        tuple(params) + (limit, offset),
    ) if where != "FALSE" else []
    total = (query_one(f"SELECT COUNT(*) AS n FROM activity_logs WHERE {where}", tuple(params))
             if where != "FALSE" else {"n": 0})
    labels = _actor_labels(tenant_id, {r["user_id"] for r in rows})
    return {
        "items": [_normalise(dict(r), labels) for r in rows],
        "total": int(total["n"]) if total else 0,
        "limit": limit, "offset": offset,
    }


def actors(tenant_id: str) -> list[dict]:
    """Everyone who appears in the trail, for the actor filter."""
    rows = query(
        "SELECT DISTINCT user_id FROM activity_logs WHERE tenant_id = %s AND action IN %s",
        (tenant_id, tuple(all_stored_actions())),
    )
    ids = {r["user_id"] for r in rows}
    labels = _actor_labels(tenant_id, ids)
    return sorted(
        ({"id": i, "kind": _actor_kind(i), "label": labels.get(i)} for i in ids),
        key=lambda a: (a["label"] or a["id"]).lower(),
    )


CSV_COLUMNS = ["at", "actor_kind", "actor_id", "actor_label", "action",
               "target_type", "target_id", "target_label", "before", "after", "status"]


def export_csv(tenant_id: str, **filters: Any) -> Iterator[str]:
    """The filtered trail as CSV, in batches so memory stays flat. Oldest cap
    is `EXPORT_MAX_ROWS`; when it bites, a final line says the export is cut."""
    import json

    def _line(cells: list[Any]) -> str:
        buf = io.StringIO()
        csv.writer(buf).writerow(cells)
        return buf.getvalue()

    yield _line(CSV_COLUMNS)
    emitted = 0
    offset = 0
    while emitted < EXPORT_MAX_ROWS:
        page = list_audit(tenant_id, limit=EXPORT_BATCH, offset=offset, **filters)
        # list_audit clamps to MAX_PAGE; step by what it actually returned.
        items = page["items"]
        if not items:
            return
        for e in items:
            yield _line([csv_safe(c) if isinstance(c, str) else c for c in [
                e["at"], e["actor"]["kind"], e["actor"]["id"], e["actor"]["label"] or "",
                e["action"], e["target"]["type"] or "", e["target"]["id"] or "",
                e["target"]["label"] or "",
                json.dumps(e["before"], default=str) if e["before"] is not None else "",
                json.dumps(e["after"], default=str) if e["after"] is not None else "",
                e["status"],
            ]])
            emitted += 1
            if emitted >= EXPORT_MAX_ROWS:
                break
        offset += len(items)
        if offset >= page["total"]:
            return
    yield _line(["# export truncated at %d rows; narrow the filters" % EXPORT_MAX_ROWS])


__all__ = ["list_audit", "export_csv", "actors", "audit_actions", "TARGET_TYPES"]
