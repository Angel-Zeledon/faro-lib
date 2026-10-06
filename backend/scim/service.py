"""SCIM users and role groups in the database, and the provisioning log.

The decisions are in `backend/scim/__init__.py`. Every query here is scoped by
the tenant of the authenticated token (`ScimContext.tenant_id`); a user id from
another tenant is simply "not found".
"""

from __future__ import annotations

import json
import logging
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from backend.scim import protocol, tokens
from backend.scim.protocol import ScimError, UserState

log = logging.getLogger(__name__)

USER_FILTER_ATTRS = frozenset({"username", "emails.value", "externalid", "id"})
GROUP_FILTER_ATTRS = frozenset({"displayname", "id"})

# One identity provider's sync, per token. An initial import of a few thousand
# people at Okta's default pace fits; a loop does not.
TOKEN_RATE_PER_MINUTE = 600
# Wrong or revoked tokens per client address before it is told to wait.
AUTH_FAILURES_MAX = 20
AUTH_FAILURE_WINDOW_SECS = 300

ACTOR = "scim"


@dataclass(frozen=True)
class ScimContext:
    tenant_id: str
    token_id: str
    manage_admins: bool
    base_url: str
    allowed_domains: tuple[str, ...]


def base_url() -> str:
    """What the tenant admin pastes into the identity provider."""
    from backend.config import settings
    return f"{settings.frontend_url.rstrip('/')}/api/v1/scim/v2"


# ── Preconditions ────────────────────────────────────────────────────────────

def sso_row_ready(tenant_id: str) -> Optional[dict]:
    """The tenant's SSO row when SCIM may run against it, else None."""
    from backend.auth.sso import service as sso
    if not sso.instance_enabled():
        return None
    row = sso.get_row(tenant_id)
    if not row or not row["enabled"]:
        return None
    return row


def context_for(token_row: dict) -> ScimContext:
    row = sso_row_ready(token_row["tenant_id"])
    if not row:
        raise ScimError(
            403, "SCIM needs company sign-in configured and enabled for this account.",
            code="scim_sso_not_configured",
        )
    return ScimContext(
        tenant_id=token_row["tenant_id"], token_id=token_row["id"],
        manage_admins=bool(token_row["manage_admins"]), base_url=base_url(),
        allowed_domains=tuple(row["allowed_domains"] or ()),
    )


# ── Rate limits ──────────────────────────────────────────────────────────────

def _testing() -> bool:
    from backend.config import settings
    return bool(settings.testing_mode)


def auth_failures_exceeded(address: str) -> bool:
    if _testing():
        return False
    from backend.db.connection import execute, query_one
    key = f"scim-fail:{address}"
    try:
        execute("DELETE FROM auth_rate_events WHERE key = %s "
                "AND created_at < NOW() - make_interval(secs => %s)",
                (key, AUTH_FAILURE_WINDOW_SECS))
        row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (key,))
        return bool(row and int(row["n"]) >= AUTH_FAILURES_MAX)
    except Exception:  # noqa: BLE001 - a broken limiter must not lock every IdP out
        log.warning("[scim] auth-failure limiter unavailable", exc_info=True)
        return False


def record_auth_failure(address: str) -> None:
    if _testing():
        return
    from backend.db.connection import execute
    try:
        execute("INSERT INTO auth_rate_events (key) VALUES (%s)", (f"scim-fail:{address}",))
    except Exception:  # noqa: BLE001
        log.warning("[scim] could not record an authentication failure", exc_info=True)


def token_within_rate(token_id: str) -> bool:
    """Same locked count-then-insert as API keys (`api_key_auth.check_rate`),
    on its own bucket. Fails OPEN on a database problem, like API keys: a
    limiter that cannot write must not stop a company's provisioning."""
    if _testing():
        return True
    from backend.auth.api_key_auth import _within
    from backend.db.connection import execute, query_one, transaction
    bucket = f"scim:{token_id}"
    try:
        with transaction() as conn:
            query_one("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"ratelimit:{bucket}",),
                      conn=conn)
            if not _within(bucket, TOKEN_RATE_PER_MINUTE, 60, conn=conn):
                return False
            execute("INSERT INTO auth_rate_events (key) VALUES (%s)", (bucket,), conn=conn)
        return True
    except Exception:  # noqa: BLE001
        log.warning("[scim] rate limiter unavailable; request allowed", exc_info=True)
        return True


