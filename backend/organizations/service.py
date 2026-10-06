"""What Python itself must do for the organization hierarchy.

Everything else (the handshake, the grants, the consolidated reads) is served by
`backend-rs/src/routes/org*.rs`. See `backend/organizations/__init__.py` for the
grant model.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from backend.activity.service import generate_id
from backend.db.connection import _json, get_conn, query

log = logging.getLogger(__name__)

# The two events a counterpart gets when a link ends without either side
# pressing a button. Same shape `record_event` stores (whitelisted detail keys,
# then severity, kind and the reason), written here with the caller's own
# connection so the row is part of the erasure transaction.
_ENDED_ACTION = "org.link_revoked"


def drop_user_grants(user_id: str, conn: Optional[Any] = None) -> int:
    """Delete every grant a person holds. Called when their account is
    deactivated or suspended: the Rust reads also refuse a non-active user, but a
    dormant grant would come back the day the account is reactivated, and
    nobody would have decided that. Returns how many grants went."""
    if conn is None:
        with get_conn() as own:
            return drop_user_grants(user_id, own)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM org_link_grants WHERE user_id = %s RETURNING link_id", (user_id,))
        dropped = len(cur.fetchall())
    if dropped:
        log.info("[org] dropped %d grant(s) of user %s", dropped, user_id)
    return dropped


def end_links_for_erasure(conn: Any, tenant_id: str) -> int:
    """Whole-tenant erasure (`tenants/data_export.py`), inside its transaction.

    A tenant being erased may be a parent (its subsidiaries lose the link), a
    child (its holding loses the subsidiary), or both of those over time. For
    each live link the SURVIVING side gets a warning in its own activity feed,
    so a subsidiary that vanishes from a consolidated view is explained, not
    silently missing. Then every link and grant that names the tenant is
    deleted; the foreign keys would cascade them anyway, listing them here
    keeps the erasure's answer to "what belongs to a tenant" complete.
    """
    ended = 0
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, parent_tenant_id, child_tenant_id, label, status
                 FROM org_links
                WHERE parent_tenant_id = %s OR child_tenant_id = %s
                FOR UPDATE""",
            (tenant_id, tenant_id),
        )
        # The pool's cursors return dict rows (RealDictCursor): read by name.
        links = cur.fetchall()
        for row in links:
            if row["status"] == "active":
                parent, child = row["parent_tenant_id"], row["child_tenant_id"]
                survivor = child if parent == tenant_id else parent
                _record_ended(cur, survivor, row["id"], row["label"] if survivor == parent else None)
                ended += 1
        link_ids = [row["id"] for row in links]
        if link_ids:
            cur.execute("DELETE FROM org_link_grants WHERE link_id = ANY(%s)", (link_ids,))
            cur.execute("DELETE FROM org_links WHERE id = ANY(%s)", (link_ids,))
    return ended


def _record_ended(cur: Any, tenant_id: str, link_id: str, label: Optional[str]) -> None:
    context: dict[str, Any] = {}
    if label:
        context["label"] = label
    context.update({"severity": "warning", "kind": "account", "reason": "org_tenant_erased"})
    cur.execute(
        """INSERT INTO activity_logs (id, tenant_id, user_id, action, resource, context, status, created_at)
           SELECT %s, %s, 'system', %s, %s, %s, 'success', NOW()
            WHERE EXISTS (SELECT 1 FROM tenants WHERE id = %s)""",
        (generate_id("act"), tenant_id, _ENDED_ACTION, link_id, _json(context), tenant_id),
    )


def export_rows(tenant_id: str) -> dict[str, list[dict]]:
    """The tenant's own part of the hierarchy for the data export.

    The PARENT gets its links (without the code hash, which is a credential)
    and its grants. The CHILD gets only what it must be able to see: that a link
    exists, since when, and how it ended. It never gets the parent's tenant id,
    label, or who in the parent holds a grant.
    """
    as_parent = query(
        """SELECT id, parent_tenant_id, child_tenant_id, label, status, code_expires_at,
                  created_by, created_at, accepted_by, accepted_at, revoked_by, revoked_at,
                  revoked_side
             FROM org_links WHERE parent_tenant_id = %s ORDER BY created_at""",
        (tenant_id,),
    )
    grants = query(
        """SELECT g.link_id, g.user_id, g.granted_by, g.granted_at
             FROM org_link_grants g JOIN org_links l ON l.id = g.link_id
            WHERE l.parent_tenant_id = %s ORDER BY g.granted_at""",
        (tenant_id,),
    )
    as_child = query(
        """SELECT id, status, accepted_by, accepted_at, revoked_at, revoked_side
             FROM org_links WHERE child_tenant_id = %s ORDER BY created_at""",
        (tenant_id,),
    )
    return {
        "org_links_as_parent": as_parent,
        "org_link_grants": grants,
        "org_links_as_child": as_child,
    }
