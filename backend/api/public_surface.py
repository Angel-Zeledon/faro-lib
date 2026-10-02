"""Which routes an `sk_live_*` API key may call — decided by rule, not by a list.

The owner's instruction (2026-10-01): every action a person can take inside the
app must also be reachable from a customer's own system. So the public API is
no longer eight hand-picked endpoints; it is every tenant-scoped action, minus
the ones a machine credential must never touch.

How a route's exposure is decided — `exposure(route)`, in this order:

1. **Its tag must be classified.** Every router tag is in exactly one of
   `EXPOSED_TAGS` or `INTERNAL_TAGS`. A tag in neither is refused (fail
   closed) and `test_public_api_surface.py` goes red until somebody decides.
   That is what makes a NEW router's API exposure a deliberate choice.
2. **It must not be on `INTERNAL_ROUTES`**, the per-route exceptions inside an
   otherwise exposed tag (an action that only makes sense for a person).
3. **It must authenticate through `get_current_user`.** An unauthenticated
   route (a signed PDF link, Twilio's inbound webhook) is not something a key
   is presented to.
4. **A key must be able to hold the role it needs.** A key is `read` (acts as
   a viewer) or `write` (acts as an analyst). A route whose guards admit only
   `admin` is not exposed: no key can ever be an admin, so publishing it would
   advertise something that answers 403 to everybody.

The scope a route needs is read off the same guards: if every role guard on it
admits `viewer`, a read key may call it; otherwise it needs a write key.

A new route inside an exposed tag IS exposed by default — the area was already
decided. It still does not slip through unseen: the committed reference
snapshot (`Frontend/src/data/public-api.json`, written by
`backend/scripts/export_public_api.py`) changes, and its staleness test fails
until the snapshot is regenerated and the diff is reviewed.

Enforcement is at runtime in `backend.auth.guards._authenticate_api_key`, which
reads the matched route from the request scope: a key presented to a route that
is not exposed gets `403 api_key_route_not_exposed` before any work and before
the call is metered. `PUBLIC_API_ONLY=true` prunes the app to the same set.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Tags whose routes a key may call (subject to rules 2–4) ──────────────────
EXPOSED_TAGS: frozenset[str] = frozenset({
    "sessions", "datasets", "data-sources", "configuration", "training",
    "planning", "forecasts", "artifacts", "reports", "analyst",
    "documents", "webhooks", "schedule",
    "inventory", "inventory-recommendation-log", "inventory-reversals",
    "scenarios", "ai-insights", "entitlements", "currency", "timezone",
    "freshness", "alerts",
    # One endpoint speaking MCP over five READ tools (backend/mcp/catalog.py).
    "mcp",
})

# ── Tags a key never reaches, and why ────────────────────────────────────────
INTERNAL_TAGS: dict[str, str] = {
    "auth": "login, signup and session tokens: a machine holds a key, it never logs in",
    "users": "user and password management belongs to people, not to a credential",
    "preferences": "per-person UI preferences; a key is not a person",
    "activity": "a person's own activity feed (/me/activity)",
    "api-keys": "key management: a key must never mint, list or revoke keys",
    "service-config": "instance and channel configuration (operators and tenant admins only)",
    "tenant": "tenant export and deletion",
    "messages": "person-to-person inbox",
    "chats": "a person's own assistant conversations",
    "demo": "onboarding demo seeding for a person exploring the app",
    # Not decided for keys: exposing it would widen the public API, which is
    # the owner's call. Kept internal until somebody asks for it.
    "inventory-payments": "marking a PO's invoice paid/unpaid from the /pedidos screen",
    # Cancelling moves every recommendation for the order's SKUs (its units
    # stop counting as on the way). Internal until the owner decides a machine
    # should be able to do that unattended.
    "inventory-cancellation": "cancelling / reopening a PO from the /pedidos screen",
    "trial": "unauthenticated trial signup",
    "whatsapp": "Twilio's inbound webhook, authenticated by signature, not by key",
    "models": "unauthenticated catalogue of model names",
    "health": "load-balancer probe",
}

# ── Per-route exceptions inside an exposed tag ───────────────────────────────
# (method, path without the /api/v1 prefix) → reason.
INTERNAL_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/inventory/po/{po_log_id}/send-to-me"):
        "mails the signed-in person; a key has no inbox",
    ("POST", "/alerts/read"):
        "marks alerts read for the signed-in person",
    ("POST", "/entitlements/upgrade-request"):
        "a person asking to talk to us about limits; it names who to answer",
}

API_PREFIX = "/api/v1"

# The role each key scope acts as. The key's `scope` column is generated from
# its `role` with exactly this mapping (backend/db/migrations.py).
SCOPE_ROLE: dict[str, str] = {"read": "viewer", "write": "analyst"}
ROLE_SCOPE: dict[str, str] = {v: k for k, v in SCOPE_ROLE.items()}


@dataclass(frozen=True)
class Exposure:
    exposed: bool
    # 'read' | 'write' when exposed; None otherwise.
    scope: str | None
    # Why not, when not exposed. Empty when exposed.
    reason: str = ""


def _walk(dependant, out: list) -> None:
    for sub in dependant.dependencies:
        out.append(sub.call)
        _walk(sub, out)


def _compute(route) -> Exposure:
    from fastapi.routing import APIRoute
    from backend.auth.guards import get_current_user

    if not isinstance(route, APIRoute):
        return Exposure(False, None, "not an HTTP API route")

    tags = set(route.tags or [])
    internal = sorted(t for t in tags if t in INTERNAL_TAGS)
    if internal:
        return Exposure(False, None, f"internal tag '{internal[0]}': {INTERNAL_TAGS[internal[0]]}")
    if not tags & EXPOSED_TAGS:
        return Exposure(False, None, f"unclassified tag(s) {sorted(tags)}; refused until decided")

    path = route.path[len(API_PREFIX):] if route.path.startswith(API_PREFIX) else route.path
    for method in route.methods or ():
        reason = INTERNAL_ROUTES.get((method.upper(), path))
        if reason:
            return Exposure(False, None, reason)

    calls: list = []
    _walk(route.dependant, calls)
    if get_current_user not in calls:
        return Exposure(False, None, "unauthenticated route")

    # Every role guard on the route must admit the role the key acts as.
    allowed = {"admin", "analyst", "viewer"}
    for call in calls:
        roles = getattr(call, "allowed_roles", None)
        if roles is not None:
            allowed &= set(roles)
    if "viewer" in allowed:
        return Exposure(True, "read")
    if "analyst" in allowed:
        return Exposure(True, "write")
    return Exposure(False, None, "admin only: no API key can hold the admin role")


def exposure(route) -> Exposure:
    """The decision for one route, computed once per route object."""
    if route is None:
        return Exposure(False, None, "no matched route")
    # Memoised ON the route object, not in a dict keyed by id(): the test suite
    # rebuilds the app, and a recycled id() would hand a new route the old
    # route's decision.
    hit = getattr(route, "_stockai_exposure", None)
    if hit is None:
        hit = _compute(route)
        try:
            route._stockai_exposure = hit
        except AttributeError:
            pass
    return hit


def exposed_routes(app) -> list[tuple[str, str, str]]:
    """Every (method, path-without-prefix, scope) a key may call, sorted."""
    out: list[tuple[str, str, str]] = []
    for route in app.routes:
        exp = exposure(route)
        if not exp.exposed:
            continue
        path = route.path[len(API_PREFIX):]
        for method in sorted(route.methods or ()):
            out.append((method.upper(), path, exp.scope or "read"))
    return sorted(out, key=lambda r: (r[1], r[0]))


def public_endpoints(app) -> tuple[tuple[str, str], ...]:
    """(method, path) pairs of the public surface — the shape the old hand list had."""
    return tuple((m, p) for m, p, _s in exposed_routes(app))


def excluded_routes(app) -> list[tuple[str, str, str]]:
    """Every authenticated /api/v1 route a key may NOT call, with the reason."""
    out: list[tuple[str, str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(API_PREFIX):
            continue
        exp = exposure(route)
        if exp.exposed:
            continue
        for method in sorted(getattr(route, "methods", None) or ()):
            out.append((method.upper(), path[len(API_PREFIX):], exp.reason))
    return sorted(out, key=lambda r: (r[1], r[0]))
