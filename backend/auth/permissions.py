"""Custom-role permissions: the catalogue, the route rules and the enforcement.

Inventory of the defect this module closes (2026-10-06): the product stored 12
per-user permissions in `user_permissions` (written by
`PATCH /users/{id}/permissions`, listed by `GET`), and NO code ever read them
back to decide anything. They are still stored and still unenforced, on
purpose: enforcing them would lock out every user of a tenant whose admin once
ticked boxes that meant nothing. The enforced mechanism is the CUSTOM ROLE.

How it works
------------
* A tenant admin defines a custom role (`custom_roles`): a name and a set of
  permissions from the catalogue in `permissions.json`.
* A user may be assigned one (`users.custom_role_id`). A user with NO custom
  role behaves exactly as before: the built-in role (admin / analyst /
  viewer) in the token is the only gate.
* A custom role NARROWS the built-in role, it never widens it: the route's
  role guard still runs first, and on top of it the route's permission (looked
  up in the rules below) must be in the role's set. To give someone more,
  change their built-in role.
* Every request re-reads the user's role from the database (one indexed
  lookup, only on routes whose rule names a permission): a permission removed
  takes effect on the very next request. There is no cache, so the staleness
  bound is zero. The built-in role in the JWT keeps its existing bound (the
  token's 15 minutes).
* Fail closed: a role id that points at nothing, a permission name that is not
  in the catalogue, a mutating route no rule covers, or a row that cannot be
  read all answer 403 for a user who holds a custom role.
* API keys are untouched: they keep their read / write scope.

`permissions.json` is shared with the Rust API (`backend-rs/src/auth/
permissions.json`, byte-identical; `test_permission_catalogue_parity.py` holds
the two together and both languages run the same `probes` table).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.errors import AppError

log = logging.getLogger(__name__)

_CATALOGUE_PATH = Path(__file__).with_name("permissions.json")
API_PREFIX = "/api/v1"
OPEN = "open"
NONE = "none"  # no rule matches the route

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _load() -> dict:
    with open(_CATALOGUE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


_DATA = _load()
PERMISSIONS: tuple[str, ...] = tuple(p["name"] for p in _DATA["permissions"])
PERMISSION_SET: frozenset[str] = frozenset(PERMISSIONS)
LEGACY_PERMISSIONS: tuple[str, ...] = tuple(_DATA["legacy_permissions"])


def segments(path: str) -> tuple[str, ...]:
    """A route template or a path as comparable segments: the API prefix goes,
    every `{param}` (whatever its name or converter) becomes `{}`."""
    p = path.split("?", 1)[0]
    if p.startswith(API_PREFIX):
        p = p[len(API_PREFIX):]
    out = []
    for seg in p.split("/"):
        if not seg:
            continue
        out.append("{}" if seg.startswith("{") and seg.endswith("}") else seg)
    return tuple(out)


def _build_rules() -> dict[str, list[tuple[tuple[str, ...], str]]]:
    rules: dict[str, list[tuple[tuple[str, ...], str]]] = {"read": [], "write": []}
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for r in _DATA["rules"]:
        seg = segments(r["prefix"])
        key = (r["class"], seg)
        if key in seen:
            raise RuntimeError(f"duplicate permission rule {key}")
        seen.add(key)
        if r["permission"] != OPEN and r["permission"] not in PERMISSION_SET:
            raise RuntimeError(f"rule {r} names a permission that is not in the catalogue")
        rules[r["class"]].append((seg, r["permission"]))
    return rules


_RULES = _build_rules()


def rule_class(method: str) -> str:
    """Read for GET, write for everything else (fail closed on odd verbs)."""
    return "read" if method.upper() in ("GET", "HEAD", "OPTIONS") else "write"


def requirement(method: str, path: str) -> str:
    """The permission a call needs: a catalogue name, `open` (any signed-in
    user) or `none` (no rule covers it). The longest matching prefix wins."""
    seg = segments(path)
    best: Optional[tuple[int, str]] = None
    for prefix, perm in _RULES[rule_class(method)]:
        if len(prefix) <= len(seg) and seg[: len(prefix)] == prefix:
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), perm)
    return best[1] if best else NONE


@dataclass(frozen=True)
class Effective:
    restricted: bool
    permissions: frozenset
    role_id: Optional[str] = None

    def allows(self, permission: str) -> bool:
        if not self.restricted:
            return True
        return permission in PERMISSION_SET and permission in self.permissions


UNRESTRICTED = Effective(False, frozenset(), None)


def effective_for(tenant_id: str, user_id: str) -> Effective:
    """The user's CURRENT custom role, read from the database right now."""
    from backend.db.connection import query_one

    row = query_one(
        """SELECT u.custom_role_id AS role_id, r.permissions AS permissions
             FROM users u
             LEFT JOIN custom_roles r
               ON r.id = u.custom_role_id AND r.tenant_id = u.tenant_id
            WHERE u.id = %s AND u.tenant_id = %s""",
        (user_id, tenant_id),
    )
    if not row or not row.get("role_id"):
        return UNRESTRICTED
    perms = row.get("permissions")
    if perms is None:
        # The role id points at nothing (deleted or foreign): no permissions.
        return Effective(True, frozenset(), row["role_id"])
    return Effective(True, frozenset(p for p in perms if p in PERMISSION_SET), row["role_id"])


def denied(permission: str) -> AppError:
    return AppError(
        "permission_denied",
        f"Your custom role does not include the permission '{permission}'.",
        status_code=403,
        params={"permission": permission},
    )


def enforce(scope: dict, tenant_id: str, user_id: str) -> None:
    """Called by `get_current_user` for a PERSON (never an API key)."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if not path:
        return
    method = (scope.get("method") or "GET").upper()
    need = requirement(method, path)
    if need == OPEN:
        return
    if need == NONE and rule_class(method) == "read":
        return  # an unclassified read stays readable (documented)
    try:
        eff = effective_for(tenant_id, user_id)
    except Exception:
        log.exception("[permissions] could not read the role of user=%s", user_id)
        raise AppError(
            "permission_check_failed",
            "Permissions could not be verified, so the request was refused.",
            status_code=403,
        )
    if not eff.restricted:
        return
    if need == NONE:
        # A mutating route nobody classified: denied for a restricted user.
        raise denied("unclassified_route")
    if not eff.allows(need):
        raise denied(need)