# ── The provisioning log ─────────────────────────────────────────────────────

def log_event(ctx: ScimContext, *, operation: str, resource_type: str,
              resource_id: Optional[str], email: Optional[str], outcome: str,
              http_status: int, error_code: Optional[str] = None,
              changes: Optional[dict] = None) -> bool:
    """One row of the admin's provisioning log. Never raises: the write it
    describes already happened (or was already refused). A failure is logged at
    ERROR with the tenant, because a provisioning log with holes in it is the
    kind of quiet nothing that log exists to prevent."""
    from backend.db.connection import execute
    from backend.utils.ids import generate_id
    try:
        execute(
            """INSERT INTO scim_events (id, tenant_id, token_id, operation, resource_type,
                                        resource_id, email, outcome, http_status,
                                        error_code, changes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)""",
            (generate_id("sce"), ctx.tenant_id, ctx.token_id, operation, resource_type,
             resource_id, email, outcome, int(http_status), error_code,
             json.dumps(changes or {}, default=str)),
        )
        return True
    except Exception:  # noqa: BLE001
        log.error("[scim] provisioning log write FAILED tenant=%s op=%s resource=%s",
                  ctx.tenant_id, operation, resource_id, exc_info=True)
        return False


def _record(ctx: ScimContext, action: str, user_id: str, details: dict,
            reason: Optional[str] = "provisioned_by_identity_provider",
            reason_params: Optional[dict] = None, status: str = "success") -> None:
    from backend.activity.events import record_event
    record_event(ctx.tenant_id, ACTOR, action, resource=user_id or None, details=details,
                 reason=reason, reason_params=reason_params, status=status)


@contextmanager
def refusals(ctx: ScimContext, operation: str, resource_type: str,
             resource_id: Optional[str] = None) -> Iterator[dict]:
    """Log a refused write before the refusal leaves.

    The body fills `info["email"]` / `info["resource_id"]` as it learns them.
    A 403 or 409 (a rule said no: last admin, ceiling, another tenant's
    address, an administrator SCIM may not touch) also reaches the tenant's
    activity feed, because the IdP administrator who sees the error is often
    not the StockAI administrator who can do something about it.
    """
    info: dict[str, Any] = {"email": None, "resource_id": resource_id}
    try:
        yield info
    except ScimError as err:
        log_event(ctx, operation=operation, resource_type=resource_type,
                  resource_id=info.get("resource_id"), email=info.get("email"),
                  outcome="error", http_status=err.status, error_code=err.code,
                  changes={"detail": err.detail[:300]})
        if err.status in (403, 409):
            _record(ctx, "account.scim_request_refused", info.get("resource_id") or "",
                    {"email": info.get("email"), "operation": operation},
                    reason="scim_request_refused", reason_params={"code": err.code},
                    status="error")
        raise


def recent_events(tenant_id: str, limit: int = 20, offset: int = 0) -> dict:
    from backend.db.connection import query, query_one
    limit = max(1, min(int(limit), 100))
    rows = query(
        """SELECT id, created_at, operation, resource_type, resource_id, email, outcome,
                  http_status, error_code, changes
             FROM scim_events WHERE tenant_id = %s
            ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s""",
        (tenant_id, limit, max(0, int(offset))),
    )
    total = query_one("SELECT COUNT(*) AS n FROM scim_events WHERE tenant_id = %s", (tenant_id,))
    return {
        "items": [{**{k: v for k, v in r.items() if k != "created_at"},
                   "created_at": r["created_at"].isoformat() if r["created_at"] else None}
                  for r in rows],
        "total": int(total["n"]) if total else 0,
    }


def last_change_at(tenant_id: str) -> Optional[str]:
    from backend.db.connection import query_one
    row = query_one(
        "SELECT MAX(created_at) AS at FROM scim_events WHERE tenant_id = %s AND outcome = 'success'",
        (tenant_id,),
    )
    return row["at"].isoformat() if row and row["at"] else None


# ── Reading users ────────────────────────────────────────────────────────────

_USER_SELECT = """
    SELECT u.id, u.tenant_id, u.email, u.full_name, u.role, u.status, u.email_verified,
           u.warehouse_scope, u.created_at, u.updated_at,
           l.external_id, l.given_name, l.family_name
      FROM users u
      LEFT JOIN scim_user_links l ON l.user_id = u.id AND l.tenant_id = u.tenant_id
"""


def _user_where(ctx: ScimContext, comps: list[protocol.Comparison]) -> tuple[str, list]:
    clauses = ["u.tenant_id = %s"]
    params: list[Any] = [ctx.tenant_id]
    for c in comps:
        if not isinstance(c.value, str):
            raise protocol.invalid_filter(f"'{c.attribute}' is compared with a quoted string.")
        if c.attribute in ("username", "emails.value"):
            clauses.append("u.email = %s")
            params.append(c.value.strip().lower())
        elif c.attribute == "externalid":
            clauses.append("l.external_id = %s")
            params.append(c.value)
        elif c.attribute == "id":
            clauses.append("u.id = %s")
            params.append(c.value)
    return " AND ".join(clauses), params


def list_users(ctx: ScimContext, filter_text: Optional[str], start_index: Any,
               count: Any) -> dict:
    from backend.db.connection import query, query_one
    comps = protocol.parse_filter(filter_text, USER_FILTER_ATTRS)
    offset, limit = protocol.parse_paging(start_index, count)
    where, params = _user_where(ctx, comps)
    total = query_one(
        "SELECT COUNT(*) AS n FROM users u LEFT JOIN scim_user_links l "
        f"ON l.user_id = u.id AND l.tenant_id = u.tenant_id WHERE {where}",
        tuple(params),
    )
    rows = query(f"{_USER_SELECT} WHERE {where} ORDER BY u.created_at, u.id LIMIT %s OFFSET %s",
                 tuple(params) + (limit, offset)) if limit else []
    return protocol.list_response(
        [protocol.user_resource(r, ctx.base_url) for r in rows],
        int(total["n"]) if total else 0, offset + 1,
    )


def user_row(ctx: ScimContext, user_id: str, conn=None, lock: bool = False) -> Optional[dict]:
    from backend.db.connection import query_one
    if not isinstance(user_id, str) or len(user_id) > 64:
        return None
    return query_one(
        f"{_USER_SELECT} WHERE u.tenant_id = %s AND u.id = %s"
        + (" FOR UPDATE OF u" if lock else ""),
        (ctx.tenant_id, user_id), conn=conn,
    )


def _not_found(user_id: str) -> ScimError:
    return ScimError(404, "No such user in this account.", code="scim_user_not_found",
                     params={"id": str(user_id)[:64]})


def get_user(ctx: ScimContext, user_id: str) -> dict:
    row = user_row(ctx, user_id)
    if not row:
        raise _not_found(user_id)
    return row


def check_version(row: dict, if_match: Optional[str]) -> None:
    if not if_match or if_match.strip() == "*":
        return
    wanted = {v.strip() for v in if_match.split(",")}
    if protocol.user_version(row) not in wanted:
        raise ScimError(412, "The resource changed since it was read.",
                        code="scim_version_mismatch")


# ── Rules shared by every write ──────────────────────────────────────────────

def _state(row: dict) -> UserState:
    return UserState(
        email=row["email"], full_name=row.get("full_name"),
        given_name=row.get("given_name"), family_name=row.get("family_name"),
        external_id=row.get("external_id"), active=protocol.is_active(row.get("status")),
        role=row["role"],
    )


def _check_domain(ctx: ScimContext, email: str) -> None:
    from backend.auth.sso import service as sso
    from backend.db.connection import query_one
    domain = sso.email_domain(email)
    owner = query_one("SELECT tenant_id FROM sso_domains WHERE domain = %s", (domain or "",))
    if (not domain or domain not in ctx.allowed_domains
            or not owner or owner["tenant_id"] != ctx.tenant_id):
        raise protocol.invalid_value(
            "userName must be an address on one of this account's company sign-in domains.",
            "scim_email_domain_not_allowed", params={"domain": domain or ""},
        )


def _email_taken(ctx: ScimContext, email: str, except_user: Optional[str] = None) -> None:
    from backend.db.connection import query_one
    other = query_one("SELECT id, tenant_id FROM users WHERE email = %s", (email,))
    if not other or other["id"] == except_user:
        return
    if other["tenant_id"] == ctx.tenant_id:
        raise ScimError(409, "A user with this userName already exists.",
                        code="scim_user_exists", scim_type="uniqueness")
    # Never touched, never linked, and nothing about that account is said
    # beyond the fact the address is taken.
    raise ScimError(409, "This e-mail address belongs to a different StockAI account.",
                    code="scim_email_in_other_tenant", scim_type="uniqueness")


def _external_id_taken(ctx: ScimContext, external_id: Optional[str],
                       except_user: Optional[str] = None) -> None:
    if not external_id:
        return
    from backend.db.connection import query_one
    other = query_one(
        "SELECT user_id FROM scim_user_links WHERE tenant_id = %s AND external_id = %s",
        (ctx.tenant_id, external_id),
    )
    if other and other["user_id"] != except_user:
        raise ScimError(409, "Another user already has this externalId.",
                        code="scim_external_id_taken", scim_type="uniqueness")


def _admin_not_managed() -> ScimError:
    return ScimError(
        403, "This SCIM token may not create, change or deactivate administrators. "
             "A StockAI administrator can allow it in the company sign-in settings.",
        code="scim_admin_not_managed",
    )


def guard_last_admin(tenant_id: str, losing: set[str], conn) -> None:
    """Refuse when the change would leave the tenant without an active
    administrator, or without one who can see every warehouse (the person who
    can change who sees what - the same rule the users screen applies)."""
    from backend.db.connection import query
    if not losing:
        return
    admins = query(
        "SELECT id, warehouse_scope FROM users WHERE tenant_id = %s AND role = 'admin' "
        "AND status = 'active' FOR UPDATE",
        (tenant_id,), conn=conn,
    )
    leaving = [a for a in admins if a["id"] in losing]
    if not leaving:
        return
    staying = [a for a in admins if a["id"] not in losing]
    unscoped_leaving = any(a["warehouse_scope"] is None for a in leaving)
    unscoped_staying = any(a["warehouse_scope"] is None for a in staying)
    if not staying or (unscoped_leaving and not unscoped_staying):
        raise ScimError(
            409, "This would leave the account without an active administrator "
                 "who can see every warehouse.",
            code="scim_last_admin", scim_type="mutability",
        )


def _upsert_link(conn, ctx: ScimContext, user_id: str, state: UserState) -> None:
    from backend.db.connection import execute
    execute(
        """INSERT INTO scim_user_links (user_id, tenant_id, external_id, given_name, family_name)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE SET
               external_id = EXCLUDED.external_id, given_name = EXCLUDED.given_name,
               family_name = EXCLUDED.family_name, updated_at = NOW()
           WHERE scim_user_links.tenant_id = EXCLUDED.tenant_id""",
        (user_id, ctx.tenant_id, state.external_id, state.given_name, state.family_name),
        conn=conn,
    )


# ── Creating ─────────────────────────────────────────────────────────────────

def create_user(ctx: ScimContext, body: Any) -> dict:
    from fastapi import HTTPException
    from backend.auth.password import hash_password
    from backend.db.connection import execute
    from backend.entitlements.service import enforce_limit, limit_guard
    from backend.users import service as user_svc
    from backend.utils.ids import generate_id

    with refusals(ctx, "create", "User") as info:
        desired = protocol.user_from_resource(body)
        info["email"] = desired.email
        _check_domain(ctx, desired.email)
        if desired.role == "admin" and not ctx.manage_admins:
            raise _admin_not_managed()
        _email_taken(ctx, desired.email)
        _external_id_taken(ctx, desired.external_id)

        user_id = generate_id("usr")
        try:
            with limit_guard(ctx.tenant_id) as conn:
                enforce_limit(ctx.tenant_id, "max_users",
                              user_svc.count_users(ctx.tenant_id, conn=conn), conn=conn)
                # Created the way a company sign-in creates people: the address
                # is vouched for by the identity provider (email_verified), and
                # there is no password - `has_password = FALSE` makes the
                # password door refuse every attempt, whatever is typed.
                execute(
                    """INSERT INTO users (id, tenant_id, email, full_name, role, hashed_password,
                                          email_verified, has_password, status,
                                          created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, TRUE, FALSE, %s, NOW(), NOW())""",
                    (user_id, ctx.tenant_id, desired.email, desired.full_name, desired.role,
                     hash_password(secrets.token_urlsafe(48)),
                     "active" if desired.active else "inactive"),
                    conn=conn,
                )
                _upsert_link(conn, ctx, user_id, desired)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("code") == "PLAN_LIMIT_REACHED":
                raise ScimError(
                    403, "The account has reached its user limit.",
                    code="scim_user_limit_reached",
                    params={"limit": "max_users", "current": detail.get("current"),
                            "max": detail.get("max")},
                )
            raise
        except ScimError:
            raise
        except Exception as exc:
            import psycopg2
            if isinstance(exc, psycopg2.IntegrityError):
                # Lost a race on the unique e-mail: answer what a second look says.
                _email_taken(ctx, desired.email)
                raise ScimError(409, "A user with this userName already exists.",
                                code="scim_user_exists", scim_type="uniqueness")
            raise
        info["resource_id"] = user_id

    log_event(ctx, operation="create", resource_type="User", resource_id=user_id,
              email=desired.email, outcome="success", http_status=201,
              changes={"role": desired.role, "active": desired.active,
                       "ignored": desired.ignored[:20]})
    _record(ctx, "account.scim_user_created", user_id,
            {"email": desired.email, "role": desired.role})
    if not desired.active:
        _record(ctx, "account.scim_user_deactivated", user_id, {"email": desired.email})
    return get_user(ctx, user_id)


# ── Changing ─────────────────────────────────────────────────────────────────

def replace_user(ctx: ScimContext, user_id: str, body: Any, if_match: Optional[str]) -> dict:
    with refusals(ctx, "replace", "User", user_id) as info:
        row = get_user(ctx, user_id)
        info["email"] = row["email"]
        check_version(row, if_match)
        desired = protocol.user_from_resource(body, _state(row))
        result = _apply(ctx, row, desired, "replace")
    return result


def patch_user(ctx: ScimContext, user_id: str, body: Any, if_match: Optional[str]) -> dict:
    with refusals(ctx, "patch", "User", user_id) as info:
        row = get_user(ctx, user_id)
        info["email"] = row["email"]
        check_version(row, if_match)
        desired = protocol.apply_user_patch(_state(row), body)
        result = _apply(ctx, row, desired, "patch")
    return result


def deactivate_user(ctx: ScimContext, user_id: str, if_match: Optional[str]) -> None:
    """DELETE /Users/{id}. Deactivates; the row and its history stay."""
    with refusals(ctx, "deactivate", "User", user_id) as info:
        row = get_user(ctx, user_id)
        info["email"] = row["email"]
        check_version(row, if_match)
        desired = _state(row)
        desired.active = False
        _apply(ctx, row, desired, "deactivate")


def _apply(ctx: ScimContext, row: dict, desired: UserState, operation: str) -> dict:
    """Diff, guard and write one user's change. Raises ScimError on refusal."""
    from backend.auth.password import hash_password
    from backend.db.connection import execute, transaction
    from backend.entitlements.service import take_tenant_lock

    user_id = row["id"]
    current = _state(row)
    status = row.get("status") or "active"

    if desired.active and status != "active":
        if status == "suspended":
            # A tenant admin put this person on hold by hand. The provider
            # saying "active" does not lift that; an admin does, in the app.
            raise ScimError(
                409, "This user was suspended by a StockAI administrator and cannot "
                     "be reactivated by provisioning.",
                code="scim_user_suspended_by_admin", scim_type="mutability",
            )
        new_status = "active"
    elif not desired.active and status in ("active", "pending_confirmation"):
        new_status = "inactive"
    else:
        new_status = status

    changed: dict[str, Any] = {}
    if desired.email != current.email:
        changed["userName"] = desired.email
    if desired.full_name != current.full_name:
        changed["displayName"] = desired.full_name
    if (desired.given_name, desired.family_name) != (current.given_name, current.family_name):
        changed["name"] = {"givenName": desired.given_name, "familyName": desired.family_name}
    if desired.external_id != current.external_id:
        changed["externalId"] = desired.external_id
    role_changed = desired.role != row["role"]
    status_changed = new_status != status

    if not changed and not role_changed and not status_changed:
        return row  # a routine sync that changes nothing writes nothing

    if not ctx.manage_admins and "admin" in (row["role"], desired.role):
        raise _admin_not_managed()
    if "userName" in changed:
        _check_domain(ctx, desired.email)
        _email_taken(ctx, desired.email, except_user=user_id)
    if "externalId" in changed:
        _external_id_taken(ctx, desired.external_id, except_user=user_id)

    reactivating = status_changed and new_status == "active"
    deactivating = status_changed and new_status == "inactive"
    # The provider vouches for the address when it (re)activates somebody. A
    # password nobody proved they own the mailbox for is dropped, exactly as a
    # company sign-in drops it (backend/auth/sso/service.py resolve_user).
    vouch_unverified = reactivating and not row.get("email_verified")
    cut_sessions = deactivating or role_changed or vouch_unverified

    with transaction() as conn:
        take_tenant_lock(ctx.tenant_id, conn)
        fresh = user_row(ctx, user_id, conn=conn, lock=True)
        if not fresh:
            raise _not_found(user_id)
        losing_admin = (fresh["role"] == "admin" and fresh["status"] == "active"
                        and (desired.role != "admin" or new_status != "active"))
        if losing_admin:
            guard_last_admin(ctx.tenant_id, {user_id}, conn)
        execute(
            """UPDATE users
                  SET email = %s, full_name = %s, role = %s, status = %s, updated_at = NOW(),
                      sessions_invalid_before = CASE WHEN %s THEN NOW()
                                                     ELSE sessions_invalid_before END
                WHERE id = %s AND tenant_id = %s""",
            (desired.email, desired.full_name, desired.role, new_status, cut_sessions,
             user_id, ctx.tenant_id),
            conn=conn,
        )
        if vouch_unverified:
            execute(
                """UPDATE users SET email_verified = TRUE, has_password = FALSE,
                          hashed_password = %s WHERE id = %s AND tenant_id = %s""",
                (hash_password(secrets.token_urlsafe(48)), user_id, ctx.tenant_id), conn=conn,
            )
        if cut_sessions:
            # The access tokens die by `sessions_invalid_before` (guards.py
            # compares it with each token's iat); the refresh tokens go here,
            # so nothing can mint a new one.
            execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,), conn=conn)
        _upsert_link(conn, ctx, user_id, desired)

    summary = dict(changed)
    if role_changed:
        summary["role"] = {"from": row["role"], "to": desired.role}
    if status_changed:
        summary["status"] = {"from": status, "to": new_status}
    if desired.ignored:
        summary["ignored"] = desired.ignored[:20]
    log_event(ctx, operation=operation, resource_type="User", resource_id=user_id,
              email=desired.email, outcome="success",
              http_status=204 if operation == "deactivate" else 200, changes=summary)
    if deactivating:
        _record(ctx, "account.scim_user_deactivated", user_id, {"email": desired.email})
    if reactivating:
        _record(ctx, "account.scim_user_reactivated", user_id, {"email": desired.email})
    if role_changed:
        _record(ctx, "account.scim_role_changed", user_id,
                {"email": desired.email, "role": desired.role, "previous_role": row["role"]})
    if changed:
        _record(ctx, "account.scim_user_updated", user_id,
                {"email": desired.email, "changes": ", ".join(sorted(changed))})
    return get_user(ctx, user_id)


# ── Groups (the three roles) ─────────────────────────────────────────────────

def _members(ctx: ScimContext, role: str) -> list[dict]:
    from backend.db.connection import query
    return query(
        "SELECT id, email FROM users WHERE tenant_id = %s AND role = %s ORDER BY created_at, id",
        (ctx.tenant_id, role),
    )


def _role_or_404(group_id: str) -> str:
    if group_id in protocol.ROLES:
        return group_id
    raise ScimError(404, "No such group. Groups are admin, analyst and viewer.",
                    code="scim_group_not_found", params={"id": str(group_id)[:64]})


def list_groups(ctx: ScimContext, filter_text: Optional[str], start_index: Any,
                count: Any, excluded_attrs: Optional[str]) -> dict:
    comps = protocol.parse_filter(filter_text, GROUP_FILTER_ATTRS)
    offset, limit = protocol.parse_paging(start_index, count)
    include = "members" not in protocol.excluded(excluded_attrs)

    def matches(role: str) -> bool:
        for c in comps:
            if not isinstance(c.value, str):
                return False
            if c.attribute == "displayname" and protocol.group_id_for(c.value) != role:
                return False
            if c.attribute == "id" and c.value != role:
                return False
        return True

    roles = [r for r in protocol.ROLES if matches(r)]
    page = roles[offset:offset + limit]
    return protocol.list_response(
        [protocol.group_resource(r, _members(ctx, r) if include else [], ctx.base_url,
                                 include_members=include) for r in page],
        len(roles), offset + 1,
    )


def get_group(ctx: ScimContext, group_id: str, excluded_attrs: Optional[str]) -> dict:
    role = _role_or_404(group_id)
    include = "members" not in protocol.excluded(excluded_attrs)
    return protocol.group_resource(role, _members(ctx, role) if include else [],
                                   ctx.base_url, include_members=include)


def create_group(ctx: ScimContext, body: Any) -> None:
    with refusals(ctx, "group_create", "Group"):
        name = body.get("displayName") if isinstance(body, dict) else None
        role = protocol.group_id_for(name)
        if role:
            raise ScimError(409, "This role group already exists; link to it instead.",
                            code="scim_group_exists", scim_type="uniqueness",
                            params={"id": role})
        raise ScimError(403, "Groups are the three StockAI roles and cannot be created.",
                        code="scim_group_create_unsupported")


def delete_group(ctx: ScimContext, group_id: str) -> None:
    with refusals(ctx, "group_delete", "Group", group_id):
        _role_or_404(group_id)
        raise ScimError(403, "Role groups cannot be deleted.",
                        code="scim_group_delete_unsupported")


def patch_group(ctx: ScimContext, group_id: str, body: Any) -> None:
    with refusals(ctx, "group_patch", "Group", group_id):
        role = _role_or_404(group_id)
        change = protocol.apply_group_patch(role, body)
        _apply_membership(ctx, role, change, "group_patch")


def replace_group(ctx: ScimContext, group_id: str, body: Any) -> dict:
    with refusals(ctx, "group_replace", "Group", group_id):
        role = _role_or_404(group_id)
        ids = protocol.group_from_resource(role, body)
        _apply_membership(ctx, role, protocol.MembershipChange(replace=ids), "group_replace")
    return get_group(ctx, role, None)


def _apply_membership(ctx: ScimContext, role: str, change: protocol.MembershipChange,
                      operation: str) -> None:
    """Membership becomes roles, all or nothing: an unknown member, an
    administrator this token may not touch or the last admin refuses the WHOLE
    request, so an IdP retry never finds it half applied."""
    from backend.db.connection import execute, query, transaction
    from backend.entitlements.service import take_tenant_lock

    current = {m["id"] for m in _members(ctx, role)}
    targets = protocol.role_changes_for_group(role, change, current)
    if not targets:
        return
    ids = list(targets)
    with transaction() as conn:
        take_tenant_lock(ctx.tenant_id, conn)
        rows = {r["id"]: r for r in query(
            "SELECT id, email, role, status FROM users WHERE tenant_id = %s AND id = ANY(%s) "
            "FOR UPDATE",
            (ctx.tenant_id, ids), conn=conn,
        )}
        unknown = [uid for uid in ids if uid not in rows]
        if unknown:
            raise protocol.invalid_value(
                "A member is not a user of this account.", "scim_member_not_found",
                params={"count": len(unknown)},
            )
        effective = {uid: new for uid, new in targets.items() if rows[uid]["role"] != new}
        if not effective:
            return
        if not ctx.manage_admins and any(
                rows[uid]["role"] == "admin" or new == "admin" for uid, new in effective.items()):
            raise _admin_not_managed()
        losing = {uid for uid, new in effective.items()
                  if rows[uid]["role"] == "admin" and new != "admin"
                  and rows[uid]["status"] == "active"}
        guard_last_admin(ctx.tenant_id, losing, conn)
        for uid, new in effective.items():
            execute(
                "UPDATE users SET role = %s, updated_at = NOW(), sessions_invalid_before = NOW() "
                "WHERE id = %s AND tenant_id = %s",
                (new, uid, ctx.tenant_id), conn=conn,
            )
            execute("DELETE FROM refresh_tokens WHERE user_id = %s", (uid,), conn=conn)

    for uid, new in effective.items():
        r = rows[uid]
        log_event(ctx, operation=operation, resource_type="Group", resource_id=uid,
                  email=r["email"], outcome="success", http_status=200,
                  changes={"group": role, "role": {"from": r["role"], "to": new}})
        _record(ctx, "account.scim_role_changed", uid,
                {"email": r["email"], "role": new, "previous_role": r["role"]})


# ── Admin side ───────────────────────────────────────────────────────────────

def admin_status(tenant_id: str) -> dict:
    from backend.auth.sso import service as sso
    token = tokens.active_public(tenant_id)
    return {
        "sso_configured": bool(sso.get_row(tenant_id)),
        "sso_ready": bool(sso_row_ready(tenant_id)),
        "base_url": base_url(),
        "token": token,
        "last_used_at": token["last_used_at"] if token else None,
        "last_change_at": last_change_at(tenant_id),
        "events": recent_events(tenant_id, 20)["items"],
    }
